from collections.abc import Mapping
from dataclasses import replace

import pytest
from tests.execution.engine.factories import running_state

from mote_kernel.execution import Graph
from mote_kernel.execution.engine.admission import admit_graph_input
from mote_kernel.execution.engine.resume_input import (
    decode_resume_input,
    encode_resume_input,
    materialize_node_input,
    node_inputs_available,
    pending_node_input_available,
)
from mote_kernel.execution.errors import (
    GraphValueAdmissionError,
    GraphValueUnavailableError,
    SnapshotMismatchError,
)
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.definition import GraphDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.ports import (
    FeedbackInputBinding,
    GraphInputRef,
    NodeOutputRef,
    ResolvedInputBindings,
    normalize_graph_output_declarations,
    normalize_input_bindings,
    normalize_output_declarations,
)
from mote_kernel.execution.graph.resume_input import ResumeInputBinding
from mote_kernel.execution.graph.topology import CompiledGraph, frozen_map
from mote_kernel.execution.graph.values import NamedValue, _make_node_input_frame, _make_node_output_frame
from mote_kernel.execution.identity import StableActivation, root_scope_run
from mote_kernel.execution.run_context import (
    AdmittedGraphInput,
    AdmittedResumeInput,
    ConfirmedPublication,
    ExecutionPublicationProvenance,
    GraphInputAvailabilityCoordinate,
    PublicationAvailabilityCoordinate,
    ResumeInputAvailabilityCoordinate,
    ScopedFrameIndex,
)
from mote_kernel.state.graph_state import (
    ActivationReference,
    GraphActivationIdentity,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionAttemptId,
    GraphExecutionToken,
    GraphFrontierNode,
    GraphFrontierState,
    GraphNodeId,
    GraphResumeInputCodecId,
    GraphResumeInputPayload,
    GraphRouteId,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    OverrideGraphNodeInput,
    PendingGraphNode,
    RoutedActivationCause,
    StartActivationCause,
    UseStepRequestInput,
)


async def echo(values: Graph.Values[str]) -> Graph.Values[str]:
    return values


class TextCodec:
    def encode(self, value: Graph.Values[str]) -> bytes:
        return value["value"].encode()

    def decode(self, payload: bytes) -> Graph.Values[str]:
        return Graph.values(value=payload.decode())


class BytesSubclass(bytes):
    pass


class SubclassBytesEncoder(TextCodec):
    def encode(self, value: Graph.Values[str]) -> bytes:
        return BytesSubclass(value["value"].encode())


class ExplodingEncoder(TextCodec):
    def encode(self, value: Graph.Values[str]) -> bytes:
        raise RuntimeError(value["value"])


class ExplodingDecoder(TextCodec):
    def decode(self, payload: bytes) -> Graph.Values[str]:
        raise RuntimeError(payload)


class BytesDecoder:
    def decode(self, payload: bytes) -> bytes:
        return payload


def callable_node(
    node_id: str,
    inputs: Mapping[str, GraphInputRef[str] | NodeOutputRef],
) -> CallableNodeDefinition[str]:
    return CallableNodeDefinition(
        GraphNodeId(node_id),
        echo,
        normalize_input_bindings(inputs),
        normalize_output_declarations({"value": str}),
    )


def compiled_graph(*, codec: TextCodec | None = None, data_dependency: bool = False) -> CompiledGraph[str]:
    source = callable_node("source", {"value": Graph.graph_input("value", str)})
    nodes = (source,)
    edges: tuple[DirectEdge, ...] = ()
    if data_dependency:
        consumer = callable_node("consumer", {"value": Graph.node_output("source", "value")})
        nodes = (source, consumer)
        edges = (DirectEdge(GraphNodeId("source"), GraphNodeId("consumer")),)
    resume_input = None if codec is None else ResumeInputBinding(GraphResumeInputCodecId("text.v1"), 1, codec, codec)
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            nodes,
            edges,
            (),
            normalize_graph_output_declarations({}),
            resume_input=resume_input,
        )
    )


def feedback_compiled_graph() -> CompiledGraph[int]:
    async def loop(values: Graph.Values[int]) -> Graph.Values[int]:
        return values

    seed = Graph.graph_input("seed", int)
    node = CallableNodeDefinition(
        GraphNodeId("loop"),
        loop,
        normalize_input_bindings({"value": FeedbackInputBinding(seed, Graph.node_output("loop", "value"))}),
        normalize_output_declarations({"value": int}),
    )
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("feedback.materialization"),
            GraphDefinitionVersion(1),
            (node,),
            (
                ConditionalEdge(GraphNodeId("loop"), GraphRouteId("continue"), GraphNodeId("loop")),
                ConditionalEdge(GraphNodeId("loop"), GraphRouteId("done"), GraphNodeId("__end__")),
            ),
            (GraphNodeId("loop"),),
            normalize_graph_output_declarations({"value": Graph.node_output("loop", "value")}),
        )
    )


