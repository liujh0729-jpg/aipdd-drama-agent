"""Local CE lifecycle port implementation."""

from __future__ import annotations


class NoOpLifecycle:
    async def on_startup(self, *, register_as_worker: bool = True) -> None:
        return None

    async def on_shutdown(self) -> None:
        return None
