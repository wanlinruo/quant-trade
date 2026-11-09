"""Windows 执行代理：大 QMT 内置执行器通道（#601）。

替代 ``run_agent.py`` 中写死的 XtQuantAdapter；Linux 协议与全部 agent 门禁不变。
须在 QMT 所在的交互 session 中运行（``Local\\`` 共享内存按 session 隔离）::

  python scripts/run_qmt_agent.py --config config/deploy.local.toml \
      --qmt-private <bundle>/private --state-root <私有目录> --strategy-id structural
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import structlog

from qsys.agent.async_dispatch import open_qmt_dispatcher, open_validation_dispatcher
from qsys.agent.execution_agent import ExecutionAgent
from qsys.agent.locking import single_instance
from qsys.agent.main_loop import run_forever
from qsys.agent.qmt.adapter import QmtIpcAdapter
from qsys.agent.qmt.link import QmtLink
from qsys.agent.qmt.memory import SharedMemory, open_named
from qsys.agent.qmt.port import QmtIpcPort
from qsys.agent.qmt.reconcile import UnknownReconciler
from qsys.agent.report_ledger import SQLiteReportLedger
from qsys.agent.stall_guard import StallGuard
from qsys.backtest.fees import AShareFeeModel
from qsys.live.config import DeployConfig, load_deploy_config
from qsys.live.safety import SafetyPolicy, TradingMode

log = structlog.get_logger("qmt-agent")


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def read_private(path: Path) -> tuple[str, bytes, str]:
    run = json.loads((path / "run.json").read_text(encoding="ascii"))
    key = bytes.fromhex((path / "key.hex").read_text(encoding="ascii").strip())
    account = (path / "account.txt").read_text(encoding="ascii").strip()
    if len(key) != 32 or not account.isdigit():
        raise ValueError("QMT private binding malformed")
    return run["run_id"], key, account


def account_scope(account: str, *, validation: bool) -> str:
    digest = hashlib.sha256(account.encode("ascii")).hexdigest()[:16]
    return ("validation:" if validation else "qmt:") + digest


@contextmanager
def assemble(cfg: DeployConfig, transport, memory: SharedMemory, *, run_id: str, key: bytes,
             account: str, state_root: Path, ledger: Path, strategy_id: str,
             validation_namespace: str | None = None, sleep=time.sleep,
             clock=datetime.now, clock_ms=_now_ms):
    """组装 agent；生产与验证命名空间共用，测试以内存传输与共享内存驱动。"""
    port = QmtIpcPort(memory, run_id=run_id, key=key, clock_ms=clock_ms, sleep=sleep)
    adapter = QmtIpcAdapter(memory, port)
    validation = validation_namespace is not None
    scope = account_scope(account, validation=validation)
    if validation:
        factory = open_validation_dispatcher(
            enabled=True, root=state_root, transport=transport, streams=cfg.streams,
            account_scope=scope, strategy_id=strategy_id,
            session_id=clock().date().isoformat(), adapter_factory=lambda: port, clock=clock)
    else:
        factory = open_qmt_dispatcher(
            enabled=True, root=state_root, transport=transport, streams=cfg.streams, port=port,
            account_scope=scope, strategy_id=strategy_id,
            session_of=lambda: clock().date().isoformat(), clock=clock)
    with factory as dispatcher:
        reconciler = UnknownReconciler(dispatcher, adapter, memory, clock=clock)
        agent = ExecutionAgent(
            transport, adapter, cfg.streams, consumer="qmt-agent",
            ledger=SQLiteReportLedger(ledger),
            fee_model=AShareFeeModel(
                commission_rate=cfg.fees.commission_rate, min_commission=cfg.fees.min_commission,
                stamp_duty_rate=cfg.fees.stamp_duty_rate,
                transfer_fee_rate=cfg.fees.transfer_fee_rate),
            safety=SafetyPolicy(
                TradingMode(cfg.safety.mode), account_id=account,
                protected_account_ids=cfg.safety.protected_account_ids,
                orders_enabled=cfg.safety.orders_enabled,
                local_kill_path=cfg.safety.local_kill_path),
            async_dispatcher=dispatcher,
            cycle_hook=QmtLink(memory, reconciler, clock_ms=clock_ms),
        )
        yield agent, adapter


def wait_scheduled(memory: SharedMemory, run_id: str, timeout_s: float, sleep=time.sleep) -> bool:
    deadline = time.monotonic() + timeout_s
    while not memory.scheduled(run_id):
        if time.monotonic() >= deadline:
            return False
        memory.beat(_now_ms())
        sleep(1.0)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Windows 执行代理（大 QMT 执行器通道）")
    parser.add_argument("--config", default=None)
    parser.add_argument("--qmt-private", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--strategy-id", required=True)
    parser.add_argument("--ledger", default="var/qmt-agent-ledger.sqlite3")
    parser.add_argument("--lock", default="var/qmt-agent.lock")
    parser.add_argument("--validation-namespace", default=None)
    parser.add_argument("--scheduled-timeout", type=float, default=300.0)
    parser.add_argument("--stall-timeout", type=float, default=60.0)
    args = parser.parse_args(argv)

    cfg = load_deploy_config(args.config)
    from qsys.live.redis_transport import RedisTransport
    transport = RedisTransport.from_config(cfg)
    if args.validation_namespace:
        from qsys.live.validation_transport import ValidationTransport
        transport = ValidationTransport(transport, args.validation_namespace)
    run_id, key, account = read_private(args.qmt_private)

    def _die_on_stall(reason: str) -> None:
        log.error("agent.stalled", reason=reason)
        os._exit(1)

    with ExitStack() as stack:
        stack.enter_context(single_instance(args.lock))
        memory = open_named(run_id)
        memory.write_coordinator_marker(run_id)
        if not wait_scheduled(memory, run_id, args.scheduled_timeout):
            log.error("qmt.executor_not_scheduled", run_id=run_id[:8])
            return 3
        agent, adapter = stack.enter_context(assemble(
            cfg, transport, memory, run_id=run_id, key=key, account=account,
            state_root=args.state_root, ledger=Path(args.ledger), strategy_id=args.strategy_id,
            validation_namespace=args.validation_namespace))
        guard = StallGuard(args.stall_timeout, _die_on_stall) if args.stall_timeout > 0 else None
        log.info("qmt-agent.started", mode=cfg.safety.mode, run=run_id[:8],
                 validation=bool(args.validation_namespace), day=date.today().isoformat())
        if guard is not None:
            guard.start()
        run_forever(agent, adapter, guard=guard)
    return 0


if __name__ == "__main__":
    sys.exit(main())
