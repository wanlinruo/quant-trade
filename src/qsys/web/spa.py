"""新前端（SPA）构建产物的定位与完整性校验（#662，设计文档 §12）。

构建产物由 CI 构建后以 hatch artifacts 打进 wheel，位于本包 ``spa_dist/``。
``build-info.json`` 列出除自身外每个文件的 sha256；任何缺失、多余、
篡改或格式错误都视为产物不可用，返回 ``None``，由调用方给出明确提示，
不尝试部分使用。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SPA_DIST = Path(__file__).resolve().parent / "spa_dist"
BUILD_INFO = "build-info.json"
SCHEMA_VERSION = 1


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def read_build_info(root: Path = SPA_DIST) -> dict[str, Any] | None:
    """读取并校验 ``root/build-info.json``；不可用时返回 ``None``。"""
    info_path = root / BUILD_INFO
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(info, dict) or info.get("schema_version") != SCHEMA_VERSION:
        return None
    files = info.get("files")
    if not isinstance(files, dict) or "index.html" not in files:
        return None

    root_resolved = root.resolve()
    actual = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.relative_to(root).as_posix() != BUILD_INFO
    }
    if set(files) != actual:
        return None
    for rel, digest in files.items():
        if not isinstance(rel, str) or not isinstance(digest, str):
            return None
        path = (root / rel).resolve()
        if not path.is_relative_to(root_resolved) or not path.is_file():
            return None
        if _sha256(path) != digest:
            return None
    if info.get("index_sha256") != files["index.html"]:
        return None
    return info
