"""每日整体情况汇报，收盘后发邮件。

交易日 20:30 跑（服务质检 20:00 之后），汇总当天：策略跑了没、账户什么状态、
服务与数据有没有问题。内容组装在 `qsys.ops.digest`（纯函数），这里只负责
取数与投递。

没配 [email] 就只打印到标准输出——这样在拿到 SMTP 之前也能先看内容对不对。

    uv run python scripts/send_daily_digest.py --config config/deploy.local.toml
    uv run python scripts/send_daily_digest.py --print   # 只看，不发
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import structlog

from qsys.core.calendar import last_closed_session
from qsys.ops.digest import build_digest
from qsys.ops.services import ServiceRun, load_runs
from qsys.paper.calendar import load_trading_calendar

log = structlog.get_logger("daily-digest")

# 每天都该留下记录的任务；缺了要在汇报里点名
EXPECTED_SERVICES = ("premarket-limits", "daily-archive", "tushare-sync",
                     "catalog-verify", "service-checks")


def _rows(path: Path, sql: str, args: tuple) -> list[dict]:
    if not path.exists():
        return []
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute(sql, args)]
    except sqlite3.Error as exc:
        log.warning("digest.read_failed", path=str(path), error=str(exc))
        return []


def strategy_runs(shadow: Path, workbench: Path, day: date) -> list[dict]:
    out = [
        {**r, "engine": "影子盘", "fills": 0}
        for r in _rows(shadow,
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


def checks_and_missing(service_runs: Path, day: date) -> tuple[list[dict], list[str]]:
    """当晚质检的全部步骤，外加当天没留下运行记录的任务。

    交全部步骤而不只是失败的：汇报要写「26 项，失败 0」，
    只给失败项就数不出分母。
    """
    runs = load_runs(service_runs, day, day)
    mine = [r for r in runs if r.get("service") == "service-checks"]
    steps = mine[-1].get("steps", []) if mine else []
    checks = [
        {"name": s.get("name", ""), "status": s.get("status", ""),
         "detail": s.get("detail", "") or s.get("error", "")}
        for s in steps
    ]
    seen = {r.get("service") for r in runs}
    missing = [name for name in EXPECTED_SERVICES if name not in seen]
    return checks, missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config/deploy.local.toml")
    parser.add_argument("--service-runs", type=Path, default=Path("var/service-runs"))
    parser.add_argument("--shadow-state", type=Path,
                        default=Path("var/shadow-state.sqlite3"))
    parser.add_argument("--workbench", type=Path,
                        default=Path("var/workbench/workbench.sqlite3"))
    parser.add_argument("--day", type=date.fromisoformat, default=None)
    parser.add_argument("--print", action="store_true", help="只打印，不发信")
    args = parser.parse_args()

    calendar = load_trading_calendar(args.config)
    day = args.day or last_closed_session(calendar, datetime.now())
    if day is None:
        print("没有已收盘的交易日，不发汇报")
        return 0
    if not calendar.is_trading_day(day):
        print(f"{day} 不是交易日，不发汇报")
        return 0

    with ServiceRun.open(args.service_runs, "daily-digest") as run:
        run.session = day
        return _run(args, day, run)


def _run(args, day, run) -> int:
    checks, missing = checks_and_missing(args.service_runs, day)
    digest = build_digest(
        day,
        strategy_runs=strategy_runs(args.shadow_state, args.workbench, day),
        accounts=accounts(args.workbench, day),
        checks=checks,
        missing_services=missing,
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
