from datetime import date
import json
import shutil

import pandas as pd
import polars as pl
import pyarrow.parquet as pq
import pytest

from scripts.win_download_dividends import export, bundle_snapshot
from qsys.data import schema
from qsys.data.pipeline.dividend_sync import (
    append_events, dividend_universe, import_window, sync_dividends, validate_snapshot,
)
from qsys.data.query import WarehouseQuery


DAY = date(2026, 10, 28)
SYMBOLS = ["000001.SZ", "600017.SH"]


def source(symbol, **_):
    if symbol == SYMBOLS[0]:
        return pd.DataFrame()
    return pd.DataFrame([dict(interest=.033, stockBonus=.1, stockGift=.2,
                             allotNum=0., allotPrice=0., dr=1.01)], index=["20261028"])


def make_bundle(tmp_path, fetch=source, run_id="sync-1"):
    snapshot = tmp_path / "snapshot"
    export(SYMBOLS, snapshot, DAY, DAY, fetch)
    bundle = tmp_path / "bundle"
    bundle_snapshot(snapshot, bundle, SYMBOLS, DAY, DAY, run_id)
    return bundle


def validate(bundle):
    return validate_snapshot(bundle, symbols=SYMBOLS, start=DAY, end=DAY, run_id="sync-1")


def test_complete_bundle_preserves_raw_arithmetic_and_observed_empty(tmp_path):
    bundle = make_bundle(tmp_path)
    frame = validate(bundle)
    assert frame.height == 1
    assert frame["stock_per_share"][0] == .1 + .2
    result = json.loads((bundle / "result.json").read_text())
    assert [r["status"] for r in result["receipts"]] == ["empty", "done"]
    assert {p.name for p in bundle.iterdir()} == {"events.parquet", "result.json"}


def test_export_and_bundle_fsync_use_writable_handles(tmp_path, monkeypatch):
    import os
    original = os.fsync
    def windows_flush(fd):
        # A zero-byte write changes no bytes, but rejects read-only handles on
        # POSIX too, matching the Windows FlushFileBuffers access requirement.
        os.write(fd, b"")
        original(fd)
    monkeypatch.setattr(os, "fsync", windows_flush)
    assert validate(make_bundle(tmp_path)).height == 1


@pytest.mark.parametrize("mutation", ["coverage", "run", "empty", "failed", "rows", "window"])
def test_incomplete_or_misbound_bundle_is_rejected(tmp_path, mutation):
    bundle = make_bundle(tmp_path)
    path = bundle / "result.json"
    result = json.loads(path.read_text())
    if mutation == "coverage":
        result["receipts"].pop()
    elif mutation == "run":
        result["run_id"] = "another-run"
    elif mutation == "empty":
        result["receipts"][1]["status"] = "empty"
    elif mutation == "failed":
        result["receipts"][0]["status"] = "failed"
    elif mutation == "rows":
        result["rows"] += 1
    else:
        result["start"] = "20261027"
    path.write_text(json.dumps(result))
    with pytest.raises(ValueError):
        validate(bundle)


def test_incomplete_source_cannot_be_bundled(tmp_path):
    snapshot = tmp_path / "snapshot"
    export(SYMBOLS, snapshot, DAY, DAY, lambda *_a, **_kw: None)
    with pytest.raises(ValueError, match="incomplete"):
        bundle_snapshot(snapshot, tmp_path / "bundle", SYMBOLS, DAY, DAY, "sync-1")
    assert not (tmp_path / "bundle").exists()


def test_changed_bytes_are_rejected_at_source_and_receiver(tmp_path):
    bundle = make_bundle(tmp_path)
    (bundle / "events.parquet").write_bytes(b"changed")
    with pytest.raises(ValueError, match="digest"):
        validate(bundle)
    (tmp_path / "snapshot" / (SYMBOLS[1] + ".parquet")).write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        bundle_snapshot(tmp_path / "snapshot", tmp_path / "another", SYMBOLS, DAY, DAY, "sync-2")


def test_append_preserves_old_rows_and_refuses_entire_conflicting_batch(warehouse, tmp_path):
    fresh = validate(make_bundle(tmp_path))
    assert append_events(warehouse, fresh) == 1
    path = warehouse.path_for("corporate_actions")
    before = path.read_bytes()
    # The Warehouse layout is checked through actual query values as well as bytes.
    old = WarehouseQuery(warehouse).corporate_actions()
    assert append_events(warehouse, fresh) == 0
    assert path.read_bytes() == before
    conflict = pl.concat([
        fresh.with_columns(pl.lit(99.).alias("cash_per_share")),
        fresh.with_columns(pl.lit(SYMBOLS[0]).alias("symbol")),
    ])
    with pytest.raises(ValueError, match="conflicts"):
        append_events(warehouse, conflict)
    assert WarehouseQuery(warehouse).corporate_actions().equals(old)
    assert path.read_bytes() == before


def test_empty_response_does_not_delete_old_events(warehouse, tmp_path):
    fresh = validate(make_bundle(tmp_path))
    append_events(warehouse, fresh)
    assert append_events(warehouse, fresh.head(0)) == 0
    assert WarehouseQuery(warehouse).corporate_actions().equals(fresh)


