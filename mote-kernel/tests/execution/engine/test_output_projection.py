from dataclasses import replace

import pytest
from tests.execution.engine.factories import activation_config, running_state
from tests.execution.graph.factories import graph, node

from mote_kernel.execution import Graph
from mote_kernel.execution.engine.admission import project_graph_outputs
from mote_kernel.execution.engine.routing import graph_outputs_available, resolve_routing
from mote_kernel.execution.errors import GraphValueAdmissionError, InvalidRoutingCommandError, SnapshotMismatchError
from mote_kernel.execution.graph.compiler import GraphCompiler
from mote_kernel.execution.graph.ports import GraphOutputBindings, normalize_graph_output_declarations
from mote_kernel.execution.graph.values import _make_graph_input_frame, _make_node_output_frame
from mote_kernel.execution.identity import StableActivation, root_scope_run
from mote_kernel.execution.run_context import (
    AdmittedGraphInput,
    ConfirmedPublication,
    ExecutionPublicationProvenance,
    GraphInputAvailabilityCoordinate,
    PublicationAvailabilityCoordinate,
    ScopedFrameIndex,
)
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    ContinueGraphRouting,
    GraphAbortReason,
    GraphExecutionAttemptId,
    GraphExecutionToken,
    GraphFrontierNode,
    GraphFrontierState,
    GraphNodeId,
    GraphRunId,
    StartActivationCause,
    SucceededGraphNode,
)


def output_graph():
    return GraphCompiler(
        graph(
            nodes=(node("source"),),
            outputs=normalize_graph_output_declarations({"value": Graph.node_output("source", "value")}),
        )
    ).compile()


def test_output_projection_rejects_a_compiled_binding_without_activation_selection() -> None:
    compiled = output_graph()
    malformed_binding = replace(compiled.transition.graph_outputs.entries[0], publication=None)
    malformed = replace(
        compiled,
        transition=replace(
            compiled.transition,
            graph_outputs=GraphOutputBindings((malformed_binding,)),
        ),
    )

    with pytest.raises(GraphValueAdmissionError, match="lacks its activation selection"):
        project_graph_outputs(
            malformed,
            root_scope_run(GraphRunId("run")),
            0,
            ScopedFrameIndex(),
        )


def test_output_projection_reports_a_missing_confirmed_publication() -> None:
    compiled = output_graph()

    with pytest.raises(GraphValueAdmissionError, match="is unavailable"):
        project_graph_outputs(
            compiled,
            root_scope_run(GraphRunId("run")),
            0,
            ScopedFrameIndex(),
        )


def test_output_projection_preserves_missing_graph_input_snapshot_boundary() -> None:
    compiled = GraphCompiler(
        graph(
            nodes=(node("source"),),
            outputs=normalize_graph_output_declarations({"input": Graph.graph_input("value", str)}),
        )
    ).compile()

    with pytest.raises(SnapshotMismatchError, match="no frame at coordinate"):
        project_graph_outputs(
            compiled,
            root_scope_run(GraphRunId("run")),
            0,
            ScopedFrameIndex(),
        )


def test_output_projection_rejects_sources_with_different_activation_configs() -> None:
    compiled = GraphCompiler(
        graph(
            nodes=(node("source"),),
            outputs=normalize_graph_output_declarations(
                {
                    "input": Graph.graph_input("value", str),
                    "output": Graph.node_output("source", "value"),
                }
            ),
        )
    ).compile()
    scope_run = root_scope_run(GraphRunId("run"))
    input_frame = _make_graph_input_frame(
        Graph.values(value="input"),
        compiled.graph_input_descriptor.declarations,
        activation_config=activation_config(1),
    )
    publication_descriptor = compiled.transition.publications[GraphNodeId("source")]
    output_frame = _make_node_output_frame(
        Graph.values(value="output"),
        publication_descriptor.declarations,
        activation_config=activation_config(2),
    )
    frames = ScopedFrameIndex().add_graph_input(
        AdmittedGraphInput(
            GraphInputAvailabilityCoordinate(scope_run, compiled.graph_input_descriptor.identity),
            input_frame,
        )
    )
    frames = frames.add_publication(
        ConfirmedPublication(
            PublicationAvailabilityCoordinate(
                StableActivation(scope_run, 0, GraphNodeId("source")),
                publication_descriptor.identity,
            ),
            output_frame,
            1,
            ExecutionPublicationProvenance(GraphExecutionToken(1, GraphExecutionAttemptId("attempt"))),
        )
    )

    with pytest.raises(GraphValueAdmissionError, match="different activation Config snapshots"):
        project_graph_outputs(compiled, scope_run, 0, frames)


def test_graph_output_availability_reports_a_missing_admitted_graph_input() -> None:
    compiled = GraphCompiler(
        graph(
            nodes=(node("complete"),),
            outputs=normalize_graph_output_declarations({"result": Graph.graph_input("value", str)}),
        )
    ).compile()

    assert not graph_outputs_available(
        compiled,
        root_scope_run(GraphRunId("run")),
        0,
        ScopedFrameIndex(),
    )


def test_graph_output_availability_rejects_a_missing_compiled_selection() -> None:
    compiled = output_graph()
    malformed_binding = replace(compiled.transition.graph_outputs.entries[0], publication=None)
    malformed = replace(
        compiled,
        transition=replace(
            compiled.transition,
            graph_outputs=GraphOutputBindings((malformed_binding,)),
        ),
    )

    with pytest.raises(InvalidRoutingCommandError, match="lacks its activation selection"):
        graph_outputs_available(
            malformed,
            root_scope_run(GraphRunId("run")),
            0,
            ScopedFrameIndex(),
        )


def test_graph_output_availability_reports_a_missing_confirmed_publication() -> None:
    compiled = output_graph()

    assert not graph_outputs_available(
        compiled,
        root_scope_run(GraphRunId("run")),
        0,
        ScopedFrameIndex(),
    )


def test_routing_aborts_when_completion_output_is_unavailable() -> None:
    compiled = output_graph()
    state = running_state(frontier=("source",))
    state = replace(
        state,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    state.frontier.nodes[0].node_id, SucceededGraphNode(ContinueGraphRouting()), StartActivationCause()
                ),
            )
        ),
    )

    command = resolve_routing(
        compiled,
        state,
        root_scope_run(state.run_id),
        ScopedFrameIndex(),
    )

    assert command == AbortGraphRun(
        state.revision,
        GraphAbortReason("required graph output values are unavailable at completion"),
    )
