"""Deterministic compiler for named value bindings and control topology."""

from typing import TypeAlias, TypeVar

from mote_kernel.execution.errors import (
    DuplicateBoundaryError,
    DuplicateEdgeError,
    GraphValidationError,
    MissingEntryError,
    UnknownNodeError,
    UnreachableNodeError,
)
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.definition import GraphDefinition, NestedGraphNodeDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge, JoinEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.ports import (
    DefinitionScope,
    FrameDescriptor,
    FrameDescriptorIdentity,
    FrameKind,
    GraphInputPort,
    GraphInputRef,
    GraphOutputBinding,
    GraphOutputBindings,
    GraphOutputPort,
    MaterializationPlan,
    NodeInputPort,
    NodeOutputPort,
    NodeOutputRef,
    NominalTypeDescriptor,
    OutputDeclaration,
    OutputDeclarations,
    PublicationSelection,
    PublicationSelectionKind,
    ResolvedInputBinding,
    ResolvedInputBindings,
    ResolvedValueSource,
)
from mote_kernel.execution.graph.topology import (
    CompiledGraph,
    DataTriggerPlan,
    FrontierTransitionPlan,
    frozen_map,
)
from mote_kernel.execution.graph.validation import validate_graph
from mote_kernel.state.graph_state import GraphNodeId, GraphRouteId

GraphValueT = TypeVar("GraphValueT")
RouteRequirements: TypeAlias = tuple[tuple[GraphNodeId, frozenset[GraphRouteId]], ...]
RouteCause: TypeAlias = tuple[GraphNodeId, GraphRouteId | None]
ActivationGate: TypeAlias = tuple[RouteCause, ...]


def _declaration(
    declarations: OutputDeclarations[GraphValueT],
    name: str,
    *,
    owner: str,
) -> OutputDeclaration[GraphValueT]:
    for declaration in declarations.entries:
        if declaration.name == name:
            return declaration
    raise GraphValidationError(f"{owner} references unknown output port {name!r}")


def _nested_outputs(graph: CompiledGraph[GraphValueT]) -> OutputDeclarations[GraphValueT]:
    return OutputDeclarations(
        tuple(
            OutputDeclaration(binding.destination.boundary_name, binding.descriptor)
            for binding in graph.transition.graph_outputs.entries
        )
    )


def _frame_descriptor(
    definition: GraphDefinition[GraphValueT],
    kind: FrameKind,
    ordinal: int,
    declarations: OutputDeclarations[GraphValueT],
) -> FrameDescriptor[GraphValueT]:
    return FrameDescriptor(
        FrameDescriptorIdentity(definition.definition_id, definition.version, kind, ordinal),
        declarations,
    )


def _collect_graph_inputs(
    definition: GraphDefinition[GraphValueT],
) -> OutputDeclarations[GraphValueT]:
    descriptors: dict[str, NominalTypeDescriptor[GraphValueT]] = {}
    refs = [
        binding.source
        for node in definition.nodes
        for binding in node.inputs.entries
        if isinstance(binding.source, GraphInputRef)
    ]
    refs.extend(output.source for output in definition.outputs.entries if isinstance(output.source, GraphInputRef))
    for ref in refs:
        existing = descriptors.get(ref.name)
        if existing is not None and existing.value_type is not ref.descriptor.value_type:
            raise GraphValidationError(f"graph input {ref.name!r} has conflicting exact type declarations")
        descriptors[ref.name] = ref.descriptor
    return OutputDeclarations(
        tuple(OutputDeclaration(name, descriptor) for name, descriptor in sorted(descriptors.items()))
    )


def _resolve_source(
    source: GraphInputRef[GraphValueT] | NodeOutputRef,
    *,
    scope: DefinitionScope,
    graph_inputs: OutputDeclarations[GraphValueT],
    node_outputs: dict[GraphNodeId, OutputDeclarations[GraphValueT]],
    consumer: GraphNodeId | None,
) -> tuple[ResolvedValueSource, NominalTypeDescriptor[GraphValueT]]:
    if isinstance(source, GraphInputRef):
        declaration = _declaration(graph_inputs, source.name, owner="graph input binding")
        return GraphInputPort(scope, source.name), declaration.descriptor
    if consumer is not None and source.node_id == consumer:
        raise GraphValidationError(f"node {consumer!r} cannot bind its own output")
    outputs = node_outputs.get(source.node_id)
    if outputs is None:
        raise UnknownNodeError(f"value source references unknown node {source.node_id!r}")
    declaration = _declaration(outputs, source.output_name, owner=f"node {source.node_id!r}")
    return NodeOutputPort(scope, source.node_id, source.output_name), declaration.descriptor


