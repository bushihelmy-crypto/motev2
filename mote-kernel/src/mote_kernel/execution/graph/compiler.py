"""Deterministic compiler for named value bindings and control topology."""

from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeVar, overload

from mote_kernel.execution.errors import (
    GraphValidationError,
    MissingEntryError,
    UnknownNodeError,
    UnreachableNodeError,
)
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.definition import (
    GraphDefinition,
    GraphNode,
    NestedGraphNodeDefinition,
)
from mote_kernel.execution.graph.edge import DirectEdge, Edge, JoinEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.ports import (
    ActivationGate,
    CompiledPredecessorInput,
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
    PredecessorOutputRef,
    PublicationSelection,
    PublicationSelectionKind,
    ResolvedInputBinding,
    ResolvedInputBindings,
    ResolvedValueSource,
)
from mote_kernel.execution.graph.topology import (
    CompiledGraph,
    CompiledJoin,
    FrontierTransitionPlan,
    frozen_map,
)
from mote_kernel.execution.graph.validation import validate_graph
from mote_kernel.state.graph_state import GraphJoinIdentity, GraphNodeId, GraphRouteId

GraphValueT = TypeVar("GraphValueT")
RouteRequirements: TypeAlias = tuple[tuple[GraphNodeId, frozenset[GraphRouteId]], ...]
_RawActivationGate: TypeAlias = tuple[tuple[GraphNodeId, GraphRouteId | None], ...]
_ControlCandidates: TypeAlias = tuple[tuple[GraphNodeId, tuple[GraphNodeId, ...]], ...]
_ControlJoinProgress: TypeAlias = tuple[CompiledJoin, int, tuple[GraphNodeId, ...]]


def _activation_gate_sort_key(
    gate: ActivationGate,
) -> tuple[tuple[GraphNodeId, tuple[tuple[bool, str], ...]], ...]:
    return tuple(
        (source, tuple(sorted((route is not None, route or "") for route in routes))) for source, routes in gate
    )


def _compiled_activation_gate(
    gate: _RawActivationGate,
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
) -> ActivationGate:
    return tuple(
        (
            source,
            frozenset(conditional_targets[source])
            if route is None and conditional_targets[source]
            else frozenset((route,)),
        )
        for source, route in sorted(gate, key=lambda item: (item[0], item[1] is not None, item[1] or ""))
    )


@dataclass(frozen=True, slots=True)
class _RouteRequirementProof:
    """A rectangular route summary used to prove one joint activation.

    An exact proof has lost no branch-local or correlated condition and may
    prove that every one-shot Join source has the same activation domain.
    Reachable-frontier coexistence is owned by ``_completion_routes``;
    a node key here is never treated as identity for two repeatable occurrences.
    """

    requirements: RouteRequirements
    exact: bool


@dataclass(frozen=True, slots=True)
class _ControlFrontier:
    """Canonical static projection of one reachable runtime control frontier."""

    nodes: tuple[GraphNodeId, ...]
    join_progress: tuple[_ControlJoinProgress, ...] = ()


def _all_single_source_gates(
    source: GraphNodeId,
    gates: list[_RawActivationGate],
) -> bool:
    return bool(gates) and all(len(gate) == 1 and gate[0][0] == source for gate in gates)


def _all_activation_gates_include(
    source: GraphNodeId,
    gates: list[_RawActivationGate],
) -> bool:
    """Return whether every way to activate a target carries this source."""

    return bool(gates) and all(any(candidate == source for candidate, _route in gate) for gate in gates)


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
    refs: list[GraphInputRef[GraphValueT]] = []
    for node in definition.nodes:
        for binding in node.inputs.entries:
            source = binding.source
            if isinstance(source, GraphInputRef):
                refs.append(source)
    refs.extend(output.source for output in definition.outputs.entries if isinstance(output.source, GraphInputRef))
    for ref in refs:
        existing = descriptors.get(ref.name)
        if existing is not None and existing.value_type is not ref.descriptor.value_type:
            raise GraphValidationError(f"graph input {ref.name!r} has conflicting exact type declarations")
        descriptors[ref.name] = ref.descriptor
    return OutputDeclarations(
        tuple(OutputDeclaration(name, descriptor) for name, descriptor in sorted(descriptors.items()))
    )


@overload
def _resolve_source(
    source: GraphInputRef[GraphValueT],
    *,
    graph_inputs: OutputDeclarations[GraphValueT],
    node_outputs: dict[GraphNodeId, OutputDeclarations[GraphValueT]],
    consumer: GraphNodeId | None,
) -> tuple[GraphInputPort, NominalTypeDescriptor[GraphValueT]]: ...


@overload
def _resolve_source(
    source: NodeOutputRef[GraphValueT],
    *,
    graph_inputs: OutputDeclarations[GraphValueT],
    node_outputs: dict[GraphNodeId, OutputDeclarations[GraphValueT]],
    consumer: GraphNodeId | None,
) -> tuple[NodeOutputPort, NominalTypeDescriptor[GraphValueT]]: ...


def _resolve_source(
    source: GraphInputRef[GraphValueT] | NodeOutputRef[GraphValueT],
    *,
    graph_inputs: OutputDeclarations[GraphValueT],
    node_outputs: dict[GraphNodeId, OutputDeclarations[GraphValueT]],
    consumer: GraphNodeId | None,
) -> tuple[ResolvedValueSource, NominalTypeDescriptor[GraphValueT]]:
    if isinstance(source, GraphInputRef):
        declaration = _declaration(graph_inputs, source.name, owner="graph input binding")
        return GraphInputPort(source.name), declaration.descriptor
    if consumer is not None and source.node_id == consumer:
        raise GraphValidationError(f"node {consumer!r} cannot bind its own output")
    outputs = node_outputs.get(source.node_id)
    if outputs is None:
        raise UnknownNodeError(f"value source references unknown node {source.node_id!r}")
    declaration = _declaration(outputs, source.output_name, owner=f"node {source.node_id!r}")
    if source.descriptor is not None and source.descriptor is not declaration.descriptor:
        raise GraphValidationError(
            f"typed node output {source.node_id!r}.{source.output_name!r} does not match "
            "its declared exact type/descriptor"
        )
    return NodeOutputPort(source.node_id, source.output_name), declaration.descriptor


