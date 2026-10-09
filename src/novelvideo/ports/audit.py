"""Audit event port."""

from __future__ import annotations

from typing import Any, Dict, Optional, Protocol


class AuditSink(Protocol):
    async def emit_audit_event(
        self,
        *,
        action: str,
        user_id: Optional[str] = None,
        admin_user_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        actor_type: str = "worker",
        project_id: Optional[str] = None,
        resource: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None: ...
