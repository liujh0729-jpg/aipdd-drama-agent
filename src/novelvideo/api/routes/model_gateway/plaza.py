"""模型广场（model plaza）路由：只读目录与单次最小探活。

[重构] 自原 ``model_gateway.py`` 物理拆分而来（纯移动，行为与 URL 契约不变）。
本模块不引用任何需要 monkeypatch 保真的模块级名字，故不经包命名空间解析。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request

# [aipdd fork delta] WP-E: 模型广场（只读目录 + 探活 + 调用示例）。
from novelvideo.model_plaza import (
    PLAZA_CONNECTORS,
    build_model_plaza,
    find_plaza_model,
    probe_plaza_model,
    set_request_origin,
)

router = APIRouter()


def _browser_origin(request: Request) -> str:
    """用户实际看到的那个 origin（scheme://host[:port]），拿不到就空。

    [aipdd fork delta req41] 广场卡片里给人复制的网关地址不再猜 ``localhost``：
    部署不在本机 / 换端口 / 走域名反代时，只有「请求自己从哪里进来」是可靠答案。
    web 容器的 nginx 已经把 ``X-Forwarded-Host`` 写成包含端口的原值、
    ``X-Forwarded-Proto`` 写成对外协议；本地 dev 由 vite proxy 补齐同一个头。
    只取第一个值（多级代理时链路最外侧那个才是用户看到的）。
    """

    headers = request.headers

    def _first(name: str) -> str:
        return str((headers.get(name) or "").split(",")[0]).strip()

    host = _first("x-forwarded-host") or str(headers.get("host") or "").strip()
    if not host:
        return ""
    scheme = _first("x-forwarded-proto") or request.url.scheme
    if scheme not in {"http", "https"}:
        scheme = "http"
    return f"{scheme}://{host}"


# [aipdd fork delta] WP-E: 模型广场——只读目录，四个 connector 全量收录本项目可配置、
# 能被调用的模型（模型网关 / comfyui 本地工作流 / 火山 MediaKit / 环境变量位；
# 原 audio 语音合成与 official-catalog 官方目录两个来源已整体移除）。不含任何
# 计费字段；未配置的条目照常返回并标 noChannel。整组路由已在 router 上挂登录闸门。
@router.get("/model-plaza")
async def get_model_plaza(request: Request, connector: str = "") -> dict[str, Any]:
    wanted = str(connector or "").strip().lower()
    if wanted and wanted not in PLAZA_CONNECTORS:
        raise HTTPException(status_code=400, detail=f"unknown connector: {wanted}")
    # 卡片上的对外地址按本次请求的 origin 推导；``asyncio.to_thread`` 会复制当前
    # contextvar 上下文，所以线程内读到的就是这里设的值。
    set_request_origin(_browser_origin(request))
    data = await asyncio.to_thread(build_model_plaza, connector=wanted)
    return {"ok": True, "data": data}


# 单次最小探活：只有 OpenAI 兼容同步端点会真发请求（max_tokens=1 / input="hi"），
# comfyui / MediaKit / 异步视频 / TTS 一律 skipped——探活不能产生真实任务与费用。
@router.post("/model-plaza/{model_id}/test")
async def post_model_plaza_test(model_id: str) -> dict[str, Any]:
    entry = await asyncio.to_thread(find_plaza_model, model_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"unknown model: {model_id}")
    result = await probe_plaza_model(entry)
    return {"ok": True, "data": {"modelId": model_id, **result}}