def _resolve_predecessor_output(
    source: PredecessorOutputRef[GraphValueT],
    *,
    target: GraphNodeId,
    input_name: str,
    node_outputs: dict[GraphNodeId, OutputDeclarations[GraphValueT]],
    gates: list[_RawActivationGate],
    start_input: GraphInputPort | None,
) -> tuple[CompiledPredecessorInput, NominalTypeDescriptor[GraphValueT]]:
    """Resolve one causal input against its compiled activation cases."""

    if any(len(gate) != 1 for gate in gates):
        raise GraphValidationError(f"predecessor-bound node {target!r} cannot be activated by a Join")
    source_ids = tuple(sorted({gate[0][0] for gate in gates}))
    if not source_ids:
        raise GraphValidationError(
            f"predecessor input {input_name!r} on node {target!r} has no routed predecessor type source"
        )
    descriptors: list[NominalTypeDescriptor[GraphValueT]] = []
    ports: list[NodeOutputPort] = []
    for source_id in source_ids:
        declaration = _declaration(
            node_outputs[source_id],
            source.output_name,
            owner=f"predecessor node {source_id!r}",
        )
        descriptors.append(declaration.descriptor)
        ports.append(NodeOutputPort(source_id, source.output_name))
    descriptor = descriptors[0]
    if any(candidate.value_type is not descriptor.value_type for candidate in descriptors[1:]):
        raise GraphValidationError(
            f"predecessor input {input_name!r} on node {target!r} has conflicting exact output types"
        )
    if source.descriptor is not None and source.descriptor.value_type is not descriptor.value_type:
        raise GraphValidationError(
            f"typed predecessor input {input_name!r} on node {target!r} does not match its declared exact type"
        )
    return CompiledPredecessorInput(target, input_name, tuple(ports), start_input), descriptor


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
    if source not in successors or target not in successors:
        return False
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


def _static_successors(
    node_ids: tuple[GraphNodeId, ...],
    direct_targets: dict[GraphNodeId, set[GraphNodeId]],
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
) -> dict[GraphNodeId, set[GraphNodeId]]:
    """Build the route-independent control successor relation once.

    A conditional edge contributes its target to static reachability for every
    declared route.  The frontier proof below retains each selected target
    separately when it needs route-sensitive reachability.
    """

    return {
        node_id: set(direct_targets[node_id])
        | {target for target in conditional_targets[node_id].values() if target != END}
        for node_id in node_ids
    }


def _guaranteed_sets(
    node_ids: tuple[GraphNodeId, ...],
    entries: tuple[GraphNodeId, ...],
    activation_gates: dict[GraphNodeId, list[_RawActivationGate]],
) -> dict[GraphNodeId, frozenset[GraphNodeId]]:
    guarantees = {node_id: frozenset((node_id,)) for node_id in node_ids}
    entry_set = frozenset(entries)
    while True:
        replacements: dict[GraphNodeId, frozenset[GraphNodeId]] = {}
        for node_id in node_ids:
            alternatives: list[frozenset[GraphNodeId]] = []
            if node_id in entry_set:
                alternatives.append(frozenset())
            gates = activation_gates[node_id]
            if gates:
                for gate in gates:
                    guaranteed: set[GraphNodeId] = set()
                    for source, _route in gate:
                        guaranteed.update(guarantees[source])
                    alternatives.append(frozenset(guaranteed))
            common: set[GraphNodeId] = set(alternatives[0]) if alternatives else set()
            for alternative in alternatives[1:]:
                common.intersection_update(alternative)
            replacements[node_id] = frozenset((*common, node_id))
        if replacements == guarantees:
            return guarantees
        guarantees = replacements


def _with_control_candidate(
    candidates: _ControlCandidates,
    target: GraphNodeId,
    sources: tuple[GraphNodeId, ...],
) -> _ControlCandidates:
    """Add one exact activation cause or reject a reachable duplicate target."""

    indexed = dict(candidates)
    existing = indexed.get(target)
    if existing is not None:
        concurrent = tuple(sorted({*existing, *sources}))
        if len(concurrent) > 1:
            guidance = f"concurrent sources may be {concurrent!r}, declare graph.add_join({concurrent!r}, {target!r})"
        else:
            guidance = f"source {concurrent!r} contributes more than one path to the same target"
        raise GraphValidationError(
            f"target {target!r} has multiple activation gates without an explicit Join; {guidance}"
        )
    indexed[target] = sources
    return tuple(sorted(indexed.items()))


def _frontier_control_base(
    frontier: _ControlFrontier,
    direct_targets: dict[GraphNodeId, set[GraphNodeId]],
    joins_by_source: dict[GraphNodeId, list[CompiledJoin]],
) -> tuple[_ControlCandidates, tuple[_ControlJoinProgress, ...]]:
    """Resolve route-independent successors and relative Join occurrences."""

    candidates: _ControlCandidates = ()
    occurrences: dict[tuple[CompiledJoin, int], set[GraphNodeId]] = {
        (plan, target_offset): set(arrived) for plan, target_offset, arrived in frontier.join_progress
    }
    for source in frontier.nodes:
        for target in sorted(direct_targets[source]):
            candidates = _with_control_candidate(candidates, target, (source,))
        for plan in joins_by_source[source]:
            key = (plan, plan.target_offset(source))
            arrived = occurrences.setdefault(key, set())
            arrived.add(source)

    remaining: list[_ControlJoinProgress] = []
    for (plan, target_offset), arrived in sorted(
        occurrences.items(),
        key=lambda item: (item[0][0].identity, item[0][1]),
    ):
        complete = set(plan.identity.sources) == arrived
        if complete:
            if plan.identity.target != END:
                candidates = _with_control_candidate(candidates, plan.identity.target, plan.identity.sources)
        else:
            remaining.append((plan, target_offset - 1, tuple(sorted(arrived))))
    return candidates, tuple(remaining)


