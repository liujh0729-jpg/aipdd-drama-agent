"""[aipdd fork delta req40] 模型槽位解析：只认「设置 › 模型」+ 渠道目录。

平台不该预先知道渠道里有什么模型 —— 只能从渠道 BaseURL 拉。于是「某个功能用哪个
模型」只剩两个合法来源：

1. 用户在「设置 › 模型」里给**槽位**选的绑定 —— 且绑定的目标必须还在渠道目录里；
2. 调用方显式点名的模型（界面上下拉选出来的 ``{provider}::{model}`` 复合名），同样
   要过渠道目录裁决。

两者都拿不到时抛 :class:`ModelSlotNotConfigured`，由 API 出口转成 HTTP 400 中文
提示，**任务不入库、不启动**。旧实现在这里会落到 ``DEFAULT_TEXT_MODEL_BY_ENV``
的别名或调用方传进来的硬编码真名（``gpt-4o`` / ``gemini-3.5-flash`` /
``index-tts-2`` …）：网关里没有它们时要么 503，要么跑到 55% 才 404 —— 名字从渠道
下架也不会因为「代码里还写着」而重新存在。

[aipdd fork delta] **环境变量不再参与模型名的选定**（原先的三层里的前两层已砍）：
``IDENTITY_PLANNER_CAST_MODEL`` 这类 per-feature env 与 ``TEXT_MODEL_FALLBACK`` /
``VISION_MODEL_FALLBACK`` 全局兜底都退出了规划链。原因不是美观：

* 它们是**设置页看不见的绑定**。用户在「设置 › 模型」里配好了，env 还在后面抢位，
  于是「配了不生效」；把 env 清空又会掉到一个陈旧的出厂名上。
* env 里的名字没有生命周期：渠道下架后它不会自动取消，生产上那条
  ``No available channel for model LingShan-NB-2``（503）就是这么来的。
* 报错里出现环境变量名，等于把内部运维键交给界面用户当错误信息看。

槽位键名（``IDENTITY_PLANNER_CAST_MODEL`` 这种）仍是**登记表主键与网关路由键**。
只是不再去进程环境里读它。env 只留给真正的部署级参数（地址 / 密钥 / 超时 / 并发）。

``DC-*`` 是**内部功能位槽位键**（不是模型名）：它进网关 model_mapping 参与路由，
target 必须由用户设置且必须来自渠道目录。本模块登记表里的 ``alias`` 就是槽位键，
不携带任何 target。
"""

from __future__ import annotations

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

# 能力档的全局兜底 env 已随「解析链不再读 env」一并删除：文本 / 视觉的区分现在
# 只由槽位登记表里的 ``capability`` 决定（见 :func:`auto_fallback_call_name`）。

# 失效原因（前端 / 日志按此分流文案，不做二次猜测）。
REASON_CATALOG_EMPTY = "catalog-empty"
REASON_UNBOUND = "unbound"
REASON_DELISTED = "delisted"
REASON_NOT_CONFIGURED = "not-configured"


@dataclass(frozen=True)
class ModelSlot:
    """一个功能槽位：登记键（= 网关路由键）+ 别名 + 能力档 + 界面用中文功能名。

    ``key`` 仍是历史上的 per-feature env 名（也是设置页 / 体检接口认的槽位 ID），但
    系统**不再去进程环境里读它** —— 绑定的唯一真源是「设置 › 模型」的别名表。
    ``label`` 只用于给用户看的中文成因，不许流入日志 / 网关协议。
    """

    key: str
    alias: str
    capability: str = "text"
    label: str = ""


def _text_slot(key: str, alias: str, *, capability: str = "text") -> ModelSlot:
    return ModelSlot(key=key, alias=alias, capability=capability, label=slot_label(key))


