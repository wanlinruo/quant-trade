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


def test_a_source_reached_only_through_an_unclassified_table_still_shows_a_domain(
    monkeypatch,
):
    """「所属域」必须把未归类的表也算进去。

    不算的话，只被未归类表引用的源会显示「—」，与「没有任何表用它」在页面上
    长得一样——那正是本支要消灭的那种看不见。这条路径只在有表未归类时才亮，
    当前 47 张全部归类，所以没有测试就等于没修。
    """
    from dataclasses import replace

    from qsys.data.catalog import DATASETS
    from qsys.data.catalog.types import UNCLASSIFIED

    monkeypatch.setitem(
        DATASETS, "bar_minute_1m",
        replace(DATASETS["bar_minute_1m"], domain=UNCLASSIFIED("跨域，待判")),
    )
    rows = {row.source.id: row.domains for row in CatalogService.sources()}
    assert "未归类" in rows["baidupan.minute"]
    assert "未归类" in rows["qmt.minute"]
