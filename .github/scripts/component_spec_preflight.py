"""Compare public component specs before any artifact download or deployment."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def verify(state: dict, manifest: dict, component: str) -> dict:
    if (
        state.get("entrypoint_version", 0) < 32
        or "component-spec-fingerprint-v1" not in state.get("capabilities", [])
        or not isinstance(state.get("component_spec_sha256"), dict)
    ):
        raise ValueError("node lacks component spec fingerprints; bootstrap the reviewed main entrypoint")
    specs = manifest.get("components")
    if (
        manifest.get("schema_version") != 1 or not isinstance(specs, dict)
        or not isinstance(specs.get(component), dict)
    ):
        raise ValueError("target manifest lacks a valid component specification")
    expected = hashlib.sha256(json.dumps(
        specs[component], sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")).hexdigest()
    if state["component_spec_sha256"].get(component) != expected:
        raise ValueError(
            f"component specification mismatch: {component}; "
            "review and bootstrap the exact main whitelist before retrying"
        )
    return {"component": component, "component_spec_sha256": expected}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--component", required=True)
    args = parser.parse_args()
    try:
        state = json.loads(args.state.read_text(encoding="utf-8"))
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or not isinstance(manifest, dict):
            raise ValueError("node state and target manifest must be JSON objects")
        print(json.dumps(verify(state, manifest, args.component)))
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(1, f"component preflight failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
