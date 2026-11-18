"""停机闸解除前核对（#662 计划 05；设计文档 §8.5、§13 第 6 项）。

受管入口 ``scripts/kill.py review live`` 的判定与采集。只读：不写 Redis、不改 KILL、不发控制指令、
不写状态库。逐项给出当前真实状态与判定，供人与页面阅读；拿不到的数据为 unknown，不当通过。

总体结论：任一项 fail/unknown → blocked；否则任一项 confirm → needs_confirmation；否则 ready。
券商活动委托多于本系统未终结委托时要求维护者确认来源（#666），不推断为人工委托。
项目 ID 稳定，新增只追加不改名。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, JsonValue, field_validator

from qsys.live.messages import AccountSnapshot, Heartbeat
from qsys.live.preflight_cli import heartbeat_freshness
from qsys.live.reconcile import reconcile_positions

SHANGHAI = ZoneInfo("Asia/Shanghai")
ACCOUNT_MAX_AGE_S = 5.0  # 与盘前检查一致
ITEM_IDS = (
    "gates.state", "trigger.cleared", "link.agent_heartbeat", "link.market_heartbeat", "link.agent_posture",
    "account.snapshot", "reconcile.positions_cash", "orders.own_pending", "orders.broker_vs_own",
    "maintainer.confirmation",
)
State = Literal["pass", "fail", "unknown", "confirm", "info"]


class ReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    label: str
    state: State
    detail: str
    observed: dict[str, JsonValue] = {}


class KillReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    target: Literal["live"] = "live"
    reviewed_at: datetime
    overall: Literal["ready", "blocked", "needs_confirmation"]
    engaged: bool
    items: list[ReviewItem]

    @field_validator("reviewed_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("reviewed_at 必须带时区")
        return v


@dataclass(frozen=True)
class ReviewInputs:
    local_kill_reason: str | None      # None：本地闸未落；"" ：已落但无原因
    redis_kill_reason: str | None      # redis_ok=False 时无意义
    redis_ok: bool
    agent: Heartbeat | None
    market: Heartbeat | None
    account: AccountSnapshot | None
    portfolio: object | None           # qsys.core.portfolio.Portfolio
    own_pending: int | None
    state_error: str | None = None


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo is not None else at.replace(tzinfo=SHANGHAI)


def _item(id: str, label: str, state: State, detail: str, **observed) -> ReviewItem:
    return ReviewItem(id=id, label=label, state=state, detail=detail, observed=observed)


def _heartbeat(id: str, label: str, beat: Heartbeat | None, redis_ok: bool, now: datetime) -> ReviewItem:
    if not redis_ok or beat is None:
        return _item(id, label, "unknown", "读不到心跳（Redis 不可达或键缺失）")
    fresh, age, threshold = heartbeat_freshness(beat.model_copy(update={"at": _aware(beat.at)}), now)
    return _item(id, label, "pass" if fresh else "fail", f"心跳 {age:.1f}s（阈值 {threshold:.1f}s）",
                 age_s=round(age, 1), threshold_s=threshold)


def review(inputs: ReviewInputs, *, confirmed_by: str | None, now: datetime) -> KillReview:
    if now.tzinfo is None:
        raise ValueError("now 必须带时区")
    i = inputs
    gates = {
        "linux_local": i.local_kill_reason is not None,
        "redis": (i.redis_kill_reason is not None) if i.redis_ok else None,
        "windows_gateway": i.agent.local_kill_engaged if i.agent is not None else None,
    }
    engaged = any(v is True for v in gates.values())
    items: dict[str, ReviewItem] = {}
    items["gates.state"] = _item(
        "gates.state", "三道停机闸状态", "info",
        "；".join(f"{k}={'已落' if v else ('未落' if v is False else '未知')}" for k, v in gates.items()), **gates)

    items["link.agent_heartbeat"] = _heartbeat("link.agent_heartbeat", "执行端（agent）心跳", i.agent, i.redis_ok, now)
    items["link.market_heartbeat"] = _heartbeat("link.market_heartbeat", "行情（market）心跳", i.market, i.redis_ok, now)

    if not i.redis_ok or i.agent is None:
        posture = _item("link.agent_posture", "执行端交易姿态", "unknown", "读不到 agent 心跳")
    else:
        flags = {"trading_mode_live": i.agent.trading_mode == "live", "orders_enabled": i.agent.orders_enabled is True,
                 "account_protected": i.agent.account_protected is True}
        posture = _item("link.agent_posture", "执行端交易姿态", "pass" if all(flags.values()) else "fail",
                        "；".join(f"{k}={v}" for k, v in flags.items()), **flags)
    items["link.agent_posture"] = posture

    if not i.redis_ok or i.account is None:
        items["account.snapshot"] = _item("account.snapshot", "券商账户快照", "unknown", "读不到账户快照")
    else:
        age = (now - _aware(i.account.at)).total_seconds()
        ok = -1 <= age <= ACCOUNT_MAX_AGE_S
        items["account.snapshot"] = _item("account.snapshot", "券商账户快照", "pass" if ok else "fail",
                                          f"快照年龄 {age:.1f}s（阈值 {ACCOUNT_MAX_AGE_S:.0f}s）", age_s=round(age, 1))

    if i.portfolio is None or i.account is None or not i.redis_ok:
        why = f"实盘状态库不可读：{i.state_error}" if i.portfolio is None else "读不到账户快照"
        items["reconcile.positions_cash"] = _item("reconcile.positions_cash", "资金与持仓对账", "unknown", why)
    else:
        result = reconcile_positions(i.portfolio, i.account, cash_decides=True)
        items["reconcile.positions_cash"] = _item(
            "reconcile.positions_cash", "资金与持仓对账", "pass" if result.is_consistent else "fail", result.summary(),
            cash_diff=result.cash_diff, mismatches=len(result.mismatches))

    if i.own_pending is None:
        items["orders.own_pending"] = _item("orders.own_pending", "本系统未终结委托", "unknown",
                                            f"实盘状态库不可读：{i.state_error}")
    else:
        items["orders.own_pending"] = _item(
            "orders.own_pending", "本系统未终结委托", "pass" if i.own_pending == 0 else "confirm",
            "无" if i.own_pending == 0 else f"{i.own_pending} 笔未终结，确认其券商状态后再解除",
            own_pending=i.own_pending)

    broker = i.account.active_order_count if (i.redis_ok and i.account is not None) else None
    if broker is None or i.own_pending is None:
        items["orders.broker_vs_own"] = _item("orders.broker_vs_own", "券商活动委托与本系统账本", "unknown",
                                              "券商活动委托数或本系统账本读不到")
    elif broker == i.own_pending:
        items["orders.broker_vs_own"] = _item("orders.broker_vs_own", "券商活动委托与本系统账本", "pass",
                                              f"一致（{broker} 笔）", broker=broker, own=i.own_pending)
    elif broker > i.own_pending:
        items["orders.broker_vs_own"] = _item(
            "orders.broker_vs_own", "券商活动委托与本系统账本", "confirm",
            f"券商比本系统多 {broker - i.own_pending} 笔，须维护者确认来源（#666，不推断为人工委托）",
            broker=broker, own=i.own_pending)
    else:
        items["orders.broker_vs_own"] = _item(
            "orders.broker_vs_own", "券商活动委托与本系统账本", "fail",
            f"本系统账本比券商多 {i.own_pending - broker} 笔，须先对账", broker=broker, own=i.own_pending)

    reason = i.local_kill_reason if i.local_kill_reason is not None else (i.redis_kill_reason or "")
    lower = reason.lower()
    if not engaged:
        trigger = _item("trigger.cleared", "触发原因是否消除", "pass", "停机闸未落", reason=None)
    elif "heartbeat timeout" in lower or "心跳超时" in reason:
        ok = items["link.agent_heartbeat"].state == items["link.market_heartbeat"].state == "pass"
        unknown = "unknown" in (items["link.agent_heartbeat"].state, items["link.market_heartbeat"].state)
        trigger = _item("trigger.cleared", "触发原因是否消除", "pass" if ok else ("unknown" if unknown else "fail"),
                        f"心跳超时触发：以当前心跳判定（{reason}）", reason=reason)
    elif "reconcile" in lower or "对账" in reason:
        state = items["reconcile.positions_cash"].state
        trigger = _item("trigger.cleared", "触发原因是否消除", state if state in ("pass", "fail", "unknown") else "unknown",
                        f"对账不一致触发：以当前对账判定（{reason}）", reason=reason)
    else:
        trigger = _item("trigger.cleared", "触发原因是否消除", "confirm",
                        f"该原因无法自动复查，须维护者确认已消除：{reason or '（无原因）'}", reason=reason)
    items["trigger.cleared"] = trigger

    items["maintainer.confirmation"] = (
        _item("maintainer.confirmation", "维护者确认", "pass", f"已确认：{confirmed_by}", confirmed_by=confirmed_by)
        if confirmed_by else
        _item("maintainer.confirmation", "维护者确认", "confirm", "尚未确认（--confirmed-by）"))

    ordered = [items[k] for k in ITEM_IDS]
    states = {x.state for x in ordered}
    overall = ("blocked" if states & {"fail", "unknown"} else
               "needs_confirmation" if "confirm" in states else "ready")
    return KillReview(reviewed_at=now, overall=overall, engaged=engaged, items=ordered)


def gather(transport, *, redis_ok: bool, heartbeat_prefix: str, account_key: str, kill_key: str,
           local_kill: Path | None, state: Path) -> ReviewInputs:
    """只读采集。任一 Redis 读取异常即视为 Redis 不可用（相应字段为 None，判定为 unknown）。"""
    local_reason = None
    if local_kill is not None and local_kill.exists():
        local_reason = local_kill.read_text(encoding="utf-8").strip()
    redis_reason = agent = market = account = None
    if redis_ok:
        try:
            redis_reason = transport.get_key(kill_key)
            raw = transport.get_key(f"{heartbeat_prefix}:agent")
            agent = Heartbeat.model_validate_json(raw) if raw else None
            raw = transport.get_key(f"{heartbeat_prefix}:market")
            market = Heartbeat.model_validate_json(raw) if raw else None
            raw = transport.get_key(account_key)
            account = AccountSnapshot.model_validate_json(raw) if raw else None
        except Exception:  # noqa: BLE001 —— 读不到就是未知，不能让复核本身失败
            redis_ok, redis_reason, agent, market, account = False, None, None, None, None
    portfolio = own_pending = None
    state_error = None
    if not Path(state).is_file():
        state_error = f"状态库不存在：{Path(state).name}"
    else:
        store = _cold_safe_store(Path(state))
        try:
            portfolio = store.load()[0]
            own_pending = len(store.pending_orders())
        except Exception as exc:  # noqa: BLE001
            portfolio, own_pending, state_error = None, None, type(exc).__name__
        finally:
            store.close()
    return ReviewInputs(local_kill_reason=local_reason, redis_kill_reason=redis_reason, redis_ok=redis_ok,
                        agent=agent, market=market, account=account, portfolio=portfolio,
                        own_pending=own_pending, state_error=state_error)


def _cold_safe_store(path: Path):
    """只读打开实盘状态库，且不在状态目录创建任何文件。

    生产者在运行时持有 WAL/SHM（pin_reader_sidecars），按 mode=ro 读即可。
    冷库（生产者已停、没有 WAL 文件）时，mode=ro 也会新建 -wal/-shm，属主是复核者而非 live-core
    运行账号，可能让 live-core 之后打不开（#529/#544）。此时库文件就是完整内容，没有 WAL 可忽略，
    用 immutable=1 读取。存在 WAL 时绝不使用 immutable（AGENTS.md：不得用 immutable 忽略 WAL）。
    """
    import sqlite3

    from qsys.live.store import LiveStore

    class _Reader(LiveStore):
        def _connect(self):
            target = self.path
            cold = not target.with_name(target.name + "-wal").exists()
            uri = target.resolve().as_uri() + ("?mode=ro&immutable=1" if cold else "?mode=ro")
            con = sqlite3.connect(uri, uri=True, timeout=30)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA query_only=ON")
            return con

    return _Reader(path, read_only=True)


def render(r: KillReview) -> str:
    mark = {"pass": "PASS", "fail": "FAIL", "unknown": "未知", "confirm": "待确认", "info": "信息"}
    lines = [f"[{mark[i.state]}] {i.id} {i.label}：{i.detail}" for i in r.items]
    lines.append(f"\n结论：{r.overall}（{r.reviewed_at:%Y-%m-%d %H:%M:%S}）")
    return "\n".join(lines)
