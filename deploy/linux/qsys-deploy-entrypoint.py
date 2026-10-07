#!/usr/bin/env python3
"""Fixed Linux deployment gateway installed outside the Git worktree."""

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

CONFIG_PATH = Path("/etc/qsys/deployment-entrypoint.json")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ALLOWED_COMPONENTS = {"test": frozenset({"test-runtime"})}


def validate_request(environment: str, sha: str, components: list[str]) -> None:
    if environment not in ALLOWED_COMPONENTS:
        raise ValueError(f"environment is not enabled: {environment}")
    if not SHA_PATTERN.fullmatch(sha):
        raise ValueError("sha must be a full 40-character lowercase commit SHA")
    requested = frozenset(components)
    if not requested or len(requested) != len(components):
        raise ValueError("components must be non-empty and unique")
    if not requested <= ALLOWED_COMPONENTS[environment]:
        raise ValueError("request contains a component not allowed for this environment")


def load_config() -> dict[str, object]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    required = {
        "schema_version", "test_repo", "test_record_root", "fingerprint_key",
        "uv_bin", "test_service", "test_health_url",
    }
    if set(config) != required or config["schema_version"] != 1:
        raise ValueError("entrypoint config must match schema version 1")
    for key in required - {"schema_version", "test_health_url", "test_service"}:
        value = config[key]
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError(f"{key} must be an absolute path")
    if not all(isinstance(config[key], str) and config[key] for key in (
        "test_health_url", "test_service",
    )):
        raise ValueError("service and health URL must be non-empty strings")
    return config


def _run(command: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=check, text=True, capture_output=True)


