#!/usr/bin/env python3
"""Build and coordinate Windows releases from the trusted Linux node."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from typing import Any

CONFIG_PATH = Path("/etc/qsys/windows-deployment-coordinator.json")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
APP_COMPONENTS = {"live_app": "windows-agent", "paper_app": "windows-gateway"}
WINDOWS_CONTROLLER_PATH = "deploy/windows/qsys-windows-deploy.py"


def _builder_module(repo: Path, sha: str):
    source = subprocess.check_output(
        ["git", "show", f"{sha}:scripts/build_windows_release.py"], cwd=repo, text=True,
    )
    module = types.ModuleType("qsys_windows_release_builder")
    module.__file__ = f"{repo}@{sha}:scripts/build_windows_release.py"
    sys.modules[module.__name__] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def _git_blob(repo: Path, sha: str, path: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{sha}:{path}"], cwd=repo)


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version", "windows_host", "windows_key", "windows_python",
        "windows_entrypoint", "windows_config", "windows_incoming",
    }
    if set(value) != required or value["schema_version"] != 1:
        raise ValueError("Windows coordinator config must match schema version 1")
    if not Path(value["windows_key"]).is_absolute():
        raise ValueError("windows_key must be an absolute Linux path")
    return value


class Transport:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    @property
    def _ssh(self) -> list[str]:
        return [
            "ssh", "-i", self.config["windows_key"], "-o", "BatchMode=yes",
            self.config["windows_host"],
        ]

    def command(
        self, *arguments: str, check: bool = True, entrypoint: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            *self._ssh, self.config["windows_python"],
            entrypoint or self.config["windows_entrypoint"],
            "--config", self.config["windows_config"], *arguments,
        ]
        return subprocess.run(command, check=check, text=True, capture_output=True)

    def copy(self, local: Path, remote_name: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", remote_name):
            raise ValueError("remote file name contains unsupported characters")
        remote = self.config["windows_incoming"].rstrip("\\/") + "/" + remote_name
        subprocess.run([
            "scp", "-i", self.config["windows_key"], "-o", "BatchMode=yes",
            str(local), f"{self.config['windows_host']}:{remote}",
        ], check=True, text=True, capture_output=True)
        return remote


def _json_result(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    value = json.loads(result.stdout)
    if not isinstance(value, dict) or not value.get("ok"):
        raise ValueError(f"Windows deployment command failed: {value}")
    return value


def coordinate(
    repo: Path, sha: str, run_id: str, apps: list[str], *, dry_run: bool,
    transport: Transport, failure_point: str = "none",
) -> dict[str, Any]:
    if not SHA_PATTERN.fullmatch(sha) or not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("target SHA or run-id is invalid")
    if not apps or len(apps) != len(set(apps)) or set(apps) - set(APP_COMPONENTS):
        raise ValueError("apps must be a unique non-empty supported subset")
    builder = _builder_module(repo, sha)
    plans: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="qsys-windows-release-") as temporary:
        root = Path(temporary)
        # Always execute the controller from the exact reviewed target SHA.
        # The installed controller remains an external bootstrap/emergency
        # entrypoint, but cannot otherwise update itself before a dry-run.
        controller_path = root / "qsys-windows-deploy.py"
        controller_path.write_bytes(_git_blob(repo, sha, WINDOWS_CONTROLLER_PATH))
        remote_controller = transport.copy(
            controller_path, f"{run_id}-qsys-windows-deploy.py",
        )
        status = _json_result(transport.command("status", entrypoint=remote_controller))
        for app in apps:
            current = status["apps"][app]["deployed_sha"]
            if not SHA_PATTERN.fullmatch(current):
                raise ValueError(f"{app} reported an invalid DEPLOYED_SHA")
            baseline = builder.manifest(repo, current, app)
            baseline_path = root / f"{app}-baseline.json"
            baseline_path.write_text(
                json.dumps(baseline, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            archive_path = root / f"{app}-{sha}.zip"
            target = builder.build(repo, sha, app, archive_path)
            remote_archive = transport.copy(archive_path, f"{run_id}-{app}.zip")
            remote_baseline = transport.copy(baseline_path, f"{run_id}-{app}-baseline.json")
            prepared = _json_result(transport.command(
                "prepare", "--run-id", run_id, "--app", app,
                "--archive", remote_archive, "--baseline-manifest", remote_baseline,
                entrypoint=remote_controller,
            ))
            plans[app] = {
                "component": APP_COMPONENTS[app], "before_sha": current,
                "target_sha": sha, "source_tree": target["source_tree"],
                "file_count": len(target["files"]), "prepare": prepared["status"],
            }
        if dry_run:
            return {"ok": True, "dry_run": True, "run_id": run_id, "apps": plans}
        deployed_run = transport.command(
            "deploy", "--run-id", run_id,
            *(argument for app in apps for argument in ("--app", app)),
            *([] if failure_point == "none" else ["--failure-point", failure_point]),
            check=False, entrypoint=remote_controller,
        )
        record_run = transport.command(
            "record", "--run-id", run_id, check=False, entrypoint=remote_controller,
        )
        record = json.loads(record_run.stdout) if record_run.stdout.strip() else {
            "ok": False, "status": "failure", "error": record_run.stderr.strip()[:500],
        }
        deployed = json.loads(deployed_run.stdout) if deployed_run.stdout.strip() else {
            "ok": False, "error": deployed_run.stderr.strip()[:500],
        }
        ok = deployed_run.returncode == 0 and record_run.returncode == 0
        return {
            "ok": ok, "dry_run": False, "run_id": run_id, "apps": plans,
            "result": deployed, "record": record,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--app", action="append", dest="apps", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--failure-point", choices=("none", "after-first-switch"), default="none",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        result = coordinate(
            args.repo.resolve(strict=True), args.sha, args.run_id, args.apps,
            dry_run=args.dry_run, transport=Transport(config),
            failure_point=args.failure_point,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("ok") else 1
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
