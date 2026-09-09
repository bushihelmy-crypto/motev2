"""Observe-local assembly seam for typed Port failover decoration.

Observe owns six narrow capabilities.  The queue, settlement, acknowledgement,
and resume-codec objects are decorated independently so a composition root can
choose a plan per operation without moving retry policy into Observe's graph.
The synchronous resume codec remains a complete codec surface after
decoration; it is not converted into an asynchronous retry operation.
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from typing import TypeVar

from mote_kernel.failover.contract import PortDecorator as FailoverPortDecorator
from mote_kernel.failover.contract import (
    PortDecoratorContractError,
    TypedPortDecorator,
    apply_port_decorator,
    require_port_decorator,
)
from mote_kernel.observe.contract import ObserveContractError
from mote_kernel.observe.port import (
    BackgroundTaskPort,
    ConfigObservationPort,
    ContextObservationPort,
    ObservationAckPort,
    ObservationQueuePort,
    ObservationResumeBinding,
    ObservationResumeCapture,
    ObservationResumePort,
    require_observe_port_contracts,
)

PortT = TypeVar("PortT")


def _validate_decorator(
    value: TypedPortDecorator[PortT] | FailoverPortDecorator | None,
    field: str,
    /,
) -> None:
    try:
        require_port_decorator(value, field)
    except PortDecoratorContractError as error:
        raise ObserveContractError(str(error)) from error


def _apply(
    port: PortT,
    decorator: TypedPortDecorator[PortT] | FailoverPortDecorator | None,
    expected: type[PortT],
    field: str,
    /,
    *,
    validate: bool = True,
) -> PortT:
    try:
        return apply_port_decorator(port, decorator, expected, field, validate=validate)
    except PortDecoratorContractError as error:
        raise ObserveContractError(str(error)) from error


@dataclass(frozen=True, slots=True)
class ObservePortSet:
    """The complete typed capability set consumed by ``ObserveNode``."""

    queue_port: ObservationQueuePort
    background_task_port: BackgroundTaskPort
    config_port: ConfigObservationPort
    context_port: ContextObservationPort
    ack_port: ObservationAckPort
    resume_port: ObservationResumePort
    _resume_binding: InitVar[ObservationResumeBinding | None] = field(default=None, kw_only=True, repr=False)
    _resume_capture: InitVar[ObservationResumeCapture | None] = field(default=None, kw_only=True, repr=False)
    _captured_resume_binding: ObservationResumeBinding = field(init=False, repr=False, compare=False)
    _captured_resume_capture: ObservationResumeCapture = field(init=False, repr=False, compare=False)

    def __post_init__(
        self,
        resume_binding: ObservationResumeBinding | None,
        resume_capture: ObservationResumeCapture | None,
    ) -> None:
        captured = require_observe_port_contracts(
            self.queue_port,
            self.background_task_port,
            self.config_port,
            self.context_port,
            self.ack_port,
            self.resume_port,
            resume_binding=resume_binding,
            resume_capture=resume_capture,
        )
        object.__setattr__(self, "_captured_resume_binding", captured)
        if resume_capture is None:
            # ``require_observe_port_contracts`` has already read metadata
            # exactly once; retain the admitted value and Port identity for a
            # later partial-decoration rebuild.
            resume_capture = ObservationResumeCapture(self.resume_port, captured)
        object.__setattr__(self, "_captured_resume_capture", resume_capture)

    @property
    def resume_binding(self) -> ObservationResumeBinding:
        """Return the immutable codec binding admitted at construction."""

        return self._captured_resume_binding

    @property
    def resume_capture(self) -> ObservationResumeCapture:
        """Return the admitted codec provenance for partial decoration."""

        return self._captured_resume_capture


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
        _validate_decorator(self.queue, "ObserveFailoverDecorators.queue")
        _validate_decorator(self.background_task, "ObserveFailoverDecorators.background_task")
        _validate_decorator(self.config, "ObserveFailoverDecorators.config")
        _validate_decorator(self.context, "ObserveFailoverDecorators.context")
        _validate_decorator(self.ack, "ObserveFailoverDecorators.ack")
        _validate_decorator(self.resume, "ObserveFailoverDecorators.resume")

    @classmethod
    def disabled(cls) -> ObserveFailoverDecorators:
        """Return explicit no-failover bindings for all Observe Ports."""

        return cls()

    @classmethod
    def uniform(cls, decorator: FailoverPortDecorator, /) -> ObserveFailoverDecorators:
        """Apply one typed-preserving decorator to all six Observe Ports."""

        _validate_decorator(decorator, "ObserveFailoverDecorators.uniform")
        return cls(decorator, decorator, decorator, decorator, decorator, decorator)

    def queue_port(self, port: ObservationQueuePort, /) -> ObservationQueuePort:
        return _apply(port, self.queue, ObservationQueuePort, "ObservationQueuePort")

    def background_task_port(self, port: BackgroundTaskPort, /) -> BackgroundTaskPort:
        return _apply(port, self.background_task, BackgroundTaskPort, "BackgroundTaskPort")

    def config_port(self, port: ConfigObservationPort, /) -> ConfigObservationPort:
        return _apply(port, self.config, ConfigObservationPort, "ConfigObservationPort")

    def context_port(self, port: ContextObservationPort, /) -> ContextObservationPort:
        return _apply(port, self.context, ContextObservationPort, "ContextObservationPort")

    def ack_port(self, port: ObservationAckPort, /) -> ObservationAckPort:
        return _apply(port, self.ack, ObservationAckPort, "ObservationAckPort")

    def resume_port(self, port: ObservationResumePort, /) -> ObservationResumePort:
        # Codec metadata is exposed by properties and must be captured once
        # by ``require_observe_port_contracts`` after all Port decorations.
        return _apply(port, self.resume, ObservationResumePort, "ObservationResumePort", validate=False)

    def decorate(self, ports: ObservePortSet, /) -> ObservePortSet:
        if type(ports) is not ObservePortSet:
            raise ObserveContractError("Observe failover decoration requires an ObservePortSet")
        queue_port = self.queue_port(ports.queue_port)
        background_task_port = self.background_task_port(ports.background_task_port)
        config_port = self.config_port(ports.config_port)
        context_port = self.context_port(ports.context_port)
        ack_port = self.ack_port(ports.ack_port)
        resume_port = self.resume_port(ports.resume_port)
        # Preserve a previously validated set when every wrapper keeps the
        # original object.  In particular this avoids rereading synchronous
        # resume codec metadata from single-read providers.
        if (
            queue_port is ports.queue_port
            and background_task_port is ports.background_task_port
            and config_port is ports.config_port
            and context_port is ports.context_port
            and ack_port is ports.ack_port
            and resume_port is ports.resume_port
        ):
            return ports
        cached_binding = ports.resume_binding if resume_port is ports.resume_port else None
        cached_capture = ports.resume_capture if resume_port is ports.resume_port else None
        return ObservePortSet(
            queue_port,
            background_task_port,
            config_port,
            context_port,
            ack_port,
            resume_port,
            _resume_binding=cached_binding,
            _resume_capture=cached_capture,
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


def decorate_observe_ports(
    queue_port: ObservationQueuePort,
    background_task_port: BackgroundTaskPort,
    config_port: ConfigObservationPort,
    context_port: ContextObservationPort,
    ack_port: ObservationAckPort,
    resume_port: ObservationResumePort,
    /,
    *,
    failover: ObserveFailoverDecorators | FailoverPortDecorator | None = None,
) -> ObservePortSet:
    """Apply Observe's failover declarations before graph-node assembly."""

    decorators = normalize_observe_failover_decorators(failover)
    # Apply declarations before constructing the validated set so resume
    # codec metadata is captured exactly once for single-read providers.
    return ObservePortSet(
        decorators.queue_port(queue_port),
        decorators.background_task_port(background_task_port),
        decorators.config_port(config_port),
        decorators.context_port(context_port),
        decorators.ack_port(ack_port),
        decorators.resume_port(resume_port),
    )


__all__ = [
    "FailoverPortDecorator",
    "ObserveFailoverDecorators",
    "ObservePortSet",
    "decorate_observe_ports",
    "normalize_observe_failover_decorators",
]
