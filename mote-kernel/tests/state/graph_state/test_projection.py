import pytest

from mote_kernel.execution.errors import SnapshotMismatchError
from mote_kernel.execution.graph import (
    END,
    DirectEdge,
    GraphDefinition,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    NodeDefinition,
    NodeSuccess,
    ResumeInputBinding,
    compile_graph,
)
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.state.graph_state import (
    ContinueGraphRouting,
    GraphResumeInputCodec,
    GraphResumeInputCodecId,
    GraphRunId,
    ParentGraphActivation,
    StartGraphRun,
    child_graph_run_id,
)


class StringCodec:
    def encode(self, value: str) -> bytes:
        return value.encode()

    def decode(self, payload: bytes) -> str:
        return payload.decode()


async def identity(node_input: str) -> NodeSuccess[str]:
    return NodeSuccess(node_input, ContinueGraphRouting())


def compiled(*, with_codec: bool = True):
    codec = StringCodec()
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(5),
            (NodeDefinition(GraphNodeId("a"), identity),),
            (DirectEdge(GraphNodeId("a"), END),),
            (GraphNodeId("a"),),
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
        (GraphNodeId("a"),),
        resume_input_codec=GraphResumeInputCodec(GraphResumeInputCodecId("input.v1"), 2),
    )


def test_compiled_graph_projects_deterministic_child_start() -> None:
    parent = ParentGraphActivation(GraphRunId("root"), 4, GraphNodeId("nested"))
    run_id = child_graph_run_id(parent.run_id, parent.superstep, parent.node_id)

    command = project_start_graph_command(compiled(), run_id, parent)

    assert command == StartGraphRun(
        run_id,
        GraphDefinitionId("graph"),
        GraphDefinitionVersion(5),
        (GraphNodeId("a"),),
        parent,
        GraphResumeInputCodec(GraphResumeInputCodecId("input.v1"), 2),
    )


def test_child_start_projection_rejects_arbitrary_run_identity() -> None:
    parent = ParentGraphActivation(GraphRunId("root"), 4, GraphNodeId("nested"))

    with pytest.raises(SnapshotMismatchError, match="child graph run identity"):
        project_start_graph_command(compiled(), GraphRunId("arbitrary-child"), parent)


def test_compiled_graph_without_resume_codec_projects_none() -> None:
    assert project_start_graph_command(compiled(with_codec=False), GraphRunId("root")).resume_input_codec is None
