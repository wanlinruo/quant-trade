from datetime import date, datetime
import re
from pathlib import Path

import polars as pl

from qsys.data.board.concepts import EXCLUDED_CONCEPTS, build_snapshot

DAY = date(2026, 11, 12)
FETCHED = datetime(2026, 11, 12, 8, 50)


def _index():
    return pl.DataFrame({
        "ts_code": ["885338.TI", "885517.TI", "881101.TI"],
        "name": ["融资融券", "机器人概念", "某行业"],
        "count": [3844, 1216, 50],
        "exchange": ["A", "A", "A"],
        "list_date": ["20100331", "20150101", "20000101"],
        "type": ["N", "N", "I"],
    })


def _members(code, symbols):
    return pl.DataFrame({"ts_code": [code] * len(symbols), "con_code": symbols,
                         "con_name": ["x"] * len(symbols)})


MEMBERS = {
    "885338.TI": _members("885338.TI", ["600000.SH", "000001.SZ", "300750.SZ"]),
    "885517.TI": _members("885517.TI", ["600000.SH"]),
    "881101.TI": _members("881101.TI", ["600000.SH"]),
}


def test_excluded_concepts_match_probe_doc():
    doc = Path(__file__).resolve().parents[2] / "docs/reference/ths-concept-probe.md"
    assert EXCLUDED_CONCEPTS == frozenset(re.findall(r"\| `(\d{6}\.TI)` \|", doc.read_text()))
    assert len(EXCLUDED_CONCEPTS) == 32
    assert {"885338.TI", "883300.TI", "885840.TI", "885705.TI"} <= EXCLUDED_CONCEPTS
    assert "885517.TI" not in EXCLUDED_CONCEPTS


def test_concepts_of_drops_excluded_and_non_concept_and_keeps_count_mismatch():
    snap = build_snapshot(DAY, _index(), MEMBERS, FETCHED)
    assert snap.concepts_of("600000.SH") == {"885517.TI"}
    assert snap.concepts_of("000001.SZ") == frozenset()
    assert snap.index_count["885338.TI"] == 3844 and len(snap.members["885338.TI"]) == 3
    assert "881101.TI" not in snap.members


def test_snapshot_sha_is_order_independent():
    a = build_snapshot(DAY, _index(), MEMBERS, FETCHED)
    b = build_snapshot(DAY, _index().reverse(),
                       {k: v.reverse() for k, v in reversed(list(MEMBERS.items()))}, FETCHED)
    assert a.source_sha == b.source_sha and len(a.source_sha) == 64
