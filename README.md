# Mote v2

Mote v2 is an agent architecture organized around one separation of responsibility:

> **Kernel defines the agent flow. Runtime implements the steps in that flow. Product configures and presents the agent.**

An agent flow is a hierarchical composition of state machines. A Swarm is not another architecture-owned workflow above those agents: the model dynamically forms it through spawn, delegation, and communication, while the control plane records lineage and reliably carries those interactions.

The goal is not merely to connect a model to tools. Mote aims to make an agent a well-defined, durable computational unit whose flow can be understood independently of model providers, storage engines, operating-system integrations, deployment topology, or user interface.

The repository is intentionally being rebuilt from first principles. It is currently **pre-alpha**: the architectural boundaries and project infrastructure exist, while most runtime and product capabilities are still planned rather than implemented.

## Why Mote v2 exists

As an agent system grows, its control flow easily becomes distributed across model clients, tool executors, callbacks, session replay, event subscribers, user interfaces, and schedulers. Eventually there is no single place that answers a basic question: *how does this agent interact?*

Mote v2 makes that answer explicit and keeps it in the Kernel.

The architecture is designed to preserve three distinct kinds of ownership:

- **Kernel owns flow and meaning:** what the agent observes, how it thinks and acts, which state transitions are legal, and how execution continues, waits, completes, or recovers.
- **Runtime owns implementation mechanisms:** how a model is called, a tool is executed, state is persisted, an external effect is queried, or an operating-system resource is accessed.
- **Product owns use and presentation:** which capabilities are selected, how they are configured and assembled, how users provide input, and how events and results are displayed.

This prevents an implementation detail from quietly becoming agent semantics, and prevents a CLI, TUI, or service endpoint from creating a second agent loop.

## Architecture at a glance

```text
User / API / UI
       │
       ▼
┌──────────────────────────────────────────┐
│ Product                                  │
│ configuration · assembly · presentation  │
└───────────────────┬──────────────────────┘
                    │ input / events / outcome
                    ▼
┌──────────────────────────────────────────┐
│ Kernel                                   │
│ flow machines composed from state machines│
│ state · transitions · recovery · ports   │
└───────────────────┬──────────────────────┘
                    │ typed Port requests/results
                    ▼
┌──────────────────────────────────────────┐
│ Runtime                                  │
│ model · tools · state · effects · sandbox│
└──────────────────────────────────────────┘
```

The control relationship is equally important:

1. Product chooses configuration and supplies the concrete capabilities required by Kernel.
2. Kernel advances the agent flow.
3. When a flow step needs an external capability, Kernel invokes a narrow typed Port.
4. Runtime implements that Port and returns a typed result; it does not mutate Kernel state.
5. Kernel interprets the result, performs the legal state transition, and decides the next step.
6. Product projects Kernel events and outcomes to the user.

## Kernel: the agent architecture

`mote-kernel` is the architectural center of Mote. A reader should be able to understand how one agent interacts by reading the Kernel, without reconstructing its control flow from a model adapter, tool executor, persistence backend, or UI callback.

The default public composition entry point has not been designed or implemented yet. Internally, the Kernel separates the major branches of agent behavior:

```text
Agent flow
├── Observe       accept and interpret inputs
├── Think         construct cognition and produce decisions
├── Act           express and settle agent actions
├── Workflow      define domain-flow topology
├── Execution     advance every flow through one graph engine
├── State         govern execution position and established facts
├── Turn Context  construct the context for an interaction
├── Restore       continue the same logical agent after interruption
└── Ports         request concrete external capabilities
```

The future composition surface must remain a controlled agent-level facade, not a proxy for every Runtime service. Runtime-specific conveniences such as browser profiles, secret stores, database handles, or process clients must not accumulate on that public surface.

### An Agent flow is a composition of state machines

Kernel does not model an Agent as one flat loop or one giant state machine. Each concept with its own state, transitions, lifecycle, and invariants is a state machine. Flow is itself a state machine that composes the state machines belonging to that flow.

For example, a ReAct flow composes Think and Act:

```text
Agent
├── ReAct Flow state machine
│   ├── Think state machine
│   └── Act state machine
└── Failover state machine
```

Think and Act are not merely functions called by a loop. They own their states and legal transitions. The ReAct Flow state machine coordinates their typed inputs and outcomes and determines how the flow proceeds. Another Flow may compose a different set of state machines without creating another execution foundation.

Composition and collaboration are different relationships. Think and Act belong to ReAct and are composed by it. Failover is also a Kernel state machine, but it does not belong to the ReAct flow. It collaborates with ReAct and other flows through explicit typed protocols:

```text
ThinkMachine ─┐
              ├── composed by ──▶ ReActFlowMachine
ActMachine ───┘

ReActFlowMachine ◀── typed protocol ──▶ FailoverMachine
OtherFlowMachine ◀── typed protocol ──▶ FailoverMachine
```

