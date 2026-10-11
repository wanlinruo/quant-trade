"""Validate branch, pull-request and commit naming policy.

GitHub Free private repositories cannot make this check required server-side.
The deployment path nevertheless refuses commits whose policy workflow failed.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys


BRANCH = re.compile(
    r"^(feat|fix|docs|chore|refactor)/[a-z0-9][a-z0-9._-]*$"
)
TITLE = re.compile(
    r"^(feat|fix|test|docs|refactor|chore|research)(\([a-z0-9_.-]+\))?!?: .+"
)


def allowed_source(head: str, base: str) -> bool:
    if base == "main":
        return head == "test" or head.startswith("fix/")
    if base == "test":
        return head == "main" or bool(BRANCH.fullmatch(head))
    return False


def commit_subjects(base: str) -> list[str]:
    result = subprocess.run(
        ["git", "log", "--format=%s", f"origin/{base}..HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def validate(head: str, base: str, title: str, subjects: list[str]) -> list[str]:
    errors: list[str] = []
    if not (BRANCH.fullmatch(head) or head in {"main", "test"}):
        errors.append(f"分支名不合规: {head}")
    if base and not allowed_source(head, base):
        errors.append(f"不允许的 PR 路径: {head} -> {base}")
    if title and not TITLE.fullmatch(title):
        errors.append(f"PR 标题不符合 Conventional Commits: {title}")
    for subject in subjects:
        if not TITLE.fullmatch(subject):
            errors.append(f"提交标题不符合 Conventional Commits: {subject}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--head", required=True)
    parser.add_argument("--base", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--subject", action="append", default=[])
    parser.add_argument("--check-commits", action="store_true")
    args = parser.parse_args()
    subjects = list(args.subject)
    if args.check_commits and args.base:
        subjects.extend(commit_subjects(args.base))
    errors = validate(args.head, args.base, args.title, subjects)
    if errors:
        print("\n".join(f"ERROR: {item}" for item in errors), file=sys.stderr)
        return 1
    print(f"policy ok: {args.head}" + (f" -> {args.base}" if args.base else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
