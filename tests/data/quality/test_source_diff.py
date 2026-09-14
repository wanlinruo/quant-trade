"""换源回归对账：差异必须能逐键列出，不能只给一个总数。"""

from datetime import date

import polars as pl

from qsys.data import schema
from qsys.data.query import WarehouseQuery
from qsys.data.quality.source_diff import compare_daily_sources

DAY = date(2026, 9, 9)


def _legacy(warehouse, rows):
    """造旧表数据作对账参照物。

    Global Constraints 说 bars_daily 冻结不写入，指的是**生产路径**；
    这里写的是 tmp_path 下的临时仓库，不触碰生产数据。
    """
    frame = pl.DataFrame(rows)
    warehouse.write("bars_daily", frame.to_arrow().cast(schema.BARS_DAILY), year=2026)


def test_reports_rows_only_in_the_paid_source(warehouse, write_daily_bars):
    """北交所 343 只只在付费源里，必须单独列出而不是混进数值差异。"""
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["000001.SZ", "920268.BJ"], "dt": [DAY, DAY],
        "open": [11.76, 5.0], "high": [11.79, 5.1], "low": [11.69, 4.9],
        "close": [11.70, 5.05], "volume": [582306.0, 100.0], "amount": [6.8e8, 5e4],
    }))
    _legacy(warehouse, {
        "symbol": ["000001.SZ"], "dt": [DAY], "open": [11.76], "high": [11.79],
        "low": [11.69], "close": [11.70], "volume": [582306], "amount": [6.8e8],
    })
    diff = compare_daily_sources(WarehouseQuery(warehouse), DAY, DAY, boards=None)
    assert diff.only_in_paid["symbol"].to_list() == ["920268.BJ"]
    assert diff.only_in_legacy.height == 0


def test_reports_rows_only_in_the_legacy_source(warehouse, write_daily_bars):
    """旧表那 10 行零成交停牌占位在付费源里没有，同样要能看见。"""
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["000001.SZ"], "dt": [DAY], "open": [11.76], "high": [11.79],
        "low": [11.69], "close": [11.70], "volume": [582306.0], "amount": [6.8e8],
    }))
    _legacy(warehouse, {
        "symbol": ["000001.SZ", "000016.SZ"], "dt": [DAY, DAY],
        "open": [11.76, 5.0], "high": [11.79, 5.0], "low": [11.69, 5.0],
        "close": [11.70, 5.0], "volume": [582306, 0], "amount": [6.8e8, 1e-9],
    })
    diff = compare_daily_sources(WarehouseQuery(warehouse), DAY, DAY, boards=None)
    assert diff.only_in_legacy["symbol"].to_list() == ["000016.SZ"]


def test_lists_every_differing_field_key_by_key(warehouse, write_daily_bars):
    """差异要逐 (symbol, dt, field) 列出。只报一个总数等于把问题藏起来。"""
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["000001.SZ"], "dt": [DAY], "open": [11.76], "high": [11.79],
        "low": [11.69], "close": [11.70],
        "volume": [582305.98], "amount": [682946782.66],
    }))
    _legacy(warehouse, {
        "symbol": ["000001.SZ"], "dt": [DAY], "open": [11.76], "high": [11.79],
        "low": [11.69], "close": [11.70],
        "volume": [582306], "amount": [682946752.0],
    })
    diff = compare_daily_sources(WarehouseQuery(warehouse), DAY, DAY, boards=None)
    fields = sorted(diff.value_diffs["field"].to_list())
    assert fields == ["amount", "volume"]
    row = diff.value_diffs.filter(pl.col("field") == "volume").to_dicts()[0]
    assert row["paid"] == 582305.98
    assert row["legacy"] == 582306.0
    assert abs(row["abs_diff"] - 0.02) < 1e-9


def test_ohlc_agreement_is_reported_as_zero_when_prices_match(warehouse, write_daily_bars):
    """OHLC 实测零差异，回归要能把这个结论固化下来。"""
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["000001.SZ"], "dt": [DAY], "open": [11.76], "high": [11.79],
        "low": [11.69], "close": [11.70], "volume": [582306.0], "amount": [6.8e8],
    }))
    _legacy(warehouse, {
        "symbol": ["000001.SZ"], "dt": [DAY], "open": [11.76], "high": [11.79],
        "low": [11.69], "close": [11.70], "volume": [582306], "amount": [6.8e8],
    })
    diff = compare_daily_sources(WarehouseQuery(warehouse), DAY, DAY, boards=None)
    assert diff.summary["ohlc_diff_rows"] == 0
    assert diff.summary["common_rows"] == 1


def test_board_filter_excludes_beijing_from_the_comparison(warehouse, write_daily_bars):
    """按板块过滤后，北交所不该再出现在「只在付费源」里。"""
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["000001.SZ", "920268.BJ"], "dt": [DAY, DAY],
        "open": [11.76, 5.0], "high": [11.79, 5.1], "low": [11.69, 4.9],
        "close": [11.70, 5.05], "volume": [582306.0, 100.0], "amount": [6.8e8, 5e4],
    }))
    _legacy(warehouse, {
        "symbol": ["000001.SZ"], "dt": [DAY], "open": [11.76], "high": [11.79],
        "low": [11.69], "close": [11.70], "volume": [582306], "amount": [6.8e8],
    })
    diff = compare_daily_sources(
        WarehouseQuery(warehouse), DAY, DAY, boards={"main", "gem", "star"}
    )
    assert diff.only_in_paid.height == 0


def test_a_null_on_one_side_only_is_a_difference(warehouse, write_daily_bars):
    """一边缺值一边有值必须算差异。

    polars 里 `null != 有值` 求值为 null，filter 会把它当 False 丢掉——
    这类差异既不进 value_diffs 也不计入 summary，还不报错，是最难发现的一类。
    """
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["000001.SZ"], "dt": [DAY], "open": [11.76], "high": [11.79],
        "low": [11.69], "volume": [582306.0], "amount": [6.8e8],
        # 不传 close——write_daily_bars 会把缺列一律补 null。
    }))
    _legacy(warehouse, {
        "symbol": ["000001.SZ"], "dt": [DAY], "open": [11.76], "high": [11.79],
        "low": [11.69], "close": [11.70], "volume": [582306], "amount": [6.8e8],
    })
    diff = compare_daily_sources(WarehouseQuery(warehouse), DAY, DAY, boards=None)
    hit = diff.value_diffs.filter(pl.col("field") == "close")
    assert hit.height == 1
    row = hit.to_dicts()[0]
    assert row["paid"] is None
    assert row["legacy"] == 11.70


def test_both_sides_null_is_not_a_difference(warehouse, write_daily_bars):
    """两边都缺值不算差异——否则会把大量正常的空值误报成差异。"""
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["000001.SZ"], "dt": [DAY], "open": [11.76], "high": [11.79],
        "low": [11.69], "volume": [582306.0], "amount": [6.8e8],
        # 不传 close——补 null。
    }))
    _legacy(warehouse, {
        "symbol": ["000001.SZ"], "dt": [DAY], "open": [11.76], "high": [11.79],
        "low": [11.69], "close": [None], "volume": [582306], "amount": [6.8e8],
    })
    diff = compare_daily_sources(WarehouseQuery(warehouse), DAY, DAY, boards=None)
    assert diff.value_diffs.filter(pl.col("field") == "close").height == 0
