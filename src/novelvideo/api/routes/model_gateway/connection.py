"""模型网关连接与状态路由：网关配置、文本模型候选、媒体中转、本地初始化。

[重构] 自原 ``model_gateway.py`` 物理拆分而来（纯移动，行为与 URL 契约不变）。
patch 敏感依赖统一经包命名空间 ``_gateway`` 解析，保持 tests 对
``monkeypatch.setattr(model_gateway, "<name>", ...)`` 的拦截契约；文本模型缓存
``_text_models_cache`` 亦存放于包命名空间（测试 fixture 直接赋值包属性清缓存）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from novelvideo import config as app_config
from novelvideo.api.routes import model_gateway as _gateway
from novelvideo.media_model_discovery import guess_model_kind, is_vision_candidate_name
from novelvideo.model_gateway_settings import (
    MODE_CUSTOM,
    MODE_HYBRID,
    build_douyin_cookie_status,
    build_media_relay_status,
    build_mediakit_status,
    build_model_gateway_status,
    get_effective_media_relay_config,
    get_effective_mediakit_config,
    get_prompt_export_password_setting,
    get_vectcut_draft_dir,
    mask_secret,
    normalize_api_key,
    normalize_relay_base_url,
    save_douyin_web_cookie,
    save_media_relay_config,
    save_mediakit_config,
    save_prompt_export_password,
    save_vectcut_draft_dir,
    set_model_gateway_mode,
)
from novelvideo.newapi_provisioner import (
    NewApiSetupCredentials,
    mask_token,
    peek_alias_mapping_health,
)
from novelvideo.security.operator_auth import get_prompt_export_password

from ._common import (
    NewApiDatabaseBody,
    _get_provisioner_config_from_request,
    _permission_error,
    _save_request_database_config,
    require_ce_gateway_management,
)

router = APIRouter()


class MediaRelayConfigBody(BaseModel):
    provider: str = "aliyun_oss"
    ttl_seconds: int = Field(default=1800, alias="ttlSeconds")
    endpoint: str | None = None
    bucket: str | None = None
    access_key_id: str | None = Field(default=None, alias="accessKeyId")
    access_key_secret: str | None = Field(default=None, alias="accessKeySecret")
    cloud_name: str | None = Field(default=None, alias="cloudName")
    cloudinary_api_key: str | None = Field(default=None, alias="apiKey")
    cloudinary_api_secret: str | None = Field(default=None, alias="apiSecret")
    cloudinary_folder: str | None = Field(default=None, alias="apiFolder")
    # [aipdd fork delta req44] AIPDD 参考媒体中转（「AIPDD 数字资产云」与「本地存储」
    # 两档共用）：Key 是必填项，地址可留空（留空时取渠道 baseUrl 的站点根）。
    # None = 不改动已存 Key，空串 = 保留（密钥不回传，同 OSS AccessKey 口径）。
    aipdd_api_key: str | None = Field(default=None, alias="aipddApiKey")
    aipdd_base_url: str | None = Field(default=None, alias="aipddBaseUrl")
    aipdd_prefix: str | None = Field(default=None, alias="aipddPrefix")
    register_asset: bool | None = Field(default=None, alias="registerAsset")
    # [aipdd fork delta] AWS S3 档（与阿里云 OSS 并行）：region/bucket/AK/SK 必填，
    # endpointUrl 仅自建 S3 兼容网关才填，sessionToken 仅临时凭据。None=不改，空串=清空。
    s3_region: str | None = Field(default=None, alias="s3Region")
    s3_bucket: str | None = Field(default=None, alias="s3Bucket")
    s3_access_key_id: str | None = Field(default=None, alias="s3AccessKeyId")
    s3_access_key_secret: str | None = Field(default=None, alias="s3AccessKeySecret")
    s3_endpoint_url: str | None = Field(default=None, alias="s3EndpointUrl")
    s3_session_token: str | None = Field(default=None, alias="s3SessionToken")


class MediaKitConfigBody(BaseModel):
    """[aipdd fork delta] 火山 AI MediaKit 视频高清（生成式超分）配置。"""

    api_key: str | None = Field(default=None, alias="apiKey")
    base_url: str | None = Field(default=None, alias="baseUrl")
    backend: str | None = None  # auto | mediakit | ffmpeg
    # [aipdd fork delta] 渠道目录「移除」动作：显式清除 Key / Base URL，configured
    # 回落 false（CE 下 Key 只认设置数据库，不再由 MEDIAKIT_API_KEY 环境变量兜底）。
    clear: bool = False


class VectcutDraftDirBody(BaseModel):
    """[aipdd fork delta] 剪映草稿目录（VectCut 服务上的草稿存储根目录）。清空 = 传空串。"""

    draft_dir: str = Field(default="", alias="draftDir")


class OperatorSecurityBody(BaseModel):
    """[aipdd fork delta] operator 口令保存体。None = 不改动，空串 = 清空（回退 env 兜底）。"""

    prompt_export_password: str | None = Field(
        default=None, alias="promptExportPassword"
    )


class DouyinCookieBody(BaseModel):
    """[aipdd fork delta] 抖音 Web 登录态 Cookie 保存体。

    后端统一归一（DevTools 整串 / EditThisCookie JSON 数组 / 对象导出都接受）。
    ``clear=true`` 与空串同为「清除」：清除后提取链路自动回落宿主 Chrome CDP。
    """

    cookie: str = ""
    clear: bool = False


class NewApiInitBody(BaseModel):
    new_api_base_url: str | None = Field(default=None, alias="newApiBaseUrl")
    database: NewApiDatabaseBody | None = None
    setup_username: str | None = Field(default=None, alias="setupUsername")
    setup_password: str | None = Field(default=None, alias="setupPassword")
    setup_confirm_password: str | None = Field(
        default=None, alias="setupConfirmPassword"
    )
    token_name: str | None = Field(default=None, alias="tokenName")
    group: str = "default"
    unlimited_quota: bool = Field(default=True, alias="unlimitedQuota")
    remain_quota: int = Field(default=0, alias="remainQuota")
    expired_time: int = Field(default=-1, alias="expiredTime")
    reuse_existing: bool = Field(default=True, alias="reuseExisting")


def _setup_credentials_from_request(body: NewApiInitBody) -> NewApiSetupCredentials:
    username = (body.setup_username or "").strip()
    if not username and body.database and body.database.admin_username:
        username = body.database.admin_username.strip()
    return NewApiSetupCredentials(
        username=username,
        password=body.setup_password or "",
        confirm_password=body.setup_confirm_password or "",
        self_use_mode_enabled=True,
        demo_site_enabled=False,
    )


def _media_relay_status() -> dict[str, Any]:
    return build_media_relay_status(
        env_provider=app_config.MEDIA_RELAY_PROVIDER,
        env_ttl_seconds=app_config.MEDIA_RELAY_TTL_SECONDS,
        env_endpoint=app_config.OSS_RELAY_ENDPOINT,
        env_bucket=app_config.OSS_RELAY_BUCKET,
        env_access_key_id=app_config.OSS_RELAY_AK,
        env_access_key_secret=app_config.OSS_RELAY_SK,
        env_cloud_name=app_config.CLOUDINARY_RELAY_CLOUD_NAME,
        env_cloudinary_api_key=app_config.CLOUDINARY_RELAY_API_KEY,
        env_cloudinary_api_secret=app_config.CLOUDINARY_RELAY_API_SECRET,
        env_cloudinary_folder=app_config.CLOUDINARY_RELAY_FOLDER,
        env_aipdd_api_key=app_config.AIPDD_CLOUD_API_KEY,
        env_aipdd_base_url=app_config.AIPDD_CLOUD_BASE_URL,
        env_s3_region=app_config.AWS_S3_REGION,
        env_s3_bucket=app_config.AWS_S3_BUCKET,
        env_s3_access_key_id=app_config.AWS_S3_ACCESS_KEY_ID,
        env_s3_access_key_secret=app_config.AWS_S3_SECRET_ACCESS_KEY,
        env_s3_endpoint_url=app_config.AWS_S3_ENDPOINT_URL,
        env_s3_session_token=app_config.AWS_S3_SESSION_TOKEN,
    )


def _mediakit_status() -> dict[str, Any]:
    # [aipdd fork delta] 火山 AI MediaKit 视频高清配置（API Key/Base URL 只认设置数据库；
    # backend 兼容运维级环境变量 VIDEO_ENHANCE_BACKEND）。
    return build_mediakit_status()


def _douyin_cookie_status() -> dict[str, Any]:
    """[aipdd fork delta] 抖音 Web 登录态 Cookie 状态（只回条数与 Cookie 名）。"""

    return build_douyin_cookie_status()


def _operator_security_status() -> dict[str, Any]:
    """[aipdd fork delta] operator 口令（导出提示词等）状态。

    设置数据库优先，其次 ``PROMPT_EXPORT_PASSWORD`` 环境变量（首启引导兜底）；皆空则
    未配置（fail-closed）。"""
    effective = ""
    try:
        effective = get_prompt_export_password() or ""
    except Exception:  # noqa: BLE001 - 状态展示不因读库异常而阻断
        effective = ""
    db_configured = bool(get_prompt_export_password_setting())
    return {
        "promptExportPasswordConfigured": bool(effective),
        "promptExportPasswordPreview": mask_secret(effective),
        "source": (
            "database" if db_configured else ("environment" if effective else "unset")
        ),
    }


def _vectcut_draft_dir_status() -> dict[str, Any]:
    # [aipdd fork delta] 剪映草稿目录配置回显；未配置时 configured=false，调用侧回退
    # VectCut 服务的部署默认（VECTCUT_DRAFT_DIR）。
    value = get_vectcut_draft_dir()
    return {"draftDir": value, "configured": bool(value)}


def _provisioner_status() -> dict[str, Any]:
    if _gateway.is_ce_effective():
        return _gateway.build_provisioner_status()
    return {
        "enabled": False,
        "adminBaseUrl": "",
        "dbConfigured": False,
        "database": {
            "configured": False,
            "available": False,
            "source": "unavailable",
        },
        "adminUsername": "",
        "relayTokenName": "",
        "providers": {},
        "providerChannels": [],
        "mediaModels": {},
        "embeddingModel": {},
        "relayBaseUrl": "",
    }


@router.get("/config")
async def get_model_gateway_config() -> dict[str, Any]:
    return {
        "ok": True,
        "data": {
            **build_model_gateway_status(
                official_base_url=app_config.official_newapi_base_url(),
                official_api_key=app_config.NEWAPI_API_KEY,
            ),
            "provisioner": _provisioner_status(),
            "mediaRelay": _media_relay_status(),
            "videoEnhance": _mediakit_status(),
            # [aipdd fork delta] 抖音文案提取的纯 HTTP 主链路凭据（存设置数据库）。
            "douyinCookie": _douyin_cookie_status(),
            # [aipdd fork delta] operator 安全（导出提示词口令，存设置数据库）。
            "operatorSecurity": _operator_security_status(),
            # [aipdd fork delta] 剪映草稿目录（设置 → 剪映草稿）。
            "vectcut": _vectcut_draft_dir_status(),
            # [aipdd fork delta] WP-B: 只读快照（不触发远程调用），面板顶部横幅用。
            "aliasMappingHealth": peek_alias_mapping_health(),
        },
    }


# [aipdd fork delta] 画布脚本节点 / 工具页的文本模型候选。权威文本路由是 per-feature
# 的 modelMapping，没有现成的「文本目录」，这里把别名真源里「是模型」的条目聚合成
# 候选。过滤口径（单一真源 ``_is_selectable_text_model``）：DC- 前缀的内部功能位、
# 按 capability/分类判定的 vision/image/video/audio/embedding、媒体目录登记名、
# 健康自检判 stale 的别名一律不出现；当前默认文本模型永远排第首项。真源为空
# （升级后还没设过默认）时回到旧的网关渠道 mapping 聚合（同口径过滤）；拉取失败
# 时只回默认文本模型一项，不阻断画布。缓存 60s。
_TEXT_MODELS_TTL_SECONDS = 60.0


def _not_granted_lookup(kind: str = "text") -> Any:
    """[aipdd fork delta] 实测里「上游明确判了不行」的档才有杀伤力。

    文本只摘 ``not-granted``，视觉多摘一档 ``not-vision``（上游明说这个名字读不了图）。
    限流 / 鉴权失败 / 传输异常 / 非 chat 协议的媒体模型都只是 ``unknown``，照旧列出
    —— 拿不准的一律保留，绝不误伤；判定器一次读缓存（候选目录一次要判上百个名字），
    缓存读不到时恒 False，等价于改造前行为。
    """

    try:
        from novelvideo.text_model_probe import build_not_granted_lookup

        return build_not_granted_lookup(kind)
    except Exception:  # noqa: BLE001 - 探测缓存不可用时保守放行
        return lambda *_args: False


def _schedule_probe(channels: list[dict[str, Any]], *, kind: str = "text") -> None:
    """[aipdd fork delta] 后台幂等补测需要实测的渠道（不阻塞候选目录响应）。

    文本与视觉各自的缓存、各自排队：视觉实测发的是带图请求，用文本结论代替会
    把「能回文本但读不了图」的名字留在视觉下拉里。
    """

    if not channels:
        return
    try:
        from novelvideo.text_model_probe import schedule_probe_refresh

        schedule_probe_refresh(kind=kind)
    except Exception:  # noqa: BLE001 - 探测调度失败不影响候选目录
        return


def _text_model_entry(
    name: str,
    channel: str = "newapi",
    *,
    is_default: bool = False,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": name,
        "providerId": "newapi",
        "provider": "newapi",
        # [aipdd fork delta] 展示用渠道：这条模型在别名真源里实际所属的渠道
        # （aipdd / newapi / volcengine ...）。providerId 仍是执行身份「newapi」，
        # 画布模型选择器读 channel 在模型旁做彩色渠道标注。
        "channel": channel,
        "apiModel": name,
        "api_model": name,
        "label": name,
    }
    # [aipdd fork delta] 当前生效的默认文本模型（永远排首项）：工具页的「默认」
    # 选项据此把具体模型名 / 渠道展示出来，而不是含糊的「跟随系统默认」。
    if is_default:
        entry["isDefault"] = True
    return entry


def _call_name_channel(name: str) -> str:
    """[aipdd fork delta] 拍平后：调用名 -> 展示渠道。

    复合名 ``{provider}::{model}`` 取前缀；裸名取「唯一拥有渠道」；无 / 多归属
    时返回空串（不猜渠道——系统不做隐式裁决，同名模型由用户显式选复合名）。
    """

    from novelvideo.model_gateway_settings import (
        model_owner_providers,
        split_provider_spot_model,
    )

    provider, _model = split_provider_spot_model(name)
    if provider:
        return provider
    owners = model_owner_providers(name)
    return owners[0] if len(owners) == 1 else ""


def _non_text_model_names() -> set[str]:
    """媒体目录（本地配置 + 官方内置）里出现过的名字，聚合后从文本候选排除。"""
    from novelvideo.model_gateway_settings import (
        get_newapi_media_model_mappings,
        get_official_media_model_catalog,
        get_official_media_model_mappings,
    )

    excluded: set[str] = set()
    for mappings in (
        get_newapi_media_model_mappings(),
        get_official_media_model_mappings(),
    ):
        for name, item in mappings.items():
            excluded.add(str(name))
            upstream = str((item or {}).get("upstreamModel") or "")
            if upstream:
                excluded.add(upstream)
    # [aipdd fork delta req46] music 一并从文本候选排除：分类真源新增「音乐」后，
    # 旧写法会让音乐模型既不在 audio 也不在 image/video 目录里，反而漏进文本下拉。
    for media_type in ("image", "video", "audio", "music"):
        for entry in get_official_media_model_catalog(media_type):
            for key in ("id", "apiModel", "gatewayModel"):
                value = str(entry.get(key) or "")
                if value:
                    excluded.add(value)
    # embedding 的内部名固定，不走文本候选。
    excluded.add("DC-cognee-embedding")
    return {name.casefold() for name in excluded if name}


# [aipdd fork delta] 用户可选文本模型的「类型」过滤口径（单一真源）：
# 1. ``DC-`` 前缀是内部功能位命名空间（DC-hermes-LLM 等），不是模型，永不出现在
#    用户候选里；
# 2. 声明 capability 优先，其次分类真源 ``guess_model_kind``（八分类）：
#    vision / image / video / audio / music / embedding 一律排除；text 保留，other 也保留
#    —— other 是「认不出」而非「不是文本」，新模型常在关键词表更新前先以 other
#    出现，保留才能被选用。
# 与 ``_non_text_model_names``（媒体目录登记名）互补：前者按类型、后者按登记。
_NON_TEXT_MODEL_CAPABILITIES = frozenset(
    {"vision", "image", "video", "audio", "music", "embedding"}
)


def _is_internal_alias_name(name: str) -> bool:
    """``DC-`` 前缀 = DramaClaw 内部功能位命名空间，不是用户可选模型。"""

    return str(name or "").strip().casefold().startswith("dc-")


def _is_selectable_text_model(name: str, capability: str = "") -> bool:
    """按「类型」判定一个名字能否作为文本模型候选（不判定渠道是否可用）。"""

    clean = str(name or "").strip()
    if not clean or _is_internal_alias_name(clean):
        return False
    declared = str(capability or "").strip().lower()
    if declared in _NON_TEXT_MODEL_CAPABILITIES:
        return False
    if declared == "text":
        return True
    return guess_model_kind(clean) not in _NON_TEXT_MODEL_CAPABILITIES


@router.get("/text-models")
async def list_text_model_options() -> dict[str, Any]:
    import time as _time

    now = _time.monotonic()
    cache = _gateway._text_models_cache
    if cache is not None and now - cache[0] < _TEXT_MODELS_TTL_SECONDS:
        return {"ok": True, "data": cache[1]}

    from novelvideo.model_gateway_settings import (
        get_effective_default_text_model,
        provider_spot_model,
    )
    from novelvideo.newapi_provisioner import upstream_passthrough_models

    default_text = str(get_effective_default_text_model() or "").strip()
    excluded = _gateway._non_text_model_names()
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _append(call_name: str, channel: str, *, is_default: bool = False) -> None:
        key = call_name.casefold()
        if not call_name or key in seen:
            return
        seen.add(key)
        entries.append(_text_model_entry(call_name, channel, is_default=is_default))

    # [aipdd fork delta] 拍平改造：当前默认文本模型永远排首项（渠道由调用名解析
    # ——复合名前缀或单渠道归属；无法判定时留空，不猜渠道）。
    if default_text and _is_selectable_text_model(default_text):
        _append(default_text, _call_name_channel(default_text), is_default=True)

    # [aipdd fork delta] 拍平清单：每个渠道名下的全部上游模型各成一条，调用名 =
    # 复合名 ``{provider}::{model}``，渠道随条目展示、由用户显式选择具体渠道。
    # 零远程（只读本地快照 + 真源声明的真名）；同名跨渠道不再做任何隐式裁决。
    #
    # [aipdd fork delta] 科普讲解视频 404 的教训：上游清单不是可用清单——火山方舟
    # ``GET /models`` 是平台全量目录，账号没开通的名字照样在清单里，选了就在调用时
    # 404 ``InvalidEndpointOrModel.NotFound``（容器实测：抽 24 个只有 4 个能调通）。
    # 因此这里按「最小实测三态」把上游明确判了 not-granted 的名字摘出候选，其余
    # （限流 / 鉴权 / 超时 / 非 chat 协议）一律保留，绝不误伤；同时后台幂等补测缺失
    # 或过期的渠道，不阻塞本次响应。关掉：``TEXT_MODEL_PROBE_ENABLED=0``。
    try:
        channels = _gateway.get_newapi_provider_channels()
    except Exception:  # noqa: BLE001 - 渠道配置读不到时只回默认项
        channels = []
    _schedule_probe(channels)
    not_granted = _not_granted_lookup()
    for channel in channels:
        provider = str(channel.get("provider") or "").strip().lower()
        if not provider or provider == "comfyui":
            continue
        try:
            models = upstream_passthrough_models(provider, from_snapshot=True)
        except Exception:  # noqa: BLE001 - 单渠道快照读失败不影响其余渠道
            continue
        for model in models:
            clean = str(model or "").strip()
            if not clean or clean.casefold() in excluded:
                continue
            if not _is_selectable_text_model(clean):
                continue
            if not_granted(provider, clean):
                continue
            _append(provider_spot_model(provider, clean), provider)

    _gateway._text_models_cache = (now, entries)
    return {"ok": True, "data": entries}


# [aipdd fork delta] 「视频反推剧本」工具页的视觉模型候选：与 text-models 同构，
# 只是类型判定反向 —— 只保留具备视觉理解能力的模型（候选资格真源
# ``is_vision_candidate_name``：声明 capability=vision 或分类判为 vision，且**排除
# 向量 embedding**）。DC- 内部别名同样不出现；未选时
# 后端回退系统默认视觉链（FREEZONE_VISION_MODEL → VISION_MODEL_FALLBACK）。
_VISION_MODELS_TTL_SECONDS = 60.0


def _is_selectable_vision_model(name: str, capability: str = "") -> bool:
    """按「类型」判定一个名字能否作为视觉模型候选（不判定渠道是否可用）。

    [aipdd fork delta] embedding 排除不在本函数里重写，而是委派
    ``media_model_discovery.is_vision_candidate_name``：那边是单一真源，视觉实测的
    候选名口径（``text_model_probe._snapshot_names``）也走它，两边不会飘移 —— 否则
    会出现「下拉里看得见但从来没被带图实测过」的名字。
    """

    clean = str(name or "").strip()
    if not clean or _is_internal_alias_name(clean):
        return False
    return is_vision_candidate_name(clean, capability)


@router.get("/vision-models")
async def list_vision_model_options() -> dict[str, Any]:
    import time as _time

    now = _time.monotonic()
    cache = _gateway._vision_models_cache
    if cache is not None and now - cache[0] < _VISION_MODELS_TTL_SECONDS:
        return {"ok": True, "data": cache[1]}

    from novelvideo.model_gateway_settings import provider_spot_model
    from novelvideo.newapi_provisioner import upstream_passthrough_models

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _append(call_name: str, channel: str) -> None:
        key = call_name.casefold()
        if not call_name or key in seen:
            return
        seen.add(key)
        entries.append(_text_model_entry(call_name, channel))

    # 拍平清单：每个渠道名下的全部上游模型里只留视觉理解类（向量 embedding 不算），
    # 调用名 = 复合名 ``{provider}::{model}``（零远程，只读本地快照）。
    try:
        channels = _gateway.get_newapi_provider_channels()
    except Exception:  # noqa: BLE001 - 渠道配置读不到时只回空列表，不阻断工具页
        channels = []
    _schedule_probe(channels, kind="vision")
    not_granted = _not_granted_lookup("vision")
    for channel in channels:
        provider = str(channel.get("provider") or "").strip().lower()
        if not provider or provider == "comfyui":
            continue
        try:
            models = upstream_passthrough_models(provider, from_snapshot=True)
        except Exception:  # noqa: BLE001 - 单渠道快照读失败不影响其余渠道
            continue
        for model in models:
            clean = str(model or "").strip()
            if not _is_selectable_vision_model(clean):
                continue
            # 视觉候选吃视觉那一套带图实测：未开通的方舟视觉名、以及上游明说读不了
            # 图的名字，放进来也只是换一个报错。
            if not_granted(provider, clean):
                continue
            _append(provider_spot_model(provider, clean), provider)

    _gateway._vision_models_cache = (now, entries)
    return {"ok": True, "data": entries}


@router.post("/custom/enable")
async def enable_custom_gateway() -> dict[str, Any]:
    try:
        require_ce_gateway_management()
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    status = build_model_gateway_status(
        official_base_url=app_config.official_newapi_base_url(),
        official_api_key=app_config.NEWAPI_API_KEY,
    )
    if not status["custom"]["configured"]:
        raise HTTPException(
            status_code=400, detail="LocalNewAPI gateway is not configured"
        )
    set_model_gateway_mode(MODE_CUSTOM)
    runtime = _gateway.refresh_model_gateway_runtime()
    return {
        "ok": True,
        "data": build_model_gateway_status(
            official_base_url=app_config.official_newapi_base_url(),
            official_api_key=app_config.NEWAPI_API_KEY,
        ),
        "runtime": runtime,
    }


@router.post("/hybrid/enable")
async def enable_hybrid_gateway() -> dict[str, Any]:
    try:
        require_ce_gateway_management()
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    status = build_model_gateway_status(
        official_base_url=app_config.official_newapi_base_url(),
        official_api_key=app_config.NEWAPI_API_KEY,
    )
    if not status["official"]["configured"] or not status["custom"]["configured"]:
        raise HTTPException(
            status_code=400,
            detail="local and official NewAPI gateways must both be configured",
        )
    set_model_gateway_mode(MODE_HYBRID)
    runtime = _gateway.refresh_model_gateway_runtime()
    return {
        "ok": True,
        "data": build_model_gateway_status(
            official_base_url=app_config.official_newapi_base_url(),
            official_api_key=app_config.NEWAPI_API_KEY,
        ),
        "runtime": runtime,
    }


@router.post("/vectcut-draft/config")
async def save_vectcut_draft_settings(body: VectcutDraftDirBody) -> dict[str, Any]:
    """[aipdd fork delta] 保存剪映草稿目录。空串 = 取消配置（回 VectCut 部署默认）。"""
    try:
        require_ce_gateway_management()
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    try:
        save_vectcut_draft_dir(body.draft_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "data": _vectcut_draft_dir_status()}


@router.post("/operator-security/config")
async def save_operator_security_settings(body: OperatorSecurityBody) -> dict[str, Any]:
    """[aipdd fork delta] 保存 operator 口令到设置数据库。

    传 None 视为不改动；传空串视为清空（回退 ``PROMPT_EXPORT_PASSWORD`` env 引导兜底）。"""
    try:
        require_ce_gateway_management()
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    if body.prompt_export_password is not None:
        save_prompt_export_password(password=body.prompt_export_password)
    return {"ok": True, "data": _operator_security_status()}


@router.post("/douyin-cookie/config")
async def save_douyin_cookie_settings(body: DouyinCookieBody) -> dict[str, Any]:
    """[aipdd fork delta] 保存 / 清除抖音 Web 登录态 Cookie（设置数据库单真源）。

    Cookie 是**完整登录态**，不是可局部掩码的 API Key：只写不读回显，响应只回
    条数 / Cookie 名清单 / 长度（见 ``build_douyin_cookie_status``），避免接口把
    凭据原文回传给任何能读到设置页的人。未传 cookie 字段（空串）或 ``clear=true``
    都视为清除。"""
    try:
        require_ce_gateway_management()
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    save_douyin_web_cookie(cookie="" if body.clear else (body.cookie or ""))
    return {"ok": True, "data": _douyin_cookie_status()}


@router.post("/douyin-cookie/capture")
async def capture_douyin_cookie_settings() -> dict[str, Any]:
    """[aipdd fork delta req45] 从宿主 Chrome 抓取抖音登录态 Cookie 并保存。

    与提取链路的自动记录同一通路，只是这里是用户显式动作。**抓不到不是用户的错，但必须
    告诉用户该做什么**：返回 400 + 成因（哪个 CDP 命令失败 / jar 里没登录态）与出路（按
    「环境安装」页起调试端口、在那个 Chrome 里登录抖音）。成功时也只回脱敏状态
    （条数 / Cookie 名 / 长度），响应体里没有凭据原文。
    """
    try:
        require_ce_gateway_management()
    except PermissionError as exc:
        raise _permission_error(exc) from exc

    from novelvideo.freezone.douyin_cookie_capture import capture_douyin_cookie
    from novelvideo.freezone.douyin_copywrite import chrome_cdp_url

    result = await capture_douyin_cookie(cdp_url=chrome_cdp_url())
    if not result.saved:
        raise HTTPException(status_code=400, detail=result.reason or "没抓到抖音登录态 Cookie")
    data = _douyin_cookie_status()
    data["captured"] = True
    return {"ok": True, "data": data}


@router.post("/media-relay/config")
async def save_media_relay_settings(body: MediaRelayConfigBody) -> dict[str, Any]:
    try:
        require_ce_gateway_management()
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    provider = body.provider.strip().lower()
    # [aipdd fork delta] 允许本地存储档位（local）。
    # [aipdd fork delta req44] 新增 aipdd_cloud：AIPDD 数字资产云（渠道 Key 即凭据）。
    # [aipdd fork delta] 新增 aws_s3：AWS S3 对象存储（与阿里云 OSS 并行）。
    if provider not in {"aliyun_oss", "cloudinary", "local", "aipdd_cloud", "aws_s3"}:
        raise HTTPException(status_code=400, detail="unsupported media relay provider")
    if body.ttl_seconds <= 0:
        raise HTTPException(status_code=400, detail="ttlSeconds must be positive")
    current = get_effective_media_relay_config(
        env_provider=app_config.MEDIA_RELAY_PROVIDER,
        env_ttl_seconds=app_config.MEDIA_RELAY_TTL_SECONDS,
        env_endpoint=app_config.OSS_RELAY_ENDPOINT,
        env_bucket=app_config.OSS_RELAY_BUCKET,
        env_access_key_id=app_config.OSS_RELAY_AK,
        env_access_key_secret=app_config.OSS_RELAY_SK,
        env_cloud_name=app_config.CLOUDINARY_RELAY_CLOUD_NAME,
        env_cloudinary_api_key=app_config.CLOUDINARY_RELAY_API_KEY,
        env_cloudinary_api_secret=app_config.CLOUDINARY_RELAY_API_SECRET,
        env_cloudinary_folder=app_config.CLOUDINARY_RELAY_FOLDER,
        env_aipdd_api_key=app_config.AIPDD_CLOUD_API_KEY,
        env_aipdd_base_url=app_config.AIPDD_CLOUD_BASE_URL,
        env_s3_region=app_config.AWS_S3_REGION,
        env_s3_bucket=app_config.AWS_S3_BUCKET,
        env_s3_access_key_id=app_config.AWS_S3_ACCESS_KEY_ID,
        env_s3_access_key_secret=app_config.AWS_S3_SECRET_ACCESS_KEY,
        env_s3_endpoint_url=app_config.AWS_S3_ENDPOINT_URL,
        env_s3_session_token=app_config.AWS_S3_SESSION_TOKEN,
    )

    def merge_field(value: str | None, saved: str, *, secret: bool = False) -> str:
        if value is None:
            return saved
        normalized = value.strip()
        if secret and not normalized:
            return saved
        return normalized

    endpoint = merge_field(body.endpoint, current.endpoint)
    bucket = merge_field(body.bucket, current.bucket)
    access_key_id = merge_field(body.access_key_id, current.access_key_id, secret=True)
    access_key_secret = merge_field(
        body.access_key_secret, current.access_key_secret, secret=True
    )
    cloud_name = merge_field(body.cloud_name, current.cloud_name)
    cloudinary_api_key = merge_field(
        body.cloudinary_api_key, current.cloudinary_api_key, secret=True
    )
    cloudinary_api_secret = merge_field(
        body.cloudinary_api_secret, current.cloudinary_api_secret, secret=True
    )
    cloudinary_folder = merge_field(
        body.cloudinary_folder, current.cloudinary_folder
    ).strip("/")
    # [aipdd fork delta req44] AIPDD 中转的四个字段与存储档位无关（OSS / Cloudinary
    # 档只是不消费它们）：先统一合并，免得一档一档重复写、切档时把已存配置抹掉。
    aipdd_api_key = merge_field(body.aipdd_api_key, current.aipdd_api_key, secret=True)
    aipdd_base_url = merge_field(
        body.aipdd_base_url, current.aipdd_base_url
    ).rstrip("/")
    aipdd_prefix = merge_field(body.aipdd_prefix, current.aipdd_prefix).strip("/")
    aipdd_register_asset = (
        bool(body.register_asset)
        if body.register_asset is not None
        else bool(current.aipdd_register_asset)
    )
    # [aipdd fork delta] AWS S3 档的六个字段与存储档位无关（其余档只是不消费）：
    # 先统一合并，免得一档一档重复写、切档时把已存配置抹掉。
    s3_region = merge_field(body.s3_region, current.s3_region)
    s3_bucket = merge_field(body.s3_bucket, current.s3_bucket)
    s3_access_key_id = merge_field(
        body.s3_access_key_id, current.s3_access_key_id, secret=True
    )
    s3_access_key_secret = merge_field(
        body.s3_access_key_secret, current.s3_access_key_secret, secret=True
    )
    s3_endpoint_url = merge_field(body.s3_endpoint_url, current.s3_endpoint_url)
    s3_session_token = merge_field(
        body.s3_session_token, current.s3_session_token, secret=True
    )

    def save_aipdd_relay_kwargs() -> dict[str, Any]:
        return {
            "aipdd_api_key": aipdd_api_key,
            "aipdd_base_url": aipdd_base_url,
            "aipdd_prefix": aipdd_prefix,
            "aipdd_register_asset": aipdd_register_asset,
        }

    def save_s3_relay_kwargs() -> dict[str, Any]:
        return {
            "s3_region": s3_region,
            "s3_bucket": s3_bucket,
            "s3_access_key_id": s3_access_key_id,
            "s3_access_key_secret": s3_access_key_secret,
            "s3_endpoint_url": s3_endpoint_url,
            "s3_session_token": s3_session_token,
        }

    if provider in {"local", "aipdd_cloud"}:
        # [aipdd fork delta req44] 两个档位都要求配好 AIPDD 中转：本地档的
        # 参考视频/音频靠它转公网直链，数字资产云档则全部参考媒体都靠它。解析不出
        # ``(站点根, Key)`` 就当场 400，而不是等提交参考视频时才发现产出一段与原视频
        # 不相干的片子。
        from novelvideo.storage.media_relay import resolve_aipdd_cloud_target

        aipdd_origin, _aipdd_resolved_key = resolve_aipdd_cloud_target(
            saved_base_url=aipdd_base_url,
            saved_api_key=aipdd_api_key,
        )
        if not aipdd_origin:
            raise HTTPException(
                status_code=400,
                detail=(
                    "AIPDD 参考媒体中转未就绪：请填写 AIPDD API Key（站点地址可留空，"
                    "留空时取已连接 AIPDD 渠道的 baseUrl），或先在「模型网关 · 渠道」"
                    "连接一条 AIPDD 渠道（它的 Key 就是上传凭据）；也可用 "
                    "AIPDD_CLOUD_BASE_URL / AIPDD_CLOUD_API_KEY 引导。"
                ),
            )

    if provider == "local":
        # [aipdd fork delta] 切回本地存储：不要求 OSS/Cloudinary 凭证，保留已存在的
        # 云端字段以便日后切回；参考图内联，参考视频/音频走上面的 AIPDD 中转校验。
        save_media_relay_config(
            provider="local",
            ttl_seconds=body.ttl_seconds,
            endpoint=endpoint,
            bucket=bucket,
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            cloud_name=cloud_name,
            cloudinary_api_key=cloudinary_api_key,
            cloudinary_api_secret=cloudinary_api_secret,
            cloudinary_folder=cloudinary_folder,
            **save_aipdd_relay_kwargs(),
            **save_s3_relay_kwargs(),
        )
        return {"ok": True, "data": _media_relay_status()}
    if provider == "aipdd_cloud":
        # [aipdd fork delta req44] 数字资产云档：除了 AIPDD Key 不要求任何云端凭证。
        save_media_relay_config(
            provider="aipdd_cloud",
            ttl_seconds=body.ttl_seconds,
            endpoint=endpoint,
            bucket=bucket,
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            cloud_name=cloud_name,
            cloudinary_api_key=cloudinary_api_key,
            cloudinary_api_secret=cloudinary_api_secret,
            cloudinary_folder=cloudinary_folder,
            **save_aipdd_relay_kwargs(),
            **save_s3_relay_kwargs(),
        )
        return {"ok": True, "data": _media_relay_status()}
    if provider == "cloudinary":
        required = {
            "cloudName": cloud_name,
            "apiKey": cloudinary_api_key,
            "apiSecret": cloudinary_api_secret,
        }
    elif provider == "aws_s3":
        # [aipdd fork delta] AWS S3 必填：region + bucket + AK + SK（endpoint 可选）。
        required = {
            "region": s3_region,
            "bucket": s3_bucket,
            "accessKeyId": s3_access_key_id,
            "accessKeySecret": s3_access_key_secret,
        }
    else:
        required = {
            "endpoint": endpoint,
            "bucket": bucket,
            "accessKeyId": access_key_id,
            "accessKeySecret": access_key_secret,
        }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise HTTPException(
            status_code=400, detail=f"missing fields: {', '.join(missing)}"
        )
    save_media_relay_config(
        provider=provider,
        ttl_seconds=body.ttl_seconds,
        endpoint=endpoint,
        bucket=bucket,
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        cloud_name=cloud_name,
        cloudinary_api_key=cloudinary_api_key,
        cloudinary_api_secret=cloudinary_api_secret,
        cloudinary_folder=cloudinary_folder,
        **save_aipdd_relay_kwargs(),
        **save_s3_relay_kwargs(),
    )
    return {"ok": True, "data": _media_relay_status()}


@router.post("/video-enhance/config")
async def save_video_enhance_settings(body: MediaKitConfigBody) -> dict[str, Any]:
    """[aipdd fork delta] 保存火山 AI MediaKit 视频高清 API Key / 后端策略。

    留空 apiKey 不覆盖既有密钥（与 relay secret 合并语义一致）。
    """
    try:
        require_ce_gateway_management()
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    backend = (body.backend or "auto").strip().lower()
    if backend not in {"auto", "mediakit", "ffmpeg"}:
        raise HTTPException(
            status_code=400, detail="backend must be auto, mediakit or ffmpeg"
        )
    # [aipdd fork delta] clear=True：渠道目录对 mediakit 的「移除」动作——显式清空
    # Key / Base URL（区别于「留空不覆盖」的保存语义），configured 回落 false。
    if body.clear:
        save_mediakit_config(api_key="", base_url="", backend=backend)
        return {"ok": True, "data": _mediakit_status()}
    current = get_effective_mediakit_config()
    api_key = current.api_key
    if body.api_key is not None:
        normalized = normalize_api_key(body.api_key)
        # 显式提交了非空值才覆盖；提交空串保留旧密钥（脱敏回显无法反推）。
        if normalized:
            api_key = normalized
    base_url = current.base_url
    if body.base_url is not None:
        base_url = body.base_url.strip().rstrip("/") or current.base_url
    save_mediakit_config(api_key=api_key, base_url=base_url, backend=backend)
    return {"ok": True, "data": _mediakit_status()}


@router.post("/custom/newapi/init")
async def init_custom_newapi(body: NewApiInitBody = NewApiInitBody()) -> dict[str, Any]:
    try:
        require_ce_gateway_management()
        cfg = _get_provisioner_config_from_request(body.new_api_base_url, body.database)
        setup_status = _gateway.ensure_newapi_setup(
            cfg, _setup_credentials_from_request(body)
        )
        admin = _gateway.ensure_admin_access_token(cfg)
        token_name = (
            body.token_name or cfg.relay_token_name
        ).strip() or cfg.relay_token_name
        token = _gateway.create_or_reuse_relay_token(
            cfg,
            admin,
            name=token_name,
            group=body.group,
            unlimited_quota=body.unlimited_quota,
            remain_quota=body.remain_quota,
            expired_time=body.expired_time,
            reuse_existing=body.reuse_existing,
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
        _save_request_database_config(cfg, body.database)
        runtime = _gateway.refresh_model_gateway_runtime()
    except PermissionError as exc:
        raise _permission_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "ok": True,
        "data": {
            "mode": "custom",
            "newApiAdminBaseUrl": cfg.admin_base_url,
            "newApiBaseUrl": relay_base_url,
            "adminUserId": admin.admin_user_id,
            "adminUsername": admin.admin_username,
            "adminTokenCreated": admin.token_created,
            "adminTokenPreview": mask_token(admin.access_token),
            "newApiSetup": {
                "initialized": setup_status.initialized,
                "rootInitialized": setup_status.root_initialized,
                "databaseType": setup_status.database_type,
                "setupPerformed": setup_status.setup_performed,
                "alreadyInitialized": setup_status.already_initialized,
            },
            "relayToken": {
                "created": bool(token["created"]),
                "tokenId": token["tokenId"],
                "name": token["name"],
                "keyPreview": token["keyPreview"],
            },
            "database": _gateway.build_provisioner_status()["database"],
            "effective": build_model_gateway_status(
                official_base_url=app_config.official_newapi_base_url(),
                official_api_key=app_config.NEWAPI_API_KEY,
            )["effective"],
            "runtime": runtime,
        },
    }