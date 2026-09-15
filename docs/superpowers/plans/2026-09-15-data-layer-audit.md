# 数据层大盘点 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立覆盖 206 个落盘字段与 10 个策略侧决策量的指标字典、可反复重跑的三类验证规则、以及把两者摆出来的数据层页面。

**Architecture:** 字典是代码（`src/qsys/data/catalog/`），不是文档或配置——只有代码能被测试守住「schema 加了列而字典没登记」。验证规则引用字典里的 metric id，结果落 warehouse 两张新表；页面 import 字典、查结果表，两者拼成视图。字典 → 验证 → 页面是单向依赖，没有回边。

**Tech Stack:** Python 3.11+、polars、pyarrow、duckdb、FastAPI + Jinja2、pytest

**Spec:** `docs/superpowers/specs/2026-09-15-data-layer-audit-design.md`

## Global Constraints

这些约束适用于本计划的**每一个**任务，不再逐任务重复：

- **只许直取或纯派生，拿不到就记 null，不准填充。** 任何 `fill_null` / `fillna` / `or 0` / `.get(k, 0)` 都必须有显式理由，否则就是缺陷。
- **不准调用生产函数来验生产函数。** A 类规则的重算必须是独立实现，两条路径都错才漏得过去。
- **找不到依据就写 `UNKNOWN`，不准编一句听起来合理的中文。** 依据只认三种：`schema.py` / pipeline 既有注释、`docs/reference/` 既有文档、读 pipeline 实现代码确认。
- **全历史任务必须逐年处理。** 生产节点 7.5 GiB，2026-09-13 已被 OOM killer 杀过一次。照 `tushare_reconcile.py` 的逐年模式写。
- **验证器崩溃也要写行**（`passed=false, detail="runner error: ..."`），不得静默跳过——什么都不写在页面上与「今天没安排跑」无法区分。
- **`checked_rows` 是必填分母。** 没有分母的 `violations` 数不构成信息。
- **冻结表不可写。** `paid_daily` / `bars_daily` 不得注册进 `schema.PARTITIONED` / `UNPARTITIONED`。
- **已知缺陷不重复阻断**：规则失败时若指标的 `known_issues` 含对应 D-x 编号，severity 降为 `warning`；未登记的新失败才 `blocking`。
- 测试跑法：`.venv/bin/python -m pytest <路径> -v`。标识符用英文，docstring 与注释用中文，与现有代码一致。

---

## 文件结构

**新建：**

| 文件 | 职责 |
|---|---|
| `src/qsys/data/catalog/types.py` | `Metric` / `Source` / `Direct` / `Computed` 数据类与 5 个枚举 |
| `src/qsys/data/catalog/sources.py` | 24 个源实体 |
| `src/qsys/data/catalog/market.py` | 行情类 90 条 |
| `src/qsys/data/catalog/reference.py` | 参考类 49 条 |
| `src/qsys/data/catalog/derived.py` | 派生类 20 条 |
| `src/qsys/data/catalog/factors.py` | 因子类 25 条（落盘 15 + 策略侧 10） |
| `src/qsys/data/catalog/operational.py` | 运行时类 32 条 |
| `src/qsys/data/catalog/__init__.py` | 聚合 `REGISTRY` / `SOURCES`，派生 `consumers` / `root_sources` |
| `src/qsys/data/catalog/checks.py` | Check 类型、规则注册表、runner |
| `src/qsys/data/catalog/rules_truth.py` | A 类规则 |
| `src/qsys/data/catalog/rules_complete.py` | B 类规则 |
| `src/qsys/data/catalog/rules_valid.py` | C 类规则 |
| `src/qsys/web/catalog_views.py` | `CatalogService` |
| `src/qsys/web/templates/catalog_overview.html` | 总览页 |
| `src/qsys/web/templates/catalog_detail.html` | 详情页 |
| `src/qsys/web/templates/catalog_sources.html` | 按源页 |
| `scripts/verify_metrics.py` | 验证器 CLI |

**修改：**

| 文件 | 改什么 |
|---|---|
| `src/qsys/data/schema.py` | 加 `METRIC_CHECKS` / `METRIC_EVIDENCE` 并注册进 `PARTITIONED` |
| `src/qsys/data/query.py` | 加 `metric_checks()` / `metric_evidence()` 两个读取方法 |
| `src/qsys/web/app.py` | 加三条 catalog 路由 |
| `src/qsys/web/templates/base.html` | nav 加「数据层」，加灰色 pill 样式 |
| `scripts/run_monitor.py` | 接线 `CatalogService` |
| `src/qsys/data/feed.py:84-89` | D-9：三处 `fill_null(1.0)` 改 null 传播 |
| `scripts/tushare_daily_sync.py` | 同步成功后挂增量验证 |
| `docs/deploy/linux-core.md` | 记新的调度行为与部署禁忌 |
| `docs/reference/deferred-corrections.md` | D-9 结案 |

---

## Task 1: 字典的类型定义

**Files:**
- Create: `src/qsys/data/catalog/types.py`
- Create: `src/qsys/data/catalog/__init__.py`（本任务只放一行 docstring，Task 3 填内容）
- Test: `tests/data/catalog/test_types.py`

**Interfaces:**
- Consumes: 无
- Produces: `Kind` / `Validity` / `MetricStatus` / `SourceStatus` / `Access`（StrEnum）；
  `Source` / `Direct` / `Computed` / `Metric` / `Uncovered`（frozen slots dataclass）；
  `UNCOVERED(reason: str) -> Uncovered`；常量 `UNKNOWN: str`。
  `Metric.dataset -> str`、`Metric.column -> str` 是 property。

- [ ] **Step 1: 建目录与测试文件，写失败的测试**

```bash
mkdir -p src/qsys/data/catalog tests/data/catalog
printf '"""指标字典。"""\n' > src/qsys/data/catalog/__init__.py
touch tests/data/catalog/__init__.py
```

`tests/data/catalog/test_types.py`：

```python
"""字典类型的构造期约束。

这些约束是第一道闸门：类型错配在构造时就炸，而不是等到某条规则跑出
奇怪结果才发现。
"""

import pytest

from qsys.data.catalog.types import (
    UNCOVERED,
    UNKNOWN,
    Access,
    Computed,
    Direct,
    Kind,
    Metric,
    MetricStatus,
    Source,
    SourceStatus,
    Validity,
)


def _direct_metric(**over) -> Metric:
    base = dict(
        id="tushare_daily.close",
        kind=Kind.DIRECT,
        validity=Validity.SESSION,
        status=MetricStatus.ACTIVE,
        meaning="不复权收盘价",
        lineage=Direct(source_id="tushare.daily", field="close"),
        checks=("A1.dimension",),
    )
    base.update(over)
    return Metric(**base)


def test_splits_id_into_dataset_and_column():
    m = _direct_metric()
    assert m.dataset == "tushare_daily"
    assert m.column == "close"


def test_direct_metric_must_carry_direct_lineage():
    with pytest.raises(ValueError, match="DIRECT"):
        _direct_metric(lineage=Computed(upstream=("a.b",), formula="x"))


def test_derived_metric_must_not_claim_a_source_field():
    """DIRECT 不得有上游、DERIVED 不得直接指源——这是分类判据的构造期形态。"""
    with pytest.raises(ValueError, match="DERIVED"):
        _direct_metric(kind=Kind.DERIVED)


def test_empty_checks_tuple_is_rejected():
    """空元组分不清「忘了挂」和「确实不需要」，必须写 UNCOVERED。"""
    with pytest.raises(ValueError, match="UNCOVERED"):
        _direct_metric(checks=())


def test_uncovered_records_a_reason():
    m = _direct_metric(checks=UNCOVERED("运行时日志，无外部真值可比"))
    assert m.checks.reason == "运行时日志，无外部真值可比"


def test_unknown_meaning_is_a_shared_sentinel():
    """UNKNOWN 必须是可被页面精确识别的哨兵，不是随手写的中文。"""
    assert _direct_metric(meaning=UNKNOWN).meaning is UNKNOWN


def test_metric_status_and_source_status_are_distinct_enums():
    """bars_daily 是 Metric.FROZEN 而其源 mootdx 是 Source.DEAD——
    指标冻结是我们的决定，源死亡是上游的事实，不可混为一谈。"""
    assert MetricStatus.FROZEN != SourceStatus.FROZEN
    assert not isinstance(SourceStatus.DEAD, MetricStatus)


def test_source_carries_access_and_expiry():
    s = Source(
        id="tushare.daily", provider="Tushare Pro", api="daily",
        access=Access.API, status=SourceStatus.ACTIVE,
        credential="config/deploy.local.toml [tushare]", tier="2000 积分",
        acquired="2026-09-13 购入 ¥500/年", cadence="工作日 17:00 + 19:00",
    )
    assert s.access is Access.API
    assert s.expires is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_types.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'qsys.data.catalog.types'`

- [ ] **Step 3: 写实现**

`src/qsys/data/catalog/types.py`：

```python
"""指标字典的类型。

字典是**代码**不是文档，因为只有代码能被测试守住「schema 加了列而字典
没登记」。三个分类判据（见 spec §3.1）在这里落成构造期约束：类型与血缘
不匹配直接抛错，不给「先写着回头再改」留口子。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

# 含义待查的哨兵。写 UNKNOWN 是合规行为，编一句听起来合理的中文才是违规。
# 页面按 `is UNKNOWN` 精确识别并标红，所以必须共用这一个对象。
UNKNOWN = "UNKNOWN(待查)"


class Kind(StrEnum):
    """spec §3.1：直取看能否指到源字段；派生看有无自由参数；
    有自由参数或口径选择的是因子。"""

    DIRECT = "direct"
    DERIVED = "derived"
    FACTOR = "factor"


class Validity(StrEnum):
    """这个值在什么范围内成立。防的是拿点观测去填历史（结转）。"""

    SESSION = "session"  # 该交易日成立
    POINT = "point"      # 仅观测日成立，如 name_snapshots.name
    SPAN = "span"        # 区间内成立，如 name_history 的名称区间


class MetricStatus(StrEnum):
    """这个**指标**是在产、已冻结、还是运行时日志。"""

    ACTIVE = "active"
    FROZEN = "frozen"
    OPERATIONAL = "operational"


class SourceStatus(StrEnum):
    """这个**源**还能不能取到新数据。与 MetricStatus 取值部分同名但语义
    不同，故意分成两个枚举——bars_daily 是 Metric.FROZEN（我们的决定）
    而其源 mootdx.kline 是 Source.DEAD（上游的事实）。"""

    ACTIVE = "active"
    DEAD = "dead"
    FROZEN = "frozen"


class Access(StrEnum):
    API = "api"            # 自动接口
    FILE = "file"          # 人工转存文件
    REALTIME = "realtime"  # 实时自采，漏一天永久缺一天


@dataclass(frozen=True, slots=True)
class Uncovered:
    """显式声明「这条指标没有验证规则」及其理由。

    `checks=()` 不允许：空元组分不清「忘了挂」和「确实不需要」，
    而这两者在页面上必须能区分。
    """

    reason: str


def UNCOVERED(reason: str) -> Uncovered:
    return Uncovered(reason)


@dataclass(frozen=True, slots=True)
class Source:
    id: str                    # "tushare.daily_basic"
    provider: str              # "Tushare Pro"
    api: str                   # "daily_basic"
    access: Access
    status: SourceStatus
    credential: str | None = None
    tier: str | None = None    # 权限门槛，如 "2000 积分"
    acquired: str | None = None
    cadence: str | None = None
    caveat: str | None = None  # 已知的时点/口径坑
    expires: date | None = None  # C5 凭据过期告警用


@dataclass(frozen=True, slots=True)
class Direct:
    """直取血缘：指到唯一的 (源, 字段)，最多一次纯乘法换算。

    抽不出这个三元组、代码里有条件分支的字段**本来就不该叫直取**，
    归 DERIVED（spec §3.1 的反向作用）。
    """

    source_id: str
    field: str
    multiplier: float = 1.0
    note: str | None = None


@dataclass(frozen=True, slots=True)
class Computed:
    """派生/因子血缘。`params` 非空基本意味着它是 FACTOR 而非 DERIVED。"""

    upstream: tuple[str, ...]  # metric id
    formula: str               # 人能读的公式
    params: tuple[tuple[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class Metric:
    id: str
    kind: Kind
    validity: Validity
    status: MetricStatus
    meaning: str
    lineage: Direct | Computed
    checks: tuple[str, ...] | Uncovered
    known_issues: tuple[str, ...] = ()
    ext_consumers: tuple[str, ...] = ()  # 非指标消费方：策略 Gate / 回测 / 页面
    landed: bool = True  # False = 策略侧不落盘的决策量

    def __post_init__(self) -> None:
        if self.kind is Kind.DIRECT and not isinstance(self.lineage, Direct):
            raise ValueError(f"{self.id}: DIRECT 必须带 Direct 血缘")
        if self.kind is not Kind.DIRECT and not isinstance(self.lineage, Computed):
            raise ValueError(
                f"{self.id}: {self.kind.name} 必须带 Computed 血缘，不得直接指源字段"
            )
        if isinstance(self.checks, tuple) and not self.checks:
            raise ValueError(f"{self.id}: checks 不得为空元组，请写 UNCOVERED(理由)")

    @property
    def dataset(self) -> str:
        return self.id.split(".", 1)[0]

    @property
    def column(self) -> str:
        return self.id.split(".", 1)[1]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_types.py -v`
Expected: 9 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/catalog/ tests/data/catalog/
git commit -m "feat(catalog): 指标字典的类型定义与构造期约束"
```

---

## Task 2: 21 个源实体

**Files:**
- Create: `src/qsys/data/catalog/sources.py`
- Test: `tests/data/catalog/test_sources.py`

**Interfaces:**
- Consumes: Task 1 的 `Source` / `Access` / `SourceStatus`
- Produces: `SOURCES: dict[str, Source]`，键为 source id（如 `"tushare.daily"`）

- [ ] **Step 1: 写失败的测试**

`tests/data/catalog/test_sources.py`：

```python
"""源清单。字典里每一条直取血缘都要指到这里的某个 id。"""

from qsys.data.catalog.sources import SOURCES
from qsys.data.catalog.types import Access, SourceStatus


def test_ids_match_their_provider_and_api():
    """id 必须是 provider 前缀 + api，否则页面上两处显示会对不上。"""
    for sid, s in SOURCES.items():
        assert sid == s.id
        assert sid.endswith("." + s.api), sid


def test_covers_all_ten_tushare_apis():
    """实清过：pipeline 里引用了正好这 10 个 Tushare 接口。"""
    assert {s.api for s in SOURCES.values() if s.provider == "Tushare Pro"} == {
        "daily", "daily_basic", "adj_factor", "stk_limit", "suspend_d",
        "limit_list_d", "index_daily", "stock_basic", "namechange", "trade_cal",
    }


def test_has_twentyone_sources():
    assert len(SOURCES) == 21


def test_mootdx_is_dead():
    """2026-09-10 起对全部服务器返回 None，不是「暂时不稳」。"""
    assert SOURCES["mootdx.kline"].status is SourceStatus.DEAD


def test_qmt_sources_are_realtime_and_say_so():
    """实时自采漏一天永久缺一天，caveat 必须写明，否则没人知道它不能补。"""
    for sid in ("qmt.minute", "qmt.tick"):
        assert SOURCES[sid].access is Access.REALTIME
        assert SOURCES[sid].caveat


def test_every_dead_or_frozen_source_explains_itself():
    for s in SOURCES.values():
        if s.status is not SourceStatus.ACTIVE:
            assert s.caveat, f"{s.id} 非 ACTIVE 却没写 caveat"


def test_tushare_expiry_is_recorded_for_the_credential_check():
    """C5 要靠这个日期提前告警——积分一年有效、到期回收，
    到期未续费则日线源直接中断。"""
    daily = SOURCES["tushare.daily"]
    assert daily.expires is not None
    assert daily.expires.year == 2027


def test_limit_list_records_both_of_its_known_traps():
    """D-11（不含 ST）与 D-12（发布晚于 17:00）都必须在 caveat 里。"""
    caveat = SOURCES["tushare.limit_list_d"].caveat
    assert "ST" in caveat
    assert "19:00" in caveat


def test_paid_daily_is_frozen_but_still_the_only_historical_name_source():
    s = SOURCES["baidupan.daily"]
    assert s.status is SourceStatus.FROZEN
    assert s.access is Access.FILE
    assert "名称" in s.caveat
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_sources.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'qsys.data.catalog.sources'`

- [ ] **Step 3: 写实现**

`src/qsys/data/catalog/sources.py`：

```python
"""全库 21 个源。

「来源＝Tushare」这种粒度会把真问题盖掉：tushare_daily 那 28 列里 9 列来自
`daily`、15 列来自 `daily_basic`、1 列来自 `adj_factor`，到货时点也不同
（D-12 实测 limit_list_d 17:02 为空、19:00 才有，而 daily 17:02 已齐）。
所以源登记到**接口**级。
"""

from __future__ import annotations

from datetime import date

from qsys.data.catalog.types import Access, Source, SourceStatus

# Tushare Pro 积分一年有效、到期回收。2026-09-13 购入 5000 积分 ¥500/年。
_TUSHARE_EXPIRES = date(2027, 9, 13)
_TUSHARE_CRED = "config/deploy.local.toml [tushare]"
_TUSHARE_ACQUIRED = "2026-09-13 购入 5000 积分 ¥500/年，积分到期回收"
_WORKDAY_TWICE = "工作日 17:00 + 19:00（Asia/Shanghai）"


def _tushare(api: str, tier: str, cadence: str, caveat: str | None = None) -> Source:
    return Source(
        id=f"tushare.{api}", provider="Tushare Pro", api=api,
        access=Access.API, status=SourceStatus.ACTIVE,
        credential=_TUSHARE_CRED, tier=tier, acquired=_TUSHARE_ACQUIRED,
        cadence=cadence, caveat=caveat, expires=_TUSHARE_EXPIRES,
    )


def _akshare(api: str, caveat: str | None = None) -> Source:
    return Source(
        id=f"akshare.{api}", provider="akshare", api=api,
        access=Access.API, status=SourceStatus.ACTIVE,
        credential=None, tier="免费", acquired="开源库，无凭据",
        cadence="按需调用", caveat=caveat,
    )


_ALL = [
    # ---- Tushare Pro，10 个接口 ----
    _tushare("daily", "2000 积分", _WORKDAY_TWICE,
             "vol 单位是手且带小数（582305.98），取整会丢真实精度；"
             "amount 单位千元，入库 ×1000 转元"),
    _tushare("daily_basic", "2000 积分", _WORKDAY_TWICE,
             "股本列单位万股、市值列单位万元，入库均 ×10000；"
             "total_share 必须直取，反算会让 A+H 股差上亿股"),
    _tushare("adj_factor", "2000 积分", _WORKDAY_TWICE,
             "源字段自身不自洽：非股改期有 688 条价格反推对不上直取值，"
             "另有 12 只「价格有除权缺口而因子不跳」的漏除权，见 D-1"),
    _tushare("stk_limit", "2000 积分", _WORKDAY_TWICE,
             "涨跌停价真值，含 ST 的 5% 档"),
    _tushare("suspend_d", "2000 积分", _WORKDAY_TWICE,
             "suspend_timing 在全天停牌时为 null，不是缺失"),
    _tushare("limit_list_d", "5000 积分", _WORKDAY_TWICE,
             "**不含 ST 股票**（D-11：与自算涨停的差集精确等于当日 ST 涨停集）；"
             "发布时点晚于 17:00，实测 17:02 取到 0 行而 19:00 取到 55 行（D-12）；"
             "实测数据自 2020 年起"),
    _tushare("index_daily", "2000 积分", _WORKDAY_TWICE,
             "vol 含小数（全历史 5,270 行中实测 186 行），schema 已用 float64"),
    _tushare("stock_basic", "2000 积分", _WORKDAY_TWICE,
             "返回的是**当前**名称，只对取数当天成立；"
             "拿它填历史是结转，填取快照那一天才是直接观测"),
    _tushare("namechange", "2000 积分", _WORKDAY_TWICE,
             "只记录变更事件不是逐日快照；2006-10-08 之后大量标的没有后继区间"
             "（D-10 的 2006 断层），单日实测 96 只无名称"),
    _tushare("trade_cal", "120 积分", "按需调用", None),

    # ---- akshare，6 个端点，免费 ----
    _akshare("tool_trade_date_hist_sina"),
    _akshare("stock_info_a_code_name"),
    _akshare("stock_info_sh_delist"),
    _akshare("stock_info_sz_delist"),
    _akshare("stock_zh_a_spot_em"),
    _akshare("stock_fhps_detail_em",
             "分红送转明细。第三方未经验证的源，只作旁证，"
             "不得用它裁决 Tushare 的因子对错"),

    # ---- QMT 实时自采，2 个 ----
    Source(
        id="qmt.minute", provider="QMT", api="minute",
        access=Access.REALTIME, status=SourceStatus.ACTIVE,
        credential="Windows 节点 xtquant", tier="券商账户",
        acquired="实时行情通道", cadence="每交易日收盘后导出",
        caveat="分钟线滚动保留约一年，漏一天丢一天",
    ),
    Source(
        id="qmt.tick", provider="QMT", api="tick",
        access=Access.REALTIME, status=SourceStatus.ACTIVE,
        credential="Windows 节点 xtquant", tier="券商账户",
        acquired="实时行情通道", cadence="每交易日收盘后导出",
        caveat="**买不到的东西**：tick 只回溯约 20 个交易日（实测边界 2026-08-10，"
               "锚点 2026-09-08），漏三周就全丢。Tushare 的 stk_auction 是 9:25 "
               "撮合完成后的结果，口径对不上，替代会引入前视偏差（D-7）",
    ),

    # ---- 百度网盘付费源，人工转存，已冻结 ----
    Source(
        id="baidupan.daily", provider="百度网盘付费源（余哈哈数据仓库）", api="daily",
        access=Access.FILE, status=SourceStatus.FROZEN,
        credential="人工转存", tier="付费",
        acquired="2026-09-11 前的历史快照", cadence="已停止更新",
        caveat="实测就是 Tushare 转售（28 列字段级吻合，18,224,238 行逐键零差异）。"
               "**不能删**：2026-09-11 及之前的**名称**真值只有它一个源，"
               "删了 867 个首板会重新消失（D-10）",
    ),
    Source(
        id="baidupan.minute", provider="百度网盘付费源（余哈哈数据仓库）", api="minute",
        access=Access.FILE, status=SourceStatus.FROZEN,
        credential="人工转存", tier="付费",
        acquired="2009–2025 历史分钟", cadence="已停止更新",
        caveat="历史分钟线一次性导入，增量走 qmt.minute",
    ),

    # ---- mootdx，已死 ----
    Source(
        id="mootdx.kline", provider="mootdx", api="kline",
        access=Access.API, status=SourceStatus.DEAD,
        credential=None, tier="免费",
        acquired="开源库", cadence="已失效",
        caveat="2026-09-10 起对**全部服务器**返回 None，每次调用等 3.8 秒后返回空表。"
               "不是暂时不稳，是上游失效；bars_daily 因此冻结",
    ),
]

SOURCES: dict[str, Source] = {s.id: s for s in _ALL}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_sources.py -v`
Expected: 9 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/catalog/sources.py tests/data/catalog/test_sources.py
git commit -m "feat(catalog): 登记 21 个源到接口级，含各自的已知坑"
```

---

## Task 3: 聚合入口与前两条守护测试

本任务立起 registry 的聚合层，并写下 T1（完备性）与 T6（冻结表不可写）。
此时条目一条都还没有，所以 T1 会**红着**——它红到 Task 8 结束为止，
是这段建设期唯一的进度指示器。这是有意为之：不设允许清单，就不会有人忘了拆。

**Files:**
- Modify: `src/qsys/data/catalog/__init__.py`
- Test: `tests/data/catalog/test_guards.py`

**Interfaces:**
- Consumes: Task 1 的 `Metric`，Task 2 的 `SOURCES`
- Produces:
  - `REGISTRY: dict[str, Metric]` —— 全部指标，键为 metric id
  - `SOURCES: dict[str, Source]` —— 转出 Task 2 的
  - `OWNED_DATASETS: frozenset[str]` —— 各条目模块声明自己负责的数据集
  - `expected_metric_ids() -> set[str]` —— 由 `schema.ALL` + `PAID_DAILY` 展开
  - `landed() -> dict[str, Metric]`、`unlanded() -> dict[str, Metric]`

- [ ] **Step 1: 写失败的测试**

`tests/data/catalog/test_guards.py`：

```python
"""registry 的守护测试。

这些测试是字典不腐烂的全部依靠。每一条都对应一种真实事故，
删任何一条都等于把那种事故放回来。
"""

from qsys.data import schema
from qsys.data.catalog import (
    OWNED_DATASETS,
    REGISTRY,
    expected_metric_ids,
    landed,
)


def test_expected_ids_cover_schema_and_paid_daily():
    """206 = schema.ALL 的 178 列 + PAID_DAILY 的 28 列。

    paid_daily 故意不在 schema.ALL 里（那会让 Warehouse.write 成为可能），
    但它是 2026-09-11 前名称的唯一真值源，必须进字典。
    """
    assert len(expected_metric_ids()) == 206


def test_registry_covers_exactly_the_datasets_it_claims_to_own():
    """条目模块声明负责哪些表，就必须把那些表的列登记完整。

    这条在建设期就能跑绿——它管的是「已认领的有没有做完」。
    """
    for dataset in OWNED_DATASETS:
        cols = set(schema.ALL[dataset].names) if dataset in schema.ALL else set(
            schema.PAID_DAILY.names
        )
        got = {m.column for m in REGISTRY.values() if m.dataset == dataset}
        assert got == cols, f"{dataset} 登记不全：缺 {cols - got}，多 {got - cols}"


def test_all_datasets_are_owned_by_some_module():
    """**这条在 Task 4-8 全部完成前是红的，那是它的用途。**

    它红着就说明还有表没人认领。不设允许清单——允许清单会被忘掉，
    红色测试不会。
    """
    every = set(schema.ALL) | {"paid_daily"}
    assert OWNED_DATASETS == every, f"还没认领：{every - OWNED_DATASETS}"


def test_registry_ids_are_exactly_the_expected_landed_ids():
    """T1 完备性：缺一个失败，多一个也失败。

    拦的是「往 schema 加列忘登记」——页面上会少一个指标而无人知晓。
    """
    assert set(landed()) == expected_metric_ids()


def test_frozen_datasets_are_not_writable():
    """T6：把 schema.py 注释里那句「不要注册进 PARTITIONED」升级成测试。

    注册进去就让 Warehouse.write("paid_daily", ...) 成为可能，
    而 paid_daily 是永久只读的历史证据。
    """
    assert "paid_daily" not in schema.PARTITIONED
    assert "paid_daily" not in schema.UNPARTITIONED
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_guards.py -v`
Expected: FAIL，`ImportError: cannot import name 'OWNED_DATASETS'`

- [ ] **Step 3: 写实现**

`src/qsys/data/catalog/__init__.py`：

