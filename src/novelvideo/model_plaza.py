"""[aipdd fork delta] WP-E: 模型广场（Model Plaza）。

定位是**只读目录 + 探活 + 调用示例**：

* 不含任何计费 / 额度 / 价格字段（CE 的额度体系已在 WP-D 整体拆除）；
* 不做模型增删改 —— 渠道与别名映射仍由「模型网关」设置面板负责，这里是消费面；
* 调用名 = 渠道上游真实模型名（``ap-glm-5.2`` / ``doubao-seed-1-6-250615``）。
  DC-* 这类内部功能位别名只活在项目内部与网关投影里，不作为「模型」对外展示 ——
  广场是给别人照着调用的，示例里的 model 必须能直接调通。
* 收录范围是「本项目里所有可配置、能被调用的模型」，按四个 connector 聚合
  （gateway / comfyui / mediakit / env-override）。
  [aipdd fork delta] 原 audio（IndexTTS2 / CosyVoice / Edge TTS 语音合成位）与
  official-catalog（官方内置媒体目录）两个来源已整体移除。
  未配置 / 缺 key 的条目**照常列出并标 ``noChannel``**，不从列表里静默消失，
  否则用户又得翻设置面板才知道自己有什么。

状态判定不另起炉灶：别名条目的 ``status`` 直接复用 WP-B 的
``newapi_provisioner.build_alias_mapping_health`` / ``peek_alias_mapping_health``
（零远程调用版，供目录页廉价读取），非别名条目按「渠道在册且密钥可用」映射到
同一套 ``mapped`` / ``noChannel`` 词表。
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextvars import ContextVar
from typing import Any

import httpx

from novelvideo.catalog.media_model_discovery import guess_model_kind
from novelvideo.model_gateway_settings import (
    detect_comfyui_media_type,
    get_effective_mediakit_config,
    get_effective_newapi_config,
    get_newapi_embedding_model_config,
    get_newapi_media_model_mappings,
    get_newapi_model_alias_mappings,
    get_newapi_provider_channel,
    get_newapi_provider_channels,
    get_text_model_probe,
    get_vision_model_probe,
    mask_secret,
    normalize_relay_base_url,
    parse_comfyui_channel_workflows,
    resolve_default_media_model,
    # [aipdd fork delta req40] 能力与端点改读渠道目录声明（关键词分类降级为兜底）。
    split_provider_spot_model,
    upstream_catalog_entry,
)
from novelvideo.model_slots import FEATURE_SLOTS

logger = logging.getLogger(__name__)

CONNECTOR_GATEWAY = "gateway"
CONNECTOR_COMFYUI = "comfyui"
CONNECTOR_MEDIAKIT = "mediakit"
CONNECTOR_ENV_OVERRIDE = "env-override"

# 顺序即广场分区的展示顺序。
PLAZA_CONNECTORS: tuple[str, ...] = (
    CONNECTOR_GATEWAY,
    CONNECTOR_COMFYUI,
    CONNECTOR_MEDIAKIT,
    CONNECTOR_ENV_OVERRIDE,
)

PLAZA_CAPABILITIES: tuple[str, ...] = (
    "text",
    "vision",
    "embedding",
    "image",
    "video",
    "audio",
    # [aipdd fork delta req46] 音乐：与语音合成不同协议（厂商直连），广场要能单独
    # 展示成一个能力位，不能伪装成配音模型。
    "music",
    # [aipdd fork delta req40] 目录能声明的两类新能力：转写（ASR）与决策。
    # ``decision`` 只登记与展示，不接入任何调用环节（网关侧也没有该路由）。
    "asr",
    "decision",
    "enhance",
)

INVOKE_OPENAI_CHAT = "openai-chat"
INVOKE_OPENAI_EMBEDDINGS = "openai-embeddings"
INVOKE_OPENAI_IMAGES = "openai-images"
INVOKE_ASYNC_VIDEO_TASK = "async-video-task"
INVOKE_COMFYUI_WORKFLOW = "comfyui-workflow"
INVOKE_MEDIAKIT_TASK = "mediakit-task"
INVOKE_TTS_VOICE = "tts-voice"
# [aipdd fork delta req40] 目录声明出的两类端点形态：转写不是合成，决策没接环节
# —— 两者都不在 PROBEABLE_INVOKE_KINDS 里，探活一律不真发请求。
INVOKE_ASR_TRANSCRIPTION = "asr-transcription"
INVOKE_SYSTEMONE = "systemone"
# [aipdd fork delta req46] 音乐不走网关：广场卡上得如实标成「直连音乐生成」，
# 而不是让人以为能在 NewAPI 里直接调。同样不可探活（会产生真实计费）。
INVOKE_DIRECT_MUSIC = "direct-music"

# 只有 OpenAI 兼容的同步端点才允许真发请求（max_tokens=1 / input="hi" 的极小用量）；
# comfyui / MediaKit / 异步视频 / TTS 一律 skipped —— 探活不能产生真实任务与费用。
PROBEABLE_INVOKE_KINDS = frozenset({INVOKE_OPENAI_CHAT, INVOKE_OPENAI_EMBEDDINGS})

STATUS_MAPPED = "mapped"
STATUS_UNMAPPED = "unmapped"
STATUS_NO_CHANNEL = "noChannel"
STATUS_STALE = "stale"
STATUS_CAPABILITY_MISMATCH = "capabilityMismatch"

# [aipdd fork delta] req33 §6.3：广场条目把实测缓存里的结论透出去。
# ``unprobed`` = 这个调用协议根本没被实测覆盖（MediaKit / ComfyUI / 异步视频 / TTS
# / embeddings），前端不该拿它当「不行」；``unknown`` = 测过但上游没给明确结论，
# 按「拿不准就保留」照旧展示。
PROBE_STATE_UNPROBED = "unprobed"
PROBE_STATE_UNUSABLE = frozenset({"not-granted", "not-vision"})

PLAZA_PROBE_TIMEOUT_SECONDS = 20.0
API_KEY_PLACEHOLDER = "<API_KEY>"

# [aipdd fork delta req41] 调用示例里「对外可复制」的网关地址**不猜主机名**。外部
# NewAPI 网关有无数个，地址是部署方自己的数据（跟渠道 / 模型名同一类，代码不能比
# 部署更早知道）：旧实现把容器内主机名硬改成 ``localhost``，等于假定「用户一定在这
# 台机器上」，局域网 / 域名反代部署下复制出来的示例地址全部连不通。
# 现在的优先级：显式 ``NEWAPI_PUBLIC_BASE_URL`` > 当前请求自己的 origin（浏览器能打开
# 这个页面就说明该 origin 对它可达；对外出口固定是 ``/gateway/v1``，见
# frontend/docker/nginx.conf.template 与 vite dev proxy）> 如实返回生效网关地址（后台
# 任务等无请求上下文时）。
GATEWAY_PUBLIC_PATH = "/gateway/v1"

# [aipdd fork delta req41] 容器网络内的服务名（``http://newapi:3000`` 这类）只能本
# 集群内解析，把它当「给用户复制的对外地址」是误导，也会泄露部署拓扑。判定不拿具体
# 主机名列表硬编码：带不出点的 host 就是 compose / k8s 服务名（``localhost`` 除外 ——
# 那是部署方自己在配置里写的、本机可达的地址，照旧如实给出）。
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})

_REQUEST_ORIGIN: ContextVar[str] = ContextVar("novelvideo_request_origin", default="")


def set_request_origin(origin: str) -> None:
    """路由层记下「本次请求自己的 origin」（scheme://host[:port]，不带尾斜杠）。"""

    _REQUEST_ORIGIN.set(str(origin or "").strip().rstrip("/"))


