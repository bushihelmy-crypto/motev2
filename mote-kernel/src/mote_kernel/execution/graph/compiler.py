"""Deterministic compiler for named value bindings and control topology."""

from dataclasses import dataclass
from itertools import combinations
from typing import TypeAlias, TypeVar, overload

from mote_kernel.execution.errors import (
    DuplicateBoundaryError,
    GraphValidationError,
    MissingEntryError,
    UnknownNodeError,
    UnreachableNodeError,
)
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.definition import GraphDefinition, GraphNode, NestedGraphNodeDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge, Edge, JoinEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.ports import (
    ActivationGate,
    CompiledActivationRule,
    DefinitionScope,
    FeedbackInputBinding,
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
    CompiledActivationRules,
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
_ControlEvent: TypeAlias = tuple[GraphNodeId, GraphRouteId | None]
_ControlEventPair: TypeAlias = tuple[_ControlEvent, _ControlEvent]


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
class _FeedbackResolution:
    initial: ResolvedValueSource
    repeat: NodeOutputPort


@dataclass(frozen=True, slots=True)
class _RouteRequirementProof:
    """A rectangular over-approximation of one activation's route domain.

    The requirements are always safe for rejecting potentially coexisting
    gates.  An exact proof has lost no branch-local or correlated condition,
    so it may also prove that every Join source has the same activation domain.
    """

    requirements: RouteRequirements
    exact: bool


@dataclass(frozen=True, slots=True)
class _ControlFlowProof:
    """Reachable same-frontier event pairs for ordinary control flow.

    A conditional node contributes exactly one selected route, while its
    direct successors are all emitted by that same activation.  The compiler
    can therefore prove that two singleton gates are mutually exclusive by
    traversing pairs of control events instead of treating every incoming
    edge as an independent entry.  Join-produced nodes are deliberately
    marked unknown: their pending-arrival state is owned by the runtime Join
    machinery and is not guessed by this ordinary-flow proof.
    """

    coexisting_events: frozenset[_ControlEventPair]
    join_affected_nodes: frozenset[GraphNodeId]


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
            elif isinstance(source, FeedbackInputBinding) and isinstance(source.initial, GraphInputRef):
                refs.append(source.initial)
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
    scope: DefinitionScope,
    graph_inputs: OutputDeclarations[GraphValueT],
    node_outputs: dict[GraphNodeId, OutputDeclarations[GraphValueT]],
    consumer: GraphNodeId | None,
) -> tuple[GraphInputPort, NominalTypeDescriptor[GraphValueT]]: ...


@overload
def _resolve_source(
    source: NodeOutputRef,
    *,
    scope: DefinitionScope,
    graph_inputs: OutputDeclarations[GraphValueT],
    node_outputs: dict[GraphNodeId, OutputDeclarations[GraphValueT]],
    consumer: GraphNodeId | None,
) -> tuple[NodeOutputPort, NominalTypeDescriptor[GraphValueT]]: ...


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


def _resolve_feedback_repeat(
    source: NodeOutputRef,
    *,
    scope: DefinitionScope,
    node_outputs: dict[GraphNodeId, OutputDeclarations[GraphValueT]],
) -> tuple[NodeOutputPort, NominalTypeDescriptor[GraphValueT]]:
    outputs = node_outputs.get(source.node_id)
    if outputs is None:
        raise UnknownNodeError(f"feedback repeat references unknown node {source.node_id!r}")
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


def _event_sort_key(event: _ControlEvent) -> tuple[GraphNodeId, bool, str]:
    node_id, route = event
    return node_id, route is not None, route or ""


def _event_pair(first: _ControlEvent, second: _ControlEvent) -> _ControlEventPair | None:
    if first[0] == second[0]:
        return None
    return (first, second) if _event_sort_key(first) <= _event_sort_key(second) else (second, first)


def _event_options(
    node_id: GraphNodeId,
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
) -> tuple[_ControlEvent, ...]:
    routes = tuple(sorted(conditional_targets[node_id]))
    return tuple((node_id, route) for route in routes) or ((node_id, None),)


