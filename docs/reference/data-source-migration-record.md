# 日线真值源迁移总记录

分支：`feat/baidupan-data-source`（领先 `main` 60 个提交）
时间跨度：2026-09-11 ~ 2026-09-14
状态：**两个计划全部完成并在生产验收**

这份文档是给「上下文丢失之后接手的人」看的：这条分支到底改了什么、
现在每张表的真值源是谁、哪些结论有证据、还剩什么没做。
逐任务的过程记录在 `.superpowers/sdd/`（git 忽略），逐段验收数据在 `artifacts/`。

---

## 一、为什么要迁移

2026-09-10 起 mootdx 的 K 线接口对**全部服务器**返回 None，每次调用等 3.8 秒
后返回空表。全市场 5,219 只跑完要 5.5 小时，systemd 在 2 小时处杀掉——
连续两天归档失败，日线停在 2026-09-09。

诊断时先被文档误导（文档把超时归因为回测并行导致的 CPU 争用），
实测两小时内 CPU 仅 45.2s/48.0s，证据直接否掉了那个解释。
真因是上游失效，已订正 `docs/deploy/linux-core.md`。

---

## 二、迁移的两步

### 计划 A：`bars_daily`（mootdx）→ `paid_daily`（百度网盘付费源）

`docs/superpowers/plans/2026-09-13-paid-daily-truth-source.md`，11 个任务，全部完成。

### 计划 C：`paid_daily` → `tushare_daily`（Tushare Pro API 直连）

`docs/superpowers/plans/2026-09-13-tushare-ingestion.md`，10 个任务，全部完成。

**为什么要走两步**：付费源实测就是 Tushare 转售——字段级 28 列吻合，
且 **18,224,238 行逐键零差异**。但付费源的链路是「卖家更新百度网盘分享链接 →
人工转存 → 系统同步」，中间三个人工环节，断一环数据就没了，不符合完全自动化。
Tushare 直连去掉了全部人工环节。

计划 B（付费源为主 + QMT 兜底）在发现这一点后**整段作废**，未执行。

---

## 三、现在每张表的真值源

| 数据集 | 真值源 | 说明 |
|---|---|---|
| `tushare_daily` | Tushare `daily` + `daily_basic` + `adj_factor` | 28 列，1990 起 18,224,238 行 |
| `daily_flags` | 派生自 `tushare_daily` | ST、涨跌停、首板 |
| `adj_factors.cum_factor` | **直取** `factor` | 价格反推降级为校验，不得覆盖真值 |
| `price_limits_daily` | Tushare `stk_limit` | |
| `suspend_daily` | Tushare `suspend_d` | |
| `index_bars` | Tushare `index_daily` | `volume` 已改 Float64（实测含小数）|
| `instruments` | Tushare `stock_basic`（含退市）| 带 `as_of` 快照日期 |
| `name_history` | Tushare `namechange` | 区间表，**有 2006 断层** |
| `name_snapshots` | Tushare `stock_basic` 每日快照 | 点观测，2026-09-14 起累积 |
| `limit_list_daily` | Tushare `limit_list_d` | 首板/炸板直取 |
| `paid_daily` | 百度网盘付费源 | **只读冻结，但不能删**——见下 |
| `bars_daily` | mootdx（已死）| 冻结，入口 `legacy_daily_bars()` |
| `auction_ticks` | QMT 实时 | **买不到，只能每天自采** |

### `paid_daily` 为什么不能删

`daily_flags.is_st` 由名称派生，名称按四级优先级解析（`flags.resolve_names`），
四个候选**都是直取字段**：

1. `tushare_daily.name`——生产恒 null
2. `paid_daily.name`——逐日观测，覆盖 1990 ~ 2026-09-11
3. `name_snapshots.name`——每日快照，2026-09-14 起累积
4. `namechange` 区间——公告事件，**2006 断层**

`name_snapshots` 只能向后累积、补不了历史；`namechange` 在 2006-10-08 之后
大量标的没有后继区间。所以 **2026-09-11 及之前的名称真值只有 `paid_daily`
一个源**。删了它，867 个首板会重新消失。详见 D-10。

---

## 四、逐段验收结论（都有产物）

三段式验收（spec §10）的意义是逐段隔离、差异可归因。

| 段 | 内容 | 结论 | 产物 |
|---|---|---|---|
| 1 | 只换源 | **OHLC 零差异通过** | `regression_stage1.json` |
| 2 | 加 ST 真值 | **通过** | `stage2_*.json/parquet` |
| 3 | 加因子直取 | **不通过，挂账 D-1** | `stage3_*.json/parquet` |
| 4 | 付费源 → Tushare | **修复后逐键一致** | `stage4_*.json/parquet` |

完整报告：`artifacts/换源回归报告_2026-09-13.md`、
`artifacts/第4段回归_付费源到Tushare换源_2026-09-14.md`。

### 值得记住的几个数

- **第 1 段**：OHLC 差异 **0**。旧表缺 **146 只**标的，有 2026 数据的 19 只里
  18 只是退市/ST——**旧口径存在幸存者偏差**。
- **第 2 段**：ST 判定翻转 102,554 行，其中 **94.3% 是旧的价格反推漏判的 ST**。
  连带 2,508 行首板翻转。`market_regime` 的 **13 个生态因子在 86%~100% 的
  交易日上全部变化**（唯一没变的 `index_ma20_slope_5d` 只依赖未动的指数源，
  反过来证明其余变化不是计算噪声）。