def test_window_tracks_successful_response_not_last_event(tmp_path):
    checkpoint = tmp_path / "last-imported.json"
    start = date(2026, 10, 1)
    assert import_window(DAY, start, checkpoint) == start
    checkpoint.write_text(json.dumps({"protocol": "qsys-dividend-import-v1",
                                     "initial_start": start.isoformat(), "end": "2026-10-25"}))
    assert import_window(DAY, start, checkpoint) == date(2026, 10, 18)
    with pytest.raises(ValueError, match="backwards"):
        import_window(date(2026, 10, 24), start, checkpoint)
    with pytest.raises(ValueError, match="scope changed"):
        import_window(DAY, date(2026, 10, 2), checkpoint)
    with pytest.raises(ValueError, match="exceeds"):
        import_window(date(2026, 12, 1), start, checkpoint)


def test_native_scope_keeps_native_and_unknown_history_without_alias_mapping():
    import pyarrow as pa
    instruments = pl.from_arrow(pa.Table.from_pylist([
        {"symbol": "T600018.SH", "delist_date": date(2006, 10, 20), "as_of": DAY},
        {"symbol": "600018.SH", "delist_date": None, "as_of": DAY},
        {"symbol": "000001.SZ", "delist_date": date(2026, 10, 27), "as_of": DAY},
    ], schema=schema.INSTRUMENTS))
    actions = pl.DataFrame({"symbol": ["600017.SH", "T600018.SH"]})
    before = actions.clone()
    symbols, excluded = dividend_universe(instruments, actions, DAY)
    assert symbols == ["000001.SZ", "600017.SH", "600018.SH"]
    assert excluded == [{"symbol": "T600018.SH", "delist_date": "2006-10-20", "as_of": "2026-10-28"}]
    assert actions.equals(before)


@pytest.mark.parametrize("delisted", [None, DAY, date(2026, 10, 29)])
def test_non_native_identity_without_past_delisting_is_not_silently_dropped(delisted):
    import pyarrow as pa
    instruments = pl.from_arrow(pa.Table.from_pylist([
        {"symbol": "T600018.SH", "delist_date": delisted, "as_of": DAY},
        {"symbol": "600018.SH", "as_of": DAY},
    ], schema=schema.INSTRUMENTS))
    with pytest.raises(ValueError, match="explicit source verification"):
        dividend_universe(instruments, pl.DataFrame({"symbol": ["600017.SH"]}), DAY)


def test_real_export_bundle_import_checkpoint_and_unknown_delivery(warehouse, tmp_path):
    # Include a symbol absent from today's master via the existing event table.
    fresh = validate(make_bundle(tmp_path / "seed"))
    append_events(warehouse, fresh)
    instruments = pl.from_arrow(schema.INSTRUMENTS.empty_table())
    instruments = pl.DataFrame({"symbol": [SYMBOLS[0]]}).join(instruments, on="symbol", how="left")
    warehouse.write("instruments", instruments.to_arrow().cast(schema.INSTRUMENTS))
    script = tmp_path / "approved.py"
    script.write_text("# approved test script")
    import hashlib
    calls = []

    class Transport:
        fail = False

        def probe(self):
            return {"scripts": {"dividend": hashlib.sha256(script.read_bytes()).hexdigest()}}

        def prepare(self, job, manifest):
            calls.append(("prepare", job))
            assert manifest.read_text().splitlines()[1:] == [s + ",2026-10-28" for s in SYMBOLS]

        def run_snapshot(self, job, script, run_id, start, end, *, timeout):
            calls.append(("run", job))
            assert timeout == 900
            if self.fail:
                raise TimeoutError("unknown delivery")
            out = tmp_path / run_id
            export(SYMBOLS, out / "snapshot", start, end, source)
            self.bundle = out / "bundle"
            bundle_snapshot(out / "snapshot", self.bundle, SYMBOLS, start, end, run_id)

        def receive(self, job, destination):
            shutil.copytree(self.bundle, destination)

    transport = Transport()
    work = tmp_path / "work"
    assert sync_dividends(warehouse, DAY, initial_start=DAY, work_root=work,
                          transport=transport, script=script, run_id="sync-1") == 0
    checkpoint = work / "dividends" / "last-imported.json"
    before = checkpoint.read_bytes()
    assert json.loads(before)["scope_sha256"]
    transport.fail = True
    with pytest.raises(TimeoutError):
        sync_dividends(warehouse, DAY, initial_start=DAY, work_root=work,
                       transport=transport, script=script, run_id="sync-2")
    assert checkpoint.read_bytes() == before
    assert len([c for c in calls if c[0] == "run"]) == 2
    assert (work / "dividends" / "dividend_20261028_sync-2" / "requested.json").exists()
    with pytest.raises(FileExistsError):
        sync_dividends(warehouse, DAY, initial_start=DAY, work_root=work,
                       transport=transport, script=script, run_id="sync-2")
    assert len([c for c in calls if c[0] == "run"]) == 2
