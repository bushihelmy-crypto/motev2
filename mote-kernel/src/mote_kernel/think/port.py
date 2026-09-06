"""Invocation-backed Port adapters for the Think stages.

The graph nodes depend on the small capability contracts declared in
``think.contract``.  These adapters are the owner-internal bridge from those
contracts to the shared :class:`mote_kernel.invocation.Invocation` seam.  A
Port does one typed strict invocation for each operation and deliberately owns
no retry, failover, state, or transport behaviour.  Each adapter receives an
explicit immutable ``InvocationTypeContract`` because Python generic
parameters are erased before a network result returns.

``PromptPort`` is one capability with three operations.  It therefore keeps
three invocation bindings on one immutable object instead of exposing three
independent Port objects.  The other four stages each have one invocation.
The classes live in this implementation module and are not re-exported from
``mote_kernel.think``; the public graph entry point remains ``ThinkNode``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.invocation import (
    Invocation,
    InvocationBoundaryError,
    InvocationTypeContract,
    invoke_typed,
)
from mote_kernel.think.contract import ThinkContractError

PayloadT = TypeVar("PayloadT")
SystemPromptT = TypeVar("SystemPromptT")
PlaceholderT = TypeVar("PlaceholderT")
UserPromptT = TypeVar("UserPromptT")
RequestT = TypeVar("RequestT")
ResultT = TypeVar("ResultT")


def _validate_invocation(invocation: Invocation[PayloadT, ResultT] | None, field: str, /) -> None:
    """Require one runtime object to expose the shared Invocation seam."""

    if not isinstance(invocation, Invocation):
        raise ThinkContractError(f"{field} requires an Invocation capability")
    if not callable(invocation.invoke):
        raise ThinkContractError(f"{field} requires an Invocation capability")


def _validate_contract(contract: InvocationTypeContract[RequestT, ResultT] | None, field: str, /) -> None:
    """Reject a forged or missing typed contract during Port assembly."""

    if type(contract) is not InvocationTypeContract:
        raise ThinkContractError(f"{field} requires an InvocationTypeContract")


async def _invoke_typed(
    invocation: Invocation[RequestT, ResultT],
    request: RequestT,
    contract: InvocationTypeContract[RequestT, ResultT],
    /,
) -> ResultT:
    """Expose typed-boundary failures in the Think contract vocabulary."""

    try:
        return await invoke_typed(invocation, request, contract)
    except InvocationBoundaryError as error:
        raise ThinkContractError(str(error)) from error


@dataclass(frozen=True, slots=True)
class PromptPort(Generic[PayloadT, SystemPromptT, PlaceholderT, UserPromptT]):
    """Adapt the three Prompt operations to three typed invocations.

    The object is the single Prompt capability injected into ``PromptNode``.
    Each method forwards the exact payload once and preserves the invocation's
    result, exception, and cancellation semantics.
    """

    system_invocation: Invocation[PayloadT, SystemPromptT]
    placeholder_invocation: Invocation[PayloadT, PlaceholderT]
    user_invocation: Invocation[PayloadT, UserPromptT]
    system_contract: InvocationTypeContract[PayloadT, SystemPromptT]
    placeholder_contract: InvocationTypeContract[PayloadT, PlaceholderT]
    user_contract: InvocationTypeContract[PayloadT, UserPromptT]

    def __post_init__(self) -> None:
        _validate_invocation(self.system_invocation, "PromptPort.system_invocation")
        _validate_invocation(self.placeholder_invocation, "PromptPort.placeholder_invocation")
        _validate_invocation(self.user_invocation, "PromptPort.user_invocation")
        _validate_contract(self.system_contract, "PromptPort.system_contract")
        _validate_contract(self.placeholder_contract, "PromptPort.placeholder_contract")
        _validate_contract(self.user_contract, "PromptPort.user_contract")

    async def load_system_prompt(self, payload: PayloadT, /) -> SystemPromptT:
        """Load the system prompt through the configured invocation."""

        return await _invoke_typed(self.system_invocation, payload, self.system_contract)

    async def load_placeholder(self, payload: PayloadT, /) -> PlaceholderT:
        """Load placeholder content through the configured invocation."""

        return await _invoke_typed(self.placeholder_invocation, payload, self.placeholder_contract)

    async def load_user_prompt(self, payload: PayloadT, /) -> UserPromptT:
        """Load the user prompt through the configured invocation."""

        return await _invoke_typed(self.user_invocation, payload, self.user_contract)


@dataclass(frozen=True, slots=True)
class ContextPort(Generic[RequestT, ResultT]):
    """Adapt one Context request to one strict invocation."""

    invocation: Invocation[RequestT, ResultT]
    contract: InvocationTypeContract[RequestT, ResultT]

    def __post_init__(self) -> None:
        _validate_invocation(self.invocation, "ContextPort.invocation")
        _validate_contract(self.contract, "ContextPort.contract")

    async def load_context(self, request: RequestT, /) -> ResultT:
        """Load one context result without adding a second execution path."""

        return await _invoke_typed(self.invocation, request, self.contract)


@dataclass(frozen=True, slots=True)
class CompactPort(Generic[RequestT, ResultT]):
    """Adapt one Compact request to one strict invocation."""

    invocation: Invocation[RequestT, ResultT]
    contract: InvocationTypeContract[RequestT, ResultT]

    def __post_init__(self) -> None:
        _validate_invocation(self.invocation, "CompactPort.invocation")
        _validate_contract(self.contract, "CompactPort.contract")

    async def compact(self, request: RequestT, /) -> ResultT:
        """Forward one compaction request exactly once."""

        return await _invoke_typed(self.invocation, request, self.contract)


@dataclass(frozen=True, slots=True)
class InferencePort(Generic[RequestT, ResultT]):
    """Adapt one model request to one strict invocation."""

    invocation: Invocation[RequestT, ResultT]
    contract: InvocationTypeContract[RequestT, ResultT]

    def __post_init__(self) -> None:
        _validate_invocation(self.invocation, "InferencePort.invocation")
        _validate_contract(self.contract, "InferencePort.contract")

    async def infer(self, request: RequestT, /) -> ResultT:
        """Forward one inference request exactly once."""

        return await _invoke_typed(self.invocation, request, self.contract)


@dataclass(frozen=True, slots=True)
class CommandPort(Generic[RequestT, ResultT]):
    """Adapt one normalized inference result to one strict invocation."""

    invocation: Invocation[RequestT, ResultT]
    contract: InvocationTypeContract[RequestT, ResultT]

    def __post_init__(self) -> None:
        _validate_invocation(self.invocation, "CommandPort.invocation")
        _validate_contract(self.contract, "CommandPort.contract")

    async def build_command(self, request: RequestT, /) -> ResultT:
        """Build one structured command through the configured invocation."""

        return await _invoke_typed(self.invocation, request, self.contract)


__all__ = ["CommandPort", "CompactPort", "ContextPort", "InferencePort", "PromptPort"]
