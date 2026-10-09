"""Embedding 模型路由：写入渠道并把维度配置设为默认。

[重构] 自原 ``model_gateway.py`` 物理拆分而来（纯移动，行为与 URL 契约不变）。
patch 敏感依赖统一经包命名空间 ``_gateway`` 解析，保持 tests 对
``monkeypatch.setattr(model_gateway, "<name>", ...)`` 的拦截契约。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from novelvideo.api.routes import model_gateway as _gateway

from ._common import (
    SaveEmbeddingModelBody,
    _build_channel_payload_from_spec,
    _build_embedding_model_channel_spec,
    _get_provisioner_config_from_request,
    _mask_sent_channel_payload,
    _permission_error,
    require_ce_gateway_management,
)

router = APIRouter()


@router.post("/custom/newapi/embedding-model")
async def save_custom_newapi_embedding_model(
    body: SaveEmbeddingModelBody,
) -> dict[str, Any]:
    try:
        require_ce_gateway_management()
        spec, normalized_model = _build_embedding_model_channel_spec(body)
        cfg = _get_provisioner_config_from_request(body.new_api_base_url, body.database)
        admin = _gateway.ensure_admin_access_token(cfg)
        payload = _build_channel_payload_from_spec(spec)
        result = _gateway.upsert_channel(cfg, admin, payload)
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    item: dict[str, Any] = {
        "provider": spec.provider,
        "ok": result["ok"],
        "httpStatus": result["httpStatus"],
        "newApiResponse": result["newApiResponse"],
        "action": result.get("action"),
        "channelId": result.get("channelId"),
        "sentPayload": _mask_sent_channel_payload(result.get("sentPayload") or payload),
    }
    if not result["ok"]:
        response = result.get("newApiResponse")
        message = ""
        if isinstance(response, dict):
            message = str(
                response.get("message") or response.get("error") or ""
            ).strip()
        item["error"] = message or "NewAPI rejected embedding model channel update"
        return {
            "ok": False,
            "data": {
                "newApiAdminBaseUrl": cfg.admin_base_url,
                "embeddingModel": {},
                "result": item,
            },
        }

    saved = _gateway.save_newapi_embedding_model_config(
        provider=normalized_model["provider"],
        upstream_model=normalized_model["upstreamModel"],
        dimension=normalized_model["dimension"],
        batch_size=normalized_model.get("batchSize"),
        send_dimensions=normalized_model["sendDimensions"],
    )
    return {
        "ok": True,
        "data": {
            "newApiAdminBaseUrl": cfg.admin_base_url,
            "embeddingModel": saved,
            "result": item,
        },
    }