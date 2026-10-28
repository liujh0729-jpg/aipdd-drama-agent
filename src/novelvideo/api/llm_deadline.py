"""请求路径同步 LLM 生成的总墙钟超时兜底。

C3/A7 已把 content/story/scripts 的同步 LLM 出网调用接进 ``request_egress_scope``
（身份/计费归因这条安全债已修毕）。剩下的是一条**可靠性**债：整轮生成在请求进程内
没有总墙钟上限。底层单次 HTTP 调用各有 read timeout（``LOCALAPI_TEXT_TIMEOUT_SECONDS``，
默认 300s），但一次「生成」往往是**多轮往返**——诊断→优化、agent 多步、内部重试——
聚合时长可以把一个 worker 无限期占住，且经 HTTP 无法取消。

本模块给请求路径的 LLM await 套一道**安全网式**的总超时：只在病态挂起（重试风暴、
卡死的工作流）时触发并转成干净的 504，正常生成（远低于该上限）逐字节不变、零行为变化。

刻意取宽松上限（默认 900s，``LLM_REQUEST_DEADLINE_SECONDS`` 可调）：它是防 worker 饿死
的兜底，不是给正常链路设的 SLA——设太紧会把合法的多步生成误杀成 504（功能回归），
设太松则失去兜底意义。真要「可取消 + 移出请求 loop」得把这几条链路整体搬进 EE 任务
队列；CE 用 ``InlineTaskBackend`` 内联执行，入队不带来任何取消/超时收益，故此处只做
契约保持的兜底，不动 HTTP 响应契约与前端。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import HTTPException

_DEFAULT_DEADLINE_SECONDS = 900.0


def llm_request_deadline_seconds() -> float:
    """总墙钟上限（秒）。env 缺失或非法一律回落到宽松默认值。"""

    raw = os.environ.get("LLM_REQUEST_DEADLINE_SECONDS")
    if raw is None or not str(raw).strip():
        return _DEFAULT_DEADLINE_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_DEADLINE_SECONDS
    return value if value > 0 else _DEFAULT_DEADLINE_SECONDS


@asynccontextmanager
async def llm_request_deadline(label: str = "模型生成") -> AsyncIterator[None]:
    """Bound the wrapped await by the request-path LLM deadline.

    超时抛 ``HTTPException(504)``（中文、可诊断），而不是让停摆的上游把 worker 占死。
    非超时的异常原样透传，交给调用点既有的 ``except`` 处理。
    """

    try:
        async with asyncio.timeout(llm_request_deadline_seconds()):
            yield
    except TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"{label}超时，请稍后重试",
        ) from None
