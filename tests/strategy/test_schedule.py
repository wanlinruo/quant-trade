"""策略在 on_init 里自己声明调度。对应聚宽的 run_daily。"""

from datetime import time

import pytest

from qsys.strategy.schedule import Schedule, parse_time


@pytest.mark.parametrize("given,expected", [
    ("09:30", time(9, 30)),
    ("09:30:15", time(9, 30, 15)),
    (time(14, 25), time(14, 25)),
])
def test_times_are_accepted_as_text_or_time(given, expected):
    assert parse_time(given) == expected


@pytest.mark.parametrize("bad", ["9点半", "09", "09:30:00:00", "aa:bb", "25:00"])
def test_a_malformed_time_is_rejected_loudly(bad):
    with pytest.raises(ValueError):
        parse_time(bad)


def test_a_fresh_schedule_declares_nothing():
    s = Schedule()
    assert s.declared is False
    assert s.auction_at is None
    assert s.checkpoints == ()


def test_declaring_the_auction_time():
    s = Schedule()
    s.auction("09:30")
    assert s.declared is True
    assert s.auction_at == time(9, 30)


def test_checkpoints_come_back_sorted():
    s = Schedule()
    s.checkpoint("14:50", "11:25", "14:25")
    assert s.checkpoints == (time(11, 25), time(14, 25), time(14, 50))


def test_declaring_the_same_checkpoint_twice_keeps_one():
    s = Schedule()
    s.checkpoint("11:25")
    s.checkpoint("11:25", time(14, 25))
    assert s.checkpoints == (time(11, 25), time(14, 25))


def test_only_checkpoints_still_counts_as_declared():
    s = Schedule()
    s.checkpoint("11:25")
    assert s.declared is True
    assert s.auction_at is None
