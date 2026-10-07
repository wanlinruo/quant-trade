#!/usr/bin/env python3
"""Fixed Linux deployment gateway installed outside the Git worktree."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

CONFIG_PATH = Path("/etc/qsys/deployment-entrypoint.json")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
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
    if set(config) != {"schema_version", "test_repo"} or config["schema_version"] != 1:
        raise ValueError("entrypoint config must match schema version 1")
    repo = config["test_repo"]
    if not isinstance(repo, str) or not Path(repo).is_absolute():
        raise ValueError("test_repo must be an absolute path")
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--component", action="append", dest="components", required=True)
    args = parser.parse_args(argv)
    try:
        validate_request(args.environment, args.sha, args.components)
        config = load_config()
        repo = Path(str(config["test_repo"])).resolve(strict=True)
        deployer = repo / "scripts" / "deploy_test_linux.sh"
        if not deployer.is_file():
            raise ValueError(f"fixed deployer is missing: {deployer}")
        completed = subprocess.run(
            ["/bin/sh", os.fspath(deployer), args.sha], cwd=repo, check=False,
        )
        return completed.returncode
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
