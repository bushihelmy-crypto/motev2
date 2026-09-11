"""Agent assembly with injected backend-independent Ports, not a storage adapter.

The composition root supplies authority and persistence. Nothing in this
example chooses a database, transport, Container or tool reconciliation path.
The import topology, business DTO and codec are reused from checkpointed_import.
"""

from example.graph.checkpointed_import import (
    ImportJob,
    ImportStatus,
    build_graph,
    decode_import_job,
    encode_import_job,
)
from mote_kernel.agent import Agent, AgentAnswer, AgentCompleted, AgentInterrupted, AgentResume, AgentStart
from mote_kernel.execution import Graph
from mote_kernel.execution.graph.codec import FrameCodec
from mote_kernel.persistence import AuthorityPort, PersistencePort


def build_agent(persistence: PersistencePort[ImportJob], authority: AuthorityPort) -> Agent[ImportJob]:
    return Agent(
        "import-agent",
        lambda _config: build_graph(),
        FrameCodec("import-job", 1, encode_import_job, decode_import_job),
        persistence,
        authority,
    )


async def demonstrate(
    persistence: PersistencePort[ImportJob], authority: AuthorityPort, *, run_id: str, source: str
) -> AgentCompleted[ImportJob]:
    waiting = await build_agent(persistence, authority).run(
        AgentStart(run_id, Graph.values(job=ImportJob(source, False, ImportStatus.NEW)))
    )
    if not isinstance(waiting, AgentInterrupted):
        raise AssertionError("the import must wait for approval")
    approved = ImportJob(source, True, ImportStatus.PARSED)
    result = await build_agent(persistence, authority).run(
        AgentResume(run_id, (AgentAnswer(waiting.interrupts[0], Graph.values(job=approved)),))
    )
    if not isinstance(result, AgentCompleted):
        raise AssertionError("the approved import must complete")
    return result
