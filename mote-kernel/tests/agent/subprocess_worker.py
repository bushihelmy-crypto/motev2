"""Harness-owned process faults over one backend-neutral Agent path."""

import asyncio
import os
import pickle
import signal
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import cast

from tests.agent.persistence_fixtures import JournalPersistence, MemoryAuthority
from tests.execution.persistence_fixtures import STRING_CODEC, MemoryPersistence

from mote_kernel.agent import (
    Agent,
    AgentAnswer,
    AgentCompleted,
    AgentConfig,
    AgentInterrupted,
    AgentRequest,
    AgentResume,
    AgentStart,
)
from mote_kernel.config import (
    Config,
    ConfigContractError,
    ConfigSnapshot,
    ConfigSnapshotKey,
    resolve_config,
    save_config_snapshot,
)
from mote_kernel.execution import Graph
from mote_kernel.execution.graph.values import _make_single_graph_value
from mote_kernel.execution.graph_result import GraphInterruptView
from mote_kernel.execution.identity import ScopeRunCoordinate
from mote_kernel.execution.persistence import GraphCheckpoint, GraphPersistenceCommit
from mote_kernel.invocation import InvocationTypeContract, invoke_typed
from mote_kernel.loop.config import ReActRuntimeConfig
from mote_kernel.persistence import (
    AgentRunKey,
    AuthorityLostError,
    CommitOutcome,
    ExecutionAuthority,
    NeverCreated,
    PersistenceContractError,
)
from mote_kernel.state.graph_state import GraphDefinitionId, GraphDefinitionVersion, GraphNodeId

PROCESS_CRASH_EXIT = 23
RUNTIME_CRASH_EXIT = 24
CONFIG_DEFINITION = GraphDefinitionId("process-config")
CONFIG_VERSION = GraphDefinitionVersion(1)


class Scenario(StrEnum):
    LINEAR = "linear"
    FRONTIER = "frontier"
    LOOP_JOIN = "loop-join"
    NESTED_CONFIG = "nested-config"
    CHILD_START = "child-start"
    INTERRUPT_FAMILY = "interrupt-family"
    STALE_AUTHORITY = "stale-authority"
    RUNTIME_BOUNDARY = "runtime-boundary"


class Phase(StrEnum):
    CAPTURE = "capture"
    RECOVER = "recover"
    REPLAY = "replay"
    PARTIAL = "partial"
    STALE = "stale"


class CrashBoundary(StrEnum):
    NONE = "none"
    BEFORE_WRITE = "before-write"
    AFTER_WRITE = "after-write"


@dataclass(frozen=True, slots=True)
class RuntimeRequest:
    value: str


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    value: str


RUNTIME_CONTRACT = InvocationTypeContract(RuntimeRequest, RuntimeResult)


def _append_record(directory: Path, stream: str, *parts: object) -> None:
    with (directory / stream).open("a", encoding="utf-8") as output:
        output.write("|".join(str(part) for part in (*parts, os.getpid())) + "\n")


@dataclass(frozen=True, slots=True)
class ProcessRuntime:
    directory: Path
    phase: Phase

    async def invoke(self, request: RuntimeRequest, /) -> RuntimeResult:
        _append_record(self.directory, "runtime.log", self.phase, "invoke", request.value)
        if self.phase is Phase.CAPTURE:
            os._exit(RUNTIME_CRASH_EXIT)
        return RuntimeResult(request.value + "-runtime")


def _config_snapshot(revision: int) -> ConfigSnapshot:
    key = ConfigSnapshotKey(CONFIG_DEFINITION, CONFIG_VERSION, revision)
    return ConfigSnapshot.capture(key, f'{{"revision":{revision}}}'.encode())


class ProcessAuthority(MemoryAuthority):
    def __init__(self, directory: Path, phase: Phase) -> None:
        super().__init__()
        self.directory = directory
        self.phase = phase
        self.generation = directory / "generation"
        self.sequence = int(self.generation.read_text()) if self.generation.exists() else 0

    async def acquire(self, run: AgentRunKey, /) -> ExecutionAuthority:
        authority = await super().acquire(run)
        self.generation.write_text(str(self.sequence))
        _append_record(self.directory, "authority.log", "acquire", self.phase, self.sequence)
        return authority

    async def release(self, authority: ExecutionAuthority, /) -> None:
        await super().release(authority)
        _append_record(self.directory, "authority.log", "release", self.phase, self.sequence)

    def validate(self, authority: ExecutionAuthority) -> None:
        super().validate(authority)
        if authority.credential != f"grant-{self.generation.read_text()}".encode():
            raise AuthorityLostError("a subsequent harness process owns the grant")


