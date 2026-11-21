from types import SimpleNamespace

from qsys.webapp.queries.search import SearchQuery


def test_search_combines_business_indexes_without_reading_service_runs():
    research = SimpleNamespace(strategies=lambda: SimpleNamespace(
        enabled=True, strategies=[SimpleNamespace(id="s1", name="策略一", version_id="v1", stage_label="回测")]))
    trading = SimpleNamespace(orders=lambda _day: SimpleNamespace(
        enabled=True, orders=[SimpleNamespace(id="o1", symbol="600000.SH", status="accepted")]))
    alerts = SimpleNamespace(center=lambda _day: SimpleNamespace(items=[SimpleNamespace(
        key="observer.x", subject="linux", title="链路警告", source="observer", status="unhandled")]))
    operations = SimpleNamespace(nodes=lambda: SimpleNamespace(
        enabled=True, nodes=[SimpleNamespace(id="linux", label="Linux", platform="linux", state="ok")]))

    result = SearchQuery(research=research, trading=trading, alerts=alerts, operations=operations).get()

    by_kind = {kind: [x for x in result.items if x.kind == kind] for kind in {x.kind for x in result.items}}
    assert by_kind["策略"][0].params == {"id": "s1"}
    assert by_kind["委托"][0].page == "orders"
    assert by_kind["告警"][0].label == "链路警告"
    assert by_kind["节点"][0].key == "linux"
    assert by_kind["服务"]  # 来自受管静态清单，不触发昂贵 services() 查询
    assert by_kind["数据表"]
    assert by_kind["指标"]
    assert result.missing == {}


def test_search_keeps_other_kinds_when_one_source_fails():
    broken = SimpleNamespace(strategies=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    result = SearchQuery(research=broken).get()
    assert result.missing == {"strategies": "策略索引不可读"}
    assert any(x.kind == "服务" for x in result.items)
