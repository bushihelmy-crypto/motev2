import ast
from collections.abc import Iterator
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "mote_kernel"
PACKAGE_PREFIX = "mote_kernel."

CORE_FLOW_PACKAGES = frozenset(
    {
        "act",
        "events",
        "failover",
        "hooks",
        "logging",
        "loop",
        "observability",
        "observe",
        "operations",
        "role",
        "think",
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
    forbidden = CORE_FLOW_PACKAGES
    for path, tree in _production_modules():
        relative = path.relative_to(PACKAGE_ROOT)
        if relative.parts[0] != "execution":
            continue
        for line, imported_root in _internal_import_roots(tree):
            if imported_root in forbidden:
                violations.append(f"{relative}:{line} imports {imported_root}")
    assert not violations, f"execution must remain domain-agnostic: {violations}"


def test_graph_definition_layer_does_not_depend_on_runtime_execution_modules() -> None:
    forbidden_modules = frozenset({"engine", "executor", "request", "result", "snapshot", "transition"})
    violations: list[str] = []
    graph_root = PACKAGE_ROOT / "execution" / "graph"
    for path in sorted(graph_root.rglob("*.py")):
        relative = path.relative_to(PACKAGE_ROOT)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            prefix = "mote_kernel.execution."
            if not node.module.startswith(prefix):
                continue
            imported_module = node.module.removeprefix(prefix).partition(".")[0]
            if imported_module in forbidden_modules:
                violations.append(f"{relative}:{node.lineno} imports execution.{imported_module}")
    assert not violations, f"graph definitions must remain below runtime execution: {violations}"


def test_graph_state_model_layers_remain_acyclic() -> None:
    forbidden_by_module = {
        "identity.py": {"routing", "frontier_model", "model", "command"},
        "routing.py": {"frontier_model", "model", "command"},
        "frontier_model.py": {"model", "command"},
        "model.py": {"command"},
    }
    violations: list[str] = []
    state_root = PACKAGE_ROOT / "state" / "graph_state"
    prefix = "mote_kernel.state.graph_state."
    for filename, forbidden in forbidden_by_module.items():
        path = state_root / filename
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None and node.module.startswith(prefix):
                imported = node.module.removeprefix(prefix).partition(".")[0]
                if imported in forbidden:
                    violations.append(f"{filename}:{node.lineno} imports {imported}")
    assert not violations, f"graph state model ownership must remain acyclic: {violations}"
