from typing import cast

import pytest
from tests.execution.engine.factories import compiled_graph, running_state

from mote_kernel.execution.engine.task import GraphTask, TaskId
from mote_kernel.execution.errors import NodeExecutionContractError, SnapshotMismatchError
from mote_kernel.execution.family_driver import admit_continued_root, project_graph_result
from mote_kernel.execution.graph_result import GraphInterruptView, _CompiledFamilyIdentity
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.result import AwaitingResume, FailedGraph, TaskFailure, _commit_result
from mote_kernel.execution.run_context import ScopedFrameIndex
from mote_kernel.state.graph_state import GraphInterruptId, GraphNodeId, GraphRunId


class TaskFailureSubclass(TaskFailure):
    pass


class AwaitingResumeSubclass(AwaitingResume):
    pass


@pytest.mark.parametrize(
    ("scope", "node_id", "interrupt_id", "payload"),
    [
        (cast(tuple[str, ...], []), "node", GraphInterruptId("interrupt"), b"payload"),
        ((" ",), "node", GraphInterruptId("interrupt"), b"payload"),
        ((), " ", GraphInterruptId("interrupt"), b"payload"),
        ((), "node", GraphInterruptId(" "), b"payload"),
        ((), "node", GraphInterruptId("interrupt"), cast(bytes, bytearray())),
    ],
)
def test_interrupt_view_rejects_each_malformed_coordinate_field(
    scope: tuple[str, ...],
    node_id: str,
    interrupt_id: GraphInterruptId,
    payload: bytes,
) -> None:
    with pytest.raises(SnapshotMismatchError, match="malformed typed coordinates"):
        GraphInterruptView(scope, node_id, interrupt_id, payload)


def test_commit_result_rejects_a_task_result_subclass() -> None:
    task = GraphTask(TaskId("task"), GraphRunId("run"), 0, GraphNodeId("node"))

    with pytest.raises(NodeExecutionContractError, match="unsupported variant"):
        _commit_result(TaskFailureSubclass(task, "failed"), None)


@pytest.mark.asyncio
async def test_graph_result_projection_rejects_a_boundary_subclass() -> None:
    graph = compiled_graph("a")
    state = running_state()
    identity = _CompiledFamilyIdentity()
    root, evidence_reader = await admit_continued_root(
        graph,
        state,
        (),
        ScopedFrameIndex(),
        ExecutionLimits(),
        None,
        (),
        (),
        identity,
        recovered=True,
    )

    with pytest.raises(SnapshotMismatchError, match="unsupported boundary"):
        project_graph_result(
            graph,
            identity,
            root,
            evidence_reader,
            AwaitingResumeSubclass(()),
            recovered=True,
            commit=None,
        )


@pytest.mark.asyncio
async def test_graph_result_projection_rejects_awaiting_without_interrupt_evidence() -> None:
    graph = compiled_graph("a")
    state = running_state()
    identity = _CompiledFamilyIdentity()
    root, evidence_reader = await admit_continued_root(
        graph,
        state,
        (),
        ScopedFrameIndex(),
        ExecutionLimits(),
        None,
        (),
        (),
        identity,
        recovered=True,
    )

    with pytest.raises(NodeExecutionContractError, match="at least one interrupt"):
        project_graph_result(
            graph,
            identity,
            root,
            evidence_reader,
            AwaitingResume(()),
            recovered=True,
            commit=None,
        )


@pytest.mark.asyncio
async def test_graph_result_projection_rejects_failed_without_failure_evidence() -> None:
    graph = compiled_graph("a")
    state = running_state()
    identity = _CompiledFamilyIdentity()
    root, evidence_reader = await admit_continued_root(
        graph,
        state,
        (),
        ScopedFrameIndex(),
        ExecutionLimits(),
        None,
        (),
        (),
        identity,
        recovered=True,
    )

    with pytest.raises(NodeExecutionContractError, match="at least one failure"):
        project_graph_result(
            graph,
            identity,
            root,
            evidence_reader,
            FailedGraph(),
            recovered=True,
            commit=None,
        )
