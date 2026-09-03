from typing import cast

import pytest

from mote_kernel.state.graph_state import (
    ClaimGraphExecution,
    ContinueGraphRouting,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionAttemptId,
    GraphFrontierActivation,
    GraphInterruptId,
    GraphInterruptPayload,
    GraphNodeId,
    GraphNodeInterruptIdentity,
    GraphNodeOutcome,
    GraphNodeResumeAction,
    GraphResumeInputCodec,
    GraphResumeInputCodecId,
    GraphResumeInputPayload,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphStateTransitionError,
    InterruptedGraphNode,
    InterruptedGraphNodeOutcome,
    OverrideGraphNodeInput,
    PendingGraphNode,
    ResumeGraphNodes,
    ResumeInterruptedNode,
    SettleGraphNode,
    StartActivationCause,
    StartGraphRun,
    SucceededGraphNode,
    SucceededGraphNodeOutcome,
    graph_interrupt_id,
    reduce_graph_run,
)

A = GraphNodeId("a")
B = GraphNodeId("b")
C = GraphNodeId("c")
CODEC = GraphResumeInputCodec(GraphResumeInputCodecId("input.v1"), 1)


def start(*nodes: GraphNodeId) -> GraphRunState:
    return reduce_graph_run(
        None,
        StartGraphRun(
            GraphRunId("run"),
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            tuple(GraphFrontierActivation(node, StartActivationCause()) for node in nodes),
            resume_input_codec=CODEC,
        ),
    )


def claim(state: GraphRunState) -> GraphRunState:
    return reduce_graph_run(
        state,
        ClaimGraphExecution(
            state.revision,
            GraphExecutionAttemptId(f"attempt-{state.execution_sequence + 1}"),
            None,
        ),
    )


def settle(state: GraphRunState, outcomes: tuple[GraphNodeOutcome, ...]) -> GraphRunState:
    current = state
    for outcome in outcomes:
        assert current.execution is not None
        current = reduce_graph_run(
            current,
            SettleGraphNode(current.revision, current.execution.token, outcome),
        )
    return current


def interrupted_state() -> GraphRunState:
    leased = claim(start(A, B))
    assert leased.execution is not None
    identity = GraphNodeInterruptIdentity(
        leased.run_id,
        leased.superstep,
        B,
        leased.execution.token.generation,
    )
    return settle(
        leased,
        (
            SucceededGraphNodeOutcome(A, ContinueGraphRouting()),
            InterruptedGraphNodeOutcome(B, identity, GraphInterruptPayload(b"question")),
        ),
    )


def multiple_interrupted_state() -> GraphRunState:
    leased = claim(start(A, B))
    assert leased.execution is not None
    generation = leased.execution.token.generation
    return settle(
        leased,
        tuple(
            InterruptedGraphNodeOutcome(
                node_id,
                GraphNodeInterruptIdentity(leased.run_id, leased.superstep, node_id, generation),
                GraphInterruptPayload(f"question-{node_id}".encode()),
            )
            for node_id in (A, B)
        ),
    )


def exact_interrupt_id(state: GraphRunState, node_id: GraphNodeId) -> GraphInterruptId:
    settlement = next(node.settlement for node in state.frontier.nodes if node.node_id == node_id)
    assert isinstance(settlement, InterruptedGraphNode)
    identity = settlement.interrupt.identity
    return graph_interrupt_id(
        identity.run_id,
        identity.superstep,
        identity.node_id,
        identity.execution_generation,
    )


def answer(state: GraphRunState, node_id: GraphNodeId, payload: bytes = b"answer") -> ResumeInterruptedNode:
    return ResumeInterruptedNode(
        node_id,
        exact_interrupt_id(state, node_id),
        OverrideGraphNodeInput(GraphResumeInputPayload(payload)),
    )


def test_interrupt_resume_requires_exact_current_projected_id() -> None:
    state = interrupted_state()

    resumed = reduce_graph_run(
        state,
        ResumeGraphNodes(state.revision, (answer(state, B),)),
    )

    assert isinstance(resumed.frontier.nodes[0].settlement, SucceededGraphNode)
    assert resumed.frontier.nodes[1].settlement == PendingGraphNode(
        OverrideGraphNodeInput(GraphResumeInputPayload(b"answer"))
    )
    assert resumed.status is GraphRunStatus.RUNNING
    assert resumed.superstep == state.superstep


