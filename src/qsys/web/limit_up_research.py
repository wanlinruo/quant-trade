"""Read-only view of the frozen Issue #210 limit-up ranking research evidence."""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from collections.abc import Callable
from datetime import date
from pathlib import Path
from threading import Lock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ResearchState = Literal["ready", "unavailable", "not_found"]
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")


class LimitUpResearchEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    rank: int = Field(ge=1, le=20)
    symbol: str = Field(min_length=1)
    name: str = ""
    board: str
    market_state: str
    p_touch: float = Field(ge=0, le=1)
    p_seal_given_touch: float = Field(ge=0, le=1)
    p_close_limit: float = Field(ge=0, le=1)
    actual_touched: bool
    actual_closed_limit: bool
    opened_at_limit: bool
    one_price_limit: bool

    @model_validator(mode="after")
    def values_are_coherent(self):
        if abs(self.p_close_limit - self.p_touch * self.p_seal_given_touch) > 1e-6:
            raise ValueError("p_close_limit must equal p_touch * p_seal_given_touch")
        if self.actual_closed_limit and not self.actual_touched:
            raise ValueError("actual close limit implies actual touch")
        if self.opened_at_limit and not self.actual_touched:
            raise ValueError("limit-up open implies actual touch")
        if self.one_price_limit and not (
            self.actual_touched and self.actual_closed_limit and self.opened_at_limit
        ):
            raise ValueError("one-price limit result is incoherent")
        return self


class LimitUpResearchDay(BaseModel):
    model_config = ConfigDict(frozen=True)

    prediction_date: date
    target_session: date
    split: Literal["TEST", "OBS"]
    precision_at_20: float = Field(ge=0, le=1)
    touch_precision_at_20: float = Field(ge=0, le=1)
    entries: tuple[LimitUpResearchEntry, ...]

    @model_validator(mode="after")
    def day_is_complete(self):
        if self.target_session <= self.prediction_date:
            raise ValueError("target_session must follow prediction_date")
        if len(self.entries) != 20:
            raise ValueError("research day must contain exactly 20 entries")
        if [row.rank for row in self.entries] != list(range(1, 21)):
            raise ValueError("research ranks must be contiguous")
        probabilities = [row.p_close_limit for row in self.entries]
        if probabilities != sorted(probabilities, reverse=True):
            raise ValueError("research entries must be ranked by p_close_limit")
        if len({row.symbol for row in self.entries}) != 20:
            raise ValueError("research symbols must be unique")
        return self


class LimitUpResearchHistory(BaseModel):
    model_config = ConfigDict(frozen=True)

    prediction_date: date
    target_session: date
    split: Literal["TEST", "OBS"]
    precision_at_20: float = Field(ge=0, le=1)
    touch_precision_at_20: float = Field(ge=0, le=1)


class LimitUpResearchMonth(BaseModel):
    model_config = ConfigDict(frozen=True)

    month: date
    split: Literal["TEST", "OBS"]
    precision_at_20: float = Field(ge=0, le=1)
    predictions: int = Field(gt=0)
    hits: int = Field(ge=0)


class LimitUpResearchSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    generated_at: str
    source_sha: str
    data_sha: str
    first_prediction_date: date
    last_prediction_date: date
    sessions: int = Field(gt=0)
    test_sessions: int = Field(gt=0)
    obs_sessions: int = Field(gt=0)
    test_precision_at_20: float = Field(ge=0, le=1)
    obs_precision_at_20: float = Field(ge=0, le=1)
    obs_baseline_precision_at_20: float = Field(ge=0, le=1)
    obs_lift_at_20: float = Field(ge=0)
    obs_month_win_rate: float = Field(ge=0, le=1)
    promoted_to_shadow: bool


class LimitUpResearchView(BaseModel):
    state: ResearchState
    available: bool
    historical_research: bool = True
    message: str
    selected: LimitUpResearchDay | None = None
    summary: LimitUpResearchSummary | None = None
    history: tuple[LimitUpResearchHistory, ...] = ()
    monthly: tuple[LimitUpResearchMonth, ...] = ()


