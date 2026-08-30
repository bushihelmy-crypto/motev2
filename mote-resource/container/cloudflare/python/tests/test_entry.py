import importlib
import sys
from http import HTTPStatus
from types import ModuleType
from typing import Protocol, cast

import pytest


class _Response:
    def __init__(self, body: object = None, status: HTTPStatus | int | None = None) -> None:
        self.body = body
        self.status = status


class _Request:
    pass


class _DurableObject:
    pass


class _WorkerEntrypoint:
    pass


class _Handler(Protocol):
    async def fetch(self, request: object) -> _Response: ...


class _HandlerFactory(Protocol):
    def __call__(self) -> _Handler: ...


class _EntryModule(Protocol):
    AgentDurableObject: _HandlerFactory
    Default: _HandlerFactory


class _WorkersModule(Protocol):
    DurableObject: type[_DurableObject]
    Request: type[_Request]
    Response: type[_Response]
    WorkerEntrypoint: type[_WorkerEntrypoint]


def _load_entry(monkeypatch: pytest.MonkeyPatch) -> _EntryModule:
    workers_module = ModuleType("workers")
    workers = cast(_WorkersModule, workers_module)
    workers.DurableObject = _DurableObject
    workers.Request = _Request
    workers.Response = _Response
    workers.WorkerEntrypoint = _WorkerEntrypoint
    monkeypatch.setitem(sys.modules, "workers", workers_module)
    sys.modules.pop("mote_container_cloudflare.entry", None)
    return cast(_EntryModule, importlib.import_module("mote_container_cloudflare.entry"))


@pytest.mark.asyncio
async def test_scaffold_rejects_unowned_routes_and_operations(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _load_entry(monkeypatch)

    worker_response = await entry.Default().fetch(object())
    object_response = await entry.AgentDurableObject().fetch(object())

    assert worker_response.status is HTTPStatus.NOT_FOUND
    assert object_response.status is HTTPStatus.NOT_IMPLEMENTED
