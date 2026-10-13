# #137 首次实盘链路联调清单

目标日为 2026-10-12（生产交易日历已确认开市）。目标不是验证策略收益，而是以
`511380.SH` 最小规模验证 Linux → Redis → Windows agent → miniQMT → 券商回报 →
账户持仓 → 反向平仓的完整链路。推荐 09:35 后开始，禁止在集合竞价、午间休市、
临近收盘或行情异常时勉强执行。

## 已完成的非交易准备

- 模拟盘最终参数累计 6 个交易日，每日 7/7 事件完成、0 error；
- 生产当前保持 `mode=shadow`、`orders_enabled=false`、live core 停止；
- Linux 健康服务与影子核心运行，Windows miniQMT、agent、行情网关运行；
- 联调标的固定 `511380.SH`，买入 100 份，名义金额硬上限 3,000 元；
- 验证工具已有实时盘口、价格档位、账户空仓、双端写保护、心跳、交易时段、停机闸、
  30 秒未完整成交自动全撤及账户快照收敛门禁；
- agent 只接管规范的 23 字符 `cYYYYMMDD-HHMMSS-xxxxxx` 回报，不把人工或旧 bridge
  委托混入本次证据；长度上限来自 2026-10-12 miniQMT 生产实测，超过后
  `order_remark` 会被截断并失去唯一性；
- 10 分钟 lease 到期会恢复心跳失败关闭，且在 live agent 未由健康 core 接管时不能解除。

## 现场 Go / No-Go

以下任一项不满足，当天不下单：

1. 维护者在交易时段再次明确确认本次真实买卖；
2. 生产代码为已验收目标 SHA、工作树干净、Linux/Windows 代码一致；
3. miniQMT 已登录实盘账户，agent/market 心跳和账户/行情快照均新鲜；
4. 账户无持仓、无活动委托、无人工委托，远端与本地 kill 均未生效；
5. `511380.SH` 未停牌，买一/卖一存在，100 份金额不超过 3,000 元；
6. 当天无交易所、券商、网络或数据源异常告警；
7. 维护者能同时查看券商客户端，随时人工撤单。

## 正常执行顺序

1. 留存账户资金、持仓、委托、成交和双端心跳基线；确认 `qsys-live-core` 心跳健康且
   `activity=idle`，行情网关保持运行。
2. 先武装 lease，等 agent 心跳明确报告 commissioning lease active，再停止
   `qsys-live-core`。顺序不能颠倒，否则 agent 会在 lease 生效前因 core 失联落闸。
3. 等 core 心跳超过 10 秒，确认 agent 仍报告 live / orders enabled / account protected /
   local kill false / commissioning lease active；执行 `live_preflight --require-live`，必须全绿。
4. 用 `commissioning_quote.py` 读取实时盘口，以卖一附近、且不超过保护带的 0.001 元档位
   提交 100 份限价买单。只执行一次。
5. 核对 qsys 与券商端委托号、`order_remark`、成交号、数量、均价；账户快照必须显示
   100 份可用持仓。重新读行情，以买一附近限价卖出 100 份。
6. 券商账户必须回到空仓且无活动委托；核对前后现金、工具反推费用与券商实收费用。
7. 启动 live core，等待 core 新鲜心跳且首次持仓/现金对账成功，再解除 lease。
8. 保持启用资金 1 万元进入至少 5 个交易日人工盯盘期；本次联调不得改策略参数。

完整命令见 `docs/deploy/live-trading.md`「上线验证单」。每一步的终端输出和
`commissioning.jsonl` 都进入 #137 证据，不能只口头报告成功。

## 异常分支

- **0 成交或拒单**：确认自动全撤和券商无活动委托，不重复下单；双端退回 shadow。
- **买入部分成交 N 份**：确认原单已撤/终态，账户快照必须显示
  `total_qty=available_qty=N`；重新取买一，只允许卖出精确 N 份，不补买、不凑整手。
- **卖出部分成交**：确认原单终态后，按最新账户快照中的剩余可用数量再次走恢复卖出；
  每次都必须与实际残仓精确一致。
- **回报或账户快照不收敛**：以券商客户端为真值，先撤活动委托，停止自动步骤并人工核对；
  不因 Redis 显示已成就继续下一步。
- **无法在 lease 到期前完成**：agent 会撤单并落本地停机闸。先在券商端确认终态和持仓，
  处置残仓后把 agent 切回 shadow；不得简单延长或伪造心跳。

异常收尾统一要求：撤销所有活动委托，确认券商持仓；Linux / Windows 同步退回
`mode=shadow`、`orders_enabled=false`，重启 shadow agent，确认其心跳后才解除 lease，
再恢复 shadow core。任何残仓、未知委托或未知现金差存在时，#137 保持阻断且不启动策略实盘。
