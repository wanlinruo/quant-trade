# Web 改版 06c：交易组 5 页 Implementation Plan

**Goal:** 用新后端契约和 React 页面替换交易组旧页面：实盘总览、委托与成交、对账与风控、运行记录、模拟账户。所有查询只读；模拟账户沿用既有受管写操作；页面不提供实盘控制。

**Architecture:**
- 新增 `qsys.webapp.contracts.trading`、`queries.trading`、`routes.trading`；查询层直接读 `SessionJournal`、只读 `LiveStore`、Redis 只读快照/心跳、`PaperStore` 与计划 05 的核对记录，不转储旧 `MonitorService` 视图对象。
- 五页共享交易日选择；实盘四页按日读取，模拟账户保留账户维度与日内/历史曲线。
- 新接口时间带时区、金额单位元、比例为 0–1；缺失为 `null`，原因进入 `missing`。
- 实盘总资产只显示原始观测，不据此计算收益；券商受理时刻未记录就保持 `null`；未知委托明确“不重发”。

## Task 1：交易查询契约与只读接口

- [x] 定义实盘总览、委托生命周期、30 日三层对账、运行记录、模拟账户契约并登记生成类型。
- [x] 查询层直接读取领域存储；状态库不存在时返回 `enabled=false` 或字段未知，不把缺失当 0。
- [x] 新增：
  - `GET /api/app/trading/live?day=`
  - `GET /api/app/trading/orders?day=`
  - `GET /api/app/trading/reconciliation?day=`
  - `GET /api/app/trading/runs?day=`
  - `GET /api/app/trading/paper`
  - `GET /api/app/trading/paper/{id}?day=`
- [x] 覆盖缺失、无会话、未知券商受理时刻、未知委托不重发、核对记录不可读、三层对账不补值。

## Task 2：模拟账户受管写操作

- [x] 新接口保留暂停、恢复、结束与通知 Webhook；写路由只在 `PaperStore` 启用时注册。
- [x] 结束操作必须由前端确认；错误账户只展示恢复指引，不新增直接改库操作。
- [x] Webhook 只回显掩码，不记录或返回凭据；请求体拒绝额外字段。

## Task 3：交易组五页

- [x] `/app/live`：原始总资产、持仓/敞口、停机闸、链路、风控额度；缺少资金流水时收益显示缺失。
- [x] `/app/orders`：生命周期、委托、成交、拒单；未记录节点不画，未知状态注明不重发。
- [x] `/app/recon`：30 日三层热力图、风控只读表、三道闸、计划 05 核对清单；无解除按钮。
- [x] `/app/runs`：计划/实际时点、候选漏斗、日报复制与 `.txt` 下载。
- [x] `/app/paper`：账户列表与详情，持仓/委托/成交/拒单/事件/通知设置；保留确认框。
- [x] 390、1280、1600 三种宽度无横向页面溢出。

## Task 4：接线与验收

- [x] `monitor_cli` 显式传入只读事实源、风险配置和核对记录路径；新增参数保持默认兼容。
- [x] 后端专项、前端单测、typecheck、build、Playwright 与全量 pytest 通过。
- [x] 本地只读预览冒烟；不得连接真实委托流或执行实盘动作。
- [x] 推送 `feat/web-redesign` 并在 #662 留完成记录；不合入 `test/main`，不发布生产。

## Review Focus

1. 总资产变化不冒充收益；缺资金流水时收益为 `null`。
2. 券商受理时刻缺失不从成交时刻反推。
3. 未知委托明确“不重发”，生命周期只画真实记录节点。
4. 停机闸核对记录过期或不可读时显示未知；页面无解除入口。
5. 对账热力图只显示真实记录，实盘启动前与缺记录日期均为“未对账”。
6. 模拟账户错误恢复不直接改库；Webhook 永不回显原值。
