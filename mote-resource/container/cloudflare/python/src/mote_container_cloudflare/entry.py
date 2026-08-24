"""Cloudflare Worker and Durable Object container entry points."""

from http import HTTPStatus

from workers import DurableObject, Request, Response, WorkerEntrypoint


class AgentDurableObject(DurableObject):
    """Cloudflare container for one logical Agent."""

    async def fetch(self, request: Request) -> Response:
        """Reject requests until the first Kernel-owned consumer defines a protocol."""
        return Response(status=HTTPStatus.NOT_IMPLEMENTED)


class Default(WorkerEntrypoint):
    """Worker entry point reserved for a future Product-owned routing surface."""

    async def fetch(self, request: Request) -> Response:
        """Reject routes until their owning contract exists."""
        return Response(status=HTTPStatus.NOT_FOUND)
