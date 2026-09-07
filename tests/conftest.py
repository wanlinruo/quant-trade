from pathlib import Path

import pytest

from qsys.data.storage import Warehouse


@pytest.fixture
def warehouse(tmp_path: Path) -> Warehouse:
    return Warehouse(tmp_path / "warehouse")
