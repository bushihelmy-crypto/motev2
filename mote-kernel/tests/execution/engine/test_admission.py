from dataclasses import replace

import pytest
from tests.execution.engine.factories import callable_node

from mote_kernel.execution import Graph
from mote_kernel.execution.engine.admission import admit_tasks, select_executable_tasks
from mote_kernel.execution.engine.task import GraphTask, TaskId
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.definition import GraphDefinition, NestedGraphNodeDefinition
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.ports import (
    normalize_graph_output_declarations,
    normalize_input_bindings,
)
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.resource import ResourceDefinition, ResourceId
from mote_kernel.state.graph_state import (
    AcquireResources,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    GraphRunId,
    ResourceLock,
    ResourceSnapshot,
    ResourceTransitionError,
    reduce_resources,
)

FILE = ResourceId("file")


def definition(
    nodes: tuple[CallableNodeDefinition[str] | NestedGraphNodeDefinition[str], ...],
    *,
    definition_id: str = "graph",
    resources: tuple[ResourceDefinition, ...] = (),
) -> GraphDefinition[str]:
    return GraphDefinition(
        GraphDefinitionId(definition_id),
        GraphDefinitionVersion(1),
        nodes,
        (),
        (),
        normalize_graph_output_declarations({}),
        resources,
    )


def task(name: str) -> GraphTask:
    return GraphTask(TaskId(name), GraphRunId("run"), 0, GraphNodeId(name))


def test_admission_allows_resource_free_tasks_and_one_exclusive_owner() -> None:
    graph = compile_graph(
        definition(
            (
                replace(callable_node("a"), resources=(FILE,)),
                replace(callable_node("b"), resources=(FILE,)),
                callable_node("c"),
            ),
            resources=(ResourceDefinition(FILE, 0),),
        )
    )

    admission = admit_tasks(graph, (task("c"), task("b"), task("a")), ResourceSnapshot((ResourceLock(FILE),)))

    assert admission.admitted_node_ids == (GraphNodeId("a"),)
    assert admission.waiting_node_ids == (GraphNodeId("b"),)
    assert admission.snapshot.resources[0].owner == GraphNodeId("a")
    assert admission.snapshot.resources[0].waiters == (GraphNodeId("b"),)


def test_admission_reuses_committed_acquisition_without_requeueing() -> None:
    graph = compile_graph(
        definition(
            (replace(callable_node("a"), resources=(FILE,)),),
            resources=(ResourceDefinition(FILE, 0),),
        )
    )
    first = admit_tasks(graph, (task("a"),), ResourceSnapshot((ResourceLock(FILE),)))

    second = admit_tasks(graph, (task("a"),), first.snapshot)

    assert second == first


def test_admission_rejects_snapshot_with_noncompiled_resource_order() -> None:
    graph = resource_graph()

    with pytest.raises(ResourceTransitionError, match="resource order"):
        admit_tasks(graph, (task("a"),), ResourceSnapshot(()))


def resource_graph() -> CompiledGraph[str]:
    return compile_graph(
        definition(
            (
                replace(callable_node("a"), resources=(FILE,)),
                callable_node("free"),
            ),
            resources=(ResourceDefinition(FILE, 0),),
        )
    )


def test_admission_rejects_duplicate_unknown_and_stale_batch_tasks() -> None:
    graph = resource_graph()
    duplicate = (task("a"), task("a"))

    with pytest.raises(ResourceTransitionError, match="unique"):
        admit_tasks(graph, duplicate, ResourceSnapshot((ResourceLock(FILE),)))
    with pytest.raises(ResourceTransitionError, match="unknown graph node"):
        admit_tasks(graph, (task("unknown"),), ResourceSnapshot((ResourceLock(FILE),)))
    acquired = reduce_resources(
        ResourceSnapshot((ResourceLock(FILE),)),
        AcquireResources(GraphNodeId("a"), (FILE,)),
    )
    with pytest.raises(ResourceTransitionError, match="outside"):
        admit_tasks(graph, (task("free"),), acquired)


