"""Single public graph composition and execution facade."""

import asyncio
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import ClassVar, Generic, Never, Self, TypeAlias, TypeVar, overload
from uuid import uuid4

from mote_kernel.execution.engine.admission import (
    admit_graph_input,
)
from mote_kernel.execution.engine.recovery import (
    preflight_recovery,
)
from mote_kernel.execution.errors import (
    ExecutionError,
    ExecutionLimitError,
    GraphValidationError,
    GraphValueAdmissionError,
    GraphValuePublicationError,
    GraphValueUnavailableError,
    RoutingError,
    SnapshotMismatchError,
)
from mote_kernel.execution.executor import GraphExecutor
from mote_kernel.execution.family_driver import (
    GraphCommit,
    GraphTransition,
    admit_root,
    commit_transition,
    drive_root,
    fresh_root,
    project_graph_result,
    scoped_commit,
)
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.constants import END, START
from mote_kernel.execution.graph.definition import GraphDefinition, NestedGraphNodeDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge, Edge, JoinEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition, NodeCallable
from mote_kernel.execution.graph.outcome import (
    GraphOutcome,
    _failure,
    _GraphFailureOutcome,
    _GraphInterruptOutcome,
    _GraphSuccessOutcome,
    _interrupt,
    _success,
)
from mote_kernel.execution.graph.ports import (
    GraphInputRef,
    GraphOutputDeclarations,
    InputBindings,
    NodeOutputRef,
    canonical_nominal_type,
    canonical_port_name,
    normalize_graph_output_declarations,
    normalize_input_bindings,
    normalize_output_declarations,
)
from mote_kernel.execution.graph.resume_input import ResumeInputBinding
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.validation import require_graph_identity
from mote_kernel.execution.graph.values import (
    FactoryValueT,
    _GraphValues,
    _make_graph_values,
    _require_graph_values,
)
from mote_kernel.execution.identity import (
    root_scope_run,
)
from mote_kernel.execution.invocation import (
    admit_state_owned_overrides,
    executors_for,
    install_confirmed_resume_frames,
    lineage_states,
    plan_fences,
    plan_resumes,
    recovery_seed,
    replace_planned_state,
    validate_context,
)
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.request import (
    OverrideNodeInput,
    ResumeFailedNodeRequest,
    ResumeInterruptedNodeRequest,
    ResumeNodeRequest,
    SkipFailedNodeRequest,
    UseMaterializedInput,
)
from mote_kernel.execution.resource import ResourceDefinition, ResourceId
from mote_kernel.execution.result import (
    GraphResult,
    _AbortedGraphResult,
    _AwaitingResumeGraphResult,
    _CompletedGraphResult,
    _GraphFailureResult,
    _GraphInterruptResult,
    _GraphSuccessResult,
    _partial_commit_error,
    _PartialCommitError,
)
from mote_kernel.execution.run_context import (
    ChildStateBinding,
    ScopedFrameIndex,
    _admit_continuation,
    _CompiledFamilyIdentity,
    _continuation_recovered,
    _GraphContinuation,
    _make_continuation,
    _new_family_identity,
)
from mote_kernel.state.graph_state import (
    GraphAbortReason,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphInterruptId,
    GraphNodeId,
    GraphResumeInputCodecId,
    GraphRouteId,
    GraphRunId,
    GraphRunState,
)

GraphValueT = TypeVar("GraphValueT")
ValueT = TypeVar("ValueT")


class _MissingRunValues:
    __slots__ = ()


_MISSING_RUN_VALUES = _MissingRunValues()


def _canonical_scope(scope: tuple[str, ...]) -> tuple[GraphNodeId, ...]:
    if type(scope) is not tuple:
        raise SnapshotMismatchError("resume scope must be a tuple of nested node identities")
    return tuple(GraphNodeId(canonical_port_name(segment, kind="scope")) for segment in scope)


