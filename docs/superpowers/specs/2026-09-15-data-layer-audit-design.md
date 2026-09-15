# 数据层大盘点：指标字典、常驻验证与数据层页面

创建日期：2026-09-15
状态：设计已确认，待写实施计划

---

## 一、为什么做

日线真值源迁移（`docs/reference/data-source-migration-record.md`）在 2026-09-14 完成，
生产链路从 mootdx 经付费源换到 Tushare 直连。迁移过程中查出一批数据缺陷，
登记在 `docs/reference/deferred-corrections.md` 的 D-1 ~ D-12，统一触发条件是
「换源完成后一次性处理」——该条件现已满足。

但比那张挂账表更根本的问题是：**这个系统现在有 206 个落盘字段和十几个不落盘的
策略因子，没有任何地方说得清每一个是什么、从哪来、算得对不对。**

迁移期间的每一次排查都在重复同一件事：翻 pipeline 代码认血缘、翻文档找口径、
写一次性脚本比对、结论写进 artifacts 然后被遗忘。D-1 的 688 条偏差查了三轮，
每轮都要重新建立上下文。

本项目要建立的是让这件事只做一次的设施：

1. **指标字典**——每个指标的含义、类型、有效性语义、血缘、消费方，单一真值源；
2. **常驻验证**——真实性/完整性/有效性三类规则，可反复重跑，结果落盘；
3. **数据层页面**——把上面两件事摆出来，包括「哪些我们还不清楚」。

## 二、范围

### 纳入

| 类别 | 数量 | 说明 |
|---|---:|---|
| `schema.ALL` 全部字段 | 178 | 22 张表 |
| `PAID_DAILY` 字段 | 28 | 故意不注册进 `PARTITIONED`，但是 2026-09-11 前名称的唯一真值源 |
| 策略侧不落盘决策量 | 10 | `stock_return_10d`、`distance_to_60d_high`、`avg_inc`、竞价价比/量比、`relative_position_60d`、左压 `zyts` 等 |
| **合计** | **216** | |

策略侧这 10 个必须纳入：它们是 S6/S7 Gate 与入场过滤的直接输入，D-4 阈值重标定就靠它们。
只盘落盘字段会让字典追不到决策。

### 不纳入

- **回测绩效指标**（`backtest/metrics.py` 的夏普、最大回撤、胜率、盈亏比；
  `fees.py` / `slippage.py` 的成本假设）。它们是结果评价而非数据真实性问题，
  验证手段与数据类完全不同，混进来会让一份 spec 同时装两种方法论。
- **D-4 的 S6/S7 阈值重标定**。文档已明确它是研究任务不是数据层任务，
  要重跑分位/消融流程，折进数据任务会毁掉三段式归因。本项目为它产出前置条件
  （因子可信度结论），不做重标定本身。

### 本项目会修的缺陷

盘点不止于发现。以下挂账项本身就是「常驻质检」的形态，不做即为重复建设：

| 编号 | 内容 | 在本项目中的落点 |
|---|---|---|
| D-1（部分） | 漏除权检测规则化 | 规则 A3「`pre_close` 缺口非零而 `factor` 不跳」 |
| D-1（部分） | 688 条非股改期偏差的第三方抽样 | 规则 A4 生成清单 + `metric_evidence` 落盘结论 |
| D-2 | 920 号段北交所因子异常簇 | 同 A4 |
| D-9 | `feed.py:85` 的 `fill_null(1.0)` | 规则 C1 非法填充扫描；扫出后按 D-9 原定三步修 |
| D-11 | `limit_list_d` 不含 ST 的口径差 | 规则 A3「差集应恰为当日 ST 涨停集」，并在字典登记口径 |
| D-12 | `limit_list_d` 发布时点晚 | 规则 B4「全市场零涨停视为失败」 |

D-3、D-5、D-7、D-8、D-10 不在本项目动手：D-3/D-10 已了结，D-5 当前影响为零且
合并两层口径是口径决策（并入 D-4），D-7 是采集纪律不是盘点问题，D-8 无调用点。

---

## 三、指标模型与分类口径

### 3.1 三分法的机械判据

分类不能靠感觉，否则同一字段今天记直取明天记派生。

