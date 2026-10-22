"""独立组件回切一次性预生产验证（#453）；仅允许一次性 GitHub-hosted Linux。

用真实组件制品、目标 SHA 自带的安装器和生产 entrypoint 的失败补偿函数，按生产白名单
路径逐个组件验证：激活 → legacy（移除 current 与 drop-in）→ 安装失败自动恢复 legacy →
重新激活。只核对 systemd 有效身份、state 保留和专属身份下可执行，不运行业务作业。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

if __package__:
    from scripts.verify_storage_report_preproduction import SHA, run, stage, validate_host
else:
    from verify_storage_report_preproduction import SHA, run, stage, validate_host

COMPONENTS = ("service-checks", "daily-digest", "health-observer", "web", "backtest-worker", "live-core")
# qsys-monitor.service lives only on the node; the stub stands in for its legacy identity.
MONITOR_STUB = """[Unit]
Description=qsys monitor legacy stand-in for preproduction rollback verification

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/qsys
ExecStart=/home/ubuntu/qsys/.venv/bin/python -u scripts/monitor.py
"""
LIVE_STUB = """[Unit]
Description=qsys Live legacy layout stand-in; no business process is started

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/qsys
ExecStart=/home/ubuntu/qsys/.venv/bin/python scripts/run_live.py --config config/deploy.local.toml
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/ubuntu/qsys/var /data/qsys
"""
# sudo's secure_path searches /usr/local/sbin before /usr/bin.
SHIM = Path("/usr/local/sbin/systemctl")
SHIM_FLAG = Path("/run/qsys-rollback-pilot-fail-daemon-reload")
SHIM_SCRIPT = f"""#!/bin/sh
if [ "$1" = daemon-reload ] && [ -e {SHIM_FLAG} ]; then
    rm -f {SHIM_FLAG}
    echo "injected daemon-reload failure" >&2
    exit 1
fi
exec /usr/bin/systemctl "$@"
"""


def parse_releases(values):
    releases = {}
    for value in values:
        component, _, sha = value.partition("=")
        if component not in COMPONENTS or not SHA.fullmatch(sha) or component in releases:
            raise ValueError("releases must be unique <component>=<40-hex SHA> pairs")
        releases[component] = sha
    if not releases:
        raise ValueError("at least one component release is required")
    return releases


def exec_path(value):
    match = re.search(r"path=(\S+)", value)
    if not match:
        raise ValueError("unit has no ExecStart")
    return match.group(1)


def effective(unit):
    value = run(["systemctl", "show", unit, "--property=User,Group,WorkingDirectory,ExecStart"]).stdout
    fields = dict(line.split("=", 1) for line in value.splitlines() if "=" in line)
    return {"user": fields.get("User", ""), "group": fields.get("Group", ""),
            "working_directory": fields.get("WorkingDirectory", ""),
            "exec_path": exec_path(fields.get("ExecStart", ""))}


def dropin_value(spec, key):
    values = [line.split("=", 1)[1] for line in spec["dropin_lines"] if line.startswith(key + "=")]
    return values[-1] if values else ""


def ensure_identities(spec):
    groups = {d["group"] for d in spec["state_directories"]} | {dropin_value(spec, "Group")}
    groups |= set(dropin_value(spec, "SupplementaryGroups").split())
    for group in sorted(g for g in groups if g):
        if run(["getent", "group", group], check=False).returncode:
            run(["sudo", "groupadd", "--system", group])
    users = {d["owner"] for d in spec["state_directories"]} | {dropin_value(spec, "User")}
    for user in sorted(u for u in users if u):
        if run(["getent", "passwd", user], check=False).returncode:
            primary = dropin_value(spec, "Group") if user == dropin_value(spec, "User") else user
            if run(["getent", "group", primary], check=False).returncode:
                run(["sudo", "groupadd", "--system", primary])
            run(["sudo", "useradd", "--system", "--no-create-home", "--shell",
                 "/usr/sbin/nologin", "-g", primary, user])


def install_base_unit(repo, unit):
    source = repo / "deploy/systemd" / unit
    target = Path("/etc/systemd/system") / unit
    if source.is_file() and unit != "qsys-live-core.service":
        run(["sudo", "install", "-m", "0644", source, target])
        return "repository"
    staged = Path(os.environ["RUNNER_TEMP"]) / unit
    staged.write_text(LIVE_STUB if unit == "qsys-live-core.service" else MONITOR_STUB)
    run(["sudo", "install", "-m", "0644", staged, target])
    return "node-only-stub"


def load_entrypoint(repo):
    path = repo / "deploy/linux/qsys-production-deploy-entrypoint.py"
    spec = importlib.util.spec_from_file_location("qsys_production_entrypoint", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fingerprint(path):
    raw = run(["sudo", "cat", path]).stdout
    return hashlib.sha256(raw.encode()).hexdigest()



LIVE_FIXTURE = r"""
import json,sys,sqlite3
from datetime import datetime
from pathlib import Path
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Order,OrderType,Side,Fill
from qsys.core.types import Symbol
from qsys.live.store import LiveStore
from scripts.verify_live_core_preproduction import state_fingerprint
phase=sys.argv[1];s=LiveStore('var/live-state.sqlite3');at=datetime(2026,1,5,9,15)
symbol=Symbol.parse('600000.SH')
def order(name): return Order(name,symbol,Side.BUY,100,OrderType.LIMIT,10.0,at)
def fill(name,qty,trade): return Fill(name,symbol,Side.BUY,qty,10.0,5.0,at,'fixture-broker',trade)
if phase=='seed':
 s.save(Portfolio(10000),{}, {},at=at,initial_value=10000)
 assert s.begin_event('fixture-completed',at,'auction')
 s.commit_event('fixture-completed',Portfolio(10000),{}, {},at=at)
 s.record_orders('fixture-completed',[order('pending-0'),order('done-0')])
 s.record_results([fill('done-0',100,'trade-0')],[],{'done-0':0})