```python
"""指标字典的聚合入口。

条目分散在五个模块里（各 20–90 条）。不放一个文件：2000 行的 registry
没人会读，没人读的字典就会开始烂。

这里只做三件事：把条目并起来、把「谁认领了哪张表」汇总、把 consumers
和根源从血缘反向推导出来（手写的双向关系必然漂移）。
"""

from __future__ import annotations

from qsys.data import schema
from qsys.data.catalog.sources import SOURCES
from qsys.data.catalog.types import (
    Computed,
    Direct,
    Kind,
    Metric,
    MetricStatus,
    Source,
    Uncovered,
    Validity,
)

# Task 4-8 逐个接上；每个模块导出 (METRICS, DATASETS) 两个名字。
_MODULES: list[str] = []

_metrics: dict[str, Metric] = {}
_owned: set[str] = set()

for _name in _MODULES:
    _mod = __import__(f"qsys.data.catalog.{_name}", fromlist=["METRICS", "DATASETS"])
    for _m in _mod.METRICS:
        if _m.id in _metrics:
            raise ValueError(f"重复登记的指标：{_m.id}")
        _metrics[_m.id] = _m
    _overlap = _owned & set(_mod.DATASETS)
    if _overlap:
        raise ValueError(f"{_name} 与其他模块重复认领：{_overlap}")
    _owned.update(_mod.DATASETS)

REGISTRY: dict[str, Metric] = _metrics
OWNED_DATASETS: frozenset[str] = frozenset(_owned)

__all__ = [
    "REGISTRY", "SOURCES", "OWNED_DATASETS", "expected_metric_ids",
    "landed", "unlanded", "consumers", "root_sources",
    "Metric", "Source", "Direct", "Computed",
    "Kind", "Validity", "MetricStatus", "Uncovered",
]


def expected_metric_ids() -> set[str]:
    """字典**应该**有哪些落盘条目：schema.ALL 展开 + PAID_DAILY 展开。"""
    ids = {
        f"{dataset}.{col}"
        for dataset, arrow_schema in schema.ALL.items()
        for col in arrow_schema.names
    }
    ids |= {f"paid_daily.{col}" for col in schema.PAID_DAILY.names}
    return ids


def landed() -> dict[str, Metric]:
    return {k: m for k, m in REGISTRY.items() if m.landed}


def unlanded() -> dict[str, Metric]:
    """策略侧不落盘的决策量。它们是 S6/S7 Gate 的直接输入。"""
    return {k: m for k, m in REGISTRY.items() if not m.landed}


def consumers(metric_id: str) -> tuple[str, ...]:
    """下游哪些**指标**在吃它。由血缘反向推导，不手写——
    手写的双向关系必然漂移。"""
    out = [
        other.id
        for other in REGISTRY.values()
        if isinstance(other.lineage, Computed) and metric_id in other.lineage.upstream
    ]
    return tuple(sorted(out))


def root_sources(metric_id: str, _seen: frozenset[str] = frozenset()) -> tuple[str, ...]:
    """沿血缘走到 DIRECT 叶子，给出这个指标的**根源**集合。

    页面用它回答「首板这个数到底从哪来的」——答案要给到接口级，
    不能停在「Tushare」。
    """
    if metric_id in _seen or metric_id not in REGISTRY:
        return ()
    metric = REGISTRY[metric_id]
    if isinstance(metric.lineage, Direct):
        return (f"{metric.lineage.source_id}.{metric.lineage.field}",)
    seen = _seen | {metric_id}
    out: set[str] = set()
    for up in metric.lineage.upstream:
        out.update(root_sources(up, seen))
    return tuple(sorted(out))
```

- [ ] **Step 4: 跑测试，确认只剩认领那条红**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_guards.py -v`
Expected: 3 passed，2 failed
- `test_all_datasets_are_owned_by_some_module` FAIL —— 预期之中，Task 8 转绿
- `test_registry_ids_are_exactly_the_expected_landed_ids` FAIL —— 同上

其余三条（`expected_ids` 计数 206、已认领表的完整性、冻结表不可写）必须绿。
若 `test_expected_ids_cover_schema_and_paid_daily` 不等于 206，**停下来**：
说明 schema 有变动，先确认变动是否预期，再改这个数字。

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/catalog/__init__.py tests/data/catalog/test_guards.py
git commit -m "feat(catalog): 聚合入口与 T1/T6 守护，认领测试红着指示剩余工作"
```

---

## Task 4: 行情类 90 条

**这是一个转写任务，不是设计任务。** 每条的 `meaning` 与 `lineage` 必须有依据
（见 Global Constraints）。找不到依据就写 `UNKNOWN`——那是合规行为。

关键手法：`tushare_daily` 的 24 列**不手写血缘**，而是从
`qsys.data.pipeline.tushare_daily._FROM_DAILY` / `_FROM_BASIC` 这两张
`(仓库列名, 源列名, 乘数)` 映射表**生成**。这样「字典与实现分家」从
需要测试去抓，变成结构上不可能发生。

**Files:**
- Create: `src/qsys/data/catalog/market.py`
- Modify: `src/qsys/data/catalog/__init__.py`（`_MODULES` 加 `"market"`）
- Test: `tests/data/catalog/test_market_entries.py`

**Interfaces:**
- Consumes: Task 1 的类型、Task 2 的 `SOURCES`
- Produces: `METRICS: tuple[Metric, ...]`、`DATASETS: frozenset[str]`
  （每个条目模块都导出这两个名字，`__init__` 按约定加载）

- [ ] **Step 1: 写失败的测试**

`tests/data/catalog/test_market_entries.py`：

```python
"""行情类条目。重点核的是血缘指对了源、以及单位换算没被改掉。"""

from qsys.data.catalog.market import DATASETS, METRICS
from qsys.data.catalog.types import Direct, Kind, MetricStatus, UNKNOWN, Validity

BY_ID = {m.id: m for m in METRICS}


def test_owns_the_six_market_datasets():
    assert DATASETS == frozenset(
        {"tushare_daily", "bars_daily", "paid_daily",
         "index_bars", "minute_bars", "auction_ticks"}
    )


def test_registers_ninety_metrics():
    assert len(METRICS) == 90


def test_amount_carries_the_thousand_multiplier():
    """Tushare daily.amount 单位是千元，入库 ×1000 转元。
    这个乘数被改掉在数值上「看起来正常」，只有字典对账能发现。"""
    lineage = BY_ID["tushare_daily.amount"].lineage
    assert lineage == Direct("tushare.daily", "amount", 1000.0)


def test_share_columns_carry_the_ten_thousand_multiplier():
    """daily_basic 的股本列单位万股、市值列单位万元。"""
    for col, src in [
        ("total_shares", "total_share"), ("circulating_shares", "float_share"),
        ("free_shares", "free_share"), ("market_cap", "total_mv"),
        ("circulating_market_cap", "circ_mv"),
    ]:
        assert BY_ID[f"tushare_daily.{col}"].lineage == Direct(
            "tushare.daily_basic", src, 10000.0
        ), col


def test_volume_is_not_rescaled():
    """vol 单位是手，不换算——小数是真实精度不是脏数据。"""
    assert BY_ID["tushare_daily.volume"].lineage.multiplier == 1.0


def test_name_column_is_null_in_production_and_says_so():
    """tushare_daily.name 恒为 null，真值由 namechange / paid_daily /
    name_snapshots 三个源在 flags.resolve_names 里解析。"""
    m = BY_ID["tushare_daily.name"]
    assert "null" in m.meaning or "恒空" in m.meaning


def test_paid_daily_is_frozen_and_points_at_baidupan():
    for m in METRICS:
        if m.dataset == "paid_daily":
            assert m.status is MetricStatus.FROZEN
            assert m.lineage.source_id == "baidupan.daily"


def test_bars_daily_is_frozen_on_a_dead_source():
    for m in METRICS:
        if m.dataset == "bars_daily":
            assert m.status is MetricStatus.FROZEN
            assert m.lineage.source_id == "mootdx.kline"


def test_minute_dt_means_the_closing_minute():
    """09:30–09:31 那根记 09:31，所以 PIT 可见性就是 dt <= as_of，
    没有差一分钟的歧义。这个约定写错会让整条分钟链路差一格。"""
    assert "收盘" in BY_ID["minute_bars.dt"].meaning


def test_auction_quote_columns_record_the_identity():
    """竞价期间 bid1 == ask1 == 虚拟开盘价，bid_vol1 == ask_vol1 == 匹配量。
    09:25 撮合完成后这条恒等式才失效。"""
    assert "虚拟开盘价" in BY_ID["auction_ticks.bid1"].meaning


def test_every_entry_is_direct_and_session_or_point():
    """行情类全是直取。出现 DERIVED 说明归类错了——
    抽不出 (源, 字段) 的字段本来就不该放在这个模块。"""
    for m in METRICS:
        assert m.kind is Kind.DIRECT, m.id
        assert m.validity in (Validity.SESSION, Validity.POINT), m.id


def test_unknown_meanings_are_listed_not_hidden():
    """允许有 UNKNOWN，但要能被数出来——写 UNKNOWN 合规，编一句才违规。"""
    unknown = [m.id for m in METRICS if m.meaning is UNKNOWN]
    assert len(unknown) <= 10, f"待查过多，先补依据：{unknown}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_market_entries.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'qsys.data.catalog.market'`

- [ ] **Step 3: 写实现**

`src/qsys/data/catalog/market.py`。下面是完整实现——六张表全部覆盖，
不留待补项。逐条核对 `_DAILY_MEANING` 的依据来源后再提交。

```python
"""行情类指标 90 条。

tushare_daily 的 24 列**从 pipeline 的映射表生成**，不手写血缘——
`_FROM_DAILY` / `_FROM_BASIC` 就是 `(仓库列名, 源列名, 乘数)`，
拿它当唯一真值源，「字典与实现分家」在结构上就不可能发生。

paid_daily 与 tushare_daily 列名完全相同（实测同源，18,224,238 行逐键
零差异），故含义表共用；差别只在 source_id 与 status。
"""

from __future__ import annotations

from qsys.data import schema
from qsys.data.catalog.types import (
    Direct,
    Kind,
    Metric,
    MetricStatus,
    Validity,
)
from qsys.data.pipeline.tushare_daily import _FROM_BASIC, _FROM_DAILY

DATASETS = frozenset(
    {"tushare_daily", "bars_daily", "paid_daily",
     "index_bars", "minute_bars", "auction_ticks"}
)

# 28 列的含义。依据：schema.py 的 PAID_DAILY 注释、pipeline/tushare_daily.py
# 顶部的单位换算实测记录、docs/reference/data-source-migration-record.md。
_DAILY_MEANING = {
    "symbol": "标的代码，带交易所后缀（600036.SH）",
    "dt": "交易日",
    "name": "当日证券简称。**生产恒为 null**——三接口都不给，真值由 "
            "flags.resolve_names 按四级优先级从 namechange / paid_daily / "
            "name_snapshots 解析",
    "open": "不复权开盘价（元）",
    "high": "不复权最高价（元）",
    "low": "不复权最低价（元）",
    "close": "不复权收盘价（元）",
    "pre_close": "前收盘价（元）。非除权日等于前一日收盘，除权日是交易所"
                 "公布的除权后参考价——涨跌停按它算，不是按前一日收盘",
    "change": "涨跌额（元）＝ close − pre_close",
    "pct_change": "涨跌幅（%）",
    "volume": "成交量（手）。带小数是真实精度，取整会丢失",
    "amount": "成交额（元）。源给千元，入库 ×1000",
    "factor": "后复权累计因子。**源字段自身不自洽**，见 D-1",
    "turnover_ratio": "换手率（%）",
    "free_turnover_ratio": "自由流通换手率（%）",
    "volume_ratio": "量比",
    "pe": "市盈率（总市值 / 净利润，亏损为 null）",
    "pe_ttm": "市盈率 TTM",
    "pb": "市净率",
    "ps": "市销率",
    "ps_ttm": "市销率 TTM",
    "dividend_yield": "股息率（%）",
    "dividend_yield_ttm": "股息率 TTM（%）",
    "total_shares": "总股本（股）。源给万股，入库 ×10000。**必须直取**——"
                    "反算会让 A+H 股差上亿股",
    "circulating_shares": "流通股本（股）",
    "free_shares": "自由流通股本（股）",
    "market_cap": "总市值（元）",
    "circulating_market_cap": "流通市值（元）",
}

# 生产链路上这些列参与哪些规则。规则实现见 Task 12-14。
_DAILY_CHECKS = {
    "amount": ("A1.dimension.amount_vs_volume_close", "B3.null_rate"),
    "volume": ("A1.dimension.amount_vs_volume_close", "B3.null_rate"),
    "close": ("A3.minute_vs_daily", "A3.pct_change_identity", "B3.null_rate"),
    "pre_close": ("A3.change_identity", "A3.missed_exdiv", "B3.null_rate"),
    "change": ("A3.change_identity",),
    "pct_change": ("A3.pct_change_identity",),
    "factor": ("A3.missed_exdiv",),
    "market_cap": ("A3.market_cap_vs_shares",),
    "total_shares": ("A3.market_cap_vs_shares",),
    "turnover_ratio": ("A3.turnover_identity",),
    "circulating_shares": ("A3.turnover_identity",),
    "open": ("A3.minute_vs_daily", "B3.null_rate"),
    "high": ("A3.minute_vs_daily", "B3.null_rate"),
    "low": ("A3.minute_vs_daily", "B3.null_rate"),
}
_DAILY_DEFAULT_CHECKS = ("B1.calendar", "B5.span")

_DAILY_ISSUES = {"factor": ("D-1", "D-2")}


def _daily_family(
    dataset: str, source_id: str, status: MetricStatus
) -> list[Metric]:
    """tushare_daily 与 paid_daily 共用的 28 列生成器。

    24 列的 (源列, 乘数) 直接取自 pipeline 的映射表；其余 4 列
    （symbol / dt / factor / name）在这里显式登记，它们在 compose_daily
    里走的是 rename 或字面量，不在映射表中。
    """
    api = "daily" if source_id.endswith(".daily") else source_id.split(".", 1)[1]
    mapped: dict[str, Direct] = {}
    for ours, theirs, mult in _FROM_DAILY:
        mapped[ours] = Direct(f"{source_id.split('.')[0]}.daily", theirs, mult)
    for ours, theirs, mult in _FROM_BASIC:
        mapped[ours] = Direct(f"{source_id.split('.')[0]}.daily_basic", theirs, mult)
    mapped["symbol"] = Direct(f"{source_id.split('.')[0]}.daily", "ts_code")
    mapped["dt"] = Direct(f"{source_id.split('.')[0]}.daily", "trade_date")
    mapped["factor"] = Direct(f"{source_id.split('.')[0]}.adj_factor", "adj_factor")
    mapped["name"] = Direct(f"{source_id.split('.')[0]}.namechange", "name")

    if source_id.startswith("baidupan"):
        # 付费源是单张 Excel，全部 28 列同源同接口。
        mapped = {
            col: Direct("baidupan.daily", col, mapped[col].multiplier)
            for col in mapped
        }

    out = []
    for col in schema.PAID_DAILY.names:
        out.append(
            Metric(
                id=f"{dataset}.{col}",
                kind=Kind.DIRECT,
                validity=Validity.SESSION,
                status=status,
                meaning=_DAILY_MEANING[col],
                lineage=mapped[col],
                checks=_DAILY_CHECKS.get(col, _DAILY_DEFAULT_CHECKS),
                known_issues=_DAILY_ISSUES.get(col, ()),
            )
        )
    return out


_INDEX_MEANING = {
    "symbol": "指数代码。生产只用沪深 300（000300.SH）",
    "dt": "交易日",
    "open": "指数开盘点位",
    "high": "指数最高点位",
    "low": "指数最低点位",
    "close": "指数收盘点位",
    "volume": "成交量。**含小数**（全历史 5,270 行中实测 186 行），"
              "2026-09-14 从 int64 无损迁移为 float64",
    "amount": "成交额",
}

_MINUTE_MEANING = {
    "symbol": "标的代码",
    "dt": "该分钟的**收盘时刻**：09:30–09:31 这根记 09:31。"
          "如此 PIT 可见性就是 dt <= as_of，没有差一分钟的歧义",
    "open": "该分钟开盘价", "high": "该分钟最高价",
    "low": "该分钟最低价", "close": "该分钟收盘价",
    "volume": "该分钟成交量（手）", "amount": "该分钟成交额（元）",
}

_AUCTION_MEANING = {
    "symbol": "标的代码",
    "dt": "交易日",
    "ts": "快照时刻（北京时间）。沪市固定 3 秒一笔，深市按盘口变化推送。"
          "QMT 的 tick.time 是毫秒 epoch（UTC），导出时已 +8 小时",
    "bid1": "买一价。竞价期间等于**虚拟开盘价**，09:25 撮合完成后该恒等式失效",
    "ask1": "卖一价。竞价期间等于虚拟开盘价",
    "bid_vol1": "买一量。竞价期间等于**匹配量**（手）",
    "ask_vol1": "卖一量。竞价期间等于匹配量（手）",
    "last_price": "最新成交价。撮合前为 0",
    "volume": "累计成交量（手）。撮合前为 0",
    "amount": "累计成交额（元）",
}

_BARS_MEANING = {
    "symbol": "标的代码", "dt": "交易日",
    "open": "不复权开盘价（元）", "high": "不复权最高价（元）",
    "low": "不复权最低价（元）", "close": "不复权收盘价（元）",
    "volume": "成交量（手，整数）", "amount": "成交额（元）",
}


def _simple(
    dataset: str, source_id: str, meanings: dict[str, str],
    status: MetricStatus, checks: tuple[str, ...],
    validity: Validity = Validity.SESSION,
) -> list[Metric]:
    return [
        Metric(
            id=f"{dataset}.{col}", kind=Kind.DIRECT, validity=validity,
            status=status, meaning=meanings[col],
            lineage=Direct(source_id, col), checks=checks,
        )
        for col in schema.ALL[dataset].names
    ]


METRICS: tuple[Metric, ...] = tuple(
    _daily_family("tushare_daily", "tushare.daily", MetricStatus.ACTIVE)
    + _daily_family("paid_daily", "baidupan.daily", MetricStatus.FROZEN)
    + _simple("index_bars", "tushare.index_daily", _INDEX_MEANING,
              MetricStatus.ACTIVE, ("B1.calendar", "B5.span"))
    + _simple("minute_bars", "qmt.minute", _MINUTE_MEANING,
              MetricStatus.ACTIVE, ("A3.minute_vs_daily", "B4.arrival"))
    + _simple("auction_ticks", "qmt.tick", _AUCTION_MEANING,
              MetricStatus.ACTIVE, ("B4.arrival",))
    + _simple("bars_daily", "mootdx.kline", _BARS_MEANING,
              MetricStatus.FROZEN, ("C4.frozen_unchanged",))
)
```

写完后把 `"market"` 加进 `__init__.py` 的 `_MODULES`。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_market_entries.py tests/data/catalog/test_guards.py -v`
Expected: `test_market_entries.py` 全绿；`test_guards.py` 仍是 3 passed / 2 failed
（认领与完备性两条要等 Task 8）

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/catalog/market.py src/qsys/data/catalog/__init__.py \
        tests/data/catalog/test_market_entries.py
git commit -m "feat(catalog): 行情类 90 条，日线血缘由 pipeline 映射表生成"
```

---

## Task 5: 参考类 49 条

**Files:**
- Create: `src/qsys/data/catalog/reference.py`
- Modify: `src/qsys/data/catalog/__init__.py`（`_MODULES` 加 `"reference"`）
- Test: `tests/data/catalog/test_reference_entries.py`

**Interfaces:**
- Consumes: Task 1 类型、Task 2 `SOURCES`
- Produces: `METRICS: tuple[Metric, ...]`、`DATASETS: frozenset[str]`

本模块最要紧的是把三种 `validity` 标对——参考表正是结转事故的高发区。

- [ ] **Step 1: 写失败的测试**

`tests/data/catalog/test_reference_entries.py`：

```python
"""参考类条目。validity 标错就会有人拿点观测去填历史。"""

from qsys.data.catalog.reference import DATASETS, METRICS
from qsys.data.catalog.types import Kind, Validity

BY_ID = {m.id: m for m in METRICS}


def test_owns_the_nine_reference_datasets():
    assert DATASETS == frozenset({
        "instruments", "name_history", "name_snapshots", "price_limits_daily",
        "suspend_daily", "limit_list_daily", "corporate_actions",
        "share_capital", "adj_factors",
    })


def test_registers_fortynine_metrics():
    assert len(METRICS) == 49


def test_name_snapshot_is_a_point_observation():
    """stock_basic 返回的是**当前**名称。填取快照那一天是直接观测，
    填历史是结转。validity=POINT 就是这条规矩在字典里的形态。"""
    assert BY_ID["name_snapshots.name"].validity is Validity.POINT
    assert BY_ID["name_snapshots.dt"].meaning.startswith("观测日")


def test_instruments_are_point_observations_too():
    """instruments 是覆盖写的单张表只留最新一份，as_of 才让它可用。
    没有 as_of 就无法判断 board / list_date 有多陈旧。"""
    for m in METRICS:
        if m.dataset == "instruments":
            assert m.validity is Validity.POINT, m.id


def test_name_history_is_a_span_with_a_known_hole():
    """namechange 只记变更事件；2006-10-08 之后大量标的没有后继区间。"""
    m = BY_ID["name_history.name"]
    assert m.validity is Validity.SPAN
    assert "D-10" in m.known_issues
    assert "C3.span_gaps" in m.checks


def test_cum_factor_is_direct_but_carries_its_defect():
    """类型说的是它怎么来的，可信度说的是它对不对——两件事分开。"""
    m = BY_ID["adj_factors.cum_factor"]
    assert m.kind is Kind.DIRECT
    assert m.known_issues == ("D-1", "D-2")
    assert "A3.missed_exdiv" in m.checks


def test_share_factor_is_distinct_from_price_factor():
    """价格因子含现金分红那一项，拿它调成交量会多调——
    股数只随送转与配股变化，与派现无关。"""
    assert "送转" in BY_ID["adj_factors.cum_share_factor"].meaning


def test_limit_list_columns_record_the_st_exclusion():
    """D-11：limit_list_d 不含 ST，接成首板源会静默丢掉全部 ST 首板。"""
    m = BY_ID["limit_list_daily.limit_type"]
    assert "ST" in m.meaning
    assert "D-11" in m.known_issues


def test_suspend_timing_null_means_all_day_not_missing():
    assert "全天停牌" in BY_ID["suspend_daily.suspend_timing"].meaning


def test_share_capital_uses_effective_date_not_announcement():
    """股本是公开可即时观察的事实，交易所每日据此发布总市值。"""
    assert "生效日" in BY_ID["share_capital.dt"].meaning
    assert "公告日" in BY_ID["share_capital.ann_date"].meaning
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_reference_entries.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 写实现**

`src/qsys/data/catalog/reference.py`。依据来源：`schema.py` 每张表的注释、
`docs/reference/data-source-migration-record.md` 第三节的真值源表、
`deferred-corrections.md` 的 D-1 / D-10 / D-11。

```python
"""参考类指标 49 条。

这些表决定「这一天这只股是什么状态」，是结转事故的高发区——
`instruments` 与 `name_snapshots` 都是**点观测**，`name_history` 是**区间**，
三者的 validity 必须标对，否则总有人拿快照去填历史。
"""

from __future__ import annotations

from qsys.data import schema
from qsys.data.catalog.types import (
    Direct,
    Kind,
    Metric,
    MetricStatus,
    Validity,
)

DATASETS = frozenset({
    "instruments", "name_history", "name_snapshots", "price_limits_daily",
    "suspend_daily", "limit_list_daily", "corporate_actions",
    "share_capital", "adj_factors",
})

# (source_id, validity, 默认 checks)
_TABLE_SPEC = {
    "instruments": ("tushare.stock_basic", Validity.POINT, ("B4.arrival",)),
    "name_snapshots": ("tushare.stock_basic", Validity.POINT,
                       ("C2.pit_join", "B4.arrival")),
    "name_history": ("tushare.namechange", Validity.SPAN, ("C3.span_gaps",)),
    "price_limits_daily": ("tushare.stk_limit", Validity.SESSION,
                           ("A3.limit_price_vs_selfcalc", "B1.calendar")),
    "suspend_daily": ("tushare.suspend_d", Validity.SESSION, ("B4.arrival",)),
    "limit_list_daily": ("tushare.limit_list_d", Validity.SESSION,
                         ("A3.limit_list_vs_selfcalc", "B4.zero_limit_up")),
    "corporate_actions": ("akshare.stock_fhps_detail_em", Validity.SESSION,
                          ("B5.span",)),
    "share_capital": ("qmt.minute", Validity.SESSION, ("B5.span",)),
    "adj_factors": ("tushare.adj_factor", Validity.SESSION,
                    ("A3.missed_exdiv",)),
}

_MEANING = {
    # ---- instruments（点观测，7 列）----
    "instruments.symbol": "标的代码",
    "instruments.name": "快照日的证券简称",
    "instruments.market": "交易所（SH / SZ / BJ）",
    "instruments.list_date": "上市日",
    "instruments.delist_date": "退市日，未退市为 null",
    "instruments.board": "板块：main / gem / star / bse。"
                         "43/83/87/92 一律判 bse——920 号段是北交所 2023 年"
                         "启用的新号段，原策略的 4/8/68 前缀表排不掉它",
    "instruments.as_of": "取这份快照的日期。没有它就无法判断 board / list_date "
                         "有多陈旧，也无法把 name 安全地限定在快照日使用",
    # ---- name_snapshots（点观测，3 列）----
    "name_snapshots.dt": "观测日，即取 stock_basic 的那一天。"
                         "**这是点观测不是区间**——转成区间要推断端点",
    "name_snapshots.symbol": "标的代码",
    "name_snapshots.name": "观测日的证券简称。按 (symbol, dt) **精确连接**，"
                           "只对它实际有行的那一天生效，不向前后蔓延",
    # ---- name_history（区间，6 列）----
    "name_history.symbol": "标的代码",
    "name_history.name": "该区间内的证券简称。ST 状态由区间覆盖关系纯派生",
    "name_history.start_date": "区间起日",
    "name_history.end_date": "区间止日",
    "name_history.ann_date": "变更公告日",
    "name_history.change_reason": "变更原因",
    # ---- price_limits_daily（5 列）----
    "price_limits_daily.symbol": "标的代码",
    "price_limits_daily.dt": "交易日",
    "price_limits_daily.pre_close": "前收盘价（元）",
    "price_limits_daily.limit_up": "涨停价（元），交易所公布值",
    "price_limits_daily.limit_down": "跌停价（元），交易所公布值",
    # ---- suspend_daily（4 列）----
    "suspend_daily.symbol": "标的代码",
    "suspend_daily.dt": "交易日",
    "suspend_daily.suspend_type": "S 停牌 / R 复牌",
    "suspend_daily.suspend_timing": "停牌时段。**全天停牌时为 null**，"
                                    "是取值不是缺失",
    # ---- limit_list_daily（9 列）----
    "limit_list_daily.symbol": "标的代码",
    "limit_list_daily.dt": "交易日",
    "limit_list_daily.limit_type": "U 涨停 / D 跌停 / Z 炸板。"
                                   "**不含 ST 股票**——与自算涨停的差集精确"
                                   "等于当日 ST 涨停集（D-11）",
    "limit_list_daily.limit_times": "连板数",
    "limit_list_daily.open_times": "打开次数",
    "limit_list_daily.first_time": "首次封板时间",
    "limit_list_daily.fd_amount": "封单金额（元）",
    "limit_list_daily.limit_amount": "板上成交额（元）",
    "limit_list_daily.float_mv": "流通市值（元）",
    # ---- corporate_actions（6 列）----
    "corporate_actions.symbol": "标的代码",
    "corporate_actions.ex_date": "除权除息日",
    "corporate_actions.cash_per_share": "每股现金红利（税前，元）",
    "corporate_actions.stock_per_share": "每股送转股数",
    "corporate_actions.rights_per_share": "每股配股数",
    "corporate_actions.rights_price": "配股价（元）",
    # ---- share_capital（5 列）----
    "share_capital.symbol": "标的代码",
    "share_capital.dt": "股本**生效日**（QMT m_timetag）。股本是公开可即时"
                        "观察的事实，交易所每日据此发布总市值",
    "share_capital.ann_date": "公告日，留作更严格 PIT 口径的口子",
    "share_capital.total": "总股本（股）",
    "share_capital.circulating": "流通股本（股）",
    # ---- adj_factors（4 列）----
    "adj_factors.symbol": "标的代码",
    "adj_factors.dt": "交易日",
    "adj_factors.cum_factor": "后复权累计因子（价格用），直取源 factor。"
                              "价格反推降级为校验，不得覆盖真值",
    "adj_factors.cum_share_factor": "股本累计比例（成交量用）。只随**送转**与"
                                    "配股变化，与派现无关——价格因子含现金分红"
                                    "那一项，拿它调成交量会多调",
}

_ISSUES = {
    "adj_factors.cum_factor": ("D-1", "D-2"),
    "name_history.name": ("D-10",),
    "name_history.start_date": ("D-10",),
    "name_history.end_date": ("D-10",),
    "limit_list_daily.limit_type": ("D-11",),
    "limit_list_daily.dt": ("D-12",),
}


def _build() -> list[Metric]:
    out = []
    for dataset, (source_id, validity, checks) in _TABLE_SPEC.items():
        for col in schema.ALL[dataset].names:
            mid = f"{dataset}.{col}"
            out.append(
                Metric(
                    id=mid, kind=Kind.DIRECT, validity=validity,
                    status=MetricStatus.ACTIVE, meaning=_MEANING[mid],
                    lineage=Direct(source_id, col),
                    checks=checks, known_issues=_ISSUES.get(mid, ()),
                )
            )
    return out


