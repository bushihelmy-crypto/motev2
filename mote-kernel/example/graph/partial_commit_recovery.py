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


async def unavailable(_values: Graph.Values[str]) -> Graph.Outcome[str]:
    return Graph.failure("provider result is missing")


def _empty_transitions() -> list[Graph.Transition[str]]:
    return []


def build_child(definition_id: str) -> Graph[str]:
    child = Graph[str](definition_id)
    child.add_node("leaf", unavailable, inputs={}, outputs={"value": str})
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
    """Build two independent child scopes whose failed leaves can be replaced."""

    graph = Graph[str]("example.partial-commit-recovery")
    graph.add_node("left", build_child("example.partial-commit-recovery.left"), inputs={})
    graph.add_node("right", build_child("example.partial-commit-recovery.right"), inputs={})
    graph.set_outputs({})
    return graph


async def main() -> None:
    graph = build_graph()
    paused = await graph.run(Graph.values(), run_id="partial-commit")
    if not isinstance(paused, Graph.AwaitingResumeResult):
        print("没有可恢复的失败节点。")
        return

    left = Graph.values(value="left-result")
    right = Graph.values(value="right-result")
    faulty = FailOnScopeCommit(("right",))
    try:
        await graph.run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(
                graph.skip_failed("leaf", "left replacement", output=left, scope=("left",)),
                graph.skip_failed("leaf", "right replacement", output=right, scope=("right",)),
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
            resume=(graph.skip_failed("leaf", "retry right", output=right, scope=("right",)),),
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