class _ResearchDataset(BaseModel):
    summary: LimitUpResearchSummary
    days: dict[date, LimitUpResearchDay]
    history: tuple[LimitUpResearchHistory, ...]
    monthly: tuple[LimitUpResearchMonth, ...]


def _boolean(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(f"invalid research boolean: {value!r}")


def _metric(
    rows: list[dict[str, str]], *, model: str, split: str, label: str, k: str = "20",
) -> float:
    matches = [
        row for row in rows
        if row["model"] == model and row["split"] == split
        and row["label"] == label and row["k"] == k
    ]
    if len(matches) != 1 or not matches[0]["precision"]:
        raise ValueError("required research metric is missing or duplicated")
    return float(matches[0]["precision"])


class LimitUpResearchReader:
    """Load only the compact, immutable research evidence needed by the Web page."""

    def __init__(
        self, root: Path,
        name_lookup: Callable[[str, date], str] | None = None,
    ) -> None:
        self.root = Path(root)
        self.name_lookup = name_lookup
        self._cache: _ResearchDataset | None = None
        self._lock = Lock()

    def view(self, prediction_date: date | None = None) -> LimitUpResearchView:
        try:
            dataset = self._dataset()
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            return LimitUpResearchView(
                state="unavailable", available=False,
                message=f"研究回测不可用：{type(exc).__name__}",
            )
        selected_date = prediction_date or dataset.history[0].prediction_date
        selected = dataset.days.get(selected_date)
        if selected is None:
            return LimitUpResearchView(
                state="not_found", available=False,
                message="该日期不在冻结研究样本中",
                summary=dataset.summary, history=dataset.history,
                monthly=dataset.monthly,
            )
        return LimitUpResearchView(
            state="ready", available=True,
            message="冻结研究回测已加载；与上线后的影子记录完全分离",
            selected=selected, summary=dataset.summary,
            history=dataset.history, monthly=dataset.monthly,
        )

    def _dataset(self) -> _ResearchDataset:
        with self._lock:
            if self._cache is None:
                self._cache = self._load()
            return self._cache

    def _load(self) -> _ResearchDataset:
        manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        verdict = json.loads((self.root / "verdict.json").read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1:
            raise ValueError("unsupported research manifest")
        if manifest.get("research") != "issue-210-limit-up-ranking":
            raise ValueError("unexpected research identity")
        source_sha = str(manifest.get("git_sha", ""))
        data_sha = str(manifest.get("dataset", {}).get("sha256", ""))
        if not _SHA40.fullmatch(source_sha) or not _SHA64.fullmatch(data_sha):
            raise ValueError("research fingerprints are invalid")
        if verdict.get("promote_to_shadow") is not True:
            raise ValueError("research did not pass the frozen promotion gate")

        with (self.root / "top20.csv").open(newline="", encoding="utf-8") as stream:
            grouped: dict[date, list[dict[str, str]]] = defaultdict(list)
            for row in csv.DictReader(stream):
                if row["model"] == "lightgbm":
                    grouped[date.fromisoformat(row["prediction_date"])].append(row)
        if not grouped:
            raise ValueError("research top20 is empty")

        days: dict[date, LimitUpResearchDay] = {}
        for prediction_day, rows in grouped.items():
            ordered = sorted(rows, key=lambda row: int(row["rank"]))
            entries = tuple(LimitUpResearchEntry(
                rank=int(row["rank"]), symbol=row["symbol"],
                name=self._name(row["symbol"], prediction_day), board=row["board"],
                market_state=row["market_state"], p_touch=float(row["p_touch"]),
                p_seal_given_touch=float(row["p_seal_given_touch"]),
                p_close_limit=float(row["p_close"]),
                actual_touched=_boolean(row["actual_touch"]),
                actual_closed_limit=_boolean(row["actual_close"]),
                opened_at_limit=_boolean(row["opened_at_limit"]),
                one_price_limit=_boolean(row["one_price_limit"]),
            ) for row in ordered)
            close_precision = sum(row.actual_closed_limit for row in entries) / 20
            touch_precision = sum(row.actual_touched for row in entries) / 20
            days[prediction_day] = LimitUpResearchDay(
                prediction_date=prediction_day,
                target_session=date.fromisoformat(ordered[0]["target_session"]),
                split=ordered[0]["split"], precision_at_20=close_precision,
                touch_precision_at_20=touch_precision, entries=entries,
            )

        history = tuple(LimitUpResearchHistory(
            prediction_date=day.prediction_date, target_session=day.target_session,
            split=day.split, precision_at_20=day.precision_at_20,
            touch_precision_at_20=day.touch_precision_at_20,
        ) for day in sorted(days.values(), key=lambda item: item.prediction_date, reverse=True))

        with (self.root / "metrics.csv").open(newline="", encoding="utf-8") as stream:
            metrics = list(csv.DictReader(stream))
        with (self.root / "monthly_precision.csv").open(
            newline="", encoding="utf-8",
        ) as stream:
            monthly = tuple(LimitUpResearchMonth(
                month=date.fromisoformat(row["month"]), split=row["split"],
                precision_at_20=float(row["precision_at_k"]),
                predictions=int(row["predictions"]), hits=int(row["hits"]),
            ) for row in csv.DictReader(stream)
            if row["model"] == "lightgbm" and row["k"] == "20")

        test_sessions = sum(day.split == "TEST" for day in days.values())
        obs_sessions = sum(day.split == "OBS" for day in days.values())
        summary = LimitUpResearchSummary(
            generated_at=str(manifest.get("generated_at", "")),
            source_sha=source_sha, data_sha=data_sha,
            first_prediction_date=min(days), last_prediction_date=max(days),
            sessions=len(days), test_sessions=test_sessions, obs_sessions=obs_sessions,
            test_precision_at_20=_metric(
                metrics, model="lightgbm", split="TEST", label="actual_close",
            ),
            obs_precision_at_20=_metric(
                metrics, model="lightgbm", split="OBS", label="actual_close",
            ),
            obs_baseline_precision_at_20=_metric(
                metrics, model=str(verdict["best_baseline"]), split="OBS",
                label="actual_close",
            ),
            obs_lift_at_20=float(verdict["obs_lift_at_20"]),
            obs_month_win_rate=float(verdict["obs_month_win_rate"]),
            promoted_to_shadow=True,
        )
        if summary.sessions != 307 or summary.test_sessions != 126 or summary.obs_sessions != 181:
            raise ValueError("research session counts differ from the frozen evidence")
        return _ResearchDataset(
            summary=summary, days=days, history=history, monthly=monthly,
        )

    def _name(self, symbol: str, prediction_date: date) -> str:
        if self.name_lookup is None:
            return ""
        name = self.name_lookup(symbol, prediction_date).strip()
        if not name:
            raise ValueError(f"security name is unavailable for {symbol}")
        return name


class HistoricalSecurityNameLookup:
    """Resolve the name effective on a research day, then fall back to master data."""

    def __init__(self, warehouse_root: Path) -> None:
        self.warehouse_root = Path(warehouse_root)
        self._intervals: dict[str, tuple[tuple[date, date | None, str], ...]] | None = None
        self._current: dict[str, str] = {}
        self._lock = Lock()

    def __call__(self, symbol: str, day: date) -> str:
        self._ensure_loaded()
        matches = [
            name for start, end, name in self._intervals.get(symbol, ())
            if start <= day and (end is None or day <= end)
        ]
        if len(matches) > 1:
            raise ValueError(f"overlapping security-name intervals for {symbol}")
        if matches:
            return matches[0]
        return self._current.get(symbol, "")

    def _ensure_loaded(self) -> None:
        with self._lock:
            if self._intervals is not None:
                return
            from qsys.data.query import WarehouseQuery
            from qsys.data.storage import Warehouse

            query = WarehouseQuery(Warehouse(self.warehouse_root))
            grouped: dict[str, list[tuple[date, date | None, str]]] = defaultdict(list)
            for row in query.security_name_history().iter_rows(named=True):
                grouped[row["symbol"]].append((
                    row["start_date"], row["end_date"], row["name"],
                ))
            master = query.security_master()
            self._current = {
                symbol: name for symbol, name in master.select("symbol", "name").iter_rows()
                if name
            }
            self._intervals = {
                symbol: tuple(sorted(
                    rows, key=lambda item: (item[0], item[1] or date.max, item[2]),
                ))
                for symbol, rows in grouped.items()
            }
