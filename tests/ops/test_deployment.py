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


def _emergency_authorization(entrypoint, tmp_path, monkeypatch):
    from types import SimpleNamespace

    path = tmp_path / "emergency.json"
    monkeypatch.setattr(entrypoint, "EMERGENCY_RESTART_AUTHORIZATION", path)
    original = entrypoint.os.fstat

    def trusted_file(fd):
        info = original(fd)
        return SimpleNamespace(st_uid=0, st_mode=info.st_mode, st_size=info.st_size)

    monkeypatch.setattr(entrypoint.os, "fstat", trusted_file)
    now = datetime.now(UTC)
    value = {
        "schema_version": 1, "confirmation": "ALLOW_EMERGENCY_RESTART",
        "target_sha": "a" * 40, "run_id": "incident-linux", "actor": "maintainer",
        "repository": "owner/repo", "authorized_at": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (now + timedelta(minutes=30)).isoformat(),
        "reason": "维护者明确授权本次紧急修复在交易时段发布",
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    args = argparse.Namespace(
        sha=value["target_sha"], run_id=value["run_id"], actor=value["actor"],
        repository=value["repository"], failure_point="none", drill_confirmation="",
        dry_run=False, components=["linux-resident"], pr_number=1,
    )
    return path, value, args


@pytest.mark.parametrize("field,bad", [
    ("schema_version", 2), ("confirmation", "yes"), ("target_sha", "b" * 40),
    ("run_id", "other-release"), ("actor", "other-maintainer"),
    ("repository", "other/repo"), ("reason", ""),
    ("expires_at", "2020-01-01T00:00:00+00:00"),
    ("expires_at", "2099-01-01T00:00:00+00:00"),
    ("authorized_at", "2099-01-01T00:00:00+00:00"),
    ("authorized_at", "2020-01-01T00:00:00"),
])
def test_emergency_release_rejects_unbound_or_expired_authorization(monkeypatch, tmp_path, field, bad):
    entrypoint = _production_entrypoint_module()
    path, value, args = _emergency_authorization(entrypoint, tmp_path, monkeypatch)
    value[field] = bad
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        entrypoint._emergency_restart_authorization(args)


@pytest.mark.parametrize("unsafe", ["owner", "writable", "symlink", "drill"])
def test_emergency_release_rejects_unsafe_approval_file(monkeypatch, tmp_path, unsafe):
    from types import SimpleNamespace

    entrypoint = _production_entrypoint_module()
    path, value, args = _emergency_authorization(entrypoint, tmp_path, monkeypatch)
    if unsafe == "owner":
        monkeypatch.setattr(entrypoint.os, "fstat", lambda fd: SimpleNamespace(
            st_uid=1000, st_mode=0o100644, st_size=500,
        ))
    elif unsafe == "writable":
        path.chmod(0o666)
    elif unsafe == "symlink":
        link = tmp_path / "link"
        link.symlink_to(path)
        monkeypatch.setattr(entrypoint, "EMERGENCY_RESTART_AUTHORIZATION", link)
    else:
        args.failure_point = "linux-health"
    with pytest.raises((ValueError, OSError)):
        entrypoint._emergency_restart_authorization(args)


@pytest.mark.parametrize("approved", [False, True])
def test_emergency_release_preserves_gate_and_validates_real_record(monkeypatch, tmp_path, approved):
    entrypoint = _production_entrypoint_module()
    path, approval, args = _emergency_authorization(entrypoint, tmp_path, monkeypatch)
    if not approved:
        path.unlink()
    commands, records = [], []
    monkeypatch.setattr(entrypoint, "_branch", lambda repo: "main")
    monkeypatch.setattr(entrypoint, "_dirty", lambda repo: False)
    monkeypatch.setattr(entrypoint, "_head", lambda repo: args.sha)
    monkeypatch.setattr(entrypoint, "_fetch_and_validate_target", lambda *a: "b" * 40)
    monkeypatch.setattr(entrypoint, "_successful_test_run", lambda *a: "test-1")
    monkeypatch.setattr(entrypoint, "_plan", lambda *a: {
        "components": ["linux-resident"], "unknown_paths": [], "changed_paths": [],
        "linux_units": ["qsys-live-core.service"], "health_checks": [],
    })
    monkeypatch.setattr(entrypoint, "_active", lambda *a: True)
    monkeypatch.setattr(entrypoint, "_restart_window", lambda *a: {
        "checked_at": datetime.now(UTC).isoformat(), "trading_day": True,
        "restart_allowed": False,
    })
    monkeypatch.setattr(entrypoint, "_resources", lambda *a: {
        "ok": True, "disk_free_bytes": 10 ** 10, "memory_available_bytes": 10 ** 9,
    })
    monkeypatch.setattr(entrypoint, "_rollback_ready", lambda *a: True)
    monkeypatch.setattr(entrypoint, "_fingerprints", lambda *a: {})
    monkeypatch.setattr(entrypoint, "_wait_healthy", lambda *a: True)
    monkeypatch.setattr(entrypoint, "_run", lambda cmd, **kw:
        commands.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))

    def write_record(config, run_id, record):
        validate_record(record)
        records.append(record)
        return tmp_path / "record.json"

    monkeypatch.setattr(entrypoint, "_write_record", write_record)
    config = {"production_repo": str(tmp_path), "production_record_root": str(tmp_path / "records")}
    if not approved:
        with pytest.raises(ValueError, match="prohibited during the trading window"):
            entrypoint.deploy(args, config)
        assert commands == records == []
    else:
        assert entrypoint.deploy(args, config) == 0
        assert records[0]["status"] == "success"
        check = next(c for c in records[0]["checks"] if c["name"] == "restart-window")
        assert "restart_allowed=false" in check["detail"]
        assert approval["reason"] in check["detail"]
        assert ["sudo", "-n", "systemctl", "restart", "qsys-live-core.service"] in commands
        (tmp_path / "records" / args.run_id).mkdir(parents=True)
        before_commands = list(commands)
        with pytest.raises(ValueError, match="run-id already exists"):
            entrypoint.deploy(args, config)
        assert commands == before_commands


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


def test_service_checks_component_has_an_isolated_release_and_state_boundary() -> None:
    specs = json.loads(Path("deploy/component-releases.json").read_text(encoding="utf-8"))
    service = specs["components"]["service-checks"]

    assert service["entrypoint"] == {
        "command": "qsys-service-checks",
        "target": "qsys.ops.service_checks_cli:main",
    }
    assert service["release_root"] == "/opt/qsys/components/service-checks/releases"
    assert service["current"] == "/opt/qsys/components/service-checks/current"
    assert service["unit"] == "qsys-service-checks.service"
    assert service["state_directories"] == [
        {
            "path": "/home/ubuntu/qsys/var/service-runs/service-checks",
            "owner": "qsys-service-checks", "group": "qsys-service-checks", "mode": "0750",
        },
        {
            "path": "/home/ubuntu/qsys/var/alerts/service-checks",
            "owner": "qsys-service-checks", "group": "qsys-service-checks", "mode": "0750",
        },
    ]
    dropin = "\n".join(service["dropin_lines"])
    assert "User=qsys-service-checks" in dropin
    assert "WorkingDirectory=/" in dropin
    assert "ReadWritePaths=/home/ubuntu/qsys/var/service-runs/service-checks" in dropin
    assert "ReadWritePaths=/home/ubuntu/qsys/var/alerts/service-checks" in dropin
    assert "--config /etc/qsys/components/service-checks/deploy.toml" in dropin
    assert "--config /home/ubuntu/qsys/config/deploy.local.toml" not in dropin
    assert "MemoryMax=1G" in dropin and "CPUQuota=50%" in dropin
    assert "--record-root /home/ubuntu/qsys/var/service-runs/service-checks" in dropin
    assert "--alert-ledger /home/ubuntu/qsys/var/alerts/service-checks" in dropin
    assert "/home/ubuntu/qsys/.venv" not in dropin
    assert "scripts/service_checks.py" not in dropin


