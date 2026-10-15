#!/usr/bin/env python3
"""Generate one JIT config, then run one rootless, disposable container."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import runner_gate


API_VERSION = "2022-11-28"
IMAGE_ID = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")


def github_json(url: str, token: str, *, body: dict[str, object] | None = None) -> dict[str, object]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        url,
        data=data,
        method="GET" if body is None else "POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "qsys-ci-runner-controller",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"GitHub API returned HTTP {error.code}") from None


def generate_jit_config(repository: str, group: int, token: str, name: str) -> str:
    base = f"https://api.github.com/repos/{repository}"
    repo = github_json(base, token)
    if repo.get("private") is not True:
        raise RuntimeError("repository must be private before self-hosted CI is enabled")
    response = github_json(
        f"{base}/actions/runners/generate-jitconfig",
        token,
        body={
            "name": name,
            "runner_group_id": group,
            "labels": ["self-hosted", "linux", "x64", "qsys-ci"],
            "work_folder": "_work",
        },
    )
    encoded = response.get("encoded_jit_config")
    if not isinstance(encoded, str) or not encoded or any(char.isspace() for char in encoded):
        raise RuntimeError("GitHub API did not return a valid JIT configuration")
    return encoded


def podman_command(*, uid: int, gid: int, state: Path, image_id: str, jit_file: Path) -> list[str]:
    runtime = f"/run/user/{uid}"
    return [
        "setpriv", "--reuid", str(uid), "--regid", str(gid), "--init-groups",
        "env", f"HOME={state}", f"XDG_RUNTIME_DIR={runtime}",
        "podman", "run", "--rm", "--name", "qsys-ci-jit",
        "--userns=keep-id:uid=1001,gid=1001",
        "--cap-drop=all", "--security-opt=no-new-privileges",
        "--network=slirp4netns:allow_host_loopback=false",
        "--memory=2300m", "--memory-swap=2800m", "--cpus=1.5", "--pids-limit=512",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=512m",
        "--volume", f"{jit_file}:/run/qsys-ci/jit-config:ro",
        image_id,
    ]


def main() -> int:
    enable_file = Path(os.environ["QSYS_CI_ENABLE_FILE"])
    state = Path(os.environ["QSYS_CI_STATE_DIR"])
    gate = runner_gate.audit_host(enable_file, state)
    print(json.dumps({"admission": gate}, sort_keys=True), flush=True)
    if not gate["ok"]:
        return 75

    token_file = Path(os.environ["QSYS_CI_TOKEN_FILE"])
    if token_file.stat().st_mode & 0o077:
        raise RuntimeError("GitHub token file must not be accessible to group or other")
    token = token_file.read_text().strip()
    if not token:
        raise RuntimeError("GitHub token is empty")
    image_id = Path(os.environ["QSYS_CI_IMAGE_ID_FILE"]).read_text().strip()
    if not IMAGE_ID.fullmatch(image_id):
        raise RuntimeError("pinned container image ID is invalid")

    uid = int(os.environ["QSYS_CI_RUNNER_UID"])
    gid = int(os.environ["QSYS_CI_RUNNER_GID"])
    repository = os.environ["QSYS_CI_REPOSITORY"]
    name = f"qsys-ci-{os.uname().nodename}-{os.getpid()}"
    jit_config = generate_jit_config(
        repository, int(os.environ["QSYS_CI_RUNNER_GROUP"]), token, name
    )
    del token

    jit_dir = state / "jit"
    with tempfile.NamedTemporaryFile(mode="w", dir=jit_dir, delete=False) as secret:
        secret.write(jit_config)
        secret.flush()
        jit_path = Path(secret.name)
    del jit_config
    os.chown(jit_path, uid, gid)
    os.chmod(jit_path, 0o400)
    try:
        return subprocess.run(
            podman_command(
                uid=uid, gid=gid, state=state, image_id=image_id, jit_file=jit_path
            ),
            check=False,
        ).returncode
    finally:
        jit_path.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"qsys CI controller refused to start: {error}", file=sys.stderr)
        raise SystemExit(1) from None
