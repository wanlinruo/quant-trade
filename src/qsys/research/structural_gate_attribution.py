"""结构策略候选漏斗与 S6/S7 组件归因。

该模块只读取回测留痕与反事实收益，不导入、写回或优化生产参数。
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from statistics import mean, median
from typing import Iterable

import polars as pl

from qsys.strategy.structural.params import (
    S6_HC_ADVANCE_NOT_LOW,
    S6_HC_TOUCHED_NOT_LOW,
    S6_HIGH_MAIN_BREADTH_HIGH,
    S6_LOW_DISTANCE60_HIGH,
    S6_LOW_HEAT_LOW_THRESHOLD,
    S6_LOW_STOCK_RET10_HIGH,
    S6_LOW_TREND_WEAK_THRESHOLD,
)

LOW_KEYS = (
    "low_history_ok", "low_auction_price_ok", "low_relative_position_ok",
    "low_amount_ok", "low_open_band_ok", "low_auction_volume_ok",
)
HIGH_KEYS = (
    "high_history_ok", "high_auction_price_ok", "high_avg_inc_ok",
    "high_amount_ok", "high_market_cap_ok", "high_circulating_cap_ok",
    "high_auction_volume_rule_ok", "high_open_band_ok", "high_left_pressure_ok",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(row: dict, key: str) -> float | None:
    value = row.get(key)
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number


def _boolean(value: object) -> bool | None:
    """Normalize booleans after heterogeneous trace rows pass through CSV.

    Polars writes the sparse ``gate_allow`` column as strings because early
    funnel rows do not have a gate decision.  Treating ``"false"`` with
    Python's ``bool`` would turn every rejected decision into ``True``.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ValueError(f"invalid serialized boolean: {value!r}")


def _and(a: bool | None, b: bool | None) -> bool | None:
    if a is False or b is False:
        return False
    if a is True and b is True:
        return True
    return None


def _or(a: bool | None, b: bool | None) -> bool | None:
    if a is True or b is True:
        return True
    if a is False and b is False:
        return False
    return None


def gate_diagnostics(row: dict) -> dict[str, object]:
    """把原始因子重放成原子条件、组合象限和预先定义的变体。"""
    tag = row.get("routed_tag") or row.get("strategy_tag")
    out: dict[str, object] = {"strategy_tag": tag}
    variants: dict[str, bool | None] = {}
    if tag == "LOW_MAIN":
        slope, heat = _number(row, "index_ma20_slope_5d"), _number(
            row, "prior_limitup_nextday_return_mean"
        )
        ret10, dist60 = _number(row, "stock_return_10d"), _number(
            row, "distance_to_60d_high"
        )
        slope_bad = None if slope is None else slope > S6_LOW_TREND_WEAK_THRESHOLD
        heat_bad = None if heat is None else heat > S6_LOW_HEAT_LOW_THRESHOLD
        ret_bad = None if ret10 is None else ret10 >= S6_LOW_STOCK_RET10_HIGH
        distance_bad = None if dist60 is None else dist60 >= S6_LOW_DISTANCE60_HIGH
        market_bad, stock_bad = _and(slope_bad, heat_bad), _and(ret_bad, distance_bad)
        risk = _or(market_bad, stock_bad)
        current = True if risk is None else not risk
        out.update(
            slope_bad=slope_bad, heat_bad=heat_bad, return10_bad=ret_bad,
            distance60_bad=distance_bad, market_bad=market_bad,
            stock_bad=stock_bad,
            gate_cell=f"market_{market_bad}|stock_{stock_bad}",
        )
        variants = {
            "current": current,
            "low_market_only": True if market_bad is None else not market_bad,
            "low_stock_only": True if stock_bad is None else not stock_bad,
            "low_both_bad_only": True if market_bad is None or stock_bad is None
            else not (market_bad and stock_bad),
            "no_gate": True,
        }
    elif tag == "HIGH_MAIN":
        breadth = _number(row, "market_median_return_1d")
        favorable = None if breadth is None else breadth > S6_HIGH_MAIN_BREADTH_HIGH
        current = True if favorable is None else favorable
        out.update(breadth_favorable=favorable, gate_cell=f"breadth_{favorable}")
        variants = {"current": current, "no_gate": True}
    elif tag == "HIGH_CHINEXT":
        touched, advance = _number(row, "touched_limit_up_count"), _number(
            row, "market_advance_ratio"
        )
        activity = None if touched is None else touched > S6_HC_TOUCHED_NOT_LOW
        breadth = None if advance is None else advance > S6_HC_ADVANCE_NOT_LOW
        favorable = _and(activity, breadth)
        current = True if favorable is None else favorable
        out.update(
            activity_favorable=activity, breadth_favorable=breadth,
            gate_cell=f"activity_{activity}|breadth_{breadth}",
        )
        variants = {
            "current": current,
            "hc_activity_only": True if activity is None else activity,
            "hc_breadth_only": True if breadth is None else breadth,
            "hc_either_ok": True if activity is None or breadth is None
            else activity or breadth,
            "no_gate": True,
        }
    else:
        out["gate_cell"] = "baseline_no_gate"
        variants = {"current": True, "no_gate": True}
    out["replayed_allow"] = variants["current"]
    out["variants"] = variants
    return out


