"""Single public graph composition and execution facade."""

import asyncio
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import ClassVar, Generic, Never, Self, TypeAlias, TypeVar, cast, overload
from uuid import uuid4

from mote_kernel.execution.cancellation import wait_for_owner_task
from mote_kernel.execution.commit import (
    GraphCommit,
    GraphCommitKey,
    GraphCommitWriteSet,
    GraphTransition,
)
from mote_kernel.execution.engine.admission import admit_graph_input
from mote_kernel.execution.engine.recovery import preflight_recovery
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
from mote_kernel.execution.family_driver import (
    admit_continued_root,
    fresh_root,
    project_graph_result,
)
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.constants import END, START
from mote_kernel.execution.graph.definition import GraphDefinition, NestedGraphNodeDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge, Edge, JoinEdge
from mote_kernel.execution.graph.node import (
    CallableNodeDefinition,
    NodeCallable,
    NodeInputMaterializer,
    NodeInputs,
    NodeOperation,
    TypedNodeAssembly,
    make_typed_node_assembly,
)
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
    NodeInputSlot,
    NodeOutputRef,
    NominalTypeDescriptor,
    PredecessorOutputRef,
    TypedInputBinding,
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
    lineage_states,
    plan_fences,
    plan_resumes,
    recovery_seed,
    validate_context,
)
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.node_adapter import make_typed_node_invoker
from mote_kernel.execution.request import (
    OverrideNodeInput,
    ResumeInterruptedNodeRequest,
    ResumeNodeRequest,
)
from mote_kernel.execution.resource import ResourceDefinition, ResourceId
from mote_kernel.execution.result import (
    GraphResult,
    _AbortedGraphResult,
    _AwaitingResumeGraphResult,
    _CompletedGraphResult,
    _FailedGraphResult,
    _GraphFailureResult,
    _GraphInterruptResult,
    _GraphSuccessResult,
    _PartialCommitError,
)
from mote_kernel.execution.run_context import (
    ChildStateBinding,
    ScopedFrameIndex,
    _admit_continuation,
    _CompiledFamilyIdentity,
    _continuation_recovered,
    _GraphContinuation,
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
InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")
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
    Inputs = NodeInputs
    InputBinding = TypedInputBinding
    OutputRef = NodeOutputRef
    SuccessOutcome = _GraphSuccessOutcome
    FailureOutcome = _GraphFailureOutcome
    InterruptOutcome = _GraphInterruptOutcome
    Outcome = GraphOutcome
    ResumeAction = ResumeNodeRequest
    Commit = GraphCommit
    CommitKey = GraphCommitKey
    CommitWriteSet = GraphCommitWriteSet
    Transition = GraphTransition
    SuccessResult = _GraphSuccessResult
    FailureResult = _GraphFailureResult
    InterruptResult = _GraphInterruptResult
    Continuation = _GraphContinuation
    PartialCommitError = _PartialCommitError
    CompletedResult = _CompletedGraphResult
    FailedResult = _FailedGraphResult
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
    @overload
    def node_output(output_name: str, /) -> PredecessorOutputRef[Never]: ...

    @staticmethod
    @overload
    def node_output(node_id: str, output_name: str, /) -> NodeOutputRef[Never]: ...

    @staticmethod
    @overload
    def node_output(output: NodeOutputRef[ValueT], /) -> PredecessorOutputRef[ValueT]: ...

    @staticmethod
    def node_output(
        node_id_or_output_name: str | NodeOutputRef[ValueT],
        output_name: str | None = None,
        /,
    ) -> NodeOutputRef[Never] | PredecessorOutputRef[Never] | PredecessorOutputRef[ValueT]:
        """Reference either one fixed producer or the actual control predecessor."""

        if isinstance(node_id_or_output_name, NodeOutputRef):
            if output_name is not None or node_id_or_output_name.descriptor is None:
                raise GraphValidationError("typed predecessor output requires one typed node output")
            return PredecessorOutputRef(
                node_id_or_output_name.output_name,
                node_id_or_output_name.descriptor,
            )
        if output_name is None:
            return PredecessorOutputRef(canonical_port_name(node_id_or_output_name, kind="source output"))
        return NodeOutputRef(
            GraphNodeId(canonical_port_name(node_id_or_output_name, kind="source node")),
            canonical_port_name(output_name, kind="source output"),
        )

    @staticmethod
    def bind(
        name: str,
        source: GraphInputRef[ValueT] | NodeOutputRef[ValueT] | PredecessorOutputRef[ValueT],
        /,
    ) -> TypedInputBinding[ValueT]:
        """Bind one typed source without repeating its nominal descriptor."""

        if type(source) not in (GraphInputRef, NodeOutputRef, PredecessorOutputRef):
            raise GraphValidationError("typed input must bind one graph input or typed node output")
        descriptor = source.descriptor
        if descriptor is None:
            raise GraphValidationError("typed input source must come from a typed Graph handle")
        destination = NodeInputSlot(canonical_port_name(name, kind="input"), descriptor)
        return TypedInputBinding(destination, source)

    def output_ref(
        self,
        node_id: str,
        output_name: str,
        /,
    ) -> NodeOutputRef[GraphValueT]:
        """Return a typed handle for a declared node or nested graph output.

        ``Graph.node_output()`` remains the address-only constructor used by
        legacy mappings.  This instance method is the one typed composition
        entry point: it resolves the declaration in the current builder and,
        for a nested node, follows that child's public ``set_outputs``
        boundary.  The descriptor is always the object owned by the original
        declaration; no type is inferred from a runtime value and no fresh
        descriptor is manufactured at the parent boundary.
        """
        canonical_node = GraphNodeId(canonical_port_name(node_id, kind="source node"))
        canonical_output = canonical_port_name(output_name, kind="source output")
        descriptor = self._resolve_declared_output_descriptor(
            canonical_node,
            canonical_output,
            {self},
        )
        return NodeOutputRef(canonical_node, canonical_output, descriptor)

    @staticmethod
    def _validated_descriptor(
        descriptor: NominalTypeDescriptor[GraphValueT],
        *,
        owner: str,
    ) -> NominalTypeDescriptor[GraphValueT]:
        if type(descriptor) is not NominalTypeDescriptor:
            raise GraphValidationError(f"{owner} has a malformed nominal descriptor")
        try:
            canonical_nominal_type(descriptor.value_type)
        except GraphValidationError as error:
            raise GraphValidationError(f"{owner} has a non-concrete nominal descriptor") from error
        return descriptor

    def _graph_input_descriptor(
        self,
        name: str,
        /,
    ) -> NominalTypeDescriptor[GraphValueT]:
        """Resolve one local graph-input descriptor like the compiler does."""

        selected: NominalTypeDescriptor[GraphValueT] | None = None
        for candidate in self._builder_state.nodes:
            for binding in candidate.inputs.entries:
                source = binding.source
                if not isinstance(source, GraphInputRef) or source.name != name:
                    continue
                descriptor = self._validated_descriptor(
                    source.descriptor,
                    owner=f"graph input {name!r}",
                )
                if selected is not None and selected.value_type is not descriptor.value_type:
                    raise GraphValidationError(f"graph input {name!r} has conflicting exact type declarations")
                selected = descriptor
        outputs = self._builder_state.outputs
        if outputs is not None:
            for declaration in outputs.entries:
                source = declaration.source
                if not isinstance(source, GraphInputRef) or source.name != name:
                    continue
                descriptor = self._validated_descriptor(
                    source.descriptor,
                    owner=f"graph input {name!r}",
                )
                if selected is not None and selected.value_type is not descriptor.value_type:
                    raise GraphValidationError(f"graph input {name!r} has conflicting exact type declarations")
                selected = descriptor
        if selected is None:
            raise GraphValidationError(f"graph input {name!r} is not declared")
        return selected

    def _resolve_boundary_source_descriptor(
        self,
        source: GraphInputRef[GraphValueT] | NodeOutputRef[GraphValueT],
        visiting: set["Graph[GraphValueT]"],
        /,
    ) -> NominalTypeDescriptor[GraphValueT]:
        if isinstance(source, GraphInputRef):
            # Graph input declarations are intentionally compared by concrete
            # value type, matching compiler input collection.  Repeated
            # Graph.graph_input() calls may therefore use equal-type descriptor
            # objects while the compiler's selected declaration remains the
            # authoritative identity.
            self._validated_descriptor(source.descriptor, owner=f"graph input {source.name!r}")
            return self._graph_input_descriptor(source.name)
        if type(source) is not NodeOutputRef:
            raise GraphValidationError("graph output boundary must bind a graph input or node output")
        descriptor = self._resolve_declared_output_descriptor(
            source.node_id,
            source.output_name,
            visiting,
        )
        if source.descriptor is not None:
            declared = self._validated_descriptor(
                source.descriptor,
                owner=f"node output {source.node_id!r}.{source.output_name!r}",
            )
            if declared is not descriptor:
                raise GraphValidationError(
                    f"declared output {source.node_id!r}.{source.output_name!r} has a foreign descriptor"
                )
        return descriptor

    def _resolve_declared_output_descriptor(
        self,
        node_id: GraphNodeId,
        output_name: str,
        visiting: set["Graph[GraphValueT]"],
        /,
    ) -> NominalTypeDescriptor[GraphValueT]:
        matches = tuple(candidate for candidate in self._builder_state.nodes if candidate.node_id == node_id)
        if len(matches) != 1:
            raise GraphValidationError(f"typed output handle requires exactly one declared node {node_id!r}")
        candidate = matches[0]
        if isinstance(candidate, CallableNodeDefinition):
            declarations = tuple(entry for entry in candidate.outputs.entries if entry.name == output_name)
            if len(declarations) != 1:
                raise GraphValidationError(f"node {node_id!r} does not declare output {output_name!r}")
            declaration = declarations[0]
            return self._validated_descriptor(
                declaration.descriptor,
                owner=f"node output {node_id!r}.{output_name!r}",
            )

        child = candidate.graph
        if child in visiting:
            raise GraphValidationError("graph composition recursively contains itself")
        child_outputs = child._builder_state.outputs
        if child_outputs is None:
            raise GraphValidationError(
                f"nested node {node_id!r} child graph requires exactly one set_outputs() declaration"
            )
        boundaries = tuple(entry for entry in child_outputs.entries if entry.boundary_name == output_name)
        if len(boundaries) != 1:
            raise GraphValidationError(f"nested node {node_id!r} does not declare boundary output {output_name!r}")
        boundary = boundaries[0]
        child_visiting = set(visiting)
        child_visiting.add(child)
        return child._resolve_boundary_source_descriptor(boundary.source, child_visiting)

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
        inputs: Mapping[
            str,
            GraphInputRef[GraphValueT] | NodeOutputRef[GraphValueT] | PredecessorOutputRef[GraphValueT],
        ],
        outputs: Mapping[str, type[GraphValueT]],
        resources: tuple[str, ...] = (),
    ) -> Self: ...

    @overload
    def add_node(
        self,
        node_id: str,
        operation: NodeOperation[InputT, OutputT],
        *,
        inputs: tuple[TypedInputBinding[GraphValueT], ...],
        input_type: type[InputT],
        materialize: NodeInputMaterializer[GraphValueT, InputT],
        output_name: str,
        output_type: type[OutputT],
        resources: tuple[str, ...] = (),
    ) -> NodeOutputRef[OutputT]: ...

    @overload
    def add_node(
        self,
        node_id: str,
        operation: "Graph[GraphValueT]",
        *,
        inputs: Mapping[
            str,
            GraphInputRef[GraphValueT] | NodeOutputRef[GraphValueT] | PredecessorOutputRef[GraphValueT],
        ],
    ) -> Self: ...

    def add_node(
        self,
        node_id: str,
        operation: NodeCallable[GraphValueT] | NodeOperation[InputT, OutputT] | "Graph[GraphValueT]",
        *,
        inputs: Mapping[
            str,
            GraphInputRef[GraphValueT] | NodeOutputRef[GraphValueT] | PredecessorOutputRef[GraphValueT],
        ]
        | tuple[TypedInputBinding[GraphValueT], ...],
        outputs: Mapping[
            str,
            type[GraphValueT] | GraphInputRef[GraphValueT] | NodeOutputRef[GraphValueT],
        ]
        | None = None,
        input_type: type[InputT] | None = None,
        materialize: NodeInputMaterializer[GraphValueT, InputT] | None = None,
        output_name: str | None = None,
        output_type: type[OutputT] | None = None,
        resources: tuple[str, ...] = (),
    ) -> Self | NodeOutputRef[OutputT]:
        state = self._require_mutable()
        canonical_id = GraphNodeId(canonical_port_name(node_id, kind="node"))
        typed_fields = (
            input_type is not None,
            materialize is not None,
            output_name is not None,
            output_type is not None,
        )
        if any(typed_fields) and not all(typed_fields):
            raise GraphValidationError("typed graph nodes require input, materializer, and output declarations")
        typed = all(typed_fields)
        typed_assembly: TypedNodeAssembly[GraphValueT, InputT, OutputT] | None = None
        if isinstance(operation, Graph):
            if typed or outputs is not None or resources or type(inputs) is tuple:
                raise GraphValidationError("nested graph nodes do not declare parent outputs or resources")
            bindings = normalize_input_bindings(inputs)
            candidate: NodeCandidate[GraphValueT] = _NestedNodeCandidate(
                canonical_id,
                operation,
                bindings,
            )
            replacement = replace(state, nodes=(*state.nodes, candidate))
        else:
            if not callable(operation):
                raise GraphValidationError("ordinary graph node operation must be callable")
            resource_ids = _canonical_resources(resources)
            if typed:
                if outputs is not None or type(inputs) is not tuple:
                    raise GraphValidationError("typed graph nodes use typed bindings and one typed output")
                if input_type is None or materialize is None or output_name is None or output_type is None:
                    raise GraphValidationError("typed graph nodes require complete input and output declarations")
                typed_assembly = make_typed_node_assembly(
                    canonical_id,
                    cast(NodeOperation[InputT, OutputT], operation),
                    inputs,
                    input_type,
                    materialize,
                    output_name,
                    output_type,
                )
                candidate = CallableNodeDefinition(
                    canonical_id,
                    None,
                    typed_assembly.inputs,
                    typed_assembly.outputs,
                    resource_ids,
                    make_typed_node_invoker(typed_assembly.contract),
                )
            else:
                if type(inputs) is tuple or outputs is None:
                    raise GraphValidationError("callable graph nodes require explicit outputs and input mappings")
                bindings = normalize_input_bindings(inputs)
                declarations = normalize_output_declarations(outputs)
                candidate = CallableNodeDefinition(
                    canonical_id,
                    cast(NodeCallable[GraphValueT], operation),
                    bindings,
                    declarations,
                    resource_ids,
                )
            known = {resource.resource_id for resource in state.resources}
            added = tuple(ResourceDefinition(resource_id) for resource_id in resource_ids if resource_id not in known)
            replacement = replace(
                state,
                nodes=(*state.nodes, candidate),
                resources=(*state.resources, *added),
            )
        self._commit_builder(state, replacement)
        if typed:
            if typed_assembly is None:
                raise GraphValidationError("typed graph node assembly was not created")
            return typed_assembly.output_ref
        return self

    def set_outputs(
        self,
        outputs: Mapping[
            str,
            GraphInputRef[GraphValueT] | NodeOutputRef[GraphValueT] | type[GraphValueT],
        ],
    ) -> Self:
        state = self._require_mutable()
        if state.outputs is not None:
            raise GraphValidationError("graph outputs can be declared exactly once")
        declaration = normalize_graph_output_declarations(outputs)
        replacement = replace(state, outputs=declaration)
        self._commit_builder(state, replacement)
        return self

    @overload
    def add_edge(self, source: str, target: str, /) -> Self: ...

    @overload
    def add_edge(self, source: str, route: str, target: str, /) -> Self: ...

    def add_edge(
        self,
        source: str,
        route_or_target: str,
        target: str | None = None,
        /,
    ) -> Self:
        state = self._require_mutable()
        canonical_source = canonical_port_name(source, kind="edge source")
        if target is None:
            canonical_target = canonical_port_name(route_or_target, kind="edge target")
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
        else:
            canonical_route = canonical_port_name(route_or_target, kind="route")
            canonical_target = canonical_port_name(target, kind="conditional target")
            if canonical_source in (Graph.START, Graph.END) or canonical_target == Graph.START:
                raise GraphValidationError("conditional edge has an invalid boundary direction")
            replacement = replace(
                state,
                edges=(
                    *state.edges,
                    ConditionalEdge(
                        GraphNodeId(canonical_source),
                        GraphRouteId(canonical_route),
                        END if canonical_target == Graph.END else GraphNodeId(canonical_target),
                    ),
                ),
            )
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
                nodes.append(
                    NestedGraphNodeDefinition(
                        candidate.node_id,
                        child,
                        candidate.inputs,
                    )
                )
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
            owner: _CompiledOwner(graph, _CompiledFamilyIdentity())
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
            root_admission = fresh_root(
                graph,
                scope_run,
                input_candidate,
                limits,
                commit,
            )
        else:
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
            planned_lineage, fences = plan_fences(graph, lineage)
            planned_lineage, candidate_frames, planned_resumes, facts = plan_resumes(
                graph,
                planned_lineage,
                frames,
                resume,
            )
            admit_state_owned_overrides(graph, planned_lineage, candidate_frames)
            if recovered or resume:
                preflight_recovery(
                    graph,
                    recovery_seed(planned_lineage, candidate_frames, limits, facts),
                )
            root_admission = admit_continued_root(
                graph,
                invocation,
                child_states,
                frames,
                limits,
                commit,
                fences,
                planned_resumes,
                owner.family_identity,
                recovered=recovered,
            )
        (root, evidence_reader), setup_cancellation = await wait_for_owner_task(asyncio.create_task(root_admission))

        async def finish_root(abort_reason: GraphAbortReason | None) -> None:
            primary: BaseException | None = None
            if abort_reason is not None:
                try:
                    await wait_for_owner_task(asyncio.create_task(root.abort(abort_reason)))
                except BaseException as error:
                    primary = error
            try:
                await wait_for_owner_task(asyncio.create_task(root.release()))
            except BaseException as error:
                if primary is None:
                    primary = error
            if primary is not None:
                raise primary

        async def drive_project_finish() -> GraphResult[GraphValueT]:
            try:
                if setup_cancellation is not None:
                    raise setup_cancellation
                disposition = await root.drive_quantum()
                result = project_graph_result(
                    graph,
                    owner.family_identity,
                    root,
                    evidence_reader,
                    disposition,
                    recovered=recovered,
                )
            except asyncio.CancelledError as error:
                if root.consume_node_origin_cancellation(error) or root.consume_commit_origin_cancellation(error):
                    with suppress(BaseException):
                        await finish_root(None)
                    raise
                try:
                    await finish_root(GraphAbortReason("graph invocation was cancelled"))
                except BaseException as cleanup_error:
                    raise error from cleanup_error
                raise
            except BaseException:
                with suppress(BaseException):
                    await finish_root(None)
                raise
            await finish_root(None)
            return result

        return await drive_project_finish()


__all__ = ["Graph"]
