"""回测运行浏览：把 `var/runs/` 下的落盘目录变成可看、可下载的东西。

**为什么需要这个**：对账是双向的。聚宽那边每天落一整套研究文件、能直接
下载；我们这边此前只有两个散落在服务器 `var/` 里的 CSV，别人看不到也
拿不到——等于把自己这一半变成了黑盒。

只读，且**只读 runs 根目录之下的文件**：路径逃逸检查放在
`file_bytes()` 里，Web 层不做拼接。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import polars as pl
import structlog

log = structlog.get_logger(__name__)

# 允许下载的扩展名。别的一律不给——runs 目录将来可能混进日志之类。
_DOWNLOADABLE = {".csv", ".json", ".txt", ".zip", ".py", ".log"}

_MEDIA = {
    ".csv": "text/csv; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".zip": "application/zip",
    ".py": "text/x-python; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
}


@dataclass(frozen=True, slots=True)
class RunFile:
    name: str
    size: int


@dataclass(frozen=True, slots=True)
class RunSummary:
    name: str
    run_id: str
    strategy: str
    start: str
    end: str
    initial_value: float
    total_value: float
    cumulative_return: float | None
    buys: int
    sells: int
    rejects: int
    closed_lots: int
    gate_allowed: int
    gate_rejected: int
    cost_model: str
    generated_at: str
    # 策略参数与竞价入场口径：做回测对比时要一眼看到用的是什么设置
    params: Mapping[str, object] = field(default_factory=dict)
    entry_fill: str = ""
    # 发起方式：web＝网页排队，cli＝命令行研究。缺失（老运行）按 cli 处理——
    # 网页跑的都有排队任务，能从任务侧认出来。
    source: str = "cli"

    @property
    def return_pct(self) -> float | None:
        return None if self.cumulative_return is None else self.cumulative_return * 100

    @property
    def param_rows(self) -> list[tuple[str, str]]:
        return param_rows(self.params)


class BacktestService:
    def __init__(self, runs_dir: Path) -> None:
        self._root = Path(runs_dir)

    @property
    def root(self) -> Path:
        return self._root

    def runs(self) -> list[RunSummary]:
        """按生成时间倒序列出所有运行。坏目录跳过，不让一个坏目录挡住全部。"""
        if not self._root.is_dir():
            return []
        out: list[RunSummary] = []
        for path in sorted(self._root.iterdir()):
            if not path.is_dir():
                continue
            try:
                summary = self._summary(path)
            except Exception as exc:  # 半截目录不该让整页打不开
                log.warning("backtest.bad_run_dir", path=str(path), error=str(exc))
                continue
            if summary is not None:
                out.append(summary)
        return sorted(out, key=lambda r: r.generated_at, reverse=True)

    def run(self, name: str) -> RunSummary | None:
        path = self._safe_dir(name)
        if path is None:
            return None
        try:
            return self._summary(path)
        except Exception:
            return None

    def files(self, name: str) -> list[RunFile]:
        path = self._safe_dir(name)
        if path is None:
            return []
        return [
            RunFile(p.name, p.stat().st_size)
            for p in sorted(path.iterdir())
            if p.is_file() and p.suffix.lower() in _DOWNLOADABLE
        ]

    def metrics(self, name: str) -> list[tuple[str, float]]:
        frame = self._read(name, "metrics.csv")
        if frame is None or not {"metric", "value"} <= set(frame.columns):
            return []
        out: list[tuple[str, float]] = []
        for key, value in zip(
            frame["metric"].to_list(), frame["value"].to_list(), strict=True
        ):
            try:
                out.append((str(key), float(value)))
            except (TypeError, ValueError):
                continue
        return out

    def metric_map(self, name: str) -> dict[str, float]:
        """Return report metrics by name for cards and list summaries."""
        return {str(key): float(value) for key, value in self.metrics(name)}

    def branch_summary(self, name: str) -> list[dict]:
        frame = self._read(name, "branch_summary.csv")
        return [] if frame is None else frame.to_dicts()

    def gate_summary(self, name: str) -> dict:
        frame = self._read(name, "gate_decisions.csv")
        empty = {"total": 0, "allowed": 0, "rejected": 0, "rate": None,
                 "by_tag": [], "by_state": []}
        if frame is None or frame.height == 0 or "real_allow" not in frame.columns:
            return empty
        allowed = int(frame["real_allow"].fill_null(False).sum())
        by_tag = (
            frame.group_by("strategy_tag").agg(
                pl.len().alias("total"),
                pl.col("real_allow").fill_null(False).sum().alias("allowed"),
            ).sort("strategy_tag").to_dicts()
            if "strategy_tag" in frame.columns else []
        )
        by_state = (
            frame.group_by("s7_state").len().sort("len", descending=True).to_dicts()
            if "s7_state" in frame.columns else []
        )
        return {"total": frame.height, "allowed": allowed,
                "rejected": frame.height - allowed,
                "rate": allowed / frame.height, "by_tag": by_tag,
                "by_state": by_state}

    def gate_rows(self, name: str, *, limit: int = 200) -> list[dict]:
        frame = self._read(name, "gate_decisions.csv")
        if frame is None or frame.height == 0:
            return []
        if "date" in frame.columns:
            frame = frame.sort("date", descending=True)
        return frame.head(limit).to_dicts()

    def rows(self, name: str, filename: str, *, limit: int = 500) -> list[dict]:
        frame = self._read(name, filename)
        return [] if frame is None else frame.tail(limit).to_dicts()

    def text(self, name: str, filename: str, *, limit: int = 100_000) -> str:
        found = self.file_bytes(name, filename)
        if found is None:
            return ""
        return found[0][-limit:].decode("utf-8", errors="replace")

    def equity_points(self, name: str) -> list[tuple[str, float]]:
        """净值曲线，给 SVG 图用。"""
        frame = self._read(name, "daily_nav.csv")
        if frame is None or frame.height == 0:
            return []
        return list(
            zip(
                [str(d) for d in frame["date"].to_list()],
                [float(v) for v in frame["total_value"].to_list()],
            )
        )

    def benchmark_points(self, name: str) -> list[tuple[str, float]]:
        frame = self._read(name, "benchmark_nav.csv")
        if frame is None or frame.height == 0:
            return []
        return list(zip([str(d) for d in frame["date"].to_list()],
                        [float(v) for v in frame["total_value"].to_list()]))

    def drawdown_points(self, name: str) -> list[float]:
        frame = self._read(name, "daily_nav.csv")
        if frame is None or frame.height == 0:
            return []
        values = [float(v) for v in frame["total_value"].to_list()]
        peak = values[0]
        out = []
        for value in values:
            peak = max(peak, value)
            out.append(value / peak - 1.0 if peak else 0.0)
        return out

    def chart_series(self, name: str) -> list[dict[str, float | str | None]]:
        """Return normalized, date-aligned series used by the interactive report chart."""
        nav = self._read(name, "daily_nav.csv")
        if nav is None or nav.height == 0:
            return []
        benchmark = self._read(name, "benchmark_nav.csv")
        benchmark_by_date: dict[str, float] = {}
        benchmark_initial = 0.0
        if benchmark is not None and benchmark.height:
            benchmark_by_date = {
                str(day): float(value)
                for day, value in zip(
                    benchmark["date"].to_list(),
                    benchmark["total_value"].to_list(),
                    strict=True,
                )
            }
            benchmark_initial = float(benchmark["total_value"][0])

        values = [float(value) for value in nav["total_value"].to_list()]
        initial = values[0]
        peak = initial
        rows: list[dict[str, float | str | None]] = []
        daily_returns = (
            nav["daily_return"].to_list()
            if "daily_return" in nav.columns
            else [0.0] + [
                values[index] / values[index - 1] - 1.0
                if values[index - 1] else 0.0
                for index in range(1, len(values))
            ]
        )
        for day, value, daily_return in zip(
            nav["date"].to_list(), values, daily_returns, strict=True
        ):
            peak = max(peak, value)
            benchmark_value = benchmark_by_date.get(str(day))
            strategy_return = value / initial - 1.0 if initial else 0.0
            benchmark_return = (
                benchmark_value / benchmark_initial - 1.0
                if benchmark_value is not None and benchmark_initial
                else None
            )
            rows.append({
                "date": str(day),
                "strategy": strategy_return,
                "benchmark": benchmark_return,
                "excess": (
                    strategy_return - benchmark_return
                    if benchmark_return is not None else None
                ),
                "drawdown": value / peak - 1.0 if peak else 0.0,
                "daily_return": float(daily_return or 0.0),
            })
        return rows

    def file_bytes(self, name: str, filename: str) -> tuple[bytes, str] | None:
        """返回 (内容, media type)。越界或类型不允许一律返回 None。"""
        directory = self._safe_dir(name)
        if directory is None:
            return None
        suffix = Path(filename).suffix.lower()
        if suffix not in _DOWNLOADABLE:
            return None
        target = (directory / filename).resolve()
        if not _within(target, directory) or not target.is_file():
            return None
        return target.read_bytes(), _MEDIA[suffix]

    # ---- 内部 ----

    def _safe_dir(self, name: str) -> Path | None:
        """把运行名解析成目录，拒绝一切路径逃逸。"""
        if not name or "/" in name or "\\" in name or name.startswith("."):
            return None
        candidate = (self._root / name).resolve()
        if not _within(candidate, self._root.resolve()) or not candidate.is_dir():
            return None
        return candidate

    def _read(self, name: str, filename: str) -> pl.DataFrame | None:
        directory = self._safe_dir(name)
        if directory is None:
            return None
        path = directory / filename
        if not path.is_file():
            return None
        try:
            return pl.read_csv(path, try_parse_dates=True)
        except Exception as exc:
            log.warning("backtest.bad_csv", path=str(path), error=str(exc))
            return None

    def _summary(self, path: Path) -> RunSummary | None:
        status_path = path / "run_status.csv"
        if not status_path.is_file():
            return None
        frame = pl.read_csv(status_path)
        # 少了必备列就不是一次完整的运行——半截目录直接跳过，
        # 而不是用默认值凑出一行假摘要。polars 读坏 CSV 不一定抛。
        required = {"run_id", "strategy", "initial_value", "total_value"}
        if frame.height == 0 or not required <= set(frame.columns):
            return None
        row = frame.to_dicts()[0]

        config = {}
        config_path = path / "config.json"
        if config_path.is_file():
            config = json.loads(config_path.read_text(encoding="utf-8"))

        generated = config.get("generated_at") or datetime.fromtimestamp(
            status_path.stat().st_mtime
        ).isoformat(timespec="seconds")

        return RunSummary(
            name=path.name,
            run_id=str(row.get("run_id", "")),
            strategy=str(row.get("strategy", "")),
            start=str(row.get("start", "")),
            end=str(row.get("end", "")),
            initial_value=float(row.get("initial_value") or 0.0),
            total_value=float(row.get("total_value") or 0.0),
            cumulative_return=(
                None if row.get("cumulative_return") is None
                else float(row["cumulative_return"])
            ),
            buys=int(row.get("buys") or 0),
            sells=int(row.get("sells") or 0),
            rejects=int(row.get("rejects") or 0),
            closed_lots=int(row.get("closed_lots") or 0),
            gate_allowed=int(row.get("gate_allowed") or 0),
            gate_rejected=int(row.get("gate_rejected") or 0),
            cost_model=str(config.get("cost_model", "")),
            generated_at=str(generated),
            params=_strategy_params(config),
            entry_fill=str(config.get("entry_fill", "")),
            source=str(config.get("source", "") or "cli"),
        )


PARAM_LABELS = {
    "require_auction_volume": "要求竞价成交量",
    "auction_at": "竞价决策时刻",
    "entry_price_buffer": "保护价上浮",
    "sizing_price_buffer": "定量价上浮",
    "max_position_value": "单票预算上限",
}
_PERCENT_PARAMS = frozenset({"entry_price_buffer", "sizing_price_buffer"})


def param_rows(params: Mapping[str, object]) -> list[tuple[str, str]]:
    """(中文名, 展示值)。参数名未登记时原样展示，不猜它的含义。"""
    return [
        (PARAM_LABELS.get(name, name), _param_text(name, value))
        for name, value in sorted(params.items())
    ]


def _param_text(name: str, value: object) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if name in _PERCENT_PARAMS and isinstance(value, (int, float)):
        return f"{float(value) * 100:g}%（开盘价 ×{1 + float(value):g}）"
    return str(value)


# 运行配置里属于"怎么跑"的键；其余视为策略参数（兼容没有 params 块的旧运行）
SOURCE_LABELS = {"web": "网页提交", "cli": "命令行研究"}

_RUN_KEYS = frozenset({
    "strategy", "strategy_file", "strategy_version", "cost_model", "jq_compat", "source",
    "benchmark", "universe_size", "generated_at", "params", "entry_fill",
    "entry_fill_mode", "entry_fill_stats", "corporate_action_days", "checkpoints",
})


def _strategy_params(config: Mapping[str, object]) -> dict[str, object]:
    stored = config.get("params")
    if isinstance(stored, Mapping):
        return dict(stored)
    return {k: v for k, v in config.items() if k not in _RUN_KEYS}


def _within(target: Path, root: Path) -> bool:
    return target == root or root in target.parents
