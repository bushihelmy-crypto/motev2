# Mote Resource engineering rules

- `mote-resource` is an umbrella for resource registration, discovery, lookup,
  capability description, and handle resolution. It is not an owner of Agent
  Flow semantics, embodiment domain state, or persistence transactions.
- `container/` and `embodiment/` are parallel resource boundaries. A Container
  is an Agent/Kernel host; an Embodiment is a physical body such as a robot.
- `mote-control` owns Agent identity, lineage, assignment, placement,
  authority, leases, and lifecycle decisions. Resource providers consume the
  bindings and narrow capability grants issued by Control.
- A Control assignment may bind a Container and an Embodiment independently.
  If brain and body must share a device, co-location is a placement constraint
  in Control; do not create a third `robot-edge` resource or package.
- `mote-runtime` owns live execution state and concrete action, sensor, and
  driver providers. Resource code resolves a handle and exposes capabilities;
  it must not become a runtime scheduler or state store.
- Container selection and persistence selection are independent. Kernel Port
  configuration selects the `Commit` backend; a Container may expose optional
  platform capabilities without selecting or requiring that backend.
- Concrete persistence and transaction mechanisms belong to
  `mote-infra/persistence`; RPC transport mechanics belong to
  `mote-infra/rpc`.
- Do not invent cross-layer request DTOs, identity encodings, or Embodiment
  wire protocols outside `conformance/` and their owning component.
- Preserve user changes and inspect the relevant Git diff before editing.
- Read the nearest child `AGENTS.md` before changing a concrete provider.
