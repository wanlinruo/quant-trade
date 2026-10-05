# 数据域目录设计（2026-10-05）

## 1. 为什么做

一天之内两次因为「看不见」而卡住：

1. 页面上找不到 `paid` 源——表的 `source` 列写 `paid`，目录登记的 id 是 `baidupan.minute`，
   两套命名并存且页面只显示后者。
2. 以为付费源「已经不用了」——实际它**停止采集但天天在读**：2020–2025 六年的分钟线
   100% 来自它，而 QMT 只有 12 个月滚动窗口、Tushare 的分钟权限不在积分体系内，
   那六年**无法用别的源替代**。

`SourceStatus` 枚举早就把「还能不能取到新数据」建模了（ACTIVE / FROZEN / DEAD），
页面没有显示它。信息在模型里，不在眼前。

同时，现有的源视图按技术 id 平铺（`tushare.daily_basic`、`qmt.minute`），
回答不了「我的行情数据来自哪些源」这类按业务含义提出的问题。

## 2. 目标与非目标

**目标**：按金融数据的标准业务分类组织目录，使两个方向的问题都能一屏答完——
「这个域的数据来自哪些源」与「这个源停了影响哪些域」。

**非目标**：
- 不做域 × 源的交叉矩阵。七域 × 25 源 = 175 格，绝大多数为空，看着全面但没人读。
- 不替换按技术 id 查源的现有页面。排查时仍需要按 `tushare.stk_limit` 直接找接口。
- 不解决「归错域」。那是判断不是事实，测试验不了（见 §6）。

## 3. 数据模型

### 3.1 新枚举 `DataDomain`

与既有的 `DatasetLayer`（架构层）、`DatasetRole`（建模角色）**正交**：

| 维度 | 回答 | 取值 |
|---|---|---|
| `layer` | 这张表在架构的哪一层 | raw / canonical / realtime / derived / operational |
| `role` | 建模角色 | dimension / fact / event / snapshot / factor / logical |
| **`domain`（新）** | **业务含义上这是什么数据** | 见下 |

```python
class DataDomain(StrEnum):
    """业务含义上的数据分类，与 layer（架构层）、role（建模角色）正交。"""
    REFERENCE   = "reference"      # 基础：标的、日历、状态、公司行为
    MARKET      = "market"         # 行情：K线、Tick、竞价、涨跌停价
    FUNDAMENTAL = "fundamental"    # 基本面：财报、财务指标、估值、股本
    FACTOR      = "factor"         # 衍生：复权因子、技术指标、信号、特征
    MACRO       = "macro"          # 宏观：利率、汇率、GDP、CPI
    ALTERNATIVE = "alternative"    # 另类：新闻、舆情、资金流、龙虎榜
    RUNTIME     = "runtime"        # 运行与质检：质检结果、成交回报、净值
```

**命名决定**：衍生域叫 `FACTOR` 而非 `DERIVED`，避免与 `DatasetLayer.DERIVED` 撞名。
两者意思不同——`layer.DERIVED` 说「这张表是算出来的」，`domain.FACTOR` 说「这是信号类
数据」。`adj_factors` 可能 `layer=RAW`（直接从源拿）而 `domain=FACTOR`（复权因子本质
是衍生数据）。

### 3.2 `Dataset.domain` 必填

照 `Metric.checks` 的先例：不得留空，拿不准必须写 `UNCLASSIFIED("理由")`，
由 `__post_init__` 强制。**不设默认值**——默认值会把「没想清楚」伪装成「已归类」。

代价是以后每加一张表都要停下来判断一次。换来的是页面上永远不会有归属不明的表。

### 3.3 源不加 `domain`，加 `aliases`

**源不分类。** 一个源常跨域——Tushare Pro 同时提供行情、基本面、宏观，贴一个标签说不清。
域→源的关系**由血缘推导**（`domain → datasets → root_sources()`），因此一个源天然可以
出现在多个域下，且是算出来的、不会与事实漂移。