class ProcessPersistence(JournalPersistence[str]):
    def __init__(
        self,
        authority: ProcessAuthority,
        directory: Path,
        scenario: Scenario,
        phase: Phase,
        crash_boundary: CrashBoundary,
    ) -> None:
        super().__init__(authority)
        self.directory = directory
        self.path = directory / "commits.pickle"
        self.scenario = scenario
        self.phase = phase
        self.crash_boundary = crash_boundary
        self.target_matches = 0

    async def view(self, key: AgentRunKey) -> MemoryPersistence[str]:
        if self.path.exists():
            try:
                decoded: object = pickle.loads(self.path.read_bytes())
            except (
                AttributeError,
                EOFError,
                ImportError,
                IndexError,
                pickle.UnpicklingError,
                TypeError,
                ValueError,
            ) as error:
                raise PersistenceContractError("the process test journal is malformed") from error
            if type(decoded) is not tuple:
                raise PersistenceContractError("the process test journal must contain exact Graph commits")
            decoded_records = cast(tuple[object, ...], decoded)
            if any(type(item) is not GraphPersistenceCommit for item in decoded_records):
                raise PersistenceContractError("the process test journal must contain exact Graph commits")
            requests = cast(tuple[GraphPersistenceCommit[str], ...], decoded_records)
            try:
                self.journals[key] = tuple(request.admit() for request in requests)
            except (AttributeError, Graph.SnapshotMismatchError, TypeError, ValueError) as error:
                raise PersistenceContractError("the process test journal contains a malformed Graph commit") from error
        return await super().view(key)

    async def load(
        self,
        authority: ExecutionAuthority,
        /,
        *,
        children: tuple[ScopeRunCoordinate, ...] = (),
    ) -> GraphCheckpoint[str] | NeverCreated:
        _append_record(self.directory, "persistence.log", "load", self.phase, len(children))
        return await super().load(authority, children=children)

    async def commit(
        self,
        authority: ExecutionAuthority,
        request: GraphPersistenceCommit[str],
        /,
    ) -> CommitOutcome[str]:
        _append_record(
            self.directory,
            "persistence.log",
            "commit",
            self.phase,
            "/".join(request.scope) or "root",
            request.candidate_state.revision,
        )
        return await super().commit(authority, request)

    async def reconcile(
        self,
        authority: ExecutionAuthority,
        request: GraphPersistenceCommit[str],
        /,
    ) -> CommitOutcome[str]:
        _append_record(
            self.directory,
            "persistence.log",
            "reconcile",
            self.phase,
            "/".join(request.scope) or "root",
            request.candidate_state.revision,
        )
        return await super().reconcile(authority, request)

    def _targets_crash(self, request: GraphPersistenceCommit[str]) -> bool:
        publications = request.writes.publications
        if self.scenario is Scenario.LINEAR:
            return any(item.coordinate.activation.node_id == "first" for item in publications)
        if self.scenario is Scenario.FRONTIER:
            return any(item.coordinate.activation.node_id == "left" for item in publications)
        if self.scenario is Scenario.LOOP_JOIN:
            if not any(item.coordinate.activation.node_id == "loop" for item in publications):
                return False
            self.target_matches += 1
            return self.target_matches == 2
        if self.scenario is Scenario.NESTED_CONFIG:
            return request.scope == (GraphNodeId("middle"),) and any(
                item.coordinate.activation.node_id == "leaf" for item in publications
            )
        if self.scenario is Scenario.CHILD_START:
            return request.scope == (GraphNodeId("child"),) and request.expected_revision is None
        return False

    async def apply(
        self,
        authority: ExecutionAuthority,
        request: GraphPersistenceCommit[str],
    ) -> GraphPersistenceCommit[str]:
        crash = (
            self.phase is Phase.CAPTURE
            and self.crash_boundary is not CrashBoundary.NONE
            and self._targets_crash(request)
        )
        if crash and self.crash_boundary is CrashBoundary.BEFORE_WRITE:
            os._exit(PROCESS_CRASH_EXIT)
        confirmed = await super().apply(authority, request)
        pending = self.path.with_name(f"{self.path.name}.{os.getpid()}.pending")
        pending.write_bytes(pickle.dumps(self.journals[authority.run]))
        pending.replace(self.path)
        if crash:
            os._exit(PROCESS_CRASH_EXIT)
        return confirmed


