#!/usr/bin/env python3
"""Atomically activate one immutable component release from the trusted whitelist."""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
from pathlib import Path

if __package__:
    from scripts.build_component_release import verify
    from scripts.component_release_config import (
        load_component_releases, load_component_releases_file,
    )
else:
    from build_component_release import verify
    from component_release_config import (
        load_component_releases, load_component_releases_file,
    )

SHA = re.compile(r"^[0-9a-f]{40}$")


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True, capture_output=True)


def _sudo(*command: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run(["sudo", "-n", *command], check=check)


def _writable_by_group_or_other(root: Path) -> Path | None:
    for path in (root, *root.rglob("*")):
        if path.is_symlink():
            continue
        if path.stat().st_mode & 0o022:
            return path
    return None


def _install_file(source: Path, target: Path, mode: str = "0644") -> None:
    temporary = target.parent / f".{target.name}.new-{os.getpid()}"
    try:
        _sudo(
            "install", "-o", "root", "-g", "root", "-m", mode,
            str(source), str(temporary),
        )
        _sudo("mv", "-Tf", str(temporary), str(target))
    finally:
        _sudo("rm", "-f", "--", str(temporary), check=False)


def _switch_current(current: Path, target: Path, label: str) -> None:
    temporary = current.parent / f".{current.name}.{label}-{os.getpid()}"
    try:
        _sudo("ln", "-sfn", str(target), str(temporary))
        _sudo("mv", "-Tf", str(temporary), str(current))
    finally:
        _sudo("rm", "-f", "--", str(temporary), check=False)


def _restore(
    current: Path, previous: str | None, dropin: Path,
    previous_dropin: bytes | None,
) -> None:
    if previous is None:
        _sudo("rm", "-f", "--", str(current), check=False)
    else:
        _switch_current(current, Path(previous), "rollback")
    if previous_dropin is None:
        _sudo("rm", "-f", "--", str(dropin), check=False)
    else:
        with tempfile.NamedTemporaryFile() as stream:
            stream.write(previous_dropin)
            stream.flush()
            _install_file(Path(stream.name), dropin)
    _sudo("systemctl", "daemon-reload", check=False)


def _require_idle_reboot_drill(service: str) -> None:
    for unit in (service, "qsys-reboot-drill.timer"):
        state = _run(["systemctl", "show", unit, "--property=ActiveState", "--value"])
        if state.stdout.strip() not in {"inactive", "failed"}:
            raise ValueError(f"refusing to switch reboot-drill while running or unknown: {unit}")


def _require_idle_limit_up_shadow(service: str) -> None:
    state = _run(["systemctl", "show", service, "--property=ActiveState", "--value"])
    if state.stdout.strip() not in {"inactive", "failed"}:
        raise ValueError("refusing to switch limit-up-shadow while running or unknown")


def _sqlite_fingerprint(connection: sqlite3.Connection) -> dict[str, object]:
    """Hash schema and every stored value; no source facts are reconstructed."""
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("limit-up-shadow state failed SQLite integrity_check")
    schema = connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    ).fetchall()
    tables = [row[1] for row in schema if row[0] == "table"]
    required = {"limit_up_shadow_runs", "limit_up_shadow_entries", "limit_up_shadow_outcomes"}
    if not required.issubset(tables):
        raise ValueError("limit-up-shadow state is missing history tables")
    result: dict[str, object] = {"schema": schema}
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        digest = hashlib.sha256()
        rows = 0
        for row in connection.execute(f"SELECT * FROM {quoted} ORDER BY rowid"):
            encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":"),
                                 default=lambda value: {"blob": value.hex()})
            digest.update(encoded.encode("utf-8") + b"\n")
            rows += 1
        result[table] = {"rows": rows, "sha256": digest.hexdigest()}
    return result


def _read_sqlite_fingerprint(path: Path, *, require_delete: bool = False) -> dict[str, object]:
    if path.is_symlink() or not path.is_file() or not path.stat().st_size:
        raise ValueError("limit-up-shadow state must be an existing nonempty regular file")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        if require_delete and connection.execute("PRAGMA journal_mode").fetchone() != ("delete",):
            raise ValueError("isolated history must use DELETE journal mode")
        return _sqlite_fingerprint(connection)