| 类型 | 判据 | 例 |
|---|---|---|
| **DIRECT 直取** | 能指到唯一 `(source_id, field)`；本地代码**无条件分支**，最多一次单位换算纯乘法 | `tushare_daily.close` ← `tushare.daily`.`close` |
| **DERIVED 派生** | 由库内已有字段按确定公式算出，可完全重算，**无自由参数** | `daily_flags.is_limit_up` = `close >= limit_up` |
| **FACTOR 因子** | 含**窗口/参数**或**口径选择** | `market_regime.market_advance_ratio`（含股票池口径）；`index_ma20_slope_5d`（MA20 + 5 日） |

一句话：直取看能否指到源字段；派生看有无自由参数；有自由参数或口径选择的是因子。

**这条线为什么这么划**：D-3 的教训是 `market_advance_ratio` 公式从来没错，
错的是分母那个股票池。归成「派生」则验证只会核公式，永远查不出来；
归成「因子」，验证被迫去核口径。

**判据的反向作用**：抽不出 `(source_id, field)` 映射、代码里有条件分支的字段，
**本来就不该叫直取**，归 DERIVED。预计 `daily_flags.adj_prev_close` 一类会被重新归类。

### 3.2 `validity`：正交的有效性语义

三分法盖不住一个真实存在的东西。`name_snapshots.name` 是 `stock_basic.name` 原样落库，
按判据是 DIRECT，但它**只对取快照那一天成立**，拿去填历史就是结转。

故每条指标加一个正交必填属性：

| validity | 含义 | 例 |
|---|---|---|
| `SESSION` | 该交易日成立 | `tushare_daily.close` |
| `POINT` | **仅观测日成立** | `name_snapshots.name`、`instruments.*` |
| `SPAN` | 区间内成立 | `name_history` 的名称区间 |

这一栏就是「有效性」在字典里的落点，页面直接显示。谁想拿 `POINT` 的值填历史，
字典先一步拦住。

### 3.3 Metric 的登记内容

```
Metric
  id            "daily_flags.is_first_board"
  kind          DIRECT | DERIVED | FACTOR
  validity      SESSION | POINT | SPAN
  status        ACTIVE | FROZEN | OPERATIONAL
  meaning       一句中文
  lineage       DIRECT:        (source_id, field, transform)
                DERIVED/FACTOR: (上游 metric id 列表, 公式, 参数)
  known_issues  ("D-1", ...)
  checks        挂的规则 id；空集不允许，须写 UNCOVERED("理由")
  ext_consumers 非指标消费方（策略 Gate / 回测 / 页面），显式声明
```

`consumers`（下游哪些**指标**在吃它）**不是登记字段**，而是由全库 `lineage`
反向推导出的计算属性——手写的双向关系必然漂移。只有非指标消费方无法从血缘推出，
故单列 `ext_consumers` 手写。

设这个属性是为了让页面能回答「这个指标坏了会影响什么」。换源第 2 段实测 13 个
生态因子在 86%~100% 的交易日上全部变化，当时要靠人去追谁受影响。

### 3.4 类型与可信度是两件事

`adj_factors.cum_factor` 会登记为 **DIRECT**（Task 9 后取自 `factor`），
同时 `known_issues=("D-1",)`——源字段自身不自洽，非股改期 688 条偏差。

字典里会出现「血缘清楚、类型明确、但已知不可信」的条目。这是对的：
**类型说的是它怎么来的，可信度说的是它对不对。** 页面分两栏显示。

---

## 四、源模型

### 4.1 源是一等实体

`tushare_daily` 那 28 列不来自同一个接口：9 列来自 `daily`，15 列来自 `daily_basic`，
1 列来自 `adj_factor`，`name` 恒 null 实由 `namechange` 另表供给。到货时点也不同
（D-12 实测 `limit_list_d` 17:02 为空、19:00 才有，而 `daily` 17:02 已齐）。

所以「来源＝Tushare」这种粒度会把真问题盖掉。新增 `catalog/sources.py`：

```
Source
  id           "tushare.daily_basic"
  provider     "Tushare Pro"
  api          "daily_basic"
  access       API | FILE | REALTIME    自动接口 / 人工转存文件 / 实时自采
  credential   凭据位置，如 config/deploy.local.toml [tushare]
  tier         权限门槛，如 "2000 积分"
  acquired     取得方式与到期，如 "2026-09-13 购入 ¥500/年，积分到期回收"
  status       ACTIVE | DEAD | FROZEN
  cadence      到货节奏，如 "工作日 17:00 + 19:00"
  caveat       已知的时点/口径坑
```

