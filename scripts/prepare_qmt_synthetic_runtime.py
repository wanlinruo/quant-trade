"""Stage and verify the exact local runtime files for the #601 QMT probe."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys


RUNTIME_FILES = (
    "qmt-synthetic-intent-protocol.py",
    "qmt-synthetic-intent-port.py",
)
REBOOT_DRIVER_SOURCE = "qmt-windows-reboot-validation-driver.py"
REBOOT_DRIVER_TARGET = "native.py"
REBOOT_COORDINATOR_SOURCE = "qmt-windows-reboot-coordinator.ps1"
REBOOT_COORDINATOR_TARGET = "coordinate.ps1"
FIXTURE_TARGET = "fixture.json"
VALIDATION_MODES = ("reboot", "rehearsal")
_RUN_ID = re.compile(r"[0-9a-f]{32}")


def coordinator_task_name(run_id: str, mode: str) -> str:
    return f"qsys-issue601-{mode}-coordinate-{run_id[:8]}"


def _fixture_payload(run_id: str, mode: str) -> bytes:
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise ValueError("run id must be 32 lowercase hex characters")
    if mode not in VALIDATION_MODES:
        raise ValueError(f"mode must be one of {VALIDATION_MODES}")
    value = {
        "coordinator_task": coordinator_task_name(run_id, mode),
        "first": run_id,
        "mode": mode,
    }
    return (json.dumps(value, ensure_ascii=True, sort_keys=True) + "\n").encode("ascii")


def _read_regular_file(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"runtime source must be a regular file: {path}")
    return path.read_bytes()


def _validate_target_dir(target_dir: Path) -> None:
    if target_dir.is_symlink() or not target_dir.is_dir():
        raise ValueError("target must be an existing non-symlink directory")


def _write_exclusive(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def stage_runtime_files(source_dir: Path, target_dir: Path) -> dict[str, str]:
    """Copy required files under their runtime names without overwriting anything."""
    _validate_target_dir(target_dir)
    payloads = {name: _read_regular_file(source_dir / name) for name in RUNTIME_FILES}
    for name in RUNTIME_FILES:
        target = target_dir / name
        if os.path.lexists(target):
            raise FileExistsError(f"refusing to overwrite runtime file: {target}")
    for name, payload in payloads.items():
        _write_exclusive(target_dir / name, payload)
    return verify_runtime_files(source_dir, target_dir)


def stage_reboot_bundle_files(
    source_dir: Path, bundle_dir: Path, run_id: str, mode: str
) -> dict[str, str]:
    """Stage driver, coordinator, fixture and exact runtime without overwrite."""
    _validate_target_dir(bundle_dir)
    fixture = _fixture_payload(run_id, mode)
    driver_source = _read_regular_file(source_dir / REBOOT_DRIVER_SOURCE)
    coordinator_source = _read_regular_file(source_dir / REBOOT_COORDINATOR_SOURCE)
    targets = (
        bundle_dir / REBOOT_DRIVER_TARGET,
        bundle_dir / REBOOT_COORDINATOR_TARGET,
        bundle_dir / FIXTURE_TARGET,
        bundle_dir / "runtime",
    )
    if any(os.path.lexists(target) for target in targets):
        raise FileExistsError("refusing to overwrite reboot driver/coordinator/fixture/runtime")
    runtime_target = bundle_dir / "runtime"
    runtime_target.mkdir(mode=0o700)
    stage_runtime_files(source_dir, runtime_target)
    _write_exclusive(bundle_dir / REBOOT_DRIVER_TARGET, driver_source)
    _write_exclusive(bundle_dir / REBOOT_COORDINATOR_TARGET, coordinator_source)
    _write_exclusive(bundle_dir / FIXTURE_TARGET, fixture)
    return verify_reboot_bundle_files(source_dir, bundle_dir, run_id, mode)


def verify_reboot_bundle_files(
    source_dir: Path, bundle_dir: Path, run_id: str, mode: str
) -> dict[str, str]:
    """Verify exact driver/coordinator bytes, run binding and runtime import shape."""
    _validate_target_dir(bundle_dir)
    runtime = verify_runtime_files(source_dir, bundle_dir / "runtime")
    source = _read_regular_file(source_dir / REBOOT_DRIVER_SOURCE)
    target = _read_regular_file(bundle_dir / REBOOT_DRIVER_TARGET)
    if target != source:
        raise ValueError("reboot validation driver differs from reviewed source")
    compile(target, str(bundle_dir / REBOOT_DRIVER_TARGET), "exec")
    coordinator = _read_regular_file(bundle_dir / REBOOT_COORDINATOR_TARGET)
    if coordinator != _read_regular_file(source_dir / REBOOT_COORDINATOR_SOURCE):
        raise ValueError("reboot coordinator differs from reviewed source")
    try:
        coordinator.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("reboot coordinator must be ASCII for Windows PowerShell 5.1") from exc
    fixture = _read_regular_file(bundle_dir / FIXTURE_TARGET)
    if fixture != _fixture_payload(run_id, mode):
        raise ValueError("fixture.json does not bind the requested run id and mode")
    return {
        **{f"runtime/{name}": value for name, value in runtime.items()},
        REBOOT_DRIVER_TARGET: hashlib.sha256(target).hexdigest(),
        REBOOT_COORDINATOR_TARGET: hashlib.sha256(coordinator).hexdigest(),
        FIXTURE_TARGET: hashlib.sha256(fixture).hexdigest(),
        "coordinator_task": coordinator_task_name(run_id, mode),
        "driver_compile": "verified",
    }


def verify_runtime_files(source_dir: Path, target_dir: Path) -> dict[str, str]:
    """Verify byte identity and import the port from the final directory shape."""
    _validate_target_dir(target_dir)
    entries = {path.name for path in target_dir.iterdir()}
    reviewed = set(RUNTIME_FILES)
    if entries != reviewed:
        missing = ",".join(sorted(reviewed - entries)) or "none"
        extra = ",".join(sorted(entries - reviewed)) or "none"
        raise ValueError(
            "runtime directory must contain only the exact reviewed file set; "
            f"missing={missing}; extra={extra}"
        )
    expected = {name: _read_regular_file(source_dir / name) for name in RUNTIME_FILES}
    hashes: dict[str, str] = {}
    for name, source in expected.items():
        target = target_dir / name
        payload = _read_regular_file(target)
        if payload != source:
            raise ValueError(f"runtime file differs from reviewed source: {name}")
        hashes[name] = hashlib.sha256(payload).hexdigest()

    port_path = (target_dir / "qmt-synthetic-intent-port.py").resolve(strict=True)
    protocol_path = (target_dir / "qmt-synthetic-intent-protocol.py").resolve(strict=True)
    spec = importlib.util.spec_from_file_location("qsys_601_bundle_port", port_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not create runtime port import spec")
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    loaded_protocol = Path(module.protocol.__file__).resolve(strict=True)
    if loaded_protocol != protocol_path:
        raise RuntimeError("port did not load the protocol from the final runtime directory")
    hashes["runtime_import"] = "verified"
    return hashes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode", choices=("stage", "verify", "stage-reboot", "verify-reboot"))
    parser.add_argument("target", type=Path)
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "docs" / "reference",
    )
    parser.add_argument("--run-id")
    parser.add_argument("--validation-mode", choices=VALIDATION_MODES)
    args = parser.parse_args()
    if args.mode in ("stage-reboot", "verify-reboot") and (
            args.run_id is None or args.validation_mode is None):
        parser.error("stage-reboot/verify-reboot require --run-id and --validation-mode")
    if args.mode == "stage":
        receipt = stage_runtime_files(args.source_dir, args.target)
    elif args.mode == "verify":
        receipt = verify_runtime_files(args.source_dir, args.target)
    elif args.mode == "stage-reboot":
        receipt = stage_reboot_bundle_files(
            args.source_dir, args.target, args.run_id, args.validation_mode)
    else:
        receipt = verify_reboot_bundle_files(
            args.source_dir, args.target, args.run_id, args.validation_mode)
    print(json.dumps(receipt, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
