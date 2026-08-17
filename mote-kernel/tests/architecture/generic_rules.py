"""Syntax-only checks for explicit type erasure in production annotations.

Variance, TypeVar flow, and cross-universe assignability require a type checker
and are owned by ``test_graph_typing_fixtures.py`` rather than this AST lint.
"""

import ast
from dataclasses import dataclass
from pathlib import Path

BARE_GENERIC_NAMES = frozenset(
    {
        "AsyncIterable",
        "AsyncIterator",
        "Awaitable",
        "Callable",
        "Collection",
        "Container",
        "Coroutine",
        "Generator",
        "Generic",
        "Iterable",
        "Iterator",
        "Mapping",
        "MutableMapping",
        "MutableSequence",
        "Protocol",
        "Sequence",
        "Set",
        "TypeGuard",
        "asyncio.Future",
        "dict",
        "frozenset",
        "list",
        "set",
        "tuple",
        "type",
    }
)


@dataclass(frozen=True, slots=True)
class TypeErasureViolation:
    line: int
    message: str


def _qualified_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _qualified_name(node.value)
        if owner is not None:
            return f"{owner}.{node.attr}"
    return None


def _annotations(tree: ast.Module) -> list[ast.expr]:
    annotations = [
        node.annotation
        for node in ast.walk(tree)
        if isinstance(node, ast.arg | ast.AnnAssign) and node.annotation is not None
    ]
    annotations.extend(
        node.returns
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.returns is not None
    )
    return annotations


def _annotation_violations(annotation: ast.expr) -> list[TypeErasureViolation]:
    violations: list[TypeErasureViolation] = []

    def visit(node: ast.expr, *, parameterized_owner: bool = False) -> None:
        if isinstance(node, ast.Subscript):
            visit(node.value, parameterized_owner=True)
            visit(node.slice)
            return
        if isinstance(node, ast.Tuple):
            for element in node.elts:
                visit(element)
            return
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            visit(node.left)
            visit(node.right)
            return
        if isinstance(node, ast.Starred):
            visit(node.value)
            return
        if isinstance(node, ast.Name | ast.Attribute):
            name = _qualified_name(node)
            if name == "object":
                violations.append(TypeErasureViolation(node.lineno, "object erases the boundary type"))
            elif name in BARE_GENERIC_NAMES and not parameterized_owner:
                violations.append(TypeErasureViolation(node.lineno, f"bare generic annotation {name}"))

    visit(annotation)
    return violations


def type_erasure_violations(
    source: str,
    *,
    filename: str = "<unknown>",
) -> tuple[TypeErasureViolation, ...]:
    tree = ast.parse(source, filename=filename)
    violations: list[TypeErasureViolation] = []

    for annotation in _annotations(tree):
        violations.extend(_annotation_violations(annotation))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = _qualified_name(node.func)
        if call_name not in {"cast", "typing.cast"} or not node.args:
            continue
        target_name = _qualified_name(node.args[0])
        if target_name in {"Any", "object", "typing.Any"}:
            violations.append(TypeErasureViolation(node.lineno, f"{call_name} cannot restore an erased generic type"))

    return tuple(violations)


def production_type_erasure_violations(package_root: Path) -> tuple[str, ...]:
    violations: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
        relative_path = path.relative_to(package_root).as_posix()
        source = path.read_text(encoding="utf-8")
        violations.extend(
            f"{relative_path}:{violation.line} {violation.message}"
            for violation in type_erasure_violations(source, filename=str(path))
        )
    return tuple(violations)
