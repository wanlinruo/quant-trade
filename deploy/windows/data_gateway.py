"""Fixed OpenSSH command for component-scoped QMT data downloads (stdlib only)."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import date

PROTOCOL = 1
COMPONENTS = {"tushare-sync", "daily-archive"}
MAX_MANIFEST = 32 * 1024 * 1024
MAX_FILE = 256 * 1024 * 1024
SHA = re.compile(r"[0-9a-f]{64}")
JOB = re.compile(r"(?:minute|auction)_[0-9]{8}")
FILE = re.compile(r"(?:[A-Za-z0-9_.-]{1,100}\.(?:parquet|json)|(?:done|failed)\.csv)")
FIELDS = {
    "probe": set(), "prepare": {"job"},
    "put": {"job", "size", "sha256"},
    "run": {"job", "sha256", "run_id", "start", "end"},
    "list": {"job"}, "get": {"job", "file"},
}


class DownloaderExit(Exception):
    def __init__(self, returncode: int):
        self.returncode = returncode if 0 < returncode < 256 else 1


def request(command: str) -> dict:
    if len(command) > 8192 or not re.fullmatch(r"qsys-data [A-Za-z0-9_-]+={0,2}", command):
        raise ValueError("unsupported data command")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate request field")
            result[key] = value
        return result
    value = json.loads(base64.b64decode(command.split()[1], altchars=b"-_", validate=True),
                       object_pairs_hook=pairs)
    if not isinstance(value, dict) or value.get("op") not in FIELDS:
        raise ValueError("unsupported operation")
    if set(value) != {"op", *FIELDS[value["op"]]}:
        raise ValueError("invalid operation fields")
    if "job" in value:
        if not isinstance(value["job"], str) or not JOB.fullmatch(value["job"]):
            raise ValueError("invalid job")
        date.fromisoformat(value["job"][-8:-4]+"-"+value["job"][-4:-2]+"-"+value["job"][-2:])
    return value


def regular(path: Path) -> None:
    # A junction/reparse point can escape an otherwise lexical Windows namespace.
    for part in [path, *path.parents]:
        if part.is_symlink() or (part.exists() and
                                getattr(part.lstat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("reparse points are forbidden")


def validate_manifest(data: bytes, *, start: date | None = None, end: date | None = None) -> None:
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    if reader.fieldnames != ["symbol", "dt"]:
        raise ValueError("manifest must contain symbol,dt")
    count = 0
    for row in reader:
        if set(row) != {"symbol", "dt"} or not re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", row["symbol"]):
            raise ValueError("invalid manifest symbol")
        stamp = date.fromisoformat(row["dt"])
        if stamp.isoformat() != row["dt"]:
            raise ValueError("invalid manifest date")
        if start is not None and end is not None and not start <= stamp <= end:
            raise ValueError("manifest date outside approved download window")
        count += 1
        if count > 200000:
            raise ValueError("too many manifest stock-days")
    if not count:
        raise ValueError("empty manifest")


class Gateway:
    def __init__(self, config: dict, component: str):
        if component not in COMPONENTS or (type(config.get("protocol")) is not int or config.get("protocol") != PROTOCOL):
            raise ValueError("invalid gateway configuration")
        roots = config["roots"]
        if set(roots) != COMPONENTS:
            raise ValueError("both component namespaces required")
        resolved = [Path(path).resolve() for path in roots.values()]
        if (any(not Path(path).is_absolute() for path in roots.values())
                or resolved[0] == resolved[1]
                or resolved[0] in resolved[1].parents or resolved[1] in resolved[0].parents):
            raise ValueError("component roots must be absolute and disjoint")
        self.root = Path(roots[component])
        self.python = Path(config["python"])
        self.scripts = config["scripts"]
        if not self.python.is_absolute() or set(self.scripts) != {"minute", "auction"}:
            raise ValueError("fixed python and both approved downloaders required")
        for item in self.scripts.values():
            if set(item) != {"path", "sha256"} or not SHA.fullmatch(str(item["sha256"])):
                raise ValueError("invalid downloader configuration")
            path = Path(item["path"])
            if not path.is_absolute() or any(root == path.resolve() or root in path.resolve().parents
                                             for root in resolved):
                raise ValueError("downloaders must be outside writable namespaces")
            regular(path)
            if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError("configured downloader hash mismatch")
        regular(self.root)
        self.component = component

    def folder(self, job: str) -> Path:
        if not isinstance(job, str) or not JOB.fullmatch(job):
            raise ValueError("invalid job")
        path = self.root / job
        regular(path)
        if path.resolve().parent != self.root.resolve():
            raise ValueError("job outside component root")
        return path

    def execute(self, req: dict, incoming, outgoing) -> dict | None:
        op = req["op"]
        if op == "probe":
            return {"protocol": PROTOCOL, "component": self.component,
                    "scripts": {kind: item["sha256"] for kind, item in self.scripts.items()}}
        folder = self.folder(req["job"])
        if op == "prepare":
            folder.mkdir(parents=True, exist_ok=True)
            return {"prepared": True}
        if not folder.is_dir():
            raise ValueError("prepare job first")
        if op == "put":
            size = req["size"]
            if type(size) is not int or not 0 < size <= MAX_MANIFEST or not SHA.fullmatch(str(req["sha256"])):
                raise ValueError("invalid upload bounds")
            data = incoming.read(size + 1)
            if len(data) != size or hashlib.sha256(data).hexdigest() != req["sha256"]:
                raise ValueError("upload size/hash mismatch")
            validate_manifest(data)
            target = folder / "manifest.csv"
            regular(target)
            temporary = folder / f".manifest-{os.getpid()}.tmp"
            try:
                with temporary.open("xb") as stream:
                    stream.write(data)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            return {"uploaded": size}
        if op == "run":
            kind = req["job"].split("_")[0]
            info = self.scripts[kind]
            script = Path(info["path"])
            regular(script)
            if (not SHA.fullmatch(str(req["sha256"])) or req["sha256"] != info["sha256"]
                    or hashlib.sha256(script.read_bytes()).hexdigest() != info["sha256"]):
                raise ValueError("downloader hash is not approved")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(req["run_id"])):
                raise ValueError("invalid run identity")
            start, end = (date.fromisoformat(req[key]) for key in ("start", "end"))
            if start > end or (end-start).days > 31:
                raise ValueError("invalid download window")
            manifest = folder / "manifest.csv"
            regular(manifest)
            validate_manifest(manifest.read_bytes(), start=start, end=end)
            command = [str(self.python), "-I", "-B", "-X", "utf8", "-u", str(script),
                       "--manifest", str(manifest), "--out", str(folder), "--sleep", "0.1"]
            if kind == "minute":
                command += ["--window-start", start.strftime("%Y%m%d"),
                            "--window-end", end.strftime("%Y%m%d"),
                            "--run-id", req["run_id"], "--whole-window"]
            result = subprocess.run(command, shell=False, timeout=7200,
                                    env={**os.environ, "PYTHONIOENCODING": "utf-8",
                                         "PYTHONDONTWRITEBYTECODE": "1"})
            if result.returncode:
                raise DownloaderExit(result.returncode)
            return None  # stdout is the unchanged downloader progress protocol.
        if op == "list":
            files = []
            for path in sorted(folder.iterdir()):
                if not FILE.fullmatch(path.name) or ".." in path.name:
                    continue
                regular(path)
                if not path.is_file() or path.stat().st_size > MAX_FILE:
                    raise ValueError("invalid output file")
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                files.append({"name": path.name, "size": path.stat().st_size, "sha256": digest})
            if len(files) > 20000:
                raise ValueError("too many output files")
            return {"files": files}
        name = req["file"]
        if not isinstance(name, str) or not FILE.fullmatch(name) or ".." in name:
            raise ValueError("invalid output filename")
        path = folder / name
        regular(path)
        if not path.is_file() or path.stat().st_size > MAX_FILE:
            raise ValueError("invalid output file")
        with path.open("rb") as stream:
            while chunk := stream.read(65536):
                outgoing.write(chunk)
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--component", choices=sorted(COMPONENTS), required=True)
    args = parser.parse_args(argv)
    try:
        gateway = Gateway(json.loads(Path(args.config).read_text(encoding="utf-8")), args.component)
        result = gateway.execute(request(os.environ.get("SSH_ORIGINAL_COMMAND", "")),
                                 sys.stdin.buffer, sys.stdout.buffer)
        if result is not None:
            print(json.dumps(result, ensure_ascii=True))
        return 0
    except DownloaderExit as exc:
        return exc.returncode
    except Exception as exc:
        print(f"data gateway refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
