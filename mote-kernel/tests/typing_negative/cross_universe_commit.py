from mote_kernel.execution import Graph


class UniverseA:
    pass


class UniverseB:
    pass


async def commit_universe_a(transition: Graph.Transition[UniverseA]) -> Graph.State:
    return transition.candidate_state


async def run_universe_b(
    graph: Graph[UniverseB],
    values: Graph.Values[UniverseB],
) -> Graph.Result[UniverseB]:
    return await graph.run(values, commit=commit_universe_a)
