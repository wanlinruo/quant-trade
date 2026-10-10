from __future__ import annotations

import importlib.util
from pathlib import Path


def test_shadow_ranking_script_imports_without_running() -> None:
    path = Path("scripts/run_limit_up_shadow.py")
    spec = importlib.util.spec_from_file_location("run_limit_up_shadow", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.main)
