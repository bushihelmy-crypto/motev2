from mote_kernel.execution import Graph


class UniverseA:
    pass


class UniverseB:
    pass


async def resume_universe_b(
    graph: Graph[UniverseB],
    state: Graph.State,
    action: Graph.ResumeAction[UniverseA],
) -> Graph.Result[UniverseB]:
    return await graph.run(state=state, resume=(action,))
