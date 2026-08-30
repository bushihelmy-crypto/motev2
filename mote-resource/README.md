# Mote Resource

`mote-resource` is Mote's resource-provider umbrella. It owns registration,
discovery, lookup, capability descriptions, and typed handle resolution for
the resources an Agent assignment can use.

The umbrella has two parallel resource boundaries:

- `container/`: an Agent/Kernel execution host, such as a local, Docker, or
  Cloudflare Container;
- `embodiment/`: a physical body and its capabilities. A robot is one kind of
  Embodiment; the name also covers manipulators, drones, and similar devices.

`mote-control` owns Agent identity, assignment, authority, leases, lifecycle,
and placement decisions. An assignment may bind a `ContainerBinding` and an
`EmbodimentBinding` independently. If brain and body must share a device,
Control records a co-location constraint; it is not a third `robot-edge`
resource or package.

`mote-runtime` owns live action, sensor, driver, and other domain providers.
Resource providers resolve narrow typed handles and inject only the context a
consumer needs; they do not become a runtime scheduler, Agent Flow engine, or
persistence owner.

```text
mote-control
    ↓ assignment / authority
mote-resource
    ├── container    Agent/Kernel hosting providers
    └── embodiment   physical capability providers
         ↓ narrow handles / capability context
      mote-runtime
```

The current concrete Container providers live under
[`container/cloudflare`](container/cloudflare). Embodiment providers are
reserved until a concrete consumer and matching `conformance` contract exist.
