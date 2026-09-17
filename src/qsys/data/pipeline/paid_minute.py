"""付费 1 分钟源的流式 ZIP 入库。

只做代码/日期/时间格式标准化，所有数值均来自 CSV 直接字段。日线聚合、
跨源对账等计算只能出现在质量报告，不能回写本数据集。
"""

from __future__ import annotations

import base64
import hashlib
import json
import multiprocessing
import os
import re
import zipfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import duckdb


HEADERS = [
    "证券代码", "交易日期", "分钟时间", "开盘价（元）", "最高价（元）",
    "最低价（元）", "收盘价（元）", "成交量", "成交额", "复权因子",
]
HEADER_VARIANTS = [
    HEADERS,
    ["交易日期", "分钟时间", "证券代码", *HEADERS[3:]],
]
SCHEMA = pa.schema(
    [
        ("symbol", pa.string()),
        ("dt", pa.timestamp("us")),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.int64()),
        ("amount", pa.float64()),
        ("factor", pa.float64()),
    ],
    metadata={
        b"provider": b"yuhaha-baidupan",
        b"frequency": b"1min",
        b"timestamp_policy": b"source value; no shift",
        b"price_unit": b"CNY",
        b"volume_unit": b"share",
        b"amount_unit": b"CNY",
        b"factor": b"source cumulative backward adjustment",
    },
)

_SYMBOL_IN_NAME = re.compile(r"(?:^|/)(\d{6}\.(?:SZ|SH|BJ))@")
_SYMBOL = re.compile(r"^\d{6}\.(?:SZ|SH|BJ)$")
_DATE_COMPACT = re.compile(r"^\d{8}$")
_DATE_DASH = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_MINUTE = re.compile(r"^\d{2}:\d{2}$")
_TIME_SECOND = re.compile(r"^\d{2}:\d{2}:\d{2}$")
_DATETIME_SECOND = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


@dataclass(frozen=True, slots=True)
class YearResult:
    year: int
    shards: int
    entries: int
    rows: int
    symbols: int
    start: str
    end: str
    zip_sha256: str


def sha256_file(path: Path, block_size: int = 8 << 20) -> str:
    digest = hashlib.sha256()
    advised_until = 0
    with path.open("rb") as stream:
        while block := stream.read(block_size):
            digest.update(block)
            if hasattr(os, "posix_fadvise") and stream.tell() - advised_until >= 256 << 20:
                os.posix_fadvise(
                    stream.fileno(), advised_until, stream.tell() - advised_until,
                    os.POSIX_FADV_DONTNEED,
                )
                advised_until = stream.tell()
        # 大文件校验页不应长期挤占受 MemoryMax 管理的历史入库任务。
        if hasattr(os, "posix_fadvise"):
            os.posix_fadvise(stream.fileno(), advised_until, 0, os.POSIX_FADV_DONTNEED)
    return digest.hexdigest()


def decoded_name(info: zipfile.ZipInfo) -> tuple[str, bytes]:
    """返回可识别的文件名及可审计的原始名字节。"""
    if info.flag_bits & 0x800:
        return info.filename, info.filename.encode("utf-8")
    raw = info.filename.encode("cp437")
    try:
        return raw.decode("utf-8"), raw
    except UnicodeDecodeError:
        return info.filename, raw


def minute_entries(archive: zipfile.ZipFile, year: int) -> list[tuple[zipfile.ZipInfo, str, bytes]]:
    selected: list[tuple[zipfile.ZipInfo, str, bytes]] = []
    for info in archive.infolist():
        name, raw = decoded_name(info)
        if info.is_dir() or "__MACOSX" in name or name.endswith("/.DS_Store"):
            continue
        if f"/{year}/1min/" in f"/{name}" and name.lower().endswith(".csv"):
            selected.append((info, name, raw))
    selected.sort(key=lambda item: item[1])
    if not selected:
        raise ValueError(f"ZIP 中没有 {year}/1min CSV")
    return selected


def _uniform_format(values: pl.Series, patterns: list[tuple[re.Pattern[str], str]], label: str) -> str:
    non_null = values.drop_nulls().unique()
    if non_null.len() == 0:
        raise ValueError(f"{label} 全为空")
    for pattern, fmt in patterns:
        if non_null.cast(pl.String).str.contains(pattern.pattern).all():
            return fmt
    sample = non_null.head(5).to_list()
    raise ValueError(f"未知{label}格式: {sample}")


def _write_quarantine(raw: pl.DataFrame, path: Path, member: str, reason: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".parquet.tmp")
    evidence = raw.with_columns(
        pl.lit(member).alias("source_member"),
        pl.lit(reason).alias("reason"),
    )
    evidence.write_parquet(temp, compression="zstd")
    os.replace(temp, path)