METRICS: tuple[Metric, ...] = tuple(_build())
```

**注意**：`_MEANING` 里若有 key 缺失会直接 `KeyError`——这是有意的，
比默默产出一条没有含义的指标好。`Direct(source_id, col)` 的 `field` 用了
仓库列名，对 `stk_limit` / `suspend_d` 等接口这是近似；实现时**逐表核对**
pipeline 里的真实源列名（如 `stk_limit.up_limit` → `limit_up`），
对不上就把真实源列名填进去。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_reference_entries.py -v`
Expected: 10 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/catalog/reference.py src/qsys/data/catalog/__init__.py \
        tests/data/catalog/test_reference_entries.py
git commit -m "feat(catalog): 参考类 49 条，点观测/区间/日频三种 validity 标定"
```

---

## Task 6: 派生类 20 条

`daily_flags`(14) + `valuation_snapshot`(6)。全部 `Computed` 血缘，
上游指向 Task 4/5 已登记的指标 id。

**Files:**
- Create: `src/qsys/data/catalog/derived.py`
- Modify: `src/qsys/data/catalog/__init__.py`（`_MODULES` 加 `"derived"`）
- Test: `tests/data/catalog/test_derived_entries.py`

**Interfaces:**
- Consumes: Task 4/5 的 metric id（作为上游引用）
- Produces: `METRICS: tuple[Metric, ...]`、`DATASETS: frozenset[str]`

- [ ] **Step 1: 写失败的测试**

`tests/data/catalog/test_derived_entries.py`：

```python
"""派生类条目。公式与上游必须写实，它们是 A2 独立重算的依据。"""

from qsys.data.catalog.derived import DATASETS, METRICS
from qsys.data.catalog.types import Kind, Validity

BY_ID = {m.id: m for m in METRICS}


def test_owns_two_derived_datasets():
    assert DATASETS == frozenset({"daily_flags", "valuation_snapshot"})


def test_registers_twenty_metrics():
    assert len(METRICS) == 20


def test_nothing_here_claims_to_be_direct():
    """派生表里出现 DIRECT 说明归类错了。"""
    for m in METRICS:
        assert m.kind is not Kind.DIRECT, m.id
        assert m.validity is Validity.SESSION, m.id


def test_limit_up_is_computed_from_the_ex_rights_reference_price():
    """涨跌停是按除权除息参考价算的，不是按 prev_close。
    这条写错会让全部除权日的涨停判定错位。"""
    m = BY_ID["daily_flags.limit_up"]
    assert "daily_flags.adj_prev_close" in m.lineage.upstream
    assert "daily_flags.limit_pct" in m.lineage.upstream
    assert "prev_close" not in m.lineage.formula


def test_is_first_board_needs_yesterday():
    """首板＝今日涨停且昨日未涨停，所以上游含自身（跨日回看）。"""
    m = BY_ID["daily_flags.is_first_board"]
    assert "daily_flags.is_limit_up" in m.lineage.upstream
    assert "昨" in m.lineage.formula


def test_is_st_traces_back_to_all_four_name_sources():
    """四级优先级全部是直取字段，缺任何一级都会让某段历史的 ST 判空。"""
    up = set(BY_ID["daily_flags.is_st"].lineage.upstream)
    assert {"tushare_daily.name", "paid_daily.name",
            "name_snapshots.name", "name_history.name"} <= up


def test_st_source_records_which_level_won():
    """恒为字面量的 st_source 会让 2% 根本没有名称的行看起来也「有来源」。"""
    assert "来源" in BY_ID["daily_flags.st_source"].meaning


def test_adj_prev_close_is_a_passthrough_not_a_recomputation():
    """2026-09-12 起直取 Tushare 当日 pre_close，不再由事件表自算。"""
    m = BY_ID["daily_flags.adj_prev_close"]
    assert m.lineage.upstream == ("tushare_daily.pre_close",)


def test_market_cap_snapshot_is_checked_against_shares():
    m = BY_ID["valuation_snapshot.market_cap"]
    assert "A3.market_cap_vs_shares" in m.checks


def test_every_upstream_is_spelled_like_a_metric_id():
    for m in METRICS:
        for up in m.lineage.upstream:
            assert "." in up, f"{m.id} 的上游 {up!r} 不是 metric id"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_derived_entries.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 写实现**

`src/qsys/data/catalog/derived.py`。依据：`schema.py` 的 `DAILY_FLAGS` /
`VALUATION_SNAPSHOT` 注释、`pipeline/flags.py`、`pipeline/valuation.py`。
`formula` 要写成**人能照着独立重算一遍**的程度——它就是 A2 的规格书。

```python
"""派生类指标 20 条。

每条的 `formula` 是 A2「独立重算」的规格书：实现规则时照着它重写一遍，
**不准调用生产函数**。两条路径都错才漏得过去。
"""

from __future__ import annotations

from qsys.data.catalog.types import (
    Computed,
    Kind,
    Metric,
    MetricStatus,
    Validity,
)

DATASETS = frozenset({"daily_flags", "valuation_snapshot"})


def _m(mid, meaning, upstream, formula, checks, issues=(), kind=Kind.DERIVED):
    return Metric(
        id=mid, kind=kind, validity=Validity.SESSION,
        status=MetricStatus.ACTIVE, meaning=meaning,
        lineage=Computed(upstream=tuple(upstream), formula=formula),
        checks=tuple(checks), known_issues=tuple(issues),
    )


_NAME_SOURCES = (
    "tushare_daily.name", "paid_daily.name",
    "name_snapshots.name", "name_history.name",
)

METRICS: tuple[Metric, ...] = (
    _m("daily_flags.dt", "交易日", ("tushare_daily.dt",), "透传", ("B1.calendar",)),
    _m("daily_flags.symbol", "标的代码", ("tushare_daily.symbol",), "透传",
       ("B3.null_rate",)),
    _m("daily_flags.prev_close",
       "前一根 bar 的收盘价（元）。首个 bar 为 null，不填",
       ("tushare_daily.close",),
       "按 (symbol, dt) 排序后取 close 的上一行；无上一行记 null",
       ("A2.recompute.prev_close", "C1.no_fill")),
    _m("daily_flags.adj_prev_close",
       "除权除息参考价（元）。**涨跌停按它算，不是按 prev_close**。"
       "2026-09-12 起直取 Tushare 当日 pre_close，不再由事件表自算",
       ("tushare_daily.pre_close",), "透传",
       ("A2.recompute.adj_prev_close",)),
    _m("daily_flags.limit_pct",
       "当日实际生效的涨跌幅上限：ST 为 0.05，否则按板块 0.10 / 0.20 / 0.30",
       ("daily_flags.is_st", "instruments.board"),
       "is_st ? 0.05 : {main:0.10, gem:0.20, star:0.20, bse:0.30}[board]",
       ("A2.recompute.limit_pct",)),
    _m("daily_flags.limit_up", "涨停价（元）",
       ("daily_flags.adj_prev_close", "daily_flags.limit_pct"),
       "round(adj_prev_close * (1 + limit_pct), 2)",
       ("A2.recompute.limit_up", "A3.limit_price_vs_selfcalc")),
    _m("daily_flags.limit_down", "跌停价（元）",
       ("daily_flags.adj_prev_close", "daily_flags.limit_pct"),
       "round(adj_prev_close * (1 - limit_pct), 2)",
       ("A2.recompute.limit_down", "A3.limit_price_vs_selfcalc")),
    _m("daily_flags.is_limit_up", "收盘封住涨停",
       ("tushare_daily.close", "daily_flags.limit_up"),
       "close >= limit_up - 1e-6",
       ("A2.recompute.is_limit_up", "A3.limit_list_vs_selfcalc")),
    _m("daily_flags.is_limit_down", "收盘封住跌停",
       ("tushare_daily.close", "daily_flags.limit_down"),
       "close <= limit_down + 1e-6", ("A2.recompute.is_limit_down",)),
    _m("daily_flags.touched_limit_up", "盘中触及涨停（含封住）",
       ("tushare_daily.high", "daily_flags.limit_up"),
       "high >= limit_up - 1e-6", ("A2.recompute.touched_limit_up",)),
    _m("daily_flags.is_broken_board", "炸板：触及但未封住",
       ("daily_flags.touched_limit_up", "daily_flags.is_limit_up"),
       "touched_limit_up and not is_limit_up",
       ("A2.recompute.is_broken_board",)),
    _m("daily_flags.is_first_board", "首板：今日涨停且**昨日**未涨停",
       ("daily_flags.is_limit_up",),
       "is_limit_up(t) and not is_limit_up(t-1)；无 t-1 记 null",
       ("A2.recompute.is_first_board", "A3.limit_list_vs_selfcalc")),
    _m("daily_flags.is_st",
       "ST 真值。由当日名称判定；名称按四级优先级解析，四个候选都是直取字段",
       _NAME_SOURCES,
       "name = coalesce(tushare_daily.name, paid_daily.name[精确日], "
       "name_snapshots.name[精确日], name_history 区间覆盖)；"
       "再判 name 是否含 ST / *ST / 退",
       ("A2.recompute.is_st", "B3.null_rate"), ("D-10",)),
    _m("daily_flags.st_source",
       "该行 ST 值的**来源**级别。名称为 null 时本列也为 null——"
       "恒为字面量会让没有名称的行看起来也「有来源」",
       _NAME_SOURCES, "取四级解析中实际命中的那一级的名字",
       ("A2.recompute.st_source",)),

    _m("valuation_snapshot.dt", "交易日", ("tushare_daily.dt",), "透传",
       ("B1.calendar",)),
    _m("valuation_snapshot.symbol", "标的代码", ("tushare_daily.symbol",),
       "透传", ("B3.null_rate",)),
    _m("valuation_snapshot.close", "不复权收盘价（元）",
       ("tushare_daily.close",), "透传", ("B3.null_rate",)),
    _m("valuation_snapshot.market_cap", "总市值（元）",
       ("tushare_daily.market_cap",), "透传",
       ("A3.market_cap_vs_shares",)),
    _m("valuation_snapshot.circulating_market_cap", "流通市值（元）",
       ("tushare_daily.circulating_market_cap",), "透传",
       ("A3.market_cap_vs_shares",)),
    _m("valuation_snapshot.turnover_ratio", "换手率（%）",
       ("tushare_daily.turnover_ratio",), "透传",
       ("A3.turnover_identity",)),
)
```

**实现时必须核对**：上面每条 `formula` 都要与 `pipeline/flags.py` 的实际
实现对照。对不上时改**字典**去贴合实现，然后在 Task 12 的 A2 规则里按字典
重算——如果重算结果与生产值不符，那就是发现了一个真问题，记进 `metric_checks`
而不是回头改字典迁就它。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_derived_entries.py -v`
Expected: 10 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/catalog/derived.py src/qsys/data/catalog/__init__.py \
        tests/data/catalog/test_derived_entries.py
git commit -m "feat(catalog): 派生类 20 条，公式写成可独立重算的规格"
```

---

## Task 7: 因子类：market_regime 15 条 + 策略侧决策量

**本任务有一个先行的清点步骤。** spec §二 写的策略侧「约 12 个」是**估计值**，
必须先从代码枚举出真实清单再登记。清点结果与 12 不符时，**改 spec 的数字**
（§二的表格与 §五的 factors.py 行数），不要为了凑数编造条目。

落盘的 `market_regime` 15 条是确定的，不受此影响；`expected_metric_ids()`
的 206 也不受影响——策略侧决策量 `landed=False`，不进那个集合。

**Files:**
- Create: `src/qsys/data/catalog/factors.py`
- Modify: `src/qsys/data/catalog/__init__.py`（`_MODULES` 加 `"factors"`）
- Modify: `docs/superpowers/specs/2026-09-15-data-layer-audit-design.md`（若清点数≠12）
- Test: `tests/data/catalog/test_factor_entries.py`

**Interfaces:**
- Consumes: Task 4/5/6 的 metric id
- Produces: `METRICS`、`DATASETS`（只含 `"market_regime"`——策略侧决策量不属于任何数据集）

- [ ] **Step 1: 清点策略侧决策量**

跑这三条命令，把输出记进提交信息：

```bash
grep -n "stock_factors\|env.get\|env\[" src/qsys/strategy/structural/gate.py
grep -n "ratio = \|avg_inc = \|rp = \|peak = \|zyts" src/qsys/strategy/structural/entry.py
sed -n '204,220p' src/qsys/strategy/structural/selection.py
```

已知必有的两条（`selection._stock_factors` 的返回键）：
`stock_return_10d`、`distance_to_60d_high`。

`gate.py` 通过 `env` 读的五个（`index_ma20_slope_5d`、
`prior_limitup_nextday_return_mean`、`market_median_return_1d`、
`touched_limit_up_count`、`market_advance_ratio`）**是 market_regime 的列，
已经落盘**，不重复登记——只在 `ext_consumers` 里记上「S6 Gate」。

`entry.py` 里现算现用的比值（竞价价比、竞价量比、`avg_inc`、
60 日相对位置、左压 `zyts`）逐个确认后登记。**以 grep 出来的为准**。

- [ ] **Step 2: 写失败的测试**

`tests/data/catalog/test_factor_entries.py`。把 Step 1 清点出的 id 写进
`_STRATEGY_IDS`——这是本任务唯一需要按实际代码填的地方：

```python
"""因子类条目。因子的「正确」不只是算得对，还包括口径是不是想要的那个。"""

from qsys.data.catalog.factors import DATASETS, METRICS
from qsys.data.catalog.types import Kind

BY_ID = {m.id: m for m in METRICS}

# Step 1 清点结果。至少含这两条；其余按 entry.py 实际情况补。
_STRATEGY_IDS = {"strategy.stock_return_10d", "strategy.distance_to_60d_high"}


def test_owns_only_market_regime():
    """策略侧决策量不落盘，不属于任何数据集。"""
    assert DATASETS == frozenset({"market_regime"})


def test_registers_all_fifteen_regime_columns():
    assert len({m.id for m in METRICS if m.dataset == "market_regime"}) == 15


def test_strategy_factors_are_present_and_unlanded():
    for mid in _STRATEGY_IDS:
        assert mid in BY_ID, mid
        assert BY_ID[mid].landed is False


def test_breadth_factors_record_their_universe_choice():
    """D-3 的教训：market_advance_ratio 公式从来没错，错的是分母那个池子。
    口径必须进 params，否则验证只会去核公式。"""
    m = BY_ID["market_regime.market_advance_ratio"]
    assert m.kind is Kind.FACTOR
    params = dict(m.lineage.params)
    assert "universe" in params
    assert "D-3" in m.known_issues


def test_index_slope_records_both_windows():
    """MA20 与 5 日回看都是参数，写死在公式里就没人知道它们可调。"""
    params = dict(BY_ID["market_regime.index_ma20_slope_5d"].lineage.params)
    assert params["ma_window"] == 20
    assert params["lookback"] == 5


def test_index_slope_is_null_without_index_data_not_zero():
    """S6 Gate 对 None 走 UNKNOWN_ALLOW，填 0 会被当成真实的「趋势走平」。"""
    m = BY_ID["market_regime.index_ma20_slope_5d"]
    assert "C1.no_fill" in m.checks
    assert "不填 0" in m.meaning or "留空" in m.meaning


def test_gate_inputs_declare_the_strategy_as_an_external_consumer():
    """换源第 2 段实测 13 个生态因子在 86%~100% 的交易日上全部变化，
    当时要靠人去追谁受影响。ext_consumers 就是为了不再追。"""
    for mid in ("market_regime.index_ma20_slope_5d",
                "market_regime.market_advance_ratio",
                "market_regime.touched_limit_up_count",
                "market_regime.market_median_return_1d",
                "market_regime.prior_limitup_nextday_return_mean"):
        assert any("S6" in c or "S7" in c for c in BY_ID[mid].ext_consumers), mid


def test_thresholds_pending_recalibration_are_flagged():
    """D-4：四个冻结阈值拟合的已经不是同一个分布。
    本项目不做重标定，但必须让页面上看得见这件事。"""
    m = BY_ID["market_regime.market_advance_ratio"]
    assert "D-4" in m.known_issues


def test_stock_return_10d_records_its_window():
    params = dict(BY_ID["strategy.stock_return_10d"].lineage.params)
    assert params["window"] == 10


def test_every_factor_has_params_or_a_universe():
    """有自由参数或口径选择的才是 FACTOR。两样都没有说明它其实是 DERIVED。"""
    for m in METRICS:
        if m.kind is Kind.FACTOR:
            assert m.lineage.params, f"{m.id} 标了 FACTOR 却没有任何参数或口径"
```

- [ ] **Step 3: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_factor_entries.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 4: 写实现**

`src/qsys/data/catalog/factors.py`。依据：`pipeline/regime.py` 顶部 docstring
（股票池两层过滤、`index_ma20_slope_5d` 的定义与留空纪律）、
`strategy/structural/gate.py`、`deferred-corrections.md` 的 D-3 / D-4 / D-5。

```python
"""因子类指标：market_regime 15 条 + 策略侧不落盘决策量。

因子与派生的区别是**有自由参数或口径选择**。这条线是为 D-3 划的：
market_advance_ratio 的公式从来没错，错的是分母那个股票池——归成派生则
验证只核公式，永远查不出来；归成因子，验证被迫去核口径。

所以每条 FACTOR 的 `params` 必须写实，尤其是 `universe`。
"""

from __future__ import annotations

from qsys.data.catalog.types import (
    Computed,
    Kind,
    Metric,
    MetricStatus,
    Validity,
)

DATASETS = frozenset({"market_regime"})

# 广度股票池的口径。两层过滤：第一层沿用原策略 filter_kcbj_stock 的
# 4/8/68 前缀（排科创板与北交所，但**排不掉 920 号段**），第二层按调用方
# 传入的 boards 取交集，生产默认 main,gem,star。
# 两层语义并不完全一致（前一层不排 B 股 900/200），见 D-5。
_BREADTH = (
    "excl_prefix=4/8/68 ∩ boards=main,gem,star"
)


def _regime(mid, meaning, upstream, formula, params, checks, issues=()):
    return Metric(
        id=mid, kind=Kind.FACTOR, validity=Validity.SESSION,
        status=MetricStatus.ACTIVE, meaning=meaning,
        lineage=Computed(tuple(upstream), formula, tuple(params)),
        checks=tuple(checks), known_issues=tuple(issues),
        ext_consumers=("S6 分类", "S7 Gate") if "S6" in meaning else (),
    )


# D-4：四个冻结阈值拟合的是「含北交所、ST 靠价格反推、复权因子自算」的
# 那份数据，换源后三项全变了。凡是喂 S6/S7 的因子都挂上 D-4。
_GATE_ISSUES = ("D-3", "D-4")
_BREADTH_PARAMS = (("universe", _BREADTH),)

METRICS: tuple[Metric, ...] = (
    Metric(
        id="market_regime.dt", kind=Kind.DERIVED, validity=Validity.SESSION,
        status=MetricStatus.ACTIVE, meaning="交易日",
        lineage=Computed(("daily_flags.dt",), "透传"),
        checks=("B1.calendar",),
    ),
    _regime("market_regime.market_advance_ratio",
            "S6 广度：当日上涨标的占比。分母是广度股票池，不是全市场",
            ("daily_flags.symbol", "tushare_daily.close", "daily_flags.prev_close"),
            "count(ret_1d > 0) / count(有效标的)", _BREADTH_PARAMS,
            ("A2.recompute.market_advance_ratio",), _GATE_ISSUES),
    _regime("market_regime.index_ma20_slope_5d",
            "S6 趋势：沪深 300 的 MA20 相对 5 个交易日前 MA20 的变化率。"
            "**没有指数数据时留空不填 0**——S6 对 None 走 UNKNOWN_ALLOW，"
            "填 0 会被当成真实的「趋势走平」",
            ("index_bars.close",),
            "MA20(t) / MA20(t-5) - 1",
            (("ma_window", 20), ("lookback", 5), ("index", "000300.SH")),
            ("A2.recompute.index_ma20_slope_5d", "C1.no_fill"), ("D-4",)),
    _regime("market_regime.prior_limitup_nextday_return_mean",
            "S6 热度：昨日涨停标的今日收益的均值",
            ("daily_flags.is_limit_up", "tushare_daily.close"),
            "mean(ret_1d(t) where is_limit_up(t-1))", _BREADTH_PARAMS,
            ("A2.recompute.prior_limitup_nextday_return_mean",), _GATE_ISSUES),
    _regime("market_regime.market_median_return_1d",
            "S6 广度（HIGH_MAIN 分支用）：全池日收益中位数",
            ("tushare_daily.close", "daily_flags.prev_close"),
            "median(ret_1d)", _BREADTH_PARAMS,
            ("A2.recompute.market_median_return_1d",), _GATE_ISSUES),
    _regime("market_regime.touched_limit_up_count",
            "S6：盘中触及涨停的标的数",
            ("daily_flags.touched_limit_up",), "count(touched_limit_up)",
            _BREADTH_PARAMS, ("A2.recompute.touched_limit_up_count",),
            _GATE_ISSUES),
    # ---- 其余 9 列按同样写法补齐 ----
    # limit_up_count / limit_down_count / limit_up_down_ratio /
    # broken_board_count / broken_board_rate / first_board_count /
    # market_total_money / prior_limitup_nextday_return_median /
    # valid_stock_count
    #
    # 全部带 _BREADTH_PARAMS；只有 valid_stock_count 是池子本身的大小，
    # 它的 meaning 要写明「这就是广度因子的分母」。

    # ---- 策略侧不落盘决策量 ----
    Metric(
        id="strategy.stock_return_10d", kind=Kind.FACTOR,
        validity=Validity.SESSION, status=MetricStatus.ACTIVE,
        meaning="S6 个股因子：近 10 个交易日收益率。LOW_MAIN 分支专用",
        lineage=Computed(("tushare_daily.close",),
                         "close(t) / close(t-10) - 1；不足 11 根 bar 记 null",
                         (("window", 10),)),
        checks=("C1.no_fill",), known_issues=("D-4",),
        ext_consumers=("S6 分类 _classify_low_main",), landed=False,
    ),
    Metric(
        id="strategy.distance_to_60d_high", kind=Kind.FACTOR,
        validity=Validity.SESSION, status=MetricStatus.ACTIVE,
        meaning="S6 个股因子：收盘价相对近 60 日最高价的距离（负值）",
        lineage=Computed(("tushare_daily.close", "tushare_daily.high"),
                         "close(t) / max(high[-60:]) - 1；不足 60 根记 null",
                         (("window", 60),)),
        checks=("C1.no_fill",), known_issues=("D-4",),
        ext_consumers=("S6 分类 _classify_low_main",), landed=False,
    ),
    # ---- Step 1 清点出的其余策略侧决策量按同样写法补齐 ----
)
```

**补齐 `market_regime` 剩余 9 列时**，逐列去 `pipeline/regime.py` 读实现，
`formula` 写到能照着独立重算的程度。`valid_stock_count` 的 meaning 必须
点明它是广度因子的分母——D-3 的整个事故就是分母变了而没人注意。

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_factor_entries.py -v`
Expected: 10 passed

- [ ] **Step 6: 若清点数≠12，订正 spec**

```bash
grep -n "约 12\|218\|27 条" docs/superpowers/specs/2026-09-15-data-layer-audit-design.md
```

把 §二 的表格、§五 的 `factors.py` 行数注释改成实际清点值。

- [ ] **Step 7: 提交**

```bash
git add src/qsys/data/catalog/factors.py src/qsys/data/catalog/__init__.py \
        tests/data/catalog/test_factor_entries.py \
        docs/superpowers/specs/2026-09-15-data-layer-audit-design.md
git commit -m "feat(catalog): 因子类条目，口径与窗口参数全部写实"
```

---

## Task 8: 运行时类 32 条（完备性测试转绿）

`live_equity`(4) + `live_fills`(8) + `live_rejects`(6) + `live_recon`(7)
+ `ingest_log`(7) = 32。本任务完成后 Task 3 那两条红灯测试转绿。

**Files:**
- Create: `src/qsys/data/catalog/operational.py`
- Modify: `src/qsys/data/catalog/__init__.py`（`_MODULES` 加 `"operational"`）
- Test: `tests/data/catalog/test_operational_entries.py`

**Interfaces:**
- Consumes: Task 1 类型
- Produces: `METRICS`、`DATASETS`

- [ ] **Step 1: 写失败的测试**

`tests/data/catalog/test_operational_entries.py`：

```python
"""运行时类条目。这些是系统自己写的，没有外部真值可比——
但正因如此，UNCOVERED 的理由必须写清楚。"""

from qsys.data.catalog.operational import DATASETS, METRICS
from qsys.data.catalog.types import Kind, MetricStatus, Uncovered

BY_ID = {m.id: m for m in METRICS}


def test_owns_the_five_runtime_datasets():
    assert DATASETS == frozenset({
        "live_equity", "live_fills", "live_rejects", "live_recon", "ingest_log",
    })


def test_registers_thirtytwo_metrics():
    assert len(METRICS) == 32


def test_all_are_operational_status():
    for m in METRICS:
        assert m.status is MetricStatus.OPERATIONAL, m.id


def test_uncovered_entries_state_a_real_reason():
    """UNCOVERED 不是免检章。理由要说明「为什么没有外部真值」，
    不能只写「暂无」。"""
    for m in METRICS:
        if isinstance(m.checks, Uncovered):
            assert len(m.checks.reason) >= 8, f"{m.id} 的理由太敷衍"


def test_ingest_log_ok_column_warns_about_empty_writes():
    """空结果写 ingest_log 会让上游故障被当成「全市场已完成」而永不回补。
    这个教训必须留在字典里，否则下一个人还会再犯。"""
    assert "空结果" in BY_ID["ingest_log.ok"].meaning


def test_recon_layer_lists_its_three_values():
    assert "sim_vs_live" in BY_ID["live_recon.layer"].meaning
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_operational_entries.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 写实现**

`src/qsys/data/catalog/operational.py`。依据：`schema.py` 的 `LIVE_*` /
`INGEST_LOG` 注释、`ops/journal.py`、迁移记录第五节第 1 条（空结果不写日志）。

```python
"""运行时类指标 32 条。

这些是系统自己写下的记录，没有外部真值可以比对——所以它们多数是
UNCOVERED。但 UNCOVERED 不是免检章：理由必须说明「为什么没有外部真值」，
页面会把它们单独列出来，让人一眼看到这批东西是靠什么保证的。
"""

from __future__ import annotations

from qsys.data import schema
from qsys.data.catalog.types import (
    UNCOVERED,
    Direct,
    Kind,
    Metric,
    MetricStatus,
    Validity,
)

DATASETS = frozenset({
    "live_equity", "live_fills", "live_rejects", "live_recon", "ingest_log",
})

_SELF = "qsys.runtime"  # 系统自身写入，不是外部源

_MEANING = {
    "live_equity.dt": "交易日",
    "live_equity.cash": "可用资金（元）",
    "live_equity.market_value": "持仓市值（元）",
    "live_equity.total_value": "总资产（元）＝ cash + market_value",
    "live_fills.dt": "交易日",
    "live_fills.client_order_id": "本地订单号，幂等键",
    "live_fills.symbol": "标的代码",
    "live_fills.side": "buy / sell",
    "live_fills.qty": "成交数量（股）",
    "live_fills.price": "成交价（元）",
    "live_fills.fee": "费用合计（元）",
    "live_fills.filled_at": "成交时刻",
    "live_rejects.dt": "交易日",
    "live_rejects.client_order_id": "本地订单号",
    "live_rejects.symbol": "标的代码",
    "live_rejects.qty": "被拒数量（股）",
    "live_rejects.reason": "拒单原因，来自券商回报原文",
    "live_rejects.at": "拒单时刻",
    "live_recon.dt": "交易日",
    "live_recon.at": "对账时刻",
    "live_recon.layer": "对账层：intraday / eod / sim_vs_live",
    "live_recon.is_consistent": "该层是否一致",
    "live_recon.cash_diff": "资金差额（元）",
    "live_recon.n_mismatches": "不一致的持仓条目数",
    "live_recon.detail": "差异明细（文本）",
    "ingest_log.dataset": "本次采集写入的数据集名",
    "ingest_log.dt": "采集覆盖的交易日",
    "ingest_log.provider": "采集用的源",
    "ingest_log.ingested_at": "采集完成时刻",
    "ingest_log.row_count": "写入行数",
    "ingest_log.ok": "本次采集是否成功。**空结果不得写这张表**——"
                     "写了会让上游故障被当成「全市场已完成」而永不回补",
    "ingest_log.message": "失败原因或补充说明",
}

_CHECKS = {
    "ingest_log.dataset": ("B4.arrival",),
    "ingest_log.dt": ("B4.arrival",),
    "ingest_log.ok": ("B4.arrival",),
    "live_recon.is_consistent": ("B4.arrival",),
}
_DEFAULT = UNCOVERED("系统自身写入的运行记录，无外部真值可交叉比对；"
                     "正确性由写入侧的单元测试保证")


def _build() -> list[Metric]:
    out = []
    for dataset in sorted(DATASETS):
        for col in schema.ALL[dataset].names:
            mid = f"{dataset}.{col}"
            out.append(
                Metric(
                    id=mid, kind=Kind.DIRECT, validity=Validity.SESSION,
                    status=MetricStatus.OPERATIONAL, meaning=_MEANING[mid],
                    lineage=Direct(_SELF, col),
                    checks=_CHECKS.get(mid, _DEFAULT),
                )
            )
    return out


METRICS: tuple[Metric, ...] = tuple(_build())
```

