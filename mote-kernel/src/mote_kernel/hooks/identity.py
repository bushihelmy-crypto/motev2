"""Stable identities owned by the Hooks package."""

from dataclasses import dataclass
from enum import Enum, auto

from mote_kernel.state.graph_state.identity import (
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    is_canonical_identity,
)


class HookStage(Enum):
    """The lifecycle boundary currently reserved for a hook node."""

    AFTER_NODE = auto()


class HookPriority(Enum):
    """The fixed node order inside one HookNode."""

    P1 = auto()
    P2 = auto()
    P3 = auto()


@dataclass(frozen=True, slots=True)
class HookSlotId:
    """A compile-time slot owned by one node in one graph definition."""

    definition_id: GraphDefinitionId
    definition_version: GraphDefinitionVersion
    node_id: GraphNodeId
    stage: HookStage = HookStage.AFTER_NODE

    def __post_init__(self) -> None:
        if not is_canonical_identity(self.definition_id):
            raise ValueError("hook slot definition id must be canonical")
        if type(self.definition_version) is not int or self.definition_version < 1:
            raise ValueError("hook slot definition version must be a positive integer")
        if not is_canonical_identity(self.node_id):
            raise ValueError("hook slot node id must be canonical")
        if type(self.stage) is not HookStage:
            raise ValueError("hook slot stage must be a HookStage")


__all__ = ["HookPriority", "HookSlotId", "HookStage"]
