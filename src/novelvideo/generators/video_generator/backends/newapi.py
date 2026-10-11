"""newAPI OpenAI 风格异步视频任务生成器（B1 拆包自单文件 video_generator.py）。

patch 敏感名（_record_video_model_call / get_video_result_delivery /
upload_media_bytes / _POST_ACCEPT_AUTHZ_* 重试参数）经包命名空间
（_video_generator.）延迟解析，保证 tests 的 monkeypatch 打点生效。
"""

import asyncio
import hashlib
import json
import math
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Optional

import aiohttp

from novelvideo.authz.authz_retry import retry_authz_read
from novelvideo.generators import video_generator as _video_generator
from novelvideo.i18n_message import lmsg
from novelvideo.newapi_asset_library import is_asset_reference
from novelvideo.ports import update_current_model_call_log
from novelvideo.ports.authz import AuthzError, detach_authz_error
from novelvideo.ports.video_delivery import (
    ArchivedVideoSource,
    VideoDeliveryError,
    VideoDeliveryReceipt,
)
from novelvideo.shared.billing_errors import find_billing_error
from novelvideo.shared.provider_costs import is_definite_no_cost_http_rejection
from novelvideo.shared.provider_errors import (
    classify_provider_video_task_error,
    provider_video_error_message_from_text,
    provider_video_task_error_message,
)
from novelvideo.shared.upstream_quota_errors import is_upstream_quota_error
from novelvideo.storage.media_relay import (
    IMAGE_TRANSFORM_AI_REFERENCE_JPEG,
    media_relay_ttl_seconds,
)
from novelvideo.task_backend.envelope import RunningTaskAuthorityIndeterminate
from novelvideo.usage.video_request_usage import (
    record_video_request,
    update_video_request_status,
)

from ..backend_utils import (
    NEWAPI_VIDEO_DISPLAY_LABELS,
    _seedance2_config_mapping,
    _seedance2_duration_from_config,
)
from ..egress import (
    NEWAPI_MEDIA_INPUT_MIN_TTL_SECONDS,
    NEWAPI_VIDEO_HTTP_TIMEOUT_SECONDS,
    VideoEgressError,
    _safe_video_error_code,
    _video_egress_context,
)
from ..models import VideoGenResult, VideoGenStatus, VideoGeneratorBase
from .seedance2 import ShotReference


# ---------------------------------------------------------------------------
# [aipdd fork delta req46] MiniMax 直连（V2 视频协议）的档位事实。
# 下面这些数字 / 枚举取自上游 V2 契约（2026-10-08 复核 ``platform.minimax.io``
# ``/docs/api-reference/video-generation-v2-create`` 的「Input media limits」两张表 +
# ``/docs/guides/video-generation`` 的 Reference entry 清单）：它们不是渠道清单，
# 所以写在这里不算「写死模型名」（req40 口径）——具体型号名一律由渠道目录自动
# 发现，代码只按名字里的 max 特征分档（快生成变体的分辨率上限与时长下限都不同）。
#
# 参考素材是**两道闸**：单类上限（图 ≤9 / 视频 ≤3 / 音频 ≤3）之外还有一道
# ``混投合计 ≤ 12 个`` 的总闸（AIPDD ``/v1/models`` 同期把它写成
# ``maxReferenceInputs: 12``，两份来源一致）。只按单类截断的话，9 图 + 3 视频 +
# 3 音频 = 15 会整单被上游拒掉 —— 总闸必须一起落，见
# :meth:`_build_minimax_v2_payload`。
# ---------------------------------------------------------------------------

MINIMAX_V2_TEXT_MAX_CHARS = 7000
MINIMAX_V2_MAX_REFERENCE_IMAGES = 9
MINIMAX_V2_MAX_REFERENCE_VIDEOS = 3
MINIMAX_V2_MAX_REFERENCE_AUDIOS = 3
# 单类截完之后还要过的总闸；渠道目录申报了 ``maxReferenceInputs`` 时以申报为准。
MINIMAX_V2_MAX_REFERENCE_TOTAL = 12
MINIMAX_V2_RATIO_ADAPTIVE = "adaptive"
# 具体画幅档位（adaptive 不在内：它是「由输入决定」的选择子，不是画幅）。
MINIMAX_V2_RATIOS: tuple[str, ...] = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")


def _clean_media_urls(value: object) -> list[str]:
    """把 metadata 里的媒体引用列表收成去空的字符串列表。"""

    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def _is_upstream_media_reference(value: object) -> bool:
    """[aipdd fork delta req47] 上游已经能直接读的媒体引用：公网直链或素材库 ``asset://{Id}``。

    素材库引用不是可下载地址，不能再走一次「下载→中转」：库里那份就是本体，而
    ``GetAsset`` / ``ListAssets`` 回的临时直链会过期。拿它当本地路径读会直接抛 OSError，
    所以识别要放在中转之前。
    """

    text = str(value or "").strip()
    if not text:
        return False
    return text.startswith(("http://", "https://")) or is_asset_reference(text)


def _aipdd_video_input_caps(
    model: str, provider: str = ""
) -> dict[str, int | None]:
    """[aipdd fork delta req42] 从渠道目录读视频模型的参考输入配额。

    网关在 ``/v1/models`` 里权威声明了每个视频模型能吃什么参考素材
    （``maxImageInputs`` / ``maxAudioInputs`` / ``maxVideoInputs``，还有混投总闸
    ``maxReferenceInputs``），这里只照**数量**声明取值，**不再拿模型名猜能力**。
    返回 ``None`` 表示目录没说（不自锁）；``0`` 表示目录明说没有这类输入。

    [aipdd fork delta req46] 2026-10-08 实拉 ``ap-minimax-h3``：``maxImageInputs 9`` /
    ``maxAudioInputs 3`` / ``maxVideoInputs 0`` / 总闸 ``maxReferenceInputs 12``。
    这里的 0 必须原样递出去：上游对递进来的参考视频回的是「当前 MiniMax H3 渠道不支持
    视频参考；本次请求未扣费」（带外探针实测），本地不先拦掉就只能拿到一条白扣的片子。

    刻意**不拿 ``supportsReferenceVideo`` / ``supportsReferenceAudio`` 这两个布尔归零**：
    同一份声明里数量与布尔已两次自相矛盾 —— req43 实拉时 H3 写的是 ``supportsReferenceVideo:
    true`` 而它 ``ref2va`` 绑定根本不吃视频，今天是 ``false`` 配数量 0。本仓已把这个布尔定性
    为会骗人（见 ``freezone/catalog.py`` 的 ``_require_omni_reference_inputs_supported``），
    所以布尔不参与判定：要说「不支持」请把数量写成 0，否则按「没说话」不表态。

    ``provider`` 是生效渠道的 provider：同名模型在不同渠道可能是不同形态，不带渠道位全表
    找会串读（本轮新登记了海外 / 国内两条 MiniMax 渠道，正好撞上这一枪）。
    """

    from novelvideo.model_gateway_settings import upstream_catalog_entry
    from novelvideo.catalog.upstream_catalog import declared_media_count, media_metadata

    meta = media_metadata(upstream_catalog_entry(str(model or ""), provider=provider) or {})

    def _declared_count(key: str) -> int | None:
        # 走 ``declared_media_count``：同一渠道里图槽上限有 ``maxImageInputs``（H3）与
        # ``maxInputImages``（LTX 2.5）两种写法，别名表在
        # ``upstream_catalog._MEDIA_COUNT_ALIASES``，只认一种会把已申报的上限读成未申报。
        return declared_media_count(meta, key)

    return {
        "max_image_inputs": _declared_count("maxImageInputs"),
        "max_video_inputs": _declared_count("maxVideoInputs"),
        "max_audio_inputs": _declared_count("maxAudioInputs"),
        # 混投总闸：没有布尔可以推，目录没写就是没说（返回 None 不拦）。
        "max_reference_inputs": _declared_count("maxReferenceInputs"),
    }


# 参考素材的角色 -> 中文名（报错文案用；顺序即摊数字时的阅读顺序）。
_REFERENCE_ROLE_LABELS: dict[str, str] = {
    "image": "参考图片",
    "video": "参考视频",
    "audio": "参考音频",
}


def _enforce_reference_caps(
    model_label: str,
    *,
    supplied: dict[str, list[str]],
    limits: dict[str, int | None],
    sources: dict[str, str],
    total: int | None,
    total_source: str,
    remedies: dict[str, str] | None = None,
) -> None:
    """参考素材的三道闸：申报 0 的类别拒、单类超上限拒、混投超总闸拒。

    [aipdd fork delta req46] 一律**拒单并摊开数字**，不做静默截断：本仓口径是不许
    静默丢素材（req44）—— 截断会让用户看到一条与自己给的素材无关的片子还照常计费。
    文案必须同时给出「你给了几个 / 这份声明只收几个 / 声明出自哪里」，让用户能自己
    决定删哪几份，也让排查时能判断是被哪一道闸拦的。

    ``limits`` 里 ``None`` = 这条来源没说话（不拦）；``0`` = 明说不吃这类素材。
    ``remedies`` 按角色补一句本链路特有的出路（如「对口型需换支持参考音频的模型」）。
    """

    blocked = [
        role
        for role in _REFERENCE_ROLE_LABELS
        if supplied.get(role) and limits.get(role) == 0
    ]
    if blocked:
        sentences = [
            f"{model_label} 不支持"
            + "、".join(_REFERENCE_ROLE_LABELS[role] for role in blocked)
            + "："
            + "、".join(
                f"{sources.get(role) or '渠道目录'} 把{_REFERENCE_ROLE_LABELS[role]}上限申报为 0"
                for role in blocked
            )
            + "。上游收到这类素材会直接拒单（本次请求不扣费），请先去掉对应素材，"
            "或换支持这类输入的视频模型；渠道哪天放开，把数量改到 >=1 就自动跟随，不用改代码"
        ]
        hints = [
            (remedies or {}).get(role)
            for role in blocked
            if (remedies or {}).get(role)
        ]
        if hints:
            sentences.append("常见用途：" + "；".join(hints))
        raise ValueError("".join(sentences))

    for role, label in _REFERENCE_ROLE_LABELS.items():
        urls = supplied.get(role) or []
        limit = limits.get(role)
        if not urls or limit is None or limit <= 0:
            continue
        if len(urls) > limit:
            raise ValueError(
                f"{label}给了 {len(urls)} 个，{sources.get(role) or '上游'} 这条链路只收 "
                f"{limit} 个：请删到 {limit} 个以内再提交"
                "（系统不替你决定丢哪几份 —— 默默截掉只会产出一条与你给的素材无关的片子，还照常计费）"
            )

    kept_total = sum(len(supplied.get(role) or []) for role in _REFERENCE_ROLE_LABELS)
    if total is not None and kept_total > total:
        # 只摊真给了的那几类：「参考音频 0」对只想删图的人来说是噪声。
        detail = " / ".join(
            f"{label} {len(supplied.get(role) or [])}"
            for role, label in _REFERENCE_ROLE_LABELS.items()
            if supplied.get(role)
        )
        raise ValueError(
            f"参考素材合计 {kept_total} 个（{detail}），超过"
            f"{total_source}声明的混投上限 {total} 个："
            f"单类都在限内也不代表递得进去，请删到 {total} 个以内再提交"
            "（系统不替你决定丢哪几份）"
        )


def _capability_bindings_for_model(model: str) -> list[dict[str, Any]]:
    """[aipdd fork delta req44] 该模型所属渠道上次读回的在线视频绑定名册；读不到返回空表。

    渠道把模型代理给第三方 / 不暴露能力接口 / 快照还没落，都是「没读到事实」，返回空表
    就是让调用侧**不表态**（保持原有提交形状），不是「都不支持」。
    """

    from novelvideo.model_gateway_settings import (
        get_provider_capability_bindings,
        model_owner_providers,
        split_provider_spot_model,
    )

    provider, bare_model = split_provider_spot_model(str(model or ""))
    bare_model = str(bare_model or "").strip()
    if not bare_model:
        return []
    providers = [provider] if provider else list(model_owner_providers(bare_model))
    for item in providers:
        provider_key = str(item or "").strip().lower()
        if not provider_key:
            continue
        try:
            bindings = get_provider_capability_bindings(provider_key)
        except Exception:  # noqa: BLE001 - 事实读不到只是没证据，不该因此挡住提交
            continue
        if bindings:
            return bindings
    return []


def _require_public_media_url(url: str, *, kind: str) -> None:
    """[aipdd fork delta req42] 参考视频/音频必须是公网 http(s) 直链，否则当场报错。

    实测依据（2026-10-08 重新校对取证，不再归因到不被读取的字段）：

    - 递一个**被读取**的字段（``image_urls``）里的 404 直链，上游回 400「参考图片链接
      无法被上游访问（HTTP 404）」且**未扣费** —— 素材可达性在上游是被校验的；
    - req42 当时观察到的「塞假 data URI / 不可达直链也照样 202 / QUEUED」其实发生在
      **根本没被读取的** ``video_url`` 字段上（见 :meth:`_build_aipdd_videos_payload` 里的
      复数字段取证），不能当作「上游不校验素材」的证据；
    - 媒体中转处于「本地存储」档时 ``upload_media_bytes`` 回的就是 base64 data URL，
      视频几十 MB 内联既过不了请求体上限、也不可能被上游拉到。

    所以守卫保留（宁可提交前报错，也不要静默出一段不相干的片），且**逐条**校验：
    参考图不在本约束里，data URL 作为图片输入是目前「本地存储」档的既有可用路径。

    [aipdd fork delta req47] 素材库引用 ``asset://{Id}`` 也算过了这道守卫：它指的就是上游
    库里那份**已过审**的素材本体，比一个会过期的临时直链更可信（临时直链约 12 小时过期，
    把它带进一次付费生成，上游大概率拉不到媒体）。
    """

    if _is_upstream_media_reference(url):
        return
    raise ValueError(
        f"{kind}必须使用公网可达的 http(s) 直链或素材库 asset:// 引用，当前拿到的是内联 data URL。"
        "请在「设置 → 媒体存储」填上 AIPDD API Key（选「AIPDD 数字资产云」档，"
        "或在「本地存储」档下用它做参考媒体中转），或配置阿里云 OSS / Cloudinary 中转，"
        "或先把素材上传成公网直链后重试。"
    )


