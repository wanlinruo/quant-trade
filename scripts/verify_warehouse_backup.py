"""Create or verify a cryptographic manifest for a warehouse backup/restore.

This tool never copies or changes warehouse data.  ``create`` inventories a
point-in-time tree; ``verify`` proves that a restored tree has the same files
and bytes, plus matching Parquet row counts and Arrow schemas.

Examples:
  flock --exclusive var/warehouse-write.lock \
    uv run python scripts/verify_warehouse_backup.py create \
      --root /data/qsys/warehouse --manifest /data/qsys/audit/warehouse-manifest.json
  uv run python scripts/verify_warehouse_backup.py verify \
    --root /mnt/isolated-restore --manifest /data/qsys/audit/warehouse-manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import pyarrow.parquet as pq

MANIFEST_VERSION = 1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 << 20):
            digest.update(block)
    return digest.hexdigest()


def _parquet_facts(path: Path) -> tuple[int, str]:
    parquet = pq.ParquetFile(path)
    return parquet.metadata.num_rows, parquet.schema_arrow.to_string()


def create_manifest(root: Path) -> dict:
    """Inventory a stable tree; callers must quiesce writers or use a snapshot."""
    root = root.resolve(strict=True)
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        entry = {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        if path.suffix == ".parquet":
            rows, arrow_schema = _parquet_facts(path)
            entry.update({"rows": rows, "arrow_schema": arrow_schema})
        files.append(entry)
    return {
        "format": "qsys-warehouse-backup-manifest",
        "version": MANIFEST_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_root": str(root),
        "files": files,
    }


def _safe_relative(raw: str) -> Path:
    value = PurePosixPath(raw)
    if value.is_absolute() or ".." in value.parts or not value.parts:
        raise ValueError(f"unsafe manifest path: {raw!r}")
    return Path(*value.parts)


def verify_manifest(root: Path, manifest: dict) -> list[str]:
    if (manifest.get("format") != "qsys-warehouse-backup-manifest"
            or manifest.get("version") != MANIFEST_VERSION):
        return ["unsupported manifest format or version"]
    root = root.resolve(strict=True)
    failures: list[str] = []
    expected_paths: set[str] = set()
    for expected in manifest.get("files", []):
        try:
            relative = _safe_relative(expected["path"])
        except (KeyError, TypeError, ValueError) as exc:
            failures.append(str(exc))
            continue
        expected_paths.add(relative.as_posix())
        path = root / relative
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError:
            failures.append(f"{relative.as_posix()}: missing")
            continue
        if not resolved.is_relative_to(root) or path.is_symlink():
            failures.append(f"{relative.as_posix()}: escapes restore root or is a symlink")
            continue
        if not path.is_file():
            failures.append(f"{relative.as_posix()}: missing")
            continue
        size = path.stat().st_size
        if size != expected.get("bytes"):
            failures.append(f"{relative.as_posix()}: bytes {size} != {expected.get('bytes')}")
            continue
        digest = _sha256(path)
        if digest != expected.get("sha256"):
            failures.append(f"{relative.as_posix()}: sha256 mismatch")
            continue
        if relative.suffix == ".parquet":
            try:
                rows, arrow_schema = _parquet_facts(path)
            except Exception as exc:
                failures.append(f"{relative.as_posix()}: unreadable parquet: {exc}")
                continue
            if rows != expected.get("rows"):
                failures.append(f"{relative.as_posix()}: rows {rows} != {expected.get('rows')}")
            if arrow_schema != expected.get("arrow_schema"):
                failures.append(f"{relative.as_posix()}: Arrow schema mismatch")
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*") if path.is_file() and not path.is_symlink()
    }
    for extra in sorted(actual_paths - expected_paths):
        failures.append(f"{extra}: unexpected file")
    return failures


def _write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=("create", "verify"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()

    if args.mode == "create":
        manifest = create_manifest(args.root)
        _write_json_atomic(args.manifest, manifest)
        print(f"manifest created: {len(manifest['files'])} files -> {args.manifest}")
        return 0

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    failures = verify_manifest(args.root, manifest)
    if failures:
        print(f"backup verification failed: {len(failures)} problem(s)")
        for failure in failures[:50]:
            print(f"  {failure}")
        return 2
    print(f"backup verified: {len(manifest['files'])} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
