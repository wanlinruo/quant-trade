"""结构建议器只按显式证据决策，并为每个结论给出理由。"""

from dataclasses import replace

from qsys.data.catalog.advisor import (
    AdviceKind,
    PairEvidence,
    SplitEvidence,
    advise_pair,
    advise_split,
)


MERGEABLE = PairEvidence(
    same_grain=True, same_primary_key=True, complete_one_to_one=True,
    same_update_cadence=True, same_lifecycle=True, same_status=True,
    same_recovery=True, same_publish_stage=True, independent_arrival=False,
    independent_consumers=False, upstream_reused=False,
    optional_relation=False, tables_green=True, relation_green=True,
)


def test_merge_requires_every_positive_and_negative_condition():
    advice = advise_pair(MERGEABLE)
    assert advice.kind is AdviceKind.MERGE_CANDIDATE
    assert len(advice.reasons) == 6


def test_missing_pair_evidence_always_wins():
    evidence = replace(
        MERGEABLE, same_primary_key=None, optional_relation=True,
        complete_one_to_one=False,
    )
    advice = advise_pair(evidence)
    assert advice.kind is AdviceKind.INSUFFICIENT_EVIDENCE
    assert advice.reasons == ("缺少关键证据：主键是否一致",)


def test_each_separation_boundary_blocks_merge():
    changes = {
        "same_grain": False, "same_primary_key": False,
        "complete_one_to_one": False, "same_update_cadence": False,
        "same_lifecycle": False, "same_status": False,
        "same_recovery": False, "same_publish_stage": False,
        "independent_arrival": True, "independent_consumers": True,
        "upstream_reused": True, "optional_relation": True,
        "tables_green": False, "relation_green": False,
    }
    for field, value in changes.items():
        advice = advise_pair(replace(MERGEABLE, **{field: value}))
        assert advice.kind is AdviceKind.KEEP_SEPARATE, field
        assert advice.reasons, field


def test_split_candidate_lists_every_trigger():
    advice = advise_split(SplitEvidence(
        True, True, True, True, True, True, True, True,
    ))
    assert advice.kind is AdviceKind.SPLIT_CANDIDATE
    assert len(advice.reasons) == 5


def test_split_requires_metadata_and_quality_evidence():
    advice = advise_split(SplitEvidence(
        None, False, None, False, False, False, False, False,
    ))
    assert advice.kind is AdviceKind.INSUFFICIENT_EVIDENCE
    assert advice.reasons == (
        "缺少关键证据：主键", "缺少关键证据：消费者",
        "缺少关键证据：完整质量证据",
    )


def test_no_split_trigger_keeps_current_structure_and_is_deterministic():
    evidence = SplitEvidence(True, True, True, False, False, False, False, False)
    first = advise_split(evidence)
    assert first.kind is AdviceKind.KEEP_SEPARATE
    assert advise_split(evidence) == first