**注意 `Source.status` 与 `Metric.status` 是两个独立枚举**，取值部分同名但语义不同：

| | 取值 | 含义 |
|---|---|---|
| `Metric.status` | `ACTIVE` / `FROZEN` / `OPERATIONAL` | 这个**指标**是在产、已冻结、还是运行时日志 |
| `Source.status` | `ACTIVE` / `DEAD` / `FROZEN` | 这个**源**还能不能取到新数据 |

两者不可互相赋值，`types.py` 里定义成两个 `StrEnum`。
`bars_daily.*` 是 `Metric.status=FROZEN` 且其源 `mootdx.kline` 是
`Source.status=DEAD`——正是这两个枚举必须分开的原因：指标冻结是我们的决定，
源死亡是上游的事实。

DIRECT 指标引用 `source_id`，不再是松散元组。连带好处：T3 对账更干净；
C5 凭据过期直接查 `acquired`；按源视图成为可能。

### 4.2 全库源清单（24 个）

| provider | 接口 | status | access |
|---|---|---|---|
| Tushare Pro | `daily` `daily_basic` `adj_factor` `stk_limit` `suspend_d` `limit_list_d` `index_daily` `stock_basic` `namechange` `trade_cal` | ACTIVE | API |
| akshare | 交易日历、A股代码名、沪市退市、深市退市、现货快照、分红送转 | ACTIVE | API（免费） |
| QMT | 分钟线、tick（竞价）、股本、分红配股 | ACTIVE | REALTIME 自采 / 按需全量 |
| 百度网盘付费源 | 日线、分钟 | FROZEN | FILE，人工转存 |
| mootdx | K 线 | **DEAD**（2026-09-10 起返回空） | API |
| qsys | runtime（系统自身运行记录） | ACTIVE | API（内部） |

---

## 五、Registry 与守护测试

### 5.1 位置与拆分

新建 `src/qsys/data/catalog/`：

```
types.py          Metric / Source 数据类 + 枚举（Kind / Validity / Status / Access）
sources.py        24 个源实体
market.py         tushare_daily(28) bars_daily(8) paid_daily(28)
                  index_bars(8) minute_bars(8) auction_ticks(10)        约 90 条
reference.py      instruments(7) name_history(6) name_snapshots(3)
                  price_limits_daily(5) suspend_daily(4) limit_list_daily(9)
                  corporate_actions(6) share_capital(5) adj_factors(4)  约 49 条
derived.py        daily_flags(14) valuation_snapshot(6)                    20 条
factors.py        market_regime(15) + 策略侧 10 个                          25 条
operational.py    live_equity/fills/rejects/recon(25) ingest_log(7)         32 条
__init__.py       汇总 REGISTRY: dict[str, Metric]，对外只暴露它与 SOURCES
```

五个条目文件各 20–90 条。不放一个文件：2000 行的 registry 没人读，没人读的字典会烂。

### 5.2 六条守护测试

registry 不腐烂的全部依靠。逐条对应一种真实事故：

**T1 完备性**——registry 的落盘条目集合**恰好等于** `schema.ALL` 展开 + `PAID_DAILY`
展开（206 条）。缺一个失败，多一个也失败。
> 拦：往 schema 加列忘登记，页面少一个指标而无人知晓。

**T2 血缘图可解析且无环**——DERIVED/FACTOR 的上游必须是 registry 中真实存在的 id；
DIRECT **不得**有上游。
> 拦：血缘写错字；把其实有上游的字段谎报成直取。

**T3 直取声明与 pipeline 实现同源**——registry 直接 `import`
`tushare_daily._FROM_DAILY` / `_FROM_BASIC`，断言两边 `(仓库列, 源列, 乘数)` 逐条相等。
`tushare_daily` 28 列中 24 列由这两张表覆盖；其余 4 列
（`symbol`←`ts_code`、`dt`←`trade_date`、`factor`←`adj_factor`、`name`=null 字面量）
单独登记并单独断言。
> 拦：改了 pipeline 的乘数（如 `amount` 的 ×1000）而字典仍写旧值。
> 这类错在数值上「看起来正常」，只有字典对账能发现。

**T4 consumers 由血缘反向推导**，不手写。