def _terminal_route_domains(
    nodes: tuple[GraphNodeId, ...],
    route_options: dict[GraphNodeId, tuple[GraphRouteId | None, ...]],
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
) -> tuple[tuple[GraphRouteId | None, ...], ...]:
    """Return each node's choices that emit no conditional successor."""

    domains: list[tuple[GraphRouteId | None, ...]] = []
    for node_id in nodes:
        conditional = conditional_targets[node_id]
        terminal = (
            tuple(route for route, target in conditional.items() if target == END)
            if conditional
            else route_options[node_id]
        )
        if not terminal:
            return ()
        domains.append(terminal)
    return tuple(domains)


def _frontier_successors(
    frontier: _ControlFrontier,
    candidates: _ControlCandidates,
    join_progress: tuple[_ControlJoinProgress, ...],
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
) -> tuple[_ControlFrontier, ...]:
    """Expand declared conditional choices into canonical next frontiers."""

    alternatives = {candidates}
    for source in frontier.nodes:
        conditional = conditional_targets[source]
        if not conditional:
            continue
        expanded: set[_ControlCandidates] = set()
        for current in alternatives:
            for target in sorted(set(conditional.values())):
                expanded.add(current if target == END else _with_control_candidate(current, target, (source,)))
        alternatives = expanded
    return tuple(
        sorted(
            (
                _ControlFrontier(tuple(target for target, _sources in candidate), join_progress)
                for candidate in alternatives
                if candidate
            ),
            key=lambda successor: (
                successor.nodes,
                tuple(
                    (plan.identity, target_offset, arrived) for plan, target_offset, arrived in successor.join_progress
                ),
            ),
        )
    )


def _completion_routes(
    entries: tuple[GraphNodeId, ...],
    route_options: dict[GraphNodeId, tuple[GraphRouteId | None, ...]],
    direct_targets: dict[GraphNodeId, set[GraphNodeId]],
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
    joins_by_source: dict[GraphNodeId, list[CompiledJoin]],
) -> frozenset[GraphRouteId | None]:
    """Prove every reachable control frontier and return its completion domain.

    A canonical frontier plus relative Join progress is a finite control state.
    Advancing that state with the compiler-owned route domains preserves the
    branch that activated a node and naturally gives repeated activations a new
    occurrence.  This is the same superstep boundary used by runtime routing,
    without executing nodes or manufacturing a parallel runtime snapshot.  The
    successful frontiers found here are the sole owner of the graph's exported
    completion domain; early route events that must advance are not completions.

    Join plans reach this proof only after ``_compile_join_occurrence_plans`` has
    proved a synchronized repeatable cohort or one absolute target coordinate.
    Consequently an occurrence cannot repeat a source, complete before offset
    one, or remain partial at offset one; the untrusted runtime state boundary
    independently validates those invariants when applying persisted evidence.
    """

    initial = _ControlFrontier(tuple(sorted(entries)))
    pending = [initial]
    seen: set[_ControlFrontier] = set()
    completion_routes: set[GraphRouteId | None] = set()
    while pending:
        frontier = pending.pop()
        if frontier in seen:
            continue
        seen.add(frontier)
        candidates, join_progress = _frontier_control_base(frontier, direct_targets, joins_by_source)
        if not candidates and not join_progress:
            domains = _terminal_route_domains(frontier.nodes, route_options, conditional_targets)
            if domains:
                exposed = frozenset(route for domain in domains for route in domain)
                if len(domains) > 1 and len(exposed) > 1:
                    raise GraphValidationError("terminal frontier may expose conflicting completion routes")
                completion_routes.update(exposed)
        pending.extend(
            successor
            for successor in reversed(_frontier_successors(frontier, candidates, join_progress, conditional_targets))
            if successor not in seen
        )
    if not completion_routes:
        raise GraphValidationError("graph has no statically viable successful completion")
    return frozenset(completion_routes)


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


def _validate_cycle_exits(
    node_ids: tuple[GraphNodeId, ...],
    successors: dict[GraphNodeId, set[GraphNodeId]],
    terminal_gates: tuple[frozenset[GraphNodeId], ...],
) -> None:
    """Require every control cycle to have a statically reachable exit.

    A control self-loop is otherwise an implicit request to keep creating
    frontiers forever.  ``max_supersteps`` is an execution safety fuse, not a
    graph completion rule, so a cycle with no path to an END gate is rejected
    while the definition is still immutable and trusted.
    """

    cyclic = _cycle_nodes(node_ids, successors)
    if not cyclic:
        return
    terminal_sources = tuple(source for gate in terminal_gates for source in gate)
    if not terminal_sources or any(
        not any(_can_reach(node_id, terminal, successors) for terminal in terminal_sources) for node_id in cyclic
    ):
        raise GraphValidationError(
            f"control cycle {tuple(sorted(cyclic))!r} has no statically reachable successful exit"
        )


def _repeatable_nodes(
    entries: tuple[GraphNodeId, ...],
    activation_gates: dict[GraphNodeId, list[_RawActivationGate]],
    successors: dict[GraphNodeId, set[GraphNodeId]],
) -> frozenset[GraphNodeId]:
    """Return nodes that may have more than one activation occurrence.

    A control cycle and a node admitted both from START and an incoming gate
    are the two repeatability seeds.  Repeatability then follows every
    activation gate because any downstream occurrence must retain the source
    occurrence coordinate when it participates in a Join.
    """

    repeatable = set(_cycle_reachable_nodes(tuple(sorted(successors)), successors))
    repeatable.update(node_id for node_id in entries if activation_gates[node_id])
    changed = True
    while changed:
        changed = False
        for node_id, gates in activation_gates.items():
            if node_id in repeatable:
                continue
            if any(any(source in repeatable for source, _route in gate) for gate in gates):
                repeatable.add(node_id)
                changed = True
    return frozenset(repeatable)


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


