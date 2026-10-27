"""Verify the reader with synthetic SQLite facts and real Linux service users.

Run as ubuntu with sudo, using a new issue544-* audit directory.  This never
opens the production database or starts/stops any production unit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time


def run(*command: str, check: bool = True):
    return subprocess.run(command, check=check, text=True, capture_output=True, timeout=45)


def sudo(*command: str, check: bool = True):
    return run("sudo", "-n", *command, check=check)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.audit_root
    if (root.parent != Path("/data/qsys/var-deployments")
            or not re.fullmatch(r"issue544-[a-z0-9-]+", root.name)
            or any(p.is_symlink() for p in (root, *root.parents))):
        raise ValueError("use a new direct issue544-* audit directory")
    root.mkdir(mode=0o755)  # Existing evidence must never be overwritten.
    source = args.source.read_bytes()
    module = root / "reader.py"
    module.write_bytes(source)
    state = root / "state"
    state.mkdir(mode=0o700)
    database = state / "live-state.sqlite3"
    sudo("chown", "qsys-live-core:qsys-live-core", str(state))
    sudo("setfacl", "-m", "u:ubuntu:r-x,g:qsys-observers:r-x,"
         "d:u:ubuntu:r-x,d:g:qsys-observers:r-x", str(state))
    sudo("-u", "qsys-live-core", "/usr/bin/python3", "-I", "-B", "-c", """
import sqlite3,sys
from pathlib import Path
c=sqlite3.connect(sys.argv[1]); c.execute('PRAGMA journal_mode=WAL')
c.execute('CREATE TABLE live_events (id INTEGER PRIMARY KEY)')
c.execute('CREATE TABLE live_strategy_runs (id INTEGER PRIMARY KEY)')
c.execute('INSERT INTO live_events VALUES (1)'); c.commit(); c.close()
Path(sys.argv[1]).with_name('KILL').write_text('synthetic fixture KILL')
Path(sys.argv[1]).with_name('live-core.lock').touch()
""", str(database))
    runner = root / "fixture-runner.py"
    runner.write_text("""
import importlib.util,json,os,sqlite3,sys,time
from pathlib import Path
spec=importlib.util.spec_from_file_location('reader',sys.argv[1])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
with module.reader_anchor(Path(sys.argv[2]),identity_path=Path(sys.argv[3])) as (c,verify):
    denied={}
    try: c.execute('DELETE FROM live_events')
    except sqlite3.Error: denied['sql']=True
    for key,path in [('main',sys.argv[2]),('directory_view',sys.argv[3]),
                     ('kill',str(Path(sys.argv[2]).with_name('KILL'))),
                     ('lock',str(Path(sys.argv[2]).with_name('live-core.lock')))]:
        try:
            fd=os.open(path,os.O_WRONLY);os.close(fd)
        except OSError: denied[key]=True
    assert denied=={'sql':True,'main':True,'directory_view':True,'kill':True,'lock':True},denied
    print(json.dumps({'fixture_ready':True,'writes_denied':denied}),flush=True)
    while True:
        time.sleep(0.1);verify()
""")
    sudo("chown", "root:root", str(root), str(module), str(runner))
    sudo("chmod", "0755", str(root))
    sudo("chmod", "0644", str(module), str(runner))
    unit = "qsys-" + root.name
    read_code = """
import json,sqlite3,sys
try:
    c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True)
    print(json.dumps({'readable':True,'rows':c.execute('SELECT count(*) FROM live_events').fetchone()[0]}))
    c.close()
except sqlite3.Error as e:
    print(json.dumps({'readable':False,'error':getattr(e,'sqlite_errorname',type(e).__name__)}))
