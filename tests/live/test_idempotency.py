import pytest

from qsys.live.idempotency import IdempotencyGuard
from qsys.live.transport import MemoryTransport


@pytest.fixture
def guard() -> IdempotencyGuard:
    return IdempotencyGuard(MemoryTransport())


def test_unseen_id_is_not_seen(guard):
    assert guard.seen("c-1") is False


def test_remembered_id_is_seen(guard):
    guard.remember("c-1", "B123")
    assert guard.seen("c-1") is True


def test_recall_returns_the_stored_result(guard):
    guard.remember("c-1", "B123")
    assert guard.recall("c-1") == "B123"


def test_recall_of_unknown_id_is_none(guard):
    assert guard.recall("c-1") is None


def test_process_once_runs_the_handler_the_first_time(guard):
    calls = []

    def handler() -> str:
        calls.append(1)
        return "B123"

    result, replayed = guard.process_once("c-1", handler)
    assert result == "B123"
    assert replayed is False
    assert calls == [1]


def test_process_once_replays_instead_of_rerunning(guard):
    """网络重传必须回放旧结果，绝不重复下单。"""
    calls = []

    def handler() -> str:
        calls.append(1)
        return f"B{len(calls)}"

    guard.process_once("c-1", handler)
    result, replayed = guard.process_once("c-1", handler)

    assert result == "B1"
    assert replayed is True
    assert calls == [1], "handler 被重复执行了——会造成重复下单"


def test_different_ids_run_independently(guard):
    guard.process_once("c-1", lambda: "A")
    result, replayed = guard.process_once("c-2", lambda: "B")
    assert result == "B"
    assert replayed is False


def test_handler_failure_is_not_remembered(guard):
    """失败的指令必须允许重试，不能被当成已处理。"""

    def failing() -> str:
        raise RuntimeError("broker down")

    with pytest.raises(RuntimeError):
        guard.process_once("c-1", failing)
    assert guard.seen("c-1") is False


def test_prefix_isolates_namespaces():
    transport = MemoryTransport()
    a = IdempotencyGuard(transport, prefix="a:")
    b = IdempotencyGuard(transport, prefix="b:")
    a.remember("c-1", "A")
    assert b.seen("c-1") is False


def test_state_survives_a_new_guard_on_the_same_transport():
    """代理重启后仍然记得已处理的指令。"""
    transport = MemoryTransport()
    IdempotencyGuard(transport).remember("c-1", "B123")
    assert IdempotencyGuard(transport).recall("c-1") == "B123"