State machines never reach into one another to mutate state. A machine accepts a typed input, performs a pure legal transition over the state it owns, and emits typed outcomes or requests. An enclosing Flow consumes child-machine outcomes; an independent machine such as Failover collaborates through an explicit protocol rather than becoming an artificial node in every Flow.

### Graphs control execution; state machines control truth

An execution graph answers *where execution should go*. It can express sequencing, branching, loops, suspension, and resumption. It does not by itself establish that something is true in the agent's world.

The state machine owns that decision:

- `GraphState` records recoverable execution position.
- `DomainState` records established domain facts.
- Pure transitions determine how both may evolve.
- They commit atomically as one agent-state change.
- Durable state is committed before the in-memory snapshot advances.

This distinction matters around external effects. Reaching an `execute` node does not prove that an external action did or did not happen. The durable domain state may instead say that an intent was recorded, a receipt was committed, or the outcome is unknown and requires reconciliation.

### Ports describe flow steps

A Port is not a generalized wrapper around an existing Runtime service. It is the smallest typed capability required by a stable Kernel step. The direction is:

```text
Kernel flow need → Port contract → Runtime implementation
```

Ports return typed results or commands and never mutate Kernel state directly. Their contracts must express the failure semantics the flow needs, including identity, rejection, completion, unknown outcome, retry, cancellation, receipt, or reconciliation where applicable.

Required Ports must be present when an agent flow is assembled. Missing optional capabilities remove their associated steps when the graph is assembled; they do not create repeated feature checks or hidden fallback paths during execution.

The ownership test is deliberately simple:

- behavior that belongs to the Agent flow is defined by the relevant Kernel state machine;
- behavior needed only to implement one requested step belongs to that Runtime implementation;
- behavior concerned with configuration or presentation belongs to Product.

For example, retry that is part of a Flow transition belongs to Kernel. Retry used internally by a network client to fulfill one Port request belongs to that Runtime implementation. No secondary ownership rule is needed.

## Runtime: implementations of Kernel steps

Runtime is independent of the Kernel's flow semantics. It provides concrete implementations for capabilities such as:

- model inference;
- tool and process execution;
- state, journal, checkpoint, and artifact persistence;
- external-effect execution, lookup, receipts, and reconciliation;
- filesystem, terminal, browser, sandbox, and device access;
- clocks, identity sources, event transport, and other environmental mechanisms.

Different Runtime implementations may run in memory, in a local Python process, behind a Rust sidecar, or on a remote worker. They may differ in performance, isolation, durability, and deployment, but they must not change the agent flow.

For example, Kernel decides that a successful Read result returns to Think. Runtime decides how the path is validated, how bytes are read, which limits are enforced, and how the typed Read result is constructed. Product decides whether Read is enabled and how its progress is shown.

## Product: configuration and presentation

Product turns the architecture into something a user can operate. It owns:

- product configuration and trusted defaults;
- selection and assembly of Kernel and Runtime capabilities;
- concrete agent definitions, prompts, toolsets, and policies;
- CLI, TUI, Web, API, IDE, and other interaction surfaces;
- input adaptation and event/result projection;
- user-facing rendering, localization, and product lifecycle.

Product does not implement a parallel agent flow. A TUI may render a tool invocation differently from an API, but neither decides whether the agent should think again, complete the turn, or recover an interrupted action. That remains Kernel behavior.

## Agent Swarm: model-assembled, infrastructure-supported

Mote does not place an architecture-owned Swarm workflow or global collaboration state machine above individual agents. It does not prescribe a supervisor/researcher/reviewer topology, fixed roles, a static delegation graph, or a framework-defined collaboration order.

The model assembles the Swarm dynamically from inside Agent flows:

```text
Agent Flow / Model
├── decides whether another Agent is needed
├── spawns it with a goal and context
├── delegates and communicates
├── changes the collaboration structure as evidence arrives
└── decides when to expand, reorganize, or converge
```

Spawn and communication are actions available to the Agent flow. Kernel defines how those actions participate in the Agent's state machines. Runtime implements the requested registration and communication steps. The Go control plane provides the durable coordination substrate:

- stable logical Agent identity and registration;
- root, parent, child, and incarnation lineage facts;
- addressing, mailboxes, and message transport;
- ownership, leases, placement, and delivery mechanisms; and
- recovery of those control-plane facts.

The control plane does **not** decide why an Agent is created, what cognitive role it plays, who should collaborate next, whether a response is sufficient, or when the semantic task is complete. It knows that an Agent and relationship exist and that a message must be carried; the model knows what the relationship and message mean.

```text
Kernel defines an Agent
         │
Model dynamically assembles Agents into a Swarm
         │
Go control plane records lineage and carries communication
         │
Product configures and projects the resulting Agent tree
```

