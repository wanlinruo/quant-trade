from pathlib import Path

import pytest

from qsys.web import backtest_worker_cli


def test_worker_cli_owns_the_queue_lifecycle(tmp_path, monkeypatch):
    calls = {}

    class Store:
        def __init__(self, path):
            calls["store"] = path

    class Queue:
        def __init__(self, store, warehouse, runs, *, timeout_seconds):
            calls["queue"] = (store, warehouse, runs, timeout_seconds)

        def stop(self):
            calls["stopped"] = True

        def serve_forever(self):
            calls["served"] = True

    handlers = {}
    monkeypatch.setattr(backtest_worker_cli, "WorkbenchStore", Store)
    monkeypatch.setattr(backtest_worker_cli, "BacktestQueue", Queue)
    monkeypatch.setattr(
        backtest_worker_cli.signal, "signal",
        lambda signum, handler: handlers.setdefault(signum, handler),
    )

    workspace = tmp_path / "workbench"
    warehouse = tmp_path / "warehouse"
    runs = tmp_path / "runs"
    assert backtest_worker_cli.main([
        "--workspace", str(workspace), "--warehouse", str(warehouse),
        "--runs", str(runs), "--timeout-seconds", "30",
    ]) == 0

    assert calls["store"] == workspace / "workbench.sqlite3"
    assert calls["queue"][1:] == (warehouse, runs, 30)
    assert calls["served"] is True
    handlers[backtest_worker_cli.signal.SIGTERM](0, None)
    assert calls["stopped"] is True


def test_worker_cli_rejects_non_positive_timeout(tmp_path: Path):
    with pytest.raises(SystemExit):
        backtest_worker_cli.main([
            "--workspace", str(tmp_path), "--warehouse", str(tmp_path),
            "--runs", str(tmp_path), "--timeout-seconds", "0",
        ])
