"""CLI for the independently released daily operations digest component.

交易日 20:30 跑（服务质检 20:00 之后），汇总当天：策略跑了没、账户什么状态、
服务与数据有没有问题。内容组装在 `qsys.ops.digest`（纯函数），这里只负责
取数与投递。

没配 [email] 就只打印到标准输出——这样在拿到 SMTP 之前也能先看内容对不对。

    uv run qsys-daily-digest --config config/deploy.local.toml
    uv run qsys-daily-digest --print   # 只看，不发
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import structlog

from qsys.ops.alerts import ledger as alert_ledger
from qsys.ops.digest import build_digest
from qsys.ops.services import SKIPPED, ServiceRun, load_runs, run_session
from qsys.paper.calendar import load_trading_calendar

log = structlog.get_logger("daily-digest")

# 每天都该留下记录的任务；缺了要在汇报里点名
EXPECTED_SERVICES = ("premarket-limits", "daily-archive", "tushare-sync",
                     "catalog-verify", "service-checks")

# 节点段要报在线状态的常驻服务
RESIDENT_UNITS = (
    ("模拟盘", "qsys-paper"),
    ("健康观测", "qsys-health-observer"),
    ("监控台", "qsys-monitor"),
    ("Redis 隧道", "qsys-redis-tunnel"),
)


def _rows(path: Path, sql: str, args: tuple) -> list[dict]:
    from qsys.live.state_binding import state_path

    path = state_path(Path(path))
    if not path.exists():
        return []
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute(sql, args)]
    except sqlite3.Error as exc:
        log.warning("digest.read_failed", path=str(path), error=str(exc))
        return []


def strategy_runs(core_state: Path | None, workbench: Path, day: date,
                  *, core_name: str = "影子盘") -> list[dict]:
    out = [] if core_state is None else [
        {**r, "engine": core_name, "fills": 0}
        for r in _rows(core_state,
                       "SELECT phase,summary,orders,rejects,error FROM"
                       " live_strategy_runs WHERE session=?", (day.isoformat(),))
    ]
    names = {r["id"]: r["name"] for r in _rows(
        workbench, "SELECT id,name FROM paper_accounts WHERE status='running'", ())}
    out += [
        {**r, "engine": names.get(r.pop("account_id", ""), "模拟"), "fills": 0}
        for r in _rows(workbench,
                       "SELECT account_id,phase,summary,orders,rejects,error FROM"
                       " paper_strategy_runs WHERE session=?", (day.isoformat(),))
    ]
    return out


def accounts(workbench: Path, day: date) -> list[dict]:
    """取当日最后一条权益记录；有持仓时现金不等于总资产，不能拿现金充数。"""
    rows = _rows(
        workbench,
        "SELECT a.name, e.cash, e.total_value, e.market_value FROM paper_accounts a"
        " JOIN paper_equity e ON e.account_id = a.id"
        " WHERE a.status='running' AND e.session=?"
        " AND e.at = (SELECT MAX(at) FROM paper_equity"
        "             WHERE account_id = a.id AND session = ?)",
        (day.isoformat(), day.isoformat()),
    )
    if rows:
        return [
            {"name": r["name"], "total_value": r["total_value"],
             "cash": r["cash"], "positions": 0}
            for r in rows
        ]
    # 当天还没有权益点（例如盘前跑）时退回账面现金，并说明
    return [
        {"name": f"{r['name']}（当日无权益记录）", "total_value": r["cash"],
         "cash": r["cash"], "positions": 0}
        for r in _rows(workbench,
                       "SELECT name,cash FROM paper_accounts WHERE status='running'",
                       ())
    ]


def checks_and_missing(service_runs: Path, day: date,
                       trading: bool = True) -> tuple[list[dict], list[str]]:
    """当晚质检的全部步骤，外加当天没留下运行记录的任务。

    交全部步骤而不只是失败的：汇报要写「26 项，失败 0」，
    只给失败项就数不出分母。
    """
    # 按**它检查的那一天**取，不按文件落在哪天：在 10-01 复查 09-30，那次执行
    # 属于 09-30；反过来次日补跑的活属于它补的那个交易日。两头都要对，所以
    # 多读一天再按 session 过滤（2026-10-01 日报把 09-30 的质检当成当天的）。
    runs = [r for r in load_runs(service_runs, day, day + timedelta(days=1))
            if run_session(r) == day.isoformat()]
    # 跳过的那次没检查任何东西，它只留下一条「确定交易日」的记账步骤；
    # 把它算成「质检 1 项」会让休市日的日报看起来像是查过了
    mine = [r for r in runs if r.get("service") == "service-checks"
            and r.get("status") != SKIPPED]
    steps = mine[-1].get("steps", []) if mine else []
    checks = [
        {"name": s.get("name", ""), "status": s.get("status", ""),
         "detail": s.get("detail", "") or s.get("error", "")}
        for s in steps
    ]
    seen = {r.get("service") for r in runs}
    # 休市日这些任务本来就不排，缺记录不是故障
    missing = [name for name in EXPECTED_SERVICES if name not in seen] if trading else []
    return checks, missing


def clean_streak(path: Path, table: str, day: date) -> int:
    """到 day 为止，连续多少个交易日一条错误都没有。

    「观察期第几天」需要一个手填的起点，会过期；「连续零错误多少天」每天都能
    重算，过不了期，而且实盘跑起来之后依然有意义。
    """
    rows = _rows(path, f"SELECT session, MAX(error IS NOT NULL AND error != '')"
                       f" AS bad FROM {table} WHERE session <= ?"
                       f" GROUP BY session ORDER BY session DESC", (day.isoformat(),))
    streak = 0
    for row in rows:
        if row["bad"]:
            break
        streak += 1
    return streak


def readiness(config_path: str, core_state: Path | None, workbench: Path, day: date,
              *, core_name: str = "影子盘") -> dict:
    """离实盘还差什么。全部现查，不读任何手填的计划表。"""
    from qsys.live.config import load_deploy_config

    out: dict = {}
    try:
        config = load_deploy_config(config_path)
        out["mode"] = config.safety.mode
        out["orders_enabled"] = config.safety.orders_enabled
    except Exception as exc:
        log.warning("digest.safety_failed", error=str(exc))
        out["mode"] = "问不出来"
    out["kill_switch"] = _kill_reason(config_path)
    out["clean_streak"] = {"模拟盘": clean_streak(
        workbench, "paper_strategy_runs", day,
    )}
    if core_state is not None:
        out["clean_streak"][core_name] = clean_streak(
            core_state, "live_strategy_runs", day,
        )
    out["agent_beat"] = _agent_beat(config_path)
    return out


def _kill_reason(config_path: str):
    try:
        from qsys.live.config import load_deploy_config
        from qsys.live.kill_switch import KillSwitch
        from qsys.live.readonly_transport import readonly_from_config

        config = load_deploy_config(config_path)
        switch = KillSwitch(readonly_from_config(config))
        return switch.reason()
    except Exception as exc:
        log.warning("digest.kill_switch_failed", error=str(exc))
        return None


def _agent_beat(config_path: str) -> dict:
    """券商代理心跳。读不到写 unknown，不写 absent——那是两件事。

    心跳时刻是不带时区的本地时间（`HeartbeatMonitor.is_alive` 也这么比），
    拿 aware 的 now 去减会抛异常；第一次实跑就踩到了，而当时的写法把异常
    吞成「无心跳」，等于每天误报代理离线（2026-10-01 修）。
    """
    try:
        from qsys.live.config import load_deploy_config
        from qsys.live.heartbeat import HeartbeatMonitor
        from qsys.live.readonly_transport import readonly_from_config

        config = load_deploy_config(config_path)
        transport = readonly_from_config(config)
        beat = HeartbeatMonitor(transport, prefix=config.streams.heartbeat).read("agent")
        if beat is None or beat.at is None:
            return {"state": "absent"}
        at = beat.at.replace(tzinfo=None) if beat.at.tzinfo else beat.at
        return {"state": "fresh",
                "age_s": max(0.0, (datetime.now() - at).total_seconds())}
    except Exception as exc:
        log.warning("digest.heartbeat_failed", error=str(exc))
        return {"state": "unknown"}


def quality(warehouse: Path | None, day: date) -> dict:
    """当晚指标字典验证的结论。取不到就返回空字典，由汇报判定要不要算异常。"""
    if warehouse is None or not Path(warehouse).exists():
        return {}
    try:
        import polars as pl

        from qsys.data.query import WarehouseQuery
        from qsys.data.storage import Warehouse

        frame = WarehouseQuery(Warehouse(warehouse)).metric_checks()
        today = frame.filter(pl.col("scope_end") == day)
        if today.height == 0:
            return {}
        latest = today.filter(pl.col("run_id") == today["run_id"].max())
        failed = latest.filter(~pl.col("passed"))
        blocking = failed.filter(pl.col("severity") == "blocking")
        return {
            "at": latest["checked_at"].max().strftime("%H:%M"),
            "total": latest.height,
            "warnings": failed.height - blocking.height,
            "known_warnings": failed.filter(
                pl.col("known_issue").is_not_null()).height,
            "blocking": [
                {"metric": row["metric_id"], "detail": row["detail"]}
                for row in blocking.iter_rows(named=True)
            ],
        }
    except Exception as exc:               # 汇报不能因为取数失败而发不出去
        log.warning("digest.quality_failed", error=str(exc))
        return {}


def infrastructure(storage_base: Path, tls_site: str | None, tls_port: int,
                   *, mode: str = "shadow") -> dict:
    """节点还撑不撑得住。任何一项取不到都记 None，不记「看起来没事」。"""
    from qsys.ops.infra import failed_units, memory, systemctl_show
    from qsys.ops.storage import GIB, disk_status

    reading = {}
    try:
        usage = disk_status(storage_base)
        reading["disk"] = {"free_gib": usage.free / GIB, "total_gib": usage.total / GIB,
                           "used_pct": round(100 * usage.used / usage.total),
                           "level": usage.level}
    except OSError as exc:
        log.warning("digest.disk_failed", error=str(exc))

    ram = memory()
    if ram is not None:
        reading["memory"] = {"available_gib": ram.available_gib,
                             "total_gib": ram.total_gib, "used_pct": ram.used_pct}

    reading["failed_units"] = failed_units()
    reading["cert_days"] = _cert_days(tls_site, tls_port)
    units = []
    core = {
        "shadow": ("影子盘核心", "qsys-shadow-core"),
        "live": ("实盘核心", "qsys-live-core"),
    }.get(mode)
    for name, unit in ((core,) if core else ()) + RESIDENT_UNITS:
        props = systemctl_show(unit)
        units.append({"name": name,
                      "state": props.get("ActiveState", "问不出来"),
                      "since": _since(props.get("ActiveEnterTimestamp"))})
    reading["units"] = units
    return reading


def _since(stamp: str | None) -> str:
    """systemd 的 `Wed 2026-09-30 11:09:00 CST` → `2026-09-30 11:09`。

    原来按字符数截断，正好切在秒中间，日报上是 `11:09:0`。
    """
    parts = (stamp or "").split()
    return f"{parts[1]} {parts[2][:5]}" if len(parts) >= 3 else ""


def _cert_days(site: str | None, port: int) -> int | None:
    if not site:
        return None
    try:
        from qsys.ops.service_checks import serving_not_after

        not_after = serving_not_after("127.0.0.1", port, site)
        # notAfter 解析出来是不带时区的 UTC，拿同样口径的当下来减
        return (not_after - datetime.now(timezone.utc).replace(tzinfo=None)).days
    except Exception as exc:
        log.warning("digest.cert_failed", site=site, error=str(exc))
        return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config/deploy.local.toml")
    parser.add_argument("--service-runs", type=Path, default=Path("var/service-runs"))
    parser.add_argument(
        "--record-root", type=Path, default=None,
        help="专属运行记录写入根；默认与 --service-runs 相同以兼容旧调用",
    )
    parser.add_argument("--shadow-state", type=Path,
                        default=Path("var/shadow-state.sqlite3"))
    parser.add_argument("--live-state", type=Path,
                        default=Path("var/live-state.sqlite3"))
    parser.add_argument("--workbench", type=Path,
                        default=Path("var/workbench/workbench.sqlite3"))
    parser.add_argument("--alert-ledger", type=Path, default=None,
                        help="告警投递流水目录；默认 var/alerts")
    parser.add_argument("--warehouse", type=Path, default=None,
                        help="仓库路径；给了才汇报数据质量")
    parser.add_argument("--storage-base", type=Path, default=Path("/data/qsys"))
    parser.add_argument("--tls-site", default=None,
                        help="公网入口域名（作 SNI 探 127.0.0.1）；不给则不报证书")
    parser.add_argument("--tls-port", type=int, default=8443)
    parser.add_argument("--day", type=date.fromisoformat, default=None)
    parser.add_argument("--print", action="store_true", help="只打印，不发信")
    return parser


def main(argv: list[str] | None = None) -> int:
    from tempfile import TemporaryDirectory
    from qsys.data.query_budget import query_budget

    with TemporaryDirectory(prefix="qsys-daily-digest-query-") as temporary:
        with query_budget(memory_limit_mb=256, temp_root=Path(temporary),
                          max_temp_directory_mb=512, threads=1):
            return _main(argv)


def _main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    calendar = load_trading_calendar(args.config)
    # 汇报的是**今天**，不是「最近一个已收盘交易日」。后者在假期里会把节前
    # 最后一个交易日的日报原样重发：定时器按 Mon..Fri 触发，不认节假日，
    # 国庆七天能发出五封一模一样的信（2026-10-01 预演发现）。
    day = args.day or datetime.now().date()
    trading = calendar.is_trading_day(day)

    with ServiceRun.open(args.record_root or args.service_runs, "daily-digest") as run:
        run.session = day
        return _run(args, day, trading, run)


def _run(args, day, trading, run) -> int:
    from qsys.live.config import load_deploy_config

    mode = load_deploy_config(args.config).safety.mode
    core_state = {
        "shadow": args.shadow_state, "live": args.live_state,
    }.get(mode)
    core_name = "实盘" if mode == "live" else "影子盘"
    checks, missing = checks_and_missing(args.service_runs, day, trading)
    digest = build_digest(
        day,
        trading=trading,
        strategy_runs=(strategy_runs(core_state, args.workbench, day, core_name=core_name)
                       if trading else []),
        accounts=accounts(args.workbench, day) if trading else [],
        checks=checks,
        missing_services=missing,
        quality=quality(args.warehouse, day),
        infra=infrastructure(args.storage_base, args.tls_site, args.tls_port, mode=mode),
        deliveries=alert_ledger(args.alert_ledger).read(day),
        readiness=readiness(args.config, core_state, args.workbench, day,
                            core_name=core_name),
    )

    with run.step("汇总当日情况", interface="只读状态库与运行记录") as step:
        step.detail = digest.subject()
        step.rows = len(digest.problems)

    if args.print:
        print(digest.as_text())
        return 0

    from qsys.live.config import load_deploy_config
    from qsys.ops.alerts import send_email

    email = load_deploy_config(args.config).email
    if not email.configured:
        run.skip("[email] 未配置，只打印不发信")
        print("[warn] [email] 未配置，只打印：", file=sys.stderr)
        print(digest.as_text())
        return 0
    with run.step("发送邮件", interface="SMTP") as step:
        send_email(email, digest.subject(), digest.as_text(), digest.as_html())
        step.detail = "、".join(email.recipients)
    log.info("digest.sent", day=str(day), problems=len(digest.problems))
    print(f"{digest.subject()} → {', '.join(email.recipients)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
