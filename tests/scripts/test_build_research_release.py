from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from scripts.build_research_release import build, verify


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='qsys'\n", encoding="utf-8")
    (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "test"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
         "commit", "-m", "fixture"],
        cwd=repo, check=True, capture_output=True,
    )
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo,
                                  text=True).strip()
    return repo, sha


def _wheel(path: Path) -> Path:
    path.parent.mkdir(parents=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("qsys-0.1.0.dist-info/METADATA", "Name: qsys\nVersion: 0.1.0\n")
        archive.writestr(
            "qsys-0.1.0.dist-info/entry_points.txt",
            "[console_scripts]\nqsys-research-runner = qsys.research.runner_cli:main\n",
        )
        archive.writestr("qsys/__init__.py", "")
    return path


def test_build_and_verify_bind_wheel_to_exact_git_tree(tmp_path: Path) -> None:
    repo, sha = _repo(tmp_path)
    wheel = _wheel(tmp_path / "dist" / "qsys-0.1.0-py3-none-any.whl")
    release = tmp_path / "release"

    value = build(
        repo, sha, wheel.parent, release,
        repository="owner/repo", run_id="123", run_attempt="1",
    )

    assert value["source_sha"] == sha
    assert value["wheel_filename"] == "qsys-0.1.0-py3-none-any.whl"
    assert value["wheel_sha256"] == hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert verify(repo, sha, release) == value
    assert json.loads((release / "release.json").read_text()) == value


def test_verify_rejects_wheel_tampering_and_wrong_source(tmp_path: Path) -> None:
    repo, sha = _repo(tmp_path)
    wheel = _wheel(tmp_path / "dist" / "qsys-0.1.0-py3-none-any.whl")
    release = tmp_path / "release"
    build(repo, sha, wheel.parent, release, repository="owner/repo", run_id="123",
          run_attempt="1")
    with (release / "dist" / "qsys-0.1.0-py3-none-any.whl").open("ab") as stream:
        stream.write(b"tampered")

    with pytest.raises(ValueError, match="wheel_sha256 mismatch"):
        verify(repo, sha, release)
    with pytest.raises(ValueError, match="exact checked-out HEAD"):
        verify(repo, "b" * 40, release)


def test_verify_requires_clean_exact_checkout(tmp_path: Path) -> None:
    repo, sha = _repo(tmp_path)
    wheel = _wheel(tmp_path / "dist" / "qsys-0.1.0-py3-none-any.whl")
    release = tmp_path / "release"
    value = build(repo, sha, wheel.parent, release, repository="owner/repo",
                  run_id="123", run_attempt="1")

    (repo / "uv.lock").write_text("tampered working tree\n", encoding="utf-8")
    with pytest.raises(ValueError, match="tracked modifications"):
        verify(repo, sha, release)
    subprocess.run(["git", "restore", "uv.lock"], cwd=repo, check=True)
    assert verify(repo, sha, release) == value

    (repo / "extra.txt").write_text("next commit\n", encoding="utf-8")
    subprocess.run(["git", "add", "extra.txt"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
         "commit", "-m", "next"],
        cwd=repo, check=True, capture_output=True,
    )
    with pytest.raises(ValueError, match="exact checked-out HEAD"):
        verify(repo, sha, release)
