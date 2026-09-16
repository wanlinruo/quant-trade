"""数据层总览页面在空仓库中也必须可用。"""

from fastapi.testclient import TestClient

from qsys.ops.journal import SessionJournal
from qsys.web.app import create_app
from qsys.web.catalog_views import CatalogService
from qsys.web.config import WebConfig
from qsys.web.views import MonitorService


def _client(warehouse):
    config = WebConfig()
    return TestClient(create_app(
        MonitorService(SessionJournal(warehouse), config=config),
        config,
        catalog=CatalogService(warehouse),
    ))


def test_overview_renders_on_empty_warehouse(warehouse):
    response = _client(warehouse).get("/data/catalog")
    assert response.status_code == 200
    assert "指标总数" in response.text


def test_overview_links_to_metric_detail(warehouse):
    assert "/data/catalog/tushare_daily.close" in _client(warehouse).get(
        "/data/catalog"
    ).text


def test_navigation_has_data_catalog_tab(warehouse):
    assert ">指标字典<" in _client(warehouse).get("/data/catalog").text


def test_kind_filter_renders(warehouse):
    assert _client(warehouse).get("/data/catalog?kind=factor").status_code == 200


def test_invalid_status_filter_returns_422(warehouse):
    assert _client(warehouse).get("/data/catalog?status=nope").status_code == 422
