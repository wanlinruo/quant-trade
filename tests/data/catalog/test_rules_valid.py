"""C 类规则：填充红线、时点连接、区间、冻结表与凭据期限。"""

from datetime import date, datetime
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
import pytest

from qsys.data import schema
from qsys.data.catalog import checks as C
from qsys.data.catalog import rules_valid
from qsys.data.query import WarehouseQuery

D = [date(2026, 9, day) for day in (10, 11)]
NOW = datetime(2026, 9, 15, 19, 30)


@pytest.fixture
def ctx(warehouse):
    return C.CheckContext(WarehouseQuery(warehouse), warehouse, D[0], D[-1])


def _run(cid, ctx):
    artifacts = ctx.warehouse.root / "check_samples"
    return C.run_checks(
        ctx, "t", NOW, only=[C.CHECKS[cid]], artifacts=artifacts
    ).row(0, named=True)


def test_fill_scan_finds_an_unregistered_default(tmp_path):
    bad = tmp_path / "leaky.py"
    bad.write_text("import polars as pl\nx = pl.col('a').fill_null(1.0)\n")
    found = rules_valid.scan_fills([tmp_path])
    assert len(found) == 1
    assert found[0].line_no == 2


def test_fill_scan_accepts_a_registered_exemption(tmp_path):
    src = "x = frame.filter(~pl.col('is_st').fill_null(False))\n"
    path = tmp_path / "ok.py"
    path.write_text(src)
    exempt = {(str(path), src.strip()): "保守判定，只会多报"}
    assert rules_valid.scan_fills([tmp_path], exemptions=exempt) == []


def test_fill_scan_ignores_comments_and_docstrings(tmp_path):
    path = tmp_path / "docs.py"
    path.write_text('"""不要写 or 0 或 .fill_null(0)。"""\n# x.fillna(0)\nx = 1\n')
    assert rules_valid.scan_fills([tmp_path]) == []


def test_feed_no_longer_fills_the_factor():
    """D-9 结案后，feed.py 不得再次出现未登记填充。"""
    sites = rules_valid.scan_fills([Path("src/qsys/data")])
    assert not [site for site in sites if "feed.py" in site.path]


def test_pit_join_catches_snapshot_used_off_observation_day(ctx, warehouse):
    warehouse.write("name_snapshots", pl.DataFrame({
        "dt": [D[0]], "symbol": ["600036.SH"], "name": ["招商银行"],
    }).to_arrow().cast(schema.NAME_SNAPSHOTS), year=2026)
    flags = pl.DataFrame({
        "dt": [D[1]], "symbol": ["600036.SH"], "st_source": ["name_snapshot"],
    })
    bools = {"is_limit_up", "is_limit_down", "touched_limit_up",
             "is_broken_board", "is_first_board", "is_st"}
    for name in schema.DAILY_FLAGS.names:
        if name not in flags.columns:
            flags = flags.with_columns(pl.lit(
                None, dtype=pl.Boolean if name in bools else pl.Float64
            ).alias(name))
    warehouse.write("daily_flags", flags.select(schema.DAILY_FLAGS.names)
                    .to_arrow().cast(schema.DAILY_FLAGS), year=2026)
    assert _run("C2.pit_join", ctx)["violations"] == 1


def test_span_gaps_finds_interval_without_successor(ctx, warehouse):
    warehouse.write("name_history", pl.DataFrame({
        "symbol": ["000006.SZ"], "name": ["深振业A"],
        "start_date": [date(2006, 1, 1)], "end_date": [date(2006, 10, 8)],
        "ann_date": [date(2006, 1, 1)], "change_reason": ["股改"],
    }).to_arrow().cast(schema.NAME_HISTORY))
    row = _run("C3.span_gaps", ctx)
    assert row["violations"] == 1
    assert row["known_issue"] == "D-10"


def _write_frozen(root: Path, dataset: str, value: float) -> None:
    path = root / dataset / "year=2024" / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pl.DataFrame({"value": [value]}).to_arrow(), path)


def test_frozen_fingerprint_initializes_then_detects_change(ctx, monkeypatch, tmp_path):
    baseline = tmp_path / "frozen.json"
    monkeypatch.setattr(rules_valid, "_FROZEN_BASELINE", baseline)
    _write_frozen(ctx.warehouse.root, "paid_daily", 1.0)
    _write_frozen(ctx.warehouse.root, "bars_daily", 2.0)
    assert _run("C4.frozen_unchanged", ctx)["passed"] is True
    assert baseline.exists()
    _write_frozen(ctx.warehouse.root, "bars_daily", 3.0)
    row = _run("C4.frozen_unchanged", ctx)
    assert row["violations"] == 1
    assert row["passed"] is False


def test_credential_expiry_has_a_nonzero_denominator(ctx):
    assert _run("C5.credential_expiry", ctx)["checked_rows"] > 0


def test_all_c_rules_are_validity_category():
    assert {f"C{i}" for i in range(1, 6)} == {
        cid.split(".")[0] for cid in C.CHECKS if cid.startswith("C")
    }
    assert all(check.category is C.Category.VALIDITY
               for cid, check in C.CHECKS.items() if cid.startswith("C"))
