# 聚宽真值数据源接入 —— 设计

> **已作废（2026-09-11）**：真值源改为百度网盘付费数据，本方案的聚宽采集部分
> （Task 1-6、8、9）不再执行。消费侧改造（Task 7、10-14）的目标仍然成立，
> 已并入 `docs/superpowers/specs/2026-09-12-paid-daily-truth-source-design.md`，
> 并已由该 spec 的实施计划
> `docs/superpowers/plans/2026-09-13-paid-daily-truth-source.md` 完成。
>
> **2026-09-13 再次变更**：日线源进一步改为 Tushare Pro API 直连
> （付费源实测即 Tushare 转售），见
> `docs/superpowers/plans/2026-09-13-tushare-ingestion.md`。

**日期**：2026-09-11
**状态**：设计已审，待实施计划

## 一、目标与非目标

**目标**：让仓库里每一个数据字段，要么是聚宽接口直接返回的值，要么是这些值的
纯算术派生。生产路径上**不存在任何推断、猜测、近似或填充**。

达成后消失的四个坑（均出自 `docs/reference/joinquant-alignment.md` 第六节）：

| 坑 | 现状 | 真值化后 |
|---|---|---|
| ST 判定 | 三条启发式规则从价格反推，近窗 Gate 一致率 93.8% | `get_extras('is_st')` 真值 |
| 涨跌停价 | 自己按除权除息参考价算 | `get_price` 的 `high_limit/low_limit` |
| 停牌 | 靠「四价等于前收 + 量为 0」判断 | `get_price` 的 `paused` |
| 退市股 | mootdx 无、QMT 返回四价全 0 的空壳，广度池平均少 14.6 只 | `get_all_securities(date=None)` 含退市 |

**非目标**：
- 不改策略逻辑。本次只换数据来源，策略行为变化单独立项（见 §8 的行为变化条目）。
- 不追求让 QMT 与聚宽两份数据对齐。QMT 从此只负责下单与接缝后的每日真值兜底。
- 不做全市场分钟线。量级是日线的 240 倍，额度上不成立。

## 二、第一硬约束：禁止编造数据

这是本设计的最高优先级条款，凌驾于性能、简洁、工期之上。

### 2.1 两个会污染数据的 API 默认值

从 `jqdatasdk-1.9.8/jqdatasdk/api.py:27` 的 docstring 原文确认：

| 参数 | 默认值 | 默认行为 | 本项目必须取值 |
|---|---|---|---|
| `fill_paused` | `True` | 「True表示用close价格填充」——**停牌日被填上停牌前的价格** | **`False`**（停牌日为 NaN） |
| `skip_paused` | `False` | 跳过时丢掉停牌行；不跳过时「停牌时会使用停牌前的数据填充」 | **`False`**，配合 `fill_paused=False` 保住行、只丢价格 |
| `fq` | `'pre'` | 前复权到请求日，落盘即腐烂 | **`None`**（原始价，复权交给 `factor` 列） |

`fill_paused=True` 造出来的正是 2026-05-06 那类污染：停牌股带着前一日的价格进入当日
截面。那次是聚宽策略侧的 `_s5_market_raw_factors` 犯的（`cumcount` 不校验日期），
但同样的语义在 `get_price` 的默认参数里就有。**采集器必须显式写全这三个参数，
并有一条测试断言它们的取值**，不能依赖默认。

### 2.2 字段血缘：每一列都要能回答「这个数从哪来」

仓库每一列必须落在两类之一，第三类不存在：

1. **直取**：某个聚宽接口的某个字段，原值落盘，不做任何变换（除类型转换）。
2. **纯派生**：由直取列经确定性算术得出，公式写在代码注释与本文档里，可逐位重算。

禁止出现：启发式规则、阈值猜测、"大概是"、缺失值填充、近似替代。
拿不到的数据就是拿不到——记为 null，由质检报出来，**不准填**。

### 2.3 可抽样验真