def enrich_traces(traces: pl.DataFrame) -> list[dict]:
    rows: list[dict] = []
    mismatches: list[str] = []
    for row in traces.iter_rows(named=True):
        item = dict(row)
        for key in ("gate_allow", "gate_known"):
            if item.get(key) is not None:
                item[key] = _boolean(item[key])
        tag = item.get("routed_tag")
        if tag:
            diag = gate_diagnostics(item)
            variants = diag.pop("variants")
            item.update(diag)
            item["variants"] = variants
            observed = item.get("gate_allow")
            if observed is not None and observed != item["replayed_allow"]:
                mismatches.append(f"{item.get('date')}/{item.get('symbol')}")
        rows.append(item)
    if mismatches:
        raise ValueError(
            f"gate replay differs from production decisions: {len(mismatches)}, "
            f"first={mismatches[0]}"
        )
    return rows


def funnel_summary(rows: Iterable[dict]) -> pl.DataFrame:
    counts: dict[tuple, int] = defaultdict(int)
    for row in rows:
        day = row.get("date")
        year = day.year if isinstance(day, date) else str(day)[:4]
        layer = "strict" if row.get("strict_auction_input") is True else "extended"
        counts[(year, layer, row.get("terminal_stage"), row.get("terminal_reason"))] += 1
    return pl.DataFrame([
        {"year": year, "sample_layer": layer, "terminal_stage": stage,
         "terminal_reason": reason, "candidates": count}
        for (year, layer, stage, reason), count in sorted(counts.items())
    ])


def single_relaxations(rows: Iterable[dict]) -> pl.DataFrame:
    """只放宽一个原子入档条件时新增的候选，不做多条件搜索。"""
    out: list[dict] = []
    for row in rows:
        if row.get("routed_tag"):
            continue
        for branch, keys in (("LOW", LOW_KEYS), ("HIGH", HIGH_KEYS)):
            # LOW 命中后即使附加竞价量过滤失败也不会回落到 HIGH；因此这种行
            # 放宽 HIGH 条件并不能改变真实路由。
            if branch == "HIGH" and row.get("low_eligible") is True:
                continue
            if branch == "LOW" and str(row.get("symbol")).startswith(("300", "301")):
                keys = tuple(key for key in keys if key != "low_auction_volume_ok")
            values = {key: row.get(key) for key in keys}
            if branch == "LOW" and row.get("strict_auction_input") is not True:
                # 扩展样本沿用回测的缺竞价量放行近似口径。
                values["low_auction_volume_ok"] = True
            failed = [key for key, value in values.items() if value is False]
            unknown = [key for key, value in values.items() if value is None]
            if len(failed) == 1 and not unknown:
                symbol = str(row.get("symbol"))
                board = "CHINEXT" if symbol.startswith(("300", "301")) else "MAIN"
                out.append({
                    "date": row.get("date"), "symbol": symbol,
                    "strategy_tag": f"{branch}_{board}",
                    "relaxed_condition": failed[0],
                    "strict_auction_input": row.get("strict_auction_input") is True,
                    "sample_layer": (
                        "strict" if row.get("strict_auction_input") is True else "extended"
                    ),
                })
    if not out:
        return pl.DataFrame(schema={
            "date": pl.Date, "symbol": pl.Utf8, "strategy_tag": pl.Utf8,
            "relaxed_condition": pl.Utf8, "strict_auction_input": pl.Boolean,
            "sample_layer": pl.Utf8,
        })
    return pl.DataFrame(out).unique().sort("date", "symbol", "strategy_tag")


