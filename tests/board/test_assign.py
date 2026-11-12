from datetime import date, datetime

from qsys.board.assign import OTHER, assign, concept_counts
from qsys.board.concepts import ConceptSnapshot


def _snap(members):
    return ConceptSnapshot(day=date(2026, 11, 12), fetched_at=datetime(2026, 11, 12, 8, 50),
                           members={k: frozenset(v) for k, v in members.items()},
                           index_count={k: len(v) for k, v in members.items()}, source_sha="x" * 64)


SNAP = _snap({
    "885517.TI": ["A.SH", "B.SH", "C.SH", "S.SZ"],     # 机器人
    "885756.TI": ["A.SH", "D.SZ"],                      # 芯片
    "885900.TI": ["A.SH", "E.SZ"],                      # 与芯片并列
    "885338.TI": ["A.SH", "B.SH", "C.SH", "D.SZ", "Z.SZ"],  # 融资融券（排除）
    "885999.TI": ["X.BJ", "Y.BJ", "D.SZ"],
})


def test_picks_concept_with_most_sealed():
    counts = concept_counts(["A.SH", "B.SH", "C.SH", "D.SZ"], SNAP)
    assert assign("A.SH", counts, SNAP) == "885517.TI"


def test_tie_breaks_by_concept_code():
    counts = concept_counts(["D.SZ", "E.SZ"], SNAP)
    assert counts["885756.TI"] == counts["885900.TI"] == 1
    assert assign("A.SH", {"885756.TI": 1, "885900.TI": 1}, SNAP) == "885756.TI"


def test_all_excluded_goes_to_other():
    assert assign("Z.SZ", concept_counts(["Z.SZ"], SNAP), SNAP) == OTHER


def test_bj_not_counted_but_st_counted():
    counts = concept_counts(["X.BJ", "Y.BJ", "D.SZ", "S.SZ"], SNAP)  # S.SZ 视为 ST，照常计入
    assert counts["885999.TI"] == 1 and counts["885517.TI"] == 1


def test_non_concept_index_is_never_assigned():
    # 行业指数不在快照（build_snapshot 只收 type=N），反查到也无从选中
    assert assign("A.SH", {"881101.TI": 99, "885517.TI": 1}, SNAP) == "885517.TI"


def test_sticky_until_less_than_half():
    assert assign("A.SH", {"885756.TI": 4, "885517.TI": 8}, SNAP, previous="885756.TI") == "885756.TI"
    assert assign("A.SH", {"885756.TI": 3, "885517.TI": 8}, SNAP, previous="885756.TI") == "885517.TI"
