#!/usr/bin/env python3
"""Build or verify a Research Runner wheel release bound to one Git tree."""

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

FORMAT = "qsys-research-release"
VERSION = 2
SHA = re.compile(r"^[0-9a-f]{40}$")
REQUIRED = frozenset({
    "format", "version", "source_sha", "source_tree", "wheel_sha256",
    "wheel_filename", "wheel_bytes", "pyproject_sha256", "uv_lock_sha256", "ci",
})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 << 20):
            digest.update(block)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _git_bytes(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=repo)


def _require_exact_checkout(repo: Path, sha: str) -> None:
    if _git(repo, "rev-parse", "HEAD") != sha:
        raise ValueError("release SHA must be the exact checked-out HEAD")
    if _git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("release checkout has tracked modifications")


def _validate_wheel(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("wheel must be a regular non-symlink file")
    with zipfile.ZipFile(path) as archive:
        metadata = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        entry_points = [
            name for name in archive.namelist()
            if name.endswith(".dist-info/entry_points.txt")
        ]
        if len(metadata) != 1 or len(entry_points) != 1:
            raise ValueError("wheel must contain one metadata and entry-points file")
        package = BytesParser().parsebytes(archive.read(metadata[0]))
        if package.get("Name", "").lower() != "qsys":
            raise ValueError("wheel package name must be qsys")
        entries = configparser.ConfigParser(interpolation=None)
        entries.read_string(archive.read(entry_points[0]).decode("utf-8"))
        if entries.get("console_scripts", "qsys-research-runner", fallback="") != (
            "qsys.research.runner_cli:main"
        ):
            raise ValueError("wheel does not expose the Research Runner entrypoint")
        if entries.get("console_scripts", "qsys-research-test-drill", fallback="") != (
            "qsys.research.drill_cli:main"
        ):
            raise ValueError("wheel does not expose the test drill entrypoint")


def build(
    repo: Path,
    sha: str,
    dist: Path,
    output: Path,
    *,
    repository: str,
    run_id: str,
    run_attempt: str,
) -> dict[str, object]:
    repo = repo.resolve(strict=True)
    if not SHA.fullmatch(sha):
        raise ValueError("release SHA must be a full lowercase commit SHA")
    _require_exact_checkout(repo, sha)
    tree = _git(repo, "rev-parse", f"{sha}^{{tree}}")
    wheels = sorted(dist.glob("qsys-*.whl"))
    if len(wheels) != 1:
        raise ValueError("dist must contain exactly one qsys wheel")
    _validate_wheel(wheels[0])
    if output.exists() and any(output.iterdir()):
        raise ValueError("release output directory must be empty")
    (output / "dist").mkdir(parents=True, exist_ok=True)
    wheel = output / "dist" / wheels[0].name
    shutil.copyfile(wheels[0], wheel)
    value: dict[str, object] = {
        "format": FORMAT,
        "version": VERSION,
        "source_sha": sha,
        "source_tree": tree,
        "wheel_filename": wheel.name,
        "wheel_sha256": _sha256(wheel),
        "wheel_bytes": wheel.stat().st_size,
        "pyproject_sha256": _sha256_bytes(
            _git_bytes(repo, "show", f"{sha}:pyproject.toml")
        ),
        "uv_lock_sha256": _sha256_bytes(_git_bytes(repo, "show", f"{sha}:uv.lock")),
        "ci": {
            "repository": repository,
            "run_id": run_id,
            "run_attempt": run_attempt,
        },
    }
    (output / "release.json").write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return value


def verify(repo: Path, sha: str, release: Path) -> dict[str, object]:
    repo = repo.resolve(strict=True)
    if not SHA.fullmatch(sha):
        raise ValueError("release SHA must be a full lowercase commit SHA")
    _require_exact_checkout(repo, sha)
    value = json.loads((release / "release.json").read_text(encoding="utf-8"))
    if set(value) != REQUIRED or value["format"] != FORMAT or value["version"] != VERSION:
        raise ValueError("release manifest fields or version are invalid")
    if value["source_sha"] != sha:
        raise ValueError("release source SHA mismatch")
    if value["source_tree"] != _git(repo, "rev-parse", f"{sha}^{{tree}}"):
        raise ValueError("release source tree mismatch")
    wheel_filename = value.get("wheel_filename")
    if (
        not isinstance(wheel_filename, str)
        or Path(wheel_filename).name != wheel_filename
        or not wheel_filename.endswith(".whl")
    ):
        raise ValueError("release wheel filename is invalid")
    wheel = release / "dist" / wheel_filename
    _validate_wheel(wheel)
    expected = {
        "wheel_sha256": _sha256(wheel),
        "wheel_bytes": wheel.stat().st_size,
        "pyproject_sha256": _sha256_bytes(
            _git_bytes(repo, "show", f"{sha}:pyproject.toml")
        ),
        "uv_lock_sha256": _sha256_bytes(_git_bytes(repo, "show", f"{sha}:uv.lock")),
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
    create.add_argument("--dist", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--repository", required=True)
    create.add_argument("--run-id", required=True)
    create.add_argument("--run-attempt", required=True)
    check = commands.add_parser("verify")
    check.add_argument("--sha", required=True)
    check.add_argument("--release", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            value = build(
                args.repo, args.sha, args.dist, args.output,
                repository=args.repository,
                run_id=args.run_id,
                run_attempt=args.run_attempt,
            )
        else:
            value = verify(args.repo, args.sha, args.release)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError,
            json.JSONDecodeError, zipfile.BadZipFile) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
