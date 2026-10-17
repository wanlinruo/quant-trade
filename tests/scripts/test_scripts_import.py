"""每个 Linux 侧入口脚本都必须能导入。

2026-09-17 把 `trading_calendar` 从 run_paper 挪到 qsys.paper.calendar 时，
run_shadow / run_live 仍从旧位置导入，影子盘重启后在导入阶段循环崩溃——
没有任何测试导入过这两个脚本。
"""

import importlib
from pathlib import Path

import pytest

# 生产入口：systemd 服务、定时任务与运维命令直接调用的脚本。一次性分析脚本
# （regression_* / reconcile_* 等）在导入时就读数据文件，不在此列。
SCRIPTS = [
    "backfill_canonical", "daily_archive", "deployment_contract", "kill",
    "live_preflight", "run_agent", "run_backtest",
    "run_health_observer", "run_live", "run_market_gateway", "run_monitor", "run_paper",
    "run_shadow",
    "catalog_verify", "tushare_daily_sync", "tushare_premarket_limits", "verify_metrics",
    "verify_warehouse_backup",
    "publish_joinquant_minute_overrides", "publish_paid_minute_overrides",
    "repair_minute_bars_padding", "repair_paid_minute_overrides", "storage_report",
]


def test_every_listed_entry_script_exists():
    assert all(Path(f"scripts/{name}.py").exists() for name in SCRIPTS)


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_module_imports(name):
    importlib.import_module(f"scripts.{name}")


def test_premarket_legacy_script_delegates_to_packaged_entrypoint():
    legacy = importlib.import_module("scripts.tushare_premarket_limits")
    packaged = importlib.import_module("qsys.data.premarket_limits_cli")

    assert legacy.main is packaged.main
