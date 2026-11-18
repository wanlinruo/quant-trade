"""结构化告警事件：模型、受管键清单、事件存储（#662 计划 03）。"""

from __future__ import annotations

import json
import re
import string
from datetime import date, datetime

import pytest

from qsys.ops.alert_events import (
    SHANGHAI, AlertEvent, AlertEventLog, Severity, new_event_id, render_text,
)
from qsys.ops.alert_keys import KEYS, lookup


def event(**over) -> AlertEvent:
    base = dict(event_id=new_event_id(), key="live.reconcile.failed", source="live-core", subject="",
                severity=Severity.CRITICAL, state="firing", title="实盘账户对账失败", detail="差 1 股",
                evidence={"diff": 1}, occurred_at=datetime(2026, 11, 18, 9, 30, 1, tzinfo=SHANGHAI))
    return AlertEvent(**(base | over))


def test_event_roundtrip_and_aware_time():
    e = event()
    back = AlertEvent.model_validate_json(e.model_dump_json())
    assert back == e and back.schema_version == 1
    assert back.occurred_at.utcoffset().total_seconds() == 8 * 3600
    with pytest.raises(ValueError):
        event(occurred_at=datetime(2026, 11, 18, 9, 30))  # 无时区时间不允许
    with pytest.raises(ValueError):
        event(extra_field=1)


def test_render_text_matches_legacy_format():
    """与旧 Alert.as_text 逐字一致：[级别] 时间 标题 + 换行 + 详情；无详情时只有首行。"""
    assert render_text(event()) == "[CRITICAL] 2026-11-18 09:30:01 实盘账户对账失败\n差 1 股"
    assert render_text(event(detail="", severity=Severity.INFO, title="t")) == "[INFO] 2026-11-18 09:30:01 t"


def test_read_skips_partial_and_unknown_version_lines(tmp_path):
    log = AlertEventLog(tmp_path)
    a, b = event(), event(title="第二条")
    log.append(a)
    path = tmp_path / "2026-11-18.events.jsonl"
    with path.open("a", encoding="utf-8") as out:
        out.write('{"schema_version": 1, "key": "x"\n')             # 半行
        out.write(json.dumps({**json.loads(a.model_dump_json()), "schema_version": 2}) + "\n")
    log.append(b)
    ns = tmp_path / "health-observer"
    AlertEventLog(ns).append(event(source="health-observer", title="观察"))
    titles = [e.title for e in log.read(date(2026, 11, 18))]
    assert titles == ["实盘账户对账失败", "第二条", "观察"]


def test_append_failure_is_swallowed(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    AlertEventLog(blocker / "sub").append(event())  # 父路径是文件，无法创建目录；不抛异常
    assert AlertEventLog(tmp_path / "missing").read(date(2026, 11, 18)) == []


def test_key_registry_ids_unique_and_templates_format():
    assert len(KEYS) == len({k.id for k in KEYS.values()})
    for key_id, k in KEYS.items():
        assert key_id == k.id and re.fullmatch(r"[a-z_]+(\.[a-z_]+){2}", key_id), key_id
        assert k.kind in ("incident", "notice") and k.severities and k.source and k.description
        names = {f for _, f, _, _ in string.Formatter().parse(k.title) if f}
        k.title.format(**{n: "x" for n in names})
    assert lookup("live.reconcile.failed") is KEYS["live.reconcile.failed"]
    assert lookup("nope.never.used") is None


def test_every_emit_key_is_registered():
    """仓库内 emit("…") 的字面量键都已在受管清单登记；infra.* 经 event_key 映射，单独测试。"""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "src" / "qsys"
    used = set()
    for path in root.rglob("*.py"):
        used |= set(re.findall(r"""\.emit\(\s*["']([a-z_.]+)["']""", path.read_text(encoding="utf-8")))
        used |= set(re.findall(r"""_alert\(runtime,\s*["']([a-z_.]+)["']""", path.read_text(encoding="utf-8")))
    assert used, "没有扫描到任何 emit 调用"
    assert used - set(KEYS) == set(), used - set(KEYS)
    assert {"live.reconcile.failed", "observer.process.down", "drill.reboot.passed"} <= used
