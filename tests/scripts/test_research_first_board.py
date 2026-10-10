import subprocess
import sys


def test_first_board_research_cli_help_imports_cleanly():
    result = subprocess.run(
        [sys.executable, "scripts/research_first_board.py", "--help"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--legacy-ranking" in result.stdout


def test_auction_parity_cli_help_imports_cleanly():
    result = subprocess.run(
        [sys.executable, "scripts/verify_auction_open_parity.py", "--help"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--warehouse" in result.stdout
