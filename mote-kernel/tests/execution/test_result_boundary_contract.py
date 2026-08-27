import pytest
from tests.execution.engine.factories import compiled_graph, running_state

from mote_kernel.execution.engine.task import GraphTask, TaskId
from mote_kernel.execution.errors import NodeExecutionContractError, SnapshotMismatchError
from mote_kernel.execution.family_driver import project_graph_result
from mote_kernel.execution.result import AwaitingResume, TaskFailure, _commit_result
from mote_kernel.execution.run_context import ScopedFrameIndex, _new_context, _new_family_identity
from mote_kernel.state.graph_state import GraphNodeId, GraphRunId


class TaskFailureSubclass(TaskFailure):
    pass


class AwaitingResumeSubclass(AwaitingResume):
    pass


def test_commit_result_rejects_a_task_result_subclass() -> None:
    task = GraphTask(TaskId("task"), GraphRunId("run"), 0, GraphNodeId("node"))

    with pytest.raises(NodeExecutionContractError, match="unsupported variant"):
        _commit_result(TaskFailureSubclass(task, "failed"))


def test_graph_result_projection_rejects_a_boundary_subclass() -> None:
    context = _new_context(
        _new_family_identity(),
        running_state(),
        ScopedFrameIndex(),
        recovered=True,
    )

    with pytest.raises(SnapshotMismatchError, match="unsupported boundary"):
        project_graph_result(compiled_graph("a"), context, AwaitingResumeSubclass((), ()))
