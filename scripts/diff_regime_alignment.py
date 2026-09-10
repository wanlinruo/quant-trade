"""生态因子的股票级集合 diff：聚宽旧口径 vs 严格日期口径。

**为什么要它**：聚宽 `_s5_market_raw_factors` 取 `count=3` 的窗口，先按
`paused == 0` 过滤，再用 `groupby().cumcount(ascending=False)` 定 t1/t2，
**从不校验日期等于目标交易日**。停牌股当日行被滤掉后 `_rank_rev == 0`
就落到前一交易日——前一日的涨跌被挂到目标日上。这不是未来函数，但会
污染 `market_advance_ratio` / `prior_limitup_nextday_return_mean` 这类
阈值极窄的因子。

本脚本在**同一份 qsys 数据**上同时跑两套口径，把差异定位到具体个股：

  OLD_JQ_STYLE      窗口内最后两根有效 bar，日期不校验（复现聚宽行为）
  STRICT_DATE_STYLE t1 必须等于目标日、t2 必须等于其上一交易日

我们的仓库不存停牌日的行，这与聚宽「滤掉 paused 行」在窗口内等价，
因此 OLD_JQ_STYLE 能忠实复现对方的取数路径。

用法：
    uv run python scripts/diff_regime_alignment.py --date 2026-06-10 \\
        --out var/regime_diff_20260610
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path

import polars as pl

from qsys.data.pipeline.regime import _in_breadth_universe
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

WINDOW = 3          # 对齐聚宽 get_price(count=3)
LU_EPS = 0.9999     # close >= high_limit * 0.9999

COLUMNS = [
    "stock", "t1_time", "t2_time", "close_t1", "close_t2", "high_limit_t2",
    "paused_t1", "paused_t2", "was_limit_up_t2", "ret_t1",
    "is_prior_limitup_sample", "stale_t1", "stale_t2",
]


def _window(query: WarehouseQuery, target: date) -> list[date]:
    """目标日往前数 WINDOW 个交易日。交易日历取自仓库里出现过的 bar 日期。"""
    lo = date(target.year - 1, target.month, 1)
    days = sorted(query.daily_bars(None, lo, target)["dt"].unique().to_list())
    if len(days) < WINDOW:
        raise SystemExit(f"{target} 之前不足 {WINDOW} 个交易日")
    return days[-WINDOW:]


def _pairs(frame: pl.DataFrame, t1: date, t2: date, traded: dict[date, set[str]],
           *, strict: bool) -> pl.DataFrame:
    if strict:
        a = frame.filter(pl.col("dt") == t1).select(
            [pl.col("symbol").alias("stock"), pl.col("close").alias("close_t1")]
        ).with_columns(pl.lit(t1).alias("t1_time"))
        b = frame.filter(pl.col("dt") == t2).select(
            [pl.col("symbol").alias("stock"), pl.col("close").alias("close_t2"),
             pl.col("limit_up").alias("high_limit_t2")]
        ).with_columns(pl.lit(t2).alias("t2_time"))
    else:
        # 聚宽的 cumcount(ascending=False)：窗口内倒数第 1 / 第 2 根有效 bar
        ranked = frame.with_columns(
            (pl.len().over("symbol") - 1 - pl.int_range(pl.len()).over("symbol"))
            .alias("_rank_rev")
        )
        a = ranked.filter(pl.col("_rank_rev") == 0).select(
            [pl.col("symbol").alias("stock"), pl.col("dt").alias("t1_time"),
             pl.col("close").alias("close_t1")])
        b = ranked.filter(pl.col("_rank_rev") == 1).select(
            [pl.col("symbol").alias("stock"), pl.col("dt").alias("t2_time"),
             pl.col("close").alias("close_t2"),
             pl.col("limit_up").alias("high_limit_t2")])

    pair = a.join(b, on="stock", how="inner").filter(
        (pl.col("close_t2") > 0) & (pl.col("high_limit_t2") > 0)
    )
    return pair.with_columns(
        (pl.col("close_t1") / pl.col("close_t2") - 1.0).alias("ret_t1"),
        (pl.col("close_t2") >= pl.col("high_limit_t2") * LU_EPS)
        .alias("was_limit_up_t2"),
        (~pl.col("stock").is_in(list(traded[t1]))).alias("paused_t1"),
        (~pl.col("stock").is_in(list(traded[t2]))).alias("paused_t2"),
    ).with_columns(
        pl.col("was_limit_up_t2").alias("is_prior_limitup_sample"),
        (pl.col("t1_time") != t1).alias("stale_t1"),
        (pl.col("t2_time") != t2).alias("stale_t2"),
    ).select(COLUMNS).sort("stock")


def _summary(frame: pl.DataFrame) -> tuple[int, int, float, float]:
    sample = frame.filter(pl.col("is_prior_limitup_sample"))["ret_t1"]
    return (frame.height, sample.len(),
            float(sample.mean() or 0.0), float(sample.median() or 0.0))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True,
                        help="source_market_date（信号日的前一交易日），YYYY-MM-DD")
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--out", default=None, help="输出目录，缺省不落盘")
    args = parser.parse_args()

    t1 = datetime.strptime(args.date, "%Y-%m-%d").date()
    query = WarehouseQuery(Warehouse(Path(args.warehouse)))
    win = _window(query, t1)
    t2 = win[-2]

    bars = _in_breadth_universe(query.daily_bars(None, win[0], t1))
    flags = _in_breadth_universe(query.daily_flags(win[0], t1))
    frame = (bars.filter(pl.col("dt").is_in(win)).select(["symbol", "dt", "close"])
             .join(flags.filter(pl.col("dt").is_in(win))
                   .select(["symbol", "dt", "limit_up"]),
                   on=["symbol", "dt"], how="left")
             .sort(["symbol", "dt"]))
    traded = {d: set(frame.filter(pl.col("dt") == d)["symbol"]) for d in win}

    old = _pairs(frame, t1, t2, traded, strict=False)
    strict = _pairs(frame, t1, t2, traded, strict=True)

    print(f"source_market_date {t1}   窗口 {[str(d) for d in win]}   "
          f"应有 t1={t1}  t2={t2}\n")
    print(f"{'口径':<20}{'universe':>10}{'sample':>8}{'mean':>16}{'median':>16}")
    for name, f in (("OLD_JQ_STYLE", old), ("STRICT_DATE_STYLE", strict)):
        u, n, mean, med = _summary(f)
        print(f"{name:<20}{u:>10}{n:>8}{mean:>16.10f}{med:>16.10f}")

    j = set(old.filter(pl.col("is_prior_limitup_sample"))["stock"])
    q = set(strict.filter(pl.col("is_prior_limitup_sample"))["stock"])
    merged = old.join(
        strict.select(["stock", pl.col("ret_t1").alias("_ret_strict")]),
        on="stock", how="inner"
    ).with_columns((pl.col("ret_t1") - pl.col("_ret_strict")).abs().alias("_d"))
    print(f"\nJQ_ONLY {len(j - q)}   QSYS_ONLY {len(q - j)}   BOTH {len(j & q)}   "
          f"BOTH_RET_DIFF {merged.filter(pl.col('_d') > 1e-12).height}")

    stale = old.filter(pl.col("stale_t1") | pl.col("stale_t2"))
    in_sample = stale.filter(pl.col("is_prior_limitup_sample"))
    print(f"STALE_T1 {old.filter(pl.col('stale_t1')).height}   "
          f"STALE_T2 {old.filter(pl.col('stale_t2')).height}   "
          f"日期错位共 {stale.height} 只，其中样本内 {in_sample.height} 只")
    if stale.height:
        print(stale.select(["stock", "t1_time", "t2_time", "ret_t1",
                            "was_limit_up_t2"]).to_pandas().to_string(index=False))

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        old.write_csv(out / "old_jq_style.csv")
        strict.write_csv(out / "strict_date_style.csv")
        strict.filter(pl.col("is_prior_limitup_sample")).sort("ret_t1").write_csv(
            out / "prior_limitup_sample.csv")
        print(f"\n写出 {out}/{{old_jq_style,strict_date_style,"
              f"prior_limitup_sample}}.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
