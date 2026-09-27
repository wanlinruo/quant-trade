"""上线前故障演练：每项先写明「应该怎样」，再跑出「实际怎样」，不一致就判失败。

清单见 docs/deploy/live-trading.md 阶段 3.3。券商回报由 scripts/drill_agent.py
的桩代理产生，所以在拿到券商仿真账户之前也能把**核心侧的处理逻辑**验一遍。

跑在完全隔离的实例上：流名一律 ``drill:`` 前缀、独立状态库、独立 kill 文件。
真代理不消费这些流，生产影子盘不受影响。脚本启动时会拒绝非 drill: 前缀的配置。

    uv run python scripts/run_drills.py --config config/drill.toml
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from qsys.core.portfolio import Portfolio
from qsys.core.trading import Order, OrderIntent, OrderType, Side
from qsys.core.types import Symbol
from qsys.live.config import load_deploy_config
from qsys.live.heartbeat import HeartbeatMonitor
from qsys.live.kill_switch import KillSwitch
from qsys.live.live_broker import LiveBroker
from qsys.live.messages import Heartbeat
from qsys.live.risk import KillSwitchRule, RiskContext, RiskGate
from qsys.live.transport import Transport

def _blank_account():
    """LiveStore.initialize 要一份券商快照；演练用空仓。"""
    from qsys.live.messages import AccountSnapshot

    return AccountSnapshot(cash=1_000_000.0, total_asset=1_000_000.0,
                           market_value=0.0, positions={}, at=datetime.now())


SYMBOL = Symbol.parse("600000.SH")
PRICE = 10.0


@dataclass
class Outcome:
    name: str
    expected: str
    actual: str = ""
    passed: bool = False
    note: str = ""


@dataclass
class Drills:
    transport: Transport
    streams: object
    results: list[Outcome] = field(default_factory=list)
    _seq: int = 0

    def record(self, name, expected, actual, passed, note="") -> None:
        self.results.append(Outcome(name, expected, actual, passed, note))

    def order(self, qty: int = 1000, side: Side = Side.BUY) -> Order:
        self._seq += 1
        return Order(
            order_id=f"drill-{datetime.now():%H%M%S}-{self._seq}",
            symbol=SYMBOL, side=side, qty=qty,
            order_type=OrderType.LIMIT, limit_price=PRICE,
            created_at=datetime.now(),
        )

    def lane(self, name: str):
        """每项演练独占一组流名。

        LiveBroker 建消费组时固定从 0-0 起读，同一条流上换个组就会把前面演练的
        回报再读一遍——第一次跑就是这样把「撤单」判成有成交的。隔开才测得准。
        """
        return replace(
            self.streams,
            orders=f"{self.streams.orders}:{name}",
            fills=f"{self.streams.fills}:{name}",
            rejects=f"{self.streams.rejects}:{name}",
            order_status=f"{self.streams.order_status}:{name}",
            control=f"{self.streams.control}:{name}",
        )

    def broker(self, streams, group: str = "drill-core") -> LiveBroker:
        """一个什么都放行的 broker：演练验的是回报处理，不是风控。"""
        return LiveBroker(
            self.transport, streams, risk_gate=RiskGate([]),
            risk_context_factory=lambda: RiskContext(
                Portfolio(1_000_000.0), datetime.now(), PRICE, 1_000_000.0
            ),
            group=group, consumer="drill-core-1",
        )


def _respond(drills: Drills, streams, scenario: str) -> None:
    from drill_agent import respond_once

    respond_once(drills.transport, streams, scenario, price=PRICE)


# ---- 逐项演练 ----

def drill_partial_fill(d: Drills) -> None:
    """部分成交：剩余量要留在未了结委托里，不能当成全部成交清掉。"""
    streams = d.lane("partial")
    broker, order = d.broker(streams), d.order(qty=1000)
    broker.submit(order)
    _respond(d, streams, "partial")
    fills = broker.poll_fills()

    filled = sum(f.qty for f in fills)
    remaining = broker.remaining(order.order_id)
    ok = len(fills) == 1 and 0 < filled < 1000 and remaining == 1000 - filled
    d.record("部分成交", "1 笔成交，剩余量 = 委托量 − 已成交，委托仍未了结",
             f"{len(fills)} 笔成交 {filled} 股，剩余 {remaining} 股", ok)


def drill_cancel(d: Drills) -> None:
    """撤单：状态流要看得到 cancelled，且不产生任何成交。"""
    streams = d.lane("cancel")
    broker, order = d.broker(streams), d.order(qty=1000)
    broker.submit(order)
    _respond(d, streams, "cancel")
    fills = broker.poll_fills()
    statuses = broker.poll_statuses()

    seen = [s.status for s in statuses if s.client_order_id == order.order_id]
    ok = not fills and "cancelled" in seen
    d.record("撤单", "状态出现 cancelled，且无成交",
             f"状态 {seen or '无'}，成交 {len(fills)} 笔", ok)


def drill_reject(d: Drills) -> None:
    """废单：要变成一条带原因的拒单，并把未了结委托清掉。"""
    streams = d.lane("reject")
    broker, order = d.broker(streams), d.order(qty=1000)
    broker.submit(order)
    _respond(d, streams, "reject")
    rejects = broker.poll_rejects()

    mine = [r for r in rejects if r.order_id == order.order_id]
    ok = len(mine) == 1 and broker.remaining(order.order_id) == 0 and bool(mine[0].reason)
    d.record("废单", "1 条带原因的拒单，委托不再挂着",
             f"{len(mine)} 条拒单（{mine[0].reason if mine else '无'}），"
             f"剩余 {broker.remaining(order.order_id)} 股", ok)


def drill_duplicate(d: Drills) -> None:
    """重复消息：同一个 broker_trade_id 投两次，只能算一笔成交。"""
    streams = d.lane("duplicate")
    broker, order = d.broker(streams), d.order(qty=1000)
    broker.submit(order)
    _respond(d, streams, "duplicate")
    fills = broker.poll_fills()

    filled = sum(f.qty for f in fills)
    ok = len(fills) == 1 and filled == 1000
    d.record("重复消息", "重复的成交回报被去重，只入账 1 笔 1000 股",
             f"{len(fills)} 笔，共 {filled} 股", ok)


def drill_duplicate_after_restart(d: Drills, state_path: Path) -> None:
    """重启后重复投递：核心换了一个进程，同一笔成交还认得出来吗？

    broker 层的去重集合 ``_seen_trade_ids`` 在内存里，重启即清空——所以这一项
    真正该验的是**落盘那层**：runtime._record_results 先用 store.fill_recorded()
    过滤、再并进组合，重放的成交必须在碰到组合之前就被挡掉。
    """
    from qsys.live.store import LiveStore

    streams = d.lane("restart")
    broker, order = d.broker(streams), d.order(qty=1000)
    broker.submit(order)
    _respond(d, streams, "normal")
    first = broker.poll_fills()

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.unlink(missing_ok=True)
    store = LiveStore(state_path)
    portfolio = Portfolio(1_000_000.0)
    for fill in first:
        portfolio.apply_fill(fill)
    store.initialize(_blank_account())
    store.commit_reports(portfolio, {}, {}, first, (), (), {}, at=datetime.now())

    # 换一个进程 = 新的 broker、新的 store 句柄；券商把同一笔成交又投了一次
    revived_store = LiveStore(state_path)
    replayed = first[0]
    blocked = revived_store.fill_recorded(replayed)

    d.record(
        "重启后重复投递", "重启后重放同一笔成交，落盘去重要在并入组合之前挡住",
        f"重启前入账 {len(first)} 笔；换新进程后重放同一 trade_id，"
        f"{'被 store.fill_recorded 挡住' if blocked else '未被挡住，会重复计数'}",
        blocked,
        note="broker 层的内存去重重启即失效，真正兜底的是 live_runtime_fills "
             "上 broker_trade_id 的唯一索引",
    )


def drill_heartbeat_timeout(d: Drills) -> None:
    """心跳超时：代理心跳过期，必须判定为 dead，核心据此停开仓。"""
    stale = datetime.now().replace(year=datetime.now().year - 1)
    d.transport.set_key(f"{d.streams.heartbeat}:agent",
                        Heartbeat(source="agent", at=stale).model_dump_json())
    monitor = HeartbeatMonitor(d.transport, prefix=d.streams.heartbeat)

    dead = monitor.dead_sources(["agent"], max_age_seconds=10.0)

    d.record("心跳超时", "过期心跳被判为 dead（核心据此停机并撤单）",
             f"dead = {list(dead) or '无'}", list(dead) == ["agent"])


def drill_kill_file(d: Drills, kill_path: Path) -> None:
    """kill 文件：只碰文件系统就能停机，且停的是开仓、不锁死减仓。"""
    switch = KillSwitch(d.transport, key=f"{d.streams.heartbeat}:drill-kill",
                        local_path=kill_path)
    switch.release()
    before = switch.is_engaged()
    kill_path.parent.mkdir(parents=True, exist_ok=True)
    kill_path.write_text("drill: 手工放置")  # 不经 Redis，只碰文件系统

    engaged = switch.is_engaged()
    gate = RiskGate([KillSwitchRule(switch)])
    ctx = RiskContext(Portfolio(1_000_000.0), datetime.now(), PRICE, 1_000_000.0)
    buy = gate.check(OrderIntent(SYMBOL, Side.BUY, 100, OrderType.LIMIT, PRICE), ctx)
    sell = gate.check(OrderIntent(SYMBOL, Side.SELL, 100, OrderType.LIMIT, PRICE), ctx)
    switch.release()

    ok = not before and engaged and not buy.allowed and sell.allowed
    d.record("kill 文件", "只写文件即停机；买单被拒、卖单仍放行",
             f"放置前 {before}，放置后 {engaged}；"
             f"买单 {'拒' if not buy.allowed else '放行'}、"
             f"卖单 {'放行' if sell.allowed else '拒'}", ok)


def drill_redis_outage(d: Drills, kill_path: Path) -> None:
    """Redis 断线：停机路径不能依赖 Redis，本地标记必须仍然有效。"""
    class Dead:
        def get_key(self, *_a, **_k):
            raise ConnectionError("drill: Redis 不可达")

        def set_key(self, *_a, **_k):
            raise ConnectionError("drill: Redis 不可达")

        def delete_key(self, *_a, **_k):
            raise ConnectionError("drill: Redis 不可达")

    kill_path.parent.mkdir(parents=True, exist_ok=True)
    kill_path.write_text("drill: Redis 断线期间停机")
    switch = KillSwitch(Dead(), key="drill:unreachable", local_path=kill_path)
    try:
        engaged = switch.is_engaged()
        reason = "本地标记生效"
    except ConnectionError as exc:
        engaged, reason = False, f"抛出 {exc}，停不下来"
    kill_path.unlink(missing_ok=True)

    d.record("Redis 断线", "Redis 不可达时，本地 kill 标记仍能判定为停机",
             reason, engaged)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--kill-file", default="var/drill/KILL")
    parser.add_argument("--state", default="var/drill/state.sqlite3")
    parser.add_argument("--json", type=Path, default=None, help="演练结果落盘位置")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    config = load_deploy_config(args.config)
    if not config.streams.orders.startswith("drill:"):
        raise SystemExit("拒绝运行：流前缀不是 drill:，演练只在隔离实例上做")

    from qsys.live.redis_transport import RedisTransport

    transport = RedisTransport.from_config(config)
    d = Drills(transport, config.streams)
    kill_path = Path(args.kill_file)

    drill_partial_fill(d)
    drill_cancel(d)
    drill_reject(d)
    drill_duplicate(d)
    drill_duplicate_after_restart(d, Path(args.state))
    drill_heartbeat_timeout(d)
    drill_kill_file(d, kill_path)
    drill_redis_outage(d, kill_path)

    width = max(len(r.name) for r in d.results)
    for r in d.results:
        print(f"[{'PASS' if r.passed else 'FAIL'}] {r.name:<{width}}  "
              f"期望：{r.expected}；实际：{r.actual}")
        if r.note:
            print(f"       备注：{r.note}")
    failed = [r for r in d.results if not r.passed]
    print(f"\n故障演练：{len(d.results)} 项，通过 {len(d.results) - len(failed)}，"
          f"未通过 {len(failed)}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(
            {"at": datetime.now().isoformat(),
             "results": [vars(r) for r in d.results]},
            ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