def test_backtest_worker_component_is_separate_from_web_and_write_bounded() -> None:
    specs = json.loads(Path("deploy/component-releases.json").read_text(encoding="utf-8"))
    worker = specs["components"]["backtest-worker"]

    # worker 已由独立不可变制品发布，不能同时残留一个固定入口不接受的
    # ``linux-backtest-worker`` 共享发布组件；否则共享 checkout 落后时，
    # 历史路径会让 production dry-run 在节点门禁处失败。
    legacy = DeploymentManifest.load(MANIFEST)
    assert "linux-backtest-worker" not in {item.name for item in legacy.components}

    assert worker["entrypoint"] == {
        "command": "qsys-backtest-worker",
        "target": "qsys.web.backtest_worker_cli:main",
    }
    assert worker["scan_roots"] == [
        "src/qsys/backtest", "src/qsys/core", "src/qsys/data",
        "src/qsys/markets", "src/qsys/strategy", "src/qsys/web/workbench.py",
        "src/qsys/web/backtest_worker_cli.py",
    ]
    assert worker["release_root"] == "/opt/qsys/components/backtest-worker/releases"
    assert worker["current"] == "/opt/qsys/components/backtest-worker/current"
    assert worker["unit"] == "qsys-backtest-worker.service"
    assert worker["state_directories"] == [
        {
            "path": "/home/ubuntu/qsys/var/workbench",
            "owner": "qsys-backtest-worker", "group": "qsys-workbench", "mode": "0770",
        },
        {
            "path": "/home/ubuntu/qsys/var/runs",
            "owner": "qsys-backtest-worker", "group": "qsys-workbench", "mode": "0770",
        },
    ]
    dropin = "\n".join(worker["dropin_lines"])
    assert "User=qsys-backtest-worker" in dropin
    assert "Group=qsys-workbench" in dropin
    assert dropin.count("ReadWritePaths=") == 3  # reset plus two explicit namespaces
    assert "ReadWritePaths=/data/qsys/warehouse" not in dropin
    assert "/home/ubuntu/qsys/.venv" not in dropin

    unit = Path("deploy/systemd/qsys-backtest-worker.service").read_text(encoding="utf-8")
    assert "/venv/bin/qsys-backtest-worker" in unit
    assert "--workspace /home/ubuntu/qsys/var/workbench" in unit
    assert "ReadWritePaths=/home/ubuntu/qsys/var/workbench" in unit
    assert "ReadWritePaths=/home/ubuntu/qsys/var/runs" in unit
    assert "ReadWritePaths=/data/qsys/warehouse" not in unit


def test_web_component_has_an_isolated_release_and_minimal_write_boundary() -> None:
    specs = json.loads(Path("deploy/component-releases.json").read_text(encoding="utf-8"))
    web = specs["components"]["web"]

    assert web["entrypoint"] == {
        "command": "qsys-web", "target": "qsys.web.monitor_cli:main",
    }
    assert web["scan_roots"] == ["src/qsys"]
    assert web["release_root"] == "/opt/qsys/components/web/releases"
    assert web["current"] == "/opt/qsys/components/web/current"
    assert web["unit"] == "qsys-monitor.service"
    assert web["state_directories"] == [
        {
            "path": "/home/ubuntu/qsys/var/workbench",
            "owner": "qsys-web", "group": "qsys-workbench", "mode": "0770",
        },
        {
            "path": "/home/ubuntu/qsys/var/workbench/strategies",
            "owner": "qsys-web", "group": "qsys-workbench", "mode": "0770",
        },
    ]
    dropin = "\n".join(web["dropin_lines"])
    assert "User=qsys-web" in dropin
    assert "Group=qsys-workbench" in dropin
    # 配置目录保持 root:qsys-web 0750、文件 0640；主组改成共享 workbench 后，
    # 必须显式补回私有配置组，不能为了可读而把凭据开放给共享组。
    assert "SupplementaryGroups=qsys-web" in dropin
    assert "WorkingDirectory=/" in dropin
    assert "QSYS_WINDOWS_SSH_KEY=/etc/qsys/web/windows-task-readonly" in dropin
    assert "ExecStart=/opt/qsys/components/web/current/venv/bin/qsys-web" in dropin
    assert "--strategies /home/ubuntu/qsys/var/workbench/strategies" in dropin
    assert dropin.count("ReadWritePaths=") == 2  # reset plus one explicit namespace
    assert "ReadWritePaths=/home/ubuntu/qsys" not in web["dropin_lines"]
    assert "ReadWritePaths=/data/qsys" not in web["dropin_lines"]
    assert "/home/ubuntu/qsys/.venv" not in dropin
    assert "scripts/run_monitor.py" not in dropin


def test_paper_and_shadow_components_have_independent_release_boundaries() -> None:
    specs = json.loads(Path("deploy/component-releases.json").read_text(encoding="utf-8"))
    paper = specs["components"]["paper"]
    shadow = specs["components"]["shadow-core"]

    assert paper["entrypoint"] == {
        "command": "qsys-paper", "target": "qsys.paper.runner_cli:main",
    }
    assert paper["release_root"] == "/opt/qsys/components/paper/releases"
    assert paper["current"] == "/opt/qsys/components/paper/current"
    assert paper["unit"] == "qsys-paper.service"
    assert paper["state_directories"] == [
        {"path": "/home/ubuntu/qsys/var/workbench",
         "owner": "qsys-web", "group": "qsys-workbench", "mode": "0770"},
        {"path": "/home/ubuntu/qsys/var/alerts/paper",
         "owner": "qsys-paper", "group": "qsys-paper", "mode": "0770"},
    ]
    paper_dropin = "\n".join(paper["dropin_lines"])
    assert "User=qsys-paper" in paper_dropin
    assert "Group=qsys-paper" in paper_dropin
    assert "SupplementaryGroups=qsys-observers" in paper_dropin
    assert "qsys-workbench" not in paper_dropin
    assert "--config /etc/qsys/components/paper/deploy.toml" in paper_dropin
    assert "--alert-ledger /home/ubuntu/qsys/var/alerts/paper" in paper_dropin
    assert "ExecStart=/opt/qsys/components/paper/current/venv/bin/qsys-paper" in paper_dropin
    assert paper_dropin.count("ReadWritePaths=") == 3
    assert "ReadWritePaths=/data/qsys" not in paper_dropin
    assert "scripts/run_paper.py" not in paper_dropin

    assert shadow["entrypoint"] == {
        "command": "qsys-shadow-core", "target": "qsys.live.shadow_cli:main",
    }
    assert shadow["release_root"] == "/opt/qsys/components/shadow-core/releases"
    assert shadow["current"] == "/opt/qsys/components/shadow-core/current"
    assert shadow["unit"] == "qsys-shadow-core.service"
    shadow_dropin = "\n".join(shadow["dropin_lines"])
    assert "User=qsys-shadow-core" in shadow_dropin
    assert "SupplementaryGroups=qsys-observers" in shadow_dropin
    assert "qsys-workbench" not in shadow_dropin
    assert "src/qsys/agent" in shadow["scan_roots"]
    assert "--config /etc/qsys/components/shadow-core/deploy.toml" in shadow_dropin
    assert "--validation-namespace" not in shadow_dropin
    assert "InaccessiblePaths=/home/ubuntu/qsys/config" in shadow_dropin
    assert {item["mode"] for item in shadow["state_directories"]} == {"0770"}
    assert "qsys-live-preflight" in shadow_dropin
    assert "ExecStart=/opt/qsys/components/shadow-core/current/venv/bin/qsys-shadow-core" in shadow_dropin
    assert "--journal-warehouse /home/ubuntu/qsys/var/shadow-core/journal" in shadow_dropin
    assert "--kill-switch /home/ubuntu/qsys/var/shadow-core/SHADOW_KILL" in shadow_dropin
    assert shadow_dropin.count("ReadWritePaths=") == 3
    assert "ReadWritePaths=/data/qsys" not in shadow_dropin
    assert "scripts/run_shadow.py" not in shadow_dropin
    assert "/opt/qsys/.venv" not in shadow_dropin


