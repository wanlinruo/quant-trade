from pathlib import Path

import pytest
from scripts import verify_component_rollback_preproduction as pilot

SHA = "a" * 40


def test_local_or_node_execution_is_refused():
    with pytest.raises(ValueError, match="GitHub-hosted"):
        pilot.validate_host({})


def test_releases_are_parsed_per_component():
    assert pilot.parse_releases([f"web={SHA}", f"service-checks={'b' * 40}"]) == {
        "web": SHA, "service-checks": "b" * 40,
    }


@pytest.mark.parametrize("values", [
    [], [f"storage-report={SHA}"], ["web=abc"], [f"web={SHA}", f"web={'b' * 40}"],
    [f"web={SHA.upper()}"],
])
def test_invalid_releases_are_refused(values):
    with pytest.raises(ValueError, match="releases|at least one"):
        pilot.parse_releases(values)


def test_exec_path_reads_systemd_show_value():
    value = "{ path=/opt/qsys/components/web/current/venv/bin/qsys-web ; argv[]=x ; ignore_errors=no }"
    assert pilot.exec_path(value) == "/opt/qsys/components/web/current/venv/bin/qsys-web"
    with pytest.raises(ValueError, match="ExecStart"):
        pilot.exec_path("")


def test_dropin_value_takes_last_assignment():
    spec = {"dropin_lines": ["[Service]", "ExecStart=", "ExecStart=/x", "User=qsys-web"]}
    assert pilot.dropin_value(spec, "ExecStart") == "/x"
    assert pilot.dropin_value(spec, "Group") == ""


def test_covered_components_have_isolated_specs():
    from scripts.component_release_config import load_component_releases
    specs = load_component_releases(Path("."))
    for component in pilot.COMPONENTS:
        assert pilot.dropin_value(specs[component], "User").startswith("qsys-")


def test_workflow_is_manual_hosted_only_without_node_credentials():
    text = Path(".github/workflows/verify-component-rollback-preproduction.yml").read_text()
    assert "workflow_dispatch:" in text
    assert "runs-on: ubuntu-24.04" in text
    assert "QSYS_CI_RUNNER_LABELS" not in text
    assert "secrets." not in text
    assert "\n    environment:" not in text
    assert "ref: main" in text
