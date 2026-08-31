"""配送兜底图：外部承运商失败时，用人工配送结果替代失败节点输出。

``skip_failed`` is useful when an operator has an authoritative result from
outside the graph.  Supplying ``output`` materializes that result for the
downstream node, so the failed operation is not executed a second time.
"""

import asyncio
from dataclasses import dataclass, replace
from enum import Enum

from mote_kernel.execution import Graph


class DeliveryStatus(Enum):
    READY = "ready"
    MANUAL = "manual"
    SENT = "sent"


@dataclass(frozen=True, slots=True)
class Delivery:
    order_id: str
    address: str
    status: DeliveryStatus
    tracking_code: str


async def reserve_courier(values: Graph.Values[Delivery]) -> Graph.Outcome[Delivery]:
    """Simulate a provider outage for addresses in the remote region."""

    delivery = values["delivery"]
    if delivery.address.startswith("偏远"):
        return Graph.failure("courier provider is unavailable for this address")
    reserved = replace(delivery, status=DeliveryStatus.READY, tracking_code="courier-pending")
    return Graph.success(Graph.values(delivery=reserved))


async def dispatch_delivery(values: Graph.Values[Delivery]) -> Graph.Values[Delivery]:
    """Dispatch either a courier reservation or an operator's replacement."""

    delivery = values["delivery"]
    dispatched = replace(delivery, status=DeliveryStatus.SENT)
    return Graph.values(delivery=dispatched)


def build_graph() -> Graph[Delivery]:
    """Build the delivery graph; its nodes have no process-local state."""

    graph = Graph[Delivery]("example.skip-failed-delivery")
    graph.add_node(
        "reserve",
        reserve_courier,
        inputs={"delivery": Graph.graph_input("delivery", Delivery)},
        outputs={"delivery": Delivery},
    )
    graph.add_node(
        "dispatch",
        dispatch_delivery,
        inputs={"delivery": Graph.node_output("reserve", "delivery")},
        outputs={"delivery": Delivery},
    )
    graph.add_edge("reserve", "dispatch")
    graph.add_edge("dispatch", Graph.END)
    graph.set_outputs({"delivery": Graph.node_output("dispatch", "delivery")})
    return graph


async def main() -> None:
    order_id = (await asyncio.to_thread(input, "订单号：")).strip()
    address = (await asyncio.to_thread(input, "收货地址（以“偏远”开头模拟承运商失败）：")).strip()
    original = Delivery(order_id, address, DeliveryStatus.READY, "")

    graph = build_graph()
    first = await graph.run(Graph.values(delivery=original), run_id="delivery-order")
    if isinstance(first, Graph.CompletedResult):
        print(f"已发出：{first.outputs['delivery'].tracking_code}")
        return
    if not isinstance(first, Graph.AwaitingResumeResult):
        print("配送流程未能进入可恢复状态。")
        return

    print(f"节点 {first.failures[0].node_id} 失败：{first.failures[0].failure}")
    fallback = replace(original, status=DeliveryStatus.MANUAL, tracking_code="operator-dispatch")

    # 人工系统已给出确定结果; 将它作为失败节点的 output 注入后继续执行.
    # 这里保留同一个 graph family, 因而显式展示 transient continuation 的入口.
    recovered = await graph.run(
        state=first.state,
        continuation=first.continuation,
        resume=(
            graph.skip_failed(
                "reserve",
                "manual courier selected",
                output=Graph.values(delivery=fallback),
            ),
        ),
    )
    if isinstance(recovered, Graph.CompletedResult):
        print(f"人工兜底后已发出：{recovered.outputs['delivery'].tracking_code}")
    else:
        print("配送仍未完成，请保留 state 继续处理。")


if __name__ == "__main__":
    asyncio.run(main())
