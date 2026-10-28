"""固定窗口送配快照：完整性验收后仅追加，既有事件冲突时整批拒绝。"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq

from qsys.data import schema
from qsys.data.pipeline.dividends import DIVIDEND_SCHEMA
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.data.windows_data_transport import WindowsDataTransport


def _sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def validate_snapshot(directory: Path, *, symbols: list[str], start: date,
                      end: date, run_id: str) -> pl.DataFrame:
    """不以子进程exit0替代逐证券完整响应、身份、schema和真实值检查。"""
    if {p.name for p in directory.iterdir()} != {"events.parquet", "result.json"}:
        raise ValueError("unexpected dividend bundle inventory")
    result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    identity = {"protocol": "qsys-dividends-v2", "symbols": symbols,
                "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"),
                "run_id": run_id}
    if any(result.get(k) != v for k, v in identity.items()):
        raise ValueError("dividend snapshot identity mismatch")
    if not symbols or len(symbols) != len(set(symbols)):
        raise ValueError("empty/duplicate dividend universe")
    path = directory / "events.parquet"
    if _sha(path) != result.get("sha256"):
        raise ValueError("dividend snapshot digest mismatch")
    arrow = pq.read_table(path)
    if not arrow.schema.equals(DIVIDEND_SCHEMA):
        raise ValueError("dividend snapshot schema mismatch")
    if type(result.get("rows")) is not int or result["rows"] != arrow.num_rows:
        raise ValueError("dividend snapshot row count mismatch")
    receipts = result.get("receipts")
    if not isinstance(receipts, list) or [r.get("symbol") for r in receipts] != symbols:
        raise ValueError("dividend snapshot coverage mismatch")
    counts = dict.fromkeys(symbols, 0)
    seen = set()
    for row in arrow.to_pylist():
        symbol, stamp = row["symbol"], row["ex_date"]
        if symbol not in counts or stamp is None or not start <= stamp <= end:
            raise ValueError("dividend event outside requested scope")
        key = (symbol, stamp)
        if key in seen:
            raise ValueError("duplicate dividend event")
        seen.add(key)
        counts[symbol] += 1
        if any(row[n] is None or not math.isfinite(row[n]) for n in DIVIDEND_SCHEMA.names[2:]):
            raise ValueError("incomplete/nonfinite dividend event")
    for receipt in receipts:
        rows = counts[receipt["symbol"]]
        if (type(receipt.get("rows")) is not int or receipt["rows"] != rows
                or receipt.get("null_cells") != 0
                or receipt.get("status") != ("done" if rows else "empty")):
            raise ValueError("dividend source receipt incomplete")
    return pl.from_arrow(arrow).select(schema.CORPORATE_ACTIONS.names)


def append_events(warehouse: Warehouse, fresh: pl.DataFrame) -> int:
    """同键同值幂等；任何旧值冲突整批拒绝，不自动覆盖旧事实。"""
    old = WarehouseQuery(warehouse).corporate_actions()
    keys = ["symbol", "ex_date"]
    if fresh.select(keys).is_duplicated().any():
        raise ValueError("duplicate incoming dividend event")
    if old.select(keys).is_duplicated().any():
        raise ValueError("duplicate existing dividend event")
    joined = old.join(fresh, on=keys, how="inner", suffix="_incoming")
    values = schema.CORPORATE_ACTIONS.names[2:]
    if joined.filter(pl.any_horizontal([
        ~pl.col(n).eq_missing(pl.col(n + "_incoming")) for n in values
    ])).height:
        raise ValueError("existing dividend event conflicts; explicit review required")
    added = fresh.join(old.select(keys), on=keys, how="anti")
    if added.height:
        merged = pl.concat([old, added], how="vertical")
        warehouse.write("corporate_actions", merged.to_arrow().cast(schema.CORPORATE_ACTIONS))
    return added.height


def import_window(day: date, initial_start: date, checkpoint: Path) -> date:
    """以成功导入窗口而非最后事件日期续采，回看7天并拒绝超出协议窗口的断流。"""
    previous = initial_start
    if checkpoint.exists():
        record = json.loads(checkpoint.read_text(encoding="utf-8"))
        if record.get("protocol") != "qsys-dividend-import-v1":
            raise ValueError("invalid dividend checkpoint")
        if record.get("initial_start") != initial_start.isoformat():
            raise ValueError("dividend initial scope changed")
        previous = date.fromisoformat(record["end"])
    if previous > day or initial_start > day:
        raise ValueError("dividend import cannot move backwards")
    start = max(initial_start, previous - timedelta(days=7))
    if (day - start).days > 31:
        raise ValueError("dividend gap exceeds approved window; explicit recovery required")
    return start


def dividend_universe(instruments: pl.DataFrame, actions: pl.DataFrame,
                      start: date) -> tuple[list[str], list[dict]]:
    """原生身份保持；非原生历史身份只按直取退市日期排除，不做别名映射。"""
    if instruments["symbol"].is_duplicated().any():
        raise ValueError("duplicate dividend instrument identity")
    metadata = {row["symbol"]: row for row in instruments.to_dicts()}
    symbols, excluded = [], []
    for symbol in set(metadata) | set(actions["symbol"].to_list()):
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("invalid dividend identity")
        if re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", symbol):
            symbols.append(symbol)
            continue
        row = metadata.get(symbol)
        if row is None or row["delist_date"] is None or row["delist_date"] >= start:
            raise ValueError("non-native dividend identity requires explicit source verification")
        excluded.append({"symbol": symbol, "delist_date": row["delist_date"].isoformat(),
                         "as_of": row["as_of"].isoformat() if row["as_of"] is not None else None})
    if not symbols:
        raise ValueError("empty dividend universe")
    return sorted(symbols), sorted(excluded, key=lambda row: row["symbol"])


def sync_dividends(warehouse: Warehouse, day: date, *, initial_start: date,
                   work_root: Path, transport: WindowsDataTransport,
                   script: Path, run_id: str) -> int:
    """调用方持实际warehouse写锁；不重发run，不自动恢复未知送达请求。"""
    root = work_root / "dividends"
    checkpoint = root / "last-imported.json"
    start = import_window(day, initial_start, checkpoint)
    query = WarehouseQuery(warehouse)
    symbols, excluded = dividend_universe(query.instruments(), query.corporate_actions(), start)
    digest = _sha(script)
    probe = transport.probe()
    if probe.get("scripts", {}).get("dividend") != digest:
        raise ValueError("dividend downloader is not approved")
    job = f"dividend_{day:%Y%m%d}_{run_id}"
    work = root / job
    work.mkdir(parents=True, exist_ok=False)
    scope = work / "scope.json"
    with scope.open("x", encoding="utf-8") as handle:
        json.dump({"start": start.isoformat(), "end": day.isoformat(),
                   "symbols": symbols, "excluded_retired_catalog_identities": excluded}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    manifest = work / "manifest.csv"
    with manifest.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["symbol", "dt"])
        writer.writerows((symbol, day.isoformat()) for symbol in symbols)
    transport.prepare(job, manifest)
    # Persist intent before any download RPC. A timeout/connection error exits;
    # only the strictly identified preauthentication reset may be retried.
    intent = {"run_id": run_id, "job": job, "start": start.isoformat(),
              "end": day.isoformat(), "script_sha256": digest,
              "manifest_sha256": _sha(manifest)}
    intent["scope_sha256"] = _sha(scope)
    with (work / "requested.json").open("x", encoding="utf-8") as handle:
        json.dump(intent, handle)
        handle.flush()
        os.fsync(handle.fileno())
    transport.run_snapshot(job, script, run_id, start, day, timeout=900)
    destination = work / "bundle"
    transport.receive(job, destination)
    fresh = validate_snapshot(destination, symbols=symbols, start=start, end=day, run_id=run_id)
    rows = append_events(warehouse, fresh)
    record = {**intent, "protocol": "qsys-dividend-import-v1",
              "initial_start": initial_start.isoformat(), "added": rows,
              "bundle_sha256": _sha(destination / "events.parquet")}
    temporary = root / f".last-imported-{run_id}.tmp"
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(record, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, checkpoint)
    return rows