def _ordinary_event_successors(
    event: _ControlEvent,
    direct_targets: dict[GraphNodeId, set[GraphNodeId]],
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
) -> tuple[_ControlEvent, ...]:
    """Return next events, keeping conditional choices on the target node."""

    source, route = event
    targets = set(direct_targets[source])
    if route is not None:
        conditional_target = conditional_targets[source][route]
        if conditional_target != END:
            targets.add(conditional_target)
    return tuple(successor for target in sorted(targets) for successor in _event_options(target, conditional_targets))


def _ordinary_reachable_events(
    entries: tuple[GraphNodeId, ...],
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
    direct_targets: dict[GraphNodeId, set[GraphNodeId]],
) -> frozenset[_ControlEvent]:
    reached = {_event for entry in entries for _event in _event_options(entry, conditional_targets)}
    pending = sorted(reached, key=_event_sort_key)
    while pending:
        event = pending.pop()
        for successor in _ordinary_event_successors(event, direct_targets, conditional_targets):
            if successor not in reached:
                reached.add(successor)
                pending.append(successor)
    return frozenset(reached)


def _join_affected_nodes(
    node_ids: tuple[GraphNodeId, ...],
    joins: tuple[JoinEdge, ...],
    direct_targets: dict[GraphNodeId, set[GraphNodeId]],
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
) -> frozenset[GraphNodeId]:
    affected = {join.target for join in joins if join.target != END}
    pending = sorted(affected)
    while pending:
        source = pending.pop()
        successors = set(direct_targets[source])
        successors.update(target for target in conditional_targets[source].values() if target != END)
        newly_affected = successors - affected
        affected.update(newly_affected)
        pending.extend(sorted(newly_affected))
    return frozenset(node_id for node_id in node_ids if node_id in affected)


def _control_flow_proof(
    node_ids: tuple[GraphNodeId, ...],
    entries: tuple[GraphNodeId, ...],
    direct_targets: dict[GraphNodeId, set[GraphNodeId]],
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
    joins: tuple[JoinEdge, ...],
) -> _ControlFlowProof:
    """Compute ordinary-control events that may share one frontier.

    The worklist tracks pairs rather than whole frontiers.  A pair can arise
    either from two initial entries, from one activation's direct fan-out, or
    from two already coexisting activations advancing one step.  This is the
    exact pair projection for ordinary (non-Join) control flow and reaches a
    finite fixed point even when the graph contains cycles.
    """

    reachable = _ordinary_reachable_events(entries, conditional_targets, direct_targets)
    coexisting: set[_ControlEventPair] = set()
    pending: list[_ControlEventPair] = []

    def remember(first: _ControlEvent, second: _ControlEvent) -> None:
        pair = _event_pair(first, second)
        if pair is not None and pair not in coexisting:
            coexisting.add(pair)
            pending.append(pair)

    for first, second in combinations(
        sorted(reachable, key=_event_sort_key),
        2,
    ):
        if first[0] in entries and second[0] in entries:
            remember(first, second)
    for event in sorted(reachable, key=_event_sort_key):
        successors = _ordinary_event_successors(event, direct_targets, conditional_targets)
        for first, second in combinations(successors, 2):
            remember(first, second)

    while pending:
        first, second = pending.pop()
        first_successors = _ordinary_event_successors(first, direct_targets, conditional_targets)
        second_successors = _ordinary_event_successors(second, direct_targets, conditional_targets)
        for first_successor in first_successors:
            for second_successor in second_successors:
                remember(first_successor, second_successor)

    return _ControlFlowProof(
        frozenset(coexisting),
        _join_affected_nodes(node_ids, joins, direct_targets, conditional_targets),
    )


def _dominates(
    source: GraphNodeId,
    target: GraphNodeId,
    entries: tuple[GraphNodeId, ...],
    successors: dict[GraphNodeId, set[GraphNodeId]],
) -> bool:
    """Return whether every control path to ``target`` passes ``source``."""

    if source == target:
        return True
    if target in entries:
        return False
    pending = [entry for entry in entries if entry != source]
    reached: set[GraphNodeId] = set(pending)
    while pending:
        current = pending.pop()
        for successor in successors[current]:
            if successor == source or successor in reached:
                continue
            reached.add(successor)
            pending.append(successor)
    return target not in reached


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

    cyclic = frozenset(
        node_id
        for node_id in node_ids
        if any(_can_reach(successor, node_id, successors) for successor in successors[node_id])
    )
    if not cyclic:
        return
    terminal_sources = tuple(source for gate in terminal_gates for source in gate)
    if not terminal_sources or any(
        not any(_can_reach(node_id, terminal, successors) for terminal in terminal_sources) for node_id in cyclic
    ):
        raise GraphValidationError(
            f"control cycle {tuple(sorted(cyclic))!r} has no statically reachable successful exit"
        )


