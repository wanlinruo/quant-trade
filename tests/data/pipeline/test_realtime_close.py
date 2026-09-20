from datetime import date, datetime, timedelta

import json
import pytest

from qsys.data.pipeline.realtime_close import publish_realtime_close
from qsys.data.query import WarehouseQuery
from qsys.data.realtime import RealtimeDailyBar, RealtimeMinuteBar


DAY = date(2026, 9, 21)
PUBLISHED = datetime(2026, 9, 21, 15, 2)
SYMBOL = "600036.SH"


def _candidates(*, close: float = 10.5, partial: bool = False):
    first = RealtimeMinuteBar(
        SYMBOL, datetime(2026, 9, 21, 9, 31), 10.0, 10.2, 9.9, 10.1,
        100.0, 100_000.0, "qmt", datetime(2026, 9, 21, 9, 31), True, partial,
    )
    second = RealtimeMinuteBar(
        SYMBOL, datetime(2026, 9, 21, 15, 0), 10.1, close, 10.0, close,
        200.0, 210_000.0, "qmt", datetime(2026, 9, 21, 15, 0), True, False,
    )
    daily = RealtimeDailyBar(
        SYMBOL, DAY, 10.0, close, 9.9, close, 9.8,
        300.0, 310_000.0, "qmt", PUBLISHED,
    )
    return [daily], [first, second]


def test_close_publish_writes_both_targets_and_verified_manifest(warehouse):
    daily, minutes = _candidates()
    result = publish_realtime_close(
        warehouse, DAY, published_at=PUBLISHED, daily=daily, minutes=minutes,
        expected_minutes={SYMBOL: 2},
    )
    assert result.status == "published" and result.manifest.exists()
    manifest = json.loads(result.manifest.read_text())
    assert manifest["status"] == "PUBLISHED" and manifest["minute_rows"] == 2
    query = WarehouseQuery(warehouse)
    assert query.bar_daily([SYMBOL], DAY, DAY)["close"].to_list() == [10.5]
    got = query.bar_minute_1m(
        [SYMBOL], datetime.combine(DAY, datetime.min.time()),
        datetime.combine(DAY, datetime.max.time()),
    )
    assert got.height == 2 and got["source"].unique().to_list() == ["qmt"]


def test_close_publish_retry_is_idempotent(warehouse):
    daily, minutes = _candidates()
    first = publish_realtime_close(
        warehouse, DAY, published_at=PUBLISHED, daily=daily, minutes=minutes,
        expected_minutes={SYMBOL: 2},
    )
    second = publish_realtime_close(
        warehouse, DAY, published_at=PUBLISHED + timedelta(seconds=1),
        daily=daily, minutes=minutes, expected_minutes={SYMBOL: 2},
    )
    assert first.status == "published" and second.status == "already_published"


@pytest.mark.parametrize("reason", ["partial", "count", "early", "aggregate"])
def test_gate_failure_preserves_history_and_writes_failure_evidence(warehouse, reason):
    daily, minutes = _candidates(partial=reason == "partial")
    expected = {SYMBOL: 3 if reason == "count" else 2}
    published = datetime(2026, 9, 21, 15, 0) if reason == "early" else PUBLISHED
    if reason == "aggregate":
        daily[0] = RealtimeDailyBar(
            SYMBOL, DAY, 10.0, 99.0, 9.9, 99.0, 9.8,
            300.0, 310_000.0, "qmt", PUBLISHED,
        )
    with pytest.raises(RuntimeError):
        publish_realtime_close(
            warehouse, DAY, published_at=published, daily=daily, minutes=minutes,
            expected_minutes=expected,
        )
    assert not warehouse.path_for("bar_daily", DAY.year).exists()
    assert not warehouse.day_path("bar_minute_1m", DAY).exists()
    evidence = list((warehouse.root / "realtime_publish_failures").glob("**/*.json"))
    assert len(evidence) == 1
    assert json.loads(evidence[0].read_text())["status"] == "FAILED"


def test_second_target_commit_failure_rolls_back_both_historical_targets(
    warehouse, monkeypatch
):
    daily, minutes = _candidates()
    publish_realtime_close(
        warehouse, DAY, published_at=PUBLISHED, daily=daily, minutes=minutes,
        expected_minutes={SYMBOL: 2},
    )
    before_daily = warehouse.path_for("bar_daily", DAY.year).read_bytes()
    before_minute = warehouse.day_path("bar_minute_1m", DAY).read_bytes()
    changed_daily, changed_minutes = _candidates(close=10.6)
    changed_minutes[1] = RealtimeMinuteBar(
        SYMBOL, changed_minutes[1].ts, 10.1, 10.6, 10.0, 10.6,
        200.0, 210_000.0, "qmt", changed_minutes[1].visible_at, True, False,
    )
    import qsys.data.pipeline.realtime_close as module
    original = module.os.replace

    def fail_minute_commit(source, target):
        if Path(source).name == "bar_minute_1m" and Path(target).name == "date=20260921":
            raise OSError("simulated minute commit failure")
        return original(source, target)

    from pathlib import Path
    monkeypatch.setattr(module.os, "replace", fail_minute_commit)
    with pytest.raises(OSError, match="simulated"):
        publish_realtime_close(
            warehouse, DAY, published_at=PUBLISHED + timedelta(seconds=2),
            daily=changed_daily, minutes=changed_minutes,
            expected_minutes={SYMBOL: 2},
        )
    assert warehouse.path_for("bar_daily", DAY.year).read_bytes() == before_daily
    assert warehouse.day_path("bar_minute_1m", DAY).read_bytes() == before_minute
