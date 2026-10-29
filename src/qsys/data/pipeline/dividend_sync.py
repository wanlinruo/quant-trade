"""固定窗口送配快照：完整性验收后仅追加，既有事件冲突时整批拒绝。"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from time import monotonic

import polars as pl
import pyarrow.parquet as pq

from qsys.data import schema
from qsys.data.pipeline.dividends import DIVIDEND_SCHEMA
from qsys.data.providers.tushare_client import TushareClient
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


_CASH_FIELDS = ("ts_code", "ann_date", "div_proc", "cash_div_tax", "ex_date")


def prepare_cash_source(work_root: Path, *, cash_source: str,
                        cash_client: TushareClient | None, run_id: str) -> None:
    """策略绑定独立于成功导入收据；兼容基线在任何外部调用前拒绝静默换回。"""
    if cash_source not in {"qmt", "tushare"} or (cash_source == "tushare" and cash_client is None):
        raise ValueError("explicit supported dividend cash source and client required")
    root = work_root / "dividends"
    policy = root / "cash-source-policy.json"
    if policy.exists():
        record = json.loads(policy.read_text(encoding="utf-8"))
        if (record.get("protocol") != "qsys-dividend-cash-policy-v1"
                or record.get("cash_source") != "tushare"):
            raise ValueError("invalid dividend cash policy binding")
        if cash_source != "tushare":
            raise ValueError("dividend cash source cannot silently revert to QMT")
    checkpoint = root / "last-imported.json"
    if checkpoint.exists():
        imported = json.loads(checkpoint.read_text(encoding="utf-8"))
        if imported.get("protocol") != "qsys-dividend-import-v1":
            raise ValueError("invalid dividend checkpoint")
        # 已发布v1旧协议固定使用QMT；这是版本契约，不以缺失字段猜市场数据。
        previous_source = imported.get("cash_source", "qmt")
        if previous_source not in {"qmt", "tushare"}:
            raise ValueError("invalid dividend cash checkpoint")
        if previous_source == "tushare" and cash_source != "tushare":
            raise ValueError("dividend cash source cannot silently revert to QMT")
    if cash_source == "tushare" and not policy.exists():
        root.mkdir(parents=True, exist_ok=True)
        with policy.open("x", encoding="utf-8") as handle:
            json.dump({"protocol": "qsys-dividend-cash-policy-v1", "cash_source": "tushare",
                       "requested_at": datetime.now(timezone.utc).isoformat(), "run_id": run_id}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        for directory in (root, root.parent):
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)


def select_tushare_cash(fresh: pl.DataFrame, *, client: TushareClient,
                        audit_dir: Path, run_id: str, qmt_sha256: str) -> pl.DataFrame:
    """独立保存现金直取收据；缺证据候选保留null，质量拒绝前不触碰仓库。"""
    if fresh.select("symbol", "ex_date").is_duplicated().any():
        raise ValueError("duplicate incoming dividend event")
    rows = fresh.to_dicts()
    selected = [None] * len(rows)
    issues = []
    receipts = audit_dir / "cash-source.jsonl"
    with receipts.open("x", encoding="utf-8") as handle:
        def record(value: dict) -> None:
            handle.write(json.dumps({"observed_at": datetime.now(timezone.utc).isoformat(), **value},
                                    ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

        record({"protocol": "qsys-dividend-cash-v1", "run_id": run_id,
                "source": "tushare.dividend.cash_div_tax", "qmt_sha256": qmt_sha256,
                "validation_scope": "cash_selection_only"})
        deadline = monotonic() + 120
        for index, row in enumerate(rows):
            symbol, stamp = row["symbol"], row["ex_date"].strftime("%Y%m%d")
            if monotonic() >= deadline:
                record({"symbol": symbol, "ex_date": stamp, "error": "cash_source_deadline_exceeded"})
                issues.append("cash_source_deadline_exceeded")
                break
            try:
                response = client.call("dividend", {"ts_code": symbol, "ex_date": stamp},
                                       ",".join(_CASH_FIELDS))
                raw = [{key: value.get(key) for key in _CASH_FIELDS} for value in response]
            except Exception as exc:
                # 私有配置/HTTP异常可能包含输入或凭据，只保留类型，禁止外传原消息。
                record({"symbol": symbol, "ex_date": stamp,
                        "error_type": type(exc).__name__})
                issues.append("cash_source_request_failed")
                break
            # 非有限数用带类型的审计表示保留，不能生成非标准JSON或写成业务0。
            audited = [{key: {"nonfinite_float": repr(value)}
                        if type(value) is float and not math.isfinite(value) else value
                        for key, value in values.items()} for values in raw]
            record({"symbol": symbol, "ex_date": stamp, "rows": audited})
            matches = [value for value in raw if value["ts_code"] == symbol
                       and value["ex_date"] == stamp and value["div_proc"] == "实施"]
            if len(matches) != 1:
                issues.append("cash_implementation_missing_or_ambiguous")
                continue
            cash = matches[0]["cash_div_tax"]
            if cash is None:
                issues.append("cash_value_missing")
            else:
                try:
                    valid = (type(cash) in (int, float) and math.isfinite(cash)
                             and cash >= 0 and float(cash) == cash)
                except OverflowError:
                    valid = False
                if valid:
                    selected[index] = cash
                else:
                    issues.append("cash_value_invalid")
        candidate = fresh.with_columns(pl.Series("cash_per_share", selected, dtype=pl.Float64))
        with (audit_dir / "cash-selected.parquet").open("xb") as output:
            pq.write_table(candidate.to_arrow().cast(schema.CORPORATE_ACTIONS), output)
            output.flush()
            os.fsync(output.fileno())
        record({"status": "rejected" if issues else "accepted", "rows": candidate.height,
                "null_cash_cells": candidate["cash_per_share"].null_count(),
                "issues": issues})
    if issues:
        raise ValueError("dividend cash quality rejected; inspect cash-source.jsonl")
    return candidate


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
                   script: Path, run_id: str, cash_source: str = "qmt",
                   cash_client: TushareClient | None = None) -> int:
    """调用方持实际warehouse写锁；不重发run，不自动恢复未知送达请求。"""
    root = work_root / "dividends"
    checkpoint = root / "last-imported.json"
    prepare_cash_source(work_root, cash_source=cash_source, cash_client=cash_client, run_id=run_id)
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
              "cash_source": cash_source,
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
    cash_evidence = {}
    if cash_source == "tushare":
        fresh = select_tushare_cash(fresh, client=cash_client, audit_dir=work,
                                    run_id=run_id, qmt_sha256=_sha(destination / "events.parquet"))
        cash_evidence = {"cash_receipts_sha256": _sha(work / "cash-source.jsonl"),
                         "cash_selected_sha256": _sha(work / "cash-selected.parquet")}
    rows = append_events(warehouse, fresh)
    record = {**intent, "protocol": "qsys-dividend-import-v1",
              "initial_start": initial_start.isoformat(), "added": rows,
              "cash_source": cash_source, **cash_evidence,
              "bundle_sha256": _sha(destination / "events.parquet")}
    temporary = root / f".last-imported-{run_id}.tmp"
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(record, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, checkpoint)
    return rows
