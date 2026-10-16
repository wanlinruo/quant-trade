from __future__ import annotations

import argparse
import base64
import json
import importlib.util
import shutil
import subprocess
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from qsys.ops.deployment import (
    ContractError,
    DeploymentManifest,
    config_fingerprints,
    require_full_sha,
    validate_config,
    validate_record,
)

MANIFEST = Path("deploy/deployment.toml")


def _contract_module():
    path = Path("scripts/deployment_contract.py")
    spec = importlib.util.spec_from_file_location("qsys_deployment_contract", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _entrypoint_module():
    path = Path("deploy/linux/qsys-deploy-entrypoint.py")
    spec = importlib.util.spec_from_file_location("qsys_deploy_entrypoint", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dispatcher_module():
    path = Path("deploy/linux/qsys-deploy-ssh.py")
    spec = importlib.util.spec_from_file_location("qsys_deploy_ssh", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _key_installer_module():
    path = Path("deploy/linux/install-test-deploy-key.py")
    spec = importlib.util.spec_from_file_location("qsys_install_test_key", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _production_entrypoint_module():
    path = Path("deploy/linux/qsys-production-deploy-entrypoint.py")
    spec = importlib.util.spec_from_file_location("qsys_production_entrypoint", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _production_dispatcher_module():
    path = Path("deploy/linux/qsys-production-deploy-ssh.py")
    spec = importlib.util.spec_from_file_location("qsys_production_ssh", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _production_key_installer_module():
    path = Path("deploy/linux/install-production-deploy-key.py")
    spec = importlib.util.spec_from_file_location("qsys_install_production_key", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _test_config(path: Path, *, password: str = "test-secret") -> Path:
    streams = (
        "orders", "control", "fills", "rejects", "order_status", "account",
        "heartbeat", "quote_requests", "quote_snapshots", "quote_latest",
        "market_snapshot", "market_schedule", "price_limits",
    )
    stream_lines = "\n".join(f'{name} = "test:{name}"' for name in streams)
    path.write_text(
        f'''schema_version = 1

[redis]
host = "127.0.0.1"
db = 9
password = "{password}"

[streams]
{stream_lines}

[safety]
mode = "disabled"
orders_enabled = false
protected_account_ids = []
local_kill_path = "/data/qsys-test/var/AGENT_KILL"

[strategy]
workbench = "/data/qsys-test/var/workbench.sqlite3"
''',
        encoding="utf-8",
    )
    return path


def _production_config(path: Path) -> Path:
    path.write_text(
        '''schema_version = 1

[redis]
host = "127.0.0.1"
db = 0
password = "prod-secret"

[safety]
mode = "shadow"
orders_enabled = false
local_kill_path = "/data/qsys/var-prod/SHADOW_KILL"

[strategy]
workbench = "/data/qsys/var-prod/workbench.sqlite3"
''',
        encoding="utf-8",
    )
    return path


def _record() -> dict:
    sha = "a" * 40
    return {
        "schema_version": 1,
        "run_id": "github-123-1",
        "environment": "test",
        "status": "success",
        "actor": "maintainer",
        "started_at": "2026-10-07T10:00:00+08:00",
        "completed_at": "2026-10-07T10:01:00+08:00",
        "repository": "wanlinruo/quant-trade",
        "pr_number": 151,
        "source_sha": sha,
        "source_tree": "b" * 40,
        "components": ["linux-web"],
        "before": {"linux": "c" * 40},
        "after": {"linux": sha},
        "config_fingerprints": {"linux": "d" * 64},
        "checks": [{"name": "healthz", "status": "passed", "detail": "HTTP 200"}],
        "rollback_targets": {"linux": "c" * 40},
        "migration_summary": "none",
    }


def test_manifest_covers_resident_services_and_windows_tasks() -> None:
    manifest = DeploymentManifest.load(MANIFEST)
    units = {unit for component in manifest.components for unit in component.linux_units}
    tasks = {task for component in manifest.components for task in component.windows_tasks}

    assert {
        "qsys-monitor.service", "qsys-paper.service", "qsys-shadow-core.service",
        "qsys-health-observer.service", "qsys-live-core.service",
    } <= units
    assert {"qsys-agent-shadow", "qsys-market-gateway", "qsys-watchdog"} <= tasks


def test_research_runner_unit_enforces_identity_resources_and_denials() -> None:
    entrypoint = _production_entrypoint_module()
    unit = Path("deploy/systemd/qsys-research-runner.service").read_text(
        encoding="utf-8",
    )
    installer = Path("scripts/install_research_runner_isolation.sh").read_text(
        encoding="utf-8",
    )

    assert "User=qsys-research" in unit
    assert "Group=qsys-research" in unit
    assert "ConditionPathExists=/etc/qsys/research-runner.activation-approved" in unit
    assert "ExecStart=/usr/bin/env /opt/qsys-research/current/venv/bin/qsys-research-runner" in unit
    assert "MemoryHigh=3G" in unit
    assert "MemoryMax=4G" in unit and "--memory-max-mb 4096" in unit
    assert "MemorySwapMax=0" in unit
    assert "CPUQuota=100%" in unit and "--cpu-quota-percent 100" in unit
    assert "CPUWeight=10" in unit
    assert "IOWeight=10" in unit and "--io-weight 10" in unit
    assert "OOMScoreAdjust=750" in unit
    assert "ManagedOOMMemoryPressure=kill" in unit
    assert "BindReadOnlyPaths=/data/qsys/warehouse-snapshot-store:" in unit
    assert "ReadWritePaths=/data/qsys/research-jobs" in unit
    assert "InaccessiblePaths=-/data/qsys/warehouse" in unit
    assert "InaccessiblePaths=" in unit and "/data/qsys/var-prod" in unit
    assert "PrivateNetwork=true" in unit
    assert "IPAddressDeny=any" in unit
    assert "CapabilityBoundingSet=" in unit
    assert "useradd --system --gid qsys-research" in installer
    assert '"$job_store/inbox" "$job_store/control"' in installer
    assert '"$job_store/jobs" "$job_store/rejected"' in installer
    assert 'find "$release_root" -xdev ! -type l -perm /022' in installer
    assert "enable --now qsys-research-runner.service" not in installer
    assert "staged inactive isolated Research Runner release" in installer
    assert "qsys-research-runner.service" not in entrypoint.SYSTEMD_ENABLE_ON_INSTALL
    assert "qsys-research-runner-test.service" in entrypoint.SYSTEMD_EXCLUDED


def test_test_research_runner_is_staged_inactive_with_test_only_paths() -> None:
    unit = Path("deploy/systemd/qsys-research-runner-test.service").read_text(
        encoding="utf-8",
    )
    installer = Path("scripts/install_test_research_runner_isolation.sh").read_text(
        encoding="utf-8",
    )

    assert "User=qsys-research-test" in unit
    assert "MemoryHigh=480M" in unit
    assert "MemoryMax=512M" in unit and "MemorySwapMax=0" in unit
    assert "CPUQuota=25%" in unit
    assert "--allow-test-drills" in unit
    assert "BindReadOnlyPaths=/data/qsys-test/warehouse-snapshot-store:" in unit
    assert "ReadWritePaths=/data/qsys-test/research-jobs" in unit
    assert "/data/qsys/warehouse-snapshot-store" in unit
    assert "PrivateNetwork=true" in unit and "IPAddressDeny=any" in unit
    assert "[Install]" not in unit
    assert "qsys-research-test" in installer
    assert "systemctl enable" not in installer
    assert "systemctl start" not in installer
    assert "systemctl is-enabled qsys-research-runner-test.service" in installer
    assert "-m 0751 /data/qsys-test/research-jobs" in installer


def test_catalog_verifier_uses_an_independent_immutable_release() -> None:
    wrapper = Path("scripts/install_catalog_verify_release.sh").read_text(
        encoding="utf-8",
    )
    installer = Path("scripts/install_component_release.py").read_text(encoding="utf-8")
    specs = Path("deploy/component-releases.json").read_text(encoding="utf-8")

    assert "install_component_release.py" in wrapper
    assert "--component catalog-verify" in wrapper
    assert "/opt/qsys-data/catalog-verify/current/venv/bin/qsys-catalog-verify" in specs
    assert "/opt/qsys-data/releases/catalog-verify" in specs
    assert "QSYS_CATALOG_SOURCE_ROOT=/opt/qsys-data/catalog-verify/current/source" in specs
    assert "QSYS_CATALOG_FROZEN_BASELINE=/data/qsys/catalog-verify-state/frozen_baseline.json" in specs
    assert "QSYS_CATALOG_LEGACY_BASELINE" in specs
    assert "refusing to initialize component state without seed" in installer
    assert "zz-isolated-release.conf" in specs
    assert "refusing to switch component while its unit is active" in installer
    assert "_writable_by_group_or_other" in installer
    assert "_restore(current, previous, dropin, previous_dropin)" in installer
    assert "systemctl restart qsys-live-core.service" not in installer
    assert "git reset" not in installer

    plan = DeploymentManifest.load(MANIFEST).plan([
        "scripts/build_component_release.py",
        "scripts/component_release_config.py",
        "scripts/install_component_release.py",
        "scripts/install_catalog_verify_release.sh",
        "deploy/component-releases.json",
    ], environment="production")
    assert plan.components == ()
    assert plan.unknown_paths == ()

    entrypoint = Path(
        "deploy/linux/qsys-production-deploy-entrypoint.py"
    ).read_text(encoding="utf-8")
    assert 'installer_environment[str(seed["source_environment"])]' in entrypoint
    assert 'repo / str(seed["source"])' in entrypoint
    assert "env=installer_environment" in entrypoint


def test_shared_redis_change_targets_both_nodes() -> None:
    plan = DeploymentManifest.load(MANIFEST).plan(
        ["src/qsys/live/redis_transport.py"], environment="production",
    )

    assert plan.nodes == ("linux", "windows")
    assert set(plan.components) == {
        "linux-resident", "windows-agent", "windows-gateway",
    }
    assert set(plan.windows_tasks) == {"qsys-agent-shadow", "qsys-market-gateway"}
    assert plan.unknown_paths == ()


def test_commissioning_change_targets_linux_and_windows_agent() -> None:
    plan = DeploymentManifest.load(MANIFEST).plan(
        ["src/qsys/live/commissioning.py"], environment="production",
    )

    assert plan.nodes == ("linux", "windows")
    assert set(plan.components) == {"linux-resident", "windows-agent"}
    assert plan.windows_apps == ("live_app",)
    assert plan.windows_tasks == ("qsys-agent-shadow",)
    assert plan.unknown_paths == ()


def test_docs_only_change_is_recorded_without_a_runtime_deployment() -> None:
    plan = DeploymentManifest.load(MANIFEST).plan(
        ["HANDOFF.md", "docs/deploy/linux-core.md"], environment="production",
    )

    assert plan.components == ()
    assert plan.ignored_paths == ("HANDOFF.md", "docs/deploy/linux-core.md")


def test_ci_runner_control_plane_never_deploys_to_application_nodes() -> None:
    paths = [
        ".github/ci-runner/job-started.sh",
        ".github/ci-runner/job-completed.sh",
        ".github/ci-runner/ci_runner_hook.py",
        ".github/ci-runner/install.sh",
    ]
    manifest = DeploymentManifest.load(MANIFEST)

    for environment in ("test", "production"):
        plan = manifest.plan(paths, environment=environment)
        assert plan.deployable is False
        assert plan.components == ()
        assert plan.unknown_paths == ()
        assert plan.ignored_paths == tuple(sorted(paths))
    assert plan.deployable is False


def test_structural_gate_research_module_targets_linux_data_jobs() -> None:
    plan = DeploymentManifest.load(MANIFEST).plan(
        ["src/qsys/research/structural_gate_attribution.py"],
        environment="production",
    )

    assert plan.components == ("linux-data-jobs",)
    assert plan.nodes == ("linux",)
    assert plan.unknown_paths == ()


@pytest.mark.parametrize("path", [
    "src/qsys/research/jobs.py",
    "src/qsys/research/runner.py",
    "src/qsys/research/runner_cli.py",
    "src/qsys/research/task_worker.py",
])
def test_research_runner_modules_target_an_isolated_component(path: str) -> None:
    plan = DeploymentManifest.load(MANIFEST).plan(
        [path], environment="production",
    )

    assert plan.components == ("linux-research-runner",)
    assert plan.nodes == ("linux",)
    assert plan.linux_units == ()
    assert plan.health_checks == ("preflight",)
    assert plan.unknown_paths == ()


def test_builtin_research_strategies_target_only_the_isolated_runner() -> None:
    plan = DeploymentManifest.load(MANIFEST).plan(
        ["src/qsys/research/builtin_strategies/structural_v1_2.py"],
        environment="production",
    )

    assert plan.components == ("linux-research-runner",)
    assert plan.nodes == ("linux",)
    assert plan.linux_units == ()
    assert plan.health_checks == ("preflight",)
    assert plan.unknown_paths == ()


def test_contract_change_does_not_restart_runtime_services() -> None:
    plan = DeploymentManifest.load(MANIFEST).plan(
        ["src/qsys/ops/deployment.py", "scripts/deployment_contract.py"],
        environment="production",
    )

    assert plan.components == ()
    assert plan.ignored_paths == (
        "scripts/deployment_contract.py", "src/qsys/ops/deployment.py",
    )
    assert plan.unknown_paths == ()


def test_changed_paths_includes_deleted_runtime_files(tmp_path, monkeypatch) -> None:
    contract = _contract_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "test"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"],
                   cwd=repo, check=True)
    marker = repo / "src" / "qsys" / "data" / "marker.py"
    marker.parent.mkdir(parents=True)
    marker.write_text("DRILL = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add marker"], cwd=repo, check=True,
                   capture_output=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo,
                                   text=True).strip()
    marker.unlink()
    subprocess.run(["git", "add", "-u"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "delete marker"], cwd=repo, check=True,
                   capture_output=True)
    target = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo,
                                     text=True).strip()
    monkeypatch.chdir(repo)

    assert contract._changed_paths(base, target) == [
        "src/qsys/data/marker.py",
    ]


def test_every_tracked_path_is_explicitly_classified() -> None:
    tracked = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
    tracked = [path for path in tracked if path]
    manifest = DeploymentManifest.load(MANIFEST)

    for environment in ("test", "production"):
        assert manifest.plan(tracked, environment=environment).unknown_paths == ()


def test_unknown_runtime_path_fails_closed() -> None:
    plan = DeploymentManifest.load(MANIFEST).plan(
        ["native/new_executor.rs"], environment="production",
    )

    assert plan.unknown_paths == ("native/new_executor.rs",)
    assert plan.deployable is False


@pytest.mark.parametrize("value", ["a" * 39, "a" * 7, "A" * 40, "main"])
def test_deployment_input_requires_an_exact_lowercase_sha(value: str) -> None:
    with pytest.raises(ContractError, match="full 40-character lowercase SHA"):
        require_full_sha(value, field="target")


def test_fixed_node_entrypoint_accepts_only_test_runtime() -> None:
    entrypoint = _entrypoint_module()

    entrypoint.validate_request("test", "a" * 40, ["test-runtime"])
    with pytest.raises(ValueError, match="not allowed"):
        entrypoint.validate_request("test", "a" * 40, ["windows-agent"])
    with pytest.raises(ValueError, match="not enabled"):
        entrypoint.validate_request("production", "a" * 40, ["linux-web"])


def test_test_research_artifact_extraction_rejects_unexpected_files(tmp_path) -> None:
    entrypoint = _entrypoint_module()
    archive = tmp_path / "research.zip"
    destination = tmp_path / "release"
    destination.mkdir()
    with zipfile.ZipFile(archive, "w") as value:
        value.writestr("release.json", "{}")
        value.writestr("dist/qsys-0.1.0-py3-none-any.whl", b"wheel")
        value.writestr("unexpected", b"no")

    with pytest.raises(ValueError, match="release.json and one wheel"):
        entrypoint._extract_research_archive(archive, destination)


def test_test_research_release_does_not_hardlink_shared_uv_cache() -> None:
    source = Path("deploy/linux/qsys-deploy-entrypoint.py").read_text(encoding="utf-8")

    assert 'environment["UV_LINK_MODE"] = "copy"' in source
    assert "uv dependency sync failed" in source
    assert 'environment["PYTHONDONTWRITEBYTECODE"] = "1"' in source
    assert '["chmod", "-R", "go-w", str(release)]' in source
    assert "test Runner installer failed" in source


def test_test_research_staging_paths_do_not_expand_production_install() -> None:
    manifest = DeploymentManifest.load(MANIFEST)
    installer = manifest.plan(
        ["scripts/install_test_research_runner_isolation.sh"],
        environment="production",
    )

    assert installer.components == ()
    assert installer.unknown_paths == ()
    entrypoint = _production_entrypoint_module()
    assert entrypoint._systemd_targets(
        "deploy/systemd/qsys-research-runner-test.service",
    ) == ()


def test_restricted_ssh_dispatcher_never_invokes_a_shell(monkeypatch) -> None:
    dispatcher = _dispatcher_module()
    monkeypatch.setenv(
        "SSH_ORIGINAL_COMMAND",
        "deploy --environment test --sha " + "a" * 40 + " --component test-runtime",
    )

    command = dispatcher.command_from_environment()

    assert command[0] == "/usr/local/libexec/qsys-deploy-entrypoint"
    assert command[1:] == [
        "deploy", "--environment", "test", "--sha", "a" * 40,
        "--component", "test-runtime",
    ]
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "bash -c whoami")
    with pytest.raises(ValueError, match="not allowed"):
        dispatcher.command_from_environment()

    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "attest --environment test")
    assert dispatcher.command_from_environment()[:2] == [
        "/usr/local/libexec/qsys-deploy-entrypoint", "attest",
    ]
    monkeypatch.setenv(
        "SSH_ORIGINAL_COMMAND", "research-stage --sha " + "a" * 40,
    )
    assert dispatcher.command_from_environment()[:2] == [
        "/usr/local/libexec/qsys-deploy-entrypoint", "research-stage",
    ]
    monkeypatch.setenv(
        "SSH_ORIGINAL_COMMAND", "research-drill --sha " + "a" * 40,
    )
    assert dispatcher.command_from_environment()[:2] == [
        "/usr/local/libexec/qsys-deploy-entrypoint", "research-drill",
    ]


def test_test_deploy_key_is_forced_and_rotated_atomically(tmp_path) -> None:
    installer = _key_installer_module()
    public = tmp_path / "deploy.pub"
    authorized = tmp_path / ".ssh" / "authorized_keys"
    encoded = base64.b64encode(b"a" * 32).decode()
    public.write_text(f"ssh-ed25519 {encoded} generated\n", encoding="utf-8")
    authorized.parent.mkdir()
    authorized.write_text("ssh-ed25519 YWJj existing\n", encoding="utf-8")

    installer.install(public, authorized)
    installer.install(public, authorized)

    lines = authorized.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "ssh-ed25519 YWJj existing"
    assert lines[1].startswith(
        'restrict,command="/usr/local/libexec/qsys-deploy-ssh" ssh-ed25519 '
    )
    assert lines[1].endswith(" qsys-github-testing")
    assert len(lines) == 2
    assert authorized.stat().st_mode & 0o777 == 0o600


def test_production_key_uses_a_distinct_forced_command(tmp_path) -> None:
    installer = _production_key_installer_module()
    public = tmp_path / "production.pub"
    authorized = tmp_path / "authorized_keys"
    encoded = base64.b64encode(b"p" * 32).decode()
    public.write_text(f"ssh-ed25519 {encoded} generated\n", encoding="utf-8")

    installer.install(public, authorized)

    line = authorized.read_text(encoding="utf-8").strip()
    assert line.startswith(
        'restrict,command="/usr/local/libexec/qsys-production-deploy-ssh" '
    )
    assert line.endswith(" qsys-github-production")
    assert "qsys-deploy-ssh\"" not in line


def test_production_dispatcher_rejects_shell_commands(monkeypatch) -> None:
    dispatcher = _production_dispatcher_module()
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "status")
    assert dispatcher.command_from_environment() == [
        "/usr/local/libexec/qsys-production-deploy-entrypoint", "status",
    ]
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "bash -c id")
    with pytest.raises(ValueError, match="not allowed"):
        dispatcher.command_from_environment()
    monkeypatch.setenv(
        "SSH_ORIGINAL_COMMAND",
        "windows-deploy --sha " + "a" * 40 + " --component windows-agent",
    )
    assert dispatcher.command_from_environment()[:2] == [
        "/usr/local/libexec/qsys-production-deploy-entrypoint", "windows-deploy",
    ]
    monkeypatch.setenv(
        "SSH_ORIGINAL_COMMAND",
        "component-deploy --sha " + "a" * 40 + " --component catalog-verify",
    )
    assert dispatcher.command_from_environment()[:2] == [
        "/usr/local/libexec/qsys-production-deploy-entrypoint", "component-deploy",
    ]


