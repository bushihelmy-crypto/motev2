import pytest

from mote_kernel.execution import Graph
from mote_kernel.execution.commit import GraphTransition
from mote_kernel.execution.engine.admission import admit_graph_input
from mote_kernel.execution.errors import GraphValueUnavailableError
from mote_kernel.execution.family_driver import (
    admit_continued_root,
    fresh_root,
    project_graph_result,
)
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
from mote_kernel.execution.run_context import ScopedFrameIndex, _CompiledFamilyIdentity
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


class _LoseAdvanceAcknowledgement(_TransitionLog):
    def __init__(self) -> None:
        super().__init__()
        self.candidate: GraphRunState | None = None

    async def __call__(self, transition: GraphTransition[int], /) -> GraphRunState:
        self.transitions.append(transition)
        if isinstance(transition.command, AdvanceGraphFrontier):
            self.candidate = transition.candidate_state
            raise RuntimeError("advance acknowledgement was lost")
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


def _compiled_multiple_feedback(operation: NodeCallable[int]):
    left_seed = Graph.graph_input("left_seed", int)
    right_seed = Graph.graph_input("right_seed", int)
    loop = GraphNodeId("loop")
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("feedback.multiple-runtime"),
            GraphDefinitionVersion(1),
            (
                CallableNodeDefinition(
                    loop,
                    operation,
                    normalize_input_bindings(
                        {
                            "left": FeedbackInputBinding(left_seed, Graph.node_output("loop", "left")),
                            "right": FeedbackInputBinding(right_seed, Graph.node_output("loop", "right")),
                        }
                    ),
                    normalize_output_declarations({"left": int, "right": int}),
                ),
            ),
            (
                ConditionalEdge(loop, GraphRouteId("continue"), loop),
                ConditionalEdge(loop, GraphRouteId("done"), END),
            ),
            (loop,),
            normalize_graph_output_declarations({"value": Graph.node_output("loop", "left")}),
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
async def test_multiple_feedback_inputs_read_their_own_fixed_repeat_publications() -> None:
    seen: list[tuple[int, int]] = []

    async def loop(values: Graph.Values[int]) -> Graph.Outcome[int]:
        current = (values["left"], values["right"])
        seen.append(current)
        return Graph.success(
            Graph.values(left=current[0] + 1, right=current[1] + 10),
            route="done" if current[0] >= 2 else "continue",
        )

    graph = _compiled_multiple_feedback(loop)
    scope_run = root_scope_run(GraphRunId("multiple-feedback-run"))
    root, evidence_reader = await fresh_root(
        graph,
        scope_run,
        admit_graph_input(graph, Graph.values(left_seed=1, right_seed=100)),
        ExecutionLimits(),
        None,
    )
    try:
        disposition = await root.drive_quantum()
        result = project_graph_result(
            graph,
            _CompiledFamilyIdentity(),
            root,
            evidence_reader,
            disposition,
            recovered=False,
        )
    finally:
        await root.release()

    assert isinstance(result, Graph.CompletedResult)
    assert seen == [(1, 100), (2, 110)]
    assert result.outputs["value"] == 3
    assert result.state.superstep == 1


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


@pytest.mark.asyncio
async def test_feedback_continuation_reuses_transient_publication_after_lost_advance() -> None:
    seen: list[int] = []

    async def loop(values: Graph.Values[int]) -> Graph.Outcome[int]:
        value = values["value"]
        seen.append(value)
        return Graph.success(Graph.values(value=value + 1), route="continue" if value == 0 else "done")

    graph = _compiled(loop)
    scope_run = root_scope_run(GraphRunId("feedback-lost-advance"))
    lost = _LoseAdvanceAcknowledgement()
    root, _evidence_reader = await fresh_root(
        graph,
        scope_run,
        admit_graph_input(graph, Graph.values(seed=0)),
        ExecutionLimits(),
        lost,
    )
    try:
        with pytest.raises(RuntimeError, match="acknowledgement was lost"):
            await root.drive_quantum()
        assert lost.candidate is not None
        candidate = lost.candidate
        transient_frames = root.frames
    finally:
        await root.release()

    state_only_root, _state_only_evidence = await admit_continued_root(
        graph,
        candidate,
        (),
        ScopedFrameIndex(),
        ExecutionLimits(),
        None,
        (),
        (),
        _CompiledFamilyIdentity(),
        recovered=True,
    )
    try:
        with pytest.raises(GraphValueUnavailableError, match="node output"):
            await state_only_root.drive_quantum()
    finally:
        await state_only_root.release()
    assert seen == [0]

    continued_root, evidence_reader = await admit_continued_root(
        graph,
        candidate,
        (),
        transient_frames,
        ExecutionLimits(),
        None,
        (),
        (),
        _CompiledFamilyIdentity(),
        recovered=False,
    )
    try:
        disposition = await continued_root.drive_quantum()
        result = project_graph_result(
            graph,
            _CompiledFamilyIdentity(),
            continued_root,
            evidence_reader,
            disposition,
            recovered=False,
        )
    finally:
        await continued_root.release()

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["value"] == 2
    assert seen == [0, 1]


