from datetime import date

import polars as pl
import pytest

from qsys.data.pipeline.paid_daily_repair import apply_accepted_repairs


D = date(1992, 8, 3)
HASH_A = "a" * 64
HASH_B = "b" * 64


def _frame() -> pl.DataFrame:
    return pl.DataFrame({"symbol": ["000001.SZ"], "dt": [D], "open": [46.6], "amount": [None]},
                        schema={"symbol": pl.String, "dt": pl.Date, "open": pl.Float64, "amount": pl.Float64})


def _decision(**updates) -> pl.DataFrame:
    row = {
        "repair_id": "r1", "batch_id": "b1", "symbol": "000001.SZ", "dt": D,
        "field": "open", "original_value": 46.6, "repaired_value": 45.6,
        "source_a_family": "baostock", "source_a_value": 45.6,
        "source_a_evidence_sha256": HASH_A, "source_b_family": "tencent",
        "source_b_value": 45.6, "source_b_evidence_sha256": HASH_B,
        "decision": "ACCEPTED", "reason": "two direct prices agree", "rule_version": "1",
    }
    row.update(updates)
    return pl.DataFrame([row])


def test_applies_accepted_direct_value_without_mutating_other_fields():
    result = apply_accepted_repairs(_frame(), _decision())
    assert result.row(0, named=True) == {"symbol": "000001.SZ", "dt": D, "open": 45.6, "amount": None}


def test_rejects_two_wrappers_from_same_source_family():
    with pytest.raises(ValueError, match="independent"):
        apply_accepted_repairs(_frame(), _decision(source_b_family="baostock"))


def test_rejects_computed_or_averaged_repair_value():
    with pytest.raises(ValueError, match="primary-source"):
        apply_accepted_repairs(_frame(), _decision(repaired_value=45.61))


def test_refuses_to_overlay_when_raw_value_changed():
    with pytest.raises(ValueError, match="raw value changed"):
        apply_accepted_repairs(_frame().with_columns(pl.lit(46.7).alias("open")), _decision())


def test_nonaccepted_decision_does_not_change_data():
    result = apply_accepted_repairs(_frame(), _decision(decision="CONFLICT"))
    assert result.equals(_frame())
