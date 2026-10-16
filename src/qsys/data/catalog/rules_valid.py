"""C 类规则：值只在其声明的时点或区间内成立。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tokenize
from dataclasses import asdict, dataclass
from datetime import timedelta
from io import StringIO
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq

from qsys.data.catalog.checks import (
    Category,
    Check,
    CheckContext,
    CheckResult,
    Scope,
    register,
)
from qsys.data.catalog.sources import SOURCES

_REPO_ROOT = Path(
    os.environ.get("QSYS_CATALOG_SOURCE_ROOT", Path(__file__).resolve().parents[4])
)
_SCAN_ROOTS = [Path("src/qsys/data"), Path("src/qsys/strategy")]
_FROZEN_BASELINE = Path(
    os.environ.get("QSYS_CATALOG_FROZEN_BASELINE", "artifacts/frozen_baseline.json")
)
_FROZEN_DATASETS = ("paid_daily", "bars_daily")
_FILL_PATTERNS = re.compile(
    r"\.fill_null\(|\.fillna\(|\bor\s+0(?:\.0+)?\b|\.get\([^)]+,\s*0(?:\.0+)?\s*\)"
)


@dataclass(frozen=True, slots=True)
class FillSite:
    path: str
    line_no: int
    source: str


# 键钉源码文本而非易漂移的行号；理由必须解释默认值为何是业务真值。
FILL_EXEMPTIONS: dict[tuple[str, str], str] = {
    (
        "src/qsys/data/catalog/rules_truth.py",
        'unexplained = only_mine.filter(~pl.col("is_st").fill_null(False))',
    ): "校验器保守取差集：null 按未解释处理只会多报，不会制造通过结论",
    (
        "src/qsys/data/catalog/rules_recompute.py",
        '* pl.col("daily_factor").fill_null(1.0) - 1).round(12)',
    ): "独立校验器中无公司行动的当日比例定义为 1，不是缺失观测的替代值",
    (
        "src/qsys/data/pipeline/regime.py",
        'expr = raw * pl.col("_daily_factor").fill_null(1.0) - 1.0',
    ): "公司行动左连接未命中表示当日无行动，乘法单位元 1 是该事件模型的真值",
    (
        "src/qsys/data/pipeline/adjust.py",
        '+ pl.col("stock_per_share").fill_null(0.0)',
    ): "公司行动某分项为空表示本次事件没有送股，份额增量定义为 0",
    (
        "src/qsys/data/pipeline/adjust.py",
        '+ pl.col("rights_per_share").fill_null(0.0)',
    ): "公司行动某分项为空表示本次事件没有配股，份额增量定义为 0",
    (
        "src/qsys/data/pipeline/adjust.py",
        '.fill_null(pl.col("_prior_close"))',
    ): "跨年分块首行明确从上一分区取前收，不是常量填充",
    (
        "src/qsys/data/pipeline/adjust.py",
        '.fill_null(pl.col("_prior_direct"))',
    ): "跨年分块首行明确从上一分区取前一因子，不是常量填充",
    (
        "src/qsys/data/pipeline/adjust.py",
        '.fill_null(1.0)',
    ): "因子比值在序列首行没有前值，首个变化率按乘法单位元 1 定义",
    (
        "src/qsys/data/providers/akshare_provider.py",
        'return pl.col(column).cast(pl.Float64).fill_nan(0.0).fill_null(0.0)',
    ): "该上游的公司行动空分项语义是本次事件该项为零，函数只用于事件分项",
    (
        "src/qsys/data/pipeline/flags.py",
        '(cond.fill_null(False).cast(pl.Int32) for _, cond in hits),',
    ): "档位解码中 null 表示该候选档位未命中；按 False 计数是布尔判定本义",
    (
        "src/qsys/data/pipeline/flags.py",
        'decoded = pl.when(cond.fill_null(False)).then(pl.lit(tier)).otherwise(decoded)',
    ): "档位解码中 null 表示该候选档位未命中，不会据此制造档位值",
    (
        "src/qsys/data/catalog/rules_recompute.py",
        'matched = sum((c.fill_null(False).cast(pl.Int32) for _, c in hits),',
    ): "独立档位校验中 null 表示候选档位未命中；按 False 只会拒绝解码",
    (
        "src/qsys/data/catalog/rules_recompute.py",
        'decoded = pl.when(cond.fill_null(False)).then(pl.lit(tier)).otherwise(decoded)',
    ): "独立档位校验中 null 表示候选档位未命中，不会据此制造档位值",
    (
        "src/qsys/data/pipeline/tushare_reference.py",
        'pl.col("_matched").fill_null(False).alias("is_st")',
    ): "stock_st 只返回正例；与完整日线左连未命中按接口定义就是非 ST",
    (
        "src/qsys/data/pipeline/canonical_state.py",
        'is_delisting = pl.col("_is_delisting").fill_null(False)',
    ): (
        "delisting_pool 是已发布的稀疏正例事实；预检先确认数据集存在，"
        "左连未命中按事件模型定义就是不处于退市整理期"
    ),
    (
        "src/qsys/data/cutover_evidence.py",
        "null_exprs.append(pl.col(column).fill_null(replacement))",
    ): (
        "策略证据摘要已单独编码 null 位；此处只把 null 的未定义底层缓冲区"
        "规范为确定字节，不改变参与对账的数据语义"
    ),
    (
        "src/qsys/data/pipeline/canonical_minute.py",
        'sources[row["source"]] = sources.get(row["source"], 0) + row["len"]',
    ): "构建报告的来源行数计数器；未出现过的来源累计行数按定义从 0 开始",
    (
        "src/qsys/strategy/structural/universe.py",
        'broken = (broken.filter(~pl.col("is_st").fill_null(True))',
    ): "炸板池对未知 ST 状态保守排除（D-11 ST 炸板不进池）；只会少选不会误放行",
    (
        "src/qsys/data/pipeline/paid_minute.py",
        'reason_counts[row["reason"]] = reason_counts.get(row["reason"], 0) + row["len"]',
    ): "计数器未见过某键时其累计次数按定义为 0，不是数据观测值",
    (
        "src/qsys/data/coverage.py",
        'DatasetCoverage(dataset, int(rows or 0), int(symbols or 0), first, last)',
    ): "SQL 聚合空表返回 null 时，覆盖报告的行数和标的数按定义均为 0",
    (
        "src/qsys/data/quality/source_diff.py",
        'value_diffs.filter(pl.col("field") == "amount")["abs_diff"].max() or 0.0',
    ): "差异集合为空时最大差异按定义为 0，仅用于质量报告摘要",
    (
        "src/qsys/strategy/context.py",
        'market += held.total_qty * prices.get(symbol, 0.0)',
    ): "只读账户视图中无可见价格的持仓不计入当次可见市值，现金仍单独保留",
}


def _masked_lines(source: str) -> list[str]:
    """遮掉注释和字符串，避免说明文字里的 ``or 0`` 被当成代码。"""
    lines = source.splitlines()
    masked = [list(line) for line in lines]
    try:
        tokens = tokenize.generate_tokens(StringIO(source).readline)
        for token in tokens:
            if token.type not in (tokenize.COMMENT, tokenize.STRING):
                continue
            (start_row, start_col), (end_row, end_col) = token.start, token.end
            for row in range(start_row, end_row + 1):
                lo = start_col if row == start_row else 0
                hi = end_col if row == end_row else len(masked[row - 1])
                masked[row - 1][lo:hi] = " " * (hi - lo)
    except (IndentationError, tokenize.TokenError):
        # 待编辑的半成品文件也要扫描；解析失败时退回逐行正则。
        return lines
    return ["".join(line) for line in masked]


def scan_fills(
    roots: list[Path] | None = None,
    exemptions: dict[tuple[str, str], str] | None = None,
) -> list[FillSite]:
    """返回源码树中所有未登记的默认值填充点。"""
    selected = roots if roots is not None else _SCAN_ROOTS
    exempt = FILL_EXEMPTIONS if exemptions is None else exemptions
    out: list[FillSite] = []
    for root in selected:
        scan_root = root if root.is_absolute() else _REPO_ROOT / root
        for path in sorted(scan_root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            original = source.splitlines()
            display = str(path if root.is_absolute() else path.relative_to(_REPO_ROOT))
            for line_no, masked in enumerate(_masked_lines(source), start=1):
                if not _FILL_PATTERNS.search(masked):
                    continue
                stripped = original[line_no - 1].strip()
                if (display, stripped) not in exempt:
                    out.append(FillSite(display, line_no, stripped))
    return out


def _no_fill(ctx: CheckContext) -> CheckResult:
    sites = scan_fills()
    total = sum(
        1
        for root in _SCAN_ROOTS
        for path in (_REPO_ROOT / root).rglob("*.py")
        if "__pycache__" not in path.parts
    )
    return CheckResult(
        len(sites),
        total,
        f"扫描 {total} 个文件，发现 {len(sites)} 处未登记的填充",
        pl.DataFrame([asdict(site) for site in sites]) if sites else None,
    )


register(Check(
    "C1.no_fill", Category.VALIDITY, Scope.INCREMENTAL,
    ("strategy.stock_return_10d", "strategy.distance_to_60d_high",
     "adj_factors.cum_factor", "market_regime.index_ma20_slope_5d",
     "daily_flags.prev_close"),
    (), "数据与策略链路不得有未登记的默认值填充", _no_fill,
))


def _pit_join(ctx: CheckContext) -> CheckResult:
    flags = ctx.query.daily_flags(ctx.start, ctx.end)
    if not flags.height:
        return CheckResult(0, 0, "区间内无 daily_flags")
    claimed = flags.filter(
        pl.col("st_source").is_in(["name_snapshot", "name_snapshots"])
    ).select("symbol", "dt")
    if not claimed.height:
        # 分母取全部 flags；没有使用点快照也是一次有效检查，而不是未执行。
        return CheckResult(0, flags.height, "区间内没有声称来自名称快照的行")
    snaps = ctx.query.name_snapshots(None, ctx.start, ctx.end).select("symbol", "dt")
    bad = claimed.join(snaps, on=["symbol", "dt"], how="anti")
    return CheckResult(
        bad.height, claimed.height,
        f"{bad.height} / {claimed.height} 行声称来自快照但当日无快照",
        bad if bad.height else None,
    )


register(Check(
    "C2.pit_join", Category.VALIDITY, Scope.INCREMENTAL,
    ("name_snapshots.name", "name_snapshots.dt", "daily_flags.st_source"),
    (), "点观测必须按 (symbol, dt) 精确连接", _pit_join,
))


def _span_gaps(ctx: CheckContext) -> CheckResult:
    hist = ctx.query.name_history(None)
    if not hist.height:
        return CheckResult(0, 0, "name_history 为空")
    ordered = hist.sort("symbol", "start_date").with_columns(
        pl.col("start_date").shift(-1).over("symbol").alias("next_start")
    )
    gaps = ordered.filter(
        pl.col("end_date").is_not_null()
        & pl.col("next_start").is_not_null()
        & (pl.col("next_start") > pl.col("end_date").dt.offset_by("1d"))
    ).select("symbol", "end_date", "next_start").with_columns(
        pl.lit("内部空洞").alias("kind")
    )
    dangling = ordered.filter(
        pl.col("next_start").is_null()
        & pl.col("end_date").is_not_null()
        & (pl.col("end_date") < ctx.end)
    ).select("symbol", "end_date", "next_start").with_columns(
        pl.lit("末段无后继").alias("kind")
    )
    bad = pl.concat([gaps, dangling])
    return CheckResult(
        bad.height, ordered.height,
        f"{bad.height} / {ordered.height} 个名称区间存在内部空洞或末段无后继",
        bad if bad.height else None,
    )


register(Check(
    "C3.span_gaps", Category.VALIDITY, Scope.FULL,
    ("name_history.name", "name_history.start_date", "name_history.end_date"),
    ("D-10",), "区间表不得提前终止且没有后继区间", _span_gaps,
))


def _dataset_fingerprint(root: Path, dataset: str) -> dict[str, object] | None:
    paths = sorted((root / dataset).glob("year=*/part.parquet"))
    if not paths:
        return None
    digest = hashlib.sha256()
    rows = 0
    for path in paths:
        relative = path.relative_to(root / dataset).as_posix()
        digest.update(relative.encode())
        rows += pq.ParquetFile(path).metadata.num_rows
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return {"files": len(paths), "rows": rows, "sha256": digest.hexdigest()}


def _frozen_unchanged(ctx: CheckContext) -> CheckResult:
    current = {
        name: fingerprint
        for name in _FROZEN_DATASETS
        if (fingerprint := _dataset_fingerprint(ctx.warehouse.root, name)) is not None
    }
    baseline_path = Path(_FROZEN_BASELINE)
    if not baseline_path.is_absolute():
        baseline_path = _REPO_ROOT / baseline_path
    if not baseline_path.exists():
        if not current:
            return CheckResult(0, 0, "冻结表尚无可建立基线的分区")
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return CheckResult(0, len(current), f"已初始化 {len(current)} 张冻结表的内容基线")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    names = sorted(set(baseline) | set(current))
    changed = [
        {"dataset": name, "baseline": json.dumps(baseline.get(name), sort_keys=True),
         "current": json.dumps(current.get(name), sort_keys=True)}
        for name in names if baseline.get(name) != current.get(name)
    ]
    return CheckResult(
        len(changed), len(names), f"{len(changed)} / {len(names)} 张冻结表的内容指纹变化",
        pl.DataFrame(changed) if changed else None,
    )


register(Check(
    "C4.frozen_unchanged", Category.VALIDITY, Scope.FULL,
    ("paid_daily.dt", "bars_daily.dt"), (), "冻结表的行数与内容指纹不得改变",
    _frozen_unchanged,
))


_EXPIRY_WARN = timedelta(days=60)


def _credential_expiry(ctx: CheckContext) -> CheckResult:
    dated = [source for source in SOURCES.values() if source.expires is not None]
    soon = [source for source in dated if source.expires - ctx.end <= _EXPIRY_WARN]
    return CheckResult(
        len(soon), len(dated),
        f"{len(soon)} / {len(dated)} 个有期限的源将在 {_EXPIRY_WARN.days} 天内到期",
        pl.DataFrame({"source": [s.id for s in soon],
                      "expires": [s.expires for s in soon]}) if soon else None,
    )


register(Check(
    "C5.credential_expiry", Category.VALIDITY, Scope.INCREMENTAL,
    ("tushare_daily.close", "tushare_daily.factor"), (), "源凭据到期提醒",
    _credential_expiry,
))
