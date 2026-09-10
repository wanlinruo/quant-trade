"""从一个 `.py` 文件装载策略。

**为什么要它**：策略应该是一个可以拿走、可以给别人、可以放进版本库的
文件，而不是必须先改运行器才能跑的模块。聚宽那边就是一个文件——这条
体验值得保留。

一个策略文件要么提供 `Strategy` 的子类，要么提供 `create(deps)` 工厂：

    # 无依赖的策略：直接给类
    class MyStrategy(Strategy):
        ...

    # 需要数据接口的策略：给工厂
    def create(deps):
        return StructuralStrategy(StructuralSelection(
            deps.feed, deps.market, deps.query))

两者都有时以 `create` 为准——工厂能表达的更多。

装载**不放进 sys.modules**：同名文件跑两次不该互相污染，
策略文件也不该被别的代码 import 到。
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qsys.strategy.base import Strategy

FACTORY = "create"


@dataclass(frozen=True, slots=True)
class StrategyDeps:
    """交给 `create(deps)` 的数据接口。

    这些都是**构造期**依赖（选股器要拿它们建索引），与运行期的 `ctx`
    不同——`ctx` 的 PIT 由框架钉死，这里是原始接口，只在构造时用。
    """

    feed: Any = None
    market: Any = None
    query: Any = None
    calendar: Any = None
    params: dict[str, Any] | None = None


def load_module(path: Path):
    """把一个 .py 装成匿名模块。不进 sys.modules。"""
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"策略文件不存在: {path}")
    if path.suffix != ".py":
        raise ValueError(f"策略必须是 .py 文件: {path}")

    spec = importlib.util.spec_from_file_location(f"qsys_strategy_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法装载 {path}")
    module = importlib.util.module_from_spec(spec)
    # 让策略文件能 import 自己旁边的模块——单文件是常态，但不该强制
    parent = str(path.parent)
    added = parent not in sys.path
    if added:
        sys.path.insert(0, parent)
    try:
        spec.loader.exec_module(module)
    finally:
        if added:
            sys.path.remove(parent)
    return module


def strategy_classes(module) -> list[type[Strategy]]:
    """模块里定义的 Strategy 子类。import 进来的不算——否则
    `from qsys.strategy.base import Strategy` 也会被当成候选。"""
    return [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, Strategy)
        and obj is not Strategy
        and obj.__module__ == module.__name__
    ]


def load_strategy(
    path: Path, deps: StrategyDeps | None = None, *, name: str | None = None
) -> Strategy:
    """装载并实例化。`name` 指定类名，文件里有多个类时必须给。"""
    module = load_module(path)
    deps = deps or StrategyDeps()

    factory = getattr(module, FACTORY, None)
    if callable(factory) and name is None:
        built = factory(deps)
        if not isinstance(built, Strategy):
            raise TypeError(
                f"{path.name} 的 {FACTORY}() 要返回 Strategy，得到 {type(built).__name__}"
            )
        return built

    found = strategy_classes(module)
    if name is not None:
        by_name = {c.__name__: c for c in found}
        if name not in by_name:
            raise LookupError(
                f"{path.name} 里没有名为 {name} 的策略；有 {sorted(by_name) or '（无）'}"
            )
        return by_name[name]()

    if not found:
        raise LookupError(
            f"{path.name} 里既没有 Strategy 子类，也没有 {FACTORY}() 工厂"
        )
    if len(found) > 1:
        raise LookupError(
            f"{path.name} 里有多个策略 {sorted(c.__name__ for c in found)}，"
            "请用 --strategy-name 指定"
        )
    return found[0]()
