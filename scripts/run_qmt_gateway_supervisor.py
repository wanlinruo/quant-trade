"""#601 大 QMT 网关的开机自启守护进程（Windows 登录任务启动，session 1 内常驻）。

网关退出即重启；执行器先起/后起都可（执行器等待协调标记后接入，#633）。
- 正常退出或崩溃：等待 ``delay_s`` 后重启。重启不重发任何指令：网关启动时把在途意图
  转为 unknown，由对账按既定条件消解（#634）。
- 重启风暴：``window_s`` 内退出达到 ``max_exits`` 次，写本地停机闸（已有闸时不覆盖原因），
  之后按 ``backoff_s`` 退避继续重启——网关在落闸下仍能处理卖出与撤单（#535 同类教训：
  无上限重启会掩盖故障，完全停止又会丢掉减仓能力）。
- 停止文件 ``SUPERVISOR_STOP`` 存在时不再启动网关（维护入口）；守护进程单实例。
本进程不读写订单、账户或行情，不调用券商接口。
"""
from __future__ import annotations

import argparse
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

GATEWAY = Path(__file__).resolve().with_name("run_qmt_agent.py")


@dataclass(frozen=True, slots=True)
class Decision:
    delay_s: float
    storm: bool
    exits_in_window: int


@dataclass(slots=True)
class RestartPolicy:
    delay_s: float = 30.0
    window_s: float = 900.0
    max_exits: int = 3
    backoff_s: float = 300.0
    _exits: deque = field(default_factory=deque, repr=False)

    def record_exit(self, now: float) -> Decision:
        self._exits.append(now)
        while self._exits and now - self._exits[0] > self.window_s:
            self._exits.popleft()
        storm = len(self._exits) >= self.max_exits
        return Decision(self.backoff_s if storm else self.delay_s, storm, len(self._exits))


@contextmanager
def single_instance(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("gateway supervisor already running") from exc
        yield
    finally:
        handle.close()


class Supervisor:
    def __init__(self, *, gateway_argv: list[str], kill_path: Path | None, stop_path: Path,
                 journal_path: Path, policy: RestartPolicy,
                 spawn: Callable[[list[str]], int] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.gateway_argv, self.kill_path, self.stop_path = gateway_argv, kill_path, stop_path
        self.journal_path, self.policy = journal_path, policy
        self.spawn = spawn or (lambda argv: subprocess.call(argv))
        self.clock, self.sleep = clock, sleep

    def _journal(self, event: str, **facts) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        row = {"at": datetime.now().isoformat(timespec="seconds"), "event": event, **facts}
        with self.journal_path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def _stop_requested(self) -> bool:
        return self.stop_path.exists()

    def _engage_kill(self, reason: str) -> None:
        if self.kill_path is None or self.kill_path.exists():
            return                                   # 不覆盖已有落闸原因（远程 KILL、冲突等）
        self.kill_path.parent.mkdir(parents=True, exist_ok=True)
        self.kill_path.write_text(reason, encoding="utf-8")
        self._journal("local_kill_engaged", reason=reason)

    def _wait(self, seconds: float) -> None:
        deadline = self.clock() + seconds
        while not self._stop_requested():
            remaining = deadline - self.clock()
            if remaining <= 0:
                return
            self.sleep(min(5.0, remaining))

    def run(self) -> int:
        if self._stop_requested():
            self._journal("stopped", reason="stop_file_present_at_start")
            return 0
        self._journal("started", gateway=Path(self.gateway_argv[1]).name
                      if len(self.gateway_argv) > 1 else "")
        while not self._stop_requested():
            began = self.clock()
            code = self.spawn(self.gateway_argv)
            decision = self.policy.record_exit(self.clock())
            self._journal("gateway_exit", code=code, ran_s=round(self.clock() - began, 1),
                          delay_s=decision.delay_s, storm=decision.storm,
                          exits_in_window=decision.exits_in_window)
            if decision.storm:
                self._engage_kill(f"gateway restart storm: {decision.exits_in_window} exits in "
                                  f"{int(self.policy.window_s)}s")
            self._wait(decision.delay_s)
        self._journal("stopped", reason="stop_file")
        return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    raw = list(sys.argv[1:] if argv is None else argv)
    if "--" not in raw:
        raise SystemExit("gateway arguments must follow --")
    split = raw.index("--")
    parser = argparse.ArgumentParser(description="#601 大 QMT 网关守护进程")
    parser.add_argument("--config", required=True)
    parser.add_argument("--state-dir", type=Path, required=True,
                        help="守护进程日志、锁与停止文件所在目录")
    parser.add_argument("--delay", type=float, default=30.0)
    parser.add_argument("--window", type=float, default=900.0)
    parser.add_argument("--max-exits", type=int, default=3)
    parser.add_argument("--backoff", type=float, default=300.0)
    args = parser.parse_args(raw[:split])
    args.gateway = raw[split + 1:]
    if not args.gateway:
        raise SystemExit("gateway arguments required after --")
    if any(a == "--config" or a.startswith("--config=") for a in args.gateway):
        raise SystemExit("gateway --config is set by the supervisor")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    from qsys.live.config import load_deploy_config
    cfg = load_deploy_config(args.config)
    kill = Path(cfg.safety.local_kill_path) if cfg.safety.local_kill_path else None
    gateway_argv = [sys.executable, "-B", str(GATEWAY), "--config", args.config, *args.gateway]
    with single_instance(args.state_dir / "supervisor.lock"):
        return Supervisor(
            gateway_argv=gateway_argv, kill_path=kill,
            stop_path=args.state_dir / "SUPERVISOR_STOP",
            journal_path=args.state_dir / "supervisor.jsonl",
            policy=RestartPolicy(args.delay, args.window, args.max_exits, args.backoff),
        ).run()


if __name__ == "__main__":
    sys.exit(main())
