from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from qsys.ops.journal import SessionJournal
from qsys.live.transport import MemoryTransport
from qsys.web.app import create_app
from qsys.web.config import WebConfig
from qsys.web.data_views import MarketService
from qsys.web.limit_up_shadow import (
    LimitUpShadowPublisher,
    LimitUpShadowReader,
    LimitUpShadowOutcome,
    LimitUpShadowSnapshot,
)
from qsys.web.views import MonitorService

def _payload(**overrides):
    payload = {
        "prediction_date": "2026-10-09",
        "target_session": "2026-10-12",
        "generated_at": "2026-10-09T17:10:00+08:00",
        "status": "ready",
        "model_version": "limit-up-lgbm-v1",
        "model_sha": "abcdef1234567890",
        "data_version": "warehouse-20261009",
        "data_sha": "1234567890abcdef",
        "eligible_count": 4821,
        "rolling_precision_at_20": 0.31,
        "calibration_error": 0.012,
        "drift_score": 0.08,
        "entries": [
            {
                "rank": 1, "symbol": "000001.SZ", "name": "平安银行",
                "board": "主板", "market_state": "震荡",
                "p_touch": 0.8, "p_seal_given_touch": 0.5,
                "p_close_limit": 0.4,
            },
            {
                "rank": 2, "symbol": "600000.SH", "name": "浦发银行",
                "board": "主板", "market_state": "震荡",
                "p_touch": 0.6, "p_seal_given_touch": 0.4,
                "p_close_limit": 0.24,
                "actual_touched": True, "actual_closed_limit": False,
            },
        ],
    }
    payload.update(overrides)
    return payload


def _client(warehouse, reader: LimitUpShadowReader) -> TestClient:
    config = WebConfig()
    monitor = MonitorService(SessionJournal(warehouse), config=config)
    return TestClient(create_app(monitor, config, limit_up_shadow=reader))


def test_reader_and_page_expose_ready_snapshot(warehouse, tmp_path: Path):
    path = tmp_path / "limit-up-shadow.sqlite3"
    snapshot = LimitUpShadowSnapshot.model_validate(_payload())
    LimitUpShadowPublisher(path).publish(snapshot)
    client = _client(warehouse, LimitUpShadowReader(path))

    page = client.get("/market/limit-up-shadow")
    payload = client.get("/api/market/limit-up-shadow").json()

    assert page.status_code == 200
    assert "AI 涨停观察" in page.text and "只读影子" in page.text
    assert "平安银行" in page.text and "40.0%" in page.text
    assert "买入" not in page.text
    assert payload["state"] == "ready" and payload["available"] is True
    assert payload["snapshot"]["entries"][0]["p_close_limit"] == 0.4


def test_missing_store_fails_closed_without_creating_a_file(warehouse, tmp_path: Path):
    path = tmp_path / "missing.sqlite3"
    client = _client(warehouse, LimitUpShadowReader(path))

    payload = client.get("/api/market/limit-up-shadow").json()

    assert payload["state"] == "unavailable"
    assert payload["available"] is False
    assert not path.exists()
    assert "不会用旧数据" in client.get("/market/limit-up-shadow").text


def test_latest_past_target_is_stale_but_explicit_history_is_inspectable(tmp_path: Path):
    path = tmp_path / "shadow.sqlite3"
    LimitUpShadowPublisher(path).publish(LimitUpShadowSnapshot.model_validate(_payload()))
    reader = LimitUpShadowReader(path)

    latest = reader.view(today=date(2026, 10, 13))
    historical = reader.view(date(2026, 10, 9), today=date(2026, 10, 13))

    assert latest.state == "stale" and latest.available is False
    assert historical.state == "ready" and historical.historical is True


def test_probability_identity_is_enforced():
    payload = _payload()
    payload["entries"][0]["p_close_limit"] = 0.81

    with pytest.raises(ValidationError, match="must equal"):
        LimitUpShadowSnapshot.model_validate(payload)