elif phase=='advance':
 s.record_orders('fixture-new',[order('pending-1'),order('done-1')])
 s.record_results([fill('done-1',100,'trade-1'),fill('pending-0',50,'trade-partial-0')],[],{'done-1':0,'pending-0':50})
elif phase=='legacy-advance':
 s.record_results([fill('pending-1',40,'trade-after-compensation')],[],{'pending-1':60})
assert not s.begin_event('fixture-completed',at,'auction')
with s._connect() as c:
 counts={name:c.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in ('live_events','live_orders','live_runtime_fills')}
remaining={o.order_id:left for o,left in s.pending_orders()}
# old filename stays intact and is never made into a symlink or overwritten.
original=Path('/home/ubuntu/qsys/var/live-state.sqlite3')
assert not original.is_symlink()
with sqlite3.connect(original.resolve().as_uri()+'?mode=ro',uri=True) as c:
 original_fills=c.execute('SELECT count(*) FROM live_runtime_fills').fetchone()[0]
print(json.dumps({'counts':counts,'remaining':remaining,'original_fills':original_fills,'actual_path':str(s.path),'fingerprint':state_fingerprint(s.path)}))
"""


def live_fixture_command(checkout, python, phase, *, legacy=False):
    validate_host(os.environ)
    user = "ubuntu" if legacy else "qsys-live-core"
    module_path = str(checkout) + (":" + str(checkout / "src") if legacy else "")
    result = run(["sudo", "-u", user, "env", "PYTHONDONTWRITEBYTECODE=1",
                  "PYTHONPATH=" + module_path, python, "-B", "-c", LIVE_FIXTURE, phase],
                 cwd=Path("/home/ubuntu/qsys"))
    return json.loads(result.stdout)


def setup_live_fixture(checkout, python):
    validate_host(os.environ)
    for path in (Path('/home/ubuntu/qsys/config/deploy.local.toml'),
                 Path('/home/ubuntu/qsys/var/live-state.sqlite3'),
                 Path('/etc/qsys/components/live-core/state-binding-v1')):
        if path.exists() or path.is_symlink():
            raise ValueError('refuse fixture over an existing production layout')
    if run(["getent", "passwd", "ubuntu"], check=False).returncode:
        run(["sudo", "useradd", "--system", "--user-group", "--home-dir", "/home/ubuntu", "--shell", "/usr/sbin/nologin", "ubuntu"])
    run(["sudo", "install", "-d", "-m", "0755", "/home/ubuntu/qsys/config"])
    run(["sudo", "install", "-d", "-o", "ubuntu", "-g", "ubuntu", "-m", "0755", "/home/ubuntu/qsys/var"])
    run(["sudo", "usermod", "-aG", "qsys-observers", "qsys-live-core"])
    run(["sudo", "setfacl", "-m", "g:qsys-observers:--x", "/home/ubuntu"])
    config = Path(os.environ["RUNNER_TEMP"]) / "live-fixture.toml"
    config.write_text('[safety]\nmode="live"\norders_enabled=true\nprotected_account_ids=["validation-only"]\n[tushare]\ntoken="validation-only"\n[strategy]\nfile='+json.dumps(str(checkout / 'strategies/structural_v1_2.py'))+'\n')
    run(["sudo", "install", "-m", "0644", config, "/home/ubuntu/qsys/config/deploy.local.toml"])
    for table in ("live_equity", "live_fills", "live_rejects", "live_recon"):
        run(["sudo", "install", "-d", "-m", "0755", "/data/qsys/warehouse/" + table])
    run(["sudo", "install", "-d", "-m", "0755", "/data/qsys/var-deployments/github"])
    helper = checkout / "scripts/prepare_live_core_release.py"
    base = ["sudo", "env", "PYTHONDONTWRITEBYTECODE=1", "PYTHONPATH="+str(checkout)+":"+str(checkout/'src'),
            python, "-B", helper, "--repo", "/home/ubuntu/qsys"]
    run([*base, "--prepare-only"])
    seed = live_fixture_command(checkout, python, "seed", legacy=True)
    run(["sudo", "-u", "ubuntu", python, "-B", "-c", "import sqlite3; c=sqlite3.connect('/home/ubuntu/qsys/var/live-state.sqlite3'); c.execute('PRAGMA journal_mode=DELETE'); c.close()"], cwd=Path("/home/ubuntu/qsys"))
    run([*base, "--bind", "--audit-root", "/data/qsys/var-deployments/github/live-fixture"])
    current = live_fixture_command(checkout, python, "advance")
    assert seed['counts']['live_runtime_fills'] == 1
    assert current['counts']['live_runtime_fills'] == 3
    assert current['remaining'] == {'pending-0':50,'pending-1':100}
    assert current['original_fills'] == 1
    return current

def verify_component(repo, root, entrypoint, component, sha, evidence_dir):
    spec = entrypoint.COMPONENT_RELEASES[component]
    unit = str(spec["unit"])
    current = Path(str(spec["current"]))
    dropin = Path(str(spec["dropin"]))
    release_root = Path(str(spec["release_root"]))
    record = {"component": component, "sha": sha, "unit": unit, "phases": []}
    ensure_identities(spec)
    record["base_unit_source"] = install_base_unit(repo, unit)
    run(["sudo", "systemctl", "daemon-reload"])
    base = effective(unit)
    # The worker's base unit already runs the isolated release; it has no legacy path.
    record["legacy_path"] = not base["exec_path"].startswith(str(release_root.parent))
    record["base_identity"] = base

    run(["sudo", "install", "-d", "-o", str(os.getuid()), "-m", "0755", release_root / sha])
    _, meta = stage(repo, root, sha, component, release_root / sha)
    record["release"] = meta
    checkout = root / "checkouts" / sha

    def activate(expect_success):
        result = run([sys.executable, "scripts/install_component_release.py", "--repo", checkout,
                      "--sha", sha, "--component", component], cwd=checkout, check=False,
                     timeout=300)
        if (result.returncode == 0) != expect_success:
            raise ValueError(f"{component}: unexpected installer outcome: {result.stderr[-500:]}")
        return result

    def assert_active_release(label):
        identity = effective(unit)
        if (current.resolve(strict=True) != (release_root / sha).resolve(strict=True)
                or identity["user"] != dropin_value(spec, "User")
                or not identity["exec_path"].startswith(str(current))):
            raise ValueError(f"{component}: {label} does not run the isolated release")
        command = [current / str(spec["executable"]), "--help"]
        # The installer-created user's primary group is the drop-in Group.
        result = run(["sudo", "-u", identity["user"], *command],
                     check=False, timeout=120)
        if result.returncode:
            raise ValueError(f"{component}: executable failed under its identity")
        record["phases"].append({"name": label, "identity": identity,
                                 "dropin_sha256": fingerprint(dropin), "help_returncode": 0})

    def assert_legacy(label):
        identity = effective(unit)
        if current.is_symlink() or current.exists() or dropin.exists() or identity != base:
            raise ValueError(f"{component}: {label} did not restore the legacy unit")
        if not (release_root / sha / str(spec["executable"])).is_file():
            raise ValueError(f"{component}: legacy switch removed the release")
        record["phases"].append({"name": label, "identity": identity})

    markers = {}

    def assert_markers(label):
        for path, (digest, owner) in markers.items():
            stat = run(["sudo", "stat", "-c", "%U", path]).stdout.strip()
            if fingerprint(path) != digest or stat != owner:
                raise ValueError(f"{component}: state marker changed after {label}")

    activate(True)
    assert_active_release("activate")
    live_evidence = None
    live_python = release_root / sha / "venv/bin/python"
    if component == "live-core":
        live_evidence = setup_live_fixture(checkout, live_python)
        record["live_state_initial"] = live_evidence
    for directory in spec["state_directories"]:
        marker = Path(directory["path"]) / f".rollback-pilot-{component}"
        run(["sudo", "-u", directory["owner"], "sh", "-c", f"echo {component} > '{marker}'"])
        markers[marker] = (fingerprint(marker), directory["owner"])

    if not entrypoint._rollback_component(component, "uninstalled", repo):
        raise ValueError(f"{component}: entrypoint legacy switch reported failure")
    assert_legacy("legacy")
    assert_markers("legacy")
    if live_evidence is not None:
        observed = live_fixture_command(checkout, live_python, "read", legacy=True)
        if observed != live_evidence:
            raise ValueError("legacy compensation did not observe latest bound state")

    run(["sudo", "touch", SHIM_FLAG])
    try:
        failed = activate(False)
    finally:
        run(["sudo", "rm", "-f", SHIM_FLAG])
    # Only the injected reload failure proves compensation ran after the switch.
    if "daemon-reload" not in failed.stderr:
        raise ValueError(f"{component}: installer failed before the injected point")
    assert_legacy("failed-activation-restored")
    record["phases"][-1]["installer_error"] = failed.stderr.strip()[-300:]
    assert_markers("failed-activation-restored")
    if live_evidence is not None:
        observed = live_fixture_command(checkout, live_python, "read", legacy=True)
        if observed != live_evidence:
            raise ValueError("installer failure compensation read stale state")
        live_evidence = live_fixture_command(checkout, live_python, "legacy-advance", legacy=True)
        if live_evidence['counts']['live_runtime_fills'] != 4 or live_evidence['original_fills'] != 1:
            raise ValueError("legacy recovery did not preserve/append latest fills")

    activate(True)
    assert_active_release("reactivate")
    assert_markers("reactivate")
    if live_evidence is not None:
        observed = live_fixture_command(checkout, live_python, "read")
        if observed != live_evidence:
            raise ValueError("reactivated component did not retain recovery facts")
        record["live_state_final"] = observed
        record["live_state_continuity"] = "new facts survive real installer failure compensation; old DB retained; completed event not replayed"
    record["status"] = "success"
    (evidence_dir / f"{component}.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", action="append", default=[],
                        help="<component>=<exact merged main SHA>; repeatable")
    args = parser.parse_args(argv)
    validate_host(os.environ)
    releases = parse_releases(args.release)
    repo = Path(__file__).resolve().parents[1]
    suffix = os.environ["GITHUB_RUN_ID"] + "-" + os.environ["GITHUB_RUN_ATTEMPT"]
    root = Path("/opt") / ("qsys-rollback-pilot-" + suffix)
    run(["sudo", "mkdir", root])  # No -p: refuse reuse; never clean arbitrary paths.
    run(["sudo", "chown", f"{os.getuid()}:{os.getgid()}", root])
    evidence_dir = Path(os.environ["RUNNER_TEMP"]) / root.name / "evidence"
    evidence_dir.mkdir(parents=True)
    evidence = {"schema_version": 1, "kind": "component-rollback-preproduction",
                "status": "running", "environment": "ephemeral-github-hosted",
                "production_changed": False, "components": []}
    try:
        # Mirror the node: the installer requires the trusted whitelist under /etc/qsys.
        run(["sudo", "install", "-d", "-m", "0755", "/etc/qsys"])
        run(["sudo", "install", "-m", "0644", repo / "deploy/component-releases.json",
             "/etc/qsys/component-releases.json"])
        shim = Path(os.environ["RUNNER_TEMP"]) / "systemctl-shim"
        shim.write_text(SHIM_SCRIPT)
        run(["sudo", "install", "-D", "-m", "0755", shim, SHIM])
        entrypoint = load_entrypoint(repo)
        for component, sha in releases.items():
            evidence["components"].append(
                verify_component(repo, root, entrypoint, component, sha, evidence_dir))
        evidence["status"] = "success"
    except Exception as exc:
        evidence["status"] = "failure"
        evidence["error_type"] = type(exc).__name__
        evidence["error"] = str(exc)[:500]
        raise
    finally:
        run(["sudo", "rm", "-f", SHIM], check=False)
        (evidence_dir / "record.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
