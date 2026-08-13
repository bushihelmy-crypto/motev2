import pytest

from mote_kernel.execution.engine.admission import admit_tasks
from mote_kernel.execution.engine.task import GraphTask, TaskId
from mote_kernel.execution.graph import (
    GraphDefinition,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    NestedGraphNodeDefinition,
    NodeDefinition,
    NodeSuccess,
    compile_graph,
)
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.resource import ResourceDefinition, ResourceId
from mote_kernel.state.graph_state import (
    AcquireResources,
    GraphRunId,
    ResourceLock,
    ResourceSnapshot,
    ResourceTransitionError,
    reduce_resources,
)

FILE = ResourceId("file")


async def execute(node_input: str) -> NodeSuccess[str]:
    return NodeSuccess(node_input)


def task(name: str) -> GraphTask:
    return GraphTask(TaskId(name), GraphRunId("run"), 0, GraphNodeId(name))


def test_admission_allows_resource_free_tasks_and_one_exclusive_owner() -> None:
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("a"), execute, (FILE,)),
                NodeDefinition(GraphNodeId("b"), execute, (FILE,)),
                NodeDefinition(GraphNodeId("c"), execute),
            ),
            (),
            (GraphNodeId("a"), GraphNodeId("b"), GraphNodeId("c")),
            (ResourceDefinition(FILE, 10),),
        )
    )

    admission = admit_tasks(graph, (task("c"), task("b"), task("a")), ResourceSnapshot((ResourceLock(FILE),)))

    assert admission.admitted_node_ids == (GraphNodeId("a"),)
    assert admission.waiting_node_ids == (GraphNodeId("b"),)
    assert admission.snapshot.resources[0].owner == GraphNodeId("a")
    assert admission.snapshot.resources[0].waiters == (GraphNodeId("b"),)


def test_admission_reuses_committed_acquisition_without_requeueing() -> None:
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(GraphNodeId("a"), execute, (FILE,)),),
            (),
            (GraphNodeId("a"),),
            (ResourceDefinition(FILE, 10),),
        )
    )
    first = admit_tasks(graph, (task("a"),), ResourceSnapshot((ResourceLock(FILE),)))

    second = admit_tasks(graph, (task("a"),), first.snapshot)

    assert second == first


def test_admission_rejects_snapshot_with_noncompiled_resource_order() -> None:
    graph = resource_graph()

    with pytest.raises(ResourceTransitionError, match="resource order"):
        admit_tasks(graph, (task("a"),), ResourceSnapshot(()))


def resource_graph() -> CompiledGraph[str, str]:
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("a"), execute, (FILE,)),
                NodeDefinition(GraphNodeId("free"), execute),
            ),
            (),
            (GraphNodeId("a"), GraphNodeId("free")),
            (ResourceDefinition(FILE, 10),),
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
    child = GraphDefinition(
        GraphDefinitionId("child"),
        GraphDefinitionVersion(1),
        (NodeDefinition(GraphNodeId("child-step"), execute),),
        (),
        (GraphNodeId("child-step"),),
    )
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (NestedGraphNodeDefinition(GraphNodeId("a"), child),),
            (),
            (GraphNodeId("a"),),
        )
    )

    with pytest.raises(ResourceTransitionError, match="executable node"):
        admit_tasks(graph, (task("a"),), ResourceSnapshot(()))


def test_admission_is_independent_of_input_task_order() -> None:
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("a"), execute, (FILE,)),
                NodeDefinition(GraphNodeId("b"), execute, (FILE,)),
            ),
            (),
            (GraphNodeId("a"), GraphNodeId("b")),
            (ResourceDefinition(FILE, 10),),
        )
    )
    snapshot = ResourceSnapshot((ResourceLock(FILE),))

    forward = admit_tasks(graph, (task("a"), task("b")), snapshot)
    reverse = admit_tasks(graph, (task("b"), task("a")), snapshot)

    assert reverse == forward
