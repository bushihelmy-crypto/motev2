"""Pure claim, node-settlement, fence, and frontier-resolution transitions."""

from dataclasses import replace

from mote_kernel.state.graph_state.command import (
    AdvanceGraphFrontier,
    ClaimGraphExecution,
    CompleteGraphFrontier,
    FailedGraphNodeOutcome,
    FenceGraphExecution,
    InterruptedGraphNodeOutcome,
    SettleGraphNode,
    StartGraphRun,
    SucceededGraphNodeOutcome,
)
from mote_kernel.state.graph_state.frontier_model import (
    FailedGraphNode,
    GraphFrontierActivation,
    GraphFrontierNode,
    GraphFrontierState,
    GraphFrontierStatus,
    GraphNodeInterrupt,
    InterruptedGraphNode,
    PendingGraphNode,
    RoutedActivationCause,
    StartActivationCause,
    SucceededGraphNode,
    UseStepRequestInput,
    frontier_node,
    frontier_status,
    pending_node_ids,
)
from mote_kernel.state.graph_state.identity import ActivationReference, GraphActivationIdentity
from mote_kernel.state.graph_state.model import (
    GraphExecutionLease,
    GraphExecutionToken,
    GraphJoinProgress,
    GraphJoinProgressKey,
    GraphRunState,
    GraphRunStatus,
)
from mote_kernel.state.graph_state.resource_command import ReleaseResources
from mote_kernel.state.graph_state.resource_model import ResourceSnapshot
from mote_kernel.state.graph_state.resource_reducer import (
    ResourceTransitionError,
    reduce_resources,
    validate_resource_snapshot,
)
from mote_kernel.state.graph_state.routing import SelectGraphRoute
from mote_kernel.state.graph_state.validation import (
    GraphStateTransitionError,
    validate_graph_frontier,
    validated_graph_run_state,
)


def start_graph_run(command: StartGraphRun) -> GraphRunState:
    activations = _start_activations(command.activations)
    frontier = GraphFrontierState(
        tuple(
            GraphFrontierNode(activation.node_id, PendingGraphNode(UseStepRequestInput()), activation.cause)
            for activation in activations
        )
    )
    return validated_graph_run_state(
        GraphRunState(
            run_id=command.run_id,
            definition_id=command.definition_id,
            definition_version=command.definition_version,
            status=GraphRunStatus.RUNNING,
            superstep=0,
            frontier=frontier,
            parent=command.parent,
            resume_input_codec=command.resume_input_codec,
        )
    )


def _start_activations(
    declared: tuple[GraphFrontierActivation, ...],
) -> tuple[GraphFrontierActivation, ...]:
    _validate_activation_batch(declared, "initial")
    if any(type(activation.cause) is not StartActivationCause for activation in declared):
        raise GraphStateTransitionError("initial activations must use the START cause")
    return declared


def _validate_activation_batch(
    declared: tuple[GraphFrontierActivation, ...],
    label: str,
) -> None:
    if (
        type(declared) is not tuple
        or not declared
        or any(type(activation) is not GraphFrontierActivation for activation in declared)
    ):
        raise GraphStateTransitionError(f"{label} activations are malformed")
    node_ids = tuple(activation.node_id for activation in declared)
    if node_ids != tuple(sorted(set(node_ids))):
        raise GraphStateTransitionError(f"{label} activations must use canonical node order")


