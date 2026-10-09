"""本地 ComfyUI 渠道与工作流解析路由。

[重构] 自原 ``model_gateway.py`` 物理拆分而来（纯移动，行为与 URL 契约不变）。
patch 敏感依赖统一经包命名空间 ``_gateway`` 解析，保持 tests 对
``monkeypatch.setattr(model_gateway, "<name>", ...)`` 的拦截契约。
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from novelvideo.api.routes import model_gateway as _gateway
from novelvideo.comfyui_adaptation import CAPABILITY_KEYS
from novelvideo.comfyui_workflow import resolve_comfyui_workflows
from novelvideo.model_gateway_settings import (
    get_newapi_media_model_mappings,
    get_newapi_provider_channels,
)

from ._common import _permission_error, require_ce_gateway_management

router = APIRouter()

COMFY_WORKFLOW_MANAGED_CONFIG_KEY = "_dcManagedByWorkflow"


def _default_comfyui_media_model_config(
    model: str,
    *,
    workflow_ids: list[str] | None = None,
    media_type: str = "video",
) -> dict[str, Any]:
    if str(media_type or "video").strip().lower() == "image":
        # [aipdd fork delta] comfyui 图片形态：走 images/generations，模式取
        # text_to_image/image_to_image，无视频时长档位（不写死具体模型）。
        return {
            "request": {"endpoint": "images/generations", "parameters": []},
            "resolutionOptions": ["1K", "2K"],
            "ratioOptions": ["1:1", "16:9", "9:16", "4:3", "3:4"],
            "supportedModes": ["text_to_image", "image_to_image"],
            "referenceImageMax": 4,
            COMFY_WORKFLOW_MANAGED_CONFIG_KEY: True,
        }
    route_tokens = {
        token
        for value in (workflow_ids or [model])
        for token in str(value or "").strip().lower().replace("-", "_").split("_")
    }
    supported_modes: list[str] = []
    reference_limits: dict[str, int | bool] = {}
    is_minimax_h3_local = model.strip().lower() == "minimax-h3-local"
    resolution_options = (
        ["480p", "768p", "1080p"]
        if is_minimax_h3_local
        else ["480p", "640p"]
    )
    ratio_options = (
        ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"]
        if is_minimax_h3_local
        else ["16:9", "1:1"]
        if "i2v" in route_tokens
        else ["1:1", "16:9"]
    )
    if "t2v" in route_tokens or not route_tokens.intersection({"i2v", "r2v"}):
        supported_modes.append("text_to_video")
    if "i2v" in route_tokens:
        supported_modes.extend(
            ["first_frame"]
            if is_minimax_h3_local
            else ["image_to_video", "image_reference"]
        )
        reference_limits["referenceImageMax"] = 1
    if "r2v" in route_tokens:
        supported_modes.append("all_reference")
        reference_limits = {
            "referenceImageMax": 9,
            "referenceVideoMax": 3,
            "referenceAudioMax": 3,
        }
    return {
        "request": {"endpoint": "video/generations", "parameters": []},
        "resolutionOptions": resolution_options,
        "ratioOptions": ratio_options,
        "minDuration": 4,
        "maxDuration": 15,
        "supportedModes": supported_modes,
        **reference_limits,
        COMFY_WORKFLOW_MANAGED_CONFIG_KEY: True,
    }


def _comfyui_media_model_config(
    model: str,
    previous: dict[str, Any] | None,
    *,
    workflow_ids: list[str] | None = None,
    media_type: str = "video",
    declared: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把该 ComfyUI 模型的媒体配置收敛成一份可信声明。

    ``declared`` 是「AI分析并自动配置」回写的真实能力（见
    ``comfyui_adaptation.CAPABILITY_KEYS``）。合并规则：

    * 上一版还只是我们按名字 token 猜出来的（``current`` 只有 request / 托管标记）
      → 声明直接覆盖猜测，并抹掉 ``_dcManagedByWorkflow`` 标记：此后这份配置是
      「分析出来的」，不会被下一次保存重新猜掉；
    * ``current`` 是用户自己编过的 → 只声明 ``current`` 里**缺失**的键，绝不把用户
      在模型广场手动维护的档位改成 AI 的猜测。
    """

    current = previous if isinstance(previous, dict) else {}
    declaration: dict[str, Any] = {}
    for key, value in (declared or {}).items():
        if key not in CAPABILITY_KEYS or key == "mediaType":
            continue
        if value in (None, "", [], {}, 0):
            continue
        declaration[key] = value
    # Older channel saves created workflow models with only a request block.
    # Backfill those records, while leaving any user-authored capabilities intact.
    if set(current).issubset({"request", COMFY_WORKFLOW_MANAGED_CONFIG_KEY}):
        merged = {
            **_default_comfyui_media_model_config(
                model,
                workflow_ids=workflow_ids,
                media_type=media_type,
            ),
            **current,
        }
        if declaration:
            merged.update(declaration)
            merged.pop(COMFY_WORKFLOW_MANAGED_CONFIG_KEY, None)
        return merged
    if not declaration:
        return current
    return {**current, **{k: v for k, v in declaration.items() if k not in current}}


