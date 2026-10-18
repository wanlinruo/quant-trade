"""CI 工作流里不许把 `${{ }}` 直接插进 shell。

2026-10-01：一个标题里带双引号的 PR 就把 policy 检查打断了——
`--title "${{ github.event.pull_request.title }}"` 展开后引号提前闭合。
坏掉只是表象，真正的问题是**标题是外部输入**：`"; curl …` 这样的标题
能在 CI runner 上执行任意命令。一律经 `env:` 传，用 "$VAR" 引用。
"""

import re
from pathlib import Path

WORKFLOWS = Path(".github/workflows")
INTERPOLATION = re.compile(r"\$\{\{")
STEP_START = re.compile(r"^\s*-\s+(name|uses):")
RUN_START = re.compile(r"^\s*run:")
CHECKOUT_REF = re.compile(r"actions/checkout@([^\s]+)")
CHECKOUT_V7_0_1 = "3d3c42e5aac5ba805825da76410c181273ba90b1"
UPLOAD_REF = re.compile(r"actions/upload-artifact@([^\s]+)")
UPLOAD_V7_0_2 = "cf430e030ddbb5b0abf93d22962f4752f3646cd9"
PRIVILEGED_WORKFLOWS = {
    "align-test-after-main.yml",
    "deploy-production-component.yml",
    "deploy-production-linux.yml",
    "deploy-production-windows.yml",
    "deploy-test.yml",
    "direct-push-audit.yml",
    "drill-test-research-runner.yml",
    "stage-test-research-runner.yml",
}


def _shell_interpolations(text: str) -> list[str]:
    """`run:` 块内出现的 `${{ }}` 行。`env:` 里的不算——那是安全的用法。"""
    out, in_run, run_indent = [], False, None
    for line in text.splitlines():
        indent = len(line) - len(line.lstrip())
        if in_run and line.strip() and run_indent is not None and indent <= run_indent:
            in_run = False
            run_indent = None
        if RUN_START.search(line):
            in_run = True
            run_indent = indent
        elif STEP_START.match(line):
            in_run = False
            run_indent = None
        if in_run and INTERPOLATION.search(line):
            out.append(line.strip())
    return out


def test_no_workflow_interpolates_into_a_shell_command():
    offenders = {
        path.name: found
        for path in sorted(WORKFLOWS.glob("*.yml"))
        if (found := _shell_interpolations(path.read_text(encoding="utf-8")))
    }

    assert offenders == {}


def test_the_detector_would_have_caught_the_original():
    original = (
        "      - name: Validate\n"
        "        run: >-\n"
        "          python scripts/check_repo_policy.py\n"
        '          --title "${{ github.event.pull_request.title }}"\n'
    )

    assert _shell_interpolations(original)


def test_passing_the_same_value_through_env_is_fine():
    fixed = (
        "      - name: Validate\n"
        "        env:\n"
        "          PR_TITLE: ${{ github.event.pull_request.title }}\n"
        "        run: >-\n"
        '          python scripts/check_repo_policy.py --title "$PR_TITLE"\n'
    )

    assert _shell_interpolations(fixed) == []


def test_every_checkout_uses_the_pinned_node24_release():
    refs = [
        ref
        for path in sorted(WORKFLOWS.glob("*.yml"))
        for ref in CHECKOUT_REF.findall(path.read_text(encoding="utf-8"))
    ]

    assert refs
    assert set(refs) == {CHECKOUT_V7_0_1}


def test_artifact_upload_uses_the_pinned_node24_release():
    refs = [
        ref
        for path in sorted(WORKFLOWS.glob("*.yml"))
        for ref in UPLOAD_REF.findall(path.read_text(encoding="utf-8"))
    ]

    assert refs
    assert set(refs) == {UPLOAD_V7_0_2}


def test_ci_routes_only_trusted_events_to_the_configured_runner():
    workflow = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")

    assert workflow.count("vars.QSYS_CI_RUNNER_LABELS") == 10
    assert workflow.count(
        "github.event.pull_request.head.repo.full_name == github.repository"
    ) == 5
    assert workflow.count("github.event_name != 'pull_request'") == 36
    assert "workflow_dispatch:" in workflow
    assert workflow.count("'\"ubuntu-24.04\"'") == 5
    assert "permissions:\n  actions: read\n  checks: read\n  contents: read" in workflow


def test_privileged_workflows_never_share_the_qsys_ci_runner():
    for name in PRIVILEGED_WORKFLOWS:
        workflow = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert "QSYS_CI_RUNNER_LABELS" not in workflow
        assert "runs-on: ubuntu-24.04" in workflow
