"""D-19 第一步：盘前把当日 stk_limit 写进仓库并发布给盘中进程。

Tushare 文档：stk_limit「每个交易日 9 点左右更新当日股票涨跌停价格」。
"""

import json
from datetime import date, datetime, timedelta

import polars as pl
import pytest

from qsys.data import schema
from qsys.data.pipeline.premarket import (
    publish_key,
    run_premarket_limits,
    sync_premarket_limits,
    sync_premarket_suspensions,
)
from qsys.data.query import WarehouseQuery
from qsys.live.config import StreamConfig
from qsys.live.transport import MemoryTransport

DAY = date(2026, 9, 17)
PREV = date(2026, 9, 16)


def _limits(codes, day=DAY):
    return pl.DataFrame({
        "trade_date": [day.strftime("%Y%m%d")] * len(codes), "ts_code": codes,
        "up_limit": [10.01] * len(codes), "down_limit": [8.19] * len(codes),
    })


class Provider:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def stk_limit(self, trade_date):
        self.calls += 1
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


class SuspensionProvider:
    def __init__(self, response=None, error=None):
        self.response, self.error = response, error

    def suspend_d(self, trade_date):
        if self.error:
            raise self.error
        return self.response if self.response is not None else pl.DataFrame()


def _seed_previous(warehouse, n):
    warehouse.write("price_limits_daily", pl.DataFrame({
        "symbol": [f"{i:06d}.SZ" for i in range(n)], "dt": [PREV] * n,
        "pre_close": [9.1] * n, "limit_up": [10.01] * n, "limit_down": [8.19] * n,
    }).to_arrow().cast(schema.PRICE_LIMITS_DAILY), year=2026)


def test_complete_response_is_written_and_published(warehouse):
    _seed_previous(warehouse, 4)
    transport, streams = MemoryTransport(), StreamConfig()
    codes = ["000001.SZ", "000002.SZ", "000003.SZ", "000010.SZ"]
    assert sync_premarket_limits(Provider(_limits(codes)), warehouse, transport, streams, DAY)

    got = WarehouseQuery(warehouse).price_limits(None, DAY, DAY).sort("symbol")
    assert got["symbol"].to_list() == codes
    assert got["limit_up"].to_list() == [10.01] * 4
    # 盘前还没有当日日线，pre_close 如实记 null，17:00 同步时由 stk_limit+daily 覆盖
    assert got["pre_close"].null_count() == 4

    published = json.loads(transport.get_key(publish_key(streams, DAY)))
    assert published["000010.SZ"] == [10.01, 8.19]


def test_an_incomplete_response_is_neither_written_nor_published(warehouse):
    """条数不到上一交易日的 90%，视为尚未发布完整——不写半张表。"""
    _seed_previous(warehouse, 100)
    transport, streams = MemoryTransport(), StreamConfig()
    assert not sync_premarket_limits(
        Provider(_limits(["000001.SZ"])), warehouse, transport, streams, DAY)
    assert WarehouseQuery(warehouse).price_limits(None, DAY, DAY).height == 0
    assert transport.get_key(publish_key(streams, DAY)) is None


def test_retries_until_published_then_stops(warehouse):
    _seed_previous(warehouse, 2)
    transport, streams = MemoryTransport(), StreamConfig()
    provider = Provider(pl.DataFrame(), _limits(["000001.SZ", "000002.SZ"]))
    clock = [datetime(2026, 9, 17, 8, 58)]
    code = run_premarket_limits(
        provider, warehouse, transport, streams, DAY,
        deadline=datetime(2026, 9, 17, 9, 25), interval=timedelta(minutes=1),
        now=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + timedelta(seconds=s)),
    )
    assert code == 0 and provider.calls == 2


def test_gives_up_with_failure_after_the_deadline(warehouse):
    _seed_previous(warehouse, 2)
    transport, streams = MemoryTransport(), StreamConfig()
    provider = Provider(pl.DataFrame())
    clock = [datetime(2026, 9, 17, 9, 20)]
    code = run_premarket_limits(
        provider, warehouse, transport, streams, DAY,
        deadline=datetime(2026, 9, 17, 9, 25), interval=timedelta(minutes=1),
        now=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + timedelta(seconds=s)),
    )
    assert code == 1
    assert WarehouseQuery(warehouse).price_limits(None, DAY, DAY).height == 0


def test_suspension_snapshot_keeps_type_timing_hash_and_observation(warehouse):
    observed = datetime(2026, 9, 17, 8, 58)
    raw = pl.DataFrame({
        "ts_code": ["000001.SZ", "000002.SZ"],
        "trade_date": ["20260917", "20260917"],
        "suspend_type": ["S", "R"],
        "suspend_timing": [None, "09:30-10:00"],
    })

    snapshot = sync_premarket_suspensions(
        SuspensionProvider(raw), warehouse, DAY, observed_at=observed,
    )

    assert snapshot.observed_at == observed
    assert snapshot.row_count == 2 and snapshot.accepted
    assert len(snapshot.content_hash) == 64
    assert "query_status=success" in snapshot.detail
    got = WarehouseQuery(warehouse).suspensions(None, DAY, DAY).sort("symbol")
    assert got["suspend_type"].to_list() == ["S", "R"]
    assert got["suspend_timing"].to_list() == [None, "09:30-10:00"]


def test_successful_empty_suspension_snapshot_is_not_a_query_failure(warehouse):
    snapshot = sync_premarket_suspensions(
        SuspensionProvider(pl.DataFrame()), warehouse, DAY,
    )
    assert snapshot.row_count == 0 and snapshot.accepted
    assert WarehouseQuery(warehouse).suspensions(None, DAY, DAY).height == 0


def test_smaller_later_suspension_response_does_not_erase_existing_truth(warehouse):
    full = pl.DataFrame({
        "ts_code": ["000001.SZ", "000002.SZ"],
        "trade_date": ["20260917", "20260917"],
        "suspend_type": ["S", "S"],
        "suspend_timing": [None, None],
    })
    sync_premarket_suspensions(SuspensionProvider(full), warehouse, DAY)

    snapshot = sync_premarket_suspensions(
        SuspensionProvider(full.head(1)), warehouse, DAY,
    )

    assert not snapshot.accepted and snapshot.previous_rows == 2
    assert WarehouseQuery(warehouse).suspensions(None, DAY, DAY).height == 2


def test_suspension_query_failure_is_not_written_as_an_empty_snapshot(warehouse):
    with pytest.raises(TimeoutError):
        sync_premarket_suspensions(
            SuspensionProvider(error=TimeoutError("slow source")), warehouse, DAY,
        )
    assert WarehouseQuery(warehouse).suspensions(None, DAY, DAY).height == 0
