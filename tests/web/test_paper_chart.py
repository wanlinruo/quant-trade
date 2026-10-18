from datetime import date

import pytest

from qsys.web.paper_chart import history_series, intraday_series

INITIAL = 100_000.0


def _day(session, total, benchmark_close):
    return {"session": session, "at": f"{session}T15:30:00", "total_value": total,
            "benchmark_value": None, "benchmark_close": benchmark_close}


def _minute(at, total, benchmark):
    return {"session": at[:10], "at": at, "total_value": total, "benchmark_value": benchmark}


DAILY = [_day("2026-09-18", 100_000, 100_000), _day("2026-09-21", 102_000, 99_000)]


def test_history_is_cumulative_return_per_session():
    series = history_series(DAILY, INITIAL)

    assert [p["session"] for p in series] == ["2026-09-18", "2026-09-21"]
    assert series[1]["strategy"] == pytest.approx(0.02)
    assert series[1]["benchmark"] == pytest.approx(-0.01)


def test_history_keeps_missing_benchmark_as_none():
    series = history_series([_day("2026-09-18", 100_000, None)], INITIAL)

    assert series[0]["benchmark"] is None


def test_intraday_day_return_is_against_the_previous_session_close():
    points = [_minute("2026-09-21T09:31:00", 103_020, 99_990),
              _minute("2026-09-21T15:30:00", 102_000, None)]

    series = intraday_series(points, DAILY, date(2026, 9, 21), INITIAL)

    assert series[0]["time"] == "09:31"
    assert series[0]["strategy_day"] == pytest.approx(0.0302)
    assert series[0]["strategy_cum"] == pytest.approx(0.0302)
    assert series[0]["benchmark_day"] == pytest.approx(-0.0001)
    assert series[0]["benchmark_cum"] == pytest.approx(-0.0001)
    assert series[1]["benchmark_day"] is None


def test_intraday_first_session_uses_initial_cash_as_the_base():
    series = intraday_series([_minute("2026-09-18T10:00:00", 101_000, 100_500)],
                             DAILY, date(2026, 9, 18), INITIAL)

    assert series[0]["strategy_day"] == pytest.approx(0.01)
    assert series[0]["benchmark_day"] == pytest.approx(0.005)


def test_intraday_benchmark_day_return_needs_an_observed_previous_close():
    daily = [_day("2026-09-18", 100_000, None), _day("2026-09-21", 101_000, 99_000)]

    series = intraday_series([_minute("2026-09-21T10:00:00", 101_000, 99_500)],
                             daily, date(2026, 9, 21), INITIAL)

    assert series[0]["strategy_day"] == pytest.approx(0.01)
    assert series[0]["benchmark_day"] is None
    assert series[0]["benchmark_cum"] == pytest.approx(-0.005)


@pytest.mark.parametrize("second_equity", [50_500.0, 100_000.0])
def test_history_withholds_shadow_returns_without_cash_flow_records(second_equity):
    """总资产不足以证明收益率；正常幅度和资金翻倍都不推断。"""
    from qsys.web.paper_chart import history_series

    daily = [
        {"session": "2026-09-24", "total_value": 100_000.0, "benchmark_close": 4000.0},
        {"session": "2026-09-28", "total_value": 104_000.0, "benchmark_close": 4040.0},
        {"session": "2026-09-29", "total_value": 103_000.0, "benchmark_close": 4020.0},
    ]
    shadow = {"2026-09-23": 50_000.0, "2026-09-24": 50_000.0, "2026-09-28": second_equity}

    got = history_series(daily, 100_000.0, shadow)

    assert [round(p["strategy"], 4) for p in got] == [0.0, 0.04, 0.03]
    assert [p["shadow"] for p in got] == [None, None, None]


def test_without_shadow_data_the_series_is_absent():
    from qsys.web.paper_chart import history_series

    daily = [{"session": "2026-09-24", "total_value": 100_000.0, "benchmark_close": None}]
    assert history_series(daily, 100_000.0)[0]["shadow"] is None
    assert history_series(daily, 100_000.0, {})[0]["shadow"] is None