```python
class Source:
    ...
    aliases: tuple[str, ...] = ()   # 别处对同一个源的称呼
```

`baidupan.minute` 的 `aliases=("paid",)`，因为 `bar_minute_1m.source` 列写的就是 `paid`。
页面显示为「百度网盘付费源（余哈哈数据仓库）· 表内写作 `paid`」。

## 4. 47 张表的归类

### 4.1 归类判据

归到**这张表回答什么问题**，不是它怎么来的：

| 域 | 判据 |
|---|---|
| REFERENCE | 描述「这个标的/这个市场是什么」——身份、日历、状态、公司行为 |
| MARKET | 描述「价格和成交发生了什么」——随交易日或每笔成交变动 |
| FUNDAMENTAL | 描述「公司值多少、经营得怎样」——财报、估值、股本 |
| FACTOR | **我们算出来的**信号与特征 |
| RUNTIME | 系统自身的运行痕迹，不描述市场 |

### 4.2 分布

合计 47 张，逐张归属无重复、无遗漏：

| 域 | 表数 | 全部表 |
|---|---:|---|
| 基础 REFERENCE | 11 | `instruments`、`security_master`、`trading_calendar`、`name_history`、`security_name_history`、`name_snapshots`、`delisting_pool`、`stock_st_daily`、`suspend_daily`、`security_status_daily`、`corporate_actions` |
| 行情 MARKET | 16 | `bar_daily`、`bars_daily`、`daily_bars`、`bar_daily_realtime`、`bar_minute_1m`、`minute_bars`、`paid_minute_bars`、`bar_minute_realtime`、`auction_ticks`、`index_bars`、`tushare_daily`、`paid_daily`、`quote_realtime_latest`、`price_limit_daily`、`price_limits_daily`、`limit_list_daily` |
| 基本面 FUNDAMENTAL | 4 | `valuation_daily`、`valuation_snapshot`、`share_capital`、`share_capital_history` |
| 衍生 FACTOR | 6 | `adj_factors`、`adjustment_factor_daily`、`daily_flags`、`market_flags_daily`、`market_regime`、`market_regime_daily` |
| **宏观 MACRO** | **0** | —— |
| **另类 ALTERNATIVE** | **0** | —— |
| 运行与质检 RUNTIME | 10 | `metric_checks`、`dataset_checks`、`relation_checks`、`metric_evidence`、`ingest_log`、`live_fills`、`live_equity`、`live_recon`、`live_rejects`、`strategy` |

**两个标准域为空，这本身是结论**：基本面只有估值与股本，**三大报表与财务指标一张未入库**；
宏观与另类完全空白。而 Tushare Pro 当前积分已含宏观经济接口，龙虎榜、资金流、
概念板块、北向也在权限内。**不是买不到，是没接。**

### 4.3 跨域表的判决

47 张里 5 张有歧义。**采用单一主域 + 页面备注，不上多域模型**：

| 表 | 判给 | 理由 | 把握 |
|---|---|---|---|
| `daily_flags` | FACTOR | 主体是首板/炸板/涨停标记，是我们算的信号。含 `is_st`（REFERENCE 性质），页面备注 | 高 |
| `price_limits_daily` | MARKET | 每日随前收变动、直接参与撮合 | 中（也可论证为交易规则→REFERENCE） |
| `share_capital` | FUNDAMENTAL | 行业惯例中「公司信息」属基本面 | 中 |
| `corporate_actions` | REFERENCE | 公司行为在行业惯例中属 reference data | 中 |
| `valuation_daily` | FUNDAMENTAL | 估值即基本面 | 高 |

把握为「中」的三张先按此落，页面标注「可另归 X」。改一个字段即可调整，不是重构。

## 5. 页面

嵌入既有的 `/data` 体系，不另起门户。

