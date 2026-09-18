"""每日服务质检先跑指标字典验证，再做各任务的到货检查。"""

from __future__ import annotations

import json
from datetime import date, datetime

from qsys.data.catalog.verification import VerificationSummary
from qsys.ops.service_checks import OK, CheckResult, ServiceCheck
from scripts import service_checks

DAY = date(2026, 9, 17)


class _Calendar:
    def is_trading_day(self, day):
        return True

    def sessions_in_range(self, start, end):
        return [date(2026, 9, d) for d in (16, 17)]


def test_verification_runs_before_the_arrival_checks(monkeypatch, tmp_path, warehouse):
    order: list[str] = []
    summary = VerificationSummary(DAY, "r", 347, 0, 2, ("B4.zero_limit_up",))
    probe = ServiceCheck("tushare-sync", "探针", "tushare_daily",
                         lambda ctx: order.append("check")
                         or CheckResult(OK, "跑过验证之后", "是"))
    monkeypatch.setattr(service_checks, "load_trading_calendar", lambda config: _Calendar())
    monkeypatch.setattr(service_checks, "verify_session",
                        lambda wh, day: order.append("verify") or summary)
    monkeypatch.setattr(service_checks, "CHECKS", (probe,))
    runs = tmp_path / "runs"
    monkeypatch.setattr("sys.argv", [
        "service_checks.py", "--warehouse", str(warehouse.root),
        "--service-runs", str(runs), "--day", DAY.isoformat(),
    ])

    assert service_checks.main() == 0
    assert order == ["verify", "check"]

    record = json.loads(next(runs.rglob("service-checks-*.json")).read_text(encoding="utf-8"))
    names = [step["name"] for step in record["steps"]]
    assert names == ["确定交易日", "指标字典验证", "tushare-sync｜探针"]
    step = record["steps"][1]
    assert step["status"] == "succeeded" and step["rows"] == 347
    assert "2026 年至今全量" in step["detail"] and "警告 2 条" in step["detail"]
    assert "B4.zero_limit_up" in step["detail"]


def test_verification_is_skipped_while_the_sync_is_still_running(
    monkeypatch, tmp_path, warehouse,
):
    """同步与验证各自峰值约 5 GB，机器只有 7.5 GB：撞上就跳过，不硬碰。"""
    runs = tmp_path / "runs"
    folder = runs / f"{datetime.now():%Y/%m/%d}"
    folder.mkdir(parents=True)
    (folder / "tushare-sync-190000-1.json").write_text(json.dumps({
        "run_id": "tushare-sync-190000-1", "service": "tushare-sync",
        "session": DAY.isoformat(),
        # 刚起步的同步才算「运行中」；超过 max_runtime 的记录是「已中断」，不该挡住验证
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "status": "running", "steps": [],
    }), encoding="utf-8")
    called = []
    monkeypatch.setattr(service_checks, "load_trading_calendar", lambda config: _Calendar())
    monkeypatch.setattr(service_checks, "verify_session",
                        lambda wh, day: called.append(day) or VerificationSummary(
                            day, "r", 0, 0, 0, ()))
    monkeypatch.setattr(service_checks, "CHECKS", ())
    monkeypatch.setattr("sys.argv", [
        "service_checks.py", "--warehouse", str(warehouse.root),
        "--service-runs", str(runs), "--day", DAY.isoformat(),
    ])

    assert service_checks.main() == 0
    assert called == []
    record = json.loads(next(runs.rglob("service-checks-*.json")).read_text(encoding="utf-8"))
    step = next(s for s in record["steps"] if s["name"] == "指标字典验证")
    assert step["status"] == "skipped" and "同步仍在运行" in step["detail"]