# [aipdd fork delta] 本地 ComfyUI「只填模型名 + 工作流名称」的解析请求体。
class ComfyUIWorkflowItem(BaseModel):
    model: str
    workflow_name: str = Field(alias="workflowName")


class ResolveComfyUIWorkflowsBody(BaseModel):
    base_url: str = Field(alias="baseUrl")
    items: list[ComfyUIWorkflowItem] = Field(default_factory=list)


@router.delete("/custom/newapi/comfyui")
async def clear_custom_newapi_comfyui() -> dict[str, Any]:
    try:
        require_ce_gateway_management()
        cfg = _gateway.get_provisioner_config()
        admin = _gateway.ensure_admin_access_token(cfg)
        deleted = _gateway.delete_channel_by_name(
            cfg,
            admin,
            name="DC-comfyui",
            channel_type=63,
        )
        channels = _gateway.save_newapi_provider_channels(
            [
                channel
                for channel in get_newapi_provider_channels()
                if channel["provider"] != "comfyui"
            ]
        )
        mappings = _gateway.save_newapi_media_model_mappings(
            {
                model: mapping
                for model, mapping in get_newapi_media_model_mappings().items()
                if mapping.get("provider") != "comfyui"
            }
        )
        return {
            "ok": True,
            "data": {
                "channelDeleted": deleted,
                "channels": channels,
                "mediaModels": mappings,
            },
        }
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# [aipdd fork delta] 本地 ComfyUI「只填模型名 + 工作流名称」：后端连上用户的
# ComfyUI，按名称取回浏览器格式工作流并在服务端转换为 API 格式。返回
# workflowByModel（model -> API 格式 JSON）与逐项 errors，供前端确认后写入渠道。
@router.post("/custom/newapi/comfyui/resolve")
async def resolve_custom_newapi_comfyui_workflows(
    body: ResolveComfyUIWorkflowsBody,
) -> dict[str, Any]:
    base_url = str(body.base_url or "").strip().rstrip("/")
    items = [
        {"model": it.model, "workflowName": it.workflow_name}
        for it in body.items
    ]
    if not base_url:
        raise HTTPException(status_code=400, detail="baseUrl is required")
    if not items:
        raise HTTPException(status_code=400, detail="items is required")
    workflow_by_model: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    try:
        require_ce_gateway_management()
        workflow_by_model, errors = await resolve_comfyui_workflows(base_url, items)
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"ComfyUI 返回 {exc.response.status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"连接 ComfyUI 失败：{exc}"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "ok": True,
        "data": {
            "baseUrl": base_url,
            "workflowByModel": workflow_by_model,
            "resolved": sorted(workflow_by_model.keys()),
            "errors": errors,
        },
    }