def _validate_next_activations(
    state: GraphRunState,
    declared: tuple[GraphFrontierActivation, ...],
) -> frozenset[GraphJoinProgressKey]:
    _validate_activation_batch(declared, "next frontier")
    consumed_progress: set[GraphJoinProgressKey] = set()
    for activation in declared:
        cause = activation.cause
        if type(cause) is not RoutedActivationCause:
            raise GraphStateTransitionError("next frontier activations must carry routed causes")
        source_ids = tuple(reference.activation.node_id for reference in cause.references)
        if len(source_ids) != len(set(source_ids)):
            raise GraphStateTransitionError("next activation cause cannot repeat one source node")
        current_references: list[ActivationReference] = []
        historical_references: list[ActivationReference] = []
        for reference in cause.references:
            source = reference.activation
            if source.run_id != state.run_id or source.superstep > state.superstep:
                raise GraphStateTransitionError("next activation cause references the wrong frontier")
            if source.superstep < state.superstep:
                historical_references.append(reference)
                continue
            current_references.append(reference)
            source_node = frontier_node(state.frontier, source.node_id)
            if source_node is None or not isinstance(source_node.settlement, SucceededGraphNode):
                raise GraphStateTransitionError("next activation cause source is not a settled success")
            routing = source_node.settlement.routing
            expected_route = routing.route if isinstance(routing, SelectGraphRoute) else None
            if reference.route != expected_route:
                raise GraphStateTransitionError("next activation cause route does not match source settlement")
        if not current_references:
            raise GraphStateTransitionError("next activation cause requires a current settled source")
        if historical_references:
            history = tuple(historical_references)
            source_ids = {reference.activation.node_id for reference in cause.references}
            matches = tuple(
                progress
                for progress in state.join_progress
                if progress.target == activation.node_id
                and progress.arrived == history
                and set(progress.sources) == source_ids
            )
            if len(matches) != 1:
                raise GraphStateTransitionError("historical activation references do not complete one pending join")
            progress = matches[0]
            key: GraphJoinProgressKey = (progress.sources, progress.target)
            consumed_progress.add(key)
    return frozenset(consumed_progress)


def _index_join_progress(
    progress: tuple[GraphJoinProgress, ...],
    label: str,
) -> dict[GraphJoinProgressKey, GraphJoinProgress]:
    if type(progress) is not tuple:
        raise GraphStateTransitionError(f"{label} join progress must be a tuple")
    indexed: dict[GraphJoinProgressKey, GraphJoinProgress] = {}
    for item in progress:
        if type(item) is not GraphJoinProgress or type(item.sources) is not tuple or type(item.arrived) is not tuple:
            raise GraphStateTransitionError(f"{label} join progress contains a malformed record")
        key = (item.sources, item.target)
        try:
            hash(key)
        except TypeError as error:
            raise GraphStateTransitionError(f"{label} join progress contains an unhashable key") from error
        if key in indexed:
            raise GraphStateTransitionError(f"{label} join progress repeats one join")
        indexed[key] = item
    return indexed


def _current_successful_references(state: GraphRunState) -> frozenset[ActivationReference]:
    references: set[ActivationReference] = set()
    activation_step = state.superstep
    for node in state.frontier.nodes:
        settlement = node.settlement
        if not isinstance(settlement, SucceededGraphNode):
            continue
        route = settlement.routing.route if isinstance(settlement.routing, SelectGraphRoute) else None
        references.add(
            ActivationReference(
                GraphActivationIdentity(state.run_id, activation_step, node.node_id),
                route,
            )
        )
    return frozenset(references)


def _validate_join_progress_delta(
    state: GraphRunState,
    declared: tuple[GraphJoinProgress, ...],
    consumed: frozenset[GraphJoinProgressKey],
    explicitly_consumed: tuple[GraphJoinProgressKey, ...],
) -> None:
    """Ensure an advance only appends current successes or consumes a Join once."""

    previous = _index_join_progress(state.join_progress, "state")
    updated = _index_join_progress(declared, "command")
    current = _current_successful_references(state)
    explicit = _validate_join_consumption(state, explicitly_consumed, current)
    if consumed & explicit:
        raise GraphStateTransitionError("one pending join cannot be consumed more than once")
    consumable = consumed | explicit

    for key, progress in updated.items():
        prior = previous.get(key)
        try:
            arrived = frozenset(progress.arrived)
        except TypeError as error:
            raise GraphStateTransitionError("command join progress contains unhashable arrivals") from error
        if prior is None:
            if not arrived <= current:
                raise GraphStateTransitionError("new join progress must contain only current settled successes")
            continue
        if key in consumable:
            raise GraphStateTransitionError("consumed join progress cannot be retained")
        try:
            prior_arrived = frozenset(prior.arrived)
        except TypeError as error:
            raise GraphStateTransitionError("state join progress contains unhashable arrivals") from error
        if not prior_arrived <= arrived:
            raise GraphStateTransitionError("join progress cannot remove or replace historical arrivals")
        if not (arrived - prior_arrived) <= current:
            raise GraphStateTransitionError("join progress additions must be current settled successes")

    for key in previous:
        if key not in updated and key not in consumable:
            raise GraphStateTransitionError("unrelated join progress cannot be discarded")


