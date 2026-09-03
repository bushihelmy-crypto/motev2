"""部分提交后的恢复：用 ``Graph.PartialCommitError`` 接住精确确认的前缀。

This is an operational example rather than a business topology.  Two child
scopes are resumed together; the durable commit adapter confirms ``left`` and
fails on ``right``.  The error hands back the confirmed state and continuation,
so a later invocation retries only the unconfirmed scope.
"""

import asyncio
from dataclasses import dataclass, field
from typing import cast

from mote_kernel.execution import Graph


def encode_text(values: Graph.Values[str]) -> bytes:
    return values["value"].encode()


def decode_text(payload: bytes) -> Graph.Values[str]:
    return Graph.values(value=payload.decode())


async def await_result(values: Graph.Values[str]) -> Graph.Outcome[str]:
    value = values["value"]
    if not value:
        return Graph.interrupt(b"provider result")
    return Graph.success(Graph.values(value=value))


def _empty_transitions() -> list[Graph.Transition[str]]:
    return []


def build_child(definition_id: str) -> Graph[str]:
    child = Graph[str](definition_id)
    child.set_resume_codec("text", 1, encode_text, decode_text)
    child.add_node(
        "leaf",
        await_result,
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
    )
    child.set_outputs({"value": Graph.node_output("leaf", "value")})
    return child


@dataclass(slots=True)
class FailOnScopeCommit:
    """A deterministic commit-port fault injector for the teaching example."""

    failed_scope: tuple[str, ...]
    transitions: list[Graph.Transition[str]] = field(default_factory=_empty_transitions)

    async def __call__(self, transition: Graph.Transition[str], /) -> Graph.State:
        self.transitions.append(transition)
        if transition.scope == self.failed_scope:
            raise RuntimeError(f"durable store unavailable at scope {self.failed_scope!r}")
        return transition.candidate_state


async def accept_commit(transition: Graph.Transition[str], /) -> Graph.State:
    return transition.candidate_state


def build_graph() -> Graph[str]:
    """Build two independent child scopes awaiting external results."""

    graph = Graph[str]("example.partial-commit-recovery")
    graph.add_node(
        "left",
        build_child("example.partial-commit-recovery.left"),
        inputs={"value": Graph.graph_input("left", str)},
    )
    graph.add_node(
        "right",
        build_child("example.partial-commit-recovery.right"),
        inputs={"value": Graph.graph_input("right", str)},
    )
    graph.set_outputs({})
    return graph


async def main() -> None:
    graph = build_graph()
    paused = await graph.run(Graph.values(left="", right=""), run_id="partial-commit")
    if not isinstance(paused, Graph.AwaitingResumeResult):
        print("没有等待外部结果的节点。")
        return

    left = Graph.values(value="left-result")
    right = Graph.values(value="right-result")
    faulty = FailOnScopeCommit(("right",))
    interrupt_by_scope = {interrupt.scope: interrupt for interrupt in paused.interrupts}
    try:
        await graph.run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(
                graph.resume_interrupted(
                    "leaf",
                    interrupt_by_scope[("left",)].interrupt_id,
                    left,
                    scope=("left",),
                ),
                graph.resume_interrupted(
                    "leaf",
                    interrupt_by_scope[("right",)].interrupt_id,
                    right,
                    scope=("right",),
                ),
            ),
            commit=faulty,
        )
    except Graph.Error as error:
        if not isinstance(error, Graph.PartialCommitError):
            raise
        partial = cast(Graph.PartialCommitError[str], error)
        print(f"已确认前缀，失败作用域：{partial.failed_scope}")
        recovered = await graph.run(
            state=partial.state,
            continuation=partial.continuation,
            resume=(
                graph.resume_interrupted(
                    "leaf",
                    interrupt_by_scope[("right",)].interrupt_id,
                    right,
                    scope=("right",),
                ),
            ),
            commit=accept_commit,
        )
    else:
        print("提交没有触发故障。")
        return

    if isinstance(recovered, Graph.CompletedResult):
        print("部分提交后的恢复完成。")
    else:
        print("仍有未确认的作用域。")


if __name__ == "__main__":
    asyncio.run(main())
