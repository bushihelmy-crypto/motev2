"""Domain identity guards shared by graph-run transition families."""

from mote_kernel.state.graph_state.model import GraphExecutionLease, GraphExecutionToken, GraphRunState
from mote_kernel.state.graph_state.validation import GraphStateTransitionError


def require_execution_lease(state: GraphRunState, token: GraphExecutionToken) -> GraphExecutionLease:
    """Require a transition to own the exact current execution lease."""

    execution = state.execution
    if execution is None or execution.token != token:
        raise GraphStateTransitionError("graph command does not own the active execution lease")
    return execution


__all__: list[str] = []