class ProcessConfigCatalog:
    def __init__(self, directory: Path, phase: Phase) -> None:
        self.directory = directory / "configs"
        self.phase = phase
        if not self.directory.exists():
            self.directory.mkdir()
            self._write(_config_snapshot(1))
            self._write(_config_snapshot(9))

    def _path(self, key: ConfigSnapshotKey) -> Path:
        return self.directory / f"revision-{key.revision}.pickle"

    def _write(self, snapshot: ConfigSnapshot) -> None:
        path = self._path(snapshot.key)
        pending = path.with_name(f"{path.name}.{os.getpid()}.pending")
        pending.write_bytes(pickle.dumps(snapshot))
        pending.replace(path)

    async def load(self, key: ConfigSnapshotKey, /) -> ConfigSnapshot:
        _append_record(self.directory.parent, "config.log", "load", self.phase, key.revision)
        path = self._path(key)
        if not path.exists():
            raise ConfigContractError("the exact process Config snapshot is unavailable")
        try:
            snapshot: object = pickle.loads(path.read_bytes())
        except (
            AttributeError,
            EOFError,
            ImportError,
            IndexError,
            pickle.UnpicklingError,
            TypeError,
            ValueError,
        ) as error:
            raise ConfigContractError("the process Config snapshot is malformed") from error
        if type(snapshot) is not ConfigSnapshot or snapshot.key != key:
            raise ConfigContractError("the process Config store returned a different snapshot")
        return snapshot

    async def save(self, snapshot: ConfigSnapshot, /) -> ConfigSnapshot:
        _append_record(self.directory.parent, "config.log", "save", self.phase, snapshot.key.revision)
        path = self._path(snapshot.key)
        if path.exists():
            existing = await self.load(snapshot.key)
            if existing != snapshot:
                raise ConfigContractError("the process Config key already names different content")
            return existing
        self._write(snapshot)
        return snapshot

    async def resolve(self, snapshot: ConfigSnapshot, /) -> Config:
        _append_record(self.directory.parent, "config.log", "resolve", self.phase, snapshot.key.revision)
        projection = ReActRuntimeConfig(snapshot.key, snapshot.key.definition_id, snapshot.key.definition_version)
        return Config(snapshot, projection, projection, projection, projection, ())


def _linear_graph(directory: Path, phase: Phase) -> Graph[str]:
    graph = Graph[str]("process.linear")

    async def first(values: Graph.Values[str]) -> Graph.Values[str]:
        _append_record(directory, "calls.log", phase, "first", values["value"])
        return Graph.values(value=values["value"] + "-first")

    async def second(values: Graph.Values[str]) -> Graph.Values[str]:
        _append_record(directory, "calls.log", phase, "second", values["value"])
        return Graph.values(value=values["value"] + "-second")

    graph.add_node("first", first, inputs={"value": graph.graph_input("value", str)}, outputs={"value": str})
    graph.add_node("second", second, inputs={"value": graph.node_output("first", "value")}, outputs={"value": str})
    graph.add_edge(Graph.START, "first")
    graph.add_edge("first", "second")
    graph.add_edge("second", Graph.END)
    graph.set_outputs({"value": graph.output_ref("second", "value")})
    return graph


def _frontier_graph(directory: Path, phase: Phase) -> Graph[str]:
    graph = Graph[str]("process.frontier")
    blocked = asyncio.Event()

    async def left(values: Graph.Values[str]) -> Graph.Values[str]:
        _append_record(directory, "calls.log", phase, "left", values["value"])
        return Graph.values(value=values["value"] + "-left")

    async def right(values: Graph.Values[str]) -> Graph.Values[str]:
        if phase is Phase.CAPTURE:
            await blocked.wait()
        _append_record(directory, "calls.log", phase, "right", values["value"])
        return Graph.values(value=values["value"] + "-right")

    async def join(values: Graph.Values[str]) -> Graph.Values[str]:
        _append_record(directory, "calls.log", phase, "join", values["left"], values["right"])
        return Graph.values(value=values["left"] + "|" + values["right"])

    graph.add_node("left", left, inputs={"value": graph.graph_input("value", str)}, outputs={"value": str})
    graph.add_node("right", right, inputs={"value": graph.graph_input("value", str)}, outputs={"value": str})
    graph.add_node(
        "join",
        join,
        inputs={"left": graph.node_output("left", "value"), "right": graph.node_output("right", "value")},
        outputs={"value": str},
    )
    graph.add_edge(Graph.START, "left")
    graph.add_edge(Graph.START, "right")
    graph.add_join(("left", "right"), "join")
    graph.add_edge("join", Graph.END)
    graph.set_outputs({"value": graph.output_ref("join", "value")})
    return graph


