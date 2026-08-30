import pytest
from example.graph.conditional_mood_radio import build_graph as build_mood_graph
from example.graph.human_in_the_loop import Article, ReviewStatus
from example.graph.human_in_the_loop import build_graph as build_editorial_graph
from example.graph.linear_treasure_hunt import build_graph as build_treasure_graph
from example.graph.nested_space_mission import build_graph as build_space_graph
from example.graph.parallel_detectives import build_graph as build_detective_graph

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
