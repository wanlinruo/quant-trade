"""Issue #230 所需的另类数据指标。"""

from __future__ import annotations

from qsys.data import schema
from qsys.data.catalog.types import (
    UNCOVERED,
    Direct,
    Kind,
    Metric,
    MetricStatus,
    Validity,
)

DATASETS = frozenset({"moneyflow_daily"})

_MEANINGS = {
    "symbol": "规范证券代码",
    "dt": "资金流所属交易日",
    "buy_sm_vol": "小单主动买入成交量（手）",
    "buy_sm_amount": "小单主动买入成交额（元）",
    "sell_sm_vol": "小单主动卖出成交量（手）",
    "sell_sm_amount": "小单主动卖出成交额（元）",
    "buy_md_vol": "中单主动买入成交量（手）",
    "buy_md_amount": "中单主动买入成交额（元）",
    "sell_md_vol": "中单主动卖出成交量（手）",
    "sell_md_amount": "中单主动卖出成交额（元）",
    "buy_lg_vol": "大单主动买入成交量（手）",
    "buy_lg_amount": "大单主动买入成交额（元）",
    "sell_lg_vol": "大单主动卖出成交量（手）",
    "sell_lg_amount": "大单主动卖出成交额（元）",
    "buy_elg_vol": "特大单主动买入成交量（手）",
    "buy_elg_amount": "特大单主动买入成交额（元）",
    "sell_elg_vol": "特大单主动卖出成交量（手）",
    "sell_elg_amount": "特大单主动卖出成交额（元）",
    "net_mf_vol": "基于 L2 主动买卖单计算的净流入量（手）",
    "net_mf_amount": "基于 L2 主动买卖单计算的净流入额（元）；不能由大小单金额简单相减",
}


def _metrics() -> list[Metric]:
    out: list[Metric] = []
    for column in schema.MONEYFLOW_DAILY.names:
        field = {"symbol": "ts_code", "dt": "trade_date"}.get(column, column)
        multiplier = 10_000.0 if column.endswith("_amount") else 1.0
        out.append(Metric(
            id=f"moneyflow_daily.{column}",
            kind=Kind.DIRECT,
            validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning=_MEANINGS[column],
            lineage=Direct(
                "tushare.moneyflow", field, multiplier,
                note="源成交额单位为万元；落库统一换算为元"
                if multiplier != 1.0 else None,
            ),
            checks=UNCOVERED("完整交易日替换、唯一键及存储契约验证"),
            ext_consumers=("首板研究",),
        ))
    return out


METRICS: tuple[Metric, ...] = tuple(_metrics())
