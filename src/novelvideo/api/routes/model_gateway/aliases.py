"""别名映射（alias-mappings）路由：单一真源读写、投影与一致性自检。

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
from novelvideo.feature_provisioner import (
    FeatureProvisionError,
    provision_feature_routes,
)
from novelvideo.model_gateway_settings import (
    build_channel_registrations,
    get_newapi_provider_channels,
    save_newapi_model_alias_mappings,
)
from novelvideo.newapi_provisioner import (
    AliasTargetValidationError,
    build_alias_mapping_health,
    peek_alias_mapping_health,
    reset_alias_mapping_health_cache,
    validate_alias_targets,
)

from ._common import (
    NewApiDatabaseBody,
    _get_provisioner_config_from_request,
    _permission_error,
    _project_provider_channel_mappings,
    require_ce_gateway_management,
)

router = APIRouter()


# [aipdd fork delta] WP-A: 模型别名映射单一真源的写入体。兼容两种写法：
#   {"DC-x-LLM": {"provider": "aipdd", "target": "glm-4.6"}}  或  {"DC-x-LLM": "glm-4.6"}
# 后者需带 defaultProvider（或条目里自己声明 provider）。
class SaveAliasMappingsBody(BaseModel):
    new_api_base_url: str | None = Field(default=None, alias="newApiBaseUrl")
    database: NewApiDatabaseBody | None = None
    mappings: dict[str, Any] = Field(default_factory=dict)
    merge: bool = True
    default_provider: str | None = Field(default=None, alias="defaultProvider")
    # [aipdd fork delta] WP-B: 整批能力归属（text|vision）。视觉位写入纯文本模型
    # 会在上游报「不支持图片能力」，这里反向拦住。
    capability: str | None = Field(default=None, alias="capability")
    # 关闭时退回 WP-A 行为（只校格式），供上游清单不可用的部署使用。
    # [aipdd fork delta] 用 alias 暴露 JSON 键：直接叫 ``validate`` 会遮蔽 pydantic
    # BaseModel 的同名属性，import 期就吐 UserWarning。
    validate_targets: bool = Field(default=True, alias="validate")
    # 落库后是否立即把真源编译成 model_mapping 投影到网关渠道。
    sync: bool = True


# [aipdd fork delta] WP-A: 投影实现已迁至共享层 `_common._project_provider_channel_mappings`
# （alias-mappings 与 media-model-mappings 两条保存路径共用，防抢注修复的一部分）。


# [aipdd fork delta] WP-A: 别名映射单一真源。GET 回真源 + 当前编译出的投影；
# POST 先落自有存储（可选 merge），随后把所有已配置渠道重投影一次，保证删除
# 别名时网关侧也会跟着收敛。
# [aipdd fork delta] WP-B: 写入前先按上游真实清单校验目标存在性与能力（拉不到就
# 降级只检格式，不阻断设默认）。
def _alias_entries_for_validation(
    mappings: dict[str, Any],
    *,
    default_provider: str,
    default_capability: str,
) -> dict[str, dict[str, str]]:
    provider_default = str(default_provider or "").strip().lower()
    capability_default = str(default_capability or "").strip().lower()
    entries: dict[str, dict[str, str]] = {}
    for alias, item in (mappings or {}).items():
        alias_name = str(alias or "").strip()
        if not alias_name:
            continue
        if isinstance(item, str):
            provider = provider_default
            target = item.strip()
            capability = capability_default
        elif isinstance(item, dict):
            provider = str(item.get("provider") or provider_default).strip().lower()
            target = str(item.get("target") or "").strip()
            capability = (
                str(item.get("capability") or "").strip().lower() or capability_default
            )
        else:
            continue
        if not provider or not target:
            continue
        entries[alias_name] = {
            "provider": provider,
            "target": target,
            "capability": capability,
        }
    return entries


@router.get("/custom/newapi/alias-mappings")
async def get_custom_newapi_alias_mappings() -> dict[str, Any]:
    try:
        require_ce_gateway_management()
        mappings = _gateway.get_newapi_model_alias_mappings()
        # [aipdd fork delta] 与投影路径保持同一语义（零远程）：每个渠道的注册表 =
        # 内部绑定裸名 + 全量复合名 + 单渠道唯一裸名（build_channel_registrations）。
        projection: dict[str, dict[str, str]] = {}
        for channel in get_newapi_provider_channels():
            projection[channel["provider"]] = build_channel_registrations(
                channel["provider"]
            )
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    return {
        "ok": True,
        "data": {
            "source": "project",
            "mappings": mappings,
            "projection": projection,
            "health": peek_alias_mapping_health(),
        },
    }


# [aipdd fork delta] WP-B: 手动/启动期一致性自检。force=true 才重新拉上游清单；
# 只告警不阻断，返回体里三类问题各自带候选模型。
@router.get("/custom/newapi/alias-mappings/health")
async def get_custom_newapi_alias_mappings_health(
    refresh: bool = False,
) -> dict[str, Any]:
    try:
        require_ce_gateway_management()
        health = await asyncio.to_thread(build_alias_mapping_health, force=bool(refresh))
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    return {"ok": True, "data": health}


@router.post("/custom/newapi/alias-mappings")
async def save_custom_newapi_alias_mappings(
    body: SaveAliasMappingsBody,
) -> dict[str, Any]:
    try:
        require_ce_gateway_management()
        if body.validate_targets:
            await asyncio.to_thread(
                validate_alias_targets,
                _alias_entries_for_validation(
                    body.mappings,
                    default_provider=body.default_provider or "",
                    default_capability=body.capability or "",
                ),
            )
        saved = save_newapi_model_alias_mappings(
            body.mappings,
            merge=body.merge,
            default_provider=body.default_provider or "",
            default_capability=body.capability or "",
        )
        # [aipdd fork delta] WP-B: 真源变了，上一轮自检结果作废（下次读时重算）。
        reset_alias_mapping_health_cache()
        projection: list[dict[str, Any]] = []
        if body.sync:
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
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except AliasTargetValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "ok": True,
        "data": {
            "source": "project",
            "mappings": saved,
            "projection": projection,
            "projectionFailed": [item for item in projection if not item.get("ok")],
        },
    }


# --------------------------------------------------------------------------- #
# [aipdd fork delta] 一键配齐：一次选择 → 整链要用的全部功能位绑定 + 媒体默认。
# --------------------------------------------------------------------------- #


class ProvisionFeatureRoutesBody(BaseModel):
    """运行前收集的三个模型（全部可选；空 = 那一类不动）。

    调用名的形状与设置页一致：文本 / 视觉用 ``{provider}::{model}`` 复合名（来自
    ``/model-gateway/text-models`` 与 ``/vision-models``），图片 / 视频用「默认生成模型」
    目录条目 id（来自 ``/freezone/{image,video}/models``）。
    """

    text_model: str | None = Field(default=None, alias="textModel")
    vision_model: str | None = Field(default=None, alias="visionModel")
    image_model: str | None = Field(default=None, alias="imageModel")
    video_model: str | None = Field(default=None, alias="videoModel")
    new_api_base_url: str | None = Field(default=None, alias="newApiBaseUrl")
    database: NewApiDatabaseBody | None = None
    # 写完是否立即把真源投影到网关渠道（与 alias-mappings 同一语义）。
    sync: bool = True


@router.post("/custom/newapi/feature-routes/provision")
async def provision_custom_newapi_feature_routes(
    body: ProvisionFeatureRoutesBody,
) -> dict[str, Any]:
    """把「这一部剧用哪几个模型」铺开成整链需要的全部绑定，并把真源投影到网关。

    为什么这个动作要存在：整链（故事 / 剧本 / 资产 / 分镜 / 风格）会逐位踩过解析链，
    出厂状态是那 22 个文本位 + 4 个视觉位全空 —— 只靠运行期兜底接住，用户永远看不见
    自己在用什么，也永远有人撞上「未绑定可用模型」。这里让用户在创建处选一次，随后
    每个功能位都有明确归属，广场与状态回显读的是同一份真源。

    「挑不到」不是错误：没选的类保持原样，向量一类的只回提示（维度不容代猜），名字不
    在渠道目录里才是 400 —— 那正是本接口要消灭的那条 503。
    """

    try:
        require_ce_gateway_management()
        result = await asyncio.to_thread(
            provision_feature_routes,
            text_model=body.text_model or "",
            vision_model=body.vision_model or "",
            image_model=body.image_model or "",
            video_model=body.video_model or "",
        )
        # 真源变了，上一轮自检结果作废（与 alias-mappings 保存同一处理）。
        reset_alias_mapping_health_cache()
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except FeatureProvisionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - 与其他网关端点同一收口：不把栈抛给用户
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # 投影是 best-effort（与 /media-model-mappings 同一语义）：真源已经落对了，网关那一侧
    # 同步失败不该把这次配齐报成失败 —— 否则前端会把整链白中止一次，用户看到的还是
    # 「配齐也报错」，而那两位其实已经指好了。
    projection: list[dict[str, Any]] = []
    projection_error = ""
    if body.sync:
        try:
            cfg = _get_provisioner_config_from_request(
                body.new_api_base_url, body.database
            )
            admin = _gateway.ensure_admin_access_token(cfg)
            touched = {
                str(item or "").strip().lower() for item in result.get("touched") or []
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
            **{key: result.get(key) for key in ("text", "vision", "image", "video")},
            "hints": result.get("hints") or [],
            "touched": result.get("touched") or [],
            "projection": projection,
            "projectionFailed": [item for item in projection if not item.get("ok")],
            "projectionError": projection_error,
        },
    }