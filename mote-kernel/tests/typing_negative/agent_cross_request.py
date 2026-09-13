from mote_kernel.agent import Agent, AgentResult, AgentStart
from mote_kernel.execution import Graph


async def run(agent: Agent[str], values: Graph.Values[int]) -> AgentResult[str]:
    request = AgentStart("run", values)
    return await agent.run(request)
