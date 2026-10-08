#!/usr/bin/env python3
"""Build and statically validate release evidence bound to a Git tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any


SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
EVIDENCE_KIND = "qsys-test-release-evidence"
EVIDENCE_SCHEMA_VERSION = 2
CI_WORKFLOW = ".github/workflows/ci.yml"
DEPLOY_WORKFLOW = ".github/workflows/deploy-test.yml"


def _git(repo: Path, *args: str, text: bool = True) -> str | bytes:
    return subprocess.check_output(["git", *args], cwd=repo, text=text)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _blob(repo: Path, sha: str, path: str) -> bytes:
    return _git(repo, "show", f"{sha}:{path}", text=False)  # type: ignore[return-value]


def _require_sha(value: str, name: str) -> None:
    if not SHA_PATTERN.fullmatch(value):
        raise ValueError(f"{name} must be a full lowercase commit SHA")


def build_evidence(
    repo: Path, *, source_sha: str, controller_sha: str, ci_run_id: int,
    ci_check_suite_id: int, node_record_run_id: str,
) -> dict[str, Any]:
    _require_sha(source_sha, "source_sha")
    _require_sha(controller_sha, "controller_sha")
    tree = str(_git(repo, "rev-parse", f"{source_sha}^{{tree}}")).strip()
    evidence = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "kind": EVIDENCE_KIND,
        "source_sha": source_sha,
        "tree_sha": tree,
        "lockfile_sha256": _sha256(_blob(repo, source_sha, "uv.lock")),
        "source_ci_workflow_sha256": _sha256(_blob(repo, source_sha, CI_WORKFLOW)),
        "source_deploy_workflow_sha256": _sha256(
            _blob(repo, source_sha, DEPLOY_WORKFLOW)
        ),
        "controller_sha": controller_sha,
        "controller_ci_workflow_sha256": _sha256(
            _blob(repo, controller_sha, CI_WORKFLOW)
        ),
        "controller_deploy_workflow_sha256": _sha256(
            _blob(repo, controller_sha, DEPLOY_WORKFLOW)
        ),
        "ci_run_id": ci_run_id,
        "ci_check_suite_id": ci_check_suite_id,
        "node_record_run_id": node_record_run_id,
    }
    validate_evidence(
        evidence, repo=repo, source_sha=source_sha, tree_sha=tree,
    )
    return evidence


def validate_evidence(
    evidence: dict[str, Any], *, repo: Path, source_sha: str, tree_sha: str,
    require_controller_match: bool = False,
) -> None:
    expected_keys = {
        "schema_version", "kind", "source_sha", "tree_sha", "lockfile_sha256",
        "source_ci_workflow_sha256", "source_deploy_workflow_sha256",
        "controller_sha", "controller_ci_workflow_sha256",
        "controller_deploy_workflow_sha256", "ci_run_id", "ci_check_suite_id",
        "node_record_run_id",
    }
    if set(evidence) != expected_keys:
        raise ValueError(
            f"release evidence fields do not match schema version {EVIDENCE_SCHEMA_VERSION}"
        )
    if evidence["schema_version"] != EVIDENCE_SCHEMA_VERSION:
        raise ValueError("unsupported release evidence schema")
    if evidence["kind"] != EVIDENCE_KIND:
        raise ValueError("unexpected release evidence kind")
    _require_sha(source_sha, "source_sha")
    _require_sha(tree_sha, "tree_sha")
    controller_sha = str(evidence["controller_sha"])
    _require_sha(controller_sha, "controller_sha")
    if evidence["source_sha"] != source_sha or evidence["tree_sha"] != tree_sha:
        raise ValueError("release evidence source or tree mismatch")
    for key in ("ci_run_id", "ci_check_suite_id"):
        if not isinstance(evidence[key], int) or evidence[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")
    run_id = evidence["node_record_run_id"]
    if not isinstance(run_id, str) or not re.fullmatch(r"github-[0-9]+-[0-9]+", run_id):
        raise ValueError("node_record_run_id is invalid")
    actual_tree = str(_git(repo, "rev-parse", f"{source_sha}^{{tree}}")).strip()
    if actual_tree != tree_sha:
        raise ValueError("Git tree does not match release evidence")
    hashes = {
        "lockfile_sha256": _sha256(_blob(repo, source_sha, "uv.lock")),
        "source_ci_workflow_sha256": _sha256(_blob(repo, source_sha, CI_WORKFLOW)),
        "source_deploy_workflow_sha256": _sha256(
            _blob(repo, source_sha, DEPLOY_WORKFLOW)
        ),
        "controller_ci_workflow_sha256": _sha256(
            _blob(repo, controller_sha, CI_WORKFLOW)
        ),
        "controller_deploy_workflow_sha256": _sha256(
            _blob(repo, controller_sha, DEPLOY_WORKFLOW)
        ),
    }
    for key, value in hashes.items():
        if evidence[key] != value:
            raise ValueError(f"release evidence {key} mismatch")
    if (
        require_controller_match
        and (
            evidence["source_ci_workflow_sha256"]
            != evidence["controller_ci_workflow_sha256"]
            or evidence["source_deploy_workflow_sha256"]
            != evidence["controller_deploy_workflow_sha256"]
        )
    ):
        raise ValueError("validation controller differs from source workflows")


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-deployment")
    create.add_argument("--repo", type=Path, default=Path("."))
    create.add_argument("--source-sha", required=True)
    create.add_argument("--controller-sha", required=True)
    create.add_argument("--ci-run-id", required=True, type=int)
    create.add_argument("--ci-check-suite-id", required=True, type=int)
    create.add_argument("--node-record-run-id", required=True)
    create.add_argument("--description", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--repo", type=Path, default=Path("."))
    validate.add_argument("--source-sha", required=True)
    validate.add_argument("--tree-sha", required=True)
    validate.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    if args.command == "create-deployment":
        evidence = build_evidence(
            repo, source_sha=args.source_sha, controller_sha=args.controller_sha,
            ci_run_id=args.ci_run_id, ci_check_suite_id=args.ci_check_suite_id,
            node_record_run_id=args.node_record_run_id,
        )
        print(json.dumps({
            "ref": args.source_sha,
            "environment": "testing",
            "auto_merge": False,
            "required_contexts": [],
            "description": args.description,
            "payload": evidence,
        }, ensure_ascii=False))
        return 0
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    validate_evidence(
        evidence, repo=repo, source_sha=args.source_sha, tree_sha=args.tree_sha,
        require_controller_match=True,
    )
    print(json.dumps({"ok": True, "tree_sha": args.tree_sha}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
