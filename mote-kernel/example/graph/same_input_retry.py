"""同一输入重试：外部服务恢复后使用 ``resume_failed``。

The graph input is intentionally unchanged between attempts.  ``ProviderHealth``
stands in for a caller-owned external capability; changing it models the
provider recovering without putting a retry counter in graph-local memory.
"""

import asyncio
from dataclasses import dataclass

from mote_kernel.execution import Graph


@dataclass(slots=True)
class ProviderHealth:
    """Caller-owned external status; it is not part of the graph state."""

    available: bool


@dataclass(frozen=True, slots=True)
class ProviderAuthorizer:
    """A callable node bound to caller-owned external provider status."""

    health: ProviderHealth

    async def __call__(self, values: Graph.Values[str], /) -> Graph.Outcome[str]:
        token = values["token"]
        if not self.health.available:
            return Graph.failure("provider is temporarily unavailable")
        return Graph.success(Graph.values(receipt=f"authorized:{token}"))


def authorize_with(health: ProviderHealth) -> ProviderAuthorizer:
    """Return a node operation bound to an explicit external capability."""

    return ProviderAuthorizer(health)


async def ship(values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values(result=f"shipped:{values['receipt']}")


def build_graph(health: ProviderHealth) -> Graph[str]:
    """Build a graph against the provider capability used by this worker."""

    graph = Graph[str]("example.same-input-retry")
    graph.add_node(
        "authorize",
        authorize_with(health),
        inputs={"token": Graph.graph_input("token", str)},
        outputs={"receipt": str},
    )
    graph.add_node(
        "ship",
        ship,
        inputs={"receipt": Graph.node_output("authorize", "receipt")},
        outputs={"result": str},
    )
    graph.add_edge("authorize", "ship")
    graph.add_edge("ship", Graph.END)
    graph.set_outputs({"result": Graph.node_output("ship", "result")})
    return graph


async def main() -> None:
    token = (await asyncio.to_thread(input, "支付 token：")).strip()
    health = ProviderHealth(False)
    graph = build_graph(health)
    first = await graph.run(Graph.values(token=token), run_id="same-input-retry")
    if not isinstance(first, Graph.AwaitingResumeResult):
        print("第一次调用意外成功。")
        return

    # The provider recovers outside the graph; retry the exact materialized input.
    health.available = True
    completed = await graph.run(
        state=first.state,
        continuation=first.continuation,
        resume=(graph.resume_failed("authorize"),),
    )
    if isinstance(completed, Graph.CompletedResult):
        print(completed.outputs["result"])
    else:
        print("重试仍未完成。")


if __name__ == "__main__":
    asyncio.run(main())