An Agent tree or communication graph shown by Product is therefore a projection of registered facts, not an orchestration authority. The projection must never be turned into a second workflow that controls the agents.

## Language and ownership boundaries

The monorepo is designed to support multiple languages without duplicating authority:

- **Python owns agent flow semantics.** `mote-kernel` defines how an agent interacts and advances.
- **Go owns control-plane mechanisms.** Future Go components may implement Agent registration, lineage, communication, placement, leases, routing, and distributed ownership without defining Swarm collaboration or interpreting agent cognition.
- **Rust owns execution and state mechanisms.** Future Rust components may implement host execution, isolation, durable state, and effect mechanisms without deciding what the agent should do.
- **`conformance/` owns shared observable contracts.** No language implementation may privately reinterpret a released cross-language behavior.

Go and Rust therefore extend deployment and implementation choices; they do not become co-owners of the Agent flow.

## Conformance

`conformance/` contains language-neutral, versioned contracts shared by implementations:

```text
conformance/
├── schemas/     strict case and wire schemas
├── vectors/     pure state-transition and codec cases
├── scenarios/   multi-step recovery and effect cases
├── traces/      canonical observable execution traces
└── spec/        normative runner behavior
```

Conformance cases describe externally stable behavior, not Python classes, Go packages, Rust types, call stacks, locks, or storage layouts. Unknown versions, tags, missing fields, extra fields, and wrong primitive types fail closed. Released cases are immutable; semantic corrections require a new case or protocol version.

Each language project owns its own runner. A cross-language DTO or durable protocol change must update the schema, applicable cases, and affected runners in the same repository change.

## Repository layout

```text
motev2/
├── conformance/       language-neutral schemas, vectors, scenarios, and traces
├── mote-kernel/       Python Agent flow and state-machine semantics
├── mote-runtime/      planned Runtime Port implementations
│   ├── control-plane/
│   ├── model-gateway/
│   ├── router/
│   ├── rust/
│   └── sandbox/
└── mote-product/      planned product application and UI
    ├── app/
    └── ui/
```

At present, `mote-kernel` and the conformance bootstrap contain the substantive project structure. `mote-runtime` and `mote-product` are reserved ownership boundaries and do not yet represent delivered components.

Each child project owns its implementation, dependencies, build configuration, local tests, and release artifact. The repository root owns coordinated architecture, conformance contracts, and cross-project CI. Nested Git repositories are not permitted.

## Design rules

The following rules preserve the architecture as the system grows:

- Every concept has one canonical owner and one authoritative type.
- Agent Flow is a hierarchical state machine that composes only the state machines belonging to that flow.
- Independent state machines such as Failover collaborate with flows through typed protocols; they are not inserted into unrelated flow topology.
- Domain packages define flow topology but do not create private execution engines.
- All flows use the Kernel's single graph-execution foundation.
- Services and tools return typed results; only Kernel state transitions establish facts.
- External implementations enter through narrow typed Ports.
- The model assembles Swarm collaboration; the control plane owns registration, lineage, and communication mechanisms, not collaboration semantics.
- Product surfaces project behavior; they do not duplicate it.
- Cross-language reuse happens through schemas and behavioral vectors, never by copying implementation code.
- Generic ownerless packages such as `utils`, `common`, `shared`, and `helpers` are forbidden.
- Compatibility aliases, duplicate execution paths, hidden mutable state, and silent fallback semantics are forbidden.
- State transitions, recovery boundaries, and public behavior require deterministic tests.

The nearest `AGENTS.md` contains the binding engineering rules for each part of the repository.

## Project status

Mote v2 is in its initial architecture and implementation phase. The current Kernel package is version `0.1.0` and marked pre-alpha. Its public API is not yet stable, and the conformance manifest intentionally contains no enabled suites until the first protocol and reviewed cases are ready.

The immediate architectural objective is a narrow end-to-end agent flow that demonstrates:

1. a public orchestration entry point accepting an input;
2. Kernel advancing Observe, Think, and Act through one execution engine;
3. Runtime implementations satisfying typed Ports;
4. atomic advancement of graph position and domain facts;
5. interruption and recovery around an external effect;
6. a deterministic outcome and canonical trace; and
7. conformance cases that preserve the behavior across implementations.

## Development

Python Kernel development requires Python 3.11 or newer:

```bash
cd mote-kernel
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
make check
```

Repository hooks are installed and run from the monorepo root:

```bash
pre-commit install
pre-commit run --all-files
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md), [`mote-kernel/README.md`](mote-kernel/README.md), and [`conformance/README.md`](conformance/README.md) for project-specific details.

## License

The implemented Python Kernel is licensed under the Apache License 2.0. See [`mote-kernel/LICENSE`](mote-kernel/LICENSE). Each future child project owns and declares the license of its release artifact.