@pytest.mark.asyncio
async def test_multi_node_feedback_cycle_reads_the_declared_previous_node_publication() -> None:
    seen: list[tuple[str, int]] = []
    graph = Graph[int]("feedback.multi-node.runtime")
    seed = graph.graph_input("seed", int)

    async def first(values: Graph.Values[int]) -> Graph.Values[int]:
        value = values["value"]
        seen.append(("a", value))
        return Graph.values(value=value + 1)

    async def middle(values: Graph.Values[int]) -> Graph.Values[int]:
        value = values["value"]
        seen.append(("b", value))
        return Graph.values(value=value + 10)

    async def last(values: Graph.Values[int]) -> Graph.Outcome[int]:
        value = values["value"]
        seen.append(("c", value))
        return Graph.success(Graph.values(value=value + 100), route="again" if value < 120 else "done")

    graph.add_node(
        "a",
        first,
        inputs={"value": Graph.feedback(initial=seed, repeat=graph.node_output("c", "value"))},
        outputs={"value": int},
    )
    graph.add_node("b", middle, inputs={"value": graph.node_output("a", "value")}, outputs={"value": int})
    graph.add_node("c", last, inputs={"value": graph.node_output("b", "value")}, outputs={"value": int})
    graph.add_edge(Graph.START, "a")
    graph.add_edge("a", "b")
    graph.add_edge("b", "c")
    graph.add_conditional_edge("c", "again", "a")
    graph.add_conditional_edge("c", "done", Graph.END)
    graph.set_outputs({"value": graph.node_output("c", "value")})

    result = await graph.run(Graph.values(seed=0))

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["value"] == 222
    assert seen == [("a", 0), ("b", 1), ("c", 11), ("a", 111), ("b", 112), ("c", 122)]


@pytest.mark.asyncio
async def test_feedback_can_use_a_node_output_as_the_initial_source() -> None:
    seen: list[tuple[str, int]] = []
    graph = Graph[int]("feedback.node-seed.runtime")
    seed = graph.graph_input("seed", int)

    async def source(values: Graph.Values[int]) -> Graph.Values[int]:
        value = values["value"]
        seen.append(("source", value))
        return Graph.values(value=value + 5)

    async def target(values: Graph.Values[int]) -> Graph.Values[int]:
        value = values["value"]
        seen.append(("target", value))
        return Graph.values(value=value + 1)

    async def worker(values: Graph.Values[int]) -> Graph.Outcome[int]:
        value = values["value"]
        seen.append(("worker", value))
        return Graph.success(Graph.values(value=value + 100), route="again" if value < 120 else "done")

    graph.add_node("source", source, inputs={"value": seed}, outputs={"value": int})
    graph.add_node(
        "target",
        target,
        inputs={
            "value": Graph.feedback(
                initial=graph.node_output("source", "value"),
                repeat=graph.node_output("worker", "value"),
            )
        },
        outputs={"value": int},
    )
    graph.add_node("worker", worker, inputs={"value": graph.node_output("target", "value")}, outputs={"value": int})
    graph.add_edge("source", "target")
    graph.add_edge("target", "worker")
    graph.add_conditional_edge("worker", "again", "target")
    graph.add_conditional_edge("worker", "done", Graph.END)
    graph.set_outputs({"value": graph.node_output("worker", "value")})

    result = await graph.run(Graph.values(seed=0))

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["value"] == 308
    assert seen == [
        ("source", 0),
        ("target", 5),
        ("worker", 6),
        ("target", 106),
        ("worker", 107),
        ("target", 207),
        ("worker", 208),
    ]


