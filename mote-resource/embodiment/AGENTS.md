# Mote Embodiment engineering rules

- This boundary owns Embodiment resource registration, discovery, capability
  description, and handle resolution.
- An Embodiment is a physical body (for example a mobile robot, manipulator,
  or drone). Do not equate the resource with a particular runtime process.
- `mote-control` owns identity, assignment, authority, leases, lifecycle, and
  optional co-location constraints with a Container.
- `mote-runtime` owns live action, sensor, and driver providers. Keep the
  resource-facing context narrow and typed; do not move scheduling or domain
  state into this boundary.
- Do not invent a Robot/Embodiment wire protocol or cross-layer DTO before a
  concrete consumer and matching `conformance` contract exist.
- Preserve user changes and inspect the relevant Git diff before editing.
