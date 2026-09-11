from mote_kernel.agent import Agent, AgentResume


async def run(agent: Agent[str]) -> None:
    await agent.run(AgentResume[str]("run"), commit=None)
