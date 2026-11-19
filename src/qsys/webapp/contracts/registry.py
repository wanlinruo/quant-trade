"""对外契约清单：前端类型由这里列出的模型生成（scripts/gen_frontend_contracts.py）。

新增或修改接口的响应模型后，必须登记到 EXPORTED 并重新生成前端类型；
测试会校验路由的响应模型都已登记、生成物与仓库一致。
"""

from __future__ import annotations

from qsys.webapp.contracts.alerts import AckResult, AlertCenter, AlertDetail, AlertsDisabled
from qsys.webapp.contracts.ailu import ResearchBoard, ShadowBoard
from qsys.webapp.contracts.board import Board, BoardSeries, BoardUnavailable
from qsys.webapp.contracts.base import Contract, ErrorBody
from qsys.webapp.contracts.data import (
    DataDisabled, DataOverview, DataTableDetail, DataTableList, MetricDetail, MetricList, QualitySummary, RuleDetail,
    TableSample,
)
from qsys.webapp.contracts.market import MarketDisabled, MarketOverview
from qsys.webapp.contracts.operations import NodesView, OperationsDisabled, ReleasesView, ServicesView
from qsys.webapp.contracts.research import (
    BacktestList, BacktestPending, BacktestReport, Cancelled, Created, ResearchDisabled, ResearchJobList,
    Resubmitted, StrategyDetail, StrategyList,
)
from qsys.webapp.contracts.shell import ShellStatus
from qsys.webapp.contracts.today import TodayDisabled, TodayView
from qsys.webapp.contracts.trading import (
    LiveTradingOverview, PaperAccountDetail, PaperAccounts, PaperActionResult,
    TradingDisabled, TradingOrders, TradingReconciliation, TradingRuns,
)

EXPORTED: tuple[type[Contract], ...] = (
    ErrorBody,
    ShellStatus,
    MarketOverview,
    MarketDisabled,
    ShadowBoard,
    ResearchBoard,
    Board,
    BoardUnavailable,
    BoardSeries,
    ResearchDisabled,
    StrategyList,
    StrategyDetail,
    BacktestList,
    BacktestReport,
    BacktestPending,
    ResearchJobList,
    Created,
    Cancelled,
    Resubmitted,
    DataDisabled,
    DataOverview,
    DataTableList,
    DataTableDetail,
    TableSample,
    MetricList,
    MetricDetail,
    QualitySummary,
    RuleDetail,
    AlertsDisabled,
    AlertCenter,
    AlertDetail,
    AckResult,
    TradingDisabled,
    LiveTradingOverview,
    TradingOrders,
    TradingReconciliation,
    TradingRuns,
    PaperAccounts,
    PaperAccountDetail,
    PaperActionResult,
    OperationsDisabled,
    ServicesView,
    NodesView,
    ReleasesView,
    TodayDisabled,
    TodayView,
)
