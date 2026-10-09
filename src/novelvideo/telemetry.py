"""Optional telemetry bootstrap for PydanticAI traces.

This module keeps Logfire fully optional:
- if logfire is not installed, nothing breaks
- if no telemetry env is configured, nothing is initialized
- if OTLP/Jaeger is configured, traces are exported without sending to Logfire SaaS
"""

from __future__ import annotations

import os
from typing import Optional

_logfire_initialized = False


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _should_enable_logfire() -> bool:
    return (
        _env_flag("NOVELVIDEO_ENABLE_LOGFIRE")
        or bool(os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"))
        or bool(os.environ.get("LOGFIRE_TOKEN"))
    )


def init_logfire(service_name: Optional[str] = None) -> bool:
    """Initialize optional Logfire/PydanticAI tracing."""
    global _logfire_initialized

    if _logfire_initialized:
        return True

    if not _should_enable_logfire():
        return False

    try:
        import logfire
    except ImportError:
        print("[Telemetry] logfire not installed; skipping telemetry init")
        return False

    try:
        send_to_logfire = bool(os.environ.get("LOGFIRE_TOKEN"))
        logfire.configure(
            service_name=service_name or os.environ.get("NOVELVIDEO_LOGFIRE_SERVICE", "novelvideo"),
            send_to_logfire=send_to_logfire,
        )
        logfire.instrument_pydantic_ai()
        _logfire_initialized = True
        target = "logfire" if send_to_logfire else "otlp"
        print(f"[Telemetry] Logfire initialized ({target})")
        return True
    except Exception as e:
        print(f"[Telemetry] Logfire init failed: {e}")
        return False
