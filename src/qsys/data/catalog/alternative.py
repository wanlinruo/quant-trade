"""Issue #230 所需的另类数据指标。"""

from __future__ import annotations

from qsys.data import schema
from qsys.data.catalog.types import (
    UNCOVERED,
    Computed,
    Direct,
    Kind,
    Metric,
    MetricStatus,
    Validity,
)

DATASETS = frozenset({"moneyflow_daily", "block_trade_daily"})

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


_BLOCK_MEANINGS = {
    "symbol": "规范证券代码",
    "dt": "大宗交易所属交易日",
    "seq": "该交易日内按（代码、价、量、额、买方、卖方）确定性排序后的序号，仅作行身份",
    "price": "成交价（元/股）",
    "vol": "成交量（万股，源单位，不换算）",
    "amount": "成交额（元）",
    "buyer": "买方营业部",
    "seller": "卖方营业部",
}


def _block_trade_metrics() -> list[Metric]:
    out: list[Metric] = []
    for column in schema.BLOCK_TRADE_DAILY.names:
        if column == "seq":
            lineage = Computed(
                ("block_trade_daily.symbol", "block_trade_daily.dt"),
                "同一交易日内按 (symbol, price, vol, amount, buyer, seller) 排序后的 0 起序号",
            )
            kind = Kind.DERIVED
        else:
            field = {"symbol": "ts_code", "dt": "trade_date"}.get(column, column)
            multiplier = 10_000.0 if column == "amount" else 1.0
            lineage = Direct(
                "tushare.block_trade", field, multiplier,
                note="源成交额单位为万元；落库换算为元" if multiplier != 1.0 else None,
            )
            kind = Kind.DIRECT
        out.append(Metric(
            id=f"block_trade_daily.{column}",
            kind=kind,
            validity=Validity.SESSION,
            status=MetricStatus.ACTIVE,
            meaning=_BLOCK_MEANINGS[column],
            lineage=lineage,
            checks=UNCOVERED("整天替换、空响应不写及存储契约验证"),
            ext_consumers=("A1 量纲规则（#608）",),
        ))
    return out


METRICS: tuple[Metric, ...] = tuple(_metrics() + _block_trade_metrics())
