"""Deterministic structural-complexity metrics for production code.

The gate intentionally measures definition burden and normalized logic shapes
instead of treating line count or textual copy/paste as semantic complexity.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import TypeAlias, cast

FunctionNode: TypeAlias = ast.FunctionDef | ast.AsyncFunctionDef

_FUNCTION_CLONE_MIN_NODES = 22
_BRANCH_CLONE_MIN_NODES = 28
_THIN_HELPER_MAX_NODES = 18
_NORMALIZED_NAME_FIELDS = frozenset({"arg", "asname", "attr", "id", "module", "name"})

RATCHET_METRIC_NAMES = frozenset(
    {
        "top_level_definitions",
        "type_definitions",
        "dataclass_types",
        "dataclass_fields",
        "decision_points",
        "logical_clone_pairs",
        "record_shape_clone_pairs",
        "thin_single_use_helpers",
        "single_use_private_dataclasses",
        "test_only_private_definitions",
    }
)
HEALTH_METRIC_NAMES = frozenset(
    {
        "logical_clone_pairs",
        "record_shape_clone_pairs",
        "thin_single_use_helpers",
        "single_use_private_dataclasses",
        "test_only_private_definitions",
    }
)


@dataclass(frozen=True, order=True, slots=True)
class DefinitionSite:
    path: str
    line: int
    qualified_name: str

    def render(self) -> str:
        return f"{self.path}:{self.line}:{self.qualified_name}"


@dataclass(frozen=True, order=True, slots=True)
class ClonePair:
    kind: str
    left: DefinitionSite
    right: DefinitionSite


@dataclass(frozen=True, slots=True)
class ComplexitySnapshot:
    top_level_definitions: int
    type_definitions: int
    dataclass_types: int
    dataclass_fields: int
    decision_points: int
    logical_clone_pairs: int
    record_shape_clone_pairs: int
    thin_single_use_helpers: int
    single_use_private_dataclasses: int
    test_only_private_definitions: int
    logical_clones: tuple[ClonePair, ...]
    record_shape_clones: tuple[ClonePair, ...]
    thin_helpers: tuple[DefinitionSite, ...]
    single_use_private_records: tuple[DefinitionSite, ...]
    test_only_definitions: tuple[DefinitionSite, ...]

    def metric_items(self) -> tuple[tuple[str, int], ...]:
        return (
            ("top_level_definitions", self.top_level_definitions),
            ("type_definitions", self.type_definitions),
            ("dataclass_types", self.dataclass_types),
            ("dataclass_fields", self.dataclass_fields),
            ("decision_points", self.decision_points),
            ("logical_clone_pairs", self.logical_clone_pairs),
            ("record_shape_clone_pairs", self.record_shape_clone_pairs),
            ("thin_single_use_helpers", self.thin_single_use_helpers),
            ("single_use_private_dataclasses", self.single_use_private_dataclasses),
            ("test_only_private_definitions", self.test_only_private_definitions),
        )


@dataclass(frozen=True, slots=True)
class ComplexityCandidateInventory:
    """Stable identities for structural candidates found by the scanner."""

    logical_clone_pairs: frozenset[str]
    record_shape_clone_pairs: frozenset[str]
    thin_single_use_helpers: frozenset[str]
    single_use_private_dataclasses: frozenset[str]
    test_only_private_definitions: frozenset[str]

    def metric_items(self) -> tuple[tuple[str, frozenset[str]], ...]:
        return (
            ("logical_clone_pairs", self.logical_clone_pairs),
            ("record_shape_clone_pairs", self.record_shape_clone_pairs),
            ("thin_single_use_helpers", self.thin_single_use_helpers),
            ("single_use_private_dataclasses", self.single_use_private_dataclasses),
            ("test_only_private_definitions", self.test_only_private_definitions),
        )

    def difference(self, other: ComplexityCandidateInventory) -> ComplexityCandidateInventory:
        return ComplexityCandidateInventory(
            logical_clone_pairs=self.logical_clone_pairs - other.logical_clone_pairs,
            record_shape_clone_pairs=self.record_shape_clone_pairs - other.record_shape_clone_pairs,
            thin_single_use_helpers=self.thin_single_use_helpers - other.thin_single_use_helpers,
            single_use_private_dataclasses=self.single_use_private_dataclasses - other.single_use_private_dataclasses,
            test_only_private_definitions=self.test_only_private_definitions - other.test_only_private_definitions,
        )

    def total(self) -> int:
        return sum(len(values) for _name, values in self.metric_items())


@dataclass(frozen=True, slots=True)
class _Module:
    relative_path: str
    tree: ast.Module


@dataclass(frozen=True, slots=True)
class _Function:
    site: DefinitionSite
    node: FunctionNode


@dataclass(frozen=True, slots=True)
class _Block:
    site: DefinitionSite
    owner: DefinitionSite
    statements: tuple[ast.stmt, ...]


@dataclass(frozen=True, slots=True)
class _Record:
    site: DefinitionSite
    node: ast.ClassDef


def _parse_modules(root: Path) -> tuple[_Module, ...]:
    return tuple(
        _Module(
            path.relative_to(root).as_posix(),
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path)),
        )
        for path in sorted(root.rglob("*.py"))
    )


def _mapping(value: object, *, name: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a TOML table")
    return cast(dict[str, object], value)


def load_metric_limits(
    project_root: Path,
    *,
    table_name: str,
    expected_names: frozenset[str],
) -> dict[str, int]:
    configuration = _mapping(
        tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8")),
        name="configuration",
    )
    tool = _mapping(configuration.get("tool"), name="tool")
    mote_kernel = _mapping(tool.get("mote_kernel"), name="tool.mote_kernel")
    limits_table = _mapping(mote_kernel.get(table_name), name=f"tool.mote_kernel.{table_name}")
    if frozenset(limits_table) != expected_names:
        raise ValueError(
            f"{table_name} must name every metric exactly; "
            f"expected {sorted(expected_names)}, found {sorted(limits_table)}"
        )

    limits: dict[str, int] = {}
    for name, value in limits_table.items():
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{table_name} limit {name} must be an integer")
        limits[name] = value
    return limits


def _load_string_set(value: object, *, name: str) -> frozenset[str]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array of strings")
    entries = cast(list[object], value)
    if any(not isinstance(entry, str) or not entry for entry in entries):
        raise ValueError(f"{name} must contain only non-empty strings")
    strings = tuple(cast(str, entry) for entry in entries)
    if len(strings) != len(set(strings)):
        raise ValueError(f"{name} must not contain duplicate candidate identities")
    return frozenset(strings)


def load_reviewed_candidates(project_root: Path) -> ComplexityCandidateInventory:
    configuration = _mapping(
        tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8")),
        name="configuration",
    )
    tool = _mapping(configuration.get("tool"), name="tool")
    mote_kernel = _mapping(tool.get("mote_kernel"), name="tool.mote_kernel")
    reviewed_table = _mapping(
        mote_kernel.get("complexity_reviewed"),
        name="tool.mote_kernel.complexity_reviewed",
    )
    if frozenset(reviewed_table) != HEALTH_METRIC_NAMES:
        raise ValueError(
            "complexity_reviewed must name every health metric exactly; "
            f"expected {sorted(HEALTH_METRIC_NAMES)}, found {sorted(reviewed_table)}"
        )
    return ComplexityCandidateInventory(
        logical_clone_pairs=_load_string_set(
            reviewed_table["logical_clone_pairs"],
            name="complexity_reviewed.logical_clone_pairs",
        ),
        record_shape_clone_pairs=_load_string_set(
            reviewed_table["record_shape_clone_pairs"],
            name="complexity_reviewed.record_shape_clone_pairs",
        ),
        thin_single_use_helpers=_load_string_set(
            reviewed_table["thin_single_use_helpers"],
            name="complexity_reviewed.thin_single_use_helpers",
        ),
        single_use_private_dataclasses=_load_string_set(
            reviewed_table["single_use_private_dataclasses"],
            name="complexity_reviewed.single_use_private_dataclasses",
        ),
        test_only_private_definitions=_load_string_set(
            reviewed_table["test_only_private_definitions"],
            name="complexity_reviewed.test_only_private_definitions",
        ),
    )


def _qualified_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _qualified_name(node.value)
        if owner is not None:
            return f"{owner}.{node.attr}"
    return None


def _is_dataclass(node: ast.ClassDef) -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = _qualified_name(target)
        if name is not None and name.rsplit(".", maxsplit=1)[-1] == "dataclass":
            return True
    return False


def _is_explicit_type_alias(node: ast.stmt) -> bool:
    if isinstance(node, ast.AnnAssign):
        name = _qualified_name(node.annotation)
        return name is not None and name.rsplit(".", maxsplit=1)[-1] == "TypeAlias"
    if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
        return False
    name = _qualified_name(node.value.func)
    return name is not None and name.rsplit(".", maxsplit=1)[-1] == "NewType"


def _without_docstring(statements: Iterable[ast.stmt]) -> tuple[ast.stmt, ...]:
    body = tuple(statements)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def _semantic_node_count(statements: Iterable[ast.stmt]) -> int:
    module = ast.Module(body=list(statements), type_ignores=[])
    return sum(1 for node in ast.walk(module) if not isinstance(node, ast.Load | ast.Store | ast.Del))


def _shape_value(value: object, *, field: str) -> str:
    if isinstance(value, ast.AST):
        return _node_shape(value)
    if isinstance(value, list):
        items = cast(list[object], value)
        if field in {"kwd_attrs"}:
            return "[" + ",".join("_" for _ in items) + "]"
        return "[" + ",".join(_shape_value(item, field="") for item in items) + "]"
    if field in _NORMALIZED_NAME_FIELDS and isinstance(value, str):
        return "_"
    return repr(value)


def _node_shape(node: ast.AST) -> str:
    parts = [type(node).__name__]
    for field, raw_value in ast.iter_fields(node):
        value = cast(object, raw_value)
        if field in {"type_comment", "type_ignores"}:
            continue
        if isinstance(node, ast.Constant) and field == "value":
            rendered = f"<{type(value).__name__}>"
        else:
            rendered = _shape_value(value, field=field)
        parts.append(f"{field}={rendered}")
    return "(" + ";".join(parts) + ")"


def _statement_shape(statements: Iterable[ast.stmt]) -> str:
    return _node_shape(ast.Module(body=list(statements), type_ignores=[]))


def _function_definitions(modules: Iterable[_Module]) -> tuple[_Function, ...]:
    definitions: list[_Function] = []
    for module in modules:
        for node in module.tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                site = DefinitionSite(module.relative_path, node.lineno, node.name)
                definitions.append(_Function(site, node))
            elif isinstance(node, ast.ClassDef):
                definitions.extend(
                    _Function(
                        DefinitionSite(module.relative_path, member.lineno, f"{node.name}.{member.name}"),
                        member,
                    )
                    for member in node.body
                    if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
                )
    return tuple(definitions)


def _dataclass_definitions(modules: Iterable[_Module]) -> tuple[_Record, ...]:
    return tuple(
        _Record(DefinitionSite(module.relative_path, node.lineno, node.name), node)
        for module in modules
        for node in module.tree.body
        if isinstance(node, ast.ClassDef) and _is_dataclass(node)
    )


def _ordered_pair(kind: str, left: DefinitionSite, right: DefinitionSite) -> ClonePair:
    first, second = sorted((left, right))
    return ClonePair(kind, first, second)


def _function_clone_pairs(functions: Iterable[_Function]) -> tuple[ClonePair, ...]:
    by_shape: defaultdict[str, list[DefinitionSite]] = defaultdict(list)
    for function in functions:
        body = _without_docstring(function.node.body)
        if _semantic_node_count(body) >= _FUNCTION_CLONE_MIN_NODES:
            by_shape[_statement_shape(body)].append(function.site)
    return tuple(
        sorted(
            _ordered_pair("function", left, right)
            for sites in by_shape.values()
            for left, right in combinations(sorted(set(sites)), 2)
        )
    )


def _control_blocks(function: _Function) -> tuple[_Block, ...]:
    blocks: list[_Block] = []
    for node in ast.walk(function.node):
        if not isinstance(
            node,
            ast.If | ast.For | ast.AsyncFor | ast.While | ast.With | ast.AsyncWith | ast.Try | ast.Match,
        ):
            continue
        for field in ("body", "orelse", "finalbody"):
            raw_statements = getattr(node, field, None)
            if not isinstance(raw_statements, list):
                continue
            items = cast(list[object], raw_statements)
            statements = tuple(statement for statement in items if isinstance(statement, ast.stmt))
            if len(statements) < 2 or _semantic_node_count(statements) < _BRANCH_CLONE_MIN_NODES:
                continue
            blocks.append(
                _Block(
                    DefinitionSite(function.site.path, statements[0].lineno, function.site.qualified_name),
                    function.site,
                    statements,
                )
            )
    return tuple(blocks)


def _branch_clone_pairs(
    functions: Iterable[_Function],
    function_pairs: Iterable[ClonePair],
) -> tuple[ClonePair, ...]:
    cloned_owners = {frozenset((pair.left, pair.right)) for pair in function_pairs if pair.kind == "function"}
    by_shape: defaultdict[str, list[_Block]] = defaultdict(list)
    for function in functions:
        for block in _control_blocks(function):
            by_shape[_statement_shape(block.statements)].append(block)

    pairs: set[ClonePair] = set()
    for blocks in by_shape.values():
        for left, right in combinations(sorted(set(blocks), key=lambda block: block.site), 2):
            if left.site == right.site or frozenset((left.owner, right.owner)) in cloned_owners:
                continue
            pairs.add(_ordered_pair("branch", left.site, right.site))
    return tuple(sorted(pairs))


def _record_field(node: ast.stmt) -> tuple[str, ast.expr, bool] | None:
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id, node.annotation, node.value is not None
    return None


def _record_shape(record: _Record) -> str | None:
    fields = [field for node in record.node.body if (field := _record_field(node)) is not None]
    if len(fields) < 2:
        return None
    return "|".join(f"{name}:{_node_shape(annotation)}:{has_default}" for name, annotation, has_default in fields)


def _record_clone_pairs(records: Iterable[_Record]) -> tuple[ClonePair, ...]:
    by_shape: defaultdict[str, list[DefinitionSite]] = defaultdict(list)
    for record in records:
        shape = _record_shape(record)
        if shape is not None:
            by_shape[shape].append(record.site)
    return tuple(
        sorted(
            _ordered_pair("record", left, right)
            for sites in by_shape.values()
            for left, right in combinations(sorted(set(sites)), 2)
        )
    )


def _name_loads(modules: Iterable[_Module]) -> Counter[str]:
    return Counter(
        node.id
        for module in modules
        for node in ast.walk(module.tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    )


def _private_top_level_functions(modules: Iterable[_Module]) -> tuple[_Function, ...]:
    return tuple(
        _Function(DefinitionSite(module.relative_path, node.lineno, node.name), node)
        for module in modules
        for node in module.tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("_")
        and not node.name.startswith("__")
    )


def _decision_points(modules: Iterable[_Module]) -> int:
    total = 0
    simple_decisions = (
        ast.If,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.IfExp,
        ast.comprehension,
        ast.ExceptHandler,
    )
    for module in modules:
        for node in ast.walk(module.tree):
            if isinstance(node, simple_decisions):
                total += 1
            elif isinstance(node, ast.BoolOp):
                total += max(1, len(node.values) - 1)
            elif isinstance(node, ast.Match):
                total += max(1, len(node.cases) - 1)
    return total


def complexity_snapshot(package_root: Path, test_root: Path) -> ComplexitySnapshot:
    production_modules = _parse_modules(package_root)
    test_modules = _parse_modules(test_root)
    functions = _function_definitions(production_modules)
    records = _dataclass_definitions(production_modules)
    function_pairs = _function_clone_pairs(functions)
    logical_clones = tuple(sorted((*function_pairs, *_branch_clone_pairs(functions, function_pairs))))
    record_shape_clones = _record_clone_pairs(records)

    production_loads = _name_loads(production_modules)
    test_loads = _name_loads(test_modules)
    private_functions = _private_top_level_functions(production_modules)
    thin_helpers = tuple(
        sorted(
            function.site
            for function in private_functions
            if production_loads[function.node.name] <= 1
            and _semantic_node_count(_without_docstring(function.node.body)) <= _THIN_HELPER_MAX_NODES
        )
    )
    single_use_private_records = tuple(
        sorted(
            record.site
            for record in records
            if record.node.name.startswith("_") and production_loads[record.node.name] <= 1
        )
    )
    test_only_definitions = tuple(
        sorted(
            function.site
            for function in private_functions
            if production_loads[function.node.name] == 0 and test_loads[function.node.name] > 0
        )
    )

    class_definitions = sum(
        isinstance(node, ast.ClassDef) for module in production_modules for node in module.tree.body
    )
    function_definitions = sum(
        isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        for module in production_modules
        for node in module.tree.body
    )
    aliases = sum(_is_explicit_type_alias(node) for module in production_modules for node in module.tree.body)
    dataclass_fields = sum(
        isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        for record in records
        for node in record.node.body
    )

    return ComplexitySnapshot(
        top_level_definitions=class_definitions + function_definitions + aliases,
        type_definitions=class_definitions + aliases,
        dataclass_types=len(records),
        dataclass_fields=dataclass_fields,
        decision_points=_decision_points(production_modules),
        logical_clone_pairs=len(logical_clones),
        record_shape_clone_pairs=len(record_shape_clones),
        thin_single_use_helpers=len(thin_helpers),
        single_use_private_dataclasses=len(single_use_private_records),
        test_only_private_definitions=len(test_only_definitions),
        logical_clones=logical_clones,
        record_shape_clones=record_shape_clones,
        thin_helpers=thin_helpers,
        single_use_private_records=single_use_private_records,
        test_only_definitions=test_only_definitions,
    )


def _clone_identity(pair: ClonePair) -> str:
    return f"[{pair.kind}] {pair.left.render()} <-> {pair.right.render()}"


def _site_identity(site: DefinitionSite) -> str:
    return site.render()


def _record_clone_identity(pair: ClonePair) -> str:
    return f"{pair.left.render()} <-> {pair.right.render()}"


def candidate_inventory(snapshot: ComplexitySnapshot) -> ComplexityCandidateInventory:
    return ComplexityCandidateInventory(
        logical_clone_pairs=frozenset(_clone_identity(pair) for pair in snapshot.logical_clones),
        record_shape_clone_pairs=frozenset(_record_clone_identity(pair) for pair in snapshot.record_shape_clones),
        thin_single_use_helpers=frozenset(_site_identity(site) for site in snapshot.thin_helpers),
        single_use_private_dataclasses=frozenset(_site_identity(site) for site in snapshot.single_use_private_records),
        test_only_private_definitions=frozenset(_site_identity(site) for site in snapshot.test_only_definitions),
    )


def health_target_gaps(
    snapshot: ComplexitySnapshot,
    targets: Mapping[str, int],
    reviewed: ComplexityCandidateInventory | None = None,
) -> tuple[tuple[str, int, int], ...]:
    actual = dict(snapshot.metric_items())
    if reviewed is not None:
        unresolved = candidate_inventory(snapshot).difference(reviewed)
        actual.update((name, len(values)) for name, values in unresolved.metric_items())
    return tuple((name, targets[name], actual[name]) for name in sorted(targets) if actual[name] > targets[name])


def _render_candidate(identity: str, reviewed: frozenset[str] | None) -> str:
    if reviewed is None:
        return f"  {identity}"
    status = "reviewed" if identity in reviewed else "UNREVIEWED"
    return f"  [{status}] {identity}"


def render_complexity_report(
    snapshot: ComplexitySnapshot,
    *,
    health_targets: Mapping[str, int] | None = None,
    reviewed: ComplexityCandidateInventory | None = None,
) -> str:
    lines = ["Structural complexity metrics:"]
    lines.extend(f"  {name}: {value}" for name, value in snapshot.metric_items())
    inventory = candidate_inventory(snapshot)
    stale: ComplexityCandidateInventory | None = None

    if health_targets is not None:
        gaps = health_target_gaps(snapshot, health_targets, reviewed)
        if reviewed is not None:
            stale = reviewed.difference(inventory)
        lines.append("")
        lines.append("Health target (unreviewed candidates):")
        if gaps or (stale is not None and stale.total() > 0):
            lines.append("  FAIL (new candidates require review; stale review entries must be removed)")
            lines.extend(f"  {name}: target={target}, actual={actual}" for name, target, actual in gaps)
        else:
            lines.append("  PASS")
        if reviewed is not None:
            unreviewed = inventory.difference(reviewed)
            lines.append(f"  reviewed candidates: {inventory.total() - unreviewed.total()}")
            lines.append(f"  unreviewed candidates: {unreviewed.total()}")
            lines.append(f"  stale reviewed entries: {stale.total() if stale is not None else 0}")

    sections: tuple[tuple[str, Iterable[str]], ...] = (
        (
            "Normalized logical clone candidates:",
            (
                _render_candidate(
                    _clone_identity(pair),
                    reviewed.logical_clone_pairs if reviewed is not None else None,
                )
                for pair in snapshot.logical_clones
            ),
        ),
        (
            "Dataclasses with matching field shapes:",
            (
                _render_candidate(
                    _record_clone_identity(pair),
                    reviewed.record_shape_clone_pairs if reviewed is not None else None,
                )
                for pair in snapshot.record_shape_clones
            ),
        ),
        (
            "Thin private helpers used at most once:",
            (
                _render_candidate(
                    _site_identity(site),
                    reviewed.thin_single_use_helpers if reviewed is not None else None,
                )
                for site in snapshot.thin_helpers
            ),
        ),
        (
            "Private dataclasses referenced at most once in production:",
            (
                _render_candidate(
                    _site_identity(site),
                    reviewed.single_use_private_dataclasses if reviewed is not None else None,
                )
                for site in snapshot.single_use_private_records
            ),
        ),
        (
            "Private production definitions referenced only by tests:",
            (
                _render_candidate(
                    _site_identity(site),
                    reviewed.test_only_private_definitions if reviewed is not None else None,
                )
                for site in snapshot.test_only_definitions
            ),
        ),
    )
    for title, entries in sections:
        rendered = tuple(entries)
        lines.append("")
        lines.append(title)
        lines.extend(rendered or ("  none",))
    if reviewed is not None:
        stale = reviewed.difference(inventory)
        lines.append("")
        lines.append("Stale reviewed candidate identities:")
        for name, values in stale.metric_items():
            lines.extend(f"  {name}: {value}" for value in sorted(values))
        if stale.total() == 0:
            lines.append("  none")
    return "\n".join(lines)


def main() -> int:
    project_root = Path.cwd()
    snapshot = complexity_snapshot(project_root / "src" / "mote_kernel", project_root / "tests")
    health_targets = load_metric_limits(
        project_root,
        table_name="complexity_health",
        expected_names=HEALTH_METRIC_NAMES,
    )
    reviewed = load_reviewed_candidates(project_root)
    sys.stdout.write(f"{render_complexity_report(snapshot, health_targets=health_targets, reviewed=reviewed)}\n")
    arguments = sys.argv[1:]
    if not arguments:
        return 0
    if arguments != ["--check-health"]:
        sys.stderr.write("usage: python -m tests.architecture.complexity_rules [--check-health]\n")
        return 2
    stale = reviewed.difference(candidate_inventory(snapshot))
    return int(bool(health_target_gaps(snapshot, health_targets, reviewed) or stale.total() > 0))


if __name__ == "__main__":
    raise SystemExit(main())
