import subprocess
import sys
from datetime import date

import pytest

from scripts.backfill_first_board_inputs import _batches


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


def test_first_board_backfill_batches_complete_sessions():
    days = [date(2026, 9, day) for day in range(1, 6)]
    assert list(_batches(days, 2)) == [days[:2], days[2:4], days[4:]]
    with pytest.raises(ValueError, match="positive"):
        list(_batches(days, 0))
