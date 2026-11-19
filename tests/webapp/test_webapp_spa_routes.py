"""新前端在 /app 下的路由（#662，设计文档 §12）。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qsys.webapp.app import WebappDeps, create_app
from qsys.webapp.config import WebConfig
from qsys.webapp.live_kill_status import LiveKillReader

INDEX = b"<!doctype html><html><div id=root></div></html>"
JS = b"console.log('app')"
ICON = b"<svg/>"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def spa_tmp(tmp_path: Path) -> Path:
    root = tmp_path / "spa_dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_bytes(INDEX)
    (root / "assets" / "index-abc.js").write_bytes(JS)
    (root / "favicon.svg").write_bytes(ICON)
    files = {"index.html": _sha(INDEX), "assets/index-abc.js": _sha(JS), "favicon.svg": _sha(ICON)}
    (root / "build-info.json").write_text(
        json.dumps({"schema_version": 1, "files": files, "index_sha256": files["index.html"]})
    )
    return root


@pytest.fixture
def client_factory():
    def make(spa_root: Path) -> TestClient:
        deps = WebappDeps(
            live_kill=LiveKillReader(None, None),
            spa_root=spa_root,
        )
        return TestClient(create_app(deps, WebConfig()))

    return make


def test_app_serves_index_with_no_store(spa_tmp, client_factory) -> None:
    c = client_factory(spa_tmp)
    for url in ("/app", "/app/"):
        r = c.get(url)
        assert r.status_code == 200
        assert r.content == INDEX
        assert r.headers["content-type"].startswith("text/html")
        assert r.headers["cache-control"] == "no-store"


def test_deep_link_serves_index_but_missing_asset_404(spa_tmp, client_factory) -> None:
    c = client_factory(spa_tmp)
    r = c.get("/app/orders")
    assert r.status_code == 200
    assert r.text.startswith("<!doctype html")
    assert c.get("/app/ops/nodes").content == INDEX
    missing = c.get("/app/assets/missing.js")
    assert missing.status_code == 404
    assert not missing.text.startswith("<!doctype html")


def test_registered_asset_is_immutable_cached(spa_tmp, client_factory) -> None:
    r = client_factory(spa_tmp).get("/app/assets/index-abc.js")
    assert r.status_code == 200
    assert r.content == JS
    assert "javascript" in r.headers["content-type"]
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_registered_root_file_is_served_not_index(spa_tmp, client_factory) -> None:
    r = client_factory(spa_tmp).get("/app/favicon.svg")
    assert r.status_code == 200
    assert r.content == ICON
    assert r.headers["cache-control"] == "no-cache"


def test_unregistered_file_on_disk_is_not_served(spa_tmp, client_factory) -> None:
    c = client_factory(spa_tmp)
    (spa_tmp / "assets" / "planted.js").write_bytes(b"evil")
    assert c.get("/app/assets/planted.js").status_code == 404
    assert c.get("/app/build-info.json").content == INDEX


def test_path_traversal_rejected(spa_tmp, client_factory) -> None:
    c = client_factory(spa_tmp)
    assert c.get("/app/assets/..%2f..%2fapp.py").status_code == 404
    assert c.get("/app/..%2f..%2fpyproject.toml").status_code == 404
    assert c.get("/app/%2fetc%2fpasswd").status_code == 404


def test_only_get_and_head_allowed(spa_tmp, client_factory) -> None:
    c = client_factory(spa_tmp)
    assert c.head("/app/orders").status_code == 200
    assert c.post("/app/orders").status_code == 405


def test_app_missing_build_returns_503_but_health_still_works(tmp_path, client_factory) -> None:
    c = client_factory(tmp_path / "none")
    for url in ("/app", "/app/orders", "/app/assets/index-abc.js"):
        r = c.get(url)
        assert r.status_code == 503
        assert "前端构建产物缺失" in r.text
        assert r.headers["cache-control"] == "no-store"
    assert c.get("/healthz").status_code == 200


def test_root_permanently_redirects_to_today(spa_tmp, client_factory) -> None:
    response = client_factory(spa_tmp).get("/", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "/app/today"


def test_tampered_build_returns_503(spa_tmp, client_factory) -> None:
    (spa_tmp / "assets" / "index-abc.js").write_bytes(b"tampered")
    assert client_factory(spa_tmp).get("/app").status_code == 503
