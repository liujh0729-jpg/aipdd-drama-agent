"""媒体模型（media models）路由：媒体模型渠道写入与本地映射单独保存。

[重构] 自原 ``model_gateway.py`` 物理拆分而来（纯移动，行为与 URL 契约不变）。
patch 敏感依赖统一经包命名空间 ``_gateway`` 解析，保持 tests 对
``monkeypatch.setattr(model_gateway, "<name>", ...)`` 的拦截契约。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from novelvideo.api.routes import model_gateway as _gateway
from novelvideo.media_model_request_schema import validate_media_model_catalog_config
from novelvideo.model_gateway_settings import get_newapi_provider_channels

from ._common import (
    ChannelSpec,
    NewApiDatabaseBody,
    _build_channel_payload_from_spec,
    _get_provisioner_config_from_request,
    _mask_sent_channel_payload,
    _permission_error,
    _project_provider_channel_mappings,
    require_ce_gateway_management,
)

router = APIRouter()

OFFICIAL_ONLY_MEDIA_MODEL_NAMES = {
    "seedance-2.0-value",
    "seedance-2.0-fast-value",
}


def _guessed_media_type(model: str) -> str:
    """面板没传 mediaType 时的名字归类：走分类真源，不在这里复制关键词表。"""

    from novelvideo.media_model_discovery import guess_media_type

    try:
        return str(guess_media_type(model) or "").strip().lower()
    except Exception:  # noqa: BLE001 - 真源不可用时回到旧的 video 兜底，不阻断保存
        return ""


class MediaModelConfigBody(BaseModel):
    provider: str
    upstream_model: str | None = Field(default=None, alias="upstreamModel")
    media_type: str | None = Field(default=None, alias="mediaType")
    label: str | None = None
    enabled: bool = True
    sort_order: int = Field(default=100, alias="sortOrder")
    config: dict[str, Any] = Field(default_factory=dict)


class SaveMediaModelsBody(BaseModel):
    new_api_base_url: str | None = Field(default=None, alias="newApiBaseUrl")
    database: NewApiDatabaseBody | None = None
    models: dict[str, MediaModelConfigBody] = Field(default_factory=dict)
    # 落库后是否立即把真源编译成 model_mapping 投影到网关渠道（best-effort）。
    sync: bool = True


def _build_media_model_channel_specs(
    models: dict[str, MediaModelConfigBody],
) -> tuple[list[ChannelSpec], dict[str, dict[str, Any]]]:
    if not models:
        raise ValueError("models must be a non-empty JSON object")

    grouped: dict[str, dict[str, str]] = {}
    normalized: dict[str, dict[str, Any]] = {}
    for raw_model, item in models.items():
        model = str(raw_model or "").strip()
        if not model:
            raise ValueError("models contains an empty model name")
        if model in OFFICIAL_ONLY_MEDIA_MODEL_NAMES:
            raise ValueError(f"media model {model} is official-channel only")
        provider = str(item.provider or "").strip().lower()
        if not provider:
            raise ValueError(f"provider is required for media model {model}")
        upstream_model = (item.upstream_model or "").strip() or model
        media_type = str(item.media_type or "").strip().lower()
        if not media_type:
            # [aipdd fork delta] 优先由面板显式传入 mediaType；兜底按已知厂商别名
            # + 通用命名特征归类。LingShan-G2 / LingShan-NB-2 是上游发行方的图片
            # 模型别名，与上游契约保持一致：丢了这两个特判，面板/CLI 不带类型
            # 保存时会把图片模型存成 video，然后在画布视频下拉里出现。
            lowered = model.lower()
            if (
                model in {"LingShan-G2", "LingShan-NB-2"}
                or lowered.startswith(("seedream-", "doubao-seedream"))
                or "image" in lowered
                or "nanobanana" in lowered
            ):
                media_type = "image"
            elif model in {"index-tts-2", "LingShan-MU-11"}:
                media_type = "audio"
            elif _guessed_media_type(model) == "music":
                # [aipdd fork delta req46] 音乐判定不在这里再抄一份关键词表：
                # 分类真源（media_model_discovery）一侧改了，这里必须跟着变，
                # 否则会出现「设置页归音乐、后端存成 video」的双口径。其余类型
                # 保留旧的写入行为（没特征就归 video），不扩大变更面。
                media_type = "music"
            else:
                media_type = "video"
        # [aipdd fork delta req46] music 不经网关（厂商直连 music_generation），因此
        # 不像 image/video 那样断言 config.request.endpoint —— 给它套端点只会把
        # 「设默认」卡在后端 400 上。
        if media_type not in {"image", "video", "audio", "music"}:
            raise ValueError(f"invalid mediaType for media model {model}")
        model_config = dict(item.config)
        if media_type in {"image", "video"}:
            model_config.setdefault(
                "request",
                {
                    "endpoint": (
                        "images/generations"
                        if media_type == "image"
                        else "video/generations"
                    ),
                    "parameters": [],
                },
            )
            validate_media_model_catalog_config(model_config, media_type)
        grouped.setdefault(provider, {})[model] = upstream_model
        normalized[model] = {
            "provider": provider,
            "upstreamModel": "" if upstream_model == model else upstream_model,
            "mediaType": media_type,
            "label": str(item.label or model).strip() or model,
            "enabled": bool(item.enabled),
            "sortOrder": int(item.sort_order),
            "config": model_config,
        }

    specs = [
        ChannelSpec(
            provider=provider,
            type=63 if provider == "comfyui" else None,
            modelMapping=mapping,
        )
        for provider, mapping in grouped.items()
    ]
    return specs, normalized


@router.post("/custom/newapi/media-models")
async def save_custom_newapi_media_models(body: SaveMediaModelsBody) -> dict[str, Any]:
    try:
        require_ce_gateway_management()
        specs, normalized_models = _build_media_model_channel_specs(body.models)
        cfg = _get_provisioner_config_from_request(body.new_api_base_url, body.database)
        admin = _gateway.ensure_admin_access_token(cfg)
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    results: list[dict[str, Any]] = []
    for index, channel in enumerate(specs):
        try:
            payload = _build_channel_payload_from_spec(channel)
            result = _gateway.upsert_channel(cfg, admin, payload)
            item: dict[str, Any] = {
                "index": index,
                "provider": channel.provider,
                "ok": result["ok"],
                "httpStatus": result["httpStatus"],
                "newApiResponse": result["newApiResponse"],
                "action": result.get("action"),
                "channelId": result.get("channelId"),
                "sentPayload": _mask_sent_channel_payload(
                    result.get("sentPayload") or payload
                ),
            }
            if not result["ok"]:
                response = result.get("newApiResponse")
                message = ""
                if isinstance(response, dict):
                    message = str(
                        response.get("message") or response.get("error") or ""
                    ).strip()
                item["error"] = message or "NewAPI rejected media model channel update"
            results.append(item)
        except Exception as exc:
            results.append(
                {
                    "index": index,
                    "provider": channel.provider,
                    "ok": False,
                    "error": str(exc),
                }
            )

    succeeded = sum(1 for item in results if item["ok"])
    failed = len(results) - succeeded
    if failed == 0:
        _gateway.save_newapi_media_model_mappings(normalized_models)

    return {
        "ok": failed == 0,
        "data": {
            "newApiAdminBaseUrl": cfg.admin_base_url,
            "total": len(results),
            "succeeded": succeeded,
            "failed": failed,
            "models": normalized_models if failed == 0 else {},
            "results": results,
        },
    }


# [aipdd fork delta] 仅写入本地媒体模型映射(mediaModels：mediaType/sortOrder/enabled)，
# 不重复 upsert NewAPI 渠道。供「默认生成模型」面板一键设默认 / 归类使用，
# 避免 /media-models 整批渠道 upsert 中任一模型失败就导致默认不生效。
# [aipdd fork delta] 真源写入后 best-effort 重投影受影响渠道：本接口不 upsert
# 渠道的历史行为会让官方渠道的直通清单停留在旧态；聚合渠道（上游清单镜像官方
# 目录）的下一次快照直通会以「后写者独占」把模型抢注过去（ap-indextts-2 被搬
# 到 DC-openai 的根因）。投影失败不回滚真源、不阻断设默认。
@router.post("/custom/newapi/media-model-mappings")
async def save_custom_newapi_media_model_mappings(
    body: SaveMediaModelsBody,
) -> dict[str, Any]:
    try:
        require_ce_gateway_management()
        _, normalized = _build_media_model_channel_specs(body.models)
        saved = _gateway.save_newapi_media_model_mappings(normalized)
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    projection: list[dict[str, Any]] = []
    projection_error = ""
    if body.sync:
        try:
            cfg = _get_provisioner_config_from_request(
                body.new_api_base_url, body.database
            )
            admin = _gateway.ensure_admin_access_token(cfg)
            touched = {
                str(item.get("provider") or "").strip().lower()
                for item in saved.values()
            }
            known = {channel["provider"] for channel in get_newapi_provider_channels()}
            targets = sorted((touched | known) - {""})
            projection = await asyncio.to_thread(
                lambda: _project_provider_channel_mappings(cfg, admin, targets)
            )
        except Exception as exc:  # noqa: BLE001 - 投影失败不回滚真源
            projection_error = str(exc)

    return {
        "ok": True,
        "data": {
            "models": saved,
            "projection": projection,
            "projectionFailed": [item for item in projection if not item.get("ok")],
            "projectionError": projection_error,
        },
    }