def test_production_requires_exact_planned_linux_components() -> None:
    entrypoint = _production_entrypoint_module()
    plan = {"components": ["linux-web", "windows-agent"], "unknown_paths": []}

    assert entrypoint._validate_components(plan, ["linux-web"]) == ["linux-web"]
    with pytest.raises(ValueError, match="do not match"):
        entrypoint._validate_components(plan, ["linux-resident"])
    with pytest.raises(ValueError, match="production Linux"):
        entrypoint._validate_components(plan, ["windows-agent"])


def test_production_requires_exact_planned_windows_components() -> None:
    entrypoint = _production_entrypoint_module()
    plan = {
        "components": ["linux-web", "windows-agent", "windows-control"],
        "unknown_paths": [],
    }

    assert entrypoint._validate_windows_components(
        plan, ["windows-control", "windows-agent"],
    ) == ["windows-agent", "windows-control"]
    with pytest.raises(ValueError, match="do not match"):
        entrypoint._validate_windows_components(plan, ["windows-agent"])
    with pytest.raises(ValueError, match="production Windows"):
        entrypoint._validate_windows_components(plan, ["linux-web"])


def test_production_windows_bootstraps_the_coordinator_from_the_target() -> None:
    source = Path("deploy/linux/qsys-production-deploy-entrypoint.py").read_text(
        encoding="utf-8",
    )

    assert 'WINDOWS_COORDINATOR_PATH = "deploy/linux/qsys-windows-deploy-coordinator.py"' in source
    assert '_git_blob(repo, args.sha, WINDOWS_COORDINATOR_PATH)' in source
    assert 'sys.executable, str(coordinator)' in source
    assert 'WINDOWS_COORDINATOR = "/usr/local/libexec/' not in source


