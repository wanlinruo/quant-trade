from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest


def _module():
    path = Path("scripts/release_evidence.py")
    spec = importlib.util.spec_from_file_location("qsys_release_evidence", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (repo / ".github" / "workflows" / "ci.yml").write_text(
        "name: CI\n", encoding="utf-8",
    )
    (repo / ".github" / "workflows" / "deploy-test.yml").write_text(
        "name: Deploy test\n", encoding="utf-8",
    )
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"],
                   cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "test: evidence"], cwd=repo,
                   check=True, capture_output=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo,
                                  text=True).strip()
    return repo, sha


def test_release_evidence_binds_tree_workflows_lock_and_runs(tmp_path) -> None:
    module = _module()
    repo, sha = _repo(tmp_path)

    evidence = module.build_evidence(
        repo, source_sha=sha, controller_sha=sha, ci_run_id=123,
        ci_check_suite_id=456, node_record_run_id="github-789-1",
    )

    assert evidence["kind"] == "qsys-test-release-evidence"
    assert evidence["source_sha"] == sha
    assert evidence["tree_sha"] == subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=repo, text=True,
    ).strip()
    assert evidence["ci_run_id"] == 123
    assert evidence["node_record_run_id"] == "github-789-1"
    module.validate_evidence(
        evidence, repo=repo, source_sha=sha, tree_sha=evidence["tree_sha"],
    )


def test_release_evidence_rejects_tampered_lock_hash(tmp_path) -> None:
    module = _module()
    repo, sha = _repo(tmp_path)
    evidence = module.build_evidence(
        repo, source_sha=sha, controller_sha=sha, ci_run_id=123,
        ci_check_suite_id=456, node_record_run_id="github-789-1",
    )
    evidence["lockfile_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="lockfile_sha256 mismatch"):
        module.validate_evidence(
            evidence, repo=repo, source_sha=sha, tree_sha=evidence["tree_sha"],
        )


def test_release_evidence_rejects_unknown_fields(tmp_path) -> None:
    module = _module()
    repo, sha = _repo(tmp_path)
    evidence = module.build_evidence(
        repo, source_sha=sha, controller_sha=sha, ci_run_id=123,
        ci_check_suite_id=456, node_record_run_id="github-789-1",
    )
    evidence["untrusted"] = True

    with pytest.raises(ValueError, match="fields do not match"):
        module.validate_evidence(
            evidence, repo=repo, source_sha=sha, tree_sha=evidence["tree_sha"],
        )


def test_release_evidence_fast_path_rejects_changed_deploy_controller(tmp_path) -> None:
    module = _module()
    repo, controller = _repo(tmp_path)
    workflow = repo / ".github" / "workflows" / "deploy-test.yml"
    workflow.write_text("name: Changed deploy test\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "test: change controller"], cwd=repo,
                   check=True, capture_output=True)
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo,
                                     text=True).strip()
    evidence = module.build_evidence(
        repo, source_sha=source, controller_sha=controller, ci_run_id=123,
        ci_check_suite_id=456, node_record_run_id="github-789-1",
    )

    with pytest.raises(ValueError, match="controller differs"):
        module.validate_evidence(
            evidence, repo=repo, source_sha=source, tree_sha=evidence["tree_sha"],
            require_controller_match=True,
        )