def _seed_limit_up_shadow(source: Path, seed: dict, *, initial: bool) -> None:
    """Consistently copy SQLite history once, without overwriting either database."""
    target = Path(str(seed["target"]))
    if any(path.is_symlink() for path in (target, *target.parents)):
        raise ValueError("limit-up-shadow state path must not contain symlinks")
    if target.exists():
        if not target.is_file() or not target.stat().st_size:
            raise ValueError("limit-up-shadow state must be an existing nonempty regular file")
        # GNU install removes inherited named ACLs. Repair a retained seed from
        # an interrupted first activation before the unprivileged reader opens it.
        _sudo("setfacl", "-P", "-m", "u:ubuntu:r--,g:qsys-observers:r--", "--", str(target))
        existing = _read_sqlite_fingerprint(target, require_delete=True)
        if initial and existing != _read_sqlite_fingerprint(source):
            raise ValueError("existing isolated history differs from legacy; reconcile without overwriting")
        return
    if not initial:
        raise ValueError("isolated history is missing during upgrade; refusing to restore frozen legacy history")
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"refusing to initialize component state without seed: {source}")
    with tempfile.TemporaryDirectory(prefix="qsys-limit-up-state-") as temporary:
        snapshot = Path(temporary) / "history.sqlite3"
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original:
            original.execute("BEGIN")
            expected = _sqlite_fingerprint(original)
            with closing(sqlite3.connect(snapshot)) as copied:
                original.backup(copied)
                copied.commit()
                if copied.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
                    raise ValueError("isolated history must use DELETE journal mode")
                if _sqlite_fingerprint(copied) != expected:
                    raise ValueError("SQLite history copy differs from legacy")
        if _read_sqlite_fingerprint(source) != expected:
            raise ValueError("legacy history changed during migration; wait for the writer")
        staged = target.parent / f".{target.name}.seed-{os.getpid()}"
        try:
            _sudo("install", "-o", str(seed["owner"]), "-g", str(seed["group"]),
                  "-m", str(seed["mode"]), str(snapshot), str(staged))
            _sudo("setfacl", "-P", "-m", "u:ubuntu:r--,g:qsys-observers:r--", "--", str(staged))
            # ln without -f atomically fails when another target already exists.
            _sudo("ln", "-T", "--", str(staged), str(target))
        finally:
            _sudo("rm", "-f", "--", str(staged), check=False)
        if _read_sqlite_fingerprint(target, require_delete=True) != expected:
            raise ValueError("installed SQLite history differs from legacy")


def _require_no_later_component_override(spec: dict) -> None:
    owned = Path(str(spec["dropin"]))
    protected = {"ExecStart", "ExecStartPre", "ExecStartPost", "ExecStop", "ExecStopPost", "ExecCondition", "ExecReload",
                 "User", "Group", "SupplementaryGroups", "WorkingDirectory", "ReadWritePaths",
                 "UMask", "RootDirectory", "RootImage"}
    protected.update(line.split("=", 1)[0] for line in spec["dropin_lines"] if "=" in line)
    for path in owned.parent.glob("*.conf"):
        if path.name <= owned.name:
            continue
        section = ""
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if line.startswith(("#", ";")):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line
            elif section == "[Service]" and "=" in line:
                if line.split("=", 1)[0].strip() in protected:
                    raise ValueError("later platform drop-in overrides component runtime; "
                                     "migrate conflicting drop-in before activation")


