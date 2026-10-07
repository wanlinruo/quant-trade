"""Validate qsys deployment plans, node configuration and evidence records."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from qsys.ops.deployment import (
    ContractError,
    DeploymentManifest,
    require_full_sha,
    validate_config,
    validate_record,
)

DEFAULT_MANIFEST = Path("deploy/deployment.toml")


def _changed_paths(base: str, target: str) -> list[str]:
    require_full_sha(base, field="base")
    require_full_sha(target, field="target")
    for value in (base, target):
        subprocess.run(
            ["git", "cat-file", "-e", f"{value}^{{commit}}"],
            check=True, capture_output=True, text=True,
        )
    result = subprocess.run(
        ["git", "diff", "--name-only", "-z", "--diff-filter=ACDMRTUXB", base, target, "--"],
        check=True, capture_output=True,
    )
    return [item.decode() for item in result.stdout.split(b"\0") if item]


def _key(path: str | None) -> bytes | None:
    return None if path is None else Path(path).read_bytes().strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    commands = parser.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan", help="plan components from an exact Git diff")
    plan.add_argument("--base", required=True)
    plan.add_argument("--target", required=True)
    plan.add_argument("--environment", choices=("test", "production"), required=True)

    config = commands.add_parser("validate-config", help="validate and fingerprint config")
    config.add_argument("--config", required=True)
    config.add_argument("--environment", choices=("test", "production"), required=True)
    config.add_argument("--against")
    config.add_argument("--fingerprint-key-file")

    record = commands.add_parser("validate-record", help="validate deployment record JSON")
    record.add_argument("path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "plan":
            manifest = DeploymentManifest.load(args.manifest)
            plan = manifest.plan(
                _changed_paths(args.base, args.target), environment=args.environment,
            )
            print(json.dumps(plan.as_dict(), ensure_ascii=False, indent=2))
            return 0 if not plan.unknown_paths else 2
        if args.command == "validate-config":
            result = validate_config(
                args.config, args.environment, against=args.against,
                hmac_key=_key(args.fingerprint_key_file),
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 2
        record = json.loads(Path(args.path).read_text(encoding="utf-8"))
        validate_record(record)
        print(json.dumps({"ok": True, "path": args.path}))
        return 0
    except (ValueError, TypeError, FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
