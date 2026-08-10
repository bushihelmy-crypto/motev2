import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ImportViolation:
    line: int
    message: str


def _is_docstring(statement: ast.stmt, *, index: int) -> bool:
    return (
        index == 0
        and isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def import_placement_violations(source: str, *, filename: str = "<unknown>") -> tuple[ImportViolation, ...]:
    tree = ast.parse(source, filename=filename)
    violations: list[ImportViolation] = []
    import_block_closed = False

    for index, statement in enumerate(tree.body):
        if _is_docstring(statement, index=index):
            continue
        if isinstance(statement, ast.ImportFrom) and statement.module == "__future__":
            if import_block_closed:
                violations.append(ImportViolation(statement.lineno, "__future__ import is outside the header"))
            continue
        if isinstance(statement, ast.Import | ast.ImportFrom):
            if import_block_closed:
                violations.append(ImportViolation(statement.lineno, "import appears after executable declarations"))
            continue
        import_block_closed = True

    violations.extend(
        ImportViolation(node.lineno, "import is not module scoped")
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom) and node not in tree.body
    )

    return tuple(violations)


def production_import_placement_violations(package_root: Path) -> tuple[str, ...]:
    violations: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
        relative_path = path.relative_to(package_root).as_posix()
        source = path.read_text(encoding="utf-8")
        violations.extend(
            f"{relative_path}:{violation.line} {violation.message}"
            for violation in import_placement_violations(source, filename=str(path))
        )
    return tuple(violations)
