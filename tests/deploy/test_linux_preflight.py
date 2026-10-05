from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "preflight_linux_deploy.sh"
)


def test_every_uv_command_is_frozen():
    commands = [line.strip() for line in SCRIPT.read_text().splitlines() if '"$uv_bin"' in line]

    assert commands == [
        '"$uv_bin" sync --frozen',
        '"$uv_bin" run --frozen python -c \\',
        '"$uv_bin" run --frozen pytest -q tests/scripts/test_scripts_import.py',
    ]


def test_it_refuses_dirty_input_and_dirty_output():
    text = SCRIPT.read_text()

    assert text.count("git status --porcelain --untracked-files=normal") == 2
    assert "git diff --quiet -- uv.lock" in text