def _clean(value: Any) -> str:
    return str(value or "").strip()


# [aipdd fork delta] 渠道展示名 override，与前端 settings / 模型广场的
# FORK_PROVIDER_LABELS 对齐：aipdd 统一显示 AIPDD；newapi（用户自配的 OpenAI
# 兼容外部网关）显示 NewAPI；openai 是用户附加在网关上的「其它额外渠道」
# （不在默认四渠道之列）显示 OpenAI；volcengine（火山方舟）显示 VolcEngine；
# mediakit（火山 AI MediaKit）虽作为独立产品线条目展示，但渠道归属 VolcEngine
#（同为火山引擎，只是 Base URL / API Key 独立），不让裸 provider id 漏进渠道筛选；
# 其余渠道取其自身 id。仅作用于「未显式传展示名」的条目（gateway 别名 / 媒体 /
# embedding / env-override），ComfyUI 等自带固定展示名的条目不受影响。
_FORK_PROVIDER_LABELS: dict[str, str] = {
    "aipdd": "AIPDD",
    "newapi": "NewAPI",
    # 「其它额外渠道」：独立于默认几家的附加渠道，展示 OpenAI 而非裸 id。
    "openai": "OpenAI",
    "volcengine": "VolcEngine",
    "mediakit": "火山 AI MediaKit",
    # [aipdd fork delta req46] MiniMax 双站渠道（海外 minimax / 国内 minimax_cn）：
    # 不登记就会把裸 provider id 漏到广场筛选器上。中文口径同 channels.py 合成行，
    # 国内站的按语言取词由前端 `brand.minimax_cn` 覆盖（后端无语言上下文）。
    "minimax": "MiniMax",
    "minimax_cn": "MiniMax 国内站",
    # [aipdd fork delta req48] OpenAI / Claude / Gemini 三家官方渠道：不登记就会把
    # 裸 provider id 漏到广场筛选器上。口径与 channels.py 的渠道目录行、前端
    # FORK_PROVIDER_LABELS 逐项对齐（openai_official 展示 OpenAI，与「其它额外
    # 渠道」的裸 openai 分得开）。
    "openai_official": "OpenAI",
    "anthropic": "Claude",
    "gemini": "Gemini",
}


# [aipdd fork delta] 「环境变量」分区只保留本 fork 自建 / 已接入渠道的模型：
# AIPDD、外部 NewAPI（newapi）与其它额外渠道（openai）、火山引擎系（volcengine /
# ark / doubao / doubaovideo / jimeng）、ComfyUI、火山 AI MediaKit、MiniMax 双站
# （minimax / minimax_cn）、以及 req48 接入的三家官方渠道（openai_official /
# anthropic / gemini）。功能位被外部渠道（或已移除的映射）接管时不再作为「环境变量
# 模型」展示。口径与 _FORK_PROVIDER_LABELS 逐项对齐：登记了展示名的渠道必须同步进本
# 白名单，否则指向该渠道的功能位会被 _env_override_visible 静默隐藏（违反「绝不静默消失」）。
_FORK_ENV_OVERRIDE_PROVIDERS: frozenset[str] = frozenset(
    {
        "aipdd",
        "openai",
        "newapi",
        "volcengine",
        "ark",
        "doubao",
        "doubaovideo",
        "jimeng",
        "comfyui",
        "mediakit",
        # [aipdd fork delta req46] MiniMax 双站。
        "minimax",
        "minimax_cn",
        # [aipdd fork delta req48] OpenAI / Claude / Gemini 官方渠道。
        "openai_official",
        "anthropic",
        "gemini",
    }
)


def _env_override_visible(provider: str) -> bool:
    """空 provider = 未登记的内置别名，保留；非空必须是本 fork 允许的渠道。"""
    return not provider or provider in _FORK_ENV_OVERRIDE_PROVIDERS


def _channel_label(provider: str) -> str:
    return _FORK_PROVIDER_LABELS.get(provider, "") or provider


