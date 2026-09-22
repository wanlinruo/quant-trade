from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qsys.ops.journal import SessionJournal
from qsys.web.app import _backtest_rows, create_app
from qsys.web.backtest_views import BacktestService
from qsys.web.config import WebConfig
from qsys.web.views import MonitorService
from qsys.web.workbench import WorkbenchStore


SOURCE = '''
from qsys.strategy.base import Strategy
PARAMETERS = {"window": {"type": "int", "default": 20, "min": 2}}
class Demo(Strategy):
    pass
'''


@pytest.fixture
def store(tmp_path):
    return WorkbenchStore(tmp_path / "workbench" / "jobs.sqlite3")


def test_draft_and_immutable_run_version_are_separate(store):
    strategy = store.create_strategy("demo", SOURCE)
    first = store.enqueue(strategy.id, {"start": "2024-01-01", "end": "2024-01-31",
                                        "cash": 100_000, "params": {"window": 20}})
    old_source = store.source_for_job(first.id)
    store.save_strategy(strategy.id, "demo", SOURCE.replace("pass", "def on_bar(self, ctx, bars): pass"))
    assert store.source_for_job(first.id) == old_source
    assert len(store.versions(strategy.id)) == 1


def test_queue_claim_is_fifo_and_only_claims_once(store):
    strategy = store.create_strategy("demo", SOURCE)
    first = store.enqueue(strategy.id, {"start": "2024-01-01"})
    store.enqueue(strategy.id, {"start": "2024-02-01"})
    assert store.claim_next().id == first.id
    assert store.claim_next() is None  # 全库同时只能有一个 running
    store.finish(first.id, "succeeded")
    assert store.claim_next().config["start"] == "2024-02-01"
    assert store.claim_next() is None


def test_progress_finish_cancel_and_restart_recovery(store):
    strategy = store.create_strategy("demo", SOURCE)
    running = store.enqueue(strategy.id, {})
    store.claim_next()
    store.progress(running.id, 3, 10, "2024-01-03")
    assert store.job(running.id).percent == 30
    store.recover_interrupted()
    assert store.job(running.id).status == "interrupted"
    queued = store.enqueue(strategy.id, {})
    assert store.cancel(queued.id)
    assert store.job(queued.id).status == "cancelled"


@pytest.fixture
def client(warehouse, tmp_path, store):
    cfg = WebConfig()
    monitor = MonitorService(SessionJournal(warehouse), config=cfg)
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "sample.py").write_text(SOURCE, encoding="utf-8")
    return TestClient(create_app(monitor, cfg, backtests=BacktestService(tmp_path / "runs"),
                                 workbench=store, strategy_templates=templates))


def test_strategy_create_edit_enqueue_and_cancel_api(client):
    created = client.post("/api/strategies", json={"name": "demo", "source": SOURCE})
    assert created.status_code == 201
    ident = created.json()["id"]
    assert "demo" in client.get(f"/strategies/{ident}").text
    saved = client.put(f"/api/strategies/{ident}", json={"name": "renamed", "source": SOURCE})
    assert saved.status_code == 200
    run = client.post("/api/backtests", json={
        "strategy_id": ident, "start": "2024-01-01", "end": "2024-01-31",
        "cash": 100000, "params": {"window": "30"},
    })
    assert run.status_code == 201
    job_id = run.json()["id"]
    assert client.post(f"/api/backtests/{job_id}/cancel").status_code == 200
    assert "cancelled" in client.get(f"/backtests/{job_id}").text


def test_job_status_api_supports_incremental_progress(client, store):
    ident = client.post("/api/strategies", json={"name": "demo", "source": SOURCE}).json()["id"]
    job_id = client.post("/api/backtests", json={
        "strategy_id": ident, "start": "2024-01-01", "end": "2024-01-31",
        "cash": 100000, "params": {"window": 20},
    }).json()["id"]
    store.claim_next()
    store.progress(job_id, 3, 10, "2024-01-03")
    payload = client.get(f"/api/backtests/{job_id}").json()
    assert payload["status"] == "running"
    assert payload["percent"] == 30
    assert payload["current_session"] == "2024-01-03"
    assert payload["report_ready"] is False
    assert client.get("/api/backtests/missing").status_code == 404


def test_history_merges_a_successful_job_with_its_published_report(tmp_path, store):
    strategy = store.create_strategy("demo", SOURCE)
    job = store.enqueue(strategy.id, {
        "start": "2024-01-01", "end": "2024-01-31", "cash": 100_000,
        "jq_compat": False, "params": {"window": 20},
    })
    store.claim_next()
    store.finish(job.id, "succeeded", run_name=job.id)
    run = tmp_path / "runs" / job.id
    run.mkdir(parents=True)
    (run / "run_status.csv").write_text(
        "run_id,strategy,start,end,initial_value,total_value,cumulative_return\n"
        "run-1,Demo,2024-01-01,2024-01-31,100000,110000,0.1\n",
        encoding="utf-8",
    )
    (run / "config.json").write_text('{"cost_model":"qsys"}', encoding="utf-8")
    rows = _backtest_rows(BacktestService(tmp_path / "runs"), store)
    assert len(rows) == 1
    assert rows[0]["id"] == job.id
    assert rows[0]["return_pct"] == pytest.approx(10.0)


def test_bad_source_and_parameters_return_422(client):
    assert client.post("/api/strategies", json={"name": "bad", "source": "if"}).status_code == 422
    ident = client.post("/api/strategies", json={"name": "demo", "source": SOURCE}).json()["id"]
    response = client.post("/api/backtests", json={
        "strategy_id": ident, "start": "2024-01-01", "end": "2024-01-31",
        "cash": 100000, "params": {"window": 1},
    })
    assert response.status_code == 422


def test_repository_import_cannot_escape_its_root(client):
    assert client.post("/api/strategies/import", json={"filename": "../secret.py"}).status_code == 422
    imported = client.post("/api/strategies/import", json={"filename": "sample.py"})
    assert imported.status_code == 201


def test_backtest_records_the_entry_fill_mode_and_defaults_to_continuous(client, store):
    ident = client.post("/api/strategies", json={"name": "demo", "source": SOURCE}).json()["id"]
    base = {"strategy_id": ident, "start": "2024-01-01", "end": "2024-01-31",
            "cash": 100000, "params": {"window": 20}}
    default = client.post("/api/backtests", json=base).json()["id"]
    legacy = client.post("/api/backtests", json={**base, "entry_fill": "open"}).json()["id"]
    assert store.job(default).config["entry_fill"] == "continuous"
    assert store.job(legacy).config["entry_fill"] == "open"
    assert "连续竞价判定" in client.get(f"/backtests/{default}").text
    bad = client.post("/api/backtests", json={**base, "entry_fill": "slippage"})
    assert bad.status_code == 422
