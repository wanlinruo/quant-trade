"""可重复的数据资产基线盘点。

只读取 schema、源码和 Parquet metadata；不载入全量数据，也不写 warehouse。
输出可安全提交的结构与统计，不包含数据行或密钥。
"""

from __future__ import annotations

import ast
import hashlib
import json
import platform
import subprocess
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from qsys.data import schema
from qsys.data.catalog import DATASETS, DEPENDENCIES, REGISTRY


@dataclass(frozen=True, slots=True)
class WarehouseDatasetStats:
    dataset: str
    files: int
    rows: int
    bytes: int
    min_time: str | None
    max_time: str | None
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceReference:
    path: str
    line: int
    kind: str
    name: str
    datasets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WarehouseDirectoryCoverage:
    directories: tuple[str, ...]
    registered: tuple[str, ...]
    auxiliary: tuple[str, ...]
    unregistered: tuple[str, ...]


_PARQUET_READERS = {"read_parquet", "scan_parquet", "ParquetFile", "read_schema"}

# warehouse 根目录下这些名字是运维证据或中间产物，不是业务数据集。
# 回滚快照统一使用 ``<dataset>.pre-<change>``，按命名规则识别。
_AUXILIARY_WAREHOUSE_DIRECTORIES = frozenset({
    "backups",
    "paid_adjustment_bases",
    "paid_daily_quality",
    "paid_daily_repairs",
    "paid_minute_quality",
    "paid_minute_quarantine",
})


def warehouse_directory_coverage(root: Path) -> WarehouseDirectoryCoverage:
    """反向盘点 warehouse 顶层目录，暴露未登记的物理资产。"""
    root = Path(root)
    directories = tuple(sorted(path.name for path in root.iterdir() if path.is_dir())) \
        if root.is_dir() else ()
    registered_ids = set(schema.ALL) | set(schema.READ_ONLY)
    registered = tuple(name for name in directories if name in registered_ids)
    auxiliary = tuple(
        name for name in directories
        if name in _AUXILIARY_WAREHOUSE_DIRECTORIES or ".pre-" in name
    )
    unregistered = tuple(
        name for name in directories if name not in registered_ids and name not in auxiliary
    )
    return WarehouseDirectoryCoverage(directories, registered, auxiliary, unregistered)


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    if hasattr(value, "item"):
        return _json_value(value.item())
    return value


def _time_bounds(path: Path, columns: tuple[str, ...]) -> tuple[list[Any], list[str]]:
    values: list[Any] = []
    errors: list[str] = []
    try:
        parquet = pq.ParquetFile(path)
        names = parquet.schema_arrow.names
        indices = [names.index(name) for name in columns if name in names]
        for group in range(parquet.metadata.num_row_groups):
            row_group = parquet.metadata.row_group(group)
            for index in indices:
                stats = row_group.column(index).statistics
                if stats is not None and stats.has_min_max:
                    values.extend((_json_value(stats.min), _json_value(stats.max)))
    except Exception as exc:  # 损坏文件也是盘点结论，不能让整份报告消失
        errors.append(f"{path}: {type(exc).__name__}: {exc}")
    return values, errors


def warehouse_stats(root: Path, dataset: str) -> WarehouseDatasetStats:
    """只读 footer 统计；不扫描数据列。"""
    base = Path(root) / dataset
    files = sorted(base.rglob("*.parquet")) if base.is_dir() else []
    rows = 0
    size = 0
    bounds: list[Any] = []
    errors: list[str] = []
    contract = DATASETS[dataset]
    for path in files:
        size += path.stat().st_size
        try:
            rows += pq.ParquetFile(path).metadata.num_rows
        except Exception as exc:
            errors.append(f"{path}: {type(exc).__name__}: {exc}")
            continue
        values, failures = _time_bounds(path, contract.time_columns)
        bounds.extend(value for value in values if value is not None)
        errors.extend(failures)
    comparable = [str(value) for value in bounds]
    return WarehouseDatasetStats(
        dataset, len(files), rows, size,
        min(comparable) if comparable else None,
        max(comparable) if comparable else None,
        tuple(errors),
    )


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _string_constants(node: ast.AST) -> set[str]:
    return {
        child.value for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }


def _mentioned_datasets(node: ast.AST) -> tuple[str, ...]:
    strings = _string_constants(node)
    found = {
        ident for ident in DATASETS
        if any(ident in value for value in strings)
    }
    return tuple(sorted(found))