def _data_cycle(data_dependencies: dict[GraphNodeId, set[GraphNodeId]]) -> bool:
    visiting: set[GraphNodeId] = set()
    visited: set[GraphNodeId] = set()

    def visit(node_id: GraphNodeId) -> bool:
        if node_id in visiting:
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        if any(visit(source) for source in data_dependencies[node_id]):
            return True
        visiting.remove(node_id)
        visited.add(node_id)
        return False

    return any(visit(node_id) for node_id in sorted(data_dependencies))


def _reachable(
    entries: tuple[GraphNodeId, ...],
    successors: dict[GraphNodeId, set[GraphNodeId]],
    joins: tuple[JoinEdge, ...],
) -> frozenset[GraphNodeId]:
    reached: set[GraphNodeId] = set(entries)
    changed = True
    while changed:
        changed = False
        for source in tuple(sorted(reached)):
            before = len(reached)
            reached.update(successors[source])
            changed = changed or len(reached) != before
        for join in joins:
            if join.target != END and set(join.sources) <= reached and join.target not in reached:
                reached.add(join.target)
                changed = True
    return frozenset(reached)


def _can_reach(
    source: GraphNodeId,
    target: GraphNodeId,
    successors: dict[GraphNodeId, set[GraphNodeId]],
) -> bool:
    pending = [source]
    visited: set[GraphNodeId] = set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(sorted(successors[current], reverse=True))
    return False


def _guaranteed_sets(
    node_ids: tuple[GraphNodeId, ...],
    entries: tuple[GraphNodeId, ...],
    control_gates: dict[GraphNodeId, list[frozenset[GraphNodeId]]],
    data_dependencies: dict[GraphNodeId, set[GraphNodeId]],
) -> dict[GraphNodeId, frozenset[GraphNodeId]]:
    guarantees = {node_id: frozenset((node_id,)) for node_id in node_ids}
    entry_set = frozenset(entries)
    while True:
        replacements: dict[GraphNodeId, frozenset[GraphNodeId]] = {}
        for node_id in node_ids:
            alternatives: list[frozenset[GraphNodeId]] = []
            if node_id in entry_set:
                alternatives.append(frozenset())
            gates = control_gates[node_id]
            if gates:
                for gate in gates:
                    guaranteed: set[GraphNodeId] = set()
                    for source in gate:
                        guaranteed.update(guarantees[source])
                    alternatives.append(frozenset(guaranteed))
            elif data_dependencies[node_id]:
                guaranteed = set()
                for source in data_dependencies[node_id]:
                    guaranteed.update(guarantees[source])
                    guaranteed.add(source)
                alternatives.append(frozenset(guaranteed))
            common: set[GraphNodeId] = set(alternatives[0]) if alternatives else set()
            for alternative in alternatives[1:]:
                common.intersection_update(alternative)
            replacements[node_id] = frozenset((*common, node_id))
        if replacements == guarantees:
            return guarantees
        guarantees = replacements


def _terminal_gates(
    node_ids: tuple[GraphNodeId, ...],
    control_gates_to_end: tuple[frozenset[GraphNodeId], ...],
    successors: dict[GraphNodeId, set[GraphNodeId]],
) -> tuple[frozenset[GraphNodeId], ...]:
    explicit = frozenset(control_gates_to_end)
    explicit_sources = frozenset(source for gate in explicit for source in gate)
    natural = (
        frozenset((node_id,)) for node_id in node_ids if not successors[node_id] and node_id not in explicit_sources
    )
    return tuple(sorted((*explicit, *natural), key=lambda gate: tuple(sorted(gate))))


