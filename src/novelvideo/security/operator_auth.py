"""Operator authorization helpers shared by REST and UI layers."""

from __future__ import annotations

import os


def get_prompt_export_password() -> str | None:
    """Return the configured operator password, or None if unset.

    [aipdd fork delta] 设置数据库优先（设置页 → 安全 → operator 口令，存
    settings.db），其次 ``PROMPT_EXPORT_PASSWORD`` 环境变量作首启引导兜底；
    两处都空则 fail-closed（返回 None，验证永不通过，无硬编码兜底）。
    """
    db_value = ""
    try:
        from novelvideo.model_gateway_settings import (
            get_prompt_export_password_setting,
        )

        db_value = get_prompt_export_password_setting()
    except Exception:  # noqa: BLE001 - 读库异常按未配置处理，交 env 兜底
        db_value = ""
    value = db_value or os.getenv("PROMPT_EXPORT_PASSWORD", "").strip()
    return value or None


__all__ = ["get_prompt_export_password"]