def test_production_windows_real_result_declares_not_dry_run() -> None:
    source = Path("deploy/linux/qsys-production-deploy-entrypoint.py").read_text(
        encoding="utf-8",
    )

    assert '"ok": success, "dry_run": False,' in source


def test_production_target_requires_successful_test_tree(tmp_path) -> None:
    entrypoint = _production_entrypoint_module()
    root = tmp_path / "records"
    record_dir = root / "github-1"
    record_dir.mkdir(parents=True)
    sha = "a" * 40
    tree = "b" * 40
    (record_dir / "record.json").write_text(json.dumps({
        "schema_version": 1,
        "run_id": "github-1",
        "environment": "test",
        "status": "success",
        "source_sha": sha,
        "source_tree": tree,
        "after": {"linux": sha},
        "completed_at": "2026-10-07T00:00:00+00:00",
    }), encoding="utf-8")

    assert entrypoint._successful_test_run({"test_record_root": str(root)}, tree) == "github-1"
    with pytest.raises(ValueError, match="no successful test deployment"):
        entrypoint._successful_test_run({"test_record_root": str(root)}, "c" * 40)


def test_production_accepts_control_plane_test_attestation(tmp_path) -> None:
    entrypoint = _production_entrypoint_module()
    root = tmp_path / "records"
    record_dir = root / "github-control"
    record_dir.mkdir(parents=True)
    tree = "d" * 40
    (record_dir / "record.json").write_text(json.dumps({
        "schema_version": 1,
        "run_id": "github-control",
        "environment": "test",
        "status": "success",
        "source_sha": "a" * 40,
        "source_tree": tree,
        "components": [],
        "after": {"linux": "b" * 40},
        "completed_at": "2026-10-07T00:00:00+00:00",
    }), encoding="utf-8")

    assert entrypoint._successful_test_run({"test_record_root": str(root)}, tree) == "github-control"


def test_production_status_declares_fixed_entrypoint_capabilities(monkeypatch, tmp_path) -> None:
    entrypoint = _production_entrypoint_module()
    monkeypatch.setattr(entrypoint, "_head", lambda repo: "a" * 40)
    monkeypatch.setattr(entrypoint, "_branch", lambda repo: "main")
    monkeypatch.setattr(entrypoint, "_dirty", lambda repo: False)
    monkeypatch.setattr(entrypoint, "_active", lambda unit, repo: True)
    monkeypatch.setattr(entrypoint, "_web_healthy", lambda config: True)

    state = entrypoint.status({"production_repo": str(tmp_path)})

    assert state["entrypoint_version"] == 13
    assert set(state["capabilities"]) == {
        "systemd-apply-v1", "systemd-changed-artifacts-v1",
        "systemd-preview-v1", "systemd-rollback-v1",
        "release-checklist-v1", "controlled-failure-injection-v1",
        "successful-release-index-v1",
        "windows-baseline-plan-v1",
        "systemd-enable-state-v1",
        "monitor-shadow-state-v1",
        "monitor-shadow-command-v1",
        "monitor-research-command-v1",
        "component-release-v1",
        "component-release-v2",
        "component-release-v3",
    }


def test_component_artifact_extraction_is_exact_and_rejects_traversal(tmp_path) -> None:
    entrypoint = _production_entrypoint_module()
    archive = tmp_path / "component.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("release.json", "{}")
        output.writestr("dist/qsys-0.1.0-py3-none-any.whl", b"wheel")
        output.writestr("source/src/qsys/data/example.py", b"DATA = 1\n")
        output.writestr("source/src/qsys/strategy/example.py", b"STRATEGY = 1\n")

    destination = tmp_path / "release"
    entrypoint._extract_component_archive(archive, destination, "catalog-verify")
    assert (destination / "release.json").read_text() == "{}"
    assert (destination / "dist/qsys-0.1.0-py3-none-any.whl").read_bytes() == b"wheel"
    assert (destination / "source/src/qsys/data/example.py").is_file()

    unsafe = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(unsafe, "w") as output:
        output.writestr("release.json", "{}")
        output.writestr("dist/qsys-0.1.0-py3-none-any.whl", b"wheel")
        output.writestr("source/src/qsys/data/example.py", b"DATA = 1\n")
        output.writestr("../escape", b"bad")
    with pytest.raises(ValueError, match="unsafe path"):
        entrypoint._extract_component_archive(
            unsafe, tmp_path / "unsafe-release", "catalog-verify",
        )

    wrong_root = tmp_path / "wrong-root.zip"
    with zipfile.ZipFile(wrong_root, "w") as output:
        output.writestr("release.json", "{}")
        output.writestr("dist/qsys-0.1.0-py3-none-any.whl", b"wheel")
        output.writestr("source/src/qsys/web/example.py", b"WEB = 1\n")
    with pytest.raises(ValueError, match="manifest, one wheel and scan sources"):
        entrypoint._extract_component_archive(
            wrong_root, tmp_path / "wrong-root-release", "catalog-verify",
        )


