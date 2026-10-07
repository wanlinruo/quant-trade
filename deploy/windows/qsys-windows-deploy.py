#!/usr/bin/env python3
"""Fixed Windows prepare/switch/verify/rollback deployment entrypoint.

This file is installed outside live_app and paper_app.  It intentionally uses
only the Python standard library so replacing either application cannot replace
the controller that performs or compensates the switch.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import locale
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

CONFIG_PATH = Path(r"C:\ProgramData\qsys\windows-deployment.json")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
APP_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
IGNORED_NAMES = frozenset({"__pycache__"})
SENSITIVE_PARTS = (
    "account", "credential", "password", "secret", "token", "username", "webhook",
)
NATIVE_COMMAND_TIMEOUT_S = 30


def normalized_sha256(data: bytes) -> str:
    return hashlib.sha256(data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")).hexdigest()


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _redact(value: Any, *, key: str = "") -> Any:
    if any(part in key.lower() for part in SENSITIVE_PARTS):
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


def config_public_fingerprint(path: Path) -> str:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    redacted = json.dumps(
        _redact(raw), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()
    return hashlib.sha256(redacted).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def validate_manifest(value: dict[str, Any], *, app: str | None = None) -> dict[str, Any]:
    if set(value) != {"schema_version", "app", "source_sha", "source_tree", "files"}:
        raise ValueError("release manifest has unexpected fields")
    if value["schema_version"] != 1 or not SHA_PATTERN.fullmatch(value["source_sha"]):
        raise ValueError("release manifest version or source SHA is invalid")
    if not SHA_PATTERN.fullmatch(value["source_tree"]):
        raise ValueError("release manifest tree is invalid")
    if app is not None and value["app"] != app:
        raise ValueError("release manifest app does not match request")
    files = value["files"]
    if not isinstance(files, dict) or not files:
        raise ValueError("release manifest files must be a non-empty object")
    for name, digest in files.items():
        path = PurePosixPath(name)
        if (
            not isinstance(name, str) or path.is_absolute() or ".." in path.parts
            or not name.startswith(("src/", "scripts/"))
            or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise ValueError(f"invalid release manifest entry: {name!r}")
    return value


def files_under(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for prefix in ("src", "scripts"):
        folder = root / prefix
        if not folder.is_dir():
            raise ValueError(f"missing release directory: {folder}")
        for path in sorted(folder.rglob("*")):
            if not path.is_file() or any(part in IGNORED_NAMES for part in path.parts):
                continue
            if path.suffix.lower() in {".pyc", ".pyo"}:
                continue
            relative = path.relative_to(root).as_posix()
            files[relative] = normalized_sha256(path.read_bytes())
    return files


def verify_files(root: Path, release: dict[str, Any]) -> None:
    actual = files_under(root)
    expected = release["files"]
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    changed = sorted(path for path in set(actual) & set(expected) if actual[path] != expected[path])
    if missing or extra or changed:
        summary = {"missing": missing[:20], "extra": extra[:20], "changed": changed[:20]}
        raise ValueError(f"Windows application file drift: {json.dumps(summary)}")


def safe_extract(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe archive member: {info.filename}")
        archive.extractall(destination)


@dataclass(frozen=True)
class App:
    name: str
    root: Path
    task: str
    entrypoint: str
    heartbeat: str


@dataclass(frozen=True)
class Config:
    python: Path
    staging_root: Path
    release_root: Path
    watchdog_task: str
    apps: dict[str, App]


def load_config(path: Path = CONFIG_PATH) -> Config:
    raw = load_json(path)
    required = {"schema_version", "python", "staging_root", "release_root", "watchdog_task", "apps"}
    if set(raw) != required or raw["schema_version"] != 1:
        raise ValueError("Windows deployment config must match schema version 1")
    apps: dict[str, App] = {}
    for name, item in raw["apps"].items():
        if not APP_PATTERN.fullmatch(name) or set(item) != {
            "root", "task", "entrypoint", "heartbeat",
        }:
            raise ValueError(f"invalid Windows app configuration: {name!r}")
        apps[name] = App(name, Path(item["root"]), item["task"], item["entrypoint"], item["heartbeat"])
    if set(apps) != {"live_app", "paper_app"}:
        raise ValueError("Windows deployment config must define live_app and paper_app")
    return Config(
        python=Path(raw["python"]), staging_root=Path(raw["staging_root"]),
        release_root=Path(raw["release_root"]), watchdog_task=raw["watchdog_task"], apps=apps,
    )


class WindowsHost:
    def run(self, command: list[str], *, check: bool = True, cwd: Path | None = None,
            env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        # OpenSSH can enable Python UTF-8 mode even though native Windows tools
        # such as schtasks still emit the machine ANSI code page (GBK on the
        # production node).  Decode child output with the actual Windows code
        # page; configure_output_encoding separately keeps our JSON protocol
        # to the Linux coordinator deterministic UTF-8.
        return subprocess.run(command, check=check, cwd=cwd, env=env, text=True,
                              encoding=locale.getencoding(), capture_output=True,
                              timeout=NATIVE_COMMAND_TIMEOUT_S)

    def task(self, action: str, name: str, *options: str,
             check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.run(["schtasks", f"/{action}", "/tn", name, *options], check=check)

    def task_status(self, name: str) -> dict[str, object]:
        result = self.run(["schtasks", "/query", "/tn", name, "/fo", "CSV", "/v"])
        rows = [row for row in csv.reader(io.StringIO(result.stdout.strip())) if len(row) > 6]
        if len(rows) < 2:
            raise ValueError(f"cannot parse scheduled task status: {name}")
        row = rows[-1]
        try:
            last_result: int | str = int(row[6], 0)
        except ValueError:
            last_result = row[6]
        return {"state": row[3], "last_run": row[5], "last_result": last_result}

    def process_count(self, marker: str) -> int:
        escaped = marker.replace("'", "''")
        script = (
            "$items=Get-CimInstance Win32_Process -Filter \"Name like '%python%'\"|"
            f"Where-Object {{$_.CommandLine -and $_.CommandLine -like '*{escaped}*'}};"
            "@($items).Count"
        )
        result = self.run(["powershell", "-NoProfile", "-Command", script])
        return int(result.stdout.strip())


def _deployed_sha(app: App) -> str:
    value = (app.root / "DEPLOYED_SHA").read_text(encoding="ascii").strip()
    if not SHA_PATTERN.fullmatch(value):
        raise ValueError(f"{app.name} DEPLOYED_SHA is invalid")
    return value


def _operation_path(config: Config, run_id: str) -> Path:
    return config.staging_root / run_id / "operation.json"


def _load_operation(config: Config, run_id: str) -> dict[str, Any]:
    return load_json(_operation_path(config, run_id))


def prepare(config: Config, run_id: str, app_name: str, archive: Path,
            baseline_path: Path, host: WindowsHost) -> dict[str, object]:
    if not RUN_ID_PATTERN.fullmatch(run_id) or app_name not in config.apps:
        raise ValueError("invalid run-id or app")
    app = config.apps[app_name]
    baseline = validate_manifest(load_json(baseline_path), app=app_name)
    current = _deployed_sha(app)
    if baseline["source_sha"] != current:
        raise ValueError("baseline manifest does not match DEPLOYED_SHA")
    verify_files(app.root, baseline)

    run_root = config.staging_root / run_id
    stage = run_root / app_name
    if stage.exists():
        shutil.rmtree(stage)
    safe_extract(archive, stage)
    target = validate_manifest(load_json(stage / "release.json"), app=app_name)
    shutil.copy2(baseline_path, stage / "baseline.json")
    payload = stage / "payload"
    verify_files(payload, target)
    if target["source_sha"] == current:
        state = "noop"
    else:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(payload / "src")
        validation = (
            "import runpy; from qsys.live.config import load_deploy_config; "
            f"load_deploy_config(r'{app.root / 'config' / 'deploy.local.toml'}'); "
            f"runpy.run_path(r'{payload / app.entrypoint}', run_name='qsys_deploy_validation')"
        )
        host.run([str(config.python), "-c", validation], cwd=payload, env=env)
        state = "prepared"
    fingerprint = config_public_fingerprint(app.root / "config" / "deploy.local.toml")
    operation_path = _operation_path(config, run_id)
    operation = load_json(operation_path) if operation_path.exists() else {
        "schema_version": 1, "run_id": run_id, "status": "prepared", "apps": {},
    }
    operation["apps"][app_name] = {
        "before_sha": current, "target_sha": target["source_sha"], "state": state,
        "stage": str(stage), "backup": None, "switched_at": None,
        "baseline_manifest": str(stage / "baseline.json"),
        "config_public_sha256": fingerprint,
    }
    atomic_text(operation_path, json.dumps(operation, ensure_ascii=False, indent=2) + "\n")
    return {"ok": True, "app": app_name, "status": state, "before_sha": current,
            "target_sha": target["source_sha"]}


def _wait_stopped(host: WindowsHost, app: App, attempts: int = 20) -> None:
    for attempt in range(attempts):
        if host.process_count(app.entrypoint.replace("\\", "/").split("/")[-1]) == 0:
            return
        if attempt < attempts - 1:
            time.sleep(1)
    raise RuntimeError(f"{app.task} process did not stop")


def _swap_in(config: Config, run_id: str, app: App, item: dict[str, Any]) -> None:
    stage = Path(item["stage"])
    backup = config.release_root / app.name / f"{run_id}-{item['before_sha']}"
    backup.mkdir(parents=True, exist_ok=False)
    shutil.copy2(app.root / "DEPLOYED_SHA", backup / "DEPLOYED_SHA")
    current_manifest = app.root / ".qsys-release-manifest.json"
    if current_manifest.exists():
        shutil.copy2(current_manifest, backup / ".qsys-release-manifest.json")
    else:
        shutil.copy2(Path(item["baseline_manifest"]), backup / ".qsys-release-manifest.json")
    moved: list[str] = []
    try:
        for name in ("src", "scripts"):
            (app.root / name).replace(backup / name)
            moved.append(name)
            (stage / "payload" / name).replace(app.root / name)
        shutil.copy2(stage / "release.json", current_manifest)
        atomic_text(app.root / "DEPLOYED_SHA", f"{item['target_sha']}\n")
        shutil.copy2(backup / "DEPLOYED_SHA", app.root / f"DEPLOYED_SHA.prev-{item['before_sha']}")
    except Exception:
        for name in reversed(moved):
            if (app.root / name).exists():
                (app.root / name).replace(stage / "payload" / name)
            (backup / name).replace(app.root / name)
        shutil.rmtree(backup, ignore_errors=True)
        raise
    item["backup"] = str(backup)
    # A Unix timestamp avoids mixing Windows-local naive heartbeat timestamps
    # with the controller's timezone-aware audit timestamps.
    item["switched_at"] = str(time.time())
    item["state"] = "switched"


def _restore(app: App, item: dict[str, Any]) -> None:
    backup = Path(item["backup"])
    failed = backup.parent / f"failed-{Path(backup).name}"
    failed.mkdir(parents=True, exist_ok=False)
    for name in ("src", "scripts"):
        (app.root / name).replace(failed / name)
        (backup / name).replace(app.root / name)
    manifest = backup / ".qsys-release-manifest.json"
    if manifest.exists():
        shutil.copy2(manifest, app.root / ".qsys-release-manifest.json")
    else:
        (app.root / ".qsys-release-manifest.json").unlink(missing_ok=True)
    atomic_text(app.root / "DEPLOYED_SHA", f"{item['before_sha']}\n")
    item["state"] = "rolled_back"


def _heartbeat_probe(
    config: Config, app: App, switched_at: str, host: WindowsHost,
) -> subprocess.CompletedProcess[str]:
    script = app.root / "scripts" / "check_agent_alive.py"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(app.root / "src")
    return host.run([
        str(config.python), str(script), "--config", str(app.root / "config" / "deploy.local.toml"),
        "--source", app.heartbeat, "--max-age", "120", "--not-before-epoch", switched_at,
    ], cwd=app.root, env=env, check=False)


def _verify(config: Config, app: App, item: dict[str, Any], host: WindowsHost,
            attempts: int = 30) -> None:
    target = validate_manifest(load_json(app.root / ".qsys-release-manifest.json"), app=app.name)
    if _deployed_sha(app) != item["target_sha"] or target["source_sha"] != item["target_sha"]:
        raise ValueError(f"{app.name} target markers do not match")
    verify_files(app.root, target)
    for attempt in range(attempts):
        process_count = host.process_count(Path(app.entrypoint).name)
        heartbeat = _heartbeat_probe(config, app, item["switched_at"], host)
        item["verification"] = {
            "attempt": attempt + 1,
            "process_count": process_count,
            "heartbeat_returncode": heartbeat.returncode,
            "heartbeat_detail": (heartbeat.stdout or heartbeat.stderr).strip()[:500],
        }
        if process_count > 0 and heartbeat.returncode == 0:
            item["task"] = host.task_status(app.task)
            item["state"] = "verified"
            return
        if attempt < attempts - 1:
            time.sleep(2)
    raise RuntimeError(f"{app.name} task or fresh heartbeat did not recover")


def deploy(config: Config, run_id: str, app_names: list[str], host: WindowsHost) -> dict[str, object]:
    operation = _load_operation(config, run_id)
    if not app_names or len(app_names) != len(set(app_names)) or set(app_names) - set(config.apps):
        raise ValueError("apps must be a unique non-empty configured subset")
    items = operation["apps"]
    if any(name not in items or items[name]["state"] not in {"prepared", "noop"}
           for name in app_names):
        raise ValueError("every requested app must be prepared")
    changed = [name for name in app_names if items[name]["state"] != "noop"]
    if not changed:
        operation["status"] = "success"
        atomic_text(_operation_path(config, run_id), json.dumps(operation, indent=2) + "\n")
        return {"ok": True, "status": "success", "apps": app_names, "noop": True}

    watchdog_disabled = False
    switched: list[str] = []
    try:
        host.task("change", config.watchdog_task, "/disable")
        watchdog_disabled = True
        for name in changed:
            host.task("end", config.apps[name].task, check=False)
        for name in changed:
            _wait_stopped(host, config.apps[name])
        for name in changed:
            _swap_in(config, run_id, config.apps[name], items[name])
            switched.append(name)
        atomic_text(_operation_path(config, run_id), json.dumps(operation, indent=2) + "\n")
        for name in changed:
            host.task("run", config.apps[name].task)
        for name in changed:
            _verify(config, config.apps[name], items[name], host)
        operation["status"] = "success"
    except Exception:
        rollback_ok = True
        for name in reversed(switched):
            app = config.apps[name]
            item = items[name]
            try:
                host.task("end", app.task, check=False)
                _wait_stopped(host, app)
                _restore(app, item)
            except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
                item["rollback_error"] = str(exc)[:500]
                item["state"] = "rollback_failed"
                rollback_ok = False
        # Every changed task was stopped before switching began.  Restart and
        # verify apps whose files were restored as well as apps that never
        # reached the switch, otherwise a partial switch failure can leave the
        # untouched application offline.
        for name in changed:
            app = config.apps[name]
            item = items[name]
            if item["state"] == "rollback_failed":
                continue
            rollback_probe = dict(item)
            rollback_probe["target_sha"] = item["before_sha"]
            try:
                rollback_probe["switched_at"] = str(time.time())
                host.task("run", app.task, check=False)
                _verify(config, app, rollback_probe, host)
                item["rollback_task"] = rollback_probe.get("task", {})
                item["state"] = "rolled_back"
            except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
                item["rollback_error"] = str(exc)[:500]
                item["state"] = "rollback_failed"
                rollback_ok = False
        operation["status"] = "rolled_back" if rollback_ok else "failure"
        raise
    finally:
        if watchdog_disabled:
            enabled = host.task("change", config.watchdog_task, "/enable", check=False)
            watchdog = host.task_status(config.watchdog_task)
            disabled_states = {"disabled", "已禁用", "已停用"}
            restored = enabled.returncode == 0 and str(watchdog["state"]).lower() not in disabled_states
            operation["watchdog"] = watchdog
            operation["watchdog_restored"] = restored
        else:
            operation["watchdog_restored"] = True
        operation["completed_at"] = datetime.now(UTC).isoformat()
        atomic_text(_operation_path(config, run_id), json.dumps(operation, indent=2) + "\n")
        if not operation["watchdog_restored"]:
            raise RuntimeError("qsys-watchdog could not be restored to enabled")
    return {"ok": True, "status": operation["status"], "apps": app_names, "noop": False}


def status(config: Config, host: WindowsHost) -> dict[str, object]:
    apps: dict[str, object] = {}
    for name, app in config.apps.items():
        deployed = _deployed_sha(app)
        manifest_path = app.root / ".qsys-release-manifest.json"
        apps[name] = {
            "deployed_sha": deployed,
            "task": host.task_status(app.task),
            "process_count": host.process_count(Path(app.entrypoint).name),
            "manifest_present": manifest_path.is_file(),
        }
    return {"ok": True, "apps": apps, "watchdog": host.task_status(config.watchdog_task)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--run-id", required=True)
    prepare_parser.add_argument("--app", required=True)
    prepare_parser.add_argument("--archive", type=Path, required=True)
    prepare_parser.add_argument("--baseline-manifest", type=Path, required=True)
    deploy_parser = commands.add_parser("deploy")
    deploy_parser.add_argument("--run-id", required=True)
    deploy_parser.add_argument("--app", action="append", dest="apps", required=True)
    record_parser = commands.add_parser("record")
    record_parser.add_argument("--run-id", required=True)
    return parser


def configure_output_encoding() -> None:
    # Windows OpenSSH inherits the machine's legacy console code page.  Keep
    # child-process decoding on that native code page, but make the controller's
    # JSON protocol deterministic for the UTF-8 Linux coordinator.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    configure_output_encoding()
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        host = WindowsHost()
        if args.command == "status":
            result = status(config, host)
        elif args.command == "record":
            result = {"ok": True, **_load_operation(config, args.run_id)}
        elif args.command == "prepare":
            result = prepare(config, args.run_id, args.app, args.archive,
                             args.baseline_manifest, host)
        else:
            result = deploy(config, args.run_id, args.apps, host)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError,
            zipfile.BadZipFile) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