def _gates_can_coexist(
    first: _RawActivationGate,
    second: _RawActivationGate,
    requirements: dict[GraphNodeId, _RouteRequirementProof] | None = None,
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]] | None = None,
    control_proof: _ControlFlowProof | None = None,
) -> bool:
    """Return whether two gates can be satisfied by one frontier.

    The first slice has no occurrence identity and therefore cannot safely
    collapse two independent gates for one target.  The only statically
    obvious mutually-exclusive shape is two conditional routes selected by
    the same source; a source emits exactly one route contribution.  Every
    other pair is rejected at compile time, leaving no runtime "pick one"
    behavior or silent double activation.
    """

    if requirements is not None and conditional_targets is not None:
        first_requirement = _gate_route_requirements(first, requirements, conditional_targets)
        second_requirement = _gate_route_requirements(second, requirements, conditional_targets)
        if first_requirement is None or second_requirement is None:
            return False
        if _merge_route_requirements((first_requirement.requirements, second_requirement.requirements)) is None:
            return False
    if control_proof is not None and conditional_targets is not None:
        proof = _ordinary_gates_can_coexist(first, second, conditional_targets, control_proof)
        if proof is not None:
            return proof
    if len(first) != 1 or len(second) != 1:
        return True
    first_source, first_route = first[0]
    second_source, second_route = second[0]
    return not (
        first_source == second_source
        and first_route is not None
        and second_route is not None
        and first_route != second_route
    )


def _ordinary_gates_can_coexist(
    first: _RawActivationGate,
    second: _RawActivationGate,
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
    control_proof: _ControlFlowProof,
) -> bool | None:
    """Answer singleton-gate coexistence from the ordinary-flow proof.

    ``None`` means that at least one gate is a Join gate or depends on a
    Join-produced node; callers must retain the conservative answer instead
    of treating the ordinary-flow proof as an exclusivity proof.
    """

    if len(first) != 1 or len(second) != 1:
        return None
    first_source, first_route = first[0]
    second_source, second_route = second[0]
    if first_source == second_source:
        return not (first_route is not None and second_route is not None and first_route != second_route)
    if first_source in control_proof.join_affected_nodes or second_source in control_proof.join_affected_nodes:
        return None
    first_events = _gate_events(first, conditional_targets)
    second_events = _gate_events(second, conditional_targets)
    return any(
        _event_pair(first_event, second_event) in control_proof.coexisting_events
        for first_event in first_events
        for second_event in second_events
    )


def _gate_events(
    gate: _RawActivationGate,
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
) -> tuple[_ControlEvent, ...]:
    """Expand one compiler-produced singleton gate into its route events."""

    source, route = next(iter(gate))
    options = _event_options(source, conditional_targets)
    if route is None:
        return options
    return ((source, route),)


def _reject_ambiguous_activation_gates(
    activation_gates: dict[GraphNodeId, list[_RawActivationGate]],
    requirements: dict[GraphNodeId, _RouteRequirementProof],
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
    feedback_targets: frozenset[GraphNodeId] = frozenset(),
    control_proof: _ControlFlowProof | None = None,
) -> None:
    for target, gates in activation_gates.items():
        if target in feedback_targets:
            continue
        ordered = tuple(gates)
        for position, first in enumerate(ordered):
            for second in ordered[position + 1 :]:
                if not _gates_can_coexist(
                    first,
                    second,
                    requirements,
                    conditional_targets,
                    control_proof,
                ):
                    continue
                sources: tuple[GraphNodeId, ...] = tuple(
                    sorted(
                        {source for source, _route in (*first, *second)},
                    )
                )
                if len(sources) > 1:
                    guidance = f"concurrent sources may be {sources!r}, declare graph.add_join({sources!r}, {target!r})"
                else:
                    single_source = next(iter(sources))
                    guidance = f"source {single_source!r} contributes more than one path to the same target"
                raise GraphValidationError(
                    f"target {target!r} has multiple activation gates without an explicit Join; {guidance}"
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
) -> dict[GraphNodeId, _RouteRequirementProof]:
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


