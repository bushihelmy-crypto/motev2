"""Runtime shape checks for the Observe capability protocols."""

from __future__ import annotations

from typing import Never, cast

import pytest

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
from mote_kernel.observe.port import (
    BackgroundTaskPort,
    ConfigObservationPort,
    ContextObservationPort,
    ObservationAckPort,
    ObservationQueuePort,
    ObservationResumeBinding,
    ObservationResumeCapture,
    ObservationResumePort,
    capture_observation_resume_binding,
    require_observe_port_contracts,
)


class _CompleteBundle:
    async def read_after(self, cursor: ObservationCursor, /) -> ObservationRead:
        del cursor
        raise NotImplementedError

    async def register_wait(self, wait: ObservationWait, /) -> WaitRegistration:
        del wait
        raise NotImplementedError

    async def snapshot(self, boundary: ObservationBoundary, /) -> BackgroundTaskSnapshot:
        del boundary
        raise NotImplementedError

    async def apply(self, batch: ConfigBatch, /) -> ConfigApplyResult:
        del batch
        raise NotImplementedError

    async def append(self, batch: ToolBatch | UserBatch | AssistantBatch, /) -> ContextAppendReceipt:
        del batch
        raise NotImplementedError

    async def acknowledge(
        self,
        delivery_ids: tuple[DeliveryId, ...],
        receipt: ObservationBatchReceipt,
        /,
    ) -> DeliveryAck:
        del delivery_ids, receipt
        raise NotImplementedError

    def encode_graph_input(self, values: Graph.Values[HookGraphValue], /) -> bytes:
        del values
        raise NotImplementedError

    def decode_graph_input(self, payload: bytes, /) -> Graph.Values[HookGraphValue]:
        del payload
        raise NotImplementedError

    @property
    def codec_id(self) -> str:
        return "observe-test.codec"

    @property
    def codec_version(self) -> int:
        return 1


class _Incomplete:
    pass


class _NonCallableQueue(ObservationQueuePort):
    read_after = None  # type: ignore[assignment]
    register_wait = None  # type: ignore[assignment]


class _NonCallableTask(BackgroundTaskPort):
    snapshot = None  # type: ignore[assignment]


class _NonCallableConfig(ConfigObservationPort):
    apply = None  # type: ignore[assignment]


class _NonCallableContext(ContextObservationPort):
    append = None  # type: ignore[assignment]


class _NonCallableAck(ObservationAckPort):
    acknowledge = None  # type: ignore[assignment]


class _NonCallableResume(ObservationResumePort):
    encode_graph_input = None  # type: ignore[assignment]
    decode_graph_input = None  # type: ignore[assignment]

    @property
    def codec_id(self) -> str:
        return "observe-test.codec"

    @property
    def codec_version(self) -> int:
        return 1


def test_runtime_checkable_protocols_admit_a_complete_bundle() -> None:
    bundle = _CompleteBundle()
    assert isinstance(bundle, ObservationQueuePort)
    assert isinstance(bundle, BackgroundTaskPort)
    assert isinstance(bundle, ConfigObservationPort)
    assert isinstance(bundle, ContextObservationPort)
    assert isinstance(bundle, ObservationAckPort)
    assert isinstance(bundle, ObservationResumePort)
    require_observe_port_contracts(bundle, bundle, bundle, bundle, bundle, bundle)


@pytest.mark.parametrize(
    "protocol",
    [
        ObservationQueuePort,
        BackgroundTaskPort,
        ConfigObservationPort,
        ContextObservationPort,
        ObservationAckPort,
        ObservationResumePort,
    ],
)
def test_runtime_checkable_protocols_reject_an_incomplete_object(protocol: type[object]) -> None:
    assert not isinstance(_Incomplete(), protocol)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("queue", "ObservationQueuePort"),
        ("task", "BackgroundTaskPort"),
        ("config", "ConfigObservationPort"),
        ("context", "ContextObservationPort"),
        ("ack", "ObservationAckPort"),
        ("resume", "ObservationResumePort"),
    ],
)
def test_assembly_rejects_missing_capabilities(field: str, message: str) -> None:
    values: dict[str, object] = {
        "queue": _CompleteBundle(),
        "task": _CompleteBundle(),
        "config": _CompleteBundle(),
        "context": _CompleteBundle(),
        "ack": _CompleteBundle(),
        "resume": _CompleteBundle(),
    }
    values[field] = None

    with pytest.raises(ValueError, match=message):
        require_observe_port_contracts(
            cast(Never, values["queue"]),
            cast(Never, values["task"]),
            cast(Never, values["config"]),
            cast(Never, values["context"]),
            cast(Never, values["ack"]),
            cast(Never, values["resume"]),
        )


