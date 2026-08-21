from dataclasses import replace

import pytest
from tests.execution.engine.factories import running_state
from tests.execution.graph.factories import graph, node

from mote_kernel.execution import Graph
from mote_kernel.execution.engine.admission import project_graph_outputs
from mote_kernel.execution.engine.routing import graph_outputs_available, resolve_routing
from mote_kernel.execution.errors import GraphValueAdmissionError, InvalidRoutingCommandError
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.ports import GraphOutputBindings, normalize_graph_output_declarations
from mote_kernel.execution.identity import root_scope_run
from mote_kernel.execution.run_context import ScopedFrameIndex
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    ContinueGraphRouting,
    GraphAbortReason,
    GraphFrontierNode,
    GraphFrontierState,
    GraphRunId,
    SucceededGraphNode,
)


def output_graph():
    return compile_graph(
        graph(
            nodes=(node("source"),),
            outputs=normalize_graph_output_declarations({"value": Graph.node_output("source", "value")}),
        )
    )


def test_output_projection_rejects_a_compiled_binding_without_activation_selection() -> None:
    compiled = output_graph()
    malformed_binding = replace(compiled.graph_outputs.entries[0], publication=None)
    malformed = replace(
        compiled,
        recovery=replace(
            compiled.recovery,
            transition=replace(
                compiled.transition,
                graph_outputs=GraphOutputBindings((malformed_binding,)),
            ),
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


def test_graph_output_availability_reports_a_missing_admitted_graph_input() -> None:
    compiled = compile_graph(
        graph(
            nodes=(node("complete"),),
            outputs=normalize_graph_output_declarations({"result": Graph.graph_input("value", str)}),
        )
    )

    assert not graph_outputs_available(
        compiled,
        root_scope_run(GraphRunId("run")),
        0,
        ScopedFrameIndex(),
    )


def test_graph_output_availability_rejects_a_missing_compiled_selection() -> None:
    compiled = output_graph()
    malformed_binding = replace(compiled.graph_outputs.entries[0], publication=None)
    malformed = replace(
        compiled,
        recovery=replace(
            compiled.recovery,
            transition=replace(
                compiled.transition,
                graph_outputs=GraphOutputBindings((malformed_binding,)),
            ),
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
            (GraphFrontierNode(state.frontier.nodes[0].node_id, SucceededGraphNode(ContinueGraphRouting())),)
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
