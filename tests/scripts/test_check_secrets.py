from pathlib import Path

import pytest

from scripts.check_secrets import forbidden_account_ids, scan

# Built at runtime so this file never contains an account-shaped literal that
# the scanner itself would flag; none of these are real accounts.
SAMPLE = "42" * 4 + "4"          # 9 digits
SAMPLE_10 = SAMPLE + "2"
OTHER_10 = SAMPLE + "3"
REAL = SAMPLE.encode("ascii")


def findings(text, forbidden=()):
    return scan(Path("f.md"), text.encode("utf-8"), list(forbidden))


@pytest.mark.parametrize("template", [
    'allowed_account_ids=("{v}",)',
    "ACCOUNT_ID = '{v}'",
    "资金账号 {v}（股票）",
    "账户：{v}",
    "accid={v}",
])
def test_account_context_flags_real_looking_ids_without_echoing(template):
    result = findings(template.format(v=SAMPLE))
    assert result and "possible account id" in result[0]
    assert SAMPLE[:6] not in " ".join(result)


@pytest.mark.parametrize("text", [
    'allowed_account_ids=("100000001",)',
    'policy.assert_account("123456789")',
    "account_id = '999999999'",
    "run 0123456789abcdef0123456789abcdef",
    "order 20261108 shares 100",
    "账户姿态在 2026-11-08 恢复",
])
def test_fake_ids_and_unrelated_numbers_pass(text):
    assert findings(text) == []


def test_exact_list_matches_anywhere_without_echoing():
    result = findings(f"截图文件名 snapshot-{SAMPLE}.png 与 {SAMPLE}9", [REAL])
    assert len(result) == 1 and "known real account id" in result[0]
    assert SAMPLE not in result[0]


def test_forbidden_ids_from_env_and_file(tmp_path):
    path = tmp_path / "ids"
    path.write_text(SAMPLE + "\n\n", encoding="ascii")
    env = {"QSYS_FORBIDDEN_ACCOUNT_IDS": OTHER_10 + ", ", "QSYS_FORBIDDEN_ACCOUNT_IDS_FILE": str(path)}
    assert forbidden_account_ids(env) == [OTHER_10.encode(), REAL]
    assert forbidden_account_ids({"QSYS_FORBIDDEN_ACCOUNT_IDS_FILE": str(tmp_path / "absent")}) == []
    with pytest.raises(ValueError, match="digits only"):
        forbidden_account_ids({"QSYS_FORBIDDEN_ACCOUNT_IDS": "acct-1"})


def test_existing_credential_patterns_still_fire():
    header = "-----BEGIN RSA " + "PRIVATE KEY-----"
    assert "possible private key" in findings(header)[0]


def test_repository_tree_is_clean_under_context_rule():
    """The checked-in tree itself must pass the context rule CI runs."""
    import subprocess
    files = subprocess.check_output(["git", "ls-files", "-z"]).split(b"\0")
    dirty = []
    for raw in filter(None, files):
        path = Path(raw.decode())
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" not in data[:8192]:
            dirty += [f for f in scan(path, data, []) if "account id" in f]
    assert dirty == []
