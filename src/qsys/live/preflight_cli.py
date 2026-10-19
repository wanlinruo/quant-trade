"""实盘与影子核心只读盘前检查；不发单、不撤单、不修改账户。"""

from __future__ import annotations

import argparse
import ipaddress
import sys
from datetime import datetime
from pathlib import Path

from qsys.live.config import load_deploy_config
from qsys.live.messages import AccountSnapshot, Heartbeat
from qsys.live.redis_transport import RedisTransport


def _is_private_host(host: str) -> bool:
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def heartbeat_freshness(
    beat: Heartbeat | None, now: datetime | None = None
) -> tuple[bool, float, float]:
    """按发送端声明节奏应用与健康观察器一致的 3 倍失联余量。"""
    if beat is None:
        return False, float("inf"), 10.0
    now = now or datetime.now(tz=beat.at.tzinfo)
    age = (now - beat.at).total_seconds()
    expected = beat.expected_interval_s or 1.0
    down_after = max(10.0, expected * 3.0)
    return -1 <= age <= down_after, age, down_after


def agent_live_posture(
    beat: Heartbeat | None, *, require_kill_released: bool = False,
) -> tuple[tuple[bool, str], ...]:
    """Effective Windows posture, optionally requiring the local kill to be clear."""
    kill_reported = beat is not None and beat.local_kill_engaged is not None
    if require_kill_released:
        kill_check = beat is not None and beat.local_kill_engaged is False
        kill_label = "Windows agent 本地停机闸未生效"
    else:
        kill_check = kill_reported
        state = (
            "未知" if not kill_reported
            else ("已生效" if beat.local_kill_engaged else "未生效")
        )
        kill_label = f"Windows agent 本地停机闸状态已上报（{state}）"
    return (
        (beat is not None and beat.trading_mode == "live",
         "Windows agent trading_mode=live"),
        (beat is not None and beat.orders_enabled is True,
         "Windows agent orders_enabled=true"),
        (beat is not None and beat.account_protected is True,
         "Windows agent 当前账号在白名单"),
        (kill_check, kill_label),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="实盘盘前只读检查")
    parser.add_argument("--config", default=None)
    parser.add_argument("--strategy", default="strategies/structural_v1_2.py")
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--require-live", action="store_true")
    parser.add_argument(
        "--require-kill-released", action="store_true",
        help="require the Windows agent local kill to be clear before trading activation",
    )
    args = parser.parse_args()
    if args.require_kill_released and not args.require_live:
        parser.error("--require-kill-released requires --require-live")

    cfg = load_deploy_config(args.config)
    checks: list[tuple[bool, str]] = []
    checks.append((Path(args.strategy).is_file(), f"策略文件: {args.strategy}"))
    checks.append((Path(args.warehouse).is_dir(), f"数据仓库: {args.warehouse}"))
    checks.append((cfg.risk.enabled_capital > 0, "启用资金必须大于 0"))
    if cfg.risk.max_account_asset:
        checks.append((cfg.risk.enabled_capital <= cfg.risk.max_account_asset,
                       "启用资金未超过账户规模上限"))
    checks.append((bool(cfg.redis.password) or _is_private_host(cfg.redis.host),
                   "非本机 Redis 必须设置密码"))
    checks.append((cfg.redis.tls or _is_private_host(cfg.redis.host),
                   "非私网 Redis 必须启用 TLS"))
    if args.require_live:
        checks.append((cfg.safety.mode == "live", "safety.mode=live"))
        checks.append((cfg.safety.orders_enabled, "orders_enabled=true"))
        checks.append((bool(cfg.safety.protected_account_ids), "实盘账户白名单非空"))

    transport = RedisTransport.from_config(cfg)
    raw_account = transport.get_key(cfg.streams.account)
    account = AccountSnapshot.model_validate_json(raw_account) if raw_account else None
    checks.append((account is not None, "券商账户快照存在"))
    if account is not None:
        age = (datetime.now() - account.at).total_seconds()
        checks.append((-1 <= age <= 5, f"账户快照新鲜度 {age:.1f}s"))
        if cfg.risk.max_account_asset:
            total_asset = float(
                account.total_asset or account.cash + account.market_value
            )
            checks.append((total_asset <= cfg.risk.max_account_asset,
                           f"账户总资产不超过 {cfg.risk.max_account_asset:,.0f}"))

    for source in ("agent", "market"):
        raw = transport.get_key(f"{cfg.streams.heartbeat}:{source}")
        beat = Heartbeat.model_validate_json(raw) if raw else None
        fresh, age, threshold = heartbeat_freshness(beat)
        checks.append((fresh,
                       f"{source} 心跳新鲜度 {age:.1f}s（阈值 {threshold:.1f}s）"))
        if args.require_live and source == "agent":
            checks.extend(agent_live_posture(
                beat, require_kill_released=args.require_kill_released,
            ))

    for ok, label in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    failed = sum(not ok for ok, _ in checks)
    print(f"\n盘前检查: {len(checks) - failed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
