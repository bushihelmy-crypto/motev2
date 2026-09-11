"""Cancellation-safe joins for acknowledged execution work."""

import asyncio
from typing import TypeVar, cast

AwaitedT = TypeVar("AwaitedT")


async def wait_for_owner_task(
    task: asyncio.Task[AwaitedT],
) -> tuple[AwaitedT, asyncio.CancelledError | None]:
    """Wait through caller cancellation until one owner task is settled."""

    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            current = cast(asyncio.Task[object], asyncio.current_task())
            if current.cancelling() == 0:
                break
            if cancellation is None:
                cancellation = error
    return task.result(), cancellation


__all__: list[str] = []
