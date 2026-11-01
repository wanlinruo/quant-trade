"""停复牌两个写入口共享的观测收据；不裁决市场事实，不自动消除差异。

调用方必须持有正常 warehouse 写锁。JSON 位于数据集的 _observations 下，
不属于查询层 year=*/part.parquet 的市场表；写前收据失败时不得继续写表。
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import polars as pl

from qsys.data import schema
from qsys.data.storage import Warehouse


def _rows(frame: pl.DataFrame) -> list[dict]:
    return json.loads(frame.select(schema.SUSPEND_DAILY.names).sort(
        schema.SUSPEND_DAILY.names, nulls_last=False,
    ).write_json())


def _encoded(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _fingerprint(path: Path) -> dict | None:
    if not path.exists():
        return None
    before = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    after = path.stat()
    identity = lambda st: [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns]
    if identity(before) != identity(after):
        raise RuntimeError("停复牌分区在审计读取期间变化")
    return {"sha256": digest, "identity": identity(after)}


def _save_new(path: Path, value: dict) -> None:
    """先持久化内容，再排他发布；已发布的收据永不覆盖。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        with os.fdopen(fd, "wb") as stream:
            stream.write(_encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class SuspensionAudit:
    observation_id: str
    path: Path
    unresolved_ids: tuple[str, ...]

    @property
    def detail(self) -> str:
        return (f"observation_id={self.observation_id}; "
                f"unresolved_observations={len(self.unresolved_ids)}; "
                "accepted_scope=current_entry_only")

    @property
    def progress(self) -> dict:
        return {"suspension_observation": str(self.path),
                "unresolved_observation_ids": list(self.unresolved_ids),
                "accepted_scope": "current_entry_only"}


def apply_observed_suspensions(
    warehouse: Warehouse, day: date, previous: pl.DataFrame, incoming: pl.DataFrame,
    *, entry: str, accepted: bool, write: Callable[[], object],
    observed_at: datetime | None = None,
) -> SuspensionAudit:
    """保存完整旧新响应后按原策略写表；只传播未决差异，不决定其对错。

    完成收据缺失/写入失败仍是未决观测，后续成功不会把它抹掉。旧版本历史
    无此收据时不能反推；既有外部 Issue 也不由本模块自动关闭。
    """
    observed_at = observed_at or datetime.now().astimezone()
    root = warehouse.root / "suspend_daily" / "_observations" / day.isoformat()
    unresolved: set[str] = set()
    for path in sorted(root.glob("*.observed.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record["version"] != 1 or record["day"] != day.isoformat():
            raise ValueError("停复牌旧观测收据版本/日期不符")
        unresolved.update(record["unresolved_ids"])
        outcome = path.with_name(path.name.replace(".observed.json", ".result.json"))
        if not outcome.exists():
            unresolved.add(record["observation_id"])
        else:
            result = json.loads(outcome.read_text(encoding="utf-8"))
            if result["observation_id"] != record["observation_id"]:
                raise ValueError("停复牌完成收据身份不符")
            if result["status"] != "completed":
                unresolved.add(record["observation_id"])

    old, new = _rows(previous), _rows(incoming)
    old_counts, new_counts = Counter(map(_encoded, old)), Counter(map(_encoded, new))
    removed = [json.loads(row) for row, count in (old_counts - new_counts).items()
               for _ in range(count)]
    added = [json.loads(row) for row, count in (new_counts - old_counts).items()
             for _ in range(count)]
    observation_id = uuid.uuid4().hex
    if removed:
        unresolved.add(observation_id)
    target = warehouse.path_for("suspend_daily", year=day.year)
    baseline = _fingerprint(target)
    path = root / f"{observation_id}.observed.json"
    _save_new(path, {
        "version": 1, "observation_id": observation_id,
        "day": day.isoformat(), "observed_at": observed_at.isoformat(),
        "entry": entry, "accepted": accepted,
        "accepted_scope": "current_entry_only",
        "previous": {"rows": old, "sha256": hashlib.sha256(_encoded(old)).hexdigest()},
        "incoming": {"rows": new, "sha256": hashlib.sha256(_encoded(new)).hexdigest()},
        "removed": removed, "added": added,
        "partition_before": baseline, "unresolved_ids": sorted(unresolved),
        "historical_observations_reconstructed": False,
    })
    result_path = root / f"{observation_id}.result.json"
    write_completed = False
    try:
        if _fingerprint(target) != baseline:
            raise RuntimeError("停复牌分区在写前审计后变化，拒绝覆盖")
        if accepted:
            write()
            write_completed = True
        after = _fingerprint(target)
    except Exception as exc:
        _save_new(result_path, {
            "observation_id": observation_id, "status": "attempt_failed",
            "write_attempted": accepted, "write_completed": write_completed,
            "error_type": type(exc).__name__,
            "finished_at": datetime.now().astimezone().isoformat(),
        })
        raise
    _save_new(result_path, {
        "observation_id": observation_id, "status": "completed",
        "written": accepted, "partition_after": after,
        "finished_at": datetime.now().astimezone().isoformat(),
    })
    return SuspensionAudit(observation_id, path, tuple(sorted(unresolved)))
