#!/usr/bin/env python3
"""Fixed production Linux deployment gateway installed outside the worktree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

CONFIG_PATH = Path("/etc/qsys/production-deployment-entrypoint.json")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
ACTOR_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ALLOWED_COMPONENTS = frozenset({
    "linux-dependencies", "linux-web", "linux-resident", "linux-data-jobs",
    "linux-systemd",
})
ALLOWED_WINDOWS_COMPONENTS = frozenset({
    "windows-agent", "windows-gateway", "windows-control",
})
WINDOWS_COORDINATOR_PATH = "deploy/linux/qsys-windows-deploy-coordinator.py"
ENTRYPOINT_VERSION = 6
ENTRYPOINT_CAPABILITIES = (
    "systemd-apply-v1",
    "systemd-changed-artifacts-v1",
    "systemd-preview-v1",
    "systemd-rollback-v1",
    "release-checklist-v1",
    "controlled-failure-injection-v1",
    "successful-release-index-v1",
    "windows-baseline-plan-v1",
    "systemd-enable-state-v1",
)
FAILURE_POINTS = frozenset({"none", "linux-health", "windows-switch"})
ALLOWED_UNITS = frozenset({
    "qsys-monitor.service", "qsys-paper.service", "qsys-shadow-core.service",
    "qsys-health-observer.service", "qsys-live-core.service",
})
SYSTEMD_SOURCE_ROOT = Path("deploy/systemd")
SYSTEMD_UNIT_ROOT = Path("/etc/systemd/system")
QSYS_ETC_ROOT = Path("/etc/qsys")
# These are repository templates, not canonical production artifacts.  The two
# strategy-core units are deliberately customized for the site's user and
# checkout path after copying; the web-test unit belongs only to test.
SYSTEMD_EXCLUDED = frozenset({
    "qsys-live-core.service",
    "qsys-shadow-core.service",
    "qsys-web-test.service",
})
MARKET_DATA_CONSUMERS = (
    "qsys-monitor.service", "qsys-paper.service",
    "qsys-shadow-core.service", "qsys-live-core.service",
)
SYSTEMD_ENABLE_ON_INSTALL = frozenset({"qsys-limit-up-shadow.timer"})


class SystemdApplyError(RuntimeError):
    def __init__(self, message: str, *, backup_root: Path) -> None:
        super().__init__(message)
        self.backup_root = backup_root


def load_config() -> dict[str, object]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    required = {
        "schema_version", "production_repo", "production_record_root",
        "test_record_root", "fingerprint_key", "uv_bin", "health_url",
    }
    if set(config) != required or config["schema_version"] != 1:
        raise ValueError("production entrypoint config must match schema version 1")
    for key in required - {"schema_version", "health_url"}:
        value = config[key]
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError(f"{key} must be an absolute path")
    if not isinstance(config["health_url"], str) or not config["health_url"]:
        raise ValueError("health_url must be a non-empty string")
    return config


def _run(
    command: list[str], *, cwd: Path, check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=check, text=True, capture_output=True)


def _git_blob(repo: Path, sha: str, path: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{sha}:{path}"], cwd=repo)


def _head(repo: Path) -> str:
    return _run(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()


def _branch(repo: Path) -> str:
    return _run(["git", "branch", "--show-current"], cwd=repo).stdout.strip()


def _dirty(repo: Path) -> bool:
    return bool(_run(
        ["git", "status", "--porcelain", "--untracked-files=normal"], cwd=repo,
    ).stdout)


def _active(unit: str, repo: Path) -> bool:
    result = _run(["systemctl", "is-active", unit], cwd=repo, check=False)
    return result.returncode == 0 and result.stdout.strip() == "active"


def _enabled(unit: str, repo: Path) -> bool:
    result = _run(["systemctl", "is-enabled", unit], cwd=repo, check=False)
    return result.returncode == 0 and result.stdout.strip() == "enabled"


def _web_healthy(config: dict[str, object]) -> bool:
    try:
        with urllib.request.urlopen(str(config["health_url"]), timeout=5) as response:
            return response.status == 200
    except OSError:
        return False


def status(config: dict[str, object]) -> dict[str, object]:
    repo = Path(str(config["production_repo"])).resolve(strict=True)
    return {
        "environment": "production",
        "sha": _head(repo),
        "branch": _branch(repo),
        "dirty": _dirty(repo),
        "healthy": _active("qsys-monitor.service", repo) and _web_healthy(config),
        "entrypoint_version": ENTRYPOINT_VERSION,
        "capabilities": list(ENTRYPOINT_CAPABILITIES),
    }


def _restart_window(repo: Path) -> dict[str, object]:
    """Use the local canonical calendar; never infer a holiday from weekdays."""
    code = """
import json
from datetime import datetime, time
from zoneinfo import ZoneInfo
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

