from collections.abc import Mapping
from dataclasses import replace

import pytest
from tests.execution.engine.factories import running_state

from mote_kernel.execution import Graph
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
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.ports import (
    GraphInputRef,
    NodeOutputRef,
    ResolvedInputBindings,
    normalize_graph_output_declarations,
    normalize_input_bindings,
    normalize_output_declarations,
)
from mote_kernel.execution.graph.resume_input import ResumeInputBinding
from mote_kernel.execution.graph.topology import CompiledGraph, frozen_map
from mote_kernel.execution.graph.values import NamedValue, _make_node_input_frame
from mote_kernel.execution.identity import StableActivation, root_scope_run
from mote_kernel.execution.run_context import (
    AdmittedResumeInput,
    ResumeInputAvailabilityCoordinate,
    ScopedFrameIndex,
)
from mote_kernel.state.graph_state import (
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphFrontierNode,
    GraphFrontierState,
    GraphNodeId,
    GraphResumeInputCodecId,
    GraphResumeInputPayload,
    GraphRunId,
    OverrideGraphNodeInput,
    PendingGraphNode,
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
    if data_dependency:
        consumer = callable_node("consumer", {"value": Graph.node_output("source", "value")})
        nodes = (source, consumer)
    resume_input = None if codec is None else ResumeInputBinding(GraphResumeInputCodecId("text.v1"), 1, codec, codec)
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            nodes,
            (),
            (),
            normalize_graph_output_declarations({}),
            resume_input=resume_input,
        )
    )


def without_publication_selection(graph: CompiledGraph[str]) -> CompiledGraph[str]:
    plan = graph.materializations[GraphNodeId("consumer")]
    binding = replace(plan.bindings.entries[0], publication=None)
    malformed_plan = replace(plan, bindings=ResolvedInputBindings((binding,)))
    materializations = frozen_map(
        {
            node_id: malformed_plan if node_id == GraphNodeId("consumer") else candidate
            for node_id, candidate in graph.materializations.items()
        }
    )
    return replace(
        graph,
        recovery=replace(
            graph.recovery,
            transition=replace(graph.transition, materializations=materializations),
        ),
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
                ),
            )
        ),
    )

    assert pending_node_input_available(graph, override_state, scope_run, ScopedFrameIndex(), node_id)

    plan = graph.materializations[node_id]
    frame = _make_node_input_frame(
        (NamedValue("value", "admitted"),),
        tuple((item.destination.local_name, item.descriptor) for item in plan.bindings.entries),
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


def test_failed_retry_materialization_requires_a_current_failed_node() -> None:
    graph = compiled_graph()
    state = running_state(frontier=("source",))

    with pytest.raises(SnapshotMismatchError, match="current failed node"):
        materialize_node_input(
            graph,
            state,
            root_scope_run(state.run_id),
            ScopedFrameIndex(),
            GraphNodeId("source"),
            failed_retry_input=UseStepRequestInput(),
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