def multiple_feedback_compiled_graph() -> CompiledGraph[int]:
    async def loop(values: Graph.Values[int]) -> Graph.Values[int]:
        return values

    left_seed = Graph.graph_input("left_seed", int)
    right_seed = Graph.graph_input("right_seed", int)
    node = CallableNodeDefinition(
        GraphNodeId("loop"),
        loop,
        normalize_input_bindings(
            {
                "left": FeedbackInputBinding(left_seed, Graph.node_output("loop", "left")),
                "right": FeedbackInputBinding(right_seed, Graph.node_output("loop", "right")),
            }
        ),
        normalize_output_declarations({"left": int, "right": int}),
    )
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("feedback.multiple-materialization"),
            GraphDefinitionVersion(1),
            (node,),
            (
                ConditionalEdge(GraphNodeId("loop"), GraphRouteId("continue"), GraphNodeId("loop")),
                ConditionalEdge(GraphNodeId("loop"), GraphRouteId("done"), GraphNodeId("__end__")),
            ),
            (GraphNodeId("loop"),),
            normalize_graph_output_declarations({"value": Graph.node_output("loop", "left")}),
        )
    )


def feedback_state(
    graph: CompiledGraph[int],
    *,
    superstep: int = 1,
    predecessor_superstep: int = 0,
    include_publication: bool = True,
) -> tuple[GraphRunState, ScopedFrameIndex[int]]:
    run_id = GraphRunId("run")
    node_id = GraphNodeId("loop")
    route = next(iter(graph.transition.activation_rules.entries[0].repeat_gates[0][0][1]))
    cause = RoutedActivationCause(
        (
            ActivationReference(
                GraphActivationIdentity(run_id, predecessor_superstep, node_id),
                route,
            ),
        )
    )
    state = GraphRunState(
        run_id,
        GraphDefinitionId("feedback.materialization"),
        GraphDefinitionVersion(1),
        GraphRunStatus.RUNNING,
        superstep,
        GraphFrontierState((GraphFrontierNode(node_id, PendingGraphNode(UseStepRequestInput()), cause),)),
        settled_activations=(
            ActivationReference(
                GraphActivationIdentity(run_id, predecessor_superstep, node_id),
                route,
            ),
        ),
    )
    scope_run = root_scope_run(run_id)
    frames: ScopedFrameIndex[int] = ScopedFrameIndex()
    frames = frames.add_graph_input(
        AdmittedGraphInput(
            GraphInputAvailabilityCoordinate(scope_run, graph.graph_input_descriptor.identity),
            admit_graph_input(graph, Graph.values(seed=7)),
        )
    )
    if include_publication:
        descriptor = graph.transition.publications[node_id]
        publication = _make_node_output_frame(Graph.values(value=11), descriptor.declarations)
        coordinate: PublicationAvailabilityCoordinate[int] = PublicationAvailabilityCoordinate(
            StableActivation(scope_run, predecessor_superstep, node_id),
            descriptor.identity,
        )
        frames = frames.add_publication(
            ConfirmedPublication(
                coordinate,
                publication,
                1,
                ExecutionPublicationProvenance(GraphExecutionToken(1, GraphExecutionAttemptId("attempt"))),
            )
        )
    return state, frames


def multiple_feedback_state(
    graph: CompiledGraph[int],
) -> tuple[GraphRunState, ScopedFrameIndex[int]]:
    run_id = GraphRunId("multiple-run")
    node_id = GraphNodeId("loop")
    route = next(iter(graph.transition.activation_rules.entries[0].repeat_gates[0][0][1]))
    reference = ActivationReference(
        GraphActivationIdentity(run_id, 0, node_id),
        route,
    )
    state = GraphRunState(
        run_id,
        GraphDefinitionId("feedback.multiple-materialization"),
        GraphDefinitionVersion(1),
        GraphRunStatus.RUNNING,
        1,
        GraphFrontierState(
            (GraphFrontierNode(node_id, PendingGraphNode(UseStepRequestInput()), RoutedActivationCause((reference,))),)
        ),
        settled_activations=(reference,),
    )
    scope_run = root_scope_run(run_id)
    frames: ScopedFrameIndex[int] = ScopedFrameIndex()
    frames = frames.add_graph_input(
        AdmittedGraphInput(
            GraphInputAvailabilityCoordinate(scope_run, graph.graph_input_descriptor.identity),
            admit_graph_input(graph, Graph.values(left_seed=7, right_seed=8)),
        )
    )
    descriptor = graph.transition.publications[node_id]
    frames = frames.add_publication(
        ConfirmedPublication(
            PublicationAvailabilityCoordinate(
                StableActivation(scope_run, 0, node_id),
                descriptor.identity,
            ),
            _make_node_output_frame(Graph.values(left=11, right=22), descriptor.declarations),
            1,
            ExecutionPublicationProvenance(GraphExecutionToken(1, GraphExecutionAttemptId("attempt"))),
        )
    )
    return state, frames


