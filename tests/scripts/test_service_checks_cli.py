from scripts.service_checks import _exit_code


def test_strategy_failure_does_not_fail_data_quality_unit():
    assert _exit_code({"data": 0, "strategy": 2}) == 0


def test_data_failure_fails_data_quality_unit():
    assert _exit_code({"data": 1, "strategy": 0}) == 1
