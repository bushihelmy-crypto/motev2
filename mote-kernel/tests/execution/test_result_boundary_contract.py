import pytest
from tests.execution.engine.factories import compiled_graph, running_state

from mote_kernel.execution.engine.task import GraphTask, TaskId
from mote_kernel.execution.errors import NodeExecutionContractError, SnapshotMismatchError
from mote_kernel.execution.executor import GraphExecutor
from mote_kernel.execution.family_driver import admit_root, project_graph_result
from mote_kernel.execution.identity import root_scope_run
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.result import AwaitingResume, TaskFailure, _commit_result
from mote_kernel.execution.run_context import ScopedFrameIndex, _new_family_identity
from mote_kernel.state.graph_state import GraphNodeId, GraphRunId


class TaskFailureSubclass(TaskFailure):
    pass


class AwaitingResumeSubclass(AwaitingResume):
    pass


def test_commit_result_rejects_a_task_result_subclass() -> None:
    task = GraphTask(TaskId("task"), GraphRunId("run"), 0, GraphNodeId("node"))

    with pytest.raises(NodeExecutionContractError, match="unsupported variant"):
        _commit_result(TaskFailureSubclass(task, "failed"))


@pytest.mark.asyncio
async def test_graph_result_projection_rejects_a_boundary_subclass() -> None:
    graph = compiled_graph("a")
    state = running_state()
    root, evidence_reader = await admit_root(
        graph,
        state,
        (),
        ScopedFrameIndex(),
        ((root_scope_run(state.run_id), GraphExecutor(graph)),),
        ExecutionLimits(),
        None,
    )

    with pytest.raises(SnapshotMismatchError, match="unsupported boundary"):
        project_graph_result(
            graph,
            _new_family_identity(),
            root,
            evidence_reader,
            AwaitingResumeSubclass((), ()),
            recovered=True,
        )