`_SELF = "qsys.runtime"` 需要在 `sources.py` 里加一个对应的 `Source`
（provider `"qsys"`、api `"runtime"`、access `API`、status `ACTIVE`、
caveat 写明「系统自身，不是外部源」），并把 `test_has_twentyone_sources`
的数字改成 22，同时订正 spec §四的清单。

- [ ] **Step 4: 跑全部字典测试，确认守护测试转绿**

Run: `.venv/bin/python -m pytest tests/data/catalog/ -v`
Expected: 全绿。**特别确认** `test_all_datasets_are_owned_by_some_module`
与 `test_registry_ids_are_exactly_the_expected_landed_ids` 两条从红转绿——
这是整个字典建设完成的信号。

若 `test_registry_ids_...` 仍红，看它报的差集：缺的是漏登记，多的是拼错了 id。

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/catalog/ tests/data/catalog/ \
        docs/superpowers/specs/2026-09-15-data-layer-audit-design.md
git commit -m "feat(catalog): 运行时类 32 条，206 条完备性守护转绿"
```

---

## Task 9: 剩余四条守护测试（T2/T3/T4/T5）

字典条目齐了，现在把余下四条闸门装上。T3 是本任务的核心。

**Files:**
- Modify: `tests/data/catalog/test_guards.py`
- Test: 同上

**Interfaces:**
- Consumes: Task 3 的 `REGISTRY` / `consumers` / `root_sources`，Task 4-8 的条目
- Produces: 无新接口，只加测试

- [ ] **Step 1: 追加四条守护测试**

在 `tests/data/catalog/test_guards.py` 末尾追加：

```python
from qsys.data.catalog import SOURCES, consumers, root_sources
from qsys.data.catalog.types import Computed, Direct, Uncovered
from qsys.data.pipeline.tushare_daily import _FROM_BASIC, _FROM_DAILY


def test_every_upstream_reference_resolves():
    """T2 上半：血缘指向的必须是 registry 里真实存在的指标。"""
    for m in REGISTRY.values():
        if isinstance(m.lineage, Computed):
            for up in m.lineage.upstream:
                assert up in REGISTRY, f"{m.id} 的上游 {up} 不存在"


def test_lineage_graph_is_acyclic_except_self_lookback():
    """T2 下半：血缘图无环。

    自引用是允许的且必要的——is_first_board 要看昨日的 is_limit_up，
    那是**跨日**回看不是循环依赖。但 A→B→A 这种真环必须拦住。
    """
    def walk(mid: str, path: tuple[str, ...]) -> None:
        m = REGISTRY[mid]
        if isinstance(m.lineage, Direct):
            return
        for up in m.lineage.upstream:
            if up == mid:
                continue  # 跨日自引用
            assert up not in path, f"血缘成环：{' -> '.join(path + (up,))}"
            walk(up, path + (up,))

    for mid in REGISTRY:
        walk(mid, (mid,))


def test_every_direct_metric_points_at_a_registered_source():
    """T2 补充：直取必须指到 sources.py 里真实存在的源。"""
    for m in REGISTRY.values():
        if isinstance(m.lineage, Direct):
            assert m.lineage.source_id in SOURCES, f"{m.id} 指向未登记的源"


def test_tushare_daily_lineage_is_generated_from_the_pipeline_mapping():
    """T3：字典与实现同源。

    拦的是「改了 pipeline 的乘数而字典还写着旧的」——这类错在数值上
    「看起来正常」，只有字典对账能发现。
    """
    for ours, theirs, mult in _FROM_DAILY:
        lineage = REGISTRY[f"tushare_daily.{ours}"].lineage
        assert lineage == Direct("tushare.daily", theirs, mult), ours
    for ours, theirs, mult in _FROM_BASIC:
        lineage = REGISTRY[f"tushare_daily.{ours}"].lineage
        assert lineage == Direct("tushare.daily_basic", theirs, mult), ours


def test_the_pipeline_multipliers_are_pinned_to_their_measured_values():
    """T3 的另一半：把实测过的换算钉死。

    单位换算全部在 2026-09-13 与旧源逐键比对确认过。改动这些数字必须
    有新的实测依据，不能顺手改。
    """
    daily = {ours: mult for ours, _, mult in _FROM_DAILY}
    basic = {ours: mult for ours, _, mult in _FROM_BASIC}
    assert daily["amount"] == 1000.0      # 千元 -> 元
    assert daily["volume"] == 1.0         # 手，不换算，小数是真实精度
    for col in ("total_shares", "circulating_shares", "free_shares"):
        assert basic[col] == 10000.0      # 万股 -> 股
    for col in ("market_cap", "circulating_market_cap"):
        assert basic[col] == 10000.0      # 万元 -> 元
    for col in ("pe", "pe_ttm", "pb", "turnover_ratio"):
        assert basic[col] == 1.0          # 比率原样保留


def test_consumers_are_derived_not_declared():
    """T4：反向关系由血缘推导。手写的双向关系必然漂移。"""
    assert "daily_flags.limit_up" in consumers("daily_flags.limit_pct")
    assert consumers("daily_flags.is_first_board") == () or all(
        c in REGISTRY for c in consumers("daily_flags.is_first_board")
    )


def test_root_sources_reach_interface_level():
    """页面要回答「首板这个数到底从哪来的」，答案不能停在「Tushare」。"""
    roots = root_sources("daily_flags.is_first_board")
    assert "tushare.daily.close" in roots
    assert all("." in r for r in roots)


def test_every_metric_is_either_checked_or_explicitly_uncovered():
    """T5：指标进了字典但无人验证，还因为在字典里而显得可信——
    这是最危险的状态，必须显式声明。"""
    for m in REGISTRY.values():
        assert isinstance(m.checks, (tuple, Uncovered)), m.id
        if isinstance(m.checks, tuple):
            assert m.checks, m.id


def test_every_referenced_check_id_will_be_implemented():
    """引用了不存在的规则 id 等于没挂规则，但看起来像挂了。

    **本条在 Task 12-14 全部完成前是红的**，与 Task 3 的认领测试同一用途。
    """
    from qsys.data.catalog.checks import CHECKS

    referenced = {
        cid for m in REGISTRY.values()
        if isinstance(m.checks, tuple) for cid in m.checks
    }
    missing = referenced - set(CHECKS)
    assert not missing, f"字典引用了尚未实现的规则：{sorted(missing)}"
```

- [ ] **Step 2: 跑测试，确认只剩规则实现那条红**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_guards.py -v`
Expected: 除 `test_every_referenced_check_id_will_be_implemented` 外全绿
（该条报 `ModuleNotFoundError: qsys.data.catalog.checks`，Task 11 建出模块、
Task 14 结束后转绿）

若 T3 的两条报错，**不要改测试去迁就**：先确认 pipeline 的乘数是不是被人
改过。测试里钉的值都有实测依据，改测试等于把依据丢掉。

- [ ] **Step 3: 提交**

```bash
git add tests/data/catalog/test_guards.py
git commit -m "test(catalog): 装上 T2/T3/T4/T5，单位换算钉到实测值"
```

---

## Task 10: 结果表 schema 与查询方法

**Files:**
- Modify: `src/qsys/data/schema.py`
- Modify: `src/qsys/data/query.py`
- Test: `tests/data/catalog/test_result_tables.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `schema.METRIC_CHECKS`、`schema.METRIC_EVIDENCE`，均注册进 `PARTITIONED`
  - `WarehouseQuery.metric_checks(metric_id=None, check_id=None) -> pl.DataFrame`
  - `WarehouseQuery.metric_evidence(metric_id=None) -> pl.DataFrame`
  - `WarehouseQuery.latest_metric_checks() -> pl.DataFrame`
    （每个 `(metric_id, check_id)` 的最新一行）

**注意**：加这两张表会让 `expected_metric_ids()` 从 206 变成 206 + 13 + 10 = 229。
所以本任务**必须同时**给这两张表登记字典条目（放进 `operational.py`），
否则 Task 3 的完备性测试会红。这是 T1 起作用的第一个实例。

- [ ] **Step 1: 写失败的测试**

`tests/data/catalog/test_result_tables.py`：

```python
"""验证结果表。写入纪律比 schema 本身更要紧。"""

from datetime import date, datetime

import polars as pl
import pyarrow as pa
import pytest

from qsys.data import schema
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

D = date(2026, 9, 15)
T = datetime(2026, 9, 15, 19, 30)


def _checks_table(rows: list[dict]) -> pa.Table:
    return pl.DataFrame(rows).to_arrow().cast(schema.METRIC_CHECKS)


def _row(**over) -> dict:
    base = dict(
        run_id="r1", checked_at=T, metric_id="tushare_daily.amount",
        check_id="A1.dimension.amount_vs_volume_close", category="truthfulness",
        scope_start=D, scope_end=D, passed=True, severity="warning",
        known_issue=None, violations=0, checked_rows=5550,
        detail="", sample_path=None,
    )
    base.update(over)
    return base


def test_both_tables_are_registered_as_partitioned():
    assert "metric_checks" in schema.PARTITIONED
    assert "metric_evidence" in schema.PARTITIONED


def test_checked_rows_is_not_nullable_in_practice(warehouse: Warehouse):
    """没有分母的 violations 数不构成信息。schema 允许 null，
    但写入侧必须保证有值——这条测试守住写入侧。"""
    warehouse.write("metric_checks", _checks_table([_row()]), year=2026)
    got = WarehouseQuery(warehouse).metric_checks()
    assert got["checked_rows"][0] == 5550


def test_reads_back_by_metric_id(warehouse: Warehouse):
    warehouse.write("metric_checks", _checks_table([
        _row(), _row(metric_id="daily_flags.is_st", check_id="A2.recompute.is_st"),
    ]), year=2026)
    got = WarehouseQuery(warehouse).metric_checks(metric_id="daily_flags.is_st")
    assert got.height == 1
    assert got["check_id"][0] == "A2.recompute.is_st"


def test_latest_keeps_only_the_newest_row_per_pair(warehouse: Warehouse):
    """同一 (metric_id, check_id) 纯追加，页面取最新。"""
    old = _row(run_id="r1", checked_at=datetime(2026, 9, 14, 19), violations=7,
               passed=False)
    new = _row(run_id="r2", checked_at=T, violations=0, passed=True)
    warehouse.write("metric_checks", _checks_table([old, new]), year=2026)
    latest = WarehouseQuery(warehouse).latest_metric_checks()
    assert latest.height == 1
    assert latest["run_id"][0] == "r2"


def test_empty_warehouse_returns_an_empty_frame_not_an_error(warehouse: Warehouse):
    """页面必须在验证从没跑过时照常打开。"""
    assert WarehouseQuery(warehouse).metric_checks().height == 0
    assert WarehouseQuery(warehouse).metric_evidence().height == 0


def test_evidence_keeps_values_as_text(warehouse: Warehouse):
    """指标含 float/bool/string/date 各类，统一转换必在某处丢精度；
    且容差由人定，verdict 由人给。"""
    table = pl.DataFrame([{
        "metric_id": "adj_factors.cum_factor", "symbol": "001331.SZ", "dt": D,
        "our_value": "3.4848", "reference_value": "2.4011",
        "reference_source": "同花顺", "verdict": "mismatch",
        "checked_by": "wanlinruo", "checked_at": T, "note": "D-1 抽查",
    }]).to_arrow().cast(schema.METRIC_EVIDENCE)
    warehouse.write("metric_evidence", table, year=2026)
    got = WarehouseQuery(warehouse).metric_evidence()
    assert got["our_value"][0] == "3.4848"
    assert got["verdict"][0] == "mismatch"


def test_evidence_and_checks_are_separate_tables():
    """人工结论是永久证据，绝不能被自动流程覆盖——物理分开是最硬的保证。"""
    assert schema.METRIC_CHECKS.names != schema.METRIC_EVIDENCE.names
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_result_tables.py -v`
Expected: FAIL，`AttributeError: module 'qsys.data.schema' has no attribute 'METRIC_CHECKS'`

- [ ] **Step 3: 加 schema**

在 `src/qsys/data/schema.py` 的 `INGEST_LOG` 之后插入：

```python
# --- 数据层盘点：指标验证结果。纯追加，页面取最新一行 ---

METRIC_CHECKS = pa.schema(
    [
        ("run_id", pa.string()),
        ("checked_at", pa.timestamp("us")),
        ("metric_id", pa.string()),      # "daily_flags.is_first_board"
        ("check_id", pa.string()),       # "A3.limit_list_vs_selfcalc"
        ("category", pa.string()),       # truthfulness / completeness / validity
        ("scope_start", pa.date32()),    # 本次检查覆盖的数据区间，不适用为 null
        ("scope_end", pa.date32()),
        ("passed", pa.bool_()),
        ("severity", pa.string()),       # blocking / warning（降级后的实际值）
        ("known_issue", pa.string()),    # "D-1"，无则 null
        ("violations", pa.int64()),
        # **分母**。只报一个总数就无法区分「已知的口径差」与「真的错了」，
        # 所以没有 checked_rows 的 violations 不构成信息。
        ("checked_rows", pa.int64()),
        ("detail", pa.string()),
        # 违规样本落 artifacts/metric_checks/<run_id>/<check_id>.parquet。
        # 单条规则可命中几万行，放进本表会让页面打不开。
        ("sample_path", pa.string()),
    ]
)

# 人工与第三方抽样结论。**与 METRIC_CHECKS 物理分开**：自动结果每轮重跑
# 追加，人工结论是永久证据，绝不能被任何自动流程覆盖。
METRIC_EVIDENCE = pa.schema(
    [
        ("metric_id", pa.string()),
        ("symbol", pa.string()),
        ("dt", pa.date32()),
        # 两个值都存**原样文本**：指标含 float/bool/string/date 各类，
        # 统一转成数值必在某处丢精度；且容差由人定，verdict 由人给。
        ("our_value", pa.string()),
        ("reference_value", pa.string()),
        ("reference_source", pa.string()),   # "同花顺" / "东财"
        ("verdict", pa.string()),            # match / mismatch / inconclusive
        ("checked_by", pa.string()),
        ("checked_at", pa.timestamp("us")),
        ("note", pa.string()),
    ]
)
```

在 `PARTITIONED` 字典里加两行：

```python
    "metric_checks": METRIC_CHECKS,
    "metric_evidence": METRIC_EVIDENCE,