需求方会随机抽样与同花顺比对。因此交付物包含 `scripts/sample_verify.py`：
随机抽 N 个 `(symbol, dt)`，把该股-日的全部直取字段以人眼可读的表格打印出来
（含 `open/high/low/close/volume/amount/pre_close/high_limit/low_limit/paused/is_st/factor`），
并标注每一列的来源接口。抽样种子可固定，便于复现同一批样本。

## 三、机器实况（2026-09-11 实测）

```
Ubuntu · Linux 6.8.0 · x86_64
4 核 Intel Xeon Platinum 8255C @ 2.50GHz
内存 7.5 GB（可用 5.6 GB）· swap 9.9 GB
磁盘 118 GB（可用 82 GB）
```

warehouse 现 433 MB：`minute_bars` 183M / `valuation_snapshot` 123M /
`bars_daily` 82M / `daily_flags` 42M / 其余 < 3M。

与 `docs/deploy/linux-core.md` 记录的「1.9 GB 内存 / 40 GB 磁盘」已不同——那份
文档本次一并更新。**原先「内存已触顶」的约束解除**：批次可放宽到 1000 只 × 1 年
（约 24 万行、30 MB）。

**采集全部跑在 Linux 节点**，数据终点在此，避免 GB 级回传。Windows 侧只保留
QMT 下单，以及接缝后的当日真值归档（§9）。

**必须避开 15:40 的每日归档窗口**：2026-09-10 实测并行跑三个回测时，日线增量从
6 分钟劣化到 335 分钟（慢 55 倍）。采集器要能被信号中断并从断点续传。

## 四、数据源与接口

全部依据 `jqdatasdk-1.9.8` 的源码 docstring（PyPI wheel，非记忆）。行号即该 wheel 内
`jqdatasdk/api.py` / `table.py` 的位置。

| 用途 | 接口 | 源码 | 关键点 |
|---|---|---|---|
| 标的主表（含退市） | `get_all_securities(types=['stock'], date=None)` | `api.py:397` | 「默认值为 None, 表示获取所有日期的股票信息」 |
| 日线 + 真值字段 | `get_price(..., fields=[...], fq=None, skip_paused=False, fill_paused=False, panel=False)` | `api.py:27` | fields 可选 `open/close/low/high/volume/money/factor/high_limit/low_limit/avg/pre_close/paused` |
| ST 真值 | `get_extras('is_st', security_list, start_date, end_date)` | `api.py:70` | `info` 第一个可选值即 `'is_st'` |
| 分红送配 | `finance.run_offset_query(query(...))` | `table.py:60` | 单次 `run_query` 上限 5000 行；`run_offset_query` 分页至 20 万行 |
| 集合竞价 | `get_call_auction(security, start_date, end_date, fields)` | `api.py:981` | 支持标的**列表** + 日期区间；五档量价 |
| 交易日历 | `get_trade_days(start_date, end_date)` | `api.py:482` | |
| 额度 | `get_query_count(field=None)` | `api.py:902` | 返回当日可请求条数 / 剩余条数 |
| 分钟线 | `get_price(..., frequency='1m')` | `api.py:27` | 同一接口，只换 frequency |

**代码后缀**：聚宽用 `.XSHE`（深）/ `.XSHG`（沪），本仓库用 `.SZ` / `.SH`
（`api.py:666`、`utils.py:405/414` 确认）。映射用**本地纯函数**，不调
`normalize_code`——那是一次服务端请求，会耗额度。北交所后缀属未知项，见 §7。

## 五、仓库表结构

`bars_daily` 的 8 列 schema **不动**。它有 76 处引用、分布在 30 个文件，且
`Warehouse.write` 做严格 schema 相等校验，加列会使所有测试夹具失效。数据全量由
聚宽重建，schema 保持原样。

新增两张事实表：

```python
DAILY_REFERENCE = pa.schema([
    ("symbol",     pa.string()),
    ("dt",         pa.date32()),
    ("pre_close",  pa.float64()),   # 直取
    ("high_limit", pa.float64()),   # 直取
    ("low_limit",  pa.float64()),   # 直取
    ("paused",     pa.bool_()),     # 直取
    ("factor",     pa.float64()),   # 直取（聚宽后复权因子）
    ("avg",        pa.float64()),   # 直取（当日均价）
])

ST_DAILY = pa.schema([
    ("symbol", pa.string()),
    ("dt",     pa.date32()),
    ("is_st",  pa.bool_()),         # 直取
])
```

