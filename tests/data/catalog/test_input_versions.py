from datetime import date, datetime, timedelta
import json
import os

import polars as pl
import pyarrow.parquet as pq
import pytest

from qsys.data import schema
from qsys.data.catalog.health import (
    ALL_DIMENSIONS, HealthEvidence, HealthReason, HealthStatus,
    dataset_check_frame, write_dataset_checks,
)
from qsys.data.catalog.input_versions import (
    InputVersionChanged, bind_inputs, binding_status, current_result,
    dataset_binding, require_unchanged,
)
from qsys.data.query import WarehouseQuery
from qsys.web.asset_views import DataAssetService

DAY = date(2026, 10, 28)
NOW = datetime(2026, 10, 28, 21)


def _frame(warehouse, *, bound=True, status=HealthStatus.GREEN, now=NOW):
    evidence = tuple(HealthEvidence(
        dimension, status, 'test.'+dimension.value, now, now+timedelta(days=1),
        HealthReason('test', 'original evidence', dimension), checked_rows=1,
    ) for dimension in ALL_DIMENSIONS)
    return dataset_check_frame('run-'+now.isoformat(), now, 'valuation_daily', evidence,
        scope_start=DAY, scope_end=DAY,
        input_version=dataset_binding(warehouse, 'valuation_daily', DAY, DAY) if bound else None)


def test_replacing_same_values_invalidates_source_and_preserves_history(warehouse):
    warehouse.write('tushare_daily', schema.TUSHARE_DAILY.empty_table(), year=DAY.year)
    old = _frame(warehouse, status=HealthStatus.RED)
    write_dataset_checks(warehouse, old)
    service = DataAssetService(warehouse)
    assert service._assessment('valuation_daily', NOW).status is HealthStatus.RED
    warehouse.write('tushare_daily', schema.TUSHARE_DAILY.empty_table(), year=DAY.year)
    assert service._assessment('valuation_daily', NOW).status is HealthStatus.GRAY
    assert WarehouseQuery(warehouse).dataset_checks().equals(old)
    # New real evidence may pass; old red records remain exactly once each.
    new = _frame(warehouse, now=NOW+timedelta(minutes=1))
    write_dataset_checks(warehouse, new)
    assert service._assessment('valuation_daily', NOW+timedelta(minutes=2)).status is HealthStatus.GREEN
    stored = WarehouseQuery(warehouse).dataset_checks()
    assert stored.filter(pl.col('run_id')==old['run_id'][0]).equals(old)


@pytest.mark.parametrize('dataset', ['valuation_daily', 'tushare_daily'])
def test_target_or_source_addition_and_removal_invalidates(warehouse, dataset):
    binding = dataset_binding(warehouse, 'valuation_daily', DAY, DAY)
    path = warehouse.write(dataset, schema.ALL[dataset].empty_table(), year=DAY.year)
    assert binding_status(warehouse, binding) is not None
    binding = dataset_binding(warehouse, 'valuation_daily', DAY, DAY)
    path.unlink()
    assert binding_status(warehouse, binding) is not None


def test_unrelated_dataset_and_quality_appends_do_not_invalidate(warehouse):
    frame = _frame(warehouse)
    warehouse.write('daily_flags', schema.DAILY_FLAGS.empty_table(), year=DAY.year)
    write_dataset_checks(warehouse, frame)
    assert binding_status(warehouse, frame['input_version'][0]) is None
    assert DataAssetService(warehouse)._assessment('valuation_daily', NOW).status is HealthStatus.GREEN
    assert DataAssetService(warehouse)._assessment('valuation_daily', NOW+timedelta(days=2)).status is HealthStatus.GRAY


