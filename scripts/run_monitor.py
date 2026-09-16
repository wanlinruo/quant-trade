"""监控台与个人回测工作台启动入口。

默认只监听 127.0.0.1 —— 通过 Tailscale / WireGuard 私有网络访问。
**绝不要绑到 0.0.0.0 并开放安全组**：这个页面能看到你的全部持仓。

用法：
  uv run python scripts/run_monitor.py --warehouse warehouse
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import structlog
import uvicorn

from qsys.data.storage import Warehouse
from qsys.ops.journal import SessionJournal
from qsys.web.app import create_app
from qsys.web.backtest_views import BacktestService
from qsys.web.catalog_views import CatalogService
from qsys.web.config import WebConfig
from qsys.web.data_views import DataService, MarketService
from qsys.web.views import MonitorService
from qsys.web.workbench import BacktestQueue, WorkbenchStore
from qsys.paper.store import PaperStore

log = structlog.get_logger("monitor")


def build_app(
    warehouse_root: Path,
    config: WebConfig,
    config_path: str | None,
    runs_dir: Path | None = None,
    workspace_dir: Path | None = None,
):
    warehouse = Warehouse(warehouse_root)
    journal = SessionJournal(warehouse)

    transport = None
    market_snapshot_key = "state:market_overview"
    market_schedule_key = "state:market_schedule"
    try:
        from qsys.live.config import load_deploy_config
        from qsys.live.redis_transport import RedisTransport

        deploy = load_deploy_config(config_path)
        transport = RedisTransport.from_config(deploy)
        market_snapshot_key = deploy.streams.market_snapshot
        market_schedule_key = deploy.streams.market_schedule
    except Exception as exc:
        # Redis 不可用时监控台仍应能打开——历史数据来自仓库，不依赖 Redis
        log.warning("monitor.redis_unavailable", error=str(exc))

    runs_root = runs_dir or Path("var/runs")
    workspace = workspace_dir or Path("var/workbench")
    workbench = WorkbenchStore(workspace / "workbench.sqlite3")
    paper = PaperStore(workspace / "workbench.sqlite3")
    queue = BacktestQueue(workbench, warehouse_root, runs_root,
                          timeout_seconds=config.backtest_timeout_seconds)
    return create_app(
        MonitorService(journal, config=config, transport=transport),
        config,
        DataService(warehouse),
        BacktestService(runs_root),
        CatalogService(warehouse),
        workbench, queue, Path("strategies"), paper,
        MarketService(
            warehouse, transport, snapshot_key=market_snapshot_key,
            schedule_key=market_schedule_key,
        ),
    )


def main() -> int:
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
    args = parser.parse_args()

    if args.host == "0.0.0.0":  # noqa: S104 - 刻意检查
        log.warning(
            "monitor.public_bind",
            hint="监控台可看到全部持仓，应通过 Tailscale/WireGuard 私有网络访问",
        )

    config = WebConfig(host=args.host, port=args.port)
    app = build_app(Path(args.warehouse), config, args.config,
                    Path(args.runs), Path(args.workspace))
    log.info("monitor.started", host=args.host, port=args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