def _canonical_resources(resources: tuple[str, ...]) -> tuple[ResourceId, ...]:
    if type(resources) is not tuple:
        raise GraphValidationError("node resources must be a tuple")
    normalized = tuple(ResourceId(canonical_port_name(resource, kind="resource")) for resource in resources)
    if len(normalized) != len(set(normalized)):
        raise GraphValidationError("a node cannot repeat one resource requirement")
    return normalized


@dataclass(frozen=True, slots=True)
class _NestedNodeCandidate(Generic[GraphValueT]):
    node_id: GraphNodeId
    graph: "Graph[GraphValueT]"
    inputs: InputBindings[GraphValueT]


NodeCandidate: TypeAlias = CallableNodeDefinition[GraphValueT] | _NestedNodeCandidate[GraphValueT]


@dataclass(frozen=True, slots=True)
class _GraphBuilderState(Generic[GraphValueT]):
    nodes: tuple[NodeCandidate[GraphValueT], ...] = ()
    edges: tuple[Edge, ...] = ()
    entries: tuple[GraphNodeId, ...] = ()
    outputs: GraphOutputDeclarations[GraphValueT] | None = None
    resources: tuple[ResourceDefinition, ...] = ()
    resume_input: ResumeInputBinding[GraphValueT] | None = None


@dataclass(frozen=True, slots=True)
class _ResumeCodec(Generic[GraphValueT]):
    encoder: Callable[[_GraphValues[GraphValueT]], bytes]
    decoder: Callable[[bytes], _GraphValues[GraphValueT]]

    def encode(self, value: _GraphValues[GraphValueT]) -> bytes:
        return self.encoder(value)

    def decode(self, payload: bytes) -> _GraphValues[GraphValueT]:
        return self.decoder(payload)


@dataclass(frozen=True, slots=True)
class _CompiledOwner(Generic[GraphValueT]):
    graph: CompiledGraph[GraphValueT]
    family_identity: _CompiledFamilyIdentity


