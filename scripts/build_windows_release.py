#!/usr/bin/env python3
"""Build an immutable Windows source release from an exact Git commit."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
APPS = frozenset({"live_app", "paper_app"})
ROOTS = ("src/", "scripts/")


def _git(repo: Path, *args: str, text: bool = True) -> str | bytes:
    return subprocess.check_output(["git", *args], cwd=repo, text=text)


def normalized_sha256(data: bytes) -> str:
    """Hash text identically after Git/Windows LF or CRLF checkouts."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")).hexdigest()


def tracked_files(repo: Path, sha: str) -> list[str]:
    if not SHA_PATTERN.fullmatch(sha):
        raise ValueError("sha must be a full 40-character lowercase commit SHA")
    _git(repo, "cat-file", "-e", f"{sha}^{{commit}}")
    raw = _git(repo, "ls-tree", "-r", "--name-only", "-z", sha, "--", *ROOTS, text=False)
    assert isinstance(raw, bytes)
    paths = sorted(item.decode("utf-8") for item in raw.split(b"\0") if item)
    if not paths or any(not item.startswith(ROOTS) for item in paths):
        raise ValueError("release must contain only tracked src/ and scripts/ files")
    return paths


def manifest(repo: Path, sha: str, app: str) -> dict[str, object]:
    if app not in APPS:
        raise ValueError(f"unsupported Windows app: {app}")
    files: dict[str, str] = {}
    for path in tracked_files(repo, sha):
        content = _git(repo, "show", f"{sha}:{path}", text=False)
        assert isinstance(content, bytes)
        files[path] = normalized_sha256(content)
    tree = _git(repo, "rev-parse", f"{sha}^{{tree}}")
    assert isinstance(tree, str)
    return {
        "schema_version": 1,
        "app": app,
        "source_sha": sha,
        "source_tree": tree.strip(),
        "files": files,
    }


def build(repo: Path, sha: str, app: str, output: Path) -> dict[str, object]:
    release = manifest(repo, sha, app)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in release["files"]:
                safe = PurePosixPath(path)
                if safe.is_absolute() or ".." in safe.parts:
                    raise ValueError(f"unsafe release path: {path}")
                content = _git(repo, "show", f"{sha}:{path}", text=False)
                assert isinstance(content, bytes)
                archive.writestr(f"payload/{path}", content)
            archive.writestr(
                "release.json",
                json.dumps(release, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            )
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return release


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--sha", required=True)
    parser.add_argument("--app", choices=sorted(APPS), required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--manifest-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        release = manifest(args.repo, args.sha, args.app)
        if not args.manifest_only:
            if args.output is None:
                raise ValueError("--output is required unless --manifest-only is used")
            release = build(args.repo, args.sha, args.app, args.output)
        print(json.dumps(release, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