| 路由 | 内容 |
|---|---|
| `/data/domains`（新） | 七域总览 |
| `/data/domains/{domain}`（新） | 某域：哪些表、来自哪些源 |
| `/data/catalog/sources`（保留） | 每行补上状态、别名、所属域 |

**保留按技术 id 查源的页面**：排查时仍需要直接找 `tushare.stk_limit` 是哪个接口。
域视图是加一层入口，不是取代。

### 5.1 总览

每行回答三个问题：有多少表、数据来自哪、还在不在采集。

| 域 | 表数 | 数据来自 | 状态 |
|---|---:|---|---|
| 基础 Reference | 11 | Tushare Pro · QMT | 在采集 |
| 行情 Market | 16 | Tushare Pro · QMT · 百度网盘付费源（已停采，历史仍在读） | 混合 |
| 基本面 Fundamental | 4 | Tushare Pro | 在采集 |
| 衍生 Factor | 6 | *(本地计算)* | —— |
| 宏观 Macro | 0 | —— | **未接入** |
| 另类 Alternative | 0 | —— | **未接入** |
| 运行与质检 Runtime | 10 | *(系统自产)* | —— |

「数据来自」一列**由血缘算出**，不手工填。

### 5.2 源状态的措辞

`SourceStatus` 必须显示，措辞直接针对已发生的误解：

| 状态 | 页面措辞 |
|---|---|
| ACTIVE | 在采集 |
| FROZEN | **已停采，历史仍在读** |
| DEAD | 上游已失效 |

### 5.3 空域的说明

空域不只显示 0，要带一句登记说明与日期：

> 宏观 Macro —— **0 张表。未接入。** Tushare Pro 当前积分已含宏观经济接口。*(2026-10-05 登记)*
>
> 另类 Alternative —— **0 张表。未接入。** 龙虎榜、资金流、概念板块、北向在当前积分内可得。*(2026-10-05 登记)*

「在当前积分内可得」是本页最有价值的一行：它把「没有这个数据」与「买不起这个数据」
分开。两者的处置完全不同——ETF 分钟线属于后者：分钟权限不在积分体系内，
加多少积分都买不到。

### 5.4 反向查询

不单开页面。源详情里展开显示：该源支撑哪些域的哪些表，以及哪些质检规则随之失效。
数据由 `consumers()` 反向遍历算出，不手工维护。

## 6. 测试

挂进 `tests/data/catalog/`，不进定时任务——`catalog_verify` 验的是**数据**，这里验的是**目录自身**。

| # | 测试 | 防什么 |
|---|---|---|
| 1 | 每张表都声明了 domain | 新表漏标 |
| 2 | **源别名对得上真实数据** | 表里出现了目录不认识的源 |
| 3 | 非空域能推导出至少一个根源 | 血缘断裂 |

第 2 条最值钱：读 `bar_minute_1m.source` 的 distinct 值（`paid`/`qmt`/`joinquant`），
断言每个都能映射到某个 `Source`。**有人新加了源却没登记时它会立刻红**
——那正是本次混乱的源头。

FACTOR 与 RUNTIME 两域豁免第 3 条（本地计算、系统自产，无外部根源）。

**「可得但未接入」清单不做测试。** 它依赖外部事实（Tushare 积分权限），测试验不了，
硬验只会在每次权限变动时变成噪声。改为在页面标注登记日期，日期旧了自然会被怀疑——
这比一个假装能验的测试诚实。

## 7. 明确的局限

**这套设计防不住「归错域」。** 测试只能验「每张表都有域」「别名对得上」，
验不了「`price_limits_daily` 该不该归 MARKET」。那是判断，不是事实。

缓解办法是在域详情页同时显示 `layer` 与 `role`：当三个维度凑在一起别扭时
（例如 `layer=RAW` 配 `domain=FACTOR`），人能察觉。**页面帮人发现，而不是测试替人判断。**