class NewApiVideoError(RuntimeError):
    """newAPI video request failure with gateway request id when available."""

    def __init__(
        self,
        message: str,
        *,
        request_id: str = "",
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.request_id = request_id
        self.status_code = status_code


class NewApiVideoGenerator(VideoGeneratorBase):
    """newAPI OpenAI-style async video task generator."""

    @staticmethod
    def _model_label(model: str) -> str:
        return NEWAPI_VIDEO_DISPLAY_LABELS.get(model, model)

    @staticmethod
    def _parse_duration_bounds_config(raw: str) -> dict[str, tuple[int, int]]:
        from novelvideo.usage.video_duration import parse_video_duration_bounds

        return parse_video_duration_bounds(raw)

    @classmethod
    def _default_generate_audio_for_model(cls, model: str) -> bool:
        from novelvideo.config import NEWAPI_VIDEO_AUDIO_MODELS

        return model.strip() in set(NEWAPI_VIDEO_AUDIO_MODELS)

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        model: Optional[str] = None,
        resolution: Optional[str] = None,
        generate_audio: Optional[bool] = None,
        model_params: Optional[dict[str, Any]] = None,
        request_schema: Optional[dict[str, Any]] = None,
        egress_context: object | None = None,
    ):
        from novelvideo.config import (
            NEWAPI_VIDEO_RESOLUTION,
            get_newapi_video_model,
        )

        # [aipdd fork delta] 默认视频模型只读「设置 → 模型与渠道」的视频映射：
        # ``NEWAPI_VIDEO_MODEL`` env 已退出解析链（面板看不见的名字不能赢过用户选的默认）。
        default_video_model = get_newapi_video_model()
        self.egress_context = _video_egress_context(egress_context)
        # [aipdd fork delta req46] 生效渠道的 provider（设置页渠道卡上的那个 key）。
        # 组织任务走信封解析、平台侧不认得它的渠道，所以留空：判定回落 host 兜底。
        self.channel_provider = ""
        if self.egress_context is not None and self.egress_context.is_organization:
            # Organization egress is request-scoped: the credential never lands on
            # the instance, so there is no LocalNewAPI to select a mode from.
            self.api_key = ""
            self.base_url = ""
        else:
            from novelvideo.config import get_effective_newapi_gateway_config
            from novelvideo.shared.runtime_env import is_ce_effective

            gateway = get_effective_newapi_gateway_config()
            if is_ce_effective():
                try:
                    from novelvideo.model_gateway_settings import (
                        MODE_CUSTOM,
                        MODE_HYBRID,
                        get_ce_newapi_config_for_mode,
                        get_effective_newapi_config,
                        get_newapi_media_model_mappings,
                    )

                    active_gateway = get_effective_newapi_config()
                    media_mapping = get_newapi_media_model_mappings().get(
                        model or default_video_model,
                        {},
                    )
                    if (
                        active_gateway.mode == MODE_HYBRID
                        and media_mapping.get("provider") == "comfyui"
                    ):
                        gateway = get_ce_newapi_config_for_mode(MODE_CUSTOM)
                except (RuntimeError, ImportError):
                    pass
            self.api_key = api_key if api_key is not None else gateway.api_key
            self.base_url = (endpoint or gateway.base_url).rstrip("/")
            # [aipdd fork delta #A] 多网关并存: 若选中视频模型绑定了自带 base+key 的
            # provider 渠道(如 aipdd 这类 OpenAI 兼容网关),按该渠道路由,覆盖单一生效网关。
            if api_key is None and not endpoint:
                try:
                    from novelvideo.model_gateway_settings import (
                        resolve_media_provider_override,
                    )

                    _provider_override = resolve_media_provider_override(
                        model or default_video_model, media_type="video"
                    )
                except Exception:
                    _provider_override = None
                if _provider_override:
                    self.base_url, self.api_key, self.channel_provider = _provider_override
        self.model = model or default_video_model
        self.resolution = resolution or NEWAPI_VIDEO_RESOLUTION
        self.model_params = model_params or {}
        self.request_schema = request_schema or {}
        raw_generate_audio = (
            os.environ.get("NEWAPI_VIDEO_GENERATE_AUDIO", "").strip().lower()
        )
        if generate_audio is not None:
            self.generate_audio = generate_audio
        elif raw_generate_audio == "auto" or not raw_generate_audio:
            self.generate_audio = self._default_generate_audio_for_model(self.model)
        else:
            self.generate_audio = raw_generate_audio in {"true", "1", "yes", "on"}

        if not self.api_key and not (
            self.egress_context is not None and self.egress_context.is_organization
        ):
            raise ValueError(
                "DramaClawAPI key must be set for DramaClawAPI video generation"
            )

    @staticmethod
    def _extract_request_id(text: str = "", headers: object | None = None) -> str:
        if headers:
            for header_name in (
                "x-request-id",
                "x-newapi-request-id",
                "x-oneapi-request-id",
            ):
                value = getattr(headers, "get", lambda _name: "")(header_name)
                if value:
                    return str(value)

        if text:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                error_obj = (
                    data.get("error") if isinstance(data.get("error"), dict) else {}
                )
                for candidate in (
                    data.get("request_id"),
                    data.get("requestId"),
                    error_obj.get("request_id"),
                    error_obj.get("requestId"),
                ):
                    if isinstance(candidate, str) and candidate:
                        return candidate
                text = str(error_obj.get("message") or text)

            match = re.search(
                r"request[\s_-]*id[:：]\s*([A-Za-z0-9_-]+)", text, re.IGNORECASE
            )
            if match:
                return match.group(1)

        return ""

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _client_timeout() -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(total=NEWAPI_VIDEO_HTTP_TIMEOUT_SECONDS)

    async def _post_json(
        self,
        url: str,
        payload: dict,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict:
        async with aiohttp.ClientSession(timeout=self._client_timeout()) as session:
            async with session.post(
                url, json=payload, headers=headers or self.headers
            ) as resp:
                text = await resp.text()
                if resp.status < 200 or resp.status >= 300:
                    request_id = self._extract_request_id(text, resp.headers)
                    raise NewApiVideoError(
                        f"DramaClawAPI submit failed: HTTP {resp.status} - {text}",
                        request_id=request_id,
                        status_code=resp.status,
                    )
                try:
                    data = json.loads(text)
                    if isinstance(data, dict):
                        data["_newapi_request_id"] = self._extract_request_id(
                            text, resp.headers
                        )
                    return data
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"DramaClawAPI submit returned invalid JSON: {text}"
                    ) from exc

    async def _get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict:
        async with aiohttp.ClientSession(timeout=self._client_timeout()) as session:
            async with session.get(url, headers=headers or self.headers) as resp:
                text = await resp.text()
                if resp.status < 200 or resp.status >= 300:
                    request_id = self._extract_request_id(text, resp.headers)
                    raise NewApiVideoError(
                        f"DramaClawAPI task query failed: HTTP {resp.status} - {text}",
                        request_id=request_id,
                        status_code=resp.status,
                    )
                try:
                    return json.loads(text)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"DramaClawAPI task query returned invalid JSON: {text}"
                    ) from exc

    async def _download_video(self, url: str, output_path: str) -> VideoDeliveryReceipt:
        from novelvideo.utils.media_download import download_to_path

        return await download_to_path(url, output_path)

    @staticmethod
    def _archive_state(task: dict) -> tuple[str, bool] | None:
        archive = task.get("archive") if isinstance(task, dict) else None
        if not isinstance(archive, dict):
            return None
        status = str(archive.get("status") or "pending").strip().lower()
        return status, bool(archive.get("retryable"))

    def _archived_video_source(
        self, task: dict, *, fallback_url: str = ""
    ) -> ArchivedVideoSource | None:
        archive = task.get("archive") if isinstance(task, dict) else None
        if not isinstance(archive, dict):
            return None
        if str(archive.get("status") or "").strip().lower() != "success":
            return None
        raw_size = archive.get("size")
        try:
            size = max(0, int(raw_size or 0))
        except (TypeError, ValueError):
            size = 0
        return ArchivedVideoSource(
            asset_id=str(archive.get("asset_id") or ""),
            storage_provider=str(archive.get("storage_provider") or "").strip(),
            bucket=str(archive.get("bucket") or "").strip(),
            object_key=str(archive.get("object_key") or "").strip(),
            url=self._resolve_result_url(
                str(archive.get("url") or fallback_url or "").strip()
            ),
            content_type=str(archive.get("content_type") or "").strip(),
            size=size,
            sha256=str(archive.get("sha256") or "").strip().lower(),
        )

    @staticmethod
    def _ext_from_data_url_header(header: str, default: str = "png") -> str:
        mime_type = header.removeprefix("data:").split(";", 1)[0].strip().lower()
        return {
            "image/jpeg": "jpg",
            "image/jpg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
            "video/mp4": "mp4",
            "video/webm": "webm",
            "audio/mpeg": "mp3",
            "audio/mp3": "mp3",
            "audio/wav": "wav",
            "audio/x-wav": "wav",
            "audio/mp4": "m4a",
        }.get(mime_type, default)

    @classmethod
    async def _relay_frame_input(
        cls, image_value: str, *, default_ext: str = "png"
    ) -> str:
        return await cls._relay_media_input(
            image_value,
            default_ext=default_ext,
            image_transform=IMAGE_TRANSFORM_AI_REFERENCE_JPEG,
        )

    @classmethod
    async def _relay_media_input(
        cls,
        media_value: str,
        *,
        default_ext: str = "png",
        resource_type: str = "image",
        image_transform: str | None = None,
    ) -> str:
        media_value = str(media_value or "").strip()
        if not media_value:
            raise ValueError("empty media input")
        if _is_upstream_media_reference(media_value):
            # 公网直链与素材库引用原样递：后者不是可下载地址，当本地路径读会直接报错。
            return media_value

        if media_value.startswith("data:"):
            header, _, encoded = media_value.partition(",")
            if ";base64" not in header or not encoded:
                raise ValueError("unsupported data URL media input")
            import base64

            data = base64.b64decode(encoded)
            ext = cls._ext_from_data_url_header(header, default_ext)
            return await asyncio.to_thread(
                _video_generator.upload_media_bytes,
                data,
                ext=ext,
                ttl=media_relay_ttl_seconds(
                    minimum=NEWAPI_MEDIA_INPUT_MIN_TTL_SECONDS,
                ),
                resource_type=resource_type,
                image_transform=image_transform,
            )

        media_path = Path(media_value)
        ext = media_path.suffix.lstrip(".") or default_ext
        return await asyncio.to_thread(
            _video_generator.upload_media_bytes,
            media_path.read_bytes(),
            ext=ext,
            ttl=media_relay_ttl_seconds(
                minimum=NEWAPI_MEDIA_INPUT_MIN_TTL_SECONDS,
            ),
            resource_type=resource_type,
            image_transform=image_transform,
        )

    async def _apply_huimeng_protocol_media_inputs(
        self,
        metadata: dict[str, object],
        *,
        mode: str,
        image_path: str,
        last_frame_path: str | None,
        references: object,
        log: Callable[[str], None],
    ) -> None:
        """Populate the stable DramaClaw-to-gateway video media protocol."""

        normalized_mode = {
            "textToVideo": "text_to_video",
            "imageToVideo": "image_reference",
            "firstLastFrame": "first_last_frame",
            "imageReference": "image_reference",
            "allReference": "all_reference",
            "videoEdit": "video_edit",
            "videoExtend": "video_extend",
        }.get(str(mode or "").strip(), str(mode or "").strip())

        if normalized_mode == "text_to_video":
            return

        if normalized_mode == "first_frame":
            first_frame_path = image_path
            if not first_frame_path:
                first_frame_path = next(
                    (
                        str(getattr(ref, "path", "") or "").strip()
                        for ref in (references if isinstance(references, list) else [])
                        if str(getattr(ref, "type", "") or "image").strip().lower()
                        == "image"
                    ),
                    "",
                )
            if not first_frame_path:
                raise ValueError("first frame is required for first_frame mode")
            metadata["image_url"] = await self._relay_frame_input(first_frame_path)
            return

        if normalized_mode == "first_last_frame":
            if not image_path and not last_frame_path:
                raise ValueError(
                    "at least one keyframe is required for first_last_frame mode"
                )
            if image_path:
                metadata["first_frame_image"] = await self._relay_frame_input(
                    image_path
                )
            if last_frame_path:
                metadata["last_frame_image"] = await self._relay_frame_input(
                    str(last_frame_path)
                )
            return

        raw_items: list[tuple[str, str, str]] = []
        for ref in references if isinstance(references, list) else []:
            media_type = str(getattr(ref, "type", "") or "image").strip().lower()
            path = str(getattr(ref, "path", "") or "").strip()
            role = str(getattr(ref, "role", "") or "").strip()
            if path and media_type in {"image", "video", "audio", "file", "link"}:
                raw_items.append((media_type, path, role))

        seen: set[tuple[str, str]] = set()
        relayed: dict[str, list[tuple[str, str]]] = {
            "image": [],
            "video": [],
            "audio": [],
            "file": [],
            "link": [],
        }
        for media_type, path, role in raw_items:
            identity = (media_type, path)
            if identity in seen:
                continue
            seen.add(identity)
            if media_type == "image":
                url = await self._relay_frame_input(path)
            elif media_type == "link":
                parsed = urllib.parse.urlsplit(path)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    raise ValueError("reference link must be an HTTP/HTTPS URL")
                url = path
            else:
                url = await self._relay_media_input(
                    path,
                    default_ext=(
                        "mp4"
                        if media_type == "video"
                        else "mp3" if media_type == "audio" else "bin"
                    ),
                    resource_type="raw" if media_type == "file" else "video",
                )
            if not _is_upstream_media_reference(path):
                log(f"{media_type} 参考素材已上传到媒体中转")
            relayed[media_type].append((url, role))

        image_urls = [url for url, _role in relayed["image"]]
        video_urls = [url for url, _role in relayed["video"]]
        audio_urls = [url for url, _role in relayed["audio"]]
        file_urls = [url for url, _role in relayed["file"]]
        link_urls = [url for url, _role in relayed["link"]]

        if file_urls and link_urls:
            raise ValueError("reference_file and reference_link are mutually exclusive")

        if normalized_mode in {
            "image_reference",
            "all_reference",
            "video_edit",
            "video_extend",
        }:
            if image_urls:
                metadata["reference_images"] = image_urls
            if video_urls:
                metadata["reference_videos"] = video_urls
            if audio_urls:
                metadata["reference_audios"] = audio_urls
            if file_urls:
                metadata["reference_file"] = file_urls[0]
            if link_urls:
                metadata["reference_link"] = link_urls[0]
            return

        raise ValueError(f"unsupported video generation mode: {normalized_mode}")

    def _duration_bounds(self) -> tuple[int, int]:
        from novelvideo.usage.video_duration import video_duration_bounds_for_backend

        bounds = video_duration_bounds_for_backend(f"newapi_{self.model}")
        if bounds[0] is None or bounds[1] is None:
            return 2, 12
        return int(bounds[0]), int(bounds[1])

    def _is_seedance2_model(self) -> bool:
        # [aipdd fork delta #6] 容错识别 "AP Seedance-2.0 标准版" 等带空格/中文的 id;涵盖 2.5。
        return "seedance-2." in self.model.strip().lower()

    def _is_happyhorse_model(self) -> bool:
        return self.model.strip().lower() == "happyhorse-1.0"

    def _is_grok_video_channel_model(self) -> bool:
        return self.model.strip().lower() == "grok-video-channel"

    @staticmethod
    def _happyhorse_ratio(value: str | None) -> str:
        text = str(value or "").strip()
        return text if text in {"16:9", "9:16", "1:1", "4:3", "3:4"} else "16:9"

    @staticmethod
    def _happyhorse_resolution(value: str | None) -> str:
        text = str(value or "").strip().lower()
        if "720" in text:
            return "720P"
        return "1080P"

    @staticmethod
    def _happyhorse_audio_setting(value: str | None) -> str:
        text = str(value or "").strip().lower()
        return text if text in {"auto", "origin"} else "auto"

    @staticmethod
    def _video_dimensions(
        resolution: object,
        ratio: object,
    ) -> tuple[int, int] | None:
        """Convert a resolution tier and aspect ratio to exact pixel dimensions."""

        resolution_text = str(resolution or "").strip().lower()
        p_match = re.fullmatch(r"(\d+)p", resolution_text)
        # Preserve compatibility with older catalogs that stored video tiers
        # as bare values such as "1080" instead of "1080p".
        if p_match is None:
            p_match = re.fullmatch(r"(\d{3,4})", resolution_text)
        video_k_long_edges = {
            "2k": 2560,
            "4k": 3840,
        }
        long_edge = video_k_long_edges.get(resolution_text)
        ratio_text = str(ratio or "").strip().lower()
        if (
            (p_match is None and long_edge is None)
            or ratio_text == "adaptive"
            or ":" not in ratio_text
        ):
            return None
        try:
            ratio_width, ratio_height = (
                float(part.strip()) for part in ratio_text.split(":", 1)
            )
        except (TypeError, ValueError):
            return None
        if ratio_width <= 0 or ratio_height <= 0:
            return None

        if long_edge is not None:
            if ratio_width >= ratio_height:
                width = long_edge
                height = round(long_edge * ratio_height / ratio_width)
            else:
                height = long_edge
                width = round(long_edge * ratio_width / ratio_height)
        else:
            short_edge = int(p_match.group(1))
            if short_edge <= 0:
                return None
            if ratio_width >= ratio_height:
                height = short_edge
                width = round(short_edge * ratio_width / ratio_height)
            else:
                width = short_edge
                height = round(short_edge * ratio_height / ratio_width)

        # Video encoders generally require even dimensions.
        width += width % 2
        height += height % 2
        return width, height

    @classmethod
    def _canonicalize_video_payload(
        cls,
        payload: dict[str, object],
        metadata: dict[str, object],
    ) -> None:
        """Translate legacy channel-shaped fields into the generic NewAPI contract.

        Existing model-specific branches still validate and collect their inputs
        during phase one. Only the final wire representation is normalized here.
        """

        # Keep the model-level semantics in metadata even after deriving the
        # generic width/height fields. NewAPI model adapters may need the
        # original ratio/resolution (notably for image-to-video) and cannot
        # reliably reconstruct them from an input image's exact pixel ratio.
        resolution = metadata.get("resolution", "")
        ratio = metadata.get("ratio", "")
        if resolution:
            metadata["resolution"] = str(resolution).strip().lower()
            resolution = metadata["resolution"]
        if ratio:
            metadata["ratio"] = str(ratio).strip().lower()
            ratio = metadata["ratio"]

        duration_value = payload.pop("duration", None)
        legacy_seconds = payload.pop("seconds", None)
        if duration_value is None:
            duration_value = legacy_seconds
        if str(duration_value or "").strip().lower() == "auto":
            payload["duration"] = "auto"
        else:
            try:
                duration_number = float(str(duration_value))
            except (TypeError, ValueError):
                duration_number = 0
            if duration_number > 0:
                payload["duration"] = (
                    int(duration_number)
                    if duration_number.is_integer()
                    else duration_number
                )

        first_frame = ""
        for key in ("first_frame_image", "image_url"):
            value = metadata.pop(key, "")
            if not first_frame and isinstance(value, str) and value.strip():
                first_frame = value.strip()

        last_frame = metadata.pop("last_frame_image", "")
        if not isinstance(last_frame, str):
            last_frame = ""

        legacy_images = payload.pop("images", None)
        if isinstance(legacy_images, list):
            image_values = [
                str(value).strip()
                for value in legacy_images
                if isinstance(value, str) and str(value).strip()
            ]
            if image_values and not first_frame:
                first_frame = image_values[0]
            if len(image_values) > 1:
                references = metadata.get("reference_images")
                existing = list(references) if isinstance(references, list) else []
                metadata["reference_images"] = [*image_values[1:], *existing]

        content = metadata.pop("content", None)
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "image_url":
                    continue
                image_url = item.get("image_url")
                url = image_url.get("url") if isinstance(image_url, dict) else image_url
                if not isinstance(url, str) or not url.strip():
                    continue
                role = str(item.get("role") or "").strip().lower()
                if role == "last_frame" and not last_frame:
                    last_frame = url.strip()
                elif role == "first_frame" and not first_frame:
                    first_frame = url.strip()

        legacy_video = metadata.pop("video_url", "")
        if isinstance(legacy_video, str) and legacy_video.strip():
            references = metadata.get("reference_videos")
            existing = list(references) if isinstance(references, list) else []
            metadata["reference_videos"] = [legacy_video.strip(), *existing]

        if first_frame:
            payload["image"] = first_frame
        if last_frame.strip():
            metadata["last_frame_image"] = last_frame.strip()

        ratio_text = str(ratio or "").strip().lower()
        if ratio_text in {"auto", "adaptive"}:
            # Video follow-input geometry is expressed by ratio only. Sending
            # width/height as well would turn an automatic request back into a
            # fixed-size request at the NewAPI adapter boundary.
            metadata["ratio"] = "auto"
            metadata.pop("aspect_ratio", None)
            payload.pop("width", None)
            payload.pop("height", None)
        elif dimensions := cls._video_dimensions(resolution, ratio_text):
            payload["width"], payload["height"] = dimensions
            # Keep the selected ratio semantics beside the derived dimensions;
            # resolution remains the independent model/charging tier.
            metadata["ratio"] = ratio_text
            metadata.pop("aspect_ratio", None)
        else:
            resolution_text = str(resolution or "").strip()
            if ratio_text:
                metadata["ratio"] = ratio_text
            if resolution_text:
                metadata["resolution"] = resolution_text

        payload["n"] = 1
        payload["response_format"] = "url"
        if metadata:
            payload["metadata"] = metadata
        else:
            payload.pop("metadata", None)

    @staticmethod
    def _task_response_data(task: dict) -> dict:
        """Accept both flat and wrapped generic task responses."""

        if not isinstance(task, dict):
            return {}
        data = task.get("data")
        if isinstance(data, dict):
            merged = dict(data)
            if "_newapi_request_id" not in merged and task.get("_newapi_request_id"):
                merged["_newapi_request_id"] = task["_newapi_request_id"]
            return merged
        return task

    @classmethod
    def _task_id_from_submit_response(cls, submitted: dict) -> str:
        task = cls._task_response_data(submitted)
        return str(task.get("task_id") or task.get("id") or "").strip()

    async def _relay_seedance2_references(
        self,
        references: list["ShotReference"] | None,
        *,
        log: Callable[[str], None],
    ) -> dict[str, list[str]]:
        reference_urls: dict[str, list[str]] = {}
        for ref in references or []:
            ref_type = str(getattr(ref, "type", "") or "image").strip().lower()
            path = str(getattr(ref, "path", "") or "").strip()
            if not path:
                continue
            if ref_type == "image":
                key = "reference_images"
                default_ext = "png"
                label = "图片参考"
                image_transform = IMAGE_TRANSFORM_AI_REFERENCE_JPEG
                resource_type = "image"
            elif ref_type == "video":
                key = "reference_videos"
                default_ext = "mp4"
                label = "视频参考"
                image_transform = None
                resource_type = "video"
            elif ref_type == "audio":
                key = "reference_audios"
                default_ext = "mp3"
                label = "音频参考"
                image_transform = None
                resource_type = "video"
            else:
                continue
            url = await self._relay_media_input(
                path,
                default_ext=default_ext,
                resource_type=resource_type,
                image_transform=image_transform,
            )
            if not _is_upstream_media_reference(path):
                log(f"{label}已上传到媒体中转")
            reference_urls.setdefault(key, []).append(url)
        return reference_urls

    @staticmethod
    def _extract_video_url(task: dict) -> str:
        if not isinstance(task, dict):
            return ""

        result_url = task.get("result_url")
        if isinstance(result_url, str) and result_url.strip():
            return result_url.strip()

        # [aipdd fork delta] AIPDD /v1/videos 完成后直接返回 content_url。
        content_url = task.get("content_url")
        if isinstance(content_url, str) and content_url.strip():
            return content_url.strip()

        metadata = task.get("metadata")
        if isinstance(metadata, dict):
            for key in ("url", "video_url"):
                value = metadata.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        for key in ("url", "video_url"):
            value = task.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _uses_aipdd_videos_api(base_url: str, model: str = "") -> bool:
        """[aipdd fork delta] 视频是否走 OpenAI 风格 ``/v1/videos`` 异步任务协议。

        与 NewAPI 的 ``/v1/video/generations`` 是不同路径（AIPDD 上后者 404）。

        [aipdd fork delta req41] 先问渠道目录：该模型的条目声明了 ``videos`` 就按
        声明走（确定渠道的端点是上游事实）；目录没写端点（自建网关清单只有名字）才
        按 host 子串兜底（默认 aipdd，可用 ``AIPDD_VIDEO_API_HOST_HINT`` 覆盖）。
        """

        from novelvideo.model_gateway_settings import catalog_endpoints
        from novelvideo.catalog.upstream_catalog import EP_VIDEOS

        declared = catalog_endpoints(model) if str(model or "").strip() else []
        if declared:
            return EP_VIDEOS in declared
        hint = (os.environ.get("AIPDD_VIDEO_API_HOST_HINT") or "aipdd").strip().lower()
        if not hint:
            return False
        host = (urllib.parse.urlsplit(str(base_url or "")).hostname or "").lower()
        return hint in host

    @staticmethod
    def _videos_request_paths(model: str, use_videos_api: bool) -> tuple[str, str]:
        """[aipdd fork delta req41] ``(提交相对路径, 轮询路径前缀)``，两条都先问渠道目录。

        旧实现把 ``/videos`` 与 ``/videos/`` 钉在代码里：命中协议后虽拼对了 AIPDD，
        但上游一旦改名就得改代码，而且轮询完全没读目录里已声明的 ``videos/{taskId}``。
        实现收敛在 ``model_gateway_settings.videos_request_paths``（与 agent 直连共用），
        目录没端点声明时行为与旧版一致。
        """

        from novelvideo.model_gateway_settings import videos_request_paths

        return videos_request_paths(model, use_videos_api)

    def _build_aipdd_videos_payload(
        self,
        payload: dict[str, Any],
        metadata: dict[str, Any],
        *,
        prompt: str,
        duration: Any,
        resolution: str,
        ratio: str,
        mode: str | None = None,
    ) -> dict[str, Any]:
        """按 AIPDD 公布的 ``/v1/videos`` 通用视频契约拼建单体。

        [aipdd fork delta req42] 契约骨架取自 AIPDD 官方技能包脚本
        ``需求/参考源码/aipdd-copywrite-fetch-1.0.2/scripts/invoke.js`` 的通用视频体：
        ``model / prompt / duration_seconds / video_resolution / ratio / image_urls[]
        / audio_urls[] / first_frame / last_frame``。旧实现只搬了
        ``image_urls`` 一件，**源视频与参考音频整个丢掉** —— 于是「替换视频人物 /
        替换视频产品 / 对口型」拿到的是一条纯文生（+参考图）请求，产物自然和原
        视频毫无关系。字段名也停在被文档列为禁用的旧写法（H3 红线：禁止
        ``duration``、``resolution``、``images``、单值 ``audio``）。

        **参考视频是复数 ``video_urls``，不是脚本里的单值 ``video_url``**：那份单值写法
        已被 2026-10-08 的带外探针推翻（递 ``video_url`` 上游 202 受理、素材拉不到也
        照样扣费出片 = 该字段没被读取；而它自己的 400 文案点名「请使用 image_urls、
        audio_urls、video_urls 或首尾帧字段」）。取证边界：**正向证据只到
        ``ap-minimax-h3``**（渠道回包里点名给的就是这一族字段），同渠道的 ``ap-ltx-2.5``
        与编辑 / 延长路径未做付费探针（要再花 awcoin 需先拿到批准）；LTX 现在在
        目录里还没申报任何参考上限（caps 全 None），本闸不拦、提交形状与本轮前一致。

        旧名 ``duration`` / ``resolution`` 仍然一并下发：本地 NewAPI 网关的计费与
        参数校验按这两个名字绑定（实测 ``duration`` 传错类型报 ``invalid_seconds``），
        AIPDD 对旧名静默忽略，所以真正生效的是 ``duration_seconds`` /
        ``video_resolution`` 这一对。

        [aipdd fork delta req44] ``mode`` 按提交模式**分槽 + 说选择子**（用户给的渠道
        路由口径）：图生视频 / 首帧 / 首尾帧 → ``first_last_frame_to_video`` 并把图递进
        ``first_frame`` / ``last_frame`` 专用字段；文生视频 → ``text_to_video`` 且不递任何
        素材；全能参考 → ``ref2va``，继续走 ``image_urls`` / ``video_urls`` / ``audio_urls``。
        旧实现把首帧、尾帧全折进 ``image_urls``：渠道首尾帧绑定的入参槽是
        ``image``（首帧图片）/ ``referenceImage``（尾帧图片），拿不到首尾帧位就等于
        没告诉它该调哪条工作流 —— 而 H3 的首尾帧与 ref2va 两条绑定入参面板一模一样、
        只差权重与节点，只按输入形状递素材根本分不开。视频编辑 / 延长等渠道没给出
        对应绑定的模式**不发 ``mode``**，保持原有 ``image_urls`` 行为。
        """

        from novelvideo.catalog.upstream_capabilities import (
            AIPDD_VIDEOS_MODE_FRAMES,
            AIPDD_VIDEOS_MODE_TEXT,
            aipdd_videos_mode_selector,
            normalize_request_mode,
            video_bindings_need_mode_selector,
        )

        body: dict[str, Any] = {"model": self.model, "prompt": prompt}
        res = str(resolution or metadata.get("resolution") or "").strip()
        if res:
            body["resolution"] = res
            body["video_resolution"] = res
        dur = duration if duration is not None else payload.get("duration")
        if dur is not None and str(dur).strip().lower() != "auto":
            try:
                dur_value: int | float = int(float(dur))
            except (TypeError, ValueError):
                dur_value = dur
            body["duration"] = dur_value
            body["duration_seconds"] = dur_value
        rat = str(ratio or metadata.get("ratio") or "").strip()
        if rat and rat.lower() not in {"auto", "adaptive"}:
            body["ratio"] = rat

        image_urls: list[str] = []
        first_frame = payload.get("image")
        if not isinstance(first_frame, str):
            first_frame = ""
        first_frame = first_frame.strip()
        refs = metadata.get("reference_images")
        ref_images = (
            [str(u).strip() for u in refs if isinstance(u, str) and str(u).strip()]
            if isinstance(refs, list)
            else []
        )
        last_frame = metadata.get("last_frame_image")
        if not isinstance(last_frame, str):
            last_frame = ""
        last_frame = last_frame.strip()

        mode_key = normalize_request_mode(mode)
        if mode_key == "image_reference" and len(ref_images) == 1 and not first_frame:
            # 画布「图生视频」与「图片参考」在执行协议里是同一个 ``image_reference``
            # （``api/schemas.py`` 明说「两者执行时都使用 image_reference 协议」），
            # 到这一步只剩素材形状能把它们分开：**单图 = 图生视频**，递首帧槽；
            # 多图仍然是参考素材，走全能参考那条。
            mode_key = "image_to_video"
        selector = aipdd_videos_mode_selector(mode_key)
        if selector and not video_bindings_need_mode_selector(
            _capability_bindings_for_model(self.model), self.model
        ):
            # [aipdd fork delta req44] 只有渠道自己按 taskKind 分不清该跑哪条绑定时，才替
            # 它把模式说出来（H3：两条绑定同名 taskKind + 同样的入参面板）。LTX 2.5 / Seedance
            # 这些名下绑定互不重名、或压根没读成名册的，建单体**与旧行为逐字节一致**：
            # 不发 ``mode``，素材照旧折进 ``image_urls``。用户 2026-10-08 的口径：「我说的是
            # minimax 模型，seedance 该怎样就怎样」。
            selector = ""
        if selector:
            body["mode"] = selector
        frame_slots = selector == AIPDD_VIDEOS_MODE_FRAMES
        text_only = selector == AIPDD_VIDEOS_MODE_TEXT

        if frame_slots:
            # 首帧优先取已经落位的首帧图，其次取第一张参考图（图生视频走的就是这一条）。
            # 这条绑定只有首 / 尾两个图槽，多出来的参考图不下发 —— 渠道自己在
            # ``/v1/models`` 申报 maxImageInputs 9 与绑定只有两槽是矛盾的，报错侧已经把
            # 两份事实都摊出来了，这里不再靠塞 ``image_urls`` 赌上游怎么折槽。
            if first_frame:
                body["first_frame"] = first_frame
            elif ref_images:
                body["first_frame"] = ref_images[0]
            if last_frame:
                body["last_frame"] = last_frame
        elif not text_only:
            if first_frame:
                image_urls.append(first_frame)
            image_urls.extend(ref_images)
            if last_frame:
                image_urls.append(last_frame)
        image_urls = list(dict.fromkeys(image_urls))

        # 文生视频不递任何素材（渠道那条 t2v 绑定只认 prompt / 时长 / 种子）；帧槽模式
        # 也不递参考视频 / 音频（首尾帧绑定没有这些槽）。两类都是「递了也没人接」，
        # 与其让上游默默忽略出一条不相干的片子，不如不递。
        video_urls = (
            [] if text_only or frame_slots else _clean_media_urls(metadata.get("reference_videos"))
        )
        audio_urls = (
            [] if text_only or frame_slots else _clean_media_urls(metadata.get("reference_audios"))
        )

        caps = _aipdd_video_input_caps(self.model, provider=self.channel_provider)
        # [aipdd fork delta req46] 三道闸统一走 :func:`_enforce_reference_caps`：先把「申报 0
        # 的类别」拒掉（这样「渠道不支持参考视频」不会被「素材不是公网直链」抢在后头说成
        # 另一回事），再比单类上限，最后过 ``maxReferenceInputs`` 混投总闸（H3 = 12）。
        # 以前这里是「超了就截」，而截断就是静默丢素材：递 12 张图只会递进去 9 张，用户
        # 看到的是“我明明挂了 12 张”的片子。现在一律拒单并把三个数字摊开。
        _enforce_reference_caps(
            self._model_label(self.model),
            supplied={
                "image": image_urls,
                "video": video_urls,
                "audio": audio_urls,
            },
            limits={
                "image": caps.get("max_image_inputs"),
                "video": caps.get("max_video_inputs"),
                "audio": caps.get("max_audio_inputs"),
            },
            sources={role: "渠道目录" for role in _REFERENCE_ROLE_LABELS},
            total=caps.get("max_reference_inputs"),
            total_source="渠道目录 maxReferenceInputs",
            remedies={
                "video": "替换视频人物 / 替换视频产品需支持参考视频的视频模型",
                "audio": "对口型需支持参考音频的视频模型",
            },
        )
        # 素材可达性：参考视频 / 音频必须是公网直链（参考图允许内联，理由见
        # :func:`_require_public_media_url`）。**逐条**校验，不是只看第一条 —— 字段复数化之后
        # 「第 2 条是个 base64 data URL」同样会静默出一条与素材无关的片子。
        for url in video_urls:
            _require_public_media_url(url, kind="参考视频")
        for url in audio_urls:
            _require_public_media_url(url, kind="参考音频")

        if image_urls:
            body["image_urls"] = image_urls
        if video_urls:
            # [aipdd fork delta req46] 参考视频字段改回复数 ``video_urls``，不再折成单值
            # ``video_url``。req42 当时按渠道参考脚本里的单值字段实现，2026-10-08 带外探针实测
            # 推翻了那份认识：递 ``video_url`` 上游回 202 开跑、拉不到素材也照样扣费出片
            # （字段根本没被读），而它自己的 400 文案点名「请使用 image_urls、audio_urls、
            # video_urls 或首尾帧字段」。按上游点名的字段递，不支持的那类才能拿到「未扣费」
            # 的明拒而不是白扣一条不相干的片子。
            body["video_urls"] = video_urls
        if audio_urls:
            body["audio_urls"] = audio_urls
        return body

    # ------------------------------------------------------------------
    # [aipdd fork delta req46] MiniMax 直连（V2 视频协议）
    #
    # H3 的 V2 协议与两条既有链路都不一样：多模态输入收在 ``content[]`` 里按
    # ``type`` + ``role`` 装配，参数是顶层 ``resolution`` / ``duration`` / ``ratio``，
    # 结果在 ``task.content.url``，错误则是**真实 HTTP 状态码 + OpenAI 风格 error**。
    # 渠道登记的是 OpenAI 兼容适配器（type=1），网关不会替它转这条协议，所以命中
    # MiniMax 时必须直连 —— 但模型名与档位仍不写死（req40 口径）：型号来自设置页
    # 目录，代码只按名字特征分档。
    # ------------------------------------------------------------------

    @staticmethod
    def _uses_minimax_videos_v2(
        base_url: str, model: str = "", provider: str = ""
    ) -> bool:
        """视频是否走 MiniMax 的 ``/v2/video_generation`` 直连协议。

        判定顺序（与 :meth:`_uses_aipdd_videos_api` 同形：先确定事实、再问目录、
        最后 host 兜底）：

        1. 生效渠道 provider 是登记过的 MiniMax 渠道 —— 设置页的确定事实，直接命中；
        2. 目录已经给这个模型声明了端点（= 网关按声明的协议代理它）就按声明走，不猜；
        3. host 兜底：默认认 MiniMax 官方域名，自建代理用
           ``MINIMAX_VIDEO_API_HOST_HINT`` 覆盖（与 ``AIPDD_VIDEO_API_HOST_HINT``
           同款口径，组织任务的 base_url 也走这一条）。
        """

        from novelvideo.freezone import minimax_api

        if minimax_api.is_minimax_provider(provider):
            return True
        from novelvideo.model_gateway_settings import catalog_endpoints

        declared = catalog_endpoints(model) if str(model or "").strip() else []
        if declared:
            return False
        host = (urllib.parse.urlsplit(str(base_url or "")).hostname or "").lower()
        hint = (os.environ.get("MINIMAX_VIDEO_API_HOST_HINT") or "").strip().lower()
        if hint:
            return bool(host) and hint in host
        return minimax_api.is_minimax_host(base_url)

    @classmethod
    def _is_minimax_h3_max_model(cls, model: str) -> bool:
        """快生成变体：上限 768P、时长从 5 秒起，与标准档不同。

        只看**名字分段**里有没有以 max 起头的那一段（``H3-Max`` / ``H3-Max-2026``）——
        直接子串匹配会被厂商名本身的「MiniMax」误命中，把标准 H3 当成快生成档。
        """

        segments = [
            part
            for part in re.split(r"[^a-z0-9]+", str(model or "").strip().lower())
            if part
        ]
        return any(part.startswith("max") for part in segments)

    @classmethod
    def _minimax_v2_allowed_resolutions(cls, model: str) -> tuple[str, ...]:
        if cls._is_minimax_h3_max_model(model):
            return ("480P", "768P")
        return ("768P", "2K")

    def _minimax_v2_resolution(self, requested: str) -> str:
        """把界面/网关传来的分辨率落到该型号真能出的档位。

        ``720p`` / ``1080p`` 这类名字在上游没有对应档（只有 480P / 768P / 2K），
        一律落 768P；**2K 只有标准档能出**，快生成变体要 2K 当场拦住 —— 与其让
        上游默默按 768P 出片、界面上却写着 2K，不如提交前说清楚。
        """

        allowed = self._minimax_v2_allowed_resolutions(self.model)
        raw = str(requested or "").strip().lower()
        wanted = ""
        if raw in {"2k", "1440p", "2048p"}:
            wanted = "2K"
        elif raw == "480p":
            wanted = "480P"
        elif raw in {"768p", "720p", "1080p"}:
            wanted = "768P"
        fallback = "768P" if "768P" in allowed else allowed[0]
        if not wanted:
            return fallback
        if wanted not in allowed:
            if wanted == "2K":
                raise ValueError(
                    f"{self._model_label(self.model)} 出不了 2K（可出档位："
                    f"{' / '.join(allowed)}）：2K 只在标准 H3 上可选，"
                    "请把视频分辨率改成 768P，或换一个支持 2K 的视频模型"
                )
            return fallback
        return wanted

    def _minimax_v2_duration(self, requested: Any) -> int:
        """时长（秒）：标准档 4–15、快生成变体 5–15；越界当场拦住，不派注定废的任务。"""

        low, high = (5, 15) if self._is_minimax_h3_max_model(self.model) else (4, 15)
        try:
            value = int(round(float(requested)))
        except (TypeError, ValueError):
            value = 6
        if value < low or value > high:
            raise ValueError(
                f"{self._model_label(self.model)} 的时长只支持 {low}–{high} 秒，"
                f"收到 {value} 秒：请调整时长，或换一个覆盖该时长的视频模型"
            )
        return value

    @classmethod
    def _minimax_v2_ratio(
        cls, requested: str, *, frame_roles: bool, has_media: bool
    ) -> str:
        """画幅：首/尾帧模式恒 ``adaptive``（上游按图片决定画幅，别的值会被忽略）；
        纯文生**必须**给具体画幅（上游不接受 adaptive），拿不到就落应用默认竖屏。"""

        clean = str(requested or "").strip().lower()
        if frame_roles:
            return MINIMAX_V2_RATIO_ADAPTIVE
        if clean in MINIMAX_V2_RATIOS:
            return clean
        if has_media:
            return MINIMAX_V2_RATIO_ADAPTIVE
        return "9:16"

    def _minimax_v2_reference_limits(self) -> tuple[dict[str, int], dict[str, str]]:
        """参考素材的槽位上限与**它的出处**：渠道目录申报优先，没申报才回落到上游契约常量。

        [aipdd fork delta req46] 9 / 3 / 3 / 合计 12 是上游契约写的**天花板**，不是
        每个渠道都开到了顶：2026-10-08 实拉 AIPDD ``/v1/models``，``ap-minimax-h3`` 就写着
        ``maxImageInputs: 9`` / ``maxAudioInputs: 3`` / ``maxVideoInputs: 0``（它只放图与音频，
        ``maxReferenceInputs: 12`` 刚好 = 9 + 3）。目录明说的 0 必须照办：把视频参考递过去
        不会「多试一次」，而是整单被上游拒掉。反过来，上游哪天把某一类放开到更高，
        申报一到就自动跟随，不需要改代码。

        返回 ``(limits, sources)``：``limits`` 是 image / video / audio / total 四个非负 int，
        ``sources`` 是写进报错文案的出处 —— 同一个数字来自渠道还是来自契约文档，用户要做的
        事完全不同（前者让渠道改申报，后者是硬约束只能删素材）。
        总闸与单类上限走**同一份 caps**、同一套「>=0 就算声明」的口径；以前这里绕过 caps
        二次查表且只认 >0，两条链路对同一份申报会判出不同结果。
        """

        caps = _aipdd_video_input_caps(self.model, provider=self.channel_provider)
        fallback = {
            "image": MINIMAX_V2_MAX_REFERENCE_IMAGES,
            "video": MINIMAX_V2_MAX_REFERENCE_VIDEOS,
            "audio": MINIMAX_V2_MAX_REFERENCE_AUDIOS,
            "total": MINIMAX_V2_MAX_REFERENCE_TOTAL,
        }
        caps_keys = {
            "image": "max_image_inputs",
            "video": "max_video_inputs",
            "audio": "max_audio_inputs",
            "total": "max_reference_inputs",
        }
        limits: dict[str, int] = {}
        sources: dict[str, str] = {}
        for role, cap_key in caps_keys.items():
            declared = caps.get(cap_key)
            if declared is None:
                limits[role] = int(fallback[role])
                sources[role] = "上游契约"
            else:
                limits[role] = max(0, int(declared))
                sources[role] = "渠道目录"
        return limits, sources

    def _build_minimax_v2_payload(
        self,
        *,
        prompt: str,
        duration: Any,
        resolution: str,
        ratio: str,
        media: dict[str, Any],
    ) -> dict[str, Any]:
        """模式 → ``content[]`` 的唯一收口：提交前按上游约束校验，越界当场报错。

        ``media`` 是本文件 ``_collect_minimax_v2_media`` 铺好的角色槽（值都已经是
        上游能读的地址：公网直链 / ``mm_file://{file_id}`` / data URI）。
        """

        clean_prompt = str(prompt or "").strip()
        if not clean_prompt:
            raise ValueError(
                "MiniMax 视频必须有文本提示词：上游要求 content 里含一条非空 text"
            )
        if len(clean_prompt) > MINIMAX_V2_TEXT_MAX_CHARS:
            raise ValueError(
                f"提示词 {len(clean_prompt)} 字，超过上游上限 "
                f"{MINIMAX_V2_TEXT_MAX_CHARS} 字，请先精简"
            )
        first_frame = str(media.get("first_frame") or "").strip()
        last_frame = str(media.get("last_frame") or "").strip()
        reference_images = _clean_media_urls(media.get("reference_images"))
        reference_videos = _clean_media_urls(media.get("reference_videos"))
        reference_audios = _clean_media_urls(media.get("reference_audios"))
        frame_roles = bool(first_frame or last_frame)
        reference_roles = bool(reference_images or reference_videos or reference_audios)
        if frame_roles and reference_roles:
            # 上游把这两组角色写成互斥：混着递交只会收到参数错误（或出一条与首帧
            # 无关的片子）。与其赌，不如把选择权还给用户。
            raise ValueError(
                "首帧 / 尾帧与参考素材不能同批递交（上游 content 里 first_frame / "
                "last_frame 与 reference_* 两组角色互斥）：要按帧生成就去掉参考素材，"
                "要按参考生成就不要带首帧或尾帧"
            )
        content: list[dict[str, Any]] = [{"type": "text", "text": clean_prompt}]
        if first_frame:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": first_frame},
                    "role": "first_frame",
                }
            )
        if last_frame:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": last_frame},
                    "role": "last_frame",
                }
            )
        # 参考素材的数量上限：渠道目录申报优先，没申报才用上游契约常量（见
        # :meth:`_minimax_v2_reference_limits`）。三道闸统一走
        # :func:`_enforce_reference_caps`：申报 0 的那一类递什么都是整单被拒、单类超限与
        # 混投超总闸一律拒单摊数字，**不静默截断**（那是丢素材不是拒单，req44 同一口径）。
        limits, sources = self._minimax_v2_reference_limits()
        _enforce_reference_caps(
            self._model_label(self.model),
            supplied={
                "image": reference_images,
                "video": reference_videos,
                "audio": reference_audios,
            },
            limits={role: limits[role] for role in _REFERENCE_ROLE_LABELS},
            sources={role: sources[role] for role in _REFERENCE_ROLE_LABELS},
            total=limits["total"],
            total_source=(
                "渠道目录 maxReferenceInputs"
                if sources["total"] == "渠道目录"
                else "上游 MiniMax 契约（Mixed input 合计）"
            ),
        )
        for url in reference_images:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": url},
                    "role": "reference_image",
                }
            )
        for url in reference_videos:
            content.append(
                {
                    "type": "video_url",
                    "video_url": {"url": url},
                    "role": "reference_video",
                }
            )
        for url in reference_audios:
            content.append(
                {
                    "type": "audio_url",
                    "audio_url": {"url": url},
                    "role": "reference_audio",
                }
            )
        return {
            "model": self.model,
            "content": content,
            "resolution": self._minimax_v2_resolution(resolution),
            "duration": self._minimax_v2_duration(duration),
            "ratio": self._minimax_v2_ratio(
                ratio,
                frame_roles=frame_roles,
                has_media=frame_roles or reference_roles,
            ),
        }

    @staticmethod
    def _minimax_v2_task_view(polled: dict) -> dict:
        """V2 任务体 → 本文件通用的扁平任务形状（``status`` / ``result_url`` / ``error``）。

        只做形态翻译：上游把产物放在 ``task.content.url``、失败原因放在
        ``task.error.code`` + ``message``，而轮询后的成功/失败分支（以及下载、记账、
        调用日志）都按网关那套扁平字段绑定。``error`` 必须带上游原话整条透出去，
        不然「敏感内容不过」这种诊断会被压成一句没有信息量的失败。
        ``queued`` / ``running`` 原样保留（落到轮询循环的「继续等」分支）。
        """

        source = polled.get("task") if isinstance(polled.get("task"), dict) else polled
        if not isinstance(source, dict):
            return {}
        view: dict[str, Any] = {
            "status": str(source.get("status") or "").strip().lower()
        }
        content = source.get("content")
        if isinstance(content, dict):
            for key, target in (("url", "result_url"), ("prompt", "expanded_prompt")):
                value = str(content.get(key) or "").strip()
                if value:
                    view[target] = value
        error = source.get("error")
        if isinstance(error, dict) and (error.get("code") or error.get("message")):
            code = str(error.get("code") or "").strip() or "未知"
            message = str(error.get("message") or "").strip() or "上游未给出 message"
            view["error"] = f"MiniMax 任务失败（code={code}）：{message}"
        elif error:
            view["error"] = str(error)
        for key in ("id", "model", "resolution", "duration", "ratio"):
            if source.get(key) is not None:
                view[key] = source[key]
        return view

    def _minimax_v2_channel(
        self, api_root: str, headers: dict[str, str] | None = None
    ) -> Any:
        """按**这次真正要用的凭据**组一个渠道对象（素材上传回落要用）。

        组织任务的 Key 来自信封解析、不是平台设置，所以这里从请求头取 Key，绝不
        回头去读设置库里的平台 Key —— 那等于把组织数据塞进别人的账号。
        """

        from novelvideo.freezone import minimax_api

        auth = str(
            (headers or {}).get("Authorization")
            or self.headers.get("Authorization")
            or ""
        )
        api_key = auth[7:].strip() if auth[:7].lower() == "bearer " else auth.strip()
        if not api_key:
            raise ValueError(
                "MiniMax 渠道没有可用 API Key：请到设置 → 模型与渠道，在对应的 "
                "MiniMax 渠道卡里填写该站点的 API Key 并保存（海外站与国内站不通用）"
            )
        return minimax_api.MiniMaxChannel(
            provider=self.channel_provider,
            api_root=str(api_root or "").rstrip("/"),
            api_key=api_key,
            base_url=str(self.base_url or api_root or ""),
            settings={},
        )

    async def _minimax_v2_media_url(
        self,
        value: str,
        *,
        kind: str,
        label: str,
        channel: Any,
        log: Callable[[str], None],
    ) -> str:
        """本地素材 → 上游能读的地址：先复用既有媒体中转公网直链，再回落素材库上传。

        两条路都不通就报详细错（两边的失败原因都带上）：静默丢掉素材会出一条与用户
        输入无关的片子，比直接失败更糟。公网直链 / ``mm_file://`` / data URI 原样透传，
        不做一次无意义的下载中转。
        """

        from novelvideo.freezone import minimax_api

        clean = str(value or "").strip()
        if not clean:
            return ""
        if clean.startswith(("http://", "https://", "mm_file://", "data:")):
            return clean
        relay_error = ""
        try:
            if kind == "image":
                url = await self._relay_frame_input(clean)
            else:
                url = await self._relay_media_input(
                    clean,
                    default_ext="mp4" if kind == "video" else "mp3",
                    resource_type="video",
                )
            if str(url or "").strip():
                log(
                    lmsg(
                        "tasks.log.video.relayUploaded",
                        f"{label}已上传到媒体中转",
                        label=label,
                    )
                )
                return str(url).strip()
            relay_error = "媒体中转没有返回公网地址"
        except Exception as exc:  # noqa: BLE001 - 回落素材库，原因要一并报出
            relay_error = f"{type(exc).__name__}: {exc}"
        try:
            file_id = await minimax_api.upload_file(channel, Path(clean))
        except Exception as exc:  # noqa: BLE001 - 两条通道都失败才报，信息要齐
            raise ValueError(
                f"{label}既拿不到公网直链、也传不进 MiniMax 素材库："
                f"媒体中转（{relay_error or '未尝试'}）；"
                f"素材上传（{type(exc).__name__}: {exc}）"
            ) from exc
        log(
            lmsg(
                "tasks.log.video.minimaxFileUploaded",
                f"{label}已上传到 MiniMax 素材库",
                label=label,
            )
        )
        return minimax_api.file_reference(file_id)

    async def _collect_minimax_v2_media(
        self,
        *,
        mode: str,
        image_path: str,
        last_frame_path: str | None,
        references: object,
        channel: Any,
        log: Callable[[str], None],
    ) -> dict[str, Any]:
        """按提交模式把素材分进 V2 的角色槽（帧槽与参考槽互斥，建单体再拦一次）。

        模式口径与网关链路一致（``api/schemas.py`` 的执行模式名）；视频编辑 / 延长
        在 V2 没有独立绑定，按上游唯一能吃源视频的 ``reference_video`` 递交 —— 这
        就是 H3 的多模态编辑路径，不假装它还有一条编辑专用协议。
        """

        normalized = {
            "textToVideo": "text_to_video",
            "imageToVideo": "image_reference",
            "imageReference": "image_reference",
            "firstFrame": "first_frame",
            "firstLastFrame": "first_last_frame",
            "allReference": "all_reference",
            "videoEdit": "video_edit",
            "videoExtend": "video_extend",
        }.get(str(mode or "").strip(), str(mode or "").strip())
        if normalized == "text_to_video":
            # 文生视频不递任何素材：递了也没人接，只会让画幅判定跟着漂移。
            return {}

        items: list[tuple[str, str, str]] = []
        for ref in references if isinstance(references, list) else []:
            media_type = str(getattr(ref, "type", "") or "image").strip().lower()
            path = str(getattr(ref, "path", "") or "").strip()
            role = str(getattr(ref, "role", "") or "").strip()
            if path and media_type in {"image", "video", "audio", "link"}:
                items.append((media_type, path, role))

        first_frame = str(image_path or "").strip()
        last_frame = str(last_frame_path or "").strip()
        reference_items = list(items)
        frame_mode = normalized in {"first_frame", "first_last_frame"} or (
            not normalized and bool(first_frame or last_frame)
        )
        if frame_mode:
            if not first_frame:
                # 画布有时只把首帧塞进 references：取第一张图顶上，并从参考里摘掉，
                # 否则同一张图会同时占首帧位与参考位（上游两组角色互斥）。
                first_frame = next(
                    (path for (_type, path, _role) in reference_items if _type == "image"),
                    "",
                )
                if first_frame:
                    reference_items = [
                        item for item in reference_items if item[1] != first_frame
                    ]
            if not (first_frame or last_frame):
                raise ValueError(
                    "首帧 / 首尾帧模式至少要一张关键帧图片："
                    "上游 content 里需要 first_frame 或 last_frame"
                )
        elif normalized in {
            "",
            "image_reference",
            "all_reference",
            "video_edit",
            "video_extend",
        }:
            # 单图、没有别的素材 = 按这张图生成，递首帧槽（与网关链路 req44 的
            # 「单图即图生视频」口径一致）；其余一律按参考角色递交。
            if (
                len(reference_items) == 1
                and reference_items[0][0] == "image"
                and not first_frame
                and not last_frame
            ):
                first_frame = reference_items[0][1]
                reference_items = []
        else:
            raise ValueError(
                f"MiniMax 视频直连不支持的提交模式：{normalized}"
                "（V2 只有文生 / 首尾帧 / 参考生成三条）"
            )

        media: dict[str, Any] = {}
        if first_frame:
            media["first_frame"] = await self._minimax_v2_media_url(
                first_frame,
                kind="image",
                label="首帧",
                channel=channel,
                log=log,
            )
        if last_frame:
            media["last_frame"] = await self._minimax_v2_media_url(
                last_frame,
                kind="image",
                label="尾帧",
                channel=channel,
                log=log,
            )
        buckets: dict[str, tuple[str, str]] = {
            "image": ("reference_images", "参考图片"),
            "link": ("reference_images", "参考链接"),
            "video": ("reference_videos", "参考视频"),
            "audio": ("reference_audios", "参考音频"),
        }
        for media_type, path, _role in reference_items:
            key, label = buckets[media_type]
            # link 只是「一条公网地址」的另一种来源，按图片槽递交（V2 没有 link 角色）。
            url = await self._minimax_v2_media_url(
                path,
                kind="image" if media_type == "link" else media_type,
                label=label,
                channel=channel,
                log=log,
            )
            if url:
                media.setdefault(key, []).append(url)
        return media

    def _resolve_result_url(self, result_url: str) -> str:
        """Make gateway-local result URLs reachable from this process."""
        value = str(result_url or "").strip()
        parsed = urllib.parse.urlsplit(value)
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            return value
        gateway = urllib.parse.urlsplit(self.base_url)
        if not gateway.scheme or not gateway.netloc:
            return value
        return urllib.parse.urlunsplit(
            (gateway.scheme, gateway.netloc, parsed.path, parsed.query, parsed.fragment)
        )

    @staticmethod
    def _extract_returned_last_frame_url(task: dict) -> str:
        if not isinstance(task, dict):
            return ""
        from novelvideo.generators.huimengi import extract_huimeng_result_last_frame_url

        containers: list[dict] = []
        for value in (
            task.get("metadata"),
            task.get("response"),
            task.get("result"),
            task.get("output"),
            task.get("data"),
            task,
        ):
            if not isinstance(value, dict):
                continue
            containers.append(value)
            for nested_key in ("result", "output", "data", "response", "metadata"):
                nested = value.get(nested_key)
                if isinstance(nested, dict):
                    containers.append(nested)
        seen: set[int] = set()
        for container in containers:
            ident = id(container)
            if ident in seen:
                continue
            seen.add(ident)
            found = extract_huimeng_result_last_frame_url(container)
            if found:
                return found
        return ""

    @staticmethod
    def _extract_provider_task_id(task: dict, *, fallback: str = "") -> str:
        if not isinstance(task, dict):
            return fallback
        containers = [
            task.get("metadata"),
            task.get("response"),
            task.get("result"),
            task.get("output"),
            task.get("data"),
            task,
        ]
        keys = (
            "provider_task_id",
            "huimeng_task_id",
            "upstream_task_id",
            "upstream_id",
            "task_id",
        )
        for container in containers:
            if not isinstance(container, dict):
                continue
            for key in keys:
                value = container.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return fallback

    @classmethod
    def _returned_last_frame_output_path(
        cls, output_path: str, last_frame_url: str
    ) -> Path:
        video_output_path = Path(output_path)
        suffix = ""
        if last_frame_url.startswith("data:"):
            header, _, _encoded = last_frame_url.partition(",")
            suffix = f".{cls._ext_from_data_url_header(header, default='png')}"
        else:
            parsed_url = urllib.parse.urlparse(last_frame_url)
            suffix = Path(parsed_url.path).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            suffix = ".png"
        return (
            video_output_path.parent
            / "returned_last_frames"
            / f"{video_output_path.stem}{suffix}"
        )

    def _request_egress_context(self, kwargs: dict[str, Any]):
        supplied = _video_egress_context(kwargs.get("egress_context"))
        if (
            supplied is not None
            and self.egress_context is not None
            and supplied != self.egress_context
        ):
            raise VideoEgressError("TASK_ENVELOPE_INVALID")
        return supplied or self.egress_context

    @staticmethod
    def _admission_from_egress_context(context):
        from novelvideo.ports.authz import AdmissionContext

        return AdmissionContext(
            requester_user_id=context.requester_user_id,
            billing_principal=context.billing_principal,
            credential=context.credential,
            admission_id=context.admission_id,
            root_task_id=context.root_task_id,
            admitted_at=context.admitted_at,
            membership_id=context.membership_id,
            authz_version=context.authz_version,
        )

    @staticmethod
    def _operation_spec(context, payload: dict[str, object], kwargs: dict[str, Any]):
        from novelvideo.ports.egress_operations import (
            HandleKind,
            OperationSpec,
            canonical_request_digest,
        )

        task_type = str(kwargs.get("task_type") or context.task_type).strip()
        episode = int(kwargs.get("episode") or 0)
        beat_num = int(kwargs.get("beat_num") or 0)
        scope = str(kwargs.get("scope") or "task").strip()
        digest_input = {
            "request": payload,
            "requester_user_id": context.requester_user_id,
            "organization_id": context.billing_principal.id,
            "membership_id": context.membership_id,
            "authz_version": context.authz_version,
            "root_task_id": context.root_task_id,
            "project_id": context.project_id,
            "credential_id": context.credential.credential_id,
            "credential_version": context.credential.key_version,
        }
        return OperationSpec(
            organization_id=context.billing_principal.id,
            project_id=context.project_id,
            root_task_id=context.root_task_id,
            business_task_id=(
                f"{task_type}:episode:{episode}:beat:{beat_num}:scope:{scope}:generate"
            ),
            capability="video.generate.gateway",
            credential_id=context.credential.credential_id,
            credential_version=context.credential.key_version,
            request_digest=canonical_request_digest(digest_input),
            handle_kind=HandleKind.PROVIDER_JOB,
        )

    @staticmethod
    def _media_request_identity(value: object) -> dict[str, object]:
        text = str(value or "").strip()
        if not text:
            return {"kind": "none"}
        if text.startswith(("http://", "https://", "data:")) or is_asset_reference(text):
            return {"kind": "remote", "value": text}
        path = Path(text)
        try:
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as media_file:
                while chunk := media_file.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
            return {"kind": "local", "sha256": digest.hexdigest(), "size": size}
        except OSError:
            return {"kind": "unreadable", "path": text}

    async def _resolve_organization_request(self, context):
        from novelvideo.ports import get_model_credentials

        resolved = await get_model_credentials().resolve(
            self._admission_from_egress_context(context)
        )
        if resolved.reference != context.credential:
            from novelvideo.ports.model_credentials import ModelCredentialError

            raise ModelCredentialError("ORG_CREDENTIAL_VERSION_MISMATCH")
        return resolved

    async def _revalidate_organization(self, context=None) -> None:
        context = context or self.egress_context
        if context is None or not context.is_organization:
            return
        from novelvideo.ports import get_authz_port
        from novelvideo.ports.authz import AuthzError

        authz = get_authz_port()

        async def read_current():
            return await authz.admit_model_task(
                user_id=context.requester_user_id,
                root_task_id=context.root_task_id,
            )

        try:
            current = await retry_authz_read(
                read_current,
                max_retries=_video_generator._POST_ACCEPT_AUTHZ_MAX_RETRIES,
                base_delay=_video_generator._POST_ACCEPT_AUTHZ_RETRY_BASE_SECONDS,
                cap_delay=_video_generator._POST_ACCEPT_AUTHZ_RETRY_CAP_SECONDS,
                sleep=_video_generator._POST_ACCEPT_AUTHZ_RETRY_SLEEP,
                random=_video_generator._POST_ACCEPT_AUTHZ_RETRY_RANDOM,
                call_site="video_post_accept_revalidation",
            )
        except AuthzError as exc:
            if exc.code not in {
                "ORG_CREDENTIAL_MISSING",
                "ORG_CREDENTIAL_DISABLED",
                "ORG_CREDENTIAL_VERSION_MISMATCH",
            }:
                raise

            async def read_authority():
                return await authz.snapshot(user_id=context.requester_user_id)

            snapshot = await retry_authz_read(
                read_authority,
                max_retries=_video_generator._POST_ACCEPT_AUTHZ_MAX_RETRIES,
                base_delay=_video_generator._POST_ACCEPT_AUTHZ_RETRY_BASE_SECONDS,
                cap_delay=_video_generator._POST_ACCEPT_AUTHZ_RETRY_CAP_SECONDS,
                sleep=_video_generator._POST_ACCEPT_AUTHZ_RETRY_SLEEP,
                random=_video_generator._POST_ACCEPT_AUTHZ_RETRY_RANDOM,
                call_site="video_post_accept_revalidation",
            )
            snapshot.require_active(expected_authz_version=context.authz_version)
            if (
                snapshot.requester_user_id != context.requester_user_id
                or snapshot.org_id != context.billing_principal.id
                or snapshot.membership_id != context.membership_id
                or snapshot.authz_version != context.authz_version
            ):
                raise AuthzError("ORG_AUTHZ_STALE") from None
            return
        # The provider job is already bound to the exact credential resolved before
        # submit. A later active-Key rotation is not an authorization change: keep
        # polling with the frozen request headers while still enforcing every other
        # organization, membership, and authz-version boundary below.
        if (
            current.requester_user_id != context.requester_user_id
            or current.billing_principal != context.billing_principal
            or current.membership_id != context.membership_id
            or current.authz_version != context.authz_version
        ):
            raise AuthzError("ORG_AUTHZ_STALE")

    @staticmethod
    def _running_authority_error(
        exc: AuthzError,
    ) -> AuthzError | RunningTaskAuthorityIndeterminate:
        if exc.code == "P0_GRAY_DISABLED":
            return detach_authz_error(exc)
        return RunningTaskAuthorityIndeterminate(
            failure_kind=str(getattr(exc, "failure_kind", "drift"))
        )

    @staticmethod
    async def _mark_operation_unknown(
        operation_port, claim, *, expected_version: int
    ) -> None:
        try:
            await operation_port.mark_unknown(
                operation_id=claim.operation.operation_id,
                transition_token=claim.transition_token,
                expected_version=expected_version,
            )
        except Exception:
            raise VideoEgressError("EGRESS_OPERATION_TRANSITION_FAILED") from None

    @staticmethod
    async def _mark_operation_rejected(operation_port, claim) -> None:
        try:
            await operation_port.mark_rejected_before_submit(
                operation_id=claim.operation.operation_id,
                transition_token=claim.transition_token,
                expected_version=claim.operation.version,
            )
        except Exception:
            raise VideoEgressError("EGRESS_OPERATION_TRANSITION_FAILED") from None

    async def generate(
        self,
        image_path: Optional[str],
        prompt: str,
        output_path: str,
        aspect_ratio: str = "9:16",
        duration: float = 5.0,
        poll_interval: float = 5.0,
        max_polls: int = 360,
        on_log: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[float], None]] = None,
        last_frame_path: Optional[str] = None,
        **kwargs,
    ) -> VideoGenResult:
        def log(msg: str):
            if on_log:
                on_log(msg)

        def progress(value: float):
            if on_progress:
                on_progress(value)

        project_output_dir = kwargs.get("project_output_dir")
        tracking_episode = kwargs.get("episode")
        tracking_beat_num = kwargs.get("beat_num")
        tracking_task_type = kwargs.get("task_type", "")
        tracking_cost_estimate = kwargs.get("cost_estimate")

        def record_accepted(request_id: str):
            if not project_output_dir or not request_id:
                return
            try:
                record_video_request(
                    project_output_dir=project_output_dir,
                    request_id=request_id,
                    provider="newapi",
                    model_name=self.model,
                    episode=tracking_episode,
                    beat_num=tracking_beat_num,
                    task_type=tracking_task_type,
                    duration_seconds=float(duration),
                    cost_estimate=tracking_cost_estimate,
                )
            except Exception as exc:
                log(f"记账失败(accepted): {exc}")

        def update_request_status(
            request_id: str,
            status: str,
            error_message: str | None = None,
        ):
            if not project_output_dir or not request_id:
                return
            try:
                update_video_request_status(
                    project_output_dir=project_output_dir,
                    request_id=request_id,
                    status=status,
                    error_message=error_message,
                )
            except Exception as exc:
                log(f"记账失败({status}): {exc}")

        is_seedance2_model = self._is_seedance2_model()
        seedance2_config = (
            _seedance2_config_mapping(kwargs.get("seedance2_config"))
            if is_seedance2_model
            else {}
        )
        duration = _seedance2_duration_from_config(seedance2_config, duration)
        original_duration = duration
        from novelvideo.usage.video_duration import normalize_video_duration_for_backend

        duration = normalize_video_duration_for_backend(
            f"newapi_{self.model}",
            duration,
        )
        if duration != original_duration:
            log(f"时长已调整: {original_duration:.1f}s -> {duration:.0f}s")

        ratio_text = str(aspect_ratio or "").strip().lower()
        # Seedance I2V uses ``adaptive`` to preserve the first-frame framing.
        # The old colon-only guard accidentally converted it back to 9:16.
        ratio = (
            ratio_text
            if ":" in ratio_text or ratio_text in {"auto", "adaptive"}
            else "9:16"
        )
        image_path = str(image_path or "").strip()
        requested_mode = str(kwargs.get("gen_mode") or "").strip()

        metadata: dict[str, object] = {
            "resolution": self.resolution,
            "ratio": ratio,
            "watermark": False,
            "generate_audio": bool(self.generate_audio),
        }
        payload: dict[str, object] = {
            "model": self.model,
            "prompt": prompt,
            "seconds": str(duration),
            "metadata": metadata,
        }

        egress_context = self._request_egress_context(kwargs)
        organization_request = bool(
            egress_context is not None and egress_context.is_organization
        )
        task_id: str | None = None
        provider_request_id = ""
        operation_port = None
        operation_claim = None
        operation_version: int | None = None
        operation_terminal = False
        submit_attempted = False
        provider_completed = False
        last_delivery_error = ""
        request_base_url = self.base_url
        request_headers = self.headers if not organization_request else None

        if organization_request:
            from novelvideo.ports import get_egress_operation_port

            references = [
                {
                    "type": str(getattr(ref, "type", "") or "image"),
                    "media": self._media_request_identity(getattr(ref, "path", "")),
                    "role": str(getattr(ref, "role", "") or ""),
                }
                for ref in (kwargs.get("references") or [])
            ]
            logical_request = {
                "model": self.model,
                "prompt": prompt,
                "seconds": str(duration),
                "ratio": ratio,
                "resolution": self.resolution,
                "generate_audio": bool(self.generate_audio),
                "gen_mode": requested_mode,
                "human_review": bool(kwargs.get("human_review", False)),
                "audio_setting": str(kwargs.get("audio_setting") or ""),
                "image": self._media_request_identity(image_path),
                "last_frame": self._media_request_identity(last_frame_path),
                "references": references,
                "seedance2_config": seedance2_config,
                "model_params": self.model_params,
                "request_schema": self.request_schema,
            }
            operation_port = get_egress_operation_port()
            try:
                operation_claim = await operation_port.claim(
                    spec=self._operation_spec(egress_context, logical_request, kwargs)
                )
            except Exception as exc:
                raise VideoEgressError(
                    _safe_video_error_code(exc, "EGRESS_OPERATION_TRANSITION_FAILED")
                ) from None
            if not operation_claim.won:
                raise VideoEgressError("EGRESS_OPERATION_REPLAYED")
            operation_version = operation_claim.operation.version
            try:
                resolved = await self._resolve_organization_request(egress_context)
            except Exception as exc:
                await self._mark_operation_rejected(operation_port, operation_claim)
                operation_terminal = True
                raise VideoEgressError(
                    _safe_video_error_code(exc, "ORG_CREDENTIAL_DECRYPT_FAILED")
                ) from None
            request_base_url = resolved.base_url.rstrip("/")
            request_headers = {
                "Authorization": f"Bearer {resolved.api_key}",
                "Content-Type": "application/json",
            }

        async def fail_before_submit(error: str) -> VideoGenResult:
            nonlocal operation_terminal
            if organization_request and not operation_terminal:
                await self._mark_operation_rejected(operation_port, operation_claim)
                operation_terminal = True
            safe_error = (
                "EGRESS_OPERATION_REJECTED_BEFORE_SUBMIT"
                if organization_request
                else error
            )
            return VideoGenResult(status=VideoGenStatus.FAILED, error=safe_error)

        # [aipdd fork delta req46] MiniMax 直连（V2 视频协议）的判定必须在素材装配
        # **之前**：命中就走 ``content[]`` 角色槽那一条，网关那套 metadata / payload
        # 转换（含「素材必须是公网直链」的几何约束）整个跳过 —— 它认的是自己的协议。
        from novelvideo.freezone import minimax_api

        use_minimax_v2 = self._uses_minimax_videos_v2(
            request_base_url, self.model, self.channel_provider
        )
        minimax_v2_api_root = (
            minimax_api.api_root_from_base_url(request_base_url)
            if use_minimax_v2
            else ""
        )
        minimax_v2_channel: Any = None
        minimax_v2_media: dict[str, Any] = {}
        if use_minimax_v2:
            try:
                minimax_v2_channel = self._minimax_v2_channel(
                    minimax_v2_api_root, request_headers
                )
                minimax_v2_media = await self._collect_minimax_v2_media(
                    mode=requested_mode,
                    image_path=image_path,
                    last_frame_path=last_frame_path,
                    references=kwargs.get("references"),
                    channel=minimax_v2_channel,
                    log=log,
                )
            except (ValueError, FileNotFoundError) as exc:
                # 素材递不上去就等于任务没意义，提交前拦住并把两条通道的失败原因都说清。
                return await fail_before_submit(str(exc))
        elif requested_mode:
            try:
                await self._apply_huimeng_protocol_media_inputs(
                    metadata,
                    mode=requested_mode,
                    image_path=image_path,
                    last_frame_path=last_frame_path,
                    references=kwargs.get("references"),
                    log=log,
                )
            except (ValueError, FileNotFoundError) as exc:
                return await fail_before_submit(str(exc))
            seedance2_config = _seedance2_config_mapping(kwargs.get("seedance2_config"))
            scene_optimize = str(seedance2_config.get("scene_optimize") or "").strip()
            if scene_optimize:
                metadata["scene_optimize"] = scene_optimize
            if kwargs.get("human_review"):
                metadata["human_review"] = True
            audio_setting = str(kwargs.get("audio_setting") or "").strip()
            if audio_setting:
                metadata["audio_setting"] = audio_setting
            metadata["return_last_frame"] = bool(
                seedance2_config.get("return_last_frame", False)
            )
        elif self._is_happyhorse_model():
            if len(prompt) > 2500:
                prompt = prompt[:2500]
                payload["prompt"] = prompt
                log("提示词已截断到 HappyHorse 1.0 上限 2500 字符")

            duration_int = int(math.ceil(duration))
            metadata.pop("generate_audio", None)
            metadata["ratio"] = self._happyhorse_ratio(ratio)
            metadata["resolution"] = self._happyhorse_resolution(self.resolution)
            payload["duration"] = duration_int
            payload["seconds"] = str(duration_int)

            first_frame_path = ""
            reference_image_paths: list[str] = []
            video_reference_paths: list[str] = []
            for ref in kwargs.get("references") or []:
                ref_type = str(getattr(ref, "type", "") or "image").strip().lower()
                path = str(getattr(ref, "path", "") or "").strip()
                if not path:
                    continue
                if ref_type == "video":
                    video_reference_paths.append(path)
                    continue
                if ref_type != "image":
                    continue
                role = str(getattr(ref, "role", "") or "").strip()
                if "首帧" in role and not first_frame_path:
                    first_frame_path = path
                else:
                    reference_image_paths.append(path)

            # image_path 是 run_freezone_video_gen 无条件传进来的「首张图」。仅当它
            # 还没作为参考图存在时，才把它被动提升为首帧——否则参考模式(所有图都在
            # reference_image_paths 里)会把首张图重复算一次（首帧位 + 参考位）。
            if (
                not first_frame_path
                and image_path
                and image_path not in reference_image_paths
            ):
                first_frame_path = image_path

            # HappyHorse 没有尾帧能力，且把尾帧混进 reference_images 会误触发 r2v，
            # 与首帧的 image_url 冲突（上游 INVALID_PARAMS）。这里直接忽略尾帧。
            if last_frame_path:
                log("HappyHorse 不支持尾帧，已忽略尾帧输入")

            # HappyHorse 的 image_url(i2v) 与 reference_images(r2v/视频编辑) 互斥，
            # 不能出现在同一次请求里。策略：参考优先——只要带了参考图或参考视频，就走
            # r2v/视频编辑。此时首帧不能再走 image_url，但它的画面仍是有效参考，
            # 应降级为 reference_images 的首位（保持身份优先），而不是整张丢弃，
            # 否则「2 张图」会只发出去 1 张。纯首帧（无其它参考）才走 i2v。
            if (video_reference_paths or reference_image_paths) and first_frame_path:
                log("HappyHorse 参考/视频编辑模式不支持首帧，已将首帧降级为参考图")
                reference_image_paths.insert(0, first_frame_path)
                first_frame_path = ""

            # 文档：ratio 仅 t2v/r2v 生效。首帧(i2v, image_url)与视频编辑(video_url)
            # 的画幅由输入媒体决定，不接受 ratio，误带会触发上游 INVALID_PARAMS。
            if first_frame_path or video_reference_paths:
                metadata.pop("ratio", None)

            try:
                if video_reference_paths:
                    video_url = await self._relay_media_input(
                        video_reference_paths[0],
                        default_ext="mp4",
                        resource_type="video",
                    )
                    if not video_reference_paths[0].startswith(("http://", "https://")):
                        log("视频参考已上传到媒体中转")
                    metadata["video_url"] = video_url
                    metadata["audio_setting"] = self._happyhorse_audio_setting(
                        kwargs.get("audio_setting")
                    )

                relayed_references: list[str] = []
                for path in reference_image_paths[: 5 if video_reference_paths else 9]:
                    url = await self._relay_frame_input(path)
                    if not path.startswith(("http://", "https://")):
                        log("图片参考已上传到媒体中转")
                    relayed_references.append(url)

                if first_frame_path:
                    first_frame_url = await self._relay_frame_input(first_frame_path)
                    if not first_frame_path.startswith(("http://", "https://")):
                        log("首帧已上传到媒体中转")
                    metadata["image_url"] = first_frame_url
                    payload["images"] = [first_frame_url]

                if relayed_references:
                    metadata["reference_images"] = relayed_references
            except Exception as exc:
                return await fail_before_submit(f"media relay upload failed: {exc}")

        elif self._is_grok_video_channel_model():
            metadata.pop("generate_audio", None)
            metadata.pop("watermark", None)
            duration_int = int(math.ceil(duration))
            payload["duration"] = duration_int
            payload["seconds"] = str(duration_int)
            first_frame_path = image_path
            reference_image_paths: list[str] = []
            for ref in kwargs.get("references") or []:
                ref_type = str(getattr(ref, "type", "") or "image").strip().lower()
                path = str(getattr(ref, "path", "") or "").strip()
                if not path or ref_type != "image":
                    continue
                role = str(getattr(ref, "role", "") or "").strip()
                if not first_frame_path and "首帧" in role:
                    first_frame_path = path
                    continue
                if path != first_frame_path:
                    reference_image_paths.append(path)

            try:
                if first_frame_path:
                    first_frame_url = await self._relay_frame_input(first_frame_path)
                    if not first_frame_path.startswith(("http://", "https://")):
                        log("首帧已上传到媒体中转")
                    metadata["image_url"] = first_frame_url
                    payload["images"] = [first_frame_url]

                relayed_references: list[str] = []
                for path in reference_image_paths[:7]:
                    url = await self._relay_frame_input(path)
                    if not path.startswith(("http://", "https://")):
                        log("参考图片已上传到媒体中转")
                    relayed_references.append(url)
                if relayed_references:
                    metadata["reference_images"] = relayed_references
            except Exception as exc:
                return await fail_before_submit(f"media relay upload failed: {exc}")

        elif is_seedance2_model:
            try:
                first_frame = (
                    await self._relay_frame_input(image_path) if image_path else ""
                )
                if image_path and not _is_upstream_media_reference(image_path):
                    log("首帧已上传到媒体中转")
                last_frame = (
                    await self._relay_frame_input(str(last_frame_path))
                    if last_frame_path
                    else ""
                )
                if last_frame_path and not _is_upstream_media_reference(
                    str(last_frame_path)
                ):
                    log("尾帧已上传到媒体中转")
                reference_params = await self._relay_seedance2_references(
                    kwargs.get("references") or [],
                    log=log,
                )
            except Exception as exc:
                return await fail_before_submit(f"media relay upload failed: {exc}")

            from novelvideo.seedance2_i2v.models import Seedance2I2VMode
            from novelvideo.seedance2_i2v.request import build_seedance2_huimeng_params

            has_references = any(reference_params.values())
            if last_frame:
                mode = Seedance2I2VMode.FIRST_LAST_FRAME
            elif has_references:
                mode = Seedance2I2VMode.MULTIMODAL_REFERENCE
            elif first_frame:
                mode = Seedance2I2VMode.FIRST_FRAME
            else:
                mode = Seedance2I2VMode.TEXT_TO_VIDEO

            config_resolution = str(
                seedance2_config.get("resolution") or self.resolution or "720p"
            ).strip()
            config_ratio = str(seedance2_config.get("ratio") or ratio or "9:16").strip()
            seedance2_config.update(
                {
                    "mode": mode.value,
                    "final_prompt": prompt,
                    "duration": duration,
                    "resolution": config_resolution,
                    "ratio": config_ratio,
                }
            )
            if "generate_audio" not in seedance2_config:
                seedance2_config["generate_audio"] = bool(self.generate_audio)
                seedance2_config["generate_audio_user_set"] = True
            elif "generate_audio_user_set" not in seedance2_config:
                seedance2_config["generate_audio_user_set"] = True
            if "return_last_frame" not in seedance2_config:
                seedance2_config["return_last_frame"] = False
            if "human_review" not in seedance2_config:
                seedance2_config["human_review"] = bool(
                    kwargs.get("human_review", False)
                )
                seedance2_config["human_review_user_set"] = True
            elif "human_review_user_set" not in seedance2_config:
                seedance2_config["human_review_user_set"] = True

            try:
                seedance2_params = build_seedance2_huimeng_params(
                    seedance2_config,
                    first_frame=first_frame,
                    last_frame=last_frame,
                    reference_images=reference_params.get("reference_images"),
                    reference_videos=reference_params.get("reference_videos"),
                    reference_audios=reference_params.get("reference_audios"),
                )
            except ValueError as exc:
                return await fail_before_submit(str(exc))

            payload["prompt"] = seedance2_params.pop("prompt")
            payload["seconds"] = str(seedance2_params.pop("duration"))
            metadata.update(seedance2_params)
        else:
            try:
                await self._apply_huimeng_protocol_media_inputs(
                    metadata,
                    mode=(
                        "first_last_frame"
                        if last_frame_path
                        else (
                            "first_frame"
                            if image_path
                            else (
                                "all_reference"
                                if kwargs.get("references")
                                else "text_to_video"
                            )
                        )
                    ),
                    image_path=image_path,
                    last_frame_path=last_frame_path,
                    references=kwargs.get("references"),
                    log=log,
                )
            except Exception as exc:
                return await fail_before_submit(f"media relay upload failed: {exc}")
        try:
            model_label = self._model_label(self.model)
            request_resolution = str(
                metadata.get("resolution") or self.resolution or ""
            ).strip()
            log(
                f"正在提交 DramaClawAPI 视频任务 ({model_label}, {duration}s, {request_resolution})..."
            )
            progress(0.1)
            from novelvideo.catalog.media_model_request_schema import (
                apply_media_request_schema,
                enforce_newapi_media_geometry_contract,
                enforce_newapi_video_duration_contract,
                enforce_newapi_video_mode_contract,
            )

            if not use_minimax_v2:
                # V2 的 body 由 ``_build_minimax_v2_payload`` 自己拼，这套网关契约转换
                # （含把本地素材钉成公网直链）对它既无用又误伤。
                self._canonicalize_video_payload(payload, metadata)
                payload = apply_media_request_schema(
                    payload, self.request_schema, self.model_params
                )
                payload = enforce_newapi_video_mode_contract(
                    payload,
                    mode=requested_mode,
                    fixed_duration=duration,
                )
                payload = enforce_newapi_media_geometry_contract(
                    payload, media_type="video"
                )
                payload = enforce_newapi_video_duration_contract(payload)
                await update_current_model_call_log(
                    request_payload=payload,
                )
            use_videos_api = (
                False
                if use_minimax_v2
                else self._uses_aipdd_videos_api(request_base_url, self.model)
            )
            videos_path, videos_poll_prefix = self._videos_request_paths(
                self.model, use_videos_api
            )
            if use_minimax_v2:
                # [aipdd fork delta req46] 直连建单体同样必须在 ``submit_attempted``
                # 置位**之前**：档位越界（快生成变体要 2K、时长超范围、帧与参考混递）
                # 都是本地校验，此刻一个上游请求都没发出去，操作该标 rejected。
                # 记进调用日志的是**真正发出去的 body**（延续 req42 的口径）。
                try:
                    submit_body = self._build_minimax_v2_payload(
                        prompt=prompt,
                        duration=duration,
                        resolution=request_resolution,
                        ratio=ratio,
                        media=minimax_v2_media,
                    )
                except ValueError as exc:
                    return await fail_before_submit(str(exc))
                await update_current_model_call_log(request_payload=submit_body)
                submit_attempted = True
                submitted = await self._post_json(
                    f"{minimax_v2_api_root}{minimax_api.PATH_VIDEO_GENERATION_V2}",
                    submit_body,
                    headers=request_headers if organization_request else None,
                )
            elif use_videos_api:
                # [aipdd fork delta] AIPDD 只认 OpenAI 风格 /v1/videos（NewAPI 的
                # /video/generations 在 AIPDD 上 404 资源不存在）。建单体按 AIPDD
                # 公布的通用视频契约拼装（字段与含意见 ``_build_aipdd_videos_payload``）；
                # 画幅字段名是顶层 ratio（AIPDD 对 aspect_ratio 静默忽略）。
                #
                # [aipdd fork delta req42] 建单体必须在 ``submit_attempted`` 置位**之前**
                # 拼好：参考素材不是公网直链时抛的是本地校验错误，此刻一个上游请求都没
                # 发出，操作该标 rejected；置位后再抛会让持久化操作被判成「已提交、结果
                # 不确定」，凭空留下悬挂状态。顺带把**真正发出去的 body** 记进调用日志 ——
                # 原先记的是转换前的 payload，``video_url`` / ``audio_urls`` 从来没进过
                # 日志，出了废片也无从对证。
                try:
                    submit_body = self._build_aipdd_videos_payload(
                        payload,
                        metadata,
                        prompt=prompt,
                        duration=duration,
                        resolution=request_resolution,
                        ratio=ratio,
                        mode=requested_mode,
                    )
                except ValueError as exc:
                    return await fail_before_submit(str(exc))
                await update_current_model_call_log(request_payload=submit_body)
                submit_attempted = True
                submitted = await self._post_json(
                    f"{request_base_url}/{videos_path}",
                    submit_body,
                    headers=request_headers if organization_request else None,
                )
            elif organization_request:
                submit_attempted = True
                submitted = await self._post_json(
                    f"{request_base_url}/video/generations",
                    payload,
                    headers=request_headers,
                )
            else:
                submit_attempted = True
                submitted = await self._post_json(
                    f"{request_base_url}/video/generations", payload
                )
            await update_current_model_call_log(
                response_payload=submitted,
            )
            task_id = self._task_id_from_submit_response(submitted)
            provider_request_id = str(submitted.get("_newapi_request_id") or "").strip()
            if not task_id:
                if organization_request:
                    await self._mark_operation_unknown(
                        operation_port,
                        operation_claim,
                        expected_version=operation_version,
                    )
                    operation_terminal = True
                return VideoGenResult(
                    status=VideoGenStatus.FAILED,
                    error=(
                        "EGRESS_OPERATION_UNKNOWN"
                        if organization_request
                        else f"No task_id in DramaClawAPI response: {submitted}"
                    ),
                )
            if organization_request:
                accepted = await operation_port.mark_accepted(
                    operation_id=operation_claim.operation.operation_id,
                    transition_token=operation_claim.transition_token,
                    expected_version=operation_version,
                    provider_job_id=task_id,
                    requester_user_id=egress_context.requester_user_id,
                    membership_id=egress_context.membership_id,
                    authz_version=egress_context.authz_version,
                )
                operation_version = accepted.version
            record_accepted(task_id)
            log(f"任务已提交: {task_id}")
            progress(0.2)

            poll_url = (
                f"{minimax_v2_api_root}{minimax_api.PATH_VIDEO_QUERY_V2}/{task_id}"
                if use_minimax_v2
                else f"{request_base_url}{videos_poll_prefix}{task_id}"
            )
            for poll_count in range(max_polls):
                if organization_request:
                    running_authority_error = None
                    try:
                        await self._revalidate_organization(egress_context)
                    except AuthzError as exc:
                        await self._mark_operation_unknown(
                            operation_port,
                            operation_claim,
                            expected_version=operation_version,
                        )
                        operation_terminal = True
                        running_authority_error = self._running_authority_error(exc)
                    except Exception as exc:
                        await self._mark_operation_unknown(
                            operation_port,
                            operation_claim,
                            expected_version=operation_version,
                        )
                        operation_terminal = True
                        return VideoGenResult(
                            status=VideoGenStatus.FAILED,
                            error=_safe_video_error_code(exc, "ORG_AUTHZ_STALE"),
                            task_id=task_id,
                        )
                    if running_authority_error is not None:
                        raise running_authority_error from None
                    polled = await self._get_json(poll_url, headers=request_headers)
                else:
                    polled = await self._get_json(poll_url)
                task = (
                    self._minimax_v2_task_view(polled)
                    if use_minimax_v2
                    else self._task_response_data(polled)
                )
                status = str(task.get("status") or "").lower()
                progress(0.2 + (poll_count / max(max_polls, 1)) * 0.7)

                if status in {"completed", "succeeded", "success", "done"}:
                    await update_current_model_call_log(
                        response_payload=polled,
                    )
                    provider_completed = True
                    await _video_generator._record_video_model_call(
                        model=self.model,
                        provider_request_id=provider_request_id,
                        provider_task_id=task_id,
                    )

                    advertised_archive = self._archive_state(task)
                    archive_delivery = _video_generator.get_video_result_delivery()
                    archive_state = (
                        advertised_archive if archive_delivery is not None else None
                    )
                    if archive_delivery is not None and archive_state is None:
                        last_delivery_error = "VIDEO_ARCHIVE_MISSING"
                        update_request_status(
                            task_id, "delivery_failed", last_delivery_error
                        )
                        if organization_request:
                            await self._mark_operation_unknown(
                                operation_port,
                                operation_claim,
                                expected_version=operation_version,
                            )
                            operation_terminal = True
                        return VideoGenResult(
                            status=VideoGenStatus.FAILED,
                            error=last_delivery_error,
                            task_id=task_id,
                        )
                    if archive_state is not None:
                        archive_status, archive_retryable = archive_state
                        if archive_status != "success":
                            if (
                                archive_status in {"pending", "failed"}
                                and archive_retryable
                            ):
                                last_delivery_error = "VIDEO_ARCHIVE_PENDING"
                                update_request_status(
                                    task_id,
                                    "delivery_pending",
                                    last_delivery_error,
                                )
                                if poll_count % 6 == 0:
                                    log(
                                        lmsg(
                                            "tasks.log.video.waitingForArchive",
                                            "视频生成完成，正在等待网关归档...",
                                        )
                                    )
                                await asyncio.sleep(poll_interval)
                                continue
                            last_delivery_error = "VIDEO_ARCHIVE_FAILED"
                            update_request_status(
                                task_id,
                                "delivery_failed",
                                last_delivery_error,
                            )
                            if organization_request:
                                await self._mark_operation_unknown(
                                    operation_port,
                                    operation_claim,
                                    expected_version=operation_version,
                                )
                                operation_terminal = True
                            return VideoGenResult(
                                status=VideoGenStatus.FAILED,
                                error=last_delivery_error,
                                task_id=task_id,
                            )

                    progress(0.9)
                    video_url = self._resolve_result_url(self._extract_video_url(task))
                    archived_source = (
                        self._archived_video_source(task, fallback_url=video_url)
                        if archive_delivery is not None
                        else None
                    )
                    if archived_source is not None:
                        video_url = archived_source.url
                    if not video_url and archived_source is None:
                        safe_missing_result_error = (
                            "EGRESS_OPERATION_UNKNOWN"
                            if organization_request
                            else "No video url in DramaClawAPI result"
                        )
                        if organization_request:
                            await self._mark_operation_unknown(
                                operation_port,
                                operation_claim,
                                expected_version=operation_version,
                            )
                            operation_terminal = True
                        update_request_status(
                            task_id, "delivery_failed", safe_missing_result_error
                        )
                        return VideoGenResult(
                            status=VideoGenStatus.FAILED,
                            error=(
                                safe_missing_result_error
                                if organization_request
                                else f"No video url in DramaClawAPI result: {task}"
                            ),
                            task_id=task_id,
                        )
                    if organization_request:
                        running_authority_error = None
                        try:
                            await self._revalidate_organization(egress_context)
                        except AuthzError as exc:
                            await self._mark_operation_unknown(
                                operation_port,
                                operation_claim,
                                expected_version=operation_version,
                            )
                            operation_terminal = True
                            running_authority_error = self._running_authority_error(exc)
                        except Exception as exc:
                            await self._mark_operation_unknown(
                                operation_port,
                                operation_claim,
                                expected_version=operation_version,
                            )
                            operation_terminal = True
                            return VideoGenResult(
                                status=VideoGenStatus.FAILED,
                                error=_safe_video_error_code(exc, "ORG_AUTHZ_STALE"),
                                task_id=task_id,
                            )
                        if running_authority_error is not None:
                            raise running_authority_error from None
                    try:
                        if archived_source is not None:
                            log(
                                lmsg(
                                    "tasks.log.video.writingProjectStorage",
                                    "视频归档完成，正在写入项目存储...",
                                )
                            )
                            delivery = await archive_delivery.deliver(
                                source=archived_source,
                                output_path=output_path,
                            )
                        else:
                            log(
                                lmsg(
                                    "tasks.log.video.downloading",
                                    "视频生成完成，正在下载...",
                                )
                            )
                            delivery = await self._download_video(
                                video_url, output_path
                            )
                    except VideoDeliveryError as exc:
                        last_delivery_error = exc.code
                        update_request_status(
                            task_id,
                            "delivery_pending" if exc.retryable else "delivery_failed",
                            last_delivery_error,
                        )
                        if exc.retryable and poll_count + 1 < max_polls:
                            log(
                                lmsg(
                                    "tasks.log.video.retryingDelivery",
                                    "项目存储暂未就绪，正在重试交付...",
                                )
                            )
                            await asyncio.sleep(poll_interval)
                            continue
                        if organization_request:
                            await self._mark_operation_unknown(
                                operation_port,
                                operation_claim,
                                expected_version=operation_version,
                            )
                            operation_terminal = True
                        return VideoGenResult(
                            status=VideoGenStatus.FAILED,
                            error=last_delivery_error,
                            task_id=task_id,
                        )
                    provider_task_id = self._extract_provider_task_id(
                        task,
                        fallback=task_id,
                    )
                    last_frame_url = ""
                    last_frame_path = ""
                    if bool(metadata.get("return_last_frame")):
                        last_frame_url = self._extract_returned_last_frame_url(task)
                        if last_frame_url:
                            if organization_request:
                                running_authority_error = None
                                try:
                                    await self._revalidate_organization(egress_context)
                                except AuthzError as exc:
                                    await self._mark_operation_unknown(
                                        operation_port,
                                        operation_claim,
                                        expected_version=operation_version,
                                    )
                                    operation_terminal = True
                                    running_authority_error = (
                                        self._running_authority_error(exc)
                                    )
                                except Exception as exc:
                                    await self._mark_operation_unknown(
                                        operation_port,
                                        operation_claim,
                                        expected_version=operation_version,
                                    )
                                    operation_terminal = True
                                    return VideoGenResult(
                                        status=VideoGenStatus.FAILED,
                                        error=_safe_video_error_code(
                                            exc, "ORG_AUTHZ_STALE"
                                        ),
                                        task_id=task_id,
                                    )
                                if running_authority_error is not None:
                                    raise running_authority_error from None
                            last_frame_output_path = (
                                self._returned_last_frame_output_path(
                                    output_path,
                                    last_frame_url,
                                )
                            )
                            await self._download_video(
                                last_frame_url,
                                str(last_frame_output_path),
                            )
                            last_frame_path = last_frame_output_path.as_posix()
                            log("已保存 DramaClawAPI 返回尾帧")
                    progress(1.0)
                    update_request_status(task_id, "completed")
                    if organization_request:
                        if isinstance(delivery, VideoDeliveryReceipt):
                            result_sha256 = delivery.sha256
                        elif isinstance(delivery, bytes):
                            # Compatibility for injected/test download seams.
                            result_sha256 = hashlib.sha256(delivery).hexdigest()
                        else:
                            raise VideoDeliveryError(
                                "VIDEO_DELIVERY_RECEIPT_INVALID", retryable=False
                            )
                        result_ref = "video:sha256:" + result_sha256
                        completed = await operation_port.mark_completed(
                            operation_id=operation_claim.operation.operation_id,
                            transition_token=operation_claim.transition_token,
                            expected_version=operation_version,
                            result_ref=result_ref,
                        )
                        operation_version = completed.version
                        operation_terminal = True
                    return VideoGenResult(
                        status=VideoGenStatus.DONE,
                        video_url=video_url,
                        video_path=output_path,
                        last_frame_url=last_frame_url or None,
                        last_frame_path=last_frame_path or None,
                        task_id=task_id,
                        provider_task_id=provider_task_id,
                        duration_seconds=float(duration),
                    )

                if status in {
                    "failure",
                    "failed",
                    "error",
                    "canceled",
                    "cancelled",
                    "expired",
                }:
                    await update_current_model_call_log(
                        response_payload=polled,
                    )
                    error = (
                        task.get("error")
                        or task.get("fail_reason")
                        or "DramaClawAPI video task failed"
                    )
                    safe_task_error = (
                        (
                            classify_provider_video_task_error(task)
                            or provider_video_task_error_message(task)
                            or "EGRESS_OPERATION_UNKNOWN"
                        )
                        if organization_request
                        else str(error)
                    )
                    if organization_request:
                        await self._mark_operation_unknown(
                            operation_port,
                            operation_claim,
                            expected_version=operation_version,
                        )
                        operation_terminal = True
                    update_request_status(task_id, "failed", safe_task_error)
                    return VideoGenResult(
                        status=VideoGenStatus.FAILED,
                        error=safe_task_error,
                        task_id=task_id,
                    )

                if poll_count % 6 == 0:
                    log(
                        f"DramaClawAPI task {task_id} status: "
                        f"{status or 'queued'} ({poll_count}/{max_polls})"
                    )
                await asyncio.sleep(poll_interval)

            timeout_error = (
                last_delivery_error or "Timeout waiting for DramaClawAPI video task"
            )
            update_request_status(
                task_id,
                "delivery_pending" if provider_completed else "failed",
                timeout_error,
            )
            await update_current_model_call_log(
                response_payload={"status": "timeout", "task_id": task_id},
                error_message="video task polling timeout",
            )
            if organization_request:
                await self._mark_operation_unknown(
                    operation_port,
                    operation_claim,
                    expected_version=operation_version,
                )
                operation_terminal = True
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=timeout_error,
                task_id=task_id,
            )
        except RunningTaskAuthorityIndeterminate:
            # [aipdd fork delta] WP-D: 额度体系已拆除；provider 受理状态由
            # egress operation 负责，这里不再做任何退款 / 结算判定。
            raise
        except VideoEgressError:
            raise
        except NewApiVideoError as exc:
            await update_current_model_call_log(
                response_payload={
                    "status_code": exc.status_code,
                    "request_id": exc.request_id,
                    "error_type": type(exc).__name__,
                },
                error_message=type(exc).__name__,
            )
            safe_exception_error = (
                (
                    _safe_video_error_code(exc, "")
                    or provider_video_error_message_from_text(str(exc))
                    or "EGRESS_OPERATION_UNKNOWN"
                )
                if organization_request
                else str(exc)
            )
            if (
                organization_request
                and operation_claim is not None
                and not operation_terminal
            ):
                if task_id is None and is_definite_no_cost_http_rejection(
                    exc.status_code
                ):
                    # A concrete HTTP rejection without a provider task id proves
                    # that the submit was not accepted.  ``submit_attempted`` only
                    # says that the request crossed our client boundary; it does
                    # not make an explicit 4xx response indeterminate.
                    await self._mark_operation_rejected(operation_port, operation_claim)
                elif submit_attempted:
                    await self._mark_operation_unknown(
                        operation_port,
                        operation_claim,
                        expected_version=operation_version,
                    )
                else:
                    await self._mark_operation_rejected(operation_port, operation_claim)
                operation_terminal = True
            if exc.request_id:
                log(f"DramaClawAPI request_id: {exc.request_id}")
            if task_id:
                log(f"DramaClawAPI task_id: {task_id}")
                update_request_status(task_id, "failed", safe_exception_error)
            # [aipdd fork delta] WP-D: typed 计费错误（保留的 EE 兼容信封）仍必须
            # 原样冒泡，不得被组织出网的脱敏路径伪装成上游失败。
            if find_billing_error(exc) is not None or (
                is_upstream_quota_error(exc) and not organization_request
            ):
                raise
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=safe_exception_error,
                task_id=task_id,
            )
        except Exception as exc:
            await update_current_model_call_log(
                error_message=type(exc).__name__,
            )
            safe_exception_error = (
                _safe_video_error_code(exc, "EGRESS_OPERATION_UNKNOWN")
                if organization_request
                else str(exc)
            )
            if (
                organization_request
                and operation_claim is not None
                and not operation_terminal
            ):
                if submit_attempted:
                    await self._mark_operation_unknown(
                        operation_port,
                        operation_claim,
                        expected_version=operation_version,
                    )
                else:
                    await self._mark_operation_rejected(operation_port, operation_claim)
                operation_terminal = True
            if task_id:
                update_request_status(task_id, "failed", safe_exception_error)
            # [aipdd fork delta] WP-D: 同上，typed 信封优先于上游额度文案判定。
            if find_billing_error(exc) is not None or (
                is_upstream_quota_error(exc) and not organization_request
            ):
                raise
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=safe_exception_error,
                task_id=task_id,
            )
