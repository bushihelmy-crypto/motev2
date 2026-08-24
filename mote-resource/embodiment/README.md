# Mote Embodiment

`mote-resource/embodiment` is the resource boundary for a physical body. A
robot is the first expected kind of Embodiment, but the name intentionally
leaves room for manipulators, drones, and other embodied devices.

This boundary provides registration, discovery, capability descriptions, and
typed handle resolution. It does not own the Agent's assignment or authority,
and it does not run the real-time action/sensor loop. Those responsibilities
remain with `mote-control` and `mote-runtime`, respectively.

An assignment may bind an `EmbodimentBinding` independently of its
`ContainerBinding`. If a deployment requires the two to be on the same device,
Control records that as a co-location constraint. No third `robot-edge`
resource is needed.

Concrete providers and any shared wire contracts should be added only with a
real consumer and corresponding `conformance` cases.