**T5 每条指标必须挂规则或显式声明未覆盖**——`checks=()` 不允许，
必须写 `checks=UNCOVERED("理由")`。
> 拦：指标进了字典但无人验证，还因在字典里而显得可信。

**T6 冻结表不可写**——`status=FROZEN` 的表断言不在 `PARTITIONED` / `UNPARTITIONED` 中。
> 把 `schema.py` 注释里「不要注册进 PARTITIONED」从注释升级成测试。

### 5.3 填写纪律

206 条含义逐条写是本项目最重的一块。规矩必须先定死，否则产出一份
**读起来专业但有一部分是编的**字典——那比没有字典危险。

`meaning` 与 `lineage` 必须来自三种依据之一：

1. `schema.py` / pipeline 的既有注释；
2. `docs/reference/` 下的既有文档；
3. 读 pipeline 实现代码确认。

**三者皆无 → 写 `meaning=UNKNOWN(待查)` 并进待查清单，不准编一句听起来合理的中文。**
页面上 `UNKNOWN` 标红。

这与「只许直取或纯派生，拿不到就记 null」是同一条规矩，对象从数据换成字典本身。

---

## 六、验证器与规则

### 6.1 复用现有框架

`src/qsys/data/quality/` 已有 `Violation` / `Severity(BLOCKING|WARNING)` /
`QualityReport.assert_usable()`，以及 `find_holes`、`minute_rules`、
`source_diff`、`tushare_reconcile`。**保留并扩展，不替换。**

唯一要加的是桥接层：现有规则是**表级**，新规则要能挂到**指标 id**。
新增 `catalog/checks.py` 定义 Check（每条声明覆盖哪些 metric id），
runner 把结果落成按 metric_id 索引的行。

### 6.2 A 类 · 真实性

**A1 单位量纲反查**——乘数错了数值上「看起来正常」，只有恒等式能抓。
如 `amount / (volume × close) ≈ 100`（手→股），错一个 ×1000 差三个数量级。
对 `tushare_daily` 的 9 个有量纲列逐一建。

**A2 派生公式独立重算**——**不准调用生产函数来验生产函数**。
按 registry 登记的公式用最朴素方式重新实现，两条独立路径比对。
覆盖 `daily_flags` 与 `valuation_snapshot` 的 20 条。

**A3 跨表恒等式**（最有价值，且全是跨表）：

| 恒等式 | 抓什么 |
|---|---|
| 分钟聚合 = 日线 OHLCV | 两独立源同时错得一样的概率很低（复用 `minute_rules`） |
| 自算涨跌停 vs `price_limits_daily` | 板块/ST 档位判错 |
| `daily_flags.is_limit_up` vs `limit_list_daily` | 严格包含，**差集应恰为当日 ST 涨停集**（D-11 实测 19 只全 ST） |
| `market_cap ÷ close` vs `total_shares` | 反算污染（旧源 A+H 股差 1.91 亿股） |
| `pct_change` vs `close/pre_close − 1`；`change` vs `close − pre_close` | 源内自洽 |
| `turnover_ratio` vs `volume×100 ÷ circulating_shares` | 估值列与行情列对不上 |
| **`pre_close` 缺口非零而 `factor` 不跳** | **D-1 的漏除权**；文档原话「这条应该做成质检规则，不是一次性排查」 |

**A4 第三方抽样清单**——按可疑度排序生成待验清单（D-1 的 688 条 + D-2 的 920 号段
+ 本轮 A3 失败行），导出为可直接在同花顺比对的格式。**人工结论落盘入库**
（见 7.3），成为该指标的长期证据；下次盘点不必重验同一只股。

### 6.3 B 类 · 完整性

- **B1 交易日历对齐**——各日频表 `dt` 集合 vs `AShareCalendar`。
- **B2 内部空洞**——复用 `find_holes`（旧源实测 5,008 个洞；换 Tushare 后需重测基线）。
- **B3 null 率，双口径**——按行数 **和** 按标的。D-10 的教训是 10% 的全窗口行数闸门
  挡不住 1.73% 的系统性缺口，文档明确建议改按标的判；两个口径都算、都设基线、突变告警。
- **B4 每日到货**——对齐 `ingest_log`。含专门规则：**全市场一天零涨停视为失败**
  （D-12 原话「不要因为『涨跌停列表为空是常态』就放过」）。
- **B5 覆盖区间**——每个指标首末日期与预期对比。

