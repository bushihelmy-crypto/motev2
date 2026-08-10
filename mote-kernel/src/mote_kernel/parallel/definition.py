"""Stable declarations for exclusive Kernel resources."""

from dataclasses import dataclass
from typing import NewType

ResourceId = NewType("ResourceId", str)


@dataclass(frozen=True, slots=True)
class ResourceDefinition:
    """Declare one exclusive resource at a stable position in its graph order."""

    resource_id: ResourceId
    order: int


__all__ = ["ResourceDefinition", "ResourceId"]
