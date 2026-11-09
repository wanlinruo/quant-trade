import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ACCOUNT = '100000001'


def tool():
    spec = importlib.util.spec_from_file_location('prep_signal', ROOT / 'scripts' / 'prepare_qmt_signal_executor.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def account_file(tmp_path):
    path = tmp_path / 'account.txt'
    path.write_text(ACCOUNT + '\n')
    return path


def staged(tmp_path, account_file, **kw):
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    receipt = tool().stage(bundle, run_id='a' * 32, account_file=account_file, **kw)
    return bundle, receipt


def test_stage_writes_exact_files_and_never_prints_secrets(tmp_path, account_file, capsys):
    bundle, receipt = staged(tmp_path, account_file)
    assert sorted(p.name for p in bundle.iterdir()) == ['executor-default.py', 'executor-enabled.py', 'private']
    assert sorted(p.name for p in (bundle / 'private').iterdir()) == ['account.txt', 'key.hex', 'run.json']
    key = (bundle / 'private' / 'key.hex').read_text()
    text = json.dumps(receipt)
    assert ACCOUNT not in text and key not in text
    assert ACCOUNT not in capsys.readouterr().out
    assert (bundle / 'private' / 'key.hex').stat().st_mode & 0o077 == 0


def test_checked_in_executor_equals_default_artifact(tmp_path, account_file):
    bundle, _ = staged(tmp_path, account_file)
    source = ROOT / 'src' / 'qsys' / 'agent' / 'qmt' / 'executor_source.py'
    assert (bundle / 'executor-default.py').read_bytes() == source.read_bytes()


def test_enabled_literals_and_market_type_default_off(tmp_path, account_file):
    bundle, _ = staged(tmp_path, account_file)
    spec = importlib.util.spec_from_file_location('enabled', bundle / 'executor-enabled.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.ENABLED is True and mod.ACCOUNT_ID == ACCOUNT and mod.RUN_ID == 'a' * 32
    assert mod.CLIENT_BUILD == '2.1.33.0' and mod.PRICE_TYPE_MARKET is None
    assert mod.KEY_HEX == (bundle / 'private' / 'key.hex').read_text()


def test_market_type_only_original_latest_price(tmp_path, account_file):
    with pytest.raises(ValueError):
        staged(tmp_path, account_file, market_price_type=42)
    other = tmp_path / 'x'
    other.mkdir()
    bundle, _ = staged(other, account_file, market_price_type=5)
    assert 'PRICE_TYPE_MARKET = 5' in (bundle / 'executor-enabled.py').read_text()


def test_verify_detects_tampering_and_refuses_overwrite(tmp_path, account_file):
    bundle, _ = staged(tmp_path, account_file)
    assert tool().verify(bundle)['status'] == 'verified'
    with pytest.raises(FileExistsError):
        tool().stage(bundle, run_id='a' * 32, account_file=account_file)
    path = bundle / 'executor-enabled.py'
    path.write_text(path.read_text().replace('MAX_ORDER_NOTIONAL = 10000.0', 'MAX_ORDER_NOTIONAL = 99999.0'))
    with pytest.raises(ValueError):
        tool().verify(bundle)


def _enabled_module(bundle):
    # Load without leaving __pycache__ in the bundle (verify rejects extra files).
    source = (bundle / 'executor-enabled.py').read_text()
    namespace = {}
    exec(compile(source, 'executor-enabled.py', 'exec'), namespace)
    return type('M', (), namespace)


def test_stage_keeps_dry_run_unless_live_orders_requested(tmp_path, account_file):
    bundle, receipt = staged(tmp_path, account_file)
    assert _enabled_module(bundle).DRY_RUN is True and receipt['dry_run'] is True
    other = tmp_path / 'live'
    other.mkdir()
    bundle2, receipt2 = staged(other, account_file, live_orders=True)
    assert _enabled_module(bundle2).DRY_RUN is False and receipt2['dry_run'] is False
    assert tool().verify(bundle2)['dry_run'] is False
