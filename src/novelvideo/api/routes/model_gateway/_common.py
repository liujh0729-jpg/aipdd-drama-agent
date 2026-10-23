"""模型网关路由包共享层：CE 管理闸门、请求体模型与渠道载荷构建辅助。

[重构] 自原 ``model_gateway.py`` 物理拆分而来（纯移动，行为与 URL 契约不变）。
patch 敏感依赖统一经包命名空间 ``_gateway`` 解析：测试以
``monkeypatch.setattr(model_gateway, "<name>", ...)`` 拦截副作用，单文件时代靠
模块全局查找命中，拆包后必须显式回到包命名空间才能保持同一语义。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field

from novelvideo import config as app_config
from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import model_gateway as _gateway
from novelvideo.model_gateway_settings import (
    build_channel_registrations,
    build_model_gateway_status,
    normalize_relay_base_url,
)
from novelvideo.newapi_provisioner import (
    ServiceControlEgressDenied,
    build_channel_payload,
    mask_token,
)
from novelvideo.authz.service_operation_gate import ServiceOperationExcluded


class NewApiDatabaseBody(BaseModel):
    sql_dsn: str | None = Field(default=None, alias="sqlDsn")
    sqlite_path: str | None = Field(default=None, alias="sqlitePath")
    admin_username: str | None = Field(default=None, alias="adminUsername")


class CreateChannelBody(BaseModel):
    new_api_base_url: str | None = Field(default=None, alias="newApiBaseUrl")
    database: NewApiDatabaseBody | None = None
    provider: str = "ali"
    type: int | None = None
    name: str | None = None
    upstream_key: str | None = Field(default=None, alias="upstreamKey")
    model_mapping: dict[str, str] = Field(alias="modelMapping")
    group: str = "default"
    priority: int | None = None
    weight: int = 0
    base_url: str | None = Field(default=None, alias="baseUrl")
    test_model: str | None = Field(default=None, alias="testModel")
    settings: dict[str, Any] = Field(default_factory=dict)


class ChannelSpec(BaseModel):
    provider: str = "ali"
    type: int | None = None
    name: str | None = None
    upstream_key: str | None = Field(default=None, alias="upstreamKey")
    model_mapping: dict[str, str] = Field(alias="modelMapping")
    group: str = "default"
    priority: int | None = None
    weight: int = 0
    base_url: str | None = Field(default=None, alias="baseUrl")
    test_model: str | None = Field(default=None, alias="testModel")
    settings: dict[str, Any] = Field(default_factory=dict)


class SaveEmbeddingModelBody(BaseModel):
    new_api_base_url: str | None = Field(default=None, alias="newApiBaseUrl")
    database: NewApiDatabaseBody | None = None
    provider: str
    upstream_model: str = Field(alias="upstreamModel")
    dimension: int
    batch_size: int | None = Field(default=None, alias="batchSize")
    send_dimensions: bool = Field(default=True, alias="sendDimensions")


def require_ce_gateway_management() -> None:
    """Reject CE LocalAPI mutations from an EE-composed process."""
    try:
        _gateway.require_legacy_local_service_operation()
    except ServiceOperationExcluded as exc:
        raise PermissionError(exc.code) from None
    if not _gateway.is_ce_effective():
        raise ServiceControlEgressDenied()
    _gateway.require_provisioner_enabled()


def _permission_error(exc: PermissionError) -> HTTPException:
    return HTTPException(status_code=403, detail=str(exc))


# 只读方法：成员 UI（聊天模型选择器 / 工具页）要读 /model-plaza、/config
# 才能列可用模型，故这些留给任意浏览器会话。
_GATEWAY_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def require_model_gateway_access(
    request: Request, user: dict = Depends(get_api_user)
) -> dict:
    """[aipdd fork delta] 网关管理面的统一权限闸门。

    网关配置含渠道上游密钥与 NewAPI 管理员令牌，并决定全平台模型流量去向，
    属管理员配置面（与 ``env_setup`` 同域）：
    1. 机器凭证（agent 会话 / 统一 API 密钥 ``st-…``）一律拒——它们按设计只是
       「干活」凭证（调模型 / MCP），绝不能读写网关配置或拓扑（否则一把外发的
       密钥可新增指向攻击者基础设施的渠道、改写上游密钥、劫持全平台流量）。
    2. 写操作（POST/PUT/PATCH/DELETE）只放行浏览器会话里的 owner；非 owner 成员
       不得改路由 / 凭据。
    3. 只读（GET/HEAD）留给任意浏览器会话。

    单用户模式（ST_CE_MULTI_USER=false）下浏览器会话恒 local/owner，全部自然
    放行，与改造前逐字一致。
    """
    if str(user.get("credential_kind") or "") in {"agent_session", "api_key"}:
        raise HTTPException(status_code=403, detail="browser session required")
    if request.method.upper() not in _GATEWAY_READ_METHODS:
        if str(user.get("role") or "").strip().lower() != "owner":
            raise HTTPException(
                status_code=403, detail="administrator privilege required"
            )
    return user


def _get_provisioner_config_from_request(
    new_api_base_url: str | None,
    database: NewApiDatabaseBody | None,
):
    return _gateway.get_provisioner_config(
        new_api_base_url,
        sql_dsn=database.sql_dsn if database else None,
        sqlite_path=database.sqlite_path if database else None,
        admin_username=database.admin_username if database else None,
    )


def _save_request_database_config(
    cfg,
    database: NewApiDatabaseBody | None,
) -> None:
    if database is None:
        return
    _gateway.save_newapi_database_config(
        sql_dsn=cfg.sql_dsn,
        sqlite_path=cfg.sqlite_path,
        admin_username=cfg.admin_username,
    )


def _build_channel_payload_from_spec(
    spec: ChannelSpec | CreateChannelBody,
) -> dict[str, Any]:
    saved_channel = _gateway.get_newapi_provider_channel(spec.provider) or {}
    # [aipdd fork delta] 拍平改造：model_mapping = 渠道完整注册表
    # （``build_channel_registrations``：内部绑定裸名 + 全量复合名 + 单渠道唯一
    # 裸名，全部零远程），再用本次请求显式提交的部分覆盖。提交整表才能让网关侧
    # 以「替换」语义与真源收敛（同渠道其余注册不因一次单点设默认而被剪掉）。
    compiled_mapping = build_channel_registrations(spec.provider)
    model_mapping = {**compiled_mapping, **{k: v for k, v in (spec.model_mapping or {}).items() if str(v or "").strip()}}
    return build_channel_payload(
        provider=spec.provider,
        channel_type=spec.type or int(saved_channel.get("type") or 0) or None,
        name=spec.name,
        upstream_key=spec.upstream_key or saved_channel.get("upstreamKey", ""),
        model_mapping=model_mapping,
        group=spec.group,
        # [aipdd fork delta] 拍平改造：优先级不再由系统推导；未显式传时沿用已存
        # 渠道值（用户设过就保留，没设过恒 0）。
        priority=(
            int(saved_channel.get("priority") or 0)
            if spec.priority is None
            else int(spec.priority)
        ),
        weight=spec.weight,
        base_url=spec.base_url or saved_channel.get("baseUrl", ""),
        test_model=spec.test_model,
        other_settings=spec.settings or saved_channel.get("settings", {}),
    )


# [aipdd fork delta] WP-A: 把项目真源编译出的 model_mapping 投影到指定 provider 渠道。
# 真源写入永不因网关拒绝而回滚（本项目才是权威），投影失败只在结果里报 ok=False。
# [重构] 自原 model_gateway.py 物理拆分时漏迁；alias-mappings 与 media-model-mappings
# 两条保存路径共用。写副作用（读渠道存档 / 渠道 upsert）一律经包命名空间解析，
# 保持单文件时代 tests 对 ``monkeypatch.setattr(model_gateway, "upsert_channel", ...)``
# 的拦截契约。
def _project_provider_channel_mappings(
    cfg,
    admin,
    providers: list[str],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for provider in providers:
        provider_key = str(provider or "").strip().lower()
        if not provider_key:
            continue
        mapping = build_channel_registrations(provider_key)
        # [aipdd fork delta] 拍平改造：投影 = 渠道完整注册表（内部绑定裸名 + 全量
        # 复合名 + 单渠道唯一裸名，全部零远程）。每次投影全量提交，配合 upsert 的
        # 「替换」语义让上游下架 / 改名随之收敛删除。
        if not mapping:
            results.append(
                {
                    "provider": provider_key,
                    "ok": True,
                    "skipped": "empty-mapping",
                    "models": 0,
                }
            )
            continue
        saved = _gateway.get_newapi_provider_channel(provider_key) or {}
        try:
            payload = build_channel_payload(
                provider=provider_key,
                channel_type=int(saved.get("type") or 0) or None,
                upstream_key=str(saved.get("upstreamKey") or ""),
                model_mapping=mapping,
                # [aipdd fork delta] 拍平改造：优先级不推导；沿用已存渠道值。
                priority=int(saved.get("priority") or 0),
                base_url=str(saved.get("baseUrl") or ""),
                other_settings=saved.get("settings") or {},
            )
            result = _gateway.upsert_channel(cfg, admin, payload)
            item: dict[str, Any] = {
                "provider": provider_key,
                "ok": bool(result.get("ok")),
                "action": result.get("action"),
                "channelId": result.get("channelId"),
                "httpStatus": result.get("httpStatus"),
                "models": len(mapping),
            }
            if not item["ok"]:
                item["error"] = str(
                    result.get("newApiResponse") or "NewAPI rejected channel update"
                )
        except Exception as exc:  # noqa: BLE001 - 投影失败不回滚真源
            item = {
                "provider": provider_key,
                "ok": False,
                "error": str(exc),
                "models": len(mapping),
            }
        results.append(item)
    return results


def _build_embedding_model_channel_spec(
    body: SaveEmbeddingModelBody,
) -> tuple[ChannelSpec, dict[str, Any]]:
    provider = str(body.provider or "").strip().lower()
    upstream_model = str(body.upstream_model or "").strip()
    dimension = int(body.dimension)
    batch_size = int(body.batch_size or 0)
    if not provider:
        raise ValueError("provider is required for embedding model")
    if not upstream_model:
        raise ValueError("upstreamModel is required for embedding model")
    if dimension <= 0:
        raise ValueError("dimension must be positive")
    if body.batch_size is not None and batch_size <= 0:
        raise ValueError("batchSize must be positive")
    normalized = {
        "provider": provider,
        "upstreamModel": upstream_model,
        "dimension": dimension,
        # Kept in the response/config schema for compatibility with older
        # clients. Runtime request behavior is controlled by the internal
        # EmbeddingModelSpec, not by this user-supplied field.
        "sendDimensions": True,
        "internalModel": "DC-cognee-embedding",
    }
    if batch_size > 0:
        normalized["batchSize"] = batch_size
    return (
        ChannelSpec(
            provider=provider,
            modelMapping={"DC-cognee-embedding": upstream_model},
        ),
        normalized,
    )


# [aipdd fork delta] 拆解上游一体化图像模型的「内置向量检索」：配置 VolcEngine
# 渠道后自动拉取上游模型清单，命中 doubao-embedding 则自动写入并设为默认 Embedding。
#
# [aipdd fork delta] WP-B: 端点解析与上游清单拉取收拢到 newapi_provisioner（带 TTL
# 缓存），面板与保存期校验共用一套规则，不再两处各写一份。


async def _refresh_provider_catalogs(
    saved_channels: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """[aipdd fork delta req40] 渠道保存 = 拉清单的时机：逐渠道刷目录并取消失效绑定。

    取代旧的「自动替用户挑 ``doubao-embedding`` 当默认」—— 系统不代选模型：渠道有
    什么名字只能拉了才知道，默认值由用户从目录里挑，名字下架时由
    ``prune_stale_bindings`` 取消。刷新链路 = 拉取 → 按渠道整体覆盖快照 → 剪失效
    绑定 → 重投影网关注册。

    永不抛异常：目录拉取失败不该拖垮渠道保存，只把 error 透出给前端提示。
    """

    results: list[dict[str, Any]] = []
    for channel in saved_channels:
        provider = str(channel.get("provider") or "").strip().lower()
        if not provider or provider == "comfyui":
            continue
        if not str(channel.get("baseUrl") or "").strip():
            continue
        if not str(channel.get("upstreamKey") or "").strip():
            continue
        try:
            outcome = await asyncio.to_thread(_gateway.refresh_provider_catalog, provider)
        except Exception as exc:  # noqa: BLE001 - surfaced, must not break channel save
            outcome = {"provider": provider, "ok": False, "error": str(exc)}
        results.append(
            {
                "provider": provider,
                "ok": bool(outcome.get("ok")),
                "count": int(outcome.get("count") or 0),
                "pruned": outcome.get("pruned") or {},
                "error": str(outcome.get("error") or ""),
                "projection": outcome.get("projection") or {},
            }
        )
    return results


def _mask_sent_channel_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload,
        "channel": {
            **payload["channel"],
            "key": mask_token(payload["channel"]["key"]),
        },
    }


# [aipdd fork delta] 本地网关（LocalAPI）内部自动初始化。桌面/自建场景下，网关是
# 纯内部调用：用户只在外层首次登录页建「应用超管」一次，绝不会再看到任何网关建号
# 表单。启动后台任务与内部修复路径共用本函数把网关跑起来：
#   ensure_newapi_setup（无凭据 → 自动播种一次性 root）
#   → ensure_admin_access_token（直写 DB 的 access_token 供后续管理）
#   → create_or_reuse_relay_token（签发/复用中继 Key）
#   → save_custom_newapi_gateway(activate=True) → refresh_model_gateway_runtime。
# 幂等且不越权：custom 已就绪就原样早退——既不重复建 token，也不覆盖用户已选的
# 网关模式（official / hybrid / custom）。永不吞异常，失败由调用方记日志、下次重试。
def bootstrap_local_gateway() -> dict[str, Any] | None:
    status = build_model_gateway_status(
        official_base_url=app_config.official_newapi_base_url(),
        official_api_key=app_config.LOCALAPI_API_KEY,
    )
    if status["custom"]["configured"]:
        return None
    cfg = _gateway.get_provisioner_config(None)
    _gateway.ensure_newapi_setup(cfg)
    admin = _gateway.ensure_admin_access_token(cfg)
    token = _gateway.create_or_reuse_relay_token(
        cfg,
        admin,
        name=cfg.relay_token_name,
        reuse_existing=True,
    )
    relay_base_url = normalize_relay_base_url(cfg.admin_base_url)
    _gateway.save_custom_newapi_gateway(
        base_url=relay_base_url,
        api_key=token["key"],
        admin_base_url=cfg.admin_base_url,
        token_name=str(token["name"]),
        token_id=token["tokenId"],
        activate=True,
    )
    runtime = _gateway.refresh_model_gateway_runtime()
    return {
        "relayBaseUrl": relay_base_url,
        "adminUserId": admin.admin_user_id,
        "relayTokenName": str(token["name"]),
        "runtime": runtime,
    }