now = datetime.now(ZoneInfo("Asia/Shanghai"))
rows = WarehouseQuery(Warehouse("warehouse")).trading_calendar(
    now.date(), now.date(), market="SSE", open_only=True,
)
trading = rows.height > 0
blocked = trading and time(8, 45) <= now.time() < time(15, 40)
print(json.dumps({
    "checked_at": now.isoformat(), "trading_day": trading,
    "restart_allowed": not blocked,
}))
"""
    python = repo / ".venv" / "bin" / "python"
    result = _run([str(python), "-c", code], cwd=repo)
    value = json.loads(result.stdout)
    if set(value) != {"checked_at", "trading_day", "restart_allowed"}:
        raise ValueError("restart-window probe returned an invalid contract")
    return value


def _resources(config: dict[str, object]) -> dict[str, object]:
    disk = shutil.disk_usage(Path(str(config["production_record_root"])).parent)
    fields: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            fields[key] = int(parts[0]) * 1024
    available = fields.get("MemAvailable", 0)
    return {
        "disk_free_bytes": disk.free,
        "memory_available_bytes": available,
        "ok": disk.free >= 5 * 1024 ** 3 and available >= 512 * 1024 ** 2,
    }


def _rollback_ready(config: dict[str, object], sha: str) -> bool:
    root = Path(str(config["production_record_root"]))
    for path in root.glob("*/record.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("status") == "success" and sha in record.get("after", {}).values():
            return True
    return False


def _checklist_checks(
    restart_window: dict[str, object], resources: dict[str, object],
    rollback_ready: bool, test_run: str, *, needs_rollback: bool,
) -> list[dict[str, str]]:
    return [
        {
            "name": "test-tree-evidence", "status": "passed",
            "detail": f"successful test deployment run={test_run}",
        },
        {
            "name": "restart-window",
            "status": "passed" if restart_window["restart_allowed"] else "failed",
            "detail": (
                f"checked_at={restart_window['checked_at']}; "
                f"trading_day={str(restart_window['trading_day']).lower()}"
            ),
        },
        {
            "name": "resource-headroom",
            "status": "passed" if resources["ok"] else "failed",
            "detail": (
                f"disk_free_bytes={resources['disk_free_bytes']}; "
                f"memory_available_bytes={resources['memory_available_bytes']}"
            ),
        },
        {
            "name": "rollback-target",
            "status": "passed" if rollback_ready or not needs_rollback else "failed",
            "detail": "previous SHA is a recorded successful release" if needs_rollback else "no version switch",
        },
    ]


def deployment_history(config: dict[str, object]) -> dict[str, object]:
    """Build the non-secret successful-release index used by rollback audits."""
    root = Path(str(config["production_record_root"]))
    successes: list[dict[str, object]] = []
    for path in root.glob("*/record.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            completed = datetime.fromisoformat(str(record["completed_at"]))
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            continue
        if record.get("status") != "success" or completed.tzinfo is None:
            continue
        successes.append({
            "run_id": str(record.get("run_id", "")),
            "completed_at": completed.isoformat(),
            "source_sha": str(record.get("source_sha", "")),
            "nodes": sorted(record.get("after", {})),
            "record": str(path),
            "_completed": completed.astimezone(UTC),
        })
    successes.sort(key=lambda item: item["_completed"], reverse=True)
    cutoff = datetime.now(UTC) - timedelta(days=30)
    recent_30_days = [item for item in successes if item["_completed"] >= cutoff]
    for item in successes:
        item.pop("_completed")
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "policy": {"minimum_successes": 10, "minimum_days": 30, "records_pruned": False},
        "latest_successes": successes[:10],
        "successes_within_30_days": recent_30_days,
    }


def _refresh_success_index(config: dict[str, object]) -> Path:
    target = Path(str(config["production_record_root"])).parent / "success-index.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="success-index.", suffix=".json", dir=target.parent)
    path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(deployment_history(config), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        path.replace(target)
    finally:
        path.unlink(missing_ok=True)
    return target


def _validate_drill(args: argparse.Namespace, *, expected: str) -> None:
    failure = getattr(args, "failure_point", "none")
    if failure not in FAILURE_POINTS:
        raise ValueError("unsupported failure injection point")
    if failure != "none" and (
        failure != expected
        or not args.run_id.startswith("drill-")
        or getattr(args, "drill_confirmation", "") != "DRILL_ROLLBACK"
    ):
        raise ValueError("failure injection requires a drill run-id and DRILL_ROLLBACK")


def _fetch_and_validate_target(repo: Path, sha: str) -> str:
    if not SHA_PATTERN.fullmatch(sha):
        raise ValueError("sha must be a full 40-character lowercase commit SHA")
    _run(["git", "fetch", "--no-tags", "origin", "main"], cwd=repo)
    _run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=repo)
    ancestor = _run(
        ["git", "merge-base", "--is-ancestor", sha, "origin/main"],
        cwd=repo, check=False,
    )
    if ancestor.returncode != 0:
        raise ValueError("target is not an ancestor of origin/main")
    return _run(["git", "rev-parse", f"{sha}^{{tree}}"], cwd=repo).stdout.strip()


def _successful_test_run(config: dict[str, object], tree: str) -> str:
    root = Path(str(config["test_record_root"])).resolve(strict=True)
    candidates: list[tuple[str, str]] = []
    for path in root.glob("*/record.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            record.get("schema_version") == 1
            and record.get("environment") == "test"
            and record.get("status") == "success"
            and record.get("source_tree") == tree
            and (
                record.get("after", {}).get("linux") == record.get("source_sha")
                or record.get("components") == []
            )
        ):
            candidates.append((str(record.get("completed_at", "")), str(record["run_id"])))
    if not candidates:
        raise ValueError("no successful test deployment has the target Git tree")
    return max(candidates)[1]


def _plan(repo: Path, before: str, target: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="qsys-production-plan-") as temporary:
        worktree = Path(temporary) / "target"
        _run(["git", "worktree", "add", "--detach", str(worktree), target], cwd=repo)
        try:
            result = _run([
                "/usr/bin/env", "PYTHONPATH=src", "python3",
                "scripts/deployment_contract.py", "plan",
                "--environment", "production", "--base", before, "--target", target,
            ], cwd=worktree)
            return json.loads(result.stdout)
        finally:
            _run(["git", "worktree", "remove", "--force", str(worktree)], cwd=repo, check=False)


def _validate_components(plan: dict[str, Any], requested: list[str]) -> list[str]:
    if len(requested) != len(set(requested)) or set(requested) - ALLOWED_COMPONENTS:
        raise ValueError("components must be unique production Linux components")
    planned = sorted(set(plan["components"]) & ALLOWED_COMPONENTS)
    if sorted(requested) != planned:
        raise ValueError(f"requested components do not match plan: expected {','.join(planned)}")
    if plan["unknown_paths"]:
        raise ValueError("deployment plan contains unknown paths")
    return planned


def _validate_windows_components(plan: dict[str, Any], requested: list[str]) -> list[str]:
    if requested == ["auto"]:
        requested = sorted(set(plan["components"]) & ALLOWED_WINDOWS_COMPONENTS)
    if len(requested) != len(set(requested)) or set(requested) - ALLOWED_WINDOWS_COMPONENTS:
        raise ValueError("components must be unique production Windows components")
    planned = sorted(set(plan["components"]) & ALLOWED_WINDOWS_COMPONENTS)
    if sorted(requested) != planned:
        raise ValueError(f"requested components do not match plan: expected {','.join(planned)}")
    if plan["unknown_paths"]:
        raise ValueError("deployment plan contains unknown paths")
    return planned


def _merge_plans(plans: list[dict[str, Any]]) -> dict[str, Any]:
    if not plans:
        raise ValueError("at least one deployment plan is required")
    fields = (
        "changed_paths", "ignored_paths", "components", "nodes", "linux_units",
        "windows_apps", "windows_tasks", "health_checks", "unknown_paths",
    )
    return {
        field: sorted({item for plan in plans for item in plan[field]})
        for field in fields
    }


def _windows_status(repo: Path, sha: str, run_id: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="qsys-windows-status-") as temporary:
        coordinator = Path(temporary) / "qsys-windows-deploy-coordinator.py"
        coordinator.write_bytes(_git_blob(repo, sha, WINDOWS_COORDINATOR_PATH))
        completed = _run([
            sys.executable, str(coordinator), "--repo", str(repo), "--sha", sha,
            "--run-id", run_id, "--status-only",
        ], cwd=repo, check=False)
    stream = completed.stdout if completed.stdout.strip() else completed.stderr
    try:
        result = json.loads(stream)
    except json.JSONDecodeError as exc:
        raise ValueError("Windows coordinator status returned invalid JSON") from exc
    if completed.returncode != 0 or not result.get("ok"):
        raise ValueError(f"Windows coordinator status failed: {result}")
    return result


def _fingerprints(config: dict[str, object], repo: Path) -> dict[str, str]:
    result = json.loads(_run([
        str(config["uv_bin"]), "run", "--frozen", "python",
        "scripts/deployment_contract.py", "validate-config",
        "--environment", "production", "--config", "config/deploy.local.toml",
        "--fingerprint-key-file", str(config["fingerprint_key"]),
    ], cwd=repo).stdout)
    if not result.get("ok"):
        raise ValueError("production configuration validation failed")
    return {f"linux_{key}": value for key, value in result["fingerprints"].items()}


def _wait_healthy(config: dict[str, object], units: list[str], repo: Path) -> bool:
    for attempt in range(20):
        if all(_active(unit, repo) for unit in units) and _web_healthy(config):
            return True
        if attempt < 19:
            time.sleep(1)
    return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _systemd_targets(relative: str) -> tuple[Path, ...]:
    """Map one repository artifact to its production-owned destinations."""
    path = Path(relative)
    if path.parent != SYSTEMD_SOURCE_ROOT:
        raise ValueError(f"unsupported systemd artifact path: {relative}")
    if path.name in SYSTEMD_EXCLUDED:
        return ()
    if path.name == "qsys-market-data.env":
        return (QSYS_ETC_ROOT / "market-data.env",)
    if path.name == "qsys-market-data.conf":
        return tuple(
            SYSTEMD_UNIT_ROOT / f"{unit}.d" / "market-data.conf"
            for unit in MARKET_DATA_CONSUMERS
        )
    if path.suffix in {".service", ".timer"}:
        return (SYSTEMD_UNIT_ROOT / path.name,)
    raise ValueError(f"unsupported systemd artifact: {relative}")


def _systemd_artifacts(
    repo: Path, changed_paths: list[str],
) -> dict[Path, Path | None]:
    """Return deployable production systemd artifacts changed by this release."""
    desired: dict[Path, Path | None] = {}
    for relative in sorted(changed_paths):
        if not relative.startswith(f"{SYSTEMD_SOURCE_ROOT}/"):
            continue
        source = repo / relative
        for target in _systemd_targets(relative):
            desired[target] = source if source.is_file() else None
    if not desired:
        raise ValueError(
            "linux-systemd selected but no deployable production artifact changed"
        )
    return desired


def _systemd_restart_units(artifacts: dict[Path, Path | None]) -> list[str]:
    units: set[str] = set()
    for target, source in artifacts.items():
        changed = target.exists() if source is None else (
            not target.is_file() or _sha256(target) != _sha256(source)
        )
        if not changed:
            continue
        if target.name == "market-data.conf" or target == QSYS_ETC_ROOT / "market-data.env":
            units.update(MARKET_DATA_CONSUMERS)
        elif target.parent == SYSTEMD_UNIT_ROOT and target.suffix in {".service", ".timer"}:
            units.add(target.name)
    return sorted(units)


def _preview_systemd(
    repo: Path, target: str, changed_paths: list[str],
) -> dict[str, list[str]]:
    """Inspect target artifacts against /etc without changing either one."""
    with tempfile.TemporaryDirectory(prefix="qsys-production-systemd-preview-") as temporary:
        worktree = Path(temporary) / "target"
        _run(["git", "worktree", "add", "--detach", str(worktree), target], cwd=repo)
        try:
            artifacts = _systemd_artifacts(worktree, changed_paths)
            units = _systemd_restart_units(artifacts)
            active_units = [unit for unit in units if _active(unit, repo)]
            enable_units = sorted(
                unit for unit in units
                if unit in SYSTEMD_ENABLE_ON_INSTALL
                and artifacts.get(SYSTEMD_UNIT_ROOT / unit) is not None
                and not _enabled(unit, repo)
            )
            return {
                "targets": sorted(str(target_path) for target_path in artifacts),
                "restart_units": active_units,
                "inactive_units": sorted(set(units) - set(active_units)),
                "enable_units": enable_units,
            }
        finally:
            _run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=repo, check=False,
            )


def _backup_systemd(
    config: dict[str, object], run_id: str, artifacts: dict[Path, Path | None],
    active: dict[str, bool], enabled: dict[str, bool],
) -> tuple[Path, dict[str, object]]:
    root = (
        Path(str(config["production_record_root"])).parent
        / "systemd-backups" / run_id
    )
    root.mkdir(parents=True, exist_ok=False)
    entries: list[dict[str, object]] = []
    for target in sorted(artifacts, key=str):
        existed = target.is_file()
        entries.append({"path": str(target), "existed": existed})
        if existed:
            backup = root / target.relative_to("/")
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
    manifest: dict[str, object] = {
        "schema_version": 2,
        "targets": entries,
        "active_units": active,
        "enabled_units": enabled,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return root, manifest


def _restart_previously_active(
    units: list[str], active: dict[str, bool], repo: Path,
) -> None:
    for unit in units:
        if not active.get(unit, False):
            continue
        if unit.endswith(".service"):
            service_type = _run(
                ["systemctl", "show", unit, "--property=Type", "--value"],
                cwd=repo, check=False,
            ).stdout.strip()
            if service_type == "oneshot":
                continue
        _run(["sudo", "-n", "systemctl", "restart", unit], cwd=repo)


def _verify_systemd_artifacts(
    artifacts: dict[Path, Path | None], units: list[str],
    active: dict[str, bool], repo: Path,
) -> None:
    for target, source in artifacts.items():
        if source is None:
            if target.exists():
                raise ValueError(f"deleted systemd artifact still exists: {target}")
        elif not target.is_file() or _sha256(target) != _sha256(source):
            raise ValueError(f"installed systemd artifact differs from target: {target}")
    installed_units = sorted(
        str(target) for target, source in artifacts.items()
        if source is not None and target.parent == SYSTEMD_UNIT_ROOT
        and target.suffix in {".service", ".timer"}
    )
    if installed_units:
        _run(["systemd-analyze", "verify", *installed_units], cwd=repo)
    for unit in units:
        load_state = _run(
            ["systemctl", "show", unit, "--property=LoadState", "--value"],
            cwd=repo, check=False,
        ).stdout.strip()
        if load_state != "loaded":
            raise ValueError(f"systemd unit is not loaded: {unit} ({load_state or 'unknown'})")
        if active.get(unit, False) and not _active(unit, repo):
            raise ValueError(f"previously active systemd unit is not active: {unit}")
        if (
            unit in SYSTEMD_ENABLE_ON_INSTALL
            and artifacts.get(SYSTEMD_UNIT_ROOT / unit) is not None
            and (not _enabled(unit, repo) or not _active(unit, repo))
        ):
            raise ValueError(f"systemd unit was not enabled and started: {unit}")


def _restore_systemd_state(
    units: list[str], active: dict[str, bool], enabled: dict[str, bool], repo: Path,
) -> None:
    for unit in units:
        action = "enable" if enabled.get(unit, False) else "disable"
        _run(["sudo", "-n", "systemctl", action, unit], cwd=repo)
    _restart_previously_active(units, active, repo)
    for unit in units:
        if not active.get(unit, False):
            _run(["sudo", "-n", "systemctl", "stop", unit], cwd=repo)


def _restore_systemd(
    backup_root: Path, manifest: dict[str, object], units: list[str], repo: Path,
) -> bool:
    try:
        for item in manifest["targets"]:
            target = Path(str(item["path"]))
            if item["existed"]:
                source = backup_root / target.relative_to("/")
                _run([
                    "sudo", "-n", "install", "-D", "-o", "root", "-g", "root",
                    "-m", "0644", str(source), str(target),
                ], cwd=repo)
            else:
                _run(["sudo", "-n", "rm", "-f", "--", str(target)], cwd=repo)
        _run(["sudo", "-n", "systemctl", "daemon-reload"], cwd=repo)
        active = {str(key): bool(value) for key, value in manifest["active_units"].items()}
        enabled = {
            str(key): bool(value) for key, value in manifest["enabled_units"].items()
        }
        _restore_systemd_state(units, active, enabled, repo)
        for item in manifest["targets"]:
            target = Path(str(item["path"]))
            if item["existed"]:
                backup = backup_root / target.relative_to("/")
                if not target.is_file() or _sha256(target) != _sha256(backup):
                    return False
            elif target.exists():
                return False
        return all(
            _active(unit, repo) == active.get(unit, False)
            and _enabled(unit, repo) == enabled.get(unit, False)
            for unit in units
        )
    except (OSError, subprocess.CalledProcessError, ValueError):
        return False


def _apply_systemd(
    config: dict[str, object], repo: Path, run_id: str, changed_paths: list[str],
) -> tuple[dict[str, str], Path]:
    """Install, reload and verify the declared systemd state, rolling back on failure."""
    artifacts = _systemd_artifacts(repo, changed_paths)
    units = _systemd_restart_units(artifacts)
    active = {unit: _active(unit, repo) for unit in units}
    enabled = {unit: _enabled(unit, repo) for unit in units}
    backup_root, manifest = _backup_systemd(
        config, run_id, artifacts, active, enabled,
    )
    try:
        for target, source in artifacts.items():
            if source is None:
                _run(["sudo", "-n", "rm", "-f", "--", str(target)], cwd=repo)
            else:
                _run([
                    "sudo", "-n", "install", "-D", "-o", "root", "-g", "root",
                    "-m", "0644", str(source), str(target),
                ], cwd=repo)
        _run(["sudo", "-n", "systemctl", "daemon-reload"], cwd=repo)
        _restart_previously_active(units, active, repo)
        for unit in units:
            if (
                unit in SYSTEMD_ENABLE_ON_INSTALL
                and artifacts.get(SYSTEMD_UNIT_ROOT / unit) is not None
            ):
                _run(
                    ["sudo", "-n", "systemctl", "enable", "--now", unit],
                    cwd=repo,
                )
        _verify_systemd_artifacts(artifacts, units, active, repo)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        rollback_ok = _restore_systemd(backup_root, manifest, units, repo)
        raise SystemdApplyError(
            f"systemd apply failed; rollback={'succeeded' if rollback_ok else 'failed'}: {exc}",
            backup_root=backup_root,
        ) from exc
    return ({
        "name": "systemd-verify", "status": "passed",
        "detail": (
            f"installed and byte-verified {len(artifacts)} targets; "
            f"preserved prior state for {len(units)} affected units; "
            f"enabled {sum(unit in SYSTEMD_ENABLE_ON_INSTALL for unit in units)} units"
        ),
    }, backup_root)


def _restore(
    config: dict[str, object], repo: Path, before: str, units: list[str],
) -> bool:
    try:
        _run(["git", "reset", "--hard", before], cwd=repo)
        _run([str(config["uv_bin"]), "sync", "--frozen"], cwd=repo)
        for unit in units:
            _run(["sudo", "-n", "systemctl", "restart", unit], cwd=repo)
    except subprocess.CalledProcessError:
        return False
    return True


def _write_record(config: dict[str, object], run_id: str, record: dict[str, Any]) -> Path:
    root = Path(str(config["production_record_root"])) / run_id
    root.mkdir(parents=True, exist_ok=False)
    fd, temporary = tempfile.mkstemp(prefix="record.", suffix=".json", dir=root)
    path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        path.replace(root / "record.json")
    finally:
        path.unlink(missing_ok=True)
    _refresh_success_index(config)
    return root / "record.json"


def deploy(args: argparse.Namespace, config: dict[str, object]) -> int:
    if not RUN_ID_PATTERN.fullmatch(args.run_id):
        raise ValueError("run-id contains unsupported characters")
    if not ACTOR_PATTERN.fullmatch(args.actor):
        raise ValueError("actor contains unsupported characters")
    if not REPOSITORY_PATTERN.fullmatch(args.repository):
        raise ValueError("repository must be owner/name")
    _validate_drill(args, expected="linux-health")
    repo = Path(str(config["production_repo"])).resolve(strict=True)
    if _branch(repo) != "main" or _dirty(repo):
        raise ValueError("production checkout must be a clean main worktree")
    before = _head(repo)
    tree = _fetch_and_validate_target(repo, args.sha)
    test_run = _successful_test_run(config, tree)
    plan = _plan(repo, before, args.sha)
    components = _validate_components(plan, args.components)
    units = sorted(set(plan["linux_units"]) & ALLOWED_UNITS)
    active_units = [unit for unit in units if _active(unit, repo)]
    restart_window = _restart_window(repo)
    resources = _resources(config)
    rollback_ready = _rollback_ready(config, before)
    systemd_preview = (
        _preview_systemd(repo, args.sha, list(plan["changed_paths"]))
        if "linux-systemd" in components else None
    )
    preview = {
        "ok": True, "dry_run": args.dry_run, "before_sha": before,
        "target_sha": args.sha, "source_tree": tree, "test_run_id": test_run,
        "components": components, "restart_units": active_units,
        "inactive_units": sorted(set(units) - set(active_units)),
        "health_checks": plan["health_checks"], "rollback_sha": before,
        "restart_window": restart_window, "resources": resources,
        "rollback_ready": rollback_ready,
        **({"systemd": systemd_preview} if systemd_preview is not None else {}),
    }
    if args.dry_run:
        print(json.dumps(preview, ensure_ascii=False))
        return 0
    if active_units and not restart_window["restart_allowed"]:
        raise ValueError("production restart is prohibited during the trading window")
    if not resources["ok"]:
        raise ValueError("production node lacks minimum disk or memory headroom")
    if before != args.sha and not rollback_ready:
        raise ValueError("current production SHA is not a recorded successful rollback target")

    started = datetime.now(UTC).isoformat()
    checks = _checklist_checks(
        restart_window, resources, rollback_ready, test_run,
        needs_rollback=before != args.sha,
    )
    success = False
    rollback_ok = False
    fingerprints: dict[str, str] = {}
    systemd_backup: Path | None = None
    try:
        if before != args.sha:
            _run(["git", "reset", "--hard", args.sha], cwd=repo)
        # Preserve the historical fail-closed preflight for every code switch,
        # even when a component's manifest only adds a narrower health check.
        if before != args.sha or "preflight" in plan["health_checks"]:
            _run(["/bin/sh", "scripts/preflight_linux_deploy.sh"], cwd=repo)
            checks.append({
                "name": "preflight", "status": "passed",
                "detail": "locked dependencies, imports and production entrypoints verified",
            })
        fingerprints = _fingerprints(config, repo)
        if "linux-systemd" in components:
            systemd_check, systemd_backup = _apply_systemd(
                config, repo, args.run_id, list(plan["changed_paths"]),
            )
            checks.append(systemd_check)
        for unit in active_units:
            _run(["sudo", "-n", "systemctl", "restart", unit], cwd=repo)
        if args.failure_point == "linux-health":
            raise RuntimeError("injected Linux health failure after switch")
        healthy = _wait_healthy(config, active_units, repo)
        success = (
            _head(repo) == args.sha and healthy
            and all(item["status"] == "passed" for item in checks)
        )
        checks.append({
            "name": "production-health", "status": "passed" if success else "failed",
            "detail": "active affected units and local health endpoint must be healthy",
        })
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        if isinstance(exc, SystemdApplyError):
            systemd_backup = exc.backup_root
        checks.append({"name": "deploy-command", "status": "failed", "detail": str(exc)[:500]})
    code_rollback_ok = True
    if not success and _head(repo) != before:
        code_rollback_ok = _restore(config, repo, before, active_units)
    systemd_rollback_ok = True
    if not success and systemd_backup is not None:
        try:
            systemd_manifest = json.loads(
                (systemd_backup / "manifest.json").read_text(encoding="utf-8")
            )
            systemd_units = sorted(systemd_manifest["active_units"])
            systemd_rollback_ok = _restore_systemd(
                systemd_backup, systemd_manifest, systemd_units, repo,
            )
        except (OSError, ValueError, json.JSONDecodeError):
            systemd_rollback_ok = False
        checks.append({
            "name": "systemd-rollback",
            "status": "passed" if systemd_rollback_ok else "failed",
            "detail": f"restore /etc artifacts and active state from {systemd_backup}",
        })
    if not success:
        rollback_ok = (
            code_rollback_ok and systemd_rollback_ok and _head(repo) == before
            and _wait_healthy(config, active_units, repo)
        )
    state = "success" if success else ("rolled_back" if rollback_ok else "failure")
    after = _head(repo)
    record = {
        "schema_version": 1, "run_id": args.run_id, "environment": "production",
        "status": state, "actor": args.actor, "started_at": started,
        "completed_at": datetime.now(UTC).isoformat(), "repository": args.repository,
        "pr_number": args.pr_number, "source_sha": args.sha, "source_tree": tree,
        "components": components, "before": {"linux": before}, "after": {"linux": after},
        "config_fingerprints": fingerprints, "checks": checks,
        "rollback_targets": {
            "linux": before,
            **({"systemd": str(systemd_backup)} if systemd_backup is not None else {}),
        },
        "migration_summary": "none",
    }
    path = _write_record(config, args.run_id, record)
    print(json.dumps({"ok": success, "status": state, "record": str(path)}))
    return 0 if success else 1


def windows_deploy(args: argparse.Namespace, config: dict[str, object]) -> int:
    if not RUN_ID_PATTERN.fullmatch(args.run_id):
        raise ValueError("run-id contains unsupported characters")
    if not ACTOR_PATTERN.fullmatch(args.actor):
        raise ValueError("actor contains unsupported characters")
    if not REPOSITORY_PATTERN.fullmatch(args.repository):
        raise ValueError("repository must be owner/name")
    _validate_drill(args, expected="windows-switch")
    repo = Path(str(config["production_repo"])).resolve(strict=True)
    if _branch(repo) != "main" or _dirty(repo):
        raise ValueError("production checkout must be a clean main worktree")
    before_linux = _head(repo)
    restart_window = _restart_window(repo)
    resources = _resources(config)
    tree = _fetch_and_validate_target(repo, args.sha)
    test_run = _successful_test_run(config, tree)
    windows_status = _windows_status(repo, args.sha, f"{args.run_id}-status")
    current_windows = {
        str(item.get("deployed_sha", ""))
        for item in windows_status.get("apps", {}).values()
    }
    if not current_windows or any(not SHA_PATTERN.fullmatch(sha) for sha in current_windows):
        raise ValueError("Windows status contains an invalid deployed SHA")
    rollback_ready = all(_rollback_ready(config, sha) for sha in current_windows)
    windows_change = any(sha != args.sha for sha in current_windows)
    plan = _merge_plans([_plan(repo, sha, args.sha) for sha in sorted(current_windows)])
    components = _validate_windows_components(plan, args.components)
    apps = sorted(set(plan["windows_apps"]) & {"live_app", "paper_app"})
    if not apps:
        raise ValueError("production plan contains no Windows application")
    if not args.dry_run:
        if not restart_window["restart_allowed"]:
            raise ValueError("production restart is prohibited during the trading window")
        if not resources["ok"]:
            raise ValueError("production node lacks minimum disk or memory headroom")
        if windows_change and not rollback_ready:
            raise ValueError("a current Windows SHA is not a recorded successful rollback target")
    started = datetime.now(UTC).isoformat()
    # The fixed SSH entrypoint is only a bootstrap. Execute the Windows
    # coordinator from the exact validated target SHA so it can update itself
    # before either a dry-run or a real switch.
    with tempfile.TemporaryDirectory(prefix="qsys-windows-coordinator-") as temporary:
        coordinator = Path(temporary) / "qsys-windows-deploy-coordinator.py"
        coordinator.write_bytes(_git_blob(repo, args.sha, WINDOWS_COORDINATOR_PATH))
        command = [
            sys.executable, str(coordinator), "--repo", str(repo), "--sha", args.sha,
            "--run-id", args.run_id,
            *(argument for app in apps for argument in ("--app", app)),
        ]
        if args.dry_run:
            command.append("--dry-run")
        if args.failure_point == "windows-switch":
            command.extend(["--failure-point", "after-first-switch"])
        completed = _run(command, cwd=repo, check=False)
    stream = completed.stdout if completed.stdout.strip() else completed.stderr
    try:
        result = json.loads(stream)
    except json.JSONDecodeError as exc:
        raise ValueError("Windows coordinator returned invalid JSON") from exc
    result.update({
        "source_sha": args.sha, "source_tree": tree, "test_run_id": test_run,
        "components": components,
    })
    if args.dry_run:
        result.update({
            "restart_window": restart_window, "resources": resources,
            "rollback_ready": rollback_ready,
        })
        print(json.dumps(result, ensure_ascii=False))
        return completed.returncode

    windows_record = result.get("record", {})
    operation_apps = windows_record.get("apps", {})
    before = {
        f"windows:{app}": str(result.get("apps", {}).get(app, {}).get("before_sha", ""))
        for app in apps
    }
    state = str(windows_record.get("status", "failure"))
    success = completed.returncode == 0 and state == "success"
    after: dict[str, str] = {}
    checks = _checklist_checks(
        restart_window, resources, rollback_ready, test_run,
        needs_rollback=windows_change,
    )
    rollback_targets = dict(before)
    for app in apps:
        item = operation_apps.get(app, {})
        item_state = str(item.get("state", "unknown"))
        after[f"windows:{app}"] = (
            args.sha if item_state in {"verified", "noop"} else before[f"windows:{app}"]
        )
        checks.append({
            "name": f"{app}-release", "status": "passed" if item_state in {"verified", "noop"} else "failed",
            "detail": f"state={item_state}; task, manifest, DEPLOYED_SHA and fresh heartbeat",
        })
    checks.append({
        "name": "watchdog-restored",
        "status": "passed" if windows_record.get("watchdog_restored") is True or success else "failed",
        "detail": "qsys-watchdog must be re-enabled after success or compensation",
    })
    fingerprints = {
        f"windows_{app}_public_sha256": str(
            operation_apps.get(app, {}).get("config_public_sha256", "")
        )
        for app in apps if operation_apps.get(app, {}).get("config_public_sha256")
    }
    record = {
        "schema_version": 1, "run_id": args.run_id, "environment": "production",
        "status": "success" if success else ("rolled_back" if state == "rolled_back" else "failure"),
        "actor": args.actor, "started_at": started,
        "completed_at": datetime.now(UTC).isoformat(), "repository": args.repository,
        "pr_number": args.pr_number, "source_sha": args.sha, "source_tree": tree,
        "components": components, "before": before, "after": after,
        "config_fingerprints": fingerprints, "checks": checks,
        "rollback_targets": rollback_targets, "migration_summary": "none",
    }
    path = _write_record(config, args.run_id, record)
    print(json.dumps({
        "ok": success, "dry_run": False,
        "status": record["status"], "record": str(path),
    }))
    return 0 if success else 1


def read_record(config: dict[str, object], run_id: str) -> dict[str, Any]:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run-id contains unsupported characters")
    path = Path(str(config["production_record_root"])) / run_id / "record.json"
    return json.loads(path.read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    deploy_parser = commands.add_parser("deploy")
    deploy_parser.add_argument("--sha", required=True)
    deploy_parser.add_argument("--component", action="append", dest="components", required=True)
    deploy_parser.add_argument("--run-id", required=True)
    deploy_parser.add_argument("--actor", required=True)
    deploy_parser.add_argument("--repository", required=True)
    deploy_parser.add_argument("--pr-number", type=int)
    deploy_parser.add_argument("--dry-run", action="store_true")
    deploy_parser.add_argument("--failure-point", default="none")
    deploy_parser.add_argument("--drill-confirmation", default="")
    windows_parser = commands.add_parser("windows-deploy")
    windows_parser.add_argument("--sha", required=True)
    windows_parser.add_argument("--component", action="append", dest="components", required=True)
    windows_parser.add_argument("--run-id", required=True)
    windows_parser.add_argument("--actor", required=True)
    windows_parser.add_argument("--repository", required=True)
    windows_parser.add_argument("--pr-number", type=int)
    windows_parser.add_argument("--dry-run", action="store_true")
    windows_parser.add_argument("--failure-point", default="none")
    windows_parser.add_argument("--drill-confirmation", default="")
    record_parser = commands.add_parser("record")
    record_parser.add_argument("--run-id", required=True)
    commands.add_parser("history")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config()
        if args.command == "status":
            print(json.dumps(status(config)))
            return 0
        if args.command == "record":
            print(json.dumps(read_record(config, args.run_id), ensure_ascii=False))
            return 0
        if args.command == "history":
            print(json.dumps(deployment_history(config), ensure_ascii=False))
            return 0
        if args.command == "windows-deploy":
            return windows_deploy(args, config)
        return deploy(args, config)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
