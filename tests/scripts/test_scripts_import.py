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
    "verify_storage_report_preproduction", "backfill_canonical", "daily_archive", "deployment_contract", "kill",
    "reboot_drill", "live_preflight", "run_agent", "run_backtest",
    "run_health_observer", "run_live", "run_market_gateway", "run_monitor", "run_paper",
    "run_shadow", "limit_up_shadow_cycle", "run_limit_up_shadow", "observe_limit_up_shadow",
    "catalog_verify", "tushare_daily_sync", "tushare_premarket_limits", "verify_metrics",
    "verify_warehouse_backup", "auction_timing_study", "gate_study", "run_structural_backtest",
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


def test_tushare_sync_legacy_script_delegates_to_packaged_entrypoint():
    legacy = importlib.import_module("scripts.tushare_daily_sync")
    packaged = importlib.import_module("qsys.data.tushare_sync_cli")

    assert legacy.main is packaged.main


def test_monitor_cannot_host_the_backtest_worker():
    monitor = Path("scripts/run_monitor.py").read_text(encoding="utf-8")
    app = Path("src/qsys/web/app.py").read_text(encoding="utf-8")

    assert "BacktestQueue" not in monitor
    assert "BacktestQueue" not in app
    assert "queue.start()" not in app


def test_monitor_legacy_script_delegates_to_packaged_entrypoint():
    legacy = importlib.import_module("scripts.run_monitor")
    packaged = importlib.import_module("qsys.web.monitor_cli")

    assert legacy.main is packaged.main
    assert legacy.build_app is packaged.build_app


@pytest.mark.parametrize("name", ["qsys.ops.service_checks_index"])
def test_packaged_production_entrypoint_imports(name):
    importlib.import_module(name)