def test_feedback_materialization_reads_the_exact_immediate_predecessor() -> None:
    graph = feedback_compiled_graph()
    state, frames = feedback_state(graph)

    materialized = materialize_node_input(
        graph,
        state,
        root_scope_run(state.run_id),
        frames,
        GraphNodeId("loop"),
    )

    assert materialized.entries == (NamedValue("value", 11),)


def test_multiple_feedback_materialization_reads_each_fixed_publication_source() -> None:
    graph = multiple_feedback_compiled_graph()
    state, frames = multiple_feedback_state(graph)

    materialized = materialize_node_input(
        graph,
        state,
        root_scope_run(state.run_id),
        frames,
        GraphNodeId("loop"),
    )

    assert materialized.entries == (NamedValue("left", 11), NamedValue("right", 22))


def test_feedback_materialization_rejects_a_non_immediate_predecessor() -> None:
    graph = feedback_compiled_graph()
    state, frames = feedback_state(graph, superstep=2, predecessor_superstep=0)

    with pytest.raises(SnapshotMismatchError, match="immediate predecessor"):
        materialize_node_input(
            graph,
            state,
            root_scope_run(state.run_id),
            frames,
            GraphNodeId("loop"),
        )


def test_feedback_materialization_uses_the_latest_cause_predecessor_not_an_older_publication() -> None:
    graph = feedback_compiled_graph()
    state, frames = feedback_state(graph, superstep=2, predecessor_superstep=1)
    descriptor = graph.transition.publications[GraphNodeId("loop")]
    older_coordinate: PublicationAvailabilityCoordinate[int] = PublicationAvailabilityCoordinate(
        StableActivation(root_scope_run(state.run_id), 0, GraphNodeId("loop")),
        descriptor.identity,
    )
    frames = frames.add_publication(
        ConfirmedPublication(
            older_coordinate,
            _make_node_output_frame(Graph.values(value=3), descriptor.declarations),
            1,
            ExecutionPublicationProvenance(GraphExecutionToken(1, GraphExecutionAttemptId("attempt"))),
        )
    )

    materialized = materialize_node_input(
        graph,
        state,
        root_scope_run(state.run_id),
        frames,
        GraphNodeId("loop"),
    )

    assert materialized.entries == (NamedValue("value", 11),)


def test_feedback_materialization_does_not_fall_back_to_the_seed_when_publication_is_missing() -> None:
    graph = feedback_compiled_graph()
    state, frames = feedback_state(graph, include_publication=False)

    with pytest.raises(GraphValueUnavailableError, match="node output"):
        materialize_node_input(
            graph,
            state,
            root_scope_run(state.run_id),
            frames,
            GraphNodeId("loop"),
        )


def test_feedback_materialization_does_not_accept_a_cached_input_frame_instead_of_the_publication() -> None:
    graph = feedback_compiled_graph()
    state, frames = feedback_state(graph, include_publication=False)
    plan = graph.transition.materializations[GraphNodeId("loop")]
    cached = _make_node_input_frame(
        (NamedValue("value", 999),),
        plan.descriptor.declarations,
    )
    frames = frames.add_resume_input(
        AdmittedResumeInput(
            ResumeInputAvailabilityCoordinate(
                StableActivation(root_scope_run(state.run_id), state.superstep, GraphNodeId("loop")),
                plan.descriptor.identity,
            ),
            cached,
        )
    )

    with pytest.raises(GraphValueUnavailableError, match="node output"):
        materialize_node_input(
            graph,
            state,
            root_scope_run(state.run_id),
            frames,
            GraphNodeId("loop"),
        )