### 6.4 C 类 · 有效性

**C1 非法填充扫描（红线）**——静态扫描数据与因子链路上每一处
`fill_null(` / `fillna(` / `or 0` / `.get(k, 0)`，**逐处要求 registry 有豁免登记**，
说明为何该默认值是真值而非填充。未登记即失败。
> 直接抓 D-9 的 `feed.py:85` `fill_null(1.0)`——新因子语义下同窗口可达 2.40–3.48，
> 填 1.0 等于把价格打到几分之一。

**C2 PIT 语义不被越界**——`validity=POINT` 的指标必须按 `(symbol, dt)` 精确连接，
不得 as-of / forward-fill。做成对 pipeline 的模式检查 + 一条数据检查
（构造一个 `paid_daily` 无覆盖的交易日，看名称是否仍能取到且不蔓延）。

**C3 `validity=SPAN` 的区间完整性**——逐标的检查区间是否连续覆盖至今，
报空洞与无后继。`name_history` 的 2006 断层正是此形态。

**C4 冻结表未被写入**——`paid_daily` / `bars_daily` 行数与内容指纹不变。

**C5 凭据过期**——Tushare 积分一年有效、到期回收，到期即日线源中断。
按 `Source.acquired` 提前告警。

### 6.5 严重度：已知缺陷不重复阻断

沿用 BLOCKING / WARNING 两档，加一条机制：

**规则失败时，若该指标的 `known_issues` 已登记对应 D-x 编号，则降级为 WARNING，
并在页面标注「已知缺陷 D-x」；未登记的新失败才 BLOCKING。**

理由：D-1 现在就是失败状态且短期修不了（要等第三方验真）。若直接 BLOCKING，
`assert_usable()` 会让回测天天罢工，最终必然被注释掉——整套闸门作废。
此设计让**新的退化挡住流程，旧账不影响日常**，同时旧账在页面上一直挂着不被遗忘。

### 6.6 运行节奏

- **增量**（当日，秒级）：接在 17:00 / 19:00 两次 sync 之后。B 类 + A3 的当日切片。
- **全量**（全历史，重）：手动或每周触发。A 类全部 + B2 + C3。

全量**必须逐年处理**——全历史两表同载约 8.2 GiB 而生产节点仅 7.5 GiB，
2026-09-13 已被 OOM killer 杀过一次。仓库各处（`universe.py` / `flags.py` /
`tushare_reconcile.py`）均为逐年模式，此处沿用。

---

## 七、结果存储

### 7.1 落 warehouse

`ingest_log` 与 `live_*` 同样不是行情数据且已在 warehouse 中，先例成立。
好处是 `WarehouseQuery` / `coverage()` / 页面全套现成，且与数据同在生产节点。

新增两张表，注册进 `schema.PARTITIONED`。

### 7.2 `metric_checks`——自动验证结果

```
run_id        string      一次运行的标识
checked_at    timestamp   
metric_id     string      "daily_flags.is_first_board"
check_id      string      "A3.limit_list_vs_selfcalc"
category      string      truthfulness / completeness / validity
scope_start   date32      本次检查覆盖的数据区间（不适用为 null）
scope_end     date32
passed        bool
severity      string      blocking / warning（6.5 降级后的实际值）
known_issue   string      "D-1"，无则 null
violations    int64       违规行数
checked_rows  int64       分母
detail        string      简短说明
sample_path   string      违规样本 parquet 路径，无则 null
```

三条设计约束：

**`checked_rows` 必填。** 这个项目吃过的亏原话是「只报一个总数，就无法区分
『已知的口径差』与『真的错了』」。没有分母的 `violations=1917` 不构成信息。

**违规样本不进表。** 单规则可命中几万行（D-1 是 1,917 条，因子分歧达 320 万行）。
样本落 `artifacts/metric_checks/<run_id>/<check_id>.parquet`，表中仅存路径。

**「没跑」必须与「跑了通过」可区分。** 前者是**表里没有那一行**，不是 `passed=true`。
页面分开显示。这与 T5 的 `UNCOVERED` 成对：T5 管静态（没挂规则），此处管动态（没跑成）。

### 7.3 `metric_evidence`——人工与第三方抽样结论

```
metric_id         string
symbol            string
dt                date32
our_value         string      我们库里的值，原样文本
reference_value   string      第三方的值，原样文本
reference_source  string      "同花顺" / "东财"
verdict           string      match / mismatch / inconclusive
checked_by        string
checked_at        timestamp
note              string
```

