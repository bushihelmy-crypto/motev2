# Mote Container

`mote-resource/container` contains providers for Agent/Kernel execution hosts.
It registers and locates a Container, then exposes a narrow typed handle while
leaving invocation infrastructure to `mote-infra/invocation`, Agent Flow
semantics to `mote-kernel`, and assignment authority to `mote-control`.

Container providers may be local, Docker, Cloudflare, or another hosting
environment. The host may inject a narrow runtime context and Port resolver
before starting Kernel; it must not select persistence, interpret Product
routes, or own Embodiment state.

Container and Embodiment are parallel resource boundaries under
`mote-resource`. An assignment can bind either one or both. When the brain and
body must be on one device, Control records a co-location constraint; this
Container package does not create a `robot-edge` resource.

The current concrete providers are:

```text
mote-resource/container/
└── cloudflare/
    ├── python/    Python Worker and Durable Object host
    └── ts/        TypeScript Worker and Durable Object host
```

The project is pre-alpha. Shared assignment and handle contracts are introduced
only with a concrete `mote-control` consumer and matching `conformance` cases.
