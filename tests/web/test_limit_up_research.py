import csv
import json
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from qsys.ops.journal import SessionJournal
from qsys.web.app import create_app
from qsys.web.config import WebConfig
from qsys.web.limit_up_research import LimitUpResearchReader
from qsys.web.views import MonitorService


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


def _client(warehouse, reader: LimitUpResearchReader) -> TestClient:
    config = WebConfig()
    monitor = MonitorService(SessionJournal(warehouse), config=config)
    return TestClient(create_app(monitor, config, limit_up_research=reader))


def test_research_page_exposes_frozen_history_and_daily_top20(warehouse, tmp_path):
    reader = LimitUpResearchReader(_research_run(tmp_path / "research"))
    client = _client(warehouse, reader)

    response = client.get("/api/market/limit-up-research")
    page = client.get("/market/limit-up-research")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True and payload["state"] == "ready"
    assert payload["summary"]["sessions"] == 307
    assert payload["summary"]["test_sessions"] == 126
    assert payload["summary"]["obs_sessions"] == 181
    assert payload["summary"]["obs_precision_at_20"] == 0.305525
    assert len(payload["selected"]["entries"]) == 20
    assert len(payload["history"]) == 307
    assert "AI 涨停研究回测" in page.text
    assert "冻结研究" in page.text and "307 日研究历史" in page.text
    assert "买入" not in page.text


def test_research_page_selects_an_explicit_test_day(warehouse, tmp_path):
    reader = LimitUpResearchReader(_research_run(tmp_path / "research"))
    client = _client(warehouse, reader)

    payload = client.get(
        "/api/market/limit-up-research?day=2025-07-01"
    ).json()

    assert payload["selected"]["prediction_date"] == "2025-07-01"
    assert payload["selected"]["split"] == "TEST"
    assert payload["selected"]["precision_at_20"] == 0.3
    assert payload["selected"]["touch_precision_at_20"] == 0.5


def test_missing_research_evidence_fails_closed_without_creating_files(
    warehouse, tmp_path,
):
    root = tmp_path / "missing"
    client = _client(warehouse, LimitUpResearchReader(root))

    payload = client.get("/api/market/limit-up-research").json()

    assert payload["available"] is False
    assert payload["state"] == "unavailable"
    assert not root.exists()
    assert "不会用生产影子数据" in client.get("/market/limit-up-research").text


def test_unpromoted_research_fails_closed(warehouse, tmp_path):
    reader = LimitUpResearchReader(
        _research_run(tmp_path / "research", promoted=False)
    )

    payload = _client(warehouse, reader).get(
        "/api/market/limit-up-research"
    ).json()

    assert payload["available"] is False
    assert payload["state"] == "unavailable"
