# Architecture

Mote Kernel separates four concerns that agent frameworks commonly merge:

- **Domain flows** define why work proceeds in a particular order.
- **Execution** provides the sole graph compiler and runtime used by every flow.
- **State machines** decide which graph and domain transitions are legal.
- **Ports** supply replaceable I/O capabilities without owning kernel state.

The authoritative snapshot is an `AgentState` composed of independently versioned `GraphState` and `DomainState`. A node produces typed graph and domain commands. Pure reducers calculate a candidate snapshot, the state store commits it atomically, and only a confirmed commit may replace the Python in-memory snapshot.

`Role` is the composition root and intended default public entry point. Required ports are validated during assembly. Missing optional ports remove their corresponding nodes when graph definitions are assembled, keeping runtime paths deterministic.

This document records the stable architectural direction. Authoritative public contracts will be documented alongside their implementation.
