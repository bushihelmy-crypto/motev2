"""General quality analyses derived from the repository semantic index."""

from __future__ import annotations

import ast
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from tests.architecture.function_analysis import FunctionComplexity
from tests.architecture.semantic_index import SemanticIndex, call_adjacency, strongly_connected_calls
from tests.architecture.semantic_model import (
    CallSite,
    Reference,
    ReferenceKind,
    SymbolDefinition,
    SymbolId,
    SymbolKind,
)

MAX_CYCLOMATIC_COMPLEXITY = 10
MAX_COGNITIVE_COMPLEXITY = 15
MAX_NESTING_DEPTH = 4
MAX_PARAMETER_COUNT = 6
MAX_SEMANTIC_NODES = 150
THIN_DEFINITION_MAX_NODES = 18

_FUNCTION_KINDS = frozenset({SymbolKind.FUNCTION, SymbolKind.METHOD, SymbolKind.NESTED_FUNCTION})


@dataclass(frozen=True, order=True, slots=True)
class CandidateSite:
    path: str
    line: int
    symbol: SymbolId

    def identity(self) -> str:
        return self.symbol.render()

    def render(self) -> str:
        return f"{self.path}:{self.line}:{self.symbol.qualified_name}"


@dataclass(frozen=True, slots=True)
class SymbolUsage:
    definition: SymbolDefinition
    production_references: tuple[Reference, ...]
    test_references: tuple[Reference, ...]
    production_runtime_references: tuple[Reference, ...]
    test_runtime_references: tuple[Reference, ...]
    conservative_production_loads: int
    conservative_test_loads: int


@dataclass(frozen=True, order=True, slots=True)
class ComplexityHotspot:
    site: CandidateSite
    complexity: FunctionComplexity


@dataclass(frozen=True, order=True, slots=True)
class AsyncCallViolation:
    path: str
    line: int
    column: int
    expression: str
    owner: SymbolId | None

    def identity(self) -> str:
        owner = "<module>" if self.owner is None else self.owner.render()
        return f"{owner}:{self.expression}"

    def render(self) -> str:
        return f"{self.path}:{self.line}:{self.column}:{self.expression}"


@dataclass(frozen=True, order=True, slots=True)
class TaskHandleViolation:
    path: str
    line: int
    variable: str | None
    owner: SymbolId | None

    def identity(self) -> str:
        owner = "<module>" if self.owner is None else self.owner.render()
        variable = "<discarded>" if self.variable is None else self.variable
        return f"{owner}:{variable}"

    def render(self) -> str:
        variable = "discarded" if self.variable is None else self.variable
        return f"{self.path}:{self.line}:{variable}"


@dataclass(frozen=True, order=True, slots=True)
class CoroutineHandleViolation:
    path: str
    line: int
    variable: str
    owner: SymbolId | None

    def render(self) -> str:
        return f"{self.path}:{self.line}:{self.variable}"


@dataclass(frozen=True, slots=True)
class CallGraphMetrics:
    edge_count: int
    recursive_components: int
    max_fan_out: int
    max_chain_depth: int
    max_chain_cognitive: int


@dataclass(frozen=True, slots=True)
class ModuleDependencyMetrics:
    edge_count: int
    cyclic_components: int
    max_fan_out: int
    max_depth: int


@dataclass(frozen=True, order=True, slots=True)
class RuntimeModuleCallPair:
    """A production callable dependency between two different modules."""

    source_module: str
    target_module: str
    symbol_edges: int
    call_sites: int

    def render(self) -> str:
        return (
            f"{self.source_module} -> {self.target_module} "
            f"symbol_edges={self.symbol_edges} call_sites={self.call_sites}"
        )


@dataclass(frozen=True, slots=True)
class RuntimeModuleCallMetrics:
    """High-recall coupling facts for resolved production callable calls."""

    edge_count: int
    module_pair_count: int
    max_fan_out: int
    pairs: tuple[RuntimeModuleCallPair, ...]


@dataclass(frozen=True, order=True, slots=True)
class ClassCohesion:
    site: CandidateSite
    methods: int
    fields: int
    components: int


@dataclass(frozen=True, slots=True)
class ClassStructureMetrics:
    max_methods: int
    max_fields: int
    max_components: int