**为什么是两张而不是一张**：`get_price` 与 `get_extras` 是两条独立通道，采集进度
必须能各自断点续传。三个月试用期里这条比省一次 join 重要。

`daily_flags` 的变化：

| 列 | 变化 |
|---|---|
| `limit_up` / `limit_down` | 保留列名，值改为 join `daily_reference` 的真值。**下游 76 处引用零改动** |
| `prev_close` | 值改为 `daily_reference.pre_close`（真值） |
| `limit_pct` | 纯派生：`high_limit / pre_close - 1`，四舍五入到 4 位 |
| `is_st` | **新增**，来自 `st_daily` |
| `adj_prev_close` | **删除**（除权参考价是自算的中间量，真值化后无意义） |
| `st_inferred_raw` / `st_inference_rejected_by_price_evidence` / `st_inferred_final` | **删除**（推断的审计痕迹） |

## 六、字段血缘表

服务 §2.3 的抽样验真。每一列可回答「从哪来」。

| 表.列 | 类型 | 来源 / 公式 |
|---|---|---|
| `bars_daily.open/high/low/close` | 直取 | `get_price(fq=None)` 同名字段 |
| `bars_daily.volume` | 直取 | `get_price` 的 `volume`（**单位必须实测确认，见 §11.1——这是本次最易造成静默污染的一处**） |
| `bars_daily.amount` | 直取 | `get_price` 的 `money` |
| `daily_reference.*` | 直取 | 见 §5 表内注释 |
| `st_daily.is_st` | 直取 | `get_extras('is_st')` |
| `adj_factors.cum_factor` | 直取 | `daily_reference.factor` |
| `adj_factors.cum_share_factor` | 纯派生 | `(1 + stock_per_share + rights_per_share)` 的累积连乘 |
| `corporate_actions.*` | 直取 | `finance` 分红送配表（列名见 §7 探针） |
| `daily_flags.prev_close` | 直取 | `daily_reference.pre_close` |
| `daily_flags.limit_up` / `limit_down` | 直取 | `daily_reference.high_limit` / `low_limit` |
| `daily_flags.is_st` | 直取 | `st_daily.is_st` |
| `daily_flags.limit_pct` | 纯派生 | `round(high_limit / pre_close - 1, 4)` |
| `daily_flags.is_limit_up` | 纯派生 | `close >= high_limit` |
| `daily_flags.touched_limit_up` | 纯派生 | `high >= high_limit` |
| `daily_flags.is_broken_board` | 纯派生 | `touched_limit_up and not is_limit_up` |
| `daily_flags.is_first_board` | 纯派生 | `is_limit_up and not is_limit_up.shift(1)` |
| `market_regime.*` | 纯派生 | 原公式不变，输入换成真值列 |

## 七、探针先行：未知项清单

以下事项**没有权威依据**，禁止预设。实施第一步是 `scripts/jq_probe.py`，逐项实测，
结果落 `var/jq_probe_<date>.json` 并写入 `docs/reference/joinquant-data-source.md`。
所有额度预算与批次大小以探针结果为输入。

1. **额度**：`get_query_count()` 的 total / spare；试用剩余天数。
2. **计费口径**：用 `spare` 夹一个 1 只 × 10 天 × 12 字段的小请求做差值，确认按行
   还是按行×字段计。这一项决定 §10 的全部预算。
3. **`get_all_securities` 的列名**与含退市股的总数（源码只写 `:return pandas.DataFrame`）。
4. **日线最早可得日期**（含退市股）。
5. **北交所代码后缀**（`.BJ` 对应聚宽的什么）。
6. **`finance` 分红送配表的表名与列名**：财经表由服务端 `get_table_orm` 动态下发，
   用 `get_table_info` 打印核对。
