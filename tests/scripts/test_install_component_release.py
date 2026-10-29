from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from scripts import install_component_release as installer


def _spec(tmp_path: Path, sha: str) -> tuple[dict[str, object], Path, Path, Path]:
    release = tmp_path / "releases" / sha
    executable = release / "venv/bin/qsys-example"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    (release / "release.json").write_text("{}\n", encoding="utf-8")
    current = tmp_path / "component/current"
    dropin = tmp_path / "systemd/qsys-example.service.d/release.conf"
    spec: dict[str, object] = {
        "entrypoint": {"command": "qsys-example", "target": "qsys.example:main"},
        "scan_roots": ["src/qsys/example"],
        "release_root": str(tmp_path / "releases"),
        "current": str(current),
        "executable": "venv/bin/qsys-example",
        "unit": "qsys-example.service",
        "dropin": str(dropin),
        "state_directories": [{
            "path": str(tmp_path / "state"), "owner": "ubuntu",
            "group": "ubuntu", "mode": "0750",
        }],
        "seed": None,
        "dropin_lines": ["[Service]", "ExecStart=/component/current/qsys-example"],
    }
    return spec, release, current, dropin


def _fake_sudo(*command: str, check: bool = True):
    if command[0] in {"chown", "setfacl"} or command[:2] == ("systemctl", "daemon-reload"):
        return subprocess.CompletedProcess(command, 0, "", "")
    if command[0] == "install" and "-d" in command:
        Path(command[-1]).mkdir(parents=True, exist_ok=True)
    elif command[0] == "install":
        target = Path(command[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(command[-2], target)
    elif command[0] == "ln":
        target = Path(command[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        if "-sfn" in command:
            target.unlink(missing_ok=True)
            target.symlink_to(command[-2])
        else:
            os.link(command[-2],target)
    elif command[0] == "mv":
        os.replace(command[-2], command[-1])
    elif command[0] == "rm":
        Path(command[-1]).unlink(missing_ok=True)
    else:
        raise AssertionError(command)
    return subprocess.CompletedProcess(command, 0, "", "")


def _prepare(monkeypatch, tmp_path: Path, sha: str):
    spec, release, current, dropin = _spec(tmp_path, sha)
    monkeypatch.setattr(
        installer, "load_component_releases", lambda repo: {"example": spec},
    )
    monkeypatch.setattr(installer, "verify", lambda *args: {})
    monkeypatch.setattr(installer, "_sudo", _fake_sudo)
    monkeypatch.setattr(
        installer, "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 3, "", ""),
    )
    return spec, release, current, dropin


def test_generic_installer_activates_whitelisted_release(monkeypatch, tmp_path: Path) -> None:
    sha = "a" * 40
    _, release, current, dropin = _prepare(monkeypatch, tmp_path, sha)

    assert installer.activate(tmp_path, sha, "example") == release

    assert current.resolve() == release.resolve()
    assert dropin.read_text(encoding="utf-8") == (
        "[Service]\nExecStart=/component/current/qsys-example\n"
    )


def test_generic_installer_rolls_back_pointer_and_dropin_after_activation_failure(
    monkeypatch, tmp_path: Path,
) -> None:
    sha = "b" * 40
    _, _, current, dropin = _prepare(monkeypatch, tmp_path, sha)
    previous = tmp_path / "releases" / ("a" * 40)
    previous.mkdir(parents=True)
    current.parent.mkdir(parents=True)
    current.symlink_to(previous)
    dropin.parent.mkdir(parents=True)
    dropin.write_text("old-dropin\n", encoding="utf-8")
    calls = 0

    def failing_sudo(*command: str, check: bool = True):
        nonlocal calls
        if command[:2] == ("systemctl", "daemon-reload"):
            calls += 1
            if calls == 1 and check:
                raise subprocess.CalledProcessError(1, command)
        return _fake_sudo(*command, check=check)

    monkeypatch.setattr(installer, "_sudo", failing_sudo)

    with pytest.raises(subprocess.CalledProcessError):
        installer.activate(tmp_path, sha, "example")

    assert current.resolve() == previous.resolve()
    assert dropin.read_text(encoding="utf-8") == "old-dropin\n"


def test_generic_installer_fails_before_activation_when_seed_is_missing(
    monkeypatch, tmp_path: Path,
) -> None:
    sha = "c" * 40
    spec, _, current, dropin = _prepare(monkeypatch, tmp_path, sha)
    spec["seed"] = {
        "source": "missing.json", "source_environment": "QSYS_TEST_SEED",
        "target": str(tmp_path / "state/seed.json"), "owner": "ubuntu",
        "group": "ubuntu", "mode": "0640",
    }

    with pytest.raises(ValueError, match="without seed"):
        installer.activate(tmp_path, sha, "example")

    assert not current.exists()
    assert not dropin.exists()


def test_catalog_upgrade_never_recreates_a_missing_frozen_baseline(monkeypatch, tmp_path):
    sha = "c" * 40
    spec, _, current, dropin = _prepare(monkeypatch, tmp_path, sha)
    monkeypatch.setattr(installer, "load_component_releases", lambda repo: {"catalog-verify": spec})
    current.parent.mkdir(parents=True)
    previous = tmp_path / "previous"
    previous.mkdir()
    current.symlink_to(previous)
    source = tmp_path / "old-baseline.json"
    source.write_text('{"historical": true}')
    target = tmp_path / "state/frozen_baseline.json"
    spec["seed"] = dict(source=source.name, source_environment="QSYS_TEST_SEED",
                        target=str(target), owner="ubuntu", group="ubuntu", mode="0640")
    with pytest.raises(ValueError, match="missing frozen baseline"):
        installer.activate(tmp_path, sha, "catalog-verify")
    assert current.resolve() == previous
    assert not target.exists()
    assert not dropin.exists()


def test_generic_installer_rejects_repository_spec_drift(
    monkeypatch, tmp_path: Path,
) -> None:
    sha = "d" * 40
    spec, _, current, _ = _prepare(monkeypatch, tmp_path, sha)
    drifted = dict(spec)
    drifted["executable"] = "venv/bin/tampered"
    monkeypatch.setattr(
        installer, "load_component_releases_file",
        lambda path: {"example": drifted},
    )

    with pytest.raises(ValueError, match="differs from installed whitelist"):
        installer.activate(
            tmp_path, sha, "example", trusted_spec=tmp_path / "trusted.json",
        )

    assert not current.exists()


@pytest.mark.parametrize("unit", ["qsys-reboot-drill.service", "qsys-reboot-drill.timer"])
@pytest.mark.parametrize("state", ["active", "activating", "deactivating", ""])
def test_reboot_installer_final_idle_gate_prevents_switch(monkeypatch, tmp_path, unit, state):
    sha = "a" * 40
    spec, _, current, dropin = _prepare(monkeypatch, tmp_path, sha)
    spec["unit"] = "qsys-reboot-drill.service"
    monkeypatch.setattr(installer, "load_component_releases", lambda repo: {"reboot-drill": spec})
    def probe(command, **kwargs):
        if "show" in command:
            value = state if unit in command else "inactive"
            return subprocess.CompletedProcess(command, 0, value + "\n", "")
        return subprocess.CompletedProcess(command, 3, "", "")
    monkeypatch.setattr(installer, "_run", probe)
    with pytest.raises(ValueError, match="running or unknown"):
        installer.activate(tmp_path, sha, "reboot-drill")
    assert not current.exists()
    assert not dropin.exists()


@pytest.mark.parametrize("fail_reload", [False, True])
def test_reboot_switch_and_restore_never_start_or_stop_units(monkeypatch, tmp_path, fail_reload):
    sha = "a" * 40
    spec, release, current, dropin = _prepare(monkeypatch, tmp_path, sha)
    spec["unit"] = "qsys-reboot-drill.service"
    monkeypatch.setattr(installer, "load_component_releases", lambda repo: {"reboot-drill": spec})
    def probe(command, **kwargs):
        return subprocess.CompletedProcess(command, 0 if "show" in command else 3,
                                           "inactive\n" if "show" in command else "", "")
    monkeypatch.setattr(installer, "_run", probe)
    commands = []
    def sudo(*command, check=True):
        commands.append(command)
        if fail_reload and command == ("systemctl", "daemon-reload") and check:
            raise subprocess.CalledProcessError(1, command)
        return _fake_sudo(*command, check=check)
    monkeypatch.setattr(installer, "_sudo", sudo)
    if fail_reload:
        with pytest.raises(subprocess.CalledProcessError):
            installer.activate(tmp_path, sha, "reboot-drill")
        assert not current.exists()
        assert not dropin.exists()
    else:
        assert installer.activate(tmp_path, sha, "reboot-drill") == release
        assert current.resolve() == release
    assert not any("systemctl" in c and any(v in c for v in ("start", "stop", "restart")) for c in commands)


@pytest.mark.parametrize("directive", ["ExecStart", "User", "ReadWritePaths", "ExecStartPre"])
def test_later_platform_override_blocks_component_before_sudo(monkeypatch, tmp_path, directive):
    sha = "a" * 40
    spec, _, current, dropin = _prepare(monkeypatch, tmp_path, sha)
    dropin.parent.mkdir(parents=True)
    (dropin.parent / "zz-legacy.conf").write_text(f"[Service]\n{directive}=/shared/checkout\n")
    monkeypatch.setattr(installer, "_sudo", lambda *args, **kw: pytest.fail("sudo reached"))
    with pytest.raises(ValueError, match="later platform drop-in"):
        installer.activate(tmp_path, sha, "example")
    assert not current.exists()
    assert not dropin.exists()


def test_nonconflicting_later_platform_environment_is_allowed(monkeypatch, tmp_path):
    sha = "a" * 40
    _, release, _, dropin = _prepare(monkeypatch, tmp_path, sha)
    dropin.parent.mkdir(parents=True)
    (dropin.parent / "zz-market.conf").write_text("[Service]\nEnvironment=MARKET=canonical\n")
    assert installer.activate(tmp_path, sha, "example") == release


def _history_database(path, *, wal=False):
    connection = sqlite3.connect(path)
    if wal:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
    for name in ("runs", "entries", "outcomes"):
        table = f"limit_up_shadow_{name}"
        connection.execute(f"CREATE TABLE {table}(id TEXT PRIMARY KEY, value BLOB)")
        connection.execute(f"INSERT INTO {table} VALUES (?,?)", ("historical", b"\x00original\xff"))
        connection.execute(f"INSERT INTO {table} VALUES (?,?)", ("null-value", None))
    connection.commit()
    return connection


def _sqlite_seed(source, target):
    return {"source":str(source), "source_environment":"QSYS_LIMIT_UP_SHADOW_LEGACY_STATE",
            "target":str(target), "owner":"ubuntu", "group":"ubuntu", "mode":"0640"}


def _only_reader_acl(*command, **kwargs):
    assert command[:-1] == ("setfacl", "-P", "-m", "u:ubuntu:r--,g:qsys-observers:r--", "--")
    return subprocess.CompletedProcess(command, 0, "", "")


def test_sqlite_seed_copies_committed_wal_history_and_preserves_original(monkeypatch, tmp_path):
    source = tmp_path / "legacy.sqlite3"
    target = tmp_path / "isolated.sqlite3"
    original = _history_database(source, wal=True)
    before = source.read_bytes()
    expected = installer._sqlite_fingerprint(original)
    monkeypatch.setattr(installer, "_sudo", _fake_sudo)
    try:
        installer._seed_limit_up_shadow(source,_sqlite_seed(source,target),initial=True)
        assert installer._read_sqlite_fingerprint(target) == expected
        assert source.read_bytes() == before
        assert installer._sqlite_fingerprint(original) == expected
        with sqlite3.connect(target) as copied:
            assert copied.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        assert not list(tmp_path.glob("*.seed-*"))
    finally:
        original.close()


def test_sqlite_seed_applies_reader_acl_before_atomic_publication(monkeypatch, tmp_path):
    source, target = tmp_path / "legacy.sqlite3", tmp_path / "isolated.sqlite3"
    _history_database(source).close()
    commands = []

    def sudo(*command, **kwargs):
        commands.append(command)
        if command[:2] == ("ln", "-T"):
            staged = command[-2]
            assert ("setfacl", "-P", "-m", "u:ubuntu:r--,g:qsys-observers:r--", "--", staged) in commands
        return _fake_sudo(*command, **kwargs)

    monkeypatch.setattr(installer, "_sudo", sudo)
    installer._seed_limit_up_shadow(source, _sqlite_seed(source, target), initial=True)
    assert not any(command[-1] == str(source) for command in commands if command[0] == "setfacl")


def test_retained_seed_reader_acl_is_repaired_before_unprivileged_verification(monkeypatch, tmp_path):
    source, target = tmp_path / "legacy.sqlite3", tmp_path / "isolated.sqlite3"
    _history_database(source).close()
    shutil.copyfile(source, target)
    before = target.read_bytes()
    repaired = False
    original = installer._read_sqlite_fingerprint

    def sudo(*command, **kwargs):
        nonlocal repaired
        _only_reader_acl(*command, **kwargs)
        assert command[-1] == str(target)
        repaired = True

    def read(path, **kwargs):
        if path == target:
            assert repaired, "unprivileged verification reached an unreadable retained seed"
        return original(path, **kwargs)

    monkeypatch.setattr(installer, "_sudo", sudo)
    monkeypatch.setattr(installer, "_read_sqlite_fingerprint", read)
    installer._seed_limit_up_shadow(source, _sqlite_seed(source, target), initial=True)
    assert repaired and target.read_bytes() == before


def test_sqlite_upgrade_keeps_new_history_without_requiring_legacy(monkeypatch, tmp_path):
    target = tmp_path / "isolated.sqlite3"
    with _history_database(target) as current:
        current.execute("INSERT INTO limit_up_shadow_runs VALUES ('new-day','new-result')")
    before = target.read_bytes()
    monkeypatch.setattr(installer, "_sudo", _only_reader_acl)
    installer._seed_limit_up_shadow(tmp_path/"missing-legacy.sqlite3",
                                   _sqlite_seed(tmp_path/"missing",target),initial=False)
    assert target.read_bytes() == before


def test_existing_wal_target_is_rejected_without_changing_journal(monkeypatch,tmp_path):
    target=tmp_path/"isolated.sqlite3"
    connection=_history_database(target,wal=True)
    monkeypatch.setattr(installer,"_sudo",_only_reader_acl)
    try:
        with pytest.raises(ValueError,match="DELETE journal"):
            installer._seed_limit_up_shadow(tmp_path/"missing",_sqlite_seed(tmp_path/"missing",target),initial=False)
        assert connection.execute("PRAGMA journal_mode").fetchone()==("wal",)
    finally:
        connection.close()


def test_sqlite_upgrade_rejects_missing_target_without_restoring_old_history(monkeypatch, tmp_path):
    source = tmp_path / "legacy.sqlite3"
    target = tmp_path / "isolated.sqlite3"
    _history_database(source).close()
    monkeypatch.setattr(installer, "_sudo", lambda *a, **k: pytest.fail("unexpected seed"))
    with pytest.raises(ValueError, match="missing during upgrade"):
        installer._seed_limit_up_shadow(source, _sqlite_seed(source, target), initial=False)
    assert not target.exists()


def test_first_sqlite_migration_rejects_different_existing_history(monkeypatch, tmp_path):
    source = tmp_path/"legacy.sqlite3"
    target = tmp_path/"isolated.sqlite3"
    _history_database(source).close()
    with _history_database(target) as copied:
        copied.execute("INSERT INTO limit_up_shadow_runs VALUES ('other','do-not-overwrite')")
    before = target.read_bytes()
    monkeypatch.setattr(installer,"_sudo",_only_reader_acl)
    with pytest.raises(ValueError, match="differs from legacy"):
        installer._seed_limit_up_shadow(source,_sqlite_seed(source,target),initial=True)
    assert target.read_bytes() == before


def test_sqlite_seed_rejects_symlink_and_missing_history_before_publish(monkeypatch, tmp_path):
    source = tmp_path/"empty.sqlite3"
    source.touch()
    target = tmp_path/"isolated.sqlite3"
    monkeypatch.setattr(installer,"_sudo",_fake_sudo)
    with pytest.raises(ValueError, match="missing history tables"):
        installer._seed_limit_up_shadow(source,_sqlite_seed(source,target),initial=True)
    assert not target.exists()
    target.symlink_to(source)
    with pytest.raises(ValueError, match="symlinks"):
        installer._seed_limit_up_shadow(source,_sqlite_seed(source,target),initial=True)
    assert source.read_bytes() == b""


def test_sqlite_target_publication_cannot_overwrite_a_racing_file(monkeypatch, tmp_path):
    source = tmp_path/"legacy.sqlite3"
    target = tmp_path/"isolated.sqlite3"
    _history_database(source).close()
    def race(*command,check=True):
        if command[:2] == ("ln","-T"):
            target.write_bytes(b"concurrent-state")
        return _fake_sudo(*command,check=check)
    monkeypatch.setattr(installer,"_sudo",race)
    with pytest.raises(FileExistsError):
        installer._seed_limit_up_shadow(source,_sqlite_seed(source,target),initial=True)
    assert target.read_bytes() == b"concurrent-state"


@pytest.mark.parametrize("state",["active","activating","deactivating","reloading",""])
def test_limit_up_shadow_installer_rejects_unknown_or_busy_state(monkeypatch,tmp_path,state):
    sha = "a"*40
    spec,_,current,_ = _prepare(monkeypatch,tmp_path,sha)
    monkeypatch.setattr(installer,"load_component_releases",lambda repo:{"limit-up-shadow":spec})
    monkeypatch.setattr(installer,"_run",lambda cmd,**kw:subprocess.CompletedProcess(
        cmd,0 if "show" in cmd else 3,state+"\n",""))
    monkeypatch.setattr(installer,"_sudo",lambda *a,**k:pytest.fail("sudo reached"))
    with pytest.raises(ValueError,match="running or unknown"):
        installer.activate(tmp_path,sha,"limit-up-shadow")
    assert not current.exists()


def test_limit_up_activation_seeds_history_before_switching_pointer(monkeypatch,tmp_path):
    sha = "a"*40
    spec,release,current,_ = _prepare(monkeypatch,tmp_path,sha)
    source=tmp_path/"legacy.sqlite3"
    _history_database(source).close()
    target=tmp_path/"state/history.sqlite3"
    spec["seed"]=_sqlite_seed(source,target)
    monkeypatch.setattr(installer,"load_component_releases",lambda repo:{"limit-up-shadow":spec})
    monkeypatch.setattr(installer,"_run",lambda cmd,**kw:subprocess.CompletedProcess(
        cmd,0 if "show" in cmd else 3,"inactive\n" if "show" in cmd else "",""))
    assert installer.activate(tmp_path,sha,"limit-up-shadow")==release
    assert current.resolve()==release
    assert installer._read_sqlite_fingerprint(target)==installer._read_sqlite_fingerprint(source)


def test_seeded_history_is_visible_to_the_existing_web_reader(monkeypatch,tmp_path):
    from datetime import date
    from qsys.web.limit_up_shadow import LimitUpShadowPublisher, LimitUpShadowReader, LimitUpShadowSnapshot

    source=tmp_path/"legacy.sqlite3"
    target=tmp_path/"isolated.sqlite3"
    snapshot=LimitUpShadowSnapshot.model_validate({
        "prediction_date":"2026-10-20", "target_session":"2026-10-21",
        "generated_at":"2026-10-20T19:35:00+08:00", "model_version":"fixture-v1",
        "model_sha":"1234567", "data_version":"fixture-data", "data_sha":"abcdef0",
        "eligible_count":1, "entries":[{"rank":1,"symbol":"600000.SH",
            "p_touch":0.5,"p_seal_given_touch":0.5,"p_close_limit":0.25}],
    })
    LimitUpShadowPublisher(source).publish(snapshot)
    old=LimitUpShadowReader(source).view(date(2026,10,20),today=date(2026,10,21))
    monkeypatch.setattr(installer,"_sudo",_fake_sudo)
    installer._seed_limit_up_shadow(source,_sqlite_seed(source,target),initial=True)
    new=LimitUpShadowReader(target).view(date(2026,10,20),today=date(2026,10,21))
    assert new==old and new.available
    assert new.snapshot==snapshot


def test_tushare_failed_activation_restores_code_but_preserves_cash_policy(monkeypatch, tmp_path):
    from qsys.data.pipeline.dividend_sync import prepare_cash_source

    sha = 'c' * 40
    spec, _, current, dropin = _prepare(monkeypatch, tmp_path, sha)
    state = tmp_path / 'tushare-state'
    spec['state_directories'] = [
        {'path': str(state), 'owner': 'qsys-tushare-sync', 'group': 'qsys-tushare-sync', 'mode': '0750'},
    ]
    monkeypatch.setattr(installer, 'load_component_releases', lambda repo: {'tushare-sync': spec})
    previous = tmp_path / 'releases' / ('b' * 40)
    previous.mkdir(parents=True)
    current.parent.mkdir(parents=True)
    current.symlink_to(previous)
    dropin.parent.mkdir(parents=True)
    old_unit = b'[Service]\nExecStart=/baseline/qsys-tushare-sync --dividend-sync\n'
    dropin.write_bytes(old_unit)
    # 首次导入还未成功，只有独立策略绑定，不能依赖成功checkpoint保护选源。
    prepare_cash_source(state, cash_source='tushare', cash_client=object(), run_id='activated')
    policy = state / 'dividends' / 'cash-source-policy.json'
    original = policy.read_bytes()
    def sudo(*command, check=True):
        if command == ('systemctl', 'daemon-reload') and check:
            raise subprocess.CalledProcessError(1, command)
        return _fake_sudo(*command, check=check)
    monkeypatch.setattr(installer, '_sudo', sudo)
    with pytest.raises(subprocess.CalledProcessError):
        installer.activate(tmp_path, sha, 'tushare-sync')
    assert current.resolve() == previous and dropin.read_bytes() == old_unit
    assert policy.read_bytes() == original
    assert not (policy.parent / 'last-imported.json').exists()
    with pytest.raises(ValueError, match='silently revert'):
        prepare_cash_source(state, cash_source='qmt', cash_client=None, run_id='compensated-default')