def _bootstrap_ci(rows: list[dict], key: str, seed: int = 20261014) -> tuple[float, float]:
    by_day: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = row.get(key)
        if value is not None:
            by_day[str(row.get("date"))].append(float(value))
    days = sorted(by_day)
    if len(days) < 2:
        return float("nan"), float("nan")
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(2000):
        values: list[float] = []
        for _ in days:
            values.extend(by_day[rng.choice(days)])
        estimates.append(mean(values))
    estimates.sort()
    return estimates[int(0.025 * len(estimates))], estimates[int(0.975 * len(estimates))]


def _outcome_layer(row: dict) -> str:
    # 严格/扩展只描述竞价输入是否真实完整；分钟结果质量是另一维，不能把
    # “严格竞价 + 缺分钟”偷换成扩展竞价样本。
    return "strict" if row.get("strict_auction_input") is True else "extended"


def _periods(row: dict) -> tuple[str, str]:
    day = row.get("date")
    return "ALL", str(day.year if isinstance(day, date) else str(day)[:4])


def variant_summary(rows: list[dict]) -> pl.DataFrame:
    groups: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("net_return_pct") is None or row.get("outcome_quality") == "no_intraday":
            continue
        diag = gate_diagnostics(row)
        for variant, allowed in diag["variants"].items():
            if allowed:
                for period in _periods(row):
                    groups[(
                        str(diag["strategy_tag"]), variant,
                        _outcome_layer(row), period,
                    )].append(row)
    output: list[dict] = []
    for (tag, variant, layer, period), values in sorted(groups.items()):
        returns = [float(row["net_return_pct"]) for row in values]
        low, high = _bootstrap_ci(values, "net_return_pct")
        n = len(returns)
        strength = "insufficient" if n < 30 else "exploratory" if n < 100 else "directional"
        output.append({
            "strategy_tag": tag, "variant": variant,
            "sample_layer": layer, "period": period, "n": n,
            "mean_net_return_pct": mean(returns),
            "median_net_return_pct": median(returns),
            "win_rate": sum(value > 0 for value in returns) / n,
            "mean_ci_low": low, "mean_ci_high": high,
            "evidence_strength": strength,
        })
    return pl.DataFrame(output) if output else pl.DataFrame(schema={
        "strategy_tag": pl.Utf8, "variant": pl.Utf8,
        "sample_layer": pl.Utf8, "period": pl.Utf8, "n": pl.Int64,
    })


def gate_cells(rows: list[dict]) -> pl.DataFrame:
    groups: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("net_return_pct") is None or row.get("outcome_quality") == "no_intraday":
            continue
        diag = gate_diagnostics(row)
        for period in _periods(row):
            groups[(
                str(diag["strategy_tag"]), str(diag["gate_cell"]),
                _outcome_layer(row), period,
            )].append(row)
    output = []
    for (tag, cell, layer, period), group in sorted(groups.items()):
        values = [float(row["net_return_pct"]) for row in group]
        low, high = _bootstrap_ci(group, "net_return_pct")
        n = len(values)
        output.append({
            "strategy_tag": tag, "gate_cell": cell, "sample_layer": layer,
            "period": period, "n": n, "mean_net_return_pct": mean(values),
            "median_net_return_pct": median(values),
            "win_rate": sum(value > 0 for value in values) / n,
            "mean_ci_low": low, "mean_ci_high": high,
            "evidence_strength": (
                "insufficient" if n < 30 else "exploratory" if n < 100
                else "directional"
            ),
        })
    return pl.DataFrame(output) if output else pl.DataFrame(schema={
        "strategy_tag": pl.Utf8, "gate_cell": pl.Utf8,
        "sample_layer": pl.Utf8, "period": pl.Utf8, "n": pl.Int64,
    })


