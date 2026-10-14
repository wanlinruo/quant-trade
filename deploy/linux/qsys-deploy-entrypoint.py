#!/usr/bin/env python3
"""Fixed Linux deployment gateway installed outside the Git worktree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

CONFIG_PATH = Path("/etc/qsys/deployment-entrypoint.json")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ACTOR_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
ALLOWED_COMPONENTS = {"test": frozenset({"test-runtime"})}
ENTRYPOINT_VERSION = 2
ENTRYPOINT_CAPABILITIES = ("research-stage-v1",)
RESEARCH_RELEASE_ROOT = Path("/opt/qsys-research-test/releases")
RESEARCH_CURRENT = Path("/opt/qsys-research-test/current")
RESEARCH_SNAPSHOT_STORE = Path("/data/qsys-test/warehouse-snapshot-store")
RESEARCH_ARCHIVE_LIMIT = 256 * 1024 * 1024


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
    research_sha = _current_research_sha()
    return {
        "environment": "test",
        "sha": _head(repo),
        "branch": _run(["git", "branch", "--show-current"], cwd=repo).stdout.strip(),
        "dirty": dirty,
        "healthy": _healthy(config, repo),
        "isolated": _isolated(config, repo),
        "entrypoint_version": ENTRYPOINT_VERSION,
        "capabilities": list(ENTRYPOINT_CAPABILITIES),
        "research_runner_test": research_sha,
    }


def _current_research_sha() -> str:
    research_sha = "uninstalled"
    if RESEARCH_CURRENT.is_symlink():
        try:
            candidate = RESEARCH_CURRENT.resolve(strict=True).name
            research_sha = candidate if SHA_PATTERN.fullmatch(candidate) else "unknown"
        except OSError:
            research_sha = "broken"
    return research_sha


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


def _target_deployer(repo: Path, sha: str) -> str:
    """Load the deployer from the exact verified test target.

    The node checkout can legitimately have divergent ancestry after `test` is
    squash-aligned. Loading the script from the target lets deployment logic
    bootstrap its own checkout transition without trusting a floating ref.
    """
    _run(["git", "fetch", "--no-tags", "origin", "test"], cwd=repo)
    remote = _run(["git", "rev-parse", "origin/test"], cwd=repo).stdout.strip()
    if remote != sha:
        raise ValueError(f"origin/test is {remote}, requested {sha}")
    return _run(["git", "show", f"{sha}:scripts/deploy_test_linux.sh"], cwd=repo).stdout


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
    deployer = _target_deployer(repo, args.sha)
    completed = subprocess.run(
        ["/bin/sh", "-s", "--", args.sha], cwd=repo, text=True, input=deployer,
    )
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _receive_research_archive(expected: str, root: Path) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("artifact SHA-256 must be 64 lowercase hexadecimal characters")
    target = root / "research.zip"
    digest = hashlib.sha256()
    size = 0
    with target.open("wb") as output:
        while block := sys.stdin.buffer.read(1024 * 1024):
            size += len(block)
            if size > RESEARCH_ARCHIVE_LIMIT:
                raise ValueError("research artifact exceeds the size limit")
            digest.update(block)
            output.write(block)
    if size == 0 or digest.hexdigest() != expected:
        raise ValueError("research artifact SHA-256 mismatch")
    return target


def _extract_research_archive(archive_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        files: dict[str, zipfile.ZipInfo] = {}
        total = 0
        for item in archive.infolist():
            path = Path(item.filename)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("research artifact contains an unsafe path")
            if stat.S_ISLNK((item.external_attr >> 16) & 0o170000):
                raise ValueError("research artifact must not contain symlinks")
            if item.is_dir():
                continue
            if item.filename in files:
                raise ValueError("research artifact contains duplicate files")
            files[item.filename] = item
            total += item.file_size
        wheels = [
            name for name in files
            if Path(name).parent == Path("dist") and name.endswith(".whl")
        ]
        if set(files) != {"release.json", *wheels} or len(wheels) != 1:
            raise ValueError("research artifact must contain release.json and one wheel")
        if total > RESEARCH_ARCHIVE_LIMIT:
            raise ValueError("research artifact expands beyond the size limit")
        for name, item in files.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _stage_research_release(
    config: dict[str, object], worktree: Path, sha: str, extracted: Path,
) -> Path:
    release = RESEARCH_RELEASE_ROOT / sha
    manifest = json.loads((extracted / "release.json").read_text(encoding="utf-8"))
    wheel_name = manifest.get("wheel_filename")
    if not isinstance(wheel_name, str) or Path(wheel_name).name != wheel_name:
        raise ValueError("research release wheel filename is invalid")
    relative = f"dist/{wheel_name}"
    verifier = [
        sys.executable, "scripts/build_research_release.py", "--repo", str(worktree),
        "verify", "--sha", sha, "--release", str(release),
    ]
    if release.exists():
        _run(verifier, cwd=worktree)
        return release
    _run([
        "sudo", "-n", "install", "-d", "-o", "ubuntu", "-g", "ubuntu",
        "-m", "0755", str(release), str(release / "dist"),
    ], cwd=worktree)
    try:
        for item in ("release.json", relative):
            _run([
                "sudo", "-n", "install", "-o", "ubuntu", "-g", "ubuntu",
                "-m", "0644", str(extracted / item), str(release / item),
            ], cwd=worktree)
        environment = dict(os.environ)
        environment["UV_PROJECT_ENVIRONMENT"] = str(release / "venv")
        subprocess.run([
            str(config["uv_bin"]), "sync", "--frozen", "--no-dev",
            "--no-install-project",
        ], cwd=worktree, env=environment, check=True, text=True, capture_output=True)
        _run([
            str(config["uv_bin"]), "pip", "install", "--python",
            str(release / "venv/bin/python"), "--no-deps", str(release / relative),
        ], cwd=worktree)
        _run(verifier, cwd=worktree)
        _run(["chmod", "-R", "go-w", str(release)], cwd=worktree)
        return release
    except (OSError, ValueError, subprocess.CalledProcessError):
        _run(["sudo", "-n", "rm", "-rf", "--", str(release)], cwd=worktree, check=False)
        raise


def _ensure_test_snapshot(
    worktree: Path, release: Path, sha: str, complete_through: str,
) -> str:
    try:
        date.fromisoformat(complete_through)
    except ValueError as exc:
        raise ValueError("snapshot complete-through must be an ISO date") from exc
    current = RESEARCH_SNAPSHOT_STORE / "current"
    if current.is_symlink():
        result = _run([
            str(release / "venv/bin/qsys-warehouse-snapshot"),
            "show-current", "--store", str(RESEARCH_SNAPSHOT_STORE),
        ], cwd=worktree)
        value = json.loads(result.stdout)
        snapshot_id = str(value["snapshot_id"])
        _run([
            str(release / "venv/bin/qsys-warehouse-snapshot"), "verify",
            "--snapshot", str(RESEARCH_SNAPSHOT_STORE / "snapshots" / snapshot_id),
        ], cwd=worktree)
        return snapshot_id
    _run([
        "sudo", "-n", "install", "-d", "-o", "ubuntu", "-g", "ubuntu",
        "-m", "0755", str(RESEARCH_SNAPSHOT_STORE),
    ], cwd=worktree)
    snapshot_id = f"test-{sha[:12]}"
    _run([
        str(release / "venv/bin/qsys-warehouse-snapshot"), "create",
        "--warehouse", "/data/qsys-test/warehouse-current",
        "--store", str(RESEARCH_SNAPSHOT_STORE),
        "--complete-through", complete_through, "--schema-version", "qsys-data-v1",
        "--snapshot-id", snapshot_id, "--publish-current",
    ], cwd=worktree)
    return snapshot_id


def _rollback_test_research(previous: str, repo: Path) -> bool:
    try:
        if SHA_PATTERN.fullmatch(previous):
            target = RESEARCH_RELEASE_ROOT / previous
            if not target.is_dir():
                return False
            temporary = RESEARCH_CURRENT.parent / f".rollback-{previous}"
            _run(["sudo", "-n", "ln", "-sfn", str(target), str(temporary)], cwd=repo)
            _run(["sudo", "-n", "mv", "-Tf", str(temporary), str(RESEARCH_CURRENT)], cwd=repo)
        elif previous == "uninstalled":
            _run(["sudo", "-n", "rm", "-f", "--", str(RESEARCH_CURRENT)], cwd=repo)
        else:
            return False
        return _current_research_sha() == previous
    except (OSError, ValueError, subprocess.CalledProcessError):
        return False


def research_stage(args: argparse.Namespace, config: dict[str, object]) -> int:
    if not SHA_PATTERN.fullmatch(args.sha):
        raise ValueError("sha must be a full 40-character lowercase commit SHA")
    if not RUN_ID_PATTERN.fullmatch(args.run_id):
        raise ValueError("run-id contains unsupported characters")
    if not ACTOR_PATTERN.fullmatch(args.actor):
        raise ValueError("actor contains unsupported characters")
    if not REPOSITORY_PATTERN.fullmatch(args.repository):
        raise ValueError("repository must be owner/name")
    repo = Path(str(config["test_repo"])).resolve(strict=True)
    node = status(config)
    if node["dirty"] or not node["healthy"] or not node["isolated"]:
        raise ValueError("test node is not healthy and isolated")
    _run(["git", "fetch", "--no-tags", "origin", "test"], cwd=repo)
    if _run(["git", "rev-parse", "origin/test"], cwd=repo).stdout.strip() != args.sha:
        raise ValueError("requested SHA is not the exact test branch head")
    tree = _run(["git", "rev-parse", f"{args.sha}^{{tree}}"], cwd=repo).stdout.strip()
    before = str(node["research_runner_test"])
    if args.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "source_tree": tree,
                          "before_research_runner_test": before}))
        return 0
    started = datetime.now(UTC).isoformat()
    checks: list[dict[str, str]] = []
    error = ""
    success = False
    rollback_ok = False
    with tempfile.TemporaryDirectory(prefix="qsys-research-stage-") as temporary:
        root = Path(temporary)
        try:
            archive = _receive_research_archive(args.artifact_sha256, root)
            extracted = root / "release"
            extracted.mkdir()
            _extract_research_archive(archive, extracted)
            worktree = root / "target"
            _run(["git", "worktree", "add", "--detach", str(worktree), args.sha], cwd=repo)
            try:
                release = _stage_research_release(config, worktree, args.sha, extracted)
                snapshot_id = _ensure_test_snapshot(
                    worktree, release, args.sha, args.snapshot_complete_through,
                )
                _run(["/bin/sh", "scripts/install_test_research_runner_isolation.sh", args.sha], cwd=worktree)
            finally:
                _run(["git", "worktree", "remove", "--force", str(worktree)], cwd=repo, check=False)
            if _run(["systemctl", "is-active", "qsys-research-runner-test.service"], cwd=repo, check=False).returncode == 0:
                raise ValueError("test Research Runner unexpectedly became active")
            enable_state = _run(
                ["systemctl", "is-enabled", "qsys-research-runner-test.service"],
                cwd=repo, check=False,
            ).stdout.strip()
            if enable_state in {"enabled", "enabled-runtime", "linked", "linked-runtime"}:
                raise ValueError("test Research Runner unexpectedly became enabled")
            if not _healthy(config, repo) or not _isolated(config, repo):
                raise ValueError("test web health or isolation changed during staging")
            checks = [
                {"name": "research-artifact", "status": "passed", "detail": f"sha256={args.artifact_sha256}"},
                {"name": "immutable-snapshot", "status": "passed", "detail": f"snapshot_id={snapshot_id}"},
                {"name": "runner-inactive", "status": "passed", "detail": "unit is neither active nor enabled"},
                {"name": "test-web-continuity", "status": "passed", "detail": "health and storage isolation preserved"},
            ]
            success = True
        except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
            error = str(exc)[:500]
            rollback_ok = _rollback_test_research(before, repo)
            checks.append({"name": "research-stage", "status": "failed", "detail": error})
    after = status(config)
    fully_rolled_back = rollback_ok and after["research_runner_test"] == before
    try:
        fingerprints = _fingerprints(config, repo)
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError):
        fingerprints = {}
    record = {
        "schema_version": 1, "run_id": args.run_id, "environment": "test",
        "status": "success" if success else ("rolled_back" if fully_rolled_back else "failure"), "actor": args.actor,
        "started_at": started, "completed_at": datetime.now(UTC).isoformat(),
        "repository": args.repository, "pr_number": args.pr_number,
        "source_sha": args.sha, "source_tree": tree,
        "components": ["research-runner-test"],
        "before": {"linux": str(node["sha"]), "research-runner-test": before},
        "after": {"linux": str(after["sha"]), "research-runner-test": str(after["research_runner_test"])},
        "config_fingerprints": fingerprints, "checks": checks,
        "rollback_targets": {"research-runner-test": before},
        "migration_summary": "test-only immutable snapshot and inactive Runner staging",
    }
    path = _write_record(config, args.run_id, record)
    print(json.dumps({"ok": success, "status": record["status"], "record": str(path), "error": error}))
    return 0 if success else 1


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
    research_parser = commands.add_parser("research-stage")
    research_parser.add_argument("--sha", required=True)
    research_parser.add_argument("--artifact-sha256", default="")
    research_parser.add_argument("--snapshot-complete-through", required=True)
    research_parser.add_argument("--run-id", required=True)
    research_parser.add_argument("--actor", required=True)
    research_parser.add_argument("--repository", required=True)
    research_parser.add_argument("--pr-number", type=int)
    research_parser.add_argument("--dry-run", action="store_true")
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
        if args.command == "research-stage":
            return research_stage(args, config)
        return deploy(args, config)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
