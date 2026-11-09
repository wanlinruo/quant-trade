"""Windows 执行代理主循环（与 scripts/run_agent.py 相同的语义，供新入口复用）。"""
from __future__ import annotations

import time

import structlog

log = structlog.get_logger("agent")


def run_forever(agent, adapter, *, guard=None, poll_seconds: float = 1.0,
                account_every: int = 2, sleep=time.sleep, max_ticks: int | None = None) -> None:
    ticks = consecutive_errors = 0
    while max_ticks is None or ticks < max_ticks:
        try:
            agent.enforce_core_heartbeat()
            agent.run_once()
            agent.beat()
            if ticks % account_every == 0:
                agent.publish_account()
            consecutive_errors = 0
        except Exception as exc:
            consecutive_errors += 1
            log.error("agent.loop_error", error=str(exc), consecutive_errors=consecutive_errors)
            # 连续故障时在 Windows 本地独立落闸；即使 Redis 同时失联也生效。
            if consecutive_errors >= 3:
                agent.engage_local_kill("three consecutive agent loop failures")
                try:
                    adapter.cancel_all()
                except Exception as cancel_exc:
                    log.error("agent.cancel_after_failure_failed", error=str(cancel_exc))
        ticks += 1
        if guard is not None:
            guard.progress()
        sleep(poll_seconds)