def _validate_join_consumption(
    state: GraphRunState,
    declared: tuple[GraphJoinProgressKey, ...],
    current: frozenset[ActivationReference],
) -> frozenset[GraphJoinProgressKey]:
    if type(declared) is not tuple:
        raise GraphStateTransitionError("consumed join progress must be a tuple")
    previous = _index_join_progress(state.join_progress, "state")
    consumed: set[GraphJoinProgressKey] = set()
    for key in declared:
        if (
            type(key) is not tuple
            or len(key) != 2
            or type(key[0]) is not tuple
            or type(key[1]) is not str
            or not key[0]
            or any(type(source) is not str for source in key[0])
            or key[0] != tuple(sorted(set(key[0])))
        ):
            raise GraphStateTransitionError("consumed join progress key is malformed")
        if key in consumed:
            raise GraphStateTransitionError("consumed join progress keys must be canonical and distinct")
        progress = previous.get(key)
        if progress is None:
            raise GraphStateTransitionError("consumed join progress does not exist in the current state")
        current_arrivals = tuple(reference for reference in current if reference.activation.node_id in progress.sources)
        arrivals = (*progress.arrived, *current_arrivals)
        source_ids = tuple(reference.activation.node_id for reference in arrivals)
        if len(source_ids) != len(set(source_ids)):
            raise GraphStateTransitionError("join progress repeats one source activation")
        if set(source_ids) != set(progress.sources):
            raise GraphStateTransitionError("consumed join progress is not complete")
        consumed.add(key)
    if tuple(sorted(consumed, key=lambda item: (item[0], item[1]))) != declared:
        raise GraphStateTransitionError("consumed join progress keys must use canonical order")
    return frozenset(consumed)


def _validate_claim_resources(state: GraphRunState, resources: ResourceSnapshot | None) -> None:
    if resources is None:
        return
    try:
        validate_resource_snapshot(resources)
    except ResourceTransitionError as error:
        raise GraphStateTransitionError("claim resource snapshot is invalid") from error
    if not resources.acquisitions:
        raise GraphStateTransitionError("an active claim cannot persist an empty resource snapshot")
    pending = frozenset(pending_node_ids(state.frontier))
    participants = frozenset(item.node_id for item in resources.acquisitions)
    if not participants <= pending:
        raise GraphStateTransitionError("claim resource participant is outside current pending nodes")


def _require_execution_lease(state: GraphRunState, token: GraphExecutionToken) -> None:
    execution = state.execution
    if execution is None or execution.token != token:
        raise GraphStateTransitionError("graph command does not own the active execution lease")


def claim_graph_execution(state: GraphRunState, command: ClaimGraphExecution) -> GraphRunState:
    if state.status is not GraphRunStatus.RUNNING or state.execution is not None or state.resources is not None:
        raise GraphStateTransitionError("only a quiescent running graph can claim execution")
    if frontier_status(state.frontier) is not GraphFrontierStatus.EXECUTABLE:
        raise GraphStateTransitionError("only an executable frontier can claim execution")
    if not pending_node_ids(state.frontier):
        raise GraphStateTransitionError("an execution claim requires pending nodes")
    _validate_claim_resources(state, command.resources)
    token = GraphExecutionToken(state.execution_sequence + 1, command.attempt_id)
    return validated_graph_run_state(
        replace(
            state,
            execution_sequence=token.generation,
            execution=GraphExecutionLease(token),
            resources=command.resources,
        )
    )


def fence_graph_execution(state: GraphRunState, command: FenceGraphExecution) -> GraphRunState:
    if state.status is not GraphRunStatus.RUNNING:
        raise GraphStateTransitionError("only a running graph can fence execution")
    _require_execution_lease(state, command.execution)
    return validated_graph_run_state(replace(state, execution=None, resources=None))


def _resolution_base(state: GraphRunState) -> None:
    if state.status is not GraphRunStatus.RUNNING:
        raise GraphStateTransitionError("frontier resolution requires a running graph")
    if frontier_status(state.frontier) is not GraphFrontierStatus.SETTLED:
        raise GraphStateTransitionError("frontier resolution requires a settled frontier")
    if state.execution is not None or state.resources is not None:
        raise GraphStateTransitionError("a settled frontier must be quiescent")


