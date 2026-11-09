"""Linux 实盘核心入口；默认配置为 disabled，不能仅靠启动脚本开启下单。"""

from __future__ import annotations

import argparse
import sys
import time
from contextlib import ExitStack, closing
from pathlib import Path

import structlog

from qsys.live.config import load_deploy_config
from qsys.live.redis_transport import RedisTransport
from qsys.live.runtime import LiveRuntime
from qsys.live.store import LiveStore
from qsys.agent.locking import single_instance
from qsys.live.lineage import StrategySource, resolve_strategy
from qsys.paper.quotes import DECISION_TIMEOUT, QuoteClient
from qsys.ops.alerts import AlertBus, ledger, strategy_sinks
from qsys.paper.calendar import load_trading_calendar
from qsys.data.cutover import add_facade_argument, apply_facade_argument

log = structlog.get_logger("live-core")


def _validate_isolated_paths(args, config, parser) -> None:
    if args.validation_namespace is None:
        if args.validation_root is not None:
            parser.error("--validation-root须与--validation-namespace一起使用")
        return
    if args.validation_root is None or not args.validation_root.is_absolute():
        parser.error("隔离预生产须显式指定绝对--validation-root")
    root = args.validation_root.resolve()
    if root == Path(root.anchor):
        parser.error("隔离预生产根目录不能为文件系统根")
    if config.alerts.strategy_url(shadow=False) or config.alerts.ops_url:
        parser.error("隔离预生产通知目标必须为空")
    for name in ("workspace", "state", "lock", "kill_switch", "journal_warehouse", "alert_ledger"):
        value = getattr(args, name)
        if value is None or not Path(value).is_absolute() or root not in Path(value).resolve().parents:
            parser.error(f"隔离预生产--{name.replace('_', '-')}须显式位于validation-root内")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="qsys 实盘策略核心")
    parser.add_argument("--strategy", default=None,
                        help="不给则按配置 [strategy]（可承袭模拟账户）确定")
    parser.add_argument("--workspace", default="var/live",
                        help="承袭模拟账户时，策略源码快照写在这里")
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--state", default="var/live-state.sqlite3")
    parser.add_argument("--journal-warehouse", default=None,
                        help="独立运行台账目录；默认兼容主warehouse")
    parser.add_argument("--alert-ledger", default=None,
                        help="独立告警流水；默认兼容var/alerts")
    parser.add_argument("--kill-switch", default="var/KILL",
                        help="本地急停文件；默认保持旧路径")
    parser.add_argument("--validation-namespace", default=None,
                        help="仅隔离预生产使用，统一隔离全部Redis地址")
    parser.add_argument("--validation-root", type=Path, default=None,
                        help="隔离预生产专属根目录；全部可写路径须位于其内")
    parser.add_argument("--config", default=None)
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument("--lock", default="var/live-core.lock")
    add_facade_argument(parser)
    args = parser.parse_args(argv)
    apply_facade_argument(args.market_data_facade)

    config = load_deploy_config(args.config)
    if config.safety.mode != "live" or not config.safety.orders_enabled:
        raise RuntimeError("实盘核心只在 [safety] mode='live' 且 orders_enabled=true 时启动")
    _validate_isolated_paths(args, config, parser)
    from qsys.live.state_binding import apply_entry_binding
    apply_entry_binding(args)
    with single_instance(args.lock), ExitStack() as resources:
        transport = RedisTransport.from_config(config)
        if args.validation_namespace is not None:
            from qsys.live.validation_transport import ValidationTransport

            transport = ValidationTransport(transport, args.validation_namespace)
        # CRITICAL 同时进运维群——实盘停机既是交易事件也是运维事故
        sinks = strategy_sinks(
            config.alerts.strategy_url(shadow=False), config.alerts.ops_url,
            timeout=config.alerts.timeout_seconds,
        )
        source = (StrategySource(Path(args.strategy)) if args.strategy
                  else resolve_strategy(config.strategy, Path(args.workspace)))
        store = resources.enter_context(closing(LiveStore(args.state)))
        store.pin_reader_sidecars()
        runtime = LiveRuntime(
            source.path, Path(args.warehouse), load_trading_calendar(args.config),
            QuoteClient(transport, config.streams, consumer="live-core",
                        timeout=DECISION_TIMEOUT),
            transport, config.streams, store,
            local_kill_path=Path(args.kill_switch),
            journal_warehouse=Path(args.journal_warehouse) if args.journal_warehouse else None,
            alerts=AlertBus(sinks, ledger(args.alert_ledger)),
            shadow=False, dispatch_tracking=config.safety.dispatch_tracking,
            risk=config.risk, params=dict(source.params),
        )
        with runtime.heartbeats:
            log.info("live_core.started", strategy=str(source.path),
                     follows=source.paper_account_id or None)
            consecutive_errors = 0
            cancel_retry_at: float | None = None
            while True:
                try:
                    runtime.tick()
                    consecutive_errors = 0
                except Exception as exc:
                    consecutive_errors += 1
                    log.error("live_core.tick_failed", error=str(exc),
                              consecutive_errors=consecutive_errors)
                    if consecutive_errors == 3:
                        cancel_retry_at = _fuse(runtime, exc)
                if cancel_retry_at is not None and time.monotonic() >= cancel_retry_at:
                    if _cancel_all(runtime):
                        cancel_retry_at = None
                        _alert(runtime, "熔断撤单已补发", "Redis 恢复后已重新投递 cancel_all")
                    else:
                        cancel_retry_at = time.monotonic() + CANCEL_RETRY_SECONDS
                time.sleep(max(0.2, args.poll))


CANCEL_RETRY_SECONDS = 30.0


def _cancel_all(runtime) -> bool:
    """投递 cancel_all；失败（多为 Redis 超时）只记录，结果按未确认处理（#535）。"""
    try:
        runtime.broker.cancel_all()
        return True
    except Exception as exc:
        log.error("live_core.fuse_cancel_failed", error=str(exc))
        return False


def _alert(runtime, title: str, detail: str) -> None:
    try:
        runtime.alerts.critical(title, detail)
    except Exception as exc:  # 告警失败不能让熔断后的进程退出
        log.error("live_core.fuse_alert_failed", title=title, error=str(exc))


def _fuse(runtime, exc: Exception) -> float | None:
    """连续故障熔断：先落 KILL，再撤单，再告警；任何一步失败都不让进程退出。

    10-24 现场：熔断分支里 cancel_all 再次 Redis 超时，异常冲出主循环，进程退出后
    systemd 反复重启、启动前检查反复拒绝。现在进程保持存活、不再新开仓（KILL 已落），
    撤单未确认时按 CANCEL_RETRY_SECONDS 重试，直到投递成功；撤单本身可重复，无副作用。
    返回下一次撤单重试的单调时钟时刻，已投递则返回 None。
    """
    reason = "three consecutive live-core loop failures"
    notes = []
    try:
        runtime.kill_switch.engage(reason)
    except Exception as kill_exc:
        log.error("live_core.fuse_kill_failed", error=str(kill_exc))
        notes.append(f"KILL 写入失败：{kill_exc}")
    cancelled = _cancel_all(runtime)
    if not cancelled:
        notes.append(f"撤单未确认，将每 {CANCEL_RETRY_SECONDS:.0f} 秒重试")
    _alert(runtime, "实盘核心连续故障熔断", "；".join([f"{reason}: {exc}", *notes]))
    return None if cancelled else time.monotonic() + CANCEL_RETRY_SECONDS


if __name__ == "__main__":
    sys.exit(main())
