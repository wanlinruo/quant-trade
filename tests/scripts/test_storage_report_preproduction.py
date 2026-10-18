import zipfile
from pathlib import Path

import pytest
from scripts import verify_storage_report_preproduction as pilot


def test_local_or_node_execution_is_refused():
    with pytest.raises(ValueError, match="GitHub-hosted"):
        pilot.validate_host({})


@pytest.mark.parametrize("name", ["../escape", "/tmp/escape", "a/../../escape"])
def test_artifact_extraction_refuses_traversal(tmp_path, name):
    archive = tmp_path / "artifact.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(name, "test fixture")
    with pytest.raises(ValueError, match="unsafe"):
        pilot.extract_zip(archive, tmp_path / "release")


def test_artifact_extraction_refuses_symlinks(tmp_path):
    archive = tmp_path / "artifact.zip"
    with zipfile.ZipFile(archive, "w") as z:
        i = zipfile.ZipInfo("link")
        i.external_attr = 0o120777 << 16
        z.writestr(i, "/etc/passwd")
    with pytest.raises(ValueError, match="unsafe"):
        pilot.extract_zip(archive, tmp_path / "release")


def test_safe_component_archive_is_readable(tmp_path):
    archive = tmp_path / "artifact.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("release.json", "{}")
    pilot.extract_zip(archive, tmp_path / "release")
    assert (tmp_path / "release/release.json").read_text() == "{}"


def test_workflow_is_manual_hosted_only_without_node_credentials():
    text = Path('.github/workflows/verify-storage-report-preproduction.yml').read_text()
    assert "workflow_dispatch:" in text
    assert "runs-on: ubuntu-24.04" in text
    assert "QSYS_CI_RUNNER_LABELS" not in text
    assert "secrets." not in text
    assert "\n    environment:" not in text
    assert "ref: main" in text
