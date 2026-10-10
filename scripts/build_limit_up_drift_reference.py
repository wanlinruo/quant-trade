#!/usr/bin/env python3
"""Build the compact immutable PSI reference from #210 OBS predictions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from qsys.research.limit_up_shadow import build_drift_reference


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite drift reference: {args.out}")
    reference = build_drift_reference(args.predictions)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(reference.as_dict(), ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(reference.as_dict(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
