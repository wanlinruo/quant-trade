"""分钟线链路端到端：候选选择 -> 导入 -> PIT 读取 -> 质量校验。

Windows 下载那一段不在此覆盖——它依赖 xtquant，Linux 上装不了。
这里从「parquet 已经落到 incoming 目录」开始。
"""

from datetime import date, datetime

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from qsys.data import schema
from qsys.data.minute_feed import MinuteFeed
from qsys.data.pipeline.candidates import candidate_stock_days, write_manifest
from qsys.data.pipeline.flags import build_daily_flags
from qsys.data.pipeline.minutes import import_minute_bars
from qsys.data.quality.minute_rules import check_minute_bars
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

D = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
SYMBOL = "600001.SH"


def seed_daily_and_flags(warehouse: Warehouse, write_daily_bars) -> None:
    """D0 平走、D1 首板涨停、D2 次日——D2 就是候选日。"""
    rows = [
        (D[0], 10.0, 10.0, 10.0, 10.0),
        (D[1], 11.0, 11.0, 11.0, 11.0),
        (D[2], 11.5, 11.9, 11.4, 11.8),
    ]
    # pre_close 取前一行 close：非除权日付费源的 pre_close 恒等于前收。
    # name 给个不含 "ST" 的名字——is_st 现在直取自它，缺了这一列
    # is_st/limit_up 都会是 null。
    pre_closes = [None] + [r[4] for r in rows[:-1]]
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": [SYMBOL] * 3,
        "dt": [r[0] for r in rows],
        "name": ["x"] * 3,
        "open": [r[1] for r in rows],
        "high": [r[2] for r in rows],
        "low": [r[3] for r in rows],
        "close": [r[4] for r in rows],
        "pre_close": pre_closes,
        "volume": [1000.0] * 3,
        "amount": [0.0] * 3,
    }))
    warehouse.write(
        "instruments",
        pa.table(
            {
                "symbol": [SYMBOL],
                "name": ["x"],
                "market": ["SH"],
                "list_date": [None],
                "delist_date": [None],
                "board": ["main"],
                "as_of": [None],
            },
            schema=schema.INSTRUMENTS,
        ),
    )
    build_daily_flags(warehouse, D[0], D[2])


def write_minutes_for_candidate(directory, volumes=(600, 400)):
    """候选日 D2 的两根分钟线，合计与日线成交量一致。"""
    directory.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table(
            {
                "symbol": [SYMBOL] * 2,
                "dt": [datetime(2024, 1, 4, 9, 31), datetime(2024, 1, 4, 15, 0)],
                "open": [11.5, 11.7],
                "high": [11.6, 11.9],
                "low": [11.4, 11.6],
                "close": [11.6, 11.8],
                "volume": list(volumes),
                "amount": [0.0] * 2,
            },
            schema=schema.MINUTE_BARS,
        ),
        directory / "d2.parquet",
    )


@pytest.fixture
def rig(warehouse, tmp_path, write_daily_bars):
    seed_daily_and_flags(warehouse, write_daily_bars)
    return warehouse, tmp_path


def test_candidate_day_is_the_session_after_the_first_board(rig):
    warehouse, _ = rig
    assert candidate_stock_days(warehouse, D[0], D[2]).rows() == [(SYMBOL, D[2])]


def test_manifest_feeds_the_download_list(rig):
    warehouse, tmp_path = rig
    frame = candidate_stock_days(warehouse, D[0], D[2])
    assert write_manifest(frame, tmp_path / "m.csv") == 1
    assert "600001.SH,2024-01-04" in (tmp_path / "m.csv").read_text()


def test_imported_minutes_become_readable(rig):
    warehouse, tmp_path = rig
    write_minutes_for_candidate(tmp_path / "in")
    assert import_minute_bars(warehouse, tmp_path / "in").rows == 2
    df = WarehouseQuery(warehouse).minute_bars(
        None, datetime(2024, 1, 4), datetime(2024, 1, 5)
    )
    assert df.height == 2


def test_pit_hides_the_afternoon_bar_from_the_morning(rig):
    """全链路的关键：早盘读不到尾盘。"""
    warehouse, tmp_path = rig
    write_minutes_for_candidate(tmp_path / "in")
    import_minute_bars(warehouse, tmp_path / "in")
    feed = MinuteFeed(WarehouseQuery(warehouse))
    morning = feed.session_bars([SYMBOL], D[2], as_of=datetime(2024, 1, 4, 9, 31))
    assert morning.height == 1
    full = feed.session_bars([SYMBOL], D[2], as_of=datetime(2024, 1, 4, 15, 0))
    assert full.height == 2


def test_first_bar_open_matches_the_daily_open(rig):
    """跨源一致：分钟首根 open 必须等于日线 open。"""
    warehouse, tmp_path = rig
    write_minutes_for_candidate(tmp_path / "in")
    import_minute_bars(warehouse, tmp_path / "in")
    feed = MinuteFeed(WarehouseQuery(warehouse))
    row = feed.first_bar([SYMBOL], D[2], as_of=datetime(2024, 1, 4, 15, 0))
    assert row["open"].to_list() == [11.5]


def test_quality_check_passes_on_consistent_data(rig):
    warehouse, tmp_path = rig
    write_minutes_for_candidate(tmp_path / "in")
    import_minute_bars(warehouse, tmp_path / "in")
    assert check_minute_bars(warehouse, D[2]) == []


def test_quality_check_catches_a_volume_mismatch(rig):
    """分钟合计与日线对不上必须被抓到——这是两源交叉验证的意义。"""
    warehouse, tmp_path = rig
    write_minutes_for_candidate(tmp_path / "in", volumes=(60, 40))
    import_minute_bars(warehouse, tmp_path / "in")
    rules = {i.rule for i in check_minute_bars(warehouse, D[2])}
    assert "volume_vs_daily" in rules


def test_reimport_is_idempotent(rig):
    warehouse, tmp_path = rig
    write_minutes_for_candidate(tmp_path / "in")
    import_minute_bars(warehouse, tmp_path / "in")
    write_minutes_for_candidate(tmp_path / "in")
    import_minute_bars(warehouse, tmp_path / "in")
    df = WarehouseQuery(warehouse).minute_bars(
        None, datetime(2024, 1, 4), datetime(2024, 1, 5)
    )
    assert df.height == 2