def _loop_join_graph(directory: Path, phase: Phase) -> Graph[str]:
    graph = Graph[str]("process.loop-join")

    async def initialize(values: Graph.Values[str]) -> Graph.Values[str]:
        _append_record(directory, "calls.log", phase, "initialize", values["seed"])
        return Graph.values(value=values["seed"])

    async def loop(values: Graph.Values[str]) -> Graph.Outcome[str]:
        current = int(values["value"])
        _append_record(directory, "calls.log", phase, "loop", current)
        return Graph.success(Graph.values(value=str(current + 1)), route="again" if current == 0 else "done")

    async def fanout(values: Graph.Values[str]) -> Graph.Values[str]:
        _append_record(directory, "calls.log", phase, "fanout", values["value"])
        return values

    async def left(values: Graph.Values[str]) -> Graph.Values[str]:
        _append_record(directory, "calls.log", phase, "left", values["value"])
        return Graph.values(value="L" + values["value"])

    async def right(values: Graph.Values[str]) -> Graph.Values[str]:
        _append_record(directory, "calls.log", phase, "right", values["value"])
        return Graph.values(value="R" + values["value"])

    async def join(values: Graph.Values[str]) -> Graph.Values[str]:
        _append_record(directory, "calls.log", phase, "join", values["left"], values["right"])
        return Graph.values(value=values["left"] + "|" + values["right"])

    graph.add_node(
        "initialize",
        initialize,
        inputs={"seed": graph.graph_input("value", str)},
        outputs={"value": str},
    )
    graph.add_node("loop", loop, inputs={"value": graph.node_output("value")}, outputs={"value": str})
    graph.add_node("fanout", fanout, inputs={"value": graph.node_output("loop", "value")}, outputs={"value": str})
    graph.add_node("left", left, inputs={"value": graph.node_output("fanout", "value")}, outputs={"value": str})
    graph.add_node("right", right, inputs={"value": graph.node_output("fanout", "value")}, outputs={"value": str})
    graph.add_node(
        "join",
        join,
        inputs={"left": graph.node_output("left", "value"), "right": graph.node_output("right", "value")},
        outputs={"value": str},
    )
    graph.add_edge(Graph.START, "initialize")
    graph.add_edge("initialize", "loop")
    graph.add_edge("loop", "again", "loop")
    graph.add_edge("loop", "done", "fanout")
    graph.add_edge("fanout", "left")
    graph.add_edge("fanout", "right")
    graph.add_join(("left", "right"), "join")
    graph.add_edge("join", Graph.END)
    graph.set_outputs({"value": graph.output_ref("join", "value")})
    return graph


def _nested_config_graph(
    directory: Path,
    phase: Phase,
    catalog: ProcessConfigCatalog,
) -> Graph[str]:
    successor = _config_snapshot(2)
    leaf = Graph[str]("process.nested-config.leaf")

    async def observe(values: Graph.Values[str]) -> Graph.Values[str]:
        revision = values.activation_config.snapshot.key.revision if values.activation_config is not None else "none"
        _append_record(directory, "calls.log", phase, "observe", revision)
        saved = await save_config_snapshot(catalog, successor)
        changed = await resolve_config(catalog, saved)
        return _make_single_graph_value("value", values["value"] + "-observed", changed)

    async def consume(values: Graph.Values[str]) -> Graph.Values[str]:
        revision = values.activation_config.snapshot.key.revision if values.activation_config is not None else "none"
        _append_record(directory, "calls.log", phase, "consume", revision)
        return values

    leaf.add_node("observe", observe, inputs={"value": leaf.graph_input("value", str)}, outputs={"value": str})
    leaf.add_node("consume", consume, inputs={"value": leaf.node_output("observe", "value")}, outputs={"value": str})
    leaf.add_edge(Graph.START, "observe")
    leaf.add_edge("observe", "consume")
    leaf.add_edge("consume", Graph.END)
    leaf.set_outputs({"value": leaf.output_ref("consume", "value")})

    middle = Graph[str]("process.nested-config.middle")

    async def middle_tail(values: Graph.Values[str]) -> Graph.Values[str]:
        revision = values.activation_config.snapshot.key.revision if values.activation_config is not None else "none"
        _append_record(directory, "calls.log", phase, "middle-tail", revision)
        return Graph.values(value=values["value"] + "-middle")

    middle.add_node("leaf", leaf, inputs={"value": middle.graph_input("value", str)})
    middle.add_node(
        "middle-tail",
        middle_tail,
        inputs={"value": middle.node_output("leaf", "value")},
        outputs={"value": str},
    )
    middle.add_edge(Graph.START, "leaf")
    middle.add_edge("leaf", "middle-tail")
    middle.add_edge("middle-tail", Graph.END)
    middle.set_outputs({"value": middle.output_ref("middle-tail", "value")})

    root = Graph[str]("process.nested-config.root")

    async def root_tail(values: Graph.Values[str]) -> Graph.Values[str]:
        revision = values.activation_config.snapshot.key.revision if values.activation_config is not None else "none"
        _append_record(directory, "calls.log", phase, "root-tail", revision)
        return Graph.values(value=values["value"] + "-root")

    root.add_node("middle", middle, inputs={"value": root.graph_input("value", str)})
    root.add_node(
        "root-tail",
        root_tail,
        inputs={"value": root.node_output("middle", "value")},
        outputs={"value": str},
    )
    root.add_edge(Graph.START, "middle")
    root.add_edge("middle", "root-tail")
    root.add_edge("root-tail", Graph.END)
    root.set_outputs({"value": root.output_ref("root-tail", "value")})
    return root


