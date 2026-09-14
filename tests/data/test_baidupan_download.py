import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location("baidupan_download_test", Path(__file__).parents[2] / "scripts/baidupan.py")
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


def response(data):
    result = Mock()
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.read.side_effect = [data, b""]
    return result


def test_prefetched_link_and_existing_skip(tmp_path, monkeypatch):
    request = Mock(return_value=response(b"xlsx"))
    monkeypatch.setattr(b.urllib.request, "urlopen", request)
    fresh = Mock(side_effect=AssertionError("must use cached link"))
    monkeypatch.setattr(b, "dlink", fresh)
    entry = {"path": "/daily/a.xlsx", "size": 4, "fs_id": 1}
    dest = tmp_path / "a.xlsx"
    assert b.download("TOKEN", entry, dest, initial_link="https://example.com/file?q=1")[1]
    assert dest.read_bytes() == b"xlsx"
    assert not b.download("TOKEN", entry, dest)[1]
    assert request.call_count == 1


def test_retry_refreshes_link_and_does_not_publish_partial_file(tmp_path, monkeypatch):
    request = Mock(side_effect=[response(b"bad"), response(b"xlsx")])
    monkeypatch.setattr(b.urllib.request, "urlopen", request)
    fresh = Mock(return_value="https://example.com/new?q=1")
    monkeypatch.setattr(b, "dlink", fresh)
    monkeypatch.setattr(b.time, "sleep", lambda _: None)
    entry = {"path": "/daily/a.xlsx", "size": 4, "fs_id": 1}
    dest = tmp_path / "a.xlsx"
    b.download("TOKEN", entry, dest, initial_link="https://example.com/old?q=1")
    fresh.assert_called_once_with("TOKEN", 1)
    assert dest.read_bytes() == b"xlsx"
    assert not dest.with_suffix(".xlsx.part").exists()


def test_large_file_resume_keeps_partial_bytes(tmp_path, monkeypatch):
    resumed = response(b"def")
    resumed.status = 206
    resumed.getcode.return_value = 206
    resumed.headers = {"Content-Range": "bytes 3-5/6"}
    request = Mock(return_value=resumed)
    monkeypatch.setattr(b.urllib.request, "urlopen", request)
    monkeypatch.setattr(b, "dlink", Mock(return_value="https://example.com/file?q=1"))
    entry = {"path": "/minute/2025.zip", "size": 6, "fs_id": 1}
    dest = tmp_path / "2025.zip"
    dest.with_suffix(".zip.part").write_bytes(b"abc")

    assert b.download_resumable("TOKEN", entry, dest)[1]
    assert dest.read_bytes() == b"abcdef"
    assert request.call_args.args[0].headers["Range"] == "bytes=3-"


def test_large_file_ignored_range_restarts_safely(tmp_path, monkeypatch):
    full = response(b"abcdef")
    full.status = 200
    full.getcode.return_value = 200
    full.headers = {}
    monkeypatch.setattr(b.urllib.request, "urlopen", Mock(return_value=full))
    monkeypatch.setattr(b, "dlink", Mock(return_value="https://example.com/file?q=1"))
    entry = {"path": "/minute/2025.zip", "size": 6, "fs_id": 1}
    dest = tmp_path / "2025.zip"
    dest.with_suffix(".zip.part").write_bytes(b"abc")

    b.download_resumable("TOKEN", entry, dest)
    assert dest.read_bytes() == b"abcdef"