def test_limit_up_shadow_is_write_bounded_and_preserves_read_only_inputs():
    import shlex

    specs = json.loads(Path("deploy/component-releases.json").read_text())
    spec = specs["components"]["limit-up-shadow"]
    lines = spec["dropin_lines"]
    assert spec["entrypoint"] == {
        "command": "qsys-limit-up-shadow", "target": "qsys.research.limit_up_shadow_cli:main",
    }
    assert {line for line in lines if line.startswith("ReadWritePaths=")} == {
        "ReadWritePaths=",
        "ReadWritePaths=/data/qsys/var-prod/limit-up-shadow",
        "ReadWritePaths=/home/ubuntu/qsys/var/service-runs/limit-up-shadow",
    }
    assert "UMask=0027" in lines
    assert "ExecStartPre=/usr/bin/test -s /data/qsys/var-prod/limit-up-shadow/limit-up-shadow.sqlite3" in lines
    assert "User=qsys-limit-up-shadow" in lines and "Group=qsys-limit-up-shadow" in lines
    command = shlex.split(next(line[len("ExecStart="):] for line in lines
                              if line.startswith("ExecStart=") and line != "ExecStart="))
    assert command[:3] == ["/usr/bin/flock", "--shared", "/home/ubuntu/qsys/var/warehouse-write.lock"]
    assert command[3] == spec["current"] + "/" + spec["executable"]
    assert command[command.index("--state") + 1] == "/data/qsys/var-prod/limit-up-shadow/limit-up-shadow.sqlite3"
    assert command[command.index("--service-runs") + 1] == spec["state_directories"][1]["path"]
    assert command[command.index("--config") + 1] == "/etc/qsys/components/limit-up-shadow/deploy.toml"
    assert spec["seed"]["source"] == "/data/qsys/var-prod/limit-up-shadow.sqlite3"
    assert spec["seed"]["target"] == command[command.index("--state") + 1]
    assert "SupplementaryGroups=qsys-observers" in lines
    web_command = next(line for line in specs["components"]["web"]["dropin_lines"]
                       if line.startswith("ExecStart=") and line != "ExecStart=")
    web_args = shlex.split(web_command.removeprefix("ExecStart="))
    assert web_args[web_args.index("--limit-up-shadow-state")+1] == spec["seed"]["target"]
    assert "scripts/" not in " ".join(command)
    assert "WorkingDirectory=/" in lines
    assert "OnCalendar=Mon..Fri 19:35 Asia/Shanghai" in Path("deploy/systemd/qsys-limit-up-shadow.timer").read_text()


@pytest.mark.parametrize("active_state", ["active", "activating", "deactivating", "reloading", ""])
def test_running_limit_up_oneshot_cannot_be_interrupted_by_component_deploy(monkeypatch, tmp_path, active_state):
    import argparse

    entrypoint = _production_entrypoint_module()
    monkeypatch.setattr(entrypoint, "_branch", lambda repo: "main")
    monkeypatch.setattr(entrypoint, "_dirty", lambda repo: False)
    monkeypatch.setattr(entrypoint, "_head", lambda repo: "b" * 40)
    monkeypatch.setattr(entrypoint, "_fetch_and_validate_target", lambda *args: "c" * 40)
    monkeypatch.setattr(entrypoint, "_successful_test_run", lambda *args: "test-1")
    monkeypatch.setattr(entrypoint, "_current_component_sha", lambda component: "d" * 40)
    # An activating Type=oneshot is not reliably represented by `is-active`.
    monkeypatch.setattr(entrypoint, "_active", lambda *args: False)
    monkeypatch.setattr(entrypoint, "_run", lambda command, **kwargs:
                        subprocess.CompletedProcess(command, 0, active_state + "\n", ""))
    args = argparse.Namespace(sha="a" * 40, component="limit-up-shadow", dry_run=True,
                              run_id="release-1", actor="maintainer", repository="owner/repo")
    with pytest.raises(ValueError, match="wait for the oneshot to finish"):
        entrypoint.component_deploy(args, {
            "production_repo": str(tmp_path), "production_record_root": str(tmp_path / "records"),
        })


def test_storage_report_component_has_a_read_only_release_boundary() -> None:
    specs = json.loads(Path("deploy/component-releases.json").read_text(encoding="utf-8"))
    storage = specs["components"]["storage-report"]

    assert storage["entrypoint"] == {
        "command": "qsys-storage-report",
        "target": "qsys.ops.storage_report_cli:main",
    }
    assert storage["scan_roots"] == ["src/qsys/ops"]
    assert storage["release_root"] == "/opt/qsys/components/storage-report/releases"
    assert storage["current"] == "/opt/qsys/components/storage-report/current"
    assert storage["unit"] == "qsys-storage-report.service"
    assert storage["state_directories"] == [{
        "path": "/home/ubuntu/qsys/var/storage-history",
        "owner": "qsys-storage-report", "group": "qsys-storage-report", "mode": "0750",
    }]
    assert storage["dropin_lines"][:-1] == [
        "[Service]",
        "User=qsys-storage-report",
        "Group=qsys-storage-report",
        "WorkingDirectory=/",
        "ExecStart=",
        "ReadWritePaths=",
        "ReadWritePaths=/home/ubuntu/qsys/var/storage-history",
    ]
    command = storage["dropin_lines"][-1]
    assert command.startswith(
        "ExecStart=/opt/qsys/components/storage-report/current/venv/bin/qsys-storage-report "
    )
    assert "--base /data/qsys" in command
    assert "--scratch /home/ubuntu/scratch" in command
    assert "--history-root /home/ubuntu/qsys/var/storage-history" in command
    assert "/home/ubuntu/qsys/.venv" not in command
    assert "scripts/storage_report.py" not in command


def test_daily_digest_component_has_one_owned_write_namespace() -> None:
    specs = json.loads(Path("deploy/component-releases.json").read_text(encoding="utf-8"))
    digest = specs["components"]["daily-digest"]

    assert digest["entrypoint"] == {
        "command": "qsys-daily-digest",
        "target": "qsys.ops.daily_digest_cli:main",
    }
    assert digest["scan_roots"] == [
        "src/qsys/data", "src/qsys/live", "src/qsys/ops", "src/qsys/paper",
    ]
    assert digest["release_root"] == "/opt/qsys/components/daily-digest/releases"
    assert digest["current"] == "/opt/qsys/components/daily-digest/current"
    assert digest["unit"] == "qsys-daily-digest.service"
    assert digest["state_directories"] == [{
        "path": "/home/ubuntu/qsys/var/service-runs/daily-digest",
        "owner": "qsys-daily-digest", "group": "qsys-daily-digest", "mode": "0750",
    }]
    dropin = "\n".join(digest["dropin_lines"])
    assert "User=qsys-daily-digest" in dropin
    assert "WorkingDirectory=/" in dropin
    assert dropin.count("ReadWritePaths=/home") == 1
    assert "ReadWritePaths=/home/ubuntu/qsys/var/service-runs/daily-digest" in dropin
    assert "--service-runs /home/ubuntu/qsys/var/service-runs" in dropin
    assert "--record-root /home/ubuntu/qsys/var/service-runs/daily-digest" in dropin
    assert "--alert-ledger /home/ubuntu/qsys/var/alerts" in dropin
    assert "/home/ubuntu/qsys/.venv" not in dropin
    assert "scripts/send_daily_digest.py" not in dropin


def test_daily_archive_component_has_explicit_data_writer_boundaries() -> None:
    specs = json.loads(Path("deploy/component-releases.json").read_text(encoding="utf-8"))
    archive = specs["components"]["daily-archive"]

    assert archive["entrypoint"] == {
        "command": "qsys-daily-archive",
        "target": "qsys.data.daily_archive_cli:main",
    }
    assert archive["scan_roots"] == [
        "src/qsys/core",
        "src/qsys/data",
        "src/qsys/live",
        "src/qsys/ops",
        "src/qsys/paper",
        "scripts/win_download_minutes.py", "scripts/win_download_auction_ticks.py",
    ]
    assert archive["release_root"] == "/opt/qsys/components/daily-archive/releases"
    assert archive["current"] == "/opt/qsys/components/daily-archive/current"
    assert archive["unit"] == "qsys-daily-archive.service"
    assert archive["state_directories"] == [
        {
            "path": "/home/ubuntu/qsys/var/daily-archive",
            "owner": "qsys-daily-archive", "group": "qsys-daily-archive",
            "mode": "0770",
        },
        {
            "path": "/home/ubuntu/qsys/var/service-runs/daily-archive",
            "owner": "qsys-daily-archive", "group": "qsys-daily-archive",
            "mode": "0770",
        },
    ]
    dropin = "\n".join(archive["dropin_lines"])
    assert "User=qsys-daily-archive" in dropin
    assert "ReadWritePaths=/data/qsys/warehouse" not in archive["dropin_lines"]
    assert "ReadWritePaths=/data/qsys/warehouse/auction_ticks /data/qsys/warehouse/minute_bars /data/qsys/warehouse/bar_minute_1m /data/qsys/warehouse/suspend_daily" in dropin
    assert "SupplementaryGroups=qsys-observers" in dropin
    assert "qsys-workbench" not in dropin
    assert "--win-transport gateway" in dropin
    assert "--config /etc/qsys/components/daily-archive/deploy.toml" in dropin
    assert "--win-known-hosts /etc/qsys/components/daily-archive/windows-known-hosts" in dropin
    assert "--win-auction-script-source /opt/qsys/components/daily-archive/current/source/scripts/win_download_auction_ticks.py" in dropin
    assert "ReadWritePaths=/home/ubuntu/qsys/var/warehouse-write.lock" not in dropin
    assert "ReadWritePaths=/home/ubuntu/qsys/var/daily-archive" in dropin
    assert "--work-root /home/ubuntu/qsys/var/daily-archive" in dropin
    assert "--record-root /home/ubuntu/qsys/var/service-runs/daily-archive" in dropin
    assert "--win-key /etc/qsys/components/daily-archive/windows-data-writer" in dropin
    assert "current/source/scripts/win_download_minutes.py" in dropin
    assert "/home/ubuntu/qsys/.venv" not in dropin