7. **`get_call_auction` 单个股-日实际返回行数**（现有 QMT 侧实测为 126 笔/股-日，
   聚宽侧未知）与最早可得日期。
8. **`paused=True` 时各字段的实际返回值**（在 `fill_paused=False` 下是否全 NaN、
   `high_limit/low_limit` 是否仍有值）。这一项直接关系 §2 的「不准填」如何落地。
9. **日线 `volume` 的单位**（股 / 手）。判据不靠文档也不靠猜：对同一批 bar 分别按
   「股」和「手」两种解释计算 `amount / volume` 与 `amount / (volume * 100)`，看哪
   一种落在 `[low, high]` 区间内。现有 mootdx 数据上这个方法给出过 99.88% 对
   0% 的明确结论，可直接复用。详见 §11.1。

## 八、采集范围与顺序

| 层 | 范围 | 估算行数 | 估算磁盘 | 依据 |
|---|---|---|---|---|
| 日线 + `daily_reference` | **全历史**（起点由探针定） | 约 1,800 万 × 2 | 约 750 MB | 现有 450 万行 = 82 MB 外推 |
| `st_daily` | 同上 | 约 1,800 万 | 约 60 MB | 单 bool 列 |
| 分红送配 | 全历史 | 约 5 万 | < 5 MB | 现 332 KB |
| 候选股集合竞价 | 先近 3 年 | 约 570 万 | 约 20 MB | 现 424 KB / 20 天外推 |
| 候选股分钟线 | 先近 3 年 | 约 1,100 万 | 约 550 MB | 现 183 MB / 约 1 年外推 |

合计约 1.4 GB 增量，对 82 GB 可用磁盘无压力。额度合计约 5,300 万条，摊到三个月
约 59 万条/天——可行性待探针第 1、2 项确认。

**顺序按「过期就永久拿不到」排**：探针 → 日线全历史 → `is_st` → 分红送配 →
集合竞价 → 分钟线。前四项拿到就已消灭全部四个坑；后两项是把「不能消除」的历史
竞价口径变成可得。

**日线拿全历史、分钟线只拿近 3 年**不是为省磁盘，是因为分钟线量级为日线的 240 倍，
全历史全市场在额度上不成立。

## 九、采集器设计

新增 `src/qsys/data/providers/joinquant_provider.py`，与现有 `mootdx_provider` /
`akshare_provider` 同一模式（依赖注入、可用假 client 测试）。

**分批**：`(1000 只 × 1 年)` 为一批，约 24 万行 / 30 MB。每批立即写分区，不跨批累积。

**断点续传**：沿用现有 `ingest_log` 表。每批成功后记一行
`(dataset, symbol_batch_id, year, rows, ts)`；重启时跳过已记录的批次。

**额度闸门**：每批开始前查 `get_query_count("spare")`，低于本批预估消耗则**干净退出
并打印续跑命令**，不做部分写入。

**幂等**：同一批重复采集结果一致（`Warehouse.write` 本就是原子幂等覆盖写）。

**重建前快照**：`tar` 一份现有 warehouse（433 MB）留在节点上，万一某段聚宽数据有
问题有退路。

**依赖**：`jqdatasdk` 拖 pandas + sqlalchemy + thrift，放进**可选依赖组**，不进入
实盘运行时。凭据写 `config/deploy.local.toml`（已 gitignore），绝不进代码库。

## 十、派生层改造与删除清单

### `flags.py`：342 行 → 约 150 行

| 删除 | 约行数 |
|---|---|
| 模块 docstring 的 ST 三条规则说明 + 标定表 + 第 3 条来历 | 60 |
| `ST_LIMIT` / `ST_CONFIRM_BARS` / `ST_CONFIRM_HITS` | 3 |
| `hit_st` / `confirmed` / `breach` / `cum_hits` / `hits_at_last_breach` / `vetoed` 整段 | 40 |
| `_with_reference_price()` + `_ACTION_COLUMNS`（除权参考价公式） | 30 |
| `_limit_expr()` 整数分运算及守护其与 Decimal 等价的测试 | 15 |

