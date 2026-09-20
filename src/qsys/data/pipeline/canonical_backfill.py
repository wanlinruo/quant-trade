"""可恢复的 canonical 分区回填。

每个年份或交易日只有在写后对账成功、目标文件哈希落入 manifest 后才算完成。
中断时已有完整分区继续可读，下一次 ``--resume`` 会验证哈希后跳过。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Callable, Iterable

from qsys.data.pipeline.canonical_daily import (
    build_adjustment_factor_daily,
    build_bar_daily,
    build_valuation_daily,
    daily_reconciliation,
    require_canonical_daily_sources,
)
from qsys.data.pipeline.canonical_minute import build_bar_minute_1m
from qsys.data.pipeline.canonical_reference import Reconciliation
from qsys.data.pipeline.canonical_state import (
    build_market_flags_daily,
    build_market_regime_daily,
    build_price_limit_daily,
    build_security_status_daily,
    require_canonical_state_sources,
    state_reconciliation,
)
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


@dataclass(frozen=True, slots=True)
class FileEvidence:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class BackfillManifest:
    family: str
    scope_start: str
    scope_end: str
    revision: str
    counts: dict[str, int]
    reconciliation: dict[str, dict[str, int | bool]]
    files: tuple[FileEvidence, ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 << 20):
            digest.update(block)
    return digest.hexdigest()


def _summary(result: Reconciliation) -> dict[str, int | bool]:
    return {
        "matches": result.matches,
        "source_rows": result.left_rows,
        "target_rows": result.right_rows,
        "source_only": len(result.left_only),
        "target_only": len(result.right_only),
        "mismatched": len(result.mismatched),
    }


def _years(start: date, end: date) -> Iterable[tuple[date, date]]:
    for year in range(start.year, end.year + 1):
        yield max(start, date(year, 1, 1)), min(end, date(year, 12, 31))


class ManifestStore:
    def __init__(
        self, root: Path, warehouse: Warehouse, *, revision: str,
        minimum_free_gib: float = 10.0,
    ) -> None:
        self.root = Path(root)
        self.warehouse = warehouse
        self.revision = revision
        self.minimum_free_bytes = int(minimum_free_gib * 1024 ** 3)

    def path(self, family: str, start: date, end: date) -> Path:
        return self.root / family / f"{start:%Y%m%d}-{end:%Y%m%d}.json"

    def require_space(self) -> None:
        free = shutil.disk_usage(self.warehouse.root).free
        if free < self.minimum_free_bytes:
            raise RuntimeError(
                f"canonical 回填剩余空间不足：{free / 1024 ** 3:.1f} GiB；"
                f"要求至少 {self.minimum_free_bytes / 1024 ** 3:.1f} GiB"
            )

    def complete(self, family: str, start: date, end: date) -> bool:
        path = self.path(family, start, end)
        if not path.is_file():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return all(
                (target := self.warehouse.root / item["path"]).is_file()
                and target.stat().st_size == item["size"]
                and _sha256(target) == item["sha256"]
                for item in payload["files"]
            )
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            return False

    def write(
        self,
        family: str,
        start: date,
        end: date,
        *,
        counts: dict[str, int],
        reconciliations: dict[str, Reconciliation],
        files: Iterable[Path],
    ) -> Path:
        failed = sorted(
            name for name, result in reconciliations.items() if not result.matches
        )
        if failed:
            raise RuntimeError(
                f"{family} {start}..{end} 写后对账失败：{', '.join(failed)}"
            )
        evidence = tuple(
            FileEvidence(
                str(path.relative_to(self.warehouse.root)),
                path.stat().st_size,
                _sha256(path),
            )
            for path in sorted(set(files))
            if path.is_file()
        )
        manifest = BackfillManifest(
            family, start.isoformat(), end.isoformat(), self.revision,
            counts,
            {name: _summary(result) for name, result in reconciliations.items()},
            evidence,
        )
        path = self.path(family, start, end)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(asdict(manifest), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
        return path


def _year_files(warehouse: Warehouse, datasets: Iterable[str], year: int) -> list[Path]:
    return [
        warehouse.root / dataset / f"year={year}" / "part.parquet"
        for dataset in datasets
    ]


def _require_files(files: Iterable[Path]) -> list[Path]:
    resolved = list(files)
    missing = [str(path) for path in resolved if not path.is_file()]
    if missing:
        raise RuntimeError(f"canonical 发布后缺少目标文件：{missing}")
    return resolved


def backfill_daily(
    warehouse: Warehouse,
    store: ManifestStore,
    start: date,
    end: date,
    *,
    resume: bool = False,
) -> dict[str, int]:
    totals = {name: 0 for name in (
        "bar_daily", "valuation_daily", "adjustment_factor_daily"
    )}
    for lo, hi in _years(start, end):
        family = "daily"
        if resume and store.complete(family, lo, hi):
            continue
        store.require_space()
        require_canonical_daily_sources(warehouse, lo, hi)
        counts = {
            "bar_daily": build_bar_daily(warehouse, lo, hi),
            "valuation_daily": build_valuation_daily(warehouse, lo, hi),
            "adjustment_factor_daily": build_adjustment_factor_daily(
                warehouse, lo, hi
            ),
        }
        diffs = daily_reconciliation(warehouse, lo, hi)
        selected = {name: diffs[name] for name in counts}
        files = _require_files(_year_files(warehouse, counts, lo.year))
        store.write(
            family, lo, hi, counts=counts, reconciliations=selected,
            files=files,
        )
        for name, count in counts.items():
            totals[name] += count
    return totals


def _backfill_state_family(
    warehouse: Warehouse,
    store: ManifestStore,
    start: date,
    end: date,
    *,
    family: str,
    builders: dict[str, Callable[[Warehouse, date, date], int]],
    resume: bool,
) -> dict[str, int]:
    totals = {name: 0 for name in builders}
    for lo, hi in _years(start, end):
        if resume and store.complete(family, lo, hi):
            continue
        store.require_space()
        require_canonical_state_sources(warehouse, lo, hi)
        counts = {name: build(warehouse, lo, hi) for name, build in builders.items()}
        diffs = state_reconciliation(warehouse, lo, hi)
        selected = {name: diffs[name] for name in builders}
        files = _require_files(_year_files(warehouse, counts, lo.year))
        store.write(
            family, lo, hi, counts=counts, reconciliations=selected,
            files=files,
        )
        for name, count in counts.items():
            totals[name] += count
    return totals


def backfill_state_base(
    warehouse: Warehouse,
    store: ManifestStore,
    start: date,
    end: date,
    *,
    resume: bool = False,
) -> dict[str, int]:
    return _backfill_state_family(
        warehouse, store, start, end, family="state-base", resume=resume,
        builders={
            "price_limit_daily": build_price_limit_daily,
            "security_status_daily": build_security_status_daily,
        },
    )


def backfill_state_derived(
    warehouse: Warehouse,
    store: ManifestStore,
    start: date,
    end: date,
    *,
    resume: bool = False,
) -> dict[str, int]:
    query = WarehouseQuery(warehouse)
    flags_bounds = query.dataset_bounds("daily_flags")
    if flags_bounds is None:
        raise RuntimeError("daily_flags 没有可回填范围")
    # regime 的首日计算依赖此前 30 个自然日的 canonical flags。先发布
    # 支撑窗口，再只从用户请求起点发布/验收 regime，避免分块首日失真。
    flags_start = max(flags_bounds[0], start - timedelta(days=30))
    flags = _backfill_state_family(
        warehouse, store, flags_start, end, family="state-flags", resume=resume,
        builders={
            "market_flags_daily": build_market_flags_daily,
        },
    )
    regime = _backfill_state_family(
        warehouse, store, start, end, family="state-regime", resume=resume,
        builders={
            "market_regime_daily": build_market_regime_daily,
        },
    )
    return {**flags, **regime}


def backfill_minutes(
    warehouse: Warehouse,
    store: ManifestStore,
    start: date,
    end: date,
    *,
    resume: bool = False,
) -> dict[str, int]:
    totals = {"bar_minute_1m": 0, "days": 0}
    current = start
    while current <= end:
        if resume and store.complete("minute", current, current):
            current += timedelta(days=1)
            continue
        store.require_space()
        result = build_bar_minute_1m(warehouse, current, current)
        target = warehouse.day_path("bar_minute_1m", current)
        # build_bar_minute_1m 已执行逐键逐字段写后对账；这里用一个空差异对象
        # 统一 manifest 格式，不重复把百万行搬入内存。
        reconciled = Reconciliation(
            result.rows, result.rows, (), (), ()
        )
        store.write(
            "minute", current, current,
            counts={"bar_minute_1m": result.rows},
            reconciliations={"bar_minute_1m": reconciled},
            files=[target],
        )
        totals["bar_minute_1m"] += result.rows
        totals["days"] += result.days
        current += timedelta(days=1)
    return totals
