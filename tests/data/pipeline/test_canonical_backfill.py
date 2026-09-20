from datetime import date, timedelta

from qsys.data.pipeline import canonical_backfill
from qsys.data.pipeline.canonical_backfill import (
    ManifestStore,
    backfill_state_derived,
)
from qsys.data.pipeline.canonical_reference import Reconciliation


DAY = date(2026, 9, 18)
MATCH = Reconciliation(1, 1, (), (), ())


def test_manifest_is_atomic_and_resume_verifies_target_hash(warehouse, tmp_path):
    target = warehouse.root / "bar_daily" / "year=2026" / "part.parquet"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"verified partition")
    store = ManifestStore(
        tmp_path / "state", warehouse, revision="a" * 40, minimum_free_gib=0
    )
    path = store.write(
        "daily", DAY, DAY,
        counts={"bar_daily": 1},
        reconciliations={"bar_daily": MATCH},
        files=[target],
    )
    assert path.is_file()
    assert not path.with_suffix(".json.tmp").exists()
    assert store.complete("daily", DAY, DAY)

    target.write_bytes(b"changed")
    assert not store.complete("daily", DAY, DAY)


def test_manifest_refuses_failed_reconciliation(warehouse, tmp_path):
    store = ManifestStore(
        tmp_path / "state", warehouse, revision="a" * 40, minimum_free_gib=0
    )
    mismatch = Reconciliation(1, 1, (), (), (("key",),))
    try:
        store.write(
            "daily", DAY, DAY, counts={"bar_daily": 1},
            reconciliations={"bar_daily": mismatch}, files=[],
        )
    except RuntimeError as exc:
        assert "对账失败" in str(exc)
    else:
        raise AssertionError("failed reconciliation must not produce a manifest")


def test_state_derived_builds_flags_support_before_requested_regime(
    warehouse, tmp_path, monkeypatch
):
    start = DAY
    calls = []

    class Query:
        def __init__(self, _warehouse):
            pass

        def dataset_bounds(self, dataset):
            assert dataset == "daily_flags"
            return start - timedelta(days=365), start

    def fake_family(_warehouse, _store, lo, hi, *, family, builders, resume):
        calls.append((family, lo, hi, tuple(builders)))
        return {name: 1 for name in builders}

    monkeypatch.setattr(canonical_backfill, "WarehouseQuery", Query)
    monkeypatch.setattr(canonical_backfill, "_backfill_state_family", fake_family)
    store = ManifestStore(
        tmp_path / "state", warehouse, revision="a" * 40, minimum_free_gib=0
    )

    result = backfill_state_derived(warehouse, store, start, start, resume=True)

    assert result == {"market_flags_daily": 1, "market_regime_daily": 1}
    assert calls == [
        ("state-flags", start - timedelta(days=30), start,
         ("market_flags_daily",)),
        ("state-regime", start, start, ("market_regime_daily",)),
    ]
