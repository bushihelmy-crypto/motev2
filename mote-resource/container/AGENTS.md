# Mote Container engineering rules

- This boundary owns Container registration, lookup, hosting, and narrow handle
  exposure across concrete environments. It is nested under the
  `mote-resource` umbrella.
- `mote-control` owns Agent identity, lineage, assignment, placement
  decisions, and lifecycle authority. Containers consume Control-issued
  identities and decisions.
- `mote-kernel` owns Agent creation and flow semantics. Containers host Kernel without copying or reinterpreting its contracts.
- `mote-infra/invocation` is the sole owner of invocation contracts, resolution, and local/RPC implementations. Container code exposes host capabilities but does not implement a parallel invoker.
- Concrete persistence and transaction mechanisms belong to
  `mote-infra/persistence`; Container code must not own SQL schemas,
  transaction code, backend-specific state semantics, or persistence-backend
  selection.
- Container selection and persistence selection are independent. Kernel Port configuration selects the `Commit` backend; a Container may expose optional platform capabilities such as Durable Object storage without requiring that they be used.
- Platform implementations may contain the minimum entry point, deployment
  binding, and Kernel-hosting glue required by their runtime, but invocation
  composition, persistence composition, Product routing, and presentation do
  not belong here.
- Container and Embodiment are parallel resource kinds. Co-location of a
  Container and an Embodiment is a Control placement constraint, not a
  `robot-edge` implementation in this boundary.
- Do not invent cross-layer request DTOs or identity encodings outside `conformance/` and their owning component.
- Preserve user changes and inspect the relevant Git diff before editing.
- Read the nearest platform/language `AGENTS.md` before changing a concrete Container implementation.