# 槽位登记表（取代旧 ``DEFAULT_TEXT_MODEL_BY_ENV``）。值只有槽位键、能力档与中文
# 功能名，不携带任何 target —— 「绑哪个模型」永远是用户设置 + 渠道目录的交集。
# ``DC-*`` 常量本身是内部功能位命名空间（不是模型名），仍留在 official_defaults 里，
# 与别名真源、投影编译共用同一批字面量。
_SLOT_ALIASES: tuple[tuple[str, str, str, str], ...] = (
    ("HERMES_MODEL", SLOT_HERMES_LLM, "text", "通用文本生成"),
    ("GLOBAL_VIDEO_OPTIMIZER_MODEL", SLOT_VIDEO_PROMPT_OPTIMIZER, "vision", "全局视频优化"),
    ("KEYFRAME_PROMPT_MODEL", SLOT_VIDEO_PROMPT_OPTIMIZER, "vision", "首尾帧提示词"),
    ("SEEDANCE2_PROMPT_COMPOSER_MODEL", SLOT_SEEDANCE2_PROMPT_COMPOSER, "text", "视频提示词编排"),
    ("GLOBAL_VIDEO_IDENTITY_DETECTOR_MODEL", SLOT_VIDEO_IDENTITY_DETECTOR, "vision", "视频身份识别"),
    ("IDENTITY_PLANNER_CAST_MODEL", SLOT_IDENTITY_CAST_PLANNER, "text", "出场角色筛选"),
    ("IDENTITY_PLANNER_ANALYSIS_MODEL", SLOT_IDENTITY_ANALYSIS_PLANNER, "text", "角色身份分析"),
    ("IDENTITY_PLANNER_APPEARANCE_MODEL", SLOT_IDENTITY_APPEARANCE_WRITER, "text", "角色外观描写"),
    ("LITERAL_BEAT_META_MODEL", SLOT_LITERAL_BEAT_META, "text", "节拍元信息"),
    ("SCENE_BUILD_MODEL", SLOT_SCENE_BUILDER, "text", "场景构建"),
    ("CHARACTER_BUILD_MODEL", SLOT_CHARACTER_BUILDER, "text", "角色构建"),
    ("EPISODE_SCENE_PLANNER_MODEL", SLOT_EPISODE_SCENE_PLANNER, "text", "分集场景规划"),
    ("EPISODE_PROP_PLANNER_MODEL", SLOT_EPISODE_PROP_PLANNER, "text", "分集道具规划"),
    ("FREEZONE_TRANSLATION_MODEL", SLOT_FREEZONE_TRANSLATION, "text", "翻译"),
    ("FREEZONE_TEXT_WRITER_MODEL", SLOT_FREEZONE_TEXT_WRITER, "text", "文本写作"),
    ("FREEZONE_STORY_SCRIPT_MODEL", SLOT_FREEZONE_STORY_SCRIPT, "text", "故事与剧本"),
    ("FREEZONE_VISION_MODEL", SLOT_FREEZONE_VISION, "vision", "图片理解"),
    ("STYLE_ANALYZER_MODEL", SLOT_STYLE_ANALYZER, "vision", "风格分析"),
    ("CONTENT_REWRITER_MODEL", SLOT_CONTENT_REWRITER, "text", "内容改写"),
    ("DIRECTOR_SCREENWRITER_MODEL", SLOT_DIRECTOR_SCREENWRITER, "text", "导演编剧"),
    ("SCREENPLAY_NORMALIZER_MODEL", SLOT_SCREENPLAY_NORMALIZER, "text", "剧本规范化"),
    ("EPISODE_SCENE_RECONCILE_MODEL", SLOT_EPISODE_SCENE_RECONCILER, "text", "分集场景校对"),
    ("NARRATED_SCENE_ASSET_MODEL", SLOT_NARRATED_SCENE_ASSET_PLANNER, "text", "叙述场景资产规划"),
    ("STAGING_PROP_MODEL", SLOT_STAGING_PROP_PLANNER, "text", "调度道具规划"),
    ("COGNEE_LLM_MODEL", SLOT_COGNEE_LLM, "text", "知识库理解"),
    ("COGNEE_EMBEDDING_MODEL", SLOT_COGNEE_EMBEDDING, "embedding", "向量模型"),
    # 场景头自动补全此前漏登记，解析不到就落到硬编码兜底真名（网关无此名 → 逐章
    # 503 → 增强文本静默回退原文 → 导入护栏误判「未导入小说」）。跟随通用文本位。
    ("SCENE_HEADER_AUTOGEN_MODEL", "DC-hermes-LLM", "text", "场景头自动补全"),
    # 走 SuperPower 通用位（MODEL_NAME）的带图调用：调用方显式给的槽位别名为视觉位。
    ("MODEL_NAME", SLOT_HERMES_LLM, "text", "通用文本生成"),
)