def _gate_contains_source(gate: _RawActivationGate, source: NodeOutputPort) -> bool:
    return any(candidate == source.node_id for candidate, _route in gate)


def _one_shot_initial_precedes_repeat(
    initial_gate: _RawActivationGate,
    repeat_gate: _RawActivationGate,
    entries: tuple[GraphNodeId, ...],
    successors: dict[GraphNodeId, set[GraphNodeId]],
    absolute_levels: dict[GraphNodeId, int],
) -> bool:
    """Prove that a one-shot initial gate cannot overlap a repeat gate.

    A node-level dominance relation is insufficient once the initial source
    can execute in a cycle: one activation may emit both the next source
    occurrence and its downstream repeat occurrence.  The only ordering proof
    admitted here starts with a source that has one compiler-owned absolute
    activation coordinate.  Every repeat source must be strictly downstream
    of that one-shot source, so its target gate is at least one frontier later.
    Unknown or repeatable coordinates remain a compile-time ambiguity.
    """

    if len(initial_gate) != 1 or not repeat_gate:
        return False
    initial_source = initial_gate[0][0]
    if initial_source not in absolute_levels:
        return False
    repeat_sources = tuple(source for source, _route in repeat_gate)
    if initial_source in repeat_sources:
        return False
    return all(
        _can_reach(initial_source, source, successors) and _dominates(initial_source, source, entries, successors)
        for source in repeat_sources
    )


def _feedback_publication_selection(
    source: NodeOutputPort,
    target: GraphNodeId,
    gate: ActivationGate,
    absolute_levels: dict[GraphNodeId, int],
    joins_by_target: dict[GraphNodeId, list[CompiledJoin]],
) -> PublicationSelection:
    """Choose the one publication coordinate proved by a feedback gate."""

    absolute = absolute_levels.get(source.node_id)
    if absolute is not None:
        return PublicationSelection(PublicationSelectionKind.ABSOLUTE, absolute)
    if len(gate) == 1:
        return PublicationSelection(PublicationSelectionKind.RELATIVE, 1)
    gate_sources = tuple(candidate for candidate, _route in gate)
    candidates = tuple(join for join in joins_by_target[target] if join.identity.sources == gate_sources)
    if len(candidates) != 1:
        raise GraphValidationError(
            f"feedback source {source.node_id!r} has no unique Join publication coordinate for {target!r}"
        )
    return PublicationSelection(
        PublicationSelectionKind.RELATIVE,
        candidates[0].target_offset(source.node_id),
    )