def relaxation_summary(rows: list[dict]) -> pl.DataFrame:
    """Summarize one-condition entry relaxations without mixing sample layers."""
    groups: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("net_return_pct") is None or row.get("outcome_quality") == "no_intraday":
            continue
        layer = _outcome_layer(row)
        for period in _periods(row):
            groups[(
                str(row.get("strategy_tag")), str(row.get("relaxed_condition")),
                layer, period,
            )].append(row)
    output = []
    for (tag, condition, layer, period), group in sorted(groups.items()):
        values = [float(row["net_return_pct"]) for row in group]
        low, high = _bootstrap_ci(group, "net_return_pct")
        n = len(values)
        output.append({
            "strategy_tag": tag, "relaxed_condition": condition,
            "sample_layer": layer, "period": period, "n": n,
            "mean_net_return_pct": mean(values),
            "median_net_return_pct": median(values),
            "win_rate": sum(value > 0 for value in values) / n,
            "mean_ci_low": low, "mean_ci_high": high,
            "evidence_strength": (
                "insufficient" if n < 30 else "exploratory" if n < 100
                else "directional"
            ),
        })
    return pl.DataFrame(output) if output else pl.DataFrame(schema={
        "strategy_tag": pl.Utf8, "relaxed_condition": pl.Utf8,
        "sample_layer": pl.Utf8, "period": pl.Utf8, "n": pl.Int64,
    })


def write_report(
    out: Path, *, trace_path: Path, rows: list[dict], outcomes: list[dict],
    relaxation_outcomes: list[dict] | None = None,
) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    funnel = funnel_summary(rows)
    coverage = pl.DataFrame([
        {"sample_layer": layer, "candidates": sum(
            1 for row in rows
            if ("strict" if row.get("strict_auction_input") is True else "extended") == layer
        )}
        for layer in ("strict", "extended")
    ])
    relaxations = single_relaxations(rows)
    cells = gate_cells(outcomes)
    variants = variant_summary(outcomes)
    relaxation_metrics = relaxation_summary(relaxation_outcomes or [])
    funnel.write_csv(out / "funnel_summary.csv")
    coverage.write_csv(out / "coverage.csv")
    relaxations.write_csv(out / "single_relaxation_candidates.csv")
    cells.write_csv(out / "gate_cells.csv")
    variants.write_csv(out / "variant_metrics.csv")
    relaxation_metrics.write_csv(out / "single_relaxation_metrics.csv")
    if relaxation_outcomes:
        pl.DataFrame(relaxation_outcomes).write_parquet(
            out / "single_relaxation_outcomes.parquet", compression="zstd"
        )
    pl.DataFrame(outcomes).write_parquet(
        out / "counterfactual_trades.parquet", compression="zstd"
    )
    dates = [row["date"] for row in rows if row.get("date") is not None]
    report = {
        "schema_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "safety": {"research_only": True, "production_parameters_modified": False},
        "sample": {
            "start": str(min(dates)) if dates else None,
            "end": str(max(dates)) if dates else None,
            "candidate_rows": len(rows),
            "routed_rows": sum(row.get("gate_allow") is not None for row in rows),
            "strict_rows": sum(row.get("strict_auction_input") is True for row in rows),
        },
        "input": {"candidate_funnel": str(trace_path), "sha256": sha256(trace_path)},
        "artifacts": [
            "candidate_funnel.parquet", "funnel_summary.csv", "coverage.csv",
            "single_relaxation_candidates.csv", "single_relaxation_metrics.csv",
            "gate_cells.csv", "variant_metrics.csv", "counterfactual_trades.parquet",
            *(["single_relaxation_outcomes.parquet"]
              if relaxation_outcomes else []),
        ],
    }
    (out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out / "report.md").write_text(
        _markdown(report, funnel, cells, variants, relaxation_metrics), encoding="utf-8"
    )
    return report


def _markdown(report: dict, funnel: pl.DataFrame, cells: pl.DataFrame,
              variants: pl.DataFrame, relaxation_metrics: pl.DataFrame) -> str:
    sample = report["sample"]
    return (
        "# 结构策略选股漏斗与门禁组件归因\n\n"
        "> 只读研究；未修改生产参数或交易配置。\n\n"
        f"区间：{sample['start']}..{sample['end']}；候选 {sample['candidate_rows']} 条，"
        f"入档 {sample['routed_rows']} 条，严格竞价输入 {sample['strict_rows']} 条。\n\n"
        "## 漏斗\n\n```csv\n" + funnel.write_csv().strip() + "\n```\n\n"
        "## 门禁象限\n\n```csv\n" + cells.write_csv().strip() + "\n```\n\n"
        "## 规则变体\n\n```csv\n" + variants.write_csv().strip() + "\n```\n"
        "\n## 单项入档放宽\n\n```csv\n"
        + relaxation_metrics.write_csv().strip() + "\n```\n"
    )
