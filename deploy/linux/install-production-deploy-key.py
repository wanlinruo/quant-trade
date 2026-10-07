#!/usr/bin/env python3
"""Install or rotate the restricted GitHub production deployment public key."""

from __future__ import annotations

import argparse
import base64
import os
import tempfile
from pathlib import Path

MARKER = "qsys-github-production"
FORCED_COMMAND = "/usr/local/libexec/qsys-production-deploy-ssh"


def restricted_key(public_key: str) -> str:
    parts = public_key.strip().split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise ValueError("deployment key must be an Ed25519 public key")
    try:
        decoded = base64.b64decode(parts[1], validate=True)
    except ValueError as exc:
        raise ValueError("deployment public key is not valid base64") from exc
    if len(decoded) < 32:
        raise ValueError("deployment public key is too short")
    return f'restrict,command="{FORCED_COMMAND}" ssh-ed25519 {parts[1]} {MARKER}'


def install(public_key_path: Path, authorized_keys: Path) -> None:
    line = restricted_key(public_key_path.read_text(encoding="utf-8"))
    authorized_keys.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    existing = authorized_keys.read_text(encoding="utf-8").splitlines() \
        if authorized_keys.exists() else []
    kept = [item for item in existing if not item.rstrip().endswith(f" {MARKER}")]
    content = "\n".join([*kept, line]) + "\n"
    fd, temporary = tempfile.mkstemp(prefix="authorized_keys.", dir=authorized_keys.parent)
    path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        path.chmod(0o600)
        path.replace(authorized_keys)
    finally:
        path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("public_key", type=Path)
    parser.add_argument(
        "--authorized-keys", type=Path,
        default=Path.home() / ".ssh" / "authorized_keys",
    )
    args = parser.parse_args()
    install(args.public_key, args.authorized_keys)
    print(f"installed restricted production deployment key in {args.authorized_keys}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
