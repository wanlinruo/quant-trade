"""按心跳判断执行代理是否还在干活，供 Windows 看门狗调用。

**为什么需要它。** 原看门狗的判据是「有没有命令行含 run_agent.py 的 python
进程」。2026-10-05 代理卡在 xtquant 的永久阻塞里（miniQMT 08:45:04 自行重启，
同步调用的回调永不到达），进程活着 11 小时 45 分，看门狗全程报告正常并什么
都没做——它是 2026-09-18 为崩溃型故障造的，对「活着但不干活」结构性失明。

心跳只在主循环走完一轮后才写，所以它是「还在干活」的证据，进程存在不是。

**自己绝不能挂。** 看门狗每 5 分钟同步调用本脚本，一旦它阻塞住，看门狗就跟着
停摆——那正是本脚本要治的病。所以这里显式建一个带 socket 超时的 Redis 客户端，
不复用 `RedisTransport.from_config`：看门狗允许用命令行指定更短的探测超时，
与业务客户端的配置相互独立。
共用客户端那条缺口单独登记，不在这里顺手改掉交易路径的失败语义。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from qsys.live.config import load_deploy_config
from qsys.live.heartbeat import HeartbeatMonitor
from qsys.live.redis_transport import RedisTransport

ALIVE = 0
STALE = 2
MISSING = 3
UNREACHABLE = 4


def decide(
    monitor: HeartbeatMonitor, source: str, max_age_s: float, now: datetime
) -> tuple[int, str]:
    """返回 (退出码, 说明)。看门狗只看退出码，说明进日志。"""
    last = monitor.last_beat(source)
    if last is None:
        return MISSING, f"{source} 心跳键不存在"
    age = (now - last).total_seconds()
    if age < 0:
        # 时钟倒退时「很新鲜」是假的，不许据此判活。
        return STALE, f"{source} 心跳时间戳在未来 {-age:.1f}s，判为不可信"
    if age > max_age_s:
        return STALE, f"{source} 心跳已过期 {age:.1f}s（阈值 {max_age_s:.0f}s）"
    return ALIVE, f"{source} 心跳新鲜，{age:.1f}s 前"


def _monitor_with_timeout(config_path: str | None, timeout_s: float):
    import redis

    cfg = load_deploy_config(config_path)
    client = redis.Redis(
        host=cfg.redis.host,
        port=cfg.redis.port,
        password=cfg.redis.password or None,
        db=cfg.redis.db,
        ssl=cfg.redis.tls,
        decode_responses=False,
        protocol=2,
        socket_timeout=timeout_s,
        socket_connect_timeout=timeout_s,
    )
    return HeartbeatMonitor(RedisTransport(client), prefix=cfg.streams.heartbeat)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None)
    parser.add_argument("--source", default="agent")
    parser.add_argument("--max-age", type=float, default=120.0)
    parser.add_argument("--redis-timeout", type=float, default=5.0)
    args = parser.parse_args()

    try:
        monitor = _monitor_with_timeout(args.config, args.redis_timeout)
        code, detail = decide(monitor, args.source, args.max_age, datetime.now())
    except Exception as exc:
        # Redis 读不到不等于代理死了。重启一个好进程比留着一个死进程更坏，
        # 所以这里单独一个退出码，由看门狗决定只记日志、不重启。
        print(f"UNREACHABLE {type(exc).__name__}: {exc}")
        return UNREACHABLE

    print(detail)
    return code


if __name__ == "__main__":
    sys.exit(main())
