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

    AFTER_NODE = "after_node"


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


_HOOK_DEFINITION_DOMAIN = "mote.hook.v1"


def hook_definition_id(slot: HookSlotId) -> GraphDefinitionId:
    """Project one Hook slot into an unambiguous nested Graph definition id."""

    if type(slot) is not HookSlotId:
        raise TypeError("hook definition identity requires a HookSlotId")
    fields = (
        _HOOK_DEFINITION_DOMAIN,
        str(slot.definition_id),
        str(slot.node_id),
        slot.stage.value,
    )
    return GraphDefinitionId("".join(f"{len(field)}:{field}" for field in fields))


__all__ = ["HookPriority", "HookSlotId", "HookStage", "hook_definition_id"]