def _child_start_graph(directory: Path, phase: Phase) -> Graph[str]:
    parent = Graph[str]("process.child-start")
    parent.add_node("child", _linear_graph(directory, phase), inputs={"value": parent.graph_input("value", str)})
    parent.add_edge(Graph.START, "child")
    parent.add_edge("child", Graph.END)
    parent.set_outputs({"value": parent.output_ref("child", "value")})
    return parent


def _interrupt_family_graph(directory: Path, phase: Phase) -> Graph[str]:
    child = Graph[str]("process.interrupt-family.child")

    async def ask(values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
        _append_record(directory, "calls.log", phase, "ask", values["value"])
        return Graph.interrupt(b"question") if values["value"] == "question" else values

    child.set_resume_codec(STRING_CODEC.codec_id, STRING_CODEC.version, STRING_CODEC.encoder, STRING_CODEC.decoder)
    child.add_node("ask", ask, inputs={"value": child.graph_input("value", str)}, outputs={"value": str})
    child.add_edge(Graph.START, "ask")
    child.add_edge("ask", Graph.END)
    child.set_outputs({"value": child.output_ref("ask", "value")})

    parent = Graph[str]("process.interrupt-family.root")
    for node_id in ("left", "right"):
        parent.add_node(node_id, child, inputs={"value": parent.graph_input("value", str)})
        parent.add_edge(Graph.START, node_id)
    parent.add_join(("left", "right"), Graph.END)
    parent.set_outputs(
        {
            "left": parent.output_ref("left", "value"),
            "right": parent.output_ref("right", "value"),
        }
    )
    return parent


def _authority_graph(directory: Path, phase: Phase) -> Graph[str]:
    graph = Graph[str]("process.stale-authority")

    async def owned(values: Graph.Values[str]) -> Graph.Values[str]:
        _append_record(directory, "calls.log", phase, "owned", values["value"])
        if phase is Phase.STALE:
            released = asyncio.Event()
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGUSR1, released.set)
            (directory / "stale-ready").write_text("ready")
            try:
                await released.wait()
            finally:
                loop.remove_signal_handler(signal.SIGUSR1)
        return Graph.values(value=values["value"] + "-owned")

    graph.add_node("owned", owned, inputs={"value": graph.graph_input("value", str)}, outputs={"value": str})
    graph.add_edge(Graph.START, "owned")
    graph.add_edge("owned", Graph.END)
    graph.set_outputs({"value": graph.output_ref("owned", "value")})
    return graph


def _runtime_boundary_graph(directory: Path, phase: Phase) -> Graph[str]:
    graph = Graph[str]("process.runtime-boundary")

    async def invoke(values: Graph.Values[str]) -> Graph.Values[str]:
        result = await invoke_typed(ProcessRuntime(directory, phase), RuntimeRequest(values["value"]), RUNTIME_CONTRACT)
        return Graph.values(value=result.value)

    graph.add_node("invoke", invoke, inputs={"value": graph.graph_input("value", str)}, outputs={"value": str})
    graph.add_edge(Graph.START, "invoke")
    graph.add_edge("invoke", Graph.END)
    graph.set_outputs({"value": graph.output_ref("invoke", "value")})
    return graph


