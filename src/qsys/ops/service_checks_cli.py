"""CLI for the independently released daily service-checks component."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import structlog

from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.ops.service_checks import (
    BAD,
    CHECKS,
    OK,
    REPAIRED,
    SKIP,
    WARN,
    CheckContext,
    earlier_failures,
    mark_repaired,
    step_name,
)
from qsys.ops.services import SKIPPED, ServiceRun, active_core_service, load_runs
from qsys.paper.calendar import load_trading_calendar

log = structlog.get_logger("service-checks")


def _exit_code(failed: dict[str, int]) -> int:
    """Only data-domain failures fail the data-quality systemd unit."""
    return 1 if failed.get("data", 0) else 0


def checks_for_mode(checks, mode: str):
    """Bind the shared strategy-event check to the core active in this mode."""
    core = active_core_service(mode)
    out = []
    for check in checks:
        if check.service != "shadow-core":
            out.append(check)
        elif core is not None:
            out.append(replace(check, service=core))
    return tuple(out)


def _notify(
    config_path, day, problems: list[tuple[str, str, str]], *,
    total: int, ledger_root: Path | None = None,
) -> None:
    """Send the nightly result to the operations alert sink without affecting checks."""
    try:
        from qsys.live.config import load_deploy_config
        from qsys.ops.alerts import AlertBus, WebhookAlertSink, ledger

        url = load_deploy_config(config_path).alerts.ops_url
        if not url:
            return
        bus = AlertBus([WebhookAlertSink(url)], ledger(ledger_root))
        bad = [problem for problem in problems if problem[0] == BAD]
        warn = [problem for problem in problems if problem[0] == WARN]
        if not bad and not warn:
            bus.info(f"服务质检通过（{day}）", f"{total} 项全部正常")
            return
        lines = [f"{name}：{detail}" for _, name, detail in (bad + warn)[:10]]
        title = f"服务质检 {day}：失败 {len(bad)} 项，告警 {len(warn)} 项"
        (bus.critical if bad else bus.warning)(title, "\n".join(lines))
    except Exception as exc:  # 通知挂了不能影响质检结论
        log.warning("service_checks.notify_failed", error=str(exc))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--config", default="config/deploy.local.toml")
    parser.add_argument("--service-runs", type=Path, default=Path("var/service-runs"))
    parser.add_argument(
        "--record-root", type=Path, default=None,
        help="专属运行记录写入根；默认与 --service-runs 相同以兼容旧调用",
    )
    parser.add_argument("--alert-ledger", type=Path, default=None)
    parser.add_argument("--shadow-state", type=Path, default=Path("var/shadow-state.sqlite3"))
    parser.add_argument("--live-state", type=Path, default=Path("var/live-state.sqlite3"))
    parser.add_argument(
        "--workbench", type=Path, default=Path("var/workbench/workbench.sqlite3"),
    )
    parser.add_argument("--storage-base", type=Path, default=None)
    parser.add_argument("--scratch", type=Path, default=Path.home() / "scratch")
    parser.add_argument("--tls-site", default=None)
    parser.add_argument("--tls-port", type=int, default=8443)
    parser.add_argument("--day", type=date.fromisoformat, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    from qsys.live.config import load_deploy_config

    mode = load_deploy_config(args.config).safety.mode
    checks = checks_for_mode(CHECKS, mode)
    strategy_state = args.live_state if mode == "live" else args.shadow_state
    record_root = args.record_root or args.service_runs

    with ServiceRun.open(record_root, "service-checks") as run:
        with run.step("确定交易日", interface="Tushare trade_cal") as step:
            calendar = load_trading_calendar(args.config)
            day = args.day or datetime.now().date()
            if not calendar.is_trading_day(day):
                run.skip(f"{day} 不是交易日")
                step.detail = run.note
                return 0
            step.detail = str(day)
        run.session = day
        sessions = calendar.sessions_in_range(day - timedelta(days=40), day)
        warehouse = Warehouse(args.warehouse)
        ctx = CheckContext(
            warehouse,
            WarehouseQuery(warehouse),
            day,
            sessions[-2] if len(sessions) > 1 else None,
            sessions,
            load_runs(args.service_runs, day, day + timedelta(days=1)),
            shadow_state=strategy_state,
            workbench=args.workbench,
            storage_base=args.storage_base or Path(args.warehouse).resolve().parent,
            scratch=args.scratch,
            tls_site=args.tls_site,
            tls_port=args.tls_port,
        )
        failed = {"data": 0, "strategy": 0}
        problems: list[tuple[str, str, str]] = []
        failures = earlier_failures(ctx.runs, day)
        for check in checks:
            with run.step(
                step_name(check), interface="只读仓库与运行记录",
                table=check.table, affects_run=check.domain == "data",
            ) as step:
                result = mark_repaired(check.fn(ctx), failures.get(step_name(check)))
                step.rows = result.rows
                detail = f"期望：{result.expected}；实际：{result.actual}"
                if result.detail:
                    detail += f"。{result.detail}"
                if result.status == BAD:
                    failed[check.domain] += 1
                    problems.append((BAD, step_name(check), detail))
                    step.fail(detail)
                elif result.status == WARN:
                    problems.append((WARN, step_name(check), detail))
                    step.warn(detail)
                elif result.status == SKIP:
                    step.skip(detail)
                elif result.status == REPAIRED:
                    step.status, step.detail = REPAIRED, detail
                else:
                    assert result.status == OK
                    step.detail = detail
        print(
            f"{day} 服务质检：{len(checks)} 项，数据未通过 {failed['data']} 项，"
            f"策略事件未通过 {failed['strategy']} 项"
        )
        _notify(
            args.config, day, problems, total=len(checks), ledger_root=args.alert_ledger,
        )
        if run.status == SKIPPED:
            return 0
        return _exit_code(failed)