def test_final_daily_snapshot_is_immutable(tmp_path: Path):
    path = tmp_path / "shadow.sqlite3"
    snapshot = LimitUpShadowSnapshot.model_validate(_payload())
    publisher = LimitUpShadowPublisher(path)
    publisher.publish(snapshot)

    with pytest.raises(ValueError, match="immutable"):
        publisher.publish(snapshot)


def test_generating_state_can_transition_to_ready(tmp_path: Path):
    path = tmp_path / "shadow.sqlite3"
    publisher = LimitUpShadowPublisher(path)
    generating = LimitUpShadowSnapshot.model_validate(_payload(
        status="generating", entries=[], rolling_precision_at_20=None,
    ))
    publisher.publish(generating)
    assert LimitUpShadowReader(path).view().state == "generating"

    publisher.publish(LimitUpShadowSnapshot.model_validate(_payload()))
    assert LimitUpShadowReader(path).view(today=date(2026, 10, 10)).state == "ready"


def test_generated_at_requires_timezone():
    with pytest.raises(ValidationError, match="timezone"):
        LimitUpShadowSnapshot.model_validate(_payload(
            generated_at=datetime(2026, 10, 9, 17, 10),
        ))


def test_market_overview_links_to_shadow_top_three(warehouse, tmp_path: Path):
    path = tmp_path / "shadow.sqlite3"
    LimitUpShadowPublisher(path).publish(LimitUpShadowSnapshot.model_validate(_payload()))
    config = WebConfig()
    monitor = MonitorService(SessionJournal(warehouse), config=config)
    client = TestClient(create_app(
        monitor, config, market=MarketService(warehouse, MemoryTransport()),
        limit_up_shadow=LimitUpShadowReader(path),
    ))

    body = client.get("/market").text

    assert "/market/limit-up-shadow" in body
    assert "AI 涨停观察" in body and "平安银行" in body


def test_outcomes_are_separate_immutable_and_drive_rolling_metrics(tmp_path: Path):
    path = tmp_path / "shadow.sqlite3"
    publisher = LimitUpShadowPublisher(path)
    publisher.publish(LimitUpShadowSnapshot.model_validate(_payload()))
    outcomes = (
        LimitUpShadowOutcome(
            symbol="000001.SZ", actual_touched=True, actual_closed_limit=True,
            opened_at_limit=False, one_price_limit=False,
        ),
        LimitUpShadowOutcome(
            symbol="600000.SH", actual_touched=True, actual_closed_limit=False,
            opened_at_limit=False, one_price_limit=False,
        ),
    )
    observed_at = datetime.fromisoformat("2026-10-12T16:00:00+08:00")

    reader = LimitUpShadowReader(path)
    assert reader.pending_outcomes(date(2026, 10, 12)) == (
        (date(2026, 10, 9), date(2026, 10, 12)),
    )
    publisher.record_outcomes(
        date(2026, 10, 9), outcomes, observed_at=observed_at,
        source_data_sha="d" * 64,
    )
    publisher.record_outcomes(
        date(2026, 10, 9), outcomes, observed_at=observed_at,
        source_data_sha="d" * 64,
    )

    view = reader.view(date(2026, 10, 9))
    assert reader.pending_outcomes(date(2026, 10, 12)) == ()
    assert view.snapshot.entries[0].actual_closed_limit is True
    assert view.snapshot.entries[1].actual_closed_limit is False
    assert view.snapshot.rolling_precision_at_20 == pytest.approx(0.5)
    assert view.snapshot.calibration_error == pytest.approx(0.2088)
    assert view.history[0].precision_at_20 == pytest.approx(0.5)

    conflicting = outcomes[0].model_copy(update={"actual_closed_limit": False})
    with pytest.raises(ValueError, match="immutable"):
        publisher.record_outcomes(
            date(2026, 10, 9), (conflicting, outcomes[1]),
            observed_at=observed_at, source_data_sha="e" * 64,
        )
