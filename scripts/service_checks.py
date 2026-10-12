"""每日服务质检：检查各定时任务当天该写的表到齐没有、数量对不对。

由 qsys-service-checks.timer 在交易日 20:00 触发（Tushare 19:00 补跑之后）。
结果写成服务 `service-checks` 的运行记录，服务管理页把每条检查挂回所属任务。

指标字典的验证是另一个任务（`scripts/catalog_verify.py`，19:40），本脚本只读它
写下的结果并核对；**只读仓库**，不修任何数据。

用法：
  uv run python scripts/service_checks.py --warehouse warehouse
  uv run python scripts/service_checks.py --day 2026-09-16      # 复查某一天
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import structlog

from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.ops.service_checks import (
    BAD, CHECKS, OK, REPAIRED, SKIP, WARN, CheckContext, earlier_failures, mark_repaired, step_name,
)
from qsys.ops.services import SKIPPED, ServiceRun, load_runs
from qsys.ops.services import active_core_service
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


def _notify(config_path, day, problems: list[tuple[str, str, str]], *, total: int) -> None:
    """把当晚的质检结论推给运维机器人。

    这一步以前完全没有：20:00 跑出失败也只写进运行记录，要人自己去翻页面
    （2026-09-27 的证书续期失效就是这样躺了一天）。通知是旁路，失败只记日志。
    质检每天只跑一次，不存在刷屏，所以不做去重——连续几天不好本来就该天天提醒。
    """
    try:
        from qsys.live.config import load_deploy_config
        from qsys.ops.alerts import AlertBus, WebhookAlertSink, ledger

        url = load_deploy_config(config_path).alerts.ops_url
        if not url:
            return
        bus = AlertBus([WebhookAlertSink(url)], ledger())
        bad = [p for p in problems if p[0] == BAD]
        warn = [p for p in problems if p[0] == WARN]
        if not bad and not warn:
            bus.info(f"服务质检通过（{day}）", f"{total} 项全部正常")
            return
        lines = [f"{name}：{text}" for _, name, text in (bad + warn)[:10]]
        detail = "\n".join(lines)
        title = f"服务质检 {day}：失败 {len(bad)} 项，告警 {len(warn)} 项"
        (bus.critical if bad else bus.warning)(title, detail)
    except Exception as exc:  # 通知挂了不能影响质检结论
        log.warning("service_checks.notify_failed", error=str(exc))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--config", default="config/deploy.local.toml")
    parser.add_argument("--service-runs", type=Path, default=Path("var/service-runs"))
    parser.add_argument("--shadow-state", type=Path, default=Path("var/shadow-state.sqlite3"),
                        help="影子盘状态库；用于核对当日策略事件是否都跑过")
    parser.add_argument("--live-state", type=Path, default=Path("var/live-state.sqlite3"),
                        help="实盘状态库；live 模式核对当日策略事件")
    parser.add_argument("--workbench", type=Path,
                        default=Path("var/workbench/workbench.sqlite3"),
                        help="模拟盘工作台；同上")
    parser.add_argument("--storage-base", type=Path, default=None,
                        help="节点存储根目录；默认取 warehouse 实体路径的上一级（/data/qsys）")
    parser.add_argument("--scratch", type=Path, default=Path.home() / "scratch",
                        help="临时工作区，纳入保留策略盘点")
    parser.add_argument("--tls-site", default=None,
                        help="公网入口域名（作 SNI 探 127.0.0.1）；不给则跳过证书检查")
    parser.add_argument("--tls-port", type=int, default=8443)
    parser.add_argument("--day", type=date.fromisoformat, default=None,
                        help="要检查的交易日；不给则取最近一个已收盘交易日")
    args = parser.parse_args()

    from qsys.live.config import load_deploy_config

    mode = load_deploy_config(args.config).safety.mode
    checks = checks_for_mode(CHECKS, mode)
    strategy_state = args.live_state if mode == "live" else args.shadow_state

    with ServiceRun.open(args.service_runs, "service-checks") as run:
        with run.step("确定交易日", interface="Tushare trade_cal") as step:
            calendar = load_trading_calendar(args.config)
            # 质检的是**今天**。取「最近一个已收盘交易日」会在假期里天天重查节前
            # 那一天：运维群连收五条一模一样的结论，日报还会把它当成当天的质检
            # 结果，于是「休市日不安排验证」和一份验证结论出现在同一封信里
            # （2026-10-01 国庆第一天实际发生）。交易日 20:00 跑时两者等价。
            day = args.day or datetime.now().date()
            if not calendar.is_trading_day(day):
                run.skip(f"{day} 不是交易日")
                step.detail = run.note
                return 0
            step.detail = str(day)
        if day is None:
            run.skip("没有已收盘的交易日")
            return 0
        run.session = day
        sessions = calendar.sessions_in_range(day - timedelta(days=40), day)
        warehouse = Warehouse(args.warehouse)
        ctx = CheckContext(
            warehouse, WarehouseQuery(warehouse), day,
            sessions[-2] if len(sessions) > 1 else None, sessions,
            # 任务在交易日当天执行；复查旧日子时读那一天的运行记录
            load_runs(args.service_runs, day, day + timedelta(days=1)),
            shadow_state=strategy_state, workbench=args.workbench,
            storage_base=args.storage_base or Path(args.warehouse).resolve().parent,
            scratch=args.scratch,
            tls_site=args.tls_site, tls_port=args.tls_port,
        )
        failed = {"data": 0, "strategy": 0}
        problems: list[tuple[str, str, str]] = []
        # 同一交易日早先的质检（本次记录已落盘，但还没有任何步骤）
        failures = earlier_failures(ctx.runs, day)
        for check in checks:
            with run.step(step_name(check), interface="只读仓库与运行记录",
                          table=check.table, affects_run=check.domain == "data") as step:
                result = mark_repaired(check.fn(ctx), failures.get(step_name(check)))
                step.rows = result.rows
                text = f"期望：{result.expected}；实际：{result.actual}"
                if result.detail:
                    text += f"。{result.detail}"
                if result.status == BAD:
                    failed[check.domain] += 1
                    problems.append((BAD, step_name(check), text))
                    step.fail(text)
                elif result.status == WARN:
                    problems.append((WARN, step_name(check), text))
                    step.warn(text)
                elif result.status == SKIP:
                    step.skip(text)
                elif result.status == REPAIRED:
                    step.status, step.detail = REPAIRED, text
                else:
                    assert result.status == OK
                    step.detail = text
        print(f"{day} 服务质检：{len(checks)} 项，数据未通过 {failed['data']} 项，"
              f"策略事件未通过 {failed['strategy']} 项")
        _notify(args.config, day, problems, total=len(checks))
        if run.status == SKIPPED:
            return 0
        # 策略事件仍作为失败步骤写入运行记录并挂回对应服务，但不能再让
        # 数据质检的 systemd unit 失败；策略链由各自 resident/observer 告警。
        return _exit_code(failed)


if __name__ == "__main__":
    sys.exit(main())
