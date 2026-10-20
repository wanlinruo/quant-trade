# Tushare `stk_limit` 的 B 股代码（#460 探针结论）

探针：`scripts/probe_issue460_bshare_codes.py`，2026-10-20 23:51 在生产节点只读运行
（交易日 2026-10-20），结果 `/data/qsys/var-deployments/issue460-20261020/probe-result.json`
（SHA-256 `ff8180807f4118df4d9123664fcc1ebbcb3cc2d43a809f7ce1c84000a27d6fbe`）。
不含凭证；仓库盘点用 DuckDB 1 GB 内存上限、单线程、受控溢写目录（实际未溢写）。

## 实测事实

- Tushare `stk_limit`（当日 5,653 行：SH 2,318 / SZ 2,943 / BJ 392）含 **77 只 B 股**：
  - 深市 B 股 `200xxx.SZ` 36 只，后缀与 QMT 一致，QMT 核对正常；
  - 沪市 B 股 `900xxx` 41 只，**后缀全部是 `.BJ`**。按 `ts_code=900901.SH` 查询返回 0 行，
    按 `900901.BJ` 查询返回 `0.625/0.511`。即 `.BJ` 是 Tushare 该接口自身的标注，不是本仓库改写。
- Tushare `daily`（当日 5,568 行）与 `stock_basic`（L/D/P 全部状态）**均不含任何 B 股**。
- 仓库中 B 股代码**只出现在** `price_limits_daily` 与 canonical `price_limit_daily`：
  `200xxx.SZ` 41 个代码 / 39,732 行（2022-08-04..2026-10-20），
  `900xxx.BJ` 44 个代码 / 24,135 行（2024-06-05..2026-10-20）。
  `tushare_daily`、`bar_daily`、`daily_flags`、`instruments`、`security_master`、
  `security_status_daily`、`stock_st_daily`、`suspend_daily`、`limit_list_daily`、
  `valuation_daily`、`moneyflow_daily`、`adjustment_factor_daily`、`name_history` 均为 0。

## 含义

- 涨跌停价核对里稳定的 "QMT 未覆盖 41 只" 全部是沪市 B 股的上游代码标注差异，
  不是 A 股覆盖缺口。
- B 股不在日线、名录与标志表里，因此不会进入候选池、广度、回测或交易链路；
  仅停留在两张涨跌停价表中。
- 源 `ts_code` 不改写、不推断后缀，既有行不删除。

## 处置（维护者 2026-10-21 选 A）

只改 Web 展示：`MarketService.limit_check` 用 `instruments`（Tushare `stock_basic` L/D/P 直取名录）
把报告里的 `missing_samples` 拆成 `missing_in_roster`（A 股真实覆盖缺口，黄色并列出代码）与
`missing_outside_roster`（不在名录，灰色说明）。名录读不到或为空时不拆分，回到原显示；
样本被截断（>200）时标明只分类了已列出部分。网关报告与 Redis 原值不变，不需要 Windows 发布。
