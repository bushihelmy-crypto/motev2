import pytest

from mote_kernel.execution import Graph
from mote_kernel.execution.commit import GraphTransition
from mote_kernel.execution.engine.admission import admit_graph_input
from mote_kernel.execution.errors import GraphValueUnavailableError
from mote_kernel.execution.family_driver import admit_continued_root, fresh_root, project_graph_result
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.definition import GraphDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition, NodeCallable
from mote_kernel.execution.graph.ports import (
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


class _LoseSecondAdvanceAcknowledgement(_TransitionLog):
    def __init__(self) -> None:
        super().__init__()
        self.candidate: GraphRunState | None = None
        self._advances = 0

    async def __call__(self, transition: GraphTransition[int], /) -> GraphRunState:
        self.transitions.append(transition)
        if isinstance(transition.command, AdvanceGraphFrontier):
            self._advances += 1
            if self._advances == 2:
                self.candidate = transition.candidate_state
                raise RuntimeError("advance acknowledgement was lost")
        return transition.candidate_state


async def initialize(values: Graph.Values[int]) -> Graph.Values[int]:
    return Graph.values(value=values["seed"])


def compiled_loop(operation: NodeCallable[int]):
    initialize_id = GraphNodeId("initialize")
    loop_id = GraphNodeId("loop")
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("predecessor.runtime"),
            GraphDefinitionVersion(1),
            (
                CallableNodeDefinition(
                    initialize_id,
                    initialize,
                    normalize_input_bindings({"seed": Graph.graph_input("seed", int)}),
                    normalize_output_declarations({"value": int}),
                ),
                CallableNodeDefinition(
                    loop_id,
                    operation,
                    normalize_input_bindings({"value": Graph.node_output("value")}),
                    normalize_output_declarations({"value": int}),
                ),
            ),
            (
                DirectEdge(initialize_id, loop_id),
                ConditionalEdge(loop_id, GraphRouteId("continue"), loop_id),
                ConditionalEdge(loop_id, GraphRouteId("done"), END),
            ),
            (),
            normalize_graph_output_declarations({"value": Graph.node_output("loop", "value")}),
        )
    )


async def run_loop(
    operation: NodeCallable[int],
    *,
    run_id: str,
    commit: _TransitionLog | None = None,
    max_supersteps: int = 1_000,
) -> Graph.Result[int]:
    graph = compiled_loop(operation)
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
async def test_loop_reads_the_initializer_then_each_immediate_self_publication() -> None:
    seen: list[int] = []

    async def loop(values: Graph.Values[int]) -> Graph.Outcome[int]:
        value = values["value"]
        seen.append(value)
        return Graph.success(
            Graph.values(value=value + 1),
            route="done" if value == 2 else "continue",
        )

    result = await run_loop(loop, run_id="predecessor-loop")

    assert isinstance(result, Graph.CompletedResult)
    assert seen == [0, 1, 2]
    assert result.outputs["value"] == 3
    assert result.state.superstep == 3


@pytest.mark.asyncio
async def test_multiple_causal_inputs_read_the_same_exact_predecessor_publication() -> None:
    seen: list[tuple[int, int]] = []
    graph = Graph[int]("predecessor.multiple")
    left_seed = graph.graph_input("left_seed", int)
    right_seed = graph.graph_input("right_seed", int)

    async def initialize_pair(values: Graph.Values[int]) -> Graph.Values[int]:
        return Graph.values(left=values["left_seed"], right=values["right_seed"])

    async def loop(values: Graph.Values[int]) -> Graph.Outcome[int]:
        current = (values["left"], values["right"])
        seen.append(current)
        return Graph.success(
            Graph.values(left=current[0] + 1, right=current[1] + 10),
            route="done" if current[0] == 2 else "continue",
        )

    graph.add_node(
        "initialize",
        initialize_pair,
        inputs={"left_seed": left_seed, "right_seed": right_seed},
        outputs={"left": int, "right": int},
    )
    graph.add_node(
        "loop",
        loop,
        inputs={"left": Graph.node_output("left"), "right": Graph.node_output("right")},
        outputs={"left": int, "right": int},
    )
    graph.add_edge("initialize", "loop")
    graph.add_conditional_edge("loop", "continue", "loop")
    graph.add_conditional_edge("loop", "done", Graph.END)
    graph.set_outputs({"value": Graph.node_output("loop", "left")})

    result = await graph.run(Graph.values(left_seed=1, right_seed=100))

    assert isinstance(result, Graph.CompletedResult)
    assert seen == [(1, 100), (2, 110)]
    assert result.outputs["value"] == 3


