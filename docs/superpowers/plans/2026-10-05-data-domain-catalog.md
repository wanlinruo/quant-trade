# 数据域目录 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让目录能按金融数据的业务分类回答「这个域的数据来自哪些源」与「这个源停了影响哪些域」。

**Architecture:** 给 `Dataset` 加一个与 `layer`/`role` 正交的 `domain` 字段（必填、无默认），47 张表逐张归位；源不加分类而加 `aliases`，域→源由既有的 `Dataset.sources` 推导；页面加 `/data/domains` 域优先入口，并把一直存在却从未显示的 `SourceStatus` 摆出来。

**Tech Stack:** Python 3.12、dataclasses（`frozen`/`slots`/`kw_only`）、polars、FastAPI + Jinja2、pytest。

**Spec:** `docs/superpowers/specs/2026-10-05-data-domain-catalog-design.md`

## Global Constraints

- `Dataset.domain` **必填、无默认值**。拿不准写 `UNCLASSIFIED("理由")`，照 `Metric.checks` 不得为空元组的先例。
- 衍生域命名为 `FACTOR`，**不得叫 `DERIVED`** —— 与 `DatasetLayer.DERIVED` 撞名会让页面上出现两个含义不同的 derived。
- **源不加 `domain` 字段。** 域→源由 `Dataset.sources` 推导，不手工维护第二份映射。
- 空域（MACRO / ALTERNATIVE）必须在页面出现，并带登记日期。
- 分支按 `docs/development-governance.md` §3.2：从最新 `test` 开 `feat/<slug>`，本地 squash 回 `test`。
- 每个任务结束 `uv run pytest` 全绿。

## 对 spec 的一处修正

Spec §6 说三条测试都挂进 `tests/data/catalog/`。**第 2 条（源别名对账）做不到**：
`tests/data/catalog/` 下所有测试都用 `tmp_path` 合成仓库，CI 环境没有 `/data/qsys`。
该检查改挂 `src/qsys/ops/service_checks.py`（跑在节点、有真实 warehouse、结果上服务管理页），
见 Task 3。Spec 其余不变。

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/qsys/data/catalog/types.py` | 新增 `DataDomain`、`Unclassified`/`UNCLASSIFIED`；`Dataset.domain`、`Source.aliases` |
| `src/qsys/data/catalog/datasets.py` | 47 张表的 `domain` 登记；`_physical()` 增参 |
| `src/qsys/data/catalog/sources.py` | `aliases` 登记 |
| `src/qsys/data/catalog/domains.py`（新） | 域→表→源的推导函数，页面与测试共用 |
| `src/qsys/web/domain_views.py`（新） | `/data/domains` 与 `/data/domains/{domain}` |
| `src/qsys/web/templates/domains.html`、`domain_detail.html`（新） | 两个页面 |
| `src/qsys/web/catalog_views.py` | 源列表补状态、别名、所属域 |
| `src/qsys/ops/service_checks.py` | 别名对账质检 |

---

### Task 1: 类型层

**Files:**
- Modify: `src/qsys/data/catalog/types.py`
- Test: `tests/data/catalog/test_types.py`

**Interfaces:**
- Consumes: 无
- Produces: `DataDomain`（StrEnum，7 值）、`Unclassified(reason: str)`、`UNCLASSIFIED(reason) -> Unclassified`、`Dataset.domain: DataDomain | Unclassified`（kw_only 必填）、`Source.aliases: tuple[str, ...] = ()`

- [ ] **Step 1: 写失败的测试**

追加到 `tests/data/catalog/test_types.py`：

```python
import pytest

from qsys.data.catalog.types import (
    DataDomain, DatasetLayer, DatasetRole, DatasetStatus, UNCLASSIFIED, Unclassified,
)


def test_factor_domain_is_not_named_derived():
    """衍生域叫 FACTOR，与 DatasetLayer.DERIVED 区分——同名会让页面出现两个 derived。"""
    assert DataDomain.FACTOR.value == "factor"
    assert "derived" not in {d.value for d in DataDomain}
    assert DatasetLayer.DERIVED.value == "derived"


def test_all_seven_domains_exist():
    assert {d.value for d in DataDomain} == {
        "reference", "market", "fundamental", "factor", "macro", "alternative", "runtime",
    }