def test_feedback_materialization_rejects_an_input_override() -> None:
    graph = feedback_compiled_graph()
    state, frames = feedback_state(graph)
    overridden = replace(
        state,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    GraphNodeId("loop"),
                    PendingGraphNode(OverrideGraphNodeInput(GraphResumeInputPayload(b"override"))),
                    state.frontier.nodes[0].cause,
                ),
            )
        ),
    )

    with pytest.raises(SnapshotMismatchError, match="feedback activation cannot use an input override"):
        materialize_node_input(
            graph,
            overridden,
            root_scope_run(overridden.run_id),
            frames,
            GraphNodeId("loop"),
        )


def test_feedback_input_availability_requires_authoritative_state() -> None:
    graph = feedback_compiled_graph()

    with pytest.raises(SnapshotMismatchError, match="authoritative graph state"):
        node_inputs_available(
            graph,
            root_scope_run(GraphRunId("run")),
            1,
            ScopedFrameIndex(),
            GraphNodeId("loop"),
        )


@pytest.mark.parametrize("case", ["scope", "superstep"])
def test_feedback_input_availability_requires_the_exact_state_coordinate(case: str) -> None:
    graph = feedback_compiled_graph()
    state, frames = feedback_state(graph)
    scope_run = root_scope_run(GraphRunId("other")) if case == "scope" else root_scope_run(state.run_id)
    activation_superstep = state.superstep - 1 if case == "superstep" else state.superstep

    with pytest.raises(SnapshotMismatchError, match=r"scope|coordinate"):
        node_inputs_available(
            graph,
            scope_run,
            activation_superstep,
            frames,
            GraphNodeId("loop"),
            state,
        )


def test_feedback_input_availability_requires_its_current_state_activation() -> None:
    graph = feedback_compiled_graph()
    state, frames = feedback_state(graph)
    missing = replace(state, frontier=GraphFrontierState(()))

    with pytest.raises(SnapshotMismatchError, match="not present in the current frontier"):
        node_inputs_available(
            graph,
            root_scope_run(state.run_id),
            state.superstep,
            frames,
            GraphNodeId("loop"),
            missing,
        )


def test_initial_feedback_input_requires_the_start_cause() -> None:
    graph = feedback_compiled_graph()
    state, frames = feedback_state(graph)
    initial = replace(state, superstep=0)

    with pytest.raises(SnapshotMismatchError, match="initial feedback activation"):
        node_inputs_available(
            graph,
            root_scope_run(state.run_id),
            0,
            frames,
            GraphNodeId("loop"),
            initial,
        )


def test_pending_feedback_availability_rejects_a_state_input_override() -> None:
    graph = feedback_compiled_graph()
    state, frames = feedback_state(graph)
    node = state.frontier.nodes[0]
    overridden = replace(
        state,
        frontier=GraphFrontierState(
            (
                replace(
                    node,
                    settlement=PendingGraphNode(
                        OverrideGraphNodeInput(GraphResumeInputPayload(b"override")),
                    ),
                ),
            )
        ),
    )

    with pytest.raises(SnapshotMismatchError, match="feedback activation cannot use an input override"):
        pending_node_input_available(
            graph,
            overridden,
            root_scope_run(state.run_id),
            frames,
            GraphNodeId("loop"),
        )


def without_publication_selection(graph: CompiledGraph[str]) -> CompiledGraph[str]:
    plan = graph.transition.materializations[GraphNodeId("consumer")]
    binding = replace(plan.bindings.entries[0], publication=None)
    malformed_plan = replace(plan, bindings=ResolvedInputBindings((binding,)))
    materializations = frozen_map(
        {
            node_id: malformed_plan if node_id == GraphNodeId("consumer") else candidate
            for node_id, candidate in graph.transition.materializations.items()
        }
    )
    return replace(
        graph,
        transition=replace(graph.transition, materializations=materializations),
    )


def test_resume_encoder_requires_exact_bytes() -> None:
    graph = compiled_graph(codec=SubclassBytesEncoder())

    with pytest.raises(GraphValueAdmissionError, match="must return bytes"):
        encode_resume_input(graph, Graph.values(value="input"))


def test_resume_encoder_exception_is_normalized_at_admission() -> None:
    graph = compiled_graph(codec=ExplodingEncoder())

    with pytest.raises(GraphValueAdmissionError, match="encoder rejected"):
        encode_resume_input(graph, Graph.values(value="input"))


def test_resume_decoder_exception_is_normalized_before_state_mutation() -> None:
    graph = compiled_graph(codec=ExplodingDecoder())

    with pytest.raises(GraphValueAdmissionError, match="decoder rejected"):
        decode_resume_input(graph, GraphNodeId("source"), b"input")


