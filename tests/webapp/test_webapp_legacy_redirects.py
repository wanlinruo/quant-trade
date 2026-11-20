"""旧 HTML 页面只永久跳到新页面；旧 API 不复活。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from qsys.webapp.app import WebappDeps, create_app
from qsys.webapp.live_kill_status import LiveKillReader


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(WebappDeps(
        live_kill=LiveKillReader(None, None),
        spa_root=tmp_path / "missing-build",
    )))


@pytest.mark.parametrize(("source", "target"), [
    ("/", "/app/today"),
    ("/live", "/app/live"),
    ("/paper/account-1", "/app/paper"),
    ("/market/limit-up-board", "/app/board"),
    ("/strategies/demo", "/app/strategies"),
    ("/backtests/run-1", "/app/backtest"),
    ("/data/tables/daily", "/app/tables"),
    ("/data/quality/rules/rule-1", "/app/quality"),
    ("/services/runs/2026-11-19/run-1", "/app/services"),
    ("/s/2026-11-19/trades", "/app/orders"),
    ("/s/2026-11-19/health", "/app/recon"),
    ("/s/2026-11-19/report", "/app/runs"),
])
def test_old_page_get_and_head_redirect_permanently(client, source, target) -> None:
    for method in (client.get, client.head):
        response = method(source, follow_redirects=False)
        assert response.status_code == 308
        assert response.headers["location"] == target


def test_redirect_preserves_existing_query_string(client) -> None:
    response = client.get("/services?day=2026-11-19&state=failed", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "/app/services?day=2026-11-19&state=failed"


@pytest.mark.parametrize(("method", "path"), [
    ("post", "/api/strategies"),
    ("put", "/api/strategies/demo"),
    ("post", "/api/backtests"),
    ("post", "/api/paper-accounts"),
    ("post", "/api/research-jobs"),
])
def test_old_write_apis_are_not_redirected(client, method, path) -> None:
    response = getattr(client, method)(path, follow_redirects=False)
    assert response.status_code == 404
    assert "location" not in response.headers


@pytest.mark.parametrize("path", [
    "/api/live/kill-status",
    "/api/market/overview",
    "/data/selection",
    "/s/2026-11-19/report.txt",
    "/fragment/2026-11-19/status",
])
def test_old_api_and_download_gets_are_not_redirected(client, path) -> None:
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 404
    assert "location" not in response.headers
