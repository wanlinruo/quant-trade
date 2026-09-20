"""基于静态证据的确定性结构优化建议器。

这里只判断已经标准化的事实，不读取仓库、时钟或全局注册表，也不执行任何
Schema 变更。调用方必须把无法确认的事实传为 ``None``，建议器会保守返回
``INSUFFICIENT_EVIDENCE``。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AdviceKind(StrEnum):
    MERGE_CANDIDATE = "MERGE_CANDIDATE"
    SPLIT_CANDIDATE = "SPLIT_CANDIDATE"
    KEEP_SEPARATE = "KEEP_SEPARATE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True, slots=True)
class StructureAdvice:
    kind: AdviceKind
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PairEvidence:
    """两张表是否适合物理合并所需的全部静态事实。"""

    same_grain: bool | None
    same_primary_key: bool | None
    complete_one_to_one: bool | None
    same_update_cadence: bool | None
    same_lifecycle: bool | None
    same_status: bool | None
    same_recovery: bool | None
    same_publish_stage: bool | None
    independent_arrival: bool | None
    independent_consumers: bool | None
    upstream_reused: bool | None
    optional_relation: bool | None
    tables_green: bool | None
    relation_green: bool | None


@dataclass(frozen=True, slots=True)
class SplitEvidence:
    """单表内部是否混合了应独立管理的语义。"""

    primary_key_known: bool | None
    consumers_known: bool | None
    health_evidence_complete: bool | None
    mixed_grain: bool | None
    mixed_validity: bool | None
    mixed_update_cadence: bool | None
    fact_and_parameterized_factor: bool | None
    independent_field_boundary: bool | None


def _missing(items: tuple[tuple[str, bool | None], ...]) -> tuple[str, ...]:
    return tuple(f"缺少关键证据：{label}" for label, value in items if value is None)


def advise_pair(evidence: PairEvidence) -> StructureAdvice:
    """建议合并或保持独立；任何关键事实未知时不作结构推断。"""
    facts = (
        ("粒度是否一致", evidence.same_grain),
        ("主键是否一致", evidence.same_primary_key),
        ("完整一对一关系", evidence.complete_one_to_one),
        ("更新节奏是否一致", evidence.same_update_cadence),
        ("生命周期是否一致", evidence.same_lifecycle),
        ("状态是否一致", evidence.same_status),
        ("失败恢复方式是否一致", evidence.same_recovery),
        ("是否同一生产阶段发布", evidence.same_publish_stage),
        ("是否独立到货", evidence.independent_arrival),
        ("是否存在独立消费者", evidence.independent_consumers),
        ("上游是否被复用", evidence.upstream_reused),
        ("关系是否可选", evidence.optional_relation),
        ("两表健康是否全绿", evidence.tables_green),
        ("关系健康是否为绿", evidence.relation_green),
    )
    missing = _missing(facts)
    if missing:
        return StructureAdvice(AdviceKind.INSUFFICIENT_EVIDENCE, missing)

    separate: list[str] = []
    if not evidence.same_grain:
        separate.append("粒度不同，应保持独立")
    if not evidence.same_primary_key:
        separate.append("主键不同，应保持独立")
    if not evidence.complete_one_to_one:
        separate.append("未形成完整一对一关系")
    if evidence.independent_arrival:
        separate.append("存在独立来源或到货时点")
    if evidence.optional_relation:
        separate.append("连接为可选关系")
    if evidence.upstream_reused:
        separate.append("上游被多个下游复用")
    if evidence.independent_consumers:
        separate.append("存在独立消费语义")
    if not evidence.same_lifecycle:
        separate.append("生命周期不同")
    if not evidence.same_update_cadence:
        separate.append("更新节奏不同")
    if not evidence.same_status:
        separate.append("数据集状态不同")
    if not evidence.same_recovery:
        separate.append("失败恢复方式不同")
    if not evidence.same_publish_stage:
        separate.append("不由同一生产阶段发布")
    if not evidence.tables_green:
        separate.append("相关表健康未全部为绿")
    if not evidence.relation_green:
        separate.append("关系健康未通过")
    if separate:
        return StructureAdvice(AdviceKind.KEEP_SEPARATE, tuple(separate))

    return StructureAdvice(AdviceKind.MERGE_CANDIDATE, (
        "粒度和主键一致",
        "关系质检确认完整一对一",
        "更新节奏、生命周期、状态和恢复方式一致",
        "由同一生产阶段发布",
        "没有独立到货、独立消费者或复用语义",
        "相关表与关系健康均为绿",
    ))


def advise_split(evidence: SplitEvidence) -> StructureAdvice:
    """判断单表是否应进入拆分评估；没有触发项即保持当前结构。"""
    required_truth = (
        ("主键", evidence.primary_key_known),
        ("消费者", evidence.consumers_known),
        ("完整质量证据", evidence.health_evidence_complete),
    )
    required_observations = (
        ("字段粒度", evidence.mixed_grain),
        ("字段有效期", evidence.mixed_validity),
        ("字段更新节奏", evidence.mixed_update_cadence),
        ("事实与参数化因子边界", evidence.fact_and_parameterized_factor),
        ("独立来源、生命周期和消费边界", evidence.independent_field_boundary),
    )
    missing = tuple(
        f"缺少关键证据：{label}"
        for label, value in required_truth if value is not True
    ) + _missing(required_observations)
    if missing:
        return StructureAdvice(AdviceKind.INSUFFICIENT_EVIDENCE, missing)

    triggers = []
    if evidence.mixed_grain:
        triggers.append("同表混合多个粒度")
    if evidence.mixed_validity:
        triggers.append("同表字段有效期语义不同")
    if evidence.mixed_update_cadence:
        triggers.append("同表字段更新节奏不同")
    if evidence.fact_and_parameterized_factor:
        triggers.append("同表混合事实与参数化因子")
    if evidence.independent_field_boundary:
        triggers.append("字段存在独立来源、生命周期和消费边界")
    if triggers:
        return StructureAdvice(AdviceKind.SPLIT_CANDIDATE, tuple(triggers))
    return StructureAdvice(
        AdviceKind.KEEP_SEPARATE,
        ("未发现混合粒度、有效期、更新节奏或独立字段边界",),
    )
