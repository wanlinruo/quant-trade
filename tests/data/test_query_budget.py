"""Real DuckDB probe: scoped memory/temp/spill/thread config is effective."""

from pathlib import Path

import duckdb
import pytest

from qsys.data.query import WarehouseQuery
from qsys.data.query_budget import query_budget
from qsys.data.storage import Warehouse


def test_research_budget_is_applied_per_connection_and_closed_on_exit(tmp_path):
    outer = WarehouseQuery(Warehouse(tmp_path))
    spill = tmp_path / "job" / "duckdb-temp"
    with query_budget(memory_limit_mb=64, temp_root=spill,
                      max_temp_directory_mb=1024, threads=1):
        first = WarehouseQuery(Warehouse(tmp_path))
        second = WarehouseQuery(Warehouse(tmp_path))
        values = first._con.execute("select current_setting('memory_limit'), "
            "current_setting('temp_directory'), current_setting('max_temp_directory_size'), "
            "current_setting('threads')").fetchone()
        assert values[0] == "64.0 MiB"
        assert Path(values[1]).parent == spill.resolve()
        assert values[2:] == ("1.0 GiB", 1)
        other = second._con.execute("select current_setting('temp_directory')").fetchone()[0]
        assert other != values[1]
    with pytest.raises(duckdb.ConnectionException):
        first._con.execute("select 1")
    assert outer._con.execute("select 1").fetchone() == (1,)
    outer._con.close()


def test_budget_closes_connections_after_failure_and_rejects_invalid_limits(tmp_path):
    with pytest.raises(RuntimeError, match="study failed"):
        with query_budget(memory_limit_mb=64, temp_root=tmp_path / "temp",
                          max_temp_directory_mb=1024, threads=1):
            query = WarehouseQuery(Warehouse(tmp_path))
            raise RuntimeError("study failed")
    with pytest.raises(duckdb.ConnectionException):
        query._con.execute("select 1")
    with pytest.raises(ValueError, match="positive"):
        with query_budget(memory_limit_mb=0, temp_root=tmp_path,
                          max_temp_directory_mb=1024, threads=1):
            pass


def test_component_environment_limits_are_effective_and_missing_values_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("QSYS_QUERY_TEMP_ROOT", str(tmp_path / "web-temp"))
    monkeypatch.setenv("QSYS_QUERY_MEMORY_MB", "64")
    monkeypatch.setenv("QSYS_QUERY_MAX_TEMP_MB", "128")
    monkeypatch.setenv("QSYS_QUERY_THREADS", "1")
    query = WarehouseQuery(Warehouse(tmp_path))
    try:
        values = query._con.execute("select current_setting('memory_limit'), "
            "current_setting('temp_directory'), current_setting('max_temp_directory_size'), "
            "current_setting('threads')").fetchone()
        assert values[0] == "64.0 MiB"
        assert Path(values[1]).parent == tmp_path / "web-temp"
        assert values[2:] == ("128.0 MiB", 1)
    finally:
        query._con.close()
    monkeypatch.delenv("QSYS_QUERY_MEMORY_MB")
    with pytest.raises(KeyError):
        WarehouseQuery(Warehouse(tmp_path))
