#!/usr/bin/env python3
"""Verify reusable test evidence against GitHub's authoritative records."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    from scripts.release_evidence import (
        EVIDENCE_KIND, EVIDENCE_SCHEMA_VERSION, validate_evidence,
    )
except ModuleNotFoundError:  # Direct execution sets sys.path[0] to scripts/.
    from release_evidence import (  # type: ignore[no-redef]
        EVIDENCE_KIND, EVIDENCE_SCHEMA_VERSION, validate_evidence,
    )


CHECK_NAMES = {"policy", "secret-scan", "test", "package"}


def _api(repo: str, path: str, token: str) -> Any:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _successful_checks(checks: dict[str, Any]) -> bool:
    selected = [item for item in checks.get("check_runs", []) if item.get("name") in CHECK_NAMES]
    return (
        len(selected) == len(CHECK_NAMES)
        and {item["name"] for item in selected} == CHECK_NAMES
        and all(item.get("conclusion") == "success" for item in selected)
    )


def verify(
    *, repo: str, source_sha: str, tree_sha: str, checkout: Path, token: str,
) -> dict[str, Any]:
    query = urllib.parse.urlencode({
        "sha": source_sha, "environment": "testing", "per_page": 100,
    })
    deployments = _api(repo, f"deployments?{query}", token)
    candidates = [
        item for item in deployments
        if item.get("sha") == source_sha
        and item.get("environment") == "testing"
        and isinstance(item.get("payload"), dict)
        and item["payload"].get("kind") == EVIDENCE_KIND
        and item["payload"].get("schema_version") == EVIDENCE_SCHEMA_VERSION
    ]
    if not candidates:
        raise ValueError("no reusable test deployment evidence")
    deployment = max(candidates, key=lambda item: int(item["id"]))
    evidence = deployment["payload"]
    validate_evidence(
        evidence, repo=checkout, source_sha=source_sha, tree_sha=tree_sha,
        require_controller_match=True,
    )
    statuses = _api(repo, f"deployments/{deployment['id']}/statuses?per_page=100", token)
    if not statuses or max(statuses, key=lambda item: int(item["id"])).get("state") != "success":
        raise ValueError("test deployment evidence is not successful")
    ci_run = _api(repo, f"actions/runs/{evidence['ci_run_id']}", token)
    if not (
        ci_run.get("event") == "push"
        and ci_run.get("head_branch") == "test"
        and ci_run.get("head_sha") == source_sha
        and ci_run.get("status") == "completed"
        and ci_run.get("conclusion") == "success"
        and ci_run.get("check_suite_id") == evidence["ci_check_suite_id"]
    ):
        raise ValueError("evidence CI run does not match the source")
    checks = _api(
        repo,
        f"check-suites/{evidence['ci_check_suite_id']}/check-runs?per_page=100",
        token,
    )
    if not _successful_checks(checks):
        raise ValueError("evidence CI does not contain four successful checks")
    return {
        "ok": True,
        "deployment_id": deployment["id"],
        "ci_run_id": evidence["ci_run_id"],
        "node_record_run_id": evidence["node_record_run_id"],
        "tree_sha": tree_sha,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--tree-sha", required=True)
    parser.add_argument("--checkout", type=Path, default=Path("."))
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("GITHUB_TOKEN is required", file=sys.stderr)
        return 2
    try:
        result = verify(
            repo=args.repo, source_sha=args.source_sha, tree_sha=args.tree_sha,
            checkout=args.checkout.resolve(), token=token,
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"release evidence is not reusable: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
