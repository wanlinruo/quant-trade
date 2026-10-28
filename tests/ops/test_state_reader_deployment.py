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


@pytest.mark.parametrize("component", sorted(module().COMPONENT_RELEASES))
def test_only_reader_and_exact_data_components_use_stopped_live_guard(monkeypatch, tmp_path, component):
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
    monkeypatch.setattr(entry, "_require_idle_limit_up_shadow", lambda *_: None)
    monkeypatch.setattr(entry, "_require_idle_reboot_drill", lambda *_: None)
    calls = []

    def stopped(repo):
        calls.append("stopped")
        return {"pid": "0", "started_monotonic": "0", "repo_sha": "a" * 40,
                "maintenance_facts": "existing state and KILL", "tushare_terminal": "fixture-inactive", "catalog_terminal": "fixture-inactive"}

    def active(repo):
        calls.append("active")
        raise ValueError("live core is not active")

    monkeypatch.setattr(entry, "_state_reader_maintenance_identity", stopped)
    monkeypatch.setattr(entry, "_tushare_maintenance_identity", stopped)
    monkeypatch.setattr(entry, "_catalog_maintenance_identity", stopped)
    monkeypatch.setattr(entry, "_live_identity", active)
    monkeypatch.setattr(entry, "_live_preflight", lambda *a: pytest.fail("no trading preflight"))
    args = argparse.Namespace(
        component=component, sha="c" * 40, run_id="reader-dry", actor="maintainer",
        repository="owner/repo", pr_number=544, dry_run=True,
    )
    config = {"production_repo": str(tmp_path), "production_record_root": str(tmp_path / "records")}
    if component in {"live-state-reader", "tushare-sync", "catalog-verify"}:
        assert entry.component_deploy(args, config) == 0
        assert calls == ["stopped", "stopped"]
    else:
        with pytest.raises(ValueError, match="not active|already be active"):
            entry.component_deploy(args, config)
        assert "stopped" not in calls


@pytest.mark.parametrize("component", ["live-state-reader", "tushare-sync", "catalog-verify"])
def test_reader_staging_drift_prevents_install_and_actual_record_is_valid(monkeypatch, tmp_path, component):
    from qsys.ops.deployment import validate_record

    entry = module()
    target = "c" * 40
    facts = {"pid": "0", "started_monotonic": "0", "repo_sha": "a" * 40,
             "maintenance_facts": "initial facts", "tushare_terminal": "fixture-inactive", "catalog_terminal": "fixture-inactive"}
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
    monkeypatch.setattr(entry, "_tushare_maintenance_identity", lambda *a: dict(facts))
    monkeypatch.setattr(entry, "_catalog_maintenance_identity", lambda *a: dict(facts))
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
    args = argparse.Namespace(component=component, sha=target, run_id="reader-drift",
                              actor="maintainer", repository="owner/repo", pr_number=544,
                              dry_run=False, artifact_sha256="e" * 64)
    assert entry.component_deploy(args, {"production_repo": str(tmp_path),
                                        "production_record_root": str(tmp_path / "records")}) == 1
    assert records[0]["status"] == "failure"
    assert not any(entry.GENERIC_COMPONENT_INSTALLER in command for command in commands)
    assert not any("systemctl" in command for command in commands)


@pytest.mark.parametrize("component,label", [("tushare-sync", "tushare"), ("catalog-verify", "catalog")])
@pytest.mark.parametrize("unit,field,value", [
    ("timer", "ActiveState", "active"), ("timer", "ActiveState", "activating"),
    ("timer", "LoadState", "not-found"), ("service", "ActiveState", "active"),
    ("service", "ActiveState", "failed"), ("service", "MainPID", "12"),
])
def test_data_guard_requires_quiescent_loaded_timer_and_oneshot(monkeypatch, tmp_path, component, label, unit, field, value):
    entry = module()
    monkeypatch.setattr(entry, "_state_reader_maintenance_identity", lambda *_: {"pid": "0"})
    states = {kind: {"LoadState": "loaded", "ActiveState": "inactive", "MainPID": "0"}
              for kind in ["timer", "service"]}
    states[unit][field] = value
    def run(command, **_):
        fields = states[command[2].rsplit(".", 1)[1]]
        return subprocess.CompletedProcess(command, 0, "\n".join(f"{k}={v}" for k,v in fields.items()), "")
    monkeypatch.setattr(entry, "_run", run)
    with pytest.raises(ValueError, match="inactive timer and idle oneshot"):
        entry._quiescent_data_maintenance_identity(tmp_path, component)