def _head(repo: Path) -> str:
    return _run(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()


def _healthy(config: dict[str, object], repo: Path) -> bool:
    service = _run(
        ["systemctl", "is-active", str(config["test_service"])], cwd=repo, check=False,
    )
    if service.returncode != 0 or service.stdout.strip() != "active":
        return False
    try:
        with urllib.request.urlopen(str(config["test_health_url"]), timeout=5) as response:
            return response.status == 200
    except OSError:
        return False


def _isolated(config: dict[str, object], repo: Path) -> bool:
    service = str(config["test_service"])

    def property_value(name: str) -> str:
        return _run(
            ["systemctl", "show", service, f"--property={name}", "--value"], cwd=repo,
            check=False,
        ).stdout.strip()

    working = property_value("WorkingDirectory")
    read_only = property_value("ReadOnlyPaths").split()
    read_write = property_value("ReadWritePaths").split()
    return (
        working == "/home/ubuntu/qsys-test"
        and "/data/qsys-test/warehouse-current" in read_only
        and "/data/qsys-test/var" in read_write
        and all(path != "/data/qsys" and not path.startswith("/data/qsys/") for path in read_write)
    )


def _wait_healthy(
    config: dict[str, object], repo: Path, *, attempts: int = 20,
) -> bool:
    for attempt in range(attempts):
        if _healthy(config, repo):
            return True
        if attempt + 1 < attempts:
            time.sleep(1)
    return False


def status(config: dict[str, object]) -> dict[str, object]:
    repo = Path(str(config["test_repo"])).resolve(strict=True)
    dirty = bool(_run(
        ["git", "status", "--porcelain", "--untracked-files=normal"], cwd=repo,
    ).stdout)
    return {
        "environment": "test",
        "sha": _head(repo),
        "branch": _run(["git", "branch", "--show-current"], cwd=repo).stdout.strip(),
        "dirty": dirty,
        "healthy": _healthy(config, repo),
        "isolated": _isolated(config, repo),
    }


def _rollback(config: dict[str, object], repo: Path, before: str) -> bool:
    commands = (
        ["git", "reset", "--hard", before],
        [str(config["uv_bin"]), "sync", "--frozen"],
        ["sudo", "-n", "systemctl", "restart", str(config["test_service"])],
    )
    try:
        for command in commands:
            _run(command, cwd=repo)
    except subprocess.CalledProcessError:
        return False
    return _wait_healthy(config, repo)


def _fingerprints(config: dict[str, object], repo: Path) -> dict[str, str]:
    command = [
        str(config["uv_bin"]), "run", "--frozen", "python",
        "scripts/deployment_contract.py", "validate-config", "--environment", "test",
        "--config", "config/deploy.local.toml", "--fingerprint-key-file",
        str(config["fingerprint_key"]),
    ]
    result = json.loads(_run(command, cwd=repo).stdout)
    if not result.get("ok"):
        raise ValueError("test configuration validation failed")
    return {f"linux_{key}": value for key, value in result["fingerprints"].items()}


def _write_record(config: dict[str, object], run_id: str, record: dict[str, Any]) -> Path:
    root = Path(str(config["test_record_root"])) / run_id
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
    validate_request(args.environment, args.sha, args.components)
    if not RUN_ID_PATTERN.fullmatch(args.run_id):
        raise ValueError("run-id contains unsupported characters")
    if not REPOSITORY_PATTERN.fullmatch(args.repository):
        raise ValueError("repository must be owner/name")
    repo = Path(str(config["test_repo"])).resolve(strict=True)
    before = _head(repo)
    if before == args.sha:
        print(json.dumps({"ok": True, "noop": True, "sha": args.sha}))
        return 0
    started = datetime.now(UTC).isoformat()
    deployer = repo / "scripts" / "deploy_test_linux.sh"
    if not deployer.is_file():
        raise ValueError(f"fixed deployer is missing: {deployer}")
    completed = subprocess.run(["/bin/sh", os.fspath(deployer), args.sha], cwd=repo)
    rollback_ok = False
    if completed.returncode != 0:
        rollback_ok = _rollback(config, repo, before)
    after = _head(repo)
    target_health = _wait_healthy(config, repo)
    success = (
        completed.returncode == 0 and after == args.sha
        and target_health and _isolated(config, repo)
    )
    if not success and not rollback_ok and after != before:
        rollback_ok = _rollback(config, repo, before)
        after = _head(repo)
    state = "success" if success else ("rolled_back" if rollback_ok else "failure")
    tree = _run(["git", "rev-parse", f"{args.sha}^{{tree}}"], cwd=repo).stdout.strip()
    try:
        fingerprints = _fingerprints(config, repo)
    except (OSError, ValueError, subprocess.CalledProcessError):
        fingerprints = {}
    final_health = _healthy(config, repo)
    final_isolation = _isolated(config, repo)
    record = {
        "schema_version": 1,
        "run_id": args.run_id,
        "environment": "test",
        "status": state,
        "actor": args.actor,
        "started_at": started,
        "completed_at": datetime.now(UTC).isoformat(),
        "repository": args.repository,
        "pr_number": args.pr_number,
        "source_sha": args.sha,
        "source_tree": tree,
        "components": args.components,
        "before": {"linux": before},
        "after": {"linux": after},
        "config_fingerprints": fingerprints,
        "checks": [
            {"name": "deploy-command", "status": "passed" if completed.returncode == 0 else "failed", "detail": f"exit {completed.returncode}"},
            {"name": "test-web-health", "status": "passed" if final_health else "failed", "detail": "service active and HTTP 200 required"},
            {"name": "test-storage-isolation", "status": "passed" if final_isolation else "failed", "detail": "test worktree, read-only warehouse and test-only write path required"},
        ],
        "rollback_targets": {"linux": before},
        "migration_summary": "none",
    }
    record_path = _write_record(config, args.run_id, record)
    print(json.dumps({"ok": success, "status": state, "record": str(record_path)}))
    return 0 if success else 1


def attest(args: argparse.Namespace, config: dict[str, object]) -> int:
    """Record a tested control-plane tree without changing the runtime checkout."""
    if not SHA_PATTERN.fullmatch(args.sha):
        raise ValueError("sha must be a full 40-character lowercase commit SHA")
    if not RUN_ID_PATTERN.fullmatch(args.run_id):
        raise ValueError("run-id contains unsupported characters")
    if not REPOSITORY_PATTERN.fullmatch(args.repository):
        raise ValueError("repository must be owner/name")
    repo = Path(str(config["test_repo"])).resolve(strict=True)
    _run(["git", "fetch", "--no-tags", "origin", "test"], cwd=repo)
    remote = _run(["git", "rev-parse", "origin/test"], cwd=repo).stdout.strip()
    if remote != args.sha:
        raise ValueError(f"origin/test is {remote}, requested {args.sha}")
    node = status(config)
    if node["dirty"] or not node["healthy"] or not node["isolated"]:
        raise ValueError("test node is not healthy and isolated")
    tree = _run(["git", "rev-parse", f"{args.sha}^{{tree}}"], cwd=repo).stdout.strip()
    now = datetime.now(UTC).isoformat()
    record = {
        "schema_version": 1,
        "run_id": args.run_id,
        "environment": "test",
        "status": "success",
        "actor": args.actor,
        "started_at": now,
        "completed_at": now,
        "repository": args.repository,
        "pr_number": args.pr_number,
        "source_sha": args.sha,
        "source_tree": tree,
        "components": [],
        "before": {"linux": str(node["sha"])},
        "after": {"linux": str(node["sha"])},
        "config_fingerprints": _fingerprints(config, repo),
        "checks": [{
            "name": "control-plane-attestation",
            "status": "passed",
            "detail": "trusted planner selected no test runtime component; node remains healthy and isolated",
        }],
        "rollback_targets": {"linux": str(node["sha"])},
        "migration_summary": "none",
    }
    path = _write_record(config, args.run_id, record)
    print(json.dumps({"ok": True, "status": "success", "record": str(path)}))
    return 0


def read_record(config: dict[str, object], run_id: str) -> dict[str, Any]:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run-id contains unsupported characters")
    path = Path(str(config["test_record_root"])) / run_id / "record.json"
    return json.loads(path.read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    status_parser = commands.add_parser("status")
    status_parser.add_argument("--environment", choices=("test",), required=True)
    deploy_parser = commands.add_parser("deploy")
    deploy_parser.add_argument("--environment", choices=("test",), required=True)
    deploy_parser.add_argument("--sha", required=True)
    deploy_parser.add_argument("--component", action="append", dest="components", required=True)
    deploy_parser.add_argument("--run-id", required=True)
    deploy_parser.add_argument("--actor", required=True)
    deploy_parser.add_argument("--repository", required=True)
    deploy_parser.add_argument("--pr-number", type=int)
    attest_parser = commands.add_parser("attest")
    attest_parser.add_argument("--environment", choices=("test",), required=True)
    attest_parser.add_argument("--sha", required=True)
    attest_parser.add_argument("--run-id", required=True)
    attest_parser.add_argument("--actor", required=True)
    attest_parser.add_argument("--repository", required=True)
    attest_parser.add_argument("--pr-number", type=int)
    record_parser = commands.add_parser("record")
    record_parser.add_argument("--environment", choices=("test",), required=True)
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
        if args.command == "attest":
            return attest(args, config)
        return deploy(args, config)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
