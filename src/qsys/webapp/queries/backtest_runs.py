"""回测运行目录读取层（#662 R3）：取代新接口对旧 BacktestService 的依赖。

运行目录由回测 worker / 命令行研究写入 ``var/runs/<name>/``。本层只读：
- 运行名必须匹配 ``RUN_NAME``，目录不能是符号链接；
- 读不到的值为 None：数值列为空不写 0，没有 ``generated_at`` 不用文件修改时间代替；
- CSV 原样读取（``try_parse_dates=False``），日期与时刻保留文件里的字符串。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import polars as pl
import structlog

from qsys.webapp.files import RUN_FILE_SUFFIXES

log = structlog.get_logger(__name__)

RUN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
REQUIRED = frozenset({"run_id", "strategy", "initial_value", "total_value"})
Cell = str | int | float | bool | None

# 运行配置里属于“怎么跑”的键；其余视为策略参数（兼容没有 params 块的旧运行，与旧口径相同）
_RUN_KEYS = frozenset({
    "strategy", "strategy_file", "strategy_version", "cost_model", "jq_compat", "source",
    "benchmark", "universe_size", "generated_at", "params", "entry_fill",
    "entry_fill_mode", "entry_fill_stats", "corporate_action_days", "checkpoints",
})


@dataclass(frozen=True)
class RunInfo:
    name: str
    run_id: str
    strategy: str
    start: str | None
    end: str | None
    initial_value: float | None
    total_value: float | None
    cumulative_return: float | None
    buys: int | None
    sells: int | None
    rejects: int | None
    closed_lots: int | None
    cost_model: str | None
    entry_fill: str | None
    params: dict[str, object] = field(default_factory=dict)
    generated_at: str | None = None
    source: Literal["web", "cli"] = "cli"


@dataclass(frozen=True)
class RunFile:
    name: str
    size: int


def _float(v) -> float | None:
    return None if v is None else float(v)


def _int(v) -> int | None:
    return None if v is None else int(v)


def _text(v) -> str | None:
    return None if v is None or v == "" else str(v)


def _params(config: Mapping[str, object]) -> dict[str, object]:
    stored = config.get("params")
    if isinstance(stored, Mapping):
        return dict(stored)
    return {k: v for k, v in config.items() if k not in _RUN_KEYS}


class BacktestRuns:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def _dir(self, name: str) -> Path | None:
        if not RUN_NAME.fullmatch(name or ""):
            return None
        path = self._root / name
        if path.is_symlink() or not path.is_dir():
            return None
        return path

    def _file(self, name: str, filename: str) -> Path | None:
        d = self._dir(name)
        if d is None:
            return None
        path = d / filename
        return None if path.is_symlink() or not path.is_file() else path

    def _read(self, path: Path) -> pl.DataFrame | None:
        try:
            return pl.read_csv(path, try_parse_dates=False, infer_schema_length=10_000)
        except Exception as exc:  # 坏 CSV 不让整页打不开
            log.warning("webapp.backtest.bad_csv", file=path.name, error_type=type(exc).__name__)
            return None

    # ---- 运行 ----
    def runs(self) -> list[RunInfo]:
        if not self._root.is_dir():
            return []
        out = []
        for path in sorted(self._root.iterdir()):
            info = self.run(path.name)
            if info is not None:
                out.append(info)
        return out

    def run(self, name: str) -> RunInfo | None:
        status = self._file(name, "run_status.csv")
        if status is None:
            return None
        try:
            return self._summary(name, status)
        except Exception as exc:
            log.warning("webapp.backtest.bad_run_dir", run=name, error_type=type(exc).__name__)
            return None

    def _summary(self, name: str, status: Path) -> RunInfo | None:
        frame = self._read(status)
        if frame is None or frame.height == 0 or not REQUIRED <= set(frame.columns):
            return None
        row = frame.row(0, named=True)
        config_path = status.parent / "config.json"
        config = {}
        if config_path.is_file() and not config_path.is_symlink():
            config = json.loads(config_path.read_text(encoding="utf-8"))
        return RunInfo(
            name=name, run_id=str(row["run_id"]), strategy=str(row["strategy"]),
            start=_text(row.get("start")), end=_text(row.get("end")),
            initial_value=_float(row["initial_value"]), total_value=_float(row["total_value"]),
            cumulative_return=_float(row.get("cumulative_return")),
            buys=_int(row.get("buys")), sells=_int(row.get("sells")),
            rejects=_int(row.get("rejects")), closed_lots=_int(row.get("closed_lots")),
            cost_model=_text(config.get("cost_model")), entry_fill=_text(config.get("entry_fill")),
            params=_params(config), generated_at=_text(config.get("generated_at")),
            source="web" if config.get("source") == "web" else "cli")

    def files(self, name: str) -> list[RunFile]:
        d = self._dir(name)
        if d is None:
            return []
        return [RunFile(p.name, p.stat().st_size) for p in sorted(d.iterdir())
                if not p.is_symlink() and p.is_file() and p.suffix.lower() in RUN_FILE_SUFFIXES]

    # ---- 表 ----
    def table(self, name: str, filename: str, *, limit: int | None = None) -> list[dict[str, Cell]]:
        path = self._file(name, filename)
        frame = None if path is None else self._read(path)
        if frame is None:
            return []
        return (frame if limit is None else frame.tail(limit)).to_dicts()

    def metrics(self, name: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for row in self.table(name, "metrics.csv"):
            try:
                out[str(row["metric"])] = float(row["value"])
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def nav(self, name: str) -> list[tuple[str, float | None]]:
        return [(str(r["date"]), _float(r.get("total_value"))) for r in self.table(name, "daily_nav.csv")]

    def benchmark(self, name: str) -> dict[str, float]:
        return {str(r["date"]): float(r["total_value"]) for r in self.table(name, "benchmark_nav.csv")
                if r.get("total_value") is not None}
