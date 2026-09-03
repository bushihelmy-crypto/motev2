import asyncio
from typing import cast

import pytest
from example.graph.bounded_execution import build_graph as build_bounded_graph
from example.graph.cancellation_abort import CheckpointStore
from example.graph.cancellation_abort import build_graph as build_cancellation_graph
from example.graph.checkpointed_import import ImportJob, ImportStatus, StateStore
from example.graph.checkpointed_import import build_graph as build_import_graph
from example.graph.concurrent_runs import build_graph as build_concurrent_graph
from example.graph.conditional_mood_radio import build_graph as build_mood_graph
from example.graph.fanout_terminal import build_graph as build_fanout_terminal_graph
from example.graph.human_in_the_loop import Article, ReviewStatus
from example.graph.human_in_the_loop import build_graph as build_editorial_graph
from example.graph.linear_treasure_hunt import build_graph as build_treasure_graph
from example.graph.nested_batch_review import ReviewDecision, ReviewPacket
from example.graph.nested_batch_review import build_graph as build_batch_review_graph
from example.graph.nested_space_mission import build_graph as build_space_graph
from example.graph.parallel_detectives import build_graph as build_detective_graph
from example.graph.partial_commit_recovery import FailOnScopeCommit
from example.graph.partial_commit_recovery import build_graph as build_partial_commit_graph
from example.graph.polling_loop import PollDecision, PollRequest
from example.graph.polling_loop import build_graph as build_polling_graph
from example.graph.resource_customer_report import CustomerSnapshot
from example.graph.resource_customer_report import build_graph as build_customer_report_graph
from example.graph.versioned_deployment import build_graph as build_versioned_graph

from mote_kernel.execution import Graph


@pytest.mark.asyncio
async def test_linear_treasure_hunt_completes_through_direct_edges() -> None:
    result = await build_treasure_graph().run(Graph.values(clue="倒着读：洞树老"), run_id="example-linear")

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["treasure"] == "打开老树洞的蓝色宝箱，得到一枚星星金币"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mood", "playlist"),
    (("开心", "霓虹派对歌单"), ("平静", "雨夜钢琴歌单")),
)
async def test_conditional_mood_radio_completes_after_the_selected_branch(mood: str, playlist: str) -> None:
    settled: list[str] = []

    async def commit(transition: Graph.Transition[str], /) -> Graph.State:
        if isinstance(transition.result, Graph.SuccessResult):
            settled.append(transition.result.node_id)
        return transition.candidate_state

    result = await build_mood_graph().run(
        Graph.values(mood=mood),
        run_id=f"example-mood-{mood}",
        commit=commit,
    )

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["message"] == f"正在播放{playlist}"
    selected = "party-playlist" if mood == "开心" else "quiet-playlist"
    unselected = "quiet-playlist" if mood == "开心" else "party-playlist"
    assert selected in settled
    assert unselected not in settled


@pytest.mark.asyncio
async def test_parallel_detectives_join_all_evidence() -> None:
    result = await build_detective_graph().run(Graph.values(case="午夜谜案"), run_id="example-parallel")

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["verdict"] == (
        "结论：园丁来过。证据：午夜谜案：窗台有湿泥；午夜谜案：时钟停在午夜；午夜谜案：猫只对园丁哈气"
    )


@pytest.mark.asyncio
async def test_nested_space_mission_projects_the_child_result() -> None:
    result = await build_space_graph().run(Graph.values(ship="银月号"), run_id="example-nested")

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["report"] == "银月号主引擎已点火，稳定进入月球轨道；探测器开始扫描冰川"