def test_component_verifier_rejects_target_whitelist_drift(
    monkeypatch, tmp_path,
) -> None:
    entrypoint = _production_entrypoint_module()
    (tmp_path / "deploy").mkdir()
    value = json.loads(Path("deploy/component-releases.json").read_text(encoding="utf-8"))
    value["components"]["catalog-verify"]["executable"] = "venv/bin/tampered"
    (tmp_path / "deploy/component-releases.json").write_text(
        json.dumps(value), encoding="utf-8",
    )
    monkeypatch.setattr(
        entrypoint, "_run",
        lambda *args, **kwargs: pytest.fail("verification command must not run"),
    )

    with pytest.raises(ValueError, match="differs from installed whitelist"):
        entrypoint._verify_component_release(
            tmp_path, "a" * 40, "catalog-verify", tmp_path / "release",
        )


def test_component_rollback_uses_whitelisted_dropin(monkeypatch, tmp_path) -> None:
    entrypoint = _production_entrypoint_module()
    dropin = tmp_path / "systemd/example.conf"
    current = tmp_path / "component/current"
    monkeypatch.setitem(entrypoint.COMPONENT_RELEASES, "example", {
        "release_root": str(tmp_path / "releases"),
        "current": str(current),
        "dropin": str(dropin),
    })
    commands = []

    def fake_run(command, **kwargs):
        commands.append([str(item) for item in command])
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(entrypoint, "_run", fake_run)
    monkeypatch.setattr(entrypoint, "_current_component_sha", lambda component: "uninstalled")

    assert entrypoint._rollback_component("example", "uninstalled", tmp_path) is True
    assert [
        "sudo", "-n", "rm", "-f", "--", str(dropin),
    ] in commands
    assert not any("qsys-catalog-verify" in item for command in commands for item in command)


def test_component_stage_installs_wheel_with_its_standard_filename(
    monkeypatch, tmp_path,
) -> None:
    entrypoint = _production_entrypoint_module()
    sha = "a" * 40
    releases = tmp_path / "releases"
    extracted = tmp_path / "artifact"
    wheel_name = "qsys-0.1.0-py3-none-any.whl"
    (extracted / "dist").mkdir(parents=True)
    (extracted / "release.json").write_text(
        json.dumps({"wheel_filename": wheel_name}), encoding="utf-8",
    )
    (extracted / "dist" / wheel_name).write_bytes(b"wheel")
    (extracted / "source/src/qsys/data").mkdir(parents=True)
    (extracted / "source/src/qsys/data/example.py").write_text(
        "DATA = 1\n", encoding="utf-8",
    )
    monkeypatch.setitem(entrypoint.COMPONENT_RELEASES, "catalog-verify", {
        "release_root": releases,
        "current": tmp_path / "current",
        "executable": "venv/bin/qsys-catalog-verify",
        "installer": "unused",
    })
    monkeypatch.setattr(entrypoint, "_verify_component_release", lambda *args: None)
    commands: list[list[str]] = []
    command_environments: list[dict[str, str] | None] = []

    def run(command, **kwargs):
        command = [str(item) for item in command]
        commands.append(command)
        command_environments.append(kwargs.get("env"))
        if command[:3] == ["sudo", "-n", "install"] and "-d" in command:
            for item in command[command.index("-m") + 2:]:
                Path(item).mkdir(parents=True, exist_ok=True)
        elif command[:3] == ["sudo", "-n", "install"]:
            shutil.copyfile(command[-2], command[-1])
        elif command[1:3] == ["sync", "--frozen"]:
            executable = releases / sha / "venv/bin/qsys-catalog-verify"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o755)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(entrypoint, "_run", run)
    release = entrypoint._stage_component_release(
        {"uv_bin": "/usr/bin/uv"}, tmp_path, sha, "catalog-verify", extracted,
    )

    assert release == releases / sha
    assert [
        "sudo", "-n", "chown", "-R", "ubuntu:ubuntu", str(release),
    ] in commands
    sync_index = next(
        index for index, command in enumerate(commands)
        if command[1:3] == ["sync", "--frozen"]
    )
    assert command_environments[sync_index]["UV_LINK_MODE"] == "copy"
    assert command_environments[sync_index]["UV_PROJECT_ENVIRONMENT"] == str(
        release / "venv"
    )
    pip_index = next(
        index for index, command in enumerate(commands)
        if command[1:3] == ["pip", "install"]
    )
    pip_command = commands[pip_index]
    assert pip_command[-1] == str(release / "dist" / wheel_name)
    assert command_environments[pip_index] is command_environments[sync_index]


def test_production_failure_detail_keeps_captured_stderr() -> None:
    entrypoint = _production_entrypoint_module()
    error = subprocess.CalledProcessError(
        2, ["uv", "sync"], output="less useful stdout", stderr="hardlink EPERM",
    )

    detail = entrypoint._failure_detail(error)

    assert "returned non-zero exit status 2" in detail
    assert "hardlink EPERM" in detail
    assert "less useful stdout" not in detail


def test_live_identity_binds_pid_start_time_and_checkout(monkeypatch, tmp_path) -> None:
    entrypoint = _production_entrypoint_module()
    monkeypatch.setattr(entrypoint, "_head", lambda repo: "a" * 40)
    monkeypatch.setattr(
        entrypoint, "_run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0,
            "ActiveState=active\nMainPID=4321\n"
            "ExecMainStartTimestampMonotonic=123456789\n", "",
        ),
    )

    assert entrypoint._live_identity(tmp_path) == {
        "pid": "4321", "started_monotonic": "123456789", "repo_sha": "a" * 40,
    }


