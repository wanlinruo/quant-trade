"""运行时类指标 56 条（会话/采集 32 条，验证结果与证据 24 条）。

这些记录由系统自身写入，多数没有独立外部真值。UNCOVERED 不是免检章：
理由明确记录验证边界，已有到货规则的关键列仍挂 B4。
"""

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

DATASETS = frozenset({
    "live_equity",
    "live_fills",
    "live_rejects",
    "live_recon",
    "ingest_log",
    "metric_checks",
    "metric_evidence",
})

_SELF = "qsys.runtime"

_MEANING = {
    "live_equity.dt": "交易日；同日重复记录时保留最后一次",
    "live_equity.cash": "可用资金（元），写入前按分取整",
    "live_equity.market_value": "持仓市值（元），写入前按分取整",
    "live_equity.total_value": "总资产（元）= cash + market_value，写入前按分取整",
    "live_fills.dt": "成交归属的交易日",
    "live_fills.client_order_id": "本地订单号，也是重复成交回报的幂等键",
    "live_fills.symbol": "成交标的代码",
    "live_fills.side": "成交方向：buy / sell",
    "live_fills.qty": "成交数量（股）",
    "live_fills.price": "成交价（元）",
    "live_fills.fee": "费用合计（元）",
    "live_fills.filled_at": "成交时刻",
    "live_rejects.dt": "拒单归属的交易日",
    "live_rejects.client_order_id": "被拒订单的本地订单号",
    "live_rejects.symbol": "被拒订单的标的代码",
    "live_rejects.qty": "被拒数量（股）",
    "live_rejects.reason": "本地约束或券商回报给出的拒单原因原文",
    "live_rejects.at": "拒单时刻",
    "live_recon.dt": "对账归属的交易日",
    "live_recon.at": "对账执行时刻；同一层每次执行都保留",
    "live_recon.layer": "对账层：intraday / eod / sim_vs_live",
    "live_recon.is_consistent": "该层本次对账是否一致",
    "live_recon.cash_diff": "本地与远端资金差额的绝对值（元）",
    "live_recon.n_mismatches": "不一致的持仓或成交条目数",
    "live_recon.detail": "差异明细文本",
    "ingest_log.dataset": "本次采集或处理写入的数据集名",
    "ingest_log.dt": "本次记录覆盖的交易日或进度日期",
    "ingest_log.provider": "采集源名称；部分长任务会复用为逐标的进度键",
    "ingest_log.ingested_at": "记录写入时的本机时刻",
    "ingest_log.row_count": "本次写入行数",
    "ingest_log.ok": "本次采集是否成功。空结果不得写成功日志——写了会让"
    "上游故障被当成全市场已完成而永不回补",
    "ingest_log.message": "失败原因或补充说明",
    "metric_checks.run_id": "同一轮自动验证的唯一运行标识",
    "metric_checks.checked_at": "本条规则完成并写入结果的时刻，也是分区年份依据",
    "metric_checks.metric_id": "被验证的指标 id",
    "metric_checks.check_id": "执行的验证规则 id",
    "metric_checks.category": "规则类别：truthfulness / completeness / validity",
    "metric_checks.scope_start": "本次检查覆盖区间起点；不适用时为 null",
    "metric_checks.scope_end": "本次检查覆盖区间终点；不适用时为 null",
    "metric_checks.passed": "是否通过；验证器自身崩溃也必须写 false，不能静默缺行",
    "metric_checks.severity": "实际严重度：blocking / warning；已知问题可降级",
    "metric_checks.known_issue": "触发降级的 D-x 已知问题编号；没有则为 null",
    "metric_checks.violations": "违规行数",
    "metric_checks.checked_rows": "实际检查的总行数，必填；没有分母的违规数不构成信息",
    "metric_checks.detail": "结果摘要或 runner 错误信息",
    "metric_checks.sample_path": "大批量违规样本的外部 Parquet 路径",
    "metric_evidence.metric_id": "人工抽样所验证的指标 id",
    "metric_evidence.symbol": "样本标的代码",
    "metric_evidence.dt": "样本对应交易日，也是分区年份依据",
    "metric_evidence.our_value": "本系统值的原样文本，避免统一数值化丢精度",
    "metric_evidence.reference_value": "第三方参考值的原样文本",
    "metric_evidence.reference_source": "人工核对所用参考源",
    "metric_evidence.verdict": "人工结论：match / mismatch / inconclusive",
    "metric_evidence.checked_by": "核对人",
    "metric_evidence.checked_at": "人工核对完成时刻",
    "metric_evidence.note": "证据说明、口径或已知问题备注",
}

_CHECKS = {
    "ingest_log.dataset": ("B4.arrival",),
    "ingest_log.dt": ("B4.arrival",),
    "ingest_log.ok": ("B4.arrival",),
    "live_recon.is_consistent": ("B4.arrival",),
}
_DEFAULT = UNCOVERED(
    "系统自身写入的运行记录，没有独立外部真值可交叉比对；"
    "字段变换和幂等性由写入侧单元测试保证"
)


def _build() -> list[Metric]:
    out: list[Metric] = []
    for dataset in sorted(DATASETS):
        for column in schema.ALL[dataset].names:
            metric_id = f"{dataset}.{column}"
            if metric_id == "live_equity.total_value":
                out.append(
                    Metric(
                        id=metric_id,
                        kind=Kind.DERIVED,
                        validity=Validity.SESSION,
                        status=MetricStatus.OPERATIONAL,
                        meaning=_MEANING[metric_id],
                        lineage=Computed(
                            ("live_equity.cash", "live_equity.market_value"),
                            "to_cents(cash + market_value)",
                        ),
                        checks=("A2.recompute.live_total_value",),
                    )
                )
                continue
            out.append(
                Metric(
                    id=metric_id,
                    kind=Kind.DIRECT,
                    validity=Validity.SESSION,
                    status=MetricStatus.OPERATIONAL,
                    meaning=_MEANING[metric_id],
                    lineage=Direct(
                        _SELF,
                        column,
                        note="SessionJournal/IngestLog 接收或生成的运行时字段",
                    ),
                    checks=_CHECKS.get(metric_id, _DEFAULT),
                )
            )
    return out


METRICS: tuple[Metric, ...] = tuple(_build())
