"""Fail a main push audit when the commit is unrelated to a merged PR."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request


def associated_pull_requests(repo: str, sha: str, token: str) -> list[dict]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/commits/{sha}/pulls",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--ref", required=True)
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("GITHUB_TOKEN is required", file=sys.stderr)
        return 2
    pulls = associated_pull_requests(args.repo, args.sha, token)
    merged = [item for item in pulls if item.get("merged_at")]
    if not merged:
        print(
            f"DIRECT PUSH DETECTED: {args.ref}@{args.sha} is not associated "
            "with a merged pull request. Deployment must stop.",
            file=sys.stderr,
        )
        return 1
    numbers = ", ".join(f"#{item['number']}" for item in merged)
    print(f"push audit ok: {args.ref}@{args.sha} via {numbers}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
