"""Compiled-graph ownership checks for execution snapshots."""

from typing import TypeVar

from mote_kernel.execution.errors import SnapshotMismatchError
from mote_kernel.execution.graph import CompiledGraph
from mote_kernel.execution.snapshot import ExecutionSnapshot

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def require_snapshot_matches_graph(graph: CompiledGraph[InputT, OutputT], snapshot: ExecutionSnapshot) -> None:
    """Reject a snapshot owned by another graph definition or version."""

    if snapshot.definition_id != graph.definition_id or snapshot.definition_version != graph.version:
        raise SnapshotMismatchError("execution snapshot does not match the compiled graph identity and version")


__all__: list[str] = []
