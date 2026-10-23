"""Web实际装配只消费observer，Windows状态不依赖SSH凭据。"""

from datetime import datetime, timedelta

import pytest

from qsys.ops.health import DOWN, HEALTHY, MORNING, SHANGHAI, HealthObservation, HealthStore
from qsys.web import monitor_cli
from qsys.web.config import WebConfig


@pytest.mark.parametrize(
    ("sample", "expected"),
    [("missing", "unknown"), ("fresh", "ok"), ("stale", "unknown"), ("failed", "bad")],
)
def test_build_app_windows_status_uses_observer_without_ssh(
    tmp_path, monkeypatch, sample, expected,
):
    import qsys.live.config as live_config
    import subprocess

    now = datetime(2026, 10, 23, 8, 40, tzinfo=SHANGHAI)
    path = tmp_path / "observer" / "service-health.sqlite3"
    if sample != "missing":
        state = DOWN if sample == "failed" else HEALTHY
        HealthStore(path).record(HealthObservation(
            service_id="watchdog",
            observed_at_utc=now - timedelta(minutes=8 if sample == "stale" else 1),
            phase=MORNING, expected_interval_s=300, warn_after_s=900, down_after_s=1500,
            state=state, process_state=state, business_state="unknown",
            reason_code="watchdog_failed" if sample == "failed" else "watchdog_fresh",
            source="windows_task",
        ))
    captured = {}
    monkeypatch.setattr(monitor_cli, "create_app", lambda *a, **kw: captured.update(kw))
    monkeypatch.setattr(live_config, "load_deploy_config",
                        lambda _: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setenv("QSYS_WINDOWS_SSH_KEY", str(tmp_path / "nonexistent-key"))

    def no_remote_probe(*a, **kw):
        raise AssertionError("Web must not start SSH/systemd probes")

    monkeypatch.setattr(subprocess, "run", no_remote_probe)
    monitor_cli.build_app(
        tmp_path / "warehouse", WebConfig(), None,
        workspace_dir=tmp_path / "workspace", health_state_path=path,
    )
    console = captured["services"]
    console._now = lambda: now
    assert console._windows_task is None
    watchdog = next(row for row in console.process_status() if row["spec"].id == "watchdog")
    assert watchdog["state"] == expected
    if sample == "stale":
        assert watchdog["health_observation"]["reason_code"] == "observer_stale"
    if sample == "failed":
        assert watchdog["health_observation"]["reason_code"] == "watchdog_failed"
