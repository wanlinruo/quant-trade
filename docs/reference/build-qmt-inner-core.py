"""#533 本地生成器：提取冻结纯计算函数，不在 QMT 或生产节点执行。"""

from __future__ import annotations

import argparse
import ast
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs/reference/qmt-inner-decision-core.py"
SOURCES = ("params.py", "gate.py", "allocation.py", "exits.py", "entry.py", "selection.py")
HISTORY_INPUT = "qmt-inner-history-input.py"
# The adjustment helper is a handwritten translation of the Polars feed, not an
# AST extraction. Bind its reference as well as the portable implementation.
REFERENCE_SOURCES = ("src/qsys/data/feed.py",)
GATE_FUNCTIONS = frozenset({
    "three_or", "three_and", "_num", "classify", "_classify_low_main",
    "_classify_high_main", "_classify_high_chinext", "gate_allow",
})


def without_docstring(body):
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        return body[1:]
    return body


class PortableSyntax(ast.NodeTransformer):
    def visit_FunctionDef(self, node):
        self.generic_visit(node)
        node.returns = None
        node.type_comment = None
        node.body = without_docstring(node.body)
        return node

    def visit_arg(self, node):
        node.annotation = None
        node.type_comment = None
        return node

    def visit_AnnAssign(self, node):
        if node.value is None:
            raise ValueError("uninitialised annotation outside a record")
        return ast.copy_location(ast.Assign(targets=[node.target], value=node.value), node)

    def visit_ClassDef(self, node):
        # The selected records are frozen dataclasses containing only fields.
        # namedtuple keeps their immutable field access without dataclasses.
        if node.bases or node.keywords or len(node.decorator_list) != 1:
            raise ValueError("unsupported record class")
        decorator = node.decorator_list[0]
        if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Name) \
                or decorator.func.id != "dataclass" or decorator.args \
                or {k.arg: ast.literal_eval(k.value) for k in decorator.keywords} \
                != {"frozen": True, "slots": True}:
            raise ValueError("only frozen slot dataclasses can be converted")
        fields, defaults = [], []
        for item in without_docstring(node.body):
            if not isinstance(item, ast.AnnAssign) or not isinstance(item.target, ast.Name):
                raise ValueError("record contains executable behaviour")
            fields.append(item.target.id)
            if item.value is not None:
                defaults.append(ast.literal_eval(item.value))
            elif defaults:
                raise ValueError("required field follows a default")
        source = "{0} = _namedtuple({0!r}, {1!r})\n".format(node.name, fields)
        if defaults:
            source += "{}.__new__.__defaults__ = {!r}\n".format(node.name, tuple(defaults))
        return ast.parse(source).body


def build_core(root: Path = ROOT) -> str:
    source_dir = root / "src/qsys/strategy/structural"
    hashes = {}
    selected = []
    for filename in SOURCES:
        data = (source_dir / filename).read_bytes()
        hashes[filename] = hashlib.sha256(data).hexdigest()
        tree = ast.parse(data)
        for node in tree.body:
            if filename == "gate.py":
                if not (isinstance(node, ast.FunctionDef) and node.name in GATE_FUNCTIONS
                        or isinstance(node, ast.ClassDef) and node.name == "Classification"):
                    continue
            elif filename == "selection.py":
                if not (isinstance(node, ast.FunctionDef) and node.name == "_stock_factors"):
                    continue
            elif isinstance(node, (ast.Import, ast.ImportFrom, ast.Expr)):
                continue
            if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.FunctionDef, ast.ClassDef)):
                raise ValueError("unsupported top-level source construct")
            selected.append(node)
    history_input = (root / "docs/reference" / HISTORY_INPUT).read_bytes()
    hashes[HISTORY_INPUT] = hashlib.sha256(history_input).hexdigest()
    for path in REFERENCE_SOURCES:
        hashes[path] = hashlib.sha256((root / path).read_bytes()).hexdigest()
    module = ast.Module(body=selected, type_ignores=[])
    module = PortableSyntax().visit(module)
    ast.fix_missing_locations(module)
    header = (
        "# coding: utf-8\n"
        '"""#533 GENERATED calculation specimen; NOT an executable QMT strategy.\n'
        "No market/account access, scheduling, state recovery or order submission.\n"
        "ORDER_SUBMITTED is the original allocation result label, not a broker event.\n"
        "Regenerate locally with docs/reference/build-qmt-inner-core.py.\n"
        '"""\n\n'
        "from collections import namedtuple as _namedtuple\n\n"
        "SOURCE_SHA256 = {!r}\n\n".format(hashes)
    )
    # Keep the handwritten input container separate from the transformed
    # production functions; its methods operate only on explicit finite data.
    output = header + history_input.decode("utf-8") + "\n" + ast.unparse(module) + "\n"
    ast.parse(output, feature_version=(3, 6))
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="检查生成物，不写文件")
    args = parser.parse_args(argv)
    output = build_core()
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != output:
            parser.exit(1, "生成物与源代码不一致，请重新生成并做差分验证\n")
    else:
        OUTPUT.write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
