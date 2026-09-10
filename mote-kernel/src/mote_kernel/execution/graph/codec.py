"""One typed codec binding shared by durable and resume value frames."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.execution.errors import GraphValueAdmissionError, InvalidGraphIdentityError
from mote_kernel.execution.graph.values import _GraphValues, _require_graph_values
from mote_kernel.state.graph_state.identity import is_canonical_identity

GraphValueT = TypeVar("GraphValueT")


@dataclass(frozen=True, slots=True)
class FrameCodec(Generic[GraphValueT]):
    codec_id: str
    version: int
    encoder: Callable[[_GraphValues[GraphValueT]], bytes]
    decoder: Callable[[bytes], _GraphValues[GraphValueT]]

    def validate(self) -> None:
        if not is_canonical_identity(self.codec_id):
            raise InvalidGraphIdentityError("frame codec identity must be canonical")
        if type(self.version) is not int or self.version < 1:
            raise InvalidGraphIdentityError("frame codec version must be an exact positive integer")
        if not callable(self.encoder) or not callable(self.decoder):
            raise InvalidGraphIdentityError("frame codec encoder and decoder must be callable")

    def encode(self, values: _GraphValues[GraphValueT], /) -> bytes:
        values = _require_graph_values(values)
        try:
            payload = self.encoder(values)
        except Exception as error:
            raise GraphValueAdmissionError("frame encoder rejected the value frame") from error
        if type(payload) is not bytes:
            raise GraphValueAdmissionError("frame encoder must return bytes")
        return payload

    def decode(self, payload: bytes, /) -> _GraphValues[GraphValueT]:
        if type(payload) is not bytes:
            raise GraphValueAdmissionError("frame decoder requires bytes")
        try:
            values = self.decoder(payload)
        except Exception as error:
            raise GraphValueAdmissionError("frame decoder rejected its opaque payload") from error
        return _require_graph_values(values)


__all__: list[str] = []