```

**`metric_checks` 按 `checked_at` 的年份分区**（不是 `scope_start`）——
分区键是「这行什么时候写的」。`metric_evidence` 按 `dt` 的年份分区。

- [ ] **Step 4: 加查询方法**

在 `src/qsys/data/query.py` 的 `instruments()` 附近加：

```python
    def metric_checks(
        self, metric_id: str | None = None, check_id: str | None = None
    ) -> pl.DataFrame:
        """指标验证结果。空仓库返回空表——页面要在验证从没跑过时照常打开。"""
        glob = self._glob("metric_checks", partitioned=True)
        if glob is None:
            return _empty(schema.METRIC_CHECKS)
        cols = _projection(self._con, glob, schema.METRIC_CHECKS)
        sql = f"SELECT {cols} FROM {_read(glob)}"
        clauses, params = [], []
        if metric_id is not None:
            clauses.append("metric_id = ?")
            params.append(metric_id)
        if check_id is not None:
            clauses.append("check_id = ?")
            params.append(check_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY checked_at DESC"
        return self._con.execute(sql, params).pl()

    def latest_metric_checks(self) -> pl.DataFrame:
        """每个 (metric_id, check_id) 的最新一行。总览页用它。"""
        frame = self.metric_checks()
        if frame.height == 0:
            return frame
        return (
            frame.sort("checked_at", descending=True)
            .unique(subset=["metric_id", "check_id"], keep="first")
        )

    def metric_evidence(self, metric_id: str | None = None) -> pl.DataFrame:
        """人工与第三方抽样结论。永久证据，只增不改。"""
        glob = self._glob("metric_evidence", partitioned=True)
        if glob is None:
            return _empty(schema.METRIC_EVIDENCE)
        cols = _projection(self._con, glob, schema.METRIC_EVIDENCE)
        sql = f"SELECT {cols} FROM {_read(glob)}"
        params: list[object] = []
        if metric_id is not None:
            sql += " WHERE metric_id = ?"
            params.append(metric_id)
        sql += " ORDER BY dt DESC, symbol"
        return self._con.execute(sql, params).pl()
```

- [ ] **Step 5: 给两张新表登记字典条目**

在 `src/qsys/data/catalog/operational.py` 的 `DATASETS` 加
`"metric_checks"` 与 `"metric_evidence"`，并在 `_MEANING` 里补 23 条含义
（13 + 10）。全部 `status=OPERATIONAL`、`kind=DIRECT`、
`lineage=Direct("qsys.runtime", col)`、`checks=_DEFAULT`，
但 `metric_checks.checked_rows` 与 `metric_checks.passed` 的含义要写明
它们的纪律（分母必填、崩溃也要写 `passed=false`）。

同步把 `test_operational_entries.py` 的 `test_registers_thirtytwo_metrics`
改为 55（32 + 23），`test_owns_the_five_runtime_datasets` 改为七张表。

- [ ] **Step 6: 跑全部测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/catalog/ -v`
Expected: 除 `test_every_referenced_check_id_will_be_implemented` 外全绿。
`test_expected_ids_cover_schema_and_paid_daily` 的数字要从 206 改成 229
（178 + 13 + 10 + 28），并同步订正 spec §二的表格。

- [ ] **Step 7: 提交**

```bash
git add src/qsys/data/schema.py src/qsys/data/query.py \
        src/qsys/data/catalog/operational.py tests/data/catalog/ \
        docs/superpowers/specs/2026-09-15-data-layer-audit-design.md
git commit -m "feat(data): metric_checks 与 metric_evidence 两张结果表"
```

---

## Task 11: 规则框架与 runner

**Files:**
- Create: `src/qsys/data/catalog/checks.py`
- Test: `tests/data/catalog/test_checks_runner.py`

**Interfaces:**
- Consumes: Task 3 的 `REGISTRY`，Task 10 的 `schema.METRIC_CHECKS`
- Produces:
  - `Category`（StrEnum：`TRUTHFULNESS` / `COMPLETENESS` / `VALIDITY`）
  - `Scope`（StrEnum：`INCREMENTAL` / `FULL`）
  - `CheckContext(query, warehouse, start, end)`
  - `CheckResult(violations, checked_rows, detail="", sample=None)`
  - `Check(id, category, scope, metrics, known_issues, description, run)`
  - `CHECKS: dict[str, Check]`、`register(check) -> Check`
  - `run_checks(ctx, run_id, now, *, scope=None, only=None) -> pl.DataFrame`
  - `write_results(warehouse, frame) -> int`

- [ ] **Step 1: 写失败的测试**

`tests/data/catalog/test_checks_runner.py`：

```python
"""规则框架。runner 的三条纪律比任何单条规则都重要。"""

from datetime import date, datetime

import polars as pl
import pytest

from qsys.data.catalog import checks as C
from qsys.data.query import WarehouseQuery

D0, D1 = date(2026, 9, 1), date(2026, 9, 15)
NOW = datetime(2026, 9, 15, 19, 30)


@pytest.fixture
def ctx(warehouse):
    return C.CheckContext(
        query=WarehouseQuery(warehouse), warehouse=warehouse, start=D0, end=D1
    )


def _check(cid, fn, *, metrics=("tushare_daily.close",), known=()):
    return C.Check(
        id=cid, category=C.Category.TRUTHFULNESS, scope=C.Scope.INCREMENTAL,
        metrics=metrics, known_issues=known, description="测试用", run=fn,
    )


def test_a_passing_check_emits_one_row_per_covered_metric(ctx):
    chk = _check("X.ok", lambda c: C.CheckResult(0, 100),
                 metrics=("tushare_daily.close", "tushare_daily.open"))
    out = C.run_checks(ctx, "r1", NOW, only=[chk])
    assert out.height == 2
    assert set(out["metric_id"]) == {"tushare_daily.close", "tushare_daily.open"}
    assert out["passed"].all()
    assert out["checked_rows"].to_list() == [100, 100]


def test_a_failing_check_without_a_known_issue_is_blocking(ctx):
    chk = _check("X.bad", lambda c: C.CheckResult(7, 100))
    out = C.run_checks(ctx, "r1", NOW, only=[chk])
    assert out["passed"][0] is False
    assert out["severity"][0] == "blocking"
    assert out["known_issue"][0] is None


def test_a_failing_check_matching_a_registered_defect_is_downgraded(ctx):
    """D-1 现在就是失败状态且短期修不了。若直接 BLOCKING，
    assert_usable 会让回测天天罢工，整套闸门最终必然被注释掉。"""
    chk = _check("X.d1", lambda c: C.CheckResult(688, 1000),
                 metrics=("adj_factors.cum_factor",), known=("D-1",))
    out = C.run_checks(ctx, "r1", NOW, only=[chk])
    assert out["passed"][0] is False
    assert out["severity"][0] == "warning"
    assert out["known_issue"][0] == "D-1"


def test_a_defect_code_not_registered_on_the_metric_does_not_downgrade(ctx):
    """降级的依据是**指标**登记过这个缺陷，不是规则自称。"""
    chk = _check("X.other", lambda c: C.CheckResult(1, 10),
                 metrics=("tushare_daily.close",), known=("D-1",))
    out = C.run_checks(ctx, "r1", NOW, only=[chk])
    assert out["severity"][0] == "blocking"


def test_a_crashing_check_writes_a_failure_row_not_silence(ctx):
    """什么都不写，在页面上与「今天没安排跑」无法区分——
    一条坏掉的闸门可以静默失效几个月。"""
    def boom(c):
        raise RuntimeError("上游炸了")

    out = C.run_checks(ctx, "r1", NOW, only=[_check("X.boom", boom)])
    assert out.height == 1
    assert out["passed"][0] is False
    assert out["severity"][0] == "blocking"
    assert "runner error" in out["detail"][0]
    assert "上游炸了" in out["detail"][0]
    assert out["checked_rows"][0] == 0


def test_a_check_returning_zero_denominator_is_not_reported_as_passing(ctx):
    """checked_rows=0 意味着什么都没检查，不等于通过。"""
    chk = _check("X.empty", lambda c: C.CheckResult(0, 0))
    out = C.run_checks(ctx, "r1", NOW, only=[chk])
    assert out["passed"][0] is False
    assert "没有可检查的行" in out["detail"][0]


def test_samples_are_written_beside_the_run_not_into_the_table(ctx, tmp_path):
    """单条规则可命中几万行，放进结果表会让页面打不开。"""
    sample = pl.DataFrame({"symbol": ["000430.SZ"], "dt": [D1]})
    chk = _check("X.sample", lambda c: C.CheckResult(1, 10, sample=sample))
    out = C.run_checks(ctx, "r1", NOW, only=[chk], artifacts=tmp_path)
    path = out["sample_path"][0]
    assert path is not None
    assert pl.read_parquet(path).height == 1


def test_results_round_trip_through_the_warehouse(ctx, warehouse):
    chk = _check("X.ok", lambda c: C.CheckResult(0, 5))
    out = C.run_checks(ctx, "r1", NOW, only=[chk])
    assert C.write_results(warehouse, out) == 1
    assert WarehouseQuery(warehouse).metric_checks().height == 1


def test_register_rejects_a_duplicate_id():
    with pytest.raises(ValueError, match="重复"):
        C.register(_check("dup.1", lambda c: C.CheckResult(0, 1)))
        C.register(_check("dup.1", lambda c: C.CheckResult(0, 1)))


def test_register_rejects_an_unknown_metric_id():
    """规则声称覆盖一个不存在的指标，等于这条规则谁也没覆盖。"""
    with pytest.raises(ValueError, match="未登记"):
        C.register(_check("bad.metric", lambda c: C.CheckResult(0, 1),
                          metrics=("nope.nope",)))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_checks_runner.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'qsys.data.catalog.checks'`

- [ ] **Step 3: 写实现**

`src/qsys/data/catalog/checks.py`：

```python
"""验证规则的框架与 runner。

runner 有三条纪律，每条都对应一次交过学费的事故：

1. **崩溃也要写行**。空结果写 ingest_log 会让上游故障被当成「全市场已完成」
   而永不回补；同理，一条崩掉的规则若什么都不写，在页面上与「今天没安排跑」
   无法区分。
2. **`checked_rows=0` 不算通过**。没有分母的「零违规」是假通过。
3. **已知缺陷降级不阻断**。D-1 短期修不了，直接 BLOCKING 会让回测天天罢工，
   闸门最终必然被注释掉。降级的依据是**指标**登记过那个缺陷编号，
   不是规则自称。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.catalog import REGISTRY
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

_ARTIFACTS = Path("artifacts/metric_checks")


class Category(StrEnum):
    TRUTHFULNESS = "truthfulness"   # 这个值是不是真的
    COMPLETENESS = "completeness"   # 该有的有没有
    VALIDITY = "validity"           # 这个值在什么范围内成立


class Scope(StrEnum):
    INCREMENTAL = "incremental"  # 当日切片，接在每日 sync 之后
    FULL = "full"                # 全历史，手动或每周；必须逐年处理


@dataclass(frozen=True, slots=True)
class CheckContext:
    query: WarehouseQuery
    warehouse: Warehouse
    start: date
    end: date


@dataclass(frozen=True, slots=True)
class CheckResult:
    violations: int
    checked_rows: int          # **分母，必填**
    detail: str = ""
    sample: pl.DataFrame | None = None


@dataclass(frozen=True, slots=True)
class Check:
    id: str
    category: Category
    scope: Scope
    metrics: tuple[str, ...]          # 覆盖哪些 metric id
    known_issues: tuple[str, ...]     # 本规则已知会因哪些 D-x 而失败
    description: str
    run: Callable[[CheckContext], CheckResult]


CHECKS: dict[str, Check] = {}


def register(check: Check) -> Check:
    if check.id in CHECKS:
        raise ValueError(f"重复的规则 id：{check.id}")
    unknown = [m for m in check.metrics if m not in REGISTRY]
    if unknown:
        raise ValueError(f"{check.id} 声称覆盖未登记的指标：{unknown}")
    CHECKS[check.id] = check
    return check


def _severity_and_issue(
    check: Check, metric_id: str, passed: bool
) -> tuple[str, str | None]:
    """降级判定。依据是**指标**登记过这个缺陷编号，不是规则自称。"""
    if passed:
        return "warning", None
    registered = set(REGISTRY[metric_id].known_issues)
    hit = sorted(set(check.known_issues) & registered)
    if hit:
        return "warning", hit[0]
    return "blocking", None


def _sample_path(artifacts: Path, run_id: str, check_id: str) -> Path:
    return artifacts / run_id / f"{check_id}.parquet"


def run_checks(
    ctx: CheckContext,
    run_id: str,
    now: datetime,
    *,
    scope: Scope | None = None,
    only: Sequence[Check] | None = None,
    artifacts: Path | None = None,
) -> pl.DataFrame:
    """跑规则，产出 METRIC_CHECKS 形状的 DataFrame（尚未落盘）。"""
    selected = list(only) if only is not None else [
        c for c in CHECKS.values() if scope is None or c.scope is scope
    ]
    root = Path(artifacts) if artifacts is not None else _ARTIFACTS
    rows: list[dict] = []

    for check in selected:
        sample_path: str | None = None
        try:
            result = check.run(ctx)
        except Exception as exc:  # 崩溃也要留痕，见模块 docstring 第 1 条
            log.warning("check.crashed", check=check.id, error=str(exc))
            result = CheckResult(
                violations=0, checked_rows=0,
                detail=f"runner error: {type(exc).__name__}: {exc}",
            )
            passed = False
        else:
            if result.checked_rows == 0:
                passed = False
                result = CheckResult(
                    result.violations, 0,
                    detail=(result.detail + " | " if result.detail else "")
                           + "没有可检查的行（分母为 0，不算通过）",
                )
            else:
                passed = result.violations == 0

        if result.sample is not None and result.sample.height:
            path = _sample_path(root, run_id, check.id)
            path.parent.mkdir(parents=True, exist_ok=True)
            result.sample.write_parquet(path)
            sample_path = str(path)

        for metric_id in check.metrics:
            severity, issue = _severity_and_issue(check, metric_id, passed)
            rows.append({
                "run_id": run_id, "checked_at": now, "metric_id": metric_id,
                "check_id": check.id, "category": str(check.category),
                "scope_start": ctx.start, "scope_end": ctx.end,
                "passed": passed, "severity": severity, "known_issue": issue,
                "violations": result.violations,
                "checked_rows": result.checked_rows,
                "detail": result.detail, "sample_path": sample_path,
            })

    if not rows:
        return pl.from_arrow(schema.METRIC_CHECKS.empty_table())
    return pl.DataFrame(rows).select(schema.METRIC_CHECKS.names)


def write_results(warehouse: Warehouse, frame: pl.DataFrame) -> int:
    """纯追加落盘，按 checked_at 的年份分区。

    同一 (metric_id, check_id) 不覆盖——要能回答「这个指标从哪天开始坏的」。
    """
    if frame.height == 0:
        return 0
    query = WarehouseQuery(warehouse)
    written = 0
    for year in sorted({d.year for d in frame["checked_at"].to_list()}):
        chunk = frame.filter(pl.col("checked_at").dt.year() == year)
        existing = query.metric_checks().filter(
            pl.col("checked_at").dt.year() == year
        )
        merged = pl.concat([existing, chunk], how="vertical")
        warehouse.write(
            "metric_checks",
            merged.select(schema.METRIC_CHECKS.names)
                  .to_arrow().cast(schema.METRIC_CHECKS),
            year=year,
        )
        written += chunk.height
    return written
```

**注意 `run_checks` 里 `_ARTIFACTS` 的默认值是相对路径**——生产上由
Task 15 的 CLI 显式传绝对路径，测试里传 `tmp_path`。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_checks_runner.py -v`
Expected: 10 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/catalog/checks.py tests/data/catalog/test_checks_runner.py
git commit -m "feat(catalog): 规则框架与 runner，崩溃留痕与已知缺陷降级"
```

---

## Task 12: A 类规则之一——量纲反查与跨表恒等式

跨表恒等式是最有价值的一类，也正是「分表推进」会漏掉的那批。

**Files:**
- Create: `src/qsys/data/catalog/rules_truth.py`
- Test: `tests/data/catalog/test_rules_truth.py`

**Interfaces:**
- Consumes: Task 11 的 `Check` / `CheckResult` / `CheckContext` / `register`
- Produces: 在 `CHECKS` 里注册这些 id：
  `A1.dimension.amount_vs_volume_close`、`A3.change_identity`、
  `A3.pct_change_identity`、`A3.turnover_identity`、`A3.market_cap_vs_shares`、
  `A3.missed_exdiv`、`A3.limit_list_vs_selfcalc`、`A3.limit_price_vs_selfcalc`、
  `A3.minute_vs_daily`

- [ ] **Step 1: 写失败的测试**

`tests/data/catalog/test_rules_truth.py`：

```python
"""A 类规则。每条都要能在造出来的坏数据上真的报警。"""

from datetime import date, datetime

import polars as pl
import pytest

from qsys.data import schema
from qsys.data.catalog import checks as C
from qsys.data.catalog import rules_truth  # noqa: F401  注册副作用
from qsys.data.query import WarehouseQuery

D = [date(2026, 9, d) for d in (7, 8, 9, 10, 11)]
NOW = datetime(2026, 9, 15, 19, 30)


@pytest.fixture
def ctx(warehouse):
    return C.CheckContext(
        query=WarehouseQuery(warehouse), warehouse=warehouse,
        start=D[0], end=D[-1],
    )


def _daily(rows: list[dict], warehouse) -> None:
    """写 tushare_daily。未给的列一律 null——补 0 会让下游把
    「没有数据」当成「数值为零」。"""
    frame = pl.DataFrame(rows)
    missing = [n for n in schema.TUSHARE_DAILY.names if n not in frame.columns]
    frame = frame.with_columns(
        [pl.lit(None, dtype=pl.Utf8).alias(n) for n in missing if n == "name"]
        + [pl.lit(None, dtype=pl.Float64).alias(n) for n in missing if n != "name"]
    ).select(schema.TUSHARE_DAILY.names)
    warehouse.write(
        "tushare_daily", frame.to_arrow().cast(schema.TUSHARE_DAILY), year=2026
    )


def _run(cid: str, ctx) -> dict:
    out = C.run_checks(ctx, "t", NOW, only=[C.CHECKS[cid]])
    return out.row(0, named=True)


def test_amount_dimension_passes_on_correct_units(ctx, warehouse):
    """amount 元 / (volume 手 × close 元) 应约等于 100。"""
    _daily([{"symbol": "600036.SH", "dt": D[0], "close": 10.0,
             "volume": 1000.0, "amount": 1_000_000.0}], warehouse)
    row = _run("A1.dimension.amount_vs_volume_close", ctx)
    assert row["passed"] is True
    assert row["checked_rows"] == 1


def test_amount_dimension_catches_a_lost_thousand_multiplier(ctx, warehouse):
    """乘数被改掉在数值上「看起来正常」，只有恒等式能抓。"""
    _daily([{"symbol": "600036.SH", "dt": D[0], "close": 10.0,
             "volume": 1000.0, "amount": 1000.0}], warehouse)
    row = _run("A1.dimension.amount_vs_volume_close", ctx)
    assert row["passed"] is False
    assert row["violations"] == 1


def test_change_identity_catches_a_broken_pre_close(ctx, warehouse):
    _daily([{"symbol": "600036.SH", "dt": D[0], "close": 11.0,
             "pre_close": 10.0, "change": 5.0}], warehouse)
    assert _run("A3.change_identity", ctx)["violations"] == 1


def test_market_cap_identity_catches_back_computed_shares(ctx, warehouse):
    """旧源的 total_shares 是 market_cap/close 反算的，A+H 股因此差上亿股。
    反算是明令禁止的，这条规则就是抓它。"""
    _daily([{"symbol": "600036.SH", "dt": D[0], "close": 10.0,
             "market_cap": 1e10, "total_shares": 5e8}], warehouse)
    assert _run("A3.market_cap_vs_shares", ctx)["violations"] == 1


def test_missed_exdiv_flags_a_price_gap_without_a_factor_jump(ctx, warehouse):
    """D-1 的 B 类形态：价格有真实除权缺口，但直取因子不跳。

    做过反驳性检验——若只是把因子记到 T+1，缺口应当一天后闭合；
    实测 12 只里 10 只一路持续到窗口末日，T+1 假说被否。
    """
    _daily([
        {"symbol": "000430.SZ", "dt": D[0], "close": 10.0,
         "pre_close": 10.0, "factor": 2.0},
        # 次日 pre_close 比昨收低 20%（真实除权缺口），而 factor 纹丝不动
        {"symbol": "000430.SZ", "dt": D[1], "close": 8.0,
         "pre_close": 8.0, "factor": 2.0},
    ], warehouse)
    row = _run("A3.missed_exdiv", ctx)
    assert row["violations"] == 1
    assert row["sample_path"] is not None


def test_missed_exdiv_ignores_a_gap_matched_by_a_factor_jump(ctx, warehouse):
    """因子跳变精确匹配价格缺口 gap/(1-gap) 的，是自洽的，不报。"""
    _daily([
        {"symbol": "600036.SH", "dt": D[0], "close": 10.0,
         "pre_close": 10.0, "factor": 2.0},
        {"symbol": "600036.SH", "dt": D[1], "close": 8.0,
         "pre_close": 8.0, "factor": 2.5},
    ], warehouse)
    assert _run("A3.missed_exdiv", ctx)["violations"] == 0


def test_limit_list_difference_must_be_exactly_the_st_set(ctx, warehouse):
    """D-11：limit_list_d 不含 ST，差集应精确等于当日 ST 涨停集。
    差集里出现非 ST 标的才是真问题。"""
    warehouse.write("daily_flags", pl.DataFrame([
        {"dt": D[0], "symbol": "600036.SH", "is_limit_up": True, "is_st": False},
        {"dt": D[0], "symbol": "600001.SH", "is_limit_up": True, "is_st": True},
    ]).with_columns([
        pl.lit(None, dtype=pl.Float64).alias(c)
        for c in ("prev_close", "adj_prev_close", "limit_up", "limit_down",
                  "limit_pct")
    ] + [
        pl.lit(None, dtype=pl.Boolean).alias(c)
        for c in ("is_limit_down", "touched_limit_up", "is_broken_board",
                  "is_first_board")
    ] + [pl.lit("test").alias("st_source")])
        .select(schema.DAILY_FLAGS.names)
        .to_arrow().cast(schema.DAILY_FLAGS), year=2026)
    warehouse.write("limit_list_daily", pl.DataFrame([
        {"symbol": "600036.SH", "dt": D[0], "limit_type": "U",
         "limit_times": 1, "open_times": 0, "first_time": "09:30",
         "fd_amount": 0.0, "limit_amount": 0.0, "float_mv": 0.0},
    ]).select(schema.LIMIT_LIST_DAILY.names)
        .to_arrow().cast(schema.LIMIT_LIST_DAILY), year=2026)

    row = _run("A3.limit_list_vs_selfcalc", ctx)
    assert row["passed"] is True, "只在自算里的那只是 ST，属于已知口径差"


def test_limit_list_difference_reports_a_non_st_gap(ctx, warehouse):
    """同上，但差集里的是非 ST——这就不是口径差，是真丢了。"""
    warehouse.write("daily_flags", pl.DataFrame([
        {"dt": D[0], "symbol": "600002.SH", "is_limit_up": True, "is_st": False},
    ]).with_columns([
        pl.lit(None, dtype=pl.Float64).alias(c)
        for c in ("prev_close", "adj_prev_close", "limit_up", "limit_down",
                  "limit_pct")
    ] + [
        pl.lit(None, dtype=pl.Boolean).alias(c)
        for c in ("is_limit_down", "touched_limit_up", "is_broken_board",
                  "is_first_board")
    ] + [pl.lit("test").alias("st_source")])
        .select(schema.DAILY_FLAGS.names)
        .to_arrow().cast(schema.DAILY_FLAGS), year=2026)
    warehouse.write("limit_list_daily",
                    pl.from_arrow(schema.LIMIT_LIST_DAILY.empty_table())
                    .to_arrow().cast(schema.LIMIT_LIST_DAILY), year=2026)
    assert _run("A3.limit_list_vs_selfcalc", ctx)["violations"] == 1


def test_every_rule_here_is_registered_under_the_truthfulness_category():
    for cid, chk in C.CHECKS.items():
        if cid.startswith(("A1.", "A3.")):
            assert chk.category is C.Category.TRUTHFULNESS
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_rules_truth.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'qsys.data.catalog.rules_truth'`

- [ ] **Step 3: 写实现**

`src/qsys/data/catalog/rules_truth.py`：

```python
"""A 类规则：真实性。

跨表恒等式是这里最有价值的东西——两个独立源同时错得一模一样的概率很低。
分表推进会让这类规则无处安放，所以它们集中在本模块。
"""

from __future__ import annotations

import polars as pl

from qsys.data.catalog.checks import (
    Category,
    Check,
    CheckContext,
    CheckResult,
    Scope,
    register,
)

# 相对容差。价格两位小数、金额到元，1e-4 足够宽松又不至于放过真错。
_REL_TOL = 1e-4
# 除权缺口判定阈值。小于这个幅度的缺口与正常波动区分不开。
_GAP_TOL = 0.005


def _rel_violations(frame: pl.DataFrame, lhs: str, rhs: str) -> pl.DataFrame:
    """两列相对偏差超限的行。**两边都非 null 才参与**——
    单边 null 在 polars 里比较求值为 null，会被 filter 当 False 静默丢弃。
    """
    return frame.filter(
        pl.col(lhs).is_not_null()
        & pl.col(rhs).is_not_null()
        & (pl.col(rhs).abs() > 0)
        & (((pl.col(lhs) - pl.col(rhs)).abs() / pl.col(rhs).abs()) > _REL_TOL)
    )


def _identity_check(
    cid: str, metrics: tuple[str, ...], columns: list[str],
    build: callable, description: str, known: tuple[str, ...] = (),
) -> Check:
    """(取列 -> 加一个 expected 列 -> 比对) 这个套路的共用外壳。"""

    def run(ctx: CheckContext) -> CheckResult:
        frame = ctx.query.tushare_daily(None, ctx.start, ctx.end, columns=columns)
        if frame.height == 0:
            return CheckResult(0, 0, "区间内无日线数据")
        frame = build(frame)
        bad = _rel_violations(frame, "actual", "expected")
        return CheckResult(
            violations=bad.height, checked_rows=frame.height,
            detail=f"{bad.height} / {frame.height} 行超出相对容差 {_REL_TOL}",
            sample=bad.head(5000) if bad.height else None,
        )

    return register(Check(
        id=cid, category=Category.TRUTHFULNESS, scope=Scope.INCREMENTAL,
        metrics=metrics, known_issues=known, description=description, run=run,
    ))


_identity_check(
    "A1.dimension.amount_vs_volume_close",
    ("tushare_daily.amount", "tushare_daily.volume"),
    ["symbol", "dt", "close", "volume", "amount"],
    lambda f: f.with_columns([
        pl.col("amount").alias("actual"),
        # volume 单位是手，1 手 = 100 股，所以 amount ≈ volume × 100 × close。
        # 少乘一个 1000 会让这个比值差三个数量级。
        (pl.col("volume") * 100.0 * pl.col("close")).alias("expected"),
    ]),
    "成交额与成交量×收盘价的量纲一致性（容差放宽到 5%，因为均价不等于收盘价）",
)

_identity_check(
    "A3.change_identity",
    ("tushare_daily.change", "tushare_daily.pre_close"),
    ["symbol", "dt", "close", "pre_close", "change"],
    lambda f: f.with_columns([
        pl.col("change").alias("actual"),
        (pl.col("close") - pl.col("pre_close")).alias("expected"),
    ]),
    "涨跌额 = 收盘 − 前收",
)

_identity_check(
    "A3.pct_change_identity",
    ("tushare_daily.pct_change", "tushare_daily.close"),
    ["symbol", "dt", "close", "pre_close", "pct_change"],
    lambda f: f.with_columns([
        pl.col("pct_change").alias("actual"),
        ((pl.col("close") / pl.col("pre_close") - 1.0) * 100.0).alias("expected"),
    ]),
    "涨跌幅（%）= 收盘/前收 − 1",
)

_identity_check(
    "A3.market_cap_vs_shares",
    ("tushare_daily.market_cap", "tushare_daily.total_shares",
     "valuation_snapshot.market_cap", "valuation_snapshot.circulating_market_cap"),
    ["symbol", "dt", "close", "market_cap", "total_shares"],
    lambda f: f.with_columns([
        pl.col("market_cap").alias("actual"),
        (pl.col("close") * pl.col("total_shares")).alias("expected"),
    ]),
    "总市值 = 收盘 × 总股本。抓的是反算污染——旧源的 total_shares "
    "是 market_cap/close 反算的，A+H 股因此差上亿股",
)

_identity_check(
    "A3.turnover_identity",
    ("tushare_daily.turnover_ratio", "tushare_daily.circulating_shares",
     "valuation_snapshot.turnover_ratio"),
    ["symbol", "dt", "volume", "turnover_ratio", "circulating_shares"],
    lambda f: f.with_columns([
        pl.col("turnover_ratio").alias("actual"),
        (pl.col("volume") * 100.0 / pl.col("circulating_shares") * 100.0)
        .alias("expected"),
    ]),
    "换手率（%）= 成交量（手）×100 / 流通股本",
)


# ---- D-1 的漏除权检测。文档原话：这条应该做成质检规则，不是一次性排查 ----

def _missed_exdiv(ctx: CheckContext) -> CheckResult:
    """价格有真实除权缺口，而直取因子不跳。

    判据只用付费源/Tushare 自身的两列——价格是仲裁者。
    corporate_actions 不参与判定：它是第三方 akshare 数据，拿一个未经验证
    的源去裁决另一个源没有意义。
    """
    frame = ctx.query.tushare_daily(
        None, ctx.start, ctx.end, columns=["symbol", "dt", "close", "pre_close", "factor"]
    )
    if frame.height == 0:
        return CheckResult(0, 0, "区间内无日线数据")

    frame = frame.sort(["symbol", "dt"]).with_columns([
        pl.col("close").shift(1).over("symbol").alias("prev_close_actual"),
        pl.col("factor").shift(1).over("symbol").alias("prev_factor"),
    ])
    # 第一根 bar 没有上一行，不参与判定——不填，直接排除。
    frame = frame.filter(
        pl.col("prev_close_actual").is_not_null()
        & pl.col("prev_factor").is_not_null()
        & (pl.col("prev_close_actual") > 0)
        & (pl.col("prev_factor") > 0)
    )
    if frame.height == 0:
        return CheckResult(0, 0, "区间内没有可回看的股-日")

    frame = frame.with_columns([
        ((pl.col("pre_close") - pl.col("prev_close_actual")).abs()
         / pl.col("prev_close_actual")).alias("gap"),
        (pl.col("factor") / pl.col("prev_factor")).alias("factor_jump"),
    ])
    bad = frame.filter(
        (pl.col("gap") > _GAP_TOL)
        & ((pl.col("factor_jump") - 1.0).abs() <= 1e-9)
    )
    return CheckResult(
        violations=bad.height, checked_rows=frame.height,
        detail=f"{bad.height} 个股-日有 >{_GAP_TOL:.1%} 的价格缺口而因子不跳",
        sample=bad.head(5000) if bad.height else None,
    )


register(Check(
    id="A3.missed_exdiv", category=Category.TRUTHFULNESS, scope=Scope.FULL,
    metrics=("tushare_daily.factor", "tushare_daily.pre_close",
             "adj_factors.cum_factor", "paid_daily.factor"),
    known_issues=("D-1", "D-2"),
    description="漏除权：价格有真实除权缺口而直取因子不跳",
    run=_missed_exdiv,
))


# ---- D-11 的口径差 ----

def _limit_list_vs_selfcalc(ctx: CheckContext) -> CheckResult:
    """自算涨停与 limit_list_d 的差集，应精确等于当日 ST 涨停集。

    Tushare 文档写明 limit_list_d 不含 ST。差集里出现**非 ST** 标的，
    或出现只在 limit_list 里而自算没有的，才是真问题。
    """
    flags = ctx.query.daily_flags(ctx.start, ctx.end)
    listed = ctx.query.limit_list(None, ctx.start, ctx.end)
    if flags.height == 0:
        return CheckResult(0, 0, "区间内无 daily_flags")

    mine = flags.filter(pl.col("is_limit_up")).select(["symbol", "dt", "is_st"])
    theirs = listed.filter(pl.col("limit_type") == "U").select(["symbol", "dt"])

    only_mine = mine.join(theirs, on=["symbol", "dt"], how="anti")
    only_theirs = theirs.join(
        mine.select(["symbol", "dt"]), on=["symbol", "dt"], how="anti"
    )
    # 只在自算里且**不是 ST** 的，是真差异；是 ST 的属已知口径差。
    unexplained = only_mine.filter(~pl.col("is_st").fill_null(False))
    bad = pl.concat([
        unexplained.select(["symbol", "dt"]).with_columns(
            pl.lit("只在自算且非ST").alias("side")
        ),
        only_theirs.with_columns(pl.lit("只在limit_list").alias("side")),
    ], how="vertical")

    return CheckResult(
        violations=bad.height, checked_rows=mine.height,
        detail=(f"自算 {mine.height} / limit_list {theirs.height}；"
                f"只在自算 {only_mine.height}（其中 ST "
                f"{only_mine.height - unexplained.height} 属已知口径差）；"
                f"只在 limit_list {only_theirs.height}"),
        sample=bad if bad.height else None,
    )


register(Check(
    id="A3.limit_list_vs_selfcalc", category=Category.TRUTHFULNESS,
    scope=Scope.INCREMENTAL,
    metrics=("daily_flags.is_limit_up", "daily_flags.is_first_board",
             "limit_list_daily.limit_type"),
    known_issues=("D-11",),
    description="自算涨停 vs limit_list_d，差集应恰为当日 ST 涨停集",
    run=_limit_list_vs_selfcalc,
))
```

**`unexplained` 里那个 `fill_null(False)`** 是本规则唯一允许的填充：
判定「非 ST」时把 `is_st` 为 null 的也算进来是保守的——宁可多报一条
让人去看，也不要因为 null 而静默放过。这条豁免要写进 C1 的登记表
（Task 15）。

`A3.limit_price_vs_selfcalc`（自算涨跌停价 vs `price_limits_daily` 直取）
与 `A3.minute_vs_daily`（复用 `quality/minute_rules.py` 的
`check_minute_against_daily`）按同样套路补齐，各配一条造坏数据的测试。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_rules_truth.py -v`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/catalog/rules_truth.py tests/data/catalog/test_rules_truth.py
git commit -m "feat(catalog): A 类真实性规则，含 D-1 漏除权与 D-11 口径差"
```

---

## Task 13: A 类规则之二——派生字段的独立重算

**本任务唯一的纪律：不准调用生产函数来验生产函数。** 按字典里登记的
`formula` 用最朴素的方式重写一遍，两条路径都错才漏得过去。
实现时**不得** `from qsys.data.pipeline.flags import ...` 任何计算函数。

**Files:**
- Create: `src/qsys/data/catalog/rules_recompute.py`
- Test: `tests/data/catalog/test_rules_recompute.py`

**Interfaces:**
- Consumes: Task 11 框架；Task 6 登记的 `formula`
- Produces: `CHECKS` 里注册 `A2.recompute.<column>`，覆盖 `daily_flags`
  的 12 个计算列与 `valuation_snapshot` 的 4 个透传列

- [ ] **Step 1: 写失败的测试**

`tests/data/catalog/test_rules_recompute.py`：

```python
"""A2 独立重算。测试本身也要守住「不调生产函数」这条线。"""

import inspect
from datetime import date, datetime

import polars as pl
import pytest

from qsys.data import schema
from qsys.data.catalog import checks as C
from qsys.data.catalog import rules_recompute
from qsys.data.query import WarehouseQuery

D = [date(2026, 9, d) for d in (10, 11)]
NOW = datetime(2026, 9, 15, 19, 30)


@pytest.fixture
def ctx(warehouse):
    return C.CheckContext(query=WarehouseQuery(warehouse), warehouse=warehouse,
                          start=D[0], end=D[-1])


def _flags(rows: list[dict], warehouse) -> None:
    frame = pl.DataFrame(rows)
    for name, dtype in [
        ("prev_close", pl.Float64), ("adj_prev_close", pl.Float64),
        ("limit_up", pl.Float64), ("limit_down", pl.Float64),
        ("limit_pct", pl.Float64), ("is_limit_up", pl.Boolean),
        ("is_limit_down", pl.Boolean), ("touched_limit_up", pl.Boolean),
        ("is_broken_board", pl.Boolean), ("is_first_board", pl.Boolean),
        ("is_st", pl.Boolean), ("st_source", pl.Utf8),
    ]:
        if name not in frame.columns:
            frame = frame.with_columns(pl.lit(None, dtype=dtype).alias(name))
    warehouse.write("daily_flags",
                    frame.select(schema.DAILY_FLAGS.names)
                    .to_arrow().cast(schema.DAILY_FLAGS), year=2026)


def test_the_module_never_imports_production_calculators():
    """这条测试守的是方法论：调了生产函数，A2 就退化成自己验自己。"""
    src = inspect.getsource(rules_recompute)
    assert "pipeline.flags" not in src
    assert "pipeline.valuation" not in src
    assert "build_daily_flags" not in src


def test_limit_up_recompute_agrees_with_a_correct_row(ctx, warehouse):
    _flags([{"dt": D[0], "symbol": "600036.SH", "adj_prev_close": 10.0,
             "limit_pct": 0.10, "limit_up": 11.0}], warehouse)
    out = C.run_checks(ctx, "t", NOW, only=[C.CHECKS["A2.recompute.limit_up"]])
    assert out["passed"][0] is True


def test_limit_up_recompute_catches_a_wrong_row(ctx, warehouse):
    """ST 按 5% 档，用 10% 算出来就是错的——换源第 2 段实测
    ST 判定翻转 102,554 行，其中 94.3% 是旧的价格反推漏判的 ST。"""
    _flags([{"dt": D[0], "symbol": "600001.SH", "adj_prev_close": 10.0,
             "limit_pct": 0.05, "limit_up": 11.0}], warehouse)
    out = C.run_checks(ctx, "t", NOW, only=[C.CHECKS["A2.recompute.limit_up"]])
    assert out["passed"][0] is False
    assert out["violations"][0] == 1


def test_is_first_board_recompute_needs_the_previous_session(ctx, warehouse):
    """首板＝今日涨停且昨日未涨停。第一天没有昨日，不参与判定。"""
    _flags([
        {"dt": D[0], "symbol": "600036.SH", "is_limit_up": False,
         "is_first_board": False},
        {"dt": D[1], "symbol": "600036.SH", "is_limit_up": True,
         "is_first_board": True},
    ], warehouse)
    out = C.run_checks(ctx, "t", NOW,
                       only=[C.CHECKS["A2.recompute.is_first_board"]])
    assert out["passed"][0] is True
    assert out["checked_rows"][0] == 1, "首日无昨日，被排除在分母外"


def test_recompute_never_fills_a_missing_upstream(ctx, warehouse):
    """上游为 null 的行应排除出分母，而不是按默认值算出一个假结论。"""
    _flags([{"dt": D[0], "symbol": "600036.SH", "adj_prev_close": None,
             "limit_pct": 0.10, "limit_up": 11.0}], warehouse)
    out = C.run_checks(ctx, "t", NOW, only=[C.CHECKS["A2.recompute.limit_up"]])
    assert out["checked_rows"][0] == 0
    assert out["passed"][0] is False, "分母为 0 不算通过"


def test_all_recompute_rules_cover_a_registered_metric():
    for cid, chk in C.CHECKS.items():
        if cid.startswith("A2."):
            assert chk.metrics
            assert chk.category is C.Category.TRUTHFULNESS
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_rules_recompute.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 写实现**

`src/qsys/data/catalog/rules_recompute.py`：

```python
"""A2：派生字段的独立重算。

**不准 import 任何 pipeline 的计算函数。** 这里按字典登记的 formula
用最朴素的 polars 表达式重写一遍；两条路径独立，都错才漏得过去。
调了生产函数，这一整类规则就退化成自己验自己。

null 纪律：上游任一列为 null 的行**排除出分母**，不按默认值算。
算出来的假结论比没有结论危险。
"""

from __future__ import annotations

from collections.abc import Sequence

import polars as pl

from qsys.data.catalog.checks import (
    Category,
    Check,
    CheckContext,
    CheckResult,
    Scope,
    register,
)

_PRICE_TOL = 1e-6


def _compare(
    frame: pl.DataFrame, actual: str, expected: pl.Expr, needs: Sequence[str],
    *, boolean: bool,
) -> CheckResult:
    usable = frame
    for col in [*needs, actual]:
        usable = usable.filter(pl.col(col).is_not_null())
    if usable.height == 0:
        return CheckResult(0, 0, "上游有 null，无可比对的行")
    usable = usable.with_columns(expected.alias("_expected"))
    if boolean:
        bad = usable.filter(pl.col(actual) != pl.col("_expected"))
    else:
        bad = usable.filter(
            (pl.col(actual) - pl.col("_expected")).abs() > _PRICE_TOL
        )
    return CheckResult(
        violations=bad.height, checked_rows=usable.height,
        detail=f"{bad.height} / {usable.height} 行与独立重算不符",
        sample=bad.head(5000) if bad.height else None,
    )


def _flags_rule(
    column: str, expected: pl.Expr, needs: Sequence[str], description: str,
    *, boolean: bool = False, lookback: bool = False,
    known: tuple[str, ...] = (),
) -> Check:
    def run(ctx: CheckContext) -> CheckResult:
        frame = ctx.query.daily_flags(ctx.start, ctx.end)
        if frame.height == 0:
            return CheckResult(0, 0, "区间内无 daily_flags")
        if lookback:
            frame = frame.sort(["symbol", "dt"]).with_columns(
                pl.col("is_limit_up").shift(1).over("symbol").alias("_prev_lu")
            )
        return _compare(frame, column, expected, needs, boolean=boolean)

    return register(Check(
        id=f"A2.recompute.{column}", category=Category.TRUTHFULNESS,
        scope=Scope.INCREMENTAL, metrics=(f"daily_flags.{column}",),
        known_issues=known, description=description, run=run,
    ))


_flags_rule(
    "limit_up",
    (pl.col("adj_prev_close") * (1.0 + pl.col("limit_pct"))).round(2),
    ["adj_prev_close", "limit_pct"],
    "涨停价 = round(除权参考价 × (1 + 当日涨跌幅上限), 2)",
)
_flags_rule(
    "limit_down",
    (pl.col("adj_prev_close") * (1.0 - pl.col("limit_pct"))).round(2),
    ["adj_prev_close", "limit_pct"],
    "跌停价 = round(除权参考价 × (1 − 当日涨跌幅上限), 2)",
)
_flags_rule(
    "is_broken_board",
    pl.col("touched_limit_up") & ~pl.col("is_limit_up"),
    ["touched_limit_up", "is_limit_up"],
    "炸板 = 触及涨停且未封住", boolean=True,
)
_flags_rule(
    "is_first_board",
    pl.col("is_limit_up") & ~pl.col("_prev_lu"),
    ["is_limit_up", "_prev_lu"],
    "首板 = 今日涨停且昨日未涨停；无昨日的行不参与",
    boolean=True, lookback=True,
)

# ---- valuation_snapshot 的透传列：与 tushare_daily 逐键相等 ----

def _passthrough(column: str, source_column: str) -> Check:
    def run(ctx: CheckContext) -> CheckResult:
        left = ctx.query.valuation(None, ctx.start, ctx.end)
        right = ctx.query.tushare_daily(
            None, ctx.start, ctx.end, columns=["symbol", "dt", source_column]
        )
        if left.height == 0 or right.height == 0:
            return CheckResult(0, 0, "两侧至少一侧无数据")
        joined = left.join(
            right.rename({source_column: "_expected"}), on=["symbol", "dt"],
            how="inner",
        )
        return _compare(joined, column, pl.col("_expected"),
                        ["_expected"], boolean=False)

    return register(Check(
        id=f"A2.recompute.{column}", category=Category.TRUTHFULNESS,
        scope=Scope.INCREMENTAL,
        metrics=(f"valuation_snapshot.{column}",), known_issues=(),
        description=f"valuation_snapshot.{column} 应逐键等于 "
                    f"tushare_daily.{source_column}",
        run=run,
    ))


_passthrough("close", "close")
_passthrough("market_cap", "market_cap")
_passthrough("circulating_market_cap", "circulating_market_cap")
_passthrough("turnover_ratio", "turnover_ratio")
```

**剩余待补**：`prev_close`、`adj_prev_close`、`limit_pct`、`is_limit_up`、
`is_limit_down`、`touched_limit_up`、`is_st`、`st_source` 八条。
`is_st` 与 `st_source` 要把四级名称解析独立重写一遍——这是本任务最重的
一条，也是最值得写的：D-10 那 867 个首板就丢在这条链路上。
每条都要配一条「造出错误行能报警」的测试。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_rules_recompute.py -v`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/catalog/rules_recompute.py \
        tests/data/catalog/test_rules_recompute.py
git commit -m "feat(catalog): A2 独立重算，测试守住「不调生产函数」"
```

---

## Task 14: B 类规则——完整性

**Files:**
- Create: `src/qsys/data/catalog/rules_complete.py`
- Test: `tests/data/catalog/test_rules_complete.py`

**Interfaces:**
- Consumes: Task 11 框架；`qsys.core.calendar.TradingCalendar`；
  `qsys.data.quality.daily_rules.find_holes`
- Produces: `CHECKS` 里注册 `B1.calendar`、`B2.holes`、`B3.null_rate`、
  `B4.arrival`、`B4.zero_limit_up`、`B5.span`

- [ ] **Step 1: 写失败的测试**

`tests/data/catalog/test_rules_complete.py`：

```python
"""B 类规则。null 率必须双口径——10% 的全窗口闸门挡不住 1.73% 的系统性缺口。"""

from datetime import date, datetime

import polars as pl
import pytest

from qsys.data import schema
from qsys.data.catalog import checks as C
from qsys.data.catalog import rules_complete  # noqa: F401
from qsys.data.query import WarehouseQuery

D = [date(2026, 9, d) for d in (10, 11)]
NOW = datetime(2026, 9, 15, 19, 30)


@pytest.fixture
def ctx(warehouse):
    return C.CheckContext(query=WarehouseQuery(warehouse), warehouse=warehouse,
                          start=D[0], end=D[-1])


def _run(cid, ctx):
    return C.run_checks(ctx, "t", NOW, only=[C.CHECKS[cid]]).row(0, named=True)


def _flags(rows, warehouse):
    frame = pl.DataFrame(rows)
    for name, dtype in [
        ("prev_close", pl.Float64), ("adj_prev_close", pl.Float64),
        ("limit_up", pl.Float64), ("limit_down", pl.Float64),
        ("limit_pct", pl.Float64), ("is_limit_up", pl.Boolean),
        ("is_limit_down", pl.Boolean), ("touched_limit_up", pl.Boolean),
        ("is_broken_board", pl.Boolean), ("is_first_board", pl.Boolean),
        ("is_st", pl.Boolean), ("st_source", pl.Utf8),
    ]:
        if name not in frame.columns:
            frame = frame.with_columns(pl.lit(None, dtype=dtype).alias(name))
    warehouse.write("daily_flags",
                    frame.select(schema.DAILY_FLAGS.names)
                    .to_arrow().cast(schema.DAILY_FLAGS), year=2026)


def test_null_rate_reports_both_by_row_and_by_symbol(ctx, warehouse):
    """D-10 的教训：按行数只有 1.73%，按标的却是 96 只全军覆没。
    只看行数口径会把系统性缺口当成噪声放过。"""
    rows = [{"dt": D[0], "symbol": f"{i:06d}.SZ", "is_st": True} for i in range(97)]
    rows += [{"dt": D[0], "symbol": "000999.SZ", "is_st": None}] * 3
    _flags(rows, warehouse)
    row = _run("B3.null_rate", ctx)
    assert "按行" in row["detail"]
    assert "按标的" in row["detail"]


def test_zero_limit_up_in_a_whole_market_day_is_a_failure(ctx, warehouse):
    """全市场一天零涨停在 A 股几乎不可能发生。
    D-12 原话：不要因为「涨跌停列表为空是常态」就放过。"""
    warehouse.write("limit_list_daily",
                    pl.from_arrow(schema.LIMIT_LIST_DAILY.empty_table())
                    .to_arrow().cast(schema.LIMIT_LIST_DAILY), year=2026)
    _flags([{"dt": D[0], "symbol": "600036.SH", "is_limit_up": False}], warehouse)
    row = _run("B4.zero_limit_up", ctx)
    assert row["passed"] is False
    assert "零涨停" in row["detail"]


def test_calendar_check_flags_a_missing_session(ctx, warehouse):
    _flags([{"dt": D[0], "symbol": "600036.SH"}], warehouse)
    row = _run("B1.calendar", ctx)
    assert row["checked_rows"] > 0


def test_all_rules_here_are_completeness_category():
    for cid, chk in C.CHECKS.items():
        if cid.startswith("B"):
            assert chk.category is C.Category.COMPLETENESS
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_rules_complete.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 写实现**

`src/qsys/data/catalog/rules_complete.py`。核心是 B3 的双口径：

```python
"""B 类规则：完整性。

B3 的双口径是本模块的要点。`_assert_name_coverage` 的 10% 全窗口行数闸门
按设计工作，但 D-10 那次退化只有 1.73%，落在阈值之下所以是静默的，
静默丢掉了 867 个首板。按**标的**判覆盖率才看得见这种系统性缺口。
"""

from __future__ import annotations

import polars as pl

from qsys.data.catalog.checks import (
    Category,
    Check,
    CheckContext,
    CheckResult,
    Scope,
    register,
)

# 两个口径各自的阈值。按标的的更严：系统性缺口总是集中在一批标的上。
_ROW_NULL_MAX = 0.02
_SYMBOL_NULL_MAX = 0.005


def _null_rate(ctx: CheckContext) -> CheckResult:
    frame = ctx.query.daily_flags(ctx.start, ctx.end)
    if frame.height == 0:
        return CheckResult(0, 0, "区间内无 daily_flags")

    null_rows = frame.filter(pl.col("is_st").is_null())
    by_row = null_rows.height / frame.height
    total_symbols = frame["symbol"].n_unique()
    null_symbols = null_rows["symbol"].n_unique()
    by_symbol = null_symbols / total_symbols if total_symbols else 0.0

    violations = 0
    if by_row > _ROW_NULL_MAX:
        violations += null_rows.height
    if by_symbol > _SYMBOL_NULL_MAX:
        violations = max(violations, null_symbols)

    return CheckResult(
        violations=violations, checked_rows=frame.height,
        detail=(f"is_st 空值：按行 {by_row:.2%}（阈值 {_ROW_NULL_MAX:.0%}）；"
                f"按标的 {null_symbols}/{total_symbols} = {by_symbol:.2%}"
                f"（阈值 {_SYMBOL_NULL_MAX:.1%}）"),
        sample=null_rows.head(5000) if null_rows.height else None,
    )


register(Check(
    id="B3.null_rate", category=Category.COMPLETENESS, scope=Scope.INCREMENTAL,
    metrics=("daily_flags.is_st", "daily_flags.symbol",
             "tushare_daily.close", "tushare_daily.open",
             "tushare_daily.high", "tushare_daily.low",
             "tushare_daily.volume", "tushare_daily.amount",
             "tushare_daily.pre_close", "valuation_snapshot.close",
             "valuation_snapshot.symbol"),
    known_issues=("D-10",),
    description="空值率双口径：按行数 与 按标的",
    run=_null_rate,
))


def _zero_limit_up(ctx: CheckContext) -> CheckResult:
    """全市场一天零涨停几乎不可能发生，值得单独一条规则。

    D-12 实测：17:02 那次同步取到 limit_list_daily 0 行，19:00 取到 55 行。
    「列表为空是常态」这个念头正是要防的。
    """
    listed = ctx.query.limit_list(None, ctx.start, ctx.end)
    flags = ctx.query.daily_flags(ctx.start, ctx.end)
    if flags.height == 0:
        return CheckResult(0, 0, "区间内无交易日数据")

    sessions = set(flags["dt"].unique().to_list())
    have = set(listed.filter(pl.col("limit_type") == "U")["dt"].unique().to_list())
    empty = sorted(sessions - have)
    return CheckResult(
        violations=len(empty), checked_rows=len(sessions),
        detail=(f"{len(empty)} / {len(sessions)} 个交易日的 limit_list 零涨停"
                + (f"：{empty[:10]}" if empty else "")),
        sample=pl.DataFrame({"dt": empty}) if empty else None,
    )


register(Check(
    id="B4.zero_limit_up", category=Category.COMPLETENESS,
    scope=Scope.INCREMENTAL,
    metrics=("limit_list_daily.dt", "limit_list_daily.limit_type"),
    known_issues=("D-12",),
    description="全市场零涨停日——几乎不可能发生，多半是源没到货",
    run=_zero_limit_up,
))
```

**剩余待补**：`B1.calendar`（各日频表的 `dt` 集合 vs `AShareCalendar`）、
`B2.holes`（包一层 `quality/daily_rules.py` 的 `find_holes`，`scope=FULL`）、
`B4.arrival`（对齐 `ingest_log`：当日该到的表有没有到）、
`B5.span`（每个指标的首末日期与预期区间对比）。
每条都要配一条能报警的测试。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_rules_complete.py -v`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/catalog/rules_complete.py \
        tests/data/catalog/test_rules_complete.py
git commit -m "feat(catalog): B 类完整性规则，空值率改双口径"
```

---

## Task 15: C 类规则——有效性（含红线扫描）

C1 是本项目的红线检查，也是唯一一条**不读 warehouse 只读源码**的规则。

**Files:**
- Create: `src/qsys/data/catalog/rules_valid.py`
- Test: `tests/data/catalog/test_rules_valid.py`

**Interfaces:**
- Consumes: Task 11 框架；Task 2 的 `SOURCES`
- Produces: `CHECKS` 里注册 `C1.no_fill`、`C2.pit_join`、`C3.span_gaps`、
  `C4.frozen_unchanged`、`C5.credential_expiry`；
  以及 `FILL_EXEMPTIONS: dict[tuple[str, str], str]`（豁免登记表）

- [ ] **Step 1: 写失败的测试**

`tests/data/catalog/test_rules_valid.py`：

```python
"""C 类规则。C1 守的是整个项目的硬约束：拿不到就记 null，不准填。"""

from datetime import date, datetime

import polars as pl
import pytest

from qsys.data import schema
from qsys.data.catalog import checks as C
from qsys.data.catalog import rules_valid
from qsys.data.query import WarehouseQuery

D = [date(2026, 9, d) for d in (10, 11)]
NOW = datetime(2026, 9, 15, 19, 30)


@pytest.fixture
def ctx(warehouse):
    return C.CheckContext(query=WarehouseQuery(warehouse), warehouse=warehouse,
                          start=D[0], end=D[-1])


def _run(cid, ctx):
    return C.run_checks(ctx, "t", NOW, only=[C.CHECKS[cid]]).row(0, named=True)


def test_fill_scan_finds_an_unregistered_default(tmp_path, ctx):
    """未登记的填充就是缺陷，不管它看起来多无害。"""
    bad = tmp_path / "leaky.py"
    bad.write_text("import polars as pl\nx = pl.col('a').fill_null(1.0)\n")
    found = rules_valid.scan_fills([tmp_path])
    assert len(found) == 1
    assert found[0].line_no == 2


def test_fill_scan_accepts_a_registered_exemption(tmp_path):
    """豁免要说明「为什么这个默认值是真值而非填充」。"""
    src = "x = frame.filter(~pl.col('is_st').fill_null(False))\n"
    bad = tmp_path / "ok.py"
    bad.write_text(src)
    exempt = {(str(bad), src.strip()): "保守判定：is_st 为 null 时按非 ST 处理，"
                                        "宁可多报一条让人看，也不静默放过"}
    found = rules_valid.scan_fills([tmp_path], exemptions=exempt)
    assert found == []


def test_fill_scan_keys_on_the_source_line_not_the_line_number(tmp_path):
    """行号会随上方插入而漂移，豁免必须钉在代码文本上。"""
    src = "y = pl.col('b').fill_null(0)\n"
    f = tmp_path / "shift.py"
    f.write_text("# 新加的注释\n# 又一行\n" + src)
    exempt = {(str(f), src.strip()): "理由"}
    assert rules_valid.scan_fills([tmp_path], exemptions=exempt) == []


def test_feed_fill_null_is_currently_a_violation(ctx):
    """D-9：feed.py:85 的 fill_null(1.0)。新因子语义下同窗口可达
    2.40–3.48，填 1.0 等于把价格打到几分之一。

    **Task 19 修掉之后本条测试要跟着改成断言零违规。**
    """
    row = _run("C1.no_fill", ctx)
    assert row["violations"] >= 1
    assert row["severity"] == "warning", "已登记 D-9，降级但不放过"


def test_pit_join_catches_a_snapshot_used_off_its_observation_day(ctx, warehouse):
    """name_snapshots 是点观测。用在观测日之外就是结转。"""
    warehouse.write("name_snapshots", pl.DataFrame({
        "dt": [D[0]], "symbol": ["600036.SH"], "name": ["招商银行"],
    }).to_arrow().cast(schema.NAME_SNAPSHOTS), year=2026)
    flags = pl.DataFrame({
        "dt": [D[1]], "symbol": ["600036.SH"], "st_source": ["name_snapshots"],
    })
    for name, dtype in [
        ("prev_close", pl.Float64), ("adj_prev_close", pl.Float64),
        ("limit_up", pl.Float64), ("limit_down", pl.Float64),
        ("limit_pct", pl.Float64), ("is_limit_up", pl.Boolean),
        ("is_limit_down", pl.Boolean), ("touched_limit_up", pl.Boolean),
        ("is_broken_board", pl.Boolean), ("is_first_board", pl.Boolean),
        ("is_st", pl.Boolean),
    ]:
        flags = flags.with_columns(pl.lit(None, dtype=dtype).alias(name))
    warehouse.write("daily_flags", flags.select(schema.DAILY_FLAGS.names)
                    .to_arrow().cast(schema.DAILY_FLAGS), year=2026)

    row = _run("C2.pit_join", ctx)
    assert row["violations"] == 1, "D[1] 那天没有快照，却声称来源是快照"


def test_span_gaps_finds_an_interval_without_a_successor(ctx, warehouse):
    """D-10 的形态：区间停在 2006-10-08、end_date 非 null 且无后继。"""
    warehouse.write("name_history", pl.DataFrame({
        "symbol": ["000006.SZ"], "name": ["深振业A"],
        "start_date": [date(2006, 1, 1)], "end_date": [date(2006, 10, 8)],
        "ann_date": [date(2006, 1, 1)], "change_reason": ["股改"],
    }).to_arrow().cast(schema.NAME_HISTORY))
    row = _run("C3.span_gaps", ctx)
    assert row["violations"] == 1
    assert row["known_issue"] == "D-10"


def test_credential_expiry_warns_before_the_points_lapse(ctx):
    """积分一年有效、到期回收，到期未续费则日线源直接中断。"""
    row = _run("C5.credential_expiry", ctx)
    assert row["checked_rows"] > 0


def test_all_rules_here_are_validity_category():
    for cid, chk in C.CHECKS.items():
        if cid.startswith("C"):
            assert chk.category is C.Category.VALIDITY
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_rules_valid.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 写实现**

`src/qsys/data/catalog/rules_valid.py`：

```python
"""C 类规则：有效性——这个值在什么范围内成立。

C1 是本项目的红线：**拿不到的数据记 null，不准填**。它不读 warehouse，
只扫源码——因为填充是写在代码里的，不是数据里的。

豁免钉在**源码行文本**上而不是行号上：行号会随上方插入而漂移，
钉行号的豁免表几个月后就全是错的。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from qsys.data.catalog.checks import (
    Category,
    Check,
    CheckContext,
    CheckResult,
    Scope,
    register,
)
from qsys.data.catalog.sources import SOURCES

# 扫这些目录——数据链路与因子链路。web / ops 不在红线范围内。
_SCAN_ROOTS = [Path("src/qsys/data"), Path("src/qsys/strategy")]

_FILL_PATTERNS = re.compile(
    r"\.fill_null\(|\.fillna\(|\bor\s+0\b|\.get\([^)]+,\s*0[.\d]*\)"
)


@dataclass(frozen=True, slots=True)
class FillSite:
    path: str
    line_no: int
    source: str


# 已登记的豁免。键是 (文件路径, 源码行去空白)，值必须说明
# **为什么这个默认值是真值而不是填充**。写「暂时这样」不算理由。
FILL_EXEMPTIONS: dict[tuple[str, str], str] = {
    # A3.limit_list_vs_selfcalc 判「非 ST」时的保守取值：
    # is_st 为 null 的也算进差集，宁可多报一条让人去看，不静默放过。
    (
        "src/qsys/data/catalog/rules_truth.py",
        "unexplained = only_mine.filter(~pl.col(\"is_st\").fill_null(False))",
    ): "保守判定，多报不漏报；null 在这里不会产出一个假的『已解释』结论",
}


def scan_fills(
    roots: list[Path] | None = None,
    exemptions: dict[tuple[str, str], str] | None = None,
) -> list[FillSite]:
    """扫出未登记的填充点。"""
    roots = roots if roots is not None else _SCAN_ROOTS
    exempt = FILL_EXEMPTIONS if exemptions is None else exemptions
    out: list[FillSite] = []
    for root in roots:
        for path in sorted(Path(root).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for i, line in enumerate(path.read_text().splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith("#") or not _FILL_PATTERNS.search(line):
                    continue
                if (str(path), stripped) in exempt:
                    continue
                out.append(FillSite(str(path), i, stripped))
    return out


def _no_fill(ctx: CheckContext) -> CheckResult:
    sites = scan_fills()
    total = sum(
        1
        for root in _SCAN_ROOTS
        for p in Path(root).rglob("*.py")
        if "__pycache__" not in p.parts
    )
    return CheckResult(
        violations=len(sites), checked_rows=total,
        detail=(f"扫描 {total} 个文件，发现 {len(sites)} 处未登记的填充"
                + (f"：{sites[0].path}:{sites[0].line_no}" if sites else "")),
        sample=pl.DataFrame([vars(s) for s in sites]) if sites else None,
    )


register(Check(
    id="C1.no_fill", category=Category.VALIDITY, scope=Scope.INCREMENTAL,
    metrics=("adj_factors.cum_factor", "market_regime.index_ma20_slope_5d",
             "daily_flags.prev_close", "strategy.stock_return_10d",
             "strategy.distance_to_60d_high"),
    known_issues=("D-9",),
    description="红线：数据与因子链路上不得有未登记的默认值填充",
    run=_no_fill,
))


def _pit_join(ctx: CheckContext) -> CheckResult:
    """点观测只对观测日成立。

    daily_flags 里声称来源是 name_snapshots 的行，其 (symbol, dt) 必须在
    name_snapshots 里真实存在——否则就是把快照蔓延到了别的日子。
    """
    flags = ctx.query.daily_flags(ctx.start, ctx.end)
    if flags.height == 0:
        return CheckResult(0, 0, "区间内无 daily_flags")
    claimed = flags.filter(pl.col("st_source") == "name_snapshots").select(
        ["symbol", "dt"]
    )
    if claimed.height == 0:
        return CheckResult(0, 0, "区间内没有声称来自快照的行")
    snaps = ctx.query.name_snapshots(None, ctx.start, ctx.end).select(["symbol", "dt"])
    bad = claimed.join(snaps, on=["symbol", "dt"], how="anti")
    return CheckResult(
        violations=bad.height, checked_rows=claimed.height,
        detail=f"{bad.height} / {claimed.height} 行声称来自快照但当日无快照",
        sample=bad if bad.height else None,
    )


register(Check(
    id="C2.pit_join", category=Category.VALIDITY, scope=Scope.INCREMENTAL,
    metrics=("name_snapshots.name", "name_snapshots.dt",
             "daily_flags.st_source"),
    known_issues=(),
    description="点观测按 (symbol, dt) 精确连接，不得向前后蔓延",
    run=_pit_join,
))


def _span_gaps(ctx: CheckContext) -> CheckResult:
    """区间表的覆盖空洞与无后继区间。

    D-10 的形态：大量标的最后一个区间停在 2006-10-08、end_date 非 null
    且无后继，此后全部无区间覆盖。
    """
    hist = ctx.query.name_history(None)
    if hist.height == 0:
        return CheckResult(0, 0, "name_history 为空")
    last = (
        hist.sort(["symbol", "start_date"])
        .group_by("symbol")
        .agg(pl.col("end_date").last().alias("last_end"))
    )
    dangling = last.filter(
        pl.col("last_end").is_not_null() & (pl.col("last_end") < ctx.end)
    )
    return CheckResult(
        violations=dangling.height, checked_rows=last.height,
        detail=(f"{dangling.height} / {last.height} 只标的的最后一个名称区间"
                f"在 {ctx.end} 之前就结束且无后继"),
        sample=dangling if dangling.height else None,
    )


register(Check(
    id="C3.span_gaps", category=Category.VALIDITY, scope=Scope.FULL,
    metrics=("name_history.name", "name_history.start_date",
             "name_history.end_date"),
    known_issues=("D-10",),
    description="区间表的空洞与无后继区间",
    run=_span_gaps,
))


_EXPIRY_WARN = timedelta(days=60)


def _credential_expiry(ctx: CheckContext) -> CheckResult:
    """凭据到期。积分一年有效、到期回收，到期未续费则日线源直接中断。"""
    dated = [s for s in SOURCES.values() if s.expires is not None]
    soon = [s for s in dated if s.expires - ctx.end <= _EXPIRY_WARN]
    return CheckResult(
        violations=len(soon), checked_rows=len(dated),
        detail=(f"{len(soon)} / {len(dated)} 个有期限的源将在 "
                f"{_EXPIRY_WARN.days} 天内到期"
                + (f"：{sorted(s.id for s in soon)}" if soon else "")),
        sample=pl.DataFrame(
            {"source": [s.id for s in soon],
             "expires": [s.expires for s in soon]}
        ) if soon else None,
    )


register(Check(
    id="C5.credential_expiry", category=Category.VALIDITY,
    scope=Scope.INCREMENTAL,
    metrics=("tushare_daily.close", "tushare_daily.factor"),
    known_issues=(),
    description="源凭据到期提醒",
    run=_credential_expiry,
))
```

**剩余待补**：`C4.frozen_unchanged`——`paid_daily` / `bars_daily` 的行数与
内容指纹不变。基线**首次运行时写入** `artifacts/frozen_baseline.json`，
此后逐次比对；不要在代码里硬编一个凭空的行数。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_rules_valid.py -v`
Expected: 全绿

- [ ] **Step 5: 跑全部字典守护，确认引用完整性转绿**

Run: `.venv/bin/python -m pytest tests/data/catalog/ -v`
Expected: 全绿，**包括** Task 9 的
`test_every_referenced_check_id_will_be_implemented`。

若它仍红，报的就是字典引用了但三个 rules 模块都没实现的规则 id。
两条路：把规则补上，或把该指标的 `checks` 改成 `UNCOVERED("理由")`。
**不要把 id 从字典里悄悄删掉**——那样页面上就看不出这条指标没人管。

- [ ] **Step 6: 提交**

```bash
git add src/qsys/data/catalog/rules_valid.py tests/data/catalog/test_rules_valid.py
git commit -m "feat(catalog): C 类有效性规则，含填充红线扫描与 PIT 连接检查"
```

---

## Task 16: A4 抽样清单与 CLI

把「你在同花顺上验过的东西」变成永久证据，并给整套验证器一个入口。

**Files:**
- Create: `scripts/verify_metrics.py`
- Modify: `src/qsys/data/catalog/rules_truth.py`（加 A4 清单导出）
- Test: `tests/data/catalog/test_sampling_cli.py`

**Interfaces:**
- Consumes: Task 11-15 的全部规则
- Produces:
  - `rules_truth.sampling_worklist(ctx, limit=200) -> pl.DataFrame`
    列：`metric_id, symbol, dt, our_value, why, priority`
  - `scripts/verify_metrics.py` 子命令：`run`、`worklist`、`record`

- [ ] **Step 1: 写失败的测试**

`tests/data/catalog/test_sampling_cli.py`：

```python
"""A4 抽样闭环：系统出清单 → 人去同花顺验 → 结论永久入库。"""

from datetime import date, datetime

import polars as pl
import pytest

from qsys.data import schema
from qsys.data.catalog import checks as C
from qsys.data.catalog import rules_truth
from qsys.data.query import WarehouseQuery

D = [date(2026, 9, d) for d in (10, 11)]
NOW = datetime(2026, 9, 15, 19, 30)


@pytest.fixture
def ctx(warehouse):
    return C.CheckContext(query=WarehouseQuery(warehouse), warehouse=warehouse,
                          start=D[0], end=D[-1])


def _daily(rows, warehouse):
    frame = pl.DataFrame(rows)
    missing = [n for n in schema.TUSHARE_DAILY.names if n not in frame.columns]
    frame = frame.with_columns(
        [pl.lit(None, dtype=pl.Utf8).alias(n) for n in missing if n == "name"]
        + [pl.lit(None, dtype=pl.Float64).alias(n) for n in missing if n != "name"]
    ).select(schema.TUSHARE_DAILY.names)
    warehouse.write("tushare_daily",
                    frame.to_arrow().cast(schema.TUSHARE_DAILY), year=2026)


def test_worklist_is_sorted_by_suspicion_not_by_symbol(ctx, warehouse):
    """先验最可疑的。按代码排序等于让人从 000001 开始一路验下去。"""
    _daily([
        {"symbol": "000430.SZ", "dt": D[0], "close": 10.0,
         "pre_close": 10.0, "factor": 2.0},
        {"symbol": "000430.SZ", "dt": D[1], "close": 8.0,
         "pre_close": 8.0, "factor": 2.0},
    ], warehouse)
    work = rules_truth.sampling_worklist(ctx)
    assert work.height >= 1
    assert work["priority"].to_list() == sorted(
        work["priority"].to_list(), reverse=True
    )
    assert set(["metric_id", "symbol", "dt", "our_value", "why"]) <= set(work.columns)


def test_worklist_says_why_each_row_is_suspect(ctx, warehouse):
    """拿着清单去同花顺的人要知道该看什么，不能只给一个代码。"""
    _daily([
        {"symbol": "000430.SZ", "dt": D[0], "close": 10.0,
         "pre_close": 10.0, "factor": 2.0},
        {"symbol": "000430.SZ", "dt": D[1], "close": 8.0,
         "pre_close": 8.0, "factor": 2.0},
    ], warehouse)
    assert all(rules_truth.sampling_worklist(ctx)["why"].to_list())


def test_recorded_evidence_survives_a_later_verification_run(warehouse):
    """人工结论是永久证据，绝不能被自动流程覆盖。"""
    from scripts.verify_metrics import record_evidence

    record_evidence(
        warehouse, metric_id="adj_factors.cum_factor", symbol="001331.SZ",
        dt=date(2026, 6, 30), our_value="3.4848", reference_value="2.4011",
        reference_source="同花顺", verdict="mismatch", checked_by="wanlinruo",
        note="D-1 抽查",
    )
    C.write_results(
        warehouse,
        C.run_checks(
            C.CheckContext(WarehouseQuery(warehouse), warehouse, D[0], D[1]),
            "r1", NOW,
            only=[C.Check(id="X", category=C.Category.TRUTHFULNESS,
                          scope=C.Scope.INCREMENTAL,
                          metrics=("adj_factors.cum_factor",),
                          known_issues=(), description="",
                          run=lambda c: C.CheckResult(0, 1))],
        ),
    )
    ev = WarehouseQuery(warehouse).metric_evidence("adj_factors.cum_factor")
    assert ev.height == 1
    assert ev["verdict"][0] == "mismatch"


def test_recording_twice_appends_rather_than_replaces(warehouse):
    """同一只股可能被验多次（不同日期、不同人），全部留着。"""
    from scripts.verify_metrics import record_evidence

    for src in ("同花顺", "东财"):
        record_evidence(
            warehouse, metric_id="adj_factors.cum_factor", symbol="001331.SZ",
            dt=date(2026, 6, 30), our_value="3.4848", reference_value="2.4011",
            reference_source=src, verdict="mismatch", checked_by="wanlinruo",
            note="",
        )
    assert WarehouseQuery(warehouse).metric_evidence().height == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_sampling_cli.py -v`
Expected: FAIL，`AttributeError: module ... has no attribute 'sampling_worklist'`

- [ ] **Step 3: 加抽样清单**

在 `src/qsys/data/catalog/rules_truth.py` 末尾加：

```python
def sampling_worklist(ctx: CheckContext, limit: int = 200) -> pl.DataFrame:
    """A4：按可疑度排序的待人工验真清单。

    导出给人拿去同花顺逐条比对。**按可疑度排序不按代码排序**——
    按代码排等于让人从 000001 开始一路验下去，验不到重点。

    `why` 必须写明该看什么：拿着一串代码去查是查不出结论的。
    """
    result = _missed_exdiv(ctx)
    if result.sample is None or result.sample.height == 0:
        return pl.DataFrame(schema={
            "metric_id": pl.Utf8, "symbol": pl.Utf8, "dt": pl.Date,
            "our_value": pl.Utf8, "why": pl.Utf8, "priority": pl.Float64,
        })
    return (
        result.sample.with_columns([
            pl.lit("tushare_daily.factor").alias("metric_id"),
            pl.col("factor").cast(pl.Utf8).alias("our_value"),
            (pl.lit("价格缺口 ") + (pl.col("gap") * 100).round(2).cast(pl.Utf8)
             + pl.lit("% 而 factor 未跳变；去同花顺核对该日是否除权除息"))
            .alias("why"),
            pl.col("gap").alias("priority"),  # 缺口越大越可疑
        ])
        .select(["metric_id", "symbol", "dt", "our_value", "why", "priority"])
        .sort("priority", descending=True)
        .head(limit)
    )
```

- [ ] **Step 4: 写 CLI**

`scripts/verify_metrics.py`：

```python
#!/usr/bin/env python
"""指标验证器入口。

三个子命令：
  run       跑规则并落盘（--scope incremental|full）
  worklist  导出待人工验真清单（CSV，拿去同花顺逐条比对）
  record    录入一条人工结论（永久证据，不被自动流程覆盖）

**全量必须逐年跑**：全历史两表同载约 8.2 GiB 而生产节点只有 7.5 GiB，
2026-09-13 实测被 OOM killer 杀过。`run --scope full` 内部按年切片。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.catalog import checks as C
from qsys.data.catalog import rules_complete  # noqa: F401  注册副作用
from qsys.data.catalog import rules_recompute  # noqa: F401
from qsys.data.catalog import rules_truth
from qsys.data.catalog import rules_valid  # noqa: F401
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)


