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
from mote_kernel.session import AgentSession, AgentSessionCodec


def _encode_session(hook_state: str, context: str) -> bytes:
    return f"{hook_state}\x1f{context}".encode()


def _decode_session(payload: bytes) -> tuple[str, str]:
    fields = payload.decode("utf-8").split("\x1f")
    if len(fields) != 2:
        raise ValueError("agent session payload is malformed")
    return fields[0], fields[1]


SESSION_CODEC = AgentSessionCodec("example.agent-session", 1, _encode_session, _decode_session)


def build_agent(persistence: PersistencePort[ImportJob], authority: AuthorityPort) -> Agent[ImportJob, str, str]:
    return Agent(
        "import-agent",
        lambda _config: build_graph(),
        FrameCodec("import-job", 1, encode_import_job, decode_import_job),
        persistence,
        authority,
        session_codec=SESSION_CODEC,
    )


async def demonstrate(
    persistence: PersistencePort[ImportJob],
    authority: AuthorityPort,
    *,
    run_id: str,
    source: str,
    session: AgentSession[str, str] | None = None,
) -> AgentCompleted[ImportJob, str, str]:
    waiting = await build_agent(persistence, authority).run(
        AgentStart(run_id, Graph.values(job=ImportJob(source, False, ImportStatus.NEW)), session)
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


async def demonstrate_next_run(
    persistence: PersistencePort[ImportJob],
    authority: AuthorityPort,
    previous: AgentCompleted[ImportJob, str, str],
    *,
    run_id: str,
    source: str,
) -> AgentCompleted[ImportJob, str, str]:
    """Start another run only by explicitly handing over the prior Session."""

    result = await build_agent(persistence, authority).run(
        AgentStart(
            run_id,
            Graph.values(job=ImportJob(source, False, ImportStatus.NEW)),
            previous.session,
        )
    )
    if not isinstance(result, AgentCompleted):
        raise AssertionError("the next import must complete")
    return result
