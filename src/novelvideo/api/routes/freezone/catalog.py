"""freezone.catalog 子模块（自 api/routes/freezone.py 拆分）。"""
from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone as _freezone
from novelvideo.api.schemas import FreezoneImageReversePromptRequest
from novelvideo.api.schemas import FreezoneJobAcceptedResponse
from novelvideo.api.schemas import FreezoneMarkDetectRequest
from novelvideo.api.schemas import FreezoneMarkDetectResponse
from novelvideo.freezone.image_node import DEFAULT_IMAGE_REVERSE_PROMPT_INSTRUCTION
from novelvideo.freezone.route_helpers import accepted_job_response as _accepted_job_response
from novelvideo.freezone.video_node import MAX_OMNI_REFERENCE_AUDIO_SECONDS
from novelvideo.freezone.video_node import MAX_OMNI_REFERENCE_AUDIO_TOTAL_SECONDS
from novelvideo.freezone.video_node import MIN_OMNI_REFERENCE_AUDIO_SECONDS
from novelvideo.freezone.video_node import get_video_camera_templates
from novelvideo.freezone.video_node import is_freezone_seedance2_backend
from novelvideo.freezone.video_node import normalize_video_aspect_ratio
from novelvideo.freezone.video_node import resolve_freezone_video_backend
from novelvideo.freezone.video_node import validate_omni_reference_audio_durations
from novelvideo.usage.video_billing import probe_video_aspect_ratio
from novelvideo.catalog.media_model_request_schema import MediaModelSchemaError
from novelvideo.catalog.media_model_request_schema import media_request_schema_for_mode
from novelvideo.catalog.media_model_request_schema import normalize_media_model_mode
from novelvideo.catalog.media_model_request_schema import validate_media_model_params
from novelvideo.catalog.media_model_request_schema import validate_media_request_schema
from novelvideo.ports import get_usage_meter
from novelvideo.ports.authz import find_authz_error

from ._common import (
    TAG_FREEZONE_AUDIO,
    TAG_FREEZONE_IMAGE,
    TAG_FREEZONE_MUSIC,
    TAG_FREEZONE_VIDEO,
    logger,
)

router = APIRouter()


# ============================================================
# 视频处理：文生视频 / 运镜模板 / 角色库
# ============================================================


async def _ee_media_model_catalog(media_type: str) -> list[dict[str, Any]] | None:
    """Use EE catalog or the CE-local catalog when custom NewAPI is active."""
    from novelvideo.ports.registry import PortNotRegistered, get_port

    try:
        catalog = get_port("media_model_catalog")
    except PortNotRegistered:
        from novelvideo.model_gateway_settings import (
            MODE_CUSTOM,
            MODE_HYBRID,
            get_ce_media_model_catalog,
            get_effective_newapi_config,
            get_official_media_model_catalog,
        )
        from novelvideo.shared.runtime_env import is_ce_effective

        if is_ce_effective():
            mode = get_effective_newapi_config().mode
            if mode == MODE_CUSTOM:
                return await _custom_gateway_media_catalog(
                    media_type,
                    get_ce_media_model_catalog(media_type),
                )
            if mode == MODE_HYBRID:
                local = get_ce_media_model_catalog(
                    media_type,
                    provider="comfyui",
                    include_disabled=True,
                )
                return _merge_media_model_catalog_defaults(
                    _static_media_model_catalog(media_type),
                    local,
                )
            return get_official_media_model_catalog(media_type)
        return None
    return await catalog.list_models(media_type)


