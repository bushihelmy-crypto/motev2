"""CAS guards shared by graph-run transition families."""

from mote_kernel.state.graph_state.model import GraphExecutionLease, GraphExecutionToken, GraphRunState
from mote_kernel.state.graph_state.validation import GraphStateTransitionError


def interrupt_generation(state: GraphRunState) -> int | None:
    """Return the interrupt generation observed by a transition."""

    return state.interrupt.identity.generation if state.interrupt is not None else None


def require_interrupt_generation(state: GraphRunState, expected_generation: int | None) -> None:
    """Require a transition to observe the current interrupt generation."""

    if expected_generation != interrupt_generation(state):
        raise GraphStateTransitionError("graph command was based on a stale interrupt generation")


def require_execution_lease(state: GraphRunState, token: GraphExecutionToken) -> GraphExecutionLease:
    """Require a transition to own the exact current execution lease."""

    execution = state.execution
    if execution is None or execution.token != token:
        raise GraphStateTransitionError("graph command does not own the active execution lease")
    return execution


__all__: list[str] = []
