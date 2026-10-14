#!/usr/bin/env python3
"""Restricted SSH dispatcher for the dedicated GitHub production key."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys

ENTRYPOINT = "/usr/local/libexec/qsys-production-deploy-entrypoint"
ALLOWED_COMMANDS = {
    "status", "deploy", "component-deploy", "windows-deploy", "record", "history",
}


def command_from_environment() -> list[str]:
    command = shlex.split(os.environ.get("SSH_ORIGINAL_COMMAND", ""))
    if not command or command[0] not in ALLOWED_COMMANDS:
        raise ValueError("SSH command is not allowed")
    if any("\n" in item or "\x00" in item for item in command):
        raise ValueError("SSH command contains an invalid character")
    return [ENTRYPOINT, *command]


def main() -> int:
    try:
        return subprocess.run(command_from_environment(), check=False).returncode
    except (OSError, ValueError) as exc:
        print(f"production deployment dispatcher rejected request: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
