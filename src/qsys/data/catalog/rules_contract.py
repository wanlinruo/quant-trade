"""基础数据契约：让每条指标至少有一项真实、可重复执行的验证。

数值真值仍由 A/B/C1-C5 专项规则负责。本模块覆盖外部真值不适用的部分：
物理文件可读、列类型符合当前 schema、身份键没有落成 null，以及不落盘策略
因子的结构化定义完整。空的可选运行表记为“不适用”，而不是伪造检查分母。
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from qsys.data import schema
from qsys.data.catalog import REGISTRY
from qsys.data.catalog.checks import (
    Category,
    Check,
    CheckContext,
    CheckResult,
    Scope,
    register,
)
from qsys.data.catalog.types import Computed


_KEYS: dict[str, tuple[str, ...]] = {
    "instruments": ("symbol", "as_of"),
    "corporate_actions": ("symbol", "ex_date"),
    "name_history": ("symbol", "start_date"),
    "ingest_log": ("dataset", "dt", "provider", "ingested_at"),
    "metric_checks": ("run_id", "checked_at", "metric_id", "check_id"),
    "metric_evidence": ("metric_id", "dt", "checked_at"),
    "live_equity": ("dt",),
    "live_fills": ("client_order_id", "broker_trade_id", "filled_at"),
    "live_rejects": ("client_order_id", "at"),
    "live_recon": ("dt", "at", "layer"),
}


def _expected(dataset: str) -> pa.Schema:
    return schema.PAID_DAILY if dataset == "paid_daily" else schema.ALL[dataset]


def _files(root: Path, dataset: str) -> list[Path]:
    base = root / dataset
    return sorted({*base.glob("part.parquet"), *base.glob("year=*/part.parquet")})


def _default_keys(dataset: str, names: Iterable[str]) -> tuple[str, ...]:
    available = set(names)
    return tuple(name for name in ("symbol", "dt") if name in available)


def _storage_contract(dataset: str, ctx: CheckContext) -> CheckResult:
    expected = _expected(dataset)
    paths = _files(ctx.warehouse.root, dataset)
    if not paths:
        return CheckResult(
            0, 0, f"{dataset} 尚无物理文件；可选数据集本次不适用", applicable=False
        )

    problems: list[dict[str, object]] = []
    total_rows = 0
    keys = _KEYS.get(dataset, _default_keys(dataset, expected.names))
    seen_columns: set[str] = set()
    for path in paths:
        try:
            parquet = pq.ParquetFile(path)
            actual = parquet.schema_arrow
            metadata = parquet.metadata
        except Exception as exc:
            problems.append({
                "file": str(path), "column": "*", "problem": f"不可读：{exc}"
            })
            continue
        total_rows += metadata.num_rows
        seen_columns.update(actual.names)
        for field in actual:
            if field.name in expected.names and field.type != expected.field(field.name).type:
                problems.append({
                    "file": str(path),
                    "column": field.name,
                    "problem": f"类型 {field.type}，期望 {expected.field(field.name).type}",
                })
        # null_count 来自 Parquet 页统计，不扫描巨型事实表；缺统计时不作推断。
        index = {name: i for i, name in enumerate(actual.names)}
        for key in keys:
            if key not in index:
                continue
            nulls = 0
            known = True
            for group_no in range(metadata.num_row_groups):
                stats = metadata.row_group(group_no).column(index[key]).statistics
                if stats is None or stats.null_count is None:
                    known = False
                    break
                nulls += stats.null_count
            if known and nulls:
                problems.append({
                    "file": str(path), "column": key,
                    "problem": f"身份键含 {nulls} 个 null",
                })

    for field in expected:
        if field.name not in seen_columns:
            problems.append({
                "file": "全部分区", "column": field.name, "problem": "列从未落盘"
            })
    detail = (
        f"{dataset}: {len(paths)} 个 Parquet，{total_rows} 行；"
        f"schema/身份键异常 {len(problems)} 项"
    )
    return CheckResult(
        len(problems), max(total_rows, len(paths)), detail,
        pl.DataFrame(problems) if problems else None,
    )


def _strategy_contract(_ctx: CheckContext) -> CheckResult:
    metrics = [metric for metric in REGISTRY.values() if not metric.landed]
    bad: list[dict[str, str]] = []
    for metric in metrics:
        if not isinstance(metric.lineage, Computed):
            bad.append({"metric_id": metric.id, "problem": "非 Computed 血缘"})
            continue
        if not metric.lineage.formula.strip():
            bad.append({"metric_id": metric.id, "problem": "公式为空"})
        if not metric.ext_consumers:
            bad.append({"metric_id": metric.id, "problem": "没有登记消费方"})
        missing = set(metric.lineage.upstream) - set(REGISTRY)
        if missing:
            bad.append({"metric_id": metric.id, "problem": f"上游不存在：{sorted(missing)}"})
    return CheckResult(
        len(bad), len(metrics),
        f"{len(metrics)} 个不落盘策略因子，结构化定义异常 {len(bad)} 项",
        pl.DataFrame(bad) if bad else None,
    )


for _dataset in sorted({metric.dataset for metric in REGISTRY.values() if metric.landed}):
    _metric_ids = tuple(
        metric.id for metric in REGISTRY.values()
        if metric.landed and metric.dataset == _dataset
    )
    register(Check(
        f"C6.storage_contract.{_dataset}", Category.VALIDITY, Scope.INCREMENTAL,
        _metric_ids, (),
        "Parquet 可读、当前 schema 可满足且身份键无已知 null",
        lambda ctx, dataset=_dataset: _storage_contract(dataset, ctx),
    ))

register(Check(
    "C7.catalog_contract.strategy", Category.VALIDITY, Scope.INCREMENTAL,
    tuple(metric.id for metric in REGISTRY.values() if not metric.landed), (),
    "不落盘策略因子的公式、上游与消费方定义完整",
    _strategy_contract,
))
