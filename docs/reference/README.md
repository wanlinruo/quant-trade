# 参考资料

## `strategy-api.md`

qsys 的策略 API 参考：一个策略 = 一个 `.py` 文件。生命周期钩子、调度声明、
`ctx` 上的全部接口、落盘格式，以及几条容易踩的坑。

可跑的示例在 `strategies/`：`ma_cross.py`（从零写的单文件策略）、
`structural_v1_2.py`（聚宽移植版的装载入口）。

## `joinquant-alignment.md`

与聚宽逐笔对齐的口径清单：已经证明一致的、我们改过的、剩余的真差异。

---

以下是**外部来源、不参与构建**的材料，供移植与对照使用。

## `joinquant-structural-v1.2.py`

用户在聚宽平台上跑了很久的实盘策略 `Structural Strategy V1.2 — FROZEN FINAL`
原文，2,651 行。**不要修改它** —— 它是移植的基准和交叉验证的对照组。

它不能在本项目里运行（依赖聚宽的 `jqdata` 运行时）。qsys 侧的移植版另放
`src/qsys/strategy/` 下。

交叉验证的方式见 spec §8.2：在同一区间比对**「哪天买了哪些票」**。
选股是离散的，错一只就看得出来，比对净值严格得多。

## 移植所需数据的就绪状态（2026-09-09）

| 策略需求 | 状态 |
|---|---|
| 候选池与首板/炸板判定 | ✅ `daily_flags` 894 天全市场 |
| `check_low_open` 60 日形态 | ✅ 日线，5,182 只有 ≥101 根 |
| `check_left_pressure` 101 日 | ✅ |
| `check_high_open` 市值过滤 | ✅ `valuation_snapshot` **100% 覆盖** |
| S6 Gate 的 `index_ma20_slope_5d` | ✅ 894/894 天非空 |
| S6 Gate 的其余生态因子 | ✅ `market_regime` |
| 竞价价 | ✅ 日线 `open` 即竞价成交价（实测相等） |
| 竞价量（仅 LOW_MAIN 需要） | ⚠️ 分钟线只覆盖最近 242 / 894 天 |
| `filter_new_stock` 上市 >50 天 | ✅ 用日线首个 bar 日期替代 |
| `filter_st_paused_stock` | ⚠️ 无历史 ST；但按 10% 判涨停使 ST 股本就进不了候选池，方向等效 |

**已知的语义分歧**（交叉验证时必须单独归因，不可误判为移植 bug）：

原策略 `get_first_auction_record` 取 `get_call_auction(09:15~09:26)` 的**第一条**
记录（作者明确注释「不按 time 重排，也不改成最后一条」），那是 09:15 的竞价
快照；我们只有 09:25 的**已成交价**。这条无法用现有数据消除。

**V1.2 已整体移除 WEAK 家族**（`_v12_keep_non_weak`），故只需移植
HIGH 与 LOW 两个分支。
