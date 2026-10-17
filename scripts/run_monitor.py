"""监控台与个人回测工作台启动入口。

默认只监听 127.0.0.1 —— 通过 Tailscale / WireGuard 私有网络访问。
**绝不要绑到 0.0.0.0 并开放安全组**：这个页面能看到你的全部持仓。

用法：
  uv run python scripts/run_monitor.py --warehouse warehouse
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

import structlog
import uvicorn

from qsys.data.storage import Warehouse
from qsys.ops.journal import SessionJournal
from qsys.ops.health import HealthStore, HealthStoreReader
from qsys.live.store import LiveStore
from qsys.web.app import create_app
from qsys.web.backtest_views import BacktestService
from qsys.web.asset_views import DataAssetService
from qsys.web.catalog_views import CatalogService
from qsys.web.config import WebConfig
from qsys.web.data_views import DataService, MarketService
from qsys.web.limit_up_shadow import LimitUpShadowReader
from qsys.web.limit_up_research import (
    HistoricalSecurityNameLookup,
    LimitUpResearchReader,
)
from qsys.web.service_views import ServiceConsole, windows_task_show
from qsys.web.research_jobs import ResearchJobConsole, ResearchSubmissionProfile
from qsys.web.views import MonitorService
from qsys.web.workbench import BacktestQueue, WorkbenchStore
from qsys.research.jobs import JobLimits
from qsys.paper.store import PaperStore

log = structlog.get_logger("monitor")


def build_app(
    warehouse_root: Path,
    config: WebConfig,
    config_path: str | None,
    runs_dir: Path | None = None,
    workspace_dir: Path | None = None,
    service_runs_dir: Path | None = None,
    tushare_config_path: str | None = None,
    state_path: Path | None = None,
    health_state_path: Path | None = None,
    limit_up_shadow_state_path: Path | None = None,
    limit_up_research_run_path: Path | None = None,
    research_jobs_path: Path | None = None,
    research_release_path: Path | None = None,
    research_snapshots_path: Path | None = None,
):
    warehouse = Warehouse(warehouse_root)
    journal = SessionJournal(warehouse)

    transport = None
    strategy_cfg = None
    strategy_mode = "shadow"
    market_snapshot_key = "state:market_overview"
    market_schedule_key = "state:market_schedule"
    limits_prefix = "state:price_limits"
    try:
        from qsys.live.config import load_deploy_config
        from qsys.live.redis_transport import RedisTransport

        deploy = load_deploy_config(config_path)
        transport = RedisTransport.from_config(deploy)
        strategy_cfg = deploy.strategy
        strategy_mode = deploy.safety.mode
        market_snapshot_key = deploy.streams.market_snapshot
        market_schedule_key = deploy.streams.market_schedule
        limits_prefix = deploy.streams.price_limits
    except Exception as exc:
        # Redis 不可用时监控台仍应能打开——历史数据来自仓库，不依赖 Redis
        log.warning("monitor.redis_unavailable", error=str(exc))

    runs_root = runs_dir or Path("var/runs")
    workspace = workspace_dir or Path("var/workbench")
    workbench = WorkbenchStore(workspace / "workbench.sqlite3")
    paper = PaperStore(workspace / "workbench.sqlite3")
    queue = BacktestQueue(workbench, warehouse_root, runs_root,
                          timeout_seconds=config.backtest_timeout_seconds)
    selected_state = state_path or Path("var/shadow-state.sqlite3")
    if strategy_mode == "live" and selected_state.name == "shadow-state.sqlite3":
        selected_state = selected_state.with_name("live-state.sqlite3")
    monitor = MonitorService(
        journal,
        config=config,
        transport=transport,
        store=LiveStore(selected_state),
    )
    trading_calendar = _daily_trading_calendar(tushare_config_path or config_path)
    research_console = None
    if research_jobs_path is not None:
        profile = None
        if research_release_path is not None and research_snapshots_path is not None:
            profile = ResearchSubmissionProfile.from_release(
                research_release_path, research_snapshots_path,
                JobLimits(4096, 100, 10, 14_400),
            )
        research_console = ResearchJobConsole(research_jobs_path, profile=profile)
    return create_app(
        monitor,
        config,
        DataService(warehouse),
        BacktestService(runs_root),
        CatalogService(warehouse),
        workbench, queue, Path("strategies"), paper,
        MarketService(
            warehouse, transport, snapshot_key=market_snapshot_key,
            schedule_key=market_schedule_key,
            limit_check_prefix=f"{limits_prefix}_check",
        ),
        paper_calendar=trading_calendar,
        # 影子核心承袭的模拟账户；每次请求现查，配置改了不用重启监控台
        shadow_lineage=(lambda: _shadow_lineage(strategy_cfg)),
        services=ServiceConsole(
            service_runs_dir or Path("var/service-runs"),
            calendar=trading_calendar,
            # 没有 Redis 时不判心跳（显示未知），不能把「查不到」当成「进程已死」
            heartbeat=monitor.heartbeat_status if transport is not None else None,
            windows_task=lambda task: windows_task_show(
                task,
                host=os.environ.get("QSYS_WINDOWS_SSH_HOST", "Administrator@118.25.42.149"),
                key=Path(os.environ.get("QSYS_WINDOWS_SSH_KEY",
                                        "/home/ubuntu/.ssh/id_ed25519_winnode")),
            ),
            health_store=(
                HealthStore(Path(health_state_path)) if health_state_path else
                HealthStoreReader((
                    Path("var/health-observer/service-health.sqlite3"),
                    Path("var/service-health.sqlite3"),
                ))
            ),
            core_service_id={
                "shadow": "shadow-core", "live": "live-core",
            }.get(strategy_mode),
        ),
        assets=DataAssetService(warehouse),
        limit_up_shadow=LimitUpShadowReader(
            limit_up_shadow_state_path or Path("var/limit-up-shadow.sqlite3")
        ),
        limit_up_research=LimitUpResearchReader(
            limit_up_research_run_path or Path("var/research/issue-210"),
            name_lookup=HistoricalSecurityNameLookup(warehouse_root),
        ),
        research_jobs=research_console,
    )


def _daily_trading_calendar(config_path: str | None):
    """每个自然日向 Tushare 取一次交易日历；取不到就抛错，由页面报 503。"""
    from qsys.paper.calendar import load_trading_calendar

    cache: dict[date, object] = {}

    def get():
        today = date.today()
        if today not in cache:
            cache.clear()
            cache[today] = load_trading_calendar(config_path)
        return cache[today]

    return get


def _shadow_lineage(strategy_cfg):
    """只读查询影子核心承袭的模拟账户；查不到就不展示，不让监控台因此报错。"""
    if strategy_cfg is None:
        return None
    from qsys.live.lineage import paper_lineage

    try:
        return paper_lineage(strategy_cfg)
    except Exception as exc:  # 配置写错或账户被删：页面少一块，不影响监控
        log.warning("monitor.shadow_lineage_unavailable", error=str(exc))
        return None


def main() -> int:
    from qsys.data.cutover import add_facade_argument, apply_facade_argument

    parser = argparse.ArgumentParser(description="量化监控与回测工作台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--config", default=None)
    parser.add_argument("--workspace", default="var/workbench",
                        help="策略草稿、不可变版本与任务数据库")
    parser.add_argument(
        "--runs", default="var/runs",
        help="回测运行的落盘根目录，回测页用它列出与下载",
    )
    parser.add_argument("--service-runs", default="var/service-runs",
                        help="定时任务运行记录目录，服务管理页只读")
    parser.add_argument(
        "--state",
        default="var/shadow-state.sqlite3",
        help="影子盘状态库，实盘运行日志页面只读",
    )
    parser.add_argument(
        "--health-state", default="var/service-health.sqlite3",
        help="服务健康状态变更与检查点 SQLite",
    )
    parser.add_argument(
        "--limit-up-shadow-state", default="var/limit-up-shadow.sqlite3",
        help="T+1 涨停概率影子榜单 SQLite（监控台严格只读）",
    )
    parser.add_argument(
        "--limit-up-research-run", default="var/research/issue-210",
        help="#210 冻结研究回测目录（监控台只读紧凑证据）",
    )
    parser.add_argument(
        "--research-jobs", default=None,
        help="隔离 Research Runner 作业目录；不给则不注册相关页面和 API",
    )
    parser.add_argument(
        "--research-release", default=None,
        help="已验证的版本化 Runner release；与 --research-snapshots 同时给出才开放提交",
    )
    parser.add_argument(
        "--research-snapshots", default=None,
        help="不可变 snapshot store；与 --research-release 同时给出才开放提交",
    )
    parser.add_argument("--tushare-config", default=None,
                        help="只取 [tushare] 凭据查交易日历；不给则用 --config。"
                             "测试环境用它避免连到生产 Redis")
    add_facade_argument(parser)
    args = parser.parse_args()
    apply_facade_argument(args.market_data_facade)

    if args.host == "0.0.0.0":  # noqa: S104 - 刻意检查
        log.warning(
            "monitor.public_bind",
            hint="监控台可看到全部持仓，应通过 Tailscale/WireGuard 私有网络访问",
        )

    config = WebConfig(host=args.host, port=args.port)
    app = build_app(
        Path(args.warehouse), config, args.config,
        Path(args.runs), Path(args.workspace), Path(args.service_runs),
        args.tushare_config, Path(args.state), Path(args.health_state),
        Path(args.limit_up_shadow_state), Path(args.limit_up_research_run),
        Path(args.research_jobs) if args.research_jobs else None,
        Path(args.research_release) if args.research_release else None,
        Path(args.research_snapshots) if args.research_snapshots else None,
    )
    log.info("monitor.started", host=args.host, port=args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
