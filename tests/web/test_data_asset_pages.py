"""表级资产目录必须在空仓库中也可解释，且不伪造健康状态。"""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from qsys.data.catalog.health import (
    ALL_DIMENSIONS,
    HealthEvidence,
    HealthReason,
    HealthStatus,
    dataset_check_frame,
    write_dataset_checks,
)
from qsys.data.catalog.advisor import AdviceKind
from qsys.ops.journal import SessionJournal
from qsys.web.app import create_app
from qsys.web.asset_views import DataAssetService
from qsys.web.config import WebConfig
from qsys.web.views import MonitorService


def _client(warehouse):
    config = WebConfig()
    return TestClient(create_app(
        MonitorService(SessionJournal(warehouse), config=config),
        config,
        assets=DataAssetService(warehouse),
    ))


def test_table_list_hides_raw_by_default_and_can_expand_it(warehouse):
    client = _client(warehouse)
    page = client.get("/data/tables")
    assert page.status_code == 200
    assert "/data/tables/bar_daily" in page.text
    assert "/data/tables/bars_daily" not in page.text
    expanded = client.get("/data/tables?include_raw=true")
    assert "/data/tables/bars_daily" in expanded.text


def test_missing_evidence_is_explicitly_gray(warehouse):
    text = _client(warehouse).get("/data/tables").text
    assert "灰·缺证据" in text
    assert "绿·健康" not in text


def test_blocking_evidence_is_rendered_red_with_reason(warehouse):
    now = datetime.now()
    evidence = tuple(
        HealthEvidence(
            dimension,
            HealthStatus.RED if dimension.value == "validity" else HealthStatus.GREEN,
            f"test.{dimension.value}", now, now + timedelta(days=1),
            HealthReason("test", f"{dimension.value} evidence", dimension),
            int(dimension.value == "validity"), 10,
        )
        for dimension in ALL_DIMENSIONS
    )
    frame = dataset_check_frame("web-test", now, "bar_daily", evidence)
    write_dataset_checks(warehouse, frame)
    response = _client(warehouse).get("/data/tables/bar_daily")
    assert "红·阻断" in response.text
    assert "validity evidence" in response.text
    detail = DataAssetService(warehouse).table("bar_daily")
    assert detail is not None
    assert detail.suggestion.kind is AdviceKind.KEEP_SEPARATE
    assert "<button" not in response.text
    rule = _client(warehouse).get("/data/quality/rules/test.validity")
    assert rule.status_code == 200
    assert "bar_daily" in rule.text


def test_table_detail_answers_contract_lineage_rules_and_consumers(warehouse):
    response = _client(warehouse).get("/data/tables/bar_daily")
    assert response.status_code == 200
    for expected in ("规范历史日线", "每标的每交易日一行", "symbol, dt",
                     "tushare_daily → bar_daily", "消费方", "结构建议",
                     "INSUFFICIENT_EVIDENCE"):
        assert expected in response.text


def test_relation_detail_shows_join_contract_and_empty_evidence(warehouse):
    response = _client(warehouse).get(
        "/data/relations/price_limits_daily/daily_flags"
    )
    assert response.status_code == 200
    assert "symbol=symbol" in response.text
    assert "dt=dt" in response.text
    assert "1:1" in response.text
    assert "尚无关系检查证据" in response.text
    assert "INSUFFICIENT_EVIDENCE" in response.text


def test_rule_detail_shows_definition_and_implementation(warehouse):
    response = _client(warehouse).get("/data/quality/rules/B1.calendar")
    assert response.status_code == 200
    assert "通过条件" in response.text
    assert "实现位置" in response.text
    assert "qsys.data.catalog.rules_complete" in response.text
    assert "规则已定义，但尚未运行" in response.text


def test_unknown_asset_routes_are_404(warehouse):
    client = _client(warehouse)
    assert client.get("/data/tables/nope").status_code == 404
    assert client.get("/data/relations/nope/nope").status_code == 404
    assert client.get("/data/quality/rules/nope").status_code == 404


def test_table_detail_no_longer_renders_table_ids_as_sources(warehouse):
    """commit 708889f 在域详情页修掉的缺陷，逐表页面还原封不动地留着。

    `/data/tables/bar_minute_1m` 曾显示「来源：paid_minute_bars、minute_bars、
    显式异常覆盖」——两个表 id 配一句备注，一个源都没有。而域详情页的表格
    主动链到这个页面，于是用户被送到一个仍在撒谎的页面上。
    """
    text = _client(warehouse).get("/data/tables/bar_minute_1m").text
    # 表 id 带「表：」前缀并可点，不是裸 join
    assert "表：minute_bars" in text
    assert "paid_minute_bars、minute_bars、显式异常覆盖" not in text
    # 自由文本标成备注
    assert '<span class="pill todo">备注</span>' in text
    # 真正的答案：递归解出的根源接口，带 SourceStatus 措辞
    assert "百度网盘付费源" in text
    assert "已停采，历史仍在读" in text


def test_table_detail_shows_the_business_domain(warehouse):
    """`Dataset.domain` 是 Task 2 做成的必填字段，却一直没有页面消费它。"""
    text = _client(warehouse).get("/data/tables/bar_minute_1m").text
    assert "业务域" in text and "行情 Market" in text


def test_table_detail_names_the_unclassified_reason(warehouse, monkeypatch):
    from dataclasses import replace

    from qsys.data.catalog import DATASETS
    from qsys.data.catalog.types import UNCLASSIFIED

    monkeypatch.setitem(
        DATASETS, "bar_minute_1m",
        replace(DATASETS["bar_minute_1m"], domain=UNCLASSIFIED("跨域，待判")),
    )
    text = _client(warehouse).get("/data/tables/bar_minute_1m").text
    assert "未归类：跨域，待判" in text


def test_a_table_whose_root_sources_cannot_be_resolved_says_so(warehouse):
    """`daily_flags.sources` 只有「规范日线与状态事实」一句话。

    解不出就写解不出——不许反推出一个最像的源（MEMORY：禁任何反推与填充）。
    文案只说「未登记到源」而不说是哪种成因：空结果有三种来路（上游只有自由
    文本、上游 id 不在 DATASETS、环被 seen 截断），页面没有派生出是哪一种，
    就不许替它选一个说出来——那本身就是一次反推。
    """
    text = _client(warehouse).get("/data/tables/daily_flags").text
    assert "解不出（上游未登记到源）" in text
