"""模型网关路由包（原 ``model_gateway.py`` 按域拆分）。

[重构] 子模块划分：
- connection: 网关配置/状态、文本模型候选、媒体中转、视频高清与 LocalNewAPI 初始化
- official:   官方密钥配置、模式启用与官方媒体目录管理
- channels:   自建渠道管理、上游模型拉取与批量创建
- aliases:    别名映射单一真源读写、渠道投影与一致性自检
- media:      媒体模型渠道写入与本地映射保存
- embedding:  Embedding 模型渠道写入与维度配置
- comfyui:    本地 ComfyUI 渠道清理与工作流解析
- plaza:      模型广场只读目录与单次最小探活
- seedance_assets: Seedance 私域素材库 / 真人活体认证（仅支持该能力的 NewAPI）
- _common:    共享层（CE 管理闸门、请求体模型、渠道载荷构建辅助）

URL 与请求/响应契约和单文件时代完全一致。测试对 ``model_gateway.<name>`` 的
直接访问与 monkeypatch 由两处机制共同保持：本模块 re-export 全量兼容面；
子模块对 patch 敏感名字一律经包命名空间（``_gateway.X``）延迟解析。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from novelvideo.api.auth import get_api_user  # noqa: F401  (re-export: patch seam for route auth)

from . import (
    aliases,
    channels,
    comfyui,
    connection,
    embedding,
    media,
    official,
    plaza,
    seedance_assets,
)

# ---------------------------------------------------------------------------
# 兼容导出：与单文件时代的顶层命名空间保持同一访问面。
# 测试按 ``model_gateway.<name>`` 直接调用 / 构造 / monkeypatch；子模块运行时
# 也经包命名空间解析这些 patch 敏感名字。
# ---------------------------------------------------------------------------
from novelvideo import config as app_config  # noqa: F401
from novelvideo.model_gateway_runtime import refresh_model_gateway_runtime  # noqa: F401
from novelvideo.model_gateway_settings import (  # noqa: F401
    get_newapi_model_alias_mappings,
    get_newapi_provider_channel,
    get_newapi_provider_channels,
    get_upstream_catalog,
    get_upstream_catalog_meta,
    save_custom_newapi_gateway,
    save_newapi_database_config,
    save_newapi_embedding_model_config,
    save_newapi_media_model_mappings,
    save_newapi_provider_channels,
)
from novelvideo.newapi_provisioner import (  # noqa: F401
    build_provisioner_status,
    create_or_reuse_relay_token,
    delete_channel_by_name,
    ensure_admin_access_token,
    ensure_newapi_setup,
    get_provisioner_config,
    list_channels,
    refresh_provider_catalog,
    require_provisioner_enabled,
    update_provider_channel_credentials,
    upsert_channel,
)
from novelvideo.authz.service_operation_gate import (  # noqa: F401
    require_legacy_local_service_operation,
)
from novelvideo.shared.runtime_env import is_ce_effective  # noqa: F401

from ._common import (  # noqa: F401
    ChannelSpec,
    NewApiDatabaseBody,
    SaveEmbeddingModelBody,
    _build_channel_payload_from_spec,
    _build_embedding_model_channel_spec,
    require_ce_gateway_management,
    require_model_gateway_access,
)
from .comfyui import _default_comfyui_media_model_config  # noqa: F401
from .connection import (  # noqa: F401
    _is_internal_alias_name,
    _is_selectable_text_model,
    _media_relay_status,
    _non_text_model_names,
    list_text_model_options,
)
from .media import (  # noqa: F401
    MediaModelConfigBody,
    _build_media_model_channel_specs,
)

# [aipdd fork delta] CE 多用户：网关配置含渠道密钥 + NewAPI 管理员令牌、决定全平台
# 模型流量去向，属管理员配置面（与 env_setup 同域）。整组路由拒机器凭证
# （agent 会话 / 统一 API 密钥），写操作再要 owner；只读留给浏览器会话（成员 UI 需
# /model-plaza、/config 列模型）。单用户模式浏览器会话恒 local/owner，全部放行、行为不变。
router = APIRouter(
    prefix="/model-gateway", dependencies=[Depends(require_model_gateway_access)]
)

router.include_router(connection.router)
router.include_router(official.router)
router.include_router(channels.router)
router.include_router(aliases.router)
router.include_router(media.router)
router.include_router(embedding.router)
router.include_router(comfyui.router)
router.include_router(plaza.router)
# [aipdd fork delta req47] Seedance 素材库/真人认证：整组路由自带能力探测闸门，
# 不支持素材库的 NewAPI 一律 503，不做「先试一把」的假设。
router.include_router(seedance_assets.router)

# 文本模型候选缓存存放在包命名空间：connection 的 text-models 路由经
# ``_gateway._text_models_cache`` 读写，测试 fixture 直接赋值包属性清缓存
# （保持单文件时代的 patch 语义）。
_text_models_cache: tuple[float, list[dict[str, Any]]] | None = None

# [aipdd fork delta] 「视频反推剧本」视觉模型候选缓存（与 text-models 同口径，
# 测试 fixture 直接赋值包属性清缓存）。
_vision_models_cache: tuple[float, list[dict[str, Any]]] | None = None


def invalidate_model_candidate_caches() -> None:
    """[aipdd fork delta] req38：实测结论刚被改写时，把两份候选缓存清掉。

    正常情况下候选缓存只有 60 秒 TTL，靠自然过期就够；但「用户刚踩了一个 404、系统
    当场判死这个名字」的场景里，多等 60 秒意味着用户按原样重试还会再踩同一个坑。
    """

    global _text_models_cache, _vision_models_cache
    _text_models_cache = None
    _vision_models_cache = None
