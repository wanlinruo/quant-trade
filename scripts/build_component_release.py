#!/usr/bin/env python3
"""Build or verify an immutable qsys component release manifest."""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from email.parser import BytesParser
from pathlib import Path

FORMAT = "qsys-component-release"
VERSION = 1
SHA = re.compile(r"^[0-9a-f]{40}$")
COMPONENT_ENTRYPOINTS = {
    "catalog-verify": ("qsys-catalog-verify", "qsys.data.catalog_verify_cli:main"),
}
REQUIRED = frozenset({
    "format", "version", "component", "entrypoint", "source_sha", "source_tree",
    "wheel_sha256", "wheel_bytes", "pyproject_sha256", "uv_lock_sha256", "ci",
})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 << 20):
            digest.update(block)
    return digest.hexdigest()


def _git(repo: Path, *args: str, text: bool = True) -> str | bytes:
    return subprocess.check_output(["git", *args], cwd=repo, text=text).strip()


def _require_checkout(repo: Path, sha: str) -> None:
    if not SHA.fullmatch(sha):
        raise ValueError("release SHA must be a full lowercase commit SHA")
    if _git(repo, "rev-parse", "HEAD") != sha:
        raise ValueError("release SHA must be the exact checked-out HEAD")
    if _git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("release checkout has tracked modifications")


def _validate_wheel(path: Path, component: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("wheel must be a regular non-symlink file")
    command, target = COMPONENT_ENTRYPOINTS[component]
    with zipfile.ZipFile(path) as archive:
        metadata = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        entries = [name for name in archive.namelist()
                   if name.endswith(".dist-info/entry_points.txt")]
        if len(metadata) != 1 or len(entries) != 1:
            raise ValueError("wheel must contain one metadata and entry-points file")
        package = BytesParser().parsebytes(archive.read(metadata[0]))
        if package.get("Name", "").lower() != "qsys":
            raise ValueError("wheel package name must be qsys")
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(archive.read(entries[0]).decode("utf-8"))
        if config.get("console_scripts", command, fallback="") != target:
            raise ValueError(f"wheel does not expose the {command} entrypoint")


def build(
    repo: Path, sha: str, component: str, dist: Path, output: Path, *,
    repository: str, run_id: str, run_attempt: str,
) -> dict[str, object]:
    repo = repo.resolve(strict=True)
    _require_checkout(repo, sha)
    if component not in COMPONENT_ENTRYPOINTS:
        raise ValueError(f"unsupported component: {component}")
    wheels = sorted(dist.glob("qsys-*.whl"))
    if len(wheels) != 1:
        raise ValueError("dist must contain exactly one qsys wheel")
    _validate_wheel(wheels[0], component)
    if output.exists() and any(output.iterdir()):
        raise ValueError("release output directory must be empty")
    (output / "dist").mkdir(parents=True, exist_ok=True)
    wheel = output / "dist" / "qsys.whl"
    shutil.copyfile(wheels[0], wheel)
    command, target = COMPONENT_ENTRYPOINTS[component]
    value: dict[str, object] = {
        "format": FORMAT,
        "version": VERSION,
        "component": component,
        "entrypoint": {"command": command, "target": target},
        "source_sha": sha,
        "source_tree": _git(repo, "rev-parse", f"{sha}^{{tree}}"),
        "wheel_sha256": _sha256(wheel),
        "wheel_bytes": wheel.stat().st_size,
        "pyproject_sha256": hashlib.sha256(
            _git(repo, "show", f"{sha}:pyproject.toml", text=False)
        ).hexdigest(),
        "uv_lock_sha256": hashlib.sha256(
            _git(repo, "show", f"{sha}:uv.lock", text=False)
        ).hexdigest(),
        "ci": {"repository": repository, "run_id": run_id, "run_attempt": run_attempt},
    }
    (output / "release.json").write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return value


def verify(repo: Path, sha: str, component: str, release: Path) -> dict[str, object]:
    repo = repo.resolve(strict=True)
    _require_checkout(repo, sha)
    if component not in COMPONENT_ENTRYPOINTS:
        raise ValueError(f"unsupported component: {component}")
    value = json.loads((release / "release.json").read_text(encoding="utf-8"))
    if set(value) != REQUIRED or value["format"] != FORMAT or value["version"] != VERSION:
        raise ValueError("release manifest fields or version are invalid")
    command, target = COMPONENT_ENTRYPOINTS[component]
    if value["component"] != component:
        raise ValueError("release component mismatch")
    if value["entrypoint"] != {"command": command, "target": target}:
        raise ValueError("release entrypoint mismatch")
    if value["source_sha"] != sha:
        raise ValueError("release source SHA mismatch")
    if value["source_tree"] != _git(repo, "rev-parse", f"{sha}^{{tree}}"):
        raise ValueError("release source tree mismatch")
    wheel = release / "dist" / "qsys.whl"
    _validate_wheel(wheel, component)
    expected = {
        "wheel_sha256": _sha256(wheel),
        "wheel_bytes": wheel.stat().st_size,
        "pyproject_sha256": hashlib.sha256(
            _git(repo, "show", f"{sha}:pyproject.toml", text=False)
        ).hexdigest(),
        "uv_lock_sha256": hashlib.sha256(
            _git(repo, "show", f"{sha}:uv.lock", text=False)
        ).hexdigest(),
    }
    for key, actual in expected.items():
        if value[key] != actual:
            raise ValueError(f"release {key} mismatch")
    ci = value["ci"]
    if not isinstance(ci, dict) or set(ci) != {"repository", "run_id", "run_attempt"}:
        raise ValueError("release CI provenance is invalid")
    if not all(isinstance(item, str) and item for item in ci.values()):
        raise ValueError("release CI provenance values must be non-empty strings")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("build")
    create.add_argument("--sha", required=True)
    create.add_argument("--component", required=True, choices=sorted(COMPONENT_ENTRYPOINTS))
    create.add_argument("--dist", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--repository", required=True)
    create.add_argument("--run-id", required=True)
    create.add_argument("--run-attempt", required=True)
    check = commands.add_parser("verify")
    check.add_argument("--sha", required=True)
    check.add_argument("--component", required=True, choices=sorted(COMPONENT_ENTRYPOINTS))
    check.add_argument("--release", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            value = build(
                args.repo, args.sha, args.component, args.dist, args.output,
                repository=args.repository, run_id=args.run_id,
                run_attempt=args.run_attempt,
            )
        else:
            value = verify(args.repo, args.sha, args.component, args.release)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError,
            json.JSONDecodeError, zipfile.BadZipFile) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

