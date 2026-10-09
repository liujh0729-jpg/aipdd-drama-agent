"""[aipdd fork delta req40] 模型槽位解析：目录 + 用户设置，没有任何硬编码兜底名。

平台不该预先知道渠道里有什么模型 —— 只能从渠道 BaseURL 拉。于是「某个功能用哪个
模型」只剩三个合法来源：

1. 该功能的单独环境变量覆盖（运维显式钉死，如 ``HERMES_MODEL=...``）；
2. 全局兜底 env（``TEXT_MODEL_FALLBACK`` / ``VISION_MODEL_FALLBACK``，同一口径）；
3. 用户在「设置 › 模型」里给**槽位**选的绑定 —— 且绑定的目标必须还在渠道目录里。

三条都不成立时抛 :class:`ModelSlotNotConfigured`，由 API 出口转成 HTTP 400 中文
提示，**任务不入库、不启动**。旧实现在这里会落到 ``DEFAULT_TEXT_MODEL_BY_ENV``
的别名或调用方传进来的硬编码真名（``gpt-4o`` / ``gemini-3.5-flash`` /
``index-tts-2`` …）：网关里没有它们时要么 503，要么跑到 55% 才 404 —— 名字从渠道
下架也不会因为「代码里还写着」而重新存在。

``DC-*`` 是**内部功能位槽位键**（不是模型名）：它进网关 model_mapping 参与路由，
target 必须由用户设置且必须来自渠道目录。本模块登记表里的 ``alias`` 就是槽位键，
不携带任何 target。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


# ---- 内部功能位槽位键（``DC-*`` 命名空间：不是模型名，只是网关 model_mapping 的
# 路由键；target 必须由用户在「设置 › 模型」里从渠道目录中选）。official_defaults 的
# ``DEFAULT_*`` 常量全部指到这里，两处不再各写一份字面量。
SLOT_HERMES_LLM = "DC-hermes-LLM"
SLOT_VIDEO_PROMPT_OPTIMIZER = "DC-video-prompt-optimizer-LLM"
SLOT_SEEDANCE2_PROMPT_COMPOSER = "DC-seedance2-prompt-composer-LLM"
SLOT_VIDEO_IDENTITY_DETECTOR = "DC-video-identity-detector-LLM"
SLOT_IDENTITY_CAST_PLANNER = "DC-identity-cast-planner-LLM"
SLOT_IDENTITY_ANALYSIS_PLANNER = "DC-identity-analysis-planner-LLM"
SLOT_IDENTITY_APPEARANCE_WRITER = "DC-identity-appearance-writer-LLM"
SLOT_LITERAL_BEAT_META = "DC-literal-beat-meta-LLM"
SLOT_SCENE_BUILDER = "DC-scene-builder-LLM"
SLOT_CHARACTER_BUILDER = "DC-character-builder-LLM"
SLOT_EPISODE_SCENE_PLANNER = "DC-episode-scene-planner-LLM"
SLOT_EPISODE_PROP_PLANNER = "DC-episode-prop-planner-LLM"
SLOT_FREEZONE_TRANSLATION = "DC-freezone-translator-LLM"
SLOT_FREEZONE_TEXT_WRITER = "DC-freezone-text-writer-LLM"
SLOT_FREEZONE_STORY_SCRIPT = "DC-freezone-story-script-writer-LLM"
SLOT_FREEZONE_VISION = "DC-freezone-vision-LLM"
SLOT_STYLE_ANALYZER = "DC-style-analyzer-LLM"
SLOT_CONTENT_REWRITER = "DC-content-rewriter-LLM"
SLOT_DIRECTOR_SCREENWRITER = "DC-director-screenwriter-LLM"
SLOT_SCREENPLAY_NORMALIZER = "DC-screenplay-normalizer-LLM"
SLOT_EPISODE_SCENE_RECONCILER = "DC-episode-scene-reconciler-LLM"
SLOT_NARRATED_SCENE_ASSET_PLANNER = "DC-narrated-scene-asset-planner-LLM"
SLOT_STAGING_PROP_PLANNER = "DC-staging-prop-planner-LLM"
SLOT_COGNEE_LLM = "DC-cognee-LLM"
SLOT_COGNEE_EMBEDDING = "DC-cognee-embedding"

TEXT_FALLBACK_ENV = "TEXT_MODEL_FALLBACK"
VISION_FALLBACK_ENV = "VISION_MODEL_FALLBACK"

# 失效原因（前端 / 日志按此分流文案，不做二次猜测）。
REASON_CATALOG_EMPTY = "catalog-empty"
REASON_UNBOUND = "unbound"
REASON_DELISTED = "delisted"
REASON_NOT_CONFIGURED = "not-configured"


@dataclass(frozen=True)
class ModelSlot:
    """一个功能槽位：调用名（内部别名）+ 能力档 + 单独覆盖 env。"""

    key: str
    alias: str
    capability: str = "text"
    env: str = ""


def _text_slot(key: str, alias: str, *, capability: str = "text") -> ModelSlot:
    return ModelSlot(key=key, alias=alias, capability=capability, env=key)


# 槽位登记表（取代旧 ``official_defaults.DEFAULT_TEXT_MODEL_BY_ENV``）。值只有槽位键
# 与能力档，不携带任何 target —— 「绑哪个模型」永远是用户设置 + 渠道目录的交集。
# ``DC-*`` 常量本身是内部功能位命名空间（不是模型名），仍留在 official_defaults 里，
# 与别名真源、投影编译共用同一批字面量。
_SLOT_ALIASES: tuple[tuple[str, str, str], ...] = (
    ("HERMES_MODEL", SLOT_HERMES_LLM, "text"),
    ("GLOBAL_VIDEO_OPTIMIZER_MODEL", SLOT_VIDEO_PROMPT_OPTIMIZER, "vision"),
    ("KEYFRAME_PROMPT_MODEL", SLOT_VIDEO_PROMPT_OPTIMIZER, "vision"),
    ("SEEDANCE2_PROMPT_COMPOSER_MODEL", SLOT_SEEDANCE2_PROMPT_COMPOSER, "text"),
    ("GLOBAL_VIDEO_IDENTITY_DETECTOR_MODEL", SLOT_VIDEO_IDENTITY_DETECTOR, "vision"),
    ("IDENTITY_PLANNER_CAST_MODEL", SLOT_IDENTITY_CAST_PLANNER, "text"),
    ("IDENTITY_PLANNER_ANALYSIS_MODEL", SLOT_IDENTITY_ANALYSIS_PLANNER, "text"),
    ("IDENTITY_PLANNER_APPEARANCE_MODEL", SLOT_IDENTITY_APPEARANCE_WRITER, "text"),
    ("LITERAL_BEAT_META_MODEL", SLOT_LITERAL_BEAT_META, "text"),
    ("SCENE_BUILD_MODEL", SLOT_SCENE_BUILDER, "text"),
    ("CHARACTER_BUILD_MODEL", SLOT_CHARACTER_BUILDER, "text"),
    ("EPISODE_SCENE_PLANNER_MODEL", SLOT_EPISODE_SCENE_PLANNER, "text"),
    ("EPISODE_PROP_PLANNER_MODEL", SLOT_EPISODE_PROP_PLANNER, "text"),
    ("FREEZONE_TRANSLATION_MODEL", SLOT_FREEZONE_TRANSLATION, "text"),
    ("FREEZONE_TEXT_WRITER_MODEL", SLOT_FREEZONE_TEXT_WRITER, "text"),
    ("FREEZONE_STORY_SCRIPT_MODEL", SLOT_FREEZONE_STORY_SCRIPT, "text"),
    ("FREEZONE_VISION_MODEL", SLOT_FREEZONE_VISION, "vision"),
    ("STYLE_ANALYZER_MODEL", SLOT_STYLE_ANALYZER, "text"),
    ("CONTENT_REWRITER_MODEL", SLOT_CONTENT_REWRITER, "text"),
    ("DIRECTOR_SCREENWRITER_MODEL", SLOT_DIRECTOR_SCREENWRITER, "text"),
    ("SCREENPLAY_NORMALIZER_MODEL", SLOT_SCREENPLAY_NORMALIZER, "text"),
    ("EPISODE_SCENE_RECONCILE_MODEL", SLOT_EPISODE_SCENE_RECONCILER, "text"),
    ("NARRATED_SCENE_ASSET_MODEL", SLOT_NARRATED_SCENE_ASSET_PLANNER, "text"),
    ("STAGING_PROP_MODEL", SLOT_STAGING_PROP_PLANNER, "text"),
    ("COGNEE_LLM_MODEL", SLOT_COGNEE_LLM, "text"),
    ("COGNEE_EMBEDDING_MODEL", SLOT_COGNEE_EMBEDDING, "embedding"),
    # 场景头自动补全此前漏登记，解析不到就落到硬编码兜底真名（网关无此名 → 逐章
    # 503 → 增强文本静默回退原文 → 导入护栏误判「未导入小说」）。跟随通用文本位。
    ("SCENE_HEADER_AUTOGEN_MODEL", "DC-hermes-LLM", "text"),
    # 走 SuperPower 通用位（MODEL_NAME）的带图调用：调用方显式给的槽位别名为视觉位。
    ("MODEL_NAME", SLOT_HERMES_LLM, "text"),
)

SLOTS: dict[str, ModelSlot] = {
    key: ModelSlot(key=key, alias=alias, capability=capability, env=key)
    for key, alias, capability in _SLOT_ALIASES
}

# ``MODEL_NAME`` 是老 Agent 调用位共用的通用位（不是某个功能），不进对外功能位清单。
GENERIC_SLOT_KEY = "MODEL_NAME"
FEATURE_SLOTS: dict[str, ModelSlot] = {
    key: slot for key, slot in SLOTS.items() if key != GENERIC_SLOT_KEY
}

# 媒体槽位（默认图 / 视频 / 语音 / 转写）：真源是媒体模型映射里「该类型已启用、
# sortOrder 最小」的那一条，没有任何内置模型名兜底。
MEDIA_SLOTS: tuple[str, ...] = ("image", "video", "audio", "asr", "text", "vision")


class ModelSlotNotConfigured(RuntimeError):
    """槽位解析不到「目录里真实存在、且用户已绑定」的模型。

    继承 ``RuntimeError`` 是为了让既有「不要 500 给用户看细节」的兜底分支仍能接住，
    但 API 出口一律优先捕获本异常，转成 HTTP 400 中文（含槽位名与设置入口）。
    """

    def __init__(self, slot: str, reason: str, *, detail: str = "") -> None:
        self.slot = str(slot or "").strip()
        self.reason = str(reason or REASON_NOT_CONFIGURED)
        self.detail = str(detail or "").strip()
        super().__init__(self.message)

    @property
    def message(self) -> str:
        label = self.slot or "该功能"
        if self.reason == REASON_CATALOG_EMPTY:
            back = "该渠道还没有同步过模型清单，请先在渠道上点「同步模型清单」"
        elif self.reason == REASON_DELISTED:
            back = "原来绑定的模型已从渠道下架，绑定已自动取消"
        elif self.reason == REASON_UNBOUND:
            back = "该功能位还没有绑定可用模型"
        else:
            back = "当前没有可用的模型绑定"
        text = f"未绑定可用模型（{label}）：{back}。请到「设置 › 模型」从渠道目录中选择。"
        if self.detail:
            text = f"{text}（{self.detail}）"
        return text


def is_internal_alias_name(name: str) -> bool:
    """``DC-`` 前缀 = 内部功能位命名空间（不是模型名）。"""

    return str(name or "").strip().casefold().startswith("dc-")


def _clean_env(name: str) -> str:
    return str(os.environ.get(name) or "").strip()


def _fallback_env_value(env_name: str) -> str:
    """两条全局兜底 env 按**字面量**读取。

    功能位键名来自登记表（``scripts/check_env_config.py`` 会按 AST 认领），而兜底键
    名是计算出来的；写在调用点上，env 棘轮才认得出「运行期真有人读它」，否则
    ``.env.example`` 里的 ``VISION_MODEL_FALLBACK`` 会被判成死键。
    """

    if env_name == TEXT_FALLBACK_ENV:
        return _clean_env("TEXT_MODEL_FALLBACK")
    if env_name == VISION_FALLBACK_ENV:
        return _clean_env("VISION_MODEL_FALLBACK")
    return _clean_env(env_name)


def slot_for_env(model_env: str) -> ModelSlot | None:
    return SLOTS.get(str(model_env or "").strip())


def binding_for_alias(alias: str) -> dict[str, Any]:
    """槽位别名当前的用户绑定（``{provider, target}``）；未绑定回空字典。"""

    from novelvideo.model_gateway_settings import get_newapi_model_alias_mappings

    clean = str(alias or "").strip()
    if not clean:
        return {}
    for key, entry in get_newapi_model_alias_mappings().items():
        if str(key or "").strip().casefold() == clean.casefold():
            return dict(entry or {})
    return {}


def catalog_is_known(provider: str = "") -> bool:
    """是否已有目录可作裁决依据。

    没有目录 = 「还没同步过」，不是「渠道里没有模型」，此时**不拦截**（旧行为）：
    一次没拉取就把全部功能位判死，等于用工具的缺失误伤用户。有目录 = 事实，未绑定 /
    已下架一律前置拒绝 —— 生产环境三个渠道都已同步过目录，正是这里让「摘掉的模型不
    可能再被调用」成立。
    """

    from novelvideo.model_gateway_settings import catalog_names_for_provider

    if provider:
        return bool(catalog_names_for_provider(provider))
    from novelvideo.model_gateway_settings import get_upstream_models_snapshot

    return any(bool(names) for names in get_upstream_models_snapshot().values())


def alias_availability(alias: str) -> tuple[bool, str, str]:
    """槽位别名是否真能调通： ``(可用? , 原因, 目标名)``。

    判定只看两件事 —— 用户有没有绑、绑的名字还在不在渠道目录里。渠道目录是拉回来的
    事实（``catalog_names_for_provider``），代码不再从配置反推模型存在。
    """

    from novelvideo.model_gateway_settings import catalog_names_for_provider

    entry = binding_for_alias(alias)
    provider = str(entry.get("provider") or "").strip().lower()
    target = str(entry.get("target") or "").strip()
    if not catalog_is_known(provider if target else ""):
        return True, "", target
    if not target:
        return False, REASON_UNBOUND, ""
    if target not in catalog_names_for_provider(provider):
        return False, REASON_DELISTED, target
    return True, "", target


def resolve_slot(
    model_env: str,
    *,
    capability: str = "",
    default_alias: str = "",
    fallback_env: str = "",
) -> str:
    """解析一个文本 / 视觉功能位，返回**调用名**（内部别名或显式 env 值）。

    顺序：单独功能 env 覆盖 → 全局兜底 env → 已绑定且绑定在目录里的槽位别名 → 抛
    :class:`ModelSlotNotConfigured`。任何分支都不会回到硬编码模型名。
    """

    key = str(model_env or "").strip()
    override = _clean_env(key)
    if override:
        return override
    slot = slot_for_env(key)
    wanted = str(capability or "").strip().lower() or (slot.capability if slot else "text")
    env_name = str(fallback_env or "").strip() or (
        VISION_FALLBACK_ENV if wanted == "vision" else TEXT_FALLBACK_ENV
    )
    global_fallback = _fallback_env_value(env_name)
    if global_fallback:
        return global_fallback
    alias = str(default_alias or "").strip() or (slot.alias if slot else "")
    if not alias:
        raise ModelSlotNotConfigured(key, REASON_NOT_CONFIGURED, detail="未登记的槽位")
    if not is_internal_alias_name(alias):
        # 调用方点名的真模型（例如用户在界面上选中的 ``{provider}::{model}``）：合法
        # 与否只由渠道目录裁决 —— 目录说没有就是没有（跑到一半 404 的旧事故），目录
        # 还没同步过时不裁决（同一放行边界）。代码里的硬编码名一律走不到这里：静态守
        # 卫用例禁止调用点再传真实模型名当兜底。
        if catalog_is_known():
            raise ModelSlotNotConfigured(alias, REASON_DELISTED, detail="不在渠道目录")
        return alias
    usable, reason, _target = alias_availability(alias)
    if usable:
        return alias
    raise ModelSlotNotConfigured(f"{alias}（{key}）", reason)


def resolve_media_slot(media_type: str) -> str:
    """解析媒体默认槽位（image / video / audio / asr）：env 覆盖 → 用户默认 → 抛异常。"""

    wanted = str(media_type or "").strip().lower()
    if not wanted:
        raise ModelSlotNotConfigured(media_type, REASON_NOT_CONFIGURED)
    env_key = f"NEWAPI_{wanted.upper()}_MODEL"
    override = _clean_env(env_key)
    if override:
        return override
    from novelvideo.model_gateway_settings import resolve_default_media_model

    try:
        selected = str(resolve_default_media_model(wanted) or "").strip()
    except Exception as exc:  # noqa: BLE001 - 配置读取异常按未绑定处理，带上成因
        raise ModelSlotNotConfigured(
            f"默认{wanted}模型", REASON_NOT_CONFIGURED, detail=str(exc)
        ) from exc
    if selected:
        return selected
    # 有目录 = 用户确实还没在这一位上选模型；没有目录 = 还没同步过清单。两种
    # 成因给用户的动作不一样，文案不能混着说。
    raise ModelSlotNotConfigured(
        f"默认{wanted}模型",
        REASON_UNBOUND if catalog_is_known() else REASON_CATALOG_EMPTY,
    )


def embedding_slot_config() -> dict[str, Any]:
    """Embedding 绑定（provider / upstreamModel / dimension）；未绑定回空字典。"""

    from novelvideo.model_gateway_settings import get_newapi_embedding_model_config

    return get_newapi_embedding_model_config() or {}


def require_embedding_slot() -> dict[str, Any]:
    config = embedding_slot_config()
    provider = str(config.get("provider") or "").strip().lower()
    upstream = str(config.get("upstreamModel") or "").strip()
    if not provider or not upstream:
        raise ModelSlotNotConfigured(SLOT_COGNEE_EMBEDDING, REASON_UNBOUND)
    from novelvideo.model_gateway_settings import catalog_names_for_provider

    if catalog_is_known(provider) and upstream not in catalog_names_for_provider(provider):
        raise ModelSlotNotConfigured(
            SLOT_COGNEE_EMBEDDING, REASON_DELISTED, detail=upstream
        )
    return config


def describe_slots() -> dict[str, Any]:
    """自检视图：每个功能位的绑定与可用性（供设置页与体检接口，不抛异常）。"""

    rows: dict[str, Any] = {}
    for key, slot in sorted(FEATURE_SLOTS.items()):
        usable, reason, target = alias_availability(slot.alias)
        rows[key] = {
            "alias": slot.alias,
            "capability": slot.capability,
            "bound": bool(target),
            "target": target,
            "usable": usable,
            "reason": "" if usable else reason,
        }
    return rows
