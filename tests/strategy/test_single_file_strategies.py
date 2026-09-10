"""仓库里的策略文件都能被装载——示例失效了要立刻知道。"""

from pathlib import Path

import pytest

from qsys.strategy.base import Strategy
from qsys.strategy.loader import StrategyDeps, load_strategy
from qsys.strategy.schedule import Schedule

STRATEGIES = Path(__file__).resolve().parents[2] / "strategies"


def _files():
    return sorted(STRATEGIES.glob("*.py"))


def test_the_strategies_directory_is_not_empty():
    assert _files(), "strategies/ 下应当有可跑的单文件策略"


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.stem)
def test_every_shipped_strategy_loads(path):
    got = load_strategy(path, StrategyDeps(feed=None, market=None, query=None))
    assert isinstance(got, Strategy)


def test_the_structural_port_declares_its_own_schedule():
    """调度是策略的一部分——原文 L188-L204 的 run_daily 对应到这里。"""
    from datetime import time

    strategy = load_strategy(STRATEGIES / "structural_v1_2.py", StrategyDeps())
    schedule = Schedule()
    strategy.on_init(_InitCtx(schedule))

    assert schedule.auction_at == time(9, 30)
    assert schedule.checkpoints == (time(11, 25), time(14, 25), time(14, 50))


def test_the_example_leaves_the_default_rhythm_alone():
    """收盘决策、次日开盘成交的策略不需要声明任何调度。"""
    strategy = load_strategy(STRATEGIES / "ma_cross.py", StrategyDeps())
    schedule = Schedule()
    strategy.on_init(_InitCtx(schedule))
    assert schedule.declared is False


class _InitCtx:
    def __init__(self, schedule):
        self.schedule = schedule
        self.params = {}
        self.state = {}
        self.log = _Log()


class _Log:
    def info(self, *a, **k):
        pass