def _feedback_gate_partition(
    target: GraphNodeId,
    initial: GraphInputPort | NodeOutputPort,
    repeat: NodeOutputPort,
    entries: tuple[GraphNodeId, ...],
    successors: dict[GraphNodeId, set[GraphNodeId]],
    gates: tuple[_RawActivationGate, ...],
    requirements: dict[GraphNodeId, _RouteRequirementProof],
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
    absolute_levels: dict[GraphNodeId, int],
    control_proof: _ControlFlowProof | None = None,
) -> tuple[tuple[ActivationGate, ...], tuple[ActivationGate, ...]]:
    """Partition every target gate into one explicit initial/repeat family.

    A feedback declaration is not permission to pick an arbitrary incoming
    edge.  Every gate must be named by exactly one side of the declaration,
    and distinct gates must be statically mutually exclusive.  A Join is one
    gate (and therefore already supplies the required synchronization); two
    independent gates that may arrive together are rejected here.
    """

    if isinstance(initial, GraphInputPort):
        if target not in entries:
            raise GraphValidationError(
                f"feedback target {target!r} with a graph-input initial source must be a graph entry"
            )
        initial_gates: tuple[_RawActivationGate, ...] = ()
    else:
        if target in entries:
            raise GraphValidationError(
                f"feedback target {target!r} with a node-output initial source cannot also be a graph entry"
            )
        initial_gates = tuple(gate for gate in gates if _gate_contains_source(gate, initial))

    repeat_gates = tuple(gate for gate in gates if _gate_contains_source(gate, repeat))
    if not repeat_gates:
        raise GraphValidationError(
            f"feedback target {target!r} repeat source {repeat.node_id!r} has no activation gate"
        )
    if not isinstance(initial, GraphInputPort) and not initial_gates:
        raise GraphValidationError(f"feedback target {target!r} initial source has no activation gate")
    overlap = set(initial_gates).intersection(repeat_gates)
    if overlap:
        raise GraphValidationError(f"feedback target {target!r} initial and repeat sources share an activation gate")
    covered = set(initial_gates).union(repeat_gates)
    if any(gate not in covered for gate in gates):
        raise GraphValidationError(f"feedback target {target!r} has an uncontrolled activation gate")

    for gates in (initial_gates, repeat_gates):
        for position, first in enumerate(gates):
            for second in gates[position + 1 :]:
                if _gates_can_coexist(first, second, requirements, conditional_targets, control_proof):
                    raise GraphValidationError(
                        f"feedback target {target!r} has activation gates that can arrive together without a Join"
                    )
    for initial_gate in initial_gates:
        for repeat_gate in repeat_gates:
            if not _gates_can_coexist(
                initial_gate,
                repeat_gate,
                requirements,
                conditional_targets,
                control_proof,
            ):
                continue
            if _one_shot_initial_precedes_repeat(
                initial_gate,
                repeat_gate,
                entries,
                successors,
                absolute_levels,
            ):
                continue
            raise GraphValidationError(
                f"feedback target {target!r} has activation gates that can arrive together without a Join"
            )
    compiled_initial = tuple(
        sorted(
            (_compiled_activation_gate(gate, conditional_targets) for gate in initial_gates),
            key=_activation_gate_sort_key,
        )
    )
    compiled_repeat = tuple(
        sorted(
            (_compiled_activation_gate(gate, conditional_targets) for gate in repeat_gates),
            key=_activation_gate_sort_key,
        )
    )
    return compiled_initial, compiled_repeat


