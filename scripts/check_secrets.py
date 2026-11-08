"""Small dependency-free secret guard for tracked repository files.

Besides credential patterns it blocks brokerage account ids (#600):

* a context rule flags 8-12 digit numbers written next to account wording,
  except an explicit allowlist of obviously fake fixture values;
* an exact list of real account ids, read from ``QSYS_FORBIDDEN_ACCOUNT_IDS``
  (comma separated) or the file named by ``QSYS_FORBIDDEN_ACCOUNT_IDS_FILE``,
  is matched anywhere.  The real values never live in the repository and
  findings never echo the matched value.
"""

from __future__ import annotations

import os
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
# "account id"/"accid"/"acct" and the Chinese 资金账号/账户/账号 near an 8-12 digit number.
ACCOUNT_CONTEXT = re.compile(
    "(?i)(account[_ -]?ids?|accid|acct|资金账号|账户|账号)[^\\n]{0,40}?"
    "(?<![0-9A-Za-z_.-])([0-9]{8,12})(?![0-9A-Za-z_.])".encode("utf-8")
)
FAKE_ACCOUNT_IDS = frozenset({
    b"100000001", b"123456789", b"987654321", b"999999999", b"777888999",
})


def tracked_files() -> list[Path]:
    output = subprocess.check_output(["git", "ls-files", "-z"])
    return [Path(raw.decode()) for raw in output.split(b"\0") if raw]


def forbidden_account_ids(environ=os.environ) -> list[bytes]:
    values = [v.strip() for v in environ.get("QSYS_FORBIDDEN_ACCOUNT_IDS", "").split(",")]
    path = environ.get("QSYS_FORBIDDEN_ACCOUNT_IDS_FILE")
    if path and Path(path).is_file():
        values += [line.strip() for line in Path(path).read_text(encoding="ascii").splitlines()]
    ids = []
    for value in values:
        if not value:
            continue
        if re.fullmatch(r"[0-9]{6,20}", value) is None:
            raise ValueError("forbidden account ids must be digits only")
        ids.append(value.encode("ascii"))
    return ids


def scan(path: Path, data: bytes, forbidden: list[bytes]) -> list[str]:
    findings = []
    for name, pattern in PATTERNS.items():
        if pattern.search(data):
            findings.append(f"{path}: possible {name}")
    for match in ACCOUNT_CONTEXT.finditer(data):
        if match.group(2) not in FAKE_ACCOUNT_IDS:
            line = data.count(b"\n", 0, match.start(2)) + 1
            findings.append(f"{path}:{line}: possible account id (value not shown)")
    for value in forbidden:
        exact = re.compile(rb"(?<![0-9])" + re.escape(value) + rb"(?![0-9])")
        for match in exact.finditer(data):
            line = data.count(b"\n", 0, match.start()) + 1
            findings.append(f"{path}:{line}: known real account id (value not shown)")
    return findings


def main() -> int:
    forbidden = forbidden_account_ids()
    findings: list[str] = []
    for path in tracked_files():
        try:
            data = path.read_bytes()
        except OSError as exc:
            findings.append(f"{path}: unreadable: {exc}")
            continue
        if b"\0" in data[:8192]:
            continue
        findings.extend(scan(path, data, forbidden))
    if findings:
        print("\n".join(findings), file=sys.stderr)
        return 1
    print(f"secret scan ok (exact account list: {len(forbidden)} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
