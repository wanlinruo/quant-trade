"""回测运行浏览：能列出、能下载，且不能越界读文件。"""

import json
from pathlib import Path

import pytest

from qsys.web.backtest_views import BacktestService

STATUS = (
    "run_id,strategy,start,end,sessions,initial_value,total_value,"
    "cumulative_return,fills,buys,sells,rejects,closed_lots,gate_decisions,"
    "gate_allowed,gate_rejected\n"
    "abc123,StructuralStrategy,2025-09-10,2026-09-08,241,100000.0,173386.25,"
    "0.7338625,168,84,84,2,84,235,109,126\n"
)


@pytest.fixture
def runs(tmp_path):
    run = tmp_path / "jq100k"
    run.mkdir()
    (run / "run_status.csv").write_text(STATUS, encoding="utf-8")
    (run / "config.json").write_text(
        json.dumps({"cost_model": "聚宽默认", "generated_at": "2026-09-10T13:48:00"}),
        encoding="utf-8",
    )
    (run / "metrics.csv").write_text(
        "metric,value\ntotal_return,0.7338625\nsharpe,1.449\n", encoding="utf-8")
    (run / "branch_summary.csv").write_text(
        "strategy_tag,closed_lots,wins,win_rate,avg_return_pct,"
        "median_return_pct,gross_realized_pnl,avg_holding_trade_days\n"
        "HIGH_MAIN,40,28,0.7,3.1,2.4,12000.0,1.5\n", encoding="utf-8")
    (run / "daily_nav.csv").write_text(
        "date,total_value,available_cash,position_count,daily_return\n"
        "2025-09-10,100000.0,100000.0,0,0.0\n"
        "2025-09-11,101000.0,0.0,1,0.01\n", encoding="utf-8")
    (run / "jq100k.zip").write_bytes(b"PK\x03\x04fake")
    return BacktestService(tmp_path)


def test_runs_are_listed_with_their_headline_numbers(runs):
    got = runs.runs()
    assert len(got) == 1
    r = got[0]
    assert r.name == "jq100k"
    assert r.strategy == "StructuralStrategy"
    assert r.buys == 84 and r.sells == 84
    assert r.return_pct == pytest.approx(73.38625)
    assert r.cost_model == "聚宽默认"


def test_an_empty_runs_root_lists_nothing(tmp_path):
    assert BacktestService(tmp_path / "nope").runs() == []


def test_a_directory_without_status_is_skipped(runs, tmp_path):
    (tmp_path / "half-written").mkdir()
    assert [r.name for r in runs.runs()] == ["jq100k"]


def test_a_broken_status_file_does_not_break_the_listing(runs, tmp_path):
    bad = tmp_path / "corrupt"
    bad.mkdir()
    (bad / "run_status.csv").write_text("not,a,valid\ncsv\n", encoding="utf-8")
    assert [r.name for r in runs.runs()] == ["jq100k"]


def test_files_are_listed_with_sizes(runs):
    names = {f.name for f in runs.files("jq100k")}
    assert {"run_status.csv", "metrics.csv", "jq100k.zip", "config.json"} <= names
    assert all(f.size > 0 for f in runs.files("jq100k"))


def test_metrics_and_branch_summary_come_back_parsed(runs):
    assert dict(runs.metrics("jq100k"))["sharpe"] == pytest.approx(1.449)
    assert runs.branch_summary("jq100k")[0]["strategy_tag"] == "HIGH_MAIN"


def test_equity_points_feed_the_chart(runs):
    assert runs.equity_points("jq100k") == [
        ("2025-09-10", 100000.0), ("2025-09-11", 101000.0)]


def test_a_file_can_be_downloaded_with_the_right_media_type(runs):
    body, media = runs.file_bytes("jq100k", "run_status.csv")
    assert body.startswith(b"run_id")
    assert media.startswith("text/csv")
    assert runs.file_bytes("jq100k", "jq100k.zip")[1] == "application/zip"


# ---- 越界 ----

@pytest.mark.parametrize("name", ["../etc", "a/b", "a\\b", ".hidden", ""])
def test_a_run_name_cannot_escape_the_root(runs, name):
    assert runs.run(name) is None
    assert runs.files(name) == []


@pytest.mark.parametrize("filename", [
    "../config.json", "../../etc/passwd", "/etc/passwd"])
def test_a_filename_cannot_escape_the_run_directory(runs, filename):
    assert runs.file_bytes("jq100k", filename) is None


def test_only_allowed_extensions_are_served(runs, tmp_path):
    (tmp_path / "jq100k" / "secret.pem").write_text("key", encoding="utf-8")
    assert runs.file_bytes("jq100k", "secret.pem") is None


def test_a_missing_file_returns_nothing(runs):
    assert runs.file_bytes("jq100k", "absent.csv") is None
