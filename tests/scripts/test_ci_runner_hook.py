import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(".github/ci-runner/ci_runner_hook.py")
SPEC = importlib.util.spec_from_file_location("qsys_ci_runner_hook", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
HOOK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOOK)
audit_environment = HOOK.audit_environment
clean_job = HOOK.clean_job


def test_audit_accepts_an_isolated_workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(HOOK.platform, "system", lambda: "Linux")
    monkeypatch.setattr(HOOK.platform, "machine", lambda: "x86_64")
    root = tmp_path / "_work"
    workspace = root / "repo" / "repo"

    result = audit_environment(
        work_root=root,
        forbidden_paths=(tmp_path / "production",),
        environ={"GITHUB_WORKSPACE": str(workspace)},
    )

    assert result["ok"] is True
    assert result["errors"] == []


def test_audit_rejects_production_material_and_escaping_paths(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(HOOK.platform, "system", lambda: "Linux")
    monkeypatch.setattr(HOOK.platform, "machine", lambda: "x86_64")
    forbidden = tmp_path / "production"
    forbidden.mkdir()

    result = audit_environment(
        work_root=tmp_path / "_work",
        forbidden_paths=(forbidden,),
        environ={
            "GITHUB_WORKSPACE": str(tmp_path / "elsewhere"),
            "PRODUCTION_DEPLOY_SSH_KEY": "must-not-be-here",
        },
    )

    assert result["ok"] is False
    assert "production or shared qsys paths exist on the CI host" in result["errors"]
    assert "forbidden production environment variables are present" in result["errors"]
    assert "GITHUB_WORKSPACE escapes the dedicated work root" in result["errors"]
    assert result["forbidden_environment_names"] == ["PRODUCTION_DEPLOY_SSH_KEY"]


def test_cleanup_removes_only_job_workspace_and_temp(tmp_path) -> None:
    root = tmp_path / "_work"
    workspace = root / "repo" / "repo"
    temp = root / "_temp"
    keep = root / "keep"
    workspace.mkdir(parents=True)
    temp.mkdir()
    keep.mkdir()
    (workspace / "checkout.txt").write_text("dirty", encoding="utf-8")
    (temp / "secret.tmp").write_text("transient", encoding="utf-8")
    (keep / "runner.txt").write_text("keep", encoding="utf-8")

    result = clean_job(
        work_root=root,
        environ={"GITHUB_WORKSPACE": str(workspace), "RUNNER_TEMP": str(temp)},
    )

    assert result == {
        "ok": True,
        "cleaned": {"GITHUB_WORKSPACE": 1, "RUNNER_TEMP": 1},
    }
    assert list(workspace.iterdir()) == []
    assert list(temp.iterdir()) == []
    assert (keep / "runner.txt").read_text(encoding="utf-8") == "keep"


def test_cleanup_refuses_to_follow_an_escaping_workspace(tmp_path) -> None:
    root = tmp_path / "_work"
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(ValueError, match="refusing to clean outside work root"):
        clean_job(work_root=root, environ={"GITHUB_WORKSPACE": str(outside)})
