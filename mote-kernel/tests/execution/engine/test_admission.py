import pytest

from mote_kernel.execution.engine import GraphTask, TaskId, admit_tasks
from mote_kernel.execution.graph import (
    GraphDefinition,
    GraphDefinitionId,
    GraphDefinitionVersion,
    NestedGraphNodeDefinition,
    NodeDefinition,
    NodeId,
    NodeSuccess,
    compile_graph,
)
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.resource import ResourceDefinition, ResourceId
from mote_kernel.execution.snapshot import GraphRunId
from mote_kernel.state.graph_state import (
    AcquireResources,
    ParticipantId,
    ResourceLock,
    ResourceSnapshot,
    ResourceTransitionError,
    reduce_resources,
)

FILE = ResourceId("file")


async def execute(node_input: str) -> NodeSuccess[str]:
    return NodeSuccess(node_input)


def task(name: str) -> GraphTask:
    return GraphTask(TaskId(name), GraphRunId("run"), 0, NodeId(name))


def test_admission_allows_resource_free_tasks_and_one_exclusive_owner() -> None:
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(NodeId("a"), execute, (FILE,)),
                NodeDefinition(NodeId("b"), execute, (FILE,)),
                NodeDefinition(NodeId("c"), execute),
            ),
            (),
            (NodeId("a"), NodeId("b"), NodeId("c")),
            (ResourceDefinition(FILE, 10),),
        )
    )

    admission = admit_tasks(graph, (task("c"), task("b"), task("a")), ResourceSnapshot((ResourceLock(FILE),)))

    assert tuple(item.task_id for item in admission.admitted) == (TaskId("a"), TaskId("c"))
    assert tuple(item.task_id for item in admission.waiting) == (TaskId("b"),)
    assert admission.snapshot.resources[0].owner == ParticipantId("a")
    assert admission.snapshot.resources[0].waiters == (ParticipantId("b"),)


def test_admission_reuses_committed_acquisition_without_requeueing() -> None:
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), execute, (FILE,)),),
            (),
            (NodeId("a"),),
            (ResourceDefinition(FILE, 10),),
        )
    )
    first = admit_tasks(graph, (task("a"),), ResourceSnapshot((ResourceLock(FILE),)))

    second = admit_tasks(graph, (task("a"),), first.snapshot)

    assert second == first


def resource_graph() -> CompiledGraph[str, str]:
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(NodeId("a"), execute, (FILE,)),
                NodeDefinition(NodeId("free"), execute),
            ),
            (),
            (NodeId("a"), NodeId("free")),
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
        AcquireResources(ParticipantId("a"), (FILE,)),
    )
    with pytest.raises(ResourceTransitionError, match="outside"):
        admit_tasks(graph, (task("free"),), acquired)


def test_admission_rejects_acquisition_for_free_task_and_requirement_drift() -> None:
    graph = resource_graph()
    acquired = reduce_resources(
        ResourceSnapshot((ResourceLock(FILE),)),
        AcquireResources(ParticipantId("free"), (FILE,)),
    )
    with pytest.raises(ResourceTransitionError, match="resource-free"):
        admit_tasks(graph, (task("free"),), acquired)

    drifted = reduce_resources(
        ResourceSnapshot((ResourceLock(FILE),)),
        AcquireResources(ParticipantId("a"), (FILE,)),
    )
    object.__setattr__(drifted.acquisitions[0], "required", ())
    with pytest.raises(ResourceTransitionError, match="does not match"):
        admit_tasks(graph, (task("a"),), drifted)


def test_admission_rejects_nested_graph_tasks_at_its_narrow_boundary() -> None:
    child = GraphDefinition(
        GraphDefinitionId("child"),
        GraphDefinitionVersion(1),
        (NodeDefinition(NodeId("child-step"), execute),),
        (),
        (NodeId("child-step"),),
    )
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (NestedGraphNodeDefinition(NodeId("a"), child),),
            (),
            (NodeId("a"),),
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
                NodeDefinition(NodeId("a"), execute, (FILE,)),
                NodeDefinition(NodeId("b"), execute, (FILE,)),
            ),
            (),
            (NodeId("a"), NodeId("b")),
            (ResourceDefinition(FILE, 10),),
        )
    )
    snapshot = ResourceSnapshot((ResourceLock(FILE),))

    forward = admit_tasks(graph, (task("a"), task("b")), snapshot)
    reverse = admit_tasks(graph, (task("b"), task("a")), snapshot)

    assert reverse == forward
