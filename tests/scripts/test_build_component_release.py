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
    (repo / "src/qsys/data").mkdir(parents=True)
    (repo / "src/qsys/strategy").mkdir(parents=True)
    (repo / "src/qsys/data/example.py").write_text("DATA = 1\n", encoding="utf-8")
    (repo / "src/qsys/strategy/example.py").write_text(
        "STRATEGY = 1\n", encoding="utf-8",
    )
    (repo / "deploy").mkdir()
    (repo / "deploy/component-releases.json").write_text(
        Path("deploy/component-releases.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
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
    assert value["version"] == 4
    assert len(value["component_spec_sha256"]) == 64
    assert value["wheel_filename"] == wheel.name
    assert value["wheel_sha256"] == hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert [item["path"] for item in value["scan_files"]] == [
        "src/qsys/data/example.py", "src/qsys/strategy/example.py",
    ]
    assert (release / "source/src/qsys/data/example.py").read_text() == "DATA = 1\n"
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
    with (release / "dist" / wheel.name).open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(ValueError, match="wheel_sha256 mismatch"):
        verify(repo, sha, "catalog-verify", release)

    build(repo, sha, "catalog-verify", wheel.parent, tmp_path / "source-release",
          repository="owner/repo", run_id="123", run_attempt="1")
    source = tmp_path / "source-release/source/src/qsys/data/example.py"
    source.write_text("DATA = 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="scan source mismatch"):
        verify(repo, sha, "catalog-verify", tmp_path / "source-release")

    spec_release = tmp_path / "spec-release"
    build(repo, sha, "catalog-verify", wheel.parent, spec_release,
          repository="owner/repo", run_id="123", run_attempt="1")
    manifest = json.loads((spec_release / "release.json").read_text(encoding="utf-8"))
    manifest["component_spec_sha256"] = "0" * 64
    (spec_release / "release.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="component specification mismatch"):
        verify(repo, sha, "catalog-verify", spec_release)