def _assemble_graph(
    scenario: Scenario,
    phase: Phase,
    directory: Path,
    catalog: ProcessConfigCatalog | None,
) -> Graph[str]:
    if scenario is Scenario.LINEAR:
        return _linear_graph(directory, phase)
    if scenario is Scenario.FRONTIER:
        return _frontier_graph(directory, phase)
    if scenario is Scenario.LOOP_JOIN:
        return _loop_join_graph(directory, phase)
    if scenario is Scenario.NESTED_CONFIG:
        if catalog is None:
            raise AssertionError("nested Config scenario requires its exact catalog")
        return _nested_config_graph(directory, phase, catalog)
    if scenario is Scenario.CHILD_START:
        return _child_start_graph(directory, phase)
    if scenario is Scenario.INTERRUPT_FAMILY:
        return _interrupt_family_graph(directory, phase)
    if scenario is Scenario.STALE_AUTHORITY:
        return _authority_graph(directory, phase)
    return _runtime_boundary_graph(directory, phase)


def _load_interrupts(directory: Path) -> tuple[GraphInterruptView, ...]:
    decoded: object = pickle.loads((directory / "interrupts.pickle").read_bytes())
    if type(decoded) is not tuple:
        raise AssertionError("the process caller must persist exact interrupt views")
    decoded_interrupts = cast(tuple[object, ...], decoded)
    if any(type(interrupt) is not GraphInterruptView for interrupt in decoded_interrupts):
        raise AssertionError("the process caller must persist exact interrupt views")
    interrupts = cast(tuple[GraphInterruptView, ...], decoded_interrupts)
    return tuple(GraphInterruptView.admit(interrupt) for interrupt in interrupts)


async def main(
    scenario: Scenario,
    phase: Phase,
    crash_boundary: CrashBoundary,
    directory: Path,
) -> None:
    authority = ProcessAuthority(directory, phase)
    persistence = ProcessPersistence(authority, directory, scenario, phase, crash_boundary)
    catalog = ProcessConfigCatalog(directory, phase) if scenario is Scenario.NESTED_CONFIG else None

    def assemble(_config: Config | None) -> Graph[str]:
        return _assemble_graph(scenario, phase, directory, catalog)

    agent = Agent(
        f"process-{scenario}",
        assemble,
        STRING_CODEC,
        persistence,
        authority,
        config=AgentConfig(catalog, catalog, _config_snapshot(1).key) if catalog is not None else None,
    )
    if scenario is Scenario.INTERRUPT_FAMILY and phase in (Phase.PARTIAL, Phase.RECOVER):
        interrupts = _load_interrupts(directory)
        selected = (
            next(interrupt for interrupt in interrupts if interrupt.scope == (GraphNodeId("left"),))
            if phase is Phase.PARTIAL
            else next(interrupt for interrupt in interrupts if interrupt.scope == (GraphNodeId("right"),))
        )
        request: AgentRequest[str] = AgentResume(
            "run",
            (AgentAnswer(selected, Graph.values(value=selected.scope[0])),),
        )
    elif phase in (Phase.CAPTURE, Phase.STALE):
        if scenario is Scenario.LOOP_JOIN:
            value = "0"
        elif scenario is Scenario.INTERRUPT_FAMILY:
            value = "question"
        else:
            value = "input"
        request = AgentStart("run", Graph.values(value=value))
    else:
        request = AgentResume[str]("run")
    try:
        result = await agent.run(request)
    except AuthorityLostError:
        if phase is not Phase.STALE:
            raise
        (directory / "stale-rejected").write_text("rejected")
        return
    if scenario is Scenario.INTERRUPT_FAMILY and phase in (Phase.CAPTURE, Phase.PARTIAL):
        if not isinstance(result, AgentInterrupted):
            raise AssertionError("the process interrupt phase must remain suspended")
        (directory / "interrupts.pickle").write_bytes(pickle.dumps(result.interrupts))
        return
    if not isinstance(result, AgentCompleted):
        raise AssertionError("the process Agent must complete")
    output = (
        result.outputs["left"] + "|" + result.outputs["right"]
        if scenario is Scenario.INTERRUPT_FAMILY
        else result.outputs["value"]
    )
    (directory / "result").write_text(output)


if __name__ == "__main__":
    asyncio.run(
        main(
            Scenario(sys.argv[1]),
            Phase(sys.argv[2]),
            CrashBoundary(sys.argv[3]),
            Path(sys.argv[4]),
        )
    )
