import sqlite3
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from scripts.prepare_live_core_release import dump_toml, snapshot
from scripts.verify_live_core_preproduction import state_fingerprint


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
