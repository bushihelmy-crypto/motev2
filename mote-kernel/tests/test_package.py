from mote_kernel import __version__


def test_package_version() -> None:
    assert __version__ == "0.1.0"


def test_agent_is_the_only_package_public_facade() -> None:
    import mote_kernel
    import mote_kernel.agent as agent_module

    assert mote_kernel.__all__ == ["Agent", "__version__"]
    assert agent_module.__all__ == ["Agent"]
    assert mote_kernel.Agent is agent_module.Agent
    assert not hasattr(mote_kernel, "AgentSession")
    assert not hasattr(mote_kernel, "AgentSessionCodec")
