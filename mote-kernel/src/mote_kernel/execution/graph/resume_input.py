"""Deterministic graph-owned resume input binding."""

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from mote_kernel.state.graph_state.frontier_model import GraphResumeInputCodecId

InputT = TypeVar("InputT")
InputT_contra = TypeVar("InputT_contra", contravariant=True)
InputT_co = TypeVar("InputT_co", covariant=True)


class ResumeInputEncoder(Protocol[InputT_contra]):
    def encode(self, value: InputT_contra) -> bytes: ...


class ResumeInputDecoder(Protocol[InputT_co]):
    def decode(self, payload: bytes) -> InputT_co: ...


@dataclass(frozen=True, slots=True)
class ResumeInputBinding(Generic[InputT]):
    codec_id: GraphResumeInputCodecId
    version: int
    encoder: ResumeInputEncoder[InputT]
    decoder: ResumeInputDecoder[InputT]


__all__ = ["ResumeInputBinding", "ResumeInputDecoder", "ResumeInputEncoder"]
