"""A harness-owned sequential-process adapter, never a production backend."""

import asyncio
import os
import pickle
import sys
from pathlib import Path
from typing import cast

from tests.agent.persistence_fixtures import JournalPersistence, MemoryAuthority
from tests.execution.persistence_fixtures import STRING_CODEC, MemoryPersistence

from mote_kernel.agent import Agent, AgentCompleted, AgentResume, AgentStart
from mote_kernel.config import Config
from mote_kernel.execution import Graph
from mote_kernel.execution.persistence import GraphPersistenceCommit
from mote_kernel.persistence import AgentRunKey, AuthorityLostError, ExecutionAuthority


class ProcessAuthority(MemoryAuthority):
    def __init__(self, directory: Path) -> None:
        super().__init__()
        self.generation = directory / "generation"
        self.sequence = int(self.generation.read_text()) if self.generation.exists() else 0

    async def acquire(self, run: AgentRunKey, /) -> ExecutionAuthority:
        authority = await super().acquire(run)
        self.generation.write_text(str(self.sequence))
        return authority

    def validate(self, authority: ExecutionAuthority) -> None:
        super().validate(authority)
        if authority.credential != f"grant-{self.generation.read_text()}".encode():
            raise AuthorityLostError("a subsequent harness process owns the grant")


class ProcessPersistence(JournalPersistence[str]):
    def __init__(self, authority: ProcessAuthority, directory: Path, phase: str) -> None:
        super().__init__(authority)
        self.path = directory / "commits.pickle"
        self.phase = phase

    async def view(self, key: AgentRunKey) -> MemoryPersistence[str]:
        if self.path.exists():
            self.journals[key] = cast(tuple[GraphPersistenceCommit[str], ...], pickle.loads(self.path.read_bytes()))
        return await super().view(key)

    async def apply(
        self, authority: ExecutionAuthority, request: GraphPersistenceCommit[str]
    ) -> GraphPersistenceCommit[str]:
        confirmed = await super().apply(authority, request)
        pending = self.path.with_suffix(".pending")
        pending.write_bytes(pickle.dumps(self.journals[authority.run]))
        pending.replace(self.path)
        if self.phase == "capture" and request.writes.publications:
            os._exit(23)
        return confirmed


async def main(phase: str, directory: Path) -> None:
    authority = ProcessAuthority(directory)
    persistence = ProcessPersistence(authority, directory, phase)

    def assemble(_config: Config | None) -> Graph[str]:
        graph = Graph[str]("process-agent")

        async def first(values: Graph.Values[str]) -> Graph.Values[str]:
            with (directory / "calls").open("a") as stream:
                stream.write(f"first:{os.getpid()}\n")
            return Graph.values(value=values["value"] + "-first")

        async def second(values: Graph.Values[str]) -> Graph.Values[str]:
            with (directory / "calls").open("a") as stream:
                stream.write(f"second:{os.getpid()}\n")
            return Graph.values(value=values["value"] + "-second")

        graph.add_node("first", first, inputs={"value": graph.graph_input("value", str)}, outputs={"value": str})
        graph.add_node("second", second, inputs={"value": graph.node_output("first", "value")}, outputs={"value": str})
        graph.add_edge("first", "second")
        graph.set_outputs({"value": graph.output_ref("second", "value")})
        return graph

    agent = Agent("process-agent", assemble, STRING_CODEC, persistence, authority)
    result = await agent.run(
        AgentStart("run", Graph.values(value="input")) if phase == "capture" else AgentResume[str]("run")
    )
    if not isinstance(result, AgentCompleted):
        raise AssertionError("the recovered process must complete")
    (directory / "result").write_text(result.outputs["value"])


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], Path(sys.argv[2])))
