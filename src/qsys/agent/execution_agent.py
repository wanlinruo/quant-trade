"""Windows 执行代理。

**这里不放任何策略逻辑。** 只做三件事：
  1. 把标准化指令翻译成券商调用
  2. 把回报翻译回标准化事件
  3. 定期发布账户快照与心跳

代码量应小到可一眼读完——它跑在难调试的 Windows 上。
"""

from __future__ import annotations

from datetime import datetime

import structlog

from qsys.agent.adapter import BrokerAdapter
from qsys.agent.fill_tracker import FillTracker
from qsys.agent.report_ledger import MemoryReportLedger, ReportLedger
from qsys.backtest.fees import AShareFeeModel, FeeModel
from qsys.live.config import StreamConfig
from qsys.live.idempotency import IdempotencyGuard
from qsys.live.messages import (
    AccountSnapshot,
    ControlCommand,
    FillEvent,
    Heartbeat,
    OrderCommand,
    RejectEvent,
)
from qsys.live.safety import SafetyPolicy
from qsys.live.safety import TradingMode
from qsys.live.transport import Transport

log = structlog.get_logger(__name__)


class ExecutionAgent:
    def __init__(
        self,
        transport: Transport,
        adapter: BrokerAdapter,
        streams: StreamConfig,
        *,
        group: str = "agent",
        consumer: str = "agent-1",
        guard: IdempotencyGuard | None = None,
        ledger: ReportLedger | None = None,
        fee_model: FeeModel | None = None,
        safety: SafetyPolicy | None = None,
    ) -> None:
        self._t = transport
        self._adapter = adapter
        self._s = streams
        self._group = group
        self._consumer = consumer
        self._guard = guard or IdempotencyGuard(transport)
        self._ledger = ledger or MemoryReportLedger()
        self._tracker = FillTracker(fee_model or AShareFeeModel())
        self._safety = safety
        self._remote_kill_seen = False
        self._started_at = datetime.now()
        for stream in (streams.orders, streams.control):
            # 首次部署时不能跳过进程启动前已经进入 Stream 的控制与订单指令。
            self._t.ensure_group(stream, group, start_id="0-0")

    def run_once(self) -> int:
        """处理一批指令，返回处理条数。"""
        self._sync_remote_kill()
        # 停机指令必须先于同一轮的订单处理，避免已经要求停机却又多发一批单。
        handled = self._drain_control() + self._drain_orders()
        self.poll_reports()
        return handled

    def _sync_remote_kill(self) -> None:
        reason = self._t.get_key("control:kill")
        if reason is None or self._remote_kill_seen:
            return
        self._remote_kill_seen = True
        self._adapter.cancel_all()
        if self._safety is not None:
            self._safety.engage_local_kill(f"remote kill: {reason}")
        log.warning("agent.remote_kill", reason=reason)

    def enforce_core_heartbeat(
        self, *, max_age_seconds: float = 10.0, startup_grace_seconds: float = 30.0,
    ) -> bool:
        """LIVE 模式下核心失联即撤单并在 Windows 本地落闸。"""
        if self._safety is None or self._safety.mode is not TradingMode.LIVE:
            return True
        raw = self._t.get_key(f"{self._s.heartbeat}:core")
        alive = False
        if raw is not None:
            try:
                beat = Heartbeat.model_validate_json(raw)
                alive = (datetime.now() - beat.at).total_seconds() <= max_age_seconds
            except Exception:
                alive = False
        if alive:
            return True
        if (datetime.now() - self._started_at).total_seconds() <= startup_grace_seconds:
            return False
        reason = "core heartbeat timeout"
        self._adapter.cancel_all()
        self._safety.engage_local_kill(reason)
        log.error("agent.core_heartbeat_timeout")
        return False

    def _drain_orders(self) -> int:
        handled = 0
        messages = self._t.pending(self._s.orders, self._group)
        messages += self._t.read_group(
            self._s.orders, self._group, self._consumer, count=50
        )
        for msg in {item.id: item for item in messages}.values():
            handled += 1
            try:
                command = OrderCommand.from_payload(msg.payload)
            except Exception as exc:  # 畸形消息不应让代理崩溃
                log.error("agent.malformed_order", error=str(exc), payload=msg.payload)
                self._t.ack(self._s.orders, self._group, msg.id)
                continue
            self._execute(command)
            # 只有券商调用结果或拒单已经持久化/发布后才确认消息。
            self._t.ack(self._s.orders, self._group, msg.id)
        return handled

    def _execute(self, command: OrderCommand) -> None:
        reducing = command.side == "sell"
        if self._t.get_key("control:kill") is not None and not reducing:
            self._reject(command, "safety: remote kill switch is engaged")
            return
        if command.expires_at is not None and datetime.now() > command.expires_at:
            self._reject(command, "order command expired")
            return
        if self._safety is not None:
            verdict = self._safety.check(reducing=reducing)
            if not verdict.allowed:
                self._reject(command, f"safety: {verdict.reason}")
                return

        def place() -> str:
            existing = self._adapter.find_order(command.client_order_id)
            if existing is not None:
                return existing
            return self._adapter.place_order(
                command.symbol, command.side, command.qty,
                command.order_type, command.limit_price, command.client_order_id,
            )

        try:
            broker_order_id, replayed = self._guard.process_once(
                command.client_order_id, place
            )
        except Exception as exc:
            # 失败不记入已处理集合，允许重试
            log.error("agent.place_failed", client_order_id=command.client_order_id,
                      error=str(exc))
            self._reject(command, str(exc))
            return

        if replayed:
            log.info("agent.duplicate_ignored", client_order_id=command.client_order_id)
            return
        self._ledger.remember_order(command.client_order_id, broker_order_id)
        log.info("agent.placed", client_order_id=command.client_order_id,
                 broker_order_id=broker_order_id)

    def _reject(self, command: OrderCommand, reason: str) -> None:
        self._t.append(
            self._s.rejects,
            RejectEvent(
                client_order_id=command.client_order_id,
                symbol=command.symbol,
                reason=reason,
                at=datetime.now(),
                side=command.side,
                unfilled_qty=command.qty,
            ).to_payload(),
        )

    def poll_reports(self) -> int:
        """发布券商真实回报；重复轮询和重启均不会重复记账。"""
        output = self._tracker.observe(
            self._adapter.query_trades(), self._adapter.query_orders(),
            seen_trade_ids=self._ledger.seen_trade_ids(),
            closed_order_ids=self._ledger.closed_order_ids(), now=datetime.now(),
        )
        published = 0
        for event in output.fills:
            self._t.append(self._s.fills, event.to_payload())
            if event.broker_trade_id:
                self._ledger.remember_trade(event.broker_trade_id)
            published += 1
        for event in output.rejects:
            self._t.append(self._s.rejects, event.to_payload())
            published += 1
        for event in output.statuses:
            signature = f"{event.status}:{event.filled_qty}:{event.remaining_qty}:{event.reason}"
            if self._ledger.last_status(event.client_order_id) == signature:
                continue
            self._t.append(self._s.order_status, event.to_payload())
            self._ledger.remember_status(event.client_order_id, signature)
            published += 1
        for client_order_id in output.closed_order_ids:
            self._ledger.remember_closed(client_order_id)
        return published

    def _drain_control(self) -> int:
        handled = 0
        messages = self._t.pending(self._s.control, self._group)
        messages += self._t.read_group(
            self._s.control, self._group, self._consumer, count=50
        )
        for msg in {item.id: item for item in messages}.values():
            handled += 1
            try:
                command = ControlCommand.from_payload(msg.payload)
            except Exception as exc:
                log.error("agent.malformed_control", error=str(exc))
                self._t.ack(self._s.control, self._group, msg.id)
                continue
            if command.action == "cancel_all":
                self._adapter.cancel_all()
                log.warning("agent.cancelled_all", reason=command.reason)
            elif command.action == "kill":
                self._adapter.cancel_all()
                if self._safety is not None:
                    self._safety.engage_local_kill(command.reason or "remote kill")
                log.warning("agent.killed", reason=command.reason)
            elif command.action == "resume":
                # 恢复前先发布未完成委托/成交与最新账户；若任一步失败，本地闸
                # 仍保持闭合，不能出现“先放行、后发现账户异常”的窗口。
                self.poll_reports()
                self.publish_account()
                if self._safety is not None:
                    self._safety.release_local_kill()
                log.warning("agent.resumed", reason=command.reason)
            elif command.action == "query":
                self.publish_account()
            self._t.ack(self._s.control, self._group, msg.id)
        return handled

    def publish_account(self) -> None:
        account = self._adapter.query_account_details()
        snapshot = AccountSnapshot(
            at=datetime.now(), cash=account.cash, positions=account.positions,
            frozen_cash=account.frozen_cash, market_value=account.market_value,
            total_asset=account.total_asset,
        )
        self._t.set_key(self._s.account, snapshot.model_dump_json())

    def beat(self) -> None:
        self._t.set_key(
            f"{self._s.heartbeat}:agent",
            Heartbeat(source="agent", at=datetime.now()).model_dump_json(),
        )

    def engage_local_kill(self, reason: str) -> None:
        if self._safety is not None:
            self._safety.engage_local_kill(reason)
