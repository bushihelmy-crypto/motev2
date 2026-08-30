"""Deterministic structural-complexity metrics for production code.

The gate intentionally measures definition burden and normalized logic shapes
instead of treating line count or textual copy/paste as semantic complexity.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from tests.architecture.clone_analysis import (
    ClonePair,
    DefinitionSite,
    NearClonePair,
    matching_record_shapes,
    near_function_clones,
    normalized_logic_clones,
    normalized_statement_clones,
)
from tests.architecture.quality_analysis import (
    AsyncCallViolation,
    CandidateSite,
    ClassCohesion,
    ComplexityHotspot,
    CoroutineHandleViolation,
    TaskHandleViolation,
    ambiguous_internal_dispatches,
    call_graph_metrics,
    class_cohesion_candidates,
    class_structure_metrics,
    complexity_hotspots,
    cyclic_module_dependencies,
    linear_private_call_chain_links,
    low_usage_private_definitions,
    module_dependency_metrics,
    orphaned_task_handles,
    production_unreferenced_definitions,
    single_use_private_dataclasses,
    stateful_async_hotspots,
    test_only_private_definitions,
    thin_single_use_methods,
    thin_single_use_private_functions,
    transparent_private_definitions,
    unconsumed_internal_async_calls,
    unowned_internal_coroutine_handles,
    unread_private_fields,
    unused_private_definitions,
)
from tests.architecture.semantic_index import build_semantic_index, strongly_connected_calls
from tests.architecture.semantic_model import CallSite, SymbolId, SymbolKind

RATCHET_METRIC_NAMES = frozenset(
    {
        "top_level_definitions",
        "type_definitions",
        "dataclass_types",
        "dataclass_fields",
        "decision_points",
        "logical_clone_pairs",
        "statement_clone_pairs",
        "near_clone_pairs",
        "record_shape_clone_pairs",
        "thin_single_use_helpers",
        "single_use_private_dataclasses",
        "test_only_private_definitions",
        "function_definitions",
        "method_definitions",
        "nested_function_definitions",
        "semantic_nodes",
        "cognitive_complexity",
        "await_points",
        "exception_handlers",
        "attribute_writes",
        "task_creations",
        "max_cyclomatic_complexity",
        "max_cognitive_complexity",
        "max_nesting_depth",
        "internal_call_edges",
        "recursive_call_components",
        "max_call_fan_out",
        "max_call_chain_depth",
        "max_call_chain_cognitive",
        "internal_import_edges",
        "import_cycle_components",
        "max_module_fan_out",
        "max_module_dependency_depth",
        "ambiguous_internal_dispatches",
        "transparent_private_definitions",
        "thin_single_use_methods",
        "low_usage_private_definitions",
        "linear_private_call_chain_links",
        "production_unreferenced_definitions",
        "low_cohesion_classes",
        "max_methods_per_class",
        "max_fields_per_class",
        "max_class_components",
        "complexity_hotspots",
        "stateful_async_hotspots",
        "unused_private_definitions",
        "unread_private_fields",
        "unconsumed_internal_async_calls",
        "unowned_internal_coroutine_handles",
        "orphaned_task_handles",
    }
)
HEALTH_METRIC_NAMES = frozenset(
    {
        "import_cycle_components",
        "test_only_private_definitions",
        "unused_private_definitions",
        "unread_private_fields",
        "unconsumed_internal_async_calls",
        "unowned_internal_coroutine_handles",
        "orphaned_task_handles",
    }
)


@dataclass(frozen=True, slots=True)
class ComplexitySnapshot:
    top_level_definitions: int
    type_definitions: int
    dataclass_types: int
    dataclass_fields: int
    decision_points: int
    logical_clone_pairs: int
    statement_clone_pairs: int
    near_clone_pairs: int
    record_shape_clone_pairs: int
    thin_single_use_helpers: int
    single_use_private_dataclasses: int
    test_only_private_definitions: int
    function_definitions: int
    method_definitions: int
    nested_function_definitions: int
    semantic_nodes: int
    cognitive_complexity: int
    await_points: int
    exception_handlers: int
    attribute_writes: int
    task_creations: int
    max_cyclomatic_complexity: int
    max_cognitive_complexity: int
    max_nesting_depth: int
    internal_call_edges: int
    recursive_call_components: int
    max_call_fan_out: int
    max_call_chain_depth: int
    max_call_chain_cognitive: int
    internal_import_edges: int
    import_cycle_components: int
    max_module_fan_out: int
    max_module_dependency_depth: int
    ambiguous_internal_dispatches: int
    transparent_private_definitions: int
    thin_single_use_methods: int
    low_usage_private_definitions: int
    linear_private_call_chain_links: int
    production_unreferenced_definitions: int
    low_cohesion_classes: int
    max_methods_per_class: int
    max_fields_per_class: int
    max_class_components: int
    complexity_hotspots: int
    stateful_async_hotspots: int
    unused_private_definitions: int
    unread_private_fields: int
    unconsumed_internal_async_calls: int
    unowned_internal_coroutine_handles: int
    orphaned_task_handles: int
    logical_clones: tuple[ClonePair, ...]
    statement_clones: tuple[ClonePair, ...]
    near_clones: tuple[NearClonePair, ...]
    record_shape_clones: tuple[ClonePair, ...]
    thin_helpers: tuple[DefinitionSite, ...]
    single_use_private_records: tuple[DefinitionSite, ...]
    test_only_definitions: tuple[DefinitionSite, ...]
    transparent_definitions: tuple[CandidateSite, ...]
    thin_methods: tuple[CandidateSite, ...]
    low_usage_definitions: tuple[CandidateSite, ...]
    linear_chain_links: tuple[CandidateSite, ...]
    unreferenced_definitions: tuple[CandidateSite, ...]
    low_cohesion: tuple[ClassCohesion, ...]
    hotspots: tuple[ComplexityHotspot, ...]
    async_hotspots: tuple[ComplexityHotspot, ...]
    ambiguous_dispatches: tuple[CallSite, ...]
    recursive_components: tuple[frozenset[SymbolId], ...]
    import_cycles: tuple[frozenset[str], ...]
    unused_definitions: tuple[CandidateSite, ...]
    unread_fields: tuple[CandidateSite, ...]
    unconsumed_async_calls: tuple[AsyncCallViolation, ...]
    unowned_coroutines: tuple[CoroutineHandleViolation, ...]
    orphaned_tasks: tuple[TaskHandleViolation, ...]

    def metric_items(self) -> tuple[tuple[str, int], ...]:
        return (
            ("top_level_definitions", self.top_level_definitions),
            ("type_definitions", self.type_definitions),
            ("dataclass_types", self.dataclass_types),
            ("dataclass_fields", self.dataclass_fields),
            ("decision_points", self.decision_points),
            ("logical_clone_pairs", self.logical_clone_pairs),
            ("statement_clone_pairs", self.statement_clone_pairs),
            ("near_clone_pairs", self.near_clone_pairs),
            ("record_shape_clone_pairs", self.record_shape_clone_pairs),
            ("thin_single_use_helpers", self.thin_single_use_helpers),
            ("single_use_private_dataclasses", self.single_use_private_dataclasses),
            ("test_only_private_definitions", self.test_only_private_definitions),
            ("function_definitions", self.function_definitions),
            ("method_definitions", self.method_definitions),
            ("nested_function_definitions", self.nested_function_definitions),
            ("semantic_nodes", self.semantic_nodes),
            ("cognitive_complexity", self.cognitive_complexity),
            ("await_points", self.await_points),
            ("exception_handlers", self.exception_handlers),
            ("attribute_writes", self.attribute_writes),
            ("task_creations", self.task_creations),
            ("max_cyclomatic_complexity", self.max_cyclomatic_complexity),
            ("max_cognitive_complexity", self.max_cognitive_complexity),
            ("max_nesting_depth", self.max_nesting_depth),
            ("internal_call_edges", self.internal_call_edges),
            ("recursive_call_components", self.recursive_call_components),
            ("max_call_fan_out", self.max_call_fan_out),
            ("max_call_chain_depth", self.max_call_chain_depth),
            ("max_call_chain_cognitive", self.max_call_chain_cognitive),
            ("internal_import_edges", self.internal_import_edges),
            ("import_cycle_components", self.import_cycle_components),
            ("max_module_fan_out", self.max_module_fan_out),
            ("max_module_dependency_depth", self.max_module_dependency_depth),
            ("ambiguous_internal_dispatches", self.ambiguous_internal_dispatches),
            ("transparent_private_definitions", self.transparent_private_definitions),
            ("thin_single_use_methods", self.thin_single_use_methods),
            ("low_usage_private_definitions", self.low_usage_private_definitions),
            ("linear_private_call_chain_links", self.linear_private_call_chain_links),
            ("production_unreferenced_definitions", self.production_unreferenced_definitions),
            ("low_cohesion_classes", self.low_cohesion_classes),
            ("max_methods_per_class", self.max_methods_per_class),
            ("max_fields_per_class", self.max_fields_per_class),
            ("max_class_components", self.max_class_components),
            ("complexity_hotspots", self.complexity_hotspots),
            ("stateful_async_hotspots", self.stateful_async_hotspots),
            ("unused_private_definitions", self.unused_private_definitions),
            ("unread_private_fields", self.unread_private_fields),
            ("unconsumed_internal_async_calls", self.unconsumed_internal_async_calls),
            ("unowned_internal_coroutine_handles", self.unowned_internal_coroutine_handles),
            ("orphaned_task_handles", self.orphaned_task_handles),
        )


@dataclass(frozen=True, slots=True)
class _Module:
    relative_path: str
    tree: ast.Module


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
    semantic = build_semantic_index(package_root, test_root)
    production_modules = tuple(_Module(module.path, module.tree) for module in semantic.production_modules)
    records = tuple(
        node
        for module in production_modules
        for node in module.tree.body
        if isinstance(node, ast.ClassDef) and _is_dataclass(node)
    )
    logical_clones = normalized_logic_clones(semantic.production_modules)
    statement_clones = normalized_statement_clones(semantic)
    near_clones = near_function_clones(semantic)
    record_shape_clones = matching_record_shapes(semantic.production_modules)
    thin_helper_candidates = thin_single_use_private_functions(semantic)
    private_record_candidates = single_use_private_dataclasses(semantic)
    test_only_candidates = test_only_private_definitions(semantic)
    transparent = transparent_private_definitions(semantic)
    thin_methods = thin_single_use_methods(semantic)
    low_usage_definitions = low_usage_private_definitions(semantic)
    linear_chain_links = linear_private_call_chain_links(semantic)
    unreferenced_definitions = production_unreferenced_definitions(semantic)
    low_cohesion = class_cohesion_candidates(semantic)
    class_metrics = class_structure_metrics(semantic)
    hotspots = complexity_hotspots(semantic)
    async_hotspots = stateful_async_hotspots(semantic)
    ambiguous_dispatches = ambiguous_internal_dispatches(semantic)
    recursive_components = strongly_connected_calls(semantic)
    unused_definitions = unused_private_definitions(semantic)
    unread_fields = unread_private_fields(semantic)
    unconsumed_async_calls = unconsumed_internal_async_calls(semantic)
    unowned_coroutines = unowned_internal_coroutine_handles(semantic)
    orphaned_tasks = orphaned_task_handles(semantic)
    graph_metrics = call_graph_metrics(semantic)
    dependency_metrics = module_dependency_metrics(semantic)
    import_cycles = cyclic_module_dependencies(semantic)

    def definition_site(candidate: CandidateSite) -> DefinitionSite:
        return DefinitionSite(candidate.path, candidate.line, candidate.symbol.qualified_name)

    thin_helpers = tuple(definition_site(candidate) for candidate in thin_helper_candidates)
    single_use_private_records = tuple(definition_site(candidate) for candidate in private_record_candidates)
    test_only_definitions = tuple(definition_site(candidate) for candidate in test_only_candidates)

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
        for node in record.body
    )

    return ComplexitySnapshot(
        top_level_definitions=class_definitions + function_definitions + aliases,
        type_definitions=class_definitions + aliases,
        dataclass_types=len(records),
        dataclass_fields=dataclass_fields,
        decision_points=_decision_points(production_modules),
        logical_clone_pairs=len(logical_clones),
        statement_clone_pairs=len(statement_clones),
        near_clone_pairs=len(near_clones),
        record_shape_clone_pairs=len(record_shape_clones),
        thin_single_use_helpers=len(thin_helpers),
        single_use_private_dataclasses=len(single_use_private_records),
        test_only_private_definitions=len(test_only_definitions),
        function_definitions=len(semantic.complexities),
        method_definitions=sum(item.symbol.kind is SymbolKind.METHOD for item in semantic.complexities),
        nested_function_definitions=sum(
            item.symbol.kind is SymbolKind.NESTED_FUNCTION for item in semantic.complexities
        ),
        semantic_nodes=sum(item.semantic_nodes for item in semantic.complexities),
        cognitive_complexity=sum(item.cognitive for item in semantic.complexities),
        await_points=sum(item.awaits for item in semantic.complexities),
        exception_handlers=sum(item.exception_handlers for item in semantic.complexities),
        attribute_writes=sum(item.attribute_writes for item in semantic.complexities),
        task_creations=sum(item.task_creations for item in semantic.complexities),
        max_cyclomatic_complexity=max((item.cyclomatic for item in semantic.complexities), default=0),
        max_cognitive_complexity=max((item.cognitive for item in semantic.complexities), default=0),
        max_nesting_depth=max((item.max_nesting for item in semantic.complexities), default=0),
        internal_call_edges=graph_metrics.edge_count,
        recursive_call_components=graph_metrics.recursive_components,
        max_call_fan_out=graph_metrics.max_fan_out,
        max_call_chain_depth=graph_metrics.max_chain_depth,
        max_call_chain_cognitive=graph_metrics.max_chain_cognitive,
        internal_import_edges=dependency_metrics.edge_count,
        import_cycle_components=dependency_metrics.cyclic_components,
        max_module_fan_out=dependency_metrics.max_fan_out,
        max_module_dependency_depth=dependency_metrics.max_depth,
        ambiguous_internal_dispatches=len(ambiguous_dispatches),
        transparent_private_definitions=len(transparent),
        thin_single_use_methods=len(thin_methods),
        low_usage_private_definitions=len(low_usage_definitions),
        linear_private_call_chain_links=len(linear_chain_links),
        production_unreferenced_definitions=len(unreferenced_definitions),
        low_cohesion_classes=len(low_cohesion),
        max_methods_per_class=class_metrics.max_methods,
        max_fields_per_class=class_metrics.max_fields,
        max_class_components=class_metrics.max_components,
        complexity_hotspots=len(hotspots),
        stateful_async_hotspots=len(async_hotspots),
        unused_private_definitions=len(unused_definitions),
        unread_private_fields=len(unread_fields),
        unconsumed_internal_async_calls=len(unconsumed_async_calls),
        unowned_internal_coroutine_handles=len(unowned_coroutines),
        orphaned_task_handles=len(orphaned_tasks),
        logical_clones=logical_clones,
        statement_clones=statement_clones,
        near_clones=near_clones,
        record_shape_clones=record_shape_clones,
        thin_helpers=thin_helpers,
        single_use_private_records=single_use_private_records,
        test_only_definitions=test_only_definitions,
        transparent_definitions=transparent,
        thin_methods=thin_methods,
        low_usage_definitions=low_usage_definitions,
        linear_chain_links=linear_chain_links,
        unreferenced_definitions=unreferenced_definitions,
        low_cohesion=low_cohesion,
        hotspots=hotspots,
        async_hotspots=async_hotspots,
        ambiguous_dispatches=ambiguous_dispatches,
        recursive_components=recursive_components,
        import_cycles=import_cycles,
        unused_definitions=unused_definitions,
        unread_fields=unread_fields,
        unconsumed_async_calls=unconsumed_async_calls,
        unowned_coroutines=unowned_coroutines,
        orphaned_tasks=orphaned_tasks,
    )


def _clone_identity(pair: ClonePair) -> str:
    return f"[{pair.kind}] {pair.left.render()} <-> {pair.right.render()}"


def _site_identity(site: DefinitionSite) -> str:
    return site.render()


def _record_clone_identity(pair: ClonePair) -> str:
    return f"{pair.left.render()} <-> {pair.right.render()}"


def health_target_gaps(
    snapshot: ComplexitySnapshot,
    targets: Mapping[str, int],
) -> tuple[tuple[str, int, int], ...]:
    actual = dict(snapshot.metric_items())
    return tuple((name, targets[name], actual[name]) for name in sorted(targets) if actual[name] > targets[name])


def render_complexity_report(
    snapshot: ComplexitySnapshot,
    *,
    health_targets: Mapping[str, int] | None = None,
) -> str:
    lines = ["Structural complexity metrics:"]
    lines.extend(f"  {name}: {value}" for name, value in snapshot.metric_items())

    if health_targets is not None:
        gaps = health_target_gaps(snapshot, health_targets)
        lines.append("")
        lines.append("Zero-debt health target:")
        if gaps:
            lines.append("  FAIL")
            lines.extend(f"  {name}: target={target}, actual={actual}" for name, target, actual in gaps)
        else:
            lines.append("  PASS")

    sections: tuple[tuple[str, Iterable[str]], ...] = (
        (
            "Normalized logical clone candidates:",
            (f"  {_clone_identity(pair)}" for pair in snapshot.logical_clones),
        ),
        (
            "Normalized statement-subtree clone candidates:",
            (f"  {_clone_identity(pair)}" for pair in snapshot.statement_clones),
        ),
        (
            "Near-duplicate function candidates:",
            (
                f"  similarity={pair.similarity}% {pair.left.render()} <-> {pair.right.render()}"
                for pair in snapshot.near_clones
            ),
        ),
        (
            "Dataclasses with matching field shapes:",
            (f"  {_record_clone_identity(pair)}" for pair in snapshot.record_shape_clones),
        ),
        (
            "Thin private helpers used at most once:",
            (f"  {_site_identity(site)}" for site in snapshot.thin_helpers),
        ),
        (
            "Private dataclasses referenced at most once in production:",
            (f"  {_site_identity(site)}" for site in snapshot.single_use_private_records),
        ),
        (
            "Private production definitions referenced only by tests:",
            (f"  {_site_identity(site)}" for site in snapshot.test_only_definitions),
        ),
        (
            "Unused private definitions:",
            (f"  {site.render()}" for site in snapshot.unused_definitions),
        ),
        (
            "Private fields written but never read:",
            (f"  {site.render()}" for site in snapshot.unread_fields),
        ),
        (
            "Internal async calls whose results are discarded:",
            (f"  {violation.render()}" for violation in snapshot.unconsumed_async_calls),
        ),
        (
            "Internal coroutine handles assigned but never consumed:",
            (f"  {violation.render()}" for violation in snapshot.unowned_coroutines),
        ),
        (
            "Created task handles with no structural owner:",
            (f"  {violation.render()}" for violation in snapshot.orphaned_tasks),
        ),
        (
            "Transparent private definitions:",
            (f"  {site.render()}" for site in snapshot.transparent_definitions),
        ),
        (
            "Thin methods and nested functions used at most once:",
            (f"  {site.render()}" for site in snapshot.thin_methods),
        ),
        (
            "Private definitions with at most one proven production use:",
            (f"  {site.render()}" for site in snapshot.low_usage_definitions),
        ),
        (
            "Single-entry/single-exit private call-chain links:",
            (f"  {site.render()}" for site in snapshot.linear_chain_links),
        ),
        (
            "Definitions with no proven production reference:",
            (f"  {site.render()}" for site in snapshot.unreferenced_definitions),
        ),
        (
            "Classes split into multiple method/field components:",
            (
                f"  {candidate.site.render()} methods={candidate.methods} "
                f"fields={candidate.fields} components={candidate.components}"
                for candidate in snapshot.low_cohesion
            ),
        ),
        (
            "Function and method complexity hotspots:",
            (
                "  "
                f"{hotspot.site.render()} "
                f"cc={hotspot.complexity.cyclomatic} "
                f"cognitive={hotspot.complexity.cognitive} "
                f"nesting={hotspot.complexity.max_nesting} "
                f"parameters={hotspot.complexity.parameters} "
                f"nodes={hotspot.complexity.semantic_nodes}"
                for hotspot in snapshot.hotspots
            ),
        ),
        (
            "Async methods that both await and mutate instance state:",
            (
                "  "
                f"{hotspot.site.render()} "
                f"awaits={hotspot.complexity.awaits} "
                f"writes={hotspot.complexity.attribute_writes}"
                for hotspot in snapshot.async_hotspots
            ),
        ),
        (
            "Unresolved dispatches sharing a private internal member name:",
            (f"  {call.path}:{call.line}:{call.column}:{call.expression}" for call in snapshot.ambiguous_dispatches),
        ),
        (
            "Recursive internal call components:",
            (
                "  " + " <-> ".join(symbol.render() for symbol in sorted(component))
                for component in snapshot.recursive_components
            ),
        ),
        (
            "Cyclic internal module dependencies:",
            ("  " + " <-> ".join(sorted(component)) for component in snapshot.import_cycles),
        ),
    )
    for title, entries in sections:
        rendered = tuple(entries)
        lines.append("")
        lines.append(title)
        lines.extend(rendered or ("  none",))
    return "\n".join(lines)


def main() -> int:
    project_root = Path.cwd()
    snapshot = complexity_snapshot(project_root / "src" / "mote_kernel", project_root / "tests")
    health_targets = load_metric_limits(
        project_root,
        table_name="complexity_health",
        expected_names=HEALTH_METRIC_NAMES,
    )
    sys.stdout.write(f"{render_complexity_report(snapshot, health_targets=health_targets)}\n")
    arguments = sys.argv[1:]
    if not arguments:
        return 0
    if arguments != ["--check-health"]:
        sys.stderr.write("usage: python -m tests.architecture.complexity_rules [--check-health]\n")
        return 2
    return int(bool(health_target_gaps(snapshot, health_targets)))


if __name__ == "__main__":
    raise SystemExit(main())
