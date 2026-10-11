"""NovelVideo 配置模块。

独立的配置系统，不依赖 SuperScript。
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from novelvideo.official_defaults import official_newapi_base_url
from novelvideo.shared.runtime_env import is_ce_effective

# 加载环境变量（必须在任何其他导入之前）
load_dotenv()

# =============================================================================
# 模型提供商配置
# =============================================================================

PROVIDER_PRESETS = {
    "openai": {
        "base_url": None,
        "default_model": "",  # [aipdd fork delta req40] 不内置模型名
        "timeout": 120,
        "api_key_env": "OPENAI_API_KEY",
    },
    "anthropic": {
        "base_url": None,
        "default_model": "",  # [aipdd fork delta req40] 不内置模型名
        "timeout": 120,
        "api_key_env": "ANTHROPIC_API_KEY",
    },
    "gemini": {
        "base_url": None,
        "default_model": "",  # [aipdd fork delta req40] 不内置模型名
        "timeout": 300,
        "api_key_env": "GOOGLE_API_KEY",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "",  # [aipdd fork delta req40] 不内置模型名
        "timeout": 300,
        "api_key_env": "OPENROUTER_API_KEY",
    },
    "volcengine": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "",  # [aipdd fork delta req40] 不内置模型名
        "timeout": 1800,
        "api_key_env": "ARK_API_KEY",
    },
}

PROVIDER_ALIASES = {
    "doubao": "volcengine",
    "ark": "volcengine",
    "claude": "anthropic",
    "gpt": "openai",
    "google": "gemini",
    "or": "openrouter",
}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def get_pydantic_model(
    provider_override: str | None = None,
    model_name_override: str | None = None,
    *,
    default_model: str | None = None,
):
    """Return a PydanticAI model routed through the effective NewAPI gateway.

    This is the compatibility factory used by older Agent call sites. Provider
    settings still select their legacy default model when no model name is
    supplied, but they no longer select a direct-provider transport. CE reads
    credentials from settings.db; EE reads its deployment-level NewAPI env.

    Args:
        provider_override: Select the legacy provider preset used for a default
            model name. The request transport remains NewAPI.
        model_name_override: Override the model name sent to NewAPI.
        default_model: [aipdd fork delta req40] 兜底**槽位键**（``DC-*`` 内部功能位），
            不是模型名 —— 视觉调用方传 ``DEFAULT_FREEZONE_VISION_MODEL`` 这类别名，
            解析时还要校验它在用户设置里绑定的目标仍存在于渠道目录。
    """
    provider = (
        provider_override or os.environ.get("MODEL_PROVIDER", "volcengine")
    ).lower()
    provider = PROVIDER_ALIASES.get(provider, provider)

    if provider not in PROVIDER_PRESETS:
        available = list(PROVIDER_PRESETS.keys()) + list(PROVIDER_ALIASES.keys())
        raise ValueError(
            f"Unknown provider: {provider}. " f"Available: {', '.join(available)}"
        )

    preset = PROVIDER_PRESETS[provider]
    # [aipdd fork delta] 只有调用方显式指定才"钉死"模型。过去这里把 preset 的 legacy
    # 默认模型（如 volcengine 的 doubao-seed-1-6-251015）也当 override 传下去，短路了
    # 槽位兜底链 —— 网关里没有该旧模型名时整条 superpower / verification / 审阅链路都
    # 会 400「endpoint closed」。未显式指定时交给 resolve_slot：槽位绑定 → 同类已绑位
    # → 渠道目录首项。
    # [aipdd fork delta] ``MODEL_NAME`` env 已退出解析链：模型名的真源是「设置 › 模型」。
    model_name = str(model_name_override or "").strip()

    if (
        model_name
        and provider == "openrouter"
        and model_name.startswith("openrouter/")
    ):
        model_name = model_name[len("openrouter/") :]

    # [aipdd fork delta req40] 不再把 preset 的 legacy 默认模型（真实模型名）当兜底：
    # 兜底链全部落空时抛 ModelSlotNotConfigured（API 出口 400 中文），而不是带着
    # 一个渠道目录里不存在的名字去撞 404。
    return get_newapi_text_pydantic_model(
        "MODEL_NAME",
        default_model,
        model_name_override=model_name or None,
        capability="text.generate.agent",
        timeout_seconds_override=_env_float(
            "MODEL_TIMEOUT",
            float(preset.get("timeout", 120)),
        ),
    )


def _clean_env_value(name: str | None) -> str | None:
    if not name:
        return None
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def get_newapi_text_model_name(
    model_env: str,
    default_model: str = "",
) -> str:
    """[aipdd fork delta req40] 功能位 → 调用名；**没有任何硬编码兜底模型名**。

    合法来源只剩两层：用户在「设置 › 模型」里给该槽位绑定、且绑定目标**仍在渠道目录
    里**的内部别名；这一位自己不可用时按能力档自动兜底（同类已绑位 → 渠道目录首项）。
    [aipdd fork delta] per-feature env 与 ``TEXT_MODEL_FALLBACK`` /
    ``VISION_MODEL_FALLBACK`` 都已退出解析链 —— 模型名的真源是设置 + 渠道目录，不是
    进程环境。文本 / 视觉的隔离由槽位登记表的 ``capability`` 保证。

    两层都拿不到时抛 :class:`novelvideo.model_slots.ModelSlotNotConfigured`：任务在提
    交前就被拒绝（HTTP 400 中文，含槽位名与设置入口），不会再带着网关里不存在的名字
    跑到一半撞 404 / 503。第二参数由「兜底模型名」变成「兜底槽位键」，调用方传的必须
    还是 ``DC-*`` 内部位。
    """
    from novelvideo.model_slots import resolve_slot

    return resolve_slot(
        str(model_env or "").strip(),
        default_alias=str(default_model or "").strip(),
    )


def _get_newapi_text_model_profile(model_name: str):
    """Attach Gemini-compatible model profile while routing through newAPI."""
    normalized = (model_name or "").strip()
    if not normalized.startswith("gemini-") or "image" in normalized:
        return None

    from pydantic_ai.providers.openrouter import OpenRouterProvider

    return OpenRouterProvider.model_profile(f"google/{normalized}")


def _newapi_text_http_client_factory(
    *,
    timeout_seconds: float,
) -> Any:
    trust_env = _env_bool("NEWAPI_TEXT_TRUST_ENV", True)

    def factory():
        import httpx

        kwargs: dict[str, Any] = {"timeout": timeout_seconds}
        if not trust_env:
            kwargs["trust_env"] = False
        return httpx.AsyncClient(**kwargs)

    return factory


def _newapi_text_openai_provider(
    *,
    api_key: str,
    base_url: str,
    timeout_seconds: float,
):
    from openai import AsyncOpenAI
    from pydantic_ai.providers.openai import OpenAIProvider

    class _LifecycleManagedOpenAIProvider(OpenAIProvider):
        def __init__(self) -> None:
            http_client_factory = _newapi_text_http_client_factory(
                timeout_seconds=timeout_seconds,
            )
            http_client = http_client_factory()
            super().__init__(
                openai_client=AsyncOpenAI(
                    api_key=api_key,
                    base_url=base_url,
                    timeout=timeout_seconds,
                    max_retries=0,
                    http_client=http_client,
                ),
            )
            self._own_http_client = http_client
            self._http_client_factory = http_client_factory

    return _LifecycleManagedOpenAIProvider()


def _newapi_text_openai_model(
    model_name: str,
    *,
    api_key: str,
    base_url: str,
    timeout_seconds: float,
    profile: Any,
):
    from contextlib import asynccontextmanager

    from pydantic_ai.models.openai import OpenAIChatModel

    from novelvideo.model_call_diagnostics import (
        is_http_call_failure,
        model_call_failure,
    )

    def _translate(exc: BaseException) -> BaseException:
        """上游 HTTP 失败 -> 带处理指引的中文诊断；其余异常原样返回。

        [aipdd fork delta] 背景：工具页选了一个火山方舟清单里存在、但账号没开通
        的模型时，PydanticAI 抛 ``ModelHTTPError``，``str()`` 是
        ``status_code: 404, model_name: ..., body: {...}`` 的英文残渣，被任务存储
        原样落库再显示到工具页 —— 用户看不出是谁的错、也不知道该去开通还是改选。
        出网拒绝码 / 结构化输出校验失败 / 取消信号不属于 HTTP 失败，必须原样透传。
        """

        if not is_http_call_failure(exc):
            return exc
        return model_call_failure(
            exc,
            model_name=str(getattr(exc, "model_name", "") or model_name),
        )

    class _AutoClosingOpenAIChatModel(OpenAIChatModel):
        async def request(self, *args: Any, **kwargs: Any) -> Any:
            async with self:
                try:
                    return await super().request(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 - 只翻译 HTTP 类失败
                    translated = _translate(exc)
                    if translated is exc:
                        raise
                    raise translated from exc

        @asynccontextmanager
        async def request_stream(self, *args: Any, **kwargs: Any):
            async with self:
                try:
                    async with super().request_stream(*args, **kwargs) as response:
                        yield response
                except Exception as exc:  # noqa: BLE001 - 只翻译 HTTP 类失败
                    translated = _translate(exc)
                    if translated is exc:
                        raise
                    raise translated from exc

    return _AutoClosingOpenAIChatModel(
        model_name,
        provider=_newapi_text_openai_provider(
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        ),
        profile=profile,
    )


def get_newapi_text_pydantic_model(
    model_env: str,
    default_model: str = "",
    *,
    model_name_override: str | None = None,
    timeout_seconds_override: float | None = None,
    capability: str = "text.generate",
):
    """Create a PydanticAI OpenAI-compatible model that routes through newAPI.

    [aipdd fork delta] ``fallback_env`` 已删除：兜底不再读 env。视觉位由槽位登记表
    的 ``capability`` 决定（见 ``model_slots.resolve_slot``），带图调用不会再被一个
    纯文本的 env 兜底接管成 400「当前模型不支持请求所需的图片能力」。
    """
    model_name = str(model_name_override or "").strip() or get_newapi_text_model_name(
        model_env, default_model
    )
    timeout_seconds = (
        float(timeout_seconds_override)
        if timeout_seconds_override is not None
        else _env_float(
            f"{model_env}_TIMEOUT_SECONDS",
            _env_float("NEWAPI_TEXT_TIMEOUT_SECONDS", 300.0),
        )
    )
    profile = _get_newapi_text_model_profile(model_name)
    if not is_ce_effective():
        from novelvideo.model_gateway_runtime import (
            create_request_scoped_gateway_model,
        )

        return create_request_scoped_gateway_model(
            model_name=model_name,
            capability=capability,
            timeout_seconds=timeout_seconds,
            profile=profile,
            delegate_factory=_newapi_text_openai_model,
            platform_credential_factory=lambda: get_newapi_runtime_credentials(
                env_api_key="MODEL_API_KEY",
                env_base_url="MODEL_BASE_URL",
            ),
        )

    api_key, base_url = get_newapi_runtime_credentials(
        env_api_key="MODEL_API_KEY",
        env_base_url="MODEL_BASE_URL",
    )
    if not api_key:
        raise ValueError("API key not set. Configure DramaClawAPI credentials.")
    return _newapi_text_openai_model(
        model_name,
        api_key=api_key,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        profile=profile,
    )


def get_newapi_structured_output_model_settings() -> dict:
    """Disable reasoning for PydanticAI structured output requests.

    DramaClaw sends opaque ``DC-*`` aliases through an OpenAI-compatible
    NewAPI endpoint.  PydanticAI cannot infer thinking capabilities from those
    aliases, so its unified ``thinking=False`` setting is silently ignored.
    Use the explicit OpenAI-compatible wire contract that existing deployments
    already send when their task-level thinking setting is ``none``.
    """
    return {"openai_reasoning_effort": "none"}


def get_newapi_structured_output_litellm_kwargs() -> dict:
    """Disable reasoning for Cognee/Instructor calls routed through LiteLLM."""
    return {
        "reasoning_effort": "none",
        "allowed_openai_params": ["reasoning_effort"],
    }


def get_newapi_litellm_call_kwargs(
    model_slot: str,
    default_model: str = "",
) -> dict[str, str]:
    """组装 litellm 直连网关必须自带的三样路由参数：``model`` / ``api_base`` / ``api_key``。

    [aipdd fork delta] 以前每个直调点自己拼，踩过的坑是「只把模型名换成设置解析的
    结果，却把它裸喂 litellm」：litellm 拿第一个 ``/`` 判 provider，而网关调用名
    （``volcengine::…`` / ``DC-…``）里没有这一撇，于是每次都是稳定的 400
    ``LLM Provider NOT provided``，再被调用点的 ``except`` 咽成默认值，表现出来
    就是「任务没报错但角色 / 图谱出不来」。这里统一补上 ``openai/`` 前缀 —— 它只是
    OpenAI-compatible 的路由标记，不是模型名；真名仍由「设置 › 模型」的槽位解析，
    凭据只取网关设置，进程环境不再是任何一环。
    """

    api_key, base_url = get_newapi_runtime_credentials(
        env_api_key="MODEL_API_KEY",
        env_base_url="MODEL_BASE_URL",
    )
    call_name = str(
        get_newapi_text_model_name(model_slot, default_model) or ""
    ).strip()
    if not call_name.startswith(("openai/", "custom/")):
        call_name = f"openai/{call_name}"
    return {"model": call_name, "api_base": base_url, "api_key": api_key}


def get_superpower_pydantic_model(
    *,
    feature_provider_env: str | None = None,
    default_model: str | None = None,
):
    """Return the multimodal model used by SuperPower prompt builders.

    By default this inherits the deployment provider preset (``MODEL_PROVIDER``);
    ``SUPERPOWER_*`` env only selects that preset (timeout / transport defaults),
    it no longer selects a model name.

    [aipdd fork delta] SuperPower 提示词构建都是带图调用（草图/首尾帧）：视觉隔离不再
    靠 ``VISION_MODEL_FALLBACK``，而由 ``default_model`` 指向的视觉槽位别名 + 登记表
    的 capability 决定。``SUPERPOWER_MODEL`` / ``SUPERPOWER_MODEL_NAME`` /
    ``feature_model_env`` 这类**用 env 点模型名**的入口已删除 —— 模型名只听设置的。
    """

    provider_override = (
        _clean_env_value(feature_provider_env)
        or _clean_env_value("SUPERPOWER_PROVIDER")
        or _clean_env_value("SUPERPOWER_MODEL_PROVIDER")
    )
    return get_pydantic_model(
        provider_override=provider_override,
        default_model=default_model,
    )


def get_model_info() -> dict:
    """获取当前模型配置信息。"""
    provider = os.environ.get("MODEL_PROVIDER", "volcengine").lower()
    provider = PROVIDER_ALIASES.get(provider, provider)
    preset = PROVIDER_PRESETS.get(provider, {})

    # [aipdd fork delta] ``model`` 回显通用位的实际解析结果（设置绑定 + 渠道目录），
    # 不再读 ``MODEL_NAME`` env：env 已不参与模型名选定，回显必须与路由同一口径。
    from novelvideo.model_slots import ModelSlotNotConfigured, resolve_slot

    try:
        resolved_model = resolve_slot("MODEL_NAME")
    except ModelSlotNotConfigured:
        resolved_model = str(preset.get("default_model") or "unknown")
    return {
        "provider": provider,
        "model": resolved_model,
        "base_url": os.environ.get("MODEL_BASE_URL", preset.get("base_url")),
        "timeout": int(os.environ.get("MODEL_TIMEOUT", preset.get("timeout", 120))),
    }


# Redis 配置
# [aipdd fork delta req41] 不预置 ``redis://localhost:6379``：本 fork 的 CE inline 模式
# 不跑 Redis（compose 把 ST_REDIS_URL 强制置空），旧默认只是一个没人读的本机假设。
# 确实要用 Redis 的部署在 .env 里写自己的 REDIS_URL。
REDIS_URL = os.environ.get("REDIS_URL", "")


# =============================================================================
# 基础配置
# =============================================================================

# 数据根目录，三类子目录 (output/state/runtime) 默认基于此目录派生
DATA_ROOT = os.path.abspath(os.environ.get("NOVELVIDEO_DATA_ROOT", "."))

# 使用绝对路径，确保 task worker 也能找到正确的目录
OUTPUT_DIR = os.path.abspath(
    os.environ.get("NOVELVIDEO_OUTPUT_DIR", os.path.join(DATA_ROOT, "output"))
)

# 状态文件目录 (data.db, cognee_system/, project_config.json)
STATE_DIR = os.path.abspath(
    os.environ.get("NOVELVIDEO_STATE_DIR", os.path.join(DATA_ROOT, "state"))
)

# 运行时临时目录 (日志、staging、temp panels)
RUNTIME_DIR = os.path.abspath(
    os.environ.get("NOVELVIDEO_RUNTIME_DIR", os.path.join(DATA_ROOT, "runtime"))
)

# =============================================================================
# OSS presign 配置
# =============================================================================

OSS_ENDPOINT = os.environ.get("OSS_ENDPOINT")
OSS_PUBLIC_ENDPOINT = os.environ.get("OSS_PUBLIC_ENDPOINT")
OSS_BUCKET = os.environ.get("OSS_BUCKET")
OSS_ACCESS_KEY_ID = os.environ.get("OSS_ACCESS_KEY_ID")
OSS_ACCESS_KEY_SECRET = os.environ.get("OSS_ACCESS_KEY_SECRET")
OSS_OBJECT_PREFIX = os.environ.get("OSS_OBJECT_PREFIX", "output")
DOWNLOAD_VIA_OSS = os.environ.get("DOWNLOAD_VIA_OSS", "1") not in {
    "0",
    "false",
    "False",
    "",
}
STATIC_VIA_OSS = os.environ.get("STATIC_VIA_OSS", "1") not in {
    "0",
    "false",
    "False",
    "",
}
OSS_STATIC_REQUIRE_READY = os.environ.get("OSS_STATIC_REQUIRE_READY", "1") not in {
    "0",
    "false",
    "False",
    "",
}
OSS_STATIC_READY_PROBE_ATTEMPTS = int(
    os.environ.get("OSS_STATIC_READY_PROBE_ATTEMPTS", "3")
)
OSS_STATIC_READY_PROBE_DELAY_SECONDS = float(
    os.environ.get("OSS_STATIC_READY_PROBE_DELAY_SECONDS", "0.15")
)
OSS_PRESIGN_EXPIRES = int(os.environ.get("OSS_PRESIGN_EXPIRES", "900"))
OSS_STATIC_PRESIGN_EXPIRES = int(os.environ.get("OSS_STATIC_PRESIGN_EXPIRES", "3600"))


# =============================================================================
# IndexTTS2 配置
# =============================================================================

INDEXTTS2_TIMEOUT_SECONDS = float(os.environ.get("INDEXTTS2_TIMEOUT_SECONDS", "1800"))

NEWAPI_BASE_URL = os.environ.get("NEWAPI_BASE_URL", "")
NEWAPI_API_KEY = os.environ.get("NEWAPI_API_KEY", "")


def get_effective_newapi_gateway_config():
    """Return the selected NewAPI runtime gateway credentials."""
    from novelvideo.model_gateway_settings import get_effective_newapi_config

    return get_effective_newapi_config(
        official_base_url=official_newapi_base_url(),
        official_api_key=NEWAPI_API_KEY,
    )


def get_newapi_runtime_credentials(
    *,
    api_key_override: str | None = None,
    base_url_override: str | None = None,
    env_api_key: str = "NEWAPI_API_KEY",
    env_base_url: str = "NEWAPI_BASE_URL",
) -> tuple[str, str]:
    """Resolve NewAPI credentials from the edition's effective gateway.

    CE reads dynamic credentials from settings.db and never falls back to the
    process environment. EE's deployment-time gateway remains environment
    backed. Explicit per-call overrides remain available for isolated tools.
    """

    gateway = get_effective_newapi_gateway_config()
    environment_backed = gateway.source == "environment"
    api_key = (
        str(api_key_override or "").strip()
        or str(gateway.api_key or "").strip()
        or (
            os.environ.get(env_api_key, "").strip()
            or NEWAPI_API_KEY
            or os.environ.get("MODEL_API_KEY", "").strip()
            or os.environ.get("OPENAI_API_KEY", "").strip()
            if environment_backed
            else ""
        )
    )
    base_url = (
        str(base_url_override or "").strip().rstrip("/")
        or str(gateway.base_url or "").strip()
        or (
            os.environ.get(env_base_url, "").strip().rstrip("/")
            or str(NEWAPI_BASE_URL or "").strip().rstrip("/")
            or os.environ.get("MODEL_BASE_URL", "").strip().rstrip("/")
            or official_newapi_base_url()
            if environment_backed
            else ""
        )
    )
    return api_key, base_url


# [aipdd fork delta req40] 不再预设模型名：语音合成模型由「设置 › 模型」的音频默认
# 位决定（渠道目录里 kind==audio 的模型），这一项只是运维显式钉死的 env 覆盖。留空 =
# 未配置，调用前直接报「未绑定可用模型」，不会拿一个网关注册表里不存在的名字去撞 404。
# [aipdd fork delta] env 不再提供音频模型名：默认音频位由「设置 › 模型」决定。
INDEXTTS2_NEWAPI_MODEL = ""
INDEXTTS2_RECORD_PROVIDER = "newapi"


def get_indextts2_record_model(invoked_model: str = "") -> str:
    """[aipdd fork delta req40] 语音调用**记账**用的模型名（不影响调用本身）。

    以前是一张写死的常量表：默认名从代码里来。现在按「实际调用名 →
    「设置 › 模型」的默认音频槽位」的
    顺序解析，全落空就记空串 —— 记账标签绝不打挂已经在跑的任务。
    """

    clean = str(invoked_model or "").strip()
    if clean:
        return clean
    try:
        from novelvideo.model_slots import resolve_media_slot

        return resolve_media_slot("audio")
    except Exception:  # noqa: BLE001 - 解析不到只影响账单可读性
        return ""
# [aipdd fork delta] 拆解 LingShan-G2/NB-2 一体化模型：不再内置具体厂商模型名作为
# 默认值；未显式配置 env 时，运行时由 model_gateway_settings.
# get_effective_default_image_model() 从「默认生成模型」面板写入的 media mappings 解析。
# [aipdd fork delta] 这两个 env 已退出解析链：默认图模型只由「设置 › 模型」的媒体
# 映射决定（get_effective_default_image_model）。常量留空是为了让旧的选项表继续可导入。
NEWAPI_IMAGE_MODEL = ""
NEWAPI_NANOBANANA2_MODEL = ""
# [aipdd fork delta wave2] 「按用途钉图」的这一批 env 也退出解析链。以前设了
# ``SCENE_MASTER_IMAGE_MODEL`` / ``PROP_REF_IMAGE_MODEL`` 就会**压过**「设置 › 模型」里配的
# 默认图：那个值在设置页上看不见，渠道下架时也不会自动取消绑定 —— ``No available
# channel for model …`` 就是这么来的。现在用途只决定取主位还是副位（两张都来自面板的
# 媒体映射），模型名一律只从设置来。下面四个常量留空只为兼容旧导入与旧选项表。
SCENE_MASTER_IMAGE_PROVIDER = (
    os.environ.get("SCENE_MASTER_IMAGE_PROVIDER", "").strip().lower() or "newapi"
)
SCENE_MASTER_IMAGE_MODEL = ""
SCENE_REVERSE_MASTER_IMAGE_PROVIDER = (
    os.environ.get("SCENE_REVERSE_MASTER_IMAGE_PROVIDER", "").strip().lower()
    or "newapi"
)
SCENE_REVERSE_MASTER_IMAGE_MODEL = ""
SCENE_360_IMAGE_PROVIDER = (
    os.environ.get("SCENE_360_IMAGE_PROVIDER", "").strip().lower() or "newapi"
)
SCENE_360_IMAGE_MODEL = ""
PROP_REF_IMAGE_PROVIDER = (
    os.environ.get("PROP_REF_IMAGE_PROVIDER", "").strip().lower() or "newapi"
)
PROP_REF_IMAGE_MODEL = ""


# =============================================================================
# 火山引擎图像生成配置
# =============================================================================

VOLCENGINE_VISUAL_API_KEY = os.environ.get(
    "VOLCENGINE_VISUAL_API_KEY"
) or os.environ.get("ARK_API_KEY")
VOLCENGINE_VISUAL_ENDPOINT = os.environ.get(
    "VOLCENGINE_VISUAL_ENDPOINT", "https://ark.cn-beijing.volces.com/api/v3"
)

SEEDREAM_MODEL = os.environ.get("SEEDREAM_MODEL", "doubao-seedream-4-5-251128")
SEEDEDIT_MODEL = os.environ.get("SEEDEDIT_MODEL", "doubao-seededit-3-0-i2i-250628")

IMAGE_DEFAULT_WIDTH = int(os.environ.get("IMAGE_DEFAULT_WIDTH", "1440"))
IMAGE_DEFAULT_HEIGHT = int(os.environ.get("IMAGE_DEFAULT_HEIGHT", "2560"))
IMAGE_DEFAULT_STYLE = os.environ.get("IMAGE_DEFAULT_STYLE", "chinese_period_drama")

# 角色参考图生成模型选择：**env 已退出解析链**。以前设了它会**压过**「设置 › 模型」的
# 默认图位（而且它装的是生成器家族名 nanobanana / seedream，不是模型名）。现在只认
# get_character_image_selection()（项目/画布显式选的那张 > 面板默认图）。常量留空只为兼容旧导入。
CHARACTER_IMAGE_MODEL = ""

# 风格预设统一由 src/novelvideo/styles/presets/*.json 提供。


def get_style_preset(
    style: str = None,
    *,
    username: str | None = None,
    project: str | None = None,
    project_dir: str | None = None,
    state_dir: str | Path | None = None,
) -> dict:
    """获取视觉风格预设配置。

    Args:
        style: 风格名称，默认使用 IMAGE_DEFAULT_STYLE

    Returns:
        风格预设字典
    """
    style = style or IMAGE_DEFAULT_STYLE

    from novelvideo.services.style_service import StyleService

    config = StyleService.get_style(
        style,
        username=username,
        project=project,
        project_dir=project_dir,
        state_dir=state_dir,
    )
    if not config:
        raise KeyError(f"Style '{style}' not found")
    return config.to_legacy_dict()


# =============================================================================
# LLM 临时媒体中转（给 newAPI/视觉模型拉取本地参考图）
# =============================================================================

# [aipdd fork delta] 默认本地存储：产物本就落在本地磁盘，未显式配置云端中转时
# 不再默认 aliyun_oss（避免「未配置」告警且不会误走 OSS 分支报错）。
MEDIA_RELAY_PROVIDER = (
    os.environ.get("MEDIA_RELAY_PROVIDER", "local").strip().lower()
)
MEDIA_RELAY_TTL_SECONDS = int(os.environ.get("MEDIA_RELAY_TTL_SECONDS", "1800"))

OSS_RELAY_ENDPOINT = os.environ.get("OSS_RELAY_ENDPOINT", "oss-cn-chengdu.aliyuncs.com")
OSS_RELAY_BUCKET = os.environ.get("OSS_RELAY_BUCKET", "claymore-llm-relay")
OSS_RELAY_AK = os.environ.get("OSS_RELAY_AK", "")
OSS_RELAY_SK = os.environ.get("OSS_RELAY_SK", "")

CLOUDINARY_RELAY_CLOUD_NAME = os.environ.get("CLOUDINARY_RELAY_CLOUD_NAME", "")
CLOUDINARY_RELAY_API_KEY = os.environ.get("CLOUDINARY_RELAY_API_KEY", "")
CLOUDINARY_RELAY_API_SECRET = os.environ.get("CLOUDINARY_RELAY_API_SECRET", "")
CLOUDINARY_RELAY_FOLDER = os.environ.get("CLOUDINARY_RELAY_FOLDER", "")

# [aipdd fork delta req44] 「AIPDD 数字资产云」档：上传凭据默认复用已连接的 AIPDD
# 渠道（渠道 Base URL 的站点根 + X-API-Key），无需另填 AccessKey。下面两项只给
# 「渠道之外单独指定」的场景兜底，默认留空不写死任何站点地址。
AIPDD_CLOUD_BASE_URL = os.environ.get("AIPDD_CLOUD_BASE_URL", "").strip()
AIPDD_CLOUD_API_KEY = os.environ.get("AIPDD_CLOUD_API_KEY", "").strip()

# [aipdd fork delta] AWS S3 档（与阿里云 OSS 并行的对象存储中转）：region/bucket/AK/SK
# 为必填，endpoint_url 留空时 boto3 按 region 解析官方端点（自建 S3 兼容网关才显式填），
# session_token 仅临时凭据场景使用。默认全部留空，不写死任何区域/地址/密钥。
AWS_S3_REGION = os.environ.get("AWS_S3_REGION", "").strip()
AWS_S3_BUCKET = os.environ.get("AWS_S3_BUCKET", "").strip()
AWS_S3_ACCESS_KEY_ID = os.environ.get("AWS_S3_ACCESS_KEY_ID", "").strip()
AWS_S3_SECRET_ACCESS_KEY = os.environ.get("AWS_S3_SECRET_ACCESS_KEY", "").strip()
AWS_S3_ENDPOINT_URL = os.environ.get("AWS_S3_ENDPOINT_URL", "").strip()
AWS_S3_SESSION_TOKEN = os.environ.get("AWS_S3_SESSION_TOKEN", "").strip()


def get_style_labels() -> dict[str, str]:
    """获取风格 ID -> 显示标签的映射。

    Returns:
        {style_id: label} 字典
    """
    from novelvideo.services.style_service import StyleService

    return StyleService.get_style_labels()


def list_available_styles() -> list[dict]:
    """列出所有可用风格（预设 + 自定义）。

    Returns:
        风格列表，每项包含 {id, name, label, type}
    """
    from novelvideo.services.style_service import StyleService

    return StyleService.list_all_styles()


def get_image_config() -> dict:
    """获取图像生成配置。"""
    from novelvideo.services.style_service import StyleService

    all_styles = StyleService.list_all_styles()
    style_presets = {
        s["id"]: StyleService.get_legacy_style_preset(s["id"]) for s in all_styles
    }

    return {
        "api_key": VOLCENGINE_VISUAL_API_KEY,
        "endpoint": VOLCENGINE_VISUAL_ENDPOINT,
        "seedream_model": SEEDREAM_MODEL,
        "seededit_model": SEEDEDIT_MODEL,
        "default_width": IMAGE_DEFAULT_WIDTH,
        "default_height": IMAGE_DEFAULT_HEIGHT,
        "default_style": IMAGE_DEFAULT_STYLE,
        "style_presets": style_presets,
        "character_image_model": CHARACTER_IMAGE_MODEL,
        "character_image_selection": get_character_image_selection(),
    }


def get_character_image_model() -> str:
    """获取角色参考图生成模型类型。

    Returns:
        "nanobanana" 或 "seedream"
    """
    return CHARACTER_IMAGE_MODEL


# =============================================================================
# TTS 配置
# =============================================================================

TTS_PROVIDER = os.environ.get("TTS_PROVIDER", "cosyvoice")  # 默认 CosyVoice
EDGE_TTS_VOICE = os.environ.get("EDGE_TTS_VOICE", "zh-CN-XiaoxiaoNeural")
VOLCENGINE_TTS_ENDPOINT = os.environ.get(
    "VOLCENGINE_TTS_ENDPOINT", "https://openspeech.bytedance.com/api/v1/tts"
)

# CosyVoice 配置（阿里云 DashScope）
COSYVOICE_MODEL = os.environ.get("COSYVOICE_MODEL", "cosyvoice-v3-flash")
COSYVOICE_VOICE = os.environ.get("COSYVOICE_VOICE", "longxiaoxia_v3")
DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY")

# CosyVoice 语速倍率（范围 [0.5, 2.0]，1.0 为标准速度）
COSYVOICE_SPEECH_RATE = float(os.environ.get("COSYVOICE_SPEECH_RATE", "1.2"))

# TTS 语速估算（实测 1.0x 均值 4.45 字/秒 × 1.3x 加速 ≈ 5.8 字/秒）
TTS_CHARS_PER_SECOND = float(os.environ.get("TTS_CHARS_PER_SECOND", "5.8"))

# Dialogue beat TTS 配置（角色台词使用不同语速）
COSYVOICE_DIALOGUE_SPEECH_RATE = float(
    os.environ.get("COSYVOICE_DIALOGUE_SPEECH_RATE", "1.0")
)
TTS_DIALOGUE_CHARS_PER_SECOND = float(
    os.environ.get("TTS_DIALOGUE_CHARS_PER_SECOND", "4.45")
)


def get_tts_config() -> dict:
    """获取 TTS 配置。"""
    return {
        "provider": TTS_PROVIDER,
        # Edge TTS
        "default_voice": EDGE_TTS_VOICE,
        "rate": os.environ.get("TTS_RATE", "+0%"),
        "pitch": os.environ.get("TTS_PITCH", "+0Hz"),
        "volcengine_endpoint": VOLCENGINE_TTS_ENDPOINT,
        "volcengine_api_key": VOLCENGINE_VISUAL_API_KEY,
        # CosyVoice
        "cosyvoice_model": COSYVOICE_MODEL,
        "cosyvoice_voice": COSYVOICE_VOICE,
        "cosyvoice_speech_rate": COSYVOICE_SPEECH_RATE,
        "dashscope_api_key": DASHSCOPE_API_KEY,
    }


# =============================================================================
# Fish Audio S2 配置（情感语音合成）
# =============================================================================

FISH_AUDIO_API_KEY = os.environ.get("FISH_AUDIO_API_KEY")
FISH_AUDIO_SPEED = float(os.environ.get("FISH_AUDIO_SPEED", "1.0"))

# Fish Audio 声音预设 (8 种: age_group × gender)
FISH_VOICE_PRESETS = {
    "child_male": os.environ.get("FISH_VOICE_CHILD_MALE", ""),
    "child_female": os.environ.get("FISH_VOICE_CHILD_FEMALE", ""),
    "youth_male": os.environ.get("FISH_VOICE_YOUTH_MALE", ""),
    "youth_female": os.environ.get("FISH_VOICE_YOUTH_FEMALE", ""),
    "middle_male": os.environ.get("FISH_VOICE_MIDDLE_MALE", ""),
    "middle_female": os.environ.get("FISH_VOICE_MIDDLE_FEMALE", ""),
    "elder_male": os.environ.get("FISH_VOICE_ELDER_MALE", ""),
    "elder_female": os.environ.get("FISH_VOICE_ELDER_FEMALE", ""),
}


def get_fish_voice_id(age_group: str, gender: str) -> str:
    """根据年龄段+性别获取预设 voice ID。

    两条抽取路写出的性别写法不同：legacy 的角色补全提示词要的是「男/女」,
    structured_v1 的抽取器要的是 ``male``/``female``。只认中文会把每一个
    structured 项目的女性角色都配成男声，所以两种写法都要认。
    """
    text = str(gender or "").strip().lower()
    gender_key = "female" if ("女" in text or "female" in text) else "male"
    return FISH_VOICE_PRESETS.get(f"{age_group}_{gender_key}", "")


# =============================================================================
# 视频合成配置
# =============================================================================

FFMPEG_PATH = os.environ.get("FFMPEG_PATH", "ffmpeg")
VIDEO_FPS = int(os.environ.get("VIDEO_FPS", "30"))
VIDEO_WIDTH = int(os.environ.get("VIDEO_WIDTH", "1080"))
VIDEO_HEIGHT = int(os.environ.get("VIDEO_HEIGHT", "1920"))
VIDEO_CODEC = os.environ.get("VIDEO_CODEC", "libx264")
VIDEO_AUDIO_CODEC = os.environ.get("VIDEO_AUDIO_CODEC", "aac")
VIDEO_BITRATE = os.environ.get("VIDEO_BITRATE", "4M")

KEN_BURNS_ZOOM_RANGE = (1.0, 1.15)
KEN_BURNS_PAN_SPEED = 0.02

# =============================================================================
# AI 视频生成配置（图生视频）
# =============================================================================


def _csv_env(name: str, default: str) -> list[str]:
    values = [item.strip() for item in os.environ.get(name, default).split(",")]
    return [item for item in values if item]


# newAPI 视频网关。VIDEO_BACKEND 使用 newapi_<model> 时会通过 NEWAPI_BASE_URL 调用。
# [aipdd fork delta req40] CE（自有渠道）下这份随包清单**不再参与候选**：可选视频模型
# = 渠道目录（kind==video）+「设置 › 模型」里勾上的视频媒体模型（见
# newapi_video_backend_options）。这里只保留 EE 官方网关的随包清单与 env 覆盖能力。
NEWAPI_VIDEO_MODELS = _csv_env(
    "NEWAPI_VIDEO_MODELS",
    "seedance-1.0-pro-fast,seedance-1.5-pro,seedance-2.0,seedance-2.0-fast,seedance-2.0-value,seedance-2.0-fast-value,seedance-2.0-mini,seedance-2.5",
)
# [aipdd fork delta] ``DEFAULT_VIDEO_MODEL`` / ``NEWAPI_VIDEO_MODEL`` env 已退出解析链：
# 默认视频模型只认「设置 › 模型」的视频媒体映射（见
# model_gateway_settings.get_effective_default_video_model）。两个常量留空只为让旧引用
# 继续可导入，真正的解析走 get_newapi_video_model()。
DEFAULT_VIDEO_MODEL = ""
NEWAPI_VIDEO_MODEL = ""


def get_newapi_video_model() -> str:
    """默认视频模型名（只读设置）；没配就回空串，由调用点决定是「未绑定」还是报错。

    不进提交前拒绝那一层：这个函数多处被用作状态回显与候选补全，拒绝发生在真正提
    交任务时（seedance2 / video_generator 的调用链）。
    """

    try:
        from novelvideo.model_gateway_settings import get_effective_default_video_model

        return str(get_effective_default_video_model() or "").strip()
    except Exception:  # noqa: BLE001 - 设置读不了就是未配置，不能抛新异常
        return ""


NEWAPI_VIDEO_RESOLUTION = os.environ.get("NEWAPI_VIDEO_RESOLUTION", "720p")
# 「哪些视频模型能带音轨」是按**已选中的模型名**派生的运营参数（不是可调用兜底名）：
# 目录当前不承载该能力，渠道换了名字就对不上号，因此仍走 env 覆盖。
NEWAPI_VIDEO_AUDIO_MODELS = _csv_env(
    "NEWAPI_VIDEO_AUDIO_MODELS",
    "seedance-1.5-pro,seedance-2.0,seedance-2.0-fast,seedance-2.0-value,seedance-2.0-fast-value,seedance-2.0-mini,seedance-2.5",
)
# 同上：时长边界是按已选中模型名派生的 UI 参数，未命中时用各后端的通用默认区间。
NEWAPI_VIDEO_DURATION_BOUNDS = os.environ.get(
    "NEWAPI_VIDEO_DURATION_BOUNDS",
    "seedance-1.0-pro-fast:2-12,seedance-1.5-pro:4-12,seedance-2.0:4-15,seedance-2.0-fast:4-15,seedance-2.0-value:4-15,seedance-2.0-fast-value:4-15,seedance-2.0-mini:4-15,seedance-2.5:4-30",
).strip()

# 视频生成后端: newapi_<模型>（模型名来自渠道目录）、comfyui、seedance_fast、
# seedance_pro、seedance_pro_silent、grok_720。没配默认视频模型时留 "newapi"，
# 由生成器按「设置 › 模型」的默认视频位解析具体模型（解析不到 = 提交前拒绝）。
# [aipdd fork delta] 不再从 ``DEFAULT_VIDEO_MODEL`` env 反推后端名：env 不参与模型名选定。
VIDEO_BACKEND = os.environ.get("VIDEO_BACKEND", "newapi").strip()

# Seedance 模型（火山方舟）
SEEDANCE_FAST_MODEL = os.environ.get(
    "SEEDANCE_FAST_MODEL", "doubao-seedance-1-0-pro-fast-251015"
)
SEEDANCE_PRO_MODEL = os.environ.get(
    "SEEDANCE_PRO_MODEL", "doubao-seedance-1-5-pro-251215"
)

# HuiMeng 视频聚合 API
HUIMENGI_BASE_URL = os.environ.get("HUIMENGI_BASE_URL", "https://api.huimengi.com")
HUIMENGI_VIDEO_RESOLUTION = os.environ.get("HUIMENGI_VIDEO_RESOLUTION", "720p")
HUIMENGI_VIDEO_GENERATE_AUDIO = os.environ.get(
    "HUIMENGI_VIDEO_GENERATE_AUDIO", "false"
).lower() in ("true", "1", "yes")

# ComfyUI 本地视频生成服务
# [aipdd fork delta req41] 不预置 ``http://localhost:9527``：ComfyUI 是部署方自建的服务
# （不同于 AIPDD / 火山引擎那种「渠道定了端点就定了」的公共厂商地址），地址、主机、端口
# 都随部署变。真源在「设置」里的渠道配置；需要时用 COMFYUI_VIDEO_URL 显式指定。
COMFYUI_VIDEO_URL = os.environ.get("COMFYUI_VIDEO_URL", "")

# ComfyUI 工作流类型: gguf (低显存，~8GB) 或 fp8 (高质量，~16GB)
# - gguf: 使用 GGUF 量化模型，适合显存较小的 GPU
# - fp8: 使用 fp8 精度模型，质量更好，支持 FLF (首尾帧) 模式
COMFYUI_WORKFLOW = os.environ.get("COMFYUI_WORKFLOW", "gguf")

# ComfyUI 是否使用 SSL（HTTPS/WSS），云服务器通常需要开启
COMFYUI_USE_SSL = os.environ.get("COMFYUI_USE_SSL", "false").lower() in (
    "true",
    "1",
    "yes",
)

# 默认视频分辨率（竖屏）
VIDEO_RESOLUTION = os.environ.get("VIDEO_RESOLUTION", "720x1280")

# 分辨率预设
VIDEO_RESOLUTION_PRESETS = {
    "720x1280": {"width": 720, "height": 1280, "label": "720p 竖屏"},
    "1080x1920": {"width": 1080, "height": 1920, "label": "1080p 竖屏"},
}


def get_video_generation_config() -> dict:
    """获取 AI 视频生成（图生视频）配置。"""
    resolution = VIDEO_RESOLUTION_PRESETS.get(
        VIDEO_RESOLUTION, VIDEO_RESOLUTION_PRESETS["720x1280"]
    )
    return {
        "backend": VIDEO_BACKEND,
        "huimengi_base_url": HUIMENGI_BASE_URL,
        "huimengi_video_resolution": HUIMENGI_VIDEO_RESOLUTION,
        "newapi_base_url": NEWAPI_BASE_URL,
        "newapi_video_models": list(NEWAPI_VIDEO_MODELS),
        "newapi_video_model": get_newapi_video_model(),
        "newapi_video_resolution": NEWAPI_VIDEO_RESOLUTION,
        "comfyui_url": COMFYUI_VIDEO_URL,
        "comfyui_workflow": COMFYUI_WORKFLOW,
        "comfyui_use_ssl": COMFYUI_USE_SSL,
        "resolution": VIDEO_RESOLUTION,
        "width": resolution["width"],
        "height": resolution["height"],
        "resolution_presets": VIDEO_RESOLUTION_PRESETS,
    }


def get_video_config() -> dict:
    """获取视频配置。"""
    return {
        "ffmpeg_path": FFMPEG_PATH,
        "fps": VIDEO_FPS,
        "width": VIDEO_WIDTH,
        "height": VIDEO_HEIGHT,
        "codec": VIDEO_CODEC,
        "audio_codec": VIDEO_AUDIO_CODEC,
        "bitrate": VIDEO_BITRATE,
        "ken_burns_zoom_range": KEN_BURNS_ZOOM_RANGE,
        "ken_burns_pan_speed": KEN_BURNS_PAN_SPEED,
    }


# =============================================================================
# 图像生成配置（Google / OpenRouter / OpenAI / HuiMeng）
# =============================================================================

GOOGLE_AI_API_KEY = os.environ.get("GOOGLE_AI_API_KEY")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
HUIMENGI_API_KEY = os.environ.get("HUIMENGI_API_KEY")
OPENROUTER_GPT_IMAGE2_MODEL = os.environ.get(
    "OPENROUTER_GPT_IMAGE2_MODEL", "openai/gpt-5.4-image-2"
)
OPENROUTER_NANOBANANA2_MODEL = os.environ.get(
    "OPENROUTER_NANOBANANA2_MODEL", "google/gemini-3.1-flash-image-preview"
)

# 图像生成 Provider: "google" / "openrouter" / "openai" / "huimeng"
# OpenRouter 价格: $0.002/图 (2K) vs Google 官方 $0.134/图 (2K)
_NANOBANANA_PROVIDER_EXPLICIT = "NANOBANANA_PROVIDER" in os.environ
NANOBANANA_PROVIDER = os.environ.get("NANOBANANA_PROVIDER", "openrouter")


NANOBANANA_MODEL = os.environ.get("NANOBANANA_MODEL", "gemini-3.1-flash-image-preview")
OPENAI_IMAGE_MODEL = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-2")
HUIMENG_IMAGE_MODEL = os.environ.get("HUIMENG_IMAGE_MODEL", "image-2")
HUIMENG_IMAGE_OFFICIAL_MODEL = os.environ.get(
    "HUIMENG_IMAGE_OFFICIAL_MODEL", "image-2-official"
)
HUIMENG_NANOBANANA2_MODEL = os.environ.get("HUIMENG_NANOBANANA2_MODEL", "nb-2")
SCENE_360_PROVIDER = os.environ.get("SCENE_360_PROVIDER") or NANOBANANA_PROVIDER
# [aipdd fork delta wave2] 这两个只是「360 图 / 场景资产图」用途上的 env 抢位：设了就会
# 盖过「设置 › 模型」。现在它们退回成各自厂商适配层的别名（走直连时才用得到），网关
# 路径的模型名一律只从面板的媒体映射来。
SCENE_360_HUIMENG_MODEL = HUIMENG_IMAGE_MODEL
SCENE_ASSET_PROVIDER = os.environ.get("SCENE_ASSET_PROVIDER") or NANOBANANA_PROVIDER
SCENE_ASSET_MODEL = ""
OPENAI_IMAGE_QUALITY = os.environ.get("OPENAI_IMAGE_QUALITY", "medium")
OPENAI_SKETCH_IMAGE_QUALITY = os.environ.get("OPENAI_SKETCH_IMAGE_QUALITY", "low")
_DEFAULT_SKETCH_SELECTION_EXPLICIT = "DEFAULT_SKETCH_IMAGE_SELECTION" in os.environ
_DEFAULT_RENDER_SELECTION_EXPLICIT = "DEFAULT_RENDER_IMAGE_SELECTION" in os.environ
DEFAULT_SKETCH_IMAGE_SELECTION = os.environ.get(
    "DEFAULT_SKETCH_IMAGE_SELECTION", "newapi_gpt_image2"
)
DEFAULT_RENDER_IMAGE_SELECTION = os.environ.get(
    "DEFAULT_RENDER_IMAGE_SELECTION", "newapi_gpt_image2"
)
CHARACTER_IMAGE_SELECTION = os.environ.get(
    "CHARACTER_IMAGE_SELECTION"
) or os.environ.get("DEFAULT_CHARACTER_IMAGE_SELECTION")

IMAGE_GENERATION_SELECTIONS: dict[str, dict[str, str]] = {
    "huimeng_gpt_image2": {
        "label": "HuiMeng GPT Image 2",
        "provider": "huimeng",
        "model": HUIMENG_IMAGE_MODEL,
    },
    "huimeng_image2_official": {
        "label": "HuiMeng Image 2 Official",
        "provider": "huimeng",
        "model": HUIMENG_IMAGE_OFFICIAL_MODEL,
    },
    "huimeng_nanobanana2": {
        "label": "HuiMeng NanoBanana 2",
        "provider": "huimeng",
        "model": HUIMENG_NANOBANANA2_MODEL,
    },
    "openai_gpt_image2": {
        "label": "OpenAI GPT Image 2",
        "provider": "openai",
        "model": OPENAI_IMAGE_MODEL,
    },
    "openrouter_gpt_image2": {
        "label": "OpenRouter GPT Image 2",
        "provider": "openrouter",
        "model": OPENROUTER_GPT_IMAGE2_MODEL,
    },
    "openrouter_nanobanana2": {
        "label": "OpenRouter NanoBanana 2",
        "provider": "openrouter",
        "model": OPENROUTER_NANOBANANA2_MODEL,
    },
    "newapi_gpt_image2": {
        "label": "NewAPI Image",
        "provider": "newapi",
        "model": NEWAPI_IMAGE_MODEL,
    },
    "newapi_nanobanana2": {
        "label": "NewAPI Nano Banana 2",
        "provider": "newapi",
        "model": NEWAPI_NANOBANANA2_MODEL,
    },
}

VISIBLE_IMAGE_GENERATION_SELECTION_KEYS = (
    "newapi_gpt_image2",
    "newapi_nanobanana2",
)


def _dynamic_image_options_from_media_models() -> dict[str, str]:
    """[aipdd fork delta] 从「默认生成模型」面板配置的 mediaModels 动态生成图源下拉选项。

    返回 {model_name: "Provider · model_name"} 的映射。若 mediaModels 为空则回退到旧
    VISIBLE_IMAGE_GENERATION_SELECTION_KEYS 硬编码选项（向后兼容）。
    """
    try:
        from novelvideo.model_gateway_settings import get_newapi_media_model_mappings

        mappings = get_newapi_media_model_mappings()
    except Exception:
        mappings = {}
    options: dict[str, str] = {}
    for name, cfg in mappings.items():
        if not isinstance(cfg, dict):
            continue
        if str(cfg.get("mediaType") or "").strip().lower() != "image":
            continue
        if cfg.get("enabled", True) is False:
            continue
        provider = str(cfg.get("provider") or "").strip()
        label = f"{provider} · {name}" if provider else name
        options[str(name).strip()] = label
    return options


def resolve_image_selection(selection: str) -> dict[str, str]:
    """[aipdd fork delta] 解析图源选择值，统一返回 {model, provider, label}。

    优先查找 IMAGE_GENERATION_SELECTIONS（兼容旧 key）；未命中时尝试从 mediaModels
    查找该名称作为 image 模型；若两者都不匹配，返回默认图像模型配置。
    """
    clean = str(selection or "").strip()
    # 1) Legacy hardcoded key
    if clean in IMAGE_GENERATION_SELECTIONS:
        return IMAGE_GENERATION_SELECTIONS[clean]
    # 2) Dynamic model from mediaModels
    if clean:
        try:
            from novelvideo.model_gateway_settings import (
                get_newapi_media_model_mappings,
            )

            mappings = get_newapi_media_model_mappings()
            cfg = mappings.get(clean)
            if isinstance(cfg, dict) and str(cfg.get("mediaType") or "").lower() == "image":
                return {
                    "model": clean,
                    "provider": str(cfg.get("provider") or "newapi"),
                    "label": clean,
                }
        except Exception:
            pass
    # 3) Fallback to effective default image
    try:
        from novelvideo.model_gateway_settings import get_effective_default_image_model

        default = get_effective_default_image_model()
        if default:
            return {"model": default, "provider": "newapi", "label": default}
    except Exception:
        pass
    # 4) Last resort: use env-based model
    return {
        "model": str(NEWAPI_IMAGE_MODEL or "").strip(),
        "provider": "newapi",
        "label": clean or NEWAPI_IMAGE_MODEL or "",
    }

LEGACY_IMAGE_GENERATION_SELECTION_ALIASES = {
    "huimeng_gpt_image2": "newapi_gpt_image2",
    "huimeng_image2_official": "newapi_gpt_image2",
    "openai_gpt_image2": "newapi_gpt_image2",
    "openrouter_gpt_image2": "newapi_gpt_image2",
    "huimeng_nanobanana2": "newapi_nanobanana2",
    "openrouter_nanobanana2": "newapi_nanobanana2",
    "nanobanana": "newapi_nanobanana2",
    "seedream": "newapi_gpt_image2",
}

# 网格生成模式配置
# 竖屏 Panel 模式（每格竖屏，适合 I2V）：
# "1x1" - 单张生成（1K 分辨率，panel 高度 1376）
# "1x3" - 横向三格（panel 高度 877，竖屏 0.78）
# "1x4" - 横向四格（panel 高度 1097，竖屏 0.58）官方推荐 3-4 panel comic
# "3x2" - 6 panels（panel 高度 1365 ✓，竖屏 0.84）
# "4x3" - 12 panels（panel 高度 1024 ✓，竖屏 0.75）最优
# "5x4" - 20 panels（panel 高度 819，竖屏 0.70）
# 正方形 Panel 模式：
# "2x2" - 紧凑四格
# "3x3" - 分批生成（更稳定）
# "4x4" - 分批生成（中等，panel 高度 1024 ✓）
# "5x5" - 批量生成，最大 25 面板
GRID_MODE = os.environ.get("GRID_MODE", "1x1")

# 网格尺寸配置表
# 格式: mode -> (rows, cols, batch_size)
MODE_CONFIG = {
    # 竖屏 Panel 模式（每格竖屏，适合 I2V）
    "1x1": (1, 1, 1),
    "1x2": (1, 2, 2),  # panel 0.89 竖屏
    "1x3": (1, 3, 3),  # panel 0.78 竖屏 ✓
    "1x4": (1, 4, 4),  # panel 0.58 竖屏 ✓ 官方推荐
    "3x2": (3, 2, 6),  # panel 0.84 竖屏, 高度 1365 ✓
    "4x3": (4, 3, 12),  # panel 0.75 竖屏 ✓ 最优
    "5x4": (5, 4, 20),  # panel 0.70 竖屏 ✓
    # 正方形 Panel 模式
    "2x2": (2, 2, 4),
    "3x3": (3, 3, 9),
    "4x4": (4, 4, 16),
    "5x5": (5, 5, 25),
}

# 网格尺寸配置（根据 GRID_MODE 自动设置）
if GRID_MODE in MODE_CONFIG:
    GRID_ROWS, GRID_COLS, GRID_BATCH_SIZE = MODE_CONFIG[GRID_MODE]
else:
    # 默认使用 1x1
    GRID_ROWS, GRID_COLS, GRID_BATCH_SIZE = 1, 1, 1
GRID_TOTAL_PANELS = 25  # 动态优化时的最大面板数


def image_generation_selection_options() -> dict[str, str]:
    """[aipdd fork delta] 动态返回图源下拉选项：从 mediaModels 读取 image 模型。

    若 mediaModels 无配置，回退到旧的 VISIBLE_IMAGE_GENERATION_SELECTION_KEYS 硬编码。
    """
    dynamic = _dynamic_image_options_from_media_models()
    if dynamic:
        return dynamic
    # Fallback: legacy hardcoded options
    return {
        key: IMAGE_GENERATION_SELECTIONS[key]["label"]
        for key in VISIBLE_IMAGE_GENERATION_SELECTION_KEYS
        if key in IMAGE_GENERATION_SELECTIONS
    }


def character_image_selection_options() -> dict[str, str]:
    """Return UI labels for character/identity image generation."""
    return image_generation_selection_options()


def _is_valid_image_selection(candidate: str) -> bool:
    """Check if candidate is a valid image selection (hardcoded or dynamic from mediaModels)."""
    if not candidate:
        return False
    # Legacy hardcoded
    if candidate in VISIBLE_IMAGE_GENERATION_SELECTION_KEYS and candidate in IMAGE_GENERATION_SELECTIONS:
        return True
    alias = LEGACY_IMAGE_GENERATION_SELECTION_ALIASES.get(candidate)
    if alias and alias in VISIBLE_IMAGE_GENERATION_SELECTION_KEYS and alias in IMAGE_GENERATION_SELECTIONS:
        return True
    # Dynamic from mediaModels
    try:
        from novelvideo.model_gateway_settings import get_newapi_media_model_mappings

        mappings = get_newapi_media_model_mappings()
        cfg = mappings.get(candidate)
        if isinstance(cfg, dict) and str(cfg.get("mediaType") or "").lower() == "image":
            return True
    except Exception:
        pass
    return False


def _visible_image_generation_selection(value: str | None) -> str:
    candidate = str(value or "").strip()
    if _is_valid_image_selection(candidate):
        # Normalize legacy aliases to their canonical form
        alias = LEGACY_IMAGE_GENERATION_SELECTION_ALIASES.get(candidate)
        if alias and alias in IMAGE_GENERATION_SELECTIONS:
            return alias
        return candidate
    return ""


def _default_image_generation_selection(fallback: str | None = None) -> str:
    for candidate in (
        fallback,
        DEFAULT_SKETCH_IMAGE_SELECTION,
        DEFAULT_RENDER_IMAGE_SELECTION,
        "newapi_gpt_image2",
        *VISIBLE_IMAGE_GENERATION_SELECTION_KEYS,
    ):
        selection = _visible_image_generation_selection(candidate)
        if selection:
            return selection
    # [aipdd fork delta] 尝试动态选项（mediaModels 里的 image 模型）
    dynamic = _dynamic_image_options_from_media_models()
    if dynamic:
        return next(iter(dynamic))
    # 最后尝试 effective default
    try:
        from novelvideo.model_gateway_settings import get_effective_default_image_model

        default = get_effective_default_image_model()
        if default:
            return default
    except Exception:
        pass
    raise ValueError("No visible image generation selection configured.")


def normalize_image_generation_selection(
    value: str | None,
    *,
    fallback: str | None = None,
) -> str:
    selection = _visible_image_generation_selection(value)
    if selection:
        return selection
    return _default_image_generation_selection(fallback)


def image_generation_selection_label(
    value: str | None, *, fallback: str | None = None
) -> str:
    selection = normalize_image_generation_selection(value, fallback=fallback)
    return IMAGE_GENERATION_SELECTIONS[selection]["label"]


def get_character_image_selection() -> str:
    """Return the configured character/identity image source selection."""
    candidate = _visible_image_generation_selection(CHARACTER_IMAGE_SELECTION)
    if candidate:
        return candidate

    # [aipdd fork delta wave2] 以前这里拿 ``CHARACTER_IMAGE_MODEL`` env 当次级来源：
    # 一个设置页看不见的名字能赢过面板。现在只认显式选择，然后回默认渲染/草图位。
    return normalize_image_generation_selection(
        DEFAULT_RENDER_IMAGE_SELECTION,
        fallback=DEFAULT_SKETCH_IMAGE_SELECTION,
    )


def normalize_character_image_selection(value: str | None) -> str:
    candidate = _visible_image_generation_selection(value)
    if candidate:
        return candidate
    return get_character_image_selection()


def infer_image_generation_selection(
    provider: str | None,
    model: str | None,
    *,
    fallback: str | None = None,
) -> str:
    provider_norm = str(provider or "").strip().lower()
    model_norm = str(model or "").strip()
    for key, entry in IMAGE_GENERATION_SELECTIONS.items():
        if entry["provider"] == provider_norm and entry["model"] == model_norm:
            return key
    if provider_norm == "openrouter" and model_norm in {
        NANOBANANA_MODEL,
        f"google/{NANOBANANA_MODEL}",
    }:
        return "openrouter_nanobanana2"
    if provider_norm == "huimeng" and model_norm == "image-2":
        return "huimeng_gpt_image2"
    if provider_norm == "huimeng" and model_norm == "image-2-official":
        return "huimeng_image2_official"
    return normalize_image_generation_selection(
        fallback, fallback=DEFAULT_SKETCH_IMAGE_SELECTION
    )


def _image_provider_config(
    provider: str,
    *,
    model_override: str | None = None,
    selection_override: str | None = None,
) -> dict:
    selection_key = ""
    if selection_override:
        selection = normalize_image_generation_selection(selection_override)
        selection_key = selection
        # [aipdd fork delta] 兼容动态 mediaModels 图源（如面板把默认图模型设成
        # 网关里的 ap-image-2.0）：旧代码只查硬编码 IMAGE_GENERATION_SELECTIONS，
        # 动态名会 KeyError。未命中硬编码表时回退到 resolve_image_selection。
        entry = (
            IMAGE_GENERATION_SELECTIONS[selection]
            if selection in IMAGE_GENERATION_SELECTIONS
            else resolve_image_selection(selection)
        )
        provider = entry["provider"]
        # 动态 mediaModels 的 provider 是网关渠道名（aipdd/volcengine/…），
        # 实际调用仍走 LocalNewAPI，由下方 newapi 分支的
        # resolve_media_provider_override 选渠道 base+key。
        if provider not in {"google", "openrouter", "huimeng", "huimengi", "openai", "newapi", "comfyui"}:
            provider = "newapi"
        model = model_override or entry["model"]
    else:
        provider = (provider or "openrouter").lower()
        model = model_override or ""

    if provider == "openrouter":
        resolved_model = model or (
            f"google/{NANOBANANA_MODEL}"
            if not NANOBANANA_MODEL.startswith("google/")
            else NANOBANANA_MODEL
        )
        return {
            "provider": provider,
            "api_key": OPENROUTER_API_KEY,
            "model": resolved_model,
        }
    if provider in {"huimeng", "huimengi"}:
        return {
            "provider": "huimeng",
            "api_key": HUIMENGI_API_KEY,
            "model": model or HUIMENG_IMAGE_MODEL,
        }
    if provider == "openai":
        return {
            "provider": provider,
            "api_key": OPENAI_API_KEY,
            "model": model or OPENAI_IMAGE_MODEL,
        }
    if provider == "newapi":
        gateway = get_effective_newapi_gateway_config()
        _img_base_url = gateway.base_url
        _img_api_key = gateway.api_key
        # [aipdd fork delta #A] 多网关并存: 若选中图片模型绑定了自带 base+key 的
        # provider 渠道(如 aipdd),按该渠道路由,覆盖单一生效网关。
        try:
            from novelvideo.model_gateway_settings import (
                get_effective_default_image_model,
                get_effective_default_nanobanana_model,
                resolve_media_provider_override,
            )

            _img_default_model = get_effective_default_image_model()
            if selection_key == "newapi_nanobanana2":
                # [aipdd fork delta] 「NewAPI Nano Banana 2」这个选项不再由
                # ``NEWAPI_NANOBANANA2_MODEL`` env 点名：它对应「默认生成模型」面板里
                # 图片类的**第二张**（只配一张时与主位同名）。两个选项的差别因此落在
                # 面板顺序上，而不是落在一个设置页看不见的 env 值上。
                _img_default_model = (
                    get_effective_default_nanobanana_model() or _img_default_model
                )
            _provider_override = resolve_media_provider_override(
                model or _img_default_model, media_type="image"
            )
        except Exception:
            # 设置读不了 = 未配置：由调用点按「未绑定可用模型」拦住。这里绝不回落任何
            # 常量名（``NEWAPI_IMAGE_MODEL`` 现在只是让旧选项表可导入的空占位常量）。
            _provider_override = None
            _img_default_model = ""
        if _provider_override:
            _img_base_url, _img_api_key, _ = _provider_override
        return {
            "provider": provider,
            "api_key": _img_api_key,
            "model": model or _img_default_model,
            "base_url": _img_base_url,
        }

    if provider == "comfyui":
        # [aipdd fork delta] comfyui 渠道图片：直连渠道 ComfyUI，无 api_key/base_url
        # （服务器地址与工作流均从渠道配置解析），保持 provider=comfyui 不被压成 newapi。
        return {
            "provider": "comfyui",
            "api_key": "",
            "model": model or "",
            "base_url": "",
        }

    return {
        "provider": "google",
        "api_key": GOOGLE_AI_API_KEY,
        "model": model or NANOBANANA_MODEL,
    }


def get_grid_generation_config(
    selection_override: str | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
    image_size_override: str | None = None,
) -> dict:
    """获取网格生成配置。

    支持四种 Provider:
    - google: 直连 Google AI Studio (GOOGLE_AI_API_KEY)
    - openrouter: 通过 OpenRouter 代理 (OPENROUTER_API_KEY)，成本降低 60 倍
    - openai: 通过 OpenAI Image API (OPENAI_API_KEY)，默认 gpt-image-2
    - huimeng: 通过 HuiMeng Tasks API (HUIMENGI_API_KEY)

    环境变量:
    - NANOBANANA_PROVIDER: "google" / "openrouter" / "openai" / "huimeng"
    - GOOGLE_AI_API_KEY: Google AI Studio API Key
    - OPENROUTER_API_KEY: OpenRouter API Key
    - OPENAI_API_KEY: OpenAI API Key
    - HUIMENGI_API_KEY: HuiMeng API Key
    - OPENAI_IMAGE_MODEL: OpenAI Image API 模型，默认 gpt-image-2
    - HUIMENG_IMAGE_MODEL: HuiMeng 图片模型，默认 image-2
    - DEFAULT_SKETCH_IMAGE_SELECTION / DEFAULT_RENDER_IMAGE_SELECTION: UI 默认图片源
    """
    # [aipdd fork delta] 与 get_sketch/render_generation_config 同一判据：官方默认
    # NANOBANANA_PROVIDER=openrouter 在本 fork 的 LocalNewAPI 部署里从来没有密钥，凡是
    # 没显式指定图源/提供者/模型的调用（封面 AI 生成、道具参考图、freezone 默认
    # 生图等）都应跟随 UI 默认图源走 LocalNewAPI，而不是落到 openrouter 直连报
    # 「API key not set. Set OPENROUTER_API_KEY environment variable.」。
    if (
        selection_override is None
        and provider_override is None
        and (_DEFAULT_RENDER_SELECTION_EXPLICIT or not _NANOBANANA_PROVIDER_EXPLICIT)
    ):
        selection_override = DEFAULT_RENDER_IMAGE_SELECTION

    provider_config = _image_provider_config(
        provider_override or NANOBANANA_PROVIDER,
        model_override=model_override,
        selection_override=selection_override,
    )

    return {
        "provider": provider_config["provider"],
        "api_key": provider_config["api_key"],
        "model": provider_config["model"],
        "base_url": provider_config.get("base_url", ""),
        "openai_image_quality": OPENAI_IMAGE_QUALITY,
        "openai_sketch_image_quality": OPENAI_SKETCH_IMAGE_QUALITY,
        "huimeng_image_quality": os.environ.get("HUIMENG_IMAGE_QUALITY", "medium"),
        "image_size": image_size_override or "1K",
        "mode": GRID_MODE,
        "rows": GRID_ROWS,
        "cols": GRID_COLS,
        "batch_size": GRID_BATCH_SIZE,
        "total_panels": GRID_TOTAL_PANELS,
    }


def get_sketch_generation_config(
    selection_override: str | None = None,
    model_override: str | None = None,
) -> dict:
    """获取草图工作台网格生成配置。

    优先级:
    1. 显式 DEFAULT_SKETCH_IMAGE_SELECTION（新选择表）
    2. 显式 NANOBANANA_PROVIDER / NANOBANANA_MODEL（旧环境变量兼容）
    3. 通用 get_grid_generation_config()
    """
    selection = selection_override
    if selection is None:
        selection = (
            DEFAULT_SKETCH_IMAGE_SELECTION
            if (_DEFAULT_SKETCH_SELECTION_EXPLICIT or not _NANOBANANA_PROVIDER_EXPLICIT)
            else None
        )
    provider_override = None if selection else NANOBANANA_PROVIDER
    config = get_grid_generation_config(
        selection_override=selection,
        provider_override=provider_override,
        model_override=model_override,
    )
    config["openai_image_quality"] = OPENAI_SKETCH_IMAGE_QUALITY
    config["huimeng_image_quality"] = "low"
    config["image_size"] = "1K"
    return config


def get_render_generation_config(
    selection_override: str | None = None,
    model_override: str | None = None,
) -> dict:
    """获取首帧渲染图像配置。"""
    selection = selection_override
    if selection is None:
        selection = (
            DEFAULT_RENDER_IMAGE_SELECTION
            if (_DEFAULT_RENDER_SELECTION_EXPLICIT or not _NANOBANANA_PROVIDER_EXPLICIT)
            else None
        )
    provider_override = None if selection else NANOBANANA_PROVIDER
    return get_grid_generation_config(
        selection_override=selection,
        provider_override=provider_override,
        model_override=model_override,
    )


# =============================================================================
# 草图（Sketch）路径管理
# =============================================================================


def get_sketch_dir(project_name: str, episode: int) -> str:
    """获取整集草图存放目录。

    Args:
        project_name: 项目名称（如 admin/test1）
        episode: 集数

    Returns:
        草图目录路径，如 output/admin/test1/grids/ep001/sketch
    """
    base_dir = os.path.abspath(os.path.join(OUTPUT_DIR, project_name))
    return os.path.join(base_dir, "grids", f"ep{episode:03d}", "sketch")


def get_sketch_path(project_name: str, episode: int, sketch_index: int = 1) -> str:
    """获取整集草图路径（已弃用，保留向后兼容）。

    新模式下草图文件名为 sketch_b{start}-{end}_{rows}x{cols}.jpg，
    建议使用 list_sketch_files() 遍历草图目录。

    Args:
        project_name: 项目名称（如 admin/test1）
        episode: 集数
        sketch_index: 草图索引（1-based），默认为 1

    Returns:
        草图目录路径（新模式下返回目录而非具体文件）
    """
    return get_sketch_dir(project_name, episode)


def list_sketch_files(project_name: str, episode: int) -> list[str]:
    """列出指定集的所有草图文件。

    支持新命名约定: sketch_b{start}-{end}_{rows}x{cols}.jpg

    Args:
        project_name: 项目名称
        episode: 集数

    Returns:
        草图文件路径列表（按文件名排序）
    """
    sketch_dir = get_sketch_dir(project_name, episode)
    if not os.path.exists(sketch_dir):
        return []

    import glob

    pattern = os.path.join(sketch_dir, "sketch_b*_*x*.jpg")
    files = glob.glob(pattern)
    return sorted(files)


# =============================================================================
# 项目管理
# =============================================================================


def get_project_dir(project_name: str) -> str:
    """获取项目输出目录。"""
    return os.path.join(OUTPUT_DIR, project_name)


def ensure_project_dirs(project_name: str) -> dict[str, str]:
    """确保项目目录结构存在，返回资源目录路径。

    `project_name` 可为 `username/project` 或历史单目录格式 `project`。
    当包含用户名时，会同时确保 output/state/runtime 三类目录存在。
    """
    base_dir = os.path.abspath(get_project_dir(project_name))

    parts = project_name.split("/", 1)
    if len(parts) == 2:
        from novelvideo.utils.project_paths import ProjectPaths

        paths = ProjectPaths(parts[0], parts[1])
        paths.ensure_dirs()
        paths.bootstrap_from_legacy_output()

    dirs = {
        "base": base_dir,
        "graph": os.path.join(base_dir, "graph"),
        "assets": os.path.join(base_dir, "assets"),
        "characters": os.path.join(base_dir, "assets", "characters"),
        "scripts": os.path.join(base_dir, "scripts"),
        "images": os.path.join(base_dir, "images"),
        "frames": os.path.join(base_dir, "frames"),  # 首帧图片
        "audio": os.path.join(base_dir, "audio"),
        "videos": os.path.join(base_dir, "videos"),
    }

    for path in dirs.values():
        os.makedirs(path, exist_ok=True)

    return dirs


def ensure_project_dirs_at_paths(
    *,
    output_dir: str | os.PathLike[str],
    state_dir: str | os.PathLike[str],
    runtime_dir: str | os.PathLike[str],
) -> dict[str, str]:
    """Ensure project directories from registry paths without legacy bootstrap."""
    base_dir = os.path.abspath(os.fspath(output_dir))
    dirs = {
        "base": base_dir,
        "graph": os.path.join(base_dir, "graph"),
        "assets": os.path.join(base_dir, "assets"),
        "characters": os.path.join(base_dir, "assets", "characters"),
        "scripts": os.path.join(base_dir, "scripts"),
        "images": os.path.join(base_dir, "images"),
        "frames": os.path.join(base_dir, "frames"),
        "audio": os.path.join(base_dir, "audio"),
        "videos": os.path.join(base_dir, "videos"),
        "state": os.path.abspath(os.fspath(state_dir)),
        "runtime": os.path.abspath(os.fspath(runtime_dir)),
        "logs": os.path.join(os.path.abspath(os.fspath(runtime_dir)), "logs"),
        "staging": os.path.join(os.path.abspath(os.fspath(runtime_dir)), "staging"),
        "temp_sketch_panels": os.path.join(
            os.path.abspath(os.fspath(runtime_dir)),
            "temp_sketch_panels",
        ),
    }

    for path in dirs.values():
        os.makedirs(path, exist_ok=True)

    return dirs
