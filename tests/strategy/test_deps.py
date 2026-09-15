import polars as pl

from qsys.strategy.deps import build_universe


def test_universe_skips_invalid_and_excluded_symbols_without_blocking_valid_stocks():
    instruments = pl.DataFrame({
        "symbol": ["600036.SH", "T600018.SH", "688001.SH", "430001.BJ"],
        "board": ["main", "main", "star", "bse"],
    })
    universe, boards = build_universe(instruments)
    assert [str(s) for s in universe] == ["600036.SH"]
    assert boards == {"600036.SH": "main"}
