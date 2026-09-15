#!/usr/bin/env python
"""指标验证器：执行规则、导出人工清单、永久记录人工证据。"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

import polars as pl
import structlog

# 直接用 ``.venv/bin/python scripts/verify_metrics.py`` 时也必须加载当前
# checkout，而不是 editable install 可能指向的另一个 worktree。
_CHECKOUT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_CHECKOUT_SRC) not in sys.path:
    sys.path.insert(0, str(_CHECKOUT_SRC))

from qsys.data import schema
from qsys.data.catalog import REGISTRY
from qsys.data.catalog import checks as C
from qsys.data.catalog import rules_truth
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)
_VERDICTS = ("match", "mismatch", "inconclusive")


def record_evidence(
    warehouse: Warehouse,
    *,
    metric_id: str,
    symbol: str,
    dt: date,
    our_value: str,
    reference_value: str,
    reference_source: str,
    verdict: str,
    checked_by: str,
    note: str = "",
) -> int:
    """把人工结论追加到对应年份；自动验证不会覆盖这张表。"""
    if metric_id not in REGISTRY:
        raise ValueError(f"未登记的指标 id：{metric_id}")
    if verdict not in _VERDICTS:
        raise ValueError(f"verdict 必须是 {_VERDICTS} 之一")
    row = pl.DataFrame([{
        "metric_id": metric_id,
        "symbol": symbol,
        "dt": dt,
        "our_value": our_value,
        "reference_value": reference_value,
        "reference_source": reference_source,
        "verdict": verdict,
        "checked_by": checked_by,
        "checked_at": datetime.now(),
        "note": note,
    }]).select(schema.METRIC_EVIDENCE.names)
    existing = WarehouseQuery(warehouse).metric_evidence().filter(
        pl.col("dt").dt.year() == dt.year
    )
    merged = pl.concat([existing, row], how="vertical")
    warehouse.write(
        "metric_evidence",
        merged.to_arrow().cast(schema.METRIC_EVIDENCE),
        year=dt.year,
    )
    return merged.height


def _year_spans(start: date, end: date) -> list[tuple[date, date]]:
    return [
        (max(start, date(year, 1, 1)), min(end, date(year, 12, 31)))
        for year in range(start.year, end.year + 1)
    ]


def _run(args: argparse.Namespace) -> int:
    warehouse = Warehouse(Path(args.warehouse))
    scope = C.Scope(args.scope)
    started = datetime.now()
    run_id = started.strftime("%Y%m%dT%H%M%S%f")
    spans = (
        [(args.start, args.end)]
        if scope is C.Scope.INCREMENTAL
        else _year_spans(args.start, args.end)
    )
    total = 0
    for lo, hi in spans:
        ctx = C.CheckContext(WarehouseQuery(warehouse), warehouse, lo, hi)
        frame = C.run_checks(
            ctx,
            run_id,
            datetime.now(),
            scope=scope,
            artifacts=Path(args.artifacts),
        )
        total += C.write_results(warehouse, frame)
        failed = frame.filter(~pl.col("passed"))
        blocking = failed.filter(pl.col("severity") == "blocking")
        log.info(
            "verify.span_done",
            start=str(lo),
            end=str(hi),
            rows=frame.height,
            failed=failed.height,
            blocking=blocking.height,
        )
    log.info("verify.done", run_id=run_id, rows=total)
    return 0


def _worklist(args: argparse.Namespace) -> int:
    warehouse = Warehouse(Path(args.warehouse))
    ctx = C.CheckContext(
        WarehouseQuery(warehouse), warehouse, args.start, args.end
    )
    work = rules_truth.sampling_worklist(ctx, limit=args.limit)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    work.write_csv(out)
    print(f"{work.height} 条待验清单 -> {out}")
    return 0


def _record(args: argparse.Namespace) -> int:
    count = record_evidence(
        Warehouse(Path(args.warehouse)),
        metric_id=args.metric,
        symbol=args.symbol,
        dt=args.dt,
        our_value=args.ours,
        reference_value=args.theirs,
        reference_source=args.source,
        verdict=args.verdict,
        checked_by=args.by,
        note=args.note,
    )
    print(f"已录入，该年累计 {count} 条证据")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="指标验证器")
    parser.add_argument("--warehouse", default="warehouse")
    commands = parser.add_subparsers(dest="cmd", required=True)

    run = commands.add_parser("run", help="跑规则并落盘")
    run.add_argument("--warehouse", default=argparse.SUPPRESS)
    run.add_argument(
        "--scope", choices=[scope.value for scope in C.Scope], default="incremental"
    )
    run.add_argument("--start", type=date.fromisoformat, required=True)
    run.add_argument("--end", type=date.fromisoformat, required=True)
    run.add_argument("--artifacts", default="artifacts/metric_checks")
    run.set_defaults(func=_run)

    worklist = commands.add_parser("worklist", help="导出待人工验真清单")
    worklist.add_argument("--warehouse", default=argparse.SUPPRESS)
    worklist.add_argument("--start", type=date.fromisoformat, required=True)
    worklist.add_argument("--end", type=date.fromisoformat, required=True)
    worklist.add_argument("--limit", type=int, default=200)
    worklist.add_argument("--out", default="artifacts/sampling_worklist.csv")
    worklist.set_defaults(func=_worklist)

    record = commands.add_parser("record", help="录入一条人工结论")
    record.add_argument("--warehouse", default=argparse.SUPPRESS)
    record.add_argument("--metric", required=True)
    record.add_argument("--symbol", required=True)
    record.add_argument("--dt", type=date.fromisoformat, required=True)
    record.add_argument("--ours", required=True)
    record.add_argument("--theirs", required=True)
    record.add_argument("--source", required=True)
    record.add_argument("--verdict", choices=_VERDICTS, required=True)
    record.add_argument("--by", required=True)
    record.add_argument("--note", default="")
    record.set_defaults(func=_record)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if hasattr(args, "start") and args.start > args.end:
        raise SystemExit("--start 不得晚于 --end")
    if hasattr(args, "limit") and args.limit < 1:
        raise SystemExit("--limit 必须大于 0")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
