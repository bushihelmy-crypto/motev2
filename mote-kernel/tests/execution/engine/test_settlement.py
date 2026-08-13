from dataclasses import replace

import pytest
from tests.execution.engine.factories import conditional, direct, leased_state, running_state, topology

from mote_kernel.execution.engine.planner import plan_tasks
from mote_kernel.execution.engine.settlement import settle_tasks
from mote_kernel.execution.engine.task import GraphTask
from mote_kernel.execution.errors import InvalidRoutingCommandError, ResultCollectionError, SnapshotMismatchError
from mote_kernel.execution.graph import (
    END,
    CompiledGraph,
    GraphDefinition,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    GraphRouteId,
    NodeDefinition,
    NodeSuccess,
    ResumeInputBinding,
    compile_graph,
)
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.result import TaskFailure, TaskInterrupt, TaskSuccess
from mote_kernel.state.graph_state import (
    AdvanceGraphFrontier,
    CompleteGraphFrontier,
    ContinueGraphRouting,
    FailedGraphNodeOutcome,
    GraphFailure,
    GraphFrontierNode,
    GraphFrontierState,
    GraphInterruptPayload,
    GraphResumeInputCodec,
    GraphResumeInputCodecId,
    GraphRunState,
    GraphSkipReason,
    InterruptedGraphNodeOutcome,
    SelectGraphRoute,
    SettleGraphExecution,
    SkippedGraphNode,
    SucceededGraphNode,
    SucceededGraphNodeOutcome,
)


def planned_and_leased(
    graph: CompiledGraph[str, str],
    state: GraphRunState | None = None,
) -> tuple[tuple[GraphTask, ...], GraphRunState]:
    initial = running_state() if state is None else state
    tasks = plan_tasks(graph, initial, ExecutionLimits())
    leased = leased_state(initial)
    assert leased.execution is not None
    return tasks, leased


class StringCodec:
    def encode(self, value: str) -> bytes:
        return value.encode()

    def decode(self, payload: bytes) -> str:
        return payload.decode()


async def _success(node_input: str) -> NodeSuccess[str]:
    return NodeSuccess(node_input)


def test_success_projects_state_owned_advance_and_complete_commands() -> None:
    graph = topology("a", "b", edges=(direct("a", "b"),))
    tasks, state = planned_and_leased(graph, running_state(superstep=4, revision=7))
    assert state.execution is not None
    assert settle_tasks(
        graph,
        state,
        tasks,
        (TaskSuccess(tasks[0], "output", ContinueGraphRouting()),),
    ) == SettleGraphExecution(
        7,
        state.execution.token,
        (SucceededGraphNodeOutcome(GraphNodeId("a"), ContinueGraphRouting()),),
        AdvanceGraphFrontier((GraphNodeId("b"),), ()),
    )

    terminal = topology("a", edges=(direct("a", END),))
    tasks, state = planned_and_leased(terminal)
    assert state.execution is not None
    assert (
        settle_tasks(
            terminal,
            state,
            tasks,
            (TaskSuccess(tasks[0], "transient", ContinueGraphRouting()),),
        ).resolution
        == CompleteGraphFrontier()
    )