class Graph(Generic[GraphValueT]):
    """Compose and execute one typed graph family through the sole engine."""

    START: ClassVar[str] = START
    END: ClassVar[str] = END
    Values = _GraphValues
    SuccessOutcome = _GraphSuccessOutcome
    FailureOutcome = _GraphFailureOutcome
    InterruptOutcome = _GraphInterruptOutcome
    Outcome = GraphOutcome
    ResumeAction = ResumeNodeRequest
    Commit = GraphCommit
    Transition = GraphTransition
    SuccessResult = _GraphSuccessResult
    FailureResult = _GraphFailureResult
    InterruptResult = _GraphInterruptResult
    Continuation = _GraphContinuation
    PartialCommitError = _PartialCommitError
    CompletedResult = _CompletedGraphResult
    AbortedResult = _AbortedGraphResult
    AwaitingResumeResult = _AwaitingResumeGraphResult
    Result = GraphResult
    State = GraphRunState
    Error = ExecutionError
    ValidationError = GraphValidationError
    SnapshotMismatchError = SnapshotMismatchError
    ExecutionLimitError = ExecutionLimitError
    ValueAdmissionError = GraphValueAdmissionError
    ValueUnavailableError = GraphValueUnavailableError
    ValuePublicationError = GraphValuePublicationError
    RoutingError = RoutingError

    __slots__ = ("_builder_state", "_compiled_owner", "_definition_id", "_version")

    def __init__(self, definition_id: str, *, version: int = 1) -> None:
        require_graph_identity(definition_id, kind="graph")
        if type(version) is not int or version < 1:
            raise GraphValidationError("graph version must be an exact positive integer")
        self._definition_id = GraphDefinitionId(definition_id)
        self._version = GraphDefinitionVersion(version)
        self._builder_state: _GraphBuilderState[GraphValueT] = _GraphBuilderState()
        self._compiled_owner: _CompiledOwner[GraphValueT] | None = None

    def _require_mutable(self) -> _GraphBuilderState[GraphValueT]:
        if self._compiled_owner is not None:
            raise GraphValidationError("a graph definition is immutable after its first successful compile")
        return self._builder_state

    def _commit_builder(
        self,
        previous: _GraphBuilderState[GraphValueT],
        replacement: _GraphBuilderState[GraphValueT],
    ) -> None:
        if self._compiled_owner is not None or self._builder_state is not previous:
            raise GraphValidationError("graph builder state changed before its atomic replacement")
        self._builder_state = replacement

    @staticmethod
    def graph_input(name: str, value_type: type[ValueT]) -> GraphInputRef[ValueT]:
        return GraphInputRef(
            canonical_port_name(name, kind="graph input"),
            canonical_nominal_type(value_type),
        )

    @staticmethod
    def node_output(node_id: str, output_name: str) -> NodeOutputRef:
        return NodeOutputRef(
            GraphNodeId(canonical_port_name(node_id, kind="source node")),
            canonical_port_name(output_name, kind="source output"),
        )

    @staticmethod
    @overload
    def values() -> "Graph.Values[Never]": ...

    @staticmethod
    @overload
    def values(**values: FactoryValueT) -> "Graph.Values[FactoryValueT]": ...

    @staticmethod
    def values(**values: FactoryValueT) -> "Graph.Values[FactoryValueT]":
        return _make_graph_values(**values)

    @staticmethod
    def success(
        output: "Graph.Values[FactoryValueT]",
        *,
        route: str | None = None,
    ) -> "Graph.SuccessOutcome[FactoryValueT]":
        return _success(output, route=route)

    @staticmethod
    def failure(reason: str) -> "Graph.FailureOutcome":
        return _failure(reason)

    @staticmethod
    def interrupt(request_payload: bytes) -> "Graph.InterruptOutcome":
        return _interrupt(request_payload)

    @overload
    def add_node(
        self,
        node_id: str,
        operation: NodeCallable[GraphValueT],
        *,
        inputs: Mapping[str, GraphInputRef[GraphValueT] | NodeOutputRef],
        outputs: Mapping[str, type[GraphValueT]],
        resources: tuple[str, ...] = (),
    ) -> Self: ...

    @overload
    def add_node(
        self,
        node_id: str,
        operation: "Graph[GraphValueT]",
        *,
        inputs: Mapping[str, GraphInputRef[GraphValueT] | NodeOutputRef],
    ) -> Self: ...

    def add_node(
        self,
        node_id: str,
        operation: NodeCallable[GraphValueT] | "Graph[GraphValueT]",
        *,
        inputs: Mapping[
            str,
            GraphInputRef[GraphValueT] | NodeOutputRef | type[GraphValueT],
        ],
        outputs: Mapping[
            str,
            type[GraphValueT] | GraphInputRef[GraphValueT] | NodeOutputRef,
        ]
        | None = None,
        resources: tuple[str, ...] = (),
    ) -> Self:
        state = self._require_mutable()
        canonical_id = GraphNodeId(canonical_port_name(node_id, kind="node"))
        bindings = normalize_input_bindings(inputs)
        if isinstance(operation, Graph):
            if outputs is not None or resources:
                raise GraphValidationError("nested graph nodes do not declare parent outputs or resources")
            candidate: NodeCandidate[GraphValueT] = _NestedNodeCandidate(
                canonical_id,
                operation,
                bindings,
            )
            replacement = replace(state, nodes=(*state.nodes, candidate))
        else:
            if not callable(operation):
                raise GraphValidationError("ordinary graph node operation must be callable")
            if outputs is None:
                raise GraphValidationError("callable graph nodes require an explicit outputs mapping")
            declarations = normalize_output_declarations(outputs)
            resource_ids = _canonical_resources(resources)
            candidate = CallableNodeDefinition(
                canonical_id,
                operation,
                bindings,
                declarations,
                resource_ids,
            )
            known = {resource.resource_id for resource in state.resources}
            added = tuple(
                ResourceDefinition(resource_id, len(state.resources) + ordinal)
                for ordinal, resource_id in enumerate(
                    resource_id for resource_id in resource_ids if resource_id not in known
                )
            )
            replacement = replace(
                state,
                nodes=(*state.nodes, candidate),
                resources=(*state.resources, *added),
            )
        self._commit_builder(state, replacement)
        return self

    def set_outputs(
        self,
        outputs: Mapping[
            str,
            GraphInputRef[GraphValueT] | NodeOutputRef | type[GraphValueT],
        ],
    ) -> Self:
        state = self._require_mutable()
        if state.outputs is not None:
            raise GraphValidationError("graph outputs can be declared exactly once")
        declaration = normalize_graph_output_declarations(outputs)
        replacement = replace(state, outputs=declaration)
        self._commit_builder(state, replacement)
        return self

    def add_edge(self, source: str, target: str) -> Self:
        state = self._require_mutable()
        canonical_source = canonical_port_name(source, kind="edge source")
        canonical_target = canonical_port_name(target, kind="edge target")
        if canonical_source == Graph.START:
            if canonical_target in (Graph.START, Graph.END):
                raise GraphValidationError("START must target one concrete node")
            replacement = replace(state, entries=(*state.entries, GraphNodeId(canonical_target)))
        else:
            replacement = replace(
                state,
                edges=(
                    *state.edges,
                    DirectEdge(
                        GraphNodeId(canonical_source),
                        END if canonical_target == Graph.END else GraphNodeId(canonical_target),
                    ),
                ),
            )
        self._commit_builder(state, replacement)
        return self

    def add_conditional_edge(self, source: str, route: str, target: str) -> Self:
        state = self._require_mutable()
        canonical_source = canonical_port_name(source, kind="conditional source")
        canonical_route = canonical_port_name(route, kind="route")
        canonical_target = canonical_port_name(target, kind="conditional target")
        if canonical_source in (Graph.START, Graph.END) or canonical_target == Graph.START:
            raise GraphValidationError("conditional edge has an invalid boundary direction")
        edge = ConditionalEdge(
            GraphNodeId(canonical_source),
            GraphRouteId(canonical_route),
            END if canonical_target == Graph.END else GraphNodeId(canonical_target),
        )
        replacement = replace(state, edges=(*state.edges, edge))
        self._commit_builder(state, replacement)
        return self

    def add_join(self, sources: tuple[str, ...], target: str) -> Self:
        state = self._require_mutable()
        if type(sources) is not tuple:
            raise GraphValidationError("join sources must be a tuple")
        canonical_sources = tuple(GraphNodeId(canonical_port_name(source, kind="join source")) for source in sources)
        canonical_target = canonical_port_name(target, kind="join target")
        if any(source in (Graph.START, Graph.END) for source in canonical_sources) or canonical_target == Graph.START:
            raise GraphValidationError("join edge has an invalid boundary direction")
        edge = JoinEdge(
            canonical_sources,
            END if canonical_target == Graph.END else GraphNodeId(canonical_target),
        )
        replacement = replace(state, edges=(*state.edges, edge))
        self._commit_builder(state, replacement)
        return self

    def set_resume_codec(
        self,
        codec_id: str,
        version: int,
        encoder: Callable[["Graph.Values[GraphValueT]"], bytes],
        decoder: Callable[[bytes], "Graph.Values[GraphValueT]"],
    ) -> Self:
        state = self._require_mutable()
        if state.resume_input is not None:
            raise GraphValidationError("resume input codec can be declared exactly once")
        if not callable(encoder) or not callable(decoder):
            raise GraphValidationError("resume input encoder and decoder must be callable")
        canonical_id = GraphResumeInputCodecId(canonical_port_name(codec_id, kind="resume codec"))
        if type(version) is not int or version < 1:
            raise GraphValidationError("resume codec version must be an exact positive integer")
        codec = _ResumeCodec(encoder, decoder)
        binding = ResumeInputBinding(canonical_id, version, codec, codec)
        replacement = replace(state, resume_input=binding)
        self._commit_builder(state, replacement)
        return self

    def resume_failed(
        self,
        node_id: str,
        *,
        scope: tuple[str, ...] = (),
    ) -> "Graph.ResumeAction[GraphValueT]":
        return ResumeFailedNodeRequest(
            _canonical_scope(scope),
            GraphNodeId(canonical_port_name(node_id, kind="resume node")),
            UseMaterializedInput(),
        )

    def resume_failed_with(
        self,
        node_id: str,
        values: "Graph.Values[GraphValueT]",
        *,
        scope: tuple[str, ...] = (),
    ) -> "Graph.ResumeAction[GraphValueT]":
        return ResumeFailedNodeRequest(
            _canonical_scope(scope),
            GraphNodeId(canonical_port_name(node_id, kind="resume node")),
            OverrideNodeInput(_require_graph_values(values)),
        )

    def resume_interrupted(
        self,
        node_id: str,
        interrupt_id: str,
        values: "Graph.Values[GraphValueT]",
        *,
        scope: tuple[str, ...] = (),
    ) -> "Graph.ResumeAction[GraphValueT]":
        return ResumeInterruptedNodeRequest(
            _canonical_scope(scope),
            GraphNodeId(canonical_port_name(node_id, kind="resume node")),
            GraphInterruptId(canonical_port_name(interrupt_id, kind="interrupt")),
            OverrideNodeInput(_require_graph_values(values)),
        )

    def skip_failed(
        self,
        node_id: str,
        reason: str,
        *,
        route: str | None = None,
        output: "Graph.Values[GraphValueT] | None" = None,
        scope: tuple[str, ...] = (),
    ) -> "Graph.ResumeAction[GraphValueT]":
        canonical_reason = canonical_port_name(reason, kind="skip reason")
        canonical_route = canonical_port_name(route, kind="skip route") if route is not None else None
        return SkipFailedNodeRequest(
            _canonical_scope(scope),
            GraphNodeId(canonical_port_name(node_id, kind="resume node")),
            canonical_reason,
            canonical_route,
            _require_graph_values(output) if output is not None else None,
        )

    def _definition(
        self,
        definitions: dict["Graph[GraphValueT]", GraphDefinition[GraphValueT]],
        visiting: set["Graph[GraphValueT]"],
    ) -> GraphDefinition[GraphValueT]:
        existing = definitions.get(self)
        if existing is not None:
            return existing
        if self in visiting:
            raise GraphValidationError("graph composition recursively contains itself")
        visiting.add(self)
        state = self._builder_state
        if state.outputs is None:
            raise GraphValidationError("graph requires exactly one set_outputs() declaration")
        nodes: list[CallableNodeDefinition[GraphValueT] | NestedGraphNodeDefinition[GraphValueT]] = []
        for candidate in state.nodes:
            if isinstance(candidate, CallableNodeDefinition):
                nodes.append(candidate)
            else:
                child = candidate.graph._definition(definitions, visiting)
                nodes.append(NestedGraphNodeDefinition(candidate.node_id, child, candidate.inputs))
        definition = GraphDefinition(
            self._definition_id,
            self._version,
            tuple(nodes),
            state.edges,
            state.entries,
            state.outputs,
            state.resources,
            state.resume_input,
        )
        definitions[self] = definition
        visiting.remove(self)
        return definition

    def _compile(self) -> _CompiledOwner[GraphValueT]:
        existing = self._compiled_owner
        if existing is not None:
            return existing
        definitions: dict[Graph[GraphValueT], GraphDefinition[GraphValueT]] = {}
        self._definition(definitions, set())
        compiled: dict[Graph[GraphValueT], CompiledGraph[GraphValueT]] = {
            owner: compile_graph(definition) for owner, definition in definitions.items()
        }
        installations = {
            owner: _CompiledOwner(graph, _new_family_identity())
            for owner, graph in compiled.items()
            if owner._compiled_owner is None
        }
        for owner, installation in installations.items():
            owner._compiled_owner = installation
        return installations[self]

    @overload
    async def run(
        self,
        values: "Graph.Values[GraphValueT]",
        /,
        *,
        run_id: str | None = None,
        commit: "Graph.Commit[GraphValueT] | None" = None,
        max_supersteps: int = 1_000,
        max_parallel_tasks: int = 64,
    ) -> "Graph.Result[GraphValueT]": ...

    @overload
    async def run(
        self,
        /,
        *,
        state: "Graph.State",
        continuation: "Graph.Continuation[GraphValueT]",
        resume: tuple["Graph.ResumeAction[GraphValueT]", ...] = (),
        commit: "Graph.Commit[GraphValueT] | None" = None,
        max_supersteps: int = 1_000,
        max_parallel_tasks: int = 64,
    ) -> "Graph.Result[GraphValueT]": ...

    @overload
    async def run(
        self,
        /,
        *,
        state: "Graph.State",
        resume: tuple["Graph.ResumeAction[GraphValueT]", ...] = (),
        commit: "Graph.Commit[GraphValueT] | None" = None,
        max_supersteps: int = 1_000,
        max_parallel_tasks: int = 64,
    ) -> "Graph.Result[GraphValueT]": ...

    async def run(
        self,
        values: "Graph.Values[GraphValueT] | _MissingRunValues" = _MISSING_RUN_VALUES,
        /,
        *,
        run_id: str | None = None,
        state: "Graph.State | None" = None,
        continuation: "Graph.Continuation[GraphValueT] | None" = None,
        resume: tuple["Graph.ResumeAction[GraphValueT]", ...] = (),
        commit: "Graph.Commit[GraphValueT] | None" = None,
        max_supersteps: int = 1_000,
        max_parallel_tasks: int = 64,
    ) -> "Graph.Result[GraphValueT]":
        limits = ExecutionLimits(max_supersteps, max_parallel_tasks)
        invocation: _GraphValues[GraphValueT] | GraphRunState
        if isinstance(values, _GraphValues):
            if state is not None or continuation is not None or resume:
                raise SnapshotMismatchError("new graph run cannot carry state, continuation, or resume actions")
            invocation = _require_graph_values(values)
        elif values is _MISSING_RUN_VALUES and state is not None and run_id is None:
            invocation = state
        else:
            raise SnapshotMismatchError("state runs require state, forbid run_id, and do not accept values")
        owner = self._compile()
        graph = owner.graph
        recovered = False
        if isinstance(invocation, _GraphValues):
            effective_run_id = GraphRunId(str(uuid4()) if run_id is None else canonical_port_name(run_id, kind="run"))
            scope_run = root_scope_run(effective_run_id)
            input_candidate = admit_graph_input(graph, invocation)
            executor = GraphExecutor(graph)
            command = executor.start_command(effective_run_id)
            current = await commit_transition(
                scope_run,
                None,
                command,
                None,
                scoped_commit(scope_run, commit),
            )
            root = await fresh_root(
                graph,
                scope_run,
                current,
                input_candidate,
                executor,
                limits,
                commit,
            )
        else:
            resumed_scopes = {action.scope for action in resume}
            substitution_actions = tuple(
                action for action in resume if isinstance(action, SkipFailedNodeRequest) and action.output is not None
            )
            if continuation is None and len(resumed_scopes) > 1 and substitution_actions:
                identities = tuple((tuple(action.scope), action.node_id) for action in substitution_actions)
                raise GraphValueUnavailableError(
                    "state-only multi-scope substitution cannot preserve a partially confirmed "
                    f"publication checkpoint before commit; actions={identities!r}"
                )
            if continuation is None:
                child_states: tuple[ChildStateBinding, ...] = ()
                frames: ScopedFrameIndex[GraphValueT] = ScopedFrameIndex()
                recovered = True
            else:
                snapshot = _admit_continuation(owner.family_identity, invocation, continuation)
                child_states = snapshot.child_states
                frames = snapshot.frames
                recovered = _continuation_recovered(snapshot)
            lineage = lineage_states(invocation, child_states)
            validate_context(graph, lineage, frames, recovered=recovered)
            executors = executors_for(graph, lineage)
            planned_states, fences = plan_fences(graph, lineage)
            planned_states, candidate_frames, planned_resumes, facts = plan_resumes(
                graph,
                planned_states,
                frames,
                resume,
                executors,
            )
            admit_state_owned_overrides(graph, planned_states, candidate_frames.confirmed)
            if recovered or any(
                action.output is None for action in resume if isinstance(action, SkipFailedNodeRequest)
            ):
                preflight_recovery(
                    graph,
                    recovery_seed(planned_states, candidate_frames, limits, facts),
                )
            confirmed_prefix = False
            confirmed_states = lineage
            confirmed_frames = frames

            def partial_continuation() -> _GraphContinuation[GraphValueT]:
                root_binding = next(binding for binding in confirmed_states if not binding.scope_run.scope)
                confirmed_children = tuple(
                    ChildStateBinding(binding.scope_run, binding.parent_activation, binding.state)
                    for binding in confirmed_states
                    if binding.parent_activation is not None
                )
                return _make_continuation(
                    owner.family_identity,
                    root_binding.state,
                    confirmed_children,
                    confirmed_frames,
                    recovered=recovered,
                )

            for fence in fences:
                binding = next(item for item in confirmed_states if item.scope_run == fence.scope_run)
                try:
                    confirmed = await commit_transition(
                        fence.scope_run,
                        binding.state,
                        fence.command,
                        None,
                        scoped_commit(fence.scope_run, commit),
                    )
                except Exception as cause:
                    if confirmed_prefix:
                        root_binding = next(item for item in confirmed_states if not item.scope_run.scope)
                        raise _partial_commit_error(
                            root_binding.state,
                            partial_continuation(),
                            cause,
                            tuple(fence.scope_run.scope),
                        ) from cause
                    raise
                confirmed_states = replace_planned_state(confirmed_states, replace(binding, state=confirmed))
                confirmed_prefix = True
            for planned_resume in planned_resumes:
                binding = next(item for item in confirmed_states if item.scope_run == planned_resume.scope_run)
                try:
                    confirmed = await commit_transition(
                        planned_resume.scope_run,
                        binding.state,
                        planned_resume.prepared.command,
                        None,
                        scoped_commit(planned_resume.scope_run, commit),
                    )
                    installed_frames = install_confirmed_resume_frames(
                        confirmed_frames,
                        planned_resume,
                        confirmed,
                    )
                    confirmed_states = replace_planned_state(
                        confirmed_states,
                        replace(binding, state=confirmed),
                    )
                    confirmed_frames = installed_frames
                except Exception as cause:
                    if confirmed_prefix:
                        root_binding = next(item for item in confirmed_states if not item.scope_run.scope)
                        raise _partial_commit_error(
                            root_binding.state,
                            partial_continuation(),
                            cause,
                            tuple(planned_resume.scope_run.scope),
                        ) from cause
                    raise
                confirmed_prefix = True
            root_binding = next(item for item in confirmed_states if not item.scope_run.scope)
            confirmed_children = tuple(
                ChildStateBinding(item.scope_run, item.parent_activation, item.state)
                for item in confirmed_states
                if item.parent_activation is not None
            )
            root = await admit_root(
                graph,
                root_binding.state,
                confirmed_children,
                confirmed_frames,
                executors,
                limits,
                commit,
            )

        async def finish(abort_reason: GraphAbortReason | None) -> None:
            async def cleanup() -> None:
                primary: BaseException | None = None
                if abort_reason is not None:
                    try:
                        await root.abort(abort_reason)
                    except BaseException as error:
                        primary = error
                try:
                    await root.release()
                except BaseException as error:
                    if primary is None:
                        primary = error
                if primary is not None:
                    raise primary

            cleanup_task = asyncio.create_task(cleanup())
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError:
                    continue
            cleanup_task.result()

        try:
            disposition = await drive_root(root)
            result = project_graph_result(
                graph,
                owner.family_identity,
                root,
                disposition,
                recovered=recovered,
            )
        except asyncio.CancelledError as error:
            if root.consume_node_origin_cancellation(error):
                with suppress(BaseException):
                    await finish(None)
                raise
            await finish(GraphAbortReason("graph invocation was cancelled"))
            raise
        except BaseException:
            with suppress(BaseException):
                await finish(None)
            raise
        await finish(None)
        return result


__all__ = ["Graph"]
