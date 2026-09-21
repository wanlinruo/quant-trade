#!/usr/bin/env python3
"""用聚宽原生 240 根分钟线覆盖 QMT 退市证券零价格记录。

覆盖数据写入独立的 ``joinquant_minute_bars``，绝不改写 QMT。聚宽 09:31
包含集合竞价与首分钟成交，不能伪拆成 09:30/09:31 两根；因此保留源时间网格。
入库前必须通过 240 根原生网格、数值有效与 Tushare 日线对账（量/额/价相对偏差
≤ 5%）。``--start/--end`` 限定处理的交易日。默认预演，传 ``--apply`` 后才原子
发布，全程持有仓库写锁。已有目标文件在覆盖前必须备份。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tomllib
import uuid
from contextlib import nullcontext
from datetime import date, datetime
from pathlib import Path

import polars as pl

from qsys.agent.locking import single_instance
from qsys.data.pipeline.minute_repairs import (
    compare_with_daily,
    invalid_qmt_price_symbols,
    valid_joinquant_overrides,
)
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


def _jq_code(symbol: str) -> str | None:
    code, _, market = symbol.partition(".")
    suffix = {"SH": "XSHG", "SZ": "XSHE"}.get(market)
    return f"{code}.{suffix}" if suffix else None


def _symbol(code: str) -> str:
    bare, _, suffix = code.partition(".")
    return f"{bare}.{'SH' if suffix == 'XSHG' else 'SZ'}"


def _fetch(client, symbols: list[str], day) -> pl.DataFrame:
    codes = [_jq_code(symbol) for symbol in symbols]
    raw = client.get_price(
        codes,
        start_date=f"{day:%Y-%m-%d} 09:00:00",
        end_date=f"{day:%Y-%m-%d} 15:01:00",
        frequency="1m",
        fields=["open", "high", "low", "close", "volume", "money"],
        skip_paused=False,
        fq=None,
        panel=False,
    )
    if raw is None or len(raw) == 0:
        return pl.DataFrame(schema={
            "symbol": pl.Utf8, "dt": pl.Datetime("us"), "open": pl.Float64,
            "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
            "volume": pl.Int64, "amount": pl.Float64,
        })
    frame = pl.from_pandas(raw.reset_index(drop=True))
    if "code" not in frame.columns:  # 单标的返回以时间为索引、没有 code 列
        frame = frame.with_columns(pl.lit(codes[0]).alias("code"))
        if "time" not in frame.columns:
            frame = frame.with_columns(pl.Series("time", raw.index))
    return frame.select(
        pl.col("code").map_elements(_symbol, return_dtype=pl.Utf8).alias("symbol"),
        pl.col("time").cast(pl.Datetime("us")).alias("dt"),
        *(pl.col(name).cast(pl.Float64) for name in ["open", "high", "low", "close"]),
        pl.col("volume").round(0).cast(pl.Int64),
        pl.col("money").cast(pl.Float64).alias("amount"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--daily-warehouse", type=Path,
                        help="提供 tushare_daily 对账的仓库，默认同 --warehouse")
    parser.add_argument("--start", type=date.fromisoformat, help="只处理该日及之后的交易日")
    parser.add_argument("--end", type=date.fromisoformat, help="只处理该日及之前的交易日")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--lock", type=Path, default=Path("var/warehouse-write.lock"),
                        help="与生产定时写任务共用的仓库写锁，仅 --apply 时持有")
    args = parser.parse_args()
    with single_instance(args.lock) if args.apply else nullcontext():
        return _run(args)


def _run(args: argparse.Namespace) -> int:
    import jqdatasdk as jq

    credentials = tomllib.loads(args.config.read_text())["joinquant"]
    jq.auth(credentials["username"], credentials["password"])
    quota_before = int(jq.get_query_count(field="spare"))
    daily_query = WarehouseQuery(Warehouse(args.daily_warehouse or args.warehouse))
    target_root = args.warehouse / "joinquant_minute_bars"
    summary = {
        "operation": "publish_joinquant_minute_overrides",
        "applied": args.apply,
        "quota_before": quota_before,
        "requested_symbols": 0,
        "accepted_symbols": 0,
        "accepted_rows": 0,
        "rejected": {},
        "rejected_samples": {},
        "written_days": [],
    }

    def reject(reason: str, day, symbol: str) -> None:
        summary["rejected"][reason] = summary["rejected"].get(reason, 0) + 1
        samples = summary["rejected_samples"].setdefault(reason, [])
        if len(samples) < 10:
            samples.append(f"{day}:{symbol}")

    for qmt_path in sorted((args.warehouse / "minute_bars").glob("year=*/date=*/part.parquet")):
        stamp = qmt_path.parent.name.split("=", 1)[1]
        day = datetime.strptime(stamp, "%Y%m%d").date()
        if (args.start and day < args.start) or (args.end and day > args.end):
            continue
        invalid = invalid_qmt_price_symbols(pl.read_parquet(qmt_path))
        if not invalid:
            continue
        paid_path = args.warehouse / "paid_minute_bars" / f"year={day.year}" \
            / f"date={stamp}" / "part.parquet"
        paid_symbols = set(pl.read_parquet(paid_path, columns=["symbol"])["symbol"].to_list()) \
            if paid_path.exists() else set()
        target = target_root / f"year={day.year}" / f"date={stamp}" / "part.parquet"
        existing = pl.read_parquet(target) if target.exists() else None
        existing_symbols = set(existing["symbol"].to_list()) if existing is not None else set()
        unresolved = [s for s in invalid if s not in paid_symbols and s not in existing_symbols]
        summary["requested_symbols"] += len(unresolved)
        supported = [s for s in unresolved if _jq_code(s) is not None]
        for symbol in set(unresolved) - set(supported):
            reject("unsupported_market", day, symbol)
        if not supported:
            continue
        try:
            fetched = _fetch(jq, supported, day)
            failed_queries: dict[str, str] = {}
        except Exception:
            pieces: list[pl.DataFrame] = []
            failed_queries = {}
            for symbol in supported:
                try:
                    pieces.append(_fetch(jq, [symbol], day))
                except Exception as exc:
                    failed_queries[symbol] = f"query_error:{type(exc).__name__}"
            fetched = pl.concat([piece for piece in pieces if piece.height], how="vertical") \
                if any(piece.height for piece in pieces) else (pieces[0].head(0) if pieces else pl.DataFrame())
        checkable = [symbol for symbol in supported if symbol not in failed_queries]
        if checkable:
            accepted, rejected = valid_joinquant_overrides(fetched, checkable, day)
        else:
            accepted, rejected = pl.DataFrame(), {}
        rejected.update(failed_queries)
        if not accepted.is_empty():
            daily = daily_query.tushare_daily(
                sorted(accepted["symbol"].unique().to_list()), day, day,
                columns=("symbol", "dt", "open", "high", "low", "close", "volume", "amount"),
            )
            gross = compare_with_daily(accepted, daily).filter(pl.col("gross").is_not_null())
            for symbol, reason in gross.select("symbol", "gross").iter_rows():
                rejected[symbol] = f"daily_{reason}"
            accepted = accepted.filter(~pl.col("symbol").is_in(gross["symbol"].to_list()))
        for symbol, reason in rejected.items():
            reject(reason, day, symbol)
        if accepted.is_empty():
            continue
        summary["accepted_symbols"] += accepted["symbol"].n_unique()
        summary["accepted_rows"] += accepted.height
        summary["written_days"].append(str(day))
        if not args.apply:
            continue
        if existing is not None:
            backup = args.backup_dir / f"year={day.year}" / f"date={stamp}" / "part.parquet"
            backup.parent.mkdir(parents=True, exist_ok=True)
            if backup.exists():
                raise FileExistsError(f"backup already exists: {backup}")
            shutil.copy2(target, backup)
            accepted = pl.concat([existing, accepted], how="vertical") \
                .unique(subset=["symbol", "dt"], keep="last").sort(["symbol", "dt"])
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            accepted.write_parquet(temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    summary["quota_after"] = int(jq.get_query_count(field="spare"))
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