def _merge_route_requirement_proofs(
    proofs: tuple[_RouteRequirementProof, ...],
) -> _RouteRequirementProof | None:
    merged = _merge_route_requirements(tuple(proof.requirements for proof in proofs))
    if merged is None:
        return None
    return _RouteRequirementProof(merged, all(proof.exact for proof in proofs))


def _source_route_requirements(
    source: GraphNodeId,
    selected_route: GraphRouteId | None,
    requirements: dict[GraphNodeId, _RouteRequirementProof],
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
) -> _RouteRequirementProof:
    proof = requirements.get(source, _RouteRequirementProof((), False))
    source_requirements = dict(proof.requirements)
    declared_routes = frozenset(conditional_targets[source])
    if selected_route is None and not declared_routes:
        return proof
    selected = frozenset((selected_route,)) if selected_route is not None else declared_routes
    existing = source_requirements.get(source)
    source_requirements[source] = selected if existing is None else existing & selected
    return _RouteRequirementProof(tuple(sorted(source_requirements.items())), proof.exact)


def _gate_route_requirements(
    gate: _RawActivationGate,
    requirements: dict[GraphNodeId, _RouteRequirementProof],
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
) -> _RouteRequirementProof | None:
    sources = tuple(
        _source_route_requirements(
            source,
            route,
            requirements,
            conditional_targets,
        )
        for source, route in gate
    )
    return _merge_route_requirement_proofs(sources)


def _alternative_route_requirements(
    alternatives: tuple[_RouteRequirementProof | None, ...],
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
) -> _RouteRequirementProof | None:
    satisfiable = tuple(alternative for alternative in alternatives if alternative is not None)
    if not satisfiable:
        return None
    by_alternative = tuple(dict(alternative.requirements) for alternative in satisfiable)
    common_sources = set(by_alternative[0])
    for alternative in by_alternative[1:]:
        common_sources.intersection_update(alternative)
    combined: list[tuple[GraphNodeId, frozenset[GraphRouteId]]] = []
    for source in sorted(common_sources):
        routes: set[GraphRouteId] = set()
        for alternative in by_alternative:
            routes.update(alternative[source])
        combined.append((source, frozenset(routes)))
    # A branch-only dimension can be erased only when it permits every route.
    # A union of rectangles remains rectangular when at most one retained
    # dimension varies; otherwise the summary loses cross-dimension correlation.
    dropped_requirements_are_exhaustive = all(
        routes == frozenset(conditional_targets[source])
        for alternative in by_alternative
        for source, routes in alternative.items()
        if source not in common_sources
    )
    first = by_alternative[0]
    varying_sources = sum(
        any(alternative[source] != first[source] for alternative in by_alternative[1:]) for source in common_sources
    )
    exact = (
        all(alternative.exact for alternative in satisfiable)
        and dropped_requirements_are_exhaustive
        and varying_sources <= 1
    )
    return _RouteRequirementProof(tuple(combined), exact)


def _validate_joint_activation_paths(
    node_ids: tuple[GraphNodeId, ...],
    entries: tuple[GraphNodeId, ...],
    activation_gates: dict[GraphNodeId, list[_RawActivationGate]],
    data_dependencies: dict[GraphNodeId, set[GraphNodeId]],
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
    successors: dict[GraphNodeId, set[GraphNodeId]],
) -> dict[GraphNodeId, _RouteRequirementProof]:
    # The control successor map already contains every activation-gate edge,
    # including normalized Join sources.  Reuse that compiler-owned relation
    # and add only value dependencies instead of rebuilding a second copy.
    dependency_successors = {node_id: set(successors[node_id]) for node_id in node_ids}
    for target in node_ids:
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
    entry_requirements = _RouteRequirementProof(
        tuple((entry, frozenset(conditional_targets[entry])) for entry in entries if conditional_targets[entry]),
        True,
    )
    requirements: dict[GraphNodeId, _RouteRequirementProof] = {}
    for node_id in ordered:
        alternatives: list[_RouteRequirementProof | None] = []
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
        if data_dependencies[node_id] and alternatives:
            alternatives = [
                None
                if alternative is None or data_requirement is None
                else _merge_route_requirement_proofs((alternative, data_requirement))
                for alternative in alternatives
            ]
        requirement = _alternative_route_requirements(tuple(alternatives), conditional_targets)
        if requirement is None:
            raise GraphValidationError(f"node {node_id!r} has no jointly satisfiable activation path")
        requirements[node_id] = requirement

    return requirements


def _activation_cohort_signature(
    node_id: GraphNodeId,
    entries: frozenset[GraphNodeId],
    activation_gates: dict[GraphNodeId, list[_RawActivationGate]],
) -> tuple[bool, tuple[_RawActivationGate, ...]]:
    gates = tuple(
        sorted(
            activation_gates[node_id],
            key=lambda gate: tuple((source, route is not None, route or "") for source, route in gate),
        )
    )
    return node_id in entries, gates


