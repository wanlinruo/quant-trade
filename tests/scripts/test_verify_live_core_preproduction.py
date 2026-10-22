import sqlite3
from datetime import datetime, timedelta

import pytest

from scripts.verify_live_core_preproduction import copy_inputs, state_fingerprint
from qsys.live.config import StreamConfig
from qsys.live.messages import AccountSnapshot, Heartbeat
from qsys.live.transport import MemoryTransport
from qsys.live.validation_transport import ValidationTransport


def test_fingerprint_includes_committed_wal_and_does_not_change_source(tmp_path):
    path = tmp_path / "state.sqlite3"
    with sqlite3.connect(path) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE events (event_key TEXT PRIMARY KEY, payload BLOB)")
        con.execute("INSERT INTO events VALUES ('completed', ?)", (b'\x00\xff',))
        con.commit()
        before = state_fingerprint(path)
        assert before["tables"]["events"]["rows"] == 1
        assert state_fingerprint(path) == before
        con.execute("INSERT INTO events VALUES ('new-fill', ?)", (b'actual',))
        con.commit()
        after = state_fingerprint(path)
        assert after["tables"]["events"]["rows"] == 2
        assert after != before


def test_input_mirror_reads_only_fixed_snapshots_and_writes_only_namespace():
    streams = StreamConfig()
    allowed = {streams.account, streams.heartbeat + ":agent", streams.heartbeat + ":market", "control:kill"}
    prefix = "component-validation:issue497-test"

    class GuardedMemory(MemoryTransport):
        def get_key(self, key):
            assert key in allowed or key.startswith(prefix + ":")
            return super().get_key(key)

        def set_key(self, key, value):
            assert key.startswith(prefix + ":")
            return super().set_key(key, value)

    raw = GuardedMemory()
    now = datetime.now()
    values = {streams.account: AccountSnapshot(at=now, cash=1, total_asset=1, positions={}).model_dump_json(),
              streams.heartbeat + ":agent": Heartbeat(source="agent", at=now).model_dump_json(),
              streams.heartbeat + ":market": Heartbeat(source="market", at=now).model_dump_json(),
              "control:kill": "source kill preserved"}
    for key, value in values.items():
        MemoryTransport.set_key(raw, key, value)
    target = ValidationTransport(raw, prefix)
    copy_inputs(raw, target, streams)
    for key, value in values.items():
        assert target.get_key(key) == value
        assert raw.get_key(key) == value


@pytest.mark.parametrize("kind", ["absent", "stale", "identifier"])
def test_unusable_source_snapshot_rejected_without_timestamp_rewrite(kind):
    import json
    raw = MemoryTransport()
    streams = StreamConfig()
    if kind != "absent":
        value = json.loads(AccountSnapshot(at=datetime.now() - timedelta(seconds=20 if kind == "stale" else 0),
                                         cash=1, positions={}).model_dump_json())
        if kind == "identifier":
            value["account_id"] = "must-not-cross"
        raw.set_key(streams.account, json.dumps(value))
    with pytest.raises(ValueError):
        copy_inputs(raw, ValidationTransport(raw, "component-validation:issue497-test"), streams)
    assert raw.get_key("component-validation:issue497-test:" + streams.account) is None
