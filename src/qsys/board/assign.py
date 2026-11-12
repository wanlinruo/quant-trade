"""题材归列（#648）：每只票只放一列，归入其所属概念中当日封过板家数最多者。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping

from qsys.board.concepts import ConceptSnapshot

OTHER = "其他"


def concept_counts(sealed_today: Iterable[str], snapshot: ConceptSnapshot) -> dict[str, int]:
    """当日截至此刻封过板的家数（只增不减由调用方传入累计集合保证）；北交所不计。"""
    counts: Counter[str] = Counter()
    for symbol in set(sealed_today):
        if symbol.endswith(".BJ"):
            continue
        counts.update(snapshot.concepts_of(symbol))
    return dict(counts)


def assign(symbol: str, counts: Mapping[str, int], snapshot: ConceptSnapshot,
           previous: str | None = None) -> str:
    own = snapshot.concepts_of(symbol)
    if not own:
        return OTHER
    best = min(own, key=lambda code: (-counts.get(code, 0), code))
    if previous and previous != OTHER and previous in own and previous != best:
        if counts.get(previous, 0) * 2 >= counts.get(best, 0):
            return previous
    return best
