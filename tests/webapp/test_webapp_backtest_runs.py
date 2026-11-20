"""回测运行目录读取层（#662 R3）：读不到的值为 null，不用 0 或文件时间顶替。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from qsys.webapp.queries.backtest_runs import BacktestRuns

HEADER = ("run_id,strategy,start,end,sessions,initial_value,total_value,cumulative_return,fills,buys,"
          "sells,rejects,closed_lots,gate_decisions,gate_allowed,gate_rejected\n")


def run_dir(root: Path, name: str, row: str, config: dict | None = None) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "run_status.csv").write_text(HEADER + row + "\n", encoding="utf-8")
    if config is not None:
        (d / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return d


FULL = "r1,Demo,2024-01-02,2024-01-05,4,100000,99000,-0.01,2,1,1,1,1,0,0,0"


def test_runs_lists_valid_and_skips_broken(tmp_path):
    run_dir(tmp_path, "good", FULL, {"cost_model": "qsys 实盘档位", "generated_at": "2024-01-06T10:00:00",
                                     "source": "web", "params": {"window": 20}, "entry_fill": "open"})
    bad = tmp_path / "half"
    bad.mkdir()
    (bad / "run_status.csv").write_text("run_id,strategy\nr2,Demo\n", encoding="utf-8")
    (tmp_path / "empty").mkdir()
    (tmp_path / "loose.csv").write_text("x", encoding="utf-8")
    runs = BacktestRuns(tmp_path).runs()
    assert [r.name for r in runs] == ["good"]
    r = runs[0]
    assert (r.run_id, r.strategy, r.start, r.end) == ("r1", "Demo", "2024-01-02", "2024-01-05")
    assert (r.initial_value, r.total_value, r.cumulative_return) == (100000.0, 99000.0, -0.01)
    assert (r.buys, r.sells, r.rejects, r.closed_lots) == (1, 1, 1, 1)
    assert (r.cost_model, r.entry_fill, r.source, r.params) == ("qsys 实盘档位", "open", "web", {"window": 20})
    assert r.generated_at == "2024-01-06T10:00:00"


def test_summary_blank_numbers_are_null(tmp_path):
    run_dir(tmp_path, "blank", "r1,Demo,2024-01-02,2024-01-05,4,,99000,,2,,1,1,1,0,0,0", {})
    r = BacktestRuns(tmp_path).run("blank")
    assert r.initial_value is None and r.cumulative_return is None and r.buys is None
    assert r.total_value == 99000.0 and r.sells == 1
    assert r.cost_model is None and r.entry_fill is None and r.source == "cli"


def test_generated_at_null_without_config(tmp_path):
    run_dir(tmp_path, "noconf", FULL)
    r = BacktestRuns(tmp_path).run("noconf")
    assert r.generated_at is None and r.params == {}


def test_legacy_config_without_params_block_keeps_strategy_keys(tmp_path):
    run_dir(tmp_path, "old", FULL, {"cost_model": "x", "window": 30, "benchmark": "000300.SH"})
    assert BacktestRuns(tmp_path).run("old").params == {"window": 30}


def test_run_name_rejects_traversal(tmp_path):
    run_dir(tmp_path / "runs", "ok", FULL)
    run_dir(tmp_path, "outside", FULL)
    runs = BacktestRuns(tmp_path / "runs")
    for name in ("../outside", ".ok", "", "a/b", "ok\\x"):
        assert runs.run(name) is None and runs.files(name) == [] and runs.table(name, "run_status.csv") == []
    assert runs.run("ok") is not None


def test_files_excludes_symlink_and_unlisted_suffix(tmp_path):
    d = run_dir(tmp_path, "r", FULL)
    (d / "fills.csv").write_text("a\n1\n", encoding="utf-8")
    (d / "state.sqlite3").write_bytes(b"x")
    os.symlink(d / "fills.csv", d / "link.csv")
    assert [f.name for f in BacktestRuns(tmp_path).files("r")] == ["fills.csv", "run_status.csv"]


def test_table_keeps_raw_date_strings(tmp_path):
    d = run_dir(tmp_path, "r", FULL)
    (d / "fills.csv").write_text("filled_at,qty,price,note\n2024-01-03T09:31:00,100,10.5,\n"
                                 "2024-01-05T14:55:00,200,9.9,x\n", encoding="utf-8")
    runs = BacktestRuns(tmp_path)
    rows = runs.table("r", "fills.csv")
    assert rows[0] == {"filled_at": "2024-01-03T09:31:00", "qty": 100, "price": 10.5, "note": None}
    assert runs.table("r", "fills.csv", limit=1) == rows[1:]
    assert runs.table("r", "missing.csv") == []


def test_nav_blank_value_is_none_and_benchmark_skips_blank(tmp_path):
    d = run_dir(tmp_path, "r", FULL)
    (d / "daily_nav.csv").write_text("date,total_value,daily_return\n2024-01-02,100000,\n2024-01-03,,0.1\n",
                                     encoding="utf-8")
    (d / "benchmark_nav.csv").write_text("date,total_value\n2024-01-02,1.0\n2024-01-03,\n", encoding="utf-8")
    runs = BacktestRuns(tmp_path)
    assert runs.nav("r") == [("2024-01-02", 100000.0), ("2024-01-03", None)]
    assert runs.benchmark("r") == {"2024-01-02": 1.0}


def test_metrics_skip_non_numeric(tmp_path):
    d = run_dir(tmp_path, "r", FULL)
    (d / "metrics.csv").write_text("metric,value\nmax_drawdown,-0.2\nnote,abc\n", encoding="utf-8")
    assert BacktestRuns(tmp_path).metrics("r") == {"max_drawdown": -0.2}
