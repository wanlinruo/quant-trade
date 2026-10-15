"""Fail-closed host checks and cleanup hooks for the qsys CI runner.

Install this file outside the Actions runner application directory and expose it
through ACTIONS_RUNNER_HOOK_JOB_STARTED / ACTIONS_RUNNER_HOOK_JOB_COMPLETED.
The hook deliberately has no GitHub API credentials and never reads repository
or production secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
from pathlib import Path


DEFAULT_WORK_ROOT = Path("/var/lib/qsys-ci-runner/_work")
FORBIDDEN_PATHS = (
    Path("/data/qsys"),
    Path("/etc/qsys"),
    Path("/opt/qsys/components"),
    Path("/home/ubuntu/qsys"),
    Path("/home/ubuntu/qsys-test"),
)
FORBIDDEN_ENV_NAMES = {
    "PRODUCTION_DEPLOY_SSH_KEY",
    "TEST_DEPLOY_SSH_KEY",
    "WINDOWS_DEPLOY_SSH_KEY",
    "REDIS_URL",
    "QMT_ACCOUNT_ID",
    "TUSHARE_TOKEN",
}


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
    except ValueError:
        return False
    return True


def audit_environment(
    *,
    work_root: Path,
    forbidden_paths: tuple[Path, ...] = FORBIDDEN_PATHS,
    environ: dict[str, str] | None = None,
) -> dict[str, object]:
    env = dict(os.environ if environ is None else environ)
    errors: list[str] = []
    if platform.system() != "Linux":
        errors.append("runner OS must be Linux")
    if platform.machine().lower() not in {"x86_64", "amd64"}:
        errors.append("runner architecture must be x64")
    if not work_root.is_absolute():
        errors.append("work root must be absolute")
    if any(path.exists() for path in forbidden_paths):
        errors.append("production or shared qsys paths exist on the CI host")
    leaked = sorted(FORBIDDEN_ENV_NAMES.intersection(env))
    if leaked:
        errors.append("forbidden production environment variables are present")
    github_workspace = env.get("GITHUB_WORKSPACE")
    if github_workspace and not _inside(Path(github_workspace), work_root):
        errors.append("GITHUB_WORKSPACE escapes the dedicated work root")
    runner_temp = env.get("RUNNER_TEMP")
    if runner_temp and not _inside(Path(runner_temp), work_root):
        errors.append("RUNNER_TEMP escapes the dedicated work root")
    return {
        "ok": not errors,
        "errors": errors,
        "forbidden_environment_names": leaked,
        "work_root": str(work_root),
    }


def _remove_children(path: Path, *, work_root: Path) -> int:
    if not _inside(path, work_root):
        raise ValueError(f"refusing to clean outside work root: {path}")
    if not path.exists():
        return 0
    removed = 0
    for child in path.iterdir():
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)
        else:
            raise ValueError(f"unsupported workspace entry: {child}")
        removed += 1
    return removed


def clean_job(*, work_root: Path, environ: dict[str, str] | None = None) -> dict[str, object]:
    env = dict(os.environ if environ is None else environ)
    cleaned: dict[str, int] = {}
    for variable in ("GITHUB_WORKSPACE", "RUNNER_TEMP"):
        value = env.get(variable)
        if value:
            cleaned[variable] = _remove_children(Path(value), work_root=work_root)
    return {"ok": True, "cleaned": cleaned}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("preflight", "cleanup"))
    parser.add_argument(
        "--work-root",
        type=Path,
        default=Path(os.environ.get("QSYS_CI_WORK_ROOT", DEFAULT_WORK_ROOT)),
    )
    args = parser.parse_args()
    if args.action == "preflight":
        result = audit_environment(work_root=args.work_root)
    else:
        result = clean_job(work_root=args.work_root)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
