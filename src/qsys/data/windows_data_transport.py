"""Versioned SSH data protocol; remote credentials cannot execute arbitrary commands."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

import structlog

PROTOCOL = 1
MAX_FILE = 256 * 1024 * 1024
NAME = re.compile(r"(?:[A-Za-z0-9_.-]{1,100}\.(?:parquet|json)|(?:done|failed)\.csv)")
PREAUTH_DELAYS = (2, 5)
log = structlog.get_logger('windows_data_transport')


def preauthentication_reset(returncode: int, output: bytes | str, *, error: str = '') -> bool:
    """Only the observed SSH key-exchange failure, before authentication/exec.

    Any business output, timeout, authentication or session error forbids replay.
    """
    if returncode != 255 or error:
        return False
    text = output.decode('utf-8', errors='replace') if isinstance(output, bytes) else output
    lines = text.strip().splitlines()
    return bool(lines and lines[0] == 'kex_exchange_identification: read: Connection reset by peer'
                and all(re.fullmatch(r'Connection reset by [A-Za-z0-9.:_-]+ port [0-9]+', line)
                        for line in lines[1:]))


class WindowsDataTransport:
    def __init__(self, host: str, key: str, *, component: str, known_hosts: str):
        if component not in {"daily-archive", "tushare-sync"}:
            raise ValueError("unsupported data component")
        if not known_hosts:
            raise ValueError("explicit trusted known_hosts required")
        if not re.fullmatch(r"[A-Za-z0-9_.@:-]+", host) or host.startswith("-"):
            raise ValueError("invalid SSH destination")
        self.component = component
        self.ssh = ["ssh", "-F", "/dev/null", "-o", "IdentitiesOnly=yes", "-i", str(Path(key).expanduser()), "-o", "BatchMode=yes",
                    "-o", "ConnectTimeout=20", "-o", "ServerAliveInterval=15",
                    "-o", "ServerAliveCountMax=2", "-o", "StrictHostKeyChecking=yes",
                    "-o", "UserKnownHostsFile=" + str(known_hosts), host]

    def command(self, op: str, **fields) -> list[str]:
        payload = json.dumps({"op": op, **fields}, separators=(",", ":"), ensure_ascii=True)
        encoded = base64.urlsafe_b64encode(payload.encode()).decode()
        return self.ssh + ["qsys-data " + encoded]

    def _json(self, op: str, *, data: bytes | None = None, **fields) -> dict:
        completed = self._run_ssh(self.command(op, **fields), input=data,
                                  capture_output=True, timeout=120, check=True)
        value = json.loads(completed.stdout)
        if not isinstance(value, dict):
            raise ValueError("invalid data gateway response")
        return value

    def _run_ssh(self, command, **kwargs):
        stream = kwargs.get('stdout')
        start_position = stream.tell() if stream is not None else None
        deadline = time.monotonic() + kwargs['timeout']
        for attempt in range(len(PREAUTH_DELAYS) + 1):
            try:
                return subprocess.run(command, **{**kwargs, 'timeout': max(1, math.ceil(deadline-time.monotonic()))})
            except subprocess.CalledProcessError as exc:
                no_output = not exc.stdout and (stream is None or stream.tell() == start_position)
                if (not no_output or not preauthentication_reset(exc.returncode, exc.stderr or b'')
                        or attempt == len(PREAUTH_DELAYS) or deadline-time.monotonic() <= PREAUTH_DELAYS[attempt]):
                    raise
                delay = PREAUTH_DELAYS[attempt]
                log.warning('ssh.preauth_retry', component=self.component, attempt=attempt+1, delay_seconds=delay)
                time.sleep(delay)

    def run_download(self, command, *, runner, **kwargs):
        deadline = time.monotonic() + kwargs['timeout']
        for attempt in range(len(PREAUTH_DELAYS) + 1):
            result = runner(command, **{**kwargs, 'timeout': max(1, math.ceil(deadline-time.monotonic()))})
            if (not preauthentication_reset(result.returncode, result.output, error=result.error)
                    or attempt == len(PREAUTH_DELAYS) or deadline-time.monotonic() <= PREAUTH_DELAYS[attempt]):
                return result
            delay = PREAUTH_DELAYS[attempt]
            log.warning('ssh.preauth_retry', component=self.component, attempt=attempt+1, delay_seconds=delay)
            time.sleep(delay)

    def probe(self) -> dict:
        value = self._json("probe")
        if (type(value.get("protocol")) is not int or value.get("protocol") != PROTOCOL
                or value.get("component") != self.component):
            raise ValueError("data gateway protocol/component mismatch")
        return value

    def prepare(self, job: str, manifest: Path) -> None:
        self._json("prepare", job=job)
        data = manifest.read_bytes()
        result = self._json("put", data=data, job=job, size=len(data),
                            sha256=hashlib.sha256(data).hexdigest())
        if result.get("uploaded") != len(data):
            raise ValueError("manifest upload not acknowledged")

    def download_command(self, job: str, script: Path, run_id: str,
                         start, end) -> list[str]:
        digest = hashlib.sha256(script.read_bytes()).hexdigest()
        return self.command("run", job=job, sha256=digest, run_id=run_id,
                            start=start.isoformat(), end=end.isoformat())

    def receive(self, job: str, destination: Path) -> int:
        files = self._json("list", job=job).get("files")
        if not isinstance(files, list) or len(files) > 20000:
            raise ValueError("invalid output inventory")
        if any(p.is_symlink() for p in [destination, *destination.parents]):
            raise ValueError("refusing output directory symlink")
        destination.mkdir(parents=True, exist_ok=True)
        seen = set()
        for item in files:
            if not isinstance(item, dict) or set(item) != {"name", "size", "sha256"}:
                raise ValueError("invalid output inventory entry")
            name, size, digest = item["name"], item["size"], item["sha256"]
            if (not isinstance(name, str) or not NAME.fullmatch(name) or ".." in name
                    or name in seen or type(size) is not int or not 0 <= size <= MAX_FILE
                    or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise ValueError("unsafe output inventory entry")
            seen.add(name)
            target = destination / name
            if target.is_symlink():
                raise ValueError("refusing output symlink")
            # Parquet is immutable resume evidence; mutable progress keeps its previous bytes.
            previous_digest = None
            if target.exists():
                with target.open("rb") as stream:
                    actual = hashlib.file_digest(stream, "sha256").hexdigest()
                if target.stat().st_size == size and actual == digest:
                    continue
                if name not in {"progress.json", "result.json", "done.csv", "failed.csv"}:
                    raise ValueError(f"existing download differs: {name}")
                previous_digest = actual
            with tempfile.NamedTemporaryFile(dir=destination, prefix=".download-", delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    self._run_ssh(self.command("get", job=job, file=name), stdout=stream,
                                  stderr=subprocess.PIPE, timeout=120, check=True)
                    stream.flush()
                    with temporary.open("rb") as downloaded:
                        actual = hashlib.file_digest(downloaded, "sha256").hexdigest()
                    if temporary.stat().st_size != size or actual != digest:
                        raise ValueError(f"output size/hash mismatch: {name}")
                    # No replace: another writer must not change a verified existing result.
                    if previous_digest is not None:
                        backup = destination / (".previous-" + name + "-" + previous_digest)
                        if not backup.exists():
                            os.link(target, backup)
                        if backup.is_symlink():
                            raise ValueError("refusing metadata backup symlink")
                        with backup.open("rb") as saved:
                            if hashlib.file_digest(saved, "sha256").hexdigest() != previous_digest:
                                raise ValueError("metadata backup mismatch")
                        os.replace(temporary, target)
                    else:
                        os.link(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
        return len(files)
