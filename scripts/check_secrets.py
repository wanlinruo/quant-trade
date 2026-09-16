"""Small dependency-free secret guard for tracked repository files."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


PATTERNS = {
    "private key": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "GitHub token": re.compile(rb"gh[pousr]_[A-Za-z0-9_]{30,}"),
    "AWS access key": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "credential URL": re.compile(rb"https?://[^/\s:@]+:[^/\s@]+@"),
    "assigned long token": re.compile(
        rb"(?i)(token|secret|password|passwd|api[_-]?key)\s*=\s*['\"][^'\"]{20,}['\"]"
    ),
}


def tracked_files() -> list[Path]:
    output = subprocess.check_output(["git", "ls-files", "-z"])
    return [Path(raw.decode()) for raw in output.split(b"\0") if raw]


def main() -> int:
    findings: list[str] = []
    for path in tracked_files():
        try:
            data = path.read_bytes()
        except OSError as exc:
            findings.append(f"{path}: unreadable: {exc}")
            continue
        if b"\0" in data[:8192]:
            continue
        for name, pattern in PATTERNS.items():
            if pattern.search(data):
                findings.append(f"{path}: possible {name}")
    if findings:
        print("\n".join(findings), file=sys.stderr)
        return 1
    print("secret scan ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