@pytest.mark.parametrize(
    "interrupt_id",
    [
        GraphInterruptId("wrong"),
        graph_interrupt_id(GraphRunId("other"), 0, B, 1),
        graph_interrupt_id(GraphRunId("run"), 1, B, 1),
        graph_interrupt_id(GraphRunId("run"), 0, B, 2),
    ],
)
def test_interrupt_resume_rejects_wrong_or_stale_id(interrupt_id: GraphInterruptId) -> None:
    state = interrupted_state()

    with pytest.raises(GraphStateTransitionError, match="does not match"):
        reduce_graph_run(
            state,
            ResumeGraphNodes(
                state.revision,
                (
                    ResumeInterruptedNode(
                        B,
                        interrupt_id,
                        OverrideGraphNodeInput(GraphResumeInputPayload(b"answer")),
                    ),
                ),
            ),
        )


def test_resume_action_must_match_current_interrupt_settlement() -> None:
    state = interrupted_state()
    action = ResumeInterruptedNode(
        A,
        GraphInterruptId("interrupt"),
        OverrideGraphNodeInput(GraphResumeInputPayload(b"answer")),
    )

    with pytest.raises(GraphStateTransitionError, match="does not match"):
        reduce_graph_run(state, ResumeGraphNodes(state.revision, (action,)))


@pytest.mark.parametrize("case", ["empty", "noncanonical", "duplicate", "unknown", "forged"])
def test_resume_action_group_requires_nonempty_distinct_canonical_known_nodes(case: str) -> None:
    state = multiple_interrupted_state()
    actions: tuple[GraphNodeResumeAction, ...]
    if case == "empty":
        actions = ()
    elif case == "noncanonical":
        actions = (answer(state, B), answer(state, A))
    elif case == "duplicate":
        actions = (answer(state, A), answer(state, A))
    elif case == "unknown":
        actions = (
            ResumeInterruptedNode(
                C,
                GraphInterruptId("interrupt"),
                OverrideGraphNodeInput(GraphResumeInputPayload(b"answer")),
            ),
        )
    else:
        actions = (cast(GraphNodeResumeAction, object()),)

    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(state, ResumeGraphNodes(state.revision, actions))


def test_any_invalid_action_rejects_entire_interrupt_resume_group_atomically() -> None:
    state = multiple_interrupted_state()
    actions = (
        answer(state, A),
        ResumeInterruptedNode(
            B,
            GraphInterruptId("wrong"),
            OverrideGraphNodeInput(GraphResumeInputPayload(b"answer")),
        ),
    )

    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(state, ResumeGraphNodes(state.revision, actions))
    assert all(isinstance(node.settlement, InterruptedGraphNode) for node in state.frontier.nodes)


def test_resume_rejects_stale_revision_and_active_lease() -> None:
    state = multiple_interrupted_state()
    with pytest.raises(GraphStateTransitionError, match="stale revision"):
        reduce_graph_run(state, ResumeGraphNodes(state.revision + 1, (answer(state, A),)))

    resumed = reduce_graph_run(state, ResumeGraphNodes(state.revision, (answer(state, A),)))
    leased = claim(resumed)
    with pytest.raises(GraphStateTransitionError, match="quiescent"):
        reduce_graph_run(leased, ResumeGraphNodes(leased.revision, (answer(state, B),)))


def test_consumed_interrupt_identity_cannot_be_resumed_again() -> None:
    state = interrupted_state()
    action = answer(state, B)
    resumed = reduce_graph_run(state, ResumeGraphNodes(state.revision, (action,)))

    with pytest.raises(GraphStateTransitionError, match="does not match"):
        reduce_graph_run(resumed, ResumeGraphNodes(resumed.revision, (action,)))


def test_multiple_interrupts_can_resume_atomically() -> None:
    state = multiple_interrupted_state()

    resumed = reduce_graph_run(
        state,
        ResumeGraphNodes(state.revision, (answer(state, A, b"a"), answer(state, B, b"b"))),
    )

    assert resumed.status is GraphRunStatus.RUNNING
    assert tuple(node.settlement for node in resumed.frontier.nodes) == (
        PendingGraphNode(OverrideGraphNodeInput(GraphResumeInputPayload(b"a"))),
        PendingGraphNode(OverrideGraphNodeInput(GraphResumeInputPayload(b"b"))),
    )
