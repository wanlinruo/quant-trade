"""付费日线源：28 列无损语义转换、按年落库、固定复权锚点与质量报告。

手数保留小数；金额/市值换算为元，股本换算为股；百分数保留百分数。
不猜 ST/停牌/涨跌停/上市日期，不填任何缺失值，不覆盖旧回测派生表。
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from pathlib import Path

import fastexcel
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

# 顺序严格对应卖家 Excel 第三行；单位转换只做乘法。
COLUMNS = [
    ("交易日期", "dt", 1), ("股票代码", "symbol", 1), ("股票名称", "name", 1),
    ("开盘价", "open", 1), ("最高价", "high", 1), ("最低价", "low", 1),
    ("收盘价", "close", 1), ("昨收价", "pre_close", 1),
    ("涨跌额", "change", 1), ("涨跌幅（%）", "pct_change", 1),
    ("成交量（手）", "volume", 1), ("成交额（万元）", "amount", 10000),
    ("复权因子", "factor", 1), ("换手率（%）", "turnover_ratio", 1),
    ("换手率（自由流通股）（%）", "free_turnover_ratio", 1),
    ("量比", "volume_ratio", 1), ("市盈率", "pe", 1),
    ("市盈率（TTM）", "pe_ttm", 1), ("市净率", "pb", 1),
    ("市销率", "ps", 1), ("市销率（TTM）", "ps_ttm", 1),
    ("股息率（%）", "dividend_yield", 1), ("股息率（TTM）（%）", "dividend_yield_ttm", 1),
    ("总股本（万股）", "total_shares", 10000),
    ("流通股本（万股）", "circulating_shares", 10000),
    ("自由流通股本（万股）", "free_shares", 10000),
    ("总市值（万元）", "market_cap", 10000),
    ("流通市值（万元）", "circulating_market_cap", 10000),
]
EXCHANGES = {"00": "SZ", "30": "SZ", "60": "SH", "68": "SH", "83": "BJ", "92": "BJ"}
SCHEMA = pa.schema(
    [("dt", pa.date32()), ("symbol", pa.string()), ("name", pa.string())]
    + [(name, pa.float64()) for _, name, _ in COLUMNS[3:]],
    metadata={b"provider": b"yuhaha-baidupan", b"volume_unit": b"lot=100 shares",
              b"money_unit": b"CNY", b"share_capital_unit": b"share",
              b"percentage_unit": b"percent", b"factor": b"cumulative backward adjustment"},
)
VERSION = 1


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))
    os.replace(temp, path)


def atomic_parquet(frame: pl.DataFrame, path: Path, schema: pa.Schema | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".parquet.tmp")
    table = frame.to_arrow()
    if schema is not None:
        table = table.cast(schema)
    pq.write_table(table, temp, compression="zstd")
    os.replace(temp, path)


def read_day(path: Path) -> pl.DataFrame:
    raw = fastexcel.read_excel(path).load_sheet("data", header_row=2, dtypes="string").to_polars()
    expected = [c[0] for c in COLUMNS]
    if raw.columns != expected:
        raise ValueError(f"{path}: unexpected headers {raw.columns}")
    raw = raw.with_columns(pl.all().str.strip_chars().replace("", None))
    # 空白尾行可以跳过；任何有数据但没有主键的行都必须报错。
    raw = raw.filter(~pl.all_horizontal(pl.all().is_null()))
    if raw.height == 0:
        raise ValueError(f"{path}: empty daily file")
    code = pl.col("股票代码").str.pad_start(6, "0")
    frame = raw.select(
        pl.col("交易日期").str.strptime(pl.Date, "%Y%m%d", strict=True).alias("dt"),
        code.alias("symbol"), pl.col("股票名称").alias("name"),
        *[(pl.col(cn).cast(pl.Float64, strict=True) * mult).alias(en)
          for cn, en, mult in COLUMNS[3:]],
    )
    if frame["dt"].null_count() or frame["symbol"].null_count():
        raise ValueError(f"{path}: null primary key")
    if not frame["symbol"].str.contains(r"^\d{6}$").all():
        raise ValueError(f"{path}: invalid stock code")
    prefixes = set(frame["symbol"].str.slice(0, 2).to_list())
    if prefixes - EXCHANGES.keys():
        raise ValueError(f"{path}: unknown exchange prefixes {prefixes - EXCHANGES.keys()}")
    frame = frame.with_columns(
        (pl.col("symbol") + "." + pl.col("symbol").str.slice(0, 2).replace_strict(EXCHANGES)).alias("symbol")
    )
    expected_date = date.fromisoformat(f"{path.stem[:4]}-{path.stem[4:6]}-{path.stem[6:]}")
    if frame["dt"].unique().to_list() != [expected_date]:
        raise ValueError(f"{path}: row date differs from filename")
    if frame.select(pl.struct("symbol", "dt").is_duplicated().any()).item():
        raise ValueError(f"{path}: duplicate primary key")
    if frame.select(pl.any_horizontal(pl.col(pl.Float64).is_infinite() | pl.col(pl.Float64).is_nan()).any()).item():
        raise ValueError(f"{path}: non-finite number")
    return frame.sort("symbol")


def inventory_files(inventory: Path, remote_root: str) -> list[dict]:
    prefix = remote_root.rstrip("/") + "/"
    result = [e for e in json.loads(inventory.read_text())
              if not e["isdir"] and e["path"].startswith(prefix) and e["path"].endswith(".xlsx")]
    result.sort(key=lambda e: e["path"])
    if not result or len({e["path"] for e in result}) != len(result):
        raise ValueError("empty inventory or duplicate file path")
    for entry in result:
        rel = Path(entry["path"][len(prefix):])
        if len(rel.parts) != 2 or rel.parts[0] != rel.stem[:4] or not rel.stem.isdigit() or len(rel.stem) != 8:
            raise ValueError(f"unexpected inventory path: {rel}")
    return result


def import_year(incoming: Path, warehouse: Path, entries: list[dict], remote_root: str, year: int) -> dict:
    """每年完整清单到齐才发布；重跑按 SHA256 签名跳过，避免覆盖为半个年份。"""
    selected = [e for e in entries if Path(e["path"]).stem.startswith(str(year))]
    if not selected:
        raise ValueError(f"no inventory for {year}")
    paths = [incoming / e["path"][len(remote_root.rstrip('/')) + 1:] for e in selected]
    missing = [str(p) for p, e in zip(paths, selected) if not p.exists() or p.stat().st_size != e["size"]]
    if missing:
        return {"year": year, "ready": False, "missing": len(missing)}
    hashes = []
    for path in paths:
        hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
    signature = hashlib.sha256(json.dumps([VERSION, hashes]).encode()).hexdigest()
    target = warehouse / "paid_daily" / f"year={year}" / "part.parquet"
    receipt = target.with_name("manifest.json")
    if target.exists() and receipt.exists():
        old = json.loads(receipt.read_text())
        if old["signature"] == signature and old["parquet_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest():
            return old | {"skipped": True}
    frame = pl.concat([read_day(p) for p in paths]).sort(["symbol", "dt"])
    atomic_parquet(frame, target, SCHEMA)
    result = {"year": year, "ready": True, "files": len(paths), "rows": frame.height,
              "start": str(frame["dt"].min()), "end": str(frame["dt"].max()),
              "symbols": frame["symbol"].n_unique(), "signature": signature,
              "parquet_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
              "source_files": [{"file": str(p.relative_to(incoming)), "sha256": h}
                               for p, h in zip(paths, hashes)],
              "null_counts": frame.null_count().row(0, named=True)}
    atomic_json(receipt, result)
    return result


def audit(warehouse: Path, base_date: date, expected_files: int) -> dict:
    """逐年检查，跨年延续上一条真实记录，仅用于自洽性比较，不回填字段。"""
    parts = sorted((warehouse / "paid_daily").glob("year=*/part.parquet"))
    if not parts:
        raise ValueError("no paid_daily partitions")
    receipts = [json.loads(p.with_name("manifest.json").read_text()) for p in parts]
    if sum(r["files"] for r in receipts) != expected_files:
        raise ValueError("not all source files have been ingested")
    last = None
    bases = None
    anomalies = []
    counts = {"rows": 0, "dates": 0, "factor_comparisons": 0, "factor_events": 0,
              "invalid_ohlc": 0, "nonpositive_factor": 0, "negative_volume_or_amount": 0}
    for path in parts:
        frame = pl.read_parquet(path).sort(["symbol", "dt"])
        counts["rows"] += frame.height
        counts["dates"] += frame["dt"].n_unique()
        counts["invalid_ohlc"] += frame.filter(
            (pl.col("low") > pl.col("high")) | (pl.col("open") < pl.col("low")) |
            (pl.col("open") > pl.col("high")) | (pl.col("close") < pl.col("low")) |
            (pl.col("close") > pl.col("high"))).height
        counts["nonpositive_factor"] += frame.filter(pl.col("factor") <= 0).height
        counts["negative_volume_or_amount"] += frame.filter((pl.col("volume") < 0) | (pl.col("amount") < 0)).height
        small = frame.select("symbol", "dt", "close", "pre_close", "factor")
        joined = pl.concat([last, small]) if last is not None else small
        joined = joined.sort(["symbol", "dt"]).with_columns(
            pl.col("close").shift(1).over("symbol").alias("previous_close"),
            pl.col("factor").shift(1).over("symbol").alias("previous_factor"),
        ).filter(pl.col("dt") >= frame["dt"].min())
        valid = joined.filter((pl.col("previous_factor") > 0) & (pl.col("factor") > 0) &
                              (pl.col("pre_close") > 0) & (pl.col("previous_close") > 0)).with_columns(
            (pl.col("factor") / pl.col("previous_factor")).alias("factor_ratio"),
            (pl.col("previous_close") / pl.col("pre_close")).alias("price_ratio"),
        ).with_columns((pl.col("factor_ratio") / pl.col("price_ratio") - 1).abs().alias("relative_error"))
        counts["factor_comparisons"] += valid.height
        counts["factor_events"] += valid.filter((pl.col("factor_ratio") - 1).abs() > 1e-12).height
        anomalies.append(valid.filter(pl.col("relative_error") > 0.01))
        history = pl.concat([last, small]) if last is not None else small
        last = history.sort(["symbol", "dt"]).group_by("symbol", maintain_order=True).tail(1)
        eligible = small.filter(pl.col("dt") <= base_date).select("symbol", "dt", "factor")
        bases = pl.concat([bases, eligible]) if bases is not None else eligible
        bases = bases.sort(["symbol", "dt"]).group_by("symbol", maintain_order=True).tail(1)
    all_symbols = pl.scan_parquet(str(warehouse / "paid_daily/year=*/part.parquet")).select("symbol").unique().collect()
    bases = all_symbols.join(bases, on="symbol", how="left").rename({"dt": "factor_date", "factor": "base_factor"}).with_columns(
        pl.lit(base_date).alias("base_date")
    ).sort("symbol")
    anchor_path = warehouse / "paid_adjustment_bases" / f"base_date={base_date}" / "part.parquet"
    atomic_parquet(bases, anchor_path)
    bad = pl.concat(anomalies)
    report_dir = warehouse / "paid_daily_quality"
    atomic_parquet(bad, report_dir / "factor_anomalies.parquet")
    summary = counts | {"files": expected_files, "years": len(parts), "symbols": all_symbols.height,
        "start": receipts[0]["start"], "end": receipts[-1]["end"],
        "base_date": str(base_date), "base_policy": "last actual observation on or before base_date; factor_date recorded; no null filling",
        "missing_base_factor": bases["base_factor"].null_count(),
        "factor_anomalies_gt_1pct": bad.height,
        "anomaly_symbols": bad["symbol"].n_unique(),
        "anomalies_2005_2006": bad.filter(pl.col("dt").dt.year().is_between(2005, 2006)).height,
        "null_counts": {name: sum(r["null_counts"][name] for r in receipts) for name in SCHEMA.names}}
    atomic_json(report_dir / "summary.json", summary)
    return summary


def query_paid_daily(
    warehouse: Path, start: date, end: date, symbols: list[str] | None = None,
    *, base_date: date | None = None,
) -> pl.DataFrame:
    """查询真值；指定已固化锚点可附加前复权 OHLC，缺因子自然得到 null。

    base_date 可以晚于查询窗口，用于研究展示；PIT 策略须显式使用决策时点锚点。
    本入口不计算成交量复权、涨跌停或 ST 状态。
    """
    if start > end:
        raise ValueError("start is after end")
    paths = [warehouse / "paid_daily" / f"year={y}" / "part.parquet" for y in range(start.year, end.year + 1)]
    paths = [p for p in paths if p.exists()]
    if paths:
        query = pl.scan_parquet(paths).filter(pl.col("dt").is_between(start, end))
        if symbols is not None:
            query = query.filter(pl.col("symbol").is_in(symbols))
        frame = query.collect()
    else:
        frame = pl.from_arrow(SCHEMA.empty_table())
    if base_date is not None:
        anchor = warehouse / "paid_adjustment_bases" / f"base_date={base_date}" / "part.parquet"
        if not anchor.exists():
            raise ValueError(f"adjustment base has not been frozen: {base_date}")
        frame = frame.join(pl.read_parquet(anchor), on="symbol", how="left").with_columns(
            *[pl.when((pl.col("base_factor") > 0) & (pl.col("factor") > 0))
              .then(pl.col(c) * pl.col("factor") / pl.col("base_factor"))
              .otherwise(None).alias(f"qfq_{c}") for c in ["open", "high", "low", "close"]]
        )
    return frame.sort(["symbol", "dt"])