@pytest.mark.asyncio
async def test_mutually_exclusive_feedback_routes_activate_a_target_once_per_round() -> None:
    branch_calls = 0
    target_calls = 0
    graph = Graph[int]("feedback.exclusive.runtime")
    seed = graph.graph_input("seed", int)

    async def target(values: Graph.Values[int]) -> Graph.Outcome[int]:
        nonlocal target_calls
        target_calls += 1
        return Graph.success(Graph.values(value=values["value"] + 1), route="run")

    async def branch(values: Graph.Values[int]) -> Graph.Outcome[int]:
        nonlocal branch_calls
        branch_calls += 1
        routes = ("left", "right", "done")
        return Graph.success(Graph.values(value=values["value"] + 10), route=routes[branch_calls - 1])

    graph.add_node(
        "target",
        target,
        inputs={"value": Graph.feedback(initial=seed, repeat=graph.node_output("branch", "value"))},
        outputs={"value": int},
    )
    graph.add_node("branch", branch, inputs={"value": graph.node_output("target", "value")}, outputs={"value": int})
    graph.add_edge(Graph.START, "target")
    graph.add_conditional_edge("target", "run", "branch")
    graph.add_conditional_edge("branch", "left", "target")
    graph.add_conditional_edge("branch", "right", "target")
    graph.add_conditional_edge("branch", "done", Graph.END)
    graph.set_outputs({"value": graph.node_output("branch", "value")})

    result = await graph.run(Graph.values(seed=0))

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["value"] == 33
    assert target_calls == 3
    assert branch_calls == 3


@pytest.mark.asyncio
async def test_feedback_repeat_source_can_be_selected_from_an_explicit_join() -> None:
    target_calls = 0
    graph = Graph[int]("feedback.join.runtime")
    seed = graph.graph_input("seed", int)

    async def source(values: Graph.Values[int]) -> Graph.Values[int]:
        return Graph.values(value=values["value"] + 1)

    async def target(values: Graph.Values[int]) -> Graph.Outcome[int]:
        nonlocal target_calls
        target_calls += 1
        return Graph.success(Graph.values(value=values["value"] + 1), route="loop" if target_calls < 3 else "finish")

    async def fanout(values: Graph.Values[int]) -> Graph.Values[int]:
        return Graph.values(value=values["value"])

    async def left(values: Graph.Values[int]) -> Graph.Values[int]:
        return Graph.values(value=values["value"] + 10)

    async def right(values: Graph.Values[int]) -> Graph.Values[int]:
        return Graph.values(value=values["value"] + 20)

    graph.add_node("source", source, inputs={"value": seed}, outputs={"value": int})
    graph.add_node(
        "target",
        target,
        inputs={
            "value": Graph.feedback(
                initial=graph.node_output("source", "value"),
                repeat=graph.node_output("left", "value"),
            )
        },
        outputs={"value": int},
    )
    graph.add_node("fanout", fanout, inputs={"value": graph.node_output("target", "value")}, outputs={"value": int})
    graph.add_node("left", left, inputs={"value": graph.node_output("fanout", "value")}, outputs={"value": int})
    graph.add_node("right", right, inputs={"value": graph.node_output("fanout", "value")}, outputs={"value": int})
    graph.add_edge("source", "target")
    graph.add_conditional_edge("target", "loop", "fanout")
    graph.add_conditional_edge("target", "finish", Graph.END)
    graph.add_edge("fanout", "left")
    graph.add_edge("fanout", "right")
    graph.add_join(("left", "right"), "target")
    graph.set_outputs({"value": graph.node_output("target", "value")})

    result = await graph.run(Graph.values(seed=0))

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["value"] == 24
    assert target_calls == 3


@pytest.mark.asyncio
async def test_nested_feedback_reuses_the_child_family_owner_and_local_publications() -> None:
    seen: list[int] = []
    child = Graph[int]("feedback.nested.child")
    child_seed = child.graph_input("seed", int)

    async def loop(values: Graph.Values[int]) -> Graph.Outcome[int]:
        value = values["value"]
        seen.append(value)
        return Graph.success(Graph.values(value=value + 1), route="again" if value < 2 else "done")

    child.add_node(
        "loop",
        loop,
        inputs={"value": child.feedback(initial=child_seed, repeat=child.node_output("loop", "value"))},
        outputs={"value": int},
    )
    child.add_edge(Graph.START, "loop")
    child.add_conditional_edge("loop", "again", "loop")
    child.add_conditional_edge("loop", "done", Graph.END)
    child.set_outputs({"value": child.node_output("loop", "value")})

    parent = Graph[int]("feedback.nested.parent")
    parent_seed = parent.graph_input("seed", int)
    parent.add_node("child", child, inputs={"seed": parent_seed})
    parent.set_outputs({"value": parent.node_output("child", "value")})

    result = await parent.run(Graph.values(seed=0))

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["value"] == 3
    assert seen == [0, 1, 2]