def _terminal_guarantees(
    guarantees: dict[GraphNodeId, frozenset[GraphNodeId]],
    terminal_gates: tuple[frozenset[GraphNodeId], ...],
) -> frozenset[GraphNodeId]:
    if not terminal_gates:
        return frozenset()
    alternatives: list[frozenset[GraphNodeId]] = []
    for gate in terminal_gates:
        guaranteed: set[GraphNodeId] = set()
        for source in gate:
            guaranteed.update(guarantees[source])
        alternatives.append(frozenset(guaranteed))
    common = set(alternatives[0])
    for alternative in alternatives[1:]:
        common.intersection_update(alternative)
    return frozenset(common)


def _merge_route_requirements(
    requirements: tuple[RouteRequirements, ...],
) -> RouteRequirements | None:
    merged: dict[GraphNodeId, frozenset[GraphRouteId]] = {}
    for requirement in requirements:
        for source, routes in requirement:
            existing = merged.get(source)
            compatible = routes if existing is None else existing & routes
            if not compatible:
                return None
            merged[source] = compatible
    return tuple(sorted(merged.items()))


def _source_route_requirements(
    source: GraphNodeId,
    selected_route: GraphRouteId | None,
    requirements: dict[GraphNodeId, RouteRequirements],
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
) -> RouteRequirements:
    source_requirements = dict(requirements[source])
    declared_routes = frozenset(conditional_targets[source])
    if selected_route is None and not declared_routes:
        return tuple(sorted(source_requirements.items()))
    selected = frozenset((selected_route,)) if selected_route is not None else declared_routes
    existing = source_requirements.get(source)
    source_requirements[source] = selected if existing is None else existing & selected
    return tuple(sorted(source_requirements.items()))


def _gate_route_requirements(
    gate: ActivationGate,
    requirements: dict[GraphNodeId, RouteRequirements],
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
) -> RouteRequirements | None:
    sources = tuple(
        _source_route_requirements(
            source,
            route,
            requirements,
            conditional_targets,
        )
        for source, route in gate
    )
    return _merge_route_requirements(sources)


def _alternative_route_requirements(
    alternatives: tuple[RouteRequirements | None, ...],
) -> RouteRequirements | None:
    satisfiable = tuple(alternative for alternative in alternatives if alternative is not None)
    if not satisfiable:
        return None
    by_alternative = tuple(dict(alternative) for alternative in satisfiable)
    common_sources = set(by_alternative[0])
    for alternative in by_alternative[1:]:
        common_sources.intersection_update(alternative)
    combined: list[tuple[GraphNodeId, frozenset[GraphRouteId]]] = []
    for source in sorted(common_sources):
        routes: set[GraphRouteId] = set()
        for alternative in by_alternative:
            routes.update(alternative[source])
        combined.append((source, frozenset(routes)))
    return tuple(combined)