def advance_graph_frontier(state: GraphRunState, command: AdvanceGraphFrontier) -> GraphRunState:
    _resolution_base(state)
    consumed = _validate_next_activations(state, command.activations)
    _validate_join_progress_delta(state, command.join_progress, consumed, command.consumed_join_progress)
    return validated_graph_run_state(
        replace(
            state,
            superstep=state.superstep + 1,
            frontier=GraphFrontierState(
                tuple(
                    GraphFrontierNode(
                        activation.node_id,
                        PendingGraphNode(UseStepRequestInput()),
                        activation.cause,
                    )
                    for activation in command.activations
                )
            ),
            join_progress=command.join_progress,
        )
    )


def complete_graph_frontier(state: GraphRunState, command: CompleteGraphFrontier) -> GraphRunState:
    _resolution_base(state)
    consumed = _validate_join_consumption(
        state,
        command.consumed_join_progress,
        _current_successful_references(state),
    )
    if len(consumed) != len(state.join_progress):
        raise GraphStateTransitionError("a completed graph cannot discard unresolved join progress")
    return validated_graph_run_state(
        replace(
            state,
            status=GraphRunStatus.COMPLETED,
            frontier=GraphFrontierState(()),
            join_progress=(),
            settled_activations=(),
        )
    )


def settle_graph_node(state: GraphRunState, command: SettleGraphNode) -> GraphRunState:
    if state.status is not GraphRunStatus.RUNNING:
        raise GraphStateTransitionError("only a running graph execution can settle a node")
    _require_execution_lease(state, command.execution)
    outcome = command.outcome
    if type(outcome) not in (SucceededGraphNodeOutcome, FailedGraphNodeOutcome, InterruptedGraphNodeOutcome):
        raise GraphStateTransitionError("settlement outcome has an unsupported variant")
    node_id = outcome.node_id
    current = frontier_node(state.frontier, node_id)
    if current is None or not isinstance(current.settlement, PendingGraphNode):
        raise GraphStateTransitionError("node settlement requires a current pending node")

    settled_activations = state.settled_activations
    if isinstance(outcome, SucceededGraphNodeOutcome):
        settlement = SucceededGraphNode(outcome.routing)
        route = outcome.routing.route if isinstance(outcome.routing, SelectGraphRoute) else None
        evidence = ActivationReference(
            GraphActivationIdentity(state.run_id, state.superstep, node_id),
            route,
        )
        if evidence in settled_activations:
            raise GraphStateTransitionError("node activation has already been committed as settled")
        settled_activations = tuple(sorted((*settled_activations, evidence), key=ActivationReference.canonical_key))
    elif isinstance(outcome, FailedGraphNodeOutcome):
        settlement = FailedGraphNode(outcome.failure)
    else:
        expected = (state.run_id, state.superstep, node_id, command.execution.generation)
        identity = outcome.identity
        if (identity.run_id, identity.superstep, identity.node_id, identity.execution_generation) != expected:
            raise GraphStateTransitionError("interrupt outcome identity does not match the active execution")
        if state.resume_input_codec is None:
            raise GraphStateTransitionError("an interrupted node requires a resume input codec")
        settlement = InterruptedGraphNode(GraphNodeInterrupt(identity, outcome.request_payload))

    frontier = GraphFrontierState(
        tuple(
            GraphFrontierNode(
                node.node_id,
                settlement if node.node_id == node_id else node.settlement,
                node.cause,
            )
            for node in state.frontier.nodes
        )
    )
    validate_graph_frontier(state, frontier)

    resources = state.resources
    if resources is not None and any(item.node_id == node_id for item in resources.acquisitions):
        try:
            resources = reduce_resources(resources, ReleaseResources(node_id))
        except ResourceTransitionError as error:
            raise GraphStateTransitionError("completed node cannot release its resource acquisition") from error
        if not resources.acquisitions:
            resources = None

    derived = frontier_status(frontier)
    if derived is GraphFrontierStatus.EXECUTABLE:
        execution = state.execution
        status = GraphRunStatus.RUNNING
    else:
        execution = None
        resources = None
        status = GraphRunStatus.FAILED if derived is GraphFrontierStatus.FAILED else GraphRunStatus.RUNNING
    return validated_graph_run_state(
        replace(
            state,
            status=status,
            frontier=frontier,
            execution=execution,
            resources=resources,
            settled_activations=settled_activations,
        )
    )


__all__ = [
    "advance_graph_frontier",
    "claim_graph_execution",
    "complete_graph_frontier",
    "fence_graph_execution",
    "settle_graph_node",
    "start_graph_run",
]
