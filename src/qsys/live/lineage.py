"""影子核心/实盘核心跑哪套策略：从模拟账户承袭。

一条链：**回测 → 模拟账户 → 影子盘**。模拟账户已经存档了那次回测的策略源码与
参数，影子盘直接读它，而不是在部署配置里再填一遍——重填就会漂移，而漂移之后
两边的收益没法比较，上线前的观察期也就失去意义。

只读工作台数据库；账户不存在就显式失败，不退回默认参数。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from collections.abc import Mapping
from pathlib import Path

from qsys.live.config import StrategyConfig


class StrategyLineageError(RuntimeError):
    """承袭的模拟账户取不到：宁可不启动，也不用别的参数跑。"""


@dataclass(frozen=True, slots=True)
class StrategySource:
    path: Path
    params: Mapping[str, object] = field(default_factory=dict)
    paper_account_id: str = ""
    paper_account_name: str = ""
    paper_status: str = ""
    backtest_id: str = ""
    version_id: str = ""
    strategy_id: str = ""
    strategy_name: str = ""
    entry_fill: str = ""

    @property
    def follows_paper(self) -> bool:
        return bool(self.paper_account_id)


def resolve_strategy(cfg: StrategyConfig, workspace: Path) -> StrategySource:
    """按部署配置确定策略源码与参数。

    配了 `follows_paper_account` 就从该模拟账户承袭：源码写进 `workspace`，
    参数取账户存档的那份。没配则用 `file` 与 `params`（老行为）。
    """
    if not cfg.follows_paper_account:
        return StrategySource(Path(cfg.file), dict(cfg.params))

    db = Path(cfg.workbench)
    if not db.exists():
        raise StrategyLineageError(f"工作台数据库不存在：{db}")
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        account = con.execute(
            "SELECT id, name, status, backtest_id, strategy_id, strategy_name,"
            " version_id, config_json FROM paper_accounts WHERE id = ?",
            (cfg.follows_paper_account,),
        ).fetchone()
        if account is None:
            raise StrategyLineageError(
                f"承袭的模拟账户不存在：{cfg.follows_paper_account}"
            )
        version = con.execute(
            "SELECT source FROM strategy_versions WHERE id = ?",
            (account["version_id"],),
        ).fetchone()
    if version is None:
        raise StrategyLineageError(
            f"模拟账户 {account['id']} 的策略版本 {account['version_id']} 缺源码"
        )

    config = json.loads(account["config_json"] or "{}")
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / "strategy.py"
    path.write_text(version["source"], encoding="utf-8")
    return StrategySource(
        path=path,
        params=dict(config.get("params", {})),
        paper_account_id=str(account["id"]),
        paper_account_name=str(account["name"]),
        paper_status=str(account["status"]),
        backtest_id=str(account["backtest_id"]),
        version_id=str(account["version_id"]),
        strategy_id=str(account["strategy_id"]),
        strategy_name=str(account["strategy_name"]),
        entry_fill=str(config.get("entry_fill", "")),
    )
