"""表级资产目录必须在空仓库中也可解释，且不伪造健康状态。"""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from qsys.data.catalog.input_versions import dataset_binding
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
    frame = dataset_check_frame("web-test", now, "bar_daily", evidence,
        scope_start=now.date(), scope_end=now.date(),
        input_version=dataset_binding(warehouse, "bar_daily", now.date(), now.date()))
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

    `/data/tables/bar_minute_1m` 曾把覆盖层写成一句自由文本。现在四个上游
    都必须是已登记、可点击并能递归解析到接口的表。
    """
    text = _client(warehouse).get("/data/tables/bar_minute_1m").text
    # 表 id 带「表：」前缀并可点，不是裸 join
    assert "表：minute_bars" in text
    assert "paid_minute_bars、minute_bars、显式异常覆盖" not in text
    assert "表：tushare_minute_bars" in text
    assert "表：joinquant_minute_bars" in text
    assert '<span class="pill todo">备注</span>' not in text
    # 真正的答案：递归解出的根源接口，带 SourceStatus 措辞
    assert "百度网盘付费源" in text
    assert "已停采，历史仍在读" in text
    assert "聚宽（JQData）" in text


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


def _seed_bar_daily(warehouse):
    import pyarrow as pa
    from qsys.data import schema
    from datetime import date

    warehouse.write('bar_daily', pa.Table.from_pylist(
        [{'symbol': '600001.SH', 'dt': date(2026, 10, 19)}], schema=schema.BAR_DAILY),
        year=2026)


def _background_client(warehouse):
    config = WebConfig()
    assets = DataAssetService(warehouse, background_coverage=True)
    app = create_app(MonitorService(SessionJournal(warehouse), config=config),
                     config, assets=assets)
    return TestClient(app), assets


def test_cold_catalog_and_detail_return_while_exact_statistics_are_still_running(warehouse, monkeypatch):
    from threading import Event
    import qsys.web.asset_views as module

    _seed_bar_daily(warehouse)
    started, release = Event(), Event()
    calls = []
    original = module.coverage

    def blocked(wh):
        calls.append(wh)
        started.set()
        assert release.wait(5), 'test did not release worker'
        return original(wh)

    monkeypatch.setattr(module, 'coverage', blocked)
    client, assets = _background_client(warehouse)
    try:
        page = client.get('/data/tables')
        assert started.wait(1)
        assert page.status_code == 200
        assert '统计中' in page.text
        assert '灰·缺证据' in page.text
        detail = client.get('/data/tables/bar_daily')
        assert detail.status_code == 200
        assert '统计中' in detail.text
        assert '未落盘' not in detail.text
        assert len(calls) == 1  # 两个页面共享一次实际统计。
        assert 'location.reload' not in page.text  # 异步更新单元格，不打断阅读。
    finally:
        release.set()
        assets._coverage_thread.join(5)
    ready = client.get('/data/tables/bar_daily')
    assert '1 行' in ready.text
    assert 'data-coverage-pending="false"' in ready.text
    assert '灰·缺证据' in ready.text  # 统计完成不能冒充质量检查通过。


def test_statistics_failure_is_not_rendered_as_missing_data_or_retried_on_every_request(warehouse, monkeypatch):
    import qsys.web.asset_views as module

    _seed_bar_daily(warehouse)
    calls = []

    def fail(wh):
        calls.append(wh)
        raise OSError('cannot read statistics')

    monkeypatch.setattr(module, 'coverage', fail)
    client, assets = _background_client(warehouse)
    client.get('/data/tables')
    assets._coverage_thread.join(5)
    detail = client.get('/data/tables/bar_daily')
    assert detail.status_code == 200
    assert '统计暂不可用' in detail.text
    assert '未落盘' not in detail.text
    assert '灰·缺证据' in detail.text
    assert len(calls) == 1


def test_changed_table_shows_timestamped_previous_count_while_refreshing(warehouse, monkeypatch):
    from qsys.data import schema
    import qsys.web.asset_views as module
    from threading import Event

    _seed_bar_daily(warehouse)
    module.coverage(warehouse)
    warehouse.write('bar_daily', schema.BAR_DAILY.empty_table(), year=2026)
    release = Event()
    original = module.coverage

    def blocked(wh):
        assert release.wait(5)
        return original(wh)

    monkeypatch.setattr(module, 'coverage', blocked)
    client, assets = _background_client(warehouse)
    try:
        page = client.get('/data/tables/bar_daily')
        assert '1 行' in page.text
        assert '上次统计' in page.text and '更新中' in page.text
        assert 'data-coverage-pending="true"' in page.text
    finally:
        release.set()
        assets._coverage_thread.join(5)
    page = client.get('/data/tables/bar_daily')
    assert '0 行' in page.text
    assert '上次统计' not in page.text


def test_changed_input_is_gray_but_historical_red_is_still_visible(warehouse):
    from qsys.data import schema
    now=datetime.now()
    evidence=tuple(HealthEvidence(
        dim,HealthStatus.RED,'version.'+dim.value,now,now+timedelta(days=1),
        HealthReason('test','historical failure retained',dim),checked_rows=1,
    ) for dim in ALL_DIMENSIONS)
    write_dataset_checks(warehouse,dataset_check_frame(
        'version-web',now,'bar_daily',evidence,scope_start=now.date(),scope_end=now.date(),
        input_version=dataset_binding(warehouse,'bar_daily',now.date(),now.date()),
    ))
    warehouse.write('tushare_daily',schema.TUSHARE_DAILY.empty_table(),year=now.year)
    page=_client(warehouse).get('/data/tables/bar_daily')
    assert page.status_code==200
    assert '灰·缺证据' in page.text and '输入已变化，待重验' in page.text
    assert 'historical failure retained' in page.text
    assert 'pill red' in page.text


def test_relation_page_reports_current_input_change_without_rewriting_history(warehouse):
    from qsys.data import schema
    from qsys.data.cutover_health import generate_cutover_relations
    from qsys.data.query import WarehouseQuery
    now=datetime.now()
    generate_cutover_relations(warehouse,now.date(),now.date(),now=now)
    old=WarehouseQuery(warehouse).relation_checks()
    warehouse.write('bar_daily',schema.BAR_DAILY.empty_table(),year=now.year)
    page=_client(warehouse).get('/data/relations/bar_daily/security_status_daily')
    assert page.status_code==200
    assert 'pill gray' in page.text and '输入已变化，待重验' in page.text
    assert WarehouseQuery(warehouse).relation_checks().equals(old)
