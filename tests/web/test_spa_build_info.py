"""新前端构建产物的哈希清单（#662，设计文档 §12）。"""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

from qsys.web.spa import read_build_info

REPO = Path(__file__).resolve().parents[2]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_build(root: Path) -> None:
    (root / "assets").mkdir(parents=True)
    index = b"<!doctype html><div id=root></div>"
    js = b"console.log(1)"
    (root / "index.html").write_bytes(index)
    (root / "assets" / "a.js").write_bytes(js)
    info = {
        "schema_version": 1,
        "files": {"index.html": _sha(index), "assets/a.js": _sha(js)},
        "index_sha256": _sha(index),
    }
    (root / "build-info.json").write_text(json.dumps(info))


def test_read_build_info_accepts_matching_hashes(tmp_path: Path) -> None:
    _make_build(tmp_path)
    info = read_build_info(tmp_path)
    assert info is not None
    assert info["schema_version"] == 1
    assert set(info["files"]) == {"index.html", "assets/a.js"}


def test_read_build_info_rejects_tampered_file(tmp_path: Path) -> None:
    _make_build(tmp_path)
    (tmp_path / "assets" / "a.js").write_bytes(b"console.log(2)")
    assert read_build_info(tmp_path) is None


def test_read_build_info_rejects_unlisted_extra_file(tmp_path: Path) -> None:
    _make_build(tmp_path)
    (tmp_path / "assets" / "stale.js").write_bytes(b"old")
    assert read_build_info(tmp_path) is None


def test_read_build_info_rejects_index_hash_mismatch(tmp_path: Path) -> None:
    _make_build(tmp_path)
    info = json.loads((tmp_path / "build-info.json").read_text())
    info["index_sha256"] = "0" * 64
    (tmp_path / "build-info.json").write_text(json.dumps(info))
    assert read_build_info(tmp_path) is None


def test_read_build_info_missing_returns_none(tmp_path: Path) -> None:
    assert read_build_info(tmp_path) is None
    (tmp_path / "index.html").write_text("x")
    assert read_build_info(tmp_path) is None


def test_read_build_info_malformed_json_returns_none(tmp_path: Path) -> None:
    _make_build(tmp_path)
    (tmp_path / "build-info.json").write_text("{not json")
    assert read_build_info(tmp_path) is None


def test_read_build_info_rejects_path_escape(tmp_path: Path) -> None:
    root = tmp_path / "dist"
    _make_build(root)
    (tmp_path / "secret").write_bytes(b"s")
    info = json.loads((root / "build-info.json").read_text())
    info["files"]["../secret"] = _sha(b"s")
    (root / "build-info.json").write_text(json.dumps(info))
    assert read_build_info(root) is None


def test_pyproject_ships_spa_dist_as_wheel_artifact() -> None:
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert "src/qsys/web/spa_dist/**" in data["tool"]["hatch"]["build"]["targets"]["wheel"]["artifacts"]


def test_pyproject_ships_spa_dist_in_sdist_too() -> None:
    # CI 用 `uv build`：先打 sdist 再从 sdist 构建 wheel；spa_dist 被 git 忽略，
    # sdist 不显式声明就会丢失，wheel 里也就没有前端。
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert "src/qsys/web/spa_dist/**" in data["tool"]["hatch"]["build"]["targets"]["sdist"]["artifacts"]
