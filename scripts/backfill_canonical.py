#!/usr/bin/env python3
"""按分区、可续跑地回填 canonical 数据，并生成逐分区 manifest。"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import subprocess

from qsys.agent.locking import single_instance
from qsys.data.pipeline.canonical_backfill import (
    ManifestStore,
    backfill_daily,
    backfill_minutes,
    backfill_state_base,
    backfill_state_derived,
)
from qsys.data.pipeline.canonical_daily import build_share_capital_history
from qsys.data.pipeline.canonical_reference import (
    build_security_master,
    build_security_name_history,
    calendar_reconciliation,
    publish_trading_calendar,
    reference_reconciliation,
    require_calendar_coverage,
    require_reference_sources,
)
from qsys.data.providers.tushare import TushareProvider
from qsys.data.providers.tushare_client import TushareClient
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.live.config import load_tushare_config


def _revision() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def _bounds(query: WarehouseQuery, dataset: str) -> tuple[date, date]:
    value = query.dataset_bounds(dataset)
    if value is None:
        raise RuntimeError(f"{dataset} 没有可回填范围")
    return value


def _scope(
    source: tuple[date, date], start: date | None, end: date | None
) -> tuple[date, date]:
    lo = max(source[0], start) if start else source[0]
    hi = min(source[1], end) if end else source[1]
    if lo > hi:
        raise RuntimeError(f"请求范围与来源无交集：source={source}, request={start}..{end}")
    return lo, hi


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--config", type=Path, default=Path("config/deploy.local.toml"))
    parser.add_argument("--datasets", default="reference,daily,state,minute")
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument(
        "--calendar-end", type=date.fromisoformat,
        help="交易日历单独回填到该自然日；可晚于最新行情日",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--state-root", type=Path, default=Path("var/canonical-backfill"))
    parser.add_argument("--lock", type=Path, default=Path("var/warehouse-write.lock"),
                        help="与生产定时写任务共用的仓库写锁")
    parser.add_argument("--minimum-free-gib", type=float, default=10.0)
    args = parser.parse_args()
    selected = {item.strip() for item in args.datasets.split(",") if item.strip()}
    unknown = selected - {"reference", "daily", "state", "minute"}
    if unknown:
        parser.error(f"未知数据族：{sorted(unknown)}")

    warehouse = Warehouse(args.warehouse)
    query = WarehouseQuery(warehouse)
    revision = _revision()
    store = ManifestStore(
        args.state_root / revision[:8], warehouse, revision=revision,
        minimum_free_gib=args.minimum_free_gib,
    )
    report: dict[str, object] = {"revision": revision, "families": {}}

    with single_instance(args.lock):
        if "reference" in selected:
            source_lo, source_hi = _bounds(query, "tushare_daily")
            lo = max(source_lo, args.start) if args.start else source_lo
            hi = args.calendar_end or args.end or source_hi
            if lo > hi:
                raise RuntimeError(f"交易日历范围无效：{lo}..{hi}")
            require_reference_sources(warehouse)
            provider = TushareProvider(
                TushareClient(load_tushare_config(args.config).token)
            )
            static_start = static_end = source_hi
            static_counts: dict[str, int] = {}
            if not (args.resume and store.complete(
                "reference-static", static_start, static_end
            )):
                static_counts = {
                    "security_master": build_security_master(warehouse),
                    "security_name_history": build_security_name_history(warehouse),
                }
                static_diffs = reference_reconciliation(warehouse)
                store.write(
                    "reference-static", static_start, static_end,
                    counts=static_counts, reconciliations=static_diffs,
                    files=[
                        warehouse.path_for("security_master"),
                        warehouse.path_for("security_name_history"),
                    ],
                )
            calendar_rows = 0
            for year_lo, year_hi in (
                (max(lo, date(year, 1, 1)), min(hi, date(year, 12, 31)))
                for year in range(lo.year, hi.year + 1)
            ):
                if args.resume and store.complete("calendar", year_lo, year_hi):
                    continue
                store.require_space()
                raw = provider.trade_cal(year_lo, year_hi, exchange="SSE")
                require_calendar_coverage(raw, year_lo, year_hi)
                rows = publish_trading_calendar(warehouse, raw)
                diff = calendar_reconciliation(
                    raw,
                    WarehouseQuery(warehouse).trading_calendar(year_lo, year_hi),
                )
                store.write(
                    "calendar", year_lo, year_hi,
                    counts={"trading_calendar": rows},
                    reconciliations={"trading_calendar": diff},
                    files=[warehouse.path_for("trading_calendar", year_lo.year)],
                )
                calendar_rows += rows
            report["families"]["reference"] = {
                **static_counts, "trading_calendar": calendar_rows
            }

        daily_requested = _scope(
            _bounds(query, "tushare_daily"), args.start, args.end
        ) if "daily" in selected else None
        state_requested = _scope(
            _bounds(query, "price_limits_daily"), args.start, args.end
        ) if "state" in selected else None
        flags_requested = _scope(
            _bounds(query, "daily_flags"), args.start, args.end
        ) if "state" in selected else None

        if "daily" in selected or "state" in selected:
            lo, hi = daily_requested or _scope(
                _bounds(query, "tushare_daily"), args.start, args.end
            )
            if flags_requested is not None:
                # flags 自身看回 150 天，regime 还需要请求起点前 30 天 flags。
                support_lo = flags_requested[0] - timedelta(days=180)
                lo = max(_bounds(query, "tushare_daily")[0], min(lo, support_lo))
            counts = backfill_daily(
                warehouse, store, lo, hi, resume=args.resume
            )
            if "daily" in selected:
                counts["share_capital_history"] = build_share_capital_history(warehouse)
                report["families"]["daily"] = counts
            else:
                report["families"]["state_support_daily"] = counts

        if "state" in selected:
            assert state_requested is not None and flags_requested is not None
            lo, hi = state_requested
            support_lo = flags_requested[0] - timedelta(days=180)
            lo = max(_bounds(query, "price_limits_daily")[0], min(lo, support_lo))
            base = backfill_state_base(
                warehouse, store, lo, hi, resume=args.resume
            )
            flags_lo, flags_hi = flags_requested
            derived = backfill_state_derived(
                warehouse, store, flags_lo, flags_hi, resume=args.resume
            )
            report["families"]["state"] = {**base, **derived}

        if "minute" in selected:
            source = query.intraday_bounds()
            if source is None:
                raise RuntimeError("没有分钟来源可回填")
            lo, hi = _scope(source, args.start, args.end)
            report["families"]["minute"] = backfill_minutes(
                warehouse, store, lo, hi, resume=args.resume
            )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