def test_admission_rejects_acquisition_for_free_task_and_requirement_drift() -> None:
    graph = resource_graph()
    acquired = reduce_resources(
        ResourceSnapshot((ResourceLock(FILE),)),
        AcquireResources(GraphNodeId("free"), (FILE,)),
    )
    with pytest.raises(ResourceTransitionError, match="resource-free"):
        admit_tasks(graph, (task("free"),), acquired)

    drifted = reduce_resources(
        ResourceSnapshot((ResourceLock(FILE),)),
        AcquireResources(GraphNodeId("a"), (FILE,)),
    )
    object.__setattr__(drifted.acquisitions[0], "required", ())
    with pytest.raises(ResourceTransitionError, match="does not match"):
        admit_tasks(graph, (task("a"),), drifted)


def test_admission_rejects_nested_graph_tasks_at_its_narrow_boundary() -> None:
    child = definition((callable_node("child-step"),), definition_id="child")
    graph = compile_graph(
        definition(
            (
                NestedGraphNodeDefinition(
                    GraphNodeId("a"),
                    child,
                    normalize_input_bindings({"value": Graph.graph_input("value", str)}),
                ),
            ),
        )
    )

    with pytest.raises(ResourceTransitionError, match="executable node"):
        admit_tasks(graph, (task("a"),), ResourceSnapshot(()))


def test_admission_is_independent_of_input_task_order() -> None:
    graph = compile_graph(
        definition(
            (
                replace(callable_node("a"), resources=(FILE,)),
                replace(callable_node("b"), resources=(FILE,)),
            ),
            resources=(ResourceDefinition(FILE, 0),),
        )
    )
    snapshot = ResourceSnapshot((ResourceLock(FILE),))

    forward = admit_tasks(graph, (task("a"), task("b")), snapshot)
    reverse = admit_tasks(graph, (task("b"), task("a")), snapshot)

    assert reverse == forward


def test_shared_selector_applies_slots_started_nodes_and_canonical_order() -> None:
    graph = resource_graph()
    snapshot = admit_tasks(
        graph,
        (task("a"),),
        ResourceSnapshot((ResourceLock(FILE),)),
    ).snapshot
    tasks = (task("free"), task("a"))

    selected = select_executable_tasks(
        graph,
        tasks,
        snapshot,
        ExecutionLimits(max_parallel_tasks=1),
        active_count=0,
        started_node_ids=frozenset(),
    )
    no_slot = select_executable_tasks(
        graph,
        tasks,
        snapshot,
        ExecutionLimits(max_parallel_tasks=1),
        active_count=1,
        started_node_ids=frozenset(),
    )
    after_a = select_executable_tasks(
        graph,
        tasks,
        snapshot,
        ExecutionLimits(max_parallel_tasks=1),
        active_count=0,
        started_node_ids=frozenset((GraphNodeId("a"),)),
    )

    assert selected == (task("a"),)
    assert no_slot == ()
    assert after_a == (task("free"),)


def test_shared_selector_skips_waiting_resource_and_nested_tasks() -> None:
    child = definition((callable_node("child-step"),), definition_id="child")
    graph = compile_graph(
        definition(
            (
                replace(callable_node("a"), resources=(FILE,)),
                replace(callable_node("b"), resources=(FILE,)),
                NestedGraphNodeDefinition(
                    GraphNodeId("nested"),
                    child,
                    normalize_input_bindings({"value": Graph.graph_input("value", str)}),
                ),
            ),
            resources=(ResourceDefinition(FILE, 0),),
        )
    )
    snapshot = admit_tasks(
        graph,
        (task("a"), task("b")),
        ResourceSnapshot((ResourceLock(FILE),)),
    ).snapshot

    selected = select_executable_tasks(
        graph,
        (task("nested"), task("b"), task("a")),
        snapshot,
        ExecutionLimits(max_parallel_tasks=3),
        active_count=0,
        started_node_ids=frozenset(),
    )

    assert selected == (task("a"),)


def test_shared_selector_requires_exact_committed_resource_requirement() -> None:
    graph = resource_graph()
    snapshot = admit_tasks(
        graph,
        (task("a"),),
        ResourceSnapshot((ResourceLock(FILE),)),
    ).snapshot
    object.__setattr__(snapshot.acquisitions[0], "required", ())

    selected = select_executable_tasks(
        graph,
        (task("a"),),
        snapshot,
        ExecutionLimits(),
        active_count=0,
        started_node_ids=frozenset(),
    )

    assert selected == ()
