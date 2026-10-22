"""真实 LiveRuntime 初始化验证；只操作显式验证根内的状态副本与隔离 Redis 地址。

生产状态的一致性备份由调用方先生成；本入口拒绝直接使用生产状态路径。
只验证初始化、持仓/待处理订单恢复和心跳，不 tick，不制造订单或自然事件证据。
"""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from qsys.agent.locking import single_instance
from qsys.live.config import load_deploy_config
from qsys.live.core_cli import _validate_isolated_paths
from qsys.live.lineage import resolve_strategy
from qsys.live.messages import AccountSnapshot, Heartbeat
from qsys.live.redis_transport import RedisTransport
from qsys.live.runtime import LiveRuntime
from qsys.live.store import LiveStore
from qsys.live.validation_transport import ValidationTransport
from qsys.ops.alerts import AlertBus, ledger
from qsys.paper.calendar import load_trading_calendar
from qsys.paper.quotes import DECISION_TIMEOUT, QuoteClient


def state_fingerprint(path: Path) -> dict:
    """事务内逐值摘要，不输出持仓/订单内容；含 WAL 中已提交事实。"""
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as con:
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        if con.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("state integrity_check failed")
        schema = con.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        ).fetchall()
        result = {"schema_sha256": hashlib.sha256(json.dumps(schema).encode()).hexdigest(), "tables": {}}
        for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            quoted = '"' + name.replace('"', '""') + '"'
            # 按编码后的完整行排序；不假设每张表有 rowid 或同一种主键。
            rows = sorted(json.dumps(row, ensure_ascii=False, separators=(",", ":"),
                                     default=lambda value: {"blob": value.hex()})
                          for row in con.execute(f"SELECT * FROM {quoted}"))
            result["tables"][name] = {"rows": len(rows), "sha256": hashlib.sha256(
                "\n".join(rows).encode()).hexdigest()}
        return result


def copy_inputs(raw, target, streams) -> dict:
    """只 GET 三个固定快照及 kill；原值与时间戳原样映射，不接触订单/成交流。"""
    ages = {}
    for key, model in ((streams.account, AccountSnapshot),
                       (streams.heartbeat + ":agent", Heartbeat),
                       (streams.heartbeat + ":market", Heartbeat)):
        value = raw.get_key(key)
        if value is None:
            raise ValueError("required source snapshot absent")
        parsed = model.model_validate_json(value)
        if {"account_id", "account_number", "account"} & json.loads(value).keys():
            raise ValueError("source snapshot contains account identifiers")
        age = (datetime.now(tz=parsed.at.tzinfo) - parsed.at).total_seconds()
        if not -1 <= age <= (5 if model is AccountSnapshot else 180):
            raise ValueError("source snapshot stale; timestamps must not be rewritten")
        ages[key] = age
        target.set_key(key, value)
    value = raw.get_key("control:kill")
    if value is not None:
        target.set_key("control:kill", value)
    return ages


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--validation-root", type=Path, required=True)
    parser.add_argument("--validation-namespace", required=True)
    args = parser.parse_args(argv)
    root = args.validation_root
    for name, suffix in {"workspace": "workspace", "state": "state.sqlite3", "lock": "core.lock",
                         "kill_switch": "KILL", "journal_warehouse": "journal", "alert_ledger": "alerts"}.items():
        setattr(args, name, str(root / suffix))
    # 配置仅作为只读输入；移除真实账号与通知，不打印或落盘完整配置。
    original = load_deploy_config(args.config)
    if original.safety.mode != "live" or not original.safety.orders_enabled:
        parser.error("只接受当前 live/true 生产配置作为只读输入")
    repo = args.source_repo.resolve(strict=True)
    strategy = replace(original.strategy,
                       file=str((repo / original.strategy.file).resolve()),
                       workbench=str((repo / original.strategy.workbench).resolve()))
    config = replace(original, strategy=strategy, safety=replace(original.safety, protected_account_ids=()),
                     alerts=replace(original.alerts, webhook_url="", shadow_webhook_url="", ops_webhook_url=""))
    _validate_isolated_paths(args, config, parser)
    # 命名空间在任何网络构造前验证；每次运行必须使用新前缀。
    ValidationTransport(None, args.validation_namespace)
    state = Path(args.state)
    if not state.is_file() or (root / "result.json").exists():
        parser.error("须预先提供验证根内一致性状态副本，并使用新的验证根")
    with single_instance(args.lock):
        before = state_fingerprint(state)
        source = resolve_strategy(config.strategy, Path(args.workspace))
        calendar = load_trading_calendar(args.config)
        raw = RedisTransport.from_config(config)
        target = ValidationTransport(raw, args.validation_namespace)
        # SET NX 原子预留，失败运行也不复用残留消费组或事件。
        if not raw._r.set(target._key("validation:reserved"), "1", nx=True):
            raise ValueError("validation namespace already used")
        ages = copy_inputs(raw, target, config.streams)
        runtime = LiveRuntime(
            source.path, args.warehouse, calendar,
            QuoteClient(target, config.streams, consumer="live-core", timeout=DECISION_TIMEOUT),
            target, config.streams, LiveStore(state), params=dict(source.params),
            local_kill_path=Path(args.kill_switch), journal_warehouse=Path(args.journal_warehouse),
            alerts=AlertBus([], ledger(args.alert_ledger)), risk=config.risk, shadow=False,
        )
        with runtime.heartbeats:
            time.sleep(2)
            if target.get_key(config.streams.heartbeat + ":core") is None:
                raise ValueError("isolated core heartbeat missing")
        after = state_fingerprint(state)
        if after != before:
            raise ValueError("Runtime initialization changed persisted state facts")
        result = {"ok": True, "level": "actual-runtime-initialization-only", "shadow": runtime.shadow,
                  "namespace": args.validation_namespace, "source_ages_seconds": ages,
                  "state_fingerprint": before, "pending_orders_restored": len(runtime.broker.pending()),
                  "source_timestamps_preserved": True, "tick_executed": False,
                  "production_order_streams_accessed": False, "notifications": False}
        (root / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
