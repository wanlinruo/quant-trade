from scripts.check_repo_policy import allowed_source, validate


def test_main_accepts_only_test_or_emergency_fix():
    assert allowed_source("test", "main")
    assert allowed_source("fix/live-recovery", "main")
    assert not allowed_source("feat/new-page", "main")


def test_test_accepts_work_branches_and_main_sync():
    assert allowed_source("feat/new-page", "test")
    assert allowed_source("fix/query-null", "test")
    assert allowed_source("docs/governance", "test")
    assert allowed_source("main", "test")


def test_policy_rejects_bad_branch_and_titles():
    errors = validate("feature/New Thing", "test", "add page", ["misc changes"])
    assert len(errors) == 4


def test_policy_accepts_conventional_pr_and_commits():
    assert validate(
        "feat/new-page",
        "test",
        "feat(web): add new page",
        ["feat(web): add route", "test(web): cover route"],
    ) == []