def _without_docstring(node: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.Module:
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return ast.Module(body=body, type_ignores=[])


def query_method_inventory(query_file: Path) -> tuple[SourceReference, ...]:
    """列出 WarehouseQuery 每个公开方法及递归解析到的表。"""
    tree = ast.parse(query_file.read_text(encoding="utf-8"))
    cls = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "WarehouseQuery"
    )
    methods = {
        node.name: node for node in cls.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    direct: dict[str, set[str]] = {}
    calls: dict[str, set[str]] = {}
    for name, node in methods.items():
        code = _without_docstring(node)
        direct[name] = set(_mentioned_datasets(code))
        calls[name] = {
            call.func.attr
            for call in ast.walk(code)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "self"
            and call.func.attr in methods
        }

    def resolve(name: str, seen: frozenset[str] = frozenset()) -> set[str]:
        if name in seen:
            return set()
        result = set(direct[name])
        for called in calls[name]:
            result.update(resolve(called, seen | {name}))
        return result

    refs = []
    display_path = str(query_file)
    if "src" in query_file.parts:
        display_path = str(Path(*query_file.parts[query_file.parts.index("src"):]))
    for name, node in methods.items():
        if name.startswith("_"):
            continue
        datasets = tuple(sorted(resolve(name)))
        # dataset_bounds(dataset) 是刻意的通用入口，不应伪造一个固定表归属。
        if name == "dataset_bounds":
            datasets = ("*",)
        refs.append(SourceReference(
            display_path, node.lineno, "query_method", name, datasets
        ))
    return tuple(refs)


def source_references(
    project_root: Path,
    query_methods: tuple[SourceReference, ...] = (),
) -> tuple[SourceReference, ...]:
    """查找仓库写入和绕过 WarehouseQuery 的 Parquet 读取点。"""
    refs: list[SourceReference] = []
    method_datasets = {item.name: item.datasets for item in query_methods}
    for folder in ("src", "scripts"):
        base = Path(project_root) / folder
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = _call_name(node)
                if name in _PARQUET_READERS:
                    refs.append(SourceReference(
                        str(path.relative_to(project_root)), node.lineno,
                        "direct_parquet_read", name, _mentioned_datasets(node),
                    ))
                elif name in {"write", "write_day"} and node.args:
                    first = node.args[0]
                    datasets = _mentioned_datasets(first)
                    if datasets:
                        refs.append(SourceReference(
                            str(path.relative_to(project_root)), node.lineno,
                            "warehouse_write", name, datasets,
                        ))
                elif name in method_datasets and isinstance(node.func, ast.Attribute):
                    refs.append(SourceReference(
                        str(path.relative_to(project_root)), node.lineno,
                        "query_call", name, method_datasets[name],
                    ))
    return tuple(refs)


def _git_sha(root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True,
            text=True, capture_output=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def build_inventory(
    project_root: Path,
    warehouse_root: Path,
    *,
    test_result: str | None = None,
) -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    warehouse_root = Path(warehouse_root).resolve()
    try:
        warehouse_display = str(warehouse_root.relative_to(project_root))
    except ValueError:
        warehouse_display = str(warehouse_root)
    physical_ids = sorted(set(schema.ALL) | set(schema.READ_ONLY))
    directory_coverage = warehouse_directory_coverage(warehouse_root)
    stats = [warehouse_stats(warehouse_root, ident) for ident in physical_ids]
    query_refs = query_method_inventory(project_root / "src/qsys/data/query.py")
    source_refs = source_references(project_root, query_refs)
    critical = {
        item.dataset: asdict(item) for item in stats
        if item.dataset in {
            "tushare_daily", "minute_bars", "paid_minute_bars",
            "adj_factors", "daily_flags", "market_regime",
        }
    }
    return {
        "format_version": 1,
        "baseline": {
            "git_sha": _git_sha(project_root),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "uv_lock_sha256": _sha256(project_root / "uv.lock"),
            "warehouse": warehouse_display,
            "test_result": test_result,
        },
        "coverage": {
            "schema_all": sorted(schema.ALL),
            "schema_read_only": sorted(schema.READ_ONLY),
            "metric_datasets": sorted({m.dataset for m in REGISTRY.values()}),
            "registered_physical": physical_ids,
            "warehouse_directories": list(directory_coverage.directories),
            "registered_directories": list(directory_coverage.registered),
            "auxiliary_directories": list(directory_coverage.auxiliary),
            "unregistered_physical": list(directory_coverage.unregistered),
            "registered_logical": sorted(
                ident for ident, item in DATASETS.items() if not item.physical
            ),
        },
        "datasets": [
            {**asdict(DATASETS[ident]), "columns": list((schema.ALL | schema.READ_ONLY)[ident].names),
             "warehouse": asdict(stats[physical_ids.index(ident)])}
            for ident in physical_ids
        ],
        "dependencies": [asdict(item) for item in DEPENDENCIES],
        "query_methods": [asdict(item) for item in query_refs],
        "source_references": [asdict(item) for item in source_refs],
        "critical_summaries": critical,
    }


def write_inventory(report: dict[str, Any], output: Path) -> None:
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
