"""同步后的验证挂钩；验证故障不得把已落盘同步改报失败。"""

from datetime import date
from types import SimpleNamespace

from scripts import tushare_daily_sync

D = date(2026, 9, 15)


def test_verification_runs_after_successful_sync(monkeypatch, warehouse):
    called = {}

    def fake_verify(actual_warehouse, day):
        called["warehouse"] = actual_warehouse
        called["day"] = day
        return 0

    monkeypatch.setattr(
        tushare_daily_sync, "run_incremental_verification", fake_verify
    )
    tushare_daily_sync.after_sync(warehouse, D, ok=True)
    assert called == {"warehouse": warehouse, "day": D}


def test_verification_is_skipped_after_failed_sync(monkeypatch, warehouse):
    called = {}
    monkeypatch.setattr(
        tushare_daily_sync,
        "run_incremental_verification",
        lambda actual_warehouse, day: called.setdefault("ran", True),
    )
    tushare_daily_sync.after_sync(warehouse, D, ok=False)
    assert "ran" not in called


def test_crashing_verifier_does_not_fail_sync(monkeypatch, warehouse):
    def boom(actual_warehouse, day):
        raise RuntimeError("验证器炸了")

    monkeypatch.setattr(tushare_daily_sync, "run_incremental_verification", boom)
    tushare_daily_sync.after_sync(warehouse, D, ok=True)


def test_main_calls_hook_after_facts_and_derived_refresh(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(
        tushare_daily_sync, "load_tushare_config",
        lambda path: SimpleNamespace(token="test"),
    )
    monkeypatch.setattr(tushare_daily_sync, "TushareClient", lambda token: object())
    monkeypatch.setattr(tushare_daily_sync, "TushareProvider", lambda client: object())
    monkeypatch.setattr(
        tushare_daily_sync, "sync",
        lambda provider, warehouse, day, run=None: events.append("facts") or {"facts": 1},
    )
    monkeypatch.setattr(
        tushare_daily_sync, "refresh_derived",
        lambda warehouse, day, run=None: events.append("derived") or {"derived": 1},
    )
    monkeypatch.setattr(
        tushare_daily_sync, "after_sync",
        lambda warehouse, day, ok, run=None: events.append(("verify", ok)),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["tushare_daily_sync.py", "--warehouse", str(tmp_path), "--day", str(D),
         "--service-runs", str(tmp_path / "runs")],
    )
    assert tushare_daily_sync.main() == 0
    assert events == ["facts", "derived", ("verify", True)]
    assert len(list((tmp_path / "runs").rglob("*.json"))) == 1
