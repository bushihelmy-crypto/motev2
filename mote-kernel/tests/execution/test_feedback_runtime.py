# pyright: reportPrivateUsage=false

import pytest

from mote_kernel.execution import Graph
from mote_kernel.execution.engine.admission import admit_graph_input
from mote_kernel.execution.family_driver import GraphTransition, fresh_root, project_graph_result
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.definition import GraphDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition, NodeCallable
from mote_kernel.execution.graph.ports import (
    FeedbackInputBinding,
    normalize_graph_output_declarations,
    normalize_input_bindings,
    normalize_output_declarations,
)
from mote_kernel.execution.identity import root_scope_run
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.run_context import _CompiledFamilyIdentity
from mote_kernel.state.graph_state import (
    ActivationReference,
    AdvanceGraphFrontier,
    CompleteGraphFrontier,
    GraphActivationIdentity,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    GraphRouteId,
    GraphRunId,
    GraphRunState,
    RoutedActivationCause,
)


class _TransitionLog:
    def __init__(self) -> None:
        self.transitions: list[GraphTransition[int]] = []

    async def __call__(self, transition: GraphTransition[int], /) -> GraphRunState:
        self.transitions.append(transition)
        return transition.candidate_state


def _compiled(operation: NodeCallable[int]):
    seed = Graph.graph_input("seed", int)
    loop = GraphNodeId("loop")
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("feedback.runtime"),
            GraphDefinitionVersion(1),
            (
                CallableNodeDefinition(
                    loop,
                    operation,
                    normalize_input_bindings({"value": FeedbackInputBinding(seed, Graph.node_output("loop", "value"))}),
                    normalize_output_declarations({"value": int}),
                ),
            ),
            (
                ConditionalEdge(loop, GraphRouteId("continue"), loop),
                ConditionalEdge(loop, GraphRouteId("done"), END),
            ),
            (loop,),
            normalize_graph_output_declarations({"value": Graph.node_output("loop", "value")}),
        )
    )


async def _run(
    operation: NodeCallable[int],
    *,
    run_id: str,
    commit: _TransitionLog | None = None,
    max_supersteps: int = 1_000,
) -> Graph.Result[int]:
    graph = _compiled(operation)
    scope_run = root_scope_run(GraphRunId(run_id))
    root, evidence_reader = await fresh_root(
        graph,
        scope_run,
        admit_graph_input(graph, Graph.values(seed=0)),
        ExecutionLimits(max_supersteps=max_supersteps),
        commit,
    )
    try:
        disposition = await root.drive_quantum()
        return project_graph_result(
            graph,
            _CompiledFamilyIdentity(),
            root,
            evidence_reader,
            disposition,
            recovered=False,
        )
    finally:
        await root.release()


@pytest.mark.asyncio
async def test_self_feedback_runs_each_activation_from_the_previous_publication() -> None:
    seen: list[int] = []

    async def loop(values: Graph.Values[int]) -> Graph.Outcome[int]:
        value = values["value"]
        seen.append(value)
        return Graph.success(
            Graph.values(value=value + 1),
            route="done" if value >= 2 else "continue",
        )

    result = await _run(loop, run_id="feedback-run")

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["value"] == 3
    assert seen == [0, 1, 2]
    assert result.state.superstep == 2


@pytest.mark.asyncio
async def test_self_feedback_failure_is_terminal_and_is_not_retried() -> None:
    calls = 0

    async def fail(_values: Graph.Values[int]) -> Graph.Outcome[int]:
        nonlocal calls
        calls += 1
        return Graph.failure("declined")

    result = await _run(fail, run_id="failed-feedback-run")

    assert isinstance(result, Graph.FailedResult)
    assert calls == 1
    assert result.state.superstep == 0
    assert result.failures[0].node_id == "loop"


@pytest.mark.asyncio
async def test_self_feedback_keeps_using_the_immediate_predecessor_after_many_rounds() -> None:
    seen: list[int] = []

    async def loop(values: Graph.Values[int]) -> Graph.Outcome[int]:
        value = values["value"]
        seen.append(value)
        return Graph.success(
            Graph.values(value=value * 10 + 1),
            route="done" if len(seen) == 6 else "continue",
        )

    result = await _run(loop, run_id="feedback-many-rounds")

    assert isinstance(result, Graph.CompletedResult)
    assert seen == [0, 1, 11, 111, 1111, 11111]
    assert result.outputs["value"] == 111111
    assert result.state.superstep == 5


@pytest.mark.asyncio
async def test_self_feedback_persists_each_immediate_predecessor_cause_and_terminal_exit() -> None:
    transitions = _TransitionLog()

    async def loop(values: Graph.Values[int]) -> Graph.Outcome[int]:
        value = values["value"]
        return Graph.success(Graph.values(value=value + 1), route="continue" if value < 2 else "done")

    result = await _run(loop, run_id="feedback-cause-state", commit=transitions)

    assert isinstance(result, Graph.CompletedResult)
    advances = tuple(
        transition for transition in transitions.transitions if isinstance(transition.command, AdvanceGraphFrontier)
    )
    assert len(advances) == 2
    for predecessor_superstep, transition in enumerate(advances):
        command = transition.command
        assert isinstance(command, AdvanceGraphFrontier)
        activation = command.activations[0]
        assert activation.node_id == GraphNodeId("loop")
        cause = activation.cause
        assert isinstance(cause, RoutedActivationCause)
        assert cause.references == (
            ActivationReference(
                GraphActivationIdentity(GraphRunId("feedback-cause-state"), predecessor_superstep, GraphNodeId("loop")),
                GraphRouteId("continue"),
            ),
        )
        assert transition.candidate_state.superstep == predecessor_superstep + 1
        assert transition.candidate_state.frontier.nodes[0].activation == activation

    completions = tuple(
        transition for transition in transitions.transitions if isinstance(transition.command, CompleteGraphFrontier)
    )
    assert len(completions) == 1
    completion = completions[0]
    assert completion.candidate_state.frontier.nodes == ()
    assert completion.candidate_state.superstep == 2


@pytest.mark.asyncio
async def test_self_feedback_execution_limit_is_only_a_safety_boundary() -> None:
    calls = 0

    async def loop(values: Graph.Values[int]) -> Graph.Outcome[int]:
        nonlocal calls
        calls += 1
        return Graph.success(Graph.values(value=values["value"] + 1), route="continue")

    with pytest.raises(Graph.ExecutionLimitError, match="superstep limit"):
        await _run(loop, run_id="feedback-limit", max_supersteps=4)

    assert calls == 4
