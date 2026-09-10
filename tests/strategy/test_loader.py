"""从 .py 文件装载策略。策略该是一个能拿走的文件，不是必须改运行器的模块。"""

import sys

import pytest

from qsys.strategy.loader import (
    StrategyDeps,
    load_module,
    load_strategy,
    strategy_classes,
)

ONE = '''
from qsys.strategy.base import Strategy

class Solo(Strategy):
    def on_init(self, ctx):
        ctx.schedule.auction("09:30")
'''

TWO = '''
from qsys.strategy.base import Strategy

class Alpha(Strategy):
    pass

class Beta(Strategy):
    pass
'''

FACTORY = '''
from qsys.strategy.base import Strategy

class Built(Strategy):
    def __init__(self, feed):
        self.feed = feed

def create(deps):
    return Built(deps.feed)
'''

NONE = "x = 1\n"

BAD_FACTORY = '''
def create(deps):
    return "not a strategy"
'''


def write(tmp_path, body, name="strat.py"):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_a_single_class_is_found_and_instantiated(tmp_path):
    got = load_strategy(write(tmp_path, ONE))
    assert type(got).__name__ == "Solo"


def test_a_factory_receives_the_dependencies(tmp_path):
    got = load_strategy(write(tmp_path, FACTORY), StrategyDeps(feed="FEED"))
    assert got.feed == "FEED"


def test_the_factory_wins_over_a_bare_class(tmp_path):
    body = ONE + "\ndef create(deps):\n    return Solo()\n"
    assert type(load_strategy(write(tmp_path, body))).__name__ == "Solo"


def test_two_classes_need_an_explicit_name(tmp_path):
    path = write(tmp_path, TWO)
    with pytest.raises(LookupError, match="strategy-name"):
        load_strategy(path)
    assert type(load_strategy(path, name="Beta")).__name__ == "Beta"


def test_an_unknown_name_lists_what_is_there(tmp_path):
    with pytest.raises(LookupError, match="Alpha"):
        load_strategy(write(tmp_path, TWO), name="Gamma")


def test_a_file_without_a_strategy_says_so(tmp_path):
    with pytest.raises(LookupError, match="create"):
        load_strategy(write(tmp_path, NONE))


def test_a_factory_returning_the_wrong_thing_is_rejected(tmp_path):
    with pytest.raises(TypeError, match="Strategy"):
        load_strategy(write(tmp_path, BAD_FACTORY))


def test_the_imported_base_class_is_not_mistaken_for_a_strategy(tmp_path):
    module = load_module(write(tmp_path, ONE))
    assert [c.__name__ for c in strategy_classes(module)] == ["Solo"]


def test_a_missing_file_is_reported_clearly(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_strategy(tmp_path / "nope.py")


def test_a_non_python_file_is_refused(tmp_path):
    path = tmp_path / "strat.txt"
    path.write_text(ONE, encoding="utf-8")
    with pytest.raises(ValueError, match=r"\.py"):
        load_strategy(path)


def test_loading_does_not_pollute_sys_modules(tmp_path):
    before = set(sys.modules)
    load_strategy(write(tmp_path, ONE))
    assert set(sys.modules) - before == set()


def test_two_files_with_the_same_name_do_not_collide(tmp_path):
    """同名文件跑两次不该互相污染——这正是不进 sys.modules 的原因。"""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = write(tmp_path / "a", ONE)
    b = write(tmp_path / "b", TWO)
    assert type(load_strategy(a)).__name__ == "Solo"
    assert type(load_strategy(b, name="Alpha")).__name__ == "Alpha"


def test_a_strategy_can_import_a_neighbour_file(tmp_path):
    (tmp_path / "helper.py").write_text("VALUE = 7\n", encoding="utf-8")
    body = (
        "from qsys.strategy.base import Strategy\n"
        "from helper import VALUE\n"
        "class WithHelper(Strategy):\n"
        "    value = VALUE\n"
    )
    assert load_strategy(write(tmp_path, body)).value == 7
