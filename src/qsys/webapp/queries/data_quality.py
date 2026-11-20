"""数据质量查询（#662 R4，设计文档 §8.13）：近 30 次质检、警告来源、规则表、规则详情、违规样本下载。

规则表的最新状态：字段级取各 (metric_id, check_id) 最新一条，表级取各 (dataset_id, check_id)，
关系级取各 (upstream, downstream, check_id)（09 遗留：此前不读 relation_checks），三者取最差。
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
from fastapi import HTTPException

from qsys.data.catalog.checks import CHECKS
from qsys.data.query import WarehouseQuery
from qsys.webapp.contracts.data import (
    QualitySummary, RuleDetail, RuleResult, RuleRow,
)
from qsys.webapp.files import controlled_file
from qsys.webapp.queries.data_catalog import sample_file
from qsys.webapp.queries.data_derive import BLOCKING, quality_runs, warning_sources

# 表级与关系级规则的登记说明（名称、通过条件、局限、实现位置），与旧服务的同名表逐字一致
RULE_METADATA = {
    "T1.table_contract": ("表结构与主键契约", "Schema 一致、主键非空且唯一", "只验证当前检查范围", "qsys.data.catalog.health.check_table_contract"),
    "T2.table_completeness": ("表完整性", "检查范围满足数据集空表与覆盖策略", "覆盖范围由运行器传入", "qsys.data.catalog.health.check_table_contract"),
    "T3.freshness": ("发布时效", "最后发布时间未超过策略 TTL", "依赖准确的发布时间", "qsys.data.catalog.health.freshness_evidence"),
    "T4.field_status": ("字段真实性汇总", "字段级证据无红、黄、灰状态", "汇总质量取决于字段规则覆盖", "qsys.data.catalog.health.field_status_evidence"),
    "T5.frozen_checksum": ("冻结文件校验和", "全部文件与基线校验和一致", "没有基线时只能判为灰色", "qsys.data.catalog.health.frozen_checksum_evidence"),
    "T6.partition_layout": ("分区布局", "分区键、目录与数据值一致", "只检查已发现的物理分区", "qsys.data.catalog.health.partition_layout_evidence"),
    "R1.join_integrity": ("关系完整性", "连接键、基数、孤儿行和刷新顺序符合契约", "只代表记录的检查范围", "qsys.data.catalog.health.check_dependency"),
    "R1.relation_contract": ("关系元数据", "关系键和基数已登记", "不替代实际数据连接检查", "qsys.data.catalog.health.check_dependency"),
    "R2.independent_recompute": ("独立重算", "独立实现与下游产出一致", "需要独立实现和同范围输入", "qsys.data.catalog.health.relation_recompute_evidence"),
}
_HEALTH_TO_RULE = {"red": "blocking", "yellow": "warning", "gray": "unknown", "green": "passed"}
_RANK = {"blocking": 0, "warning": 1, "unknown": 2, "passed": 3}
SAMPLE_SUFFIXES = frozenset({".parquet"})


def _target(row: dict) -> str:
    if row.get("metric_id"):
        return str(row["metric_id"])
    if row.get("dataset_id"):
        return str(row["dataset_id"])
    return f"{row.get('upstream')}->{row.get('downstream')}"


def _result(row: dict) -> RuleResult:
    data = {k: row.get(k) for k in RuleResult.model_fields if k != "sample_file"}
    return RuleResult(**data, sample_file=sample_file(row) if row.get("metric_id") else None)


def _latest_by_target(frame: pl.DataFrame, keys: list[str]) -> pl.DataFrame:
    return frame.sort("checked_at").group_by(keys).last() if frame.height else frame


class DataQuality:
    def __init__(self, query: WarehouseQuery, sample_root: Path | None) -> None:
        self._query = query
        self._sample_root = sample_root

    def _rule_rows(self, m: pl.DataFrame, d: pl.DataFrame, r: pl.DataFrame) -> list[RuleRow]:
        status: dict[str, str] = {}

        def worse(cid: str, value: str) -> None:
            if cid not in status or _RANK[value] < _RANK[status[cid]]:
                status[cid] = value

        for row in _latest_by_target(m, ["metric_id", "check_id"]).iter_rows(named=True):
            worse(row["check_id"], "passed" if row["passed"] else
                  "blocking" if row["severity"] == BLOCKING else "warning")
        for frame, keys in ((d, ["dataset_id", "check_id"]), (r, ["upstream", "downstream", "check_id"])):
            for row in _latest_by_target(frame, keys).iter_rows(named=True):
                worse(row["check_id"], _HEALTH_TO_RULE.get(str(row["status"]), "unknown"))
        rows = [RuleRow(id=c.id, name=c.description, category=str(c.category.value), scope=str(c.scope.value),
                        known_issues=list(c.known_issues), upstream_self_consistency=c.upstream_self_consistency,
                        latest_status=status.get(c.id, "unknown")) for c in CHECKS.values()]
        rows += [RuleRow(id=cid, name=meta[0], category="dataset/relation", scope="表/关系", known_issues=[],
                         upstream_self_consistency=False, latest_status=status.get(cid, "unknown"))
                 for cid, meta in RULE_METADATA.items()]
        known = {x.id for x in rows}
        rows += [RuleRow(id=cid, name=cid, category="persisted", scope="仅有历史结果", known_issues=[],
                         upstream_self_consistency=False, latest_status=st)
                 for cid, st in status.items() if cid not in known]
        return sorted(rows, key=lambda x: (_RANK[x.latest_status], x.id))

    def summary(self) -> QualitySummary:
        m, d, r = self._query.metric_checks(), self._query.dataset_checks(), self._query.relation_checks()
        return QualitySummary(runs=quality_runs(m, d), warning_sources=warning_sources(m),
                              rules=self._rule_rows(m, d, r))

    def rule(self, check_id: str) -> RuleDetail | None:
        rows = [*self._query.metric_checks(check_id=check_id).to_dicts(),
                *self._query.dataset_checks(check_id=check_id).to_dicts(),
                *[x for x in self._query.relation_checks().to_dicts() if x["check_id"] == check_id]]
        history = sorted(rows, key=lambda x: x["checked_at"], reverse=True)
        check, meta = CHECKS.get(check_id), RULE_METADATA.get(check_id)
        if check is None and meta is None and not history:
            return None
        if check is not None:
            name, category = check.description, str(check.category.value)
            condition = "违规数为 0，且适用范围内检查行数大于 0"
            limitation = f"执行范围：{check.scope.value}；已知限制：{', '.join(check.known_issues) or '无登记'}"
            implementation = f"{check.run.__module__}.{check.run.__name__}"
            targets = list(check.metrics)
        elif meta is not None:
            name, condition, limitation, implementation = meta
            category, targets = "dataset/relation", sorted({_target(x) for x in history})
        else:
            name, category, targets = check_id, "persisted", []
            condition = "以最近持久化结果的状态或 passed 字段为准"
            limitation = "规则定义未在当前代码注册表中登记"
            implementation = "仅有历史结果"
        latest: dict[str, dict] = {}
        for x in history:  # 已按时间倒序：每个对象取第一条
            latest.setdefault(_target(x), x)
        return RuleDetail(id=check_id, name=name, category=category, condition=condition, limitation=limitation,
                          implementation=implementation, targets=targets,
                          latest=[_result(x) for x in latest.values()], history=[_result(x) for x in history])

    def sample(self, run_id: str, filename: str):
        if self._sample_root is None:
            raise HTTPException(status_code=404, detail="文件不存在")
        return controlled_file(self._sample_root, f"{run_id}/{filename}", suffixes=SAMPLE_SUFFIXES)
