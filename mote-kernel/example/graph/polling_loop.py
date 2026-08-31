"""轮询式人工流程：显式 START、自循环和条件退出都可以组合使用。

The loop deliberately asks the outside world for a fresh answer on every
iteration.  A graph node never keeps a process-local counter: ``again`` and
``done`` are explicit resume values, so the same state can be handed to a new
worker between iterations.
"""

import asyncio
from dataclasses import dataclass
from enum import Enum

from mote_kernel.execution import Graph


class PollDecision(Enum):
    WAIT = "wait"
    AGAIN = "again"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class PollRequest:
    ticket: str
    decision: PollDecision


_SEPARATOR = "\x1f"


def encode_poll_request(values: Graph.Values[PollRequest]) -> bytes:
    """Encode one externally supplied answer for durable resume."""

    request = values["request"]
    if _SEPARATOR in request.ticket:
        raise ValueError("ticket cannot contain the resume separator")
    return _SEPARATOR.join((request.ticket, request.decision.value)).encode("utf-8")


def decode_poll_request(payload: bytes) -> Graph.Values[PollRequest]:
    """Decode a resume answer and reject malformed payloads."""

    fields = payload.decode("utf-8").split(_SEPARATOR)
    if len(fields) != 2:
        raise ValueError("poll request payload must contain two fields")
    try:
        decision = PollDecision(fields[1])
    except ValueError as error:
        raise ValueError("poll request payload has an unknown decision") from error
    return Graph.values(request=PollRequest(fields[0], decision))


async def poll_ticket(values: Graph.Values[PollRequest]) -> Graph.Outcome[PollRequest]:
    """Pause, loop, or finish according to the explicit resume decision."""

    request = values["request"]
    if request.decision is PollDecision.WAIT:
        return Graph.interrupt(f"请处理工单：{request.ticket}".encode())
    if request.decision is PollDecision.AGAIN:
        return Graph.success(Graph.values(request=request), route="again")
    return Graph.success(Graph.values(request=request), route="done")


def build_graph() -> Graph[PollRequest]:
    """Build a graph with an explicit entry and a conditional back-edge."""

    graph = Graph[PollRequest]("example.polling-loop")
    graph.set_resume_codec("poll-request", 1, encode_poll_request, decode_poll_request)
    graph.add_node(
        "poll",
        poll_ticket,
        inputs={"request": Graph.graph_input("request", PollRequest)},
        outputs={"request": PollRequest},
    )
    graph.add_edge(Graph.START, "poll")
    graph.add_conditional_edge("poll", "again", "poll")
    graph.add_conditional_edge("poll", "done", Graph.END)
    graph.set_outputs({"request": Graph.node_output("poll", "request")})
    return graph


async def main() -> None:
    ticket = (await asyncio.to_thread(input, "工单号：")).strip()
    graph = build_graph()
    result: Graph.Result[PollRequest] = await graph.run(
        Graph.values(request=PollRequest(ticket, PollDecision.WAIT)),
        run_id="polling-ticket",
        max_supersteps=8,
    )
    while isinstance(result, Graph.AwaitingResumeResult):
        interrupt = result.interrupts[0]
        answer = (await asyncio.to_thread(input, "输入 again 继续轮询，或 done 结束：")).strip().lower()
        decision = PollDecision.DONE if answer == "done" else PollDecision.AGAIN
        result = await graph.run(
            state=result.state,
            continuation=result.continuation,
            resume=(
                graph.resume_interrupted(
                    "poll",
                    interrupt.interrupt_id,
                    Graph.values(request=PollRequest(ticket, decision)),
                ),
            ),
            max_supersteps=8,
        )
    if isinstance(result, Graph.CompletedResult):
        print(f"工单完成：{result.outputs['request'].ticket}")
    else:
        print("工单未完成。")


if __name__ == "__main__":
    asyncio.run(main())