def _compile_activation_rules(
    nodes: dict[GraphNodeId, GraphNode[GraphValueT]],
    node_ids: tuple[GraphNodeId, ...],
    feedback_bindings: dict[GraphNodeId, tuple[tuple[str, _FeedbackResolution], ...]],
    entries: tuple[GraphNodeId, ...],
    activation_gates: dict[GraphNodeId, list[_RawActivationGate]],
    successors: dict[GraphNodeId, set[GraphNodeId]],
    direct_targets: dict[GraphNodeId, set[GraphNodeId]],
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
    joins_by_source: dict[GraphNodeId, list[CompiledJoin]],
    joins_by_target: dict[GraphNodeId, list[CompiledJoin]],
    requirements: dict[GraphNodeId, _RouteRequirementProof],
    edges: tuple[Edge, ...],
    graph_outputs: GraphOutputBindings[GraphValueT],
    absolute_levels: dict[GraphNodeId, int],
    control_proof: _ControlFlowProof | None = None,
) -> CompiledActivationRules[GraphValueT]:
    declared = tuple(
        (node_id, input_name, resolution)
        for node_id in node_ids
        for input_name, resolution in feedback_bindings[node_id]
    )
    if not declared:
        return CompiledActivationRules(())
    by_target: dict[GraphNodeId, list[tuple[str, _FeedbackResolution]]] = {}
    for target, input_name, resolution in declared:
        by_target.setdefault(target, []).append((input_name, resolution))

    rules: list[CompiledActivationRule[GraphValueT]] = []
    for target in node_ids:
        declarations = by_target.get(target)
        if not declarations:
            continue

        # A self-repeat rule is still allowed only in the original closed
        # two-route shape.  The check is shared by all bindings on the target;
        # their value ports may differ, but the control topology cannot.
        self_repeat = tuple(
            resolution
            for _input_name, resolution in declarations
            if resolution.repeat.node_id == target and isinstance(resolution.initial, GraphInputPort)
        )
        feedback_route: GraphRouteId | None = None
        if self_repeat:
            if len(node_ids) != 1 or not isinstance(nodes[target], CallableNodeDefinition):
                raise GraphValidationError("feedback graph requires one callable target node")
            if (
                direct_targets[target]
                or joins_by_source[target]
                or any(not isinstance(edge, ConditionalEdge) for edge in edges)
            ):
                raise GraphValidationError("feedback target cannot have ordinary or join control edges")
            routes = conditional_targets[target]
            feedback_routes = tuple(route for route, destination in routes.items() if destination == target)
            terminal_routes = tuple(route for route, destination in routes.items() if destination == END)
            if len(routes) != 2 or len(feedback_routes) != 1 or len(terminal_routes) != 1:
                raise GraphValidationError(
                    "feedback target requires exactly one self feedback route and one terminal route"
                )
            if len(graph_outputs.entries) != 1:
                raise GraphValidationError("feedback target requires exactly one graph output")
            output = graph_outputs.entries[0]
            repeat_sources = frozenset(resolution.repeat for resolution in self_repeat)
            if not isinstance(output.source, NodeOutputPort) or output.source not in repeat_sources:
                raise GraphValidationError("feedback graph output must publish the target repeat output")
            feedback_route = feedback_routes[0]

        expected_partition: tuple[tuple[ActivationGate, ...], tuple[ActivationGate, ...]] | None = None
        for input_name, resolution in declarations:
            initial_source = resolution.initial
            repeat_source = resolution.repeat
            if isinstance(initial_source, NodeOutputPort) and initial_source.node_id == target:
                raise GraphValidationError(
                    f"feedback target {target!r} initial source cannot be the target's own output"
                )
            initial_gates, repeat_gates = _feedback_gate_partition(
                target,
                initial_source,
                repeat_source,
                entries,
                successors,
                tuple(activation_gates[target]),
                requirements,
                conditional_targets,
                absolute_levels,
                control_proof,
            )
            partition = (initial_gates, repeat_gates)
            if expected_partition is None:
                expected_partition = partition
            elif partition != expected_partition:
                raise GraphValidationError(
                    f"feedback target {target!r} bindings must share one activation gate partition"
                )
            if not _can_reach(target, repeat_source.node_id, successors) or not _can_reach(
                repeat_source.node_id,
                target,
                successors,
            ):
                raise GraphValidationError(
                    f"feedback target {target!r} and repeat source {repeat_source.node_id!r} "
                    "do not form a control cycle"
                )

            initial_selection: PublicationSelection | None = None
            if isinstance(initial_source, NodeOutputPort):
                selections = tuple(
                    _feedback_publication_selection(
                        initial_source,
                        target,
                        gate,
                        absolute_levels,
                        joins_by_target,
                    )
                    for gate in initial_gates
                )
                if not selections or len(set(selections)) != 1:
                    raise GraphValidationError(
                        f"feedback target {target!r} initial source has no unique publication coordinate"
                    )
                initial_selection = selections[0]
            repeat_selections = tuple(
                _feedback_publication_selection(
                    repeat_source,
                    target,
                    gate,
                    absolute_levels,
                    joins_by_target,
                )
                for gate in repeat_gates
            )
            if len(set(repeat_selections)) != 1:
                raise GraphValidationError(
                    f"feedback target {target!r} repeat source has no unique publication coordinate"
                )
            repeat_selection = repeat_selections[0]
            if self_repeat:
                if not isinstance(initial_source, GraphInputPort) or repeat_source.node_id != target:
                    raise GraphValidationError("feedback target mixes incompatible self and predecessor rules")
                if repeat_gates != (((target, frozenset((feedback_route,))),),):
                    raise GraphValidationError("feedback repeat gate must select the feedback route")
            rules.append(
                CompiledActivationRule(
                    target,
                    input_name,
                    initial_source,
                    repeat_source,
                    repeat_selection,
                    initial_gates,
                    repeat_gates,
                    initial_selection,
                )
            )
    return CompiledActivationRules(tuple(rules))


