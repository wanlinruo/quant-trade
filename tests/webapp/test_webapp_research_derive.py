"""回测分析与策略推进位置的纯派生（#662，设计文档 §8.10、§8.11）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from qsys.webapp.queries.research_derive import (
    aligned_series,
    backtest_warnings,
    drawdown_episodes,
    in_use_versions,
    strategy_stage,
)
from qsys.web.workbench import WorkbenchStore, version_id_for

SOURCE = "from qsys.strategy.base import Strategy\nclass Demo(Strategy):\n    pass\n"


def acct(name, strategy_id, version_id, status="running"):
    return SimpleNamespace(name=name, strategy_id=strategy_id, version_id=version_id, status=status)


def test_version_id_matches_enqueue(tmp_path):
    store = WorkbenchStore(tmp_path / "wb" / "jobs.sqlite3")
    s = store.create_strategy("demo", SOURCE)
    job = store.enqueue(s.id, {"start": "2024-01-01", "end": "2024-01-31", "cash": 1, "params": {}})
    assert job.version_id == version_id_for(s.id, SOURCE)


def test_drawdown_episodes_top5_and_unrecovered():
    nav = [("d1", 100), ("d2", 90), ("d3", 100), ("d4", 80), ("d5", 85)]
    eps = drawdown_episodes(nav)
    assert eps[0] == {"peak_date": "d3", "trough_date": "d4", "recovery_date": None,
                      "depth": pytest.approx(-0.2)}
    assert eps[1]["peak_date"] == "d1" and eps[1]["trough_date"] == "d2"
    assert eps[1]["recovery_date"] == "d3" and eps[1]["depth"] == pytest.approx(-0.1)


def test_drawdown_episodes_limit():
    nav, v = [], 100.0
    for i in range(7):
        nav += [(f"a{i}", v), (f"b{i}", v * (1 - 0.01 * (i + 1))), (f"c{i}", v)]
    eps = drawdown_episodes(nav)
    assert len(eps) == 5
    assert [round(e["depth"], 2) for e in eps] == [-0.07, -0.06, -0.05, -0.04, -0.03]


def test_drawdown_episodes_empty_and_flat():
    assert drawdown_episodes([]) == []
    assert drawdown_episodes([("d1", 100), ("d2", 101)]) == []


def test_warnings_list_missing_benchmark_days():
    w = backtest_warnings(rejects=2, has_benchmark=True, missing_benchmark_days=3,
                          missing_daily_returns=1, cost_model="聚宽兼容", status="interrupted")
    text = "\n".join(w)
    assert "下单被拒 2 笔" in text and "3 个交易日缺基准" in text
    assert "1 日缺日收益" in text and "聚宽兼容" in text and "被中断" in text
    assert any("没有基准净值" in x for x in backtest_warnings(
        rejects=0, has_benchmark=False, missing_benchmark_days=0, missing_daily_returns=0,
        cost_model="qsys", status=None))


def test_warnings_empty_when_clean():
    assert backtest_warnings(rejects=0, has_benchmark=True, missing_benchmark_days=0,
                             missing_daily_returns=0, cost_model="qsys 实盘档位",
                             status="succeeded") == []


def test_aligned_series_keeps_missing_benchmark_as_null():
    nav = [{"date": "d1", "total_value": 100.0}, {"date": "d2", "total_value": 110.0},
           {"date": "d3", "total_value": 99.0}]
    s = aligned_series(nav, {"d1": 50.0, "d3": 55.0})
    assert s[0] == {"date": "d1", "strategy": 0.0, "benchmark": 0.0, "excess": 0.0, "drawdown": 0.0}
    assert s[1]["benchmark"] is None and s[1]["excess"] is None
    assert s[1]["strategy"] == pytest.approx(0.1)
    assert s[2]["benchmark"] == pytest.approx(0.1) and s[2]["drawdown"] == pytest.approx(-0.1)


def test_stage_priority_live_over_paper_over_backtest():
    lineage = SimpleNamespace(strategy_id="S1", version_id="v1")
    live = strategy_stage("S1", succeeded_strategy_ids={"S1"},
                          active_accounts=[acct("A", "S1", "v1")], lineage=lineage, core_label="影子盘")
    assert (live["stage"], live["label"]) == ("live", "影子盘")
    paper = strategy_stage("S1", succeeded_strategy_ids={"S1"},
                           active_accounts=[acct("A", "S1", "v1")], lineage=None, core_label="影子盘")
    assert (paper["stage"], paper["label"], paper["detail"]) == ("paper", "模拟", "A")
    bt = strategy_stage("S1", succeeded_strategy_ids={"S1"}, active_accounts=[], lineage=None,
                        core_label="实盘")
    assert bt["stage"] == "backtest"
    assert strategy_stage("S2", succeeded_strategy_ids=set(), active_accounts=[], lineage=None,
                          core_label="实盘")["stage"] == "draft"


def test_in_use_versions_from_lineage_and_active_accounts():
    lineage = SimpleNamespace(strategy_id="S1", version_id="v1")
    got = in_use_versions([acct("A", "S1", "v1"), acct("B", "S2", "v2"),
                           acct("C", "S3", "v3", status="ended")], lineage, core_label="实盘")
    assert got == {"v1": ["实盘", "模拟账户 A"], "v2": ["模拟账户 B"]}


def test_aligned_series_null_value_day():
    nav = [{"date": "d1", "total_value": 100.0}, {"date": "d2", "total_value": None},
           {"date": "d3", "total_value": 110.0}]
    rows = aligned_series(nav, {"d1": 1.0, "d2": 1.1, "d3": 1.2})
    assert rows[1]["strategy"] is None and rows[1]["drawdown"] is None and rows[1]["excess"] is None
    assert rows[1]["benchmark"] == pytest.approx(0.1)
    assert rows[2]["strategy"] == pytest.approx(0.1) and rows[2]["drawdown"] == 0.0


def test_aligned_series_null_initial_value_has_no_returns():
    rows = aligned_series([{"date": "d1", "total_value": None}, {"date": "d2", "total_value": 90.0}], {})
    assert [r["strategy"] for r in rows] == [None, None]


def test_episodes_skip_null_value_day():
    eps = drawdown_episodes([("d1", 100), ("d2", None), ("d3", 80), ("d4", 100)])
    assert eps == [{"peak_date": "d1", "trough_date": "d3", "recovery_date": "d4",
                    "depth": pytest.approx(-0.2)}]
