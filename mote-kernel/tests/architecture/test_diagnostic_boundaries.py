"""Architecture boundaries for backend-neutral logging and observability."""

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "mote_kernel"
DIAGNOSTIC_PACKAGES = ("logging", "observability")
ALLOWED_IMPORT_ROOTS = frozenset(
    {
        "__future__",
        "asyncio",
        "collections",
        "dataclasses",
        "enum",
        "math",
        "mote_kernel",
        "time",
        "typing",
    }
)


def test_diagnostic_packages_do_not_import_a_concrete_backend() -> None:
    violations: list[str] = []
    for package in DIAGNOSTIC_PACKAGES:
        for path in sorted((PACKAGE_ROOT / package).glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = tuple(alias.name.partition(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module is not None:
                    roots = (node.module.partition(".")[0],)
                else:
                    continue
                violations.extend(
                    f"{path.relative_to(PACKAGE_ROOT)}:{node.lineno} imports {root}"
                    for root in roots
                    if root not in ALLOWED_IMPORT_ROOTS
                )
    assert not violations, f"diagnostic contracts must remain backend-neutral: {violations}"


def test_observability_has_no_graph_commit_decorator() -> None:
    assert not (PACKAGE_ROOT / "observability" / "commit.py").exists()
