"""Versioned graph-owned decoding of durable interrupt resolutions."""

from dataclasses import dataclass
from typing import Generic, NewType, Protocol, TypeVar

ResolutionCodecId = NewType("ResolutionCodecId", str)
InputT_co = TypeVar("InputT_co", covariant=True)


class ResolutionDecoder(Protocol[InputT_co]):
    """Deterministically decode durable bytes into the graph's immutable node input."""

    def decode(self, payload: bytes) -> InputT_co:
        """Return the input represented by this codec version."""
        ...


@dataclass(frozen=True, slots=True)
class ResolutionBinding(Generic[InputT_co]):
    """Bind a stable durable codec identity to one graph definition version."""

    codec_id: ResolutionCodecId
    version: int
    decoder: ResolutionDecoder[InputT_co]


__all__ = ["ResolutionBinding", "ResolutionCodecId", "ResolutionDecoder"]