def _validate_joint_activation_paths(
    node_ids: tuple[GraphNodeId, ...],
    entries: tuple[GraphNodeId, ...],
    activation_gates: dict[GraphNodeId, list[ActivationGate]],
    data_dependencies: dict[GraphNodeId, set[GraphNodeId]],
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
    joins: tuple[JoinEdge, ...],
) -> None:
    dependency_successors = {node_id: set[GraphNodeId]() for node_id in node_ids}
    for target in node_ids:
        for gate in activation_gates[target]:
            for source, _route in gate:
                dependency_successors[source].add(target)
        for source in data_dependencies[target]:
            dependency_successors[source].add(target)
    variable = _cycle_reachable_nodes(node_ids, dependency_successors)
    fixed = frozenset(node_id for node_id in node_ids if node_id not in variable)
    dependencies = {
        node_id: frozenset(source for gate in activation_gates[node_id] for source, _route in gate if source in fixed)
        | frozenset(source for source in data_dependencies[node_id] if source in fixed)
        for node_id in fixed
    }
    pending = set(fixed)
    ordered: list[GraphNodeId] = []
    while pending:
        ready = tuple(sorted(node_id for node_id in pending if dependencies[node_id] <= set(ordered)))
        ordered.extend(ready)
        pending.difference_update(ready)

    entry_set = frozenset(entries)
    entry_requirements: RouteRequirements = tuple(
        (entry, frozenset(conditional_targets[entry])) for entry in entries if conditional_targets[entry]
    )
    requirements: dict[GraphNodeId, RouteRequirements] = {}
    for node_id in ordered:
        alternatives: list[RouteRequirements | None] = []
        if node_id in entry_set:
            alternatives.append(entry_requirements)
        alternatives.extend(
            _gate_route_requirements(gate, requirements, conditional_targets) for gate in activation_gates[node_id]
        )
        data_requirement = _gate_route_requirements(
            tuple((source, None) for source in sorted(data_dependencies[node_id])),
            requirements,
            conditional_targets,
        )
        if data_dependencies[node_id]:
            if alternatives:
                alternatives = [
                    None
                    if alternative is None or data_requirement is None
                    else _merge_route_requirements((alternative, data_requirement))
                    for alternative in alternatives
                ]
            else:
                alternatives.append(data_requirement)
        requirement = _alternative_route_requirements(tuple(alternatives))
        if requirement is None:
            raise GraphValidationError(f"node {node_id!r} has no jointly satisfiable activation path")
        requirements[node_id] = requirement

    for join in joins:
        if any(source in variable for source in join.sources) or (join.target != END and join.target in variable):
            continue
        gate = tuple((source, None) for source in join.sources)
        source_requirements = tuple(
            _source_route_requirements(source, None, requirements, conditional_targets) for source in join.sources
        )
        if _gate_route_requirements(gate, requirements, conditional_targets) is None:
            raise GraphValidationError(
                f"join {join.sources!r} -> {join.target!r} has mutually exclusive activation sources"
            )
        if len(set(source_requirements)) != 1:
            raise GraphValidationError(
                f"join {join.sources!r} -> {join.target!r} can receive only a partial source set on a route"
            )


def _cycle_reachable_nodes(
    node_ids: tuple[GraphNodeId, ...],
    successors: dict[GraphNodeId, set[GraphNodeId]],
) -> frozenset[GraphNodeId]:
    cycle_nodes = {
        node_id
        for node_id in node_ids
        if any(_can_reach(successor, node_id, successors) for successor in successors[node_id])
    }
    reached = set(cycle_nodes)
    pending = sorted(cycle_nodes)
    while pending:
        source = pending.pop()
        for target in sorted(successors[source]):
            if target not in reached:
                reached.add(target)
                pending.append(target)
    return frozenset(reached)


def _absolute_activation_levels(
    node_ids: tuple[GraphNodeId, ...],
    entries: tuple[GraphNodeId, ...],
    successors: dict[GraphNodeId, set[GraphNodeId]],
) -> dict[GraphNodeId, int]:
    variable = _cycle_reachable_nodes(node_ids, successors)
    levels = {node_id: set[int]() for node_id in node_ids}
    for entry in entries:
        if entry not in variable:
            levels[entry].add(0)
    while True:
        changed = False
        for source in node_ids:
            for target in sorted(successors[source]):
                if target in variable:
                    continue
                before = len(levels[target])
                levels[target].update(level + 1 for level in levels[source])
                changed = changed or before != len(levels[target])
        if not changed:
            break
    return {node_id: next(iter(candidates)) for node_id, candidates in levels.items() if len(candidates) == 1}


def _input_publication_selection(
    source: NodeOutputPort,
    target: GraphNodeId,
    absolute_levels: dict[GraphNodeId, int],
    control_gates: dict[GraphNodeId, list[frozenset[GraphNodeId]]],
    data_dependencies: dict[GraphNodeId, set[GraphNodeId]],
) -> PublicationSelection:
    absolute = absolute_levels.get(source.node_id)
    if absolute is not None:
        return PublicationSelection(PublicationSelectionKind.ABSOLUTE, absolute)
    gates = control_gates[target]
    directly_causal = (bool(gates) and all(gate == frozenset((source.node_id,)) for gate in gates)) or (
        not gates and data_dependencies[target] == {source.node_id}
    )
    if directly_causal:
        return PublicationSelection(PublicationSelectionKind.RELATIVE, 1)
    raise GraphValidationError(
        f"node output {source.node_id!r} has no unique activation coordinate for consumer {target!r}"
    )


