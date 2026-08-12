"""Pure transition selection after deterministic result collection."""

from typing import TypeVar

from mote_kernel.execution.engine.collector import CollectedResults
from mote_kernel.execution.engine.routing import route_results
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.graph import CompiledGraph
from mote_kernel.execution.snapshot import ExecutionSnapshot
from mote_kernel.execution.transition import AdvanceTransition, CompleteTransition, ExecutionTransition, FailTransition

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def select_transition(
    graph: CompiledGraph[InputT, OutputT],
    snapshot: ExecutionSnapshot,
    collected: CollectedResults[OutputT],
) -> ExecutionTransition:
    """Select fail, advance, or complete for one fully settled superstep."""

    execution = snapshot.execution
    if execution is None:
        raise ResultCollectionError("settlement requires a committed execution lease")
    if collected.failure is not None:
        return FailTransition(snapshot.revision, execution.token, collected.failure.failure)
    decision = route_results(graph, snapshot, collected)
    if decision.frontier:
        return AdvanceTransition(
            snapshot.revision,
            execution.token,
            decision.frontier,
            decision.join_progress,
        )
    return CompleteTransition(snapshot.revision, execution.token)


__all__: list[str] = []
