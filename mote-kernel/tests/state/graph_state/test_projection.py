import pytest
from tests.execution.engine.factories import callable_node

from mote_kernel.execution import Graph
from mote_kernel.execution.errors import SnapshotMismatchError
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.definition import GraphDefinition
from mote_kernel.execution.graph.edge import DirectEdge
from mote_kernel.execution.graph.ports import normalize_graph_output_declarations
from mote_kernel.execution.graph.resume_input import ResumeInputBinding
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.state.graph_state import (
    GraphActivationIdentity,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphFrontierActivation,
    GraphNodeId,
    GraphResumeInputCodec,
    GraphResumeInputCodecId,
    GraphRunId,
    StartActivationCause,
    StartGraphRun,
    child_graph_run_id,
)


class StringCodec:
    def encode(self, value: Graph.Values[str]) -> bytes:
        return value["value"].encode()

    def decode(self, payload: bytes) -> Graph.Values[str]:
        return Graph.values(value=payload.decode())


def compiled(*, with_codec: bool = True):
    codec = StringCodec()
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(5),
            (callable_node("a"),),
            (DirectEdge(GraphNodeId("a"), END),),
            (),
            normalize_graph_output_declarations({}),
            resume_input=(
                ResumeInputBinding(GraphResumeInputCodecId("input.v1"), 2, codec, codec) if with_codec else None
            ),
        )
    )


def test_compiled_graph_projects_root_start_with_fixed_resume_codec() -> None:
    command = project_start_graph_command(compiled(), GraphRunId("root"))

    assert command == StartGraphRun(
        GraphRunId("root"),
        GraphDefinitionId("graph"),
        GraphDefinitionVersion(5),
        (GraphFrontierActivation(GraphNodeId("a"), StartActivationCause()),),
        resume_input_codec=GraphResumeInputCodec(GraphResumeInputCodecId("input.v1"), 2),
    )


def test_compiled_graph_projects_deterministic_child_start() -> None:
    parent = GraphActivationIdentity(GraphRunId("root"), 4, GraphNodeId("nested"))
    run_id = child_graph_run_id(parent.run_id, parent.superstep, parent.node_id)

    command = project_start_graph_command(compiled(), run_id, parent)

    assert command == StartGraphRun(
        run_id,
        GraphDefinitionId("graph"),
        GraphDefinitionVersion(5),
        (GraphFrontierActivation(GraphNodeId("a"), StartActivationCause()),),
        parent,
        GraphResumeInputCodec(GraphResumeInputCodecId("input.v1"), 2),
    )


def test_child_start_projection_rejects_arbitrary_run_identity() -> None:
    parent = GraphActivationIdentity(GraphRunId("root"), 4, GraphNodeId("nested"))

    with pytest.raises(SnapshotMismatchError, match="child graph run identity"):
        project_start_graph_command(compiled(), GraphRunId("arbitrary-child"), parent)


def test_compiled_graph_without_resume_codec_projects_none() -> None:
    assert project_start_graph_command(compiled(with_codec=False), GraphRunId("root")).resume_input_codec is None
