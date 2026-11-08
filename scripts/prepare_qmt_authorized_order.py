"""Stage and verify a private bundle for one authorized #601 QMT order plan.

The bundle contains the reviewed executor (default and literal-bound enabled
copies), the canonical plan, a fresh HMAC key, the run fixture, the driver and
the session coordinator.  The account id comes from a private file; neither
the account nor the key is ever printed.  Nothing is overwritten.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import sys

SOURCE_DIR = Path(__file__).resolve().parents[1] / "docs" / "reference"
EXECUTOR_SOURCE = "qmt-authorized-order-executor.py"
DRIVER_SOURCE = "qmt-authorized-order-driver.py"
SESSION_SOURCE = "qmt-authorized-order-session.ps1"
TARGETS = {
    "executor-default.py": EXECUTOR_SOURCE,
    "native-order.py": DRIVER_SOURCE,
    "session.ps1": SESSION_SOURCE,
}
GENERATED = ("executor-enabled.py", "plan.json", "key.hex", "fixture.json")
CLIENT_BUILD = "2.1.33.0"
_RUN_ID = re.compile(r"[0-9a-f]{32}")
_PLACE = re.compile(
    r"(?P<tag>[a-z]):(?P<side>buy|sell):(?P<symbol>[0-9]{6}\.(?:SH|SZ)):(?P<qty>[0-9]+)"
    r":(?P<price>[0-9]+\.[0-9]{1,3}):(?P<expect>resting|fill)")


def _load_executor(source_dir: Path):
    spec = importlib.util.spec_from_file_location(
        "qsys_601_authorized_order_executor", source_dir / EXECUTOR_SOURCE)
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _read_account(path: Path) -> str:
    account = path.read_text(encoding="ascii").strip()
    if re.fullmatch(r"[0-9]{6,20}", account) is None:
        raise ValueError("account file must contain only the numeric account id")
    return account


def _utc(text: str) -> str:
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        raise ValueError("window times need an explicit offset")
    return value.astimezone(timezone.utc).isoformat()


def build_plan(run_id: str, places: list[str], not_before: str, not_after: str):
    if _RUN_ID.fullmatch(run_id) is None:
        raise ValueError("run id must be 32 lowercase hex characters")
    steps, expect, tags = [], {}, set()
    for text in places:
        match = _PLACE.fullmatch(text)
        if match is None:
            raise ValueError(f"--place must be tag:side:symbol:qty:price:expect, got {text!r}")
        if match["tag"] in tags:
            raise ValueError("duplicate place tag")
        tags.add(match["tag"])
        cid = f"q601-{run_id[:8]}-{match['tag']}"
        steps.append({"step": len(steps) + 1, "action": "place", "client_order_id": cid,
                      "symbol": match["symbol"], "side": match["side"],
                      "qty": int(match["qty"]), "limit_price": float(match["price"])})
        steps.append({"step": len(steps) + 1, "action": "cancel", "client_order_id": cid})
        expect[cid] = match["expect"]
    plan = {"run_id": run_id, "not_before": _utc(not_before), "not_after": _utc(not_after),
            "steps": steps}
    return plan, expect


def render_enabled(default: str, run_id: str, account: str, key_hex: str, plan_text: str) -> str:
    replacements = {
        "RUN_ID = ''": f"RUN_ID = {run_id!r}",
        "CLIENT_BUILD = ''": f"CLIENT_BUILD = {CLIENT_BUILD!r}",
        "ACCOUNT_ID = ''": f"ACCOUNT_ID = {account!r}",
        "KEY_HEX = ''": f"KEY_HEX = {key_hex!r}",
        "AUTHORIZED_PLAN = ''": f"AUTHORIZED_PLAN = {plan_text!r}",
        "ENABLED = False": "ENABLED = True",
    }
    lines = default.split("\n")
    for old, new in replacements.items():
        if lines.count(old) != 1:
            raise ValueError(f"reviewed executor must contain exactly one line {old!r}")
        lines[lines.index(old)] = new
    return "\n".join(lines)


def _write_exclusive(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(payload)


def stage(bundle: Path, run_id: str, account_file: Path, places: list[str], not_before: str,
          not_after: str, source_dir: Path = SOURCE_DIR) -> dict:
    if bundle.is_symlink() or not bundle.is_dir():
        raise ValueError("bundle must be an existing non-symlink directory")
    account = _read_account(account_file)
    executor = _load_executor(source_dir)
    plan, expect = build_plan(run_id, places, not_before, not_after)
    plan_text = executor.canonical(plan).decode("ascii")
    executor.validate_plan(plan_text, run_id)
    names = [*TARGETS, *GENERATED]
    if any(os.path.lexists(bundle / name) for name in names):
        raise FileExistsError("refusing to overwrite an existing authorized-order bundle")
    key_hex = secrets.token_hex(32)
    default = (source_dir / EXECUTOR_SOURCE).read_text(encoding="utf-8")
    fixture = {
        "first": run_id, "mode": "authorized-order",
        "session_task": f"qsys-issue601-order-session-{run_id[:8]}",
        "expect": expect, "scheduled_timeout": 300,
        "not_before_ms": int(datetime.fromisoformat(plan["not_before"]).timestamp() * 1000),
        "not_after_ms": int(datetime.fromisoformat(plan["not_after"]).timestamp() * 1000),
    }
    for target, source in TARGETS.items():
        _write_exclusive(bundle / target, (source_dir / source).read_bytes())
    _write_exclusive(bundle / "executor-enabled.py",
                     render_enabled(default, run_id, account, key_hex, plan_text).encode("utf-8"))
    _write_exclusive(bundle / "plan.json", plan_text.encode("ascii"))
    _write_exclusive(bundle / "key.hex", key_hex.encode("ascii"))
    _write_exclusive(bundle / "fixture.json",
                     (json.dumps(fixture, sort_keys=True) + "\n").encode("ascii"))
    return verify(bundle, run_id, account_file, source_dir)


def verify(bundle: Path, run_id: str, account_file: Path, source_dir: Path = SOURCE_DIR) -> dict:
    if bundle.is_symlink() or not bundle.is_dir():
        raise ValueError("bundle must be an existing non-symlink directory")
    entries = {p.name for p in bundle.iterdir()}
    expected = {*TARGETS, *GENERATED}
    if not expected <= entries:
        raise ValueError(f"bundle missing files: {sorted(expected - entries)}")
    for target, source in TARGETS.items():
        if (bundle / target).read_bytes() != (source_dir / source).read_bytes():
            raise ValueError(f"{target} differs from reviewed source")
    (bundle / "session.ps1").read_bytes().decode("ascii")
    account = _read_account(account_file)
    executor = _load_executor(source_dir)
    plan_text = (bundle / "plan.json").read_text(encoding="ascii")
    plan = executor.validate_plan(plan_text, run_id)
    key_hex = (bundle / "key.hex").read_text(encoding="ascii")
    if re.fullmatch(r"[0-9a-f]{64}", key_hex) is None:
        raise ValueError("key.hex must hold 64 lowercase hex characters")
    default = (bundle / "executor-default.py").read_text(encoding="utf-8")
    enabled = (bundle / "executor-enabled.py").read_text(encoding="utf-8")
    if enabled != render_enabled(default, run_id, account, key_hex, plan_text):
        raise ValueError("executor-enabled.py is not the exact literal binding of the reviewed source")
    compile(enabled, "executor-enabled.py", "exec")
    compile((bundle / "native-order.py").read_bytes(), "native-order.py", "exec")
    fixture = json.loads((bundle / "fixture.json").read_text(encoding="ascii"))
    cids = {s["client_order_id"] for s in plan["steps"] if s["action"] == "place"}
    if (fixture.get("first") != run_id or fixture.get("mode") != "authorized-order"
            or fixture.get("session_task") != f"qsys-issue601-order-session-{run_id[:8]}"
            or set(fixture.get("expect", {})) != cids
            or set(fixture["expect"].values()) - {"resting", "fill"}
            or (fixture["not_before_ms"], fixture["not_after_ms"]) != plan["window_ms"]):
        raise ValueError("fixture.json does not bind this run and plan")
    hashes = {name: hashlib.sha256((bundle / name).read_bytes()).hexdigest()
              for name in sorted(expected) if name != "key.hex"}
    return {
        "run_id": run_id, "hashes": hashes, "steps": [
            {k: v for k, v in s.items()} for s in plan["steps"]],
        "expect": fixture["expect"], "window": [plan["not_before"], plan["not_after"]],
        "enabled_binding": "verified", "account_printed": False, "key_printed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("stage", "verify"))
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--account-file", type=Path, required=True)
    parser.add_argument("--place", action="append", default=[])
    parser.add_argument("--not-before")
    parser.add_argument("--not-after")
    args = parser.parse_args()
    if args.mode == "stage":
        if not args.place or not args.not_before or not args.not_after:
            parser.error("stage requires --place, --not-before and --not-after")
        receipt = stage(args.bundle, args.run_id, args.account_file, args.place,
                        args.not_before, args.not_after)
    else:
        receipt = verify(args.bundle, args.run_id, args.account_file)
    print(json.dumps(receipt, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