def _site(definition: SymbolDefinition) -> CandidateSite:
    return CandidateSite(definition.path, definition.line, definition.symbol)


def _syntactic_loads(index: SemanticIndex, definition: SymbolDefinition, *, tests: bool) -> int:
    if definition.symbol.kind in {SymbolKind.METHOD, SymbolKind.FIELD}:
        loads = index.test_attribute_loads if tests else index.production_attribute_loads
    else:
        loads = index.test_name_loads if tests else index.production_name_loads
    return loads.get(definition.symbol.name, 0)


def symbol_usages(index: SemanticIndex) -> tuple[SymbolUsage, ...]:
    usages: list[SymbolUsage] = []
    for definition in index.definitions:
        if definition.in_tests:
            continue
        production = index.references_to(definition.symbol, tests=False)
        tests = index.references_to(definition.symbol, tests=True)
        usages.append(
            SymbolUsage(
                definition,
                production,
                tests,
                tuple(
                    reference
                    for reference in production
                    if reference.kind not in {ReferenceKind.ANNOTATION, ReferenceKind.DECORATOR, ReferenceKind.BASE}
                ),
                tuple(
                    reference
                    for reference in tests
                    if reference.kind not in {ReferenceKind.ANNOTATION, ReferenceKind.DECORATOR, ReferenceKind.BASE}
                ),
                _syntactic_loads(index, definition, tests=False),
                _syntactic_loads(index, definition, tests=True),
            )
        )
    return tuple(usages)


def unused_private_definitions(index: SemanticIndex) -> tuple[CandidateSite, ...]:
    return tuple(
        sorted(
            _site(usage.definition)
            for usage in symbol_usages(index)
            if usage.definition.private
            and not usage.definition.implicit
            and not usage.production_references
            and not usage.test_references
            and usage.conservative_production_loads == 0
            and usage.conservative_test_loads == 0
        )
    )


def test_only_private_definitions(index: SemanticIndex) -> tuple[CandidateSite, ...]:
    return tuple(
        sorted(
            _site(usage.definition)
            for usage in symbol_usages(index)
            if usage.definition.private
            and not usage.definition.implicit
            and not usage.production_references
            and bool(usage.test_references)
            and usage.conservative_production_loads == 0
        )
    )


def production_unreferenced_definitions(index: SemanticIndex) -> tuple[CandidateSite, ...]:
    return tuple(
        sorted(
            _site(usage.definition)
            for usage in symbol_usages(index)
            if not usage.definition.implicit
            and usage.definition.symbol.kind in _FUNCTION_KINDS | {SymbolKind.CLASS, SymbolKind.TYPE_ALIAS}
            and not usage.production_runtime_references
            and usage.conservative_production_loads == 0
        )
    )


def low_usage_private_definitions(index: SemanticIndex) -> tuple[CandidateSite, ...]:
    return tuple(
        sorted(
            _site(usage.definition)
            for usage in symbol_usages(index)
            if usage.definition.private
            and not usage.definition.implicit
            and usage.definition.symbol.kind in _FUNCTION_KINDS | {SymbolKind.CLASS}
            and len(usage.production_runtime_references) <= 1
        )
    )


def unread_private_fields(index: SemanticIndex) -> tuple[CandidateSite, ...]:
    return tuple(
        sorted(
            _site(usage.definition)
            for usage in symbol_usages(index)
            if usage.definition.symbol.kind is SymbolKind.FIELD
            and usage.definition.private
            and not usage.definition.implicit
            and any(reference.kind is ReferenceKind.WRITE for reference in usage.production_references)
            and not any(
                reference.kind in {ReferenceKind.READ, ReferenceKind.RUNTIME, ReferenceKind.CALL}
                for reference in usage.production_references
            )
            and index.production_attribute_loads.get(usage.definition.symbol.name, 0) == 0
        )
    )


