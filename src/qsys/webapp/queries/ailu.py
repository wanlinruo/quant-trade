"""AI 涨停查询（设计文档 §8.12）：把影子榜单与研究回测读取器的结果映射为对外契约。

状态与消息取读取器的判断，不另行推断；模型与数据的内部指纹不进入契约。
"""

from __future__ import annotations

from datetime import date, datetime

from qsys.webapp.contracts.ailu import (
    ResearchBoard,
    ResearchDay,
    ResearchEntry,
    ResearchHistoryDay,
    ResearchMonth,
    ResearchSummary,
    ShadowBoard,
    ShadowDay,
    ShadowEntry,
    ShadowSnapshot,
)

_REASON = "影子榜单记录尚未提供基线与分桶校准数据（见 #676）"
MISSING = {"baseline": _REASON, "rolling_10d": _REASON, "calibration_curve": _REASON,
           "drift_series": _REASON}
_ENTRY = ("rank", "symbol", "name", "board", "p_touch", "p_seal_given_touch", "p_close_limit",
          "actual_touched", "actual_closed_limit", "opened_at_limit", "one_price_limit")


def _pick(obj, names) -> dict:
    return {n: getattr(obj, n) for n in names}


def shadow_board(reader, day: date | None) -> ShadowBoard:
    if reader is None:
        return ShadowBoard(state="unavailable", message="影子榜单数据源未配置", historical=day is not None,
                           snapshot=None, history=[], missing=MISSING)
    v = reader.view(day)
    s = v.snapshot
    snapshot = None if s is None else ShadowSnapshot(
        **_pick(s, ("prediction_date", "target_session", "generated_at", "status", "model_version",
                    "eligible_count", "rolling_precision_at_20", "calibration_error", "drift_score")),
        entries=[ShadowEntry(**_pick(e, _ENTRY)) for e in s.entries])
    history = [ShadowDay(**_pick(h, ("prediction_date", "target_session", "generated_at", "status",
                                     "model_version", "entry_count", "precision_at_20")))
               for h in v.history]
    return ShadowBoard(state=v.state, message=v.message, historical=v.historical,
                       snapshot=snapshot, history=history, missing=MISSING)


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def research_board(reader, day: date | None) -> ResearchBoard:
    if reader is None:
        return ResearchBoard(state="unavailable", message="研究回测数据源未配置", summary=None,
                             selected=None, history=[], monthly=[])
    v = reader.view(day)
    s = v.summary
    summary = None if s is None else ResearchSummary(
        generated_at=_parse_time(s.generated_at),
        **_pick(s, ("source_sha", "data_sha", "first_prediction_date", "last_prediction_date", "sessions",
                    "test_sessions", "obs_sessions", "test_precision_at_20", "obs_precision_at_20",
                    "obs_baseline_precision_at_20", "obs_lift_at_20", "obs_month_win_rate",
                    "promoted_to_shadow")))
    d = v.selected
    selected = None if d is None else ResearchDay(
        **_pick(d, ("prediction_date", "target_session", "split", "precision_at_20", "touch_precision_at_20")),
        entries=[ResearchEntry(**_pick(e, _ENTRY)) for e in d.entries])
    return ResearchBoard(
        state=v.state, message=v.message, summary=summary, selected=selected,
        history=[ResearchHistoryDay(**_pick(h, ("prediction_date", "target_session", "split",
                                                 "precision_at_20", "touch_precision_at_20")))
                 for h in v.history],
        monthly=[ResearchMonth(**_pick(m, ("month", "split", "precision_at_20", "predictions", "hits")))
                 for m in v.monthly])
