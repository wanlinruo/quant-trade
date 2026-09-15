"""A4 抽样闭环：系统出清单，人核验，结论永久入库。"""

from datetime import date, datetime

import polars as pl
import pytest

from qsys.data import schema
from qsys.data.catalog import checks as C
from qsys.data.catalog import rules_truth
from qsys.data.query import WarehouseQuery

D = [date(2026, 9, day) for day in (10, 11)]
NOW = datetime(2026, 9, 15, 19, 30)


@pytest.fixture
def ctx(warehouse):
    return C.CheckContext(WarehouseQuery(warehouse), warehouse, D[0], D[-1])


def _daily(rows, warehouse):
    frame = pl.DataFrame(rows)
    missing = [name for name in schema.TUSHARE_DAILY.names if name not in frame.columns]
    frame = frame.with_columns(
        [pl.lit(None, dtype=pl.Utf8).alias(name) for name in missing if name == "name"]
        + [pl.lit(None, dtype=pl.Float64).alias(name)
           for name in missing if name != "name"]
    ).select(schema.TUSHARE_DAILY.names)
    warehouse.write(
        "tushare_daily", frame.to_arrow().cast(schema.TUSHARE_DAILY), year=2026
    )


def _seed_suspicious_daily(warehouse):
    _daily([
        {"symbol": "000430.SZ", "dt": D[0], "close": 10.0,
         "pre_close": 10.0, "factor": 2.0},
        {"symbol": "000430.SZ", "dt": D[1], "close": 8.0,
         "pre_close": 8.0, "factor": 2.0},
    ], warehouse)


def test_worklist_is_sorted_by_suspicion_not_symbol(ctx, warehouse):
    _seed_suspicious_daily(warehouse)
    work = rules_truth.sampling_worklist(ctx)
    assert work.height >= 1
    assert work["priority"].to_list() == sorted(
        work["priority"].to_list(), reverse=True
    )
    assert {"metric_id", "symbol", "dt", "our_value", "why"} <= set(work.columns)


def test_worklist_says_why_each_row_is_suspect(ctx, warehouse):
    _seed_suspicious_daily(warehouse)
    assert all(rules_truth.sampling_worklist(ctx)["why"].to_list())


def test_empty_worklist_preserves_export_schema(ctx):
    work = rules_truth.sampling_worklist(ctx)
    assert work.columns == [
        "metric_id", "symbol", "dt", "our_value", "why", "priority"
    ]


def test_recorded_evidence_survives_later_verification_run(warehouse):
    from scripts.verify_metrics import record_evidence

    record_evidence(
        warehouse,
        metric_id="adj_factors.cum_factor",
        symbol="001331.SZ",
        dt=date(2026, 6, 30),
        our_value="3.4848",
        reference_value="2.4011",
        reference_source="同花顺",
        verdict="mismatch",
        checked_by="wanlinruo",
        note="D-1 抽查",
    )
    C.write_results(
        warehouse,
        C.run_checks(
            C.CheckContext(WarehouseQuery(warehouse), warehouse, D[0], D[1]),
            "r1",
            NOW,
            only=[C.Check(
                "X", C.Category.TRUTHFULNESS, C.Scope.INCREMENTAL,
                ("adj_factors.cum_factor",), (), "",
                lambda context: C.CheckResult(0, 1),
            )],
            artifacts=warehouse.root / "samples",
        ),
    )
    evidence = WarehouseQuery(warehouse).metric_evidence("adj_factors.cum_factor")
    assert evidence.height == 1
    assert evidence["verdict"][0] == "mismatch"


def test_recording_twice_appends_rather_than_replaces(warehouse):
    from scripts.verify_metrics import record_evidence

    for source in ("同花顺", "东财"):
        record_evidence(
            warehouse,
            metric_id="adj_factors.cum_factor",
            symbol="001331.SZ",
            dt=date(2026, 6, 30),
            our_value="3.4848",
            reference_value="2.4011",
            reference_source=source,
            verdict="mismatch",
            checked_by="wanlinruo",
        )
    assert WarehouseQuery(warehouse).metric_evidence().height == 2


def test_full_scope_is_split_at_year_boundaries():
    from scripts.verify_metrics import _year_spans

    assert _year_spans(date(2024, 12, 30), date(2026, 1, 2)) == [
        (date(2024, 12, 30), date(2024, 12, 31)),
        (date(2025, 1, 1), date(2025, 12, 31)),
        (date(2026, 1, 1), date(2026, 1, 2)),
    ]


def test_warehouse_option_is_accepted_after_the_subcommand():
    """部署文档中的自然写法是 ``run --warehouse ...``。"""
    from scripts.verify_metrics import _parser

    args = _parser().parse_args([
        "run", "--warehouse", "/data/qsys", "--start", "2026-09-11",
        "--end", "2026-09-11",
    ])
    assert args.warehouse == "/data/qsys"
