"""进程内提交与控制态恢复示例，不是持久化恢复示例。

``StateStore`` records transitions only in memory. Rebuilding the Graph below
retains that memory and explicitly supplies the complete approval resume
input. It does not demonstrate process restart, stored business frames, or a
durable checkpoint read. Production recovery requires the complete atomic
state/value contract and the Agent wiring described in the persistence plan.
"""

import asyncio
from dataclasses import dataclass, field, replace
from enum import Enum

from mote_kernel.execution import Graph


class ImportStatus(Enum):
    NEW = "new"
    PARSED = "parsed"
    APPROVED = "approved"
    LOADED = "loaded"


@dataclass(frozen=True, slots=True)
class ImportJob:
    source: str
    approved: bool
    status: ImportStatus


def _empty_transitions() -> list[Graph.Transition[ImportJob]]:
    return []


@dataclass(slots=True)
class StateStore:
    """A caller-owned in-memory transition recorder, not a persistence adapter."""

    state: Graph.State | None = None
    transitions: list[Graph.Transition[ImportJob]] = field(default_factory=_empty_transitions)

    async def __call__(self, transition: Graph.Transition[ImportJob], /) -> Graph.State:
        self.transitions.append(transition)
        self.state = transition.candidate_state
        return self.state


_SEPARATOR = "\x1f"


def encode_import_job(values: Graph.Values[ImportJob]) -> bytes:
    """Encode the approval resume input in a deterministic, versioned codec."""

    job = values["job"]
    fields = (job.source, "1" if job.approved else "0", job.status.value)
    if any(_SEPARATOR in field for field in fields):
        raise ValueError("import job fields cannot contain the resume separator")
    return _SEPARATOR.join(fields).encode("utf-8")


def decode_import_job(payload: bytes) -> Graph.Values[ImportJob]:
    """Decode one approval input and reject malformed state transfer data."""

    fields = payload.decode("utf-8").split(_SEPARATOR)
    if len(fields) != 3 or fields[1] not in {"0", "1"}:
        raise ValueError("import job resume payload is malformed")
    try:
        status = ImportStatus(fields[2])
    except ValueError as error:
        raise ValueError("import job resume payload has an unknown status") from error
    return Graph.values(job=ImportJob(fields[0], fields[1] == "1", status))


async def parse_source(values: Graph.Values[ImportJob]) -> Graph.Values[ImportJob]:
    """Publish the parsed job for the next acknowledged graph transition."""

    return Graph.values(job=replace(values["job"], status=ImportStatus.PARSED))


async def request_approval(values: Graph.Values[ImportJob]) -> Graph.Outcome[ImportJob]:
    """Pause until an operator supplies an explicit approval decision."""

    job = values["job"]
    if not job.approved:
        return Graph.interrupt(job.source.encode("utf-8"))
    return Graph.success(Graph.values(job=replace(job, status=ImportStatus.APPROVED)))


async def load_records(values: Graph.Values[ImportJob]) -> Graph.Values[ImportJob]:
    """Load records only after the approval checkpoint has been confirmed."""

    return Graph.values(job=replace(values["job"], status=ImportStatus.LOADED))


def build_graph() -> Graph[ImportJob]:
    """Build a fresh graph object for initial execution or control-only recovery."""

    graph = Graph[ImportJob]("example.checkpointed-import")
    graph.set_resume_codec("import-job", 1, encode_import_job, decode_import_job)
    graph.add_node(
        "parse",
        parse_source,
        inputs={"job": Graph.graph_input("job", ImportJob)},
        outputs={"job": ImportJob},
    )
    graph.add_node(
        "approve",
        request_approval,
        inputs={"job": Graph.node_output("parse", "job")},
        outputs={"job": ImportJob},
    )
    graph.add_node(
        "load",
        load_records,
        inputs={"job": Graph.node_output("approve", "job")},
        outputs={"job": ImportJob},
    )
    graph.add_edge("parse", "approve")
    graph.add_edge("approve", "load")
    graph.set_outputs({"job": Graph.node_output("load", "job")})
    return graph


async def main() -> None:
    source = (await asyncio.to_thread(input, "待导入文件名：")).strip()
    store = StateStore()
    graph = build_graph()
    pending = ImportJob(source, False, ImportStatus.NEW)
    paused = await graph.run(Graph.values(job=pending), run_id="import-job", commit=store)
    if not isinstance(paused, Graph.AwaitingResumeResult):
        print("导入流程没有等待审批。")
        return

    interrupt = paused.interrupts[0]
    print(f"请审批导入：{interrupt.request_payload.decode('utf-8')}")
    if store.state is None:
        print("提交检查点缺失，无法安全恢复。")
        return

    approved = ImportJob(source, True, ImportStatus.PARSED)
    recovered_graph = build_graph()
    completed = await recovered_graph.run(
        state=store.state,
        resume=(
            recovered_graph.resume_interrupted(
                "approve",
                interrupt.interrupt_id,
                Graph.values(job=approved),
            ),
        ),
        commit=store,
    )
    if isinstance(completed, Graph.CompletedResult):
        print(f"导入完成：{completed.outputs['job'].status.value}（提交 {len(store.transitions)} 次）")
    else:
        print("导入仍未完成，请保留 state 继续处理。")


if __name__ == "__main__":
    asyncio.run(main())
