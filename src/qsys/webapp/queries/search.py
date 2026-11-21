"""全局搜索只读索引；各来源独立失败，缺一类不阻断其余结果。"""

from __future__ import annotations

from qsys.data.catalog import DATASETS, REGISTRY
from qsys.ops.services import SERVICES
from qsys.webapp.contracts.search import SearchIndex, SearchItem


class SearchQuery:
    def __init__(self, *, research=None, trading=None, alerts=None, operations=None) -> None:
        self.research, self.trading, self.alerts, self.operations = research, trading, alerts, operations

    def get(self) -> SearchIndex:
        items: list[SearchItem] = []
        missing: dict[str, str] = {}
        items.extend(SearchItem(kind="服务", key=s.id, label=s.name, hint=s.schedule, page="services", params={}) for s in SERVICES)
        items.extend(SearchItem(kind="数据表", key=d.id, label=d.name, hint=d.purpose, page="tables", params={"id": d.id}) for d in DATASETS.values())
        items.extend(SearchItem(kind="指标", key=m.id, label=m.id, hint=m.meaning or "含义未登记", page="catalog", params={"id": m.id}) for m in REGISTRY.values())
        if self.research is not None:
            try:
                view = self.research.strategies()
                if getattr(view, "enabled", False):
                    items.extend(SearchItem(kind="策略", key=x.id, label=x.name, hint=f"版本 {x.version_id} · {x.stage_label}", page="strategies", params={"id": x.id}) for x in view.strategies)
            except Exception:
                missing["strategies"] = "策略索引不可读"
        if self.trading is not None:
            try:
                view = self.trading.orders(None)
                if getattr(view, "enabled", False):
                    items.extend(SearchItem(kind="委托", key=x.id, label=x.id, hint=f"{x.symbol} · {x.status}", page="orders", params={"order": x.id}) for x in view.orders)
            except Exception:
                missing["orders"] = "委托索引不可读"
        if self.alerts is not None:
            try:
                view = self.alerts.center(None)
                items.extend(SearchItem(kind="告警", key=f"{x.key}|{x.subject}", label=x.title, hint=f"{x.source} · {x.status}", page="alerts", params={"key": x.key, "subject": x.subject}) for x in view.items)
            except Exception:
                missing["alerts"] = "告警索引不可读"
        if self.operations is not None:
            try:
                view = self.operations.nodes()
                if getattr(view, "enabled", False):
                    items.extend(SearchItem(kind="节点", key=x.id, label=x.label, hint=f"{x.platform} · {x.state}", page="nodes", params={"node": x.id}) for x in view.nodes)
            except Exception:
                missing["nodes"] = "节点索引不可读"
        return SearchIndex(items=items, missing=missing)
