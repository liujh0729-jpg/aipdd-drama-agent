# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 可复用传输层：进程内 ASGI 自调用。

统一计划器与执行入口都只依赖一个 `call(method, path, *, query, body) -> dict`
的协程接口，返回形状**严格对齐** MCP 插件的 ``_request``：

- 2xx 且响应体是 JSON 对象 → ``{status_code, **路由响应体}``，即信封只有一层
  （``data`` 就是路由 ``data`` 里的业务载荷）；
- 2xx 但响应体不是对象 → ``{"ok": True, "status_code": int, "data": Any}``；
- 非 2xx → ``{"ok": False, "status_code": int, "error": str, "data": Any}``。

REST 路由用 ``asgi_caller`` 让后端进程直接调用自身已挂载的细粒度端点——
无需真实端口 / Nginx / 反向环回，也不会阻塞线程池；调用时把当前请求的
``Authorization`` / ``Cookie`` 头透传下去，令各端点的鉴权与项目 scope 校验
与外部调用完全一致（单一事实源）。

[aipdd fork delta 教训] 早先本模块在成功分支又套了一层
``{"ok": .., "data": <整份路由响应>}``，而计划器各读法（``source["data"]["text"]``、
``_rows`` 等）按 MCP 的单层形状编写 —— 结果是经 REST ``/pipeline/plan`` 的两个自动化
入口（前端 driver 与 MCP 工具）都读到空载荷：故事源明明有内容却被判
``requires_input``，整链在第一道闸门原地结束。单测当时假的是 MCP 形状，因此
从未暴露。改任何一侧形状都必须同时跑一遍本文件的契约测试。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import httpx

# call(method, path, *, query=None, body=None) -> {ok, status_code, data, error?}
TransportCall = Callable[..., Awaitable[dict[str, Any]]]

_INTERNAL_BASE = "http://pipeline.internal"
_FORWARD_HEADERS = ("authorization", "cookie")


def _normalize(path: str) -> str:
    raw = str(path or "").strip()
    if not raw.startswith("/"):
        raw = f"/{raw}"
    if raw.startswith("/projects/"):
        raw = f"/api/v1{raw}"
    return raw


def _error_text(data: Any) -> str:
    if isinstance(data, dict):
        for key in ("error", "message", "detail"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def asgi_caller(app: Any, headers: Any = None) -> TransportCall:
    """返回一个进程内自调用协程：经 ASGITransport 打本 app 的 REST 端点。"""

    forward = {
        key: value
        for key, value in (dict(headers or {}) or {}).items()
        if key.lower() in _FORWARD_HEADERS and value
    }

    async def call(
        method: str,
        path: str,
        *,
        query: Any = None,
        body: Any = None,
    ) -> dict[str, Any]:
        clean_query = {
            str(k): v
            for k, v in (query or {}).items()
            if v is not None and v != ""
        } if isinstance(query, dict) else None
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url=_INTERNAL_BASE,
            headers=forward,
            timeout=httpx.Timeout(120.0),
        ) as client:
            resp = await client.request(
                method.upper(),
                _normalize(path),
                params=clean_query,
                json=body if body is not None else None,
            )
        try:
            parsed = resp.json()
        except ValueError:
            parsed = resp.text or None
        status_code = resp.status_code
        ok = 200 <= status_code < 300
        if ok and isinstance(parsed, dict):
            # 与 MCP _decode_response 的 dict 分支同形：把路由响应体摊平，信封只留一层
            # （仅多补一个 ok=True 默认值，路由自己回了 ok 就以路由为准）。
            spread = {"status_code": status_code, **parsed}
            spread.setdefault("ok", True)
            return spread
        if ok:
            return {"ok": True, "status_code": status_code, "data": parsed}
        return {
            "ok": False,
            "status_code": status_code,
            "error": _error_text(parsed) or f"HTTP {status_code}",
            "data": parsed,
        }

    return call
