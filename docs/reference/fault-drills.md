# 上线前故障演练

`docs/deploy/live-trading.md` 阶段 3.3 要求在切实盘前演练部分成交、撤单、废单、
Redis 断线、两端重启、重复消息、心跳超时和 kill 文件。本文记录怎么做、以及做出了什么。

## 怎么做的

### 隔离实例，不碰生产

演练会真的按下停机开关，所以不在生产影子盘上做。`config/drill.toml`（节点本地，
不入库）从生产配置派生，只改两处：

- `[streams]` 全部键加 `drill:` 前缀；
- `[safety]` 设 `mode="live"`、`orders_enabled=true`、账户白名单写死 `DRILL-0001`。

**14 条流一条都不能漏。** 生产配置只写了 6 条，其余走 `StreamConfig` 的默认值；
派生时若只给写出来的那几条加前缀，`order_status` 等 7 条就会仍是默认名，
和生产撞在同一条流上。第一次生成就踩了这个，已在生成脚本里补全并加了校验。

`scripts/run_drills.py` 与 `scripts/drill_agent.py` 启动时都会检查前缀，
不是 `drill:` 直接拒绝运行。

### 券商回报由桩代理产生

部分成交、撤单、废单、重复消息这些回报只有券商会发，而券商仿真账户尚未到位。
`scripts/drill_agent.py` 扮演 Windows 执行代理，消费下单指令、按剧本回吐回报。
**要验的本来就是核心侧的处理逻辑**，券商那端是 QMT 的事。

一套代码两种用法：当库用（进程内驱动，确定性强，用于断言式演练），
或当进程用（给一个真起的核心实例当对手方，同时发心跳与账户快照）。

### 每项演练独占一组流名

`LiveBroker` 建消费组时固定从 `0-0` 起读。同一条流上换个消费组，就会把前面演练的
回报再读一遍——第一次跑正是这样把「撤单」判成有成交（那笔成交是上一项漏过来的）、
把「重复消息」的 1000 股算成 1500 股。`Drills.lane()` 给每项演练单独的流名，隔开才测得准。

## 2026-09-27 首次演练

```bash
uv run python scripts/run_drills.py --config config/drill.toml \
    --kill-file var/drill/KILL --state var/drill/state.sqlite3 \
    --json var/drill/results.json
```

8 项全部通过（修掉下面那个缺陷之后）。结果落在 `/data/qsys/var-deployments/20260927-fault-drills/`。

| 演练 | 验的是什么 | 结果 |
|---|---|---|
| 部分成交 | 剩余量 = 委托量 − 已成交，委托仍未了结 | 1 笔 500 股，剩余 500 |
| 撤单 | 状态流出现 cancelled，且无成交 | accepted → cancelled，0 笔成交 |
| 废单 | 变成一条带原因的拒单，委托不再挂着 | 1 条拒单，剩余 0 股 |
| 重复消息 | 同一 `broker_trade_id` 投两次只入账一笔 | 1 笔 1000 股 |
| 重启后重复投递 | 换进程后重放，落盘去重在并入组合之前挡住 | 被 `store.fill_recorded` 挡住 |
| 心跳超时 | 过期心跳判为 dead（核心据此停机撤单） | `dead = ['agent']` |
| kill 文件 | 只写文件即停机；买单拒、卖单仍放行 | 符合 |
| Redis 断线 | Redis 不可达时本地 kill 标记仍有效 | 符合（修复后） |

### 发现的缺陷：Redis 一断，停机开关就失灵

`KillSwitch` 的整个存在理由是「Web 挂了、核心挂了、Redis 连不上——只要能碰到
文件系统就能停机」。演练发现这个承诺在代码里是反的：

```python
def engage(self, reason):
    self._transport.set_key(self._key, stamp)   # Redis 不可达时抛在这里
    if self._path is not None:
        self._path.write_text(stamp)            # 永远到不了

def is_engaged(self):
    return self._transport.get_key(...) is not None or self._local_reason() is not None
    #      ^^^ 先问 Redis，抛了就整个抛出去，本地标记根本没机会被读到
```

两条路径写成了「先 Redis，后本地」，而 `RedisTransport` 不吞异常、redis-py 直接抛。
后果：

- **读**：Redis 断线期间，手工放下的本地 KILL 标记读不到，只会抛异常；
- **写**：核心连续 3 次 tick 失败会熔断停机，但如果坏的正是 Redis，
  `engage()` 在第一行就抛了，**本地标记一个字都写不下去**——偏偏这正是最需要它的时候。

改法（`src/qsys/live/kill_switch.py`）：本地这条路径不依赖任何服务，所以先走它。
`engage` 先写文件再写 Redis，Redis 失败只记日志；`is_engaged` 先看本地，再看 Redis，
Redis 读失败按**停机**处理——遵循本模块原本就写明的原则：宁可误停，不可停不下来。

**为什么单元测试没发现**：测试一律用 `MemoryTransport`，它从不抛异常，
于是整类「依赖方挂了」的故障被测试盖住了。已补 4 条用 `BrokenRedis` 的回归测试，
它们在修复前全红。这和 2026-09-24 那次「假 Context 掩盖了 `report(routed=)` 的
TypeError」是同一类教训：**替身不会出错，就测不出出错时会怎样**。

## 还差什么

- **两端重启**：Windows 端已于 2026-09-27 真做过一次（见 `docs/deploy/windows-node.md`）；
  核心进程重启的恢复语义由「重启后重复投递」这项覆盖。

  **Linux 整机重启不做实测**——2026-09-27 维护者决定以静态核对为准，不为演练中断线上
  （那台机器上还跑着复盘子系统）。静态核对见 `docs/deploy/linux-core.md`
  「重启后能自己回来的」：数据盘在 fstab、常驻单元与 timer 全部 enabled、
  Redis 隧道 `Restart=always`，唯一不会回来的是临时 `systemd-run` 起的观察单元。
  这是一个**有意接受的残余风险**：纸面推演与真实重启之间总有差距，Windows 那次
  正是重启才发现 miniQMT 起不来。

  核对时理清了一件容易想错的事：**Redis 跑在 Windows 节点**，Linux 靠 SSH 隧道连过去。
  所以 Windows 重启时，控制面（Redis 服务，Automatic 启动）能自动恢复，
  断的只是 miniQMT 与执行代理这一段。
- **券商仿真账户**：上面 4 项回报类演练用的是桩代理，验的是核心侧。等仿真账户到位后
  应当用真链路（含 QMT 适配器）再复验一遍。