def _compile_graph(
    definition: GraphDefinition[GraphValueT],
    scope: DefinitionScope,
) -> CompiledGraph[GraphValueT]:
    nested_graphs: dict[GraphNodeId, CompiledGraph[GraphValueT]] = {}
    for node in definition.nodes:
        if isinstance(node, NestedGraphNodeDefinition):
            nested_graphs[node.node_id] = _compile_graph(node.graph, (*scope, node.node_id))
    resource_order = tuple(resource.resource_id for resource in definition.resources)
    positions = {resource_id: position for position, resource_id in enumerate(resource_order)}
    nodes = {
        node.node_id: (
            CallableNodeDefinition(
                node.node_id,
                node.operation,
                node.inputs,
                node.outputs,
                tuple(sorted(node.resources, key=positions.__getitem__)),
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
    input_bindings_by_node: dict[GraphNodeId, ResolvedInputBindings[GraphValueT]] = {}
    feedback_bindings_by_node: dict[GraphNodeId, tuple[tuple[str, _FeedbackResolution], ...]] = {}
    data_dependencies = {node_id: set[GraphNodeId]() for node_id in node_ids}
    for node_id in node_ids:
        node = nodes[node_id]
        resolved: list[ResolvedInputBinding[GraphValueT]] = []
        feedback_bindings: list[tuple[str, _FeedbackResolution]] = []
        for binding in node.inputs.entries:
            declared_source = binding.source
            if isinstance(declared_source, FeedbackInputBinding):
                source, descriptor = _resolve_source(
                    declared_source.initial,
                    scope=scope,
                    graph_inputs=graph_inputs,
                    node_outputs=node_outputs,
                    # A feedback source is deliberately allowed to refer to
                    # the target itself; unlike an ordinary binding it is
                    # materialized from a prior activation publication.
                    consumer=None,
                )
                repeat, repeat_descriptor = _resolve_feedback_repeat(
                    declared_source.repeat,
                    scope=scope,
                    node_outputs=node_outputs,
                )
                if descriptor.value_type is not repeat_descriptor.value_type:
                    raise GraphValidationError(
                        f"feedback input {binding.local_name!r} initial and repeat sources have different exact types"
                    )
                feedback = _FeedbackResolution(
                    source,
                    repeat,
                )
                feedback_bindings.append((binding.local_name, feedback))
            else:
                source, descriptor = _resolve_source(
                    declared_source,
                    scope=scope,
                    graph_inputs=graph_inputs,
                    node_outputs=node_outputs,
                    consumer=node_id,
                )
            if not isinstance(declared_source, FeedbackInputBinding) and isinstance(source, NodeOutputPort):
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
        feedback_bindings_by_node[node_id] = tuple(feedback_bindings)
        if isinstance(node, NestedGraphNodeDefinition):
            expected = nested_graphs[node_id].graph_input_descriptor.declarations.entries
            if len(resolved_bindings.entries) != len(expected) or any(
                binding.destination.local_name != declaration.name
                or binding.descriptor.value_type is not declaration.descriptor.value_type
                for binding, declaration in zip(resolved_bindings.entries, expected, strict=True)
            ):
                raise GraphValidationError(f"nested node {node_id!r} inputs do not exactly match child boundary")
        input_bindings_by_node[node_id] = resolved_bindings
    if _data_cycle(data_dependencies):
        raise GraphValidationError("ordinary node value bindings contain a data cycle")

    direct_targets: dict[GraphNodeId, set[GraphNodeId]] = {node_id: set() for node_id in node_ids}
    conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]] = {node_id: {} for node_id in node_ids}
    activation_gates: dict[GraphNodeId, list[_RawActivationGate]] = {node_id: [] for node_id in node_ids}
    gates_to_end: list[frozenset[GraphNodeId]] = []
    joins: list[JoinEdge] = []
    for edge in definition.edges:
        if isinstance(edge, DirectEdge):
            if edge.target == END:
                gates_to_end.append(frozenset((edge.source,)))
            else:
                direct_targets[edge.source].add(edge.target)
                activation_gates[edge.target].append(((edge.source, None),))
        elif isinstance(edge, ConditionalEdge):
            conditional_targets[edge.source][edge.route] = edge.target
            if edge.target == END:
                gates_to_end.append(frozenset((edge.source,)))
            else:
                activation_gates[edge.target].append(((edge.source, edge.route),))
        else:
            normalized = JoinEdge(tuple(sorted(edge.sources)), edge.target)
            joins.append(normalized)
            if edge.target == END:
                gates_to_end.append(frozenset(edge.sources))
            else:
                activation_gates[edge.target].append(tuple((source, None) for source in normalized.sources))
    explicit_entries = tuple(sorted(definition.entries))
    if any(data_dependencies[node_id] for node_id in explicit_entries):
        raise GraphValidationError("an explicit START target cannot require a node output")
    for target, sources in data_dependencies.items():
        if sources and not activation_gates[target]:
            raise GraphValidationError(
                f"node {target!r} consumes node outputs from {tuple(sorted(sources))!r} "
                "but has no incoming control edge"
            )
    automatic_entries = tuple(
        node_id for node_id in node_ids if not data_dependencies[node_id] and not activation_gates[node_id]
    )
    duplicates = set(explicit_entries).intersection(automatic_entries)
    if duplicates:
        raise DuplicateBoundaryError(f"automatic entry is also declared from START: {tuple(sorted(duplicates))!r}")
    entries = tuple(sorted((*explicit_entries, *automatic_entries)))
    if not entries:
        raise MissingEntryError("graph definition requires at least one automatic or explicit entry")

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
            directly_causal = _all_activation_gates_include(source, activation_gates[target])
            if target == source or (_can_reach(target, source, successors) and not directly_causal):
                raise GraphValidationError(
                    f"node output {source!r} is not guaranteed before controlled node {target!r}"
                )
    reached = _reachable(entries, reachability_successors, tuple(joins))
    unreachable = set(node_ids) - reached
    if unreachable:
        raise UnreachableNodeError(f"unreachable nodes: {', '.join(sorted(unreachable))}")
    route_requirements = _validate_joint_activation_paths(
        node_ids,
        entries,
        activation_gates,
        data_dependencies,
        conditional_targets,
    )
    feedback_targets = frozenset(node_id for node_id, bindings in feedback_bindings_by_node.items() if bindings)
    control_proof = _control_flow_proof(
        node_ids,
        entries,
        direct_targets,
        conditional_targets,
        tuple(joins),
    )
    _reject_ambiguous_activation_gates(
        activation_gates,
        route_requirements,
        conditional_targets,
        feedback_targets,
        control_proof,
    )

    guarantees = _guaranteed_sets(node_ids, entries, activation_gates)
    for target, sources in data_dependencies.items():
        if not sources <= guarantees[target]:
            missing = tuple(sorted(sources - guarantees[target]))
            raise GraphValidationError(f"controlled node {target!r} can activate before required producers {missing!r}")
    terminal_gates = _terminal_gates(
        node_ids,
        tuple(gates_to_end),
        successors,
    )
    _validate_cycle_exits(node_ids, successors, terminal_gates)
    terminal_guarantees = _terminal_guarantees(guarantees, terminal_gates)
    absolute_levels = _absolute_activation_levels(node_ids, entries, successors)
    compiled_joins = _compile_join_occurrence_plans(
        tuple(joins),
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
    publications: dict[GraphNodeId, FrameDescriptor[GraphValueT]] = {
        node_id: _frame_descriptor(
            definition,
            FrameKind.NODE_OUTPUT,
            ordinal,
            node_outputs[node_id],
        )
        for ordinal, node_id in enumerate(node_ids)
    }
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

    activation_rules = _compile_activation_rules(
        nodes,
        node_ids,
        feedback_bindings_by_node,
        entries,
        activation_gates,
        successors,
        direct_targets,
        conditional_targets,
        joins_by_source,
        joins_by_target,
        route_requirements,
        definition.edges,
        graph_outputs,
        absolute_levels,
        control_proof,
    )

    materializations: dict[GraphNodeId, MaterializationPlan[GraphValueT]] = {}
    for ordinal, node_id in enumerate(node_ids):
        bindings = input_bindings_by_node[node_id]
        published_bindings: list[ResolvedInputBinding[GraphValueT]] = []
        for binding in bindings.entries:
            rule = activation_rules.for_input(node_id, binding.destination.local_name)
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
                publication = None
            published_bindings.append(
                ResolvedInputBinding(
                    binding.destination,
                    rule if rule is not None else source,
                    binding.descriptor,
                    None if rule is not None else publication,
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
        activation_rules,
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
        definition_scope=scope,
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
    )


def compile_graph(definition: GraphDefinition[GraphValueT]) -> CompiledGraph[GraphValueT]:
    """Compile one complete graph family without mutating its builder owners."""

    validate_graph(definition)
    return _compile_graph(definition, ())


__all__: list[str] = []