def test_premarket_limits_component_has_minimal_writer_boundaries() -> None:
    specs = json.loads(Path("deploy/component-releases.json").read_text(encoding="utf-8"))
    premarket = specs["components"]["premarket-limits"]

    assert premarket["entrypoint"] == {
        "command": "qsys-premarket-limits",
        "target": "qsys.data.premarket_limits_cli:main",
    }
    assert premarket["scan_roots"] == [
        "src/qsys/core", "src/qsys/data", "src/qsys/live", "src/qsys/ops",
    ]
    assert premarket["release_root"] == (
        "/opt/qsys/components/premarket-limits/releases"
    )
    assert premarket["current"] == "/opt/qsys/components/premarket-limits/current"
    assert premarket["unit"] == "qsys-premarket-limits.service"
    assert premarket["state_directories"] == [{
        "path": "/home/ubuntu/qsys/var/service-runs/premarket-limits",
        "owner": "qsys-premarket-limits", "group": "qsys-premarket-limits",
        "mode": "0770",
    }]
    dropin = "\n".join(premarket["dropin_lines"])
    assert "User=qsys-premarket-limits" in dropin
    assert "IPAddressDeny=127.0.0.1/32 ::1/128 ::ffff:127.0.0.1/128" in dropin
    assert "ReadWritePaths=/data/qsys/warehouse" not in premarket["dropin_lines"]
    assert "ReadWritePaths=/data/qsys/warehouse/price_limits_daily /data/qsys/warehouse/suspend_daily /data/qsys/warehouse/price_limit_daily" in dropin
    assert "ReadWritePaths=/home/ubuntu/qsys/var/warehouse-write.lock" not in dropin
    assert "SupplementaryGroups=qsys-observers" in dropin
    assert "qsys-workbench" not in dropin
    assert "--config /etc/qsys/components/premarket-limits/deploy.toml" in dropin
    assert "--publisher-key /etc/qsys/components/premarket-limits/price-limits-publisher" in dropin
    assert "--publisher-known-hosts /etc/qsys/components/premarket-limits/publisher-known-hosts" in dropin
    assert (
        "ReadWritePaths=/home/ubuntu/qsys/var/service-runs/premarket-limits"
        in dropin
    )
    assert dropin.count("ReadWritePaths=/home") == 1
    assert "--service-runs /home/ubuntu/qsys/var/service-runs/premarket-limits" in dropin
    assert "/home/ubuntu/qsys/.venv" not in dropin
    assert "scripts/tushare_premarket_limits.py" not in dropin


def test_tushare_sync_component_owns_bounded_writer_and_convergence_paths() -> None:
    specs = json.loads(Path("deploy/component-releases.json").read_text(encoding="utf-8"))
    sync = specs["components"]["tushare-sync"]

    assert sync["entrypoint"] == {
        "command": "qsys-tushare-sync",
        "target": "qsys.data.tushare_sync_cli:main",
    }
    assert sync["scan_roots"] == [
        "src/qsys/core", "src/qsys/data", "src/qsys/strategy", "src/qsys/live", "src/qsys/ops",
        "src/qsys/paper", "scripts/win_download_minutes.py", "scripts/win_download_auction_ticks.py",
    ]
    assert sync["release_root"] == "/opt/qsys/components/tushare-sync/releases"
    assert sync["current"] == "/opt/qsys/components/tushare-sync/current"
    assert sync["unit"] == "qsys-tushare-sync.service"
    assert sync["state_directories"] == [
        {
            "path": "/home/ubuntu/qsys/var/tushare-sync",
            "owner": "qsys-tushare-sync", "group": "qsys-tushare-sync",
            "mode": "0750",
        },
        {
            "path": "/home/ubuntu/qsys/var/service-runs/tushare-sync",
            "owner": "qsys-tushare-sync", "group": "qsys-tushare-sync",
            "mode": "0750",
        },
    ]
    dropin = "\n".join(sync["dropin_lines"])
    assert "Environment=QSYS_CATALOG_SOURCE_ROOT=/opt/qsys/components/tushare-sync/current/source" in dropin
    assert "User=qsys-tushare-sync" in dropin
    assert "WorkingDirectory=/" in dropin
    assert "ReadWritePaths=/data/qsys/warehouse" in dropin
    assert "ReadWritePaths=/home/ubuntu/qsys/var/warehouse-write.lock" not in dropin
    assert "ReadWritePaths=/home/ubuntu/qsys/var/tushare-sync" in dropin
    assert "ReadWritePaths=/home/ubuntu/qsys/var/service-runs/tushare-sync" in dropin
    assert dropin.count("ReadWritePaths=/home") == 3
    assert "--late-minute-convergence" in dropin
    assert "--late-minute-work-root /home/ubuntu/qsys/var/tushare-sync" in dropin
    assert "--late-minute-record-root /home/ubuntu/qsys/var/service-runs/tushare-sync" in dropin
    assert "--late-minute-paper-store /home/ubuntu/qsys/var/workbench/workbench.sqlite3" in dropin
    assert "Group=qsys-tushare-sync" in dropin
    assert "SupplementaryGroups=qsys-observers" in dropin
    assert "qsys-workbench" not in dropin
    assert "--late-minute-win-transport gateway" in dropin
    assert "--late-minute-win-known-hosts /etc/qsys/components/tushare-sync/windows-known-hosts" in dropin
    assert "--config /etc/qsys/components/tushare-sync/deploy.toml" in dropin
    assert "ReadWritePaths=/data/qsys/warehouse" not in sync["dropin_lines"]
    assert "--late-minute-win-key /etc/qsys/components/tushare-sync/windows-data-writer" in dropin
    assert "current/source/scripts/win_download_minutes.py" in dropin
    assert "/home/ubuntu/qsys/.venv" not in dropin
    assert "scripts/tushare_daily_sync.py" not in dropin


def test_health_observer_component_owns_only_timeline_and_alert_namespaces() -> None:
    specs = json.loads(Path("deploy/component-releases.json").read_text(encoding="utf-8"))
    observer = specs["components"]["health-observer"]

    assert observer["entrypoint"] == {
        "command": "qsys-health-observer",
        "target": "qsys.ops.health_observer_cli:main",
    }
    assert observer["scan_roots"] == [
        "src/qsys/data", "src/qsys/live", "src/qsys/ops", "src/qsys/paper",
        "src/qsys/web",
    ]
    assert observer["release_root"] == "/opt/qsys/components/health-observer/releases"
    assert observer["current"] == "/opt/qsys/components/health-observer/current"
    assert observer["unit"] == "qsys-health-observer.service"
    assert observer["state_directories"] == [
        {
            "path": "/home/ubuntu/qsys/var/health-observer",
            "owner": "qsys-health-observer", "group": "qsys-health-observer",
            "mode": "0750",
        },
        {
            "path": "/home/ubuntu/qsys/var/alerts/health-observer",
            "owner": "qsys-health-observer", "group": "qsys-health-observer",
            "mode": "0750",
        },
    ]
    dropin = "\n".join(observer["dropin_lines"])
    assert "User=qsys-health-observer" in dropin
    assert dropin.count("ReadWritePaths=/home") == 2
    assert "--state /home/ubuntu/qsys/var/health-observer/service-health.sqlite3" in dropin
    assert "--alert-ledger /home/ubuntu/qsys/var/alerts/health-observer" in dropin
    assert "QSYS_WINDOWS_SSH_KEY=/etc/qsys/health-observer/windows-task-readonly" in dropin
    assert "/home/ubuntu/qsys/.venv" not in dropin
    assert "scripts/run_health_observer.py" not in dropin


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


