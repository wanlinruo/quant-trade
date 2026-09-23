"""影子/实盘核心从模拟账户承袭策略：回测 → 模拟账户 → 影子盘。"""

import json
import sqlite3

import pytest

from qsys.live.config import StrategyConfig
from qsys.live.lineage import StrategyLineageError, resolve_strategy

SOURCE = "from qsys.strategy.base import Strategy\n\nclass Demo(Strategy):\n    pass\n"


def _workbench(tmp_path, *, account="acct-1", version="v1", status="running",
               params=None, with_source=True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "workbench.sqlite3"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE paper_accounts (id TEXT, name TEXT, status TEXT,"
                    " backtest_id TEXT, strategy_id TEXT, strategy_name TEXT,"
                    " version_id TEXT, config_json TEXT)")
        con.execute("CREATE TABLE strategy_versions (id TEXT, source TEXT)")
        con.execute("INSERT INTO paper_accounts VALUES (?,?,?,?,?,?,?,?)", (
            account, "模拟-09:25入场", status, "bt-9", "strat-1", "structural_v1_2",
            version, json.dumps({"entry_fill": "continuous",
                                 "params": params if params is not None else
                                 {"auction_at": "09:25:10", "entry_price_buffer": 0.03}}),
        ))
        if with_source:
            con.execute("INSERT INTO strategy_versions VALUES (?,?)", (version, SOURCE))
    return db


def test_following_a_paper_account_takes_its_source_and_params(tmp_path):
    db = _workbench(tmp_path)
    cfg = StrategyConfig(follows_paper_account="acct-1", workbench=str(db))

    got = resolve_strategy(cfg, tmp_path / "workspace")

    assert got.path.read_text(encoding="utf-8") == SOURCE
    assert got.params == {"auction_at": "09:25:10", "entry_price_buffer": 0.03}
    assert (got.paper_account_id, got.backtest_id, got.version_id) == ("acct-1", "bt-9", "v1")
    assert got.paper_account_name == "模拟-09:25入场"
    assert got.entry_fill == "continuous"
    assert got.follows_paper


def test_without_a_paper_account_it_keeps_the_configured_file(tmp_path):
    cfg = StrategyConfig(file="strategies/structural_v1_2.py", params={"a": 1})

    got = resolve_strategy(cfg, tmp_path / "workspace")

    assert str(got.path) == "strategies/structural_v1_2.py"
    assert got.params == {"a": 1} and not got.follows_paper


def test_a_missing_account_or_source_refuses_to_start(tmp_path):
    db = _workbench(tmp_path)
    with pytest.raises(StrategyLineageError, match="不存在"):
        resolve_strategy(
            StrategyConfig(follows_paper_account="nope", workbench=str(db)),
            tmp_path / "w1",
        )
    db2 = _workbench(tmp_path / "b", account="acct-2", with_source=False)
    with pytest.raises(StrategyLineageError, match="缺源码"):
        resolve_strategy(
            StrategyConfig(follows_paper_account="acct-2", workbench=str(db2)),
            tmp_path / "w2",
        )
    with pytest.raises(StrategyLineageError, match="工作台数据库不存在"):
        resolve_strategy(
            StrategyConfig(follows_paper_account="acct-1", workbench=str(tmp_path / "no.db")),
            tmp_path / "w3",
        )
