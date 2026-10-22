# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] OpenAI 兼容客户端公共层（唯一真源）。

收敛此前散落在 ``agent/gateway``、``verification/sketch_visual_gate``、
``director_world/*``、``generators/grid_generation`` 的「构造请求 + 凭据 +
错误映射」样板：

- 凭据解析（透传 ``config.get_newapi_runtime_credentials``）；
- base_url 规范化（恰好一个 ``/v1``，禁止二次拼接）；
- Authorization / OpenRouter 直连头构造；
- SSE（``aiter_lines``）解析；
- 错误详情提取与统一错误文案；
- 超时策略。

边界：只做 OpenAI 兼容协议的公共部分。调用方保留各自的错误类型、重试与业务
语义（组织出网闸门、用量计量、图像能力预检、任务轮询等一律不动）。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx

CONNECT_TIMEOUT_SECONDS = 30.0
READ_TIMEOUT_SECONDS = 600.0
WRITE_TIMEOUT_SECONDS = 60.0
POOL_TIMEOUT_SECONDS = 60.0

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_REFERER = "https://novelvideo.ai"
OPENROUTER_TITLE = "NovelVideo Studio"


def api_timeout(read_seconds: float = READ_TIMEOUT_SECONDS) -> httpx.Timeout:
    """统一超时策略：连接 30s、读默认 600s、写/池 60s。"""
    return httpx.Timeout(
        connect=CONNECT_TIMEOUT_SECONDS,
        read=float(read_seconds),
        write=WRITE_TIMEOUT_SECONDS,
        pool=POOL_TIMEOUT_SECONDS,
    )


def normalize_base_url(value: str | None) -> str:
    """规范化 OpenAI 兼容 base_url：恰好一个 ``/v1`` 后缀（禁止二次 ``/v1``）。"""
    base = str(value or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def resolve_credentials(
    *,
    api_key_override: str | None = None,
    base_url_override: str | None = None,
    env_api_key: str = "LOCALAPI_API_KEY",
    env_base_url: str = "LOCALAPI_BASE_URL",
) -> tuple[str, str]:
    """按生效网关解析运行期凭据（透传 ``config.get_newapi_runtime_credentials``）。"""
    from novelvideo.config import get_newapi_runtime_credentials

    api_key, base_url = get_newapi_runtime_credentials(
        api_key_override=api_key_override,
        base_url_override=base_url_override,
        env_api_key=env_api_key,
        env_base_url=env_base_url,
    )
    return str(api_key or "").strip(), str(base_url or "").strip()


def auth_headers(
    api_key: str, *, extra: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Authorization（Bearer）+ ``Content-Type: application/json``，可追加额外头。"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update({str(key): str(value) for key, value in extra.items()})
    return headers


def openrouter_headers(
    api_key: str,
    *,
    referer: str = OPENROUTER_REFERER,
    title: str = OPENROUTER_TITLE,
) -> dict[str, str]:
    """OpenRouter 直连头：Bearer + Content-Type + HTTP-Referer + X-Title。

    调用方通过 ``referer`` / ``title`` 保留各自的身份标识（行为保持）。
    """
    return auth_headers(
        api_key,
        extra={"HTTP-Referer": referer, "X-Title": title},
    )


def chat_completions_url(base_url: str) -> str:
    """``{base}/chat/completions``（base 尾斜杠已剔除）。"""
    return f"{str(base_url or '').rstrip('/')}/chat/completions"


async def iter_sse_json(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    """逐块解析 OpenAI 兼容 SSE：``data:`` 前缀、``[DONE]`` 终止、坏块跳过。"""
    async for line in response.aiter_lines():
        text = line.strip()
        if not text or not text.startswith("data:"):
            continue
        data = text[5:].strip()
        if not data:
            continue
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except ValueError:
            continue
        if isinstance(chunk, dict):
            yield chunk


def error_detail(body: str, *, limit: int = 300) -> str:
    """提取错误详情：``error.message`` / ``error`` / ``message`` / ``detail``，兜底截断原文。"""
    parsed: Any = None
    try:
        parsed = json.loads(body or "")
    except ValueError:
        parsed = None
    detail = ""
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict):
            detail = str(error.get("message") or "")
        elif error:
            detail = str(error)
        detail = detail or str(parsed.get("message") or parsed.get("detail") or "")
    if not detail:
        detail = (body or "").strip()[:limit]
    return detail


def error_message(status_code: int, body: str, *, prefix: str = "公共网关") -> str:
    """统一错误映射：``{prefix}返回 {status}：{detail}``（detail 为空时「无详情」）。"""
    return f"{prefix}返回 {status_code}：{error_detail(body) or '无详情'}"


def first_message_text(result: Mapping[str, Any]) -> str:
    """提取非流式 chat/completions 响应的 ``choices[0].message.content`` 文本。

    兼容 content 为字符串或分段数组（逐 part 取 ``text`` 拼接）两种形态；
    无 choices / 非法形态一律返回空字符串。
    """
    choices = result.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                text = part.get("text")
                if text:
                    parts.append(str(text))
        return "".join(parts).strip()
    return ""


async def download_bytes(
    url: str,
    *,
    timeout: float = 120.0,
    follow_redirects: bool = True,
) -> tuple[bytes, str]:
    """GET 下载外链媒体，返回 ``(bytes, content_type)``（content_type 已去参数）。

    异常为原始 httpx 异常，由调用方映射为各自的错误类型。
    """
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=follow_redirects
    ) as client:
        response = await client.get(url)
        # 关闭重定向时，3xx 不再被 raise_for_status 拦下（<400）——显式拒，
        # 否则调用方会把空重定向体当成有效字节落盘（SSRF 重定向绕过）。
        if not follow_redirects and response.is_redirect:
            raise httpx.HTTPStatusError(
                "redirect refused", request=response.request, response=response
            )
        response.raise_for_status()
        content_type = (
            str(response.headers.get("content-type") or "").split(";")[0].strip()
        )
        return response.content, content_type