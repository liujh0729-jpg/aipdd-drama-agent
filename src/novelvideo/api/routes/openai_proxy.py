# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 统一模型入口：``/api/v1/openai/*`` → 生效网关（OpenAI 兼容）。

设置页生成的统一 API 密钥（``st-…``）在这里同样生效：一把 key 既能调外部 MCP，
也能调模型广场里的模型。上游网关的运行时令牌由服务端注入，永不外泄给调用方。

对外 URL（web 的 nginx 把 ``/gateway/v1/`` 反代到这里，对外形态保持稳定）：
- ``POST {PUBLIC}/gateway/v1/chat/completions`` → ``/api/v1/openai/chat/completions``
- ``GET  {PUBLIC}/gateway/v1/models``             → ``/api/v1/openai/models``

实现要点：
- 全方法透传，请求与响应 body 均为流式：长响应（SSE）与多模态大 body 都不整段缓冲。
- 上游取「生效网关」（settings.db → ``get_newapi_runtime_credentials``），与项目自身
  调用模型、模型广场探活是同一条路径，不另起第二套地址解析。
"""

from __future__ import annotations

import logging
import time

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from novelvideo.api.auth import get_api_user
from novelvideo.config import get_newapi_runtime_credentials
from novelvideo.model_gateway_settings import normalize_relay_base_url

router = APIRouter()
logger = logging.getLogger("novelvideo.api.openai_proxy")

_GATEWAY_TIMEOUT = httpx.Timeout(connect=15.0, read=600.0, write=600.0, pool=15.0)

_RELAY_EGRESS_PROJECT_ID = "__gateway_relay__"
"""泛化透传无项目作用域，出网身份用的 project 哨兵值。

照 `chat/hermes_egress.py` 的 home 哨兵先例：`egress_operations` 把 `project_id` 定义成
 TEXT NOT NULL、只校非空，**没有指向 projects 的外键**，所以哨兵不需要对应真实
 project 记录。真实 project id 是 ULID（26 位），与本哨兵值域不相交。本常量只在组织
 分支进入 `TrustedEgressContext`（其 `__post_init__` 要非空 project_id）；非组织下
 `build_request_egress_context` 直接返回 None，根本不会构造 context。
"""


def _relay_requester_user_id(user: dict) -> str:
    return str(
        user.get("user_id") or user.get("id") or user.get("username") or ""
    )


async def _resolve_effective_credentials(user: dict) -> tuple[str, str]:
    """Return ``(api_key, base_url)`` for this relay, attributed to the right principal.

    非组织（CE 单机 / 平台 / 个人 / 无控制面）→ 沿用 ambient 平台凭据，路径逐字节不变。
    组织 → 用组织自身网关凭据，避免平台垫付。authz 准入失败作 ``AuthzError`` 原样上抛
    （fail-closed）——交给 app 层 handler 渲染正确码，**绝不静默回落平台 key**。
    """

    from novelvideo.api.egress_binding import build_request_egress_context
    from novelvideo.ports.registry import PortNotRegistered

    requester_user_id = _relay_requester_user_id(user)
    context = None
    if requester_user_id:
        try:
            context = await build_request_egress_context(
                requester_user_id=requester_user_id,
                project_id=_RELAY_EGRESS_PROJECT_ID,
                task_type="openai_relay",
            )
        except PortNotRegistered:
            # 没有 authz 端口＝裸单机 CE，本就不存在组织租户→平台凭据。
            # （与 `egress_binding` 注释同口径：缺席端口只可能是无组织部署。）
            context = None
    if context is None:
        return get_newapi_runtime_credentials()

    from novelvideo.model_gateway_runtime import (
        resolve_organization_relay_credentials,
    )

    credential = await resolve_organization_relay_credentials(context)
    return credential.api_key, credential.base_url


# 请求侧：Hop-by-hop 头不能透传；Authorization / Cookie 必须换成服务端令牌。
_STRIP_REQUEST_HEADERS = {
    "host",
    "connection",
    "keep-alive",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "authorization",
    "cookie",
    "x-api-key",
    "proxy-authorization",
    "accept-encoding",
}

# 响应侧：同样过滤 hop-by-hop；content-length / content-encoding 由本层重算。
_STRIP_RESPONSE_HEADERS = {
    "connection",
    "keep-alive",
    "transfer-encoding",
    "te",
    "trailer",
    "upgrade",
    "content-encoding",
    "content-length",
}


def _gateway_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": "gateway_error", "code": code}},
    )


def _rejects_traversal_path(path: str) -> bool:
    """【C6】拒绝含目录跳出段的透传子路径。

    ``/openai/{path:path}`` 把子路径拼到网关 base（形如 ``…/v1``）后面。调用方（虽已
    鉴权）可用 ``..`` 拼出 ``/openai/../api/user`` 一类，经 httpx/服务端归一后
    逃出 ``/v1``、触达网关自身的 admin/console API。这里按段拆开后拒一切 ``.``/``..``
    与空段，封死这条穿越面。

    刻意**不做固定端点白名单**：本 fork 的推理面是渠道目录驱动的（/videos/{id} 轮询、
    images/edits、tools/* 及目录声明的任意端点），硬白名单会打断这些合法面。
    非穿越的透传仍照目录自由转发。
    """

    return any(segment in {".", ".."} for segment in path.split("/"))


@router.api_route(
    "/openai/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def relay_openai(path: str, request: Request, user: dict = Depends(get_api_user)):
    """把 OpenAI 兼容请求透传到生效网关（服务端注入网关令牌）。"""
    if _rejects_traversal_path(path):
        return _gateway_error(
            404,
            "invalid_relay_path",
            "不支持的网关透传路径",
        )
    api_key, base_url = await _resolve_effective_credentials(user)
    api_key = (api_key or "").strip()
    base = normalize_relay_base_url(base_url)
    if not api_key or not base:
        return _gateway_error(
            503, "gateway_not_configured", "网关尚未配置（请到「设置 → 模型与渠道」连接渠道）"
        )
    target = f"{base}/{path.lstrip('/')}"
    if request.url.query:
        target = f"{target}?{request.url.query}"

    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() not in _STRIP_REQUEST_HEADERS
    }
    headers["authorization"] = f"Bearer {api_key}"
    # 让上游不做压缩：本层按流透传，避免压缩头与解压状态不一致。
    headers["accept-encoding"] = "identity"

    has_body = request.headers.get("content-length") not in (None, "", "0") or bool(
        request.headers.get("transfer-encoding")
    )
    client = httpx.AsyncClient(timeout=_GATEWAY_TIMEOUT)
    started = time.perf_counter()
    try:
        upstream_request = client.build_request(
            request.method,
            target,
            headers=headers,
            content=request.stream() if has_body else None,
        )
        upstream = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        logger.warning(
            "[aipdd gateway] 上游请求失败 method=%s target=%s error=%s",
            request.method,
            target,
            exc,
        )
        return _gateway_error(502, "gateway_unreachable", f"上游网关请求失败: {exc}")

    async def _stream_body():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    response_headers = {
        name: value
        for name, value in upstream.headers.items()
        if name.lower() not in _STRIP_RESPONSE_HEADERS
    }
    logger.info(
        "[aipdd gateway] %s %s -> %s status=%s user=%s duration_ms=%.1f",
        request.method,
        path,
        base,
        upstream.status_code,
        user.get("username"),
        (time.perf_counter() - started) * 1000,
    )
    return StreamingResponse(
        _stream_body(),
        status_code=upstream.status_code,
        headers=response_headers,
    )