"""
    readers = ("qsys-service-checks", "qsys-health-observer")
    report = {"source_sha256": hashlib.sha256(source).hexdigest(), "unit": unit,
              "production_database_opened": False, "observations": []}
    counter = 0

    def observe(phase, expected):
        nonlocal counter
        for user in readers:
            counter += 1
            result = sudo(
                "systemd-run", "--quiet", "--wait", "--pipe", "--collect",
                "--unit", unit + "-read-" + str(counter),
                "-p", "User=" + user, "-p", "ProtectSystem=strict",
                "-p", "ProtectHome=read-only", "-p", "PrivateTmp=true",
                "/usr/bin/python3", "-I", "-B", "-c", read_code, str(database),
            )
            value = json.loads(result.stdout.strip())
            report["observations"].append({"phase": phase, "user": user, **value})
            assert value["readable"] == (expected is not None), value
            if expected is not None:
                assert value["rows"] == expected, value

    try:
        observe("cold", None)
        properties = [
            "User=qsys-live-core", "Group=qsys-live-core", "SupplementaryGroups=qsys-observers",
            "ProtectSystem=strict", "ProtectHome=read-only", "PrivateTmp=true",
            "PrivateNetwork=true", "PrivateDevices=true", "NoNewPrivileges=true",
            "InaccessiblePaths=/home/ubuntu/qsys/config /etc/qsys/components/live-core",
            "CapabilityBoundingSet=", "RestrictAddressFamilies=AF_UNIX",
            "RestrictSUIDSGID=true", "LockPersonality=true",
            "UMask=0027", "MemoryMax=128M", "TasksMax=8",
            "ReadWritePaths=" + str(state),
            "ReadOnlyPaths=" + " ".join(str(p) for p in
                                         (database, state / "KILL", state / "live-core.lock")),
            "BindReadOnlyPaths=" + str(state) + ":/run/qsys-live-state-reader-source:norbind",
        ]
        command = ["systemd-run", "--quiet", "--unit", unit]
        for property in properties:
            command.extend(["-p", property])
        sudo(*command, "/usr/bin/python3", "-I", "-B", str(runner), str(module),
             str(database), "/run/qsys-live-state-reader-source/live-state.sqlite3")
        limit = time.monotonic() + 15
        while time.monotonic() < limit:
            journal = sudo("journalctl", "-u", unit, "--no-pager", "-o", "cat").stdout
            if '"fixture_ready": true' in journal:
                break
            if run("systemctl", "is-failed", "--quiet", unit, check=False).returncode == 0:
                raise RuntimeError(journal)
            time.sleep(0.2)
        else:
            raise TimeoutError("reader fixture did not become ready")
        report["writes_denied"] = {
            "sql": True, "main": True, "directory_view": True, "kill": True, "lock": True,
        }
        observe("pinned", 1)
        sudo("-u", "qsys-live-core", "/usr/bin/python3", "-I", "-B", "-c", """
import gc,sqlite3,sys
c=sqlite3.connect(sys.argv[1]); c.execute('INSERT INTO live_events VALUES (2)');c.commit()
assert c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]==0
c.close();gc.collect()
""", str(database))
        observe("new_commit_after_writer_close", 2)
        sudo("-u", "qsys-live-core", "/usr/bin/python3", "-I", "-B", "-c", """
import os,sqlite3,sys
source=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True)
target=sqlite3.connect(sys.argv[1]+'.replacement');source.backup(target)
target.close();source.close();os.replace(sys.argv[1]+'.replacement',sys.argv[1])
""", str(database))
        limit = time.monotonic() + 10
        while time.monotonic() < limit:
            if run("systemctl", "is-active", "--quiet", unit, check=False).returncode:
                break
            time.sleep(0.1)
        journal = sudo("journalctl", "-u", unit, "--no-pager", "-o", "cat").stdout
        replacements = (
            "state file was replaced; refusing to pin stale history",
            "state file was replaced outside the read-only file mount",
        )
        assert any(message in journal for message in replacements), journal
        assert "status=1/FAILURE" in journal, journal
        report["replacement_detected"] = True
        report["ok"] = True
    except Exception as exc:
        report.update(ok=False, error=str(exc)[-3000:])
    finally:
        report["journal"] = sudo("journalctl", "-u", unit, "--no-pager", "-o", "cat",
                                 check=False).stdout
        report["unit_terminal"] = run("systemctl", "show", unit,
                                   "--property=ActiveState,SubState,MainPID", check=False).stdout
        # Only this synthetic fixture unit is stopped. Its journal and terminal
        # record remain; production failure history is never cleared.
        sudo("systemctl", "stop", unit, check=False)
        sudo("systemctl", "reset-failed", unit, check=False)
        report["unit_after"] = run("systemctl", "show", unit,
                                   "--property=ActiveState,SubState,MainPID", check=False).stdout
        with tempfile.NamedTemporaryFile(mode="w") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.flush()
            sudo("install", "-o", "root", "-g", "root", "-m", "0644",
                 stream.name, str(root / "result.json"))
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
