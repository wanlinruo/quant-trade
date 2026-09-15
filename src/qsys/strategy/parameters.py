"""策略参数声明、静态读取与运行时校验。"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


_TYPES = {"int": int, "float": float, "bool": bool, "str": str}


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    name: str
    type: str
    default: object
    label: str = ""
    description: str = ""
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[object, ...] = ()


def parameter_specs(source: str) -> tuple[ParameterSpec, ...]:
    """从源码静态提取模块级 ``PARAMETERS``，绝不执行策略。"""
    tree = ast.parse(source)
    raw: object = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            names = [node.target.id] if isinstance(node.target, ast.Name) else []
            value = node.value
        else:
            continue
        if "PARAMETERS" in names:
            raw = ast.literal_eval(value)
            break
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError("PARAMETERS 必须是字典字面量")

    out: list[ParameterSpec] = []
    for name, config in raw.items():
        if not isinstance(name, str) or not name.isidentifier():
            raise ValueError(f"参数名必须是合法标识符: {name!r}")
        if not isinstance(config, dict):
            raise ValueError(f"参数 {name} 的配置必须是字典")
        kind = config.get("type")
        if kind not in _TYPES:
            raise ValueError(f"参数 {name} 的 type 必须是 int/float/bool/str")
        if "default" not in config:
            raise ValueError(f"参数 {name} 缺少 default")
        choices = config.get("choices", ())
        if not isinstance(choices, (list, tuple)):
            raise ValueError(f"参数 {name} 的 choices 必须是列表")
        spec = ParameterSpec(
            name=name, type=kind, default=config["default"],
            label=str(config.get("label", name)),
            description=str(config.get("description", "")),
            minimum=config.get("min"), maximum=config.get("max"),
            choices=tuple(choices),
        )
        _validate_one(spec, spec.default)
        out.append(spec)
    return tuple(out)


def parameter_specs_from_file(path: Path) -> tuple[ParameterSpec, ...]:
    return parameter_specs(Path(path).read_text(encoding="utf-8"))


def validate_parameters(
    specs: tuple[ParameterSpec, ...], supplied: Mapping[str, object]
) -> dict[str, object]:
    """合并默认值并转成声明类型；无声明时保留旧版自由键值行为。"""
    if not specs:
        return dict(supplied)
    by_name = {s.name: s for s in specs}
    unknown = sorted(set(supplied) - set(by_name))
    if unknown:
        raise ValueError(f"未知策略参数: {', '.join(unknown)}")
    out: dict[str, object] = {}
    for spec in specs:
        converted = _convert(spec, supplied.get(spec.name, spec.default))
        _validate_one(spec, converted)
        out[spec.name] = converted
    return out


def _convert(spec: ParameterSpec, value: object) -> object:
    if spec.type == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"参数 {spec.name} 不是布尔值")
    try:
        return _TYPES[spec.type](value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"参数 {spec.name} 不是有效的 {spec.type}") from exc


def _validate_one(spec: ParameterSpec, value: object) -> None:
    converted = _convert(spec, value)
    if spec.choices and converted not in spec.choices:
        raise ValueError(f"参数 {spec.name} 必须是 {list(spec.choices)} 之一")
    if spec.minimum is not None and converted < spec.minimum:
        raise ValueError(f"参数 {spec.name} 不能小于 {spec.minimum}")
    if spec.maximum is not None and converted > spec.maximum:
        raise ValueError(f"参数 {spec.name} 不能大于 {spec.maximum}")
