import pytest

from qsys.strategy.parameters import parameter_specs, validate_parameters


SOURCE = '''
PARAMETERS = {
    "window": {"type": "int", "default": 20, "min": 2, "max": 60},
    "enabled": {"type": "bool", "default": True},
    "mode": {"type": "str", "default": "fast", "choices": ["fast", "slow"]},
}
'''


def test_parameter_schema_is_read_without_executing_source():
    specs = parameter_specs(SOURCE + "\nraise RuntimeError('must not execute')\n")
    assert [s.name for s in specs] == ["window", "enabled", "mode"]


def test_parameters_are_typed_and_defaults_are_merged():
    got = validate_parameters(parameter_specs(SOURCE), {"window": "30", "enabled": "false"})
    assert got == {"window": 30, "enabled": False, "mode": "fast"}


@pytest.mark.parametrize("values", [{"window": 1}, {"window": 100}, {"mode": "other"},
                                     {"unknown": 1}])
def test_invalid_declared_parameters_are_rejected(values):
    with pytest.raises(ValueError):
        validate_parameters(parameter_specs(SOURCE), values)


def test_undeclared_strategy_keeps_free_form_values():
    assert validate_parameters((), {"anything": "goes"}) == {"anything": "goes"}


def test_parameters_must_be_a_literal_and_never_a_function_call():
    with pytest.raises(ValueError):
        parameter_specs("PARAMETERS = make_parameters()")