def _compile_join_occurrence_plans(
    joins: tuple[JoinEdge, ...],
    entries: tuple[GraphNodeId, ...],
    activation_gates: dict[GraphNodeId, list[_RawActivationGate]],
    successors: dict[GraphNodeId, set[GraphNodeId]],
    requirements: dict[GraphNodeId, _RouteRequirementProof],
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
    absolute_levels: dict[GraphNodeId, int],
) -> tuple[CompiledJoin, ...]:
    """Compile the sole source-to-target coordinate proof for every Join.

    A one-shot Join uses its sources' unique absolute activation levels.  A
    repeatable Join is admitted only when every source has the same activation
    cohort, which proves that all of its arrivals share one frontier and hence
    the same target offset.  More complex cyclic schedules remain closed until
    the compiler can prove their offsets without adding a mutable loop counter.
    """

    repeatable = _repeatable_nodes(entries, activation_gates, successors)
    entry_set = frozenset(entries)
    compiled: list[CompiledJoin] = []
    for edge in joins:
        gate = tuple((source, None) for source in edge.sources)
        if _gate_route_requirements(gate, requirements, conditional_targets) is None:
            raise GraphValidationError(
                f"join {edge.sources!r} -> {edge.target!r} has mutually exclusive activation sources"
            )
        source_requirements = tuple(
            _source_route_requirements(source, None, requirements, conditional_targets) for source in edge.sources
        )
        repeated_sources = tuple(source for source in edge.sources if source in repeatable)
        if repeated_sources:
            cohort_signatures = {
                _activation_cohort_signature(source, entry_set, activation_gates) for source in edge.sources
            }
            if len(repeated_sources) != len(edge.sources) or len(cohort_signatures) != 1:
                raise GraphValidationError(
                    f"join {edge.sources!r} -> {edge.target!r} has no provable occurrence identity; "
                    "multiple activation gates cannot supply its sources"
                )
            offsets = tuple((source, 1) for source in edge.sources)
        else:
            if not all(proof.exact for proof in source_requirements) or len(set(source_requirements)) != 1:
                raise GraphValidationError(
                    f"join {edge.sources!r} -> {edge.target!r} can receive only a partial source set on a route"
                )
            try:
                target_level = max(absolute_levels[source] for source in edge.sources) + 1
            except KeyError as error:
                raise GraphValidationError(
                    f"join {edge.sources!r} -> {edge.target!r} has no unique occurrence coordinate; "
                    "multiple activation gates create repeatable source paths"
                ) from error
            offsets = tuple((source, target_level - absolute_levels[source]) for source in edge.sources)
        compiled.append(CompiledJoin(GraphJoinIdentity(edge.sources, edge.target), offsets))
    return tuple(compiled)


def _cycle_reachable_nodes(
    node_ids: tuple[GraphNodeId, ...],
    successors: dict[GraphNodeId, set[GraphNodeId]],
) -> frozenset[GraphNodeId]:
    cycle_nodes = _cycle_nodes(node_ids, successors)
    reached = set(cycle_nodes)
    pending = sorted(cycle_nodes)
    while pending:
        source = pending.pop()
        for target in sorted(successors[source]):
            if target not in reached:
                reached.add(target)
                pending.append(target)
    return frozenset(reached)


def _cycle_nodes(
    node_ids: tuple[GraphNodeId, ...],
    successors: dict[GraphNodeId, set[GraphNodeId]],
) -> frozenset[GraphNodeId]:
    """Return the nodes that participate in a control cycle.

    Cycle membership is one topology fact used by exit validation, repeatable
    activation analysis, and absolute-level inference.  Keep its definition
    in one place; callers decide whether they need only the cycle or its
    forward-reachable descendants.
    """

    return frozenset(
        node_id
        for node_id in node_ids
        if any(_can_reach(successor, node_id, successors) for successor in successors[node_id])
    )


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
    activation_gates: dict[GraphNodeId, list[_RawActivationGate]],
    joins_by_target: dict[GraphNodeId, list[CompiledJoin]],
) -> PublicationSelection:
    absolute = absolute_levels.get(source.node_id)
    if absolute is not None:
        return PublicationSelection(PublicationSelectionKind.ABSOLUTE, absolute)
    if _all_single_source_gates(source.node_id, activation_gates[target]):
        return PublicationSelection(PublicationSelectionKind.RELATIVE, 1)
    target_joins = joins_by_target[target]
    if (
        len(target_joins) == 1
        and len(activation_gates[target]) == 1
        and source.node_id in target_joins[0].identity.sources
    ):
        join = target_joins[0]
        return PublicationSelection(PublicationSelectionKind.RELATIVE, join.target_offset(source.node_id))
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


