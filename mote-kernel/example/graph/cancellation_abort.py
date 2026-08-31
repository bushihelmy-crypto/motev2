"""调用方取消与 ``AbortedResult``：如何安全停止一个运行中的 graph。

Cancelling the task that owns ``Graph.run`` is different from returning
``Graph.failure`` from a node.  The facade fences and commits an abort before
propagating ``CancelledError``; a later state-only invocation observes the
authoritative ``AbortedResult`` and does not execute the node again.
"""

import asyncio
from dataclasses import dataclass

from mote_kernel.execution import Graph


@dataclass(slots=True)
class CheckpointStore:
    """Small caller-owned adapter that records the last confirmed state."""

    state: Graph.State | None = None

    async def __call__(self, transition: Graph.Transition[str], /) -> Graph.State:
        self.state = transition.candidate_state
        return self.state


def build_graph(started: asyncio.Event) -> Graph[str]:
    """Build a graph whose node waits until its owner is cancelled."""

    async def wait_for_external_work(_values: Graph.Values[str]) -> Graph.Values[str]:
        started.set()
        await asyncio.Event().wait()
        return Graph.values()

    graph = Graph[str]("example.cancellation-abort")
    graph.add_node("work", wait_for_external_work, inputs={}, outputs={})
    graph.set_outputs({})
    return graph


async def main() -> None:
    started = asyncio.Event()
    graph = build_graph(started)
    store = CheckpointStore()
    task = asyncio.create_task(graph.run(Graph.values(), run_id="cancelled-run", commit=store))
    await started.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        print("调用方已取消运行；取消异常会继续抛给调用方。")

    if store.state is None:
        print("没有拿到 authoritative state。")
        return
    observed = await graph.run(state=store.state)
    if isinstance(observed, Graph.AbortedResult):
        print(f"持久化状态：aborted（{observed.abort.reason}）")
    else:
        print("状态没有进入 aborted。")


if __name__ == "__main__":
    asyncio.run(main())
