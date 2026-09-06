"""Deterministic graph-local resume-frame codec binding."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.execution.graph.values import _GraphValues
from mote_kernel.state.graph_state import GraphResumeInputCodecId

GraphValueT = TypeVar("GraphValueT")


@dataclass(frozen=True, slots=True)
class ResumeInputBinding(Generic[GraphValueT]):
    codec_id: GraphResumeInputCodecId
    version: int
    encoder: Callable[[_GraphValues[GraphValueT]], bytes]
    decoder: Callable[[bytes], _GraphValues[GraphValueT]]


__all__: list[str] = []
