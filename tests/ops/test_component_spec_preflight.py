"""Exercise the actual node status and workflow verifier across whitelist drift."""

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def node(monkeypatch, tmp_path):
    path = Path("deploy/linux/qsys-production-deploy-entrypoint.py")
    spec = importlib.util.spec_from_file_location("preflight_node", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, value in {
        "_head": "a" * 40, "_branch": "main", "_dirty": False,
        "_active": True, "_web_healthy": True,
    }.items():
        monkeypatch.setattr(module, name, lambda *a, result=value: result)
    return module


@pytest.mark.parametrize(
    ("case", "success", "error"),
    [
        ("matching", True, ""),
        ("stale-web", False, "component specification mismatch"),
        ("unrelated-change", True, ""),
        ("old-node", False, "bootstrap"),
        ("missing-component", False, "component specification mismatch"),
        ("missing-spec", False, "valid component specification"),
    ],
)
def test_actual_status_and_workflow_cli(node, tmp_path, case, success, error):
    manifest = json.loads(Path("deploy/component-releases.json").read_text())
    node.COMPONENT_RELEASES = copy.deepcopy(manifest["components"])
    if case in {"stale-web", "unrelated-change"}:
        target = "web" if case == "stale-web" else "health-observer"
        node.COMPONENT_RELEASES[target]["dropin_lines"].append(
            "Environment=QSYS_WINDOWS_SSH_KEY=/etc/qsys/web/windows-task-readonly"
        )
    state = node.status({"production_repo": str(tmp_path)})
    if case == "old-node":
        state["entrypoint_version"] = 31
        state.pop("component_spec_sha256")
    elif case == "missing-component":
        state["component_spec_sha256"].pop("web")
    elif case == "missing-spec":
        manifest["components"].pop("web")
    state["private"] = "DO_NOT_PRINT_THIS"
    state_path, manifest_path = tmp_path / "state.json", tmp_path / "manifest.json"
    state_path.write_text(json.dumps(state))
    # Formatting and object key order must not affect comparison.
    manifest_path.write_text(json.dumps(manifest, indent=3, sort_keys=True))
    result = subprocess.run([
        sys.executable, ".github/scripts/component_spec_preflight.py",
        "--state", str(state_path), "--manifest", str(manifest_path), "--component", "web",
    ], text=True, capture_output=True)
    assert (result.returncode == 0) is success
    assert "DO_NOT_PRINT_THIS" not in result.stdout + result.stderr
    if success:
        assert json.loads(result.stdout) == {
            "component": "web", "component_spec_sha256": state["component_spec_sha256"]["web"],
        }
    else:
        assert error in result.stderr
        assert not result.stdout


def test_workflow_checks_spec_for_dry_and_formal_before_downloading():
    workflow = Path(".github/workflows/deploy-production-component.yml").read_text()
    source = workflow.split("      - name: Validate source, evidence and gateway", 1)[1]
    preflight, download = source.split("      - name: Download exact immutable artifact", 1)
    assert "if: inputs.dry_run" not in preflight
    assert 'git show "$TARGET_SHA:deploy/component-releases.json"' in preflight
    assert "component_spec_preflight.py" in preflight
    assert '--component "$COMPONENT"' in preflight
    assert "actions/artifacts/$ARTIFACT_ID/zip" in download