def read_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    decoded: str,
    year: int,
    *,
    quarantine_dir: Path | None = None,
) -> pl.DataFrame:
    match = _SYMBOL_IN_NAME.search(decoded)
    if match is None:
        raise ValueError(f"无法从文件名识别证券代码: {decoded}")
    file_symbol = match.group(1)
    with archive.open(info) as stream:
        raw = pl.read_csv(
            stream,
            schema_overrides={name: pl.String for name in HEADERS},
            infer_schema=False,
            try_parse_dates=False,
            encoding="utf8",
        )
    raw.columns = [name.lstrip("\ufeff") for name in raw.columns]
    if raw.columns not in HEADER_VARIANTS:
        raise ValueError(f"{decoded}: 表头不符 {raw.columns}")
    if raw.height == 0:
        raise ValueError(f"{decoded}: 空文件")
    raw = raw.with_row_index("source_row", offset=2).with_columns(
        pl.exclude("source_row").str.strip_chars()
    )
    missing = pl.any_horizontal(
        pl.exclude("source_row").is_null() | (pl.exclude("source_row") == "")
    )
    bad_nulls = raw.filter(missing)
    if bad_nulls.height:
        if quarantine_dir is None:
            raise ValueError(f"{decoded}: 核心字段存在空值")
        _write_quarantine(
            bad_nulls,
            quarantine_dir / "null-core" / f"{file_symbol}.parquet",
            decoded,
            "CORE_FIELD_NULL",
        )
        raw = raw.filter(~missing)
        if raw.height == 0:
            return pl.from_arrow(SCHEMA.empty_table())
    date_fmt = _uniform_format(
        raw["交易日期"], [(_DATE_COMPACT, "%Y%m%d"), (_DATE_DASH, "%Y-%m-%d")], "日期"
    )
    time_fmt = _uniform_format(
        raw["分钟时间"], [(_TIME_MINUTE, "%H:%M"), (_TIME_SECOND, "%H:%M:%S"),
                           (_DATETIME_SECOND, "%Y-%m-%d %H:%M:%S")], "时间"
    )
    date_text = pl.col("交易日期")
    if time_fmt.startswith("%Y"):
        datetime_expr = pl.col("分钟时间").str.strptime(pl.Datetime("us"), time_fmt, strict=True)
    else:
        datetime_expr = pl.concat_str([date_text, pl.lit(" "), pl.col("分钟时间")]).str.strptime(
            pl.Datetime("us"), f"{date_fmt} {time_fmt}", strict=True
        )
    frame = raw.select(
        pl.col("source_row"),
        pl.col("证券代码").alias("symbol"),
        date_text.str.strptime(pl.Date, date_fmt, strict=True).alias("trade_date"),
        datetime_expr.alias("dt"),
        pl.col("开盘价（元）").cast(pl.Float64, strict=True).alias("open"),
        pl.col("最高价（元）").cast(pl.Float64, strict=True).alias("high"),
        pl.col("最低价（元）").cast(pl.Float64, strict=True).alias("low"),
        pl.col("收盘价（元）").cast(pl.Float64, strict=True).alias("close"),
        pl.col("成交量").cast(pl.Float64, strict=True).alias("volume_float"),
        pl.col("成交额").cast(pl.Float64, strict=True).alias("amount"),
        pl.col("复权因子").cast(pl.Float64, strict=True).alias("factor"),
    )
    if frame["symbol"].unique().to_list() != [file_symbol] or not _SYMBOL.fullmatch(file_symbol):
        raise ValueError(f"{decoded}: 文件名与证券代码不一致")
    if frame.filter(pl.col("trade_date") != pl.col("dt").dt.date()).height:
        raise ValueError(f"{decoded}: 交易日期与分钟时间日期不一致")
    if frame["dt"].dt.year().unique().to_list() != [year]:
        raise ValueError(f"{decoded}: 行日期不属于 {year}")
    numeric = ["open", "high", "low", "close", "volume_float", "amount", "factor"]
    if frame.select(pl.any_horizontal(pl.col(numeric).is_nan() | pl.col(numeric).is_infinite()).any()).item():
        raise ValueError(f"{decoded}: 存在非有限数值")
    if frame.filter(pl.col("volume_float") != pl.col("volume_float").floor()).height:
        raise ValueError(f"{decoded}: 成交量不是源整数")
    invalid = frame.filter(
        (pl.col("low") > pl.col("high"))
        | (pl.col("open") < pl.col("low")) | (pl.col("open") > pl.col("high"))
        | (pl.col("close") < pl.col("low")) | (pl.col("close") > pl.col("high"))
        | (pl.col("open") <= 0) | (pl.col("high") <= 0)
        | (pl.col("low") <= 0) | (pl.col("close") <= 0)
        | (pl.col("volume_float") < 0) | (pl.col("amount") < 0) | (pl.col("factor") <= 0)
    )
    if invalid.height:
        if quarantine_dir is None:
            raise ValueError(f"{decoded}: {invalid.height} 行违反价格/非负/因子约束")
        _write_quarantine(
            raw.join(invalid.select("source_row"), on="source_row", how="semi"),
            quarantine_dir / "invalid-values" / f"{file_symbol}.parquet",
            decoded,
            "VALUE_CONSTRAINT",
        )
        frame = frame.join(invalid.select("source_row"), on="source_row", how="anti")
        if frame.height == 0:
            return pl.from_arrow(SCHEMA.empty_table())
    frame = frame.with_columns(pl.col("volume_float").cast(pl.Int64).alias("volume")).drop("volume_float")
    if frame.select(pl.struct("symbol", "dt").is_duplicated().any()).item():
        raise ValueError(f"{decoded}: 重复 (symbol, dt)")
    return frame.select(SCHEMA.names).sort("dt")


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")
    os.replace(temp, path)


