"""Lifecycle port."""

from __future__ import annotations

from typing import Protocol


class LifecyclePort(Protocol):
    async def on_startup(self, *, register_as_worker: bool = True) -> None: ...

    async def on_shutdown(self) -> None: ...