**连带简化**：`_LOOKBACK_DAYS = 150` 的跨年回看存在的两个理由（给年初第一根 bar
补前收、给 ST 滚动计数留窗口）同时消失——`pre_close` 与 `is_st` 都是真值。逐年
处理退化为纯粹的分区写。

新 `_compute` 只做三件事：join `daily_reference` + `st_daily` → 比较出四个布尔 →
`is_first_board` 移一位。

### `adjust.py`

`build_adj_factors` 改为直接把 `daily_reference.factor` 填入 `cum_factor`，删掉跑
全市场的 `iter_rows` 循环。`cum_share_factor` 仍自算——聚宽的 `factor` 是价格因子，
不含股本比例（301379.SZ 2026-06-05 实测两者差 0.5%）。`daily_factor()` 不删，
**降级为校验函数**：测试断言自算因子与聚宽 `factor` 的比值在 1e-6 内。

### 行为变化（必须单独 commit、单独对账）

`src/qsys/strategy/structural/universe.py:81` 现用 `limit_pct == 0.05` 代理 ST 过滤。
有真值后应直接读 `is_st`。但 5% 档并非只有 ST 一种情形（退市整理期等），**此改动
会改变候选池进而改变回测结果**。它必须与真值接入分开提交，否则无法归因收益变化。

## 十一、质检

`quality/daily_rules.py` 新增（有真值才卡得住的规则）：

- `bars_daily` 与 `daily_reference` 逐年逐日**行数相同**；差集为空
- `paused == False` 时 `low_limit <= low <= high <= high_limit`，越界即报
- `paused == True` 时 `volume == 0` 的反例计数（应为 0）
- `limit_pct` 落在 `{0.05, 0.10, 0.20, 0.30}` 之外的股-日全部报出（上市首日无涨跌幅
  限制会落在集合外，这正是要看见的）
- `st_daily` 与 `bars_daily` 的 `(dt, symbol)` 差集为空
- 全量校验 `amount / volume`（按 §11.1 定论的单位换算后）落在 `[low, high]` 内

### 11.1 `volume` 单位：一处必须显式决断的语义变更

**现状**：`bars_daily.volume` 存的是**手**。这不是推测，是实测结论——
`src/qsys/backtest/sim_broker.py:28-31` 记着：86.5 万根日线里 99.88% 满足
`low <= amount/(volume*100) <= high`，按「股」解释 **0 条**命中。当年正是把它
当成股，才把成交量上限收紧了 100 倍，用 159.6 万本金对账时一天冒出 155 次
`volume_capped` 才暴露。

**聚宽侧**：`get_call_auction` 的 docstring 明确写「累计成交量（股）」，但那是竞价
tick，**不能据此推断日线**。日线单位由探针第 9 项实测确定。

**如果实测为「股」**（大概率），则这是一次真正的语义变更，必须显式处理，不能
悄悄落盘：

| 消费点 | 现有代码 | 处置 |
|---|---|---|
| `backtest/sim_broker.py:117` | `cap = int(bar.volume * LOT * self._volume_cap)` | 去掉 `* LOT` |
| `data/pipeline/valuation.py:113` | `volume * _LOT / circulating * 100.0`（换手率） | 去掉 `* _LOT` |
| `strategy/structural/entry.py` docstring | `amount/(volume*100) = 41.00` | 改写为 `amount/volume` |
| `data/feed.py` 的 `volume_factor` | 复权量换算 | 与单位无关，核对后不动 |
| `minute_bars.volume` | QMT 来源，单位可能与聚宽日线不同 | **必须单独实测**，两张表单位不一致是最危险的情形 |

**禁止的做法**：在采集时把股 `/100` 转成手来"保持兼容"。那是一次变换，且 100 股
以下的零碎成交会产生非整数，属于 §2 明令禁止的第三类。**存真值、改消费点。**

这一项在实施顺序里必须排在第 3 步（日线采集）之前定论，否则 1,800 万行落盘后
再发现单位不对，要全量重采。

