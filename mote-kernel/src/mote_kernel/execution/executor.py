"""Graph-owned execution engine with explicit prepare and one-shot execute phases."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Generic, TypeVar

from mote_kernel.execution.claim import ExecutionClaimOwner, PreparedExecutionClaim
from mote_kernel.execution.engine.resolution_input import require_resolution_binding
from mote_kernel.execution.engine.superstep import execute_claimed_superstep, prepare_superstep
from mote_kernel.execution.errors import SnapshotMismatchError
from mote_kernel.execution.graph import CompiledGraph, NestedGraphNodeDefinition, compile_graph
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.request import StepRequest
from mote_kernel.execution.result import ExecutedSuperstep, PreparedFrontier
from mote_kernel.state.graph_state import GraphRunId, ParentGraphTask, StartGraphRun

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")
GraphKey = tuple[str, int]


def _compiled_graphs(root: CompiledGraph[InputT, OutputT]) -> Mapping[GraphKey, CompiledGraph[InputT, OutputT]]:
    graphs: dict[GraphKey, CompiledGraph[InputT, OutputT]] = {}
    root_key = (root.definition_id, root.version)
    pending: dict[GraphKey, CompiledGraph[InputT, OutputT]] = {root_key: root}
    while pending:
        key, graph = pending.popitem()
        graphs[key] = graph
        for definition in graph.nodes.values():
            if isinstance(definition, NestedGraphNodeDefinition):
                child_key = (definition.graph.definition_id, definition.graph.version)
                if child_key not in graphs and child_key not in pending:
                    pending[child_key] = compile_graph(definition.graph)
    return MappingProxyType(graphs)


class GraphExecutor(Generic[InputT, OutputT]):
    """Own one immutable compiled graph family and every executable decoder within it."""

    __slots__ = ("_claim_owner", "_graphs", "_root_key")

    def __init__(self, graph: CompiledGraph[InputT, OutputT]) -> None:
        self._graphs = _compiled_graphs(graph)
        self._root_key = (graph.definition_id, graph.version)
        self._claim_owner = ExecutionClaimOwner()

    def start_command(self, run_id: GraphRunId, parent: ParentGraphTask | None = None) -> StartGraphRun:
        """Create initial state for this executor's root graph definition."""

        return project_start_graph_command(self._graphs[self._root_key], run_id, parent)

    def _graph_for(self, request: StepRequest[InputT, OutputT]) -> CompiledGraph[InputT, OutputT]:
        state = request.state
        graph = self._graphs.get((state.definition_id, state.definition_version))
        if graph is None:
            raise SnapshotMismatchError("graph run is not owned by this graph executor")
        require_resolution_binding(graph, state)
        return graph

    async def prepare(self, request: StepRequest[InputT, OutputT]) -> PreparedFrontier[InputT, OutputT]:
        """Prepare admission, nested runs, or one claim without invoking graph nodes."""

        if request.state.execution is not None:
            raise SnapshotMismatchError("an active execution lease requires its original one-shot claim")
        return await prepare_superstep(self._claim_owner, self._graph_for(request), request)

    async def execute(
        self,
        claim: PreparedExecutionClaim,
        request: StepRequest[InputT, OutputT],
    ) -> ExecutedSuperstep[OutputT]:
        """Consume one exact durably accepted claim and invoke its task batch at most once."""

        graph = self._graph_for(request)
        await claim.consume(self._claim_owner, request.state, request.attempt_id)
        return await execute_claimed_superstep(graph, request, claim)


__all__ = ["GraphExecutor"]