def activate(
    repo: Path, sha: str, component: str, *, trusted_spec: Path | None = None,
) -> Path:
    repo = repo.resolve(strict=True)
    if not SHA.fullmatch(sha):
        raise ValueError("code SHA must contain exactly 40 lowercase hexadecimal characters")
    components = load_component_releases(repo)
    if component not in components:
        raise ValueError(f"unsupported component: {component}")
    spec = components[component]
    if trusted_spec is not None:
        trusted = load_component_releases_file(trusted_spec)
        if trusted.get(component) != spec:
            raise ValueError("target component specification differs from installed whitelist")
    release_root = Path(str(spec["release_root"])) / sha
    current = Path(str(spec["current"]))
    executable = release_root / str(spec["executable"])
    dropin = Path(str(spec["dropin"]))
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError(f"versioned component release is incomplete: {release_root}")
    if not (release_root / "release.json").is_file():
        raise ValueError(f"versioned component release is incomplete: {release_root}")
    verify(repo, sha, component, release_root)
    writable = _writable_by_group_or_other(release_root)
    if writable is not None:
        raise ValueError(f"component release must not be writable by group or other: {writable}")
    active = _run(
        ["systemctl", "is-active", "--quiet", str(spec["unit"])], check=False,
    )
    if active.returncode == 0:
        raise ValueError(f"refusing to switch component while its unit is active: {spec['unit']}")

    if component == "reboot-drill":
        _require_idle_reboot_drill(str(spec["unit"]))
    if component == "limit-up-shadow":
        _require_idle_limit_up_shadow(str(spec["unit"]))

    _require_no_later_component_override(spec)
    _sudo("chown", "-R", "root:root", str(release_root))
    for path in {release_root.parent, current.parent, dropin.parent}:
        _sudo("install", "-d", "-o", "root", "-g", "root", "-m", "0755", str(path))
    for directory in spec["state_directories"]:
        _sudo(
            "install", "-d", "-o", str(directory["owner"]),
            "-g", str(directory["group"]), "-m", str(directory["mode"]),
            str(directory["path"]),
        )
    seed = spec["seed"]
    if seed is not None and component == "limit-up-shadow":
        source = Path(os.environ.get(str(seed["source_environment"]), str(repo / str(seed["source"]))))
        _seed_limit_up_shadow(source, seed, initial=not current.is_symlink())
        _require_idle_limit_up_shadow(str(spec["unit"]))
    elif seed is not None and not Path(str(seed["target"])).is_file():
        if component == "catalog-verify" and current.is_symlink():
            raise ValueError("refusing to recreate missing frozen baseline during catalog upgrade")
        source = Path(
            os.environ.get(str(seed["source_environment"]), str(repo / str(seed["source"])))
        )
        if not source.is_file():
            raise ValueError(f"refusing to initialize component state without seed: {source}")
        _sudo(
            "install", "-o", str(seed["owner"]), "-g", str(seed["group"]),
            "-m", str(seed["mode"]), str(source), str(seed["target"]),
        )

    previous = os.readlink(current) if current.is_symlink() else None
    previous_dropin = dropin.read_bytes() if dropin.is_file() else None
    activated = False
    try:
        _switch_current(current, release_root, sha)
        activated = True
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as stream:
            stream.write("\n".join(str(line) for line in spec["dropin_lines"]) + "\n")
            stream.flush()
            _install_file(Path(stream.name), dropin)
        _sudo("systemctl", "daemon-reload")
        if current.resolve(strict=True) != release_root.resolve(strict=True):
            raise ValueError("component current pointer does not match the target release")
    except (OSError, ValueError, subprocess.CalledProcessError):
        if activated:
            if component == "reboot-drill":
                _require_idle_reboot_drill(str(spec["unit"]))
            _restore(current, previous, dropin, previous_dropin)
        raise
    return release_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--sha", required=True)
    parser.add_argument("--component", required=True)
    parser.add_argument(
        "--trusted-spec", type=Path,
        default=Path("/etc/qsys/component-releases.json"),
    )
    args = parser.parse_args(argv)
    try:
        release = activate(
            args.repo, args.sha, args.component, trusted_spec=args.trusted_spec,
        )
        print(f"activated isolated {args.component} release: {release.name}")
        return 0
    except (OSError, ValueError, sqlite3.Error, subprocess.CalledProcessError) as exc:
        print(str(exc), file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