def _write_shard(frame: pl.DataFrame, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".parquet.tmp")
    table = frame.to_arrow().cast(SCHEMA).replace_schema_metadata(SCHEMA.metadata)
    pq.write_table(table, temp, compression="zstd", compression_level=3, row_group_size=262_144)
    os.replace(temp, target)
    return sha256_file(target)


def _write_member_group(
    archive: zipfile.ZipFile,
    group: list[tuple[zipfile.ZipInfo, str, bytes]],
    year: int,
    target: Path,
    quarantine_dir: Path,
) -> dict:
    """逐证券写 row group，避免一个分片的全部证券同时驻留内存。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".parquet.tmp")
    temp.unlink(missing_ok=True)
    writer: pq.ParquetWriter | None = None
    rows = 0
    symbols: set[str] = set()
    first: datetime | None = None
    last: datetime | None = None
    try:
        for info, name, _ in group:
            frame = read_member(archive, info, name, year, quarantine_dir=quarantine_dir)
            if frame.height == 0:
                continue
            table = frame.to_arrow().cast(SCHEMA).replace_schema_metadata(SCHEMA.metadata)
            if writer is None:
                writer = pq.ParquetWriter(temp, SCHEMA, compression="zstd", compression_level=3)
            writer.write_table(table, row_group_size=262_144)
            rows += frame.height
            symbols.update(frame["symbol"].unique().to_list())
            member_first = frame["dt"].min()
            member_last = frame["dt"].max()
            first = member_first if first is None or member_first < first else first
            last = member_last if last is None or member_last > last else last
        if writer is None:
            return {
                "rows": 0, "symbols": 0, "start": None, "end": None,
                "parquet_sha256": _write_shard(pl.from_arrow(SCHEMA.empty_table()), target),
            }
        writer.close()
        writer = None
        os.replace(temp, target)
    except Exception:
        if writer is not None:
            writer.close()
        temp.unlink(missing_ok=True)
        raise
    return {
        "rows": rows,
        "symbols": len(symbols),
        "start": str(first),
        "end": str(last),
        "parquet_sha256": sha256_file(target),
    }


def _member_group_worker(
    zip_path: str,
    members: list[tuple[str, str, bytes]],
    year: int,
    target: str,
    result_path: str,
    quarantine_dir: str,
) -> None:
    """独立进程写一个分片，让解析器内存池随进程退出彻底释放。"""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            group = [(archive.getinfo(stored), decoded, raw) for stored, decoded, raw in members]
            result = _write_member_group(
                archive, group, year, Path(target), Path(quarantine_dir)
            )
        if hasattr(os, "posix_fadvise"):
            with open(zip_path, "rb") as source:
                os.posix_fadvise(source.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        _atomic_json(Path(result_path), {"ok": True, "result": result})
    except Exception as exc:
        _atomic_json(Path(result_path), {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        })


def _write_member_group_isolated(
    zip_path: Path,
    group: list[tuple[zipfile.ZipInfo, str, bytes]],
    year: int,
    target: Path,
    quarantine_dir: Path,
) -> dict:
    result_path = target.with_name("worker-result.json")
    result_path.unlink(missing_ok=True)
    members = [(info.filename, decoded, raw) for info, decoded, raw in group]
    process = multiprocessing.get_context("spawn").Process(
        target=_member_group_worker,
        args=(str(zip_path), members, year, str(target), str(result_path), str(quarantine_dir)),
    )
    process.start()
    process.join()
    if process.exitcode != 0 or not result_path.exists():
        raise RuntimeError(f"分片子进程失败 exitcode={process.exitcode}")
    outcome = json.loads(result_path.read_text())
    result_path.unlink()
    if not outcome["ok"]:
        raise ValueError(f"{outcome['error_type']}: {outcome['error']}")
    return outcome["result"]


def ingest_year(zip_path: Path, warehouse: Path, year: int, *, shard_members: int = 64) -> YearResult:
    """把一个年度 ZIP 的 1min 数据写入暂存年度目录，可按分片续跑。"""
    if zip_path.stat().st_size <= 0:
        raise ValueError("ZIP 为空")
    zip_hash = sha256_file(zip_path)
    staging = warehouse / "paid_minute_bars_staging" / f"year={year}"
    quarantine = warehouse / "paid_minute_quarantine" / f"year={year}"
    staging.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as archive:
        # ZipFile 打开时已解析并校验中央目录。被采用的每个 1min 条目会在
        # read_member 完整读到 EOF，由 zipfile 校验该条目的 CRC；未采用的
        # 其他周期不额外解压，避免为验收 1min 重复展开约 124 GiB 数据。
        entries = minute_entries(archive, year)
        symbols = []
        entry_meta = []
        for info, name, raw_name in entries:
            match = _SYMBOL_IN_NAME.search(name)
            if match is None:
                raise ValueError(f"无法识别证券代码: {name}")
            symbols.append(match.group(1))
            entry_meta.append({
                "name": name,
                "raw_name_base64": base64.b64encode(raw_name).decode("ascii"),
                "crc32": f"{info.CRC:08x}",
                "compressed_size": info.compress_size,
                "uncompressed_size": info.file_size,
            })
        if len(symbols) != len(set(symbols)):
            raise ValueError("同一年度出现重复证券文件")

        shard_records = []
        for start in range(0, len(entries), shard_members):
            shard_no = start // shard_members
            group = entries[start:start + shard_members]
            shard_dir = staging / f"shard={shard_no:04d}"
            target = shard_dir / "part.parquet"
            receipt = shard_dir / "manifest.json"
            expected_names = [name for _, name, _ in group]
            if target.exists() and receipt.exists():
                old = json.loads(receipt.read_text())
                if (old.get("source_entries") == expected_names
                        and old.get("parquet_sha256") == sha256_file(target)):
                    shard_records.append(old)
                    print(f"[{len(shard_records)}/{(len(entries) + shard_members - 1) // shard_members}] "
                          f"shard={shard_no:04d} 已校验跳过", flush=True)
                    continue
            written = _write_member_group_isolated(zip_path, group, year, target, quarantine)
            record = {
                "shard": shard_no,
                "source_entries": expected_names,
                **written,
            }
            _atomic_json(receipt, record)
            shard_records.append(record)
            print(f"[{len(shard_records)}/{(len(entries) + shard_members - 1) // shard_members}] "
                  f"shard={shard_no:04d} rows={record['rows']:,}", flush=True)

    starts = [str(item["start"]) for item in shard_records if item["start"] is not None]
    ends = [str(item["end"]) for item in shard_records if item["end"] is not None]
    if not starts or not ends:
        raise ValueError(f"{year} 所有 1min 源成员均被隔离")
    result = YearResult(
        year=year,
        shards=len(shard_records),
        entries=len(entries),
        rows=sum(int(item["rows"]) for item in shard_records),
        # 源成员可能整文件被严格隔离；事实证券数只统计实际写入的证券。
        symbols=sum(int(item["symbols"]) for item in shard_records),
        start=min(starts),
        end=max(ends),
        zip_sha256=zip_hash,
    )
    _atomic_json(staging / "manifest.json", {
        **asdict(result),
        "zip_path": str(zip_path),
        "schema": SCHEMA.to_string(),
        "entry_inventory": entry_meta,
        "shard_inventory": shard_records,
        "status": "STAGED",
    })
    return result


def publish_year(warehouse: Path, year: int) -> Path:
    """质量报告批准后原子发布一个年度；不在本函数内决定质量结论。"""
    staging = warehouse / "paid_minute_bars_staging" / f"year={year}"
    target = warehouse / "paid_minute_bars" / f"year={year}"
    if target.exists():
        raise ValueError(f"{year} 已发布，拒绝覆盖")
    if not (staging / "manifest.json").exists():
        raise ValueError(f"{year} 暂存清单不存在")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, target)
    parent = staging.parent
    if parent.exists() and not any(parent.iterdir()):
        parent.rmdir()
    return target


def _placeholder_shard_worker(
    source: str,
    cleaned: str,
    evidence_tmp: str,
    candidate_rows: list[tuple[str, str]],
    result_path: str,
) -> None:
    """在短生命周期进程中核验并改写一个含占位日的分片。"""
    try:
        candidates = pl.DataFrame(
            candidate_rows, schema={"symbol": pl.String, "trade_date_text": pl.String}, orient="row"
        ).with_columns(
            pl.col("trade_date_text").str.strptime(pl.Date, "%Y-%m-%d").alias("trade_date")
        ).drop("trade_date_text").with_columns(pl.lit(True).alias("quarantine_day"))
        frame = pl.read_parquet(source)
        marked = frame.with_columns(pl.col("dt").dt.date().alias("trade_date")).join(
            candidates, on=["symbol", "trade_date"], how="left",
        )
        quarantined = marked.filter(pl.col("quarantine_day") == True)
        checks = quarantined.group_by(["symbol", "trade_date"]).agg(
            pl.len().alias("minute_count"),
            pl.col("dt").dt.time().filter(
                pl.col("dt").dt.time() <= datetime.strptime("15:00", "%H:%M").time()
            ).len().alias("regular_count"),
            pl.col("volume").sum().alias("volume"),
            pl.col("amount").sum().alias("amount"),
            pl.concat_list(["open", "high", "low", "close"]).list.explode().min().alias("price_min"),
            pl.concat_list(["open", "high", "low", "close"]).list.explode().max().alias("price_max"),
        )
        invalid = checks.filter(
            (pl.col("regular_count") != 241) | (pl.col("volume") != 0)
            | (pl.col("amount") != 0) | (pl.col("price_min") != pl.col("price_max"))
        )
        if invalid.height or checks.height != len(candidate_rows):
            raise ValueError(f"候选占位日不满足严格条件: {invalid.head(10).to_dicts()}")
        kept = marked.filter(pl.col("quarantine_day").is_null()).select(SCHEMA.names)
        quarantined = quarantined.select(SCHEMA.names)
        cleaned_hash = _write_shard(kept, Path(cleaned))
        Path(evidence_tmp).parent.mkdir(parents=True, exist_ok=True)
        quarantined.with_columns(
            pl.lit("ZERO_TRADE_FLAT_NO_DAILY_RECORD").alias("reason")
        ).write_parquet(evidence_tmp, compression="zstd")
        if hasattr(os, "posix_fadvise"):
            with open(source, "rb") as source_file:
                os.posix_fadvise(source_file.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        _atomic_json(Path(result_path), {"ok": True, "result": {
            "rows": kept.height,
            "symbols": kept["symbol"].n_unique(),
            "start": str(kept["dt"].min()),
            "end": str(kept["dt"].max()),
            "parquet_sha256": cleaned_hash,
            "quarantined_rows": quarantined.height,
            "keys": [(row["symbol"], str(row["trade_date"])) for row in checks.to_dicts()],
        }})
    except Exception as exc:
        _atomic_json(Path(result_path), {
            "ok": False, "error_type": type(exc).__name__, "error": str(exc),
        })


def quarantine_unmatched_zero_trade_days(warehouse: Path, year: int) -> dict:
    """隔离无日线记录的全天零成交恒价占位分钟，不修改任何源字段。

    候选键来自最近一次质量聚合与付费日线的直接交叉核验。只有整日所有
    分钟成交量/额均为零、四价始终为同一值且常规时段恰有 241 根时才会
    被隔离；任一候选不满足这些条件时整次操作在改写前失败。
    """
    staging = warehouse / "paid_minute_bars_staging" / f"year={year}"
    manifest_path = staging / "manifest.json"
    aggregate = warehouse / "paid_minute_quality" / f"year={year}" / "daily_aggregate.parquet"
    daily = warehouse / "paid_daily" / f"year={year}" / "part.parquet"
    if not manifest_path.exists() or not aggregate.exists() or not daily.exists():
        raise ValueError("需要暂存清单、最近一次质量聚合和同年度付费日线")

    con = duckdb.connect()
    agg_sql = str(aggregate).replace("'", "''")
    daily_sql = str(daily).replace("'", "''")
    unmatched = pl.from_arrow(con.execute(f"""
        SELECT a.symbol, a.dt AS trade_date
        FROM read_parquet('{agg_sql}') a
        LEFT JOIN read_parquet('{daily_sql}') p USING(symbol, dt)
        WHERE p.symbol IS NULL
        ORDER BY a.symbol, a.dt
    """).arrow())
    candidates = pl.from_arrow(con.execute(f"""
        SELECT a.symbol, a.dt AS trade_date
        FROM read_parquet('{agg_sql}') a
        LEFT JOIN read_parquet('{daily_sql}') p USING(symbol, dt)
        WHERE p.symbol IS NULL AND a.regular_minute_count=241
          AND a.volume=0 AND a.amount=0
          AND a.open=a.high AND a.high=a.low AND a.low=a.close
        ORDER BY a.symbol, a.dt
    """).arrow())
    con.close()
    retained_unmatched = unmatched.join(candidates, on=["symbol", "trade_date"], how="anti")
    if candidates.height == 0:
        return {
            "year": year, "days": 0, "rows": 0, "shards": 0,
            "retained_unmatched_days": retained_unmatched.height,
        }

    shard_paths = sorted(staging.glob("shard=*/part.parquet"))
    quarantine_root = warehouse / "paid_minute_quarantine" / f"year={year}" / "zero-trade-flat-no-daily"
    for stale in staging.glob("shard=*/part.cleaned.parquet"):
        stale.unlink()
    if quarantine_root.exists():
        for stale in quarantine_root.rglob("*.tmp"):
            stale.unlink()
    candidate_by_symbol: dict[str, list[tuple[str, str]]] = {}
    for row in candidates.to_dicts():
        candidate_by_symbol.setdefault(row["symbol"], []).append(
            (row["symbol"], str(row["trade_date"]))
        )
    prepared: list[tuple[Path, Path, Path, dict]] = []
    observed_keys: list[tuple[str, str]] = []
    total_rows = 0
    completed_shards = 0
    try:
        for path in shard_paths:
            receipt = path.parent / "manifest.json"
            old = json.loads(receipt.read_text())
            shard_symbols = {
                match.group(1) for name in old["source_entries"]
                if (match := _SYMBOL_IN_NAME.search(name)) is not None
            }
            shard_candidates = [
                item for symbol in shard_symbols for item in candidate_by_symbol.get(symbol, [])
            ]
            if not shard_candidates:
                continue
            shard_name = path.parent.name
            evidence = quarantine_root / shard_name / "part.parquet"
            already_recorded = "quarantined_placeholder_rows" in old
            if already_recorded or evidence.exists():
                if not (already_recorded and evidence.exists()):
                    raise ValueError(f"{shard_name}: 隔离证据与分片清单提交状态不一致")
                evidence_keys = sorted({
                    (row["symbol"], str(row["trade_date"]))
                    for row in pl.read_parquet(evidence, columns=["symbol", "dt"])
                    .with_columns(pl.col("dt").dt.date().alias("trade_date"))
                    .select("symbol", "trade_date").unique().to_dicts()
                })
                if evidence_keys != sorted(shard_candidates):
                    raise ValueError(f"{shard_name}: 已提交隔离证据与候选键不一致")
                observed_keys.extend(evidence_keys)
                total_rows += int(old["quarantined_placeholder_rows"])
                completed_shards += 1
                continue
            cleaned = path.with_name("part.cleaned.parquet")
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence_tmp = evidence.with_suffix(".parquet.tmp")
            result_path = path.with_name("placeholder-worker-result.json")
            result_path.unlink(missing_ok=True)
            process = multiprocessing.get_context("spawn").Process(
                target=_placeholder_shard_worker,
                args=(str(path), str(cleaned), str(evidence_tmp), shard_candidates, str(result_path)),
            )
            process.start()
            process.join()
            if process.exitcode != 0 or not result_path.exists():
                raise RuntimeError(f"占位隔离子进程失败 {shard_name} exitcode={process.exitcode}")
            outcome = json.loads(result_path.read_text())
            result_path.unlink()
            if not outcome["ok"]:
                raise ValueError(f"{shard_name}: {outcome['error_type']}: {outcome['error']}")
            result = outcome["result"]
            record = {
                **old,
                "rows": result["rows"],
                "symbols": result["symbols"],
                "start": result["start"],
                "end": result["end"],
                "parquet_sha256": result["parquet_sha256"],
                "quarantined_placeholder_rows": result["quarantined_rows"],
            }
            prepared.append((path, cleaned, evidence_tmp, record))
            observed_keys.extend(tuple(item) for item in result["keys"])
            total_rows += result["quarantined_rows"]
        expected_keys = sorted((row["symbol"], str(row["trade_date"])) for row in candidates.to_dicts())
        if sorted(observed_keys) != expected_keys:
            raise ValueError("质量聚合候选键与事实分片不一致，拒绝改写")
        for path, cleaned, evidence_tmp, record in prepared:
            os.replace(cleaned, path)
            evidence = quarantine_root / path.parent.name / "part.parquet"
            os.replace(evidence_tmp, evidence)
            _atomic_json(path.parent / "manifest.json", record)
    finally:
        for _, cleaned, evidence_tmp, _ in prepared:
            cleaned.unlink(missing_ok=True)
            evidence_tmp.unlink(missing_ok=True)

    manifest = json.loads(manifest_path.read_text())
    records = [json.loads((path.parent / "manifest.json").read_text()) for path in shard_paths]
    starts = [record["start"] for record in records if record["start"] != "None"]
    ends = [record["end"] for record in records if record["end"] != "None"]
    manifest.update({
        "rows": sum(int(record["rows"]) for record in records),
        "symbols": sum(int(record["symbols"]) for record in records),
        "start": min(starts),
        "end": max(ends),
        "shard_inventory": records,
        "placeholder_quarantine": {
            "reason": "ZERO_TRADE_FLAT_NO_DAILY_RECORD",
            "days": candidates.height,
            "rows": total_rows,
            "policy": "direct source rows preserved; no values calculated or written back",
        },
        "retained_unmatched_days": {
            "days": retained_unmatched.height,
            "symbols": retained_unmatched["symbol"].n_unique(),
            "policy": "non-placeholder source facts retained for separate identity/source validation",
        },
    })
    _atomic_json(manifest_path, manifest)
    return {
        "year": year, "days": candidates.height, "rows": total_rows,
        "shards": completed_shards + len(prepared),
        "retained_unmatched_days": retained_unmatched.height,
    }


def validate_year(warehouse: Path, year: int) -> dict:
    """生成年度质量报告；所有聚合仅用于核验，不写回事实表。"""
    staging = warehouse / "paid_minute_bars_staging" / f"year={year}"
    manifest_path = staging / "manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"{year} 暂存清单不存在")
    manifest = json.loads(manifest_path.read_text())
    minute_glob = str(staging / "shard=*/part.parquet").replace("'", "''")
    daily_path = warehouse / "paid_daily" / f"year={year}" / "part.parquet"
    if not daily_path.exists():
        raise ValueError(f"{year} 付费日线不存在")
    daily_sql_path = str(daily_path).replace("'", "''")
    quality = warehouse / "paid_minute_quality" / f"year={year}"
    quality.mkdir(parents=True, exist_ok=True)
    aggregate = quality / "daily_aggregate.parquet"
    aggregate_tmp = quality / "daily_aggregate.parquet.tmp"
    aggregate_tmp.unlink(missing_ok=True)

    con = duckdb.connect()
    duckdb_temp = quality / "duckdb-temp"
    duckdb_temp.mkdir(exist_ok=True)
    con.execute("SET memory_limit='3GB'")
    con.execute("SET threads=4")
    con.execute(f"SET temp_directory='{str(duckdb_temp).replace(chr(39), chr(39)*2)}'")
    minute = f"read_parquet('{minute_glob}', union_by_name=true)"
    structural_query = f"""
        SELECT count(*)::BIGINT row_count,
               count(DISTINCT symbol)::BIGINT symbols,
               min(dt) min_dt, max(dt) max_dt,
               count(*) FILTER (WHERE
                   NOT ((strftime(dt, '%H:%M') BETWEEN '09:30' AND '11:30') OR
                        (strftime(dt, '%H:%M') BETWEEN '13:00' AND '15:00') OR
                        (right(symbol, 2)='BJ' AND strftime(dt, '%H:%M') BETWEEN '15:01' AND '15:30'))
               )::BIGINT invalid_session_times,
               count(*) FILTER (WHERE right(symbol, 2)='BJ' AND
                   strftime(dt, '%H:%M') BETWEEN '15:01' AND '15:30')::BIGINT bj_post_close_rows,
               count(*) FILTER (WHERE right(symbol, 2)='BJ' AND
                   strftime(dt, '%H:%M') BETWEEN '15:01' AND '15:30' AND
                   (volume<>0 OR amount<>0))::BIGINT bj_post_close_trade_rows
        FROM {minute}
    """
    cursor = con.execute(structural_query)
    structural = dict(zip([d[0] for d in cursor.description], cursor.fetchone()))
    if structural["row_count"] != manifest["rows"] or structural["symbols"] != manifest["symbols"]:
        raise ValueError("暂存清单与 Parquet 行数/证券数不一致")

    aggregate_query = f"""
        SELECT symbol, CAST(dt AS DATE) AS dt,
               arg_min(open, dt) FILTER (WHERE strftime(dt, '%H:%M')<='15:00') AS "open",
               max(high) FILTER (WHERE strftime(dt, '%H:%M')<='15:00') AS high,
               min(low) FILTER (WHERE strftime(dt, '%H:%M')<='15:00') AS low,
               arg_max(close, dt) FILTER (WHERE strftime(dt, '%H:%M')<='15:00') AS "close",
               sum(volume)::HUGEINT AS volume,
               sum(amount) AS amount, min(factor) AS factor_min, max(factor) AS factor_max,
               count(*)::INTEGER AS minute_count,
               count(*) FILTER (WHERE strftime(dt, '%H:%M')<='15:00')::INTEGER AS regular_minute_count,
               count(*) FILTER (WHERE strftime(dt, '%H:%M')>'15:00')::INTEGER AS post_close_minute_count
        FROM {minute}
        GROUP BY symbol, CAST(dt AS DATE)
    """
    con.execute(
        f"COPY ({aggregate_query}) TO '{str(aggregate_tmp).replace(chr(39), chr(39)*2)}' "
        "(FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    os.replace(aggregate_tmp, aggregate)
    agg = f"read_parquet('{str(aggregate).replace(chr(39), chr(39)*2)}')"
    paid = f"read_parquet('{daily_sql_path}')"
    comparison_query = f"""
        SELECT count(*)::BIGINT stock_days,
               count(p.symbol)::BIGINT matched_paid_days,
               count(*) FILTER (WHERE p.symbol IS NULL)::BIGINT missing_paid_days,
               count(*) FILTER (WHERE a.regular_minute_count <> 241)::BIGINT non_241_regular_days,
               min(a.minute_count)::INTEGER min_minutes_per_day,
               max(a.minute_count)::INTEGER max_minutes_per_day,
               count(*) FILTER (WHERE a.factor_min <> a.factor_max)::BIGINT factor_not_unique_days,
               count(*) FILTER (WHERE p.symbol IS NOT NULL AND
                   (abs(a.open-p.open) > 0.005 OR abs(a.high-p.high) > 0.005 OR
                    abs(a.low-p.low) > 0.005 OR abs(a.close-p.close) > 0.005)
               )::BIGINT ohlc_mismatch_days,
               count(*) FILTER (WHERE p.symbol IS NOT NULL AND
                   abs(CAST(a.volume AS DOUBLE)-p.volume*100) > 0.5
               )::BIGINT volume_mismatch_days,
               count(*) FILTER (WHERE p.symbol IS NOT NULL AND
                   abs(a.amount-p.amount) > 100
               )::BIGINT amount_mismatch_gt_100_days,
               max(abs(a.amount-p.amount)) FILTER (WHERE p.symbol IS NOT NULL) max_amount_abs_diff,
               count(*) FILTER (WHERE p.symbol IS NOT NULL AND
                   (p.factor IS NULL OR abs(a.factor_min-p.factor) > 1e-10 OR
                    abs(a.factor_max-p.factor) > 1e-10)
               )::BIGINT factor_mismatch_days
        FROM {agg} a LEFT JOIN {paid} p USING(symbol, dt)
    """
    cursor = con.execute(comparison_query)
    daily = dict(zip([d[0] for d in cursor.description], cursor.fetchone()))

    discrepancies = quality / "daily_discrepancies.parquet"
    discrepancies_tmp = quality / "daily_discrepancies.parquet.tmp"
    discrepancies_tmp.unlink(missing_ok=True)
    discrepancy_query = f"""
        SELECT a.*, p.open paid_open, p.high paid_high, p.low paid_low, p.close paid_close,
               p.volume paid_volume_lots, p.amount paid_amount, p.factor paid_factor,
               p.symbol IS NULL missing_paid,
               p.symbol IS NOT NULL AND
                 (abs(a.open-p.open)>0.005 OR abs(a.high-p.high)>0.005 OR
                  abs(a.low-p.low)>0.005 OR abs(a.close-p.close)>0.005) ohlc_mismatch,
               p.symbol IS NOT NULL AND abs(CAST(a.volume AS DOUBLE)-p.volume*100)>0.5 volume_mismatch,
               p.symbol IS NOT NULL AND abs(a.amount-p.amount)>100 amount_mismatch_gt_100,
               p.symbol IS NOT NULL AND
                 (p.factor IS NULL OR abs(a.factor_min-p.factor)>1e-10 OR
                  abs(a.factor_max-p.factor)>1e-10) factor_mismatch
        FROM {agg} a LEFT JOIN {paid} p USING(symbol, dt)
        WHERE p.symbol IS NULL OR a.regular_minute_count<>241 OR a.factor_min<>a.factor_max OR
              (p.symbol IS NOT NULL AND
                (abs(a.open-p.open)>0.005 OR abs(a.high-p.high)>0.005 OR
                 abs(a.low-p.low)>0.005 OR abs(a.close-p.close)>0.005 OR
                 abs(CAST(a.volume AS DOUBLE)-p.volume*100)>0.5 OR
                 abs(a.amount-p.amount)>100 OR p.factor IS NULL OR
                 abs(a.factor_min-p.factor)>1e-10 OR abs(a.factor_max-p.factor)>1e-10))
        ORDER BY dt, symbol
    """
    con.execute(
        f"COPY ({discrepancy_query}) TO '{str(discrepancies_tmp).replace(chr(39), chr(39)*2)}' "
        "(FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    os.replace(discrepancies_tmp, discrepancies)

    # QMT 分钟线可能已按交易日分区（D-20），也可能仍是整年文件；两种布局都认
    qmt_dir = warehouse / "minute_bars" / f"year={year}"
    qmt_files = sorted(qmt_dir.glob("date=*/part.parquet")) or (
        [qmt_dir / "part.parquet"] if (qmt_dir / "part.parquet").exists() else [])
    qmt: dict[str, object] = {"available": bool(qmt_files)}
    if qmt_files:
        listed = ", ".join("'" + str(f).replace(chr(39), chr(39) * 2) + "'" for f in qmt_files)
        qmt_source = f"read_parquet([{listed}])"
        qmt_query = f"""
            SELECT count(*)::BIGINT qmt_rows,
                   count(p.symbol)::BIGINT exact_key_overlap,
                   count(*) FILTER (WHERE p.symbol IS NULL)::BIGINT qmt_keys_without_paid,
                   count(*) FILTER (WHERE p.symbol IS NOT NULL AND
                     (abs(q.open-p.open)>1e-8 OR abs(q.high-p.high)>1e-8 OR
                      abs(q.low-p.low)>1e-8 OR abs(q.close-p.close)>1e-8))::BIGINT ohlc_mismatch_rows,
                   count(*) FILTER (WHERE p.symbol IS NOT NULL AND q.volume<>p.volume)::BIGINT volume_mismatch_rows,
                   count(*) FILTER (WHERE p.symbol IS NOT NULL AND abs(q.amount-p.amount)>1)::BIGINT amount_mismatch_gt_1_rows
            FROM {qmt_source} q LEFT JOIN {minute} p USING(symbol, dt)
        """
        cursor = con.execute(qmt_query)
        qmt.update(dict(zip([d[0] for d in cursor.description], cursor.fetchone())))

    quarantine_root = warehouse / "paid_minute_quarantine" / f"year={year}"
    quarantine_files = sorted(quarantine_root.rglob("*.parquet")) if quarantine_root.exists() else []
    quarantine_summary: dict[str, object] = {
        "files": len(quarantine_files),
        "rows": 0,
        "reasons": {},
    }
    if quarantine_files:
        reason_counts: dict[str, int] = {}
        for path in quarantine_files:
            reasons = pl.read_parquet(path, columns=["reason"])
            quarantine_summary["rows"] += reasons.height
            for row in reasons.group_by("reason").len().to_dicts():
                reason_counts[row["reason"]] = reason_counts.get(row["reason"], 0) + row["len"]
        quarantine_summary["reasons"] = reason_counts

    report = {
        "year": year,
        "status": "VALIDATED_NOT_PUBLISHED",
        "source_manifest": str(manifest_path),
        "structural": structural,
        "daily_comparison": daily,
        "qmt_comparison": qmt,
        "quarantine": quarantine_summary,
        "artifacts": {
            "daily_aggregate": str(aggregate),
            "daily_discrepancies": str(discrepancies),
        },
        "rules": {
            "ohlc_daily_tolerance_cny": 0.005,
            "volume_rule": "sum(source minute shares) == source daily lots * 100, tolerance 0.5 share",
            "amount_daily_tolerance_cny": 100,
            "factor_tolerance": 1e-10,
            "ohlc_session": "09:30-15:00; BJ 15:01-15:30 retained as direct post-close/bulk-trade observations",
            "note": "aggregates are validation-only and never written to source facts",
        },
    }
    _atomic_json(quality / "quality-summary.json", report)
    return report


def query_paid_minute(
    warehouse: Path,
    symbols: Sequence[str] | None,
    start: datetime,
    end: datetime,
) -> pl.DataFrame:
    """查询已发布的付费 1min 事实分片，不读取暂存或隔离数据。"""
    if start > end:
        raise ValueError("start is after end")
    if symbols is not None and not symbols:
        return pl.from_arrow(SCHEMA.empty_table())
    files = []
    for year in range(start.year, end.year + 1):
        files.extend(sorted((warehouse / "paid_minute_bars" / f"year={year}").glob("shard=*/part.parquet")))
    if not files:
        return pl.from_arrow(SCHEMA.empty_table())
    query = pl.scan_parquet(files, hive_partitioning=False).select(SCHEMA.names).filter(
        pl.col("dt").is_between(start, end)
    )
    if symbols is not None:
        query = query.filter(pl.col("symbol").is_in(symbols))
    return query.collect().sort(["symbol", "dt"])