def _collect_control_topology(
    nodes: dict[GraphNodeId, GraphNode[GraphValueT]],
    nested_graphs: dict[GraphNodeId, CompiledGraph[GraphValueT]],
    edges: tuple[Edge, ...],
) -> tuple[
    dict[GraphNodeId, set[GraphNodeId]],
    dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
    dict[GraphNodeId, list[_RawActivationGate]],
    tuple[frozenset[GraphNodeId], ...],
    tuple[JoinEdge, ...],
    dict[GraphNodeId, tuple[GraphRouteId | None, ...]],
]:
    """Lower declared edges into the compiler's single control relations."""

    node_ids = tuple(sorted(nodes))
    direct_targets: dict[GraphNodeId, set[GraphNodeId]] = {node_id: set() for node_id in node_ids}
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]] = {node_id: {} for node_id in node_ids}
    activation_gates: dict[GraphNodeId, list[_RawActivationGate]] = {node_id: [] for node_id in node_ids}
    gates_to_end: list[frozenset[GraphNodeId]] = []
    joins: list[JoinEdge] = []
    route_options: dict[GraphNodeId, tuple[GraphRouteId | None, ...]] = {}
    for edge in edges:
        if isinstance(edge, JoinEdge):
            normalized = JoinEdge(tuple(sorted(edge.sources)), edge.target)
            joins.append(normalized)
            sources = normalized.sources
            target = normalized.target
            gate = tuple((source, None) for source in sources)
        elif isinstance(edge, DirectEdge):
            source = edge.source
            target = edge.target
            sources = (source,)
            gate = ((source, None),)
            if target != END:
                direct_targets[source].add(target)
        else:
            # ``Edge`` is a closed alias, so the remaining variant is
            # ConditionalEdge.  Keeping this final case explicit avoids a
            # pattern-match fallthrough that could leave ``target`` unset.
            source = edge.source
            route = edge.route
            target = edge.target
            sources = (source,)
            gate = ((source, route),)
            conditional_targets[source][route] = target
        if target != END:
            activation_gates[target].append(gate)
        else:
            gates_to_end.append(frozenset(sources))
    join_sources = frozenset(source for join in joins for source in join.sources)
    for node_id, node in nodes.items():
        conditional = conditional_targets[node_id]
        has_control_successor = bool(direct_targets[node_id] or node_id in join_sources)
        if isinstance(node, CallableNodeDefinition):
            terminal_domain = node.exported_routes or frozenset((None,))
        else:
            terminal_domain = nested_graphs[node_id].completion_routes
        if conditional:
            route_options[node_id] = tuple(sorted(conditional))
        elif has_control_successor:
            route_options[node_id] = (None,)
        else:
            route_options[node_id] = tuple(sorted(terminal_domain, key=lambda route: (route is not None, route or "")))
        if isinstance(node, CallableNodeDefinition):
            if node.exported_routes and (conditional or has_control_successor):
                raise GraphValidationError(f"node {node_id!r} exports terminal routes but is not a terminal callable")
            continue
        child_routes = frozenset(terminal_domain)
        parent_routes = frozenset(route_options[node_id])
        missing = child_routes - parent_routes
        if conditional and missing:
            raise GraphValidationError(
                f"nested node {node_id!r} may complete with routes not declared by its conditional edges: "
                f"{tuple(sorted(repr(route) for route in missing))!r}"
            )
        if conditional and parent_routes - child_routes:
            raise GraphValidationError(
                f"nested node {node_id!r} declares conditional routes the child cannot expose: "
                f"{tuple(sorted(repr(route) for route in parent_routes - child_routes))!r}"
            )
        if has_control_successor and missing:
            raise GraphValidationError(
                f"nested node {node_id!r} may export routes before its control successors complete: "
                f"{tuple(sorted(repr(route) for route in missing))!r}"
            )
    return (
        direct_targets,
        conditional_targets,
        activation_gates,
        tuple(gates_to_end),
        tuple(joins),
        route_options,
    )


def _resolve_input_bindings(
    nodes: dict[GraphNodeId, GraphNode[GraphValueT]],
    node_ids: tuple[GraphNodeId, ...],
    graph_inputs: OutputDeclarations[GraphValueT],
    node_outputs: dict[GraphNodeId, OutputDeclarations[GraphValueT]],
) -> tuple[
    dict[GraphNodeId, ResolvedInputBindings[GraphValueT]],
    dict[GraphNodeId, tuple[tuple[str, PredecessorOutputRef[GraphValueT]], ...]],
    dict[GraphNodeId, set[GraphNodeId]],
]:
    """Resolve ordinary value sources and retain predecessor declarations."""

    input_bindings_by_node: dict[GraphNodeId, ResolvedInputBindings[GraphValueT]] = {}
    predecessor_bindings_by_node: dict[
        GraphNodeId,
        tuple[tuple[str, PredecessorOutputRef[GraphValueT]], ...],
    ] = {}
    data_dependencies = {node_id: set[GraphNodeId]() for node_id in node_ids}
    for node_id in node_ids:
        node = nodes[node_id]
        resolved: list[ResolvedInputBinding[GraphValueT]] = []
        predecessor_bindings: list[tuple[str, PredecessorOutputRef[GraphValueT]]] = []
        for binding in node.inputs.entries:
            declared_source = binding.source
            if isinstance(declared_source, PredecessorOutputRef):
                predecessor_bindings.append((binding.local_name, declared_source))
                continue
            source, descriptor = _resolve_source(
                declared_source,
                graph_inputs=graph_inputs,
                node_outputs=node_outputs,
                consumer=node_id,
            )
            if isinstance(source, NodeOutputPort):
                data_dependencies[node_id].add(source.node_id)
            resolved.append(
                ResolvedInputBinding(
                    NodeInputPort(node_id, binding.local_name),
                    source,
                    descriptor,
                    None,
                )
            )
        input_bindings_by_node[node_id] = ResolvedInputBindings(tuple(resolved))
        predecessor_bindings_by_node[node_id] = tuple(predecessor_bindings)
    if _data_cycle(data_dependencies):
        raise GraphValidationError("ordinary node value bindings contain a data cycle")
    return input_bindings_by_node, predecessor_bindings_by_node, data_dependencies


def _resolve_entries(
    node_ids: tuple[GraphNodeId, ...],
    declared_entries: tuple[GraphNodeId, ...],
    data_dependencies: dict[GraphNodeId, set[GraphNodeId]],
    activation_gates: dict[GraphNodeId, list[_RawActivationGate]],
) -> tuple[GraphNodeId, ...]:
    """Resolve explicit entries, then fill undeclared roots automatically."""

    explicit_entries = tuple(sorted(declared_entries))
    if any(data_dependencies[node_id] for node_id in explicit_entries):
        raise GraphValidationError("an explicit START target cannot require a node output")
    for target, sources in data_dependencies.items():
        if sources and not activation_gates[target]:
            raise GraphValidationError(
                f"node {target!r} consumes node outputs from {tuple(sorted(sources))!r} "
                "but has no incoming control edge"
            )
    automatic_entries = tuple(
        node_id
        for node_id in node_ids
        if node_id not in explicit_entries
        if not data_dependencies[node_id] and not activation_gates[node_id]
    )
    entries = tuple(sorted((*explicit_entries, *automatic_entries)))
    if not entries:
        raise MissingEntryError("graph definition requires at least one automatic or explicit entry")
    return entries


