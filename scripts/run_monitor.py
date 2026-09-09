"""监控台启动入口。

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
from qsys.web.config import WebConfig
from qsys.web.data_views import DataService
from qsys.web.views import MonitorService

log = structlog.get_logger("monitor")


def build_app(
    warehouse_root: Path,
    config: WebConfig,
    config_path: str | None,
    selection_path: Path | None = None,
):
    warehouse = Warehouse(warehouse_root)
    journal = SessionJournal(warehouse)

    transport = None
    try:
        from qsys.live.config import load_deploy_config
        from qsys.live.redis_transport import RedisTransport

        transport = RedisTransport.from_config(load_deploy_config(config_path))
    except Exception as exc:
        # Redis 不可用时监控台仍应能打开——历史数据来自仓库，不依赖 Redis
        log.warning("monitor.redis_unavailable", error=str(exc))

    return create_app(
        MonitorService(journal, config=config, transport=transport),
        config,
        DataService(warehouse, selection_path),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="只读监控台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--config", default=None)
    parser.add_argument(
        "--selection", default="var/selection_loose.csv",
        help="run_selection.py 产出的选股结果，数据台用它渲染",
    )
    args = parser.parse_args()

    if args.host == "0.0.0.0":  # noqa: S104 - 刻意检查
        log.warning(
            "monitor.public_bind",
            hint="监控台可看到全部持仓，应通过 Tailscale/WireGuard 私有网络访问",
        )

    config = WebConfig(host=args.host, port=args.port)
    app = build_app(Path(args.warehouse), config, args.config, Path(args.selection))
    log.info("monitor.started", host=args.host, port=args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