@pytest.mark.asyncio
async def test_failed_causal_node_is_terminal_and_not_retried() -> None:
    calls = 0

    async def fail(_values: Graph.Values[int]) -> Graph.Outcome[int]:
        nonlocal calls
        calls += 1
        return Graph.failure("declined")

    result = await run_loop(fail, run_id="predecessor-failure")

    assert isinstance(result, Graph.FailedResult)
    assert calls == 1
    assert result.failures[0].node_id == "loop"


@pytest.mark.asyncio
async def test_many_rounds_never_read_an_older_publication() -> None:
    seen: list[int] = []

    async def loop(values: Graph.Values[int]) -> Graph.Outcome[int]:
        value = values["value"]
        seen.append(value)
        return Graph.success(
            Graph.values(value=value * 10 + 1),
            route="done" if len(seen) == 6 else "continue",
        )

    result = await run_loop(loop, run_id="predecessor-many-rounds")

    assert isinstance(result, Graph.CompletedResult)
    assert seen == [0, 1, 11, 111, 1111, 11111]
    assert result.outputs["value"] == 111111


@pytest.mark.asyncio
async def test_each_round_persists_its_exact_immediate_predecessor_cause() -> None:
    transitions = _TransitionLog()

    async def loop(values: Graph.Values[int]) -> Graph.Outcome[int]:
        value = values["value"]
        return Graph.success(Graph.values(value=value + 1), route="continue" if value < 2 else "done")

    result = await run_loop(loop, run_id="predecessor-causes", commit=transitions)

    assert isinstance(result, Graph.CompletedResult)
    advances: list[AdvanceGraphFrontier] = []
    completions: list[CompleteGraphFrontier] = []
    for transition in transitions.transitions:
        command = transition.command
        if isinstance(command, AdvanceGraphFrontier):
            advances.append(command)
        elif isinstance(command, CompleteGraphFrontier):
            completions.append(command)
    assert tuple(command.activations[0].node_id for command in advances) == (
        GraphNodeId("loop"),
        GraphNodeId("loop"),
        GraphNodeId("loop"),
    )
    expected_sources = (GraphNodeId("initialize"), GraphNodeId("loop"), GraphNodeId("loop"))
    for predecessor_superstep, (command, source_id) in enumerate(zip(advances, expected_sources, strict=True)):
        activation = command.activations[0]
        cause = activation.cause
        assert isinstance(cause, RoutedActivationCause)
        route = None if source_id == GraphNodeId("initialize") else GraphRouteId("continue")
        assert cause.references == (
            ActivationReference(
                GraphActivationIdentity(GraphRunId("predecessor-causes"), predecessor_superstep, source_id),
                route,
            ),
        )
    assert len(completions) == 1


@pytest.mark.asyncio
async def test_execution_limit_remains_a_safety_boundary_for_causal_loops() -> None:
    calls = 0

    async def loop(values: Graph.Values[int]) -> Graph.Outcome[int]:
        nonlocal calls
        calls += 1
        return Graph.success(Graph.values(value=values["value"] + 1), route="continue")

    with pytest.raises(Graph.ExecutionLimitError, match="superstep limit"):
        await run_loop(loop, run_id="predecessor-limit", max_supersteps=4)

    assert calls == 3


@pytest.mark.asyncio
async def test_recovery_requires_the_exact_loop_publication_after_a_lost_repeat_advance() -> None:
    seen: list[int] = []

    async def loop(values: Graph.Values[int]) -> Graph.Outcome[int]:
        value = values["value"]
        seen.append(value)
        return Graph.success(Graph.values(value=value + 1), route="continue" if value == 0 else "done")

    graph = compiled_loop(loop)
    scope_run = root_scope_run(GraphRunId("predecessor-lost-advance"))
    lost = _LoseSecondAdvanceAcknowledgement()
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
async def test_multi_node_cycle_uses_the_actual_predecessor_at_every_hop() -> None:
    seen: list[tuple[str, int]] = []
    graph = Graph[int]("predecessor.multi-node")
    seed = graph.graph_input("seed", int)

    async def initialize_value(values: Graph.Values[int]) -> Graph.Values[int]:
        return Graph.values(value=values["seed"])

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

    graph.add_node("initialize", initialize_value, inputs={"seed": seed}, outputs={"value": int})
    graph.add_node("a", first, inputs={"value": Graph.node_output("value")}, outputs={"value": int})
    graph.add_node("b", middle, inputs={"value": Graph.node_output("value")}, outputs={"value": int})
    graph.add_node("c", last, inputs={"value": Graph.node_output("value")}, outputs={"value": int})
    graph.add_edge("initialize", "a")
    graph.add_edge("a", "b")
    graph.add_edge("b", "c")
    graph.add_conditional_edge("c", "again", "a")
    graph.add_conditional_edge("c", "done", Graph.END)
    graph.set_outputs({"value": Graph.node_output("c", "value")})

    result = await graph.run(Graph.values(seed=0))

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["value"] == 222
    assert seen == [("a", 0), ("b", 1), ("c", 11), ("a", 111), ("b", 112), ("c", 122)]


