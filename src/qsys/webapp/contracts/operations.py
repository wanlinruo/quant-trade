"""运维组三页对外契约（#662 计划 07）。"""

from __future__ import annotations

from datetime import date
from typing import Literal

from qsys.webapp.contracts.base import Contract, Timestamp

OpsState = Literal["ok", "warning", "failed", "running", "fixed", "paused", "unknown", "not_due"]


class OperationsDisabled(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[False] = False


class ServiceCell(Contract):
    day: date
    state: OpsState
    run_id: str | None
    detail: str | None


class ServiceStep(Contract):
    name: str
    state: str
    started_at: Timestamp | None
    finished_at: Timestamp | None
    detail: str
    affects_run: bool


class ServiceRunItem(Contract):
    run_id: str
    service_id: str
    session: date | None
    started_at: Timestamp
    finished_at: Timestamp | None
    state: str
    note: str
    error: str
    steps: list[ServiceStep]


class ServiceDurationPoint(Contract):
    run_id: str
    started_at: Timestamp
    duration_seconds: float


class ServiceRow(Contract):
    id: str
    name: str
    kind: str
    node: str
    schedule: str
    latest_state: OpsState
    latest_at: Timestamp | None
    completion_ratio_30d: float | None
    duration_seconds: float | None
    average_duration_seconds: float | None
    duration_history: list[ServiceDurationPoint]
    recent_runs: list[ServiceRunItem]
    version: str | None
    cells: list[ServiceCell]


class ServicesView(Contract):
    schema_version: Literal[2] = 2
    enabled: Literal[True] = True
    selected_day: date
    days: list[date]
    services: list[ServiceRow]
    runs: list[ServiceRunItem]
    missing: dict[str, str]


class MetricPoint(Contract):
    at: Timestamp
    value: float | None


class DiskInfo(Contract):
    id: str
    label: str
    mount: str
    free_bytes: int | None
    total_bytes: int | None
    fail_below_bytes: int | None
    state: OpsState


class NodeInfo(Contract):
    id: str
    label: str
    platform: str
    state: OpsState
    cpu: list[MetricPoint]
    memory: list[MetricPoint]
    io: list[MetricPoint]
    disks: list[DiskInfo]


class LinkInfo(Contract):
    id: str
    label: str
    state: OpsState
    last_ok_at: Timestamp | None
    latency_ms: list[MetricPoint]
    last_error: str | None


class ProcessInfo(Contract):
    id: str
    label: str
    node_id: str
    state: OpsState
    pid: int | None
    restart_count: int | None
    identity: str | None
    resource_limit: str | None
    last_log: str | None


class SecurityCheck(Contract):
    id: str
    label: str
    state: OpsState
    mismatches: list[str]
    failed_logins_24h: int | None
    rejected_unauthenticated_24h: int | None


class NodesView(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    captured_at: Timestamp
    ttl_seconds: int
    stale: bool
    nodes: list[NodeInfo]
    links: list[LinkInfo]
    processes: list[ProcessInfo]
    security: list[SecurityCheck]
    missing: dict[str, str]


class ReleaseStep(Contract):
    id: str
    label: str
    state: OpsState
    at: Timestamp | None
    detail: str | None


class ComponentRelease(Contract):
    id: str
    label: str
    current_sha: str | None
    rollback_sha: str | None
    released_at: Timestamp | None
    state: OpsState
    three_way_consistent: bool | None
    workflow_id: str | None
    deployment_id: str | None
    record_id: str | None
    steps: list[ReleaseStep]


class ReleaseEvent(Contract):
    id: str
    component_id: str
    at: Timestamp
    sha: str | None
    state: OpsState
    current: bool
    detail: str | None


class ReleasesView(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    captured_at: Timestamp
    ttl_seconds: int
    stale: bool
    main_sha: str | None
    consistent_components: int | None
    component_count: int | None
    components: list[ComponentRelease]
    events: list[ReleaseEvent]
    missing: dict[str, str]
