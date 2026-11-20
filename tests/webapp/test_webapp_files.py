"""受控下载（#662 R3，总路线 v2 第 3 节 files.py）。"""

from __future__ import annotations

import hashlib
import os

import pytest
from fastapi import HTTPException

from qsys.webapp.files import RUN_FILE_SUFFIXES, Expected, controlled_file


def _rejected(root, rel, **kw) -> bool:
    with pytest.raises(HTTPException) as exc:
        controlled_file(root, rel, **kw)
    return exc.value.status_code == 404


def test_download_serves_file_with_attachment_headers(tmp_path):
    (tmp_path / "fills.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    r = controlled_file(tmp_path, "fills.csv", suffixes=RUN_FILE_SUFFIXES)
    assert r.body == b"a,b\n1,2\n"
    assert r.media_type.startswith("text/csv")
    assert r.headers["content-disposition"].startswith('attachment; filename="fills.csv"')
    assert r.headers["cache-control"] == "no-store"


def test_download_rejects_escape_and_symlinks(tmp_path):
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    outside = tmp_path / "x.csv"
    outside.write_text("secret", encoding="utf-8")
    (root / ".hidden.csv").write_text("h", encoding="utf-8")
    os.symlink(outside, root / "link.csv")
    real = tmp_path / "realdir"
    real.mkdir()
    (real / "in.csv").write_text("x", encoding="utf-8")
    os.symlink(real, root / "dirlink")
    for rel in ("../x.csv", str(outside), "a/../../x.csv", ".hidden.csv", "a\\b.csv",
                "link.csv", "dirlink/in.csv", "", "a//b.csv", "a/./b.csv"):
        assert _rejected(root, rel, suffixes=RUN_FILE_SUFFIXES), rel
    assert _rejected(root, "a", suffixes=None)  # 目录不是文件


def test_download_rejects_suffix_not_allowed(tmp_path):
    (tmp_path / "x.sqlite3").write_bytes(b"db")
    assert _rejected(tmp_path, "x.sqlite3", suffixes=RUN_FILE_SUFFIXES)
    assert controlled_file(tmp_path, "x.sqlite3", suffixes=None).body == b"db"


def test_download_rejects_hash_mismatch(tmp_path):
    (tmp_path / "sub").mkdir()
    body = b"result"
    (tmp_path / "sub" / "out.parquet").write_bytes(body)
    good = Expected(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
    assert controlled_file(tmp_path, "sub/out.parquet", suffixes=None, expected=good).body == body
    assert _rejected(tmp_path, "sub/out.parquet", suffixes=None,
                     expected=Expected(bytes=len(body) + 1, sha256=good.sha256))
    assert _rejected(tmp_path, "sub/out.parquet", suffixes=None,
                     expected=Expected(bytes=len(body), sha256="0" * 64))


def test_download_non_ascii_filename_header(tmp_path):
    (tmp_path / "回测报告.csv").write_text("x", encoding="utf-8")
    cd = controlled_file(tmp_path, "回测报告.csv", suffixes=RUN_FILE_SUFFIXES).headers["content-disposition"]
    assert cd.isascii()
    assert "filename*=UTF-8''%E5%9B%9E%E6%B5%8B%E6%8A%A5%E5%91%8A.csv" in cd


def test_download_missing_root_is_404(tmp_path):
    assert _rejected(tmp_path / "nope", "a.csv", suffixes=RUN_FILE_SUFFIXES)