@pytest.mark.parametrize("component", ["tushare-sync", "catalog-verify"])
def test_data_guard_keeps_actual_kill_and_state_binding_checks(monkeypatch, tmp_path, component):
    entry, fields, _ = fixture(monkeypatch, tmp_path)
    assert entry._quiescent_data_maintenance_identity(tmp_path, component)["pid"] == "0"
    fields["UnitFileState"] = "enabled"
    with pytest.raises(ValueError, match="inactive, disabled"):
        entry._quiescent_data_maintenance_identity(tmp_path, component)


@pytest.mark.parametrize("component,label", [("tushare-sync", "tushare"), ("catalog-verify", "catalog")])
@pytest.mark.parametrize("broken", [None, "pid", "invocation", "result", "status", "timer"])
def test_failed_data_oneshot_requires_preserved_exit_and_invocation(monkeypatch, tmp_path, component, label, broken):
    entry = module()
    monkeypatch.setattr(entry, "_state_reader_maintenance_identity", lambda *_: {"pid": "0"})
    timer = {"LoadState": "loaded", "ActiveState": "inactive", "MainPID": "0"}
    service = {"LoadState": "loaded", "ActiveState": "failed", "MainPID": "0",
               "Result": "exit-code", "ExecMainStatus": "1", "InvocationID": "a" * 32}
    if broken == "pid": service["MainPID"] = "123"
    if broken == "invocation": service["InvocationID"] = ""
    if broken == "result": service["Result"] = "success"
    if broken == "status": service["ExecMainStatus"] = "0"
    if broken == "timer": timer["ActiveState"] = "failed"
    def run(command, **_):
        values = timer if command[2].endswith('.timer') else service
        return subprocess.CompletedProcess(command, 0, "\n".join(f"{k}={v}" for k,v in values.items()), "")
    monkeypatch.setattr(entry, "_run", run)
    if broken is not None:
        with pytest.raises(ValueError, match="inactive timer and idle oneshot"):
            entry._quiescent_data_maintenance_identity(tmp_path, component)
    else:
        before = entry._quiescent_data_maintenance_identity(tmp_path, component)
        assert json.loads(before[f'{label}_terminal']) == service
        service['InvocationID'] = 'b' * 32
        assert entry._quiescent_data_maintenance_identity(tmp_path, component) != before


def test_tushare_compensation_restores_previous_arguments_and_metadata(monkeypatch, tmp_path):
    import shutil
    entry = module()
    dropin = tmp_path / "unit.conf"
    dropin.write_bytes(b"ExecStart=old-code --old-arguments\n")
    dropin.chmod(0o640)
    before = dropin.stat()
    monkeypatch.setitem(entry.COMPONENT_RELEASES["tushare-sync"], "dropin", str(dropin))
    commands = []
    def run(command, **_):
        commands.append(command)
        if "cp" in command:
            shutil.copy2(command[-2], command[-1])
        elif "mv" in command:
            Path(command[-2]).replace(command[-1])
        return subprocess.CompletedProcess(command, 0, "user::rw-\n", "")
    monkeypatch.setattr(entry, "_run", run)
    backup = entry._backup_tushare_dropin(tmp_path, tmp_path / "release", "a" * 40)
    dropin.write_bytes(b"ExecStart=new-code --new-arguments\n")
    dropin.chmod(0o600)
    assert entry._restore_tushare_dropin(tmp_path, backup)
    assert dropin.read_bytes() == b"ExecStart=old-code --old-arguments\n"
    assert (dropin.stat().st_mode, dropin.stat().st_mtime_ns) == (before.st_mode, before.st_mtime_ns)
    assert backup.is_file()
    assert any(c[-1] == "daemon-reload" for c in commands)
    with pytest.raises(ValueError, match="already exists"):
        entry._backup_tushare_dropin(tmp_path, tmp_path / "release", "a" * 40)


