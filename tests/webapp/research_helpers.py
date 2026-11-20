"""研究 Web 测试共用的隔离 Runner 控制台夹具。"""

from __future__ import annotations

from pathlib import Path

from qsys.data.snapshots import create_snapshot
from qsys.research.job_console import ResearchJobConsole, ResearchSubmissionProfile
from qsys.research.jobs import JobLimits


def research_console(tmp_path: Path) -> ResearchJobConsole:
    jobs = tmp_path / "research-jobs"
    for name in ("inbox", "jobs", "control", "rejected"):
        (jobs / name).mkdir(parents=True)
    warehouse = tmp_path / "warehouse"
    warehouse.mkdir()
    (warehouse / "sample.txt").write_text("immutable\n", encoding="utf-8")
    snapshots = tmp_path / "snapshot-store"
    create_snapshot(
        warehouse,
        snapshots,
        complete_through="2026-10-13",
        schema_version="test-v1",
        snapshot_id="close-20261013",
        publish_current=True,
    )
    profile = ResearchSubmissionProfile(
        code_sha="a" * 40,
        wheel_sha256="b" * 64,
        snapshot_store=snapshots,
        limits=JobLimits(4096, 100, 10, 14_400),
    )
    return ResearchJobConsole(jobs, profile=profile)
