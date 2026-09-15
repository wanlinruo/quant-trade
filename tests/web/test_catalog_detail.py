"""详情页回答血缘与影响范围，样本下载必须限制在产物目录。"""

from datetime import date, datetime

import polars as pl
from fastapi.testclient import TestClient

from qsys.data import schema
from qsys.ops.journal import SessionJournal
from qsys.web.app import create_app
from qsys.web.catalog_views import CatalogService
from qsys.web.config import WebConfig
from qsys.web.views import MonitorService

D = date(2026, 9, 15)
T = datetime(2026, 9, 15, 19, 30)


def _client(warehouse):
    config = WebConfig()
    return TestClient(create_app(
        MonitorService(SessionJournal(warehouse), config=config),
        config,
        catalog=CatalogService(warehouse),
    ))


def test_detail_shows_upstream_and_downstream(warehouse):
    """2026-09-15 起方向反转：档位由涨停价解码，不再是涨停价的上游。"""
    detail = CatalogService(warehouse).detail("daily_flags.limit_up")
    assert detail is not None
    assert "price_limits_daily.limit_up" in detail.upstream
    assert "daily_flags.limit_pct" in detail.downstream
    assert "daily_flags.is_limit_up" in detail.downstream


def test_direct_detail_has_no_upstream(warehouse):
    detail = CatalogService(warehouse).detail("tushare_daily.close")
    assert detail is not None
    assert detail.upstream == ()
    assert detail.roots == ("tushare.daily.close",)


def test_detail_returns_none_for_unknown_metric(warehouse):
    assert CatalogService(warehouse).detail("nope.nope") is None


def test_detail_lists_manual_evidence(warehouse):
    warehouse.write("metric_evidence", pl.DataFrame([{
        "metric_id": "adj_factors.cum_factor", "symbol": "001331.SZ", "dt": D,
        "our_value": "3.4848", "reference_value": "2.4011",
        "reference_source": "同花顺", "verdict": "mismatch",
        "checked_by": "wanlinruo", "checked_at": T, "note": "D-1 抽查",
    }]).select(schema.METRIC_EVIDENCE.names)
        .to_arrow().cast(schema.METRIC_EVIDENCE), year=2026)
    detail = CatalogService(warehouse).detail("adj_factors.cum_factor")
    assert detail is not None
    assert len(detail.evidence) == 1
    assert detail.evidence[0]["reference_source"] == "同花顺"


def test_sources_count_direct_metrics(warehouse):
    rows = {row.source.id: row for row in CatalogService(warehouse).sources()}
    assert rows["tushare.daily"].metric_count >= 9
    assert rows["mootdx.kline"].metric_count == 8


def test_non_active_sources_are_listed_first(warehouse):
    rows = CatalogService(warehouse).sources()
    first_active = next(i for i, row in enumerate(rows) if row.source.status == "active")
    assert all(row.source.status != "active" for row in rows[:first_active])


def test_detail_page_renders(warehouse):
    response = _client(warehouse).get("/data/catalog/daily_flags.is_first_board")
    assert response.status_code == 200
    assert "首板" in response.text


def test_unknown_detail_is_404(warehouse):
    assert _client(warehouse).get("/data/catalog/nope.nope").status_code == 404


def test_sources_page_renders_before_dynamic_metric_route(warehouse):
    response = _client(warehouse).get("/data/catalog/sources")
    assert response.status_code == 200
    assert "mootdx" in response.text
    assert "已失效" in response.text


def test_sample_download_rejects_files_outside_artifact_root(
    warehouse, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    allowed = tmp_path / "artifacts" / "metric_checks" / "r1" / "sample.parquet"
    allowed.parent.mkdir(parents=True)
    allowed.write_bytes(b"PAR1allowed")
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    service = CatalogService(warehouse)
    assert service.sample_bytes(str(allowed)) == (
        b"PAR1allowed", "application/octet-stream"
    )
    assert service.sample_bytes(str(secret)) is None


def test_sample_route_downloads_only_allowed_artifact(warehouse, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sample = tmp_path / "artifacts" / "metric_checks" / "r1" / "sample.parquet"
    sample.parent.mkdir(parents=True)
    sample.write_bytes(b"PAR1")
    client = _client(warehouse)
    response = client.get("/data/catalog/sample", params={"path": str(sample)})
    assert response.status_code == 200
    assert response.content == b"PAR1"
    assert "attachment" in response.headers["content-disposition"]
    assert client.get(
        "/data/catalog/sample", params={"path": str(tmp_path / "outside")}
    ).status_code == 404
