"""Bind quality evidence to physical input versions without reading market values.

The metadata proves applicability to unchanged local files, not data correctness.
External responses are captured by digest; their freshness still depends on TTL
and the next real verification. All paths are derived from known dataset names.
"""
from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
import re
from pathlib import Path

from qsys.data import schema


class InputVersionChanged(RuntimeError):
    """Inputs changed while evidence was being calculated; do not publish it."""


# These are verifier read sets, not the builders' transitive dependencies.
SOURCES = {
    "security_master": ("instruments",),
    "security_name_history": ("name_history",),
    "trading_calendar": (),
    "bar_daily": ("tushare_daily",),
    "valuation_daily": ("tushare_daily",),
    "share_capital_history": ("tushare_daily",),
    "adjustment_factor_daily": ("adj_factors",),
    "price_limit_daily": ("price_limits_daily",),
    "security_status_daily": ("bar_daily", "stock_st_daily", "suspend_daily", "delisting_pool"),
    "market_flags_daily": ("daily_flags",),
    "market_regime_daily": ("market_regime",),
    "bar_minute_1m": ("paid_minute_bars", "minute_bars", "tushare_minute_bars",
                       "joinquant_minute_bars", "tushare_daily", "trading_calendar"),
}


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _files(root: Path, dataset: str, start: date, end: date) -> list[Path]:
    base = root / dataset
    if dataset == "minute_bars":
        # Keep exactly the query's daily-over-annual selection, including the
        # appearance of a first daily partition that shadows an annual file.
        from qsys.data.query import minute_partition_files
        return [Path(p) for p in minute_partition_files(base, start, end)]
    if dataset in {"tushare_minute_bars", "joinquant_minute_bars"}:
        files = []
        for year in range(start.year, end.year + 1):
            for p in (base / f"year={year}").glob("date=*/part.parquet"):
                if start <= datetime.strptime(p.parent.name.split('=', 1)[1], '%Y%m%d').date() <= end:
                    files.append(p)
        return sorted(files)
    if dataset == "paid_minute_bars":
        # The real reader scans all shards/day files in each selected year;
        # its manifest also controls availability and source selection.
        return sorted(p for year in range(start.year, end.year + 1)
                      for pattern in ("shard=*/part.parquet", "date=*/part.parquet", "manifest.json")
                      for p in (base / f"year={year}").glob(pattern))
    # Canonical minute freshness uses all published partitions, so include them
    # as well as the selected day's values. This does not open their contents.
    if dataset == "bar_minute_1m":
        return sorted(base.rglob("*.parquet"))
    return sorted(base.glob("year=*/part.parquet" if dataset in schema.PARTITIONED else "part.parquet"))


def _snapshot(root: Path, datasets: list[str], start: date, end: date,
              freshness: list[str], external_digest: str | None) -> str:
    rows = []
    for dataset in datasets:
        for p in _files(root, dataset, start, end):
            s = p.stat()
            rows.append([p.relative_to(root).as_posix(), s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns])
    freshness_rows = []
    for dataset in freshness:
        for p in sorted((root / dataset).rglob("*.parquet")):
            info = p.stat()
            freshness_rows.append([p.relative_to(root).as_posix(), info.st_dev, info.st_ino,
                                   info.st_size, info.st_mtime_ns])
    return hashlib.sha256(_json([rows, freshness_rows, external_digest]).encode()).hexdigest()


def bind_inputs(warehouse, datasets: tuple[str, ...], start: date, end: date,
                *, external_digest: str | None = None,
                freshness: tuple[str, ...] = ()) -> str:
    if start > end or not datasets or any(d not in schema.ALL | schema.READ_ONLY for d in datasets):
        raise ValueError("invalid quality input selection")
    if any(d not in datasets for d in freshness):
        raise ValueError("freshness inputs must be included in the read set")
    selected = sorted(set(datasets))
    fresh = sorted(set(freshness))
    return _json({"version": 1, "datasets": selected, "start": start.isoformat(),
                  "end": end.isoformat(), "external_digest": external_digest, "freshness": fresh,
                  "fingerprint": _snapshot(warehouse.root, selected, start, end, fresh, external_digest)})


def dataset_binding(warehouse, dataset: str, start: date, end: date,
                    *, external_digest: str | None = None) -> str:
    return bind_inputs(warehouse, (dataset, *SOURCES[dataset]), start, end,
                       external_digest=external_digest, freshness=(dataset,))


def binding_status(warehouse, value: str | None) -> str | None:
    """None means the binding is valid; errors fail closed with stable messages."""
    if value is None:
        return "历史证据未绑定输入版本，待重验"
    if not isinstance(value, str) or len(value) > 65536:
        return "证据版本元数据无效，待重验"
    try:
        meta = json.loads(value)
        if not isinstance(meta, dict) or type(meta.get("version")) is not int or meta.get("version") != 1:
            return "证据版本格式未知，待重验"
        datasets = meta["datasets"]
        if (not isinstance(datasets, list) or not datasets or len(datasets) > 32
                or any(not isinstance(d, str) or d not in schema.ALL | schema.READ_ONLY for d in datasets)):
            return "证据输入范围无效，待重验"
        start, end = date.fromisoformat(meta["start"]), date.fromisoformat(meta["end"])
        if start > end or end.year - start.year > 200:
            return "证据日期范围无效，待重验"
        fresh = meta["freshness"]
        external = meta["external_digest"]
        if (not isinstance(fresh, list) or len(fresh) > 32
                or any(not isinstance(d, str) or d not in datasets for d in fresh)
                or (external is not None and (not isinstance(external, str)
                    or re.fullmatch(r"[0-9a-f]{64}", external) is None))):
            return "证据版本元数据无效，待重验"
        if _snapshot(warehouse.root, datasets, start, end, fresh, external) != meta["fingerprint"]:
            return "输入已变化，待重验；历史检查结论保留"
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        return "输入版本无法核验，待重验"
    return None


def require_unchanged(warehouse, bindings: list[str]) -> None:
    if any(binding_status(warehouse, value) is not None for value in bindings):
        raise InputVersionChanged("quality inputs changed during verification")


def current_result(warehouse, row: dict, *, cache: dict | None = None) -> dict:
    """Return a current interpretation without mutating the persisted history."""
    value = row.get("input_version")
    if cache is None:
        reason = binding_status(warehouse, value)
    else:
        if value not in cache:
            cache[value] = binding_status(warehouse, value)
        reason = cache[value]
    if reason is None:
        try:
            meta = json.loads(value)
            dataset = row.get("dataset_id")
            expected = ({dataset, *SOURCES[dataset]} if dataset in SOURCES
                        else {row["upstream"], row["downstream"]})
            fresh_expected = {dataset} if dataset in SOURCES else expected
            if (set(meta["freshness"]) != fresh_expected
                    or (dataset == "trading_calendar" and meta["external_digest"] is None)
                    or set(meta["datasets"]) != expected) or any(
                str(row[key]) != meta[name]
                for key, name in (("scope_start", "start"), ("scope_end", "end"))
            ):
                reason = "证据绑定对象或范围不符，待重验"
        except (ValueError, TypeError, KeyError):
            reason = "证据绑定对象无法核验，待重验"
    if reason is None:
        return row
    return {**row, "status": "gray", "detail": reason}
