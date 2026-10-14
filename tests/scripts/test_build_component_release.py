from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from scripts.build_component_release import build, verify


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
         "commit", "-m", "fixture"], cwd=repo, check=True, capture_output=True,
    )
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo,
                                  text=True).strip()
    return repo, sha


def _wheel(path: Path, *, entrypoint: str = "qsys.data.catalog_verify_cli:main") -> Path:
    path.parent.mkdir(parents=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("qsys-0.1.0.dist-info/METADATA", "Name: qsys\nVersion: 0.1.0\n")
        archive.writestr(
            "qsys-0.1.0.dist-info/entry_points.txt",
            f"[console_scripts]\nqsys-catalog-verify = {entrypoint}\n",
        )
        archive.writestr("qsys/__init__.py", "")
    return path


def test_catalog_release_binds_entrypoint_wheel_and_tree(tmp_path: Path) -> None:
    repo, sha = _repo(tmp_path)
    wheel = _wheel(tmp_path / "dist" / "qsys-0.1.0-py3-none-any.whl")
    release = tmp_path / "release"

    value = build(
        repo, sha, "catalog-verify", wheel.parent, release,
        repository="owner/repo", run_id="123", run_attempt="1",
    )

    assert value["component"] == "catalog-verify"
    assert value["source_sha"] == sha
    assert value["wheel_sha256"] == hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert verify(repo, sha, "catalog-verify", release) == value
    assert json.loads((release / "release.json").read_text()) == value


def test_catalog_release_rejects_wrong_entrypoint_and_tampering(tmp_path: Path) -> None:
    repo, sha = _repo(tmp_path)
    wheel = _wheel(
        tmp_path / "bad" / "qsys-0.1.0-py3-none-any.whl",
        entrypoint="qsys.research.runner_cli:main",
    )
    with pytest.raises(ValueError, match="does not expose"):
        build(repo, sha, "catalog-verify", wheel.parent, tmp_path / "bad-release",
              repository="owner/repo", run_id="123", run_attempt="1")

    wheel = _wheel(tmp_path / "dist" / "qsys-0.1.0-py3-none-any.whl")
    release = tmp_path / "release"
    build(repo, sha, "catalog-verify", wheel.parent, release,
          repository="owner/repo", run_id="123", run_attempt="1")
    with (release / "dist" / "qsys.whl").open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(ValueError, match="wheel_sha256 mismatch"):
        verify(repo, sha, "catalog-verify", release)
