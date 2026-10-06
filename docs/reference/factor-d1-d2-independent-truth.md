# D-1 / D-2 复权因子独立验真

执行日期：2026-10-06  
跟踪：GitHub Issue #119

## 结论

原 `rel_diff > 1%` 的 1,807 个候选并非同一种问题：

- 1,119 条属于 2005—2006 股改期，保留为历史口径证据；
- 369 条属于 D-2 北交所，其中 **全部发生在规范上市日或上市日前**，根因是
  Tushare 将全国股转历史映射到当前北交所代码后，消费方跨证券身份边界计算了
  收益和因子跳变；
- 扣除上述两类后，D-1 非股改期实际为 319 条、229 只证券，不是原先把 D-2
  混入后记录的 688 条。

QMT 独立证据确认 D-1 中 16 条 Tushare 单日因子跳变不受支持，价格缺口与 QMT
单次因子互相吻合；87 条由 QMT 支持 Tushare；其余 216 条证据不足，继续标记为
`inconclusive`，不猜测、不改写源事实。

## 来源、独立性与使用边界

独立源是生产 Windows QMT 的 `get_divid_factors` 原始归档，Linux 同步副本位于
`/home/ubuntu/qsys/var/dividends-done/`。两份 parquet 共 60,423 行、5,742 只证券，
覆盖 1991-03-11 至 2026-09-09；60,422 行含正的单次因子 `dr/factor`，并包含配股
事件。QMT 与 Tushare 是不同采集链路，符合本次“不得用 Tushare 验 Tushare”的
独立性要求。

本项目只将这些数据用于现有生产账户内的内部质量核验，不把 QMT 原始文件或全量内容
提交到仓库、对外分发或作为新行情产品。仓库只保存归因统计、必要样本和永久质量证据；
这是一条工程使用边界记录，不代替账号持有人对券商/QMT 合同条款的法律判断。

## 方法与统计

候选来自 `artifacts/factor_verification.parquet`：

```text
direct   = factor(t) / factor(t-1)
computed = close(t-1) / pre_close(t)
```

按证券和除权日连接 QMT 单次因子，匹配相对容差为 0.1%。完整可复跑脚本是
`scripts/analyze_factor_d1_d2.py`，统计产物是
`artifacts/factor-d1-d2-qmt-20261006.json`，逐条归因是
`artifacts/factor-d1-d2-qmt-details-20261006.parquet`。

### D-1 非股改期

| 形态 | 行数 |
|---|---:|
| 因子跳、价格无缺口 | 165 |
| 跳变幅度不符 | 62 |
| 价格有缺口、因子静止 | 92 |
| **合计** | **319** |

| QMT 归因 | 行数 | 裁决 |
|---|---:|---|
| 支持 Tushare 因子 | 87 | `match` |
| 支持价格缺口 | 16 | `mismatch`，确认源错 |
| 两边均不匹配 | 40 | `inconclusive` |
| 当日无 QMT 公司行动 | 159 | `inconclusive` |
| QMT 缺证券 | 17 | `inconclusive` |

16 条确认问题中，10 条为“价格有缺口、因子静止”，6 条为“幅度不符”。给数据源方的
最小反馈清单保存在 `artifacts/tushare-factor-feedback-20261006.csv`；累计因子不被
静默覆盖，部署时把 319 条结论写入生产 `metric_evidence` 作为永久证据。

### D-2 北交所

| 分组 | 行数 | 上市日前 | 上市当日 | 上市日后 |
|---|---:|---:|---:|---:|
| `920xxx.BJ` | 366 | 131 | 235 | 0 |
| 旧北交所代码 | 3 | 0 | 3 | 0 |

`920` 组中 362 条在 QMT 覆盖该证券、但当日没有公司行动；其余 4 条缺 QMT 证券。
这说明异常簇不是北交所上市后的复权错误，而是上市前全国股转历史与上市后北交所历史
被同一当前代码串接。修复在进入 `market_regime` 和 `A3.missed_exdiv` 前使用
`security_master.list_date` 切断边界，上市首日 `prev_close` 置空；未知上市日不删除。

## 质量规则与影响

`A3.missed_exdiv` 继续负责“价格缺口与因子跳变不匹配”的常驻检测；本次补上规范上市日
边界，避免把 D-2 身份切换误报为 D-1。规则有构造历史样本回归，并由每日
`catalog-verify` 以当年至今全量口径运行，因此同时覆盖历史重验和新增数据。

在生产仓库只读对比 2023-01-03 至 2026-09-30 共 908 个交易日：

| 指标 | 变化日期 | 最大绝对变化 |
|---|---:|---:|
| `market_advance_ratio` | 28 | 0.000317619 |
| `market_median_return_1d` | 26 | 0.000009266221 |
| `prior_limitup_nextday_return_mean` | 0 | 0 |
| `prior_limitup_nextday_return_median` | 0 | 0 |
| `valid_stock_count` | 0 | 0 |

三条冻结 Gate 阈值（上涨家数比、中位收益、昨日涨停溢价）均为 **0 个阈值穿越日**，
所以当前可用回测窗口内不会因为本修复改变 S6/S7 放行结果。量化脚本为
`scripts/analyze_d2_regime_impact.py`；它只读生产源表，不写仓库。

## 保留项

`inconclusive` 不是通过。它们继续保留 D-1 质量标记和逐条证据；若以后增加另一独立
公司行动源，可在同一 `metric_evidence` 生命周期中追加结论。2005—2006 股改期也不以
本次北交所修复为由改写。
