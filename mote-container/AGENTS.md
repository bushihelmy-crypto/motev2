# Mote Container engineering rules

- This boundary owns container registration, lookup, and uniform invocation across concrete environments.
- `mote-control` owns Agent identity, lineage, placement decisions, and lifecycle authority. Containers consume Control-issued identities and decisions.
- `mote-kernel` owns Agent creation and flow semantics. Containers invoke Kernel contracts without copying or reinterpreting them.
- Concrete persistence and transaction mechanisms belong to `mote-persistence`; Container code must not own SQL schemas, transaction code, backend-specific state semantics, or persistence-backend selection.
- Container selection and persistence selection are independent. Kernel Port configuration selects the `Commit` backend; a Container may expose optional platform capabilities such as Durable Object storage without requiring that they be used.
- Platform implementations may contain the minimum entry point, deployment binding, and Kernel-hosting glue required by their runtime, but persistence composition, Product routing, and presentation do not belong here.
- Do not invent cross-layer request DTOs or identity encodings outside `conformance/` and their owning component.
- Preserve user changes and inspect the relevant Git diff before editing.
- Read the nearest platform/language `AGENTS.md` before changing a concrete Container implementation.