@pytest.mark.parametrize(
    ("port", "message"),
    [
        (_NonCallableQueue(), "read/wait methods"),
        (_NonCallableTask(), "BackgroundTaskPort.snapshot"),
        (_NonCallableConfig(), "ConfigObservationPort.apply"),
        (_NonCallableContext(), "ContextObservationPort.append"),
        (_NonCallableAck(), "ObservationAckPort.acknowledge"),
        (_NonCallableResume(), "codec methods"),
    ],
)
def test_assembly_rejects_structural_ports_with_noncallable_members(port: object, message: str) -> None:
    values: list[object] = [_CompleteBundle()] * 6
    if isinstance(port, _NonCallableQueue):
        values[0] = port
    elif isinstance(port, _NonCallableTask):
        values[1] = port
    elif isinstance(port, _NonCallableConfig):
        values[2] = port
    elif isinstance(port, _NonCallableContext):
        values[3] = port
    elif isinstance(port, _NonCallableAck):
        values[4] = port
    else:
        values[5] = port

    with pytest.raises(ValueError, match=message):
        require_observe_port_contracts(
            cast(Never, values[0]),
            cast(Never, values[1]),
            cast(Never, values[2]),
            cast(Never, values[3]),
            cast(Never, values[4]),
            cast(Never, values[5]),
        )


def test_resume_contract_rejects_a_nonnull_object_without_codec_members() -> None:
    bundle = _CompleteBundle()
    with pytest.raises(ValueError, match="ObservationResumePort"):
        require_observe_port_contracts(bundle, bundle, bundle, bundle, bundle, cast(Never, _Incomplete()))


def test_resume_contract_captures_and_reuses_codec_provenance() -> None:
    bundle = _CompleteBundle()
    capture = capture_observation_resume_binding(bundle)
    assert isinstance(capture, ObservationResumeCapture)
    assert capture.binding.codec_id == "observe-test.codec"
    assert (
        require_observe_port_contracts(
            bundle,
            bundle,
            bundle,
            bundle,
            bundle,
            bundle,
            resume_capture=capture,
            resume_binding=ObservationResumeBinding(
                capture.binding.codec_id,
                capture.binding.codec_version,
                capture.binding.encoder,
                capture.binding.decoder,
            ),
        )
        == capture.binding
    )

    with pytest.raises(ObserveContractError, match="provenance"):
        require_observe_port_contracts(
            bundle,
            bundle,
            bundle,
            bundle,
            bundle,
            _CompleteBundle(),
            resume_capture=capture,
        )
    with pytest.raises(ObserveContractError, match="captured contract"):
        require_observe_port_contracts(
            bundle,
            bundle,
            bundle,
            bundle,
            bundle,
            bundle,
            resume_capture=capture,
            resume_binding=ObservationResumeBinding(
                "other.codec",
                capture.binding.codec_version,
                capture.binding.encoder,
                capture.binding.decoder,
            ),
        )
    with pytest.raises(ObserveContractError, match="Port contract"):
        require_observe_port_contracts(
            bundle,
            bundle,
            bundle,
            bundle,
            bundle,
            bundle,
            resume_binding=ObservationResumeBinding(
                "other.codec",
                capture.binding.codec_version,
                capture.binding.encoder,
                capture.binding.decoder,
            ),
        )
    with pytest.raises(ObserveContractError, match="malformed"):
        require_observe_port_contracts(
            bundle,
            bundle,
            bundle,
            bundle,
            bundle,
            bundle,
            resume_binding=cast(ObservationResumeBinding, object()),
        )
    malformed_capture = ObservationResumeCapture(bundle, cast(ObservationResumeBinding, object()))
    with pytest.raises(ObserveContractError, match="malformed"):
        require_observe_port_contracts(
            bundle,
            bundle,
            bundle,
            bundle,
            bundle,
            bundle,
            resume_capture=malformed_capture,
        )

    class MissingCodecMetadata(_CompleteBundle):
        @property
        def codec_id(self) -> str:
            raise AttributeError("codec_id unavailable")

        @property
        def codec_version(self) -> int:
            raise AttributeError("codec_version unavailable")

    with pytest.raises(ObserveContractError, match="ObservationResumePort"):
        capture_observation_resume_binding(MissingCodecMetadata())


@pytest.mark.parametrize(
    ("codec_id", "codec_version", "message"),
    [
        (" bad", 1, "codec_id"),
        ("observe-test.codec", 0, "codec_version"),
    ],
)
def test_resume_contract_rejects_noncanonical_codec_metadata(
    codec_id: str,
    codec_version: int,
    message: str,
) -> None:
    class BadMetadataBundle(_CompleteBundle):
        @property
        def codec_id(self) -> str:
            return codec_id

        @property
        def codec_version(self) -> int:
            return codec_version

    bundle = BadMetadataBundle()
    with pytest.raises(ValueError, match=message):
        require_observe_port_contracts(bundle, bundle, bundle, bundle, bundle, bundle)