def _complete_input_bindings(
    nodes: dict[GraphNodeId, GraphNode[GraphValueT]],
    nested_graphs: dict[GraphNodeId, CompiledGraph[GraphValueT]],
    graph_inputs: OutputDeclarations[GraphValueT],
    input_bindings_by_node: dict[GraphNodeId, ResolvedInputBindings[GraphValueT]],
    predecessor_bindings_by_node: dict[
        GraphNodeId,
        tuple[tuple[str, PredecessorOutputRef[GraphValueT]], ...],
    ],
    node_outputs: dict[GraphNodeId, OutputDeclarations[GraphValueT]],
    entries: tuple[GraphNodeId, ...],
    activation_gates: dict[GraphNodeId, list[_RawActivationGate]],
) -> tuple[dict[GraphNodeId, ResolvedInputBindings[GraphValueT]], OutputDeclarations[GraphValueT]]:
    """Resolve causal inputs and their START-side graph-input declarations."""

    completed = dict(input_bindings_by_node)
    graph_input_descriptors = {declaration.name: declaration.descriptor for declaration in graph_inputs.entries}
    for node_id in sorted(completed):
        resolved = list(completed[node_id].entries)
        for input_name, declared_source in predecessor_bindings_by_node[node_id]:
            start_input = GraphInputPort(input_name) if node_id in entries else None
            source, descriptor = _resolve_predecessor_output(
                declared_source,
                target=node_id,
                input_name=input_name,
                node_outputs=node_outputs,
                gates=activation_gates[node_id],
                start_input=start_input,
            )
            if start_input is not None:
                entry_descriptor = graph_input_descriptors.get(input_name)
                if entry_descriptor is not None and entry_descriptor.value_type is not descriptor.value_type:
                    raise GraphValidationError(
                        f"graph input {input_name!r} conflicts with its START causal input exact type"
                    )
                if entry_descriptor is None:
                    graph_input_descriptors[input_name] = descriptor
                else:
                    descriptor = entry_descriptor
            resolved.append(
                ResolvedInputBinding(
                    NodeInputPort(node_id, input_name),
                    source,
                    descriptor,
                    None,
                )
            )
        resolved_bindings = ResolvedInputBindings(
            tuple(sorted(resolved, key=lambda binding: binding.destination.local_name))
        )
        node = nodes[node_id]
        if isinstance(node, NestedGraphNodeDefinition):
            expected = nested_graphs[node_id].graph_input_descriptor.declarations.entries
            if len(resolved_bindings.entries) != len(expected) or any(
                binding.destination.local_name != declaration.name
                or binding.descriptor.value_type is not declaration.descriptor.value_type
                for binding, declaration in zip(resolved_bindings.entries, expected, strict=True)
            ):
                raise GraphValidationError(f"nested node {node_id!r} inputs do not exactly match child boundary")
        completed[node_id] = resolved_bindings
    completed_graph_inputs = OutputDeclarations(
        tuple(OutputDeclaration(name, descriptor) for name, descriptor in sorted(graph_input_descriptors.items()))
    )
    return completed, completed_graph_inputs