**必须与 `metric_checks` 物理分开**：自动结果每轮重跑追加，人工结论是
**永久证据，绝不能被任何自动流程覆盖**。一次录入永久生效，这正是 D-1 现在缺的东西。

两个值存 **string 原样文本**，不转 float：指标含 float/bool/string/date 各类，
统一转换必在某处丢精度；且容差由人定，`verdict` 由人给。

### 7.4 追加不压缩

同一 `(metric_id, check_id)` 重跑**纯追加**——要能回答「这个指标从哪天开始坏的」，
D-4 重标定也需要这条时间线。

体量：增量每日约 500 行 × 250 交易日 + 全量每周约 7,000 行 × 52
≈ **不到 50 万行/年，约 100 MB**。不做压缩或保留策略。

页面默认取每个 `(metric_id, check_id)` 的最新一行；详情页展示历史时间线。

### 7.5 写入纪律

**验证器自身崩溃时必须写 `passed=false, detail="runner error: ..."`，不得静默跳过。**

抄的是已交过学费的教训——空结果写 `ingest_log` 会让上游故障被当成「全市场已完成」
而永不回补。此处的对应形态是：挂掉的规则若什么都不写，在页面上与「今天没安排跑」
无法区分，一条坏掉的闸门可静默失效数月。

### 7.6 副作用

`coverage()` 遍历 `schema.ALL`，新表会自动出现在现有「数据覆盖」页。
这是对的——验证结果本身也该被覆盖统计看见。

---

## 八、数据层页面

### 8.1 服务层

新建 `src/qsys/web/catalog_views.py` 的 `CatalogService`，**不塞进 `DataService`**。
`DataService` 三块全是 warehouse 查询，而 catalog 的字典部分来自 import registry、
结果部分才来自 warehouse；混在一起以后分不清哪个方法会碰磁盘。

### 8.2 导航

`base.html` 的 data nav 加一项，置于第二位：

```
数据覆盖 → 数据层 → 选股结果 → 市场生态 → 回测运行
```

顺序逻辑：有多少（覆盖）→ 是什么、对不对（数据层）→ 后三者是数据的用法。

### 8.3 总览页 `/data/catalog`

**KPI 行**：指标总数 · 直取/派生/因子三个数 · 规则覆盖率 · 待处理数 · 最近验证时间

两个 KPI 的定义写死，避免各处口径不一：

- **规则覆盖率** = `挂了规则且最近一轮真的跑出了结果的指标数 ÷ 指标总数`。
  分母含策略侧 10 个；挂了规则但本轮没跑成的**不计入分子**（对应 7.2 的
  「没跑 ≠ 通过」）。
- **待处理数** = `失败 + 含义待查 + 未覆盖` 三格之和。**不含**「已知缺陷」——
  后者已挂账有归属，混进来会让这个数永远降不到零而失去意义。

**主表**：指标 | 表 | 类型 | 有效性 | 含义 | 来源 | 状态

状态 pill 五取值：

| pill | 含义 | 样式 |
|---|---|---|
| 通过 | 有规则、跑过、全过 | `.pill.ok` |
| 失败 | 新的 BLOCKING 失败 | `.pill.bad` |
| 已知缺陷 D-x | 挂了账，按 6.5 降级 | `.pill.warn` |
| 未覆盖 | registry 中为 `UNCOVERED`，压根没挂规则 | 新增灰色 pill |
| 含义待查 | `meaning=UNKNOWN`，我们不知道这列是什么 | 新增灰色 pill |

**后两个是本页最该突出的东西。** 目标是「清楚知道」，那就必须包括
「哪些我们还不清楚」。一个全绿但悄悄漏掉几十个没人验过的字段的页面，
比没有页面更糟。

**默认排序按该看的程度**，不按字母：失败 → 含义待查 → 未覆盖 → 已知缺陷 → 通过。

**过滤**：按表 / 类型 / 状态，走 query param 服务端渲染，零 JS——
与 `/data/selection` 的 `?tag=` 同一写法。

### 8.4 来源显示到接口级与字段级

主表「来源」列不显示 provider 级：