def _output_publication_selection(
    source: NodeOutputPort,
    absolute_levels: dict[GraphNodeId, int],
    terminal_gates: tuple[frozenset[GraphNodeId], ...],
) -> PublicationSelection:
    absolute = absolute_levels.get(source.node_id)
    if absolute is not None:
        return PublicationSelection(PublicationSelectionKind.ABSOLUTE, absolute)
    if terminal_gates and all(gate == frozenset((source.node_id,)) for gate in terminal_gates):
        return PublicationSelection(PublicationSelectionKind.RELATIVE, 0)
    raise GraphValidationError(f"graph output source {source.node_id!r} has no unique completion activation coordinate")


def _compile_graph(
    definition: GraphDefinition[GraphValueT],
    scope: DefinitionScope,
) -> CompiledGraph[GraphValueT]:
    nested_graphs: dict[GraphNodeId, CompiledGraph[GraphValueT]] = {}
    for node in definition.nodes:
        if isinstance(node, NestedGraphNodeDefinition):
            nested_graphs[node.node_id] = _compile_graph(node.graph, (*scope, node.node_id))
    nodes = {node.node_id: node for node in definition.nodes}
    node_ids = tuple(sorted(nodes))
    graph_inputs = _collect_graph_inputs(definition)
    node_outputs = {
        node_id: (
            node.outputs
            if isinstance(node := nodes[node_id], CallableNodeDefinition)
            else _nested_outputs(nested_graphs[node_id])
        )
        for node_id in node_ids
    }
    resolved_by_node: dict[GraphNodeId, ResolvedInputBindings[GraphValueT]] = {}
    data_dependencies = {node_id: set[GraphNodeId]() for node_id in node_ids}
    for node_id in node_ids:
        node = nodes[node_id]
        resolved: list[ResolvedInputBinding[GraphValueT]] = []
        for binding in node.inputs.entries:
            source, descriptor = _resolve_source(
                binding.source,
                scope=scope,
                graph_inputs=graph_inputs,
                node_outputs=node_outputs,
                consumer=node_id,
            )
            if isinstance(source, NodeOutputPort):
                data_dependencies[node_id].add(source.node_id)
            resolved.append(
                ResolvedInputBinding(
                    NodeInputPort(scope, node_id, binding.local_name),
                    source,
                    descriptor,
                    None,
                )
            )
        resolved_bindings = ResolvedInputBindings(tuple(resolved))
        if isinstance(node, NestedGraphNodeDefinition):
            expected = nested_graphs[node_id].graph_input_descriptor.declarations.entries
            actual = tuple(
                OutputDeclaration(binding.destination.local_name, binding.descriptor)
                for binding in resolved_bindings.entries
            )
            if tuple(item.name for item in actual) != tuple(item.name for item in expected) or any(
                left.descriptor.value_type is not right.descriptor.value_type
                for left, right in zip(actual, expected, strict=True)
            ):
                raise GraphValidationError(f"nested node {node_id!r} inputs do not exactly match child boundary")
        resolved_by_node[node_id] = resolved_bindings
    if _data_cycle(data_dependencies):
        raise GraphValidationError("ordinary node value bindings contain a data cycle")

    direct_targets: dict[GraphNodeId, set[GraphNodeId]] = {node_id: set() for node_id in node_ids}
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]] = {node_id: {} for node_id in node_ids}
    joins_by_source: dict[GraphNodeId, list[JoinEdge]] = {node_id: [] for node_id in node_ids}
    control_gates: dict[GraphNodeId, list[frozenset[GraphNodeId]]] = {node_id: [] for node_id in node_ids}
    activation_gates: dict[GraphNodeId, list[ActivationGate]] = {node_id: [] for node_id in node_ids}
    gates_to_end: list[frozenset[GraphNodeId]] = []
    direct_pairs: set[tuple[GraphNodeId, GraphNodeId]] = set()
    joins: list[JoinEdge] = []
    for edge in definition.edges:
        if isinstance(edge, DirectEdge):
            direct_pairs.add((edge.source, edge.target))
            if edge.target == END:
                gates_to_end.append(frozenset((edge.source,)))
            else:
                direct_targets[edge.source].add(edge.target)
                control_gates[edge.target].append(frozenset((edge.source,)))
                activation_gates[edge.target].append(((edge.source, None),))
        elif isinstance(edge, ConditionalEdge):
            conditional_targets[edge.source][edge.route] = edge.target
            if edge.target == END:
                gates_to_end.append(frozenset((edge.source,)))
            else:
                control_gates[edge.target].append(frozenset((edge.source,)))
                activation_gates[edge.target].append(((edge.source, edge.route),))
        else:
            normalized = JoinEdge(tuple(sorted(edge.sources)), edge.target)
            joins.append(normalized)
            if edge.target == END:
                gates_to_end.append(frozenset(edge.sources))
            else:
                control_gates[edge.target].append(frozenset(edge.sources))
                activation_gates[edge.target].append(tuple((source, None) for source in normalized.sources))
            for source in edge.sources:
                joins_by_source[source].append(normalized)
    for target, sources in data_dependencies.items():
        for source in sources:
            if (source, target) in direct_pairs:
                raise DuplicateEdgeError(f"node output binding and direct edge duplicate {source!r} -> {target!r}")

    explicit_entries = tuple(sorted(definition.entries))
    automatic_entries = tuple(
        node_id for node_id in node_ids if not data_dependencies[node_id] and not control_gates[node_id]
    )
    duplicates = set(explicit_entries).intersection(automatic_entries)
    if duplicates:
        raise DuplicateBoundaryError(f"automatic entry is also declared from START: {tuple(sorted(duplicates))!r}")
    if any(data_dependencies[node_id] for node_id in explicit_entries):
        raise GraphValidationError("an explicit START target cannot require a node output")
    entries = tuple(sorted((*explicit_entries, *automatic_entries)))
    if not entries:
        raise MissingEntryError("graph definition requires at least one automatic or explicit entry")

    data_targets = {node_id: set[GraphNodeId]() for node_id in node_ids}
    successors = {node_id: set(targets) for node_id, targets in direct_targets.items()}
    for source, routes in conditional_targets.items():
        successors[source].update(target for target in routes.values() if target != END)
    reachability_successors = {node_id: set(targets) for node_id, targets in successors.items()}
    for join in joins:
        if join.target != END:
            for source in join.sources:
                successors[source].add(join.target)
    for target, sources in data_dependencies.items():
        for source in sources:
            if control_gates[target]:
                directly_causal = all(gate == frozenset((source,)) for gate in control_gates[target])
                if target == source or (_can_reach(target, source, successors) and not directly_causal):
                    raise GraphValidationError(
                        f"node output {source!r} is not guaranteed before controlled node {target!r}"
                    )
            else:
                data_targets[source].add(target)
                successors[source].add(target)
                reachability_successors[source].add(target)
    reached = _reachable(entries, reachability_successors, tuple(joins))
    unreachable = set(node_ids) - reached
    if unreachable:
        raise UnreachableNodeError(f"unreachable nodes: {', '.join(sorted(unreachable))}")

    _validate_joint_activation_paths(
        node_ids,
        entries,
        activation_gates,
        data_dependencies,
        conditional_targets,
        tuple(joins),
    )

    guarantees = _guaranteed_sets(node_ids, entries, control_gates, data_dependencies)
    for target, sources in data_dependencies.items():
        if control_gates[target] and not sources <= guarantees[target]:
            missing = tuple(sorted(sources - guarantees[target]))
            raise GraphValidationError(f"controlled node {target!r} can activate before required producers {missing!r}")
    terminal_gates = _terminal_gates(
        node_ids,
        tuple(gates_to_end),
        successors,
    )
    terminal_guarantees = _terminal_guarantees(guarantees, terminal_gates)
    absolute_levels = _absolute_activation_levels(node_ids, entries, successors)
    for node_id, bindings in tuple(resolved_by_node.items()):
        resolved_by_node[node_id] = ResolvedInputBindings(
            tuple(
                ResolvedInputBinding(
                    binding.destination,
                    binding.source,
                    binding.descriptor,
                    None
                    if isinstance(binding.source, GraphInputPort)
                    else _input_publication_selection(
                        binding.source,
                        node_id,
                        absolute_levels,
                        control_gates,
                        data_dependencies,
                    ),
                )
                for binding in bindings.entries
            )
        )
    graph_output_bindings: list[GraphOutputBinding[GraphValueT]] = []
    for output in definition.outputs.entries:
        source, descriptor = _resolve_source(
            output.source,
            scope=scope,
            graph_inputs=graph_inputs,
            node_outputs=node_outputs,
            consumer=None,
        )
        if isinstance(source, NodeOutputPort) and source.node_id not in terminal_guarantees:
            raise GraphValidationError(
                f"graph output {output.boundary_name!r} is not guaranteed before every successful completion"
            )
        graph_output_bindings.append(
            GraphOutputBinding(
                GraphOutputPort(scope, output.boundary_name),
                source,
                descriptor,
                None
                if isinstance(source, GraphInputPort)
                else _output_publication_selection(source, absolute_levels, terminal_gates),
            )
        )
    graph_outputs = GraphOutputBindings(tuple(graph_output_bindings))

    materializations: dict[GraphNodeId, MaterializationPlan[GraphValueT]] = {}
    publications: dict[GraphNodeId, FrameDescriptor[GraphValueT]] = {}
    for ordinal, node_id in enumerate(node_ids):
        input_declarations = OutputDeclarations(
            tuple(
                OutputDeclaration(binding.destination.local_name, binding.descriptor)
                for binding in resolved_by_node[node_id].entries
            )
        )
        input_descriptor = _frame_descriptor(definition, FrameKind.NODE_INPUT, ordinal, input_declarations)
        materializations[node_id] = MaterializationPlan(
            resolved_by_node[node_id],
            input_descriptor,
        )
        output_descriptor = _frame_descriptor(
            definition,
            FrameKind.NODE_OUTPUT,
            ordinal,
            node_outputs[node_id],
        )
        publications[node_id] = output_descriptor

    transition = FrontierTransitionPlan(
        entries,
        frozen_map({node_id: tuple(sorted(targets)) for node_id, targets in direct_targets.items()}),
        frozen_map({node_id: frozen_map(routes) for node_id, routes in conditional_targets.items()}),
        frozen_map(
            {
                node_id: tuple(sorted(edges, key=lambda edge: (edge.target, edge.sources)))
                for node_id, edges in joins_by_source.items()
            }
        ),
        frozen_map({node_id: DataTriggerPlan(tuple(sorted(targets))) for node_id, targets in data_targets.items()}),
        frozen_map(materializations),
        frozen_map(publications),
        graph_outputs,
        tuple(resource.resource_id for resource in definition.resources),
    )
    resource_order = transition.resource_order
    positions = {resource_id: position for position, resource_id in enumerate(resource_order)}
    canonical_nodes = {
        node_id: (
            CallableNodeDefinition(
                node.node_id,
                node.operation,
                node.inputs,
                node.outputs,
                tuple(sorted(node.resources, key=positions.__getitem__)),
            )
            if isinstance(node := nodes[node_id], CallableNodeDefinition)
            else node
        )
        for node_id in node_ids
    }
    return CompiledGraph(
        definition_id=definition.definition_id,
        version=definition.version,
        definition_scope=scope,
        nodes=frozen_map(canonical_nodes),
        nested_graphs=frozen_map(nested_graphs),
        graph_input_descriptor=_frame_descriptor(definition, FrameKind.GRAPH_INPUT, 0, graph_inputs),
        graph_output_descriptor=_frame_descriptor(
            definition,
            FrameKind.GRAPH_OUTPUT,
            0,
            OutputDeclarations(
                tuple(
                    OutputDeclaration(binding.destination.boundary_name, binding.descriptor)
                    for binding in graph_outputs.entries
                )
            ),
        ),
        transition=transition,
        resources=frozen_map({resource.resource_id: resource for resource in definition.resources}),
        resume_input=definition.resume_input,
    )


def compile_graph(definition: GraphDefinition[GraphValueT]) -> CompiledGraph[GraphValueT]:
    """Compile one complete graph family without mutating its builder owners."""

    validate_graph(definition)
    return _compile_graph(definition, ())


__all__: list[str] = []
