import ast
from collections.abc import Iterator
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "mote_kernel"
PACKAGE_PREFIX = "mote_kernel."

CORE_FLOW_PACKAGES = frozenset(
    {
        "act",
        "events",
        "extensions",
        "failover",
        "hooks",
        "logging",
        "observability",
        "observe",
        "operations",
        "role",
        "think",
        "tools",
        "turn_context",
        "workflow",
    }
)


def _production_modules() -> Iterator[tuple[Path, ast.Module]]:
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _internal_import_roots(tree: ast.Module) -> Iterator[tuple[int, str]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(PACKAGE_PREFIX):
                    remainder = alias.name.removeprefix(PACKAGE_PREFIX)
                    yield node.lineno, remainder.partition(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.module.startswith(PACKAGE_PREFIX):
            remainder = node.module.removeprefix(PACKAGE_PREFIX)
            yield node.lineno, remainder.partition(".")[0]


def test_state_does_not_depend_on_flow_packages() -> None:
    violations: list[str] = []
    for path, tree in _production_modules():
        relative = path.relative_to(PACKAGE_ROOT)
        if relative.parts[0] != "state":
            continue
        for line, imported_root in _internal_import_roots(tree):
            if imported_root in CORE_FLOW_PACKAGES or imported_root == "execution":
                violations.append(f"{relative}:{line} imports {imported_root}")
    assert not violations, f"state must remain below flows and execution: {violations}"


def test_execution_does_not_depend_on_domain_packages() -> None:
    violations: list[str] = []
    forbidden = CORE_FLOW_PACKAGES | {"state"}
    for path, tree in _production_modules():
        relative = path.relative_to(PACKAGE_ROOT)
        if relative.parts[0] != "execution":
            continue
        for line, imported_root in _internal_import_roots(tree):
            if imported_root in forbidden:
                violations.append(f"{relative}:{line} imports {imported_root}")
    assert not violations, f"execution must remain domain- and state-agnostic: {violations}"


def test_workflow_does_not_depend_on_tools_or_act() -> None:
    violations: list[str] = []
    for path, tree in _production_modules():
        relative = path.relative_to(PACKAGE_ROOT)
        if relative.parts[0] != "workflow":
            continue
        for line, imported_root in _internal_import_roots(tree):
            if imported_root in {"act", "tools"}:
                violations.append(f"{relative}:{line} imports {imported_root}")
    assert not violations, f"workflow receives node execution through a narrow injected boundary: {violations}"