def test_component_deploy_dry_run_proves_live_without_reading_artifact(
    monkeypatch, tmp_path, capsys,
) -> None:
    entrypoint = _production_entrypoint_module()
    sha = "a" * 40
    monkeypatch.setattr(entrypoint, "_branch", lambda repo: "main")
    monkeypatch.setattr(entrypoint, "_dirty", lambda repo: False)
    monkeypatch.setattr(entrypoint, "_head", lambda repo: "b" * 40)
    monkeypatch.setattr(entrypoint, "_fetch_and_validate_target", lambda repo, value: "c" * 40)
    monkeypatch.setattr(entrypoint, "_successful_test_run", lambda config, tree: "test-1")
    monkeypatch.setattr(entrypoint, "_current_component_sha", lambda component: "uninstalled")
    monkeypatch.setattr(entrypoint, "_live_identity", lambda repo: {
        "pid": "42", "started_monotonic": "99", "repo_sha": "b" * 40,
    })
    monkeypatch.setattr(entrypoint, "_live_preflight", lambda repo: None)
    monkeypatch.setattr(entrypoint, "_fingerprints", lambda config, repo: {"linux": "f"})
    args = argparse.Namespace(
        sha=sha, component="catalog-verify", artifact_sha256="", run_id="release-1",
        actor="maintainer", repository="owner/repo", pr_number=302, dry_run=True,
    )

    assert entrypoint.component_deploy(
        args, {
            "production_repo": str(tmp_path), "test_record_root": str(tmp_path),
            "production_record_root": str(tmp_path / "records"),
        },
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["live_restart"] is False
    assert result["live_identity"]["pid"] == "42"


def test_component_activation_failure_rolls_back_and_preserves_live_identity(
    monkeypatch, tmp_path, capsys,
) -> None:
    entrypoint = _production_entrypoint_module()
    target = "a" * 40
    production = "b" * 40
    previous = "c" * 40
    live = {"pid": "42", "started_monotonic": "99", "repo_sha": production}
    records = []
    monkeypatch.setattr(entrypoint, "_branch", lambda repo: "main")
    monkeypatch.setattr(entrypoint, "_dirty", lambda repo: False)
    monkeypatch.setattr(entrypoint, "_head", lambda repo: production)
    monkeypatch.setattr(entrypoint, "_fetch_and_validate_target", lambda repo, sha: "d" * 40)
    monkeypatch.setattr(entrypoint, "_successful_test_run", lambda config, tree: "test-1")
    monkeypatch.setattr(entrypoint, "_current_component_sha", lambda component: previous)
    monkeypatch.setattr(entrypoint, "_live_identity", lambda repo: dict(live))
    monkeypatch.setattr(entrypoint, "_live_preflight", lambda repo: None)
    monkeypatch.setattr(entrypoint, "_fingerprints", lambda config, repo: {"linux": "f"})
    monkeypatch.setattr(
        entrypoint, "_receive_component_archive",
        lambda digest, root: root / "component.zip",
    )
    monkeypatch.setattr(entrypoint, "_extract_component_archive", lambda *args: None)
    monkeypatch.setattr(entrypoint, "_verify_component_release", lambda *args: None)
    monkeypatch.setattr(entrypoint, "_stage_component_release", lambda *args: tmp_path / target)
    monkeypatch.setattr(entrypoint, "_rollback_component", lambda *args: True)

    def fake_run(command, **kwargs):
        if entrypoint.GENERIC_COMPONENT_INSTALLER in [str(item) for item in command]:
            raise subprocess.CalledProcessError(1, command, stderr="activation failed")
        return subprocess.CompletedProcess(command, 0, "", "")

    def write_record(config, run_id, record):
        records.append(record)
        return tmp_path / "record.json"

    monkeypatch.setattr(entrypoint, "_run", fake_run)
    monkeypatch.setattr(entrypoint, "_write_record", write_record)
    args = argparse.Namespace(
        sha=target, component="catalog-verify", artifact_sha256="e" * 64,
        run_id="release-rollback", actor="maintainer", repository="owner/repo",
        pr_number=366, dry_run=False,
    )

    assert entrypoint.component_deploy(
        args, {
            "production_repo": str(tmp_path), "test_record_root": str(tmp_path),
            "production_record_root": str(tmp_path / "records"),
        },
    ) == 1

    assert records[0]["status"] == "rolled_back"
    assert records[0]["before"] == records[0]["after"]
    assert records[0]["rollback_targets"] == {"catalog-verify": previous}
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "rolled_back"
    assert "activation failed" in result["error"]


def test_windows_plans_are_merged_from_each_windows_baseline() -> None:
    entrypoint = _production_entrypoint_module()
    first = {
        "changed_paths": ["src/a.py"], "ignored_paths": [],
        "components": ["windows-agent"], "nodes": ["windows"],
        "linux_units": [], "windows_apps": ["live_app"],
        "windows_tasks": ["qsys-agent-shadow"],
        "health_checks": ["agent-heartbeat"], "unknown_paths": [],
    }
    second = {
        "changed_paths": ["src/b.py"], "ignored_paths": [],
        "components": ["windows-gateway"], "nodes": ["windows"],
        "linux_units": [], "windows_apps": ["paper_app"],
        "windows_tasks": ["qsys-market-gateway"],
        "health_checks": ["market-heartbeat"], "unknown_paths": [],
    }

    merged = entrypoint._merge_plans([first, second])

    assert merged["components"] == ["windows-agent", "windows-gateway"]
    assert merged["windows_apps"] == ["live_app", "paper_app"]
    assert entrypoint._validate_windows_components(merged, ["auto"]) == [
        "windows-agent", "windows-gateway",
    ]


def test_failure_injection_requires_explicit_drill_identity() -> None:
    entrypoint = _production_entrypoint_module()
    valid = argparse.Namespace(
        failure_point="linux-health", run_id="drill-156-linux",
        drill_confirmation="DRILL_ROLLBACK",
    )

    entrypoint._validate_drill(valid, expected="linux-health")
    valid.run_id = "release-156-linux"
    with pytest.raises(ValueError, match="requires a drill run-id"):
        entrypoint._validate_drill(valid, expected="linux-health")


def test_success_history_indexes_latest_ten_and_last_thirty_days(tmp_path) -> None:
    entrypoint = _production_entrypoint_module()
    root = tmp_path / "github"
    now = datetime.now(UTC)
    for index in range(12):
        folder = root / f"release-{index}"
        folder.mkdir(parents=True)
        completed = now - timedelta(days=index * 4)
        (folder / "record.json").write_text(json.dumps({
            "run_id": f"release-{index}", "status": "success",
            "completed_at": completed.isoformat(), "source_sha": f"{index:040x}",
            "after": {"linux": f"{index:040x}"},
        }), encoding="utf-8")
    failed = root / "failed"
    failed.mkdir()
    (failed / "record.json").write_text(json.dumps({
        "run_id": "failed", "status": "failure", "completed_at": now.isoformat(),
    }), encoding="utf-8")

    history = entrypoint.deployment_history({"production_record_root": str(root)})

    assert len(history["latest_successes"]) == 10
    assert [item["run_id"] for item in history["latest_successes"][:2]] == [
        "release-0", "release-1",
    ]
    assert all(
        datetime.fromisoformat(item["completed_at"]) >= now - timedelta(days=30)
        for item in history["successes_within_30_days"]
    )
    assert history["policy"] == {
        "minimum_successes": 10, "minimum_days": 30, "records_pruned": False,
    }


def test_systemd_artifact_mapping_is_explicit_and_excludes_site_templates(
    monkeypatch, tmp_path,
) -> None:
    entrypoint = _production_entrypoint_module()
    unit_root = tmp_path / "etc" / "systemd" / "system"
    qsys_etc = tmp_path / "etc" / "qsys"
    monkeypatch.setattr(entrypoint, "SYSTEMD_UNIT_ROOT", unit_root)
    monkeypatch.setattr(entrypoint, "QSYS_ETC_ROOT", qsys_etc)

    assert entrypoint._systemd_targets("deploy/systemd/qsys-daily-archive.timer") == (
        unit_root / "qsys-daily-archive.timer",
    )
    assert entrypoint._systemd_targets("deploy/systemd/qsys-market-data.env") == (
        qsys_etc / "market-data.env",
    )
    assert entrypoint._systemd_targets(
        "deploy/systemd/qsys-limit-up-shadow-web.conf"
    ) == (
        unit_root / "qsys-monitor.service.d" / "limit-up-shadow.conf",
    )
    assert entrypoint._systemd_targets(
        "deploy/systemd/qsys-zz-limit-up-shadow-web.conf"
    ) == (
        unit_root / "qsys-monitor.service.d" / "zz-limit-up-shadow.conf",
    )
    assert entrypoint._systemd_targets("deploy/systemd/qsys-web-test.service") == ()
    assert entrypoint._systemd_targets("deploy/systemd/qsys-shadow-core.service") == ()
    assert entrypoint._systemd_targets("deploy/systemd/qsys-live-core.service") == ()
    assert len(entrypoint._systemd_targets("deploy/systemd/qsys-market-data.conf")) == 4


def test_systemd_artifacts_only_include_changed_deployable_files(
    monkeypatch, tmp_path,
) -> None:
    entrypoint = _production_entrypoint_module()
    repo = tmp_path / "repo"
    source_root = repo / "deploy" / "systemd"
    source_root.mkdir(parents=True)
    changed = source_root / "qsys-example.timer"
    changed.write_text("[Timer]\nOnCalendar=09:24\n", encoding="utf-8")
    unrelated = source_root / "qsys-unrelated.service"
    unrelated.write_text("[Service]\nExecStart=/bin/false\n", encoding="utf-8")
    target_root = tmp_path / "etc" / "systemd" / "system"
    monkeypatch.setattr(entrypoint, "SYSTEMD_UNIT_ROOT", target_root)
    monkeypatch.setattr(entrypoint, "QSYS_ETC_ROOT", tmp_path / "etc" / "qsys")

    artifacts = entrypoint._systemd_artifacts(
        repo, ["deploy/systemd/qsys-example.timer"],
    )

    assert artifacts == {target_root / changed.name: changed}
    assert target_root / unrelated.name not in artifacts


def test_limit_up_shadow_web_dropin_restarts_monitor(monkeypatch, tmp_path) -> None:
    entrypoint = _production_entrypoint_module()
    unit_root = tmp_path / "etc" / "systemd" / "system"
    source = tmp_path / "zz-limit-up-shadow.conf"
    source.write_text("[Service]\nReadOnlyPaths=/data/qsys/var-prod\n", encoding="utf-8")
    target = unit_root / "qsys-monitor.service.d" / source.name
    monkeypatch.setattr(entrypoint, "SYSTEMD_UNIT_ROOT", unit_root)

    assert entrypoint._systemd_restart_units({target: source}) == [
        "qsys-monitor.service",
    ]


def test_limit_up_shadow_web_dropin_verifies_effective_execstart(
    monkeypatch, tmp_path,
) -> None:
    entrypoint = _production_entrypoint_module()
    target = (
        tmp_path / "qsys-monitor.service.d" / "zz-limit-up-shadow.conf"
    )
    target.parent.mkdir(parents=True)
    target.write_text("[Service]\n", encoding="utf-8")
    artifacts = {target: target}

    def effective(command, *, cwd, check=True):
        return subprocess.CompletedProcess(
            command, 0,
            "python scripts/run_monitor.py "
            "--limit-up-shadow-state /data/qsys/var-prod/limit-up-shadow.sqlite3 "
            "--limit-up-research-run /data/qsys/research/issue-210/run-20261010\n",
            "",
        )

    monkeypatch.setattr(entrypoint, "_run", effective)
    entrypoint._verify_systemd_artifacts(artifacts, [], {}, tmp_path)

    def overridden(command, *, cwd, check=True):
        return subprocess.CompletedProcess(
            command, 0, "python scripts/run_monitor.py\n", "",
        )

    monkeypatch.setattr(entrypoint, "_run", overridden)
    with pytest.raises(ValueError, match="effective ExecStart lacks"):
        entrypoint._verify_systemd_artifacts(artifacts, [], {}, tmp_path)


def test_systemd_artifacts_fail_closed_for_only_excluded_template(
    monkeypatch, tmp_path,
) -> None:
    entrypoint = _production_entrypoint_module()
    source_root = tmp_path / "repo" / "deploy" / "systemd"
    source_root.mkdir(parents=True)
    (source_root / "qsys-shadow-core.service").write_text(
        "[Service]\nExecStart=/bin/true\n", encoding="utf-8",
    )
    monkeypatch.setattr(entrypoint, "SYSTEMD_UNIT_ROOT", tmp_path / "etc")

    with pytest.raises(ValueError, match="no deployable production artifact changed"):
        entrypoint._systemd_artifacts(
            tmp_path / "repo", ["deploy/systemd/qsys-shadow-core.service"],
        )


def test_systemd_preview_reports_dynamic_restart_and_inactive_units(
    monkeypatch, tmp_path,
) -> None:
    entrypoint = _production_entrypoint_module()
    source = tmp_path / "source.timer"
    source.write_text("[Timer]\nOnCalendar=09:24\n", encoding="utf-8")
    active_target = tmp_path / "qsys-active.timer"
    inactive_target = tmp_path / "qsys-inactive.timer"
    active_target.write_text("[Timer]\nOnCalendar=08:58\n", encoding="utf-8")
    inactive_target.write_text("[Timer]\nOnCalendar=08:58\n", encoding="utf-8")
    artifacts = {active_target: source, inactive_target: source}
    monkeypatch.setattr(entrypoint, "SYSTEMD_UNIT_ROOT", tmp_path)
    monkeypatch.setattr(entrypoint, "_systemd_artifacts", lambda repo, paths: artifacts)
    monkeypatch.setattr(entrypoint, "_active", lambda unit, repo: unit == active_target.name)

    def fake_run(command, *, cwd, check=True):
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(entrypoint, "_run", fake_run)

    preview = entrypoint._preview_systemd(
        tmp_path, "a" * 40, ["deploy/systemd/qsys-active.timer"],
    )

    assert preview == {
        "targets": sorted((str(active_target), str(inactive_target))),
        "restart_units": [active_target.name],
        "inactive_units": [inactive_target.name],
        "enable_units": [],
    }


def test_systemd_verify_rejects_repository_unit_not_installed_to_etc(tmp_path) -> None:
    entrypoint = _production_entrypoint_module()
    source = tmp_path / "repo.timer"
    target = tmp_path / "etc.timer"
    source.write_text("OnCalendar=09:24\n", encoding="utf-8")
    target.write_text("OnCalendar=08:58\n", encoding="utf-8")

    with pytest.raises(ValueError, match="differs from target"):
        entrypoint._verify_systemd_artifacts({target: source}, [], {}, tmp_path)


def _fake_systemd_run(command, *, cwd, check=True):
    if command[:4] == ["sudo", "-n", "install", "-D"]:
        source, target = Path(command[-2]), Path(command[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return subprocess.CompletedProcess(command, 0, "", "")
    if command[:4] == ["sudo", "-n", "rm", "-f"]:
        Path(command[-1]).unlink(missing_ok=True)
        return subprocess.CompletedProcess(command, 0, "", "")
    if command[:2] == ["systemctl", "is-active"]:
        return subprocess.CompletedProcess(command, 0, "active\n", "")
    if command[:2] == ["systemctl", "show"]:
        value = "loaded\n" if "--property=LoadState" in command else "simple\n"
        return subprocess.CompletedProcess(command, 0, value, "")
    return subprocess.CompletedProcess(command, 0, "", "")


def test_systemd_apply_installs_reloads_verifies_and_preserves_backup(
    monkeypatch, tmp_path,
) -> None:
    entrypoint = _production_entrypoint_module()
    repo = tmp_path / "repo"
    source_root = repo / "deploy" / "systemd"
    source_root.mkdir(parents=True)
    source = source_root / "qsys-example.timer"
    source.write_text("[Timer]\nOnCalendar=09:24\n", encoding="utf-8")
    target_root = tmp_path / "etc" / "systemd" / "system"
    target_root.mkdir(parents=True)
    target = target_root / source.name
    target.write_text("[Timer]\nOnCalendar=08:58\n", encoding="utf-8")
    monkeypatch.setattr(entrypoint, "SYSTEMD_UNIT_ROOT", target_root)
    monkeypatch.setattr(entrypoint, "QSYS_ETC_ROOT", tmp_path / "etc" / "qsys")
    monkeypatch.setattr(entrypoint, "_run", _fake_systemd_run)
    config = {"production_record_root": str(tmp_path / "records" / "github")}

    check, backup = entrypoint._apply_systemd(
        config, repo, "github-1", ["deploy/systemd/qsys-example.timer"],
    )

    assert check["name"] == "systemd-verify" and check["status"] == "passed"
    assert target.read_bytes() == source.read_bytes()
    assert (backup / target.relative_to("/")).read_text(encoding="utf-8").endswith(
        "OnCalendar=08:58\n"
    )
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["active_units"] == {"qsys-example.timer": True}
    assert manifest["enabled_units"] == {"qsys-example.timer": False}


def test_systemd_apply_enables_and_starts_declared_new_timer(
    monkeypatch, tmp_path,
) -> None:
    entrypoint = _production_entrypoint_module()
    repo = tmp_path / "repo"
    source_root = repo / "deploy" / "systemd"
    source_root.mkdir(parents=True)
    source = source_root / "qsys-new.timer"
    source.write_text("[Timer]\nOnCalendar=19:35\n", encoding="utf-8")
    target_root = tmp_path / "etc" / "systemd" / "system"
    state = {"active": False, "enabled": False}
    commands: list[list[str]] = []

    def fake_run(command, *, cwd, check=True):
        commands.append(command)
        if command[:4] == ["sudo", "-n", "install", "-D"]:
            target = Path(command[-1])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(command[-2]), target)
        elif command[:5] == ["sudo", "-n", "systemctl", "enable", "--now"]:
            state.update(active=True, enabled=True)
        elif command[:2] == ["systemctl", "show"]:
            return subprocess.CompletedProcess(command, 0, "loaded\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(entrypoint, "SYSTEMD_UNIT_ROOT", target_root)
    monkeypatch.setattr(entrypoint, "QSYS_ETC_ROOT", tmp_path / "etc" / "qsys")
    monkeypatch.setattr(entrypoint, "SYSTEMD_ENABLE_ON_INSTALL", frozenset({source.name}))
    monkeypatch.setattr(entrypoint, "_active", lambda unit, cwd: state["active"])
    monkeypatch.setattr(entrypoint, "_enabled", lambda unit, cwd: state["enabled"])
    monkeypatch.setattr(entrypoint, "_run", fake_run)

    check, backup = entrypoint._apply_systemd(
        {"production_record_root": str(tmp_path / "records" / "github")},
        repo,
        "github-enable",
        ["deploy/systemd/qsys-new.timer"],
    )

    assert check["status"] == "passed"
    assert ["sudo", "-n", "systemctl", "enable", "--now", source.name] in commands
    assert state == {"active": True, "enabled": True}
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["active_units"] == {source.name: False}
    assert manifest["enabled_units"] == {source.name: False}


def test_systemd_apply_failure_restores_new_timer_disabled_and_inactive(
    monkeypatch, tmp_path,
) -> None:
    entrypoint = _production_entrypoint_module()
    repo = tmp_path / "repo"
    source_root = repo / "deploy" / "systemd"
    source_root.mkdir(parents=True)
    source = source_root / "qsys-new.timer"
    source.write_text("[Timer]\nOnCalendar=19:35\n", encoding="utf-8")
    target_root = tmp_path / "etc" / "systemd" / "system"
    state = {"active": False, "enabled": False}
    failed = False

    def fake_run(command, *, cwd, check=True):
        nonlocal failed
        if command[:4] == ["sudo", "-n", "install", "-D"]:
            target = Path(command[-1])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(command[-2]), target)
        elif command[:4] == ["sudo", "-n", "rm", "-f"]:
            Path(command[-1]).unlink(missing_ok=True)
        elif command[:5] == ["sudo", "-n", "systemctl", "enable", "--now"]:
            state.update(active=True, enabled=True)
        elif command[:4] == ["sudo", "-n", "systemctl", "disable"]:
            state["enabled"] = False
        elif command[:4] == ["sudo", "-n", "systemctl", "stop"]:
            state["active"] = False
        elif command[0] == "systemd-analyze" and not failed:
            failed = True
            raise subprocess.CalledProcessError(1, command, stderr="invalid unit")
        elif command[:2] == ["systemctl", "show"]:
            return subprocess.CompletedProcess(command, 0, "loaded\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(entrypoint, "SYSTEMD_UNIT_ROOT", target_root)
    monkeypatch.setattr(entrypoint, "QSYS_ETC_ROOT", tmp_path / "etc" / "qsys")
    monkeypatch.setattr(entrypoint, "SYSTEMD_ENABLE_ON_INSTALL", frozenset({source.name}))
    monkeypatch.setattr(entrypoint, "_active", lambda unit, cwd: state["active"])
    monkeypatch.setattr(entrypoint, "_enabled", lambda unit, cwd: state["enabled"])
    monkeypatch.setattr(entrypoint, "_run", fake_run)

    with pytest.raises(RuntimeError, match="rollback=succeeded"):
        entrypoint._apply_systemd(
            {"production_record_root": str(tmp_path / "records" / "github")},
            repo,
            "github-enable-failure",
            ["deploy/systemd/qsys-new.timer"],
        )

    assert state == {"active": False, "enabled": False}
    assert not (target_root / source.name).exists()


def test_systemd_verify_failure_restores_installed_files(monkeypatch, tmp_path) -> None:
    entrypoint = _production_entrypoint_module()
    repo = tmp_path / "repo"
    source_root = repo / "deploy" / "systemd"
    source_root.mkdir(parents=True)
    source = source_root / "qsys-example.service"
    source.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")
    target_root = tmp_path / "etc" / "systemd" / "system"
    target_root.mkdir(parents=True)
    target = target_root / source.name
    old = b"[Service]\nExecStart=/bin/false\n"
    target.write_bytes(old)
    monkeypatch.setattr(entrypoint, "SYSTEMD_UNIT_ROOT", target_root)
    monkeypatch.setattr(entrypoint, "QSYS_ETC_ROOT", tmp_path / "etc" / "qsys")
    failed = False

    def fail_verify_once(command, *, cwd, check=True):
        nonlocal failed
        result = _fake_systemd_run(command, cwd=cwd, check=check)
        if command[0] == "systemd-analyze" and not failed:
            failed = True
            raise subprocess.CalledProcessError(1, command, stderr="invalid unit")
        return result

    monkeypatch.setattr(entrypoint, "_run", fail_verify_once)
    config = {"production_record_root": str(tmp_path / "records" / "github")}

    with pytest.raises(RuntimeError, match="rollback=succeeded"):
        entrypoint._apply_systemd(
            config, repo, "github-2", ["deploy/systemd/qsys-example.service"],
        )
    assert target.read_bytes() == old


def test_failed_deployment_rollback_restores_code_dependencies_and_service(monkeypatch, tmp_path) -> None:
    entrypoint = _entrypoint_module()
    calls: list[list[str]] = []

    def fake_run(command, *, cwd, check=True):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "active\n", "")

    monkeypatch.setattr(entrypoint, "_run", fake_run)
    monkeypatch.setattr(entrypoint, "_healthy", lambda config, repo: True)
    config = {"uv_bin": "/opt/uv", "test_service": "qsys-web-test.service"}

    assert entrypoint._rollback(config, tmp_path, "b" * 40) is True
    assert calls == [
        ["git", "reset", "--hard", "b" * 40],
        ["/opt/uv", "sync", "--frozen"],
        ["sudo", "-n", "systemctl", "restart", "qsys-web-test.service"],
    ]


def test_test_gateway_loads_deployer_from_exact_verified_target(monkeypatch, tmp_path) -> None:
    entrypoint = _entrypoint_module()
    sha = "a" * 40
    commands: list[list[str]] = []

    def fake_run(command, *, cwd, check=True):
        commands.append(command)
        stdout = sha if command == ["git", "rev-parse", "origin/test"] else "deployer\n"
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(entrypoint, "_run", fake_run)

    assert entrypoint._target_deployer(tmp_path, sha) == "deployer\n"
    assert commands == [
        ["git", "fetch", "--no-tags", "origin", "test"],
        ["git", "rev-parse", "origin/test"],
        ["git", "show", f"{sha}:scripts/deploy_test_linux.sh"],
    ]


def test_test_gateway_rejects_deployer_from_moved_target(monkeypatch, tmp_path) -> None:
    entrypoint = _entrypoint_module()

    def fake_run(command, *, cwd, check=True):
        return subprocess.CompletedProcess(command, 0, "b" * 40, "")

    monkeypatch.setattr(entrypoint, "_run", fake_run)

    with pytest.raises(ValueError, match="origin/test is"):
        entrypoint._target_deployer(tmp_path, "a" * 40)


def test_rollback_health_wait_tolerates_service_startup(monkeypatch, tmp_path) -> None:
    entrypoint = _entrypoint_module()
    states = iter((False, False, True))
    sleeps: list[int] = []
    monkeypatch.setattr(entrypoint, "_healthy", lambda config, repo: next(states))
    monkeypatch.setattr(entrypoint.time, "sleep", sleeps.append)

    assert entrypoint._wait_healthy({}, tmp_path, attempts=3) is True
    assert sleeps == [1, 1]


def test_privileged_workflow_uses_trusted_main_controller() -> None:
    workflow = Path(".github/workflows/deploy-test.yml").read_text(encoding="utf-8")

    assert "github.event.workflow_run.event == 'push'" in workflow
    assert "github.event.workflow_run.head_branch == 'test'" in workflow
    assert "ref: main" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "TEST_DEPLOY_SSH_KEY" in workflow
    assert 'git fetch --no-tags origin "$BEFORE_SHA"' in workflow
    assert "Attest control-plane-only target" in workflow
    assert "scripts/release_evidence.py create-deployment" in workflow
    assert "CI_CHECK_SUITE_ID" in workflow
    assert 'record["source_tree"]' in workflow


def test_post_merge_alignment_is_evidence_bound_and_lease_safe() -> None:
    workflow = Path(".github/workflows/align-test-after-main.yml").read_text(
        encoding="utf-8",
    )

    assert "contents: write" in workflow
    assert 'merge_commit_sha == $main' in workflow
    assert "qsys-test-release-evidence" in workflow
    assert "scripts/release_evidence.py validate" in workflow
    assert "ci_check_suite_id" in workflow
    assert "event=pull_request" in workflow
    assert "record --environment test" in workflow
    assert "scripts/check_secrets.py" in workflow
    assert "scripts/check_repo_policy.py" in workflow
    assert '--force-with-lease="refs/heads/test:$PR_HEAD"' in workflow
    assert "actions: write" in workflow
    assert "gh workflow run ci.yml --ref test" in workflow


def test_pr_ci_reuses_valid_tree_evidence_or_runs_full_checks() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "reuse-evidence:" in workflow
    assert "ref: ${{ github.event.pull_request.base.sha }}" in workflow
    assert ".trusted-controller/scripts/verify_github_release_evidence.py" in workflow
    assert '--checkout "$GITHUB_WORKSPACE"' in workflow
    assert "Evidence unavailable or mismatched; full PR verification is required." in workflow
    assert "needs.reuse-evidence.outputs.reusable != 'true'" in workflow
    assert "Reuse full-suite evidence for the identical tree" in workflow
    assert "Reuse package evidence for the identical tree" in workflow


def test_production_workflow_is_manual_and_fail_closed() -> None:
    workflow = Path(".github/workflows/deploy-production-linux.yml").read_text(
        encoding="utf-8",
    )

    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
    assert "ref: main" in workflow
    assert "DEPLOY_PRODUCTION" in workflow
    assert "git merge-base --is-ancestor" in workflow
    assert "actions/workflows/ci.yml/runs?head_sha=$pr_head&event=pull_request" in workflow
    assert "check-suites/$check_suite_id/check-runs?per_page=100" in workflow
    assert '.status == "completed" and .conclusion == "success"' in workflow
    assert "PRODUCTION_DEPLOY_SSH_KEY" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "production entrypoint lacks required deployment capabilities" in workflow
    assert "successful-release-index-v1" in workflow
    assert "release=$RELEASE_ID node=linux" in workflow
    assert "DRILL_ROLLBACK" in workflow


def test_windows_workflow_uses_only_the_linux_production_credential() -> None:
    workflow = Path(".github/workflows/deploy-production-windows.yml").read_text(
        encoding="utf-8",
    )

    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
    assert "ref: main" in workflow
    assert "DEPLOY_WINDOWS" in workflow
    assert "windows-deploy" in workflow
    assert "PRODUCTION_DEPLOY_SSH_KEY" in workflow
    assert "WINDOWS_DEPLOY_SSH_KEY" not in workflow
    assert "cancel-in-progress: false" in workflow
    assert "set -euo pipefail" in workflow
    assert "jq -e '.ok == true' result.json" in workflow
    assert "production entrypoint lacks required deployment capabilities" in workflow
    assert "successful-release-index-v1" in workflow
    assert "windows-baseline-plan-v1" in workflow
    assert "release=$RELEASE_ID node=windows" in workflow
    assert "DRILL_ROLLBACK" in workflow


def test_component_workflow_streams_only_a_verified_artifact() -> None:
    workflow = Path(".github/workflows/deploy-production-component.yml").read_text(
        encoding="utf-8",
    )

    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
    assert "DEPLOY_COMPONENT" in workflow
    assert "qsys-$COMPONENT-$TARGET_SHA" in workflow
    assert '.event == "push" or .event == "workflow_dispatch"' in workflow
    assert "actions/artifacts/$ARTIFACT_ID/zip" in workflow
    assert "component-deploy --sha $TARGET_SHA" in workflow
    assert "<component.zip" in workflow
    assert '.before["live-core-pid"] == .after["live-core-pid"]' in workflow
    assert '.before["live-core-started"] == .after["live-core-started"]' in workflow
    assert ".before.linux == .after.linux" in workflow
    assert "PRODUCTION_DEPLOY_SSH_KEY" in workflow
    assert "cancel-in-progress: false" in workflow
    assert 'required = {"component-release-v3"}' in workflow
    assert 'entrypoint_version", 0) < 13' in workflow


def test_test_research_workflow_streams_ci_artifact_without_activation() -> None:
    workflow = Path(".github/workflows/stage-test-research-runner.yml").read_text(
        encoding="utf-8",
    )

    assert "workflow_dispatch:" in workflow
    assert "STAGE_TEST_RESEARCH" in workflow
    assert "qsys-research-$TARGET_SHA" in workflow
    assert "research-stage --sha $TARGET_SHA" in workflow
    assert "<research.zip" in workflow
    assert "ServerAliveInterval=30" in workflow
    assert "ServerAliveCountMax=30" in workflow
    assert "research-stage-v1" in workflow
    assert ".before.linux == .after.linux" in workflow
    assert "Runner activation: never performed" in workflow


def test_test_research_drill_is_manual_bounded_and_preserves_service_identity() -> None:
    workflow = Path(".github/workflows/drill-test-research-runner.yml").read_text(
        encoding="utf-8",
    )

    assert "workflow_dispatch:" in workflow
    assert "DRILL_TEST_RESEARCH" in workflow
    assert "research-drill --sha $TARGET_SHA" in workflow
    assert "research-drill-v1" in workflow
    assert "timeout-minutes: 45" in workflow
    assert "ServerAliveInterval=30" in workflow
    assert "ServerAliveCountMax=90" in workflow
    assert '.before["live-core"] == .after["live-core"]' in workflow
    assert '.before["test-web"] == .after["test-web"]' in workflow
    assert "Runner activation after drill: forbidden" in workflow


def test_production_entrypoint_installer_preserves_bootstrap_backup() -> None:
    installer = Path("scripts/install_production_deployment_entrypoint.sh").read_text(
        encoding="utf-8",
    )

    assert "/data/qsys/var-deployments/bootstrap-backups" in installer
    assert "cp -p" in installer
    assert "/etc/qsys/component-releases.json" in installer
    assert "deploy/component-releases.json" in installer
    assert installer.index("deploy/linux/production-deployment-entrypoint.json") < installer.index(
        "deploy/linux/qsys-production-deploy-entrypoint.py"
    )
    assert installer.index("deploy/component-releases.json") < installer.index(
        "deploy/linux/qsys-production-deploy-entrypoint.py"
    )


def test_windows_control_targets_both_independent_apps() -> None:
    plan = DeploymentManifest.load(MANIFEST).plan(
        ["scripts/check_agent_alive.py"], environment="production",
    )

    assert plan.components == ("linux-data-jobs", "windows-control")
    assert plan.windows_apps == ("live_app", "paper_app")
    assert plan.windows_tasks == ("qsys-watchdog",)


def test_windows_coordinator_config_is_readable_by_forced_command_user() -> None:
    installer = Path("scripts/install_windows_deployment_coordinator.sh").read_text(
        encoding="utf-8",
    )

    assert "-m 0644" in installer
    assert "-m 0600" not in installer


def test_repeated_test_deployment_is_a_noop(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = Path("scripts/deploy_test_linux.sh").read_text(encoding="utf-8")
    (repo / "deploy.sh").write_text(script, encoding="utf-8")
    subprocess.run(["git", "init", "-b", "test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "deploy.sh"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
         "commit", "-m", "fixture"],
        cwd=repo, check=True, capture_output=True,
    )
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    completed = subprocess.run(
        ["/bin/sh", "deploy.sh", sha], cwd=repo, text=True, capture_output=True,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == f"test deployment already at target: {sha}"


def test_test_deployment_switches_to_the_verified_exact_sha() -> None:
    script = Path("scripts/deploy_test_linux.sh").read_text(encoding="utf-8")

    assert 'remote_sha=$(git rev-parse origin/test)' in script
    assert 'if [ "$remote_sha" != "$target_sha" ]; then' in script
    assert 'git reset --hard "$target_sha"' in script
    assert "git merge --ff-only" not in script


def test_test_deployment_installs_and_rolls_back_its_web_unit() -> None:
    script = Path("scripts/deploy_test_linux.sh").read_text(encoding="utf-8")

    assert 'unit_target=/etc/systemd/system/qsys-web-test.service' in script
    assert 'systemd-analyze verify "$repo/deploy/systemd/qsys-web-test.service"' in script
    assert '"$repo/deploy/systemd/qsys-web-test.service" "$unit_target"' in script
    assert '"$unit_backup_dir/qsys-web-test.service" "$unit_target"' in script
    assert 'rm -f -- "$unit_target"' in script
    assert 'cmp -s "$repo/deploy/systemd/qsys-web-test.service" "$unit_target"' in script
    assert "systemctl daemon-reload" in script
    assert "trap cleanup EXIT" in script
    assert "trap 'exit 143' TERM" in script


def test_test_plan_never_targets_windows() -> None:
    plan = DeploymentManifest.load(MANIFEST).plan(
        ["src/qsys/live/redis_transport.py", "scripts/run_agent.py"],
        environment="test",
    )

    assert plan.components == ("test-runtime",)
    assert plan.nodes == ("linux",)
    assert plan.linux_units == ("qsys-web-test.service",)
    assert plan.windows_apps == ()
    assert plan.windows_tasks == ()


def test_test_config_is_isolated_and_fingerprinted(tmp_path) -> None:
    test = _test_config(tmp_path / "test.toml")
    production = _production_config(tmp_path / "production.toml")

    result = validate_config(test, "test", against=production, hmac_key=b"node-key")

    assert result["ok"] is True
    assert result["errors"] == []
    assert set(result["fingerprints"]) == {"public_sha256", "private_hmac_sha256"}
    assert "test-secret" not in json.dumps(result)


def test_test_config_rejects_a_production_stream_and_order_mode(tmp_path) -> None:
    path = _test_config(tmp_path / "test.toml")
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace('orders = "test:orders"', 'orders = "cmd:orders"')
        .replace('mode = "disabled"', 'mode = "live"')
        .replace("orders_enabled = false", "orders_enabled = true"),
        encoding="utf-8",
    )

    result = validate_config(path, "test")

    assert result["ok"] is False
    assert any("safety" in error for error in result["errors"])
    assert any("test: prefix" in error for error in result["errors"])


def test_config_requires_known_schema_version(tmp_path) -> None:
    path = _test_config(tmp_path / "test.toml")
    path.write_text(
        path.read_text(encoding="utf-8").replace("schema_version = 1", "schema_version = 2"),
        encoding="utf-8",
    )

    with pytest.raises(ContractError, match="schema_version must be 1"):
        validate_config(path, "test")


def test_cross_environment_collisions_are_rejected(tmp_path) -> None:
    production = _production_config(tmp_path / "production.toml")
    collision = _production_config(tmp_path / "collision.toml")

    result = validate_config(collision, "production", against=production)

    assert result["ok"] is False
    assert any("Redis endpoint" in error for error in result["errors"])
    assert any("stream names" in error for error in result["errors"])


def test_public_fingerprint_redacts_secrets_and_private_hmac_tracks_rotation() -> None:
    first = {"redis": {"host": "127.0.0.1", "password": "alpha"}}
    second = {"redis": {"host": "127.0.0.1", "password": "beta"}}

    a = config_fingerprints(first, hmac_key=b"node-only")
    b = config_fingerprints(second, hmac_key=b"node-only")

    assert a["public_sha256"] == b["public_sha256"]
    assert a["private_hmac_sha256"] != b["private_hmac_sha256"]
    assert "alpha" not in json.dumps(a)


def test_deployment_record_matches_the_versioned_contract() -> None:
    record = _record()

    validate_record(record)

    schema = json.loads(Path("deploy/record.schema.json").read_text(encoding="utf-8"))
    assert set(schema["required"]) == set(record)


def test_deployment_record_rejects_secret_shaped_fields() -> None:
    record = _record()
    record["before"]["password"] = "must-not-be-recorded"

    with pytest.raises(ContractError, match="sensitive key"):
        validate_record(record)
