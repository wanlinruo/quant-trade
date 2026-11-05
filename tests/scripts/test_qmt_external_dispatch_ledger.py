"""Real SQLite in marked private temporary fixtures, never production state."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

SOURCE = Path(__file__).resolve().parents[2] / 'docs/reference/qmt-external-dispatch-ledger.py'
RUN = 'a' * 32
MSG = 'b' * 32
ATTEMPT = 'c' * 32
SESSION = 'd' * 32
SHA = 'e' * 64


def load():
    spec = importlib.util.spec_from_file_location('diagnostic_ledger', SOURCE)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj.DiagnosticLedger


@pytest.fixture
def fixture_root(tmp_path):
    root = tmp_path / ('qsys599-ledger-' + RUN)
    root.mkdir(mode=0o700)
    (root / '.diagnostic-only').write_text(RUN)
    return root


def test_prepare_is_immutable_and_unmarked_root_is_rejected(fixture_root, tmp_path):
    Ledger = load()
    with pytest.raises(ValueError):
        Ledger(tmp_path, RUN)
    ledger = Ledger(fixture_root, RUN)
    ledger.prepare(MSG, 1000, 2000)
    ledger.prepare(MSG, 1000, 2000)
    with pytest.raises(ValueError):
        ledger.prepare(MSG, 1001, 2000)
    assert ledger.read(MSG)['state'] == 'prepared'


def test_two_connections_cannot_claim_same_dispatch_twice(fixture_root):
    Ledger = load()
    first, second = Ledger(fixture_root, RUN), Ledger(fixture_root, RUN)
    first.prepare(MSG, 1000, 2000)
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda ledger: ledger.claim(MSG, ATTEMPT, SESSION, 1500),
                                    (first, second)))
    assert sorted(outcomes) == [False, True]


@pytest.mark.parametrize('after_ack', [False, True])
def test_process_exit_after_committed_dispatch_never_requeues_unknown(fixture_root, after_ack):
    Ledger = load()
    ledger = Ledger(fixture_root, RUN)
    ledger.prepare(MSG, 1000, 2000)
    # Real child exits at a simulated crash point after a durable commit.
    # No external effect/broker is present; no failure is injected into a node.
    code = '''import importlib.util,os,sys
spec=importlib.util.spec_from_file_location('fixture',sys.argv[1])
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
l=m.DiagnosticLedger(sys.argv[2],sys.argv[3])
assert l.claim(sys.argv[4],sys.argv[5],sys.argv[6],1500)
if sys.argv[7]=='1':l.transport_ack(sys.argv[4],sys.argv[5],sys.argv[8])
os._exit(23)
'''
    result = subprocess.run([sys.executable, '-B', '-c', code, str(SOURCE), str(fixture_root),
                             RUN, MSG, ATTEMPT, SESSION, '1' if after_ack else '0', SHA],
                            timeout=15, capture_output=True)
    assert result.returncode == 23
    restarted = Ledger(fixture_root, RUN)
    assert restarted.recover_unfinished() == 1
    row = restarted.read(MSG)
    assert row['state'] == 'unknown'
    assert row['attempt_id'] == ATTEMPT
    assert not restarted.claim(MSG, 'f' * 32, '1' * 32, 1500)
    assert restarted.recover_unfinished() == 0
    assert row['transport_ack_sha256'] == (SHA if after_ack else None)


def test_transport_ack_is_not_terminal_and_expired_prepared_stays_unsent(fixture_root):
    Ledger = load()
    ledger = Ledger(fixture_root, RUN)
    ledger.prepare(MSG, 1000, 2000)
    assert not ledger.claim(MSG, ATTEMPT, SESSION, 2000)
    assert ledger.read(MSG)['state'] == 'prepared'
    assert ledger.claim(MSG, ATTEMPT, SESSION, 1500)
    with pytest.raises(ValueError):
        ledger.transport_ack(MSG, 'f' * 32, SHA)
    ledger.transport_ack(MSG, ATTEMPT, SHA)
    assert ledger.read(MSG)['state'] == 'transport_received'
    with pytest.raises(ValueError):
        ledger.complete_diagnostic_echo(MSG, ATTEMPT, 'f' * 64)
    ledger.complete_diagnostic_echo(MSG, ATTEMPT, SHA)
    restarted = Ledger(fixture_root, RUN)
    assert restarted.recover_unfinished() == 0
    assert restarted.read(MSG)['state'] == 'diagnostic_echoed'
    assert not restarted.claim(MSG, 'f' * 32, SESSION, 1500)