def test_mixed_outcomes_are_all_preserved_without_early_routing() -> None:
    codec = StringCodec()
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            tuple(NodeDefinition(GraphNodeId(node_id), _success) for node_id in ("a", "b", "c")),
            (),
            (GraphNodeId("a"), GraphNodeId("b"), GraphNodeId("c")),
            resume_input=ResumeInputBinding(GraphResumeInputCodecId("input.v1"), 1, codec, codec),
        )
    )
    initial = running_state(frontier=("a", "b", "c"))
    initial = replace(
        initial,
        resume_input_codec=GraphResumeInputCodec(GraphResumeInputCodecId("input.v1"), 1),
    )
    tasks, state = planned_and_leased(graph, initial)
    assert state.execution is not None

    command = settle_tasks(
        graph,
        state,
        tasks,
        (
            TaskFailure(tasks[1], GraphFailure("failed")),
            TaskInterrupt(tasks[2], GraphInterruptPayload(b"question")),
            TaskSuccess(tasks[0], "output", ContinueGraphRouting()),
        ),
    )

    assert command.resolution is None
    assert command.outcomes[0] == SucceededGraphNodeOutcome(GraphNodeId("a"), ContinueGraphRouting())
    assert command.outcomes[1] == FailedGraphNodeOutcome(GraphNodeId("b"), GraphFailure("failed"))
    interrupt = command.outcomes[2]
    assert isinstance(interrupt, InterruptedGraphNodeOutcome)
    assert interrupt.identity.execution_generation == state.execution.token.generation
    assert interrupt.identity.node_id == GraphNodeId("c")


def test_no_codec_rejects_interrupt_before_command_projection() -> None:
    graph = topology("a")
    tasks, state = planned_and_leased(graph)
    with pytest.raises(ResultCollectionError, match="codec"):
        settle_tasks(graph, state, tasks, (TaskInterrupt(tasks[0], GraphInterruptPayload(b"q")),))


def test_invalid_success_routing_rejects_entire_mixed_batch() -> None:
    graph = topology("a", "b", "next", edges=(conditional("a", "go", "next"),), entries=("a", "b"))
    tasks, state = planned_and_leased(graph, running_state(frontier=("a", "b")))
    with pytest.raises(InvalidRoutingCommandError):
        settle_tasks(
            graph,
            state,
            tasks,
            (
                TaskSuccess(tasks[0], "output", ContinueGraphRouting()),
                TaskFailure(tasks[1], GraphFailure("failed")),
            ),
        )


def test_retained_success_and_skip_are_included_only_when_last_pending_succeeds() -> None:
    graph = topology(
        "a",
        "b",
        "c",
        "from-success",
        "from-skip",
        edges=(
            direct("a", "from-success"),
            direct("b", "from-skip"),
            direct("c", END),
        ),
        entries=("a", "b", "c"),
    )
    state = running_state(frontier=("a", "b", "c"))
    state = replace(
        state,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(GraphNodeId("a"), SucceededGraphNode(ContinueGraphRouting())),
                GraphFrontierNode(
                    GraphNodeId("b"),
                    SkippedGraphNode(
                        GraphFailure("b failed"),
                        GraphSkipReason("operator skip"),
                        ContinueGraphRouting(),
                    ),
                ),
                state.frontier.nodes[2],
            )
        ),
    )
    tasks, leased = planned_and_leased(graph, state)
    command = settle_tasks(
        graph,
        leased,
        tasks,
        (TaskSuccess(tasks[0], "output", ContinueGraphRouting()),),
    )
    assert command.resolution == AdvanceGraphFrontier(
        (GraphNodeId("from-skip"), GraphNodeId("from-success")),
        (),
    )


def test_settlement_requires_matching_graph_and_committed_lease() -> None:
    graph = topology("a")
    task = plan_tasks(graph, running_state(), ExecutionLimits())[0]
    with pytest.raises(ResultCollectionError, match="lease"):
        settle_tasks(
            graph,
            running_state(),
            (task,),
            (TaskSuccess(task, "output", ContinueGraphRouting()),),
        )
    mismatched = leased_state(running_state(definition_id="other"))
    with pytest.raises(SnapshotMismatchError):
        settle_tasks(
            graph,
            mismatched,
            (task,),
            (TaskFailure(task, GraphFailure("failed")),),
        )


def test_conditional_route_to_end_completes() -> None:
    graph = topology("a", edges=(conditional("a", "finish", END),))
    tasks, state = planned_and_leased(graph)
    command = settle_tasks(
        graph,
        state,
        tasks,
        (TaskSuccess(tasks[0], "output", SelectGraphRoute(GraphRouteId("finish"))),),
    )
    assert command.resolution == CompleteGraphFrontier()
