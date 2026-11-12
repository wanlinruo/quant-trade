"""同花顺概念快照与泛概念排除名单（#648）。

探针结论见 docs/reference/ths-concept-probe.md：ths_member 无生效/剔除日期，只能按日
留存拉取当时的成分；ths_index.count 与实际成分行数可能不一致，两者并存、不互相替代；
按股票反查会混入行业/地域等非概念指数，只认 ths_index(type=N) 的代码集合。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

import polars as pl

# 维护者 2026-11-12 确认的 32 个泛概念（按代码，不按名称）。
EXCLUDED_CONCEPTS: frozenset[str] = frozenset({
    "885338.TI", "885694.TI", "885520.TI", "883300.TI", "883304.TI", "883303.TI",
    "883302.TI", "883301.TI", "885916.TI", "886045.TI", "886075.TI", "886096.TI",
    "886082.TI", "886110.TI", "886109.TI", "885699.TI", "885598.TI", "885905.TI",
    "885907.TI", "885663.TI", "886021.TI", "885595.TI", "885929.TI", "886072.TI",
    "885494.TI", "886086.TI", "885705.TI", "885620.TI", "885835.TI", "885623.TI",
    "885761.TI", "885840.TI",
})


@dataclass(frozen=True)
class ConceptSnapshot:
    day: date
    fetched_at: datetime
    members: Mapping[str, frozenset[str]]
    index_count: Mapping[str, int]
    source_sha: str

    def concepts_of(self, symbol: str) -> frozenset[str]:
        return frozenset(code for code, symbols in self.members.items()
                         if symbol in symbols and code not in EXCLUDED_CONCEPTS)


def build_snapshot(day: date, index: pl.DataFrame, members: Mapping[str, pl.DataFrame],
                   fetched_at: datetime) -> ConceptSnapshot:
    concepts = index.filter(pl.col("type") == "N")
    codes = set(concepts["ts_code"].to_list())
    member_sets = {
        code: frozenset(frame["con_code"].to_list())
        for code, frame in members.items() if code in codes
    }
    counts = dict(zip(concepts["ts_code"].to_list(), concepts["count"].to_list(), strict=True))
    canonical = {
        "index": sorted(json.dumps(row, sort_keys=True, default=str)
                        for row in concepts.to_dicts()),
        "members": {code: sorted(syms) for code, syms in sorted(member_sets.items())},
    }
    sha = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()
    return ConceptSnapshot(day=day, fetched_at=fetched_at, members=member_sets,
                           index_count=counts, source_sha=sha)
