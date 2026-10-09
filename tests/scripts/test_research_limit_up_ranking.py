from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def test_research_script_imports_without_running() -> None:
    path = Path("scripts/research_limit_up_ranking.py")
    spec = importlib.util.spec_from_file_location("research_limit_up_ranking", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.main)
    assert module._source_sha(None, "a" * 40) == "a" * 40
    assert module._source_sha("b" * 40, "a" * 40) == "b" * 40
    with pytest.raises(ValueError, match="full lowercase Git SHA"):
        module._source_sha("deadbeef", "a" * 40)
