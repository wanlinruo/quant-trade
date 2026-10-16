"""Strict loader for the trusted component release whitelist."""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

SPEC_PATH = Path("deploy/component-releases.json")
COMPONENT = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
MODE = re.compile(r"^0[0-7]{3}$")
COMPONENT_FIELDS = frozenset({
    "entrypoint", "scan_roots", "release_root", "current", "executable",
    "unit", "dropin", "state_directories", "seed", "dropin_lines",
})


def _relative(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"component {field} must be a non-empty relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise ValueError(f"component {field} must be a normalized relative path")
    return value


def _absolute(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ValueError(f"component {field} must be an absolute path")
    if ".." in Path(value).parts:
        raise ValueError(f"component {field} must be normalized")
    return value


def _identity(value: object, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", value):
        raise ValueError(f"component {field} is invalid")
    return value


def _mode(value: object, field: str) -> str:
    if not isinstance(value, str) or not MODE.fullmatch(value):
        raise ValueError(f"component {field} must be a four-digit octal mode")
    return value


def _validate_component(name: str, value: object) -> dict[str, Any]:
    if not COMPONENT.fullmatch(name) or not isinstance(value, dict):
        raise ValueError("component release whitelist contains an invalid component")
    if set(value) != COMPONENT_FIELDS:
        raise ValueError(f"component release fields are invalid: {name}")
    entrypoint = value["entrypoint"]
    if not isinstance(entrypoint, dict) or set(entrypoint) != {"command", "target"}:
        raise ValueError(f"component entrypoint is invalid: {name}")
    if not all(isinstance(item, str) and item for item in entrypoint.values()):
        raise ValueError(f"component entrypoint values are invalid: {name}")
    roots = value["scan_roots"]
    if not isinstance(roots, list) or not roots:
        raise ValueError(f"component scan_roots are invalid: {name}")
    normalized_roots = [_relative(item, "scan_roots") for item in roots]
    if len(set(normalized_roots)) != len(normalized_roots):
        raise ValueError(f"component scan_roots contain duplicates: {name}")
    for field in ("release_root", "current", "dropin"):
        _absolute(value[field], field)
    _relative(value["executable"], "executable")
    if not isinstance(value["unit"], str) or not re.fullmatch(
        r"qsys-[a-z0-9-]+\.service", value["unit"]
    ):
        raise ValueError(f"component unit is invalid: {name}")
    directories = value["state_directories"]
    if not isinstance(directories, list):
        raise ValueError(f"component state_directories are invalid: {name}")
    for directory in directories:
        if not isinstance(directory, dict) or set(directory) != {
            "path", "owner", "group", "mode",
        }:
            raise ValueError(f"component state directory is invalid: {name}")
        _absolute(directory["path"], "state directory path")
        _identity(directory["owner"], "state directory owner")
        _identity(directory["group"], "state directory group")
        _mode(directory["mode"], "state directory mode")
    seed = value["seed"]
    if seed is not None:
        if not isinstance(seed, dict) or set(seed) != {
            "source", "source_environment", "target", "owner", "group", "mode",
        }:
            raise ValueError(f"component seed is invalid: {name}")
        _relative(seed["source"], "seed source")
        if not isinstance(seed["source_environment"], str) or not re.fullmatch(
            r"[A-Z][A-Z0-9_]{0,63}", seed["source_environment"]
        ):
            raise ValueError(f"component seed source_environment is invalid: {name}")
        _absolute(seed["target"], "seed target")
        _identity(seed["owner"], "seed owner")
        _identity(seed["group"], "seed group")
        _mode(seed["mode"], "seed mode")
    lines = value["dropin_lines"]
    if (
        not isinstance(lines, list) or not lines
        or any(not isinstance(line, str) or "\n" in line or "\r" in line for line in lines)
    ):
        raise ValueError(f"component dropin_lines are invalid: {name}")
    return value


def load_component_releases_file(path: Path) -> dict[str, dict[str, Any]]:
    """Load and validate one static component whitelist file."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {"schema_version", "components"}:
        raise ValueError("component release whitelist fields are invalid")
    if value["schema_version"] != 1 or not isinstance(value["components"], dict):
        raise ValueError("component release whitelist schema is invalid")
    if not value["components"]:
        raise ValueError("component release whitelist must not be empty")
    return {
        name: _validate_component(name, component)
        for name, component in value["components"].items()
    }


def load_component_releases(repo: Path) -> dict[str, dict[str, Any]]:
    """Load the repository-owned component whitelist."""
    return load_component_releases_file(repo / SPEC_PATH)
