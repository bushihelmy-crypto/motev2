"""Assembly of one fixed failover graph around one concrete Port.

Role/Flow assembly will resolve profile inheritance and bind each Port to its
own graph activation.  This module must return the canonical execution Graph
and must not introduce a second runner or state owner.
"""