def test_live_preparation_helper_is_shipped_without_resident_restarts() -> None:
    plan = DeploymentManifest.load(MANIFEST).plan(
        ["scripts/prepare_live_core_release.py"], environment="production",
    )
    assert plan.components == ("linux-data-jobs",)
    assert plan.linux_units == ()
    assert plan.ignored_paths == ()
    assert plan.unknown_paths == ()


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

    assert state["entrypoint_version"] == 31
    assert set(state["capabilities"]) == {
        "systemd-apply-v1", "systemd-component-boundary-v1", "systemd-changed-artifacts-v1",
        "systemd-preview-v1", "systemd-runtime-paths-v1", "systemd-rollback-v1",
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
        "component-release-v4",
        "component-release-v5",
        "component-release-v6",
        "component-release-v7",
        "component-release-v8",
        "component-release-v9",
        "component-release-v10",
        "component-release-v11",
        "component-release-v12",
        "component-release-v13",
        "component-release-v14",
        "component-release-v15",
        "component-web-readiness-v1",
        "component-sqlite-seed-v1",
        "component-live-continuity-v1",
        "emergency-restart-authorization-v1",
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


def test_component_artifact_extraction_supports_an_exact_file_scan_root(
    tmp_path,
) -> None:
    entrypoint = _production_entrypoint_module()
    archive = tmp_path / "daily-archive.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("release.json", "{}")
        output.writestr("dist/qsys-0.1.0-py3-none-any.whl", b"wheel")
        output.writestr("source/src/qsys/data/example.py", b"DATA = 1\n")
        output.writestr(
            "source/scripts/win_download_minutes.py", b"WINDOWS_DOWNLOADER = 1\n",
        )

    destination = tmp_path / "daily-archive-release"
    entrypoint._extract_component_archive(archive, destination, "daily-archive")
    assert (destination / "source/scripts/win_download_minutes.py").is_file()

    archive_with_sibling = tmp_path / "daily-archive-with-sibling.zip"
    with zipfile.ZipFile(archive_with_sibling, "w") as output:
        output.writestr("release.json", "{}")
        output.writestr("dist/qsys-0.1.0-py3-none-any.whl", b"wheel")
        output.writestr("source/src/qsys/data/example.py", b"DATA = 1\n")
        output.writestr(
            "source/scripts/win_download_minutes.py", b"WINDOWS_DOWNLOADER = 1\n",
        )
        output.writestr("source/scripts/unrelated.py", b"UNRELATED = 1\n")

    with pytest.raises(ValueError, match="manifest, one wheel and scan sources"):
        entrypoint._extract_component_archive(
            archive_with_sibling,
            tmp_path / "daily-archive-with-sibling-release",
            "daily-archive",
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
    monkeypatch.setattr(entrypoint, "_active", lambda unit, repo: False)
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


def test_protected_unit_identity_is_stable_and_record_safe(monkeypatch, tmp_path) -> None:
    entrypoint = _production_entrypoint_module()
    monkeypatch.setattr(
        entrypoint, "_run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0,
            "LoadState=loaded\nActiveState=active\nMainPID=77\n"
            "ExecMainStartTimestampMonotonic=1234\n", "",
        ),
    )

    assert json.loads(entrypoint._unit_identity_token(
        "qsys-backtest-worker.service", tmp_path,
    )) == {
        "load": "loaded", "active": "active", "pid": "77", "started": "1234",
    }


def test_paper_and_shadow_protect_each_other_and_non_trading_peers() -> None:
    entrypoint = _production_entrypoint_module()

    assert entrypoint.COMPONENT_PROTECTED_PEERS["paper"] == {
        "shadow-core": "qsys-shadow-core.service",
        "web": "qsys-monitor.service",
        "backtest-worker": "qsys-backtest-worker.service",
    }
    assert entrypoint.COMPONENT_PROTECTED_PEERS["shadow-core"] == {
        "paper": "qsys-paper.service",
        "web": "qsys-monitor.service",
        "backtest-worker": "qsys-backtest-worker.service",
    }


@pytest.mark.parametrize("component,active", [("health-observer", True), ("storage-report", False),
                                            ("limit-up-shadow", False)])
def test_component_record_preserves_state_and_matches_contract(
    monkeypatch, tmp_path, capsys, component, active,
) -> None:
    entrypoint = _production_entrypoint_module()
    target = "a" * 40
    production = "b" * 40
    previous = "c" * 40 if active else "uninstalled"
    live = {"pid": "42", "started_monotonic": "99", "repo_sha": production}
    state = {"active": active, "component": previous}
    records = []
    commands = []
    monkeypatch.setattr(entrypoint, "_branch", lambda repo: "main")
    monkeypatch.setattr(entrypoint, "_dirty", lambda repo: False)
    monkeypatch.setattr(entrypoint, "_head", lambda repo: production)
    monkeypatch.setattr(entrypoint, "_fetch_and_validate_target", lambda repo, sha: "d" * 40)
    monkeypatch.setattr(entrypoint, "_successful_test_run", lambda config, tree: "test-1")
    monkeypatch.setattr(
        entrypoint, "_current_component_sha", lambda component: state["component"],
    )
    monkeypatch.setattr(entrypoint, "_active", lambda unit, repo: state["active"])
    monkeypatch.setattr(entrypoint, "_live_identity", lambda repo: dict(live))
    monkeypatch.setattr(entrypoint, "_live_preflight", lambda repo: None)
    monkeypatch.setattr(entrypoint, "_fingerprints", lambda config, repo: {"linux": "f"})
    monkeypatch.setattr(entrypoint, "_unit_identity_token", lambda unit, repo: "unchanged")
    monkeypatch.setattr(
        entrypoint, "_receive_component_archive",
        lambda digest, root: root / "component.zip",
    )
    monkeypatch.setattr(entrypoint, "_extract_component_archive", lambda *args: None)
    monkeypatch.setattr(entrypoint, "_verify_component_release", lambda *args: None)
    monkeypatch.setattr(entrypoint, "_stage_component_release", lambda *args: tmp_path / target)

    def fake_run(command, **kwargs):
        command = [str(item) for item in command]
        commands.append(command)
        if command[:3] == ["systemctl", "show", "qsys-limit-up-shadow.service"]:
            return subprocess.CompletedProcess(command, 0, "inactive\n", "")
        if command[-2:] == ["stop", f"qsys-{component}.service"]:
            state["active"] = False
        elif entrypoint.GENERIC_COMPONENT_INSTALLER in command:
            assert state["active"] is False
            state["component"] = target
        elif command[-2:] == ["start", f"qsys-{component}.service"]:
            state["active"] = True
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(entrypoint, "_run", fake_run)
    monkeypatch.setattr(
        entrypoint, "_write_record",
        lambda config, run_id, record: records.append(record) or tmp_path / "record.json",
    )
    args = argparse.Namespace(
        sha=target, component=component, artifact_sha256="e" * 64,
        run_id="release-observer", actor="maintainer", repository="owner/repo",
        pr_number=381, dry_run=False,
    )

    assert entrypoint.component_deploy(
        args, {
            "production_repo": str(tmp_path), "test_record_root": str(tmp_path),
            "production_record_root": str(tmp_path / "records"),
        },
    ) == 0
    assert state == {"active": active, "component": target}
    if active:
        stop = next(i for i, command in enumerate(commands) if command[-2:] == [
            "stop", f"qsys-{component}.service",
        ])
        install = next(i for i, command in enumerate(commands)
                       if entrypoint.GENERIC_COMPONENT_INSTALLER in command)
        start = next(i for i, command in enumerate(commands) if command[-2:] == [
            "start", f"qsys-{component}.service",
        ])
        assert stop < install < start
    else:
        assert not any("systemctl" in command and
                       any(action in command for action in ("start", "stop", "restart"))
                       for command in commands)
    assert records[0]["before"][f"{component}-active"] == str(active).lower()
    assert records[0]["after"][f"{component}-active"] == str(active).lower()
    if component == "limit-up-shadow":
        assert "initial SQLite history seed" in records[0]["migration_summary"]
    validate_record(records[0])
    assert json.loads(capsys.readouterr().out)["status"] == "success"


@pytest.mark.parametrize("component,active,peer_drift,busy_after_stage", [
    ("web", True, False, False),
    ("limit-up-shadow", False, False, False),
    ("limit-up-shadow", False, True, False),
    ("reboot-drill", False, False, False),
    ("reboot-drill", False, True, False),
    ("reboot-drill", False, False, True),
])
def test_component_health_and_peer_identity_gate(
    monkeypatch, tmp_path, capsys, component, active, peer_drift, busy_after_stage,
) -> None:
    entrypoint = _production_entrypoint_module()
    target = "a" * 40
    production = "b" * 40
    previous = "c" * 40
    live = {"pid": "42", "started_monotonic": "99", "repo_sha": production}
    state = {"active": active, "component": previous}
    worker_identity = json.dumps({
        "load": "loaded", "active": "active", "pid": "77", "started": "1234",
    }, sort_keys=True, separators=(",", ":"))
    commands = []
    idle_probes = 0
    records = []
    health_checks = []
    monkeypatch.setattr(entrypoint, "_branch", lambda repo: "main")
    monkeypatch.setattr(entrypoint, "_dirty", lambda repo: False)
    monkeypatch.setattr(entrypoint, "_head", lambda repo: production)
    monkeypatch.setattr(entrypoint, "_fetch_and_validate_target", lambda repo, sha: "d" * 40)
    monkeypatch.setattr(entrypoint, "_successful_test_run", lambda config, tree: "test-1")
    monkeypatch.setattr(
        entrypoint, "_current_component_sha", lambda component: state["component"],
    )
    monkeypatch.setattr(entrypoint, "_active", lambda unit, repo: state["active"])
    monkeypatch.setattr(entrypoint, "_live_identity", lambda repo: dict(live))
    monkeypatch.setattr(entrypoint, "_live_preflight", lambda repo: None)
    monkeypatch.setattr(entrypoint, "_fingerprints", lambda config, repo: {"linux": "f"})
    monkeypatch.setattr(entrypoint, "_unit_identity_token", lambda unit, repo:
                        "changed" if peer_drift and state["component"] == target
                        and unit == "qsys-monitor.service" else worker_identity)
    monkeypatch.setattr(entrypoint, "_rollback_component", lambda *args:
                        state.update(component=previous) is None)
    monkeypatch.setattr(
        entrypoint, "_web_healthy",
        lambda config: health_checks.append(True) is None or True,
    )
    monkeypatch.setattr(
        entrypoint, "_receive_component_archive",
        lambda digest, root: root / "component.zip",
    )
    monkeypatch.setattr(entrypoint, "_extract_component_archive", lambda *args: None)
    monkeypatch.setattr(entrypoint, "_verify_component_release", lambda *args: None)
    monkeypatch.setattr(entrypoint, "_stage_component_release", lambda *args: tmp_path / target)

    def fake_run(command, **kwargs):
        nonlocal idle_probes
        command = [str(item) for item in command]
        commands.append(command)
        if command[:3] == ["systemctl", "show", "qsys-reboot-drill.service"]:
            idle_probes += 1
            value = "activating" if busy_after_stage and idle_probes > 1 else "inactive"
            return subprocess.CompletedProcess(command, 0, value + "\n", "")
        if command[:3] == ["systemctl", "show", "qsys-reboot-drill.timer"]:
            return subprocess.CompletedProcess(command, 0, "inactive\n", "")
        if command[:3] == ["systemctl", "show", "qsys-limit-up-shadow.service"]:
            return subprocess.CompletedProcess(command, 0, "inactive\n", "")
        if command[-2:] == ["stop", "qsys-monitor.service"]:
            state["active"] = False
        elif entrypoint.GENERIC_COMPONENT_INSTALLER in command:
            assert state["active"] is False
            state["component"] = target
        elif command[-2:] == ["start", "qsys-monitor.service"]:
            state["active"] = True
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(entrypoint, "_run", fake_run)
    monkeypatch.setattr(
        entrypoint, "_write_record",
        lambda config, run_id, record: records.append(record) or tmp_path / "record.json",
    )
    args = argparse.Namespace(
        sha=target, component=component, artifact_sha256="e" * 64,
        run_id="release-web", actor="maintainer", repository="owner/repo",
        pr_number=396, dry_run=False,
    )

    assert entrypoint.component_deploy(
        args, {
            "production_repo": str(tmp_path), "test_record_root": str(tmp_path),
            "production_record_root": str(tmp_path / "records"),
        },
    ) == (1 if peer_drift or busy_after_stage else 0)
    validate_record(records[0])
    assert health_checks == ([True] if component == "web" else [])
    if component == "reboot-drill":
        assert idle_probes == (2 if busy_after_stage else 3)
        assert not any("systemctl" in c and any(v in c for v in ("start", "stop", "restart")) for c in commands)
    if busy_after_stage:
        assert state["component"] == previous
        assert json.loads(capsys.readouterr().out)["status"] == "failure"
        assert not any(entrypoint.GENERIC_COMPONENT_INSTALLER in c for c in commands)
        return
    if peer_drift:
        assert state["component"] == previous
        assert json.loads(capsys.readouterr().out)["status"] == "rolled_back"
        return
    assert state["active"] == active
    assert set(entrypoint.COMPONENT_PROTECTED_PEERS[component]) == (
        {"backtest-worker"} if component == "web" else
        {"paper", "shadow-core", "web", "backtest-worker"}
    )
    for peer in entrypoint.COMPONENT_PROTECTED_PEERS[component]:
        assert records[0]["before"][f"{peer}-identity"] == worker_identity
        assert records[0]["after"][f"{peer}-identity"] == worker_identity
    assert records[0]["before"]["backtest-worker-identity"] == worker_identity
    assert records[0]["after"]["backtest-worker-identity"] == worker_identity
    assert any(
        check["name"] == "backtest-worker-continuity"
        for check in records[0]["checks"]
    )
    assert json.loads(capsys.readouterr().out)["status"] == "success"


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
    monkeypatch.setattr(entrypoint, "_active", lambda unit, repo: False)
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

    validate_record(records[0])
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
        value = ("" if "--property=ReadWritePaths" in command else
                 "loaded\n" if "--property=LoadState" in command else "simple\n")
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
    assert "backtest-worker|web|paper|shadow-core|live-core|limit-up-shadow|reboot-drill|catalog-verify|service-checks|storage-report|daily-digest|health-observer|daily-archive|premarket-limits|tushare-sync" in workflow
    assert 'required = {"component-release-v15", "systemd-component-boundary-v1", "component-web-readiness-v1"}' in workflow
    assert 'entrypoint_version", 0) < 27' in workflow
    assert '.before["backtest-worker-identity"] == .after["backtest-worker-identity"]' in workflow
    assert '.before["shadow-core-identity"] == .after["shadow-core-identity"]' in workflow
    assert '.before["paper-identity"] == .after["paper-identity"]' in workflow
    assert '.before["web-identity"] == .after["web-identity"]' in workflow


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


@pytest.mark.parametrize("unit", ["qsys-reboot-drill.service", "qsys-reboot-drill.timer"])
@pytest.mark.parametrize("state", ["active", "activating", "deactivating", "reloading", ""])
def test_reboot_release_refuses_busy_or_unknown_service_and_timer(monkeypatch, tmp_path, unit, state):
    entrypoint = _production_entrypoint_module()
    def probe(command, **kwargs):
        value = state if unit in command else "inactive"
        return subprocess.CompletedProcess(command, 0, value + "\n", "")
    monkeypatch.setattr(entrypoint, "_run", probe)
    with pytest.raises(ValueError, match="running or unknown"):
        entrypoint._require_idle_reboot_drill(tmp_path)


def test_reboot_component_never_encodes_execution_confirmation():
    import shlex
    from scripts.component_release_config import load_component_releases
    spec = load_component_releases(Path("."))["reboot-drill"]
    lines = spec["dropin_lines"]
    command = shlex.split(next(line for line in lines if line.startswith("ExecStart=/"))[10:])
    assert "--dry-run" in command
    assert not {"--execute", "--approved-day", "--approved-host", "--force"} & set(command)
    assert command[0] == spec["current"] + "/venv/bin/qsys-reboot-drill"
    assert command[command.index("--known-hosts") + 1] == "/etc/qsys/reboot-drill/known_hosts"
    assert command[command.index("--key") + 1] == "/etc/qsys/reboot-drill/windows-reboot"
    writes = [line for line in lines if line.startswith("ReadWritePaths=")]
    assert writes == ["ReadWritePaths=", "ReadWritePaths=/home/ubuntu/qsys/var/service-runs/reboot-drill",
                      "ReadWritePaths=/home/ubuntu/qsys/var/alerts/reboot-drill"]
    assert "User=qsys-reboot-drill" in lines
    assert "Group=qsys-reboot-drill" in lines
    assert {x["path"] for x in spec["state_directories"]} == {line.split("=", 1)[1] for line in writes[1:]}


def test_reboot_rollback_refuses_mutation_when_timer_resumes(monkeypatch, tmp_path):
    entrypoint = _production_entrypoint_module()
    commands = []
    def probe(command, **kwargs):
        commands.append(command)
        value = "active" if "qsys-reboot-drill.timer" in command else "inactive"
        return subprocess.CompletedProcess(command, 0, value + "\n", "")
    monkeypatch.setattr(entrypoint, "_run", probe)
    assert entrypoint._rollback_component("reboot-drill", "uninstalled", tmp_path) is False
    assert not any("sudo" in c for c in commands)


@pytest.mark.parametrize("delete", [False, True])
@pytest.mark.parametrize("directive", ["ExecStart", "User", "ReadWritePaths", "ExecStartPre", "Environment"])
def test_platform_cannot_override_installed_component(monkeypatch, tmp_path, delete, directive):
    entrypoint = _production_entrypoint_module()
    dropin = tmp_path / "unit.service.d/zz-isolated-release.conf"
    dropin.parent.mkdir()
    dropin.write_text("[Service]\nExecStart=/immutable/current/bin/app\n")
    monkeypatch.setattr(entrypoint, "COMPONENT_RELEASES", {"example": {
        "dropin": str(dropin), "dropin_lines": ["ExecStart=", "Environment=BOUND=value"],
    }})
    other = dropin.parent / "zz-legacy.conf"
    other.write_text(f"[Service]\n{directive}=/legacy/checkout\n")
    with pytest.raises(ValueError, match="platform conflicts"):
        entrypoint._require_platform_component_boundary({other: None if delete else other})


def test_platform_preserves_legacy_and_base_unit_updates(monkeypatch, tmp_path):
    entrypoint = _production_entrypoint_module()
    dropin = tmp_path / "unit.service.d/zz-isolated-release.conf"
    dropin.parent.mkdir()
    monkeypatch.setattr(entrypoint, "COMPONENT_RELEASES", {"example": {
        "dropin": str(dropin), "dropin_lines": ["ExecStart="],
    }})
    legacy = dropin.parent / "zz-legacy.conf"
    legacy.write_text("[Service]\nExecStart=/shared/checkout\n")
    entrypoint._require_platform_component_boundary({legacy: legacy})
    dropin.write_text("[Service]\nExecStart=/immutable/current\n")
    base = tmp_path / "unit.service"
    base.write_text("[Service]\nExecStart=/shared/checkout\n")
    env = dropin.parent / "market-data.conf"
    env.write_text("[Service]\nEnvironment=MARKET_DATA=canonical\n")
    entrypoint._require_platform_component_boundary({base: base, env: env})
    with pytest.raises(ValueError, match="component-owned"):
        entrypoint._require_platform_component_boundary({dropin: None})


@pytest.mark.parametrize("preview", [True, False])
def test_platform_conflict_rejected_before_backup_or_restart(monkeypatch, tmp_path, preview):
    entrypoint = _production_entrypoint_module()
    calls = []
    monkeypatch.setattr(entrypoint, "_systemd_artifacts", lambda *args: {})
    def reject(_):
        raise ValueError("platform conflicts")
    monkeypatch.setattr(entrypoint, "_require_platform_component_boundary", reject)
    monkeypatch.setattr(entrypoint, "_run", lambda command, **kw:
                        calls.append(command) or subprocess.CompletedProcess(command, 0, "", ""))
    monkeypatch.setattr(entrypoint, "_backup_systemd", lambda *args: pytest.fail("backup reached"))
    with pytest.raises(ValueError, match="platform conflicts"):
        if preview:
            entrypoint._preview_systemd(tmp_path, "a" * 40, [])
        else:
            entrypoint._apply_systemd({}, tmp_path, "run-1", [])
    assert not any("sudo" in c or "systemctl" in c for c in calls)


@pytest.mark.parametrize("ready_after", [0, 3, None])
def test_component_web_readiness_retries_and_times_out(monkeypatch, ready_after):
    entrypoint = _production_entrypoint_module()
    clock = [0.0]
    probes = []
    monkeypatch.setattr(entrypoint.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(entrypoint.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    def probe(config):
        probes.append(clock[0])
        return ready_after is not None and clock[0] >= ready_after

    monkeypatch.setattr(entrypoint, "_web_healthy", probe)
    assert entrypoint._wait_for_web_healthy({}, timeout_seconds=5) is (ready_after is not None)
    assert probes == ([0.0] if ready_after == 0 else list(range(4 if ready_after == 3 else 6)))
    assert clock[0] <= 5


def test_systemd_apply_prepares_archive_before_loading_inactive_oneshot(monkeypatch, tmp_path):
    entrypoint = _production_entrypoint_module()
    source = tmp_path / 'source.service'
    source.write_text('[Service]\nType=oneshot\nExecStart=/bin/true\n')
    unit_root = tmp_path / 'etc'
    target = unit_root / 'qsys-daily-archive.service'
    work = tmp_path / 'var' / 'daily-archive'
    records = tmp_path / 'var' / 'service-runs' / 'daily-archive'
    records.parent.mkdir(parents=True)
    monkeypatch.setattr(entrypoint, 'SYSTEMD_UNIT_ROOT', unit_root)
    monkeypatch.setattr(entrypoint, 'SYSTEMD_RUNTIME_DIRECTORIES', {target.name: (work, records)})
    monkeypatch.setattr(entrypoint, 'COMPONENT_RELEASES', {})
    monkeypatch.setattr(entrypoint, '_systemd_artifacts', lambda *a: {target: source})
    monkeypatch.setattr(entrypoint, '_active', lambda *a: False)
    monkeypatch.setattr(entrypoint, '_enabled', lambda *a: False)
    def run(command, *, cwd, check=True):
        if command[:4] == ['sudo', '-n', 'install', '-d']:
            Path(command[-1]).mkdir()
        if command[:4] == ['sudo', '-n', 'systemctl', 'daemon-reload']:
            assert work.is_dir() and records.is_dir()
        if '--property=ReadWritePaths' in command:
            return subprocess.CompletedProcess(command, 0, f'{work} {records}', '')
        return _fake_systemd_run(command, cwd=cwd, check=check)
    monkeypatch.setattr(entrypoint, '_run', run)
    result, _ = entrypoint._apply_systemd(
        {'production_record_root': str(tmp_path / 'records' / 'github')},
        tmp_path, 'archive-paths', ['deploy/systemd/qsys-daily-archive.service'])
    assert result['status'] == 'passed'


def test_systemd_runtime_rejects_missing_required_path_even_for_inactive_service(monkeypatch, tmp_path):
    entrypoint = _production_entrypoint_module()
    missing = tmp_path / 'missing'
    monkeypatch.setattr(entrypoint, '_run', lambda command, **kw:
        subprocess.CompletedProcess(command, 0, str(missing), ''))
    with pytest.raises(ValueError, match='required writable path missing'):
        entrypoint._verify_systemd_runtime('qsys-example.service', tmp_path)
    monkeypatch.setattr(entrypoint, '_run', lambda command, **kw:
        subprocess.CompletedProcess(command, 0, '-' + str(missing), ''))
    entrypoint._verify_systemd_runtime('qsys-example.service', tmp_path)


def test_systemd_runtime_preparation_refuses_redirected_parent(monkeypatch, tmp_path):
    entrypoint = _production_entrypoint_module()
    outside = tmp_path / 'outside'
    outside.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(outside, target_is_directory=True)
    target = tmp_path / 'qsys-daily-archive.service'
    monkeypatch.setattr(entrypoint, 'SYSTEMD_UNIT_ROOT', tmp_path)
    monkeypatch.setattr(entrypoint, 'COMPONENT_RELEASES', {})
    monkeypatch.setattr(entrypoint, 'SYSTEMD_RUNTIME_DIRECTORIES', {target.name: (link / 'work',)})
    with pytest.raises(ValueError, match='symlink'):
        entrypoint._prepare_systemd_runtime({target: Path('source')}, tmp_path)
    assert not (outside / 'work').exists()


def test_production_workflow_requires_runtime_path_capability():
    workflow = Path('.github/workflows/deploy-production-linux.yml').read_text()
    assert '"systemd-runtime-paths-v1"' in workflow


@pytest.mark.parametrize("component", ["catalog-verify", "limit-up-shadow"])
def test_readonly_component_identity_never_inherits_workbench_write_group(component):
    from scripts.component_release_config import load_component_releases
    spec = load_component_releases(Path("."))[component]
    assert "Group=qsys-" + component in spec["dropin_lines"]
    assert "SupplementaryGroups=qsys-observers" in spec["dropin_lines"]
    assert not any("qsys-workbench" in line for line in spec["dropin_lines"]
                   if line.startswith(("Group=", "SupplementaryGroups=")))


def test_reboot_component_has_private_dry_only_baseline_transport():
    import json
    from pathlib import Path
    spec = json.loads(Path("deploy/component-releases.json").read_text())["components"]["reboot-drill"]
    lines = "\n".join(spec["dropin_lines"])
    assert "SupplementaryGroups=qsys-observers" in lines
    assert "--dry-run" in lines and "--win-transport readonly-baseline" in lines
    assert "--execute" not in lines
    assert "--config /etc/qsys/components/reboot-drill/deploy.toml" in lines
    assert "InaccessiblePaths=/home/ubuntu/qsys/config" in lines
    assert {p["mode"] for p in spec["state_directories"]} == {"0770"}


@pytest.mark.parametrize('failure', [None, 'binding', 'activation', 'peer'])
def test_live_target_record_uses_latest_binding_and_preserves_other_peers(monkeypatch, tmp_path, capsys, failure):
    entrypoint = _production_entrypoint_module()
    target, production = 'a'*40, 'b'*40
    state = {'active': True, 'current': 'uninstalled', 'pid': '42', 'started': '99', 'bound': False, 'latest': 0}
    records, calls = [], []
    for name, value in {'_branch': lambda repo:'main', '_dirty':lambda repo:False,
                        '_head':lambda repo:production, '_fetch_and_validate_target':lambda *a:'d'*40,
                        '_successful_test_run':lambda *a:'test-1', '_live_preflight':lambda *a:None,
                        '_fingerprints':lambda *a:{'linux':'f'},
                        '_require_live_release_ready':lambda *a,**kw:{'private_config_sha256':'f'*64,'private_strategy_sha256':'e'*64},
                        '_wait_live_ready':lambda *a:True,
                        '_receive_component_archive':lambda digest,root:root/'component.zip',
                        '_extract_component_archive':lambda *a:None, '_verify_component_release':lambda *a:None,
                        '_stage_component_release':lambda *a:None}.items():
        monkeypatch.setattr(entrypoint,name,value)
    monkeypatch.setattr(entrypoint,'_current_component_sha',lambda c:state['current'])
    monkeypatch.setattr(entrypoint,'_active',lambda unit,repo:state['active'])
    monkeypatch.setattr(entrypoint,'_live_identity',lambda repo:{'pid':state['pid'],'started_monotonic':state['started'],'repo_sha':production})
    monkeypatch.setattr(entrypoint,'_unit_identity_token',lambda unit,repo:'changed' if failure=='peer' and state['current']==target and unit=='qsys-monitor.service' else 'same')
    monkeypatch.setattr(entrypoint,'_write_record',lambda config,run_id,record:records.append(record) or tmp_path/'record.json')
    def rollback(component, previous, repo):
        state['current']=previous
        assert state['bound'] and state['latest']==2  # 恢复代码，绝不恢复旧状态。
        return True
    monkeypatch.setattr(entrypoint,'_rollback_component',rollback)
    def run(command, **kwargs):
        calls.append(command)
        if command[-2:]==['stop','qsys-live-core.service']:
            state['active']=False
        elif '--bind' in command:
            assert not state['active']
            state.update(bound=True, latest=1)
            if failure=='binding': raise ValueError('after-marker failure')
            return subprocess.CompletedProcess(command,0,json.dumps({'ok':True,'original_state_retained':True}),'')
        elif entrypoint.GENERIC_COMPONENT_INSTALLER in command:
            assert state['bound'] and not state['active']
            state.update(current=target,latest=2)
            if failure=='activation': raise ValueError('after-activation failure')
        elif command[-2:]==['start','qsys-live-core.service']:
            state.update(active=True,pid=str(int(state['pid'])+1),started=str(int(state['started'])+1))
        return subprocess.CompletedProcess(command,0,'','')
    monkeypatch.setattr(entrypoint,'_run',run)
    args=argparse.Namespace(sha=target,component='live-core',artifact_sha256='e'*64,run_id='live-release',actor='maintainer',repository='owner/repo',pr_number=503,dry_run=False)
    result=entrypoint.component_deploy(args,{'production_repo':str(tmp_path),'production_record_root':str(tmp_path/'records')})
    validate_record(records[0])
    assert result==(0 if failure is None else 1)
    assert records[0]['status']==('success' if failure is None else 'rolled_back')
    assert state['bound'] and state['latest']==(1 if failure=='binding' else 2)
    assert records[0]['before']['live-core-pid']!=records[0]['after']['live-core-pid']
    assert set(entrypoint.COMPONENT_PROTECTED_PEERS['live-core'])=={'paper','shadow-core','web','backtest-worker','health-observer'}
    if failure is None:
        assert any(c['name']=='live-core-transition' and c['status']=='passed' for c in records[0]['checks'])
        if shutil.which('jq'):
            workflow=Path('.github/workflows/deploy-production-component.yml').read_text()
            filter_text=chr(10).join(workflow.split("jq -e --arg sha",1)[1].splitlines()[1:]).split("' record.json",1)[0]
            def accepted(record,component):
                result=subprocess.run(['jq','-e','--arg','sha',target,'--arg','component',component,filter_text],input=json.dumps(record),text=True,capture_output=True)
                return result.returncode==0
            assert accepted(records[0],'live-core')
            changed=json.loads(json.dumps(records[0]))
            changed['after']['web-identity']='changed'
            assert not accepted(changed,'live-core')
            other=json.loads(json.dumps(records[0]))
            other['components']=['health-observer'];other['after']['health-observer']=target
            assert not accepted(other,'health-observer')  # 普通组件不得借Live例外改变PID。



def test_live_release_gate_blocks_trading_window_before_source_or_network(monkeypatch, tmp_path):
    e = _production_entrypoint_module()
    monkeypatch.setattr(e, '_restart_window', lambda repo:{'restart_allowed':False})
    monkeypatch.setattr(e, '_run', lambda *a,**kw:pytest.fail('blocked window must not run preparation'))
    with pytest.raises(ValueError, match='maintenance window'):
        e._require_live_release_ready(tmp_path, 'a'*40, initial=True)


def test_first_live_migration_requires_installed_consumers_but_upgrades_do_not_republish_shared_code(monkeypatch, tmp_path):
    e = _production_entrypoint_module()
    monkeypatch.setattr(e, '_restart_window', lambda repo:{'restart_allowed':True})
    critical=('src/qsys/live/state_binding.py','src/qsys/live/store.py','src/qsys/live/core_cli.py',
              'scripts/kill.py','scripts/prepare_live_core_release.py','src/qsys/web/live_kill_status.py')
    for relative in critical:
        path=tmp_path/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('exact source\n')
    specs={}
    for name in ('web','daily-digest','health-observer','service-checks'):
        current=tmp_path/name;specs[name]={'current':str(current)}
        for module in ('live/state_binding.py','live/store.py','web/live_kill_status.py'):
            path=current/'venv/lib/python3.12/site-packages/qsys'/module
            path.parent.mkdir(parents=True,exist_ok=True);path.write_text('exact source\n')
    monkeypatch.setattr(e,'COMPONENT_RELEASES',specs)
    def run(command,**kw):
        value='exact source\n' if command[:2]==['git','show'] else json.dumps({'private_config_sha256':'f'*64,'private_strategy_sha256':'e'*64})
        return subprocess.CompletedProcess(command,0,value,'')
    monkeypatch.setattr(e,'_run',run)
    assert e._require_live_release_ready(tmp_path,'a'*40,initial=True)['private_config_sha256']=='f'*64
    (tmp_path/'web/venv/lib/python3.12/site-packages/qsys/live/store.py').unlink()
    with pytest.raises(ValueError,match='current production consumer'):
        e._require_live_release_ready(tmp_path,'a'*40,initial=True)
    # 已独立的组件正常升级不再依赖共享源码/四消费者逐字相同，保留稳定v1绑定。
    assert e._require_live_release_ready(tmp_path,'a'*40,initial=False)['private_config_sha256']=='f'*64


def test_restart_calendar_probe_has_budgets_and_runs_outside_checkout(monkeypatch,tmp_path):
    e=_production_entrypoint_module();seen=[]
    def run(command,**kw):
        seen.append((command,kw))
        code=command[command.index('-c')+1]
        compile(code,'restart-probe','exec')
        assert 'memory_limit_mb=128' in code and 'max_temp_directory_mb=128' in code
        assert 'rows.height != 1' in code and 'open_only=False' in code
        return subprocess.CompletedProcess(command,0,json.dumps({'checked_at':'2026-10-22T14:00:00+08:00','trading_day':True,'restart_allowed':False}),'')
    monkeypatch.setattr(e,'_run',run)
    assert not e._restart_window(tmp_path)['restart_allowed']
    assert seen[0][0][:6]==['nice','-n','10','ionice','-c3','timeout']
    assert seen[0][1]['cwd']==Path('/data/qsys/var-deployments')