def _compile_definition(
    definition: GraphDefinition[GraphValueT],
    nested_graphs: dict[GraphNodeId, CompiledGraph[GraphValueT]],
) -> CompiledGraph[GraphValueT]:
    resource_order = tuple(resource.resource_id for resource in definition.resources)
    positions = {resource_id: position for position, resource_id in enumerate(resource_order)}
    nodes = {
        node.node_id: (
            CallableNodeDefinition(
                node.node_id,
                node.invoker,
                node.inputs,
                node.outputs,
                tuple(sorted(node.resources, key=positions.__getitem__)),
                node.exported_routes,
            )
            if isinstance(node, CallableNodeDefinition)
            else node
        )
        for node in definition.nodes
    }
    node_ids = tuple(sorted(nodes))
    graph_inputs = _collect_graph_inputs(definition)
    node_outputs: dict[GraphNodeId, OutputDeclarations[GraphValueT]] = {
        node_id: (
            node.outputs
            if isinstance(node := nodes[node_id], CallableNodeDefinition)
            else _nested_outputs(nested_graphs[node_id])
        )
        for node_id in node_ids
    }
    input_bindings_by_node, predecessor_bindings_by_node, data_dependencies = _resolve_input_bindings(
        nodes,
        node_ids,
        graph_inputs,
        node_outputs,
    )
    (
        direct_targets,
        conditional_targets,
        activation_gates,
        end_gates,
        join_edges,
        route_options,
    ) = _collect_control_topology(
        nodes,
        nested_graphs,
        definition.edges,
    )
    entries = _resolve_entries(node_ids, definition.entries, data_dependencies, activation_gates)
    input_bindings_by_node, graph_inputs = _complete_input_bindings(
        nodes,
        nested_graphs,
        graph_inputs,
        input_bindings_by_node,
        predecessor_bindings_by_node,
        node_outputs,
        entries,
        activation_gates,
    )
    # Reachability must use the route-independent relation before Join edges
    # are expanded into the successor map.  `_reachable` already owns Join
    # activation, so keeping a second copied map here would create a mirror
    # of the same static fact.
    successors = _static_successors(node_ids, direct_targets, conditional_targets)
    reached = _reachable(entries, successors, join_edges)
    unreachable = set(node_ids) - reached
    if unreachable:
        raise UnreachableNodeError(f"unreachable nodes: {', '.join(sorted(unreachable))}")

    for join in join_edges:
        if join.target != END:
            for source in join.sources:
                successors[source].add(join.target)
    terminal_gates = _terminal_gates(
        node_ids,
        end_gates,
        successors,
    )
    for target, sources in data_dependencies.items():
        for source in sources:
            directly_causal = _all_activation_gates_include(source, activation_gates[target])
            if target == source or (_can_reach(target, source, successors) and not directly_causal):
                raise GraphValidationError(
                    f"node output {source!r} is not guaranteed before controlled node {target!r}"
                )
    route_requirements = _validate_joint_activation_paths(
        node_ids,
        entries,
        activation_gates,
        data_dependencies,
        conditional_targets,
        successors,
    )
    guarantees = _guaranteed_sets(node_ids, entries, activation_gates)
    for target, sources in data_dependencies.items():
        if not sources <= guarantees[target]:
            missing = tuple(sorted(sources - guarantees[target]))
            raise GraphValidationError(f"controlled node {target!r} can activate before required producers {missing!r}")
    _validate_cycle_exits(node_ids, successors, terminal_gates)
    terminal_guarantees = _terminal_guarantees(guarantees, terminal_gates)
    absolute_levels = _absolute_activation_levels(node_ids, entries, successors)
    compiled_joins = _compile_join_occurrence_plans(
        join_edges,
        entries,
        activation_gates,
        successors,
        route_requirements,
        conditional_targets,
        absolute_levels,
    )
    joins_by_source: dict[GraphNodeId, list[CompiledJoin]] = {node_id: [] for node_id in node_ids}
    joins_by_target: dict[GraphNodeId, list[CompiledJoin]] = {node_id: [] for node_id in node_ids}
    for join in compiled_joins:
        if join.identity.target != END:
            joins_by_target[join.identity.target].append(join)
        for source in join.identity.sources:
            joins_by_source[source].append(join)
    completion_routes = _completion_routes(
        entries,
        route_options,
        direct_targets,
        conditional_targets,
        joins_by_source,
    )
    graph_output_bindings: list[GraphOutputBinding[GraphValueT]] = []
    for output in definition.outputs.entries:
        source, descriptor = _resolve_source(
            output.source,
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
                GraphOutputPort(output.boundary_name),
                source,
                descriptor,
                None
                if isinstance(source, GraphInputPort)
                else _output_publication_selection(source, absolute_levels, terminal_gates),
            )
        )
    graph_outputs = GraphOutputBindings(tuple(graph_output_bindings))

    # Output and input descriptors share the same stable node ordinal.  Build
    # both plans in one pass so the compiler has one owner for per-node frame
    # layout and never re-traverses the node set for a second ordinal map.
    publications: dict[GraphNodeId, FrameDescriptor[GraphValueT]] = {}
    materializations: dict[GraphNodeId, MaterializationPlan[GraphValueT]] = {}
    for ordinal, node_id in enumerate(node_ids):
        publications[node_id] = _frame_descriptor(
            definition,
            FrameKind.NODE_OUTPUT,
            ordinal,
            node_outputs[node_id],
        )
        bindings = input_bindings_by_node[node_id]
        published_bindings: list[ResolvedInputBinding[GraphValueT]] = []
        for binding in bindings.entries:
            source = binding.source
            if isinstance(source, NodeOutputPort):
                publication = _input_publication_selection(
                    source,
                    node_id,
                    absolute_levels,
                    activation_gates,
                    joins_by_target,
                )
            else:
                publication = binding.publication
            published_bindings.append(
                ResolvedInputBinding(
                    binding.destination,
                    source,
                    binding.descriptor,
                    publication,
                )
            )
        resolved_bindings = ResolvedInputBindings(tuple(published_bindings))
        input_declarations = OutputDeclarations(
            tuple(
                OutputDeclaration(binding.destination.local_name, binding.descriptor)
                for binding in resolved_bindings.entries
            )
        )
        materializations[node_id] = MaterializationPlan(
            resolved_bindings,
            _frame_descriptor(definition, FrameKind.NODE_INPUT, ordinal, input_declarations),
        )

    transition = FrontierTransitionPlan(
        entries,
        frozen_map({node_id: tuple(sorted(targets)) for node_id, targets in direct_targets.items()}),
        frozen_map({node_id: frozen_map(routes) for node_id, routes in conditional_targets.items()}),
        frozen_map(route_options),
        frozen_map(
            {
                node_id: tuple(
                    sorted(
                        edges,
                        key=lambda join: (join.identity.target, join.identity.sources),
                    )
                )
                for node_id, edges in joins_by_source.items()
            }
        ),
        frozen_map(materializations),
        frozen_map(publications),
        graph_outputs,
        resource_order,
        frozen_map(
            {
                node_id: tuple(
                    sorted(
                        (_compiled_activation_gate(gate, conditional_targets) for gate in gates),
                        key=_activation_gate_sort_key,
                    )
                )
                for node_id, gates in activation_gates.items()
            }
        ),
    )
    return CompiledGraph(
        definition_id=definition.definition_id,
        version=definition.version,
        nodes=frozen_map(nodes),
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
        completion_routes=completion_routes,
    )


class GraphCompiler(Generic[GraphValueT]):
    """Short-lived owner of one immutable graph-family compilation."""

    __slots__ = ("_compiled_definitions", "_root")

    def __init__(
        self,
        root: GraphDefinition[GraphValueT],
        retained: tuple[tuple[GraphDefinition[GraphValueT], CompiledGraph[GraphValueT]], ...] = (),
    ) -> None:
        self._root = root
        self._compiled_definitions = {id(definition): compiled for definition, compiled in retained}

    def compile(self) -> CompiledGraph[GraphValueT]:
        validate_graph(self._root)
        return self._compile_validated(self._root)

    def plan_for(self, definition: GraphDefinition[GraphValueT]) -> CompiledGraph[GraphValueT]:
        """Return a definition's plan after the root family was compiled."""

        return self._compiled_definitions[id(definition)]

    def _compile_validated(self, definition: GraphDefinition[GraphValueT]) -> CompiledGraph[GraphValueT]:
        existing = self._compiled_definitions.get(id(definition))
        if existing is not None:
            return existing
        nested_graphs = {
            node.node_id: self._compile_validated(node.graph)
            for node in definition.nodes
            if isinstance(node, NestedGraphNodeDefinition)
        }
        compiled = _compile_definition(definition, nested_graphs)
        self._compiled_definitions[id(definition)] = compiled
        return compiled


__all__: list[str] = []