def test_unclassified_carries_a_reason():
    sentinel = UNCLASSIFIED("跨行情与基础，待定")
    assert isinstance(sentinel, Unclassified)
    assert sentinel.reason == "跨行情与基础，待定"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/catalog/test_types.py -o addopts="" -q -k "domain or unclassified"`
Expected: FAIL with `ImportError: cannot import name 'DataDomain'`

- [ ] **Step 3: 实现**

`src/qsys/data/catalog/types.py` 顶部的 import 改为：

```python
from dataclasses import dataclass, field
```

在 `class DatasetRole` 之后加入：

```python
class DataDomain(StrEnum):
    """业务含义上的数据分类，与 layer（架构层）、role（建模角色）正交。

    `layer.DERIVED` 说「这张表是算出来的」，`domain.FACTOR` 说「这是信号类数据」。
    `adj_factors` 同时是 layer=RAW 与 domain=FACTOR，两者不是一回事，故不同名。
    """

    REFERENCE = "reference"       # 基础：标的、日历、状态、公司行为
    MARKET = "market"             # 行情：K线、Tick、竞价、涨跌停价
    FUNDAMENTAL = "fundamental"   # 基本面：财报、财务指标、估值、股本
    FACTOR = "factor"             # 衍生：复权因子、技术指标、信号、特征
    MACRO = "macro"               # 宏观：利率、汇率、GDP、CPI
    ALTERNATIVE = "alternative"   # 另类：新闻、舆情、资金流、龙虎榜
    RUNTIME = "runtime"           # 运行与质检：质检结果、成交回报、净值
```

在 `UNCOVERED` 之后加入（照 `Uncovered` 的先例）：

```python
@dataclass(frozen=True, slots=True)
class Unclassified:
    """显式声明「这张表归不进任何业务域」及其理由。

    不设默认值：默认值会把「没想清楚」伪装成「已归类」，而这两者在页面上
    必须能区分。
    """

    reason: str


def UNCLASSIFIED(reason: str) -> Unclassified:
    return Unclassified(reason)
```

`Dataset` 类加字段（**放在 `physical` 之前**，用 `kw_only` 以免打乱既有的位置参数调用）：

```python
    health_policy: str | None
    domain: "DataDomain | Unclassified" = field(kw_only=True)
    physical: bool = True
```

`Source` 类末尾加：

```python
    aliases: tuple[str, ...] = ()   # 别处对同一个源的称呼，如表内 source 列的取值
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/data/catalog/test_types.py -o addopts="" -q`
Expected: 全部 PASS

- [ ] **Step 5: 跑全量**

Run: `uv run pytest -o addopts="" -q`
Expected: **大量 FAIL** —— `Dataset.__init__() missing 1 required keyword-only argument: 'domain'`。
这是预期的：47 张表尚未登记 domain，Task 2 处理。**不要在此处加默认值绕过。**

- [ ] **Step 6: 提交**

```bash
git add src/qsys/data/catalog/types.py tests/data/catalog/test_types.py
git commit -m "feat(catalog): a domain axis that says what kind of data a table holds"
```

---

### Task 2: 47 张表归位

**Files:**
- Modify: `src/qsys/data/catalog/datasets.py`
- Test: `tests/data/catalog/test_dataset_registry.py`

**Interfaces:**
- Consumes: Task 1 的 `DataDomain`、`UNCLASSIFIED`
- Produces: `DATASETS` 中每个 `Dataset` 都带 `domain`

**完整归属表**（spec §4.2，合计 47，无重复无遗漏）：

| 域 | 表 |
|---|---|
| `REFERENCE`(11) | `instruments`、`security_master`、`trading_calendar`、`name_history`、`security_name_history`、`name_snapshots`、`delisting_pool`、`stock_st_daily`、`suspend_daily`、`security_status_daily`、`corporate_actions` |
| `MARKET`(16) | `bar_daily`、`bars_daily`、`daily_bars`、`bar_daily_realtime`、`bar_minute_1m`、`minute_bars`、`paid_minute_bars`、`bar_minute_realtime`、`auction_ticks`、`index_bars`、`tushare_daily`、`paid_daily`、`quote_realtime_latest`、`price_limit_daily`、`price_limits_daily`、`limit_list_daily` |
| `FUNDAMENTAL`(4) | `valuation_daily`、`valuation_snapshot`、`share_capital`、`share_capital_history` |
| `FACTOR`(6) | `adj_factors`、`adjustment_factor_daily`、`daily_flags`、`market_flags_daily`、`market_regime`、`market_regime_daily` |
| `RUNTIME`(10) | `metric_checks`、`dataset_checks`、`relation_checks`、`metric_evidence`、`ingest_log`、`live_fills`、`live_equity`、`live_recon`、`live_rejects`、`strategy` |

- [ ] **Step 1: 写失败的测试**

追加到 `tests/data/catalog/test_dataset_registry.py`：

```python
from collections import Counter

from qsys.data.catalog.types import DataDomain, Unclassified


def test_every_dataset_declares_a_domain():
    """新表不标 domain 就建不起来；Unclassified 必须带理由，不能是空字符串。"""
    for ident, item in DATASETS.items():
        assert isinstance(item.domain, (DataDomain, Unclassified)), ident
        if isinstance(item.domain, Unclassified):
            assert item.domain.reason.strip(), f"{ident}: UNCLASSIFIED 必须说明理由"


def test_domain_distribution_matches_the_spec():
    """数目对不上说明有表漏标或归错——spec §4.2 的表是这份测试的唯一依据。"""
    counts = Counter(
        item.domain.value for item in DATASETS.values() if isinstance(item.domain, DataDomain)
    )
    assert counts == {
        "reference": 11, "market": 16, "fundamental": 4, "factor": 6, "runtime": 10,
    }
    assert sum(counts.values()) == len(DATASETS) == 47


def test_empty_domains_have_no_datasets():
    """宏观与另类当前为空，若将来接入，这条测试会提醒更新 spec 与页面说明。"""
    present = {item.domain for item in DATASETS.values() if isinstance(item.domain, DataDomain)}
    assert DataDomain.MACRO not in present
    assert DataDomain.ALTERNATIVE not in present
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/catalog/test_dataset_registry.py -o addopts="" -q -k domain`
Expected: FAIL —— `DATASETS` 尚无法构建（Task 1 已让 `domain` 必填）

- [ ] **Step 3: 给 `_physical()` 增参**

`src/qsys/data/catalog/datasets.py` 的 `_physical()` 签名，在 `health: str | None = None,` 之后加一行，并在 `return Dataset(...)` 里透传：

```python
    health: str | None = None,
    domain: DataDomain | Unclassified,          # 关键字必填，不给默认值
) -> Dataset:
    return Dataset(
        ...
        health_policy=health,
        domain=domain,
    )
```

文件顶部 import 加上 `DataDomain` 与 `Unclassified`：

```python
from qsys.data.catalog.types import (
    Computed,
    DataDomain as D,
    Dataset,
    DatasetDependency,
    DatasetLayer as L,
    DatasetRole as R,
    DatasetStatus as S,
    ...
)
```

- [ ] **Step 4: 逐条登记**

43 个 `_physical(...)` 调用各加一个 `domain=D.XXX`，5 个直接 `Dataset(...)` 调用各加 `domain=D.XXX`
（后者是位置参数调用，`domain` 为 kw_only，追加在末尾即可）。按上方归属表取值。

两个已完成的样例：

```python
    _physical("instruments", "证券主数据快照", "当前证券代码、市场、板块和上市状态",
              L.CANONICAL, R.SNAPSHOT, S.ACTIVE, "每标的一行当前快照", ("symbol",),
              ("as_of",), partitioned=False,
              builder="qsys.data.pipeline.tushare_reference.ingest_instruments",
              update="全表原子覆盖", sources=("tushare.stock_basic",),
              consumers=("策略股票池", "daily_flags", "Web 数据页"), health="snapshot",
              domain=D.REFERENCE),
```

```python
    Dataset("daily_bars", "生产日线查询视图", "稳定八列历史日线门面", L.CANONICAL,
            R.LOGICAL, S.ACTIVE, "每标的每交易日一行", ("symbol", "dt"), ("dt",), (),
            ("symbol", "dt"), None, 1, "八列契约保持兼容", "WarehouseQuery.daily_bars",
            "查询时投影", "仅包含已同步历史日", "逻辑视图", ("tushare_daily",),
            ..., domain=D.MARKET),
```

**跨域五张按 spec §4.3 判决**，并在该行加一句行内注释写明可另归何处：

```python
              # 主体是首板/炸板标记（我们算的信号）；含 is_st 属 REFERENCE 性质
              domain=D.FACTOR),
```

对应关系：`daily_flags`→FACTOR、`price_limits_daily`→MARKET、`share_capital`→FUNDAMENTAL、
`corporate_actions`→REFERENCE、`valuation_daily`→FUNDAMENTAL。

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/data/catalog/ -o addopts="" -q`
Expected: 全部 PASS。若 `test_domain_distribution_matches_the_spec` 红，**先核对归属表再改数字**——
数目是 spec 的结论，不是可以迁就代码的参数。

- [ ] **Step 6: 跑全量并提交**

```bash
uv run pytest -o addopts="" -q
git add src/qsys/data/catalog/datasets.py tests/data/catalog/test_dataset_registry.py
git commit -m "feat(catalog): file all 47 tables under a business domain"
```

---

### Task 3: 源别名与对账质检

**Files:**
- Modify: `src/qsys/data/catalog/sources.py`
- Modify: `src/qsys/ops/service_checks.py`
- Test: `tests/data/catalog/test_sources.py`、`tests/ops/test_service_checks.py`

**Interfaces:**
- Consumes: Task 1 的 `Source.aliases`
- Produces: `_source_aliases_resolve(ctx) -> CheckResult`，注册为
  `ServiceCheck("catalog-verify", "源别名对得上数据", "bar_minute_1m", _source_aliases_resolve)`

- [ ] **Step 1: 写失败的测试（目录侧）**

追加到 `tests/data/catalog/test_sources.py`：

```python
def test_paid_minute_source_is_findable_by_the_name_the_data_uses():
    """bar_minute_1m.source 列写的是 paid，目录 id 是 baidupan.minute。
    2026-10-05 的教训：按 paid 查不到任何东西。"""
    assert "paid" in SOURCES["baidupan.minute"].aliases


def test_aliases_do_not_collide():
    seen: dict[str, str] = {}
    for source in SOURCES.values():
        for alias in source.aliases:
            assert alias not in seen, f"别名 {alias} 同时属于 {seen[alias]} 与 {source.id}"
            seen[alias] = source.id
        assert source.id not in seen, f"{source.id} 的别名与某个源 id 相同"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/catalog/test_sources.py -o addopts="" -q -k alias`
Expected: FAIL —— `aliases` 为空元组

- [ ] **Step 3: 登记别名**

`src/qsys/data/catalog/sources.py`，给三个源加 `aliases`（取值来自 `bar_minute_1m.source`
列的 distinct 值，2026-10-05 实测为 `paid` / `qmt` / `joinquant`）：

```python
        id="baidupan.minute", provider="百度网盘付费源（余哈哈数据仓库）", api="minute",
        ..., aliases=("paid",),
```

```python
        id="qmt.minute", provider="QMT", api="minute",
        ..., aliases=("qmt",),
```

聚宽源同理加 `aliases=("joinquant",)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/data/catalog/test_sources.py -o addopts="" -q`
Expected: 全部 PASS

- [ ] **Step 5: 写质检的失败测试**

追加到 `tests/ops/test_service_checks.py`（沿用该文件既有的 `FakeQuery` + `ctx()` 模式）：

```python
def test_unknown_source_value_in_data_is_bad(monkeypatch):
    """表里出现目录不认识的源值就报——这是「有人加了源却没登记」的唯一信号。"""
    import qsys.ops.service_checks as sc
    monkeypatch.setattr(sc, "_minute_source_values", lambda ctx: {"paid", "qmt", "newvendor"})
    result = sc._source_aliases_resolve(ctx(FakeQuery()))
    assert result.status == sc.BAD and "newvendor" in result.detail


def test_all_known_source_values_pass(monkeypatch):
    import qsys.ops.service_checks as sc
    monkeypatch.setattr(sc, "_minute_source_values", lambda ctx: {"paid", "qmt", "joinquant"})
    assert sc._source_aliases_resolve(ctx(FakeQuery())).status == sc.OK
```

- [ ] **Step 6: 跑测试确认失败**

Run: `uv run pytest tests/ops/test_service_checks.py -o addopts="" -q -k source_alias`
Expected: FAIL with `AttributeError: module 'qsys.ops.service_checks' has no attribute '_source_aliases_resolve'`

- [ ] **Step 7: 实现质检**

`src/qsys/ops/service_checks.py` 加入（import 处加 `from qsys.data.catalog import SOURCES`）：

```python
def _minute_source_values(ctx: CheckContext) -> set[str]:
    """canonical 分钟层 source 列的全部取值。单独成函数便于测试替换。"""
    frame = ctx.query.bar_minute_1m_sources()
    return set(frame["source"].to_list())


def _source_aliases_resolve(ctx: CheckContext) -> CheckResult:
    """数据里出现的每个 source 值都必须能在目录里找到归属。

    目录按技术 id 登记（`baidupan.minute`），而表里写的是别名（`paid`）。
    两边对不上时，页面按任一个名字都查不到那个源——2026-10-05 实际发生过。
    """
    known = {s.id for s in SOURCES.values()} | {a for s in SOURCES.values() for a in s.aliases}
    seen = _minute_source_values(ctx)
    unknown = sorted(seen - known)
    return CheckResult(
        BAD if unknown else OK, "源别名对得上数据",
        f"{len(unknown)} 个源值在目录里查无此源",
        _samples(set(unknown)), rows=len(seen),
    )
```

在 `CHECKS` 元组里注册：

```python
    ServiceCheck("catalog-verify", "源别名对得上数据", "bar_minute_1m", _source_aliases_resolve),
```

`WarehouseQuery` 加配套方法（`src/qsys/data/query.py`）：

```python
    def bar_minute_1m_sources(self) -> pl.DataFrame:
        """canonical 分钟层出现过的 source 取值。只扫元数据列，不读行情列。"""
        glob = self._glob("bar_minute_1m", partitioned=True)
        if glob is None:
            return pl.DataFrame(schema={"source": pl.Utf8})
        return pl.from_arrow(
            self._con.execute(f"SELECT DISTINCT source FROM {_read(glob)}").arrow()
        )
```

- [ ] **Step 8: 跑测试确认通过并提交**

Run: `uv run pytest -o addopts="" -q`
Expected: 退出码 0

```bash
git add src/qsys/data/catalog/sources.py src/qsys/data/query.py \
        src/qsys/ops/service_checks.py tests/data/catalog/test_sources.py \
        tests/ops/test_service_checks.py
git commit -m "fix(catalog): the data calls it paid, the catalog calls it baidupan.minute"
```

---

### Task 4: 域页面

**Files:**
- Create: `src/qsys/data/catalog/domains.py`
- Create: `src/qsys/web/domain_views.py`
- Create: `src/qsys/web/templates/domains.html`、`src/qsys/web/templates/domain_detail.html`
- Modify: `src/qsys/web/catalog_views.py`、`src/qsys/web/templates/catalog_sources.html`
- Test: `tests/data/catalog/test_domains.py`（新）、`tests/web/test_service_pages.py`

**Interfaces:**
- Consumes: Task 1–3 的 `DataDomain`、`Dataset.domain`、`Source.aliases`
- Produces: `domain_summary() -> tuple[DomainRow, ...]`，`DomainRow(domain, datasets, sources)`
  —— 状态标签由页面层从 `source.status` 现算，不进数据结构

- [ ] **Step 1: 写失败的测试**

新建 `tests/data/catalog/test_domains.py`：

```python
from qsys.data.catalog.domains import domain_summary
from qsys.data.catalog.types import DataDomain


def test_every_domain_appears_including_the_empty_ones():
    """宏观与另类必须出现在总览里——0 张表本身是信息。"""
    rows = {row.domain for row in domain_summary()}
    assert rows == set(DataDomain)


def test_market_domain_names_the_frozen_paid_source():
    """行情域必须显示付费源，且它是 FROZEN——用户曾因此以为它不再使用。"""
    market = next(r for r in domain_summary() if r.domain is DataDomain.MARKET)
    assert "baidupan.minute" in {s.id for s in market.sources}


def test_empty_domains_have_no_sources():
    for row in domain_summary():
        if row.domain in (DataDomain.MACRO, DataDomain.ALTERNATIVE):
            assert row.datasets == () and row.sources == ()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/data/catalog/test_domains.py -o addopts="" -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'qsys.data.catalog.domains'`

- [ ] **Step 3: 实现推导层**

新建 `src/qsys/data/catalog/domains.py`：

```python
"""域视图：按业务分类聚合数据集与其根源。

域→源**算出来、不手工填**：手工填会和 `Dataset.sources` 漂移，而漂移正是
2026-10-05 那次「以为付费源已停用」的成因之一。
"""

from __future__ import annotations

from dataclasses import dataclass

from qsys.data.catalog import DATASETS
from qsys.data.catalog.sources import SOURCES
from qsys.data.catalog.types import DataDomain, Dataset, Source


@dataclass(frozen=True, slots=True)
class DomainRow:
    domain: DataDomain
    datasets: tuple[Dataset, ...]
    sources: tuple[Source, ...]


def domain_summary() -> tuple[DomainRow, ...]:
    """七个域各一行，空域也出现。"""
    rows = []
    for domain in DataDomain:
        datasets = tuple(
            d for d in DATASETS.values()
            if isinstance(d.domain, DataDomain) and d.domain is domain
        )
        source_ids = sorted({s for d in datasets for s in d.sources})
        sources = tuple(SOURCES[i] for i in source_ids if i in SOURCES)
        rows.append(DomainRow(domain, datasets, sources))
    return tuple(rows)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/data/catalog/test_domains.py -o addopts="" -q`
