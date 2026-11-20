import csv
import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from qsys.research.limit_up_research_store import (
    HistoricalSecurityNameLookup,
    LimitUpResearchReader,
)


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _research_run(root: Path, *, promoted: bool = True) -> Path:
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "research": "issue-210-limit-up-ranking",
        "generated_at": "2026-10-10T07:30:38",
        "git_sha": "a" * 40,
        "dataset": {"sha256": "b" * 64},
    }), encoding="utf-8")
    (root / "verdict.json").write_text(json.dumps({
        "promote_to_shadow": promoted,
        "best_baseline": "logistic",
        "obs_lift_at_20": 23.900991,
        "obs_month_win_rate": 1.0,
    }), encoding="utf-8")

    fields = [
        "prediction_date", "target_session", "symbol", "board", "market_state",
        "split", "actual_touch", "actual_close", "opened_at_limit",
        "one_price_limit", "model", "p_touch", "p_seal_given_touch",
        "p_close", "rank",
    ]
    rows: list[dict[str, object]] = []
    first = date(2025, 7, 1)
    for offset in range(307):
        prediction = first + timedelta(days=offset)
        target = prediction + timedelta(days=1)
        split = "TEST" if offset < 126 else "OBS"
        for rank in range(1, 21):
            p_touch = 0.81 - rank / 100
            p_seal = 0.5
            rows.append({
                "prediction_date": prediction.isoformat(),
                "target_session": target.isoformat(),
                "symbol": f"{offset:03d}{rank:03d}.SZ",
                "board": "main", "market_state": "neutral", "split": split,
                "actual_touch": "true" if rank <= 10 else "false",
                "actual_close": "true" if rank <= 6 else "false",
                "opened_at_limit": "false", "one_price_limit": "false",
                "model": "lightgbm", "p_touch": p_touch,
                "p_seal_given_touch": p_seal, "p_close": p_touch * p_seal,
                "rank": rank,
            })
    _write_csv(root / "top20.csv", fields, rows)

    metric_fields = [
        "model", "split", "label", "k", "sessions", "precision", "recall",
        "lift", "brier", "log_loss",
    ]
    _write_csv(root / "metrics.csv", metric_fields, [
        {"model": "lightgbm", "split": "TEST", "label": "actual_close", "k": 20,
         "sessions": 126, "precision": 0.370238, "recall": "", "lift": "",
         "brier": "", "log_loss": ""},
        {"model": "lightgbm", "split": "OBS", "label": "actual_close", "k": 20,
         "sessions": 181, "precision": 0.305525, "recall": "", "lift": "",
         "brier": "", "log_loss": ""},
        {"model": "logistic", "split": "OBS", "label": "actual_close", "k": 20,
         "sessions": 181, "precision": 0.189227, "recall": "", "lift": "",
         "brier": "", "log_loss": ""},
    ])
    monthly_fields = ["model", "split", "month", "precision_at_k", "predictions", "hits", "k"]
    _write_csv(root / "monthly_precision.csv", monthly_fields, [
        {"model": "lightgbm", "split": "TEST", "month": "2025-07-01",
         "precision_at_k": 0.4, "predictions": 400, "hits": 160, "k": 20},
        {"model": "lightgbm", "split": "OBS", "month": "2026-01-01",
         "precision_at_k": 0.35, "predictions": 400, "hits": 140, "k": 20},
    ])
    return root


def test_research_reader_exposes_frozen_history_and_daily_top20(tmp_path):
    reader = LimitUpResearchReader(
        _research_run(tmp_path / "research"),
        name_lookup=lambda symbol, day: f"示例股份{symbol[-6:-3]}",
    )
    payload = reader.view()
    assert payload.available is True and payload.state == "ready"
    assert payload.summary.sessions == 307
    assert payload.summary.test_sessions == 126
    assert payload.summary.obs_sessions == 181
    assert payload.summary.obs_precision_at_20 == 0.305525
    assert len(payload.selected.entries) == 20
    assert len(payload.history) == 307
    assert payload.selected.entries[0].name.startswith("示例股份")


def test_research_reader_selects_an_explicit_test_day(tmp_path):
    reader = LimitUpResearchReader(_research_run(tmp_path / "research"))
    payload = reader.view(date(2025, 7, 1))
    assert payload.selected.prediction_date == date(2025, 7, 1)
    assert payload.selected.split == "TEST"
    assert payload.selected.precision_at_20 == 0.3
    assert payload.selected.touch_precision_at_20 == 0.5


def test_missing_research_evidence_fails_closed_without_creating_files(
    tmp_path,
):
    root = tmp_path / "missing"
    payload = LimitUpResearchReader(root).view()
    assert payload.available is False
    assert payload.state == "unavailable"
    assert not root.exists()


def test_unpromoted_research_fails_closed(tmp_path):
    reader = LimitUpResearchReader(
        _research_run(tmp_path / "research", promoted=False)
    )

    payload = reader.view()
    assert payload.available is False
    assert payload.state == "unavailable"


def test_security_names_use_historical_interval_then_current_fallback(tmp_path):
    lookup = HistoricalSecurityNameLookup(tmp_path)
    lookup._intervals = {
        "000001.SZ": ((date(2020, 1, 1), date(2025, 12, 31), "历史名称"),),
    }
    lookup._current = {"000001.SZ": "当前名称", "600000.SH": "浦发银行"}

    assert lookup("000001.SZ", date(2025, 7, 1)) == "历史名称"
    assert lookup("000001.SZ", date(2026, 1, 1)) == "当前名称"
    assert lookup("600000.SH", date(2025, 7, 1)) == "浦发银行"


def test_overlapping_historical_names_fail_closed(tmp_path):
    lookup = HistoricalSecurityNameLookup(tmp_path)
    lookup._intervals = {
        "000001.SZ": (
            (date(2020, 1, 1), date(2025, 12, 31), "名称一"),
            (date(2025, 1, 1), None, "名称二"),
        ),
    }

    with pytest.raises(ValueError, match="overlapping"):
        lookup("000001.SZ", date(2025, 7, 1))
