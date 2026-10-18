"""Opt-in, scoped DuckDB budgets for isolated research workers."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
import os
from uuid import uuid4

import duckdb


@dataclass
class _Budget:
    memory_limit_mb: int
    temp_root: Path
    max_temp_directory_mb: int
    threads: int
    connections: list = field(default_factory=list)


_CURRENT: ContextVar[_Budget | None] = ContextVar("qsys_query_budget", default=None)


def connect_warehouse_query():
    budget = _CURRENT.get()
    if budget is None and os.environ.get("QSYS_QUERY_TEMP_ROOT"):
        budget = _Budget(
            memory_limit_mb=int(os.environ["QSYS_QUERY_MEMORY_MB"]),
            temp_root=Path(os.environ["QSYS_QUERY_TEMP_ROOT"]),
            max_temp_directory_mb=int(os.environ["QSYS_QUERY_MAX_TEMP_MB"]),
            threads=int(os.environ["QSYS_QUERY_THREADS"]),
        )
        if min(budget.memory_limit_mb, budget.max_temp_directory_mb, budget.threads) <= 0:
            raise ValueError("query budget limits must be positive")
    if budget is None:
        return duckdb.connect()
    # Independent directories avoid collisions across multiple live connections.
    temporary = budget.temp_root / uuid4().hex
    temporary.mkdir(parents=True)
    connection = duckdb.connect(config={
        "memory_limit": f"{budget.memory_limit_mb}MiB",
        "temp_directory": str(temporary),
        "max_temp_directory_size": f"{budget.max_temp_directory_mb}MiB",
        "threads": str(budget.threads),
    })
    budget.connections.append(connection)
    return connection


@contextmanager
def query_budget(*, memory_limit_mb: int, temp_root: Path,
                 max_temp_directory_mb: int, threads: int):
    if min(memory_limit_mb, max_temp_directory_mb, threads) <= 0:
        raise ValueError("query budget limits must be positive")
    budget = _Budget(memory_limit_mb, Path(temp_root).resolve(), max_temp_directory_mb, threads)
    token = _CURRENT.set(budget)
    try:
        yield {"memory_limit_mb": memory_limit_mb, "temp_root": str(budget.temp_root),
               "max_temp_directory_mb": max_temp_directory_mb, "threads": threads}
    finally:
        _CURRENT.reset(token)
        for connection in budget.connections:
            connection.close()
