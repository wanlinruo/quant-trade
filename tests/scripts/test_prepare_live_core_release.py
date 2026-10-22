import sqlite3
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from scripts.prepare_live_core_release import dump_toml, snapshot
from scripts.verify_live_core_preproduction import state_fingerprint
from scripts import prepare_live_core_release as preparation


@pytest.mark.parametrize('kind', ['missing', 'file', 'symlink', 'nested-symlink'])
def test_invalid_journal_is_rejected_before_config_state_or_acl_mutation(monkeypatch, tmp_path, kind):
    root = tmp_path / 'warehouse'
    root.mkdir()
    for name in preparation.JOURNAL_TABLES[:-1]:
        (root / name).mkdir()
    target = root / preparation.JOURNAL_TABLES[-1]
    if kind == 'file':
        target.write_bytes(b'preserve')
    elif kind == 'symlink':
        target.symlink_to(root / preparation.JOURNAL_TABLES[0], target_is_directory=True)
    elif kind == 'nested-symlink':
        target.mkdir()
        (target / 'escape').symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setattr(preparation, 'JOURNAL_ROOT', root)
    monkeypatch.setattr(preparation, 'sudo', lambda *a: pytest.fail('no ACL or identity command before path rejection'))
    monkeypatch.setattr(preparation, 'expected_config', lambda *a: pytest.fail('journal gate precedes configuration'))
    with pytest.raises(ValueError, match='journal'):
        preparation.check(tmp_path)
    with pytest.raises(ValueError, match='journal'):
        preparation.prepare_journals()
    assert not (tmp_path / 'var').exists()


def test_real_identity_permission_probe_is_read_only_and_failure_precedes_config(monkeypatch, tmp_path):
    root = tmp_path / 'warehouse'
    for name in preparation.JOURNAL_TABLES:
        (root / name).mkdir(parents=True)
    monkeypatch.setattr(preparation, 'JOURNAL_ROOT', root)
    seen = []
    def denied(*command):
        seen.append(command)
        code = command[command.index('-c') + 1]
        compile(code, 'journal-permission-probe', 'exec')
        assert 'os.access' in code and 'onerror=fail' in code
        raise subprocess.CalledProcessError(1, command, stderr='permission denied')
    monkeypatch.setattr(preparation, 'sudo', denied)
    monkeypatch.setattr(preparation, 'expected_config', lambda *a: pytest.fail('permission denial precedes config/state'))
    with pytest.raises(subprocess.CalledProcessError):
        preparation.check(tmp_path)
    assert seen[0][:3] == ('-u', 'qsys-live-core', '/usr/bin/python3')
    assert not list(root.rglob('*.sqlite3'))


def test_prepare_script_executes_directly_without_repository_on_pythonpath(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    environment = dict(os.environ, PYTHONPATH=str(repo / 'src'))
    result = subprocess.run([sys.executable, str(repo / 'scripts/prepare_live_core_release.py'), '--help'],
                            cwd=tmp_path, env=environment, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert '--check-only' in result.stdout


def test_config_serializer_preserves_nested_explicit_values():
    value = {'risk': {'enabled_capital': 10000.0, 'enabled': True},
             'strategy': {'file': '/private/策略.py', 'params': {'exact.key': 0, 'symbols': ['x', 'y']}},
             'safety': {'protected_account_ids': ['validation-only']}}
    assert tomllib.loads(dump_toml(value)) == value
    with pytest.raises(ValueError):
        dump_toml({'risk': {'unsupported': None}})


def test_consistent_seed_keeps_pending_completed_and_trade_facts_in_wal(tmp_path):
    source = tmp_path / 'old.sqlite3'
    target = tmp_path / 'new.sqlite3'
    with sqlite3.connect(source) as con:
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('CREATE TABLE facts (kind TEXT, payload BLOB)')
        con.executemany('INSERT INTO facts VALUES (?,?)', [('pending', b'1'), ('completed', b'2'), ('fill', b'3')])
        con.commit()
        before = state_fingerprint(source)
        assert snapshot(source, target) == before
        assert state_fingerprint(target) == before == state_fingerprint(source)
        assert target.stat().st_mode & 0o077 == 0
        with pytest.raises(ValueError, match='existing'):
            snapshot(source, target)
        assert state_fingerprint(target) == before
