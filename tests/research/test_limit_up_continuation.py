from pathlib import Path

import polars as pl

from qsys.research.limit_up_continuation import (
    bootstrap_daily_mean,
    historical_verdict,
    summarize_run,
)


def _artifact(path: Path) -> Path:
    path.mkdir()
    pl.DataFrame({"metric": ["total_return", "sharpe", "max_drawdown"],
                  "value": [0.05, 1.2, 0.04]}).write_csv(path / "metrics.csv")
    pl.DataFrame({"start": ["2025-01-01"], "end": ["2025-03-31"],
                  "closed_lots": [40]}).write_csv(path / "run_status.csv")
    pl.DataFrame({"daily_return": [0.01] * 60}).write_csv(path / "daily_nav.csv")
    pl.DataFrame({"return": [0.01, 0.02, 0.03]}).write_csv(
        path / "monthly_returns.csv")
    pl.DataFrame({"realized_pnl_gross": [10.0] * 40}).write_csv(
        path / "closed_lots.csv")
    pl.DataFrame({"side": ["buy"] * 40}).write_csv(path / "orders.csv")
    pl.DataFrame({"side": ["buy"] * 40}).write_csv(path / "fills.csv")
    (path / "config.json").write_text(
        '{"entry_fill_stats":"continuous=40,orders=40"}', encoding="utf-8")
    return path


def test_summary_and_verdict_keep_historical_pass_separate_from_trading(tmp_path):
    rows = [
        summarize_run(_artifact(tmp_path / split), split=split,
                      exit_policy="next_open", bootstrap_samples=100)
        for split in ("TEST", "OBS")
    ]
    verdict = historical_verdict(rows)
    assert verdict["historical_support"] is True
    assert verdict["prospective_shadow_required"] is True
    assert verdict["promote_to_trading"] is False


def test_bootstrap_rejects_empty_returns():
    try:
        bootstrap_daily_mean([])
    except ValueError as exc:
        assert "empty" in str(exc)
    else:
        raise AssertionError("empty returns must fail closed")
