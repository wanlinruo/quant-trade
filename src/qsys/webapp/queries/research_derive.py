"""新前端研究侧的纯派生（#662，设计文档 §8.10、§8.11；R3 起位于新后端查询层）。

只做确定性计算，不读文件、不补值：基准缺某日时该日基准与超额为 None；
净值为空的日子策略收益与回撤为 None，回撤段跳过该日；首日净值为空时没有收益基数；
回撤段从前高到谷底再到收复前高，未收复时 recovery_date 为 None。
推进位置与“使用中”的口径为设计者决定（见计划 08c）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from qsys.paper.store import ACTIVE

TOP_EPISODES = 5


def drawdown_episodes(nav: Sequence[tuple[str, float | None]], top: int = TOP_EPISODES) -> list[dict]:
    episodes: list[dict] = []
    nav = [(day, value) for day, value in nav if value is not None]
    if not nav:
        return episodes
    peak_date, peak = nav[0]
    current: dict | None = None
    for day, value in nav[1:]:
        if value >= peak:
            if current is not None:
                current["recovery_date"] = day
                episodes.append(current)
                current = None
            peak_date, peak = day, value
            continue
        depth = value / peak - 1.0 if peak else 0.0
        if current is None:
            current = {"peak_date": peak_date, "trough_date": day, "recovery_date": None,
                       "depth": depth}
        elif depth < current["depth"]:
            current["trough_date"], current["depth"] = day, depth
    if current is not None:
        episodes.append(current)
    return sorted(episodes, key=lambda e: e["depth"])[:top]


def backtest_warnings(*, rejects: int, has_benchmark: bool, missing_benchmark_days: int,
                      missing_daily_returns: int, cost_model: str, status: str | None) -> list[str]:
    out: list[str] = []
    if rejects > 0:
        out.append(f"下单被拒 {rejects} 笔（详见 rejects.csv）")
    if not has_benchmark:
        out.append("没有基准净值，超额收益不可用")
    elif missing_benchmark_days > 0:
        out.append(f"{missing_benchmark_days} 个交易日缺基准，图上断开")
    if missing_daily_returns > 0:
        out.append(f"{missing_daily_returns} 日缺日收益")
    if "聚宽兼容" in (cost_model or ""):
        out.append("按聚宽兼容费用计算，不代表实盘档位")
    if status == "interrupted":
        out.append("回测被中断，结果不完整")
    return out


def aligned_series(nav: Sequence[Mapping[str, Any]], benchmark: Mapping[str, float]) -> list[dict]:
    if not nav:
        return []
    first = nav[0]["total_value"]
    initial = None if first is None else float(first)
    bench_initial = next(iter(benchmark.values()), None) if benchmark else None
    peak: float | None = None
    rows = []
    for row in nav:
        day, raw = str(row["date"]), row["total_value"]
        value = None if raw is None else float(raw)
        if value is not None:
            peak = value if peak is None else max(peak, value)
        strategy = value / initial - 1.0 if value is not None and initial else None
        b = benchmark.get(day)
        bench = b / bench_initial - 1.0 if b is not None and bench_initial else None
        rows.append({
            "date": day, "strategy": strategy, "benchmark": bench,
            "excess": strategy - bench if strategy is not None and bench is not None else None,
            "drawdown": value / peak - 1.0 if value is not None and peak else None,
        })
    return rows


def _active(accounts: Iterable) -> list:
    return [a for a in accounts if a.status in ACTIVE]


def strategy_stage(strategy_id: str, *, succeeded_strategy_ids: set[str], active_accounts: Iterable,
                   lineage, core_label: str) -> dict:
    if lineage is not None and getattr(lineage, "strategy_id", "") == strategy_id:
        return {"stage": "live", "label": core_label, "detail": f"{core_label}承袭的模拟账户版本"}
    names = [a.name for a in _active(active_accounts) if a.strategy_id == strategy_id]
    if names:
        return {"stage": "paper", "label": "模拟", "detail": "、".join(names)}
    if strategy_id in succeeded_strategy_ids:
        return {"stage": "backtest", "label": "回测", "detail": ""}
    return {"stage": "draft", "label": "草稿", "detail": ""}


def in_use_versions(accounts: Iterable, lineage, *, core_label: str) -> dict[str, list[str]]:
    used: dict[str, list[str]] = {}
    if lineage is not None and getattr(lineage, "version_id", ""):
        used.setdefault(lineage.version_id, []).append(core_label)
    for a in _active(accounts):
        used.setdefault(a.version_id, []).append(f"模拟账户 {a.name}")
    return used
