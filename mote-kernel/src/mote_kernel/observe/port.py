"""Narrow capability Ports consumed by the Observe graph.

Providers own the physical queue, Context/config stores, task registry, and
acknowledgement transaction.  Observe receives only these typed facets and
never keeps a second copy of provider state.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from mote_kernel.execution import Graph
from mote_kernel.hooks.contract import HookGraphValue
from mote_kernel.observe.contract import (
    AssistantBatch,
    BackgroundTaskSnapshot,
    ConfigApplyResult,
    ConfigBatch,
    ContextAppendReceipt,
    DeliveryAck,
    ObservationBatchReceipt,
    ObservationRead,
    ObserveContractError,
    ToolBatch,
    UserBatch,
)
from mote_kernel.observe.identity import (
    DeliveryId,
    ObservationBoundary,
    ObservationCursor,
    ObservationWait,
    WaitRegistration,
)


@runtime_checkable
class ObservationQueuePort(Protocol):
    """Read one complete FIFO window and register durable wake coordinates."""

    async def read_after(self, cursor: ObservationCursor, /) -> ObservationRead: ...

    async def register_wait(self, wait: ObservationWait, /) -> WaitRegistration: ...


@runtime_checkable
class BackgroundTaskPort(Protocol):
    """Project task facts at one atomic queue boundary."""

    async def snapshot(self, boundary: ObservationBoundary, /) -> BackgroundTaskSnapshot: ...


@runtime_checkable
class ConfigObservationPort(Protocol):
    """Apply Config deliveries and return receipt plus optional successor."""

    async def apply(self, batch: ConfigBatch, /) -> ConfigApplyResult: ...


@runtime_checkable
class ContextObservationPort(Protocol):
    """Append one complete non-Config family to Context in FIFO order."""

    async def append(self, batch: ToolBatch | UserBatch | AssistantBatch, /) -> ContextAppendReceipt: ...


@runtime_checkable
class ObservationAckPort(Protocol):
    """Acknowledge every settled delivery after the Graph commit is confirmed."""

    async def acknowledge(
        self,
        delivery_ids: tuple[DeliveryId, ...],
        receipt: ObservationBatchReceipt,
        /,
    ) -> DeliveryAck: ...


@runtime_checkable
class ObservationResumePort(Protocol):
    """Own the durable codec for an Observe graph-input resume frame.

    ``Graph.interrupt`` transports only opaque bytes.  The queue wait payload
    tells the host *where* to reread, while this capability owns encoding and
    decoding the concrete ``ObserveRequest`` (including the Hook state) that
    the interrupted ``get_observation`` node needs when it is resumed.  The
    codec is synchronous and deterministic; it must not keep an in-memory
    token table.
    """

    def encode_graph_input(self, values: Graph.Values[HookGraphValue], /) -> bytes: ...

    def decode_graph_input(self, payload: bytes, /) -> Graph.Values[HookGraphValue]: ...

    @property
    def codec_id(self) -> str: ...

    @property
    def codec_version(self) -> int: ...


@dataclass(frozen=True, slots=True)
class ObservationResumeBinding:
    """Resume callables and metadata captured exactly once during assembly."""

    codec_id: str
    codec_version: int
    encoder: Callable[[Graph.Values[HookGraphValue]], bytes]
    decoder: Callable[[bytes], Graph.Values[HookGraphValue]]


@dataclass(frozen=True, slots=True)
class ObservationResumeCapture:
    """Provenance for a single-read Observe resume codec.

    This is an Observe-internal assembly value and is not part of the package
    level failover API.
    """

    port: ObservationResumePort
    binding: ObservationResumeBinding


def _validate_resume_codec_identity(codec_id: str, codec_version: int, /) -> tuple[str, int]:
    if (
        type(codec_id) is not str
        or not codec_id
        or codec_id != codec_id.strip()
        or "\n" in codec_id
        or "\r" in codec_id
    ):
        raise ObserveContractError("ObservationResumePort.codec_id must be a canonical string")
    if type(codec_version) is not int or codec_version < 1:
        raise ObserveContractError("ObservationResumePort.codec_version must be a positive integer")
    return codec_id, codec_version


def _resume_methods(
    resume_port: ObservationResumePort | None,
    /,
) -> tuple[
    Callable[[Graph.Values[HookGraphValue]], bytes],
    Callable[[bytes], Graph.Values[HookGraphValue]],
]:
    if resume_port is None:
        raise ObserveContractError("ObserveNode requires a ObservationResumePort")
    try:
        encoder = resume_port.encode_graph_input
        decoder = resume_port.decode_graph_input
    except AttributeError as error:
        raise ObserveContractError("ObserveNode requires a ObservationResumePort") from error
    if not callable(encoder) or not callable(decoder):
        raise ObserveContractError("ObservationResumePort codec methods must be callable")
    return encoder, decoder


def capture_observation_resume_binding(
    resume_port: ObservationResumePort | None,
    /,
) -> ObservationResumeCapture:
    """Capture one complete resume codec without rereading its properties."""

    encoder, decoder = _resume_methods(resume_port)
    assert resume_port is not None
    try:
        codec_id = resume_port.codec_id
        codec_version = resume_port.codec_version
    except AttributeError as error:
        raise ObserveContractError("ObserveNode requires a ObservationResumePort") from error
    codec_id, codec_version = _validate_resume_codec_identity(codec_id, codec_version)
    return ObservationResumeCapture(
        resume_port,
        ObservationResumeBinding(codec_id, codec_version, encoder, decoder),
    )


def _reuse_observation_resume_binding(
    resume_port: ObservationResumePort | None,
    binding: ObservationResumeBinding,
    /,
) -> ObservationResumeBinding:
    if type(binding) is not ObservationResumeBinding:
        raise ObserveContractError("ObservationResumePort codec binding is malformed")
    encoder, decoder = _resume_methods(resume_port)
    codec_id, codec_version = _validate_resume_codec_identity(binding.codec_id, binding.codec_version)
    return ObservationResumeBinding(codec_id, codec_version, encoder, decoder)


def _require_observation_resume_binding(
    resume_port: ObservationResumePort | None,
    binding: ObservationResumeBinding | None,
    capture: ObservationResumeCapture | None,
    /,
) -> ObservationResumeCapture:
    """Admit cached metadata only when its provenance matches the Port."""

    if binding is not None and type(binding) is not ObservationResumeBinding:
        raise ObserveContractError("ObservationResumePort codec binding is malformed")
    if capture is not None:
        if resume_port is None or capture.port is not resume_port:
            raise ObserveContractError("ObservationResumePort codec binding provenance does not match the Port")
        admitted = _reuse_observation_resume_binding(resume_port, capture.binding)
        if binding is not None and (
            binding.codec_id != admitted.codec_id or binding.codec_version != admitted.codec_version
        ):
            raise ObserveContractError("ObservationResumePort codec binding does not match the captured contract")
        return ObservationResumeCapture(resume_port, admitted)
    captured = capture_observation_resume_binding(resume_port)
    if binding is not None and (
        binding.codec_id != captured.binding.codec_id or binding.codec_version != captured.binding.codec_version
    ):
        raise ObserveContractError("ObservationResumePort codec binding does not match the Port contract")
    return captured


def require_observe_port_contracts(
    queue_port: ObservationQueuePort | None,
    task_port: BackgroundTaskPort | None,
    config_port: ConfigObservationPort | None,
    context_port: ContextObservationPort | None,
    ack_port: ObservationAckPort | None,
    resume_port: ObservationResumePort | None,
    *,
    resume_binding: ObservationResumeBinding | None = None,
    resume_capture: ObservationResumeCapture | None = None,
) -> ObservationResumeBinding:
    """Validate all Observe capabilities before Graph assembly is touched.

    Resume encoding is required alongside the five provider capabilities:
    an empty queue is a durable Graph interrupt, and a graph that cannot
    materialize its ``request`` input on resume is not a valid Observe graph.
    The helper is intentionally kept in this module so assembly and tests use
    one structural check.  It does not invoke a provider or inspect its state.
    """

    if not isinstance(queue_port, ObservationQueuePort):
        raise ObserveContractError("ObserveNode requires a ObservationQueuePort")
    if not callable(queue_port.read_after) or not callable(queue_port.register_wait):
        raise ObserveContractError("ObservationQueuePort read/wait methods must be callable")

    if not isinstance(task_port, BackgroundTaskPort):
        raise ObserveContractError("ObserveNode requires a BackgroundTaskPort")
    if not callable(task_port.snapshot):
        raise ObserveContractError("BackgroundTaskPort.snapshot must be callable")

    if not isinstance(config_port, ConfigObservationPort):
        raise ObserveContractError("ObserveNode requires a ConfigObservationPort")
    if not callable(config_port.apply):
        raise ObserveContractError("ConfigObservationPort.apply must be callable")

    if not isinstance(context_port, ContextObservationPort):
        raise ObserveContractError("ObserveNode requires a ContextObservationPort")
    if not callable(context_port.append):
        raise ObserveContractError("ContextObservationPort.append must be callable")

    if not isinstance(ack_port, ObservationAckPort):
        raise ObserveContractError("ObserveNode requires a ObservationAckPort")
    if not callable(ack_port.acknowledge):
        raise ObserveContractError("ObservationAckPort.acknowledge must be callable")

    captured = _require_observation_resume_binding(resume_port, resume_binding, resume_capture)
    return captured.binding


__all__ = [
    "BackgroundTaskPort",
    "ConfigObservationPort",
    "ContextObservationPort",
    "ObservationAckPort",
    "ObservationQueuePort",
    "ObservationResumePort",
    "require_observe_port_contracts",
]
