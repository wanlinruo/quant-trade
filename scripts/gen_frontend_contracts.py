"""由后端契约（pydantic 模型）生成前端 TypeScript 类型（#662，总路线 v2）。

用法：uv run python scripts/gen_frontend_contracts.py
输出：frontend/src/api/contracts.gen.ts（提交进仓库；测试校验与契约一致）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic.json_schema import models_json_schema

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "frontend" / "src" / "api" / "contracts.gen.ts"
HEADER = "// 由 scripts/gen_frontend_contracts.py 生成，勿手改。契约来源：src/qsys/webapp/contracts/\n"


def _ts(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if "const" in schema:
        return json.dumps(schema["const"], ensure_ascii=False)
    if "enum" in schema:
        return " | ".join(json.dumps(v, ensure_ascii=False) for v in schema["enum"])
    for key in ("anyOf", "oneOf"):
        if key in schema:
            parts: list[str] = []
            for sub in schema[key]:
                t = _ts(sub)
                if t not in parts:
                    parts.append(t)
            return " | ".join(parts)
    kind = schema.get("type")
    if kind == "string":
        return "string"
    if kind in ("integer", "number"):
        return "number"
    if kind == "boolean":
        return "boolean"
    if kind == "null":
        return "null"
    if kind == "array":
        if "prefixItems" in schema:
            return "[" + ", ".join(_ts(s) for s in schema["prefixItems"]) + "]"
        inner = _ts(schema.get("items", {}))
        return f"({inner})[]" if " | " in inner else f"{inner}[]"
    if kind == "object":
        if "properties" in schema:
            return _inline_object(schema)
        extra = schema.get("additionalProperties")
        return f"Record<string, {_ts(extra) if isinstance(extra, dict) else 'unknown'}>"
    return "unknown"


def _fields(schema: dict[str, Any], indent: str) -> list[str]:
    required = set(schema.get("required", ()))
    return [f"{indent}{name}{'' if name in required else '?'}: {_ts(prop)}"
            for name, prop in schema.get("properties", {}).items()]


def _inline_object(schema: dict[str, Any]) -> str:
    return "{ " + "; ".join(f.strip() for f in _fields(schema, "")) + " }"


def render(models) -> str:
    out = [HEADER]
    if not models:
        return "".join(out)
    _, top = models_json_schema([(m, "serialization") for m in models],
                                ref_template="#/$defs/{model}")
    defs: dict[str, Any] = top.get("$defs", {})
    for name in sorted(defs):
        schema = defs[name]
        if schema.get("type") == "object" and "properties" in schema:
            body = "\n".join(_fields(schema, "  "))
            out.append(f"\nexport interface {name} {{\n{body}\n}}\n")
        else:
            out.append(f"\nexport type {name} = {_ts(schema)}\n")
    return "".join(out)


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    from qsys.webapp.contracts.registry import EXPORTED

    OUTPUT.write_text(render(EXPORTED), encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({len(EXPORTED)} contracts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