## 十二、QMT 接缝

接缝日 `T_jq` = 试用覆盖终点。`T_jq` 之后 `daily_reference` 由每日归档写入：
Windows 侧收盘后取全市场 `get_instrument_detail` 的 `high_limit / low_limit / paused`
→ 回传 parquet → Linux 导入。挂进现有 `daily_archive.py` 编排，不另起定时器
（「一个编排者、一份日志、一处看失败」）。

`T_jq` 之后的 `is_st` 有真实缺口，**不允许退回反推**。两个候选真值源列为探针项：

1. Windows 侧打印一只 ST 股的 `get_instrument_detail` 全字段，确认是否带名称或 ST 标记
2. akshare 是否有当日 ST 列表接口（当日真值即可，每日归档自然长出历史）

两条都不成立时，`st_daily` 在 `T_jq` 之后进入「待确认区间」，由质检**显式报出**，
而不是悄悄推断。反推代码删除后不再回来。

## 十三、测试策略

- **fixtures**：沿用 `scripts/record_fixtures.py` 的路子录一份聚宽返回，单测不联网
- **假 client**：采集器走依赖注入，与现有 provider 同一模式
- **参数守护**：一条测试断言 `get_price` 调用点的 `fq=None` / `fill_paused=False` /
  `skip_paused=False`（§2.1 是设计的最高条款，必须有测试守着）
- **血缘守护**：一条测试断言 `daily_flags` 的每个派生列可由直取列逐位重算
- **黄金回归**：固定区间的选股集合与回测净值落进 `tests/`。以后任何口径改动都得
  显式更新这个数字——**这就是替代「人工翻聚宽对账」的东西**

## 十四、验收标准

数据同源后，「对齐口径」本身失去意义，剩余差异只可能来自策略逻辑。门槛因此提高：

- 候选池逐日集合**完全相同**
- Gate 判定 **241/241** 一致
- 买入笔数与成交价**逐笔一致**
- 抽样验真：随机 100 个股-日与同花顺比对，**不复权价格逐字段相同**；成交量在
  §11.1 定论的单位换算后相同；`high_limit/low_limit/paused/is_st` 逐字段相同
- 质检 §11 全部规则 0 违反

达成后，`docs/reference/joinquant-alignment.md` 中「不能宣称百分百对齐」一句删除。

## 十五、风险与未决项

| 风险 | 处置 |
|---|---|
| 试用额度不足以拉完全历史 | 采集顺序已按价值排；额度闸门保证部分完成也是干净状态 |
| 聚宽某段历史本身有问题 | 重建前 tar 快照；质检 §11 是独立于来源的事实校验 |
| `T_jq` 之后 ST 真值无源 | §12 两个探针项；都不成立则显式标记待确认区间，不推断 |
| 全历史含 2005 年前数据，涨跌停制度与策略假设不符 | `limit_pct` 真值化后制度差异自然体现；策略区间由使用方选择 |
| `bars_daily` 换源后历史回测数字变化 | 预期内；真值接入与行为变化分开提交，逐步归因 |
| **`volume` 单位与现有消费点不一致** | §11.1：探针第 9 项先定论，再改消费点，**不做单位变换** |

未决项（探针输出后补齐本文档）：§7 全部 8 项。

## 十六、实施顺序

1. 探针脚本 + 结果落档（§7）
2. schema 新增两表 + `joinquant_provider`（§5、§9）
3. **`volume` 单位定论 + 消费点修正**（§11.1）—— 必须在全量采集之前
4. 日线 + `daily_reference` 全历史采集
5. `st_daily` 采集
6. 分红送配采集 + `adjust.py` 改造（§10）
7. `flags.py` 重写与删除（§10）
8. 质检规则 + 抽样验真脚本（§11、§2.3）
9. 集合竞价、分钟线采集（§8）
10. QMT 接缝（§12）
11. 黄金回归 + 全量对账 + 文档更新（§13、§14）

第 7 步之后即可宣布生产路径上再无推断。第 11 步之后对账工作结案。
