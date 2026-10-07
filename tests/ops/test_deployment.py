from __future__ import annotations

import base64
import json
import importlib.util
import subprocess
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


def test_docs_only_change_is_recorded_without_a_runtime_deployment() -> None:
    plan = DeploymentManifest.load(MANIFEST).plan(
        ["docs/deploy/linux-core.md"], environment="production",
    )

    assert plan.components == ()
    assert plan.ignored_paths == ("docs/deploy/linux-core.md",)
    assert plan.deployable is False


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


def test_production_requires_exact_planned_linux_components() -> None:
    entrypoint = _production_entrypoint_module()
    plan = {"components": ["linux-web", "windows-agent"], "unknown_paths": []}

    assert entrypoint._validate_components(plan, ["linux-web"]) == ["linux-web"]
    with pytest.raises(ValueError, match="do not match"):
        entrypoint._validate_components(plan, ["linux-resident"])
    with pytest.raises(ValueError, match="production Linux"):
        entrypoint._validate_components(plan, ["windows-agent"])


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
    assert "Attest control-plane-only target" in workflow


def test_production_workflow_is_manual_and_fail_closed() -> None:
    workflow = Path(".github/workflows/deploy-production-linux.yml").read_text(
        encoding="utf-8",
    )

    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
    assert "ref: main" in workflow
    assert "DEPLOY_PRODUCTION" in workflow
    assert "git merge-base --is-ancestor" in workflow
    assert "check-runs?per_page=100" in workflow
    assert "PRODUCTION_DEPLOY_SSH_KEY" in workflow
    assert "cancel-in-progress: false" in workflow


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