SLOTS: dict[str, ModelSlot] = {
    key: ModelSlot(key=key, alias=alias, capability=capability, label=label)
    for key, alias, capability, label in _SLOT_ALIASES
}

# 别名 → 登记项：同一个别名可能挂在多个键下（如 KEYFRAME_PROMPT_MODEL 与
# GLOBAL_VIDEO_OPTIMIZER_MODEL 共用 DC-video-prompt-optimizer-LLM），能力档一致。
_ALIAS_TO_SLOT: dict[str, ModelSlot] = {}
for _slot in SLOTS.values():
    _ALIAS_TO_SLOT.setdefault(_slot.alias.casefold(), _slot)

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

    继承 ``RuntimeError`` 是为了让既有「不要 500 给用户看细节」的既有兜底分支仍能接住，
    但 API 出口一律优先捕获本异常，转成 HTTP 400 中文（含中文功能名与设置入口）。

    [aipdd fork delta] 主标签是**中文功能名**（ ``出场角色筛选``），内部槽位键只作为
    排错用的附注留在括号里 —— 以前直接拿 env 键名当标签，界面用户看到的是一句
    他完全读不懂、也没地方去改的 ``IDENTITY_PLANNER_CAST_MODEL``。
    """

    def __init__(self, slot: str, reason: str, *, detail: str = "", label: str = "") -> None:
        self.slot = str(slot or "").strip()
        self.reason = str(reason or REASON_NOT_CONFIGURED)
        self.detail = str(detail or "").strip()
        self.label = str(label or "").strip() or slot_label(self.slot) or media_slot_label(self.slot)
        super().__init__(self.message)

    @property
    def display_name(self) -> str:
        """给用户看的名字：中文功能名，没登记时回槽位键。"""

        return self.label or self.slot or "该功能"

    @property
    def message(self) -> str:
        # 中文功能名在前，内部键在后：前者是用户能对上设置页的词，后者只给排障的人用。
        shown = f"{self.display_name} · {self.slot}" if self.label and self.slot else (self.slot or "该功能")
        if self.reason == REASON_CATALOG_EMPTY:
            back = "该渠道还没有同步过模型清单，请先在渠道上点「同步模型清单」"
        elif self.reason == REASON_DELISTED:
            back = "它原来指向的模型已不在渠道清单里（下架或未开通），绑定已自动取消"
        elif self.reason == REASON_UNBOUND:
            back = "该功能位还没有绑定可用模型"
        else:
            back = "当前没有可用的模型绑定"
        text = f"未绑定可用模型（{shown}）：{back}。请到「设置 › 模型」从渠道目录中选择。"
        if self.detail:
            text = f"{text}（{self.detail}）"
        return text


_MEDIA_SLOT_LABELS: dict[str, str] = {
    "image": "默认图片模型",
    "video": "默认视频模型",
    "audio": "默认音频模型",
    "asr": "默认转写模型",
    "text": "默认文本模型",
    "vision": "默认视觉理解模型",
}


def slot_label(model_env: str) -> str:
    """功能位的中文功能名（供报错 / 体检接口回显）；没登记回空串。"""

    slot = SLOTS.get(str(model_env or "").strip())
    return slot.label if slot else ""


def media_slot_label(media_type: str) -> str:
    """媒体默认位的中文名（``image`` → 默认图片模型）；认不出回空串。"""

    return _MEDIA_SLOT_LABELS.get(str(media_type or "").strip().lower(), "")


def is_internal_alias_name(name: str) -> bool:
    """``DC-`` 前缀 = 内部功能位命名空间（不是模型名）。"""

    return str(name or "").strip().casefold().startswith("dc-")


def slot_for_env(model_env: str) -> ModelSlot | None:
    return SLOTS.get(str(model_env or "").strip())


def slot_for_alias(alias: str) -> ModelSlot | None:
    """按**槽位别名**反查登记项；没登记回 None。

    [aipdd fork delta]  capability 的推断需要它：旧链靠 ``VISION_MODEL_FALLBACK``
    保证带图调用不会被纯文本兜底接管，env 退出后这个隔离只剩「调用点显式给的视觉槽位
    别名自带的能力档」这一个依据。
    """

    clean = str(alias or "").strip().casefold()
    return _ALIAS_TO_SLOT.get(clean) if clean else None


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


def _local_alias_registry_is_source() -> bool:
    """本进程是否拥有别名 / 目录的本地真源（与 ``model_gateway_settings`` 同一谓词）。

    只有回答「是」时，「表里没有绑定」才是事实；否则别名表与目录恒为空，任何拦截
    都只是在把一个好端端的网关路由打挂。
    """

    from novelvideo.shared.runtime_env import is_ce_effective

    try:
        return bool(is_ce_effective())
    except Exception:  # noqa: BLE001 - 判定不了就不裁决，维持放行
        return False


def catalog_is_known(provider: str = "") -> bool:
    """是否已有目录可作裁决依据。

    没有目录 = 「还没同步过」，不是「渠道里没有模型」，对**已绑定**的别名不拦截（旧
    行为）：一次没拉取就把全部功能位判死，等于用工具的缺失误伤用户。有目录 = 事实，
    未绑定 / 已下架一律前置拒绝 —— 生产环境三个渠道都已同步过目录，正是这里让「摘掉
    的模型不可能再被调用」成立。未绑定的槽位键不在这条保护范围内（见
    :func:`alias_availability`）：它本身就不是模型名，网关里没有它的路由。
    """

    from novelvideo.model_gateway_settings import catalog_names_for_provider

    if provider:
        return bool(catalog_names_for_provider(provider))
    from novelvideo.model_gateway_settings import get_upstream_models_snapshot

    return any(bool(names) for names in get_upstream_models_snapshot().values())


def alias_availability(alias: str) -> tuple[bool, str, str]:
    """槽位别名是否真能调通： ``(可用? , 原因, 目标名)``。

    判定只看两件事 —— 用户有没有绑、绑的名字还在不在渠道目录里。渠道目录是拉回来的
    事实（``catalog_names_for_provider``），代码不再从配置反推模型存在。唯一例外是
    「没同步过目录 + 没绑定」：那时两个依据都缺，但网关的注册只从绑定表投影，所以下
    一行仍然不可能被路由 —— 依旧前置拒绝（REASON_CATALOG_EMPTY 指向可执行的动作）。
    """

    from novelvideo.model_gateway_settings import catalog_names_for_provider

    entry = binding_for_alias(alias)
    provider = str(entry.get("provider") or "").strip().lower()
    target = str(entry.get("target") or "").strip()
    if not catalog_is_known(provider if target else ""):
        if target or not _local_alias_registry_is_source():
            # 绑了目标但目录还没同步过：不拦截（旧行为）——「还没拉过清单」不等于
            # 「渠道里没有」，一次没拉就把全部功能位判死是用工具的缺失误伤用户。
            # 非 CE 进程同理：它读不到本地别名表与目录（恒为空），路由由控制面 /
            # 网关自证，这里没有裁决资格，只能放行。
            return True, "", target
        # 没绑 + 没目录：也不能放行。网关的 model_mapping 只从这份绑定表投影
        # （``build_channel_model_mapping`` 要求 target 非空），没绑 = 网关里必然没有
        # 这个槽位键，发出去只会撞 distributor 的 ``model_not_found``（HTTP 503）——
        # 用户看到的正是那条「报错来自网关」的废话式失败。前置拒绝，把动作说清楚：
        # 先把渠道清单同步出来，功能位才有模型可选。
        return False, REASON_CATALOG_EMPTY, ""
    if not target:
        return False, REASON_UNBOUND, ""
    if target not in catalog_names_for_provider(provider):
        return False, REASON_DELISTED, target
    return True, "", target


# ---- 未绑定不等于「该失败」：[aipdd fork delta] 两档自动兜底。
#
# 背景（容器实测）：新环境配好 Key、渠道清单也同步了，但别名表里一个文本功能位都没
# 绑 —— 「全空」是出厂状态而不是用户的错，而把第一次生成挡在「请逐个选 22 个模型」
# 面前不叫开箱即用。这里按两档接住，**绝不代用户写设置**（绑定表一直是他的决定）：
#
#   1) 同类功能位里用户已经绑好的那一个 —— 他批量绑过哪个就是用哪个；返回的是那个
#      槽位别名，它已随绑定投影进网关，路由依据现成；
#   2) 渠道目录里的第一个可用项（复合名 ``{provider}::{model}``）—— 拍平后目录里每个
#      名字都按复合名注册，不靠绑定也能路由；上游实测「没开通」的名字跳过。
#
# 两档都不成立（渠道没配 / 目录空 / 候选全被上游判死）才回到前置拒绝。
_TEXT_DEFAULT_PRIORITY: tuple[str, ...] = (
    "HERMES_MODEL",
    "FREEZONE_TEXT_WRITER_MODEL",
    "COGNEE_LLM_MODEL",
)
_VISION_DEFAULT_PRIORITY: tuple[str, ...] = (
    "FREEZONE_VISION_MODEL",
    "GLOBAL_VIDEO_IDENTITY_DETECTOR_MODEL",
    "KEYFRAME_PROMPT_MODEL",
    "GLOBAL_VIDEO_OPTIMIZER_MODEL",
)


def _not_granted_lookup(capability: str):
    """``(provider, model) -> 上游是否明确不可用``：与工具页下拉同一口径。"""

    try:
        from novelvideo.text_model_probe import build_not_granted_lookup

        return build_not_granted_lookup(capability)
    except Exception:  # noqa: BLE001 - 探测子系统再不可靠也不能伤兜底
        return lambda *_args: False


def _bound_slot_alias_for_capability(capability: str) -> str:
    """同类功能位里用户已经绑好的那一个（按通用位优先的固定序）；都没绑回空串。"""

    wanted = str(capability or "text").strip().lower()
    priority = _VISION_DEFAULT_PRIORITY if wanted == "vision" else _TEXT_DEFAULT_PRIORITY
    ordered = [key for key in priority if key in FEATURE_SLOTS]
    ordered += [key for key in sorted(FEATURE_SLOTS) if key not in ordered]
    for key in ordered:
        slot = FEATURE_SLOTS.get(key)
        if slot is None or slot.capability != wanted:
            continue
        usable, _reason, target = alias_availability(slot.alias)
        if usable and target:
            return slot.alias
    return ""


def _catalog_default_call_name(capability: str) -> str:
    """渠道目录里的首个可用项（复合调用名）；没有目录 / 没有渠道时回空串。

    只在「目录已知」的前提下提名：复合名是跟着目录投影进网关的，没同步过目录时
    编出来的名字同样不会被路由 —— 那正是本次要消灭的那条 503。
    """

    wanted = str(capability or "text").strip().lower()
    if wanted not in ("text", "vision"):
        return ""
    try:
        from novelvideo.catalog.upstream_catalog import text_entries, vision_entries
        from novelvideo.model_gateway_settings import (
            get_newapi_provider_channels,
            get_upstream_catalog,
            provider_spot_model,
        )
    except Exception:  # noqa: BLE001 - 兜底失败回到前置拒绝，不抛新异常
        return ""
    picker = vision_entries if wanted == "vision" else text_entries
    not_granted = _not_granted_lookup(wanted)
    try:
        channels = get_newapi_provider_channels()
    except Exception:  # noqa: BLE001
        return ""
    for channel in channels:
        provider = str(channel.get("provider") or "").strip().lower()
        if not provider or provider == "comfyui":
            continue
        try:
            entries = picker(get_upstream_catalog(provider))
        except Exception:  # noqa: BLE001 - 单渠道目录读失败不影响其余渠道
            continue
        for name in sorted(
            {
                str(entry.get("name") or "").strip()
                for entry in entries
                if str(entry.get("name") or "").strip()
            }
        ):
            if not_granted(provider, name):
                continue
            return provider_spot_model(provider, name)
    return ""


def auto_fallback_call_name(capability: str) -> str:
    """未绑定 / 绑定失效时的自动兜底调用名（供解析链与状态回显共用同一口径）。"""

    wanted = str(capability or "text").strip().lower()
    return _bound_slot_alias_for_capability(wanted) or _catalog_default_call_name(wanted)


def _explicit_name_is_routable(name: str) -> bool:
    """一个**真模型名**能不能在网关里路由：在任一渠道目录里找得到。

    复合名 ``{provider}::{model}`` 按指定渠道查（拍平后目录里每个名字都按复合名注册）；
    裸名只要任一渠道目录里有就算（裸名只在「恰好单渠道拥有」时才注册，那是网关侧的
    判定，不在这里抢着复述）。
    """

    from novelvideo.model_gateway_settings import (
        catalog_names_for_provider,
        get_upstream_models_snapshot,
    )

    clean = str(name or "").strip()
    if not clean:
        return False
    provider, sep, bare = clean.partition("::")
    if sep:
        return bare in catalog_names_for_provider(str(provider or "").strip().lower())
    snapshot = get_upstream_models_snapshot()
    return any(clean in (names or []) for names in snapshot.values())


def _resolve_named_call_name(key: str, name: str, capability: str) -> str:
    """调用方**点名**给的值：核过才放行。

    - 内部别名（``DC-*``）：按那条别名的在册情况裁决，同样享受两档兜底；
    - 真模型名（用户在界面上选中的 ``{provider}::{model}``）：目录已知就必须在某个
      渠道目录里找得到，找不到就是 400，而不是让它跑到 distributor 拿一句
      「No available channel」的 503。
    """

    if is_internal_alias_name(name):
        usable, reason, _target = alias_availability(name)
        if usable:
            return name
        fallback = auto_fallback_call_name(capability)
        if fallback:
            return fallback
        if reason == REASON_CATALOG_EMPTY:
            return name
        raise ModelSlotNotConfigured(key, reason, detail=f"槽位 {name}")
    if catalog_is_known() and not _explicit_name_is_routable(name):
        raise ModelSlotNotConfigured(
            key,
            REASON_DELISTED,
            detail=f"{name} 不在渠道的模型清单里（已从渠道下架或尚未开通）："
            "渠道目录才是模型名的真源，请到「设置 › 模型」改选目录里的名字",
        )
    return name


def resolve_slot(
    model_env: str,
    *,
    capability: str = "",
    default_alias: str = "",
) -> str:
    """解析一个文本 / 视觉功能位，返回**调用名**（内部别名或调用方点名的模型）。

    [aipdd fork delta] 顺序只剩两层事实：**已绑定且绑定在目录里的槽位别名** → 同类
    功能位已绑过的那一个 / 渠道目录首项（见 :func:`auto_fallback_call_name`）→ 抛
    :class:`ModelSlotNotConfigured`。原先排在最前面的两层 env（per-feature
    ``IDENTITY_PLANNER_CAST_MODEL`` 与全局 ``TEXT_MODEL_FALLBACK`` /
    ``VISION_MODEL_FALLBACK``）已全部退出解析链：一个在设置页看不见、渠道下架后不会
    自动取消的名字，不能排在用户的绑定前面 —— 那正是「模型配齐了还是报错」与
    ``No available channel`` 503 的源头。文本 / 视觉的隔离不再靠 env，而由槽位登记表
    的 ``capability`` 控制（兜底只会提名同能力的模型）。

    任何分支都不会回到硬编码模型名：兜底名来自用户已有的绑定或拉回来的清单。唯一例外
    是「清单从没同步过且一个都没绑」的两难：那时没有任何可提名的事实，仍旧把槽位别名
    交回网关（旧行为），而不是凭空编一个名字。
    """

    key = str(model_env or "").strip()
    slot = slot_for_env(key)
    alias = str(default_alias or "").strip() or (slot.alias if slot else "")
    if not alias:
        raise ModelSlotNotConfigured(key, REASON_NOT_CONFIGURED, detail="未登记的槽位")
    # 能力档：调用方显式给的 > 点名别名的 > 功能位登记的 > text。
    # 老 Agent 调用位（MODEL_NAME 这种通用位）自己不区分图/文，它靠调用点传进来的视
    # 觉槽位别名来推断 —— 没有这一层，审核 / 验证那些带图调用会被当成纯文本位，
    # 兜底挑到一个不支持图片的模型（就是旧 env 链靠 VISION_MODEL_FALLBACK 避免的
    # 那个 400）。
    alias_slot = slot_for_alias(alias) if is_internal_alias_name(alias) else None
    wanted_cap = str(capability or "").strip().lower()
    wanted = (
        wanted_cap
        or (alias_slot.capability if alias_slot else "")
        or (slot.capability if slot else "")
        or "text"
    )
    if not is_internal_alias_name(alias):
        # 调用方点名的真模型（例如用户在界面上选中的 ``{provider}::{model}``）：合法
        # 与否只由渠道目录裁决 —— 目录说没有就是没有（跑到一半 404 的旧事故），目录
        # 还没同步过时不裁决（同一放行边界）。代码里的硬编码名一律走不到这里：静态守
        # 卫用例禁止调用点再传真实模型名当兜底。
        return _resolve_named_call_name(key, alias, wanted)
    usable, reason, _target = alias_availability(alias)
    if usable:
        return alias
    # 这一位自己有问题：先看同类功能位里用户绑过的，再看渠道目录 —— 两档都来自事实
    # （他的绑定 + 拉回来的清单），代码里没有任何硬编码模型名。
    fallback = auto_fallback_call_name(wanted)
    if fallback:
        return fallback
    if reason == REASON_CATALOG_EMPTY:
        # 一个事实都没拿到（清单从没同步过，而且一个功能位都没绑）：不拦截，把槽位
        # 别名交回网关（req40 旧行为）。这里编不出任何名字，硬拦只会把路由由控制面 /
        # 人工维护的部署打成 400；真撞上 distributor 的 503，处理指引会指向「先同步
        # 模型清单」（见 model_call_diagnostics）。广场 / 状态回显仍按不可用显示 ——
        # 它们直读 :func:`alias_availability`，不走这一层放行。
        return alias
    raise ModelSlotNotConfigured(key, reason, detail=f"槽位 {alias}")


def resolve_media_slot(media_type: str) -> str:
    """解析媒体默认槽位（image / video / audio / asr）：只看设置里的默认位。

    [aipdd fork delta] 以前第一层是 ``NEWAPI_{IMAGE,VIDEO,AUDIO,ASR}_MODEL`` env 覆盖：
    它比「设置 › 模型」的默认生成模型面板还高，面板里换了模型而 env 还写着旧名时，
    用户看到的和实际调用的不是同一个模型。现在真源只有媒体模型映射表（同类型里
    enabled 且 sortOrder 最小的那一条）。
    """

    wanted = str(media_type or "").strip().lower()
    if not wanted:
        raise ModelSlotNotConfigured(media_type, REASON_NOT_CONFIGURED)
    from novelvideo.model_gateway_settings import resolve_default_media_model

    try:
        selected = str(resolve_default_media_model(wanted) or "").strip()
    except Exception as exc:  # noqa: BLE001 - 配置读取异常按未绑定处理，带上成因
        raise ModelSlotNotConfigured(
            wanted, REASON_NOT_CONFIGURED, detail=str(exc)
        ) from exc
    if selected:
        return selected
    # 有目录 = 用户确实还没在这一位上选模型；没有目录 = 还没同步过清单。两种
    # 成因给用户的动作不一样，文案不能混着说。
    raise ModelSlotNotConfigured(
        wanted,
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
        raise ModelSlotNotConfigured(
            "COGNEE_EMBEDDING_MODEL", REASON_UNBOUND, detail=f"槽位 {SLOT_COGNEE_EMBEDDING}"
        )
    from novelvideo.model_gateway_settings import catalog_names_for_provider

    if catalog_is_known(provider) and upstream not in catalog_names_for_provider(provider):
        raise ModelSlotNotConfigured(
            "COGNEE_EMBEDDING_MODEL", REASON_DELISTED, detail=upstream
        )
    return config


def describe_slots() -> dict[str, Any]:
    """自检视图：每个功能位的绑定与可用性（供设置页与体检接口，不抛异常）。

    [aipdd fork delta] 未绑定的位同时交出「解析链会自动兜底到哪个调用名」：状态回显
    必须和真实路由一致，不能让设置页说「没绑」而生成照跑在某个模型上 —— 那个模型得
    看得见、能改。
    """

    rows: dict[str, Any] = {}
    for key, slot in sorted(FEATURE_SLOTS.items()):
        usable, reason, target = alias_availability(slot.alias)
        fallback = "" if usable else auto_fallback_call_name(slot.capability)
        rows[key] = {
            "alias": slot.alias,
            "label": slot.label,
            "capability": slot.capability,
            "bound": bool(target),
            "target": target,
            "usable": usable,
            "reason": "" if usable else reason,
            "autoFallback": fallback,
            "autoFallbackSource": (
                "" if usable or not fallback else (
                    "boundSibling" if is_internal_alias_name(fallback) else "catalogDefault"
                )
            ),
        }
    return rows