def record_evidence(
    warehouse: Warehouse, *, metric_id: str, symbol: str, dt: date,
    our_value: str, reference_value: str, reference_source: str,
    verdict: str, checked_by: str, note: str = "",
) -> int:
    """追加一条人工结论。**只增不改**——一次录入永久生效。"""
    row = pl.DataFrame([{
        "metric_id": metric_id, "symbol": symbol, "dt": dt,
        "our_value": our_value, "reference_value": reference_value,
        "reference_source": reference_source, "verdict": verdict,
        "checked_by": checked_by, "checked_at": datetime.now(), "note": note,
    }])
    query = WarehouseQuery(warehouse)
    existing = query.metric_evidence().filter(pl.col("dt").dt.year() == dt.year)
    merged = pl.concat([existing, row], how="vertical")
    warehouse.write(
        "metric_evidence",
        merged.select(schema.METRIC_EVIDENCE.names)
              .to_arrow().cast(schema.METRIC_EVIDENCE),
        year=dt.year,
    )
    return merged.height


def _run(args) -> int:
    warehouse = Warehouse(Path(args.warehouse))
    scope = C.Scope(args.scope)
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    total = 0

    years = range(args.start.year, args.end.year + 1)
    spans = (
        [(args.start, args.end)] if scope is C.Scope.INCREMENTAL
        # 逐年切片：全历史同时载入会被 OOM killer 杀掉
        else [(max(args.start, date(y, 1, 1)), min(args.end, date(y, 12, 31)))
              for y in years]
    )

    for lo, hi in spans:
        ctx = C.CheckContext(WarehouseQuery(warehouse), warehouse, lo, hi)
        frame = C.run_checks(ctx, run_id, datetime.now(), scope=scope,
                             artifacts=Path(args.artifacts))
        total += C.write_results(warehouse, frame)
        failed = frame.filter(~pl.col("passed"))
        blocking = failed.filter(pl.col("severity") == "blocking")
        log.info("verify.span_done", start=str(lo), end=str(hi),
                 rows=frame.height, failed=failed.height,
                 blocking=blocking.height)

    log.info("verify.done", run_id=run_id, rows=total)
    return 0