```
tushare_daily.amount    DIRECT   Tushare·daily.amount ×1000
tushare_daily.pe_ttm    DIRECT   Tushare·daily_basic.pe_ttm
adj_factors.cum_factor  DIRECT   Tushare·adj_factor.adj_factor    ⚠ D-1
name_snapshots.name     DIRECT   Tushare·stock_basic.name  (POINT)
```

派生/因子显示**根源**——沿血缘图走到 DIRECT 叶子，自动算出根源集合，不手写：

```
daily_flags.is_first_board   DERIVED
  根源: Tushare·daily.close / daily.pre_close
        Tushare·namechange + paid_daily.name + stock_basic.name（四级名称解析）
```

### 8.5 详情页 `/data/catalog/{metric_id}`

- 头部：id + 类型/有效性/状态三徽章
- 含义
- **血缘三栏**：上游 → 本指标 → 下游消费方，每项可点击跳转。
  DIRECT 显示 `(source_id, field, transform)`；DERIVED/FACTOR 显示公式与参数。
  **不画 SVG 图**——血缘最深 3–4 层，三栏列表足够；`svg.py` 现只有 `line_chart`，
  为此加一套图布局不划算。
- 验证规则清单 + 每条最新结果（`violations / checked_rows`、样本 parquet 下载，
  复用 `/backtests/{name}/file/` 的 `Content-Disposition` 下载）
- 规则历史：`violations` 随时间走势，复用 `line_chart`
- 已知缺陷：D-x 编号 + 链到 `deferred-corrections.md` 对应段
- 人工证据：`metric_evidence` 中该指标的全部记录

### 8.6 按源看 `/data/catalog/sources`

反向问题：**这个源挂了会影响什么？**

每源一行：状态 / 获取方式 / 门槛 / 到货节奏 / **支撑多少指标** / 展开看是哪些。

回答现在无处可答的问题：Tushare 积分到期会打掉几个指标？mootdx 死掉时哪些指标
跟着冻结？QMT 断一天永久缺的是哪几个？

### 8.7 硬性质

**验证从未跑过时本页必须照常打开**，显示完整字典 + 全部标「未验证」，不得 500。

`metric_checks` 为空是常态（新部署、新指标、验证器未接调度）。字典本身即有价值，
不能因结果表空而整页打不开。现有 `DataService` 处处对空数据返回 empty，沿用。

---

## 九、验收标准

1. **T1–T6 六条守护测试全绿**，且 T1 断言的条目数恰为 206。
2. **策略侧 10 个决策量全部登记**，血缘可追溯到落盘指标。
3. **`meaning=UNKNOWN` 的条目有明确清单**，数量记录在案（允许非零，但必须可见）。
4. **`UNCOVERED` 的条目有明确清单**，每条带理由。
5. **A/B/C 三类规则至少各有一条在生产数据上实跑出结果**，写入 `metric_checks`。
6. **C1 扫出 `feed.py:85`** 并按 D-9 原定三步处理完毕。
7. **A3 的漏除权规则复现 D-1 记录的 12 只标的**（`000430.SZ` 等）——
   复现不了说明规则写错了，不是数据变好了。
8. **A3 的 `limit_list` 口径规则复现 D-11 的差集全为 ST**。
9. **页面三条路由可访问**，且在 `metric_checks` 为空时正常渲染。
10. **A4 生成的抽样清单可导出**，且 `metric_evidence` 至少录入一条人工结论走通闭环。

## 十、风险

**最大风险是 5.3 的填写纪律被稀释。** 206 条含义逐条写、每条都要有依据，
过程枯燥且没有即时反馈，很容易滑向「照着列名编一句通顺的中文」。
一旦发生，字典会变成一份自带权威感的错误信息源，危害大于没有字典。

缓解：`UNKNOWN` 是一等公民，在页面上和「失败」同样显眼。写 `UNKNOWN` 是合规行为，
编一句才是违规。实施计划里每批条目的评审要专门抽查依据。

**第二风险是规则写成「自己验自己」。** A2 明令禁止调用生产函数验生产函数，
但实施时调用一个看似无关的 helper 就可能间接绕回去。
实施计划里每条 A2 规则需标注它的独立实现路径。

## 十一、非目标

- 不做 D-4 的 S6/S7 阈值重标定（研究任务，另起）。
- 不盘回测绩效指标与成本假设。
- 不重构 `quality/` 现有框架，只加桥接层。
- 不为血缘画 SVG 图。
- 不做结果表的压缩与保留策略。