# [aipdd fork delta] 「AI分析并自动配置」：在本地 ComfyUI 地址上按名称取回工作流，
# 交给已配置的文本模型分析出「节点绑定 + 真实能力」，落一份适配文件（state 里），
# 返回渠道可直接保的指针表与能力声明。分析失败逐模型上报，不静默产出猜测。
class ComfyUIAdaptItem(BaseModel):
    model: str
    workflow_name: str = Field(alias="workflowName")


class AdaptComfyUIWorkflowsBody(BaseModel):
    base_url: str = Field(alias="baseUrl")
    items: list[ComfyUIAdaptItem] = Field(default_factory=list)
    # 卡片里显式选的「分析模型」；没选则后端回落文本功能位三层解析。
    text_model: str = Field(default="", alias="textModel")


@router.post("/custom/newapi/comfyui/ai-adapt")
async def adapt_custom_newapi_comfyui_workflows(
    body: AdaptComfyUIWorkflowsBody,
) -> dict[str, Any]:
    from novelvideo import comfyui_adaptation
    from novelvideo.model_slots import ModelSlotNotConfigured

    base_url = str(body.base_url or "").strip().rstrip("/")
    items = [
        {"model": it.model, "workflowName": it.workflow_name}
        for it in body.items
    ]
    if not base_url:
        raise HTTPException(status_code=400, detail="baseUrl is required")
    if not items:
        raise HTTPException(status_code=400, detail="items is required")

    try:
        require_ce_gateway_management()
    except PermissionError as exc:
        raise _permission_error(exc) from exc

    try:
        text_model = comfyui_adaptation.resolve_analyzer_model(body.text_model)
    except ModelSlotNotConfigured as exc:
        # 文本功能位未绑定：把「去设置里选一个分析模型」的原话回 400，
        # 不能交给下面的兼容分支当成 500。
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        workflow_by_model, errors = await resolve_comfyui_workflows(base_url, items)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"ComfyUI 返回 {exc.response.status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"连接 ComfyUI 失败：{exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    workflow_name_by_model = {
        str(it["model"]): str(it["workflowName"]) for it in items
    }
    entries: dict[str, Any] = {}
    capabilities_by_model: dict[str, Any] = {}
    for model, workflow in workflow_by_model.items():
        try:
            outcome = await comfyui_adaptation.analyze_one(
                model=model,
                workflow_name=workflow_name_by_model.get(model, ""),
                workflow=workflow,
                text_model=text_model,
            )
        except (comfyui_adaptation.ComfyUIAdaptationError, httpx.HTTPError) as exc:
            errors.append(
                {
                    "model": model,
                    "workflowName": workflow_name_by_model.get(model, ""),
                    "error": str(exc),
                }
            )
            continue
        entries[model] = outcome["entry"]
        capabilities_by_model[model] = outcome["entry"]["capabilities"]
        for message in outcome["errors"]:
            errors.append(
                {
                    "model": model,
                    "workflowName": workflow_name_by_model.get(model, ""),
                    "error": message,
                }
            )

    # 只有拿到可用绑定（有输出节点）的才落盘，避免把一条残缺适配当成真源。
    usable = {
        model: entry
        for model, entry in entries.items()
        if isinstance(entry.get("binding"), dict) and entry["binding"].get("outputs")
    }
    written = comfyui_adaptation.write_adaptations(base_url, usable) if usable else {
        "file": "",
        "pointers": {},
    }
    capabilities_by_model = {
        model: capabilities_by_model.get(model) or {}
        for model in usable
    }

    return {
        "ok": True,
        "data": {
            "baseUrl": base_url,
            "analyzerModel": text_model,
            "workflowByModel": {
                model: workflow_by_model[model] for model in usable
            },
            "workflowNameByModel": {
                model: entries[model]["workflowName"] for model in usable
            },
            "typeByModel": {model: entries[model]["mediaType"] for model in usable},
            "adaptationFile": written["file"],
            "adaptationPointers": written["pointers"],
            "capabilitiesByModel": capabilities_by_model,
            "adapted": sorted(usable.keys()),
            "errors": errors,
        },
    }