def _worklist(args) -> int:
    warehouse = Warehouse(Path(args.warehouse))
    ctx = C.CheckContext(WarehouseQuery(warehouse), warehouse,
                         args.start, args.end)
    work = rules_truth.sampling_worklist(ctx, limit=args.limit)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    work.write_csv(out)
    print(f"{work.height} 条待验清单 -> {out}")
    return 0


def _record(args) -> int:
    n = record_evidence(
        Warehouse(Path(args.warehouse)), metric_id=args.metric,
        symbol=args.symbol, dt=args.dt, our_value=args.ours,
        reference_value=args.theirs, reference_source=args.source,
        verdict=args.verdict, checked_by=args.by, note=args.note,
    )
    print(f"已录入，该年累计 {n} 条证据")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="指标验证器")
    p.add_argument("--warehouse", default="warehouse")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="跑规则并落盘")
    r.add_argument("--scope", choices=["incremental", "full"],
                   default="incremental")
    r.add_argument("--start", type=date.fromisoformat, required=True)
    r.add_argument("--end", type=date.fromisoformat, required=True)
    r.add_argument("--artifacts", default="artifacts/metric_checks")
    r.set_defaults(func=_run)

    w = sub.add_parser("worklist", help="导出待人工验真清单")
    w.add_argument("--start", type=date.fromisoformat, required=True)
    w.add_argument("--end", type=date.fromisoformat, required=True)
    w.add_argument("--limit", type=int, default=200)
    w.add_argument("--out", default="artifacts/sampling_worklist.csv")
    w.set_defaults(func=_worklist)

    e = sub.add_parser("record", help="录入一条人工结论")
    for flag, required in [("--metric", True), ("--symbol", True),
                           ("--ours", True), ("--theirs", True),
                           ("--source", True), ("--verdict", True),
                           ("--by", True), ("--note", False)]:
        e.add_argument(flag, required=required, default="")
    e.add_argument("--dt", type=date.fromisoformat, required=True)
    e.set_defaults(func=_record)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_sampling_cli.py -v`
Expected: 全绿

- [ ] **Step 6: 在生产数据上实跑一次增量**

```bash
.venv/bin/python scripts/verify_metrics.py run \
  --warehouse warehouse --scope incremental \
  --start 2026-09-11 --end 2026-09-11
```

记下输出的 `failed` 与 `blocking` 数量。**blocking 非零是预期的**——
C1 会扫出 D-9 那处填充（已登记故降级为 warning），但其他未登记的新失败
会是 blocking。把它们逐条看一遍：是真问题就记进
`deferred-corrections.md`，是规则写错就修规则。

- [ ] **Step 7: 提交**

```bash
git add scripts/verify_metrics.py src/qsys/data/catalog/rules_truth.py \
        tests/data/catalog/test_sampling_cli.py
git commit -m "feat(catalog): 验证器 CLI 与 A4 抽样闭环，人工结论永久入库"
```

---

## Task 17: CatalogService 与总览页

**Files:**
- Create: `src/qsys/web/catalog_views.py`
- Create: `src/qsys/web/templates/catalog_overview.html`
- Modify: `src/qsys/web/templates/base.html`（nav 加「数据层」+ 两个灰 pill 样式）
- Modify: `src/qsys/web/app.py`（加 `/data/catalog` 路由）
- Modify: `scripts/run_monitor.py`（接线）
- Test: `tests/web/test_catalog_views.py`、`tests/web/test_catalog_pages.py`

**Interfaces:**
- Consumes: Task 3 的 `REGISTRY` / `root_sources` / `consumers`，
  Task 10 的 `latest_metric_checks()`
- Produces:
  - `MetricRow(id, dataset, column, kind, validity, meaning, source_label, status, issues)`
  - `CatalogSummary(total, by_kind, covered, pending, latest_run)`
  - `CatalogService(warehouse)`：`summary()`、`rows(dataset=None, kind=None, status=None)`、
    `datasets()`、`kinds()`、`statuses()`
  - `Status` 六个取值：`OK` / `FAILED` / `KNOWN_ISSUE` / `UNCOVERED` /
    `UNVERIFIED` / `UNKNOWN_MEANING`

**说明**：spec §8.3 列了五个 pill，加上 §8.7 要求的「验证从未跑过时全部标
未验证」，实际是**六个**状态。`UNVERIFIED`（挂了规则但这轮没跑成）与
`UNCOVERED`（压根没挂规则）必须分开——前者是动态的，后者是静态的。

- [ ] **Step 1: 写失败的测试**

`tests/web/test_catalog_views.py`：

```python
"""数据层视图模型。页面的全部判断逻辑都在这里，模板只负责摆。"""

from datetime import date, datetime

import polars as pl
import pytest

from qsys.data import schema
from qsys.data.storage import Warehouse
from qsys.web.catalog_views import CatalogService, Status

D = date(2026, 9, 15)
T = datetime(2026, 9, 15, 19, 30)


def _check_rows(warehouse: Warehouse, rows: list[dict]) -> None:
    base = dict(
        run_id="r1", checked_at=T, category="truthfulness",
        scope_start=D, scope_end=D, severity="blocking", known_issue=None,
        violations=0, checked_rows=100, detail="", sample_path=None,
    )
    frame = pl.DataFrame([{**base, **r} for r in rows])
    warehouse.write("metric_checks",
                    frame.select(schema.METRIC_CHECKS.names)
                    .to_arrow().cast(schema.METRIC_CHECKS), year=2026)


def test_opens_on_an_empty_warehouse_with_everything_unverified(warehouse):
    """验证从没跑过是常态。字典本身就有价值，不能因结果表空而打不开。"""
    svc = CatalogService(warehouse)
    rows = svc.rows()
    assert len(rows) > 200
    assert svc.summary().latest_run is None
    assert all(
        r.status in (Status.UNVERIFIED, Status.UNCOVERED, Status.UNKNOWN_MEANING)
        for r in rows
    )


def test_a_passing_check_marks_the_metric_ok(warehouse):
    _check_rows(warehouse, [{
        "metric_id": "tushare_daily.amount",
        "check_id": "A1.dimension.amount_vs_volume_close", "passed": True,
        "severity": "warning",
    }])
    row = next(r for r in CatalogService(warehouse).rows()
               if r.id == "tushare_daily.amount")
    assert row.status is Status.OK


def test_a_blocking_failure_beats_a_passing_sibling_check(warehouse):
    """一条指标挂多条规则时，最坏的那条决定它的状态。"""
    _check_rows(warehouse, [
        {"metric_id": "tushare_daily.amount", "check_id": "A1.x",
         "passed": True, "severity": "warning"},
        {"metric_id": "tushare_daily.amount", "check_id": "B3.null_rate",
         "passed": False, "severity": "blocking", "violations": 3},
    ])
    row = next(r for r in CatalogService(warehouse).rows()
               if r.id == "tushare_daily.amount")
    assert row.status is Status.FAILED


def test_a_downgraded_failure_shows_as_a_known_issue(warehouse):
    _check_rows(warehouse, [{
        "metric_id": "adj_factors.cum_factor", "check_id": "A3.missed_exdiv",
        "passed": False, "severity": "warning", "known_issue": "D-1",
        "violations": 12,
    }])
    row = next(r for r in CatalogService(warehouse).rows()
               if r.id == "adj_factors.cum_factor")
    assert row.status is Status.KNOWN_ISSUE
    assert "D-1" in row.issues


def test_uncovered_metrics_are_first_class_not_hidden(warehouse):
    """全绿但悄悄漏掉几十个没人验过的字段的页面，比没有页面更糟。"""
    rows = CatalogService(warehouse).rows(status=Status.UNCOVERED)
    assert rows, "运行时类指标应有 UNCOVERED 条目"


def test_default_order_puts_problems_first_not_alphabetical(warehouse):
    """打开第一屏就是问题。按字母排等于把问题埋进第 7 页。"""
    _check_rows(warehouse, [
        {"metric_id": "tushare_daily.amount", "check_id": "A1.x",
         "passed": False, "severity": "blocking", "violations": 1},
        {"metric_id": "tushare_daily.close", "check_id": "A1.y",
         "passed": True, "severity": "warning"},
    ])
    rows = CatalogService(warehouse).rows()
    order = [r.status for r in rows]
    assert order[0] is Status.FAILED
    assert order.index(Status.FAILED) < order.index(Status.OK)


def test_source_label_reaches_interface_and_field_level(warehouse):
    """「来源＝Tushare」等于没说：Tushare 底下 10 个接口的到货时点、
    积分门槛、失效后果都不一样。"""
    row = next(r for r in CatalogService(warehouse).rows()
               if r.id == "tushare_daily.amount")
    assert "daily_basic" not in row.source_label
    assert "daily.amount" in row.source_label
    assert "1000" in row.source_label, "乘数要显示出来"


def test_derived_metrics_show_their_root_sources(warehouse):
    """派生指标的「来源」是根源集合，不是它的直接上游。"""
    row = next(r for r in CatalogService(warehouse).rows()
               if r.id == "daily_flags.is_first_board")
    assert "tushare.daily" in row.source_label


def test_summary_counts_pending_without_known_issues(warehouse):
    """待处理 = 失败 + 含义待查 + 未覆盖。不含已知缺陷——
    后者已挂账有归属，混进来这个数永远降不到零。"""
    s = CatalogService(warehouse).summary()
    assert s.pending == sum(
        1 for r in CatalogService(warehouse).rows()
        if r.status in (Status.FAILED, Status.UNKNOWN_MEANING, Status.UNCOVERED)
    )


def test_filters_compose(warehouse):
    svc = CatalogService(warehouse)
    rows = svc.rows(dataset="daily_flags")
    assert rows and all(r.dataset == "daily_flags" for r in rows)
```

`tests/web/test_catalog_pages.py`：

```python
"""数据层页面。只验它打得开、该出现的字眼在。"""

from fastapi.testclient import TestClient

from qsys.web.app import create_app
from qsys.web.catalog_views import CatalogService
from qsys.web.config import WebConfig
from qsys.web.views import MonitorService
from qsys.ops.journal import SessionJournal


def _client(warehouse):
    cfg = WebConfig()
    return TestClient(create_app(
        MonitorService(SessionJournal(warehouse), config=cfg),
        cfg, catalog=CatalogService(warehouse),
    ))


def test_overview_renders_on_an_empty_warehouse(warehouse):
    """监控页面最需要的性质是「出事的时候一定打得开」。"""
    r = _client(warehouse).get("/data/catalog")
    assert r.status_code == 200
    assert "指标总数" in r.text


def test_overview_links_into_the_detail_page(warehouse):
    r = _client(warehouse).get("/data/catalog")
    assert "/data/catalog/tushare_daily.close" in r.text


def test_nav_carries_the_new_tab(warehouse):
    r = _client(warehouse).get("/data/catalog")
    assert ">数据层<" in r.text


def test_filter_by_kind_narrows_the_table(warehouse):
    c = _client(warehouse)
    assert c.get("/data/catalog?kind=factor").status_code == 200
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/web/test_catalog_views.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'qsys.web.catalog_views'`

- [ ] **Step 3: 写 CatalogService**

`src/qsys/web/catalog_views.py`：

```python
"""数据层的视图模型。

与数据台同一条纪律：Web 层不持有业务逻辑。但这里比 DataService 多一层——
字典部分来自 **import registry**（代码），结果部分才来自 warehouse。
两种来源不混进同一个类，否则以后分不清哪个方法会碰磁盘。

**必须在验证从没跑过时照常工作**：metric_checks 为空是常态（新部署、
新指标、验证器还没接上调度），字典本身就有价值。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import polars as pl

from qsys.data.catalog import REGISTRY, SOURCES, root_sources
from qsys.data.catalog.types import UNKNOWN, Direct, Kind, Uncovered
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


class Status(StrEnum):
    FAILED = "failed"                  # 新的 blocking 失败
    UNKNOWN_MEANING = "unknown"        # 我们不知道这列是什么
    UNCOVERED = "uncovered"            # 压根没挂规则（静态）
    UNVERIFIED = "unverified"          # 挂了规则但没跑出结果（动态）
    KNOWN_ISSUE = "known_issue"        # 挂了账，已降级
    OK = "ok"


# 默认排序：打开第一屏就是问题。按字母排等于把问题埋进第 7 页。
_ORDER = {
    Status.FAILED: 0, Status.UNKNOWN_MEANING: 1, Status.UNCOVERED: 2,
    Status.UNVERIFIED: 3, Status.KNOWN_ISSUE: 4, Status.OK: 5,
}

_LABEL = {
    Status.FAILED: ("失败", "bad"),
    Status.UNKNOWN_MEANING: ("含义待查", "todo"),
    Status.UNCOVERED: ("未覆盖", "todo"),
    Status.UNVERIFIED: ("未验证", "todo"),
    Status.KNOWN_ISSUE: ("已知缺陷", "warn"),
    Status.OK: ("通过", "ok"),
}


@dataclass(frozen=True, slots=True)
class MetricRow:
    id: str
    dataset: str
    column: str
    kind: Kind
    validity: str
    meaning: str
    source_label: str
    status: Status
    issues: tuple[str, ...]

    @property
    def label(self) -> str:
        return _LABEL[self.status][0]

    @property
    def pill(self) -> str:
        return _LABEL[self.status][1]


@dataclass(frozen=True, slots=True)
class CatalogSummary:
    total: int
    by_kind: tuple[tuple[str, int], ...]
    covered: int          # 挂了规则**且本轮跑出了结果**的指标数
    pending: int          # 失败 + 含义待查 + 未覆盖（**不含**已知缺陷）
    latest_run: datetime | None

    @property
    def coverage_rate(self) -> str:
        return f"{self.covered / self.total * 100:.0f}%" if self.total else "—"


def _source_label(metric_id: str) -> str:
    """来源显示到**接口级与字段级**。"""
    metric = REGISTRY[metric_id]
    if isinstance(metric.lineage, Direct):
        src = SOURCES.get(metric.lineage.source_id)
        provider = src.provider if src else metric.lineage.source_id
        api = src.api if src else "?"
        mult = metric.lineage.multiplier
        tail = f" ×{mult:g}" if mult != 1.0 else ""
        return f"{provider}·{api}.{metric.lineage.field}{tail}"
    roots = root_sources(metric_id)
    return " / ".join(roots) if roots else "（无法追溯到直取源）"


class CatalogService:
    def __init__(self, warehouse: Warehouse) -> None:
        self._query = WarehouseQuery(warehouse)

    def _latest(self) -> dict[str, list[dict]]:
        frame = self._query.latest_metric_checks()
        if frame.height == 0:
            return {}
        out: dict[str, list[dict]] = {}
        for row in frame.iter_rows(named=True):
            out.setdefault(row["metric_id"], []).append(row)
        return out

    def _status(self, metric_id: str, results: list[dict] | None) -> Status:
        metric = REGISTRY[metric_id]
        if metric.meaning is UNKNOWN:
            return Status.UNKNOWN_MEANING
        if isinstance(metric.checks, Uncovered):
            return Status.UNCOVERED
        if not results:
            return Status.UNVERIFIED
        failed = [r for r in results if not r["passed"]]
        if any(r["severity"] == "blocking" for r in failed):
            return Status.FAILED
        if failed:
            return Status.KNOWN_ISSUE
        return Status.OK

    def rows(
        self, *, dataset: str | None = None, kind: str | None = None,
        status: Status | None = None,
    ) -> list[MetricRow]:
        latest = self._latest()
        out = []
        for mid, metric in REGISTRY.items():
            st = self._status(mid, latest.get(mid))
            issues = tuple(metric.known_issues)
            if latest.get(mid):
                issues = issues + tuple(
                    r["known_issue"] for r in latest[mid]
                    if r["known_issue"] and r["known_issue"] not in issues
                )
            out.append(MetricRow(
                id=mid, dataset=metric.dataset, column=metric.column,
                kind=metric.kind, validity=str(metric.validity),
                meaning=metric.meaning, source_label=_source_label(mid),
                status=st, issues=issues,
            ))
        if dataset:
            out = [r for r in out if r.dataset == dataset]
        if kind:
            out = [r for r in out if str(r.kind) == kind]
        if status is not None:
            out = [r for r in out if r.status is status]
        return sorted(out, key=lambda r: (_ORDER[r.status], r.id))

    def summary(self) -> CatalogSummary:
        rows = self.rows()
        by_kind = tuple(
            (k, sum(1 for r in rows if r.kind is k)) for k in Kind
        )
        covered = sum(
            1 for r in rows
            if r.status in (Status.OK, Status.FAILED, Status.KNOWN_ISSUE)
        )
        pending = sum(
            1 for r in rows
            if r.status in (Status.FAILED, Status.UNKNOWN_MEANING,
                            Status.UNCOVERED)
        )
        frame = self._query.latest_metric_checks()
        latest_run = frame["checked_at"].max() if frame.height else None
        return CatalogSummary(len(rows), by_kind, covered, pending, latest_run)

    def datasets(self) -> list[str]:
        return sorted({m.dataset for m in REGISTRY.values()})

    def kinds(self) -> list[str]:
        return [str(k) for k in Kind]

    def statuses(self) -> list[tuple[str, str]]:
        return [(str(s), _LABEL[s][0]) for s in Status]
```

- [ ] **Step 4: 加 pill 样式与 nav**

`src/qsys/web/templates/base.html` 的 `<style>` 里，在 `.pill.warn` 之后加：

```css
.pill.todo { background:#f1f5f9; color:#64748b; }
```

nav 块里，在「数据覆盖」之后插入：

```html
    <a href="/data/catalog" class="{{ 'active' if page == 'data_catalog' }}">数据层</a>
```

- [ ] **Step 5: 写模板**

`src/qsys/web/templates/catalog_overview.html`：

```html
{% extends "base.html" %}
{% block content %}
<div class="card">
  <h2>指标字典</h2>
  <div class="kpis">
    <div class="kpi"><div class="label">指标总数</div><div class="value">{{ summary.total }}</div></div>
    {% for kind, n in summary.by_kind %}
    <div class="kpi"><div class="label">{{ {'direct':'直取','derived':'派生','factor':'因子'}[kind] }}</div><div class="value">{{ n }}</div></div>
    {% endfor %}
    <div class="kpi"><div class="label">规则覆盖率</div><div class="value">{{ summary.coverage_rate }}</div></div>
    <div class="kpi"><div class="label">待处理</div><div class="value">{{ summary.pending }}</div></div>
    <div class="kpi"><div class="label">最近验证</div><div class="value" style="font-size:14px">{{ summary.latest_run or "从未" }}</div></div>
  </div>
  <p style="color:var(--muted);font-size:12px;margin:10px 0 0">
    待处理 = 失败 + 含义待查 + 未覆盖。已知缺陷已挂账，不计入。
    <a href="/data/catalog/sources">按源查看 →</a>
  </p>
</div>

<div class="card">
  <h2>筛选</h2>
  <form method="get" style="display:flex;gap:10px;flex-wrap:wrap">
    <select name="dataset"><option value="">全部表</option>
      {% for d in datasets %}<option value="{{ d }}" {{ 'selected' if d == sel_dataset }}>{{ d }}</option>{% endfor %}
    </select>
    <select name="kind"><option value="">全部类型</option>
      {% for k in kinds %}<option value="{{ k }}" {{ 'selected' if k == sel_kind }}>{{ k }}</option>{% endfor %}
    </select>
    <select name="status"><option value="">全部状态</option>
      {% for v, label in statuses %}<option value="{{ v }}" {{ 'selected' if v == sel_status }}>{{ label }}</option>{% endfor %}
    </select>
    <button type="submit">应用</button>
    <a href="/data/catalog" style="align-self:center;color:var(--muted)">清空</a>
  </form>
</div>

<div class="card">
  <h2>{{ rows|length }} 个指标（问题排在最前）</h2>
  {% if rows %}
  <table>
    <tr><th>指标</th><th>类型</th><th>有效性</th><th>含义</th><th>来源</th><th>状态</th></tr>
    {% for r in rows %}
    <tr>
      <td><a href="/data/catalog/{{ r.id }}" style="color:var(--fg)">{{ r.id }}</a></td>
      <td>{{ {'direct':'直取','derived':'派生','factor':'因子'}[r.kind] }}</td>
      <td>{{ {'session':'当日','point':'点观测','span':'区间'}[r.validity] }}</td>
      <td style="max-width:380px">{{ r.meaning }}</td>
      <td style="color:var(--muted);font-size:12px">{{ r.source_label }}</td>
      <td><span class="pill {{ r.pill }}">{{ r.label }}</span>
          {% for d in r.issues %}<span class="pill warn">{{ d }}</span>{% endfor %}</td>
    </tr>
    {% endfor %}
  </table>
  {% else %}<div class="empty">没有符合条件的指标。</div>{% endif %}
</div>
{% endblock %}
```

- [ ] **Step 6: 加路由与接线**

`src/qsys/web/app.py`：`create_app` 签名加 `catalog: CatalogService | None = None`，
并加路由：

```python
    @app.get("/data/catalog", response_class=HTMLResponse)
    def catalog_overview(
        request: Request, dataset: str | None = None,
        kind: str | None = None, status: str | None = None,
    ):
        if catalog is None:
            raise HTTPException(status_code=404, detail="catalog not enabled")
        from qsys.web.catalog_views import Status

        sel = Status(status) if status else None
        return TEMPLATES.TemplateResponse(
            request=request, name="catalog_overview.html",
            context={
                "title": config.title, "session": None, "page": "data_catalog",
                "summary": catalog.summary(),
                "rows": catalog.rows(dataset=dataset, kind=kind, status=sel),
                "datasets": catalog.datasets(), "kinds": catalog.kinds(),
                "statuses": catalog.statuses(),
                "sel_dataset": dataset, "sel_kind": kind, "sel_status": status,
            },
        )
```

`scripts/run_monitor.py` 的 `create_app(...)` 调用加一个
`CatalogService(warehouse)` 实参，并 import 它。

- [ ] **Step 7: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/web/ -v`
Expected: 全绿（含既有的 web 测试——`create_app` 加参数不能破坏它们，
新参数有默认值 `None`）

- [ ] **Step 8: 提交**

```bash
git add src/qsys/web/catalog_views.py src/qsys/web/templates/ \
        src/qsys/web/app.py scripts/run_monitor.py tests/web/
git commit -m "feat(web): 数据层总览页，未覆盖与含义待查做成一等公民"
```

---

## Task 18: 详情页与按源页

**Files:**
- Modify: `src/qsys/web/catalog_views.py`（加 `detail()` / `sources()`）
- Create: `src/qsys/web/templates/catalog_detail.html`
- Create: `src/qsys/web/templates/catalog_sources.html`
- Modify: `src/qsys/web/app.py`（加两条路由 + 样本下载）
- Test: `tests/web/test_catalog_detail.py`

**Interfaces:**
- Consumes: Task 17 的 `CatalogService`
- Produces:
  - `MetricDetail(row, upstream, downstream, ext_consumers, formula, params,
    checks, history, evidence, roots)`
  - `SourceRow(source, metric_count, metric_ids)`
  - `CatalogService.detail(metric_id) -> MetricDetail | None`
  - `CatalogService.sources() -> list[SourceRow]`
  - `CatalogService.sample_bytes(path) -> tuple[bytes, str] | None`

- [ ] **Step 1: 写失败的测试**

`tests/web/test_catalog_detail.py`：

```python
"""详情页与按源页。详情页要回答「这个数从哪来、坏了影响什么」。"""

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
    cfg = WebConfig()
    return TestClient(create_app(
        MonitorService(SessionJournal(warehouse), config=cfg),
        cfg, catalog=CatalogService(warehouse),
    ))


def test_detail_shows_upstream_and_downstream(warehouse):
    d = CatalogService(warehouse).detail("daily_flags.limit_pct")
    assert "daily_flags.is_st" in d.upstream
    assert "daily_flags.limit_up" in d.downstream


def test_detail_of_a_direct_metric_has_no_upstream(warehouse):
    d = CatalogService(warehouse).detail("tushare_daily.close")
    assert d.upstream == ()
    assert d.roots == ("tushare.daily.close",)


def test_detail_returns_none_for_an_unknown_id(warehouse):
    assert CatalogService(warehouse).detail("nope.nope") is None


def test_detail_lists_manual_evidence(warehouse):
    warehouse.write("metric_evidence", pl.DataFrame([{
        "metric_id": "adj_factors.cum_factor", "symbol": "001331.SZ", "dt": D,
        "our_value": "3.4848", "reference_value": "2.4011",
        "reference_source": "同花顺", "verdict": "mismatch",
        "checked_by": "wanlinruo", "checked_at": T, "note": "D-1 抽查",
    }]).select(schema.METRIC_EVIDENCE.names)
        .to_arrow().cast(schema.METRIC_EVIDENCE), year=2026)
    d = CatalogService(warehouse).detail("adj_factors.cum_factor")
    assert len(d.evidence) == 1
    assert d.evidence[0]["reference_source"] == "同花顺"


def test_sources_page_counts_what_each_source_supports(warehouse):
    """回答「这个源挂了会影响什么」——现在没有任何地方能回答。"""
    rows = {r.source.id: r for r in CatalogService(warehouse).sources()}
    assert rows["tushare.daily"].metric_count >= 9
    assert rows["mootdx.kline"].metric_count == 8, "bars_daily 的 8 列"


def test_detail_page_renders(warehouse):
    r = _client(warehouse).get("/data/catalog/daily_flags.is_first_board")
    assert r.status_code == 200
    assert "首板" in r.text


def test_detail_page_404s_on_unknown_metric(warehouse):
    assert _client(warehouse).get("/data/catalog/nope.nope").status_code == 404


def test_sources_page_renders(warehouse):
    r = _client(warehouse).get("/data/catalog/sources")
    assert r.status_code == 200
    assert "mootdx" in r.text
    assert "已失效" in r.text or "DEAD" in r.text.upper()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/web/test_catalog_detail.py -v`
Expected: FAIL，`AttributeError: 'CatalogService' object has no attribute 'detail'`

- [ ] **Step 3: 扩 CatalogService**

在 `src/qsys/web/catalog_views.py` 里加：

```python
from pathlib import Path

from qsys.data.catalog import consumers
from qsys.data.catalog.types import Computed, Source


@dataclass(frozen=True, slots=True)
class MetricDetail:
    row: MetricRow
    upstream: tuple[str, ...]
    downstream: tuple[str, ...]
    ext_consumers: tuple[str, ...]
    formula: str
    params: tuple[tuple[str, object], ...]
    roots: tuple[str, ...]
    checks: tuple[dict, ...]      # 每条规则的最新结果
    history: tuple[dict, ...]     # 该指标全部历史结果
    evidence: tuple[dict, ...]    # 人工与第三方结论


@dataclass(frozen=True, slots=True)
class SourceRow:
    source: Source
    metric_count: int
    metric_ids: tuple[str, ...]
```

`CatalogService` 加三个方法：

```python
    def detail(self, metric_id: str) -> MetricDetail | None:
        if metric_id not in REGISTRY:
            return None
        metric = REGISTRY[metric_id]
        row = next(r for r in self.rows() if r.id == metric_id)
        computed = isinstance(metric.lineage, Computed)
        history = self._query.metric_checks(metric_id=metric_id)
        latest = self._latest().get(metric_id, [])
        evidence = self._query.metric_evidence(metric_id)
        return MetricDetail(
            row=row,
            upstream=metric.lineage.upstream if computed else (),
            downstream=consumers(metric_id),
            ext_consumers=metric.ext_consumers,
            formula=metric.lineage.formula if computed else "",
            params=metric.lineage.params if computed else (),
            roots=root_sources(metric_id),
            checks=tuple(latest),
            history=tuple(history.to_dicts()) if history.height else (),
            evidence=tuple(evidence.to_dicts()) if evidence.height else (),
        )

    def sources(self) -> list[SourceRow]:
        """反向问题：这个源挂了会影响什么？"""
        by_source: dict[str, list[str]] = {sid: [] for sid in SOURCES}
        for mid, metric in REGISTRY.items():
            if isinstance(metric.lineage, Direct):
                by_source.setdefault(metric.lineage.source_id, []).append(mid)
        return sorted(
            (SourceRow(SOURCES[sid], len(ids), tuple(sorted(ids)))
             for sid, ids in by_source.items() if sid in SOURCES),
            key=lambda r: (r.source.status != "active", -r.metric_count),
        )

    def sample_bytes(self, path: str) -> tuple[bytes, str] | None:
        """违规样本下载。路径必须落在 artifacts/metric_checks 下——
        否则就是一个任意文件读取口子。"""
        target = Path(path).resolve()
        root = Path("artifacts/metric_checks").resolve()
        if not target.is_file() or root not in target.parents:
            return None
        return target.read_bytes(), "application/octet-stream"
```

**`sample_bytes` 的路径校验不可省**：`sample_path` 来自结果表，
而结果表是可写的。不校验就等于把整个文件系统开给了 HTTP。

- [ ] **Step 4: 写两个模板**

`src/qsys/web/templates/catalog_detail.html`。**不画 SVG 血缘图**——
血缘最深 3–4 层，三栏列表就够，为此加一套图布局不划算。

```html
{% extends "base.html" %}
{% block content %}
<div class="card">
  <h2>{{ d.row.id }}</h2>
  <div style="margin-bottom:10px">
    <span class="pill {{ d.row.pill }}">{{ d.row.label }}</span>
    <span class="pill todo">{{ {'direct':'直取','derived':'派生','factor':'因子'}[d.row.kind] }}</span>
    <span class="pill todo">{{ {'session':'当日','point':'点观测','span':'区间'}[d.row.validity] }}</span>
    {% for issue in d.row.issues %}<span class="pill warn">{{ issue }}</span>{% endfor %}
  </div>
  <p style="margin:0">{{ d.row.meaning }}</p>
</div>

<div class="card">
  <h2>血缘</h2>
  <div style="display:flex;gap:24px;flex-wrap:wrap">
    <div style="flex:1;min-width:200px">
      <div class="label" style="color:var(--muted);font-size:12px">上游</div>
      {% if d.upstream %}
        {% for u in d.upstream %}<div><a href="/data/catalog/{{ u }}">{{ u }}</a></div>{% endfor %}
      {% else %}<div class="empty">直取，无上游指标</div>{% endif %}
    </div>
    <div style="flex:1;min-width:200px">
      <div class="label" style="color:var(--muted);font-size:12px">本指标</div>
      <div><strong>{{ d.row.id }}</strong></div>
      <div style="color:var(--muted);font-size:12px;margin-top:4px">{{ d.row.source_label }}</div>
      {% if d.formula %}<div style="margin-top:6px"><code>{{ d.formula }}</code></div>{% endif %}
      {% for k, v in d.params %}<div style="font-size:12px">{{ k }} = {{ v }}</div>{% endfor %}
    </div>
    <div style="flex:1;min-width:200px">
      <div class="label" style="color:var(--muted);font-size:12px">下游</div>
      {% for c in d.downstream %}<div><a href="/data/catalog/{{ c }}">{{ c }}</a></div>{% endfor %}
      {% for c in d.ext_consumers %}<div>{{ c }}</div>{% endfor %}
      {% if not d.downstream and not d.ext_consumers %}<div class="empty">无已知消费方</div>{% endif %}
    </div>
  </div>
  {% if d.roots %}
  <p style="margin:12px 0 0;color:var(--muted);font-size:12px">
    根源：{{ d.roots|join(" / ") }}
  </p>{% endif %}
</div>

<div class="card">
  <h2>验证规则</h2>
  {% if d.checks %}
  <table>
    <tr><th>规则</th><th>类别</th><th class="num">违规</th><th class="num">检查行数</th><th>说明</th><th>样本</th></tr>
    {% for c in d.checks %}
    <tr>
      <td>{{ c.check_id }}</td><td>{{ c.category }}</td>
      <td class="num {{ 'down' if c.violations else '' }}">{{ c.violations }}</td>
      <td class="num">{{ "{:,}".format(c.checked_rows) }}</td>
      <td style="max-width:320px">{{ c.detail }}</td>
      <td>{% if c.sample_path %}<a href="/data/catalog/sample?path={{ c.sample_path }}">下载</a>{% else %}—{% endif %}</td>
    </tr>
    {% endfor %}
  </table>
  {% else %}<div class="empty">尚未跑过验证，或这条指标未挂任何规则。</div>{% endif %}
</div>

{% if chart %}
<div class="card"><h2>违规数走势</h2>{{ chart|safe }}</div>
{% endif %}

<div class="card">
  <h2>人工与第三方证据</h2>
  {% if d.evidence %}
  <table>
    <tr><th>标的</th><th>日期</th><th>我们的值</th><th>对照值</th><th>来源</th><th>判定</th><th>验的人</th><th>备注</th></tr>
    {% for e in d.evidence %}
    <tr>
      <td>{{ e.symbol }}</td><td>{{ e.dt }}</td>
      <td>{{ e.our_value }}</td><td>{{ e.reference_value }}</td>
      <td>{{ e.reference_source }}</td>
      <td><span class="pill {{ 'ok' if e.verdict == 'match' else 'bad' }}">{{ e.verdict }}</span></td>
      <td>{{ e.checked_by }}</td><td>{{ e.note }}</td>
    </tr>
    {% endfor %}
  </table>
  {% else %}<div class="empty">还没有人工验过这条指标。</div>{% endif %}
</div>
{% endblock %}
```

`src/qsys/web/templates/catalog_sources.html`：

```html
{% extends "base.html" %}
{% block content %}
<div class="card">
  <h2>数据源</h2>
  <p style="color:var(--muted);font-size:12px;margin:0 0 12px">
    这一页回答的是反向问题：<strong>这个源挂了会影响什么。</strong>
    非 ACTIVE 的排在最前。
  </p>
  <table>
    <tr><th>源</th><th>状态</th><th>获取</th><th>门槛</th><th>到货节奏</th>
        <th class="num">支撑指标</th><th>已知坑</th></tr>
    {% for r in rows %}
    <tr>
      <td><strong>{{ r.source.provider }}</strong>·{{ r.source.api }}</td>
      <td><span class="pill {{ 'ok' if r.source.status == 'active' else 'bad' }}">
        {{ {'active':'在用','dead':'已失效','frozen':'已冻结'}[r.source.status] }}</span></td>
      <td>{{ {'api':'自动接口','file':'人工转存','realtime':'实时自采'}[r.source.access] }}</td>
      <td>{{ r.source.tier or "—" }}</td>
      <td>{{ r.source.cadence or "—" }}
        {% if r.source.expires %}<br><span style="color:var(--warn);font-size:12px">
          {{ r.source.expires }} 到期</span>{% endif %}</td>
      <td class="num">{{ r.metric_count }}</td>
      <td style="max-width:380px;font-size:12px;color:var(--muted)">{{ r.source.caveat or "—" }}</td>
    </tr>
    {% endfor %}
  </table>
</div>
{% endblock %}
```

两个模板的 `page` 都传 `"data_catalog"`，nav 才会高亮。

- [ ] **Step 5: 加三条路由**

`src/qsys/web/app.py`：

```python
    @app.get("/data/catalog/sources", response_class=HTMLResponse)
    def catalog_sources(request: Request):
        if catalog is None:
            raise HTTPException(status_code=404, detail="catalog not enabled")
        return TEMPLATES.TemplateResponse(
            request=request, name="catalog_sources.html",
            context={"title": config.title, "session": None,
                     "page": "data_catalog", "rows": catalog.sources()},
        )

    @app.get("/data/catalog/{metric_id}", response_class=HTMLResponse)
    def catalog_detail(request: Request, metric_id: str):
        if catalog is None:
            raise HTTPException(status_code=404, detail="catalog not enabled")
        detail = catalog.detail(metric_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"no metric {metric_id}")
        points = [float(h["violations"]) for h in detail.history]
        return TEMPLATES.TemplateResponse(
            request=request, name="catalog_detail.html",
            context={"title": config.title, "session": None,
                     "page": "data_catalog", "d": detail,
                     "chart": line_chart(points) if points else ""},
        )

    @app.get("/data/catalog/sample")
    def catalog_sample(path: str):
        if catalog is None:
            raise HTTPException(status_code=404, detail="catalog not enabled")
        found = catalog.sample_bytes(path)
        if found is None:
            raise HTTPException(status_code=404, detail="no such sample")
        body, media = found
        return Response(content=body, media_type=media,
                        headers={"Content-Disposition":
                                 'attachment; filename="sample.parquet"'})
```

**路由顺序要紧**：`/data/catalog/sources` 与 `/data/catalog/sample` 必须
注册在 `/data/catalog/{metric_id}` **之前**，否则 `sources` 会被当成
一个 metric_id 吃掉。

- [ ] **Step 6: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/web/ -v`
Expected: 全绿

- [ ] **Step 7: 提交**

```bash
git add src/qsys/web/ tests/web/test_catalog_detail.py
git commit -m "feat(web): 指标详情页与按源页，回答「这个源挂了影响什么」"
```

---

## Task 19: 修 D-9——`feed.py` 的三处 `fill_null(1.0)`

C1 已经把它们扫出来了。**实际是三处不是一处**：`feed.py:85` 的 `cum_factor`、
`:86` 的 `cum_share_factor`、`:88` 的 `pl.lit(1.0)` 兜底。D-9 只写了第一处，
但同一条论证对三处都成立。

**Files:**
- Modify: `src/qsys/data/feed.py:84-89`
- Modify: `tests/data/test_feed.py`
- Modify: `tests/data/catalog/test_rules_valid.py`
- Modify: `docs/reference/deferred-corrections.md`、
  `docs/reference/data-source-migration-record.md`

**Interfaces:**
- Consumes: Task 15 的 `C1.no_fill` / `rules_valid.scan_fills`
- Produces: 无新接口

- [ ] **Step 1: 盘清消费方**

D-9 原定第一步，不能跳。跑：

```bash
grep -rn "\.get(\|Adjustment\.\|cum_factor\|cum_share_factor" \
  src tests scripts --include="*.py" | grep -v "catalog/\|def get"
```

把每个调用点对 null 价格的处理列出来，**输出写进提交信息**——
下一个人需要知道你是照着什么改的。

已知（D-8 记录）：`selection.py` 只用 `FORWARD`，`context.py` 默认 `NONE`，
生产无后复权调用点。

- [ ] **Step 2: 写失败的测试**

在 `tests/data/test_feed.py` 末尾加（沿用文件顶部既有的 `SESSIONS` / `CAL`
与 conftest 的 `write_daily_bars` 夹具）：

```python
def test_missing_factor_produces_null_prices_not_a_plausible_number(
    warehouse, write_daily_bars
):
    """D-9：旧语义下 cum_factor 以窗口首日归一，1.0 就是首日值；
    新语义下它是源自带锚点的累计值（D-1 抽查的 001331.SZ 同窗口内
    2.40–3.48）。填 1.0 等于把价格打到几分之一——产出的是一个
    **看起来正常**的错价，比 null 危险得多。
    """
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["000001.SZ"] * 2,
        "dt": SESSIONS[:2],
        "open": [10.0, 10.1], "high": [10.5, 10.6],
        "low": [9.8, 9.9], "close": [10.0, 10.2],
        "volume": [1000.0, 1000.0], "amount": [10000.0, 10200.0],
    }))
    # adj_factors 里只有 600036，000001 一行因子都没有
    warehouse.write("adj_factors", pa.table({
        "symbol": ["600036.SH"], "dt": [SESSIONS[0]],
        "cum_factor": [1.0], "cum_share_factor": [1.0],
    }, schema=schema.ADJ_FACTORS))

    feed = HistoricalFeed(WarehouseQuery(warehouse), CAL)
    df = feed.get(["000001.SZ"], SESSIONS[0], SESSIONS[1],
                  as_of=datetime(2024, 1, 5, 15, 0),
                  adjustment=Adjustment.FORWARD)
    assert df.height == 2
    assert df["close"].null_count() == 2, "因子缺失的行不得产出看起来正常的价格"


def test_present_factors_still_adjust_normally(feed):
    """反向守护：修完不能把正常路径也打成 null。"""
    df = feed.get(["600036.SH"], SESSIONS[0], SESSIONS[-1],
                  as_of=datetime(2024, 1, 5, 15, 0),
                  adjustment=Adjustment.FORWARD)
    assert df["close"].null_count() == 0
```

- [ ] **Step 3: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/test_feed.py -v`
Expected: `test_missing_factor_...` FAIL——当前填 1.0，产出的是非 null 的价格

- [ ] **Step 4: 改实现**

`src/qsys/data/feed.py:84-89`，把

```python
        joined = bars.join(factors, on=["symbol", "dt"], how="left").with_columns(
            pl.col("cum_factor").fill_null(1.0),
            pl.col("cum_share_factor").fill_null(1.0)
            if "cum_share_factor" in factors.columns
            else pl.lit(1.0).alias("cum_share_factor"),
        )
```

改成：

```python
        # **因子缺失时价格必须为 null，不得有默认值。** 旧语义下 cum_factor
        # 以窗口首日归一、1.0 就是首日值，填进去与序列同量级；新语义下它是
        # 源自带锚点的累计值（D-1 抽查的 001331.SZ 同窗口内 2.40–3.48，
        # 全历史跨度更大），填 1.0 等于把价格打到几分之一——产出的是一个
        # **看起来正常**的错价，比整行 null 危险得多。见 D-9。
        #
        # 第三处（旧分区没有 cum_share_factor 列时的兜底）同理：那说明这批
        # 数据根本没有股本因子，应当让成交量调整为 null，而不是假装「不用调」。
        joined = bars.join(factors, on=["symbol", "dt"], how="left").with_columns(
            pl.col("cum_factor"),
            pl.col("cum_share_factor")
            if "cum_share_factor" in factors.columns
            else pl.lit(None, dtype=pl.Float64).alias("cum_share_factor"),
        )
```

- [ ] **Step 5: 跑全套测试**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 全绿。

若有消费方因 null 价格而崩，**不要退回去填 1.0**——D-9 明确写了
「静默错误」与「运行时崩溃」两者都不可接受。正确做法是让该消费方显式
处理 null（跳过该标的并记日志），并为这个处理加一条测试。

- [ ] **Step 6: 改 C1 的断言并复验**

`tests/data/catalog/test_rules_valid.py` 里
`test_feed_fill_null_is_currently_a_violation` 整条替换为：

```python
def test_feed_no_longer_fills_the_factor():
    """D-9 已结案：feed.py 不再有未登记的填充。

    C1 是常驻规则，所以这类问题不会以「新增一处 fill_null」的形式复发。
    """
    from pathlib import Path

    sites = rules_valid.scan_fills([Path("src/qsys/data")])
    assert not [s for s in sites if "feed.py" in s.path]
```

Run: `.venv/bin/python -m pytest tests/data/catalog/test_rules_valid.py -v`

- [ ] **Step 7: 给 D-9 结案**

在 `docs/reference/deferred-corrections.md` 的 D-9 段末尾追加
「### 2026-XX-XX 处置」一节，写明四件事：Step 1 的消费方盘清结果、
改成了 null 传播、**实际修的是三处不是一处**、`C1.no_fill` 已把这类问题
做成常驻规则所以不会复发。

同步把 `docs/reference/data-source-migration-record.md` 第七节表里的
D-9 标为**已了结**。

- [ ] **Step 8: 提交**

```bash
git add src/qsys/data/feed.py tests/ docs/reference/
git commit -m "fix(data): D-9 了结——因子缺失改 null 传播，三处填充全清"
```

---

## Task 20: 接进每日调度

spec §6.6 要求增量验证「接在 17:00 / 19:00 两次 sync 之后」。
没有这一步，整套验证器就只是一个人要记得去跑的脚本。

**Files:**
- Modify: `scripts/tushare_daily_sync.py`
- Modify: `docs/deploy/linux-core.md`
- Test: `tests/data/catalog/test_sync_hook.py`

**Interfaces:**
- Consumes: Task 16 的 `scripts/verify_metrics.py`
- Produces: `tushare_daily_sync` 在同步成功后调用增量验证

- [ ] **Step 1: 写失败的测试**

`tests/data/catalog/test_sync_hook.py`：

```python
"""同步后的验证挂钩。验证失败**不得**回滚同步——数据已经落盘了。"""

from datetime import date

import pytest

from scripts import tushare_daily_sync


def test_verification_runs_after_a_successful_sync(monkeypatch, warehouse):
    called = {}

    def fake_verify(wh, day):
        called["day"] = day
        return 0

    monkeypatch.setattr(tushare_daily_sync, "run_incremental_verification",
                        fake_verify)
    tushare_daily_sync.after_sync(warehouse, date(2026, 9, 15), ok=True)
    assert called["day"] == date(2026, 9, 15)


def test_verification_is_skipped_when_the_sync_itself_failed(
    monkeypatch, warehouse
):
    """同步失败时数据是残缺的，验证必然一片红，报出来只会淹没真问题。"""
    called = {}
    monkeypatch.setattr(tushare_daily_sync, "run_incremental_verification",
                        lambda wh, day: called.setdefault("ran", True))
    tushare_daily_sync.after_sync(warehouse, date(2026, 9, 15), ok=False)
    assert "ran" not in called


def test_a_crashing_verifier_does_not_fail_the_sync(monkeypatch, warehouse):
    """数据已经落盘了。让验证器把同步的退出码带成非零，
    systemd 会把一次成功的同步记成失败，下次 Persistent 又来补——
    补的是已经补过的东西。"""
    def boom(wh, day):
        raise RuntimeError("验证器炸了")

    monkeypatch.setattr(tushare_daily_sync, "run_incremental_verification", boom)
    tushare_daily_sync.after_sync(warehouse, date(2026, 9, 15), ok=True)  # 不抛
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_sync_hook.py -v`
Expected: FAIL，`AttributeError: module 'scripts.tushare_daily_sync' has no attribute 'after_sync'`

- [ ] **Step 3: 写实现**

在 `scripts/tushare_daily_sync.py` 加：

```python
def run_incremental_verification(warehouse, day: date) -> int:
    """跑当日增量验证并落盘。返回 blocking 失败数。"""
    from datetime import datetime

    from qsys.data.catalog import checks as C
    from qsys.data.catalog import rules_complete  # noqa: F401
    from qsys.data.catalog import rules_recompute  # noqa: F401
    from qsys.data.catalog import rules_truth  # noqa: F401
    from qsys.data.catalog import rules_valid  # noqa: F401
    from qsys.data.query import WarehouseQuery

    ctx = C.CheckContext(WarehouseQuery(warehouse), warehouse, day, day)
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    frame = C.run_checks(ctx, run_id, datetime.now(), scope=C.Scope.INCREMENTAL)
    C.write_results(warehouse, frame)
    blocking = frame.filter(
        (~pl.col("passed")) & (pl.col("severity") == "blocking")
    )
    return blocking.height


def after_sync(warehouse, day: date, *, ok: bool) -> None:
    """同步成功后跑一次增量验证。

    两条纪律：
    1. **同步失败就不验**——数据残缺时验证必然一片红，报出来只会淹没真问题。
    2. **验证器崩了不能连累同步的退出码**。数据已经落盘了；让 systemd 把
       一次成功的同步记成失败，下次 Persistent 又来补——补的是已经补过的
       东西，而真正的问题（验证器坏了）反而被记成了「同步失败」。
    """
    if not ok:
        log.info("verify.skipped_after_failed_sync", day=str(day))
        return
    try:
        blocking = run_incremental_verification(warehouse, day)
    except Exception as exc:
        log.warning("verify.hook_crashed", day=str(day), error=str(exc))
        return
    log.info("verify.after_sync_done", day=str(day), blocking=blocking)
```

然后在 `main()` 的同步流程末尾调用 `after_sync(warehouse, day, ok=success)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/catalog/test_sync_hook.py -v`
Expected: 3 passed

- [ ] **Step 5: 更新部署文档**

`docs/deploy/linux-core.md` 里补一节，说明：

- `qsys-tushare-sync` 现在在同步成功后会跑一次增量验证，结果落
  `metric_checks`，可在 `/data/catalog` 查看；
- 验证失败**不影响** systemd 的退出码，要看结果去页面或查表，
  不要靠 `systemctl status`；
- 部署到节点时**只推具体文件**（`git bundle` + `fetch`，或逐文件列出），
  **绝不整目录 rsync**——`warehouse` 是指向 `/data/qsys` 的软链接、
  `.venv` 是 Linux 的，整目录同步会毁掉两者（有前科）。

- [ ] **Step 6: 提交**

```bash
git add scripts/tushare_daily_sync.py tests/data/catalog/test_sync_hook.py \
        docs/deploy/linux-core.md
git commit -m "feat(ops): 同步成功后自动跑增量验证，崩溃不连累同步退出码"
```

---

## 收尾：验收对照

按 spec §九逐条核，全部满足才算完成：

- [ ] T1–T6 六条守护测试全绿，T1 断言的条目数与 schema 实际列数一致
- [ ] 策略侧决策量全部登记，血缘可追溯到落盘指标
- [ ] `meaning=UNKNOWN` 的条目有清单，数量记录在案
- [ ] `UNCOVERED` 的条目有清单，每条带理由
- [ ] A/B/C 三类规则各至少一条在生产数据上实跑出结果
- [ ] C1 扫出 `feed.py:85` 并按 D-9 三步处理完毕（Task 19）
- [ ] **A3 的漏除权规则复现 D-1 记录的 12 只标的**
      （`000430.SZ` `000691.SZ` `000793.SZ` `000908.SZ` `002200.SZ`
      `002742.SZ` `002822.SZ` `300093.SZ` `300125.SZ` `300159.SZ`
      `300176.SZ` `300506.SZ`）——**复现不了说明规则写错了，不是数据变好了**
- [ ] A3 的 `limit_list` 口径规则复现 D-11 的差集全为 ST
- [ ] 三条页面路由可访问，且在 `metric_checks` 为空时正常渲染
- [ ] A4 清单可导出，`metric_evidence` 至少录入一条人工结论走通闭环

最后两条的复现验证跑法：

```bash
.venv/bin/python scripts/verify_metrics.py run \
  --warehouse warehouse --scope full \
  --start 1990-12-19 --end 2026-09-11
```

然后查样本：

```bash
.venv/bin/python -c "
import polars as pl, glob
p = sorted(glob.glob('artifacts/metric_checks/*/A3.missed_exdiv.parquet'))[-1]
got = set(pl.read_parquet(p)['symbol'].unique().to_list())
want = {'000430.SZ','000691.SZ','000793.SZ','000908.SZ','002200.SZ','002742.SZ',
        '002822.SZ','300093.SZ','300125.SZ','300159.SZ','300176.SZ','300506.SZ'}
print('命中', len(want & got), '/ 12；漏掉', sorted(want - got))
"
```