Expected: 3 passed

- [ ] **Step 5: 加路由与模板**

新建 `src/qsys/web/domain_views.py`，照 `catalog_views.py` 的注册方式挂两个路由：

```python
DOMAIN_LABELS = {
    "reference": "基础 Reference", "market": "行情 Market",
    "fundamental": "基本面 Fundamental", "factor": "衍生 Factor",
    "macro": "宏观 Macro", "alternative": "另类 Alternative",
    "runtime": "运行与质检 Runtime",
}

STATUS_LABELS = {
    "active": "在采集",
    "frozen": "已停采，历史仍在读",
    "dead": "上游已失效",
}

# 已知可得但未接入。依赖 Tushare 积分权限这一外部事实，测试验不了，
# 故带登记日期：日期旧了自然会被怀疑，这比假装能验的测试诚实。
NOT_INGESTED = {
    "macro": ("Tushare Pro 当前积分已含宏观经济接口。", "2026-10-05"),
    "alternative": ("龙虎榜、资金流、概念板块、北向在当前积分内可得。", "2026-10-05"),
}
```

`domains.html` 每行显示：域名、表数、数据来自（源 provider + 别名 + 状态标签）、空域的说明与登记日期。
`domain_detail.html` 列该域下的表，每行显示 `layer`、`role`、根源——**三个维度并列显示是有意的**：
测试验不出「归错域」，但人看到 `layer=RAW` 配 `domain=FACTOR` 之类的组合会起疑（spec §7）。

- [ ] **Step 6: 源列表补三列**

`src/qsys/web/catalog_views.py` 的 `SourceRow` 加字段，`catalog_sources.html` 对应补列：

```python
@dataclass(frozen=True, slots=True)
class SourceRow:
    source: Source
    metric_count: int
    metric_ids: tuple[str, ...]
    status_label: str          # domain_views.STATUS_LABELS[source.status]，从该模块 import
    alias_text: str            # "表内写作 paid"，无别名时为空串
    domains: tuple[str, ...]   # 该源支撑的域（由 domain_summary 反查）
```

- [ ] **Step 7: 页面冒烟测试**

追加到 `tests/web/test_service_pages.py`（沿用该文件既有的 TestClient 模式）：

```python
def test_domains_page_lists_empty_domains_with_their_reason(client):
    body = client.get("/data/domains").text
    assert "宏观 Macro" in body and "另类 Alternative" in body
    assert "当前积分内可得" in body


def test_sources_page_says_the_paid_source_is_still_being_read(client):
    body = client.get("/data/catalog/sources").text
    assert "已停采，历史仍在读" in body
    assert "paid" in body        # 别名可见，按表里的名字也能找到
```

- [ ] **Step 8: 跑全量并提交**

```bash
uv run pytest -o addopts="" -q
git add src/qsys/data/catalog/domains.py src/qsys/web/domain_views.py \
        src/qsys/web/templates/domains.html src/qsys/web/templates/domain_detail.html \
        src/qsys/web/catalog_views.py src/qsys/web/templates/catalog_sources.html \
        tests/data/catalog/test_domains.py tests/web/test_service_pages.py
git commit -m "feat(web): a data page that says which domain comes from where"
```

---

## 收尾

四个任务完成后按 §3.2 squash 回 `test` 并 push，CI 全绿后随下一次 `test -> main` 发布 PR。

**部署只需重启 `qsys-monitor`**（生产 Web；§9.2 文档里写的 `qsys-web-prod` 不存在，
2026-10-04 实测确认）。数据同步器、paper、shadow、live core 不受影响，不重启。

## 不在本计划范围

- **canonical 分钟层 21,088 个标的日的填充**（qmt 9,940 + paid 11,147）。已定性为与
  `minute_bars` 同一缺陷，但属数据修复，与目录分类无关，另案处理。
- **接入宏观与另类数据。** 本计划只让"没接"这件事在页面上可见，不接。