- **第 3 段**：同锚归一后 **71.5% 的股-日有差异**。`rel > 1%` 的 59 只里
  **39 只新值自洽、20 只不自洽**——换成直取**不是单向改善**。
- **第 4 段**：修复后全部差异归零，首板 **50,525** 完全还原。

---

## 五、这条分支上被修掉的真缺陷

1. **归档超时**（`universe.py` / `daily_archive.py`）：加上游探针，失效时 4 分钟
   干净退出而不是 2 小时被杀；空结果不再写 `ingest_log`（写了会让上游故障
   被当成「全市场已完成」而永不回补）。
2. **`listing_dates()` 绕过换源**：它直接 glob 旧表，不修的话新上市标的会
   **永久**被排除出候选池。
3. **换源对账吞掉单边 null**：`pl.col(a) != pl.col(b)` 在 polars 里对
   「一边 null」求值为 null，被 filter 当 False 静默丢弃。
4. **重算只增不删**：分区合并是「并集 + keep=last」，窗口内被源删掉的行
   永远留着，生产上留下过 10 行「一半旧真值源、一半新 schema null」的混血。
5. **`boards` 参数无生产调用点**：`build_daily_flags` 加了参数却没人传，
   343 只北交所会一天天流回广度因子且不报错。
6. **`namechange` 2006 断层**：静默丢掉 **867 个首板**，全局占比只有 2.05%，
   10% 的闸门挡不住。
7. **`st_source` 恒为字面量**：让 2% 根本没有名称的行看起来也「有来源」，
   这是 6 没被早点发现的原因之一。现改记具体来源、名称为 null 时也为 null。

---

## 六、生产状态

节点 `/home/ubuntu/qsys`，warehouse 实体路径 `/data/qsys/warehouse`。

- `qsys-tushare-sync.timer`：**工作日 17:00 + 19:00 双触发**（Asia/Shanghai），
  `enabled` + `active`。双触发的理由：`sync` 幂等，而 `Persistent=true`
  **只补「错过的触发」、不补「已触发但失败」**——单次触发若撞上发布延迟，
  那天就要人工介入。
- 全链路实跑 `Result=success` / `ExecMainStatus=0` / 45.5 秒 CPU。
- 回滚点：`/data/qsys/warehouse/backups/*_before_st_truth`（计划 A 前）、
  `/data/qsys/backups/tushare-switch-20260914`（计划 C 换源前）、
  `/data/qsys/backups/daily_flags_before_name_fix`（D-10 修复前）。

### 操作节点的两条硬规矩

- **别整目录 rsync 到节点**。`warehouse` 是指向 `/data/qsys` 的软链接，
  `.venv` 是 Linux 的——整目录同步会毁掉两者（有前科）。列具体文件，
  或者用 `git bundle` + `fetch`。
- **systemd `ProtectSystem=strict` 必须带 `ReadWritePaths=/home/ubuntu/qsys /data/qsys`**。
  少写 `/data/qsys` 会在第一次写入时失败，因为 warehouse 是软链接。

---

## 七、还没做的事

全部登记在 `docs/reference/deferred-corrections.md`：

| 编号 | 内容 | 状态 |
|---|---|---|
| D-1 | 复权因子非股改期偏差 | 待独立行情源验真 |
| D-2 | 920 号段北交所因子异常簇 | 同上 |
| D-3 | 广度池过滤从未生效 | 已修（Task 8），留作 D-4 的成因 |
| **D-4** | **S6/S7 阈值重标定** | **最要紧的一条** |
| D-5 | 两层过滤口径不一致 | 当前无 B 股，影响为零 |
| D-6 | `tushare_daily.name` 恒 null | 已解除（闸门 + 四级名称解析）|
| D-7 | 竞价逐笔只有 8 天 | 不等换源，每天必须采 |
| D-8 | 后复权路径锚点相关 | 当前无调用点 |
| D-9 | `feed.py` 的 `fill_null(1.0)` | 价格链路上一处字面填充 |
| D-10 | `namechange` 2006 断层 | **已了结** |

**D-4 是下一件该做的事**：第 2 段实测 13 个生态因子在 86%~100% 的交易日上
全部变化，`params.py` 里冻结的四个阈值拟合的已经不是同一个分布。

按用户 2026-09-13 定的原则——尊重数据等客观事实，按数据正确性重新计算阈值——
这必须做。但它是**研究任务不是数据层任务**：要重跑原来那套分位/消融流程，
不能折进任何一个数据任务里，折进去就毁掉了三段式归因。

---

## 八、买了什么、花了多少

Tushare Pro 5000 积分，¥500/年，2026-09-13 购买。**积分是权限档位不是消耗额度**，
重跑不花钱。凭据在 `config/deploy.local.toml`（已 gitignore，绝不入库）。

**积分一年有效、到期回收**——到期未续费则日线源直接中断。
`TushareConfig.purchased` 留作到期提醒依据。

竞价逐笔（`get_call_auction` 的等价物）**买不到**：Tushare 的 `stk_auction`
是 9:25 撮合完成后的结果数据，既不是策略用的第一条快照、也不是 09:24 那一条，
用它替代会引入前视偏差。只能靠 QMT 每天自采，**漏一天永久缺一天**。
