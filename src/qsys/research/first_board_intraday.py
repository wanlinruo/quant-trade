"""Issue #230 first-minute gate for the delayed 09:31 execution variant.

The overnight model is frozen.  This module only builds information that is
observable after the 09:30--09:31 continuous-auction minute, trains a small
VALID-only gate, and freezes daily selections before TEST/OBS trading runs.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterable, Mapping, Sequence

import numpy as np
import polars as pl

from qsys.core.trading import OrderType, Side
from qsys.core.types import Symbol
from qsys.strategy.base import Strategy


MODEL = "lightgbm_price"
TOP_K = 20
MAX_POSITIONS = 3
DECISION_AT = time(9, 31)
ENTRY_BAR_AT = time(9, 32)
THRESHOLD_QUANTILES = (0.80, 0.90, 0.95)
GATE_FEATURES = (
    "p_close", "rank_fraction", "open_gap", "first_minute_return",
    "first_minute_range", "first_amount_ratio", "first_volume_ratio",
    "close_location", "pullback_from_high", "distance_to_limit",
)
EXIT_TIMES = {"11:25": time(11, 25), "14:25": time(14, 25)}
TWO_STAGE_EXIT = "two_stage_limit_hold"
SAFE_SELECTION_COLUMNS = (
    "target_session", "symbol", "split", "policy", "selection_rank",
    "decision_price", "limit_up", "gate_score",
)


@dataclass(frozen=True, slots=True)
class VisibleMinutePair:
    observation: Mapping[str, object] | None
    execution: Mapping[str, object] | None


@dataclass(frozen=True, slots=True)
class FrozenSelection:
    target_session: date
    symbol: str
    selection_rank: int
    decision_price: float
    limit_up: float
    gate_score: float


class FrozenSelectionBook:
    """Read only the frozen decision columns; research outcomes stay hidden."""

    def __init__(self, path, *, policy: str, split: str) -> None:
        self.path = path
        self.policy = policy
        self.split = split
        self._by_day: dict[date, tuple[FrozenSelection, ...]] | None = None

    def for_target(self, day: date) -> tuple[FrozenSelection, ...]:
        if self._by_day is None:
            self._by_day = self._load()
        return self._by_day.get(day, ())

    def _load(self) -> dict[date, tuple[FrozenSelection, ...]]:
        lazy = pl.scan_parquet(self.path)
        missing = set(SAFE_SELECTION_COLUMNS) - set(lazy.collect_schema().names())
        if missing:
            raise ValueError(f"selection artifact misses columns: {sorted(missing)}")
        safe = (
            lazy.filter(
                (pl.col("policy") == self.policy) & (pl.col("split") == self.split)
            )
            .select(*SAFE_SELECTION_COLUMNS)
            .sort(["target_session", "selection_rank"])
            .collect()
        )
        out: dict[date, tuple[FrozenSelection, ...]] = {}
        for key, group in safe.group_by("target_session", maintain_order=True):
            ranks = group["selection_rank"].to_list()
            if ranks != list(range(1, len(ranks) + 1)) or len(ranks) > MAX_POSITIONS:
                raise ValueError(f"invalid frozen selection ranks for {key[0]}: {ranks}")
            out[key[0]] = tuple(
                FrozenSelection(
                    target_session=row["target_session"], symbol=row["symbol"],
                    selection_rank=int(row["selection_rank"]),
                    decision_price=float(row["decision_price"]),
                    limit_up=float(row["limit_up"]),
                    gate_score=float(row["gate_score"]),
                )
                for row in group.iter_rows(named=True)
            )
        return out


class FirstMinuteResearchStrategy(Strategy):
    """Research-only executor for already frozen 09:31 selections."""

    def __init__(
        self, book: FrozenSelectionBook, *, position_weight: float = 0.10,
        entry_price_buffer: float = 0.03, exit_policy: str = "next_open",
        decision_at: time = DECISION_AT,
        strategy_tag: str = "AI_FIRST_BOARD_0931",
    ) -> None:
        if exit_policy not in {"next_open", TWO_STAGE_EXIT, *EXIT_TIMES}:
            raise ValueError(f"unknown exit policy: {exit_policy}")
        self._selection = book
        self.position_weight = position_weight
        self.entry_price_buffer = entry_price_buffer
        self.exit_policy = exit_policy
        self.decision_at = decision_at
        self.strategy_tag = strategy_tag

    def on_init(self, ctx) -> None:
        # AUCTION is deliberately repurposed as a once-per-session decision hook.
        # The entry rule prices these orders from the strictly later 09:32 bar.
        ctx.schedule.auction(self.decision_at)
        if self.exit_policy in EXIT_TIMES:
            ctx.schedule.checkpoint(EXIT_TIMES[self.exit_policy])
        elif self.exit_policy == TWO_STAGE_EXIT:
            ctx.schedule.checkpoint(EXIT_TIMES["11:25"], EXIT_TIMES["14:25"])

    def on_session_start(self, ctx) -> None:
        if self.exit_policy == "next_open":
            self._exit_available(ctx)

    def on_auction(self, ctx) -> None:
        selected = self._selection.for_target(ctx.now.date())
        rejected = Counter()
        routed = 0
        held = set(ctx.portfolio.symbols)
        for signal in selected:
            symbol = Symbol.parse(signal.symbol)
            if symbol in held:
                rejected["already held"] += 1
                continue
            protect = min(
                round(signal.decision_price * (1 + self.entry_price_buffer), 2),
                signal.limit_up,
            )
            budget = ctx.portfolio.total_value * self.position_weight
            qty = int(budget / protect / 100) * 100
            if qty <= 0:
                rejected["position budget below one lot"] += 1
                continue
            ctx.state.setdefault("tags", {})[signal.symbol] = self.strategy_tag
            ctx.order.submit(symbol, Side.BUY, qty, OrderType.LIMIT, protect)
            routed += 1
        ctx.report(
            candidates=len(selected), routed=routed, passed=routed,
            reasons=dict(rejected),
        )

    def on_checkpoint(self, ctx, at: time) -> None:
        if EXIT_TIMES.get(self.exit_policy) == at:
            self._exit_available(ctx)
        elif self.exit_policy == TWO_STAGE_EXIT:
            self._two_stage_exit(ctx, at)

    @staticmethod
    def _two_stage_exit(ctx, at: time) -> None:
        held = sorted(
            (
                symbol for symbol in ctx.portfolio.symbols
                if ctx.portfolio.position(symbol).available_qty > 0
            ),
            key=str,
        )
        if not held:
            return

        today = ctx.now.date()
        keys = [str(symbol) for symbol in held]
        quotes = ctx.market.intraday(keys, today, at)
        limits = ctx.market.limit_up_price(keys, today)
        previous_closes: dict[str, float] = {}
        if at == EXIT_TIMES["11:25"]:
            history = ctx.data.get(keys, today - timedelta(days=14), today)
            if history.height:
                previous_closes = {
                    row["symbol"]: float(row["close"])
                    for row in (
                        history.sort(["symbol", "dt"])
                        .group_by("symbol", maintain_order=True)
                        .agg(pl.col("close").last().alias("close"))
                        .iter_rows(named=True)
                    )
                }

        sold = 0
        missing = 0
        for symbol in held:
            key = str(symbol)
            quote = quotes.get(key)
            limit_up = limits.get(key)
            if quote is None or limit_up is None:
                missing += 1
                continue
            is_limit_up = quote.price >= limit_up
            should_exit = (
                at == EXIT_TIMES["14:25"] and not is_limit_up
            ) or (
                at == EXIT_TIMES["11:25"]
                and key in previous_closes
                and quote.price > previous_closes[key]
                and not is_limit_up
            )
            if should_exit:
                qty = ctx.portfolio.position(symbol).available_qty
                ctx.order.submit(symbol, Side.SELL, qty)
                sold += 1
            elif at == EXIT_TIMES["11:25"] and key not in previous_closes:
                missing += 1
        ctx.report(
            note=(
                f"{at:%H:%M} two-stage exit: held={len(held)}, "
                f"sold={sold}, missing={missing}"
            )
        )

    @staticmethod
    def _exit_available(ctx) -> None:
        for symbol in sorted(ctx.portfolio.symbols, key=str):
            qty = ctx.portfolio.position(symbol).available_qty
            if qty > 0:
                ctx.order.submit(symbol, Side.SELL, qty)


def visible_minute_pair(
    rows: Iterable[Mapping[str, object]], day: date,
    *, decision_at: time = DECISION_AT, entry_bar_at: time = ENTRY_BAR_AT,
) -> VisibleMinutePair:
    """Choose the first continuous minute and the strictly subsequent bar.

    Canonical sources have different source labels: paid data stores the first
    continuous minute at ``ts=09:30, visible_at=09:31`` while QMT stores it at
    ``ts=09:31, visible_at=09:31``.  ``visible_at`` is therefore authoritative.
    """
    opened = datetime.combine(day, time(9, 30))
    decision = datetime.combine(day, decision_at)
    entry_end = datetime.combine(day, entry_bar_at)
    values = list(rows)
    observed = [
        row for row in values
        if opened < row["visible_at"] <= decision
    ]
    executable = [
        row for row in values
        if decision < row["visible_at"] <= entry_end
    ]
    observation = max(
        observed, key=lambda row: (row["visible_at"], row["ts"]), default=None,
    )
    execution = min(
        executable, key=lambda row: (row["visible_at"], row["ts"]), default=None,
    )
    return VisibleMinutePair(observation, execution)


def load_candidates(path, *, model: str = MODEL) -> pl.DataFrame:
    lazy = pl.scan_parquet(path)
    required = {
        "prediction_date", "target_session", "symbol", "split", "model",
        "rank", "p_close", "actual_touch", "actual_close",
    }
    missing = required - set(lazy.collect_schema().names())
    if missing:
        raise ValueError(f"ranking artifact misses columns: {sorted(missing)}")
    frame = (
        lazy.filter(
            (pl.col("model") == model)
            & (pl.col("rank") <= TOP_K)
            & pl.col("split").is_in(["VALID", "TEST", "OBS"])
        )
        .select(*sorted(required - {"model"}), pl.lit(model).alias("model"))
        .sort(["target_session", "rank"])
        .collect()
    )
    if frame.is_empty():
        raise ValueError(f"no {model} Top-{TOP_K} candidates")
    duplicate = frame.group_by("target_session", "symbol").len().filter(
        pl.col("len") != 1
    )
    if duplicate.height:
        raise ValueError("ranking contains duplicate target/symbol rows")
    return frame


def build_feature_table(query, candidates: pl.DataFrame) -> pl.DataFrame:
    """Attach daily truth and two PIT minute bars to frozen candidates."""
    symbols = sorted(candidates["symbol"].unique().to_list())
    start = min(candidates["prediction_date"].min(), candidates["target_session"].min())
    end = candidates["target_session"].max()
    daily = query.bar_daily(
        symbols, start, end,
        columns=("symbol", "dt", "pre_close", "open", "close", "volume", "amount"),
    )
    daily_by_key = {
        (row["symbol"], row["dt"]): row for row in daily.iter_rows(named=True)
    }
    limits = query.price_limit_daily(symbols, candidates["target_session"].min(), end)
    limit_by_key = {
        (row["symbol"], row["dt"]): row for row in limits.iter_rows(named=True)
    }
    statuses = query.security_status_daily(
        symbols, candidates["target_session"].min(), end,
    )
    status_by_key = {
        (row["symbol"], row["dt"]): row for row in statuses.iter_rows(named=True)
    }

    output: list[dict[str, object]] = []
    for group in candidates.partition_by("target_session", maintain_order=True):
        day = group["target_session"][0]
        wanted = group["symbol"].to_list()
        bars = query.bar_minute_1m(
            wanted,
            datetime.combine(day, time(9, 30)),
            datetime.combine(day, ENTRY_BAR_AT),
            columns=(
                "symbol", "ts", "open", "high", "low", "close", "volume",
                "amount", "source", "visible_at",
            ),
        )
        minute_by_symbol: dict[str, list[dict[str, object]]] = {}
        for row in bars.iter_rows(named=True):
            minute_by_symbol.setdefault(row["symbol"], []).append(row)

        for candidate in group.iter_rows(named=True):
            symbol = candidate["symbol"]
            prediction_day = candidate["prediction_date"]
            previous = daily_by_key.get((symbol, prediction_day))
            target = daily_by_key.get((symbol, day))
            limit = limit_by_key.get((symbol, day))
            status = status_by_key.get((symbol, day))
            pair = visible_minute_pair(minute_by_symbol.get(symbol, ()), day)
            reason = _invalid_reason(previous, target, limit, status, pair)
            row = dict(candidate)
            row.update(_minute_values(pair))
            row["invalid_reason"] = reason
            if reason:
                row.update({name: None for name in GATE_FEATURES if name not in row})
                row["decision_price"] = None
                row["limit_up"] = None
            else:
                row.update(_features(candidate, previous, target, limit, pair))
            output.append(row)
    return pl.DataFrame(output).sort(["target_session", "rank"])


def _invalid_reason(previous, target, limit, status, pair: VisibleMinutePair) -> str:
    if previous is None or target is None or limit is None:
        return "missing daily or price-limit truth"
    if status is None or any(status.get(name) is None for name in (
        "is_st", "is_suspended", "is_delisting",
    )):
        return "missing security status"
    if status["is_st"] or status["is_suspended"] or status["is_delisting"]:
        return "ineligible security status"
    if pair.observation is None:
        return "missing first visible minute"
    if pair.execution is None:
        return "missing next execution minute"
    required = (
        previous.get("close"), previous.get("volume"), previous.get("amount"),
        target.get("open"), limit.get("limit_up"),
        *[pair.observation.get(name) for name in ("open", "high", "low", "close", "volume", "amount")],
        *[pair.execution.get(name) for name in ("open", "low")],
    )
    if any(value is None or not np.isfinite(float(value)) for value in required):
        return "non-finite market input"
    if min(float(previous["close"]), float(previous["volume"]), float(previous["amount"]),
           float(target["open"]), float(limit["limit_up"])) <= 0:
        return "non-positive daily input"
    obs = pair.observation
    if min(float(obs[name]) for name in ("open", "high", "low", "close")) <= 0:
        return "non-positive first minute"
    # A stock already sealed at the decision point cannot be assigned an
    # optimistic queue fill merely because a later OHLC bar exists.
    if float(obs["close"]) >= float(limit["limit_up"]) - 0.005:
        return "sealed at limit at decision"
    return ""


def _minute_values(pair: VisibleMinutePair) -> dict[str, object]:
    out: dict[str, object] = {}
    for prefix, bar in (("observation", pair.observation), ("execution", pair.execution)):
        for name in ("ts", "visible_at", "source", "open", "high", "low", "close",
                     "volume", "amount"):
            out[f"{prefix}_{name}"] = bar.get(name) if bar is not None else None
    return out


def _features(candidate, previous, target, limit, pair: VisibleMinutePair) -> dict[str, float]:
    obs = pair.observation
    assert obs is not None
    target_open = float(target["open"])
    high, low, close = (float(obs[name]) for name in ("high", "low", "close"))
    spread = high - low
    return {
        "p_close": float(candidate["p_close"]),
        "rank_fraction": float(candidate["rank"]) / TOP_K,
        "open_gap": target_open / float(previous["close"]) - 1.0,
        "first_minute_return": close / target_open - 1.0,
        "first_minute_range": spread / target_open,
        "first_amount_ratio": float(obs["amount"]) / float(previous["amount"]),
        "first_volume_ratio": float(obs["volume"]) / float(previous["volume"]),
        "close_location": (close - low) / spread if spread > 0 else 0.5,
        "pullback_from_high": close / high - 1.0,
        "distance_to_limit": float(limit["limit_up"]) / close - 1.0,
        "decision_price": close,
        "limit_up": float(limit["limit_up"]),
    }


def fit_frozen_gate(features: pl.DataFrame):
    """Fit on early VALID, choose one bounded threshold on late VALID."""
    eligible = features.filter(
        (pl.col("split") == "VALID") & (pl.col("invalid_reason") == "")
    )
    days = sorted(eligible["target_session"].unique().to_list())
    if len(days) < 20:
        raise ValueError("VALID minute coverage is too short for a frozen gate")
    cut = max(1, min(len(days) - 1, int(len(days) * 0.70)))
    fit_days, select_days = set(days[:cut]), set(days[cut:])
    fit = eligible.filter(pl.col("target_session").is_in(fit_days))
    select = eligible.filter(pl.col("target_session").is_in(select_days))

    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(
            C=0.10, class_weight="balanced", max_iter=500, random_state=230,
        ),
    )
    model.fit(fit.select(GATE_FEATURES).to_numpy(), fit["actual_close"].to_numpy())
    scored = score_gate(features, model)
    fit_scores = scored.filter(pl.col("target_session").is_in(fit_days))["gate_score"]
    candidates = [float(fit_scores.quantile(q, interpolation="linear"))
                  for q in THRESHOLD_QUANTILES]
    rows: list[dict[str, object]] = []
    for quantile, threshold in zip(THRESHOLD_QUANTILES, candidates):
        selected = daily_select(
            scored.filter(pl.col("target_session").is_in(select_days)),
            policy="gate_threshold", threshold=threshold,
        )
        hits = int(selected["actual_close"].sum()) if selected.height else 0
        rows.append({
            "quantile": quantile, "threshold": threshold,
            "selected": selected.height, "hits": hits,
            "precision": hits / selected.height if selected.height else 0.0,
            "sessions": selected["target_session"].n_unique() if selected.height else 0,
        })
    viable = [row for row in rows if int(row["selected"]) >= 20]
    if not viable:
        raise ValueError("no VALID threshold has at least 20 selected candidates")
    chosen = max(viable, key=lambda row: (row["precision"], row["selected"]))
    metadata = {
        "fit_first": str(days[0]), "fit_last": str(days[cut - 1]),
        "select_first": str(days[cut]), "select_last": str(days[-1]),
        "fit_sessions": len(fit_days), "select_sessions": len(select_days),
        "threshold_candidates": rows, "chosen_threshold": chosen["threshold"],
        "chosen_quantile": chosen["quantile"],
    }
    return model, scored, metadata


def score_gate(features: pl.DataFrame, model) -> pl.DataFrame:
    eligible = features.filter(pl.col("invalid_reason") == "")
    scores = model.predict_proba(eligible.select(GATE_FEATURES).to_numpy())[:, 1]
    scored = eligible.with_columns(pl.Series("gate_score", scores))
    return features.join(
        scored.select("target_session", "symbol", "gate_score"),
        on=["target_session", "symbol"], how="left",
    )


def daily_select(
    scored: pl.DataFrame, *, policy: str, threshold: float | None = None,
) -> pl.DataFrame:
    eligible = scored.filter(
        (pl.col("invalid_reason") == "") & pl.col("gate_score").is_not_null()
    )
    if policy == "rank_top1":
        ordered, limit = eligible.sort(["target_session", "rank"]), 1
    elif policy == "rank_top3":
        ordered, limit = eligible.sort(["target_session", "rank"]), 3
    elif policy == "gate_top3":
        ordered, limit = eligible.sort(
            ["target_session", "gate_score", "rank"],
            descending=[False, True, False],
        ), 3
    elif policy == "gate_threshold":
        if threshold is None:
            raise ValueError("gate_threshold requires a threshold")
        ordered, limit = eligible.filter(pl.col("gate_score") >= threshold).sort(
            ["target_session", "gate_score", "rank"],
            descending=[False, True, False],
        ), MAX_POSITIONS
    else:
        raise ValueError(f"unknown selection policy: {policy}")
    return (
        ordered.with_columns(
            pl.int_range(pl.len()).over("target_session").alias("selection_rank") + 1,
            pl.lit(policy).alias("policy"),
        )
        .filter(pl.col("selection_rank") <= limit)
    )


def freeze_selections(scored: pl.DataFrame, threshold: float) -> pl.DataFrame:
    frames = [
        daily_select(scored, policy=policy, threshold=threshold)
        for policy in ("rank_top1", "rank_top3", "gate_top3", "gate_threshold")
    ]
    return pl.concat(frames, how="vertical_relaxed").sort(
        ["policy", "target_session", "selection_rank"]
    )
