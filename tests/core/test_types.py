from datetime import date

import pytest

from qsys.core.types import Bar, Frequency, Market, Symbol


def test_symbol_str_is_code_dot_market():
    assert str(Symbol("600036", Market.SH)) == "600036.SH"


def test_symbol_parse_round_trip():
    s = Symbol.parse("000001.SZ")
    assert s.code == "000001"
    assert s.market is Market.SZ
    assert str(s) == "000001.SZ"


def test_symbol_parse_is_case_insensitive_on_market():
    assert Symbol.parse("688981.sh") == Symbol("688981", Market.SH)


def test_symbol_rejects_non_six_digit_code():
    with pytest.raises(ValueError, match="invalid code"):
        Symbol("60036", Market.SH)


def test_symbol_parse_rejects_missing_market_suffix():
    with pytest.raises(ValueError, match="missing market suffix"):
        Symbol.parse("600036")


def test_symbol_is_hashable_and_usable_as_dict_key():
    d = {Symbol("600036", Market.SH): 1}
    assert d[Symbol.parse("600036.SH")] == 1


def test_frequency_values():
    assert Frequency.DAILY.value == "1d"
    assert Frequency.MIN1.value == "1m"


def test_bar_holds_ohlcv():
    bar = Bar(
        symbol=Symbol("600036", Market.SH),
        dt=date(2024, 1, 2),
        open=30.0,
        high=31.0,
        low=29.5,
        close=30.5,
        volume=1000,
        amount=30_500.0,
    )
    assert bar.close == 30.5
    assert bar.dt == date(2024, 1, 2)


def test_bar_volume_is_a_float():
    """付费源的手数带小数（582305.98 手），Bar 不能把它标成 int。

    标注错了不会立刻报错——dataclass 不做运行时校验——但会误导
    每一个读这段代码的人，并让静态检查失去意义。
    """
    import typing

    assert typing.get_type_hints(Bar)["volume"] is float

    bar = Bar(symbol=Symbol.parse("000001.SZ"), dt=date(2026, 9, 9),
              open=11.76, high=11.79, low=11.69, close=11.70,
              volume=582305.98, amount=6.8e8)
    assert bar.volume == 582305.98
