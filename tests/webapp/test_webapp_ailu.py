"""AI 涨停：新契约与查询层（#662，设计文档 §8.12）。"""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from qsys.webapp.app import WebappDeps, create_app
from qsys.webapp.config import WebConfig
from qsys.research.limit_up_research_store import (
    LimitUpResearchDay, LimitUpResearchEntry, LimitUpResearchHistory, LimitUpResearchMonth,
    LimitUpResearchSummary, LimitUpResearchView,
)
from qsys.research.limit_up_shadow_store import (
    LimitUpShadowEntry, LimitUpShadowHistory, LimitUpShadowSnapshot, LimitUpShadowView,
)
from qsys.webapp.live_kill_status import LiveKillReader
from qsys.webapp.queries.ailu import MISSING, research_board, shadow_board

GEN = datetime(2026, 11, 16, 11, 36, tzinfo=timezone.utc)


def shadow_view(state="ready") -> LimitUpShadowView:
    entry = LimitUpShadowEntry(rank=1, symbol="600000.SH", name="甲", board="主板", market_state="",
                               p_touch=0.5, p_seal_given_touch=0.6, p_close_limit=0.3)
    snap = LimitUpShadowSnapshot(
        prediction_date=date(2026, 11, 16), target_session=date(2026, 11, 17), generated_at=GEN,
        model_version="v3", model_sha="abcdef1", data_version="d1", data_sha="1234567",
        eligible_count=87, rolling_precision_at_20=0.31, entries=(entry,))
    hist = LimitUpShadowHistory(prediction_date=date(2026, 11, 16), target_session=date(2026, 11, 17),
                                generated_at=GEN, status="ready", model_version="v3", entry_count=20)
    return LimitUpShadowView(state=state, available=state == "ready", message="只读影子结果已就绪",
                             snapshot=snap, history=(hist,))


class Reader:
    def __init__(self, view):
        self.v, self.calls = view, []

    def view(self, day=None):
        self.calls.append(day)
        return self.v


def research_view() -> LimitUpResearchView:
    entries = tuple(LimitUpResearchEntry(
        rank=i, symbol=f"6000{i:02d}.SH", name=f"样本{i}", board="主板", market_state="",
        p_touch=0.5, p_seal_given_touch=round(0.8 - i * 0.01, 4), p_close_limit=round(0.5 * (0.8 - i * 0.01), 6),
        actual_touched=True, actual_closed_limit=i == 1, opened_at_limit=False, one_price_limit=False)
        for i in range(1, 21))
    day = LimitUpResearchDay(prediction_date=date(2026, 10, 30), target_session=date(2026, 10, 31), split="OBS",
                             precision_at_20=0.05, touch_precision_at_20=1.0, entries=entries)
    return LimitUpResearchView(
        state="ready", available=True, message="研究回测结果", selected=day,
        summary=LimitUpResearchSummary(
            generated_at="2026-11-01T10:00:00", source_sha="s" * 40, data_sha="d" * 40,
            first_prediction_date=date(2024, 1, 2), last_prediction_date=date(2026, 10, 31), sessions=690,
            test_sessions=480, obs_sessions=210, test_precision_at_20=0.33, obs_precision_at_20=0.29,
            obs_baseline_precision_at_20=0.12, obs_lift_at_20=2.4, obs_month_win_rate=0.8, promoted_to_shadow=True),
        history=(LimitUpResearchHistory(prediction_date=date(2026, 10, 30), target_session=date(2026, 10, 31),
                                        split="OBS", precision_at_20=0.3, touch_precision_at_20=0.5),),
        monthly=(LimitUpResearchMonth(month=date(2026, 10, 1), split="OBS", precision_at_20=0.3,
                                      predictions=440, hits=132),))


def test_shadow_contract_maps_reader_view():
    b = shadow_board(Reader(shadow_view()), None).model_dump(mode="json")
    assert b["state"] == "ready" and b["schema_version"] == 1
    snap = b["snapshot"]
    assert snap["generated_at"] == "2026-11-16T19:36:00+08:00"
    assert snap["entries"][0]["p_close_limit"] == 0.3 and snap["entries"][0]["actual_touched"] is None
    assert "model_sha" not in snap and "data_sha" not in snap
    assert b["history"][0]["precision_at_20"] is None


def test_shadow_unconfigured_is_unavailable():
    b = shadow_board(None, None)
    assert b.state == "unavailable" and b.snapshot is None and b.message


def test_ailu_missing_reasons_cite_676():
    assert set(MISSING) == {"baseline", "rolling_10d", "calibration_curve", "drift_series"}
    assert all("#676" in v for v in MISSING.values())


def test_research_contract_maps_reader_view():
    b = research_board(Reader(research_view()), None).model_dump(mode="json")
    assert b["summary"]["generated_at"] == "2026-11-01T10:00:00+08:00"
    assert b["selected"]["entries"][0]["actual_closed_limit"] is True
    assert b["monthly"][0]["hits"] == 132


def test_research_unparseable_generated_at_is_null():
    v = research_view()
    v = v.model_copy(update={"summary": v.summary.model_copy(update={"generated_at": ""})})
    assert research_board(Reader(v), None).summary.generated_at is None


def _client(warehouse, shadow=None, research=None) -> TestClient:
    cfg = WebConfig()
    deps = WebappDeps(live_kill=LiveKillReader(None, None), shadow=shadow, research=research)
    return TestClient(create_app(deps, cfg))


def test_routes_pass_day_and_validate(warehouse):
    sh, rs = Reader(shadow_view()), Reader(research_view())
    c = _client(warehouse, sh, rs)
    r = c.get("/api/app/ailu/shadow", params={"day": "2026-11-13"})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert sh.calls == [date(2026, 11, 13)]
    c.get("/api/app/ailu/research")
    assert rs.calls == [None]
    bad = c.get("/api/app/ailu/shadow", params={"day": "11/13"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_request"


def test_routes_unconfigured_200(warehouse):
    c = _client(warehouse)
    assert c.get("/api/app/ailu/shadow").json()["state"] == "unavailable"
    assert c.get("/api/app/ailu/research").json()["state"] == "unavailable"
