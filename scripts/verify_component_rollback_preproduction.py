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

COMPONENTS = ("service-checks", "daily-digest", "health-observer", "web", "backtest-worker")
# qsys-monitor.service lives only on the node; the stub stands in for its legacy identity.
MONITOR_STUB = """[Unit]
Description=qsys monitor legacy stand-in for preproduction rollback verification

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/qsys
ExecStart=/home/ubuntu/qsys/.venv/bin/python -u scripts/monitor.py
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
    if source.is_file():
        run(["sudo", "install", "-m", "0644", source, target])
        return "repository"
    staged = Path(os.environ["RUNNER_TEMP"]) / unit
    staged.write_text(MONITOR_STUB)
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
    for directory in spec["state_directories"]:
        marker = Path(directory["path"]) / f".rollback-pilot-{component}"
        run(["sudo", "-u", directory["owner"], "sh", "-c", f"echo {component} > '{marker}'"])
        markers[marker] = (fingerprint(marker), directory["owner"])

    if not entrypoint._rollback_component(component, "uninstalled", repo):
        raise ValueError(f"{component}: entrypoint legacy switch reported failure")
    assert_legacy("legacy")
    assert_markers("legacy")

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

    activate(True)
    assert_active_release("reactivate")
    assert_markers("reactivate")
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