async def _scoped_media_model_catalog(
    media_type: str,
    *,
    requester_user_id: str,
) -> list[dict[str, Any]] | None:
    """Read the catalog for the authenticated user without accepting an org id.

    CE supplies only the trusted user id resolved by ``ProjectContext``. EE owns
    the user-to-organization decision and the effective visibility expression.
    A registered control-plane catalog must support the scoped method; silently
    falling back to the platform-wide list would turn an integration mismatch
    into a tenant-isolation bypass.
    """
    from typing import cast

    from novelvideo.ports.media_model_catalog import MediaModelCatalogPort
    from novelvideo.ports.registry import PortNotRegistered, get_port

    try:
        catalog = cast(MediaModelCatalogPort, get_port("media_model_catalog"))
    except PortNotRegistered:
        return await _freezone._ee_media_model_catalog(media_type)

    clean_user_id = str(requester_user_id or "").strip()
    if not clean_user_id:
        raise HTTPException(401, "无法确认当前用户身份")
    try:
        return await catalog.list_models_for_user(
            media_type,
            user_id=clean_user_id,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - cross-edition port boundary
        code = str(getattr(exc, "code", "") or "")
        if code == "MEDIA_MODEL_SCOPE_DENIED":
            raise HTTPException(403, "当前账号无权使用媒体模型") from None
        logger.exception("failed to resolve scoped media model catalog")
        raise HTTPException(503, "媒体模型目录暂不可用，请稍后重试") from None


# [aipdd fork delta wave2] 请求体里「默认图」那个位装的是**选择键**（``newapi_gpt_image2``）
# 而不是模型名：它是多个 Pydantic 请求体的 default 值，一路传到入队前的目录门禁。以前官方
# 目录靠一条带 aliases 的条目兜住这个键；条目下线后键就悬空了 —— 用户什么都没改，门禁却回
# 409「该媒体模型未对当前组织开放、已停用或不存在」。现在按面板的默认图位把它换成真名：
# 先问面板（主位 / 副位），面板没配再退到目录自己的第一条；目录也为空才报「当前没有可用的
# 图片模型」。旧项目/旧画布节点里存的 huimeng_gpt_image2 这类别名键一并走同一个口径。
_DEFAULT_IMAGE_SELECTION_SLOTS = {
    "newapi_gpt_image2": "primary",
    "newapi_nanobanana2": "secondary",
}


def _default_image_selection_slot(value: str | None) -> str:
    """认出请求里的「默认图」选择键：返回它对应的面板位（primary/secondary），否则空串。"""
    clean = str(value or "").strip()
    if not clean:
        return ""
    slot = _DEFAULT_IMAGE_SELECTION_SLOTS.get(clean)
    if slot:
        return slot
    try:
        from novelvideo.config import LEGACY_IMAGE_GENERATION_SELECTION_ALIASES
    except Exception:  # noqa: BLE001 - 认不出就当普通模型名
        return ""
    canonical = str(LEGACY_IMAGE_GENERATION_SELECTION_ALIASES.get(clean) or "").strip()
    return _DEFAULT_IMAGE_SELECTION_SLOTS.get(canonical, "")


def _default_image_catalog_identifier(
    slot: str,
    catalog: list[dict[str, Any]] | None,
) -> str:
    """把「默认图」选择键解成一个目录里真实可见的模型名：先面板，再目录默认。"""
    resolved = ""
    try:
        if slot == "secondary":
            from novelvideo.model_gateway_settings import (
                get_effective_default_nanobanana_model,
            )

            resolved = str(get_effective_default_nanobanana_model() or "").strip()
        else:
            from novelvideo.model_gateway_settings import (
                get_effective_default_image_model,
            )

            resolved = str(get_effective_default_image_model() or "").strip()
    except Exception:  # noqa: BLE001 - 解不到就回目录默认
        resolved = ""
    if resolved:
        return resolved
    for entry in catalog or []:
        identifiers = _catalog_entry_identifiers(entry)
        if identifiers:
            return str(
                entry.get("catalogId")
                or entry.get("catalog_id")
                or entry.get("id")
                or next(iter(identifiers))
            )
    return ""


def _normalize_requested_media_model(
    media_type: str,
    requested: str | None,
    catalog: list[dict[str, Any]] | None,
) -> str:
    """把请求里的媒体模型名归一：「默认图」选择键换成目录里真实可见的模型名。"""
    clean = str(requested or "").strip()
    if str(media_type or "").strip().lower() != "image":
        return clean
    slot = _default_image_selection_slot(clean)
    if not slot:
        return clean
    return _default_image_catalog_identifier(slot, catalog)


def _media_model_unavailable(media_type: str, catalog: list[dict[str, Any]]) -> HTTPException:
    media_label = {
        "image": "图片",
        "video": "视频",
        # [aipdd fork delta req46] 目录不可用的文案按真实类型说：旧写法只认
        # image / 其余一律归「视频」，音乐条目会报成「没有可用的视频模型」。
        "music": "音乐",
        "audio": "音频",
    }.get(str(media_type or "").strip().lower(), "视频")
    detail = (
        f"当前没有可用的{media_label}模型，请联系管理员或刷新后重试"
        if not catalog
        else "该媒体模型未对当前组织开放、已停用或不存在，请刷新后选择其他模型"
    )
    return HTTPException(409, detail)


async def _require_scoped_media_model(
    media_type: str,
    requested: str | None,
    *,
    requester_user_id: str,
) -> dict[str, Any] | None:
    """Recheck model visibility immediately before enqueueing."""
    from novelvideo.ports.registry import PortNotRegistered, get_port

    try:
        get_port("media_model_catalog")
    except PortNotRegistered:
        # Community Edition owns its local model map and has no organization
        # policy to enforce. Keep its existing submission behavior unchanged.
        return None
    catalog = await _freezone._scoped_media_model_catalog(
        media_type,
        requester_user_id=requester_user_id,
    )
    if catalog is None:
        return None
    clean_requested = _normalize_requested_media_model(media_type, requested, catalog)
    entry = next(
        (
            item
            for item in catalog
            if clean_requested in _catalog_entry_identifiers(item)
        ),
        None,
    )
    if entry is None:
        raise _media_model_unavailable(media_type, catalog)
    return entry


def _static_media_model_catalog(media_type: str) -> list[dict[str, Any]]:
    from novelvideo.model_gateway_settings import get_official_media_model_catalog

    return get_official_media_model_catalog(media_type)


async def _custom_gateway_media_catalog(
    media_type: str,
    configured: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """LocalAPI 下的媒体模型目录：只呈现用户在设置页配置的模型。

    [aipdd fork delta] 「工具 / 画布里的模型必须来自用户配置的渠道」：过去 custom
    模式会把发行方内置目录（``official_media_models.json``）按网关实拉名单收敛后
    合并、并把网关里有、目录里没有的模型自动补成条目——于是工具下拉里混进一堆
    用户从没配置过的上游原名模型（如 ``doubao-*`` / ``wan2-*`` / ``AP *``），
    用户根本不知道该走哪条渠道。现在 custom 模式的媒体目录与「设置→默认生成模型」
    面板严格同源：只保留用户显式配置且启用的条目（停用条目不进目录），并给图片 /
    视频条目补齐空缺的档位声明，让画布参数下拉照常可用。
    """

    from novelvideo.catalog import media_model_discovery
    # 停用条目（enabled=false）不进目录：候选里不得出现被用户停用的模型。
    entries = [
        dict(item)
        for item in configured
        if isinstance(item, dict) and item.get("enabled") is not False
    ]
    if media_type not in {"image", "video"}:
        return entries
    # [aipdd fork delta] vres「真·跟随网关声明」：视频档位优先采用网关 /v1/models 的
    # 逐模型能力声明（如 AIPDD ap-minimax-h3 只有 480p/768p/1080p，没有 720p），拉不
    # 到时 default_video_capabilities 自动退回通用默认。best-effort，不拖住画布开目录。
    gateway_capabilities: dict[str, dict[str, Any]] = {}
    if media_type == "video":
        gateway_capabilities = await media_model_discovery.gateway_video_capabilities_by_name()
    if media_type == "image":
        media_model_discovery.apply_default_image_capabilities(entries)
    else:
        media_model_discovery.apply_default_video_capabilities(
            entries, gateway_capabilities=gateway_capabilities
        )
        # [aipdd fork delta req44] 再给条目挂一份「渠道在线备了哪几种模式绑定」的**咨询
        # 证据**（bindingModes，只加不减）：画布首尾帧页签过去只按模型名猜（非 Seedance 2.0
        # 一律藏掉），而 aipdd 给 ap-minimax-h3 明明接了 first_last_frame_to_video 绑定。
        # 读不到绑定快照的条目不写字段，前端保持原有判定。
        media_model_discovery.apply_video_binding_mode_evidence(entries)
    return entries


def _merge_media_model_catalog_defaults(
    defaults: list[dict[str, Any]], configured: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Overlay CE mappings on the existing mainline capabilities."""
    merged: list[dict[str, Any]] = []
    consumed: set[int] = set()
    for base in defaults:
        base_id = _catalog_entry_id(base)
        match_index = next(
            (
                index
                for index, item in enumerate(configured)
                if index not in consumed
                and base_id
                and _catalog_entry_id(item) == base_id
            ),
            None,
        )
        if match_index is None:
            merged.append(base)
            continue
        consumed.add(match_index)
        override = configured[match_index]
        if override.get("enabled") is not False:
            merged.append({**base, **override})
    merged.extend(
        item
        for index, item in enumerate(configured)
        if index not in consumed and item.get("enabled") is not False
    )
    return merged


def _catalog_entry_identifiers(entry: dict[str, Any]) -> set[str]:
    """Return new and legacy identifiers accepted at the API boundary."""
    identifiers = {
        str(entry.get("catalogId") or ""),
        str(entry.get("catalog_id") or ""),
        str(entry.get("id") or ""),
        str(entry.get("apiModel") or ""),
        str(entry.get("api_model") or ""),
        str(entry.get("gatewayModel") or ""),
        str(entry.get("gateway_model") or ""),
    }
    aliases = entry.get("aliases")
    if isinstance(aliases, list):
        identifiers.update(str(alias) for alias in aliases)
    return {identifier for identifier in identifiers if identifier}


def _catalog_entry_id(entry: dict[str, Any] | None) -> str:
    if not entry:
        return ""
    return str(entry.get("catalogId") or entry.get("catalog_id") or "").strip()


def _catalog_image_execution_selection(
    entry: dict[str, Any] | None,
    *,
    requested_provider: str | None,
    requested_model: str | None,
) -> tuple[str | None, str | None]:
    """Derive the execution identity from one catalog row."""
    if entry is None:
        return requested_provider, requested_model

    provider = str(entry.get("providerId") or entry.get("provider") or "").strip()
    model = str(
        entry.get("apiModel")
        or entry.get("api_model")
        or entry.get("gatewayModel")
        or entry.get("gateway_model")
        or ""
    ).strip()
    if not provider or not model:
        raise HTTPException(400, "configured media model is missing provider or gateway model")

    clean_provider = str(requested_provider or "").strip()
    if clean_provider and clean_provider.casefold() != provider.casefold():
        raise HTTPException(400, "model provider does not match configured media model")

    clean_model = str(requested_model or "").strip()
    # [aipdd fork delta wave2] 「默认图」那个请求体默认值装的是选择键而不是模型名：
    # 上面已经按它解出了目录条目，这里再拿它去对目录身份就是自己跟自己 400。
    if _default_image_selection_slot(clean_model):
        clean_model = ""
    if clean_model and clean_model not in _catalog_entry_identifiers(entry):
        raise HTTPException(400, "model does not match configured media model")
    return provider, model


async def _resolve_catalog_request(
    media_type: str,
    model: str | None,
    model_params: dict[str, Any] | None,
    *,
    mode: str | None = None,
    requester_user_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    catalog = (
        await _freezone._scoped_media_model_catalog(
            media_type,
            requester_user_id=requester_user_id,
        )
        if requester_user_id is not None
        else await _freezone._ee_media_model_catalog(media_type)
    )
    requested = _normalize_requested_media_model(media_type, model, catalog)
    entry = next(
        (
            item
            for item in catalog or []
            if requested in _catalog_entry_identifiers(item)
        ),
        None,
    )
    if entry is None:
        # In EE the catalog is authoritative. Never fall back to CE's static
        # model map when no enabled model matches the submitted identifier.
        if catalog is not None and requested:
            raise _media_model_unavailable(media_type, catalog)
        if model_params:
            raise HTTPException(
                400, "model parameters require a configured media model"
            )
        return {}, {}, None
    try:
        full_schema = validate_media_request_schema(entry.get("request"))
        expected_endpoint = (
            "images/generations" if media_type == "image" else "video/generations"
        )
        if full_schema and full_schema.get("endpoint") != expected_endpoint:
            raise MediaModelSchemaError(f"endpoint must be {expected_endpoint}")
        schema = media_request_schema_for_mode(full_schema, mode)
        active_keys = {
            str(parameter["key"]) for parameter in schema.get("parameters") or []
        }
        defined_keys = {
            str(parameter["key"]) for parameter in full_schema.get("parameters") or []
        }
        filtered_params = {
            key: value
            for key, value in (model_params or {}).items()
            if key in active_keys or key not in defined_keys
        }
        if media_type == "image" and entry.get("qualityOptions"):
            schema = {**schema, "includeQuality": True}
        if media_type == "image" and entry.get("minPixels") is not None:
            schema = {**schema, "minPixels": entry["minPixels"]}
        values = validate_media_model_params(schema, filtered_params)
    except MediaModelSchemaError as exc:
        raise HTTPException(400, str(exc)) from exc
    return schema, values, entry


def _catalog_mode_enabled(
    capabilities: dict[str, Any] | None, mode: str
) -> bool | None:
    if capabilities is None:
        return None
    modes = capabilities.get("supportedModes") or []
    # [aipdd fork delta] 未声明 supportedModes（空/缺失，如 LocalAPI 下自动纳入或
    # 已配置但未填模式的 ap-minimax-h3）不能当作禁止，否则 mode in [] 会把连
    # 文生视频在内的全部模式拦下、模型直接全废。返回 None 交给网关裁决（前端
    # 对未声明条目也是走按名启发式放开，两端一致）。
    if not modes:
        return None
    return mode in modes


def _require_catalog_video_mode(
    capabilities: dict[str, Any] | None,
    mode: str,
) -> None:
    """声明了 supportedModes 的条目必须显式声明所请求的视频模式。

    [aipdd fork delta] 上游在 video_gen / i2v / keyframes 三处都调本函数校验
    声明与请求的一致性；迁移中调用与函数一起被删了，导致声明了模式集合的条目
    不再校验、跨模式请求静默落到别的执行模式（如 imageToVideo 落到
    image_reference），直到上游拒单才报错。判定沿用 _catalog_mode_enabled：
    未声明 supportedModes 仍是 None（交给网关裁决），只有明确声明且不含请求
    模式才拒绝。
    """

    normalized = normalize_media_model_mode(mode)
    if _catalog_mode_enabled(capabilities, normalized) is False:
        raise HTTPException(400, f"this model does not support {normalized} mode")


def _catalog_omni_reference_capable(capabilities: dict[str, Any] | None) -> bool:
    """目录条目是否按网关权威信号具备「全能参考」的引用输入容量。

    [aipdd fork delta] 发现层刻意不为视频条目猜 supportedModes（猜错会被上游拒单），
    所以 ap-minimax-h3 这类模型即使网关明确声明了 maxAudioInputs / maxImageInputs，
    supportedModes 仍是空 → `_catalog_mode_enabled` 返回 None → omni 端点会退回
    「只 seedance2」的历史硬闸把它拦死。引用上限是网关的权威声明、不是模式猜测：
    声明了参考音频 / 参考视频容量（>0）即具备全能参考消费能力，与前端按同一信号
    放开全能参考，两端口径一致。文生 / 图生视频不读这个，只影响 omni 一条闸。

    [aipdd fork delta req44] 同样的「只加不减」也适用于渠道绑定事实：条目带上
    ``bindingModes`` 且其中确有 ``all_reference``（渠道真挂了全能参考的在线绑定）时也
    算具备容量 —— 前端 `isVideoModeSupportedByModel` 读的是同一份证据，两端保持一致，
    不会出现「页签放开了、提交被历史硬闸拦下」。没有任何声明时仍返回 False。
    """

    if not capabilities:
        return False
    for key in ("referenceAudioMax", "referenceVideoMax"):
        value = capabilities.get(key)
        if type(value) is int and value > 0:
            return True
    binding_modes = capabilities.get("bindingModes")
    if isinstance(binding_modes, (list, tuple, set, frozenset)):
        if "all_reference" in {str(item).strip() for item in binding_modes}:
            return True
    return False


def _omni_reference_binding_facts(
    model: str,
    *,
    mode: str | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """取该模型在各渠道里**关联得上**的能力绑定事实（``[(provider, facts), ...]``）。

    关联不上就不进表 —— 渠道把视频模型代理给第三方时，那份自托管绑定清单里根本
    没有这个名字，拿局部事实去否全局清单是误伤。

    给 ``mode`` 时只问「这条提交模式要走的哪条绑定吃什么」：同一个模型名下可能挂着
    文生 / 首尾帧 / 全能参考几条入参集完全不同的绑定（H3 就是这种），按模型名取并集
    无论哪个方向都会给出错答案。
    """

    from novelvideo.model_gateway_settings import (
        get_provider_capability_bindings,
        model_owner_providers,
        split_provider_spot_model,
    )
    from novelvideo.catalog.upstream_capabilities import video_input_facts_for_model

    provider, bare_model = split_provider_spot_model(str(model or ""))
    bare_model = str(bare_model or "").strip()
    if not bare_model:
        return []
    providers = [provider] if provider else list(model_owner_providers(bare_model))
    out: list[tuple[str, dict[str, Any]]] = []
    for item in providers:
        provider_key = str(item or "").strip().lower()
        if not provider_key:
            continue
        facts = video_input_facts_for_model(
            get_provider_capability_bindings(provider_key), bare_model, mode=mode
        )
        if facts:
            out.append((provider_key, facts))
    return out

def _catalog_entry_omni_gap_evidence(capabilities: dict[str, Any] | None) -> str:
    """把渠道自己在 ``/v1/models`` 里给该模型打的参考声明拼成一句对照证据。

    全部读目录条目上已有的字段，不臆断也不抄官方参数表：渠道给什么就说什么，没给的
    项直接说「未申报」，给了 0 就说 0（2026-10-08 实拉：H3 的参考视频是**明说 0** 配
    ``supportsReferenceVideo: false``，不是没写），这样两边矛盾时用户和运维能自己看出来
    缺的是哪一个。没有任何声明时返回空串。
    """

    if not capabilities:
        return ""
    declared: list[str] = []
    for label, key in (
        ("参考图上限", "referenceImageMax"),
        ("参考视频上限", "referenceVideoMax"),
        ("参考音频上限", "referenceAudioMax"),
    ):
        value = capabilities.get(key)
        declared.append(f"{label} {value}" if type(value) is int else f"{label}未申报")
    return "渠道 /v1/models 对它的申报是：" + "、".join(declared) + "。"


def _omni_reference_alternative_hint(
    facts_list: list[tuple[str, dict[str, Any]]],
    kind: str,
) -> str:
    """报错里的「出路」一句：同渠道哪些在线绑定确实吃这类素材、能不能按模型名调到。

    全部从事实表现算，不把任何绑定名 / 模型名抄进代码：渠道今天只有那条自托管
    对口型绑定（它没出现在模型清单里、按名字提交不到），就照实说「需要渠道把它
    发布成可调用模型」；哪天发布了或上架了新的吃参考视频的绑定，这句话自动变。
    """

    from novelvideo.model_gateway_settings import get_provider_capability_bindings
    from novelvideo.catalog.upstream_capabilities import bindings_accepting_input

    matched_codes = {
        str(code)
        for _provider, facts in facts_list
        for code in (facts.get("bindings") or [])
        if code
    }
    listed: list[str] = []
    reachable: list[str] = []
    for provider_key in sorted({item for item, _facts in facts_list}):
        alternatives = bindings_accepting_input(
            get_provider_capability_bindings(provider_key),
            kind,
            exclude_codes=matched_codes,
        )
        if not alternatives:
            continue
        try:
            from novelvideo.newapi_provisioner import list_provider_upstream_models

            models, models_error = list_provider_upstream_models(provider_key)
        except Exception:  # noqa: BLE001 - 模型清单读不到只影响措辞，不影响拒单
            models, models_error = [], "lookup failed"
        model_names = {str(name).strip().casefold() for name in models if str(name).strip()}
        for binding in alternatives:
            code = str(binding.get("code") or "")
            task_kind = str(binding.get("taskKind") or "").strip() or "未声明"
            if models_error:
                # 清单没读到就不断言它调不调得到，避免把远程故障说成渠道缺陷。
                listed.append(f"{code}（渠道 {provider_key}，任务类型：{task_kind}）")
                continue
            if code.casefold() in model_names:
                listed.append(
                    f"{code}（渠道 {provider_key}，任务类型：{task_kind}，已发布为可调用模型）"
                )
                reachable.append(code)
            else:
                listed.append(
                    f"{code}（渠道 {provider_key}，任务类型：{task_kind}，"
                    f"但没出现在渠道的模型清单里、按模型名提交不到）"
                )
    if not listed:
        return ""
    lead = (
        "可直接改用的在线绑定："
        if reachable
        else "同渠道里确实吃这类素材的在线绑定只有以下几条，但它们当前都调不到："
    )
    return f"{lead}{'、'.join(listed)}。"


# 提交模式的用户叫法（进报错文案）。只翻我们自己路由的模式名，不翻任何模型/渠道词。
_OMNI_MODE_LABELS = {
    "all_reference": "全能参考",
    "first_last_frame": "首尾帧",
    "first_frame": "首帧图生",
    "image_to_video": "图生视频",
    "text_to_video": "文生视频",
    "video_edit": "视频编辑",
    "video_extend": "视频续写",
}
# 这类参考素材在渠道绑定里应当出现的控件类型（渠道自己的词表，不是我们造的概念）。
_OMNI_REFERENCE_UI_HINTS = {"video": "video_url", "audio": "audio_url"}


def _video_frame_slot_shortfall(
    *,
    model: str,
    capabilities: dict[str, Any] | None,
    limit: int,
    needed: int,
    mode: str = "first_last_frame",
) -> str:
    """关键帧张数超过图槽上限时的拒单文案：把两份事实 + 可执行出路一次说清。

    [aipdd fork delta req44] 这道闸是为「渠道备了帧路绑定、图槽却只申报了一个」这类模型
    准备的（2026-10-08 实测：LTX 2.5 的 ``image_to_video`` 绑定只有 ``image``「首帧图片」
    一个图槽，``/v1/models`` 也写着 ``maxInputImages: 1``）。页签按产品口径放开（不拿
    渠道接线不足当能力边界），但真递两张时尾帧根本没槽接住 —— 旧文案只有一句
    ``this model supports at most N image references``，用户看不出是**申报的数量**不够、
    也不知道该动哪里。所有数字都从目录条目现算，不写死任何模型名 / 上限。
    """

    mode_label = _OMNI_MODE_LABELS.get(mode) or "首尾帧"
    # 只有真两张时才说「首帧 + 尾帧」：渠道申报 0 个图槽时单递一张首帧也会走到这里，
    # 括号里多出一个不存在的尾帧会把人带偏。
    frame_note = "（首帧 + 尾帧）" if needed >= 2 else ""
    sentences = [
        f"{model} 的「{mode_label}」这次要递 {needed} 张关键帧{frame_note}，"
        f"但目录给它记的参考图上限只有 {limit} 张 —— 多发的那张帧图没有图槽可放。",
    ]
    gap = _catalog_entry_omni_gap_evidence(capabilities)
    if gap:
        sentences.append(gap)
    sentences.append(
        "拦的依据是这份申报，不是模型能力结论：图槽不够时递过去只会被上游默默截成"
        "一张、出一条与多递那张帧无关的片子（还照常计费），所以先在提交前拦下。"
    )
    sentences.append(
        "出路：① 只留首帧、把模式改成「首帧生成视频」；② 改成参考图上限 ≥"
        f"{needed} 的视频模型；③ 渠道其实收得下 {needed} 张时，在模型设置里把该模型的"
        f"「参考图上限」配到 ≥{needed}（或让渠道给这条工作流补上尾帧节点），"
        "配好这道闸自动放行，不用改代码。"
    )
    return "".join(sentences)


def _require_omni_reference_inputs_supported(
    model: str,
    reference_items: list[dict[str, Any]] | None,
    *,
    mode: str | None = None,
    declared: dict[str, Any] | None = None,
) -> None:
    """[aipdd fork delta req43] 参考素材递不给上游时，提交前当场拦下。

    拦的依据是「**这条提交模式要走的哪一条绑定**不吃这类素材」，不是「这个模型没这
    个能力」。实测取证（H3，req43 当时）：渠道 ``/v1/models`` 申报了 ``supportsReferenceVideo:
    true`` / ``maxImageInputs: 9`` / ``maxAudioInputs: 3``，而它 ``/v1/capabilities`` 里名字写着
    ``ref2va`` 的那条绑定实际只接到「首帧图片 + 尾帧图片」（连 taskKind 都写成
    ``first_last_frame_to_video``）。两边矛盾时我们替渠道判不了哪一侧写错，但提交只会
    走绑定那条路，所以按绑定拦 —— 不然就是“静默出一条不相干的片”（req42 那次）。

    【同一份声明里的布尔会漂】2026-10-08 复拉：同一个 ``supportsReferenceVideo`` 已翻成
    ``false`` 并配 ``maxVideoInputs: 0``（带外探针递 ``video_urls`` 拿到的就是「当前 MiniMax H3
    渠道不支持视频参考；本次请求未扣费」）。两个时点都是渠道写的，因此布尔既不能当放行
    依据、也不能当归零依据 —— 只照**数量**申报取（见 ``newapi._aipdd_video_input_caps``）。

    【已更正的旧口径】不能反过来把绑定当作模型能力的结论：渠道接线不足不等于模型不支持，
    因此也不再拿这个事实去把目录里的参考上限归零（那会把用户本可以用的入口直接藏掉，
    渠道补好绑定后也不会自动回来）。能力面按渠道申报展示，递不递得进去到提交这一步再说。

    口径（宁可少拦，不可静默出废片）：

    - 只读渠道自己声明的**在线绑定入参**，不按模型名猜能力；
    - 按 ``mode`` 筛不出属于该模式的绑定时不表态（词表没对上读不出“不支持”）；
    - 关联得上的渠道里任一声明吃这类素材就放行（同名模型在不同渠道可能是不同形态）。
    """

    kinds = {
        str(item.get("type") or "").strip().lower()
        for item in (reference_items or [])
        if isinstance(item, dict)
    }
    facts_list = _omni_reference_binding_facts(model, mode=mode)
    if not facts_list:
        return
    checks = {
        "video": (
            "参考视频",
            "acceptsReferenceVideo",
            "替换视频产品 / 替换视频人物",
            "产物将是一条与原视频毫不相干的片子",
        ),
        "audio": (
            "参考音频",
            "acceptsReferenceAudio",
            "对口型",
            "口型不会按这条音频走，产物与原音频无关",
        ),
    }
    for kind in ("video", "audio"):
        if kind not in kinds:
            continue
        label, flag, feature, consequence = checks[kind]
        if any(facts.get(flag) for _provider, facts in facts_list):
            continue
        raise ValueError(
            _omni_reference_input_block_text(
                model=model,
                kind=kind,
                label=label,
                feature=feature,
                consequence=consequence,
                facts_list=facts_list,
                mode=str(facts_list[0][1].get("mode") or ""),
                declared=declared,
            )
        )


def _omni_reference_input_block_text(
    *,
    model: str,
    kind: str,
    label: str,
    feature: str,
    consequence: str,
    facts_list: list[tuple[str, dict[str, Any]]],
    mode: str = "",
    declared: dict[str, Any] | None = None,
) -> str:
    """把「这条参考素材为什么递不进去 + 怎么办」拼成一句能照着做的诊断。

    [aipdd fork delta req43] 报错不能只复读结论：先把该模式关联到的绑定和它们**实际收到
    的入参名**摊出来（这才是证据），再把渠道自己在 ``/v1/models`` 里的申报拿出来对照，
    明确说「这是两边冲突、我们只按提交会走的绑定拦」，最后给可执行的出路。不写死
    任何模型名 / 官方参数表 / 备选模型名，全部从事实表现算。
    """

    def _joined(key: str, separator: str = "、") -> str:
        return separator.join(
            sorted(
                {
                    str(value)
                    for _provider, facts in facts_list
                    for value in (facts.get(key) or [])
                    if str(value or "").strip()
                }
            )
        )

    providers = "、".join(sorted({item for item, _facts in facts_list}))
    mode_label = _OMNI_MODE_LABELS.get(mode, "") or "当前提交模式"
    bindings = _joined("bindings") or "未声明"
    # 多条绑定时逐条前缀 code（运维要对着渠道配置逐条排查），单条时不必重复报一遍名字。
    per_binding = [
        str(item)
        for _provider, facts in facts_list
        for item in (facts.get("inputParams") or [])
        if str(item or "").strip()
    ]
    multi = len(
        {
            str(code)
            for _provider, facts in facts_list
            for code in (facts.get("bindings") or [])
            if str(code or "").strip()
        }
    ) > 1
    if multi:
        input_params = "；".join(sorted(set(per_binding)))
    else:
        input_params = " / ".join(item.split(": ", 1)[-1] for item in per_binding) or _joined(
            "inputUiTypes", " / "
        )
    ui_hint = _OMNI_REFERENCE_UI_HINTS.get(kind, "")
    sentences = [
        (
            f"渠道 {providers} 的 {model} 在「{mode_label}」下关联到的在线绑定是 {bindings}"
            f"（任务类型：{_joined('taskKinds') or '未声明'}），"
            f"{'这些绑定' if multi else '该绑定'}实际收到的入参只有："
            f"{input_params or '提示词等文本类控件'}"
            f"——没有任何{label}输入节点（控件类型 {ui_hint or label}）。"
        ),
        (
            f"现在提交的话{label}递不进上游，只会按现有任务类型照常出片：{consequence}。"
        ),
    ]
    gap = _catalog_entry_omni_gap_evidence(declared)
    sentences.append(
        (
            f"{gap}申报与绑定入参冲突时我们无法替渠道断定哪一侧写错，"
            f"但提交只会走绑定这条路，所以先按绑定拦下，不出不相干的片子。"
            if gap
            else "拦的依据是这条绑定自己的入参声明，不是模型能力结论："
            "不能据此判定模型不支持参考输入，只是这个渠道今天递不进去。"
        )
    )
    alternative = _omni_reference_alternative_hint(facts_list, kind)
    if alternative:
        sentences.append("出路：" + alternative)
    fix = (
        f"要在这条模型上做「{feature}」：让渠道把上述绑定按该模式的真实入参接上"
        f"{label}输入节点（控件类型 {ui_hint or label}、taskKind 带 reference），"
        f"并把 /v1/models 的申报补齐；渠道补好后这道闸自动放行、界面也自动放开，"
        f"不用再改代码。"
    )
    # 没有可用备选时不指向一个不存在的「上面那份出路」。
    sentences.append(
        fix + (f"或直接改用上面出路里确实接了{label}的绑定/模型。" if alternative else "")
    )
    return "".join(sentences)


def _catalog_resolution_options(
    capabilities: dict[str, Any] | None,
) -> list[str] | None:
    if not capabilities:
        return None
    options = capabilities.get("resolutionOptions")
    if not isinstance(options, list):
        return None
    normalized = [str(option).strip() for option in options if str(option).strip()]
    return normalized or None


def _catalog_ratio_options(
    capabilities: dict[str, Any] | None,
) -> list[str] | None:
    """该视频模型在渠道目录里声明的可用画幅档位（没声明返回 None）。"""

    if not capabilities:
        return None
    options = capabilities.get("ratioOptions")
    if not isinstance(options, list):
        return None
    normalized = [str(option).strip() for option in options if str(option).strip()]
    return normalized or None


async def _resolve_omni_aspect_ratio(
    explicit: str | None,
    reference_items: list[dict[str, Any]],
    capabilities: dict[str, Any] | None = None,
) -> str:
    """全能参考的输出画幅：显式值照用，``auto`` 按**源视频画面**落到目录允许的档位。

    [aipdd fork delta req42] 「替换视频人物 / 产品」「对口型」的输入就是一条竖屏短视频，
    旧口径把 ``auto`` 一路传到建单体（AIPDD 收不到 ``ratio`` 时固定出 864x480 横屏），
    产物与原视频「看着完全无关」的另一半原因就在这里：画幅是素材里已经写着的答案，
    不该由请求默认值拍脑袋定。

    只对本地素材文件探测；素材是直链 / 探测失败时退回 16:9 并记警告，不拦提交。
    """

    normalized = normalize_video_aspect_ratio(explicit)
    if normalized != "auto":
        return normalized
    video_ref = next(
        (
            item
            for item in reference_items
            if str(item.get("type") or "") == "video"
            and str(item.get("path") or "").strip()
        ),
        None,
    )
    if video_ref is None:
        return "16:9"
    probe_target = str(video_ref["path"])
    if probe_target.startswith(("http://", "https://", "data:")) or not Path(
        probe_target
    ).is_file():
        return "16:9"
    try:
        return await probe_video_aspect_ratio(
            probe_target, _catalog_ratio_options(capabilities)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "omni reference video aspect probe failed, falling back to 16:9: %s", exc
        )
        return "16:9"


def _catalog_reference_limits(
    capabilities: dict[str, Any] | None,
    *,
    image_default: int,
    video_default: int,
    audio_default: int,
    file_default: int = 0,
    link_default: int = 0,
) -> dict[str, int]:
    def _limit(key: str, default: int) -> int:
        value = capabilities.get(key) if capabilities else None
        return value if type(value) is int and value >= 0 else default

    return {
        "image": _limit("referenceImageMax", image_default),
        "video": _limit("referenceVideoMax", video_default),
        "audio": _limit("referenceAudioMax", audio_default),
        "file": _limit("referenceFileMax", file_default),
        "link": _limit("referenceLinkMax", link_default),
    }


def _catalog_reference_duration_bounds(
    capabilities: dict[str, Any] | None,
    media: str,
) -> tuple[float | None, float | None, float | None, float | None]:
    prefix = "referenceAudio" if media == "audio" else "referenceVideo"

    def _duration(suffix: str) -> float | None:
        value = capabilities.get(f"{prefix}{suffix}") if capabilities else None
        if type(value) in (int, float) and math.isfinite(value) and value > 0:
            return float(value)
        return None

    return (
        _duration("MinSeconds"),
        _duration("MaxSeconds"),
        _duration("TotalMinSeconds"),
        _duration("TotalMaxSeconds"),
    )


def _catalog_audio_total_duration_max(capabilities: dict[str, Any] | None) -> float | None:
    """目录里配的全能参考音频**总时长**上限（秒），没配返回 None。

    与 `_catalog_reference_limits` 同一套「目录优先」的口径，只是这里允许小数
    （15.2 本身就不是整数）。非正数 / 非数字 / inf / nan 一律当没配——写入侧
    `media_model_request_schema` 已经挡了这些，但目录条目也可能是那道校验存在之前落的库，
    读出来再挡一次才不会让 inf 静默关掉整个上限。

    刻意**不在这里兜底**成 15.2：调用方要靠「有没有配」来决定这个模型该不该受管，
    在这里默默返回 15.2 就分不清「管理员配了 15.2」和「压根没配」。
    """
    return _catalog_reference_duration_bounds(capabilities, "audio")[3]


async def _probe_reference_audio_seconds(path: str) -> float | None:
    """ffprobe 读音频时长，读不出返回 None（校验器会跳过这条）。

    不能改用 `utils.media_io.get_audio_duration`：它探测失败时返回 **5.0**，一个正好
    合法的值，会把兜底变成静默放行。这里要的就是「不知道」和「知道」分得清。
    """
    from novelvideo.seedance2_i2v.character_voice_storage import (
        probe_voice_sample_duration_seconds,
    )
    from novelvideo.utils.async_ops import call_blocking

    try:
        return float(await call_blocking(probe_voice_sample_duration_seconds, path))
    except Exception as exc:  # ffprobe 缺失 / 文件损坏 / 权限，都只是「测不出」
        logger.warning("[freezone] reference audio duration probe failed: %s (%s)", path, exc)
        return None


async def _validate_catalog_reference_audio_items(
    reference_items: list[dict[str, str]],
    *,
    capabilities: dict[str, Any] | None,
    backend: str,
) -> None:
    """按媒体目录校验参考音频时长；旧 Seedance 2.0 目录保留历史兜底。"""
    audio_items = [item for item in reference_items if item["type"] == "audio"]
    if not audio_items:
        return

    audio_bounds = list(_catalog_reference_duration_bounds(capabilities, "audio"))
    if is_freezone_seedance2_backend(backend):
        if audio_bounds[0] is None:
            audio_bounds[0] = MIN_OMNI_REFERENCE_AUDIO_SECONDS
        if audio_bounds[1] is None:
            audio_bounds[1] = MAX_OMNI_REFERENCE_AUDIO_SECONDS
        if audio_bounds[3] is None:
            audio_bounds[3] = MAX_OMNI_REFERENCE_AUDIO_TOTAL_SECONDS
    if not any(value is not None for value in audio_bounds):
        return

    probed_seconds = await asyncio.gather(
        *(_freezone._probe_reference_audio_seconds(item["path"]) for item in audio_items)
    )
    probed = [
        (Path(item["path"]).name or f"audio{index}", seconds)
        for index, (item, seconds) in enumerate(
            zip(audio_items, probed_seconds), start=1
        )
    ]
    validate_omni_reference_audio_durations(
        probed,
        min_seconds=audio_bounds[0],
        max_seconds=audio_bounds[1],
        total_min_seconds=audio_bounds[2],
        total_max_seconds=audio_bounds[3],
    )


def _catalog_duration_bounds(
    capabilities: dict[str, Any] | None,
) -> tuple[int | None, int | None]:
    def _bound(key: str) -> int | None:
        value = capabilities.get(key) if capabilities else None
        return value if type(value) is int and value > 0 else None

    return _bound("minDuration"), _bound("maxDuration")


async def _resolve_catalog_video_backend(
    model: str | None,
    *,
    requester_user_id: str | None = None,
) -> str:
    requested = str(model or "").strip()
    if requested:
        catalog = (
            await _freezone._scoped_media_model_catalog(
                "video",
                requester_user_id=requester_user_id,
            )
            if requester_user_id is not None
            else await _freezone._ee_media_model_catalog("video")
        )
        for entry in catalog or []:
            if requested in _catalog_entry_identifiers(entry):
                return str(entry.get("apiModel") or requested)
    return resolve_freezone_video_backend(model)


@router.get("/projects/{project}/freezone/video/camera-templates", tags=[TAG_FREEZONE_VIDEO])
async def freezone_video_camera_templates(
    project: str,
    user: dict = Depends(get_api_user),
):
    """视频处理：返回文生视频运镜模板库。"""
    await _freezone._resolve_freezone_project(project, user, required_role="viewer")
    return {"ok": True, "data": get_video_camera_templates()}


@router.get("/projects/{project}/freezone/video/models", tags=[TAG_FREEZONE_VIDEO])
async def freezone_video_models(
    project: str,
    user: dict = Depends(get_api_user),
):
    """视频处理：返回设置页「默认生成模型」配置的视频模型目录；空列表即无可用模型。"""
    ctx, _username, _project_name, _project_dir, _output_dir = (
        await _freezone._resolve_freezone_project(project, user, required_role="viewer")
    )
    catalog = await _freezone._scoped_media_model_catalog(
        "video",
        requester_user_id=ctx.requester_user_id,
    )
    return {"ok": True, "data": catalog or []}


@router.get("/projects/{project}/freezone/image/models", tags=[TAG_FREEZONE_IMAGE])
async def freezone_image_models(
    project: str,
    user: dict = Depends(get_api_user),
):
    """图片处理：返回设置页「默认生成模型」配置的图片模型目录；空列表即无可用模型。"""
    ctx, _username, _project_name, _project_dir, _output_dir = (
        await _freezone._resolve_freezone_project(project, user, required_role="viewer")
    )
    catalog = await _freezone._scoped_media_model_catalog(
        "image",
        requester_user_id=ctx.requester_user_id,
    )
    return {"ok": True, "data": catalog or []}


# [aipdd fork delta] 画布音频节点的模型候选：后台「默认生成模型」面板里
# mediaType=audio 的条目。没有旧配置兜底可退（语音默认引擎不依赖目录），
# 空列表是合法状态：面板展示「默认引擎」选项即可。
@router.get("/projects/{project}/freezone/audio/models", tags=[TAG_FREEZONE_AUDIO])
async def freezone_audio_models(
    project: str,
    user: dict = Depends(get_api_user),
):
    """音频处理：返回后台配置的音频模型目录。"""
    ctx, _username, _project_name, _project_dir, _output_dir = (
        await _freezone._resolve_freezone_project(project, user, required_role="viewer")
    )
    catalog = await _freezone._scoped_media_model_catalog(
        "audio",
        requester_user_id=ctx.requester_user_id,
    )
    return {"ok": True, "data": catalog or []}


# [aipdd fork delta req46] 工具页「音乐创作」的模型候选：设置页「音乐」TAB 里
# mediaType=music 的条目。与 audio 目录同语义：没配就是空列表（合法状态），
# 提交时按「未配置音乐默认模型」拦住，不拿名字猜一个顶上去。
@router.get("/projects/{project}/freezone/music/models", tags=[TAG_FREEZONE_MUSIC])
async def freezone_music_models(
    project: str,
    user: dict = Depends(get_api_user),
):
    """音乐创作：返回后台配置的音乐模型目录。"""
    ctx, _username, _project_name, _project_dir, _output_dir = (
        await _freezone._resolve_freezone_project(project, user, required_role="viewer")
    )
    catalog = await _freezone._scoped_media_model_catalog(
        "music",
        requester_user_id=ctx.requester_user_id,
    )
    return {"ok": True, "data": catalog or []}


@router.post(
    "/projects/{project}/freezone/marks/detect",
    response_model=FreezoneMarkDetectResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_mark_detect(
    project: str,
    body: FreezoneMarkDetectRequest,
    user: dict = Depends(get_api_user),
):
    """图片处理：识别单张图片中点击点或框选区域的局部元素标记。"""
    ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    source_paths = _freezone._resolve_url_list(project_dir, [body.source_url])
    if not source_paths:
        raise HTTPException(400, "source_url is required")

    has_point = body.point_x is not None and body.point_y is not None
    has_box = all(
        value is not None for value in [body.box_x, body.box_y, body.box_width, body.box_height]
    )
    if not (has_point or has_box):
        raise HTTPException(400, "point or box selection is required")

    usage_meter = get_usage_meter()
    usage_context = {
        "source": "sync_api",
        "endpoint": "freezone_mark_detect",
        "selection": "box" if has_box else "point",
    }
    usage_user_id = str(
        getattr(ctx, "requester_user_id", "")
        or user.get("id")
        or user.get("user_id")
        or user.get("username")
        or ""
    )
    usage_project_id = str(getattr(ctx, "project_id", "") or project)
    # [aipdd fork delta] WP-D: 额度预留已拆除；出网身份作用域仍须覆盖整次 detect，
    # 组织侧真实拒绝时抛 AuthzError，而不是被压成裸 5xx。
    # 非组织身份（平台／个人／CE local／灰度未开）下它什么都不绑，路径不变。
    from novelvideo.api.egress_binding import request_egress_scope

    async with request_egress_scope(
        requester_user_id=ctx.requester_user_id,
        project_id=ctx.project_id,
        task_type="freezone_image_mark_detect",
    ):
        usage_meter.set_llm_usage_context(
            usage_user_id,
            project_id=usage_project_id,
            resource_kind="image",
            billing_metadata=dict(usage_context),
        )
        try:
            result = await _freezone.detect_freezone_mark(
                image_path=Path(source_paths[0]),
                point_x=body.point_x,
                point_y=body.point_y,
                box_x=body.box_x,
                box_y=body.box_y,
                box_width=body.box_width,
                box_height=body.box_height,
            )
        except Exception as exc:
            # 组织拒绝不得压成裸 5xx。两者都是 RuntimeError 子类，原来一起被吞成
            # `HTTPException(500, ...)`，前端只拿到一句自由文本。照 `:353-358` 的
            # 既有裁定办：上抛后由 `app.py:210-232` 的 handler 渲染契约信封，机器码
            # 原样透传（表里没有的码按设计回落 403，`ports/authz.py:47`）。
            from novelvideo.ports.authz import AuthzError
            from novelvideo.task_backend.subprocesses import EgressBoundaryError

            authz_denial = find_authz_error(exc)
            if authz_denial is not None:
                raise authz_denial from exc
            if isinstance(exc, EgressBoundaryError):
                raise AuthzError(exc.code) from exc
            raise HTTPException(500, f"mark detect failed: {exc}") from exc
        finally:
            usage_meter.clear_llm_usage_context()

        return {
            "ok": True,
            "data": {
                "mark": {
                    "label": result["label"],
                    "source_url": body.source_url,
                    "point_x": body.point_x,
                    "point_y": body.point_y,
                    "box_x": body.box_x,
                    "box_y": body.box_y,
                    "box_width": body.box_width,
                    "box_height": body.box_height,
                    "note": result.get("note", ""),
                },
                "provider": result["provider"],
                "model": result["model"],
            },
        }


@router.post(
    "/projects/{project}/freezone/image/reverse-prompt",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_image_reverse_prompt(
    project: str,
    body: FreezoneImageReversePromptRequest,
    user: dict = Depends(get_api_user),
):
    """图片处理：异步反推图片提示词。"""

    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    source_paths = _freezone._resolve_url_list(project_dir, [body.source_url])
    if not source_paths:
        raise HTTPException(400, "source_url is required")
    source_path = Path(source_paths[0])
    if not source_path.exists():
        raise HTTPException(404, f"source not found: {source_path}")
    instruction = (
        body.instruction.strip()
        or DEFAULT_IMAGE_REVERSE_PROMPT_INSTRUCTION
    )

    try:
        job_id = _freezone._new_job_id()
        if ctx is not None:
            return await _freezone._enqueue_freezone_background_job(
                ctx=ctx,
                project_dir=project_dir,
                task_type="freezone_image_reverse_prompt",
                job_id=job_id,
                payload={
                    "source_path": source_path.as_posix(),
                    "instruction": instruction,
                    "canvas_id": body.canvas_id or "",
                    "node_id": body.node_id or "",
                },
            )
        _freezone._start_freezone_image_reverse_prompt_task(
            username=username,
            project=project_name,
            project_dir=project_dir,
            job_id=job_id,
            source_path=source_path,
            instruction=instruction,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
        )
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error("reverse prompt failed", exc)
        raise HTTPException(500, f"reverse prompt failed: {exc}") from exc

    return _accepted_job_response(
        task_type="freezone_image_reverse_prompt",
        username=username,
        project=project_name,
        job_id=job_id,
    )
