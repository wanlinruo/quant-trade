"""storage-report 单组件预生产试点；仅允许一次性 GitHub-hosted Linux。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile

if __package__:
    from scripts.build_component_release import verify
else:
    from build_component_release import verify

SHA = re.compile(r"[0-9a-f]{40}")
REPOSITORY = "wanlinruo/quant-trade"


def run(command, *, check=True, timeout=180, **kwargs):
    return subprocess.run([str(x) for x in command], check=check, text=True,
                          capture_output=True, timeout=timeout, **kwargs)


def gh(path):
    return json.loads(run(["gh", "api", path]).stdout)


def validate_host(env):
    if (env.get("GITHUB_ACTIONS") != "true"
            or env.get("QSYS_PILOT_RUNNER_ENVIRONMENT") != "github-hosted"
            or env.get("GITHUB_REPOSITORY") != REPOSITORY or sys.platform != "linux"):
        raise ValueError("pilot requires the dedicated GitHub-hosted Linux workflow")
    for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        if not re.fullmatch(r"[1-9][0-9]*", env.get(key, "")):
            raise ValueError("invalid workflow identity")


def extract_zip(archive, destination):
    with zipfile.ZipFile(archive) as z:
        names = set()
        for member in z.infolist():
            p = Path(member.filename)
            if (p.is_absolute() or ".." in p.parts or member.filename in names
                    or (member.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("unsafe artifact archive")
            names.add(member.filename)
        if sum(x.file_size for x in z.infolist()) > 100 * 1024 * 1024:
            raise ValueError("oversized artifact")
        z.extractall(destination)


def stage(repo, root, sha):
    # Only commits already merged into trusted main may supply code/artifacts.
    run(["git", "merge-base", "--is-ancestor", sha, "origin/main"], cwd=repo)
    runs = gh(f"repos/{REPOSITORY}/actions/workflows/ci.yml/runs?head_sha={sha}&per_page=100")
    candidates = [r for r in runs["workflow_runs"] if r["conclusion"] == "success"
                  and r["head_sha"] == sha and r["head_branch"] == "test"
                  and r["event"] in {"push", "workflow_dispatch"}]
    if not candidates:
        raise ValueError("no successful exact-SHA test CI")
    ci = max(candidates, key=lambda x: x["id"])
    jobs = gh(f"repos/{REPOSITORY}/actions/runs/{ci['id']}/jobs?per_page=100")["jobs"]
    for name in ("test", "package", "policy", "secret-scan"):
        if not any(j["name"] == name and j["conclusion"] == "success" for j in jobs):
            raise ValueError("required CI job missing")
    artifacts = gh(f"repos/{REPOSITORY}/actions/runs/{ci['id']}/artifacts?per_page=100")["artifacts"]
    matches = [a for a in artifacts if a["name"] == f"qsys-storage-report-{sha}" and not a["expired"]]
    if len(matches) != 1:
        raise ValueError("missing or ambiguous component artifact")
    artifact = matches[0]
    archive = root / f"{sha}.zip"
    with archive.open("wb") as f:
        subprocess.run(["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{artifact['id']}/zip"],
                       stdout=f, check=True, timeout=120)
    digest = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != artifact.get("digest"):
        raise ValueError("artifact digest mismatch")
    release = root / "releases" / sha
    extract_zip(archive, release)
    checkout = root / "checkouts" / sha
    run(["git", "worktree", "add", "--detach", checkout, sha], cwd=repo)
    manifest = verify(checkout, sha, "storage-report", release)
    if (manifest["ci"]["repository"] != REPOSITORY
            or str(manifest["ci"]["run_id"]) != str(ci["id"])
            or str(manifest["ci"]["run_attempt"]) != str(ci["run_attempt"])):
        raise ValueError("artifact provenance mismatch")
    environment = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(release / "venv"), UV_LINK_MODE="copy")
    run(["uv", "sync", "--project", checkout, "--python", "/usr/bin/python3",
         "--frozen", "--no-dev", "--no-install-project"], env=environment, timeout=900)
    run(["uv", "pip", "install", "--python", release / "venv/bin/python", "--no-deps",
         release / "dist" / manifest["wheel_filename"]])
    run(["sudo", "chown", "-R", "root:root", release])
    run(["sudo", "chmod", "-R", "go-w", release])
    return release, {"sha": sha, "tree": manifest["source_tree"], "ci_run_id": ci["id"],
                     "artifact_id": artifact["id"], "artifact_digest": digest,
                     "wheel_sha256": manifest["wheel_sha256"]}


def identity(unit):
    value = run(["systemctl", "show", unit, "--property=MainPID,ActiveEnterTimestampMonotonic,NRestarts,ActiveState"]).stdout
    fields = dict(line.split("=", 1) for line in value.splitlines() if "=" in line)
    if fields.get("ActiveState") != "active" or fields.get("MainPID") in {None, "0"}:
        raise ValueError("pilot peer is not alive")
    return fields


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-sha", required=True)
    parser.add_argument("--target-sha", required=True)
    args = parser.parse_args(argv)
    validate_host(os.environ)
    if not all(SHA.fullmatch(x) for x in (args.baseline_sha, args.target_sha)) or args.baseline_sha == args.target_sha:
        raise ValueError("two distinct exact SHAs required")
    repo = Path(__file__).resolve().parents[1]
    suffix = os.environ["GITHUB_RUN_ID"] + "-" + os.environ["GITHUB_RUN_ATTEMPT"]
    root = Path("/var/tmp") / ("qsys-storage-pilot-" + suffix)
    root.mkdir(mode=0o755)  # Refuse reuse; never clean arbitrary paths.
    evidence_dir = Path(os.environ["RUNNER_TEMP"]) / root.name / "evidence"
    evidence_dir.mkdir(parents=True)
    evidence = {"schema_version": 1, "kind": "storage-report-preproduction",
                "status": "running", "environment": "ephemeral-github-hosted",
                "production_changed": False, "phases": []}
    units = []
    peer = "qsys-storage-pilot-peer-" + suffix
    user = "qsys-storage-pilot"
    try:
        baseline, baseline_meta = stage(repo, root, args.baseline_sha)
        target, target_meta = stage(repo, root, args.target_sha)
        evidence["releases"] = {"baseline": baseline_meta, "target": target_meta}
        fixture = root / "fixture"
        (fixture / "warehouse").mkdir(parents=True)
        marker = fixture / "warehouse/fixture.txt"
        marker.write_text("storage test fixture; no financial records\n")
        (fixture / "backups").mkdir()
        os.link(marker, fixture / "backups/fixture-link.txt")
        original = hashlib.sha256(marker.read_bytes()).hexdigest()
        state = root / "state"
        state.mkdir()
        run(["sudo", "useradd", "--system", "--user-group", "--no-create-home", "--shell", "/usr/sbin/nologin", user])
        run(["sudo", "chown", user + ":" + user, state])
        run(["sudo", "chmod", "0750", state])
        units.append(peer)
        run(["sudo", "systemd-run", "--unit=" + peer, "--property=DynamicUser=yes",
             "/usr/bin/sleep", "infinity"])
        before = identity(peer)
        current = root / "current"

        def switch(release):
            temporary = root / "next"
            temporary.symlink_to(release)
            temporary.replace(current)

        def execute(label, command, succeeds):
            unit = "qsys-storage-pilot-" + suffix + "-" + label
            units.append(unit)
            result = run(["sudo", "systemd-run", "--unit=" + unit, "--wait", "--collect", "--pipe",
                          "--property=Type=exec", "--property=User=" + user,
                          "--property=Group=" + user, "--property=WorkingDirectory=/",
                          "--property=NoNewPrivileges=yes", "--property=ProtectSystem=strict",
                          "--property=ProtectHome=yes", "--property=PrivateTmp=yes",
                          "--property=ReadOnlyPaths=" + str(fixture),
                          "--property=ReadWritePaths=" + str(state),
                          "--property=MemoryMax=512M", "--property=CPUQuota=50%",
                          "--property=RuntimeMaxSec=120", *command], check=False)
            (evidence_dir / (label + ".log")).write_text(result.stdout + result.stderr)
            if (result.returncode == 0) != succeeds:
                raise ValueError("unexpected phase outcome: " + label)
            if hashlib.sha256(marker.read_bytes()).hexdigest() != original or identity(peer) != before:
                raise ValueError("input or peer identity changed")
            phase = {"name": label, "returncode": result.returncode,
                     "current_sha": current.resolve().name, "peer_identity": before}
            evidence["phases"].append(phase)
            return phase

        expected_history = 0

        def report(label, release, fail=False):
            nonlocal expected_history
            switch(release)
            output = state / (label + ".json")
            phase = execute(label, [current / "venv/bin/qsys-storage-report", "--base",
                                    fixture / "missing" if fail else fixture,
                                    "--scratch", fixture / "scratch", "--json", output,
                                    "--history-root", state / "history"], not fail)
            if fail:
                if output.exists():
                    raise ValueError("failed candidate wrote a successful report")
            else:
                raw_report = run(["sudo", "cat", output]).stdout
                (evidence_dir / (label + "-report.json")).write_text(raw_report)
                payload = json.loads(raw_report)
                if payload["schema_version"] != 1 or payload["base"] != str(fixture):
                    raise ValueError("invalid component report")
                usage = {r["category"]: r for r in payload["categories"]}
                if usage["操作回滚副本"]["unique_inodes"] != 1:
                    raise ValueError("hardlink deduplication failed")
                phase["report_sha256"] = hashlib.sha256(raw_report.encode()).hexdigest()
                phase["history_files"] = int(run(["sudo", "find", state / "history", "-name", "*.json"]).stdout.count(".json"))
                expected_history += 1
                if phase["history_files"] != expected_history:
                    raise ValueError("history continuity failed")

        report("baseline", baseline)
        report("upgrade", target)
        report("rollback", baseline)
        report("candidate-failure", target, fail=True)
        report("failure-recovery", baseline)
        execute("input-write-denied", ["/usr/bin/touch", fixture / "blocked"], False)
        execute("release-write-denied", ["/usr/bin/touch", baseline / "blocked"], False)
        if (fixture / "blocked").exists() or (baseline / "blocked").exists():
            raise ValueError("read-only boundary broken")
        evidence["final_sha"] = current.resolve().name
        evidence["peer_identity"] = before
        evidence["status"] = "success"
    except Exception as exc:
        evidence["status"] = "failure"
        evidence["error_type"] = type(exc).__name__
        raise
    finally:
        for unit in reversed(units):
            run(["sudo", "systemctl", "stop", unit], check=False)
        (evidence_dir / "record.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
