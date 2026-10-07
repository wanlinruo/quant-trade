"""Deployment contracts shared by GitHub workflows and node-side executors.

This module plans and validates deployments; it deliberately does not perform
SSH, restart services, or mutate application data. Later deployment stages use
the same manifest and record contract instead of re-implementing policy.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import tomllib
from dataclasses import dataclass, fields
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from qsys.live.config import DeployConfig, load_deploy_config

SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SENSITIVE_PARTS = (
    "account", "credential", "password", "secret", "token", "username", "webhook",
)
CONFIG_SECTIONS = frozenset({
    "schema_version", "redis", "streams", "agent", "fees", "strategy",
    "safety", "alerts", "email", "risk", "tushare", "joinquant",
})
RECORD_REQUIRED = frozenset({
    "schema_version", "run_id", "environment", "status", "actor",
    "started_at", "completed_at", "repository", "pr_number", "source_sha",
    "source_tree", "components", "before", "after", "config_fingerprints",
    "checks", "rollback_targets", "migration_summary",
})
RECORD_STATUSES = {"queued", "in_progress", "success", "failure", "rolled_back"}
CHECK_STATUSES = {"passed", "failed", "skipped"}


class ContractError(ValueError):
    """A deployment input violates the versioned contract."""


def require_full_sha(value: str, *, field: str) -> str:
    if not SHA_PATTERN.fullmatch(value):
        raise ContractError(f"{field} must be a full 40-character lowercase SHA")
    return value


@dataclass(frozen=True, slots=True)
class EnvironmentContract:
    name: str
    branch: str
    config_path: str
    record_root: str
    web_health: str


@dataclass(frozen=True, slots=True)
class Component:
    name: str
    environments: tuple[str, ...]
    paths: tuple[str, ...]
    nodes: tuple[str, ...]
    linux_units: tuple[str, ...] = ()
    windows_apps: tuple[str, ...] = ()
    windows_tasks: tuple[str, ...] = ()
    health_checks: tuple[str, ...] = ()
    rollback: str = ""
    manual_gate: bool = False


@dataclass(frozen=True, slots=True)
class DeploymentPlan:
    changed_paths: tuple[str, ...]
    ignored_paths: tuple[str, ...]
    components: tuple[str, ...]
    nodes: tuple[str, ...]
    linux_units: tuple[str, ...]
    windows_apps: tuple[str, ...]
    windows_tasks: tuple[str, ...]
    health_checks: tuple[str, ...]
    manual_gate: bool
    unknown_paths: tuple[str, ...]

    @property
    def deployable(self) -> bool:
        return bool(self.components) and not self.unknown_paths

    def as_dict(self) -> dict[str, object]:
        return {
            "changed_paths": list(self.changed_paths),
            "ignored_paths": list(self.ignored_paths),
            "components": list(self.components),
            "nodes": list(self.nodes),
            "linux_units": list(self.linux_units),
            "windows_apps": list(self.windows_apps),
            "windows_tasks": list(self.windows_tasks),
            "health_checks": list(self.health_checks),
            "manual_gate": self.manual_gate,
            "unknown_paths": list(self.unknown_paths),
            "deployable": self.deployable,
        }


@dataclass(frozen=True, slots=True)
class DeploymentManifest:
    schema_version: int
    ignored_paths: tuple[str, ...]
    environments: dict[str, EnvironmentContract]
    components: tuple[Component, ...]

    @classmethod
    def load(cls, path: str | Path) -> "DeploymentManifest":
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        if raw.get("schema_version") != 1:
            raise ContractError("deployment manifest schema_version must be 1")
        environments = {
            name: EnvironmentContract(name=name, **values)
            for name, values in raw.get("environments", {}).items()
        }
        if set(environments) != {"production", "test"}:
            raise ContractError("manifest must define exactly production and test")
        components = tuple(Component(
            name=item["name"],
            environments=tuple(item.get("environments", ())),
            paths=tuple(item.get("paths", ())),
            nodes=tuple(item.get("nodes", ())),
            linux_units=tuple(item.get("linux_units", ())),
            windows_apps=tuple(item.get("windows_apps", ())),
            windows_tasks=tuple(item.get("windows_tasks", ())),
            health_checks=tuple(item.get("health_checks", ())),
            rollback=item.get("rollback", ""),
            manual_gate=bool(item.get("manual_gate", False)),
        ) for item in raw.get("components", ()))
        names = [item.name for item in components]
        if len(names) != len(set(names)) or not names:
            raise ContractError("component names must be non-empty and unique")
        for component in components:
            if (
                not component.environments or not component.paths
                or not component.nodes or not component.rollback
            ):
                raise ContractError(f"component {component.name!r} is incomplete")
            if set(component.environments) - set(environments):
                raise ContractError(f"component {component.name!r} has an unknown environment")
            if set(component.nodes) - {"linux", "windows"}:
                raise ContractError(f"component {component.name!r} has an unknown node")
        return cls(
            schema_version=1,
            ignored_paths=tuple(raw.get("planning", {}).get("ignored_paths", ())),
            environments=environments,
            components=components,
        )

    def plan(
        self, changed_paths: list[str] | tuple[str, ...], *, environment: str,
    ) -> DeploymentPlan:
        if environment not in self.environments:
            raise ContractError(f"unknown deployment environment: {environment}")
        changed = tuple(sorted(set(changed_paths)))
        selected: list[Component] = []
        ignored: list[str] = []
        unknown: list[str] = []
        for path in changed:
            # Exact control-plane exclusions must win over broad runtime globs
            # such as src/qsys/ops/** and scripts/*.py.
            if any(fnmatch(path, pattern) for pattern in self.ignored_paths):
                ignored.append(path)
                continue
            matches = [
                component for component in self.components
                if environment in component.environments
                and any(fnmatch(path, pattern) for pattern in component.paths)
            ]
            if matches:
                for component in matches:
                    if component not in selected:
                        selected.append(component)
            else:
                unknown.append(path)
        selected.sort(key=lambda item: item.name)

        def combined(attribute: str) -> tuple[str, ...]:
            values = {
                value
                for component in selected
                for value in getattr(component, attribute)
            }
            return tuple(sorted(values))

        return DeploymentPlan(
            changed_paths=changed,
            ignored_paths=tuple(ignored),
            components=tuple(item.name for item in selected),
            nodes=combined("nodes"),
            linux_units=combined("linux_units"),
            windows_apps=combined("windows_apps"),
            windows_tasks=combined("windows_tasks"),
            health_checks=combined("health_checks"),
            manual_gate=any(item.manual_gate for item in selected),
            unknown_paths=tuple(unknown),
        )


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_PARTS)


def _redact(value: Any, *, key: str = "") -> Any:
    if _is_sensitive(key):
        if isinstance(value, str):
            return "<set>" if value else "<unset>"
        if isinstance(value, (list, tuple, dict)):
            return f"<{len(value)} items>"
        return "<set>" if value is not None else "<unset>"
    if isinstance(value, dict):
        return {item: _redact(value[item], key=item) for item in sorted(value)}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def config_fingerprints(
    raw: dict[str, Any], *, hmac_key: bytes | None = None,
) -> dict[str, str]:
    redacted = json.dumps(
        _redact(raw), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()
    out = {"public_sha256": hashlib.sha256(redacted).hexdigest()}
    if hmac_key is not None:
        if not hmac_key:
            raise ContractError("config fingerprint HMAC key must not be empty")
        canonical = json.dumps(
            raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode()
        out["private_hmac_sha256"] = hmac.new(
            hmac_key, canonical, hashlib.sha256,
        ).hexdigest()
    return out


def _validate_common(config: DeployConfig) -> list[str]:
    errors: list[str] = []
    redis = config.redis
    if not redis.host:
        errors.append("redis.host must not be empty")
    if not 1 <= redis.port <= 65_535:
        errors.append("redis.port must be between 1 and 65535")
    for name, value in (
        ("connect_timeout_seconds", redis.connect_timeout_seconds),
        ("command_timeout_seconds", redis.command_timeout_seconds),
    ):
        if not math.isfinite(value) or value <= 0:
            errors.append(f"redis.{name} must be a finite positive number")
    stream_values = _field_values(config.streams)
    if len(stream_values.values()) != len(set(stream_values.values())):
        errors.append("stream names must be unique")
    return errors


def validate_config(
    path: str | Path, environment: str, *,
    against: str | Path | None = None,
    hmac_key: bytes | None = None,
) -> dict[str, object]:
    if environment not in {"production", "test"}:
        raise ContractError("environment must be production or test")
    target = Path(path)
    raw, config = _load_contract_config(target)
    errors = _validate_common(config)
    if environment == "test":
        if config.safety.mode != "disabled" or config.safety.orders_enabled:
            errors.append("test safety must be disabled with orders_enabled=false")
        if config.safety.protected_account_ids:
            errors.append("test must not contain protected production account ids")
        if config.redis.db == 0:
            errors.append("test Redis db must not use production default db 0")
        unprefixed = [
            name for name, value in _field_values(config.streams).items()
            if not value.startswith("test:")
        ]
        if unprefixed:
            errors.append("test stream names must use the test: prefix")
        if "test" not in config.strategy.workbench.lower():
            errors.append("test strategy workbench path must be test-specific")
    if against is not None:
        _, other = _load_contract_config(Path(against))
        if (config.redis.host, config.redis.port, config.redis.db) == (
            other.redis.host, other.redis.port, other.redis.db,
        ):
            errors.append("environments must not share the same Redis endpoint and db")
        collisions = (
            set(_field_values(config.streams).values())
            & set(_field_values(other.streams).values())
        )
        if collisions:
            errors.append("environments must not share stream names")
        if config.strategy.workbench == other.strategy.workbench:
            errors.append("environments must not share the strategy workbench")
        if config.safety.local_kill_path == other.safety.local_kill_path:
            errors.append("environments must not share the local kill path")
    return {
        "ok": not errors,
        "environment": environment,
        "errors": errors,
        "fingerprints": config_fingerprints(raw, hmac_key=hmac_key),
    }


def _load_contract_config(target: Path) -> tuple[dict[str, Any], DeployConfig]:
    raw = tomllib.loads(target.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1:
        raise ContractError("deployment config schema_version must be 1")
    unknown_sections = set(raw) - CONFIG_SECTIONS
    if unknown_sections:
        raise ContractError(
            f"deployment config has unknown sections: {sorted(unknown_sections)}"
        )
    return raw, load_deploy_config(target)


def _field_values(value: Any) -> dict[str, Any]:
    return {item.name: getattr(value, item.name) for item in fields(value)}


def validate_record(record: dict[str, Any]) -> None:
    if not isinstance(record, dict):
        raise ContractError("deployment record must be an object")
    missing = RECORD_REQUIRED - record.keys()
    extra = record.keys() - RECORD_REQUIRED
    if missing or extra:
        raise ContractError(
            f"deployment record keys mismatch: missing={sorted(missing)}, extra={sorted(extra)}"
        )
    if record["schema_version"] != 1:
        raise ContractError("deployment record schema_version must be 1")
    if record["environment"] not in {"test", "production"}:
        raise ContractError("deployment record environment is invalid")
    if record["status"] not in RECORD_STATUSES:
        raise ContractError("deployment record status is invalid")
    for field in ("source_sha", "source_tree"):
        if not isinstance(record[field], str):
            raise ContractError(f"deployment record {field} must be a string")
        require_full_sha(record[field], field=f"deployment record {field}")
    for field in ("run_id", "actor", "repository", "migration_summary"):
        if not isinstance(record[field], str) or not record[field]:
            raise ContractError(f"deployment record {field} must be a non-empty string")
    for field in ("started_at", "completed_at"):
        value = record[field]
        if value is None and field == "completed_at":
            continue
        if not isinstance(value, str):
            raise ContractError(f"deployment record {field} must be an ISO datetime")
        try:
            moment = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ContractError(
                f"deployment record {field} must be an ISO datetime"
            ) from exc
        if moment.tzinfo is None:
            raise ContractError(f"deployment record {field} must include a timezone")
    if record["status"] in {"success", "failure", "rolled_back"} and not record["completed_at"]:
        raise ContractError("a terminal deployment record must have completed_at")
    if record["pr_number"] is not None and (
        isinstance(record["pr_number"], bool)
        or not isinstance(record["pr_number"], int) or record["pr_number"] < 1
    ):
        raise ContractError("deployment record pr_number is invalid")
    if not isinstance(record["components"], list) or not all(
        isinstance(item, str) and item for item in record["components"]
    ):
        raise ContractError("deployment record components must be a string list")
    if len(record["components"]) != len(set(record["components"])):
        raise ContractError("deployment record components must be unique")
    for field in ("before", "after", "config_fingerprints", "rollback_targets"):
        value = record[field]
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(item, str)
            for key, item in value.items()
        ):
            raise ContractError(f"deployment record {field} must map strings to strings")
    if not isinstance(record["checks"], list):
        raise ContractError("deployment record checks must be a list")
    for check in record["checks"]:
        if not isinstance(check, dict):
            raise ContractError("deployment check must be an object")
        if set(check) != {"name", "status", "detail"}:
            raise ContractError("deployment check keys are invalid")
        if not all(isinstance(check[field], str) for field in check):
            raise ContractError("deployment check values must be strings")
        if check["status"] not in CHECK_STATUSES:
            raise ContractError("deployment check status is invalid")
    _reject_sensitive_record_keys(record)


def _reject_sensitive_record_keys(value: Any, path: str = "record") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if _is_sensitive(str(key)):
                raise ContractError(f"sensitive key is forbidden in deployment record: {path}.{key}")
            _reject_sensitive_record_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive_record_keys(child, f"{path}[{index}]")
