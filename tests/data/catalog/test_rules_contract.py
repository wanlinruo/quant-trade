from datetime import date, datetime

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from qsys.data import schema
from qsys.data.catalog import checks as C
from qsys.data.query import WarehouseQuery


def _ctx(warehouse):
    return C.CheckContext(
        WarehouseQuery(warehouse), warehouse,
        date(2026, 9, 15), date(2026, 9, 15),
    )


def _run(warehouse, check_id):
    return C.run_checks(
        _ctx(warehouse), "contract-test", datetime(2026, 9, 15, 20),
        only=[C.CHECKS[check_id]],
    ).row(0, named=True)


def test_missing_optional_dataset_is_an_explicit_na(warehouse):
    row = _run(warehouse, "C6.storage_contract.live_fills")
    assert row["passed"] is True
    assert row["checked_rows"] == 0
    assert "不适用" in row["detail"]


def test_storage_contract_accepts_current_schema(warehouse):
    warehouse.write("instruments", schema.INSTRUMENTS.empty_table())
    row = _run(warehouse, "C6.storage_contract.instruments")
    assert row["passed"] is True
    assert row["checked_rows"] == 1  # 空表仍实际检查了一个物理 schema


def test_storage_contract_rejects_wrong_physical_type(warehouse):
    path = warehouse.path_for("instruments")
    path.parent.mkdir(parents=True)
    wrong = pa.table({
        "symbol": pa.array([1], type=pa.int64()),
        "name": ["x"], "market": ["SZ"],
        "list_date": pa.array([date(2020, 1, 1)], type=pa.date32()),
        "delist_date": pa.array([None], type=pa.date32()),
        "board": ["main"],
        "as_of": pa.array([date(2026, 9, 15)], type=pa.date32()),
    })
    pq.write_table(wrong, path)
    row = _run(warehouse, "C6.storage_contract.instruments")
    assert row["passed"] is False
    assert row["violations"] == 1


def test_strategy_catalog_contract_checks_all_unlanded_metrics(warehouse):
    row = _run(warehouse, "C7.catalog_contract.strategy")
    assert row["passed"] is True
    assert row["checked_rows"] == 10


@pytest.mark.parametrize('dataset', ['dataset_checks', 'relation_checks'])
@pytest.mark.parametrize('change', ['legacy', 'missing_other', 'wrong_version_type'])
def test_only_exact_legacy_quality_metadata_omission_is_compatible(warehouse, dataset, change):
    current=schema.ALL[dataset]
    old=current.remove(current.get_field_index('input_version'))
    if change=='legacy':physical=old
    elif change=='missing_other':physical=old.remove(old.get_field_index('status'))
    else:physical=pa.schema([*old,pa.field('input_version',pa.int64())])
    path=warehouse.path_for(dataset,2026);path.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([],schema=physical),path)
    row=_run(warehouse,'C6.storage_contract.'+dataset)
    assert row['passed'] is (change=='legacy')
    assert row['checked_rows']==1
    if change!='legacy':assert row['severity']=='blocking'
