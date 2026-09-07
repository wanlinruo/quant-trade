import pytest

from qsys.live.transport import MemoryTransport, StreamMessage

STREAM = "cmd:orders"
GROUP = "core"


@pytest.fixture
def t() -> MemoryTransport:
    tr = MemoryTransport()
    tr.ensure_group(STREAM, GROUP)
    return tr


def test_append_returns_an_id(t):
    mid = t.append(STREAM, {"type": "X", "data": "1"})
    assert isinstance(mid, str) and mid


def test_ids_are_monotonic(t):
    ids = [t.append(STREAM, {"n": str(i)}) for i in range(5)]
    assert ids == sorted(ids)


def test_read_group_returns_appended_messages(t):
    t.append(STREAM, {"type": "X", "data": "1"})
    msgs = t.read_group(STREAM, GROUP, "c1")
    assert len(msgs) == 1
    assert msgs[0].payload["data"] == "1"


def test_read_group_does_not_redeliver_unacked_to_same_consumer(t):
    t.append(STREAM, {"n": "1"})
    assert len(t.read_group(STREAM, GROUP, "c1")) == 1
    assert t.read_group(STREAM, GROUP, "c1") == []


def test_unacked_messages_stay_pending(t):
    t.append(STREAM, {"n": "1"})
    t.read_group(STREAM, GROUP, "c1")
    assert len(t.pending(STREAM, GROUP)) == 1


def test_ack_clears_pending(t):
    t.append(STREAM, {"n": "1"})
    msg = t.read_group(STREAM, GROUP, "c1")[0]
    t.ack(STREAM, GROUP, msg.id)
    assert t.pending(STREAM, GROUP) == []


def test_messages_survive_a_consumer_restart(t):
    """Stream 的核心价值：断线期间的消息不丢，重启后仍可消费。"""
    t.append(STREAM, {"n": "1"})
    t.append(STREAM, {"n": "2"})
    first = t.read_group(STREAM, GROUP, "c1")
    assert len(first) == 2
    recovered = t.pending(STREAM, GROUP)
    assert [m.payload["n"] for m in recovered] == ["1", "2"]


def test_count_limits_the_batch(t):
    for i in range(5):
        t.append(STREAM, {"n": str(i)})
    assert len(t.read_group(STREAM, GROUP, "c1", count=2)) == 2


def test_read_group_on_unknown_group_raises(t):
    t.append(STREAM, {"n": "1"})
    with pytest.raises(KeyError, match="no such group"):
        t.read_group(STREAM, "nope", "c1")


def test_ensure_group_is_idempotent(t):
    t.ensure_group(STREAM, GROUP)
    t.ensure_group(STREAM, GROUP)
    t.append(STREAM, {"n": "1"})
    assert len(t.read_group(STREAM, GROUP, "c1")) == 1


def test_group_created_later_does_not_see_earlier_messages(t):
    """与 Redis XGROUP CREATE ... $ 一致：新组只看新消息。"""
    t.append(STREAM, {"n": "old"})
    t.ensure_group(STREAM, "late")
    t.append(STREAM, {"n": "new"})
    msgs = t.read_group(STREAM, "late", "c1")
    assert [m.payload["n"] for m in msgs] == ["new"]


def test_key_set_get_delete(t):
    assert t.get_key("k") is None
    t.set_key("k", "v")
    assert t.get_key("k") == "v"
    t.delete_key("k")
    assert t.get_key("k") is None


def test_delete_missing_key_is_not_an_error(t):
    t.delete_key("nope")


def test_all_messages_bypasses_group_semantics(t):
    """测试自省：不受消费组 $ 语义影响，能看到全部消息。"""
    t.append(STREAM, {"n": "1"})
    t.append(STREAM, {"n": "2"})
    assert [m.payload["n"] for m in t.all_messages(STREAM)] == ["1", "2"]


def test_all_messages_on_unknown_stream_is_empty(t):
    assert t.all_messages("nope") == []


def test_stream_message_is_frozen():
    msg = StreamMessage("1-0", {"a": "b"})
    with pytest.raises(Exception):
        msg.id = "2-0"
