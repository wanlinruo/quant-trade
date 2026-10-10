from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from scripts import tushare_daily_sync


DAY = date(2026, 10, 9)


class FakeQuery:
    def __init__(self, _warehouse):
        pass

    def instruments(self):
        return pl.DataFrame({
            "symbol": ["920157.BJ", "301001.SZ", "688001.SH", "600001.SH"],
            "list_date": [DAY, DAY, DAY, date(2020, 1, 1)],
        })

    def tushare_daily(self, symbols, start, end, *, columns):
        assert symbols == ["920157.BJ", "301001.SZ", "688001.SH"]
        assert start == end == DAY
        assert columns == ["symbol", "dt", "volume"]
        return pl.DataFrame({
            "symbol": ["920157.BJ", "301001.SZ", "688001.SH"],
            "dt": [DAY, DAY, DAY],
            "volume": [122_021.0, 0.0, 8_000.0],
        })

    def minute_bars(self, symbols, start, end):
        assert symbols == ["688001.SH", "920157.BJ"]
        assert start == datetime(2026, 10, 9)
        assert end.date() == DAY
        return pl.DataFrame({"symbol": ["688001.SH"]})


def test_late_listing_gaps_require_same_day_listing_trade_and_missing_minutes(
    monkeypatch,
):
    monkeypatch.setattr(tushare_daily_sync, "WarehouseQuery", FakeQuery)
    assert tushare_daily_sync.late_listing_minute_gaps(object(), DAY) == ["920157.BJ"]


def test_convergence_runs_a_bounded_targeted_archive(monkeypatch, tmp_path):
    checks = iter([["920157.BJ"], []])
    monkeypatch.setattr(
        tushare_daily_sync, "late_listing_minute_gaps",
        lambda warehouse, day: next(checks),
    )
    called = []

    def run(command, **kwargs):
        called.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(tushare_daily_sync.subprocess, "run", run)
    got = tushare_daily_sync.converge_late_listing_minutes(
        object(), DAY,
        warehouse_path=tmp_path / "warehouse",
        config_path=tmp_path / "deploy.toml",
        service_runs=tmp_path / "runs",
        limit=20,
        timeout=900,
    )
    assert got == 1
    command, kwargs = called[0]
    assert command[0] == tushare_daily_sync.sys.executable
    assert command[1:3] == ["-u", "scripts/daily_archive.py"]
    assert command[command.index("--minute-symbols") + 1] == "920157.BJ"
    assert "--skip-auction" in command
    assert command[command.index("--lookback-days") + 1] == "0"
    assert kwargs == {"timeout": 900, "check": False}


def test_convergence_does_not_turn_a_broad_archive_failure_into_auto_retry(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        tushare_daily_sync, "late_listing_minute_gaps",
        lambda warehouse, day: [f"{index:06d}.SZ" for index in range(21)],
    )
    monkeypatch.setattr(
        tushare_daily_sync.subprocess, "run",
        lambda *args, **kwargs: pytest.fail("broad gap must not start a retry"),
    )
    with pytest.raises(RuntimeError, match="超过自动补采上限"):
        tushare_daily_sync.converge_late_listing_minutes(
            object(), DAY,
            warehouse_path=tmp_path / "warehouse",
            config_path=tmp_path / "deploy.toml",
            service_runs=tmp_path / "runs",
            limit=20,
            timeout=900,
        )


def test_convergence_propagates_archive_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        tushare_daily_sync, "late_listing_minute_gaps",
        lambda warehouse, day: ["920157.BJ"],
    )
    monkeypatch.setattr(
        tushare_daily_sync.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1),
    )
    with pytest.raises(RuntimeError, match="补采失败 rc=1"):
        tushare_daily_sync.converge_late_listing_minutes(
            object(), DAY,
            warehouse_path=Path("warehouse"),
            config_path=Path("config.toml"),
            service_runs=Path("runs"),
            limit=20,
            timeout=900,
        )


def test_production_timer_explicitly_enables_late_minute_convergence():
    root = Path(__file__).resolve().parents[2]
    unit = (root / "deploy/systemd/qsys-tushare-sync.service").read_text(
        encoding="utf-8"
    )
    assert "--late-minute-convergence" in unit
