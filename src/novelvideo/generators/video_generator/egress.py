"""视频生成器包 · 出网脱敏与共享设施（B1 拆包自单文件 video_generator.py）。

含 logger / newAPI 超时与 TTL 常量 / post-accept 重试参数 / VideoEgressError
安全码工具 / _run_video_subprocess / _record_video_model_call。

get_usage_meter 经包命名空间（_video_generator.）延迟解析，保证 tests 对
novelvideo.generators.video_generator.get_usage_meter 的 monkeypatch 打点生效。
"""

import asyncio
import logging
import random
import subprocess
from typing import Any

from dotenv import load_dotenv

from novelvideo.egress_context import ambient_egress_context
from novelvideo.generators import video_generator as _video_generator
from novelvideo.shared.provider_errors import provider_video_error_code_from_text
from novelvideo.task_backend.subprocesses import run_project_subprocess

logger = logging.getLogger(__name__)

# 确保加载 .env 环境变量
load_dotenv()

NEWAPI_VIDEO_HTTP_TIMEOUT_SECONDS = 1800.0
NEWAPI_MEDIA_INPUT_MIN_TTL_SECONDS = 2 * 60 * 60
_POST_ACCEPT_AUTHZ_MAX_RETRIES = 5
_POST_ACCEPT_AUTHZ_RETRY_BASE_SECONDS = 2.0
_POST_ACCEPT_AUTHZ_RETRY_CAP_SECONDS = 60.0
_POST_ACCEPT_AUTHZ_RETRY_SLEEP = asyncio.sleep
_POST_ACCEPT_AUTHZ_RETRY_RANDOM = random.random

_VIDEO_EGRESS_ERROR_MESSAGES = {
    "TASK_ENVELOPE_INVALID": "trusted video egress context is invalid",
    "ORG_EGRESS_DENIED": "organization video egress is denied",
    "ORG_SERVICE_EGRESS_DENIED": "organization video service egress is denied",
    "EGRESS_OPERATION_REPLAYED": "video egress operation cannot be replayed",
    "EGRESS_OPERATION_TRANSITION_FAILED": "video egress operation transition failed",
}


class VideoEgressError(RuntimeError):
    """Stable video egress policy failure without endpoint or secret details."""

    def __init__(self, code: str) -> None:
        super().__init__(_VIDEO_EGRESS_ERROR_MESSAGES.get(code, "video egress failed"))
        self.code = code


def _video_egress_context(value: object):
    if value is None:
        return None
    from novelvideo.egress_context import TrustedEgressContext

    if type(value) is not TrustedEgressContext:
        raise VideoEgressError("TASK_ENVELOPE_INVALID")
    return value


def _deny_direct_organization_video(kwargs: dict[str, Any]) -> None:
    context = _video_egress_context(kwargs.get("egress_context"))
    if context is None:
        context = ambient_egress_context()
    if context is not None and context.billing_principal.kind != "platform":
        raise VideoEgressError("ORG_EGRESS_DENIED")


def _direct_video_context_denied(context: object | None) -> bool:
    if context is None:
        context = ambient_egress_context()
    return bool(context is not None and context.billing_principal.kind != "platform")


def _safe_video_error_code(exc: BaseException, fallback: str) -> str:
    code = getattr(exc, "code", "")
    if isinstance(code, str) and code in {
        "P0_GRAY_DISABLED",
        "ORG_CREDENTIAL_MISSING",
        "ORG_CREDENTIAL_DISABLED",
        "ORG_CREDENTIAL_VERSION_MISMATCH",
        "ORG_CREDENTIAL_DECRYPT_FAILED",
        "ORG_MEMBERSHIP_INACTIVE",
        "ORG_AUTHZ_STALE",
        "EGRESS_OPERATION_CONFLICT",
        "EGRESS_OPERATION_REPLAYED",
    }:
        return code
    # 网关用 VIDEO_* 枚举码打回（尺寸/审核）时放行该码；原文本身不回传。
    provider_code = provider_video_error_code_from_text(str(exc))
    if provider_code:
        return provider_code
    return fallback


def _run_video_subprocess(
    cmd: list[str], *, timeout: int = 30 * 60
) -> subprocess.CompletedProcess:
    return run_project_subprocess(cmd, capture_output=True, text=True, timeout=timeout)


async def _record_video_model_call(
    *,
    model: str,
    provider_request_id: str = "",
    provider_task_id: str = "",
) -> None:
    """[aipdd fork delta] WP-D: 只记一次上游调用，不做预约 / 退款。"""
    try:
        await _video_generator.get_usage_meter().bump_model_call(
            user_id=None,
            model=model,
            provider_request_id=provider_request_id,
            provider_task_id=provider_task_id,
        )
    except Exception:
        pass