@pytest.mark.asyncio
async def test_human_in_the_loop_resumes_on_a_fresh_graph_from_authoritative_state() -> None:
    pending = Article("初稿", ReviewStatus.PENDING)
    paused = await build_editorial_graph().run(Graph.values(article=pending), run_id="example-editorial")

    assert isinstance(paused, Graph.AwaitingResumeResult)
    assert len(paused.interrupts) == 1
    assert paused.interrupts[0].request_payload == "初稿".encode()

    approved = Article("终稿", ReviewStatus.APPROVED)
    recovered_graph = build_editorial_graph()
    completed = await recovered_graph.run(
        state=paused.state,
        resume=(
            recovered_graph.resume_interrupted(
                "publish",
                paused.interrupts[0].interrupt_id,
                Graph.values(article=approved),
            ),
        ),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert completed.outputs["published"] == approved


@pytest.mark.asyncio
async def test_polling_loop_uses_explicit_start_and_repeated_interrupt_resumes() -> None:
    graph = build_polling_graph()
    first = await graph.run(
        Graph.values(request=PollRequest("ticket-1", PollDecision.WAIT)),
        run_id="example-polling",
    )

    assert isinstance(first, Graph.AwaitingResumeResult)
    second = await graph.run(
        state=first.state,
        continuation=first.continuation,
        resume=(
            graph.resume_interrupted(
                "poll",
                first.interrupts[0].interrupt_id,
                Graph.values(request=PollRequest("ticket-1", PollDecision.AGAIN)),
            ),
        ),
    )

    assert isinstance(second, Graph.AwaitingResumeResult)
    completed = await graph.run(
        state=second.state,
        continuation=second.continuation,
        resume=(
            graph.resume_interrupted(
                "poll",
                second.interrupts[0].interrupt_id,
                Graph.values(request=PollRequest("ticket-1", PollDecision.DONE)),
            ),
        ),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert completed.outputs["request"] == PollRequest("ticket-1", PollDecision.DONE)


@pytest.mark.asyncio
async def test_nested_batch_review_resumes_multiple_scopes_in_one_call() -> None:
    graph = build_batch_review_graph()
    pending = ReviewPacket("policy.md", ReviewDecision.PENDING, "")
    paused = await graph.run(Graph.values(packet=pending), run_id="example-batch-review")

    assert isinstance(paused, Graph.AwaitingResumeResult)
    assert tuple(interrupt.scope for interrupt in paused.interrupts) == (("legal",), ("safety",))
    actions = tuple(
        graph.resume_interrupted(
            "review",
            interrupt.interrupt_id,
            Graph.values(packet=ReviewPacket("policy.md", ReviewDecision.APPROVED, scope[0])),
            scope=interrupt.scope,
        )
        for interrupt, scope in zip(paused.interrupts, (("legal",), ("safety",)), strict=True)
    )
    completed = await graph.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=actions,
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert completed.outputs["packet"] == ReviewPacket("policy.md", ReviewDecision.APPROVED, "legal；safety")


@pytest.mark.asyncio
async def test_fanout_terminal_projects_input_and_node_output_after_join_to_end() -> None:
    result = await build_fanout_terminal_graph().run(
        Graph.values(request="invoice-42"),
        run_id="example-fanout-terminal",
        max_parallel_tasks=2,
    )

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["original_request"] == "invoice-42"
    assert result.outputs["prepared_token"] == "token:invoice-42"


@pytest.mark.asyncio
async def test_resource_customer_report_joins_shared_resource_reads() -> None:
    result = await build_customer_report_graph().run(
        Graph.values(request=CustomerSnapshot("customer-1", "")),
        run_id="example-customer-report",
        max_parallel_tasks=3,
    )

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["report"].content == ("客户 customer-1：高级会员；最近订单：3 笔，均已完成；偏好：环保包装")


@pytest.mark.asyncio
async def test_checkpointed_import_commits_before_state_only_recovery() -> None:
    store = StateStore()
    paused = await build_import_graph().run(
        Graph.values(job=ImportJob("customers.csv", False, ImportStatus.NEW)),
        run_id="example-import",
        commit=store,
    )

    assert isinstance(paused, Graph.AwaitingResumeResult)
    assert store.state == paused.state
    assert store.transitions
    assert store.state is not None

    recovered_graph = build_import_graph()
    completed = await recovered_graph.run(
        state=store.state,
        resume=(
            recovered_graph.resume_interrupted(
                "approve",
                paused.interrupts[0].interrupt_id,
                Graph.values(job=ImportJob("customers.csv", True, ImportStatus.PARSED)),
            ),
        ),
        commit=store,
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert completed.outputs["job"].status is ImportStatus.LOADED
    assert store.state == completed.state


@pytest.mark.asyncio
async def test_bounded_execution_fails_closed_before_a_sufficient_budget() -> None:
    graph = build_bounded_graph()
    with pytest.raises(Graph.ExecutionLimitError):
        await graph.run(Graph.values(report="daily report"), max_supersteps=1)

    completed = await graph.run(Graph.values(report="daily report"), max_supersteps=3)

    assert isinstance(completed, Graph.CompletedResult)
    assert completed.outputs["report"] == "daily report -> stage -> stage -> stage"


@pytest.mark.asyncio
async def test_partial_commit_example_retries_only_the_unconfirmed_scope() -> None:
    graph = build_partial_commit_graph()
    paused = await graph.run(Graph.values(left="", right=""), run_id="example-partial-commit")
    assert isinstance(paused, Graph.AwaitingResumeResult)
    interrupt_by_scope = {interrupt.scope: interrupt for interrupt in paused.interrupts}

    faulty = FailOnScopeCommit(("right",))
    try:
        await graph.run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(
                graph.resume_interrupted(
                    "leaf",
                    interrupt_by_scope[("left",)].interrupt_id,
                    Graph.values(value="L"),
                    scope=("left",),
                ),
                graph.resume_interrupted(
                    "leaf",
                    interrupt_by_scope[("right",)].interrupt_id,
                    Graph.values(value="R"),
                    scope=("right",),
                ),
            ),
            commit=faulty,
        )
    except Graph.Error as error:
        assert isinstance(error, Graph.PartialCommitError)
        partial = cast(Graph.PartialCommitError[str], error)
    else:
        pytest.fail("the injected right-scope commit failure must produce a partial handoff")

    completed = await graph.run(
        state=partial.state,
        continuation=partial.continuation,
        resume=(
            graph.resume_interrupted(
                "leaf",
                interrupt_by_scope[("right",)].interrupt_id,
                Graph.values(value="R"),
                scope=("right",),
            ),
        ),
    )
    assert isinstance(completed, Graph.CompletedResult)
    assert partial.failed_scope == ("right",)


@pytest.mark.asyncio
async def test_cancellation_example_persists_an_abort_and_returns_aborted_result() -> None:
    started = asyncio.Event()
    graph = build_cancellation_graph(started)
    store = CheckpointStore()
    task = asyncio.create_task(graph.run(Graph.values(), run_id="example-cancel", commit=store))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert store.state is not None
    aborted = await graph.run(state=store.state)
    assert isinstance(aborted, Graph.AbortedResult)
    assert aborted.abort.reason == "graph invocation was cancelled"


@pytest.mark.asyncio
async def test_concurrent_runs_keep_independent_inputs_on_one_graph_instance() -> None:
    graph = build_concurrent_graph()
    first, second = await asyncio.gather(
        graph.run(Graph.values(name="小明")),
        graph.run(Graph.values(name="小红")),
    )

    assert isinstance(first, Graph.CompletedResult)
    assert isinstance(second, Graph.CompletedResult)
    assert first.outputs["greeting"] == "你好，小明！"
    assert second.outputs["greeting"] == "你好，小红！"


@pytest.mark.asyncio
async def test_versioned_deployment_rejects_an_old_snapshot_before_new_run() -> None:
    old = await build_versioned_graph(1).run(
        Graph.values(version="1", payload="legacy"),
        run_id="example-version-v1",
    )
    assert isinstance(old, Graph.CompletedResult)

    new_graph = build_versioned_graph(2)
    with pytest.raises(Graph.SnapshotMismatchError):
        await new_graph.run(state=old.state)

    restarted = await new_graph.run(
        Graph.values(version="2", payload="migrated"),
        run_id="example-version-v2",
    )
    assert isinstance(restarted, Graph.CompletedResult)
    assert restarted.outputs["message"] == "v2:migrated"
