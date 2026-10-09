"""Local CE usage port implementations.

`[aipdd fork delta]` WP-D: nothing here reserves, charges, refunds or settles.
The methods that used to do so returned "allowed / cost 0" and were the reason
the whole credit apparatus read as inert plumbing rather than a real gate; they
are removed together with the protocol. What is left is best-effort attribution
context plus the counters the troubleshooting tooling reads.
"""

from __future__ import annotations

from typing import Any, Optional

from novelvideo.llm_instrumentation import (
    clear_llm_usage_context,
    set_llm_usage_context,
)


class NoOpUsageMeter:
    async def update_current_model_call_log(
        self,
        *,
        request_payload: Optional[dict[str, Any]] = None,
        response_payload: Optional[dict[str, Any]] = None,
        error_message: str = "",
    ) -> None:
        return None

    async def bump_model_call(
        self,
        *,
        user_id: Optional[str],
        model: str = "",
        project_id: Optional[str] = None,
        resource_kind: str = "",
        provider_request_id: str = "",
        provider_task_id: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        return None

    def set_llm_usage_context(
        self,
        user_id: Optional[str],
        project_id: Optional[str] = None,
        resource_kind: str = "",
        billing_metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        set_llm_usage_context(
            user_id,
            project_id=project_id,
            resource_kind=resource_kind,
            billing_metadata=billing_metadata,
        )

    def clear_llm_usage_context(self) -> None:
        clear_llm_usage_context()

    async def set_project_llm_usage_context(
        self,
        *,
        username: Optional[str],
        project_name: Optional[str],
        resource_kind: str = "",
        billing_metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        return None

    async def bump_content_counter(
        self,
        *,
        user_id: Optional[str],
        metric: str,
        value: int,
        model: str = "",
        project_id: Optional[str] = None,
        resource_kind: str = "",
    ) -> None:
        return None

    async def log_resource_attempts(
        self,
        *,
        user_id: Optional[str],
        project_id: Optional[str],
        kind: str,
        refs: list[str],
        outcome: str = "success",
        model: str = "",
    ) -> None:
        return None

    async def record_llm_tokens(
        self,
        *,
        user_id: Optional[str],
        input_tokens: int,
        output_tokens: int,
        model: Optional[str] = None,
        project_id: Optional[str] = None,
        resource_kind: str = "",
    ) -> None:
        return None


class NoOpProviderInstrumentation:
    def install(self) -> None:
        return None
