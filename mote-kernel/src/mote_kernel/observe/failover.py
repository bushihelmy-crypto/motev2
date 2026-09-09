"""Observe-local assembly seam for typed Port failover decoration.

Observe owns six narrow capabilities.  The queue, settlement, acknowledgement,
and resume-codec objects are decorated independently so a composition root can
choose a plan per operation without moving retry policy into Observe's graph.
The synchronous resume codec remains a complete codec surface after
decoration; it is not converted into an asynchronous retry operation.
"""

from __future__ import annotations

from dataclasses import dataclass

from mote_kernel.failover.contract import PortDecorator as FailoverPortDecorator
from mote_kernel.failover.contract import (
    TypedPortDecorator,
    apply_port_decorator_boundary,
    require_port_decorator_boundary,
)
from mote_kernel.observe.contract import ObserveContractError
from mote_kernel.observe.port import (
    BackgroundTaskPort,
    ConfigObservationPort,
    ContextObservationPort,
    ObservationAckPort,
    ObservationQueuePort,
    ObservationResumePort,
)


@dataclass(frozen=True, slots=True)
class ObserveFailoverDecorators:
    """Per-Port failover declarations for Observe.

    ``None`` is an explicit disabled binding.  ``resume`` covers the complete
    synchronous codec and is intentionally separate from queue retry policy.
    """

    queue: TypedPortDecorator[ObservationQueuePort] | FailoverPortDecorator | None = None
    background_task: TypedPortDecorator[BackgroundTaskPort] | FailoverPortDecorator | None = None
    config: TypedPortDecorator[ConfigObservationPort] | FailoverPortDecorator | None = None
    context: TypedPortDecorator[ContextObservationPort] | FailoverPortDecorator | None = None
    ack: TypedPortDecorator[ObservationAckPort] | FailoverPortDecorator | None = None
    resume: TypedPortDecorator[ObservationResumePort] | FailoverPortDecorator | None = None

    def __post_init__(self) -> None:
        require_port_decorator_boundary(self.queue, "ObserveFailoverDecorators.queue", ObserveContractError)
        require_port_decorator_boundary(
            self.background_task,
            "ObserveFailoverDecorators.background_task",
            ObserveContractError,
        )
        require_port_decorator_boundary(self.config, "ObserveFailoverDecorators.config", ObserveContractError)
        require_port_decorator_boundary(self.context, "ObserveFailoverDecorators.context", ObserveContractError)
        require_port_decorator_boundary(self.ack, "ObserveFailoverDecorators.ack", ObserveContractError)
        require_port_decorator_boundary(self.resume, "ObserveFailoverDecorators.resume", ObserveContractError)

    @classmethod
    def disabled(cls) -> ObserveFailoverDecorators:
        """Return explicit no-failover bindings for all Observe Ports."""

        return cls()

    @classmethod
    def uniform(cls, decorator: FailoverPortDecorator, /) -> ObserveFailoverDecorators:
        """Apply one typed-preserving decorator to all six Observe Ports."""

        require_port_decorator_boundary(decorator, "ObserveFailoverDecorators.uniform", ObserveContractError)
        return cls(decorator, decorator, decorator, decorator, decorator, decorator)

    def queue_port(self, port: ObservationQueuePort, /) -> ObservationQueuePort:
        return apply_port_decorator_boundary(
            port, self.queue, ObservationQueuePort, "ObservationQueuePort", ObserveContractError
        )

    def background_task_port(self, port: BackgroundTaskPort, /) -> BackgroundTaskPort:
        return apply_port_decorator_boundary(
            port,
            self.background_task,
            BackgroundTaskPort,
            "BackgroundTaskPort",
            ObserveContractError,
        )

    def config_port(self, port: ConfigObservationPort, /) -> ConfigObservationPort:
        return apply_port_decorator_boundary(
            port,
            self.config,
            ConfigObservationPort,
            "ConfigObservationPort",
            ObserveContractError,
        )

    def context_port(self, port: ContextObservationPort, /) -> ContextObservationPort:
        return apply_port_decorator_boundary(
            port,
            self.context,
            ContextObservationPort,
            "ContextObservationPort",
            ObserveContractError,
        )

    def ack_port(self, port: ObservationAckPort, /) -> ObservationAckPort:
        return apply_port_decorator_boundary(
            port, self.ack, ObservationAckPort, "ObservationAckPort", ObserveContractError
        )

    def resume_port(self, port: ObservationResumePort, /) -> ObservationResumePort:
        # Codec metadata is exposed by properties and must be captured once
        # by ``require_observe_port_contracts`` after all Port decorations.
        return apply_port_decorator_boundary(
            port,
            self.resume,
            ObservationResumePort,
            "ObservationResumePort",
            ObserveContractError,
            validate=False,
        )


def normalize_observe_failover_decorators(
    value: ObserveFailoverDecorators | FailoverPortDecorator | None,
    /,
) -> ObserveFailoverDecorators:
    """Normalize a per-Port bundle or one uniform composition decorator."""

    if value is None:
        return ObserveFailoverDecorators.disabled()
    if type(value) is ObserveFailoverDecorators:
        return value
    if callable(value):
        return ObserveFailoverDecorators.uniform(value)
    raise ObserveContractError("Observe failover decoration requires a callable decorator or ObserveFailoverDecorators")


__all__ = [
    "FailoverPortDecorator",
    "ObserveFailoverDecorators",
    "normalize_observe_failover_decorators",
]
