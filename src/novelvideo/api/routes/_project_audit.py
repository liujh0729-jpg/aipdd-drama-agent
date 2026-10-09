"""Shared project audit helper."""

from __future__ import annotations

import logging

from novelvideo.ports import get_audit_sink

logger = logging.getLogger("novelvideo.api.projects")


async def emit_project_audit(
    *,
    action: str,
    ctx,
    metadata: dict | None = None,
) -> None:
    try:
        await get_audit_sink().emit_audit_event(
            action=action,
            user_id=ctx.requester_user_id,
            actor_type="user",
            project_id=ctx.project_id,
            metadata=metadata or {},
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("project audit emit failed: %s", exc)
