from mote_kernel.agent import Agent, AgentResult
from mote_kernel.execution import Graph


async def run(agent: Agent[str], state: Graph.State) -> AgentResult[str]:
    return await agent.run(state)
