#!/usr/bin/env python3
"""Adjudicate frozen Issue #230 forward observations; never create orders."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import polars as pl

from qsys.research.first_board_quality import forward_verdict


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument(
        "--settled", type=Path, required=True,
        help="append-only settled forward record parquet",
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"forward adjudication is immutable: {args.out}")

    source_manifest = json.loads((args.run / "manifest.json").read_text())
    settled = pl.read_parquet(args.settled)
    verdict = forward_verdict(
        settled, bootstrap_samples=max(args.bootstrap_samples, 100),
    )
    args.out.mkdir(parents=True)
    (args.out / "verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2) + "\n"
    )
    manifest = {
        "schema_version": 1,
        "research": "issue-230-first-board-quality-forward-adjudication",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_run_manifest_sha256": _sha256(args.run / "manifest.json"),
        "source_run_git_sha": source_manifest["git_sha"],
        "settled_sha256": _sha256(args.settled),
        "bootstrap_samples": max(args.bootstrap_samples, 100),
        "bonferroni_lower_quantile": 0.0125,
        "research_only": True,
        "orders_created": 0,
    }
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
