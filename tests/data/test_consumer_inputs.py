from datetime import date, datetime, timedelta
import json

import pytest
from pydantic import ValidationError

from qsys.data.quality.consumer_inputs import (
    ConsumerInputs, InputEvidence, InputScope, assess_consumer_inputs, main,
)

NOW = datetime(2026, 11, 3, 18)


def need(name="sentiment", **changes):
    return InputScope(name=name, dataset="market_regime", frequency="daily", universe="all_a",
                      fields=["market_advance_ratio"], symbols=["600001.SH", "920026.BJ"],
                      start=date(2026, 11, 2), end=date(2026, 11, 2),
                      input_version="a" * 64).model_copy(update=changes)


def evidence(scope=None, **changes):
    return InputEvidence(**(scope or need()).model_dump(), status="passed", run_id="actual-run",
                         checked_at=NOW - timedelta(minutes=1),
                         expires_at=NOW + timedelta(minutes=1)).model_copy(update=changes)


def assess(records, requirements=None):
    return assess_consumer_inputs(ConsumerInputs(consumer="fixed-scope-review",
                                 requirements=requirements or [need()], evidence=records, now=NOW))


def test_unconsumed_archive_failure_is_preserved_without_becoming_required_sentiment():
    archive = evidence(need("bj-minute", dataset="minute_bars", frequency="1m", universe="archive"),
                       status="failed")
    result = assess([evidence(), archive])
    assert result["inputs_ready"] is True
    assert result["observed_quality_failed"] is True
    assert result["outside_contract"][0]["status"] == "failed"
    assert result["authorizes_trading"] is False


@pytest.mark.parametrize("change,reason", [
    ({"symbols": ["600001.SH"]}, "scope_mismatch"),
    ({"universe": "trading"}, "scope_mismatch"),
    ({"dataset": "minute_bars"}, "scope_mismatch"),
    ({"frequency": "1m"}, "scope_mismatch"),
    ({"fields": ["close"]}, "scope_mismatch"),
    ({"start": date(2026, 11, 3), "end": date(2026, 11, 3)}, "scope_mismatch"),
    ({"input_version": None}, "unbound_input_version"),
    ({"input_version": "b" * 64}, "input_version_changed"),
    ({"expires_at": NOW}, "evidence_not_current"),
    ({"checked_at": NOW + timedelta(seconds=1)}, "evidence_not_current"),
    ({"status": "failed"}, "required_failed"),
    ({"status": "warning"}, "required_warning"),
    ({"status": "unknown"}, "required_unknown"),
])
def test_required_input_cannot_be_cleared_by_omission_scope_or_version_change(change, reason):
    result = assess([evidence(**change)])
    assert result["inputs_ready"] is False
    assert reason in result["required_inputs"][0]["reasons"]


def test_missing_or_ambiguous_evidence_is_not_defaulted_to_success():
    assert assess([])["required_inputs"][0]["reasons"] == ["missing_evidence"]
    assert assess([evidence(), evidence()])["required_inputs"][0]["reasons"] == ["ambiguous_evidence"]
    assert assess([evidence()], [need(input_version=None)])["inputs_ready"] is False


def test_cli_keeps_unknown_nonzero_and_redacts_invalid_input(tmp_path, capsys):
    p = tmp_path / "input.json"
    p.write_text(ConsumerInputs(consumer="review", requirements=[need()], evidence=[], now=NOW).model_dump_json())
    assert main([str(p)]) == 1
    assert json.loads(capsys.readouterr().out)["inputs_ready"] is False
    p.write_text('{"private_token":"do-not-print"}')
    assert main([str(p)]) == 2
    assert "do-not-print" not in capsys.readouterr().out


def test_empty_contract_is_not_vacuously_ready():
    with pytest.raises(ValidationError):
        ConsumerInputs(consumer="review", requirements=[], evidence=[], now=NOW)