@pytest.mark.asyncio
@pytest.mark.parametrize(("selected", "expected"), [("left", "from-left"), ("right", "from-right")])
async def test_shared_node_reads_the_output_of_the_branch_that_actually_activated_it(
    selected: str,
    expected: str,
) -> None:
    seen: list[str] = []
    graph = Graph[str]("predecessor.shared")
    choice = graph.graph_input("choice", str)

    async def decide(values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.success(Graph.values(), route=values["choice"])

    async def left(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(hook_request="from-left")

    async def right(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(hook_request="from-right")

    async def hook(values: Graph.Values[str]) -> Graph.Values[str]:
        seen.append(values["request"])
        return Graph.values(result=values["request"])

    graph.add_node("decision", decide, inputs={"choice": choice}, outputs={})
    graph.add_node("left", left, inputs={}, outputs={"hook_request": str})
    graph.add_node("right", right, inputs={}, outputs={"hook_request": str})
    graph.add_node(
        "hook",
        hook,
        inputs={"request": Graph.node_output("hook_request")},
        outputs={"result": str},
    )
    graph.add_conditional_edge("decision", "left", "left")
    graph.add_conditional_edge("decision", "right", "right")
    graph.add_edge("left", "hook")
    graph.add_edge("right", "hook")
    graph.set_outputs({"result": Graph.node_output("hook", "result")})

    result = await graph.run(Graph.values(choice=selected))

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["result"] == expected
    assert seen == [expected]


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_route", ["left", "right"])
async def test_same_predecessor_routes_share_one_exact_causal_publication(selected_route: str) -> None:
    graph = Graph[str](f"predecessor.same-source-{selected_route}")

    async def source(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.success(Graph.values(hook_request=selected_route), route=selected_route)

    async def hook(values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(result=values["request"])

    graph.add_node("source", source, inputs={}, outputs={"hook_request": str})
    graph.add_node(
        "hook",
        hook,
        inputs={"request": Graph.node_output("hook_request")},
        outputs={"result": str},
    )
    graph.add_conditional_edge("source", "left", "hook")
    graph.add_conditional_edge("source", "right", "hook")
    graph.set_outputs({"result": Graph.node_output("hook", "result")})

    result = await graph.run(Graph.values())

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["result"] == selected_route


@pytest.mark.asyncio
async def test_nested_node_can_receive_its_actual_parent_predecessor_publication() -> None:
    child = Graph[str]("predecessor.nested.child")
    request = child.graph_input("hook_request", str)

    async def child_hook(values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(result=f"hooked:{values['hook_request']}")

    child.add_node(
        "hook",
        child_hook,
        inputs={"hook_request": request},
        outputs={"result": str},
    )
    child.set_outputs({"result": child.node_output("hook", "result")})

    parent = Graph[str]("predecessor.nested.parent")

    async def produce(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(hook_request="request")

    parent.add_node("produce", produce, inputs={}, outputs={"hook_request": str})
    parent.add_node(
        "hook",
        child,
        inputs={"hook_request": Graph.node_output("hook_request")},
    )
    parent.add_edge("produce", "hook")
    parent.set_outputs({"result": parent.node_output("hook", "result")})

    result = await parent.run(Graph.values())

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["result"] == "hooked:request"


@pytest.mark.asyncio
async def test_actual_nested_predecessor_supplies_its_graph_output() -> None:
    producer = Graph[str]("predecessor.nested-source.child")

    async def produce(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(hook_request="nested-request")

    producer.add_node("produce", produce, inputs={}, outputs={"hook_request": str})
    producer.set_outputs({"hook_request": producer.node_output("produce", "hook_request")})

    parent = Graph[str]("predecessor.nested-source.parent")

    async def hook(values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(result=values["request"])

    parent.add_node("producer", producer, inputs={})
    parent.add_node(
        "hook",
        hook,
        inputs={"request": parent.node_output("hook_request")},
        outputs={"result": str},
    )
    parent.add_edge("producer", "hook")
    parent.set_outputs({"result": parent.node_output("hook", "result")})

    result = await parent.run(Graph.values())

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["result"] == "nested-request"


@pytest.mark.asyncio
async def test_nested_causal_loop_reuses_child_family_owner_and_local_publications() -> None:
    seen: list[int] = []
    child = Graph[int]("predecessor.nested-loop.child")

    async def initialize_child(values: Graph.Values[int]) -> Graph.Values[int]:
        return Graph.values(value=values["seed"])

    async def loop(values: Graph.Values[int]) -> Graph.Outcome[int]:
        value = values["value"]
        seen.append(value)
        return Graph.success(Graph.values(value=value + 1), route="again" if value < 2 else "done")

    child.add_node(
        "initialize",
        initialize_child,
        inputs={"seed": child.graph_input("seed", int)},
        outputs={"value": int},
    )
    child.add_node(
        "loop",
        loop,
        inputs={"value": child.node_output("value")},
        outputs={"value": int},
    )
    child.add_edge("initialize", "loop")
    child.add_conditional_edge("loop", "again", "loop")
    child.add_conditional_edge("loop", "done", Graph.END)
    child.set_outputs({"value": child.node_output("loop", "value")})

    parent = Graph[int]("predecessor.nested-loop.parent")
    parent.add_node(
        "child",
        child,
        inputs={"seed": parent.graph_input("seed", int)},
    )
    parent.set_outputs({"value": parent.node_output("child", "value")})

    result = await parent.run(Graph.values(seed=0))

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["value"] == 3
    assert seen == [0, 1, 2]


@pytest.mark.asyncio
async def test_shared_nested_node_reads_each_actual_predecessor_across_rounds() -> None:
    seen: list[str] = []
    hook = Graph[str]("predecessor.shared-nested.hook")

    async def apply_hook(values: Graph.Values[str]) -> Graph.Values[str]:
        request = values["request"]
        seen.append(request)
        return Graph.values(result=f"hooked:{request}")

    hook.add_node(
        "apply",
        apply_hook,
        inputs={"request": hook.graph_input("request", str)},
        outputs={"result": str},
    )
    hook.set_outputs({"result": hook.node_output("apply", "result")})

    parent = Graph[str]("predecessor.shared-nested.parent")
    controller_calls = 0

    async def initialize(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(hook_request="initialize")

    async def route_after_hook(values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal controller_calls
        routes = ("left", "right", "done")
        route = routes[controller_calls]
        controller_calls += 1
        return Graph.success(Graph.values(result=values["result"]), route=route)

    async def left(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(hook_request="left")

    async def right(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(hook_request="right")

    async def finish(values: Graph.Values[str]) -> Graph.Values[str]:
        return values

    parent.add_node("initialize", initialize, inputs={}, outputs={"hook_request": str})
    parent.add_node(
        "hook",
        hook,
        inputs={"request": parent.node_output("hook_request")},
    )
    parent.add_node(
        "route",
        route_after_hook,
        inputs={"result": parent.node_output("hook", "result")},
        outputs={"result": str},
    )
    parent.add_node("left", left, inputs={}, outputs={"hook_request": str})
    parent.add_node("right", right, inputs={}, outputs={"hook_request": str})
    parent.add_node(
        "finish",
        finish,
        inputs={"result": parent.node_output("route", "result")},
        outputs={"result": str},
    )
    parent.add_edge("initialize", "hook")
    parent.add_edge("hook", "route")
    parent.add_conditional_edge("route", "left", "left")
    parent.add_conditional_edge("route", "right", "right")
    parent.add_conditional_edge("route", "done", "finish")
    parent.add_edge("left", "hook")
    parent.add_edge("right", "hook")
    parent.set_outputs({"result": parent.node_output("finish", "result")})

    result = await parent.run(Graph.values())

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["result"] == "hooked:right"
    assert seen == ["initialize", "left", "right"]
    assert controller_calls == 3