def _without_docstring(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[ast.stmt, ...]:
    body = tuple(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def _call_expression(node: ast.expr | None) -> ast.Call | None:
    if isinstance(node, ast.Call):
        return node
    if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
        return node.value
    return None


def _is_transparent(definition: SymbolDefinition) -> bool:
    if definition.symbol.kind not in _FUNCTION_KINDS or definition.implicit:
        return False
    node = cast(ast.FunctionDef | ast.AsyncFunctionDef, definition.node)
    body = _without_docstring(node)
    if len(body) != 1:
        return False
    statement = body[0]
    if isinstance(statement, ast.Return):
        return _call_expression(statement.value) is not None
    if isinstance(statement, ast.Expr):
        return _call_expression(statement.value) is not None
    return False


def transparent_private_definitions(index: SemanticIndex) -> tuple[CandidateSite, ...]:
    return tuple(
        sorted(
            _site(definition)
            for definition in index.definitions
            if not definition.in_tests and definition.private and _is_transparent(definition)
        )
    )


def thin_single_use_methods(index: SemanticIndex) -> tuple[CandidateSite, ...]:
    calls = Counter(
        call.target
        for call in index.calls
        if not call.in_tests and call.target is not None and call.target.kind in _FUNCTION_KINDS
    )
    complexity = {item.symbol: item for item in index.complexities}
    return tuple(
        sorted(
            _site(definition)
            for definition in index.definitions
            if not definition.in_tests
            and definition.symbol.kind in {SymbolKind.METHOD, SymbolKind.NESTED_FUNCTION}
            and definition.private
            and not definition.implicit
            and calls[definition.symbol] <= 1
            and complexity[definition.symbol].semantic_nodes <= THIN_DEFINITION_MAX_NODES
        )
    )


def thin_single_use_private_functions(index: SemanticIndex) -> tuple[CandidateSite, ...]:
    complexity = {item.symbol: item for item in index.complexities}
    return tuple(
        sorted(
            _site(usage.definition)
            for usage in symbol_usages(index)
            if usage.definition.symbol.kind is SymbolKind.FUNCTION
            and usage.definition.private
            and not usage.definition.implicit
            and len(usage.production_runtime_references) <= 1
            and complexity[usage.definition.symbol].semantic_nodes <= THIN_DEFINITION_MAX_NODES
        )
    )


def _is_dataclass(definition: SymbolDefinition) -> bool:
    if not isinstance(definition.node, ast.ClassDef):
        return False
    return any(
        (
            isinstance(target := decorator.func if isinstance(decorator, ast.Call) else decorator, ast.Name)
            and target.id == "dataclass"
        )
        or (isinstance(target, ast.Attribute) and target.attr == "dataclass")
        for decorator in definition.node.decorator_list
    )


def single_use_private_dataclasses(index: SemanticIndex) -> tuple[CandidateSite, ...]:
    return tuple(
        sorted(
            _site(usage.definition)
            for usage in symbol_usages(index)
            if usage.definition.symbol.kind is SymbolKind.CLASS
            and usage.definition.private
            and _is_dataclass(usage.definition)
            and len(usage.production_runtime_references) <= 1
        )
    )


def complexity_hotspots(index: SemanticIndex) -> tuple[ComplexityHotspot, ...]:
    definitions = {definition.symbol: definition for definition in index.definitions if not definition.in_tests}
    return tuple(
        sorted(
            ComplexityHotspot(_site(definitions[item.symbol]), item)
            for item in index.complexities
            if item.cyclomatic > MAX_CYCLOMATIC_COMPLEXITY
            or item.cognitive > MAX_COGNITIVE_COMPLEXITY
            or item.max_nesting > MAX_NESTING_DEPTH
            or item.parameters > MAX_PARAMETER_COUNT
            or item.semantic_nodes > MAX_SEMANTIC_NODES
        )
    )


def stateful_async_hotspots(index: SemanticIndex) -> tuple[ComplexityHotspot, ...]:
    definitions = {definition.symbol: definition for definition in index.definitions if not definition.in_tests}
    return tuple(
        sorted(
            ComplexityHotspot(_site(definitions[item.symbol]), item)
            for item in index.complexities
            if item.awaits > 0 and item.attribute_writes > 0
        )
    )


def class_cohesion_candidates(index: SemanticIndex) -> tuple[ClassCohesion, ...]:
    definitions = {definition.symbol: definition for definition in index.definitions if not definition.in_tests}
    candidates: list[ClassCohesion] = []
    for owner, definition in definitions.items():
        if owner.kind is not SymbolKind.CLASS or definition.implicit:
            continue
        methods = tuple(
            symbol
            for symbol, member in definitions.items()
            if member.parent == owner and symbol.kind is SymbolKind.METHOD and not member.implicit
        )
        if len(methods) < 4:
            continue
        fields = frozenset(
            symbol
            for symbol, member in definitions.items()
            if member.parent == owner and symbol.kind is SymbolKind.FIELD
        )
        method_set = frozenset(methods)
        connections: defaultdict[SymbolId, set[SymbolId]] = defaultdict(set)
        field_users: defaultdict[SymbolId, set[SymbolId]] = defaultdict(set)
        for reference in index.references:
            if reference.in_tests or reference.source not in method_set:
                continue
            if reference.target in method_set:
                connections[reference.source].add(reference.target)
                connections[reference.target].add(reference.source)
            elif reference.target in fields:
                field_users[reference.target].add(reference.source)
        for users in field_users.values():
            for method in users:
                connections[method].update(users - {method})

        remaining = set(methods)
        components = 0
        while remaining:
            components += 1
            pending = [remaining.pop()]
            while pending:
                current = pending.pop()
                reached = connections[current] & remaining
                remaining.difference_update(reached)
                pending.extend(reached)
        if components > 1:
            candidates.append(ClassCohesion(_site(definition), len(methods), len(fields), components))
    return tuple(sorted(candidates))


def class_structure_metrics(index: SemanticIndex) -> ClassStructureMetrics:
    method_counts: Counter[SymbolId] = Counter()
    field_counts: Counter[SymbolId] = Counter()
    for definition in index.definitions:
        if definition.in_tests or definition.parent is None:
            continue
        if definition.symbol.kind is SymbolKind.METHOD and not definition.implicit:
            method_counts[definition.parent] += 1
        elif definition.symbol.kind is SymbolKind.FIELD:
            field_counts[definition.parent] += 1
    cohesion = class_cohesion_candidates(index)
    return ClassStructureMetrics(
        max(method_counts.values(), default=0),
        max(field_counts.values(), default=0),
        max((candidate.components for candidate in cohesion), default=1),
    )


def ambiguous_internal_dispatches(index: SemanticIndex) -> tuple[CallSite, ...]:
    private_methods: defaultdict[str, set[SymbolId]] = defaultdict(set)
    for definition in index.definitions:
        if (
            not definition.in_tests
            and definition.private
            and definition.symbol.kind in {SymbolKind.METHOD, SymbolKind.NESTED_FUNCTION}
        ):
            private_methods[definition.symbol.name].add(definition.symbol)
    return tuple(
        call
        for call in index.calls
        if not call.in_tests
        and call.target is None
        and "." in call.expression
        and call.expression.rsplit(".", maxsplit=1)[-1] in private_methods
    )


def linear_private_call_chain_links(index: SemanticIndex) -> tuple[CandidateSite, ...]:
    definitions = {definition.symbol: definition for definition in index.definitions if not definition.in_tests}
    incoming = Counter(
        call.target
        for call in index.calls
        if not call.in_tests and call.source is not None and call.target in definitions
    )
    adjacency = call_adjacency(index)
    return tuple(
        sorted(
            _site(definition)
            for symbol, definition in definitions.items()
            if definition.private
            and not definition.implicit
            and symbol.kind in _FUNCTION_KINDS
            and incoming[symbol] <= 1
            and len(adjacency.get(symbol, frozenset())) == 1
        )
    )


def _parent_nodes(tree: ast.AST) -> Mapping[int, ast.AST]:
    return {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _call_nodes(tree: ast.AST) -> Mapping[tuple[int, int], ast.Call]:
    return {(node.lineno, node.col_offset): node for node in ast.walk(tree) if isinstance(node, ast.Call)}


def _is_consumed_call(call: ast.Call, parents: Mapping[int, ast.AST]) -> bool:
    current: ast.AST = call
    while (parent := parents.get(id(current))) is not None:
        if isinstance(
            parent, ast.Await | ast.Return | ast.Yield | ast.YieldFrom | ast.Assign | ast.AnnAssign | ast.NamedExpr
        ):
            return True
        if isinstance(parent, ast.Call):
            return current is not parent.func
        if isinstance(parent, ast.Expr | ast.stmt):
            return False
        current = parent
    return False


def unconsumed_internal_async_calls(index: SemanticIndex) -> tuple[AsyncCallViolation, ...]:
    definitions = {definition.symbol: definition for definition in index.definitions if not definition.in_tests}
    calls = {
        (call.path, call.line, call.column): call
        for call in index.calls
        if not call.in_tests
        and call.target in definitions
        and isinstance(definitions[call.target].node, ast.AsyncFunctionDef)
    }
    violations: list[AsyncCallViolation] = []
    for module in index.production_modules:
        parents = _parent_nodes(module.tree)
        for coordinate, node in _call_nodes(module.tree).items():
            call = calls.get((module.path, *coordinate))
            if call is not None and not _is_consumed_call(node, parents):
                violations.append(
                    AsyncCallViolation(
                        call.path,
                        call.line,
                        call.column,
                        call.expression,
                        call.source,
                    )
                )
    return tuple(sorted(violations))


def unowned_internal_coroutine_handles(index: SemanticIndex) -> tuple[CoroutineHandleViolation, ...]:
    definitions = {definition.symbol: definition for definition in index.definitions if not definition.in_tests}
    calls = {
        (call.path, call.line, call.column): call
        for call in index.calls
        if not call.in_tests
        and call.target in definitions
        and isinstance(definitions[call.target].node, ast.AsyncFunctionDef)
    }
    violations: list[CoroutineHandleViolation] = []
    for module in index.production_modules:
        parents = _parent_nodes(module.tree)
        for coordinate, node in _call_nodes(module.tree).items():
            call = calls.get((module.path, *coordinate))
            assignment = _local_assignment(node, parents)
            if call is None or assignment is None:
                continue
            variable, function = assignment
            used = any(
                isinstance(candidate, ast.Name) and isinstance(candidate.ctx, ast.Load) and candidate.id == variable
                for candidate in ast.walk(function)
            )
            if not used:
                violations.append(CoroutineHandleViolation(module.path, node.lineno, variable, call.source))
    return tuple(sorted(violations))


def _task_creation(node: ast.Call) -> bool:
    if isinstance(node.func, ast.Name):
        name = node.func.id
    elif isinstance(node.func, ast.Attribute):
        name = node.func.attr
    else:
        return False
    return name in {"create_task", "ensure_future"}


def _enclosing_function(node: ast.AST, parents: Mapping[int, ast.AST]) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    current = node
    while (parent := parents.get(id(current))) is not None:
        if isinstance(parent, ast.FunctionDef | ast.AsyncFunctionDef):
            return parent
        current = parent
    return None


def _local_assignment(
    call: ast.Call,
    parents: Mapping[int, ast.AST],
) -> tuple[str, ast.FunctionDef | ast.AsyncFunctionDef] | None:
    parent = parents.get(id(call))
    target: ast.expr | None = None
    if isinstance(parent, ast.Assign) and len(parent.targets) == 1:
        target = parent.targets[0]
    elif isinstance(parent, ast.AnnAssign):
        target = parent.target
    function = _enclosing_function(call, parents)
    if not isinstance(target, ast.Name) or function is None:
        return None
    return target.id, function


def orphaned_task_handles(index: SemanticIndex) -> tuple[TaskHandleViolation, ...]:
    owner_by_site = {(call.path, call.line, call.column): call.source for call in index.calls if not call.in_tests}
    violations: list[TaskHandleViolation] = []
    for module in index.production_modules:
        parents = _parent_nodes(module.tree)
        for coordinate, call in _call_nodes(module.tree).items():
            if not _task_creation(call):
                continue
            parent = parents.get(id(call))
            owner = owner_by_site.get((module.path, *coordinate))
            if isinstance(parent, ast.Expr):
                violations.append(TaskHandleViolation(module.path, call.lineno, None, owner))
                continue
            assignment = _local_assignment(call, parents)
            if assignment is None:
                continue
            variable, function = assignment
            used = any(
                isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id == variable
                for node in ast.walk(function)
            )
            if not used:
                violations.append(TaskHandleViolation(module.path, call.lineno, variable, owner))
    return tuple(sorted(violations))


def call_graph_metrics(index: SemanticIndex) -> CallGraphMetrics:
    adjacency = call_adjacency(index)
    nodes = frozenset((*adjacency, *(target for targets in adjacency.values() for target in targets)))
    recursive = strongly_connected_calls(index)
    component_by_node: dict[SymbolId, int] = {}
    components: list[frozenset[SymbolId]] = list(recursive)
    for position, component in enumerate(components):
        for node in component:
            component_by_node[node] = position
    for node in sorted(nodes):
        if node not in component_by_node:
            component_by_node[node] = len(components)
            components.append(frozenset((node,)))

    condensed: defaultdict[int, set[int]] = defaultdict(set)
    for source, targets in adjacency.items():
        source_component = component_by_node[source]
        for target in targets:
            target_component = component_by_node[target]
            if source_component != target_component:
                condensed[source_component].add(target_component)

    cognitive = {item.symbol: item.cognitive for item in index.complexities}
    weights = tuple(sum(cognitive.get(node, 0) for node in component) for component in components)
    depth_cache: dict[int, int] = {}
    burden_cache: dict[int, int] = {}

    def depth(component: int) -> int:
        if component not in depth_cache:
            depth_cache[component] = 1 + max((depth(target) for target in condensed[component]), default=0)
        return depth_cache[component]

    def burden(component: int) -> int:
        if component not in burden_cache:
            burden_cache[component] = weights[component] + max(
                (burden(target) for target in condensed[component]),
                default=0,
            )
        return burden_cache[component]

    return CallGraphMetrics(
        edge_count=sum(len(targets) for targets in adjacency.values()),
        recursive_components=len(recursive),
        max_fan_out=max((len(targets) for targets in adjacency.values()), default=0),
        max_chain_depth=max((depth(component) for component in range(len(components))), default=0),
        max_chain_cognitive=max((burden(component) for component in range(len(components))), default=0),
    )


def runtime_module_call_metrics(index: SemanticIndex) -> RuntimeModuleCallMetrics:
    """Measure resolved callable calls that cross production module boundaries.

    The metric intentionally follows the same conservative resolution policy as
    the call graph, while retaining every resolved runtime-capable target:
    functions, methods, nested functions, classes, and ``NewType`` aliases.
    Unresolved dynamic dispatch remains outside the count and is reported by
    the separate dispatch detector.
    """

    pair_edges: defaultdict[tuple[str, str], set[tuple[SymbolId, SymbolId]]] = defaultdict(set)
    pair_sites: Counter[tuple[str, str]] = Counter()
    target_modules: defaultdict[str, set[str]] = defaultdict(set)
    runtime_kinds = frozenset(
        {
            SymbolKind.CLASS,
            SymbolKind.FUNCTION,
            SymbolKind.METHOD,
            SymbolKind.NESTED_FUNCTION,
            SymbolKind.TYPE_ALIAS,
        }
    )

    for call in index.calls:
        if (
            call.in_tests
            or call.source is None
            or call.target is None
            or call.target.kind not in runtime_kinds
            or call.source.module == call.target.module
        ):
            continue
        pair = (call.source.module, call.target.module)
        pair_edges[pair].add((call.source, call.target))
        pair_sites[pair] += 1
        target_modules[call.source.module].add(call.target.module)

    pairs = tuple(
        RuntimeModuleCallPair(source, target, len(pair_edges[(source, target)]), pair_sites[(source, target)])
        for source, target in sorted(pair_edges)
    )
    return RuntimeModuleCallMetrics(
        edge_count=sum(len(edges) for edges in pair_edges.values()),
        module_pair_count=len(pairs),
        max_fan_out=max((len(targets) for targets in target_modules.values()), default=0),
        pairs=pairs,
    )


def _absolute_import(module: str, path: str, imported: str | None, level: int) -> str:
    if level == 0:
        return imported or ""
    package = module if path.endswith("/__init__.py") or path == "__init__.py" else module.rpartition(".")[0]
    parts = package.split(".") if package else []
    retained = parts[: max(0, len(parts) - level + 1)]
    return ".".join((*retained, *((imported or "").split(".") if imported else ())))


def _internal_import_adjacency(index: SemanticIndex) -> Mapping[str, frozenset[str]]:
    known = frozenset(module.name for module in index.production_modules)
    ordered_known = tuple(sorted(known, key=len, reverse=True))
    adjacency: defaultdict[str, set[str]] = defaultdict(set)
    for module in index.production_modules:
        for node in ast.walk(module.tree):
            candidates: tuple[str, ...]
            if isinstance(node, ast.Import):
                candidates = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = _absolute_import(module.name, module.path, node.module, node.level)
                children = tuple(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
                candidates = (*children, base)
            else:
                continue
            for candidate in candidates:
                target = next(
                    (name for name in ordered_known if candidate == name or candidate.startswith(f"{name}.")),
                    None,
                )
                if target is not None and target != module.name:
                    adjacency[module.name].add(target)
    return {source: frozenset(targets) for source, targets in adjacency.items()}


def _module_components(adjacency: Mapping[str, frozenset[str]]) -> tuple[frozenset[str], ...]:
    nodes = frozenset((*adjacency, *(target for targets in adjacency.values() for target in targets)))
    serial = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    active: set[str] = set()
    components: list[frozenset[str]] = []

    def connect(node: str) -> None:
        nonlocal serial
        indices[node] = lowlinks[node] = serial
        serial += 1
        stack.append(node)
        active.add(node)
        for target in adjacency.get(node, frozenset()):
            if target not in indices:
                connect(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in active:
                lowlinks[node] = min(lowlinks[node], indices[target])
        if lowlinks[node] != indices[node]:
            return
        component: set[str] = set()
        while True:
            member = stack.pop()
            active.remove(member)
            component.add(member)
            if member == node:
                break
        components.append(frozenset(component))

    for node in sorted(nodes):
        if node not in indices:
            connect(node)
    return tuple(components)


def cyclic_module_dependencies(index: SemanticIndex) -> tuple[frozenset[str], ...]:
    adjacency = _internal_import_adjacency(index)
    return tuple(
        sorted(
            (component for component in _module_components(adjacency) if len(component) > 1),
            key=lambda component: tuple(sorted(component)),
        )
    )


def module_dependency_metrics(index: SemanticIndex) -> ModuleDependencyMetrics:
    adjacency = _internal_import_adjacency(index)
    components = _module_components(adjacency)
    component_by_node = {node: position for position, component in enumerate(components) for node in component}
    condensed: defaultdict[int, set[int]] = defaultdict(set)
    for source, targets in adjacency.items():
        for target in targets:
            source_component = component_by_node[source]
            target_component = component_by_node[target]
            if source_component != target_component:
                condensed[source_component].add(target_component)

    cache: dict[int, int] = {}

    def depth(component: int) -> int:
        if component not in cache:
            cache[component] = 1 + max((depth(target) for target in condensed[component]), default=0)
        return cache[component]

    return ModuleDependencyMetrics(
        edge_count=sum(len(targets) for targets in adjacency.values()),
        cyclic_components=sum(len(component) > 1 for component in components),
        max_fan_out=max((len(targets) for targets in adjacency.values()), default=0),
        max_depth=max((depth(component) for component in range(len(components))), default=0),
    )


__all__ = [
    "MAX_COGNITIVE_COMPLEXITY",
    "MAX_CYCLOMATIC_COMPLEXITY",
    "MAX_NESTING_DEPTH",
    "MAX_PARAMETER_COUNT",
    "MAX_SEMANTIC_NODES",
    "AsyncCallViolation",
    "CallGraphMetrics",
    "CandidateSite",
    "ClassCohesion",
    "ClassStructureMetrics",
    "ComplexityHotspot",
    "CoroutineHandleViolation",
    "ModuleDependencyMetrics",
    "RuntimeModuleCallMetrics",
    "RuntimeModuleCallPair",
    "SymbolUsage",
    "TaskHandleViolation",
    "ambiguous_internal_dispatches",
    "call_graph_metrics",
    "class_cohesion_candidates",
    "class_structure_metrics",
    "complexity_hotspots",
    "cyclic_module_dependencies",
    "linear_private_call_chain_links",
    "low_usage_private_definitions",
    "module_dependency_metrics",
    "orphaned_task_handles",
    "production_unreferenced_definitions",
    "runtime_module_call_metrics",
    "single_use_private_dataclasses",
    "stateful_async_hotspots",
    "symbol_usages",
    "test_only_private_definitions",
    "thin_single_use_methods",
    "thin_single_use_private_functions",
    "transparent_private_definitions",
    "unconsumed_internal_async_calls",
    "unowned_internal_coroutine_handles",
    "unread_private_fields",
    "unused_private_definitions",
]