def test_legacy_schema_remains_readable_and_old_rows_are_not_upgraded(warehouse):
    old = _frame(warehouse, bound=False).drop('input_version')
    path = warehouse.path_for('dataset_checks', DAY.year)
    path.parent.mkdir(parents=True)
    pq.write_table(old.to_arrow(), path)
    assert WarehouseQuery(warehouse).dataset_checks()['input_version'].null_count()==old.height
    assert DataAssetService(warehouse)._assessment('valuation_daily', NOW).status is HealthStatus.GRAY
    write_dataset_checks(warehouse, _frame(warehouse, now=NOW+timedelta(minutes=1)))
    stored = WarehouseQuery(warehouse).dataset_checks()
    assert stored.filter(pl.col('run_id')==old['run_id'][0]).drop('input_version').equals(old)
    # An actual old-schema SELECT still sees every original field and row.
    query = WarehouseQuery(warehouse)
    from qsys.data.query import _projection, _read
    old_schema = schema.DATASET_CHECKS.remove(schema.DATASET_CHECKS.get_field_index('input_version'))
    selection = _projection(query._con, str(path), old_schema)
    assert query._con.execute(f'SELECT {selection} FROM {_read(str(path))}').pl().height==stored.height


def test_qmt_daily_partition_shadowing_matches_real_reader(warehouse):
    year = warehouse.write('minute_bars', schema.MINUTE_BARS.empty_table(), year=DAY.year)
    binding = bind_inputs(warehouse, ('minute_bars',), DAY, DAY)
    warehouse.write_day('minute_bars', schema.MINUTE_BARS.empty_table(), day=DAY)
    assert binding_status(warehouse, binding) is not None
    binding = bind_inputs(warehouse, ('minute_bars',), DAY, DAY)
    # The annual file is now shadowed and cannot invalidate this read.
    os.utime(year, ns=(year.stat().st_atime_ns, year.stat().st_mtime_ns+1000))
    assert binding_status(warehouse, binding) is None
    warehouse.write_day('minute_bars', schema.MINUTE_BARS.empty_table(), day=DAY-timedelta(days=1))
    assert binding_status(warehouse, binding) is None


@pytest.mark.parametrize('value', [None, '{}', '{bad', '{"version":2}',
    '{"version":1,"datasets":["../config"],"start":"2026-10-28","end":"2026-10-28"}'])
def test_unknown_legacy_or_malformed_bindings_fail_closed(warehouse, value):
    assert binding_status(warehouse, value) is not None


def test_wrong_object_or_scope_cannot_reuse_a_valid_digest(warehouse):
    row = _frame(warehouse).row(0, named=True)
    row['scope_end'] = DAY+timedelta(days=1)
    assert current_result(warehouse, row)['status']=='gray'
    row = _frame(warehouse).row(0, named=True)
    row['input_version']=bind_inputs(warehouse, ('valuation_daily',), DAY, DAY)
    assert current_result(warehouse, row)['status']=='gray'


def test_relation_binds_both_sides(warehouse):
    value = bind_inputs(warehouse, ('bar_daily', 'security_master'), DAY, DAY,
                        freshness=('bar_daily', 'security_master'))
    row = {'upstream':'security_master','downstream':'bar_daily',
           'scope_start':DAY,'scope_end':DAY,'input_version':value,'status':'green'}
    assert current_result(warehouse,row)['status']=='green'
    warehouse.write('security_master',schema.SECURITY_MASTER.empty_table())
    assert current_result(warehouse,row)['status']=='gray'


def test_input_drift_during_actual_verifier_publishes_no_table_results(warehouse, monkeypatch):
    import qsys.data.cutover_health as module
    from qsys.data.pipeline.canonical_reference import normalize_trading_calendar
    raw=pl.DataFrame({'cal_date':['20261028'],'is_open':[1],'pretrade_date':['20261027']})
    warehouse.write('trading_calendar',normalize_trading_calendar(raw).to_arrow().cast(schema.TRADING_CALENDAR),year=DAY.year)
    original=module.daily_reconciliation
    def drift(*args,**kwargs):
        result=original(*args,**kwargs)
        warehouse.write('tushare_daily',schema.TUSHARE_DAILY.empty_table(),year=DAY.year)
        return result
    monkeypatch.setattr(module,'daily_reconciliation',drift)
    with pytest.raises(InputVersionChanged):
        module.generate_cutover_health(warehouse,DAY,DAY,raw,now=NOW)
    assert WarehouseQuery(warehouse).dataset_checks().height==0
    assert WarehouseQuery(warehouse).relation_checks().height==0