def test_catalog_maintenance_records_preserved_failed_terminal_without_starting_any_unit(monkeypatch, tmp_path):
    from qsys.ops.deployment import validate_record

    entry = module()
    target = "c" * 40
    current = ["b" * 40]
    facts = {"pid": "0", "started_monotonic": "0", "repo_sha": "a" * 40,
             "maintenance_facts": "unchanged state and KILL",
             "catalog_terminal": json.dumps({"ActiveState": "failed", "MainPID": "0",
                                              "Result": "exit-code", "ExecMainStatus": "1",
                                              "InvocationID": "d" * 32})}
    records, commands = [], []
    monkeypatch.setattr(entry, "_branch", lambda *a: "main")
    monkeypatch.setattr(entry, "_dirty", lambda *a: False)
    monkeypatch.setattr(entry, "_head", lambda *a: "a" * 40)
    monkeypatch.setattr(entry, "_fetch_and_validate_target", lambda *a: "e" * 40)
    monkeypatch.setattr(entry, "_successful_test_run", lambda *a: "validated-test")
    monkeypatch.setattr(entry, "_current_component_sha", lambda *a: current[0])
    monkeypatch.setattr(entry, "_active", lambda *a: False)
    monkeypatch.setattr(entry, "_catalog_maintenance_identity", lambda *a: dict(facts))
    monkeypatch.setattr(entry, "_live_preflight", lambda *a: pytest.fail("trading preflight"))
    monkeypatch.setattr(entry, "_live_identity", lambda *a: pytest.fail("active Live identity"))
    monkeypatch.setattr(entry, "_fingerprints", lambda *a: {})
    monkeypatch.setattr(entry, "_unit_identity_token", lambda *a: "preserved-peer")
    monkeypatch.setattr(entry, "_receive_component_archive", lambda digest, root: root / "artifact.zip")
    monkeypatch.setattr(entry, "_extract_component_archive", lambda *a: None)
    monkeypatch.setattr(entry, "_verify_component_release", lambda *a: None)
    monkeypatch.setattr(entry, "_stage_component_release", lambda *a: None)

    def run(command, **kwargs):
        commands.append(command)
        if entry.GENERIC_COMPONENT_INSTALLER in command:
            current[0] = target
        return subprocess.CompletedProcess(command, 0, "", "")

    def write(config, run_id, record):
        validate_record(record)
        records.append(record)
        return tmp_path / "record.json"

    monkeypatch.setattr(entry, "_run", run)
    monkeypatch.setattr(entry, "_write_record", write)
    args = argparse.Namespace(component="catalog-verify", sha=target, run_id="catalog-maintenance",
                              actor="maintainer", repository="owner/repo", pr_number=562,
                              dry_run=False, artifact_sha256="f" * 64)
    assert entry.component_deploy(args, {"production_repo": str(tmp_path),
                                        "production_record_root": str(tmp_path / "records")}) == 0
    assert records[0]["status"] == "success"
    assert any(item["name"] == "catalog-terminal-preserved" and
               item["detail"] == facts["catalog_terminal"] for item in records[0]["checks"])
    assert not any("systemctl" in command for command in commands)
    assert len(entry.COMPONENT_PROTECTED_PEERS["catalog-verify"]) == 8


def test_quiescent_data_guard_rejects_other_component_before_inspecting_live(monkeypatch, tmp_path):
    entry = module()
    monkeypatch.setattr(entry, "_state_reader_maintenance_identity", lambda *a: pytest.fail("guard widened"))
    with pytest.raises(ValueError, match="unsupported"):
        entry._quiescent_data_maintenance_identity(tmp_path, "paper")
