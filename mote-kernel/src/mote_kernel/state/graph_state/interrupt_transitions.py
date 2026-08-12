"""Pure interrupt lifecycle transitions for one graph run."""

from dataclasses import replace

from mote_kernel.state.graph_state.command import (
    AbortGraphRun,
    RequestGraphRunInterrupt,
    ResolveGraphRunInterrupt,
)
from mote_kernel.state.graph_state.model import (
    GraphInterruptLifecycle,
    GraphInterruptReceipt,
    GraphInterruptRecord,
    GraphRunState,
    GraphRunStatus,
)
from mote_kernel.state.graph_state.transition_guard import require_interrupt_generation
from mote_kernel.state.graph_state.validation import GraphStateTransitionError, validated_graph_run_state


def consume_graph_resolution(state: GraphRunState) -> GraphInterruptRecord | None:
    """Finalize the current resolution only after graph progress settles."""

    interrupt = state.interrupt
    if interrupt is None or interrupt.lifecycle is GraphInterruptLifecycle.CONSUMED:
        return interrupt
    return replace(
        interrupt,
        lifecycle=GraphInterruptLifecycle.CONSUMED,
        receipt=GraphInterruptReceipt(state.superstep),
    )


def _cancel_graph_interrupt(state: GraphRunState) -> GraphInterruptRecord | None:
    interrupt = state.interrupt
    if interrupt is None or interrupt.lifecycle in {
        GraphInterruptLifecycle.CONSUMED,
        GraphInterruptLifecycle.CANCELLED,
    }:
        return interrupt
    return replace(
        interrupt,
        lifecycle=GraphInterruptLifecycle.CANCELLED,
        receipt=GraphInterruptReceipt(state.superstep),
    )


def request_graph_interrupt(state: GraphRunState, command: RequestGraphRunInterrupt) -> GraphRunState:
    """Suspend one quiescent graph run for a new interrupt generation."""

    if state.status is not GraphRunStatus.RUNNING:
        raise GraphStateTransitionError("only a running graph can request an interrupt")
    if command.expected_superstep != state.superstep:
        raise GraphStateTransitionError("interrupt request was based on a stale superstep")
    if state.execution is not None:
        raise GraphStateTransitionError("graph execution must drain before interruption")
    codec = state.resolution_codec
    if codec is None:
        raise GraphStateTransitionError("an interrupted graph requires a durable resolution codec")
    identity = command.identity
    prior_generation = state.interrupt.identity.generation if state.interrupt is not None else 0
    if identity.generation <= prior_generation:
        raise GraphStateTransitionError("interrupt generation must advance monotonically")
    if state.interrupt is not None and state.interrupt.lifecycle not in {
        GraphInterruptLifecycle.CONSUMED,
        GraphInterruptLifecycle.CANCELLED,
    }:
        raise GraphStateTransitionError("graph run has an unfinished interrupt generation")
    return validated_graph_run_state(
        replace(
            state,
            status=GraphRunStatus.SUSPENDED,
            resources=None,
            interrupt=GraphInterruptRecord(
                identity,
                command.request_payload,
                codec,
                GraphInterruptLifecycle.REQUESTED,
            ),
        )
    )


def resolve_graph_interrupt(state: GraphRunState, command: ResolveGraphRunInterrupt) -> GraphRunState:
    """Persist one exact resolution and resume its graph run."""

    interrupt = state.interrupt
    if command.expected_superstep != state.superstep:
        raise GraphStateTransitionError("interrupt resolution was based on a stale superstep")
    if (
        state.status is not GraphRunStatus.SUSPENDED
        or interrupt is None
        or interrupt.identity != command.identity
        or interrupt.lifecycle is not GraphInterruptLifecycle.REQUESTED
    ):
        raise GraphStateTransitionError("interrupt resolution does not match the suspended generation")
    return validated_graph_run_state(
        replace(
            state,
            status=GraphRunStatus.RUNNING,
            interrupt=replace(
                interrupt,
                lifecycle=GraphInterruptLifecycle.RESOLVED,
                resolution_payload=command.resolution_payload,
            ),
        )
    )


def abort_graph_run(state: GraphRunState, command: AbortGraphRun) -> GraphRunState:
    """Fail one quiescent running or suspended graph run."""

    if state.status in {GraphRunStatus.COMPLETED, GraphRunStatus.FAILED}:
        raise GraphStateTransitionError("a terminal graph cannot abort again")
    if command.expected_superstep != state.superstep:
        raise GraphStateTransitionError("abort command was based on a stale superstep")
    require_interrupt_generation(state, command.expected_interrupt_generation)
    if state.execution is not None or state.resources is not None:
        raise GraphStateTransitionError("graph execution and resources must be fenced before abort")
    return validated_graph_run_state(
        replace(
            state,
            status=GraphRunStatus.FAILED,
            frontier=(),
            failure=command.failure,
            join_progress=(),
            interrupt=_cancel_graph_interrupt(state),
        )
    )


__all__: list[str] = []
