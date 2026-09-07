from datetime import date

import numpy as np
import pandas as pd
import pytest

from qsys.data.providers.akshare_provider import AkshareProvider


class FakeApi:
    """列名与 Task 5 探针录得的真实结构一致。"""

    def trade_dates(self):
        return pd.DataFrame({"trade_date": ["2024-01-02", "2024-01-03", "2024-01-04"]})

    def code_name(self):
        return pd.DataFrame(
            {"code": ["600036", "300750", "830799"], "name": ["招商银行", "宁德时代", "圣泉集团"]}
        )

    def delist_sh(self):
        return pd.DataFrame(
            {
                "公司代码": ["600001"],
                "公司简称": ["邯郸钢铁"],
                "上市日期": ["1998-01-22"],
                "暂停上市日期": ["2009-12-29"],
            }
        )

    def delist_sz(self):
        return pd.DataFrame(
            {
                "证券代码": ["000003"],
                "证券简称": ["PT金田Ａ"],
                "上市日期": ["1991-01-14"],
                "终止上市日期": ["2002-06-14"],
            }
        )

    def dividends(self, code):
        # 真实数据特征：送转比例为 NaN 而非 0；含未实施的"预披露"行
        return pd.DataFrame(
            {
                "除权除息日": ["2024-07-11", "2025-06-20", None],
                "现金分红-现金分红比例": [19.72, 10.13, np.nan],
                "送转股份-送股比例": [np.nan, np.nan, np.nan],
                "送转股份-转股比例": [3.0, np.nan, np.nan],
                "方案进度": ["实施分配", "实施分配", "预披露"],
            }
        )


@pytest.fixture
def provider() -> AkshareProvider:
    return AkshareProvider(api=FakeApi())


def test_fetch_sessions_filters_range(provider):
    assert provider.fetch_sessions(date(2024, 1, 3), date(2024, 1, 4)) == [
        date(2024, 1, 3),
        date(2024, 1, 4),
    ]


def test_fetch_instruments_columns(provider):
    df = provider.fetch_instruments()
    assert df.columns == ["symbol", "name", "market", "list_date", "delist_date", "board"]


def test_fetch_instruments_normalizes_symbols_and_boards(provider):
    df = provider.fetch_instruments().sort("symbol")
    listed = df.filter(df["delist_date"].is_null()).sort("symbol")
    assert listed["symbol"].to_list() == ["300750.SZ", "600036.SH", "830799.BJ"]
    assert listed["board"].to_list() == ["gem", "main", "bse"]
    assert listed["market"].to_list() == ["SZ", "SH", "BJ"]


def test_fetch_instruments_includes_delisted_with_dates_and_names(provider):
    df = provider.fetch_instruments()
    delisted = df.filter(df["delist_date"].is_not_null()).sort("symbol")
    assert delisted["symbol"].to_list() == ["000003.SZ", "600001.SH"]
    assert delisted["delist_date"].to_list() == [date(2002, 6, 14), date(2009, 12, 29)]
    assert delisted["list_date"].to_list() == [date(1991, 1, 14), date(1998, 1, 22)]
    assert delisted["name"].to_list() == ["PT金田Ａ", "邯郸钢铁"]


def test_corporate_actions_converts_per_ten_shares_to_per_share(provider):
    df = provider.fetch_corporate_actions("600036.SH")
    assert df["ex_date"].to_list() == [date(2024, 7, 11), date(2025, 6, 20)]
    assert df["cash_per_share"].to_list()[0] == pytest.approx(1.972)
    assert df["cash_per_share"].to_list()[1] == pytest.approx(1.013)


def test_corporate_actions_turns_nan_ratios_into_zero(provider):
    """真实数据里送转比例是 NaN 不是 0；NaN 若泄漏会污染整条复权因子链。"""
    df = provider.fetch_corporate_actions("600036.SH")
    stock = df["stock_per_share"].to_list()
    assert stock[0] == pytest.approx(0.3)  # 送 NaN + 转 3.0 -> 0.3
    assert stock[1] == pytest.approx(0.0)  # 两者皆 NaN -> 0.0
    assert not any(x != x for x in stock)  # 无 NaN 残留


def test_corporate_actions_excludes_unimplemented_plans(provider):
    """只有'实施分配'才产生复权因子，预案/预披露不算。"""
    df = provider.fetch_corporate_actions("600036.SH")
    assert df.height == 2


def test_corporate_actions_columns(provider):
    df = provider.fetch_corporate_actions("600036.SH")
    assert df.columns == [
        "ex_date",
        "cash_per_share",
        "stock_per_share",
        "rights_per_share",
        "rights_price",
    ]


def test_provider_name(provider):
    assert provider.name == "akshare"


@pytest.mark.parametrize(
    ("code", "board"),
    [
        ("600036", "main"),
        ("300750", "gem"),
        ("688981", "star"),
        ("689009", "star"),
        ("830799", "bse"),
        ("920002", "bse"),
        ("900935", "bshare"),
        ("200011", "bshare"),
    ],
)
def test_board_of_covers_real_code_segments(code, board):
    from qsys.data.providers.akshare_provider import _board_of

    assert _board_of(code) == board


def test_unknown_code_is_skipped_not_fatal():
    """交易所新开号段不应让整条采集管线崩溃。"""

    class ApiWithUnknownCode(FakeApi):
        def code_name(self):
            return pd.DataFrame(
                {"code": ["600036", "777777"], "name": ["招商银行", "未知标的"]}
            )

    df = AkshareProvider(api=ApiWithUnknownCode()).fetch_instruments()
    listed = df.filter(df["delist_date"].is_null())
    assert listed["symbol"].to_list() == ["600036.SH"]
