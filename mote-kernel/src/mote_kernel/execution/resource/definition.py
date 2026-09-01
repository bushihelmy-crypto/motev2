"""Stable declarations for exclusive execution resources."""

from dataclasses import dataclass

from mote_kernel.state.graph_state.resource_model import ResourceId


@dataclass(frozen=True, slots=True)
class ResourceDefinition:
    """Declare one exclusive resource; the containing tuple supplies its graph order."""

    resource_id: ResourceId


__all__ = ["ResourceDefinition", "ResourceId"]