def test_resume_decoder_must_return_graph_values() -> None:
    graph = compiled_graph(codec=TextCodec())
    assert graph.resume_input is not None
    malformed_binding = replace(graph.resume_input, decoder=BytesDecoder())
    malformed = replace(graph, resume_input=malformed_binding)

    with pytest.raises(GraphValueAdmissionError, match=r"must return Graph\.Values"):
        decode_resume_input(malformed, GraphNodeId("source"), b"input")


def test_node_input_availability_reports_missing_graph_input() -> None:
    graph = compiled_graph()
    scope_run = root_scope_run(GraphRunId("run"))

    assert not node_inputs_available(
        graph,
        scope_run,
        0,
        ScopedFrameIndex(),
        GraphNodeId("source"),
    )


def test_node_input_availability_reports_missing_publication() -> None:
    graph = compiled_graph(data_dependency=True)
    scope_run = root_scope_run(GraphRunId("run"))

    assert not node_inputs_available(
        graph,
        scope_run,
        1,
        ScopedFrameIndex(),
        GraphNodeId("consumer"),
    )


def test_availability_rejects_compiled_node_output_without_selection() -> None:
    graph = without_publication_selection(compiled_graph(data_dependency=True))

    with pytest.raises(SnapshotMismatchError, match="lacks its activation selection"):
        node_inputs_available(
            graph,
            root_scope_run(GraphRunId("run")),
            1,
            ScopedFrameIndex(),
            GraphNodeId("consumer"),
        )


def test_materialization_rejects_compiled_node_output_without_selection() -> None:
    graph = without_publication_selection(compiled_graph(data_dependency=True))
    state = running_state(superstep=1, frontier=("consumer",))

    with pytest.raises(SnapshotMismatchError, match="lacks its activation selection"):
        materialize_node_input(
            graph,
            state,
            root_scope_run(state.run_id),
            ScopedFrameIndex(),
            GraphNodeId("consumer"),
        )


def test_materialization_reports_missing_confirmed_publication() -> None:
    graph = compiled_graph(data_dependency=True)
    state = running_state(superstep=1, frontier=("consumer",))

    with pytest.raises(GraphValueUnavailableError, match="node output"):
        materialize_node_input(
            graph,
            state,
            root_scope_run(state.run_id),
            ScopedFrameIndex(),
            GraphNodeId("consumer"),
        )


def test_pending_input_availability_requires_a_current_pending_node() -> None:
    graph = compiled_graph()
    state = running_state(frontier=("source",))

    with pytest.raises(SnapshotMismatchError, match="current pending node"):
        pending_node_input_available(
            graph,
            state,
            root_scope_run(state.run_id),
            ScopedFrameIndex(),
            GraphNodeId("missing"),
        )


def test_pending_input_availability_accepts_state_and_acknowledged_overrides() -> None:
    graph = compiled_graph()
    state = running_state(frontier=("source",))
    node_id = GraphNodeId("source")
    scope_run = root_scope_run(state.run_id)
    override_state = replace(
        state,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    node_id,
                    PendingGraphNode(OverrideGraphNodeInput(GraphResumeInputPayload(b"state-owned"))),
                    StartActivationCause(),
                ),
            )
        ),
    )

    assert pending_node_input_available(graph, override_state, scope_run, ScopedFrameIndex(), node_id)

    plan = graph.transition.materializations[node_id]
    frame = _make_node_input_frame(
        (NamedValue("value", "admitted"),),
        plan.descriptor.declarations,
    )
    coordinate: ResumeInputAvailabilityCoordinate[str] = ResumeInputAvailabilityCoordinate(
        StableActivation(scope_run, state.superstep, node_id),
        plan.descriptor.identity,
    )
    frames = ScopedFrameIndex(
        resume_inputs=(AdmittedResumeInput(coordinate, frame),),
    )

    assert pending_node_input_available(graph, state, scope_run, frames, node_id)


def test_materialization_requires_the_authoritative_graph_run_coordinate() -> None:
    graph = compiled_graph()
    state = running_state(frontier=("source",))

    with pytest.raises(SnapshotMismatchError, match="scope does not match"):
        materialize_node_input(
            graph,
            state,
            root_scope_run(GraphRunId("other")),
            ScopedFrameIndex(),
            GraphNodeId("source"),
        )


def test_materialization_reports_missing_admitted_graph_input() -> None:
    graph = compiled_graph()
    state = running_state(frontier=("source",))

    with pytest.raises(GraphValueUnavailableError, match="graph input"):
        materialize_node_input(
            graph,
            state,
            root_scope_run(state.run_id),
            ScopedFrameIndex(),
            GraphNodeId("source"),
        )
