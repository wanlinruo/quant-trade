#!/usr/bin/env python3
"""Fixed production Linux deployment gateway installed outside the worktree."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import UTC, datetime
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
ALLOWED_UNITS = frozenset({
    "qsys-monitor.service", "qsys-paper.service", "qsys-shadow-core.service",
    "qsys-health-observer.service", "qsys-live-core.service",
})


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
    }


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
    if len(requested) != len(set(requested)) or set(requested) - ALLOWED_WINDOWS_COMPONENTS:
        raise ValueError("components must be unique production Windows components")
    planned = sorted(set(plan["components"]) & ALLOWED_WINDOWS_COMPONENTS)
    if sorted(requested) != planned:
        raise ValueError(f"requested components do not match plan: expected {','.join(planned)}")
    if plan["unknown_paths"]:
        raise ValueError("deployment plan contains unknown paths")
    return planned


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
    return _wait_healthy(config, units, repo)


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
    return root / "record.json"


def deploy(args: argparse.Namespace, config: dict[str, object]) -> int:
    if not RUN_ID_PATTERN.fullmatch(args.run_id):
        raise ValueError("run-id contains unsupported characters")
    if not ACTOR_PATTERN.fullmatch(args.actor):
        raise ValueError("actor contains unsupported characters")
    if not REPOSITORY_PATTERN.fullmatch(args.repository):
        raise ValueError("repository must be owner/name")
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
    preview = {
        "ok": True, "dry_run": args.dry_run, "before_sha": before,
        "target_sha": args.sha, "source_tree": tree, "test_run_id": test_run,
        "components": components, "restart_units": active_units,
        "inactive_units": sorted(set(units) - set(active_units)),
        "health_checks": plan["health_checks"], "rollback_sha": before,
    }
    if args.dry_run:
        print(json.dumps(preview, ensure_ascii=False))
        return 0

    started = datetime.now(UTC).isoformat()
    checks: list[dict[str, str]] = []
    success = False
    rollback_ok = False
    fingerprints: dict[str, str] = {}
    try:
        if before != args.sha:
            _run(["git", "reset", "--hard", args.sha], cwd=repo)
            _run(["/bin/sh", "scripts/preflight_linux_deploy.sh"], cwd=repo)
            fingerprints = _fingerprints(config, repo)
            for unit in active_units:
                _run(["sudo", "-n", "systemctl", "restart", unit], cwd=repo)
        success = _head(repo) == args.sha and _wait_healthy(config, active_units, repo)
        checks.append({
            "name": "production-health", "status": "passed" if success else "failed",
            "detail": "active affected units and local health endpoint must be healthy",
        })
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        checks.append({"name": "deploy-command", "status": "failed", "detail": str(exc)[:500]})
    if not success and _head(repo) != before:
        rollback_ok = _restore(config, repo, before, active_units)
    state = "success" if success else ("rolled_back" if rollback_ok else "failure")
    after = _head(repo)
    record = {
        "schema_version": 1, "run_id": args.run_id, "environment": "production",
        "status": state, "actor": args.actor, "started_at": started,
        "completed_at": datetime.now(UTC).isoformat(), "repository": args.repository,
        "pr_number": args.pr_number, "source_sha": args.sha, "source_tree": tree,
        "components": components, "before": {"linux": before}, "after": {"linux": after},
        "config_fingerprints": fingerprints, "checks": checks,
        "rollback_targets": {"linux": before}, "migration_summary": "none",
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
    repo = Path(str(config["production_repo"])).resolve(strict=True)
    if _branch(repo) != "main" or _dirty(repo):
        raise ValueError("production checkout must be a clean main worktree")
    before_linux = _head(repo)
    tree = _fetch_and_validate_target(repo, args.sha)
    test_run = _successful_test_run(config, tree)
    plan = _plan(repo, before_linux, args.sha)
    components = _validate_windows_components(plan, args.components)
    apps = sorted(set(plan["windows_apps"]) & {"live_app", "paper_app"})
    if not apps:
        raise ValueError("production plan contains no Windows application")
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
    checks: list[dict[str, str]] = []
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
    print(json.dumps({"ok": success, "status": record["status"], "record": str(path)}))
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
    windows_parser = commands.add_parser("windows-deploy")
    windows_parser.add_argument("--sha", required=True)
    windows_parser.add_argument("--component", action="append", dest="components", required=True)
    windows_parser.add_argument("--run-id", required=True)
    windows_parser.add_argument("--actor", required=True)
    windows_parser.add_argument("--repository", required=True)
    windows_parser.add_argument("--pr-number", type=int)
    windows_parser.add_argument("--dry-run", action="store_true")
    record_parser = commands.add_parser("record")
    record_parser.add_argument("--run-id", required=True)
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
        if args.command == "windows-deploy":
            return windows_deploy(args, config)
        return deploy(args, config)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