def _effective_feature_routes() -> dict[str, tuple[str, str]]:
    """功能位 -> ``(实际生效的模型名, 命中层级)``。

    层级词表对齐 ``config.get_newapi_text_model_name`` 的三级优先级：per-env 覆盖 >
    全局兜底（TEXT/VISION_MODEL_FALLBACK）> 槽位别名。

    [aipdd fork delta req40] 功能位清单直接读 ``model_slots`` 登记表（旧
    ``DEFAULT_TEXT_MODEL_BY_ENV`` 表已并入）。解析不出「目录里真实存在且已绑定」的模
    型时标记为 ``unbound`` 而不是假装能用官方别名 —— 广场显示的就是实际会走的路由。
    """
    from novelvideo import config as _config

    routes: dict[str, tuple[str, str]] = {}
    for env, slot in sorted(FEATURE_SLOTS.items()):
        default_alias = slot.alias
        fallback_env = (
            "VISION_MODEL_FALLBACK"
            if slot.capability == "vision"
            else "TEXT_MODEL_FALLBACK"
        )
        per_env = _clean(os.environ.get(env))
        global_fallback = _clean(os.environ.get(fallback_env))
        if per_env:
            routes[env] = (per_env, _LAYER_PER_ENV)
        elif global_fallback:
            routes[env] = (global_fallback, _LAYER_FALLBACK)
        else:
            try:
                effective = _clean(
                    _config.get_newapi_text_model_name(
                        env,
                        _clean(default_alias),
                        # 视觉位只能被 VISION_MODEL_FALLBACK 接管：把纯文本兜底塞进
                        # 多模态请求就是 250828 那类 400 的源头。
                        fallback_env=fallback_env,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - 目录不因单个功能位解析失败而崩
                logger.debug(
                    "[aipdd fork delta] WP-E: env layer %s unresolved: %s", env, exc
                )
                routes[env] = (_clean(default_alias), _LAYER_UNBOUND)
                continue
            routes[env] = (effective or _clean(default_alias), _LAYER_DEFAULT_ALIAS)
    return routes


def _is_container_internal_host(url: str) -> bool:
    """地址主机部分是不是容器网络服务名（单段无点，且不是 localhost 这类本机地址）。"""

    text = str(url or "").strip()
    if "://" in text:
        text = text.split("://", 1)[1]
    host = text.split("/", 1)[0].rsplit("@", 1)[-1]
    if host.startswith("["):
        host = host[1:].split("]", 1)[0]
    elif ":" in host:
        host = host.rsplit(":", 1)[0]
    if not host or host.lower() in _LOCAL_HOSTS:
        return False
    return "." not in host


def newapi_public_base_url(*, base_url: str | None = None) -> str:
    """调用示例里对外可复制的网关地址（见上面 req41 注：不猜主机名）。"""
    override = _clean(os.environ.get("NEWAPI_PUBLIC_BASE_URL"))
    if override:
        return normalize_relay_base_url(override)
    origin = _clean(_REQUEST_ORIGIN.get())
    if origin:
        return normalize_relay_base_url(f"{origin}{GATEWAY_PUBLIC_PATH}")
    base = _clean(base_url) or _clean(get_effective_newapi_config().base_url)
    if not base:
        return ""
    if _is_container_internal_host(base):
        # 无请求上下文（后台任务 / 脚本）且生效地址是容器内服务名：宁可不给地址，
        # 也不把集群内部主机名当成用户能打开的入口——那是假装知道公网入口在哪。
        return ""
    # 其余情况不改写：部署方在配置里填了什么就照什么给。
    return normalize_relay_base_url(base)


# [aipdd fork delta req40] 能力口径：条目显式声明 > **渠道目录声明** > 关键词分类。
# 目录里的 ``inferred`` 条目不算声明（那是我们按名字猜的），只有渠道自己写了
# kind / modalities 才直接采信 —— 消掉遗留项「视觉能力位关键词漏网面」。
PLAZA_CAPABILITY_SET = frozenset(
    {
        "text",
        "vision",
        "embedding",
        "image",
        "video",
        "audio",
        "music",
        "asr",
        "decision",
    }
)


def _catalog_entry_for(name: str, provider: str = "") -> dict[str, Any] | None:
    """按调用名取渠道目录条目（复合名先拆出真名；provider 缺省时跨渠道按名反查）。"""

    spot_provider, model_name = split_provider_spot_model(name)
    return upstream_catalog_entry(model_name, provider or spot_provider)


def _catalog_capability(name: str, provider: str = "") -> str:
    """目录条目**声明**的媒体类型；推断条目返回空串（交给调用面的关键词兜底）。"""

    entry = _catalog_entry_for(name, provider)
    if entry is None or entry.get("inferred"):
        return ""
    media_type = _clean(entry.get("mediaType")).lower()
    return media_type if media_type in PLAZA_CAPABILITY_SET else ""


def _alias_capability(alias: str, entry: dict[str, Any], provider: str = "") -> str:
    """已声明的能力位优先；其次渠道目录；最后才按名字猜（七分类）。

    [aipdd fork delta] WP-A 的一次性反向导入会把网关 mapping 里项目侧没有媒体
    元数据的条目（如「AP Seedance-2.0 标准版」/ ap-jimeng-image-4.0）一并收进
    别名表，它们事实上是图/影模型：当成 openai-chat 展示，调用示例和探活都是错的。
    [aipdd fork delta req40] 中间插入目录层之后，新命名风格的多模态模型不再依赖
    关键词表命中（旧坑：名字里没有 vision/vl 就被判成纯文本）。
    """
    declared = _clean(entry.get("capability")).lower()
    if declared in PLAZA_CAPABILITY_SET:
        return declared
    catalog_kind = _catalog_capability(alias, provider)
    if catalog_kind:
        return catalog_kind
    kind = guess_model_kind(alias)
    if kind in {"embedding", "image", "video", "audio", "music"}:
        return kind
    return "vision" if kind == "vision" else "text"


def _passthrough_capability(
    name: str,
    tracked: dict[str, Any],
    provider: str = "",
) -> str:
    """直通条目（渠道自己的真名）的能力：声明 > 目录 > 关键词 + 视觉资格兜底。

    与 :func:`_alias_capability` 同一优先级，但多一条：名字归类为文本而目录/关键词
    说它能读图时仍归 vision（doubao-seed 系列就是这类多模态输入模型）。
    """

    declared = _clean(tracked.get("capability")).lower()
    if declared in PLAZA_CAPABILITY_SET:
        return declared
    catalog_kind = _catalog_capability(name, provider)
    if catalog_kind:
        return catalog_kind
    kind = guess_model_kind(name)
    if kind in {"embedding", "image", "video", "audio", "music"}:
        return kind
    from novelvideo.newapi_provisioner import model_supports_vision

    if kind == "vision" or model_supports_vision(name, provider):
        return "vision"
    return "text"


# 媒体类能力的调用形态 + **兜底**端点：渠道目录声明了 ``endpoints`` 时以声明为准
# （见 :func:`_media_invoke`），这张表只在通用 OpenAI 兼容渠道没写元数据时给出
# 兜底路径 —— 端点不再是「代码认定上游长这样」。
_ALIAS_MEDIA_INVOKE: dict[str, tuple[str, str]] = {
    "image": (INVOKE_OPENAI_IMAGES, "images/generations"),
    "audio": (INVOKE_TTS_VOICE, "audio/speech"),
    # [aipdd fork delta req46] 音乐不经网关：网关里没有 music 路由，这里的端点只是
    # 厂商直连协议的路径名（目录声明了端点时仍以声明为准）。
    "music": (INVOKE_DIRECT_MUSIC, "music_generation"),
    "asr": (INVOKE_ASR_TRANSCRIPTION, "audio/transcriptions"),
    "video": (INVOKE_ASYNC_VIDEO_TASK, "video/generations"),
    "embedding": (INVOKE_OPENAI_EMBEDDINGS, "embeddings"),
    "decision": (INVOKE_SYSTEMONE, "systemone"),
}


def _media_invoke(
    capability: str,
    *,
    model_name: str = "",
    provider: str = "",
) -> tuple[str, str] | None:
    """能力 → ``(调用形态, 端点)``；端点优先取渠道目录声明，文本 / 未知能力返回 None。"""

    hit = _ALIAS_MEDIA_INVOKE.get(str(capability or "").strip().lower())
    if not hit:
        return None
    invoke_kind, fallback_endpoint = hit
    entry = _catalog_entry_for(model_name, provider) if model_name else None
    if entry is not None:
        from novelvideo.upstream_catalog import endpoint_for

        declared = endpoint_for(entry, fallback_endpoint)
        if declared:
            return invoke_kind, declared
    return invoke_kind, fallback_endpoint


def _entry(
    *,
    entry_id: str,
    display_name: str,
    connector: str,
    invoke_kind: str,
    capabilities: list[str],
    model_name: str,
    provider: str = "",
    channel_name: str = "",
    upstream_model: str = "",
    vendor: str = "",
    base_url: str = "",
    status: str = STATUS_UNMAPPED,
    in_use_by: list[str] | None = None,
    sort_order: int = 100,
    note: str = "",
    api_key_preview: str = "",
    endpoint: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": entry_id,
        "displayName": display_name,
        "connector": connector,
        "invokeKind": invoke_kind,
        "capabilities": [c for c in capabilities if c in PLAZA_CAPABILITIES],
        "modelName": model_name,
        "provider": provider,
        "channelName": channel_name or _channel_label(provider),
        "upstreamModel": upstream_model,
        "vendor": vendor or provider,
        "baseUrl": base_url,
        "endpoint": endpoint,
        "status": status,
        "inUseBy": list(in_use_by or []),
        "sortOrder": sort_order,
        "note": note,
        "apiKeyPreview": api_key_preview,
    }
    if extra:
        entry.update(extra)
    entry["examples"] = build_invocation_examples(entry)
    entry["testable"] = invoke_kind in PROBEABLE_INVOKE_KINDS
    return entry


def _channel_is_configured(channel: dict[str, Any]) -> bool:
    if not channel:
        return False
    if _clean(channel.get("provider")).lower() == "comfyui":
        return bool(_clean(channel.get("baseUrl")) and channel.get("settings"))
    return bool(_clean(channel.get("upstreamKey")))


def _models_by_provider(
    channels: dict[str, dict[str, Any]],
) -> dict[str, set[str]]:
    """上游真名 -> 服务它的渠道集合（按渠道存档顺序；同一真名多渠道路由即隐式备援）。

    [aipdd fork delta] 渠道归属反查的唯一真源：模型广场各处要回答「这个模型实际
    由哪个渠道供着」，快照（零远程）是唯一依据；查不到即无渠道 —— 不以任何身份
    回落兜底（LocalNewAPI 被误列为渠道的根因）；newapi 渠道只有在快照确有它的
    上游模型时才作为渠道出现。
    """

    from novelvideo.newapi_provisioner import upstream_passthrough_models

    by_name: dict[str, set[str]] = {}
    for provider in channels:
        if provider == "comfyui":
            continue
        for name in upstream_passthrough_models(provider, from_snapshot=True):
            clean_name = _clean(name)
            if clean_name and "," not in clean_name:
                by_name.setdefault(clean_name, set()).add(provider)
    return by_name


def _pick_provider_for_model(
    name: str,
    by_name: dict[str, set[str]],
    channels: dict[str, dict[str, Any]],
) -> str:
    """解析一个调用名归属的渠道：复合名取前缀；裸名必须「恰好一个渠道拥有」。

    [aipdd fork delta] 拍平改造：不再「按渠道存档顺序取第一个」——那正是要移除的
    隐式裁决；无 / 多归属一律返回空串，由用户显式选复合名。
    """

    from novelvideo.model_gateway_settings import split_provider_spot_model

    provider, model = split_provider_spot_model(_clean(name))
    if provider:
        return provider
    providers = by_name.get(model)
    if not providers or len(providers) != 1:
        return ""
    return next(iter(providers))


# ---------------------------------------------------------------------------
# connector: gateway（上游真名直通 + 媒体模型映射 + embedding）
# ---------------------------------------------------------------------------


def _route_flat_target(
    model: str,
    alias_mappings: dict[str, Any],
    by_name: dict[str, set[str]] | None = None,
) -> tuple[str, str]:
    """功能位生效值 -> (渠道, 上游真名)。DC-* 别名换 provider/target；复合名取前缀；

    裸名只在「恰好一个渠道拥有」时归属那个渠道，否则渠道留空（不猜）。
    """

    from novelvideo.model_gateway_settings import split_provider_spot_model

    clean = _clean(model)
    tracked = alias_mappings.get(clean)
    if isinstance(tracked, dict):
        provider = _clean(tracked.get("provider")).lower()
        target = _clean(tracked.get("target"))
        if provider and target:
            return provider, target
    provider, bare = split_provider_spot_model(clean)
    if provider:
        return provider, bare
    owners = (by_name or {}).get(bare)
    if owners and len(owners) == 1:
        return next(iter(owners)), bare
    return "", bare


def _gateway_passthrough_entries(
    routes: dict[str, tuple[str, str]],
    health: dict[str, Any],
) -> list[dict[str, Any]]:
    """渠道上游模型目录（拍平）：每个 (渠道, 模型) 一条，调用名 = 复合名。

    [aipdd fork delta] 拍平改造：模型广场是对外调用目录，每个渠道名下每个上游
    模型各占一条（清单快照 ∪ 别名真源声明的 target ∪ 媒体 / embedding 上游名，
    全部零远程）。调用名一律用复合名 ``{provider}::{model}``——同名模型在不同
    渠道各有一条、由用户显式选择，系统不做归属让位、优先级或备援的隐式裁决。
    已被媒体条目 / embedding 条目承担的真实名不在这里重复列。
    """
    from novelvideo.model_gateway_settings import provider_spot_model
    from novelvideo.newapi_provisioner import upstream_passthrough_models

    alias_mappings = get_newapi_model_alias_mappings()
    media_mappings = get_newapi_media_model_mappings()
    embedding = get_newapi_embedding_model_config()
    channels = {
        channel["provider"]: channel for channel in get_newapi_provider_channels()
    }
    alias_health = (
        health.get("aliases") if isinstance(health.get("aliases"), dict) else {}
    )

    # 已被媒体 / 嵌入条目覆盖的真实名：调用名由那些条目承担，同一模型不摆两张卡。
    covered: set[str] = set()
    for model, item in media_mappings.items():
        if not isinstance(item, dict):
            continue
        if _clean(item.get("provider")).lower() == "comfyui":
            continue
        covered.add(_clean(model))
        covered.add(_clean(item.get("upstreamModel")))
    covered.add(_clean(embedding.get("upstreamModel")))
    covered.add(_clean(embedding.get("internalModel")))
    covered.discard("")

    # 真名条目的能力声明 / 备援 / 健康判定，挂的是「以它为 target 的别名」
    # （DC-freezone-vision-LLM -> doubao-vision）：按真名反查补上，否则这些条目
    # 会丢掉 capability=vision 之类的声明、备援信息与 stale 判定。
    declared_by_target: dict[str, dict[str, Any]] = {}
    for alias, item in alias_mappings.items():
        if not isinstance(item, dict):
            continue
        target = _clean(item.get("target"))
        if target:
            declared_by_target.setdefault(target, item)
    health_by_target: dict[str, dict[str, Any]] = {}
    for alias, verdict in alias_health.items():
        item = alias_mappings.get(alias)
        if isinstance(item, dict) and isinstance(verdict, dict):
            target = _clean(item.get("target"))
            if target:
                health_by_target.setdefault(target, verdict)

    # 裸名唯一归属反查（in_use_by 判定用；零远程）。
    by_name = _models_by_provider(channels)

    base_url = newapi_public_base_url()
    gateway_key = _clean(get_effective_newapi_config().api_key)
    entries: list[dict[str, Any]] = []
    index = 0
    for provider, channel in channels.items():
        # comfyui 无 OpenAI 兼容清单，归 comfyui 分区，不参与拍平直通。
        if provider == "comfyui":
            continue
        for name in upstream_passthrough_models(provider, from_snapshot=True):
            clean_name = _clean(name)
            if not clean_name or clean_name in covered:
                continue
            call_name = provider_spot_model(provider, clean_name)
            tracked = alias_mappings.get(clean_name)
            if not isinstance(tracked, dict):
                tracked = declared_by_target.get(clean_name) or {}
            capability = _passthrough_capability(clean_name, tracked, provider)
            media_invoke = _media_invoke(
                capability, model_name=clean_name, provider=provider
            )
            invoke_kind = media_invoke[0] if media_invoke else INVOKE_OPENAI_CHAT
            endpoint = media_invoke[1] if media_invoke else ""
            configured = _channel_is_configured(channel)
            # 拍平后每条就是「该渠道自己的模型」：渠道可用则无备注，不可用才提示。
            note = "" if configured else "noChannel"
            tracked_health = alias_health.get(clean_name)
            if not isinstance(tracked_health, dict):
                tracked_health = health_by_target.get(clean_name)
            tracked_health = tracked_health if isinstance(tracked_health, dict) else {}
            status = _clean(tracked_health.get("status")) or (
                STATUS_MAPPED if configured else STATUS_NO_CHANNEL
            )
            entries.append(
                _entry(
                    # 调用名 = 复合名（渠道::模型），天然唯一；展示名保留上游真名，
                    # 渠道标徽由 provider / channelName 渲染 —— 同名双渠道两条并列。
                    entry_id=f"{CONNECTOR_GATEWAY}:{call_name}",
                    display_name=clean_name,
                    connector=CONNECTOR_GATEWAY,
                    invoke_kind=invoke_kind,
                    capabilities=(
                        [capability]
                        if media_invoke
                        else (
                            ["vision", "text"]
                            if capability == "vision"
                            else ["text"]
                        )
                    ),
                    model_name=call_name,
                    provider=provider,
                    upstream_model=clean_name,
                    vendor=provider,
                    base_url=base_url,
                    endpoint=endpoint,
                    status=status,
                    in_use_by=[
                        env
                        for env, (model, _layer) in routes.items()
                        if _route_flat_target(model, alias_mappings, by_name)
                        == (provider, clean_name)
                    ],
                    sort_order=index,
                    note=note,
                    api_key_preview=mask_secret(gateway_key),
                )
            )
            index += 1
    return entries


def _gateway_media_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    mappings = get_newapi_media_model_mappings()
    channels = {channel["provider"]: channel for channel in get_newapi_provider_channels()}
    base_url = newapi_public_base_url()
    gateway_key = _clean(get_effective_newapi_config().api_key)
    defaults = {
        "image": resolve_default_media_model("image"),
        "video": resolve_default_media_model("video"),
        "audio": resolve_default_media_model("audio"),
    }
    for index, (model, item) in enumerate(sorted(mappings.items())):
        if not isinstance(item, dict):
            continue
        provider = _clean(item.get("provider")).lower()
        # comfyui 渠道的模型归 comfyui 分区，别在这里重复列一遍。
        if not model or provider == "comfyui":
            continue
        media_type = _clean(item.get("mediaType")).lower() or "image"
        enabled = item.get("enabled", True) is not False
        channel = channels.get(provider) or {}
        if not enabled:
            status, note = STATUS_NO_CHANNEL, "disabled"
        elif provider not in channels or not _channel_is_configured(channel):
            status, note = STATUS_NO_CHANNEL, "noChannel"
        else:
            status, note = STATUS_MAPPED, ""
        # [aipdd fork delta] 调用名 = 上游真名：外部调用者拿真名直接调（登记名如
        # seedance-2.0 只留在项目内部 / 渠道映射里做桥接，不再是叫别人用的名）。
        call_name = _clean(item.get("upstreamModel")) or model
        # [aipdd fork delta req41] 媒体行也不再把端点写死在代码里：走 ``_media_invoke``，
        # 渠道目录声明了什么就给人复制什么。以前这张表统一按通用 OpenAI 路径写，于是
        # AIPDD 的视频卡上显示 ``video/generations``（上游只认 ``/videos``，照抄必 404）。
        invoke_kind, endpoint = _media_invoke(
            media_type,
            model_name=call_name,
            provider=provider,
        ) or (INVOKE_ASYNC_VIDEO_TASK, "video/generations")
        entries.append(
            _entry(
                entry_id=f"{CONNECTOR_GATEWAY}:{model}",
                display_name=_clean(item.get("label")) or model,
                connector=CONNECTOR_GATEWAY,
                invoke_kind=invoke_kind,
                capabilities=[media_type if media_type in PLAZA_CAPABILITIES else "video"],
                model_name=call_name,
                provider=provider,
                channel_name=_channel_label(provider),
                upstream_model=call_name,
                base_url=base_url,
                endpoint=endpoint,
                status=status,
                note=note,
                in_use_by=[f"default_{media_type}_model"]
                if defaults.get(media_type) == model
                else [],
                sort_order=210 + index,
                api_key_preview=mask_secret(gateway_key),
                extra={"mediaType": media_type},
            )
        )
    return entries


def _embedding_entry() -> list[dict[str, Any]]:
    config = get_newapi_embedding_model_config()
    provider = _clean(config.get("provider")).lower()
    upstream_name = _clean(config.get("upstreamModel"))
    if not provider or not upstream_name:
        # 未配置就没有可对外教的调用名：宁可不出卡，也不把内部名当模型名摆出来。
        return []
    channel = get_newapi_provider_channel(provider) or {}
    if provider not in {c["provider"] for c in get_newapi_provider_channels()} or not _channel_is_configured(channel):
        status, note = STATUS_NO_CHANNEL, "noChannel"
    else:
        status, note = STATUS_MAPPED, ""
    return [
        _entry(
            entry_id=f"{CONNECTOR_GATEWAY}:{upstream_name}",
            display_name=upstream_name,
            connector=CONNECTOR_GATEWAY,
            # [aipdd fork delta] 调用名 = 上游 embedding 真名（外部调用者拿真名直接
            # 调 ``/embeddings``；内部名 DC-cognee-embedding 只留给项目自身与映射）。
            invoke_kind=INVOKE_OPENAI_EMBEDDINGS,
            capabilities=["embedding"],
            model_name=upstream_name,
            provider=provider,
            channel_name=_channel_label(provider),
            upstream_model=upstream_name,
            base_url=newapi_public_base_url(),
            endpoint="embeddings",
            status=status,
            note=note,
            in_use_by=["knowledge_graph_embedding"],
            sort_order=200,
            api_key_preview=mask_secret(_clean(get_effective_newapi_config().api_key)),
            extra={
                "dimension": config.get("dimension"),
                "batchSize": config.get("batchSize"),
            },
        )
    ]


# ---------------------------------------------------------------------------
# connector: comfyui（本地工作流模型）
# ---------------------------------------------------------------------------


def _comfyui_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    mappings = get_newapi_media_model_mappings()
    comfyui_models = {
        _clean(model): item
        for model, item in mappings.items()
        if _clean(item.get("provider")).lower() == "comfyui" and _clean(model)
    }
    channels = [
        channel
        for channel in get_newapi_provider_channels()
        if channel["provider"] == "comfyui"
    ]
    names: list[str] = sorted(set(comfyui_models))
    for channel in channels:
        try:
            models, _routes = parse_comfyui_channel_workflows(channel.get("settings") or {})
        except (ValueError, TypeError, AttributeError) as exc:
            logger.debug("[aipdd fork delta] WP-E: comfyui channel unreadable: %s", exc)
            continue
        for model in models:
            model = _clean(model)
            if model and model not in names:
                names.append(model)
    base_channel = channels[0] if channels else {}
    base_url = _clean(base_channel.get("baseUrl"))
    for index, model in enumerate(names):
        mapping = comfyui_models.get(model) or {}
        media_type = (
            _clean(mapping.get("mediaType")).lower()
            or detect_comfyui_media_type(base_channel.get("settings") or {}, model)
            or "video"
        )
        if not base_url:
            status, note = STATUS_NO_CHANNEL, "noChannel"
        elif mapping and mapping.get("enabled", True) is False:
            status, note = STATUS_NO_CHANNEL, "disabled"
        else:
            status, note = STATUS_MAPPED, ""
        entries.append(
            _entry(
                entry_id=f"{CONNECTOR_COMFYUI}:{model}",
                display_name=_clean(mapping.get("label")) or model,
                connector=CONNECTOR_COMFYUI,
                invoke_kind=INVOKE_COMFYUI_WORKFLOW,
                capabilities=[media_type if media_type in PLAZA_CAPABILITIES else "video"],
                model_name=model,
                provider="comfyui",
                channel_name="ComfyUI",
                vendor="ComfyUI",
                upstream_model=model,
                base_url=base_url,
                status=status,
                note=note,
                in_use_by=[f"default_{media_type}_model"]
                if resolve_default_media_model(media_type) == model
                else [],
                sort_order=index,
                extra={"mediaType": media_type},
            )
        )
    return entries


# ---------------------------------------------------------------------------
# connector: mediakit（火山 AI MediaKit 生成式视频高清）
# ---------------------------------------------------------------------------


def _mediakit_entries() -> list[dict[str, Any]]:
    config = get_effective_mediakit_config()
    forced_ffmpeg = _clean(os.environ.get("VIDEO_ENHANCE_BACKEND")).lower() == "ffmpeg"
    if forced_ffmpeg:
        status, note = STATUS_NO_CHANNEL, "backendForcedFfmpeg"
    elif not _clean(config.api_key):
        status, note = STATUS_NO_CHANNEL, "noChannel"
    else:
        status, note = STATUS_MAPPED, ""
    return [
        _entry(
            entry_id=f"{CONNECTOR_MEDIAKIT}:video-enhance",
            display_name="MediaKit Generative Video Enhance",
            connector=CONNECTOR_MEDIAKIT,
            invoke_kind=INVOKE_MEDIAKIT_TASK,
            capabilities=["enhance", "video"],
            model_name="enhance-video-generative",
            provider="mediakit",
            channel_name="火山 AI MediaKit",
            vendor="Volcano Engine",
            upstream_model="enhance-video-generative",
            base_url=_clean(config.base_url),
            endpoint="tools/enhance-video-generative",
            status=status,
            note=note,
            in_use_by=["video_enhance"],
            api_key_preview=mask_secret(_clean(config.api_key)),
            extra={"backend": config.backend, "configSource": config.source},
        )
    ]


# ---------------------------------------------------------------------------
# connector: env-override（环境变量级可配置位，并标出当前哪一层生效）
# ---------------------------------------------------------------------------

#  get_newapi_text_model_name 的优先级：per-env > 全局兜底 > 官方别名 > 硬编码兑底。
_LAYER_PER_ENV = "perEnv"
_LAYER_FALLBACK = "globalFallback"
_LAYER_DEFAULT_ALIAS = "defaultAlias"
# [aipdd fork delta req40] 槽位未绑定 / 绑定目标已从渠道下架：广场照实标记，不再显示成可用。
_LAYER_UNBOUND = "unbound"

# 这些 env 实际设置的位（语音合成相关项已随 audio 来源整体移除）。
_ENV_SLOT_NAMES: tuple[str, ...] = (
    "TEXT_MODEL_FALLBACK",
    "VISION_MODEL_FALLBACK",
    "NEWAPI_TEXT_MODEL",
    "NEWAPI_IMAGE_MODEL",
    "NEWAPI_NANOBANANA2_MODEL",
    "COGNEE_EMBEDDING_MODEL",
    "NEWAPI_PUBLIC_BASE_URL",
)


def _env_override_entries(routes: dict[str, tuple[str, str]]) -> list[dict[str, Any]]:
    """每个功能位一条，并标出当前哪一层生效。

    没有 env 覆盖的功能位会作为 ``defaultAlias`` 列出 —— 广场要说清「这个步骤
    现在到底跑在哪个模型上」，只列已设的 env 反而看不到全貌。
    """
    entries: list[dict[str, Any]] = []
    alias_mappings = get_newapi_model_alias_mappings()
    gateway = get_effective_newapi_config()
    base_url = newapi_public_base_url(base_url=gateway.base_url)
    has_key = bool(_clean(gateway.api_key))
    # [aipdd fork delta] 渠道归属反查真源（快照零远程）：未登记归属的功能位按上游
    # 真名反查服务渠道，查不到留空 —— 绝不回落成「newapi」（网关自身执行身份）。
    channels = {
        channel["provider"]: channel for channel in get_newapi_provider_channels()
    }
    by_name = _models_by_provider(channels)

    for index, (env, (effective_model, layer)) in enumerate(sorted(routes.items())):
        if not effective_model:
            continue
        tracked = alias_mappings.get(effective_model)
        tracked = tracked if isinstance(tracked, dict) else {}
        provider = _clean(tracked.get("provider")).lower()
        # [aipdd fork delta] 指向外部渠道的功能位不进环境变量分区（清理规则）。
        if not _env_override_visible(provider):
            continue
        # [aipdd fork delta] 调用名 = 上游真名（别名换 target）：示例里出现的名字
        # 必须是能直接调通的模型名，DC-* 这类内部功能位名不作为调用对象。
        call_name = _clean(tracked.get("target")) or effective_model
        if not provider:
            provider = _pick_provider_for_model(
                call_name, by_name, channels
            ) or _pick_provider_for_model(effective_model, by_name, channels)
            if not _env_override_visible(provider):
                continue
        capability = _alias_capability(call_name, tracked, provider)
        media_invoke = _media_invoke(capability, model_name=call_name, provider=provider)
        entries.append(
            _entry(
                # 同一个 env 只一条；env 名是业务标识、模型名才是展示与调用对象，
                # 所以标题放真名、env 回填 extra，id 保持幂等。
                entry_id=f"{CONNECTOR_ENV_OVERRIDE}:{env}",
                display_name=call_name,
                connector=CONNECTOR_ENV_OVERRIDE,
                invoke_kind=media_invoke[0] if media_invoke else INVOKE_OPENAI_CHAT,
                capabilities=[capability]
                if media_invoke
                else (["vision", "text"] if capability == "vision" else ["text"]),
                model_name=call_name,
                provider=provider,
                upstream_model=call_name,
                vendor="Environment override",
                base_url=base_url,
                # 别名目标→看真源是否已映射；直接写的真实模型名→网关在册即可调。
                status=STATUS_MAPPED
                if has_key and (layer != _LAYER_DEFAULT_ALIAS or tracked)
                else STATUS_UNMAPPED
                if not tracked and layer == _LAYER_DEFAULT_ALIAS
                else STATUS_NO_CHANNEL,
                note=layer,
                in_use_by=[env],
                sort_order=index,
                api_key_preview=mask_secret(_clean(gateway.api_key)),
                extra={"env": env},
            )
        )

    for index, env in enumerate(_ENV_SLOT_NAMES):
        # [aipdd fork delta] 只指地址的环境变量不是模型：compose 里默认设了
        # NEWAPI_PUBLIC_BASE_URL 后，这里会凭空长出一张「模型名 = URL」的卡片，
        # 探活也会把 URL 当模型名发给网关。对外地址的展示位在 gateway.publicBaseUrl。
        if env == "NEWAPI_PUBLIC_BASE_URL":
            continue
        value = _clean(os.environ.get(env))
        if not value:
            continue
        capabilities = (
            ["embedding"]
            if env == "COGNEE_EMBEDDING_MODEL"
            else ["image"]
            if env in {"NEWAPI_IMAGE_MODEL", "NEWAPI_NANOBANANA2_MODEL"}
            else ["text", "vision"]
            if env == "VISION_MODEL_FALLBACK"
            else ["text"]
        )
        tracked = alias_mappings.get(value)
        tracked = tracked if isinstance(tracked, dict) else {}
        # [aipdd fork delta] 调用名 = 上游真名（别名换 target）；调用形式由槽位能力
        # 决定（嵌入/图像各自走对应的 OpenAI 兼容端点）。
        call_name = _clean(tracked.get("target")) or value
        # [aipdd fork delta] 未登记归属按上游真名反查服务渠道，查不到留空；不再
        # 回落成「newapi」（网关自身执行身份不是渠道）。
        provider = _clean(tracked.get("provider")).lower()
        if not provider:
            provider = _pick_provider_for_model(
                call_name, by_name, channels
            ) or _pick_provider_for_model(value, by_name, channels)
        # [aipdd fork delta] 与功能位循环同一条清理规则。
        if not _env_override_visible(provider):
            continue
        media_invoke = _media_invoke(
            capabilities[0], model_name=call_name, provider=provider
        )
        entries.append(
            _entry(
                entry_id=f"{CONNECTOR_ENV_OVERRIDE}:{env}",
                display_name=call_name,
                connector=CONNECTOR_ENV_OVERRIDE,
                invoke_kind=media_invoke[0] if media_invoke else INVOKE_OPENAI_CHAT,
                capabilities=capabilities,
                model_name=call_name,
                provider=provider,
                upstream_model=call_name,
                vendor="Environment override",
                base_url=base_url,
                status=STATUS_MAPPED,
                note="envSet",
                in_use_by=[env],
                sort_order=200 + index,
                api_key_preview=mask_secret(_clean(gateway.api_key)),
                extra={"env": env},
            )
        )
    return entries


# ---------------------------------------------------------------------------
# 调用示例
# ---------------------------------------------------------------------------


def _auth_header_block(api_key: str) -> str:
    return f'-H "Authorization: Bearer {api_key or API_KEY_PLACEHOLDER}"'


def _openai_curl(base_url: str, endpoint: str, payload: dict[str, Any], api_key: str) -> str:
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        f"curl -sS {base_url.rstrip('/')}/{endpoint} \\\n"
        f"  {_auth_header_block(api_key)} \\\n"
        f'  -H "Content-Type: application/json" \\\n'
        f"  -d '{body}'"
    )


def build_invocation_examples(entry: dict[str, Any]) -> dict[str, Any]:
    """按 ``invokeKind`` 生成可复制示例。密钥只回占位符，绝不回真值。"""
    invoke_kind = _clean(entry.get("invokeKind"))
    base_url = _clean(entry.get("baseUrl"))
    model_name = _clean(entry.get("modelName"))
    endpoint = _clean(entry.get("endpoint"))
    examples: dict[str, Any] = {}

    if invoke_kind == INVOKE_OPENAI_CHAT:
        examples["curl"] = _openai_curl(
            base_url,
            "chat/completions",
            {"model": model_name, "messages": [{"role": "user", "content": "你好"}], "max_tokens": 64},
            API_KEY_PLACEHOLDER,
        )
        examples["python"] = (
            "from openai import OpenAI\n\n"
            "client = OpenAI(base_url=\"" + base_url + "\", api_key=\"<API_KEY>\")\n"
            "resp = client.chat.completions.create(\n"
            "    model=\"" + model_name + "\",\n"
            "    messages=[{\"role\": \"user\", \"content\": \"你好\"}],\n"
            "    max_tokens=64,\n"
            ")\n"
            "print(resp.choices[0].message.content)\n"
        )
        examples["node"] = (
            "import OpenAI from \"openai\";\n\n"
            "const client = new OpenAI({ baseURL: \"" + base_url + "\", apiKey: \"<API_KEY>\" });\n"
            "const resp = await client.chat.completions.create({\n"
            "  model: \"" + model_name + "\",\n"
            "  messages: [{ role: \"user\", content: \"你好\" }],\n"
            "  max_tokens: 64,\n"
            "});\n"
            "console.log(resp.choices[0].message.content);\n"
        )
    elif invoke_kind == INVOKE_OPENAI_EMBEDDINGS:
        examples["curl"] = _openai_curl(
            base_url,
            endpoint or "embeddings",
            {"model": model_name, "input": "你好"},
            API_KEY_PLACEHOLDER,
        )
        examples["python"] = (
            "from openai import OpenAI\n\n"
            "client = OpenAI(base_url=\"" + base_url + "\", api_key=\"<API_KEY>\")\n"
            "resp = client.embeddings.create(model=\"" + model_name + "\", input=[\"你好\"])\n"
            "print(len(resp.data[0].embedding))\n"
        )
        examples["node"] = (
            "import OpenAI from \"openai\";\n\n"
            "const client = new OpenAI({ baseURL: \"" + base_url + "\", apiKey: \"<API_KEY>\" });\n"
            "const resp = await client.embeddings.create({ model: \"" + model_name + "\", input: [\"你好\"] });\n"
            "console.log(resp.data[0].embedding.length);\n"
        )
    elif invoke_kind == INVOKE_OPENAI_IMAGES:
        examples["curl"] = _openai_curl(
            base_url,
            endpoint or "images/generations",
            {"model": model_name, "prompt": "一只在雨夜里撑伞的柴犬", "size": "1024x1024", "n": 1},
            API_KEY_PLACEHOLDER,
        )
        examples["python"] = (
            "from openai import OpenAI\n\n"
            "client = OpenAI(base_url=\"" + base_url + "\", api_key=\"<API_KEY>\")\n"
            "resp = client.images.generate(model=\"" + model_name + "\", prompt=\"...\", size=\"1024x1024\")\n"
            "print(resp.data[0].url)\n"
        )
        # [aipdd fork delta req41] 目录里这个模型除了文生图还声明了 ``images/edits``
        # （图生图 / 图片编辑）时，卡内并列一条可复制示例。端点仍然照目录给，代码不
        # 自己拼路径 —— AIPDD 的两个路径都是上游事实，不能只教一半。
        from novelvideo.model_gateway_settings import catalog_endpoints
        from novelvideo.upstream_catalog import EP_IMAGES_EDITS

        edit_path = next(
            (
                path
                for path in catalog_endpoints(model_name, _clean(entry.get("provider")))
                if path.startswith(EP_IMAGES_EDITS)
            ),
            "",
        )
        if edit_path and base_url:
            examples["edit"] = _openai_curl(
                base_url,
                edit_path,
                {
                    "model": model_name,
                    "prompt": "把参考图改成雨夜霓虹风格，人物不变",
                    "image": "<IMAGE_URL>",
                    "size": "1024x1024",
                },
                API_KEY_PLACEHOLDER,
            )
    elif invoke_kind == INVOKE_ASYNC_VIDEO_TASK:
        examples["curl"] = _openai_curl(
            base_url,
            endpoint or "video/generations",
            {"model": model_name, "prompt": "镜头缓推，主角转身", "duration": 5},
            API_KEY_PLACEHOLDER,
        )
        # [aipdd fork delta req41] 轮询地址也照目录给：AIPDD 在条目里声明了
        # ``videos/{taskId}``，而旧代码把 ``video/generations/<TASK_ID>`` 写死 ——
        # 于是卡上提交走 ``/videos``、轮询走另一条路径，用户复制第二条必 404。
        from novelvideo.upstream_catalog import task_lookup_endpoint

        catalog_entry = (
            _catalog_entry_for(model_name, _clean(entry.get("provider")))
            if model_name
            else None
        )
        poll_path = task_lookup_endpoint(catalog_entry or {}, endpoint) or (
            f"{endpoint}/<TASK_ID>"
        )
        examples["poll"] = (
            f"curl -sS {base_url.rstrip('/')}/{poll_path} \\\n"
            f"  {_auth_header_block(API_KEY_PLACEHOLDER)}"
        )
        examples["note"] = "asyncVideoTask"
    elif invoke_kind == INVOKE_COMFYUI_WORKFLOW:
        examples["curl"] = (
            f"curl -sS {base_url.rstrip('/')}/api/prompt \\\n"
            '  -H "Content-Type: application/json" \\\n'
            "  -d '{\"prompt\": <API-FORMAT-WORKFLOW-JSON>, \"client_id\": \"plaza\"}'"
        )
        examples["poll"] = f"curl -sS {base_url.rstrip('/')}/api/history/<PROMPT_ID>"
        examples["note"] = "comfyuiWorkflow"
    elif invoke_kind == INVOKE_MEDIAKIT_TASK:
        examples["curl"] = _openai_curl(
            base_url,
            endpoint or "tools/enhance-video-generative",
            {"video_url": "mediakit://<FILE_ID>", "resolution": "1080p"},
            API_KEY_PLACEHOLDER,
        )
        examples["poll"] = (
            f"curl -sS {base_url.rstrip('/')}/tasks/<TASK_ID> \\\n"
            f"  {_auth_header_block(API_KEY_PLACEHOLDER)}"
        )
        examples["note"] = "mediakitTask"
    elif invoke_kind == INVOKE_TTS_VOICE:
        if endpoint:
            examples["curl"] = _openai_curl(
                base_url,
                endpoint,
                {"model": model_name, "input": "测试语音合成", "voice": "default"},
                API_KEY_PLACEHOLDER,
            )
        examples["note"] = "ttsVoice"
    elif invoke_kind == INVOKE_DIRECT_MUSIC:
        # [aipdd fork delta req46] 音乐示例按厂商直连协议给（prompt + lyrics），
        # 不假成 OpenAI 兼容形态；探活不真发请求（会产生真实计费）。
        if endpoint:
            examples["curl"] = _openai_curl(
                base_url,
                endpoint,
                {
                    "model": model_name,
                    "prompt": "轻快的流行摇滚，副歌开阔",
                    "lyrics": "[Verse]\n……\n[Chorus]\n……",
                },
                API_KEY_PLACEHOLDER,
            )
        examples["note"] = "musicDirect"
    return examples

# ---------------------------------------------------------------------------
# 聚合入口 + 探活
# ---------------------------------------------------------------------------


def _probe_caches() -> dict[str, dict[str, Any]]:
    """一次读两份 chat 实测缓存（文本 / 视觉各一份，互不干涉）。"""

    caches: dict[str, dict[str, Any]] = {}
    for kind, reader in (("text", get_text_model_probe), ("vision", get_vision_model_probe)):
        try:
            caches[kind] = reader() or {}
        except Exception:  # noqa: BLE001 - 探测缓存再不可靠也不能伤目录
            caches[kind] = {}
    return caches


def _entry_probe_kind(entry: dict[str, Any]) -> str:
    """该用哪份实测结论判这个条目。能力位优先，退一步才看名字关键词。"""

    capabilities = entry.get("capabilities") or []
    if "vision" in capabilities:
        return "vision"
    if "text" in capabilities:
        return "text"
    kind = guess_model_kind(str(entry.get("modelName") or ""))
    return kind if kind in ("text", "vision") else ""


def _annotate_probe_states(entries: list[dict[str, Any]]) -> None:
    """[aipdd fork delta] req33 §6.3：给广场条目补 ``probeState`` / ``probeUnusable``。

    本模块的不变量是「永不远程调用」，所以这里只读已有实测缓存。只有
    ``openai-chat`` 协议的名字才可能有结论；查名字时先看调用名（``modelName``），
    再看上游真名（``upstreamModel``）——网关别名没被单独测过，但它指向的上游名字
    如果已经实测判了 not-granted，那结论对这个条目同样成立。
    """

    caches = _probe_caches()
    for entry in entries:
        state = PROBE_STATE_UNPROBED
        if entry.get("invokeKind") == INVOKE_OPENAI_CHAT:
            kind = _entry_probe_kind(entry)
            provider = str(entry.get("provider") or "").strip().lower()
            if kind and provider:
                states = ((caches.get(kind) or {}).get(provider) or {}).get("models") or {}
                state = "unknown"
                for name in (entry.get("modelName"), entry.get("upstreamModel")):
                    candidate = str(states.get(str(name or "").strip()) or "")
                    if candidate and candidate != "unknown":
                        state = candidate
                        break
        entry["probeState"] = state
        entry["probeUnusable"] = state in PROBE_STATE_UNUSABLE


def build_model_plaza(*, connector: str = "", health: dict[str, Any] | None = None) -> dict[str, Any]:
    """四个 connector 聚合为模型清单。永不远程调用、永不抛上游错误。"""
    wanted = _clean(connector).lower()
    if wanted and wanted not in PLAZA_CONNECTORS:
        raise ValueError(f"unknown connector: {wanted}")

    if health is None:
        from novelvideo.newapi_provisioner import peek_alias_mapping_health

        health = peek_alias_mapping_health()
    routes = _effective_feature_routes()

    builders = {
        CONNECTOR_GATEWAY: lambda: [
            *_gateway_passthrough_entries(routes, health),
            *_gateway_media_entries(),
            *_embedding_entry(),
        ],
        CONNECTOR_COMFYUI: _comfyui_entries,
        CONNECTOR_MEDIAKIT: _mediakit_entries,
        CONNECTOR_ENV_OVERRIDE: lambda: _env_override_entries(routes),
    }
    models: list[dict[str, Any]] = []
    for connector_id in PLAZA_CONNECTORS:
        if wanted and wanted != connector_id:
            continue
        try:
            models.extend(builders[connector_id]())
        except Exception as exc:  # noqa: BLE001 - 一个分区坏不能拖垮整个目录
            logger.warning("[aipdd fork delta] WP-E: plaza connector %s failed: %s", connector_id, exc)

    seen: set[str] = set()
    ordered: list[dict[str, Any]] = []
    for entry in sorted(
        models,
        key=lambda item: (PLAZA_CONNECTORS.index(item["connector"]), item["sortOrder"], item["id"]),
    ):
        if entry["id"] in seen:
            continue
        seen.add(entry["id"])
        ordered.append(entry)

    _annotate_probe_states(ordered)

    gateway = get_effective_newapi_config()
    connectors = [
        {
            "id": connector_id,
            "modelCount": sum(1 for item in ordered if item["connector"] == connector_id),
            "availableCount": sum(
                1
                for item in ordered
                if item["connector"] == connector_id and item["status"] == STATUS_MAPPED
            ),
        }
        for connector_id in PLAZA_CONNECTORS
        if not wanted or wanted == connector_id
    ]
    return {
        "connectors": connectors,
        "capabilities": list(PLAZA_CAPABILITIES),
        "models": ordered,
        "gateway": {
            "mode": gateway.mode,
            "publicBaseUrl": newapi_public_base_url(base_url=gateway.base_url),
            "apiKeyPreview": mask_secret(_clean(gateway.api_key)),
            "configured": bool(_clean(gateway.api_key)),
        },
        "aliasHealth": {
            key: value
            for key, value in (health or {}).items()
            if key in {"status", "checked", "unmapped", "staleTargets", "capabilityMismatch", "noChannel", "degraded", "generatedAt"}
        },
    }


def find_plaza_model(model_id: str) -> dict[str, Any] | None:
    for entry in build_model_plaza()["models"]:
        if entry["id"] == _clean(model_id):
            return entry
    return None


def _probe_endpoint(entry: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """(url, api_key, payload)。只有 OpenAI 兼容同步端点会走到这里。"""
    from novelvideo.config import get_newapi_runtime_credentials

    api_key, resolved_base = get_newapi_runtime_credentials()
    # [aipdd fork delta] WP-E: 卡片上的 baseUrl 是给人复制的对外地址（[req41] 由请
    # 求 origin 推导，不再改写容器内主机名），服务端探活必须走自己可达的生效网关
    # 地址，否则在容器里探活永远连不上（实测全部 All connection attempts failed）。
    base_url = normalize_relay_base_url(
        _clean(resolved_base) or _clean(entry.get("baseUrl"))
    )
    model_name = _clean(entry.get("modelName"))
    if _clean(entry.get("invokeKind")) == INVOKE_OPENAI_EMBEDDINGS:
        endpoint = _clean(entry.get("endpoint")) or "embeddings"
        return f"{base_url}/{endpoint}", api_key, {"model": model_name, "input": "hi"}
    return f"{base_url}/chat/completions", api_key, {
        "model": model_name,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }


async def probe_plaza_model(entry: dict[str, Any]) -> dict[str, Any]:
    """单次最小探活。非 OpenAI 兼容类一律 ``skipped``，不发任务、不烧上游额度。"""
    invoke_kind = _clean(entry.get("invokeKind"))
    if invoke_kind not in PROBEABLE_INVOKE_KINDS:
        return {
            "ok": False,
            "skipped": True,
            "latencyMs": 0,
            "httpStatus": 0,
            "error": "",
            "reason": "notProbeableInvokeKind",
        }
    url, api_key, payload = _probe_endpoint(entry)
    if not api_key:
        return {
            "ok": False,
            "skipped": True,
            "latencyMs": 0,
            "httpStatus": 0,
            "error": "",
            "reason": "noGatewayKey",
        }
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=PLAZA_PROBE_TIMEOUT_SECONDS) as client:
            response = await client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        latency = int((time.monotonic() - started) * 1000)
        return {
            "ok": response.status_code < 400,
            "skipped": False,
            "latencyMs": latency,
            "httpStatus": response.status_code,
            "error": "" if response.status_code < 400 else _truncate_error(response.text),
        }
    except Exception as exc:  # noqa: BLE001 - 探活失败是结果，不是接口错误
        return {
            "ok": False,
            "skipped": False,
            "latencyMs": int((time.monotonic() - started) * 1000),
            "httpStatus": 0,
            "error": _truncate_error(str(exc)),
        }


def _truncate_error(text: str, limit: int = 400) -> str:
    clean = " ".join(str(text or "").split())
    return clean if len(clean) <= limit else f"{clean[:limit]}..."
