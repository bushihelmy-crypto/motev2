import ast
from collections.abc import Iterator
from pathlib import Path

from tests.architecture.import_rules import import_placement_violations, production_import_placement_violations

PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "mote_kernel"


def _production_modules() -> Iterator[tuple[Path, ast.Module]]:
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _relative(path: Path) -> str:
    return path.relative_to(PACKAGE_ROOT).as_posix()


def test_imports_form_a_contiguous_module_header() -> None:
    violations = production_import_placement_violations(PACKAGE_ROOT)
    assert not violations, f"imports must form one contiguous module-header block: {violations}"


def test_import_gate_accepts_docstring_future_and_header_imports() -> None:
    source = """\"\"\"Module documentation.\"\"\"
from __future__ import annotations
import ast
from pathlib import Path

VALUE = 1
"""
    assert import_placement_violations(source) == ()


def test_import_gate_rejects_every_non_header_import() -> None:
    source = """
VALUE = 1
import ast

if VALUE:
    import pathlib

def load() -> None:
    from collections.abc import Iterator

class Factory:
    import typing
"""
    violations = import_placement_violations(source)
    assert [(violation.line, violation.message) for violation in violations] == [
        (3, "import appears after executable declarations"),
        (6, "import is not module scoped"),
        (9, "import is not module scoped"),
        (12, "import is not module scoped"),
    ]


def test_dynamic_import_and_reflection_escape_hatches_are_forbidden() -> None:
    forbidden_calls = frozenset({"__import__", "getattr", "hasattr", "setattr"})
    violations: list[str] = []
    for path, tree in _production_modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in forbidden_calls:
                violations.append(f"{_relative(path)}:{node.lineno} calls {node.func.id}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "importlib"
            ):
                violations.append(f"{_relative(path)}:{node.lineno} calls importlib.{node.func.attr}")
    assert not violations, f"dynamic dependency escape hatches are forbidden: {violations}"


def test_internal_any_is_forbidden() -> None:
    violations: list[str] = []
    for path, tree in _production_modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "typing":
                violations.extend(
                    f"{_relative(path)}:{node.lineno} imports typing.Any" for alias in node.names if alias.name == "Any"
                )
            elif (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "typing"
                and node.attr == "Any"
            ):
                violations.append(f"{_relative(path)}:{node.lineno} uses typing.Any")
    assert not violations, f"internal Any is forbidden; decode dynamic values at adapters: {violations}"


def test_execution_is_the_only_generic_executor_owner() -> None:
    executor_modules = sorted(path.relative_to(PACKAGE_ROOT).as_posix() for path in PACKAGE_ROOT.rglob("executor.py"))
    assert executor_modules in ([], ["execution/executor.py"]), (
        "only execution/executor.py may own the generic graph executor; "
        f"use capability-specific names such as node_executor.py elsewhere: {executor_modules}"
    )
