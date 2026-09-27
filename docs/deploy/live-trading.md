# 小资金实盘上线手册

当前闭环为：Linux 策略核心 → Redis Streams → Windows 执行代理 → miniQMT。
首期仅启用现有 09:30 竞价后路径，专用账户总资产不超过 10 万元，配置默认只
启用 1 万元策略资金。旧 `qmt-bridge` 只能保留为影子比较器，必须使用不同的
Stream 名称，且不得连接该实盘账户发单。

## 上线前的外部事项

代码不能代办以下事项，未完成不得进入 live：券商量化权限与 miniQMT 实盘
权限开通、交易接口协议/风险揭示签署、程序化交易报告或报备要求确认、专用
账户与佣金/最低佣金/印花税/过户费核对、云主机 IP 白名单与时间同步。监管和
券商规则会变化，正式启用前以券商当日书面要求为准。

## 三道写保护

真实委托同时要求：`mode = "live"`、`orders_enabled = true`、资金账号出现在
`protected_account_ids`。示例配置永久保持 disabled。公网 Redis 必须 TLS、强
密码，并用安全组只放行两台主机；监控页面只绑定 `127.0.0.1` 或私网地址。

紧急停止：

```bash
uv run python scripts/kill.py stop "manual stop"   # 实盘核心与影子核心一起停
uv run python scripts/kill.py status               # 两个都报
uv run python scripts/kill.py resume live          # 恢复必须指名
```

该命令让核心停止开仓，通知 Windows 撤销活动委托，并落下远端/本地停止闸；
减仓单在风控层仍被允许。恢复前必须先对账，再显式执行 `resume`。系统不会
自动清仓，避免在行情或网络异常时失控卖出。

**实盘核心与影子核心是两套开关**：`control:kill` / `var/KILL` 对实盘，
`shadow:control:kill` / `var/SHADOW_KILL` 对影子。所以这个脚本的三条命令是这样定的：

- `stop` 默认**两个都停**——紧急时要的是全停，停掉没在跑的那个也无害，
  漏停正在跑的那个才要命。要单停就在原因后面加 `live` 或 `shadow`。
- `status` **两个都报**——2026-09-27 之前它只看实盘那套，观察期里明明跑着影子核心，
  它却回一句"运行中"，报的是停用中的实盘核心。查了却查错对象，比没查更危险。
- `resume` **必须指名**，不接受默认值：解除停机是危险方向。

停机标记落地的顺序也要紧：**先写本地文件，再写 Redis**。反过来的话，Redis 不可达时
`set_key` 会抛，本地标记就永远写不下去——而核心连续故障熔断时，坏的往往正是 Redis
（2026-09-27 故障演练发现，见 `docs/reference/fault-drills.md`）。相应地，
Redis 读不到时按**停机**处理：宁可误停，不可停不下来。

这条规则有个副作用要知道：Redis 持续断线超过 3 个 tick，核心熔断会真的落下本地
停机标记，Redis 恢复后它**不会自动消失**，得人工 `resume`。这是有意的——熔断之后
本就该有人来看一眼。

## 首次接入顺序

1. Windows 先以只读方式校验字段：

   ```powershell
   uv run python scripts/win_smoke_reports.py --account <账号> --qmt <userdata路径>
   ```

2. 真实执行配置保持 `mode="disabled"`、`orders_enabled=false`。用现有
   `scripts/run_paper.py` 跑 qsys 影子盘，并让旧 `qmt-bridge` 只记录比较结果、
   不连接实盘发单 Stream，连续跑满至少五个交易日；核对选股、委托意图、
   行情时间戳、费用和每日对账。
3. 仿真或券商测试环境执行部分成交、撤单、废单、Redis 断线、Linux/Windows
   重启、重复消息、心跳超时和本地 kill 文件演练。
4. 切到 live 前运行只读盘前门禁：

   ```bash
   uv run python scripts/live_preflight.py --config config/deploy.local.toml --require-live
   ```

5. 第一阶段启用资金 10,000 元，人工盯盘；稳定至少五个交易日后才评估扩容。
   不因短期收益跳过观察期。每日收盘核对券商资金、持仓、委托、成交和报告。

## 进程与恢复顺序

启动顺序：Redis → miniQMT → Windows agent → market gateway → 盘前检查 → live
core。停止顺序反过来，先执行 kill/cancel。Linux unit 模板位于
`deploy/systemd/qsys-live-core.service`；复制后要按实际用户和路径修改。行情网关
依赖 QMT，也和 agent 一样运行在 Windows。Windows 使用任务计划程序或 NSSM
分别守护 `scripts/run_agent.py` 与 `scripts/run_market_gateway.py`，设置“无论用户
是否登录都运行”、失败一分钟后重启；账户级 lock 文件不可共用。

成交回报在组合状态和成交明细同一 SQLite 事务落盘后才 ACK；核心重启会恢复
未终态委托，Windows 重启则通过券商 `order_remark` 找回原委托并避免重复下单。
`var/live-state.sqlite3`、`var/agent-ledger.sqlite3` 和 warehouse 每日加密备份，
备份恢复演练至少每季度一次。

## 盘中门限

- 单票不超过账户净值 5%，总敞口不超过 10%，启用资金 10,000 元；
- 单笔不超过 10,000 元，日换手不超过日初净值 100%；
- 日内亏损达到 2%、连续拒单达到 3 次或核心/代理心跳超时即停止开仓并撤单；
- 买单不超过当时累计成交量 5%，且不超过前一交易日成交额 1%；
- 委托价格相对买一/卖一偏离不超过 2%，停牌、涨跌停和 T+1 由共用约束拦截。

每个交易日结束发送净值、成交、拒单和 EOD 对账报告。任何对账不一致、无行情、
账户快照超过 5 秒、心跳超过 10 秒，都视为不具备自动开仓条件。
