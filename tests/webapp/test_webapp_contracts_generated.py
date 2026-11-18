"""前端类型由后端契约生成，且与仓库中的生成物一致（#662，总路线 v2）。"""

from __future__ import annotations

import importlib.util
import typing
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi.testclient import TestClient

from qsys.webapp.contracts.base import Contract, Timestamp
from qsys.webapp.contracts.registry import EXPORTED

ROOT = Path(__file__).resolve().parents[2]
GENERATED = ROOT / "frontend" / "src" / "api" / "contracts.gen.ts"


def _gen():
    spec = importlib.util.spec_from_file_location("gen_frontend_contracts", ROOT / "scripts" / "gen_frontend_contracts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Inner(Contract):
    name: str
    n: int | None


class Outer(Contract):
    schema_version: Literal[1]
    state: Literal["a", "b"]
    at: Timestamp
    day: date
    ratio: float | None = None
    items: list[Inner]
    pair: tuple[str, int | None]
    counts: dict[str, int]
    either: Inner | None
    union: Inner | int


def test_render_maps_schema_shapes():
    out = _gen().render((Outer,))
    assert "export interface Inner {\n  name: string\n  n: number | null\n}" in out
    assert "  schema_version: 1\n" in out
    assert '  state: "a" | "b"\n' in out
    assert "  at: string\n" in out and "  day: string\n" in out
    assert "  ratio: number | null\n" in out  # 序列化时总会输出（值为 null）
    assert "  items: Inner[]\n" in out
    assert "  pair: [string, number | null]\n" in out
    assert "  counts: Record<string, number>\n" in out
    assert "  either: Inner | null\n" in out
    assert "  union: Inner | number\n" in out
    assert out.startswith("// 由 scripts/gen_frontend_contracts.py 生成，勿手改")


def test_render_is_deterministic():
    g = _gen()
    assert g.render((Outer,)) == g.render((Outer,))


def test_generated_contracts_are_current():
    expected = _gen().render(EXPORTED)
    actual = GENERATED.read_text(encoding="utf-8") if GENERATED.exists() else ""
    assert actual == expected, "契约已变化：运行 uv run python scripts/gen_frontend_contracts.py 重新生成"


def _members(tp) -> set:
    args = typing.get_args(tp)
    return {a for a in args if isinstance(a, type)} if args else ({tp} if isinstance(tp, type) else set())


def test_every_route_response_model_is_exported(warehouse):
    from qsys.ops.journal import SessionJournal
    from qsys.web.app import create_app
    from qsys.web.config import WebConfig
    from qsys.web.views import MonitorService
    cfg = WebConfig()
    app = TestClient(create_app(MonitorService(SessionJournal(warehouse), config=cfg), cfg)).app
    exported = set(EXPORTED)
    for route in app.routes:
        model = getattr(route, "response_model", None)
        if not str(getattr(route, "path", "")).startswith("/api/app/") or model is None:
            continue
        missing = {m for m in _members(model) if issubclass(m, Contract)} - exported
        assert not missing, f"{route.path} 的响应模型未登记到 EXPORTED：{missing}"


def test_exported_contract_names_are_unique():
    """不同领域的契约重名时 pydantic 会生成带模块路径的长名字，前端无法使用：契约名必须全局唯一。"""
    out = GENERATED.read_text(encoding="utf-8")
    assert "__" not in "".join(line for line in out.splitlines() if line.startswith("export "))
