from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest


def module():
    spec = importlib.util.spec_from_file_location(
        "reader_deployment", "deploy/linux/qsys-production-deploy-entrypoint.py",
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def fixture(monkeypatch, tmp_path):
    entry = module()
    binding = tmp_path / "binding"
    binding.write_bytes(b"qsys-live-core-state-v1\n")
    binding.chmod(0o644)
    for name in ("KILL", "live-state.sqlite3"):
        (tmp_path / name).write_bytes(b"existing production fact")
    monkeypatch.setattr(entry, "LIVE_STATE_BINDING", binding)
    monkeypatch.setattr(entry, "LIVE_STATE_ROOT", tmp_path)
    monkeypatch.setattr(entry, "_head", lambda repo: "a" * 40)
    real_stat = Path.lstat

    def trusted(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        if path == binding:
            return SimpleNamespace(st_uid=0, st_mode=info.st_mode)
        return info

    monkeypatch.setattr(Path, "lstat", trusted)
    fields = {"LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead",
              "MainPID": "0", "UnitFileState": "disabled", "ExecMainStartTimestampMonotonic": "0"}
    monkeypatch.setattr(entry, "_run", lambda command, **kw: subprocess.CompletedProcess(
        command, 0, "\n".join(f"{k}={v}" for k, v in fields.items()), "",
    ))
    return entry, fields, binding


def test_guard_binds_stopped_unit_kill_and_state_without_requiring_heartbeat(monkeypatch, tmp_path):
    entry, _, _ = fixture(monkeypatch, tmp_path)
    before = entry._state_reader_maintenance_identity(tmp_path)
    assert before["pid"] == "0"
    assert set(json.loads(before["maintenance_facts"])) == {"KILL", "live-state.sqlite3"}
    (tmp_path / "KILL").write_bytes(b"another fact")
    assert entry._state_reader_maintenance_identity(tmp_path) != before


@pytest.mark.parametrize("field,value", [
    ("LoadState", "not-found"), ("ActiveState", "active"), ("SubState", "running"),
    ("MainPID", "12"), ("UnitFileState", "enabled"), ("ActiveState", "failed"),
])
def test_guard_refuses_running_unknown_or_enabled_live(monkeypatch, tmp_path, field, value):
    entry, fields, _ = fixture(monkeypatch, tmp_path)
    fields[field] = value
    with pytest.raises(ValueError, match="inactive, disabled"):
        entry._state_reader_maintenance_identity(tmp_path)


@pytest.mark.parametrize("name", ["KILL", "live-state.sqlite3"])
def test_guard_requires_existing_nonempty_regular_business_facts(monkeypatch, tmp_path, name):
    entry, _, _ = fixture(monkeypatch, tmp_path)
    (tmp_path / name).write_bytes(b"")
    with pytest.raises(ValueError, match="existing state and local KILL"):
        entry._state_reader_maintenance_identity(tmp_path)


def test_guard_rejects_invalid_binding(monkeypatch, tmp_path):
    entry, _, binding = fixture(monkeypatch, tmp_path)
    binding.write_bytes(b"untrusted\n")
    with pytest.raises(ValueError, match="trusted existing"):
        entry._state_reader_maintenance_identity(tmp_path)


@pytest.mark.parametrize("component", ["live-state-reader", "web", "live-core"])
def test_only_reader_uses_stopped_live_guard(monkeypatch, tmp_path, component):
    entry = module()
    monkeypatch.setattr(entry, "_branch", lambda repo: "main")
    monkeypatch.setattr(entry, "_dirty", lambda repo: False)
    monkeypatch.setattr(entry, "_head", lambda repo: "a" * 40)
    monkeypatch.setattr(entry, "_fetch_and_validate_target", lambda *a: "b" * 40)
    monkeypatch.setattr(entry, "_successful_test_run", lambda *a: "success")
    monkeypatch.setattr(entry, "_current_component_sha", lambda *a: "uninstalled")
    monkeypatch.setattr(entry, "_active", lambda *a: False)
    monkeypatch.setattr(entry, "_fingerprints", lambda *a: {"linux": "unchanged"})
    monkeypatch.setattr(entry, "_unit_identity_token", lambda *a: "unchanged")
    calls = []

    def stopped(repo):
        calls.append("stopped")
        return {"pid": "0", "started_monotonic": "0", "repo_sha": "a" * 40,
                "maintenance_facts": "existing state and KILL"}

    def active(repo):
        calls.append("active")
        raise ValueError("live core is not active")

    monkeypatch.setattr(entry, "_state_reader_maintenance_identity", stopped)
    monkeypatch.setattr(entry, "_live_identity", active)
    monkeypatch.setattr(entry, "_live_preflight", lambda *a: pytest.fail("no trading preflight"))
    args = argparse.Namespace(
        component=component, sha="c" * 40, run_id="reader-dry", actor="maintainer",
        repository="owner/repo", pr_number=544, dry_run=True,
    )
    config = {"production_repo": str(tmp_path), "production_record_root": str(tmp_path / "records")}
    if component == "live-state-reader":
        assert entry.component_deploy(args, config) == 0
        assert calls == ["stopped", "stopped"]
    else:
        with pytest.raises(ValueError, match="not active|already be active"):
            entry.component_deploy(args, config)
        assert "stopped" not in calls


def test_reader_staging_drift_prevents_install_and_actual_record_is_valid(monkeypatch, tmp_path):
    from qsys.ops.deployment import validate_record

    entry = module()
    target = "c" * 40
    facts = {"pid": "0", "started_monotonic": "0", "repo_sha": "a" * 40,
             "maintenance_facts": "initial facts"}
    commands, records = [], []
    monkeypatch.setattr(entry, "_branch", lambda *a: "main")
    monkeypatch.setattr(entry, "_dirty", lambda *a: False)
    monkeypatch.setattr(entry, "_head", lambda *a: "a" * 40)
    monkeypatch.setattr(entry, "_fetch_and_validate_target", lambda *a: "b" * 40)
    monkeypatch.setattr(entry, "_successful_test_run", lambda *a: "success")
    monkeypatch.setattr(entry, "_current_component_sha", lambda *a: "uninstalled")
    monkeypatch.setattr(entry, "_active", lambda *a: False)
    monkeypatch.setattr(entry, "_fingerprints", lambda *a: {"linux": "unchanged"})
    monkeypatch.setattr(entry, "_unit_identity_token", lambda *a: "unchanged")
    monkeypatch.setattr(entry, "_state_reader_maintenance_identity", lambda *a: dict(facts))
    monkeypatch.setattr(entry, "_receive_component_archive", lambda digest, root: root / "artifact.zip")
    monkeypatch.setattr(entry, "_extract_component_archive", lambda *a: None)
    monkeypatch.setattr(entry, "_verify_component_release", lambda *a: None)

    def stage(*args):
        facts["maintenance_facts"] = "changed during staging"

    monkeypatch.setattr(entry, "_stage_component_release", stage)
    monkeypatch.setattr(entry, "_run", lambda command, **kw:
                        commands.append(command) or subprocess.CompletedProcess(command, 0, "", ""))

    def write(config, run_id, record):
        validate_record(record)
        records.append(record)
        return tmp_path / "record.json"

    monkeypatch.setattr(entry, "_write_record", write)
    args = argparse.Namespace(component="live-state-reader", sha=target, run_id="reader-drift",
                              actor="maintainer", repository="owner/repo", pr_number=544,
                              dry_run=False, artifact_sha256="e" * 64)
    assert entry.component_deploy(args, {"production_repo": str(tmp_path),
                                        "production_record_root": str(tmp_path / "records")}) == 1
    assert records[0]["status"] == "failure"
    assert not any(entry.GENERIC_COMPONENT_INSTALLER in command for command in commands)
    assert not any("systemctl" in command for command in commands)
