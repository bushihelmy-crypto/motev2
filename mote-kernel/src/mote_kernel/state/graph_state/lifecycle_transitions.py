"""Pure terminal lifecycle transitions for graph runs."""

from dataclasses import replace

from mote_kernel.state.graph_state.command import AbortGraphRun
from mote_kernel.state.graph_state.model import GraphAbort, GraphRunState, GraphRunStatus
from mote_kernel.state.graph_state.validation import GraphStateTransitionError, validated_graph_run_state


def abort_graph_run(state: GraphRunState, command: AbortGraphRun) -> GraphRunState:
    if state.status is not GraphRunStatus.RUNNING or state.execution is not None:
        raise GraphStateTransitionError("only a quiescent running graph can abort")
    return validated_graph_run_state(
        replace(state, status=GraphRunStatus.ABORTED, resources=None, abort=GraphAbort(command.reason))
    )


__all__: list[str] = []
