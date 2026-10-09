# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] Agent 公共网关客户端：聊天补全（流式 + 工具调用）与图片生成。

凭据来自 ``config.get_newapi_runtime_credentials()``——与设置页 / OpenAI 代理
（/api/v1/openai/*，对外 /gateway/v1/*）同一个「生效网关」；服务端注入上游令牌，
Agent 不接触任何上游密钥，也不需要用户单独填 key。

协议（OpenAI 兼容）：
- 聊天：POST ``{base}/chat/completions``（stream=true，SSE 逐块解析）；
- 图片：POST ``{base}/images/generations``，同步出图（``data[0].url`` /
  ``b64_json``）或任务式（``task_id``）轮询 ``GET /images/generations/{task_id}``。

请求构造 / SSE 解析 / 错误映射统一收敛到 ``novelvideo.shared.openai_compat``；
本模块保留对外函数签名（``chat_stream / generate_image / download_media /
gateway_credentials``）与既有错误文案不变。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

import httpx

from novelvideo.shared import openai_compat

logger = logging.getLogger("novelvideo.agent.gateway")

CONNECT_TIMEOUT_SECONDS = openai_compat.CONNECT_TIMEOUT_SECONDS
CHAT_READ_TIMEOUT_SECONDS = openai_compat.READ_TIMEOUT_SECONDS
IMAGE_READ_TIMEOUT_SECONDS = openai_compat.READ_TIMEOUT_SECONDS
IMAGE_POLL_INTERVAL_SECONDS = 3.0
IMAGE_MAX_POLLS = 100  # 100 * 3s = 5 分钟
VIDEO_READ_TIMEOUT_SECONDS = openai_compat.READ_TIMEOUT_SECONDS
VIDEO_POLL_INTERVAL_SECONDS = 5.0
VIDEO_MAX_POLLS = 120  # 120 * 5s = 10 分钟

_IMAGE_FAILURE_STATUSES = {"failed", "failure", "error", "cancelled", "canceled"}
# 视频任务完成态：以 URL 出现为准，完成态只用于确认「不是失败」。
_VIDEO_DONE_STATUSES = {"completed", "succeeded", "success", "done"}


class GatewayNotConfigured(RuntimeError):
    """生效网关未配置（没有可用凭据）。"""


class GatewayError(RuntimeError):
    """公共网关请求失败（消息面向用户可读）。"""


def gateway_credentials() -> tuple[str, str]:
    """返回 (api_key, base_url)；未配置时抛 GatewayNotConfigured。

    base_url 走共享层规范化（恰好一个 ``/v1``）：环境回填的地址缺 ``/v1`` 时，
    原实现会把请求 POST 到错误路径（与 openai_proxy 的对外约定不一致）。
    """
    api_key, base_url = openai_compat.resolve_credentials()
    base_url = openai_compat.normalize_base_url(base_url)
    if not api_key or not base_url:
        raise GatewayNotConfigured("公共网关未配置，请先在设置中完成网关配置")
    return api_key, base_url


async def chat_stream(
    *,
    api_key: str,
    base_url: str,
    payload: dict[str, Any],
) -> AsyncIterator[dict[str, Any]]:
    """流式请求聊天补全，逐块产出解析后的 SSE JSON（``choices[].delta`` 结构）。"""
    url = openai_compat.chat_completions_url(base_url)
    try:
        async with httpx.AsyncClient(
            timeout=openai_compat.api_timeout(CHAT_READ_TIMEOUT_SECONDS)
        ) as client:
            async with client.stream(
                "POST", url, headers=openai_compat.auth_headers(api_key), json=payload
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    raise GatewayError(
                        openai_compat.error_message(response.status_code, body)
                    )
                async for chunk in openai_compat.iter_sse_json(response):
                    yield chunk
    except GatewayError:
        raise
    except httpx.TimeoutException as exc:
        raise GatewayError("公共网关响应超时，请稍后重试") from exc
    except httpx.HTTPError as exc:
        raise GatewayError(f"公共网关通信失败：{exc}") from exc


def _image_output(result: dict[str, Any]) -> tuple[str, str]:
    """从图片响应中提取 (b64_json, url)，兼容嵌套形状。"""
    data = result.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        first = data[0]
        b64 = str(first.get("b64_json") or "")
        url = str(first.get("url") or first.get("image_url") or "")
        if b64 or url:
            return b64, url
    b64 = str(result.get("b64_json") or "")
    url = str(result.get("image_url") or result.get("url") or "")
    if b64 or url:
        return b64, url
    output = result.get("output")
    if isinstance(output, dict):
        images = output.get("images")
        if isinstance(images, list) and images and isinstance(images[0], dict):
            first = images[0]
            b64 = str(first.get("b64_json") or "")
            url = str(first.get("url") or first.get("image_url") or "")
    return b64, url


def _image_status(result: dict[str, Any]) -> str:
    for key in ("status", "state", "task_status"):
        value = result.get(key)
        if value:
            return str(value).strip().lower()
    return ""


def _image_error(result: dict[str, Any]) -> str:
    for key in ("error", "message", "detail"):
        value = result.get(key)
        if isinstance(value, dict):
            value = value.get("message")
        if value:
            return str(value).strip()
    return ""


async def generate_image(
    *,
    api_key: str,
    base_url: str,
    model: str,
    prompt: str,
    size: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, str]:
    """请求图片生成，返回 ``{"b64": ..., "url": ...}`` 二者至少其一。

    任务式响应的处理：提交后拿到 ``task_id``（或带状态的 ``id``）时轮询到出图为止。
    """
    payload: dict[str, Any] = {"model": model, "prompt": prompt, "n": 1}
    if size:
        payload["size"] = size
    if extra:
        payload.update(extra)
    timeout = openai_compat.api_timeout(IMAGE_READ_TIMEOUT_SECONDS)
    # [aipdd fork delta req41] 出图路径先问渠道目录（AIPDD 声明了 images/generations），
    # 目录没说话才用通用 OpenAI 路径。轮询拼的是 ``{url}/{task_id}``，所以两条地址会跟着声明走。
    from novelvideo.model_gateway_settings import catalog_endpoint_path
    from novelvideo.catalog.upstream_catalog import EP_IMAGES_GENERATIONS

    image_path = (
        catalog_endpoint_path(model, EP_IMAGES_GENERATIONS) or EP_IMAGES_GENERATIONS
    )
    url = f"{base_url}/{image_path}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(
                url, headers=openai_compat.auth_headers(api_key), json=payload
            )
        except httpx.TimeoutException as exc:
            raise GatewayError("图片生成请求超时") from exc
        except httpx.HTTPError as exc:
            raise GatewayError(f"图片生成请求失败：{exc}") from exc
        if response.status_code >= 400:
            raise GatewayError(
                openai_compat.error_message(response.status_code, response.text)
            )
        try:
            result = response.json()
        except ValueError as exc:
            raise GatewayError("图片生成响应不是合法 JSON") from exc
        if not isinstance(result, dict):
            raise GatewayError("图片生成响应格式异常")
        b64, image_url = _image_output(result)
        task_id = str(result.get("task_id") or "").strip()
        if not b64 and not image_url and not task_id:
            fallback_id = str(result.get("id") or "").strip()
            if fallback_id and _image_status(result):
                task_id = fallback_id
        if b64 or image_url:
            return {"b64": b64, "url": image_url}
        if not task_id:
            raise GatewayError("图片生成响应缺少图片数据")
        logger.info("agent image task accepted: task_id=%s", task_id)
        for _ in range(IMAGE_MAX_POLLS):
            await asyncio.sleep(IMAGE_POLL_INTERVAL_SECONDS)
            try:
                poll_response = await client.get(
                    f"{url}/{task_id}", headers=openai_compat.auth_headers(api_key)
                )
            except httpx.HTTPError:
                continue  # 轮询瞬时错误容忍，等待下一轮
            if poll_response.status_code >= 400:
                continue
            try:
                result = poll_response.json()
            except ValueError:
                continue
            if not isinstance(result, dict):
                continue
            b64, image_url = _image_output(result)
            if b64 or image_url:
                return {"b64": b64, "url": image_url}
            status = _image_status(result)
            if status in _IMAGE_FAILURE_STATUSES:
                raise GatewayError(f"图片任务失败：{_image_error(result) or status}")
        raise GatewayError(f"图片任务超时未完成：task_id={task_id}")


async def download_media(
    url: str, *, timeout: float = 120.0, follow_redirects: bool = True
) -> tuple[bytes, str]:
    """下载外链媒体（图片/视频/音频；任务式出图出 url 时的落盘路径）。返回 (字节, content_type)。"""
    try:
        return await openai_compat.download_bytes(
            url, timeout=timeout, follow_redirects=follow_redirects
        )
    except httpx.HTTPError as exc:
        raise GatewayError(f"图片下载失败：{exc}") from exc


# ---------------------------------------------------------------------------
# 视频生成（异步任务式）
# ---------------------------------------------------------------------------


def _uses_aipdd_videos_api(base_url: str, model: str = "") -> bool:
    """[aipdd fork delta] 视频是否走 OpenAI 风格 ``/v1/videos`` 异步任务协议。

    [aipdd fork delta req41] 判定顺序改成「先看渠道目录，再看 host」：AIPDD 这类
    确定渠道的上游会在目录里给自己的模型写明 ``videos`` / ``videos/{taskId}``，端点
    是上游事实，不用猜；只有目录压根没声明端点（自建 newapi 网关常见：清单只有名字）
    时才退回 host 子串判定（默认 aipdd，可用 ``AIPDD_VIDEO_API_HOST_HINT`` 覆盖）。
    """
    from novelvideo.model_gateway_settings import catalog_endpoints
    from novelvideo.catalog.upstream_catalog import EP_VIDEOS

    declared = catalog_endpoints(model) if str(model or "").strip() else []
    if declared:
        return EP_VIDEOS in declared
    hint = (os.environ.get("AIPDD_VIDEO_API_HOST_HINT") or "aipdd").strip().lower()
    if not hint:
        return False
    host = (urlsplit(str(base_url or "")).hostname or "").lower()
    return hint in host


def _video_task_data(result: dict[str, Any]) -> dict[str, Any]:
    """视频任务响应常为 ``{code, data:{...}}`` 包裹，统一解包（展平层）。"""
    data = result.get("data")
    if isinstance(data, dict):
        return dict(data)
    return result


def _video_url(task: dict[str, Any]) -> str:
    """从任务响应提取视频 URL（字段优先级与生成器后端一致）。"""
    for key in ("result_url", "content_url"):
        value = task.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    metadata = task.get("metadata")
    if isinstance(metadata, dict):
        for key in ("url", "video_url"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for key in ("url", "video_url"):
        value = task.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


async def generate_video(
    *,
    api_key: str,
    base_url: str,
    model: str,
    prompt: str,
    resolution: str | None = None,
    duration: int | None = None,
    ratio: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, str]:
    """请求视频生成（异步任务式），返回 ``{"url": ...}``。

    提交端点按渠道目录声明选：声明了 ``videos`` → ``/videos``（AIPDD 这类确定渠道）；
    目录没声明端点时才按 host 兜底；其余 → ``/video/generations``。
    提交后轮询任务到 URL 出现（完成但地址未归档时继续轮询），失败状态直接报错。
    """
    payload: dict[str, Any] = {"model": model, "prompt": prompt}
    if resolution:
        payload["resolution"] = resolution
    if duration:
        try:
            payload["duration"] = int(duration)
        except (TypeError, ValueError):
            payload["duration"] = duration
    if ratio:
        payload["ratio"] = ratio
    if extra:
        payload.update(extra)
    from novelvideo.model_gateway_settings import videos_request_paths

    _videos_path, _ = videos_request_paths(
        model, _uses_aipdd_videos_api(base_url, model)
    )
    submit_url = f"{base_url}/{_videos_path}"
    timeout = openai_compat.api_timeout(VIDEO_READ_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(
                submit_url, headers=openai_compat.auth_headers(api_key), json=payload
            )
        except httpx.TimeoutException as exc:
            raise GatewayError("视频生成请求超时") from exc
        except httpx.HTTPError as exc:
            raise GatewayError(f"视频生成请求失败：{exc}") from exc
        if response.status_code >= 400:
            raise GatewayError(
                openai_compat.error_message(response.status_code, response.text)
            )
        try:
            result = response.json()
        except ValueError as exc:
            raise GatewayError("视频生成响应不是合法 JSON") from exc
        if not isinstance(result, dict):
            raise GatewayError("视频生成响应格式异常")
        task = _video_task_data(result)
        video_url = _video_url(task)
        if video_url:
            return {"url": video_url}
        task_id = str(task.get("task_id") or task.get("id") or "").strip()
        if not task_id:
            # [aipdd fork delta] 部分网关提交响应的任务编号在嵌套容器里
            # （metadata/result/output 等），解析不到时把响应原文留到日志，
            # 便于按真实结构对齐解析（见 gateway 视频协议回归测试）。
            logger.warning(
                "agent video submit missing task id; response=%s",
                json.dumps(result, ensure_ascii=False)[:800],
            )
            raise GatewayError("视频生成响应缺少任务编号")
        logger.info("agent video task accepted: task_id=%s", task_id)
        for _ in range(VIDEO_MAX_POLLS):
            await asyncio.sleep(VIDEO_POLL_INTERVAL_SECONDS)
            try:
                poll_response = await client.get(
                    f"{submit_url}/{task_id}", headers=openai_compat.auth_headers(api_key)
                )
            except httpx.HTTPError:
                continue  # 轮询瞬时错误容忍，等待下一轮
            if poll_response.status_code >= 400:
                continue
            try:
                result = poll_response.json()
            except ValueError:
                continue
            if not isinstance(result, dict):
                continue
            task = _video_task_data(result)
            video_url = _video_url(task)
            if video_url:
                return {"url": video_url}
            status = _image_status(task)
            if status in _IMAGE_FAILURE_STATUSES:
                raise GatewayError(f"视频任务失败：{_image_error(task) or status}")
            if status in _VIDEO_DONE_STATUSES:
                # 已完成但地址尚未返回（网关归档中）：继续轮询等地址出现。
                continue
        raise GatewayError(f"视频任务超时未完成：task_id={task_id}")