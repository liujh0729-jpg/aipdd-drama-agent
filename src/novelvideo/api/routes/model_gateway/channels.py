"""自建渠道（LocalAPI channels）管理路由：渠道增改、同步、上游模型与批量创建。

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
from novelvideo.model_gateway_settings import (
    MEDIAKIT_DEFAULT_BASE_URL,
    comfyui_declared_capabilities,
    detect_comfyui_media_type,
    get_newapi_embedding_model_config,
    get_newapi_media_model_mappings,
    is_masked_secret,
    parse_comfyui_channel_workflows,
    record_newapi_model_alias_mappings,
)
from novelvideo.newapi_provisioner import (
    build_channel_payload,
    list_channel_types,
    list_upstream_catalog,
    mask_token,
    resolve_models_endpoint as _resolve_models_endpoint,
)

from ._common import (
    ChannelSpec,
    CreateChannelBody,
    NewApiDatabaseBody,
    _refresh_provider_catalogs,
    _build_channel_payload_from_spec,
    _get_provisioner_config_from_request,
    _mask_sent_channel_payload,
    _permission_error,
    require_ce_gateway_management,
)
from .comfyui import _comfyui_media_model_config

router = APIRouter()

# [aipdd fork delta] 渠道类型修正表（fork 事实优先，整体覆盖镜像注册项；镜像表
# 没有的 fork 自有渠道则在列表末尾补合成行，保证「渠道管理」目录与已配置渠道
# 一一对应）：
#   newapi：镜像注册表里是 type 60 的「New API 上游适配器」（能力仅 text），但本
#   fork 的外部 NewAPI 渠道（provider key 已从 openai 重命名为 newapi）实际走
#   OpenAI 兼容适配器（type 1）。若不覆盖，前端 type 解析链（channelTypeByProvider
#   按注册表解析）会把 newapi 解析成 60 并把错误 type 存进渠道真源，渠道能力筛选
#   也会因只有 text 而丢 vision/媒体候选。此处整体覆盖为 type 1 + NewAPI 展示名 +
#   全能力，屏蔽镜像内置的 type 60。
#   aipdd：镜像注册表没有该类型，渠道目录里会凭空缺席（已配置的 AIPDD 渠道在
#   「渠道管理」里看不到、状态无法对应）。按 fork 事实合成 type 1 + AIPDD 展示名 +
#   全能力 + 预填 base 地址，与 PROVIDER_PRESETS / 连接卡保持一致。
#   [aipdd fork delta req41 分类] 这里的 base 地址**可以预填**：AIPDD 是公共厂商渠道，
#   渠道定了端点就定了（同 volcengine / mediakit）。只有 newapi 那一行必须留空：外部
#   NewAPI 网关有无数个，地址属于部署方自己的数据。
#   mediakit：镜像注册表同样没有该类型。火山 AI MediaKit（视频高清 / 智能去字幕）
#   本质归属 VolcEngine 渠道（同为火山引擎），只是 Base URL / API Key 各自一份；
#   它的凭证真源在 video-enhance 配置（不进 LocalAPI 渠道表），但「供应商渠道管理」
#   目录需要它出现并与渠道连接卡状态一一对应，否则已配置的 MediaKit 渠道凭空缺席。
#   合成行仅作目录 / 状态展示：前端对 mediakit 的「添加」引导去 VolcEngine 卡内的
#   火山 AI MediaKit 子区块，「移除」清除 video-enhance 配置，不写渠道真源。
_FORK_CHANNEL_TYPE_OVERRIDES: dict[str, dict[str, Any]] = {
    "newapi": {
        "type": 1,
        "name": "NewAPI",
        "defaultBaseUrl": "",
        # 没有预设地址的渠道必须由用户自己填（前端据此把「通常不填」换成必填提示）：
        # 只写 defaultBaseUrl 为空是不够的，两者语义不同——空预设不等于无需填写。
        "requiresBaseUrl": True,
        "capabilities": ["text", "vision", "embedding", "image", "video", "audio"],
    },
    "aipdd": {
        "type": 1,
        "name": "AIPDD",
        "defaultBaseUrl": "https://api.aipdd.work/v1",
        "capabilities": ["text", "vision", "embedding", "image", "video", "audio"],
        "requiresBaseUrl": True,
        "supportsBaseUrlOverride": True,
    },
    "mediakit": {
        "type": 1,
        "name": "火山 AI MediaKit",
        "description": "火山 AI MediaKit（归属 VolcEngine 渠道）：视频高清（生成式超分）与智能去字幕",
        "defaultBaseUrl": MEDIAKIT_DEFAULT_BASE_URL,
        "capabilities": ["video"],
        "requiresBaseUrl": True,
        "supportsBaseUrlOverride": True,
    },
    # [aipdd fork delta req46] MiniMax 两条渠道（海外站 minimax / 国内站 minimax_cn）：
    # 镜像注册表里的 minimax 是 type 35 的厂商私有适配器（能力仅 text），本 fork 的
    # 文本走 OpenAI 兼容 `/v1/chat/completions`、模型清单靠 `{base}/v1/models` 自动
    # 发现，因此整体覆盖为 type 1（与 PROVIDER_PRESETS 一致）。minimax_cn 镜像根本没有
    # 该 provider，走末尾合成行。capabilities 里的 `music` 是本需求新增的能力位：
    # 音乐候选依赖它（见 media_model_discovery 的 music 分类），不声明就不会在
    # 「设置 › 音乐」里出现。刻意不声明 vision——两侧分类正则都不认 MiniMax 的思考
    # 模型名，声明了也只会在候选列表里制造选不到的错位行。
    "minimax": {
        "type": 1,
        "name": "MiniMax",
        "defaultBaseUrl": "https://api.minimax.io/v1",
        "capabilities": ["text", "video", "audio", "music"],
        "requiresBaseUrl": True,
        "supportsBaseUrlOverride": True,
    },
    "minimax_cn": {
        "type": 1,
        "name": "MiniMax CN",
        "defaultBaseUrl": "https://api.minimax.cn/v1",
        "capabilities": ["text", "video", "audio", "music"],
        "requiresBaseUrl": True,
        "supportsBaseUrlOverride": True,
    },
    # [aipdd fork delta req48] OpenAI / Claude / Gemini 三家官方渠道。能力位只
    # 声明该厂商**真的提供**的类别（设置页媒体模型下拉的唯一过滤器，多一个就是在
    # 对应 TAB 里造一个选不动的行，少一个就是配好的渠道在 TAB 里凭空消失）：
    #   openai_official —— 文本/视觉/向量/生图/视频（Sora）/语音（TTS-Whisper）；
    #     没有音乐模型，所以不挂 music。
    #   anthropic —— 只有文本与图像理解（Claude 不提供生图/视频/语音/音乐），所以
    #     只声明 text/vision。
    #   gemini —— 文本/视觉/向量/生图（Imagen・nano-banana）/视频（Veo）/语音
    #     （TTS 系列）；Lyria 音乐是非 OpenAI 兼容的厂商私有协议而**网关里没有
    #     music 路由**，所以上面不挂 music（与 MiniMax 不同：那边有直连适配器）。
    # openai_official 镜像注册表里根本没有这个 provider，走末尾合成行；anthropic
    # （type 14）/ gemini（type 24）是镜像已有行，由本表整体覆盖展示名 / 默认地址 /
    # 能力位（镜像表那两行大概只声明 text，不覆盖则三家在媒体 TAB 全部选不到）。
    # type 与 PROVIDER_PRESETS 同一口径：覆盖已有行时值不变，合成行靠它拿到 type。
    "openai_official": {
        "type": 1,
        "name": "OpenAI",
        "defaultBaseUrl": "https://api.openai.com/v1",
        "capabilities": [
            "text",
            "vision",
            "embedding",
            "image",
            "video",
            "audio",
        ],
        "requiresBaseUrl": True,
        "supportsBaseUrlOverride": True,
    },
    "anthropic": {
        "type": 14,
        "name": "Claude",
        "defaultBaseUrl": "https://api.anthropic.com/v1",
        "capabilities": ["text", "vision"],
        "requiresBaseUrl": True,
        "supportsBaseUrlOverride": True,
    },
    "gemini": {
        "type": 24,
        "name": "Gemini",
        "defaultBaseUrl": "https://generativelanguage.googleapis.com/v1beta",
        "capabilities": [
            "text",
            "vision",
            "embedding",
            "image",
            "video",
            "audio",
        ],
        "requiresBaseUrl": True,
        "supportsBaseUrlOverride": True,
    },
}

# [aipdd fork delta] 渠道能力补丁表：按 fork 事实给镜像内置的渠道类型叠加缺失
# 能力位（业务模型映射的「渠道候选」按此过滤）。当前仅火山方舟（volcengine）一项，
# 补的两个能力位都是镜像表没声明、而本 fork 实测可用的：
#   vision —— 火山方舟提供 doubao-seed-1-6 / doubao-vision 等视觉理解模型；
#   image  —— Seedream 系生图经**生效网关**实测可用：POST /v1/images/generations +
#             model=doubao-seedream-4-0-20260415（请求体用网关路径原形：不带 size，只
#             有 width/height 与 metadata）返回 HTTP 200 与图片 URL，/v1/images/edits
#             同样到达 Ark 参数校验；图片协议由网关做 Ark 转换。
# 这一位是设置页媒体模型渠道下拉的唯一过滤器（前端 providersSupportingCapability：
# 拿到渠道类型就以 capabilities 为准），缺位就等于该渠道在对应 TAB 凭空消失 → 用户
# 配不出映射 → 画布目录（与设置页严格同源）没有这个模型。火山方舟生图缺的正是这一
# 条，不是目录里缺模型。Ark 账号侧未开通的型号（如 doubao-seededit-3-0-i2i）仍会由
# Ark 回 InvalidEndpointOrModel，那是账号问题，不在此替它瞒能力位。
_FORK_CHANNEL_TYPE_CAPABILITY_PATCH: dict[str, tuple[str, ...]] = {
    "volcengine": ("vision", "image"),
}


class CreateChannelsBatchBody(BaseModel):
    new_api_base_url: str | None = Field(default=None, alias="newApiBaseUrl")
    database: NewApiDatabaseBody | None = None
    channels: list[ChannelSpec] = Field(min_length=1)


class ProviderChannelConfigBody(BaseModel):
    provider: str
    type: int | None = None
    upstream_key: str | None = Field(default=None, alias="upstreamKey")
    base_url: str | None = Field(default=None, alias="baseUrl")
    priority: int | None = None
    # [aipdd fork delta] None = 未提及（保留渠道原有 settings）；dict = 显式整体替换。
    settings: dict[str, Any] | None = None


class SaveProviderChannelsBody(BaseModel):
    channels: list[ProviderChannelConfigBody] = Field(default_factory=list)
    preserve_unmentioned: bool = Field(default=False, alias="preserveUnmentioned")


class SyncProviderChannelBody(BaseModel):
    new_api_base_url: str | None = Field(default=None, alias="newApiBaseUrl")
    database: NewApiDatabaseBody | None = None
    provider: str
    upstream_key: str | None = Field(default=None, alias="upstreamKey")
    base_url: str | None = Field(default=None, alias="baseUrl")


# [aipdd fork delta] 拉取上游模型清单的请求体：只需 provider，后端会用已存的
# base+key 去查 OpenAI 兼容的 {base}/v1/models，避免前端重复传 Key。
class FetchUpstreamModelsBody(BaseModel):
    provider: str
    upstream_key: str | None = Field(default=None, alias="upstreamKey")
    base_url: str | None = Field(default=None, alias="baseUrl")
    # [aipdd fork delta] WP-B: 清单拉取现在带 TTL 缓存；面板默认仍强制刷新，
    # 需要复用缓存（如连续多个设默认）时传 force=false。
    force: bool = True
    # [aipdd fork delta] req33 §6.3：业务模型映射的下拉按调用场景收敛候选。
    # 只接受 ``text`` / ``vision``（chat 协议才有实测结论）；不传就是原来的上游全量清单。
    kind: str | None = None


class SyncProviderCatalogBody(BaseModel):
    provider: str
    # [aipdd fork delta req40] 默认强制实拉（用户主动同步 / 进入模型 Tab）；只读缓存
    # 场景（连续展开多个下拉）传 force=false。
    force: bool = True


@router.post("/custom/newapi/provider-channels")
async def save_custom_newapi_provider_channels(
    body: SaveProviderChannelsBody,
) -> dict[str, Any]:
    try:
        require_ce_gateway_management()
        saved = _gateway.save_newapi_provider_channels(
            [
                {
                    "provider": channel.provider,
                    "type": channel.type or 0,
                    "upstreamKey": channel.upstream_key or "",
                    "baseUrl": channel.base_url or "",
                    "priority": channel.priority,
                    # [aipdd fork delta] 未显式传 settings = 保留渠道原有 settings：
                    # 连接卡只改 Key/Base 时不应清掉渠道级扩展配置（如 volcengine 的
                    # 语音识别凭证、comfyui 的工作流表）；显式传 = 整体替换。
                    **(
                        {"settings": channel.settings}
                        if channel.settings is not None
                        else {}
                    ),
                }
                for channel in body.channels
            ],
            preserve_unmentioned=body.preserve_unmentioned,
        )
        requested_providers = {
            str(channel.provider or "").strip().lower() for channel in body.channels
        }
        comfyui_channels = [
            channel
            for channel in saved
            if channel["provider"] == "comfyui" and "comfyui" in requested_providers
        ]
        if comfyui_channels:
            cfg = _gateway.get_provisioner_config()
            admin = _gateway.ensure_admin_access_token(cfg)
            existing_media_mappings = get_newapi_media_model_mappings()
            media_mappings = {
                model: mapping
                for model, mapping in existing_media_mappings.items()
                if mapping.get("provider") != "comfyui"
            }
            for channel in comfyui_channels:
                models, workflow_ids = parse_comfyui_channel_workflows(
                    channel["settings"]
                )
                model_mapping = {model: model for model in models}
                payload = build_channel_payload(
                    provider="comfyui",
                    channel_type=channel.get("type") or 63,
                    upstream_key=channel["upstreamKey"],
                    model_mapping=model_mapping,
                    base_url=channel["baseUrl"],
                    priority=channel.get("priority", 0),
                    other_settings=channel["settings"],
                )
                result = _gateway.upsert_channel(cfg, admin, payload)
                if not result.get("ok"):
                    raise RuntimeError("LocalAPI rejected ComfyUI channel configuration")
                for model in model_mapping:
                    previous = existing_media_mappings.get(model, {})
                    # [aipdd fork delta] 媒体类型优先取已声明值，缺失时按工作流输出
                    # 节点自动推断（图→image / 视频→video），不再一律写死 video。
                    detected_media_type = detect_comfyui_media_type(
                        channel["settings"], model
                    )
                    media_type = str(
                        previous.get("mediaType")
                        or detected_media_type
                        or "video"
                    )
                    # [aipdd fork delta] 「AI分析并自动配置」回写的真实能力优先于
                    # 按名字 token 的档位猜测（用户手动编过的键不被覆盖）。
                    declared_capabilities = comfyui_declared_capabilities(
                        channel["settings"], model
                    )
                    media_mappings[model] = {
                        "provider": "comfyui",
                        "upstreamModel": "",
                        "mediaType": media_type,
                        "label": previous.get("label", model),
                        "enabled": previous.get("enabled", True),
                        "sortOrder": previous.get("sortOrder", 100),
                        "config": _comfyui_media_model_config(
                            model,
                            previous.get("config"),
                            workflow_ids=workflow_ids,
                            media_type=media_type,
                            declared=declared_capabilities,
                        ),
                    }
            _gateway.save_newapi_media_model_mappings(media_mappings)
        # [aipdd fork delta req40] 保存渠道 = 拉清单的时机：逐渠道刷目录、取消失效
        # 绑定并重投影网关。系统不代选任何默认模型（旧实现按关键词自动挑 embedding）。
        catalog_refresh = await _refresh_provider_catalogs(saved)
        media_models = get_newapi_media_model_mappings()
        embedding_model = get_newapi_embedding_model_config()
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "ok": True,
        "data": {
            "channels": [
                {
                    "provider": channel["provider"],
                    "type": channel.get("type", 0),
                    "configured": bool(channel["upstreamKey"])
                    or (
                        channel["provider"] == "comfyui"
                        and bool(channel["baseUrl"])
                        and bool(channel.get("settings"))
                    ),
                    "upstreamKeyPreview": mask_token(channel["upstreamKey"]),
                    "baseUrl": channel["baseUrl"],
                    "priority": channel.get("priority", 0),
                    "settings": channel.get("settings", {}),
                }
                for channel in saved
            ],
            "mediaModels": media_models,
            "embeddingModel": embedding_model or None,
            "catalogRefresh": catalog_refresh,
        },
    }


@router.get("/custom/newapi/channel-types")
async def get_custom_newapi_channel_types() -> dict[str, Any]:
    try:
        require_ce_gateway_management()
        cfg = _gateway.get_provisioner_config()
        # Token discovery may wait for an uninitialized LocalAPI. Keep all
        # blocking HTTP/SQLite work off the ASGI event loop so official gateway
        # saves and application startup remain responsive.
        items = await asyncio.to_thread(
            lambda: list_channel_types(cfg, _gateway.ensure_admin_access_token(cfg))
        )
        # [aipdd fork delta] 渠道类型修正：先记录已见 provider（供末尾合成行
        # 去重），再整体覆盖 fork 事实项（_FORK_CHANNEL_TYPE_OVERRIDES，如
        # newapi→type 1 / aipdd 登记），最后叠加上文 capability patch 的缺失能力
        # 位。volcengine（type 45）镜像表只声明 text/embedding/video，但火山方舟
        # 实际提供 doubao-seed-1-6 / doubao-vision 等视觉理解模型。设置页「业务
        # 模型映射」的视觉候选依赖 capabilities 过滤，缺 vision 会让 VolcEngine
        # 渠道在「视觉理解」TAB 里凭空消失。此处按 fork 事实叠加缺失能力（其余
        # 渠道保持镜像口径，不做任何改写）。
        seen_providers: set[str] = set()
        for item in items:
            provider_key = str(item.get("provider") or "").strip().lower()
            seen_providers.add(provider_key)
            override = _FORK_CHANNEL_TYPE_OVERRIDES.get(provider_key)
            if override:
                item.update(override)
            patch = _FORK_CHANNEL_TYPE_CAPABILITY_PATCH.get(provider_key)
            if not patch:
                continue
            capabilities = [
                str(value).strip().lower()
                for value in item.get("capabilities") or []
                if str(value).strip()
            ]
            for capability in patch:
                if capability not in capabilities:
                    capabilities.append(capability)
            item["capabilities"] = capabilities
        # [aipdd fork delta] 追加镜像注册表缺失的 fork 自有渠道类型行（aipdd / mediakit）：
        # 「渠道管理」目录与前端 type / 能力解析链共用这份数据，合成后已配置的 AIPDD
        # 渠道才会出现在列表中且状态为「已添加」，而不是凭空缺席。
        for provider_key, override in _FORK_CHANNEL_TYPE_OVERRIDES.items():
            if provider_key in seen_providers:
                continue
            items.append(
                {
                    "type": int(override.get("type") or 0),
                    "provider": provider_key,
                    "name": str(override.get("name") or provider_key),
                    "description": str(override.get("description") or ""),
                    "icon": "",
                    "defaultBaseUrl": str(override.get("defaultBaseUrl") or ""),
                    "status": 1,
                    "capabilities": list(override.get("capabilities") or []),
                    "requiresBaseUrl": bool(override.get("requiresBaseUrl")),
                    "supportsBaseUrlOverride": bool(
                        override.get("supportsBaseUrlOverride", True)
                    ),
                }
            )
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "data": {"items": items}}


@router.post("/custom/newapi/provider-channel/sync")
async def sync_custom_newapi_provider_channel(
    body: SyncProviderChannelBody,
) -> dict[str, Any]:
    provider = str(body.provider or "").strip().lower()
    if not provider:
        raise HTTPException(status_code=400, detail="provider is required")
    try:
        require_ce_gateway_management()
        saved_channel = _gateway.get_newapi_provider_channel(provider) or {}
        upstream_key = (body.upstream_key or "").strip() or saved_channel.get(
            "upstreamKey", ""
        )
        if not upstream_key:
            raise ValueError(f"upstreamKey is required for provider {provider}")
        base_url = (
            body.base_url
            if body.base_url is not None
            else saved_channel.get("baseUrl", "")
        )
        cfg = _get_provisioner_config_from_request(body.new_api_base_url, body.database)
        admin = _gateway.ensure_admin_access_token(cfg)
        update_kwargs: dict[str, Any] = {
            "provider": provider,
            "upstream_key": upstream_key,
            "base_url": base_url,
        }
        saved_channel_type = int(saved_channel.get("type") or 0)
        if saved_channel_type > 0:
            update_kwargs["channel_type"] = saved_channel_type
        result = _gateway.update_provider_channel_credentials(cfg, admin, **update_kwargs)
        saved = []
        catalog_result: dict[str, Any] = {}
        if result.get("ok"):
            saved = _gateway.save_newapi_provider_channels(
                [
                    {
                        "provider": provider,
                        "upstreamKey": upstream_key,
                        "baseUrl": base_url or "",
                    }
                ],
                preserve_unmentioned=True,
            )
            # [aipdd fork delta req40] 渠道地址 / 密钥一变，目录就必须重拉：旧目录里
            # 的名字可能整体作废（换账号 / 换站点）。拉失败保留旧快照，只透出 error。
            if saved and provider != "comfyui":
                catalog_result = await asyncio.to_thread(
                    _gateway.refresh_provider_catalog, provider
                )
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    sent_payload = result.get("sentPayload")
    return {
        "ok": result["ok"],
        "data": {
            "provider": provider,
            "channelId": result.get("channelId"),
            "httpStatus": result.get("httpStatus"),
            "newApiResponse": result.get("newApiResponse"),
            "sentPayload": (
                _mask_sent_channel_payload(sent_payload)
                if isinstance(sent_payload, dict) and "channel" in sent_payload
                else sent_payload
            ),
            "savedChannel": next(
                (
                    {
                        "provider": channel["provider"],
                        "configured": bool(channel["upstreamKey"]),
                        "upstreamKeyPreview": mask_token(channel["upstreamKey"]),
                        "baseUrl": channel["baseUrl"],
                    }
                    for channel in saved
                    if channel["provider"] == provider
                ),
                None,
            ),
            "catalog": {
                "count": int(catalog_result.get("count") or 0),
                "error": str(catalog_result.get("error") or ""),
                "pruned": catalog_result.get("pruned") or {},
                "projection": catalog_result.get("projection") or {},
            },
        },
    }


def _not_granted_lookup(kind: str) -> Any:
    """[aipdd fork delta] req33 §6.3：一次读实测缓存，返回 ``(provider, name) -> 不可用``。

    与文本 / 视觉候选目录用的是同一个判定器；探测子系统不可用时恒 False，
    等价于未收敛前的行为（拿不准就保留，绝不误伤）。
    """

    try:
        from novelvideo.text_model_probe import build_not_granted_lookup

        return build_not_granted_lookup(kind)
    except Exception:  # noqa: BLE001
        return lambda *_args: False


# [aipdd fork delta] 拉取上游模型清单：用户填 Key 保存后，用已存的 base+key
# 直连 OpenAI 兼容的 {base}/v1/models，返回模型 id 列表供前端下拉设默认。
@router.post("/custom/newapi/upstream-models")
async def fetch_custom_newapi_upstream_models(
    body: FetchUpstreamModelsBody,
) -> dict[str, Any]:
    provider = str(body.provider or "").strip().lower()
    if not provider:
        raise HTTPException(status_code=400, detail="provider is required")
    base_url = ""
    try:
        require_ce_gateway_management()
        saved_channel = _gateway.get_newapi_provider_channel(provider) or {}
        typed_key = str(body.upstream_key or "").strip()
        if is_masked_secret(typed_key):
            raise ValueError(
                "提交的 API Key 是界面回显的掩码预览（如 sk-abcd...wxyz）而非完整令牌，"
                "无法用它拉清单：请把完整 Key 粘进输入框后重试"
            )
        saved_key = str(saved_channel.get("upstreamKey") or "").strip()
        upstream_key = typed_key or saved_key
        # 地址与 Key 各自「来自本次提交」还是「来自库内已存」必须说清：只传 provider
        # 的调用会回查库里那份，用户「刚填了新地址还没保存就点拉取」时发出去的是旧
        # 地址 + 旧 Key，报错里的域名根本不是用户输入的那个（实测：填 movstory、报错
        # 域名是 susciyuan，两边各执一词、无从判定）。
        key_source = "本次提交" if typed_key else "库内已存（改完需先保存）"
        base_url = str(
            body.base_url
            if body.base_url is not None
            else saved_channel.get("baseUrl", "")
        ).strip().rstrip("/")
        base_source = "本次提交" if body.base_url else "库内已存（改完需先保存）"
        if not base_url:
            raise ValueError("baseUrl is required（先保存渠道或填写 Base URL）")
        if not upstream_key:
            raise ValueError(f"upstreamKey is required for provider {provider}")
        if not typed_key and is_masked_secret(saved_key):
            # 历史脏数据：在写库拦截加进来之前的某次保存里，掩码预览被当成了真 Key。
            # 这种值发给上游必然 401，而且预览和用户手里那份看着一样，必须直接点名。
            raise ValueError(
                f"provider {provider} 库里已存的 API Key 是掩码预览"
                f"（{mask_token(saved_key)}）而非完整令牌，无法用于拉取清单："
                "请在渠道连接卡重新粘贴完整 Key 并保存"
            )
        entries, error = await asyncio.to_thread(
            list_upstream_catalog,
            provider,
            base_url=base_url,
            upstream_key=upstream_key,
            force=bool(body.force),
        )
        if error:
            # 报错必须把「这次实际用了哪个端点、哪把 Key、它从哪来」摊出来：渠道
            # base+key 的真源在服务端库里，界面上只显示掩码占位，用户无从核对自己是
            # 改对了还是根本没存进去（实测：库里存的是 A 站、用户以为填的是 B 站）。
            raise HTTPException(
                status_code=502,
                detail=(
                    f"拉取上游模型清单失败：{error}\n"
                    f"本次实际请求：{_resolve_models_endpoint(base_url, provider)}/models"
                    f"（provider={provider}，"
                    f"Base URL={base_url or '（未填）'}[{base_source}]，"
                    f"Key={mask_token(upstream_key)}[{key_source}]）"
                ),
            )
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # 目录按名字稳定排序：前端按条目分组展示，顺序不能跟着上游响应抖动。
    entries = sorted(entries, key=lambda item: str(item.get("name") or "").strip())
    unique = sorted(
        {str(entry.get("name") or "").strip() for entry in entries} - {""}
    )
    kept_entries = list(entries)
    probe_kind = str(body.kind or "").strip().lower()
    dropped = 0
    if probe_kind in ("text", "vision"):
        # 上游已经明确判过 not-granted（视觉再加 not-vision）的名字配上去就是死映射；
        # 限流 / 鉴权失败 / 没测过的 unknown 照旧保留，判据和文本、视觉候选那两条一致。
        not_usable = _not_granted_lookup(probe_kind)
        kept = [name for name in unique if not not_usable(provider, name)]
        dropped = len(unique) - len(kept)
        unique = kept
        kept_set = set(kept)
        kept_entries = [
            entry for entry in kept_entries if str(entry.get("name") or "") in kept_set
        ]
    return {
        "ok": True,
        "data": {
            "provider": provider,
            "baseUrl": base_url,
            "endpoint": _resolve_models_endpoint(base_url, provider),
            "models": unique,
            "catalog": kept_entries,
            "count": len(unique),
            "probeKind": probe_kind,
            "droppedNotGranted": dropped,
        },
    }


# [aipdd fork delta req40] 目录同步：渠道 BaseURL 拉回来的是**结构化目录**，不只是一串
# 名字 —— kind / 端点 / 模态 / 向量维度 / available 都来自渠道自己的声明，平台不预先
# 知道渠道里有什么模型。这里是「用户动设置 = 拉清单」的写路径唯一入口：拉取 → 按渠道
# 整体覆盖快照 → 取消指向已下架模型的默认绑定 → 重投影网关注册。
@router.post("/custom/newapi/provider-catalog/sync")
async def sync_custom_newapi_provider_catalog(
    body: SyncProviderCatalogBody,
) -> dict[str, Any]:
    provider = str(body.provider or "").strip().lower()
    if not provider:
        raise HTTPException(status_code=400, detail="provider is required")
    try:
        require_ce_gateway_management()
        result = await asyncio.to_thread(
            _gateway.refresh_provider_catalog,
            provider,
            force=bool(body.force),
        )
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not result.get("ok"):
        # 拉取失败不动目录也不剪绑定（一次网络抖动不该取消用户全部默认），但要让
        # 前端知道「现在看到的是上次的快照」。
        raise HTTPException(
            status_code=502,
            detail=f"拉取渠道模型目录失败：{result.get('error') or '未知错误'}",
        )
    entries = result.get("entries") or []
    return {
        "ok": True,
        "data": {
            "provider": provider,
            "count": len(entries),
            "models": [str(entry.get("name") or "") for entry in entries],
            "catalog": entries,
            "pruned": result.get("pruned") or {},
            "projection": result.get("projection") or {},
        },
    }


# [aipdd fork delta req40] 目录的只读视图：设置页「模型」Tab 进来先看渠道已经声明过的
# 事实，而不是让前端自己拼候选。零远程调用（只读快照），首屏可以放心调；``provider``
# 省略时回全部有快照的渠道。
@router.get("/custom/newapi/provider-catalog")
async def get_custom_newapi_provider_catalog(
    provider: str = "",
) -> dict[str, Any]:
    try:
        require_ce_gateway_management()
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    provider_key = str(provider or "").strip().lower()
    meta = _gateway.get_upstream_catalog_meta()
    providers = [provider_key] if provider_key else sorted(meta)
    data = [
        {
            "provider": item,
            "baseUrl": str(meta.get(item, {}).get("baseUrl") or ""),
            "fetchedAt": str(meta.get(item, {}).get("fetchedAt") or ""),
            "entries": _gateway.get_upstream_catalog(item),
        }
        for item in providers
    ]
    return {"ok": True, "data": {"providers": data, "meta": meta}}


@router.post("/custom/newapi/channels")
async def create_custom_newapi_channel(body: CreateChannelBody) -> dict[str, Any]:
    try:
        require_ce_gateway_management()
        cfg = _get_provisioner_config_from_request(body.new_api_base_url, body.database)
        admin = _gateway.ensure_admin_access_token(cfg)
        payload = _build_channel_payload_from_spec(body)
        result = _gateway.upsert_channel(cfg, admin, payload)
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "ok": result["ok"],
        "data": {
            "newApiAdminBaseUrl": cfg.admin_base_url,
            "httpStatus": result["httpStatus"],
            "newApiResponse": result["newApiResponse"],
            "action": result.get("action"),
            "channelId": result.get("channelId"),
            "sentPayload": _mask_sent_channel_payload(
                result.get("sentPayload") or payload
            ),
        },
    }


@router.post("/custom/newapi/channels/batch")
async def create_custom_newapi_channels_batch(
    body: CreateChannelsBatchBody,
) -> dict[str, Any]:
    try:
        require_ce_gateway_management()
        cfg = _get_provisioner_config_from_request(body.new_api_base_url, body.database)
        admin = _gateway.ensure_admin_access_token(cfg)
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # [aipdd fork delta] WP-A: 先落自有真源（按渠道归属并入 alias->target），再
    # 由 _build_channel_payload_from_spec 编译投影到网关。前端提交的 mapping 不
    # 再是“只活在网关里”的唯一副本。
    for channel in body.channels:
        try:
            record_newapi_model_alias_mappings(
                channel.model_mapping or {},
                provider=str(channel.provider or "").strip().lower(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    results: list[dict[str, Any]] = []
    for index, channel in enumerate(body.channels):
        try:
            payload = _build_channel_payload_from_spec(channel)
            result = _gateway.upsert_channel(cfg, admin, payload)
            item: dict[str, Any] = {
                "index": index,
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
                item["error"] = "LocalAPI rejected channel creation"
            results.append(item)
        except Exception as exc:
            results.append(
                {
                    "index": index,
                    "ok": False,
                    "error": str(exc),
                }
            )

    succeeded = sum(1 for item in results if item["ok"])
    failed = len(results) - succeeded
    return {
        "ok": failed == 0,
        "data": {
            "newApiAdminBaseUrl": cfg.admin_base_url,
            "total": len(results),
            "succeeded": succeeded,
            "failed": failed,
            "aliasMappings": _gateway.get_newapi_model_alias_mappings(),
            "aliasMappingSource": "project",
            "results": results,
        },
    }