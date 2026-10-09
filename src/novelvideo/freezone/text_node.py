"""Freezone 文本工具辅助逻辑。

当前包含：
- 中英文提示词互译
- 自由文本生成
- 故事脚本生成
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from novelvideo import prompt_library
from novelvideo.egress_context import TrustedEgressContext
from novelvideo.model_gateway_runtime import (
    current_model_gateway_context,
    model_gateway_output_retries,
)

from novelvideo.official_defaults import (
    DEFAULT_FREEZONE_STORY_SCRIPT_MODEL,
    DEFAULT_FREEZONE_TEXT_WRITER_MODEL,
    DEFAULT_FREEZONE_TRANSLATION_MODEL,
)

FREEZONE_TRANSLATION_PROVIDER = "newapi"
FREEZONE_TRANSLATION_MODEL = DEFAULT_FREEZONE_TRANSLATION_MODEL
FREEZONE_TEXT_WRITER_MODEL = DEFAULT_FREEZONE_TEXT_WRITER_MODEL
FREEZONE_STORY_SCRIPT_MODEL = {
    "id": DEFAULT_FREEZONE_STORY_SCRIPT_MODEL,
    "provider": "newapi",
    "model": DEFAULT_FREEZONE_STORY_SCRIPT_MODEL,
    "label": "DramaClawAPI Story Script",
}
LEGACY_FREEZONE_STORY_SCRIPT_MODEL_IDS = {
    "newapi_gemini_flash",
    "openrouter_gemini_flash",
    "OpenRouter Gemini 2.5 Flash",
}# [aipdd fork delta] 提示词库覆盖：系统提示词改为运行时读取，可在「设置 → 提示词库」里覆盖。
def freezone_translation_system_prompt() -> str:
    """Freezone 中英互译系统提示词（提示词库键 freezone.translator.system）。"""
    return prompt_library.get_prompt_text("freezone.translator.system")


def freezone_text_writer_system_prompt() -> str:
    """Freezone 自由文本生成系统提示词（提示词库键 freezone.text_writer.system）。"""
    return prompt_library.get_prompt_text("freezone.text_writer.system")


def freezone_story_script_system_prompt(*, with_vision: bool = False) -> str:
    """分镜脚本系统提示词（提示词库键 freezone.story_script.system）。

    with_vision=True 时追加「视频参考 / 角色参考」视觉模式说明段
    （提示词库键 freezone.story_script.video_suffix）。
    """
    base = prompt_library.get_prompt_text("freezone.story_script.system")
    if not with_vision:
        return base
    suffix = prompt_library.get_prompt_text("freezone.story_script.video_suffix")
    return base.rstrip("\n") + "\n\n" + suffix.strip("\n") + "\n"


FREEZONE_NODE_TYPE_LABELS: dict[str, str] = {
    "generic": "通用提示词",
    "image": "图片节点提示词",
    "video": "视频节点提示词",
    "audio": "音频节点提示词",
    "text": "文本节点提示词",
}

_translation_agent: Optional[Agent] = None
_text_writer_agent: Optional[Agent] = None
# [aipdd fork delta] 文本步骤支持用户自选文本模型后，Agent 按模型分键缓存（与
# 脚本节点同口径），避免换模型后命中上一个模型的缓存 Agent。
_text_writer_agents: dict[str, Agent] = {}
_story_script_agent: Optional[Agent] = None
# [aipdd fork delta] 脚本节点支持自选文本模型后，Agent 按模型 id 分键缓存。
_story_script_agents: dict[str, Agent] = {}
_video_story_script_agent: Optional[Agent] = None
# [aipdd fork delta] 「视频反推剧本」支持用户自选视觉模型后，视觉 Agent 同样
# 按模型分键缓存，避免换模型后命中上一个模型的缓存 Agent。
_video_story_script_agents: dict[str, Agent] = {}

# [aipdd fork delta] 提示词库覆盖变化后，清空 Agent 缓存让新 system_prompt 生效。
_prompt_revision: Optional[str] = None


def _refresh_prompt_agents() -> None:
    global _prompt_revision, _translation_agent, _text_writer_agent
    global _story_script_agent, _video_story_script_agent
    revision = prompt_library.revision()
    if revision == _prompt_revision:
        return
    _prompt_revision = revision
    _translation_agent = None
    _text_writer_agent = None
    _text_writer_agents.clear()
    _story_script_agent = None
    _story_script_agents.clear()
    _video_story_script_agent = None
    _video_story_script_agents.clear()



class FreezoneTranslationResult(BaseModel):
    """Structured translation result produced by the LLM."""

    translated_text: str = Field(description="Translated prompt text.")
    source_language: Literal["zh", "en"] = Field(
        description="Dominant natural language detected from the source text."
    )
    target_language: Literal["zh", "en"] = Field(
        description="Opposite target language used for translation."
    )


def create_freezone_translation_agent() -> Agent:
    """创建 Freezone 中英互译 Agent。"""
    from novelvideo.config import (
        get_newapi_structured_output_model_settings,
        get_newapi_text_pydantic_model,
    )

    model = get_newapi_text_pydantic_model(
        "FREEZONE_TRANSLATION_MODEL",
        FREEZONE_TRANSLATION_MODEL,
        capability="freezone.text.generate",
    )
    return Agent(
        model,
        system_prompt=freezone_translation_system_prompt(),
        model_settings=get_newapi_structured_output_model_settings(),
        output_type=FreezoneTranslationResult,
        name="Freezone Prompt Translator",
    )


def get_freezone_translation_agent() -> Agent:
    """获取翻译 Agent 单例。"""
    global _translation_agent
    _refresh_prompt_agents()
    context = current_model_gateway_context()
    if context is not None and context.is_organization:
        return create_freezone_translation_agent()
    if _translation_agent is None:
        _translation_agent = create_freezone_translation_agent()
    return _translation_agent


def create_freezone_text_writer_agent(model: str | None = None) -> Agent:
    """创建 Freezone 自由文本生成 Agent。

    [aipdd fork delta] ``model`` 为用户显式选定的文本模型（如工具页「一句话
    短视频」的文本步骤）；留空走 env 默认链，显式选择时不让 env 默认反向覆盖。
    """
    from novelvideo.config import get_newapi_text_pydantic_model

    model_text = str(model or "").strip()
    llm_model = get_newapi_text_pydantic_model(
        "FREEZONE_TEXT_WRITER_MODEL",
        FREEZONE_TEXT_WRITER_MODEL,
        # 用户显式选了模型时不让 env 默认反向覆盖（与脚本节点同口径）。
        model_name_override=model_text or None,
    )
    return Agent(
        llm_model,
        system_prompt=freezone_text_writer_system_prompt(),
        output_type=str,
        name="Freezone AI Text Writer",
    )


def get_freezone_text_writer_agent(model: str | None = None) -> Agent:
    """获取自由文本生成 Agent；按模型分键缓存（[aipdd fork delta]：原来是全局
    单例，用户换模型会命中第一个模型的缓存 Agent）。组织上下文每次新建，与
    翻译/脚本节点的出网口径一致。"""
    global _text_writer_agent
    _refresh_prompt_agents()
    model_text = str(model or "").strip()
    context = current_model_gateway_context()
    if context is not None and context.is_organization:
        return create_freezone_text_writer_agent(model_text or None)
    agent_key = model_text
    cached = _text_writer_agents.get(agent_key)
    if cached is not None:
        return cached
    agent = create_freezone_text_writer_agent(model_text or None)
    _text_writer_agents[agent_key] = agent
    if not agent_key:
        _text_writer_agent = agent
    return agent


def resolve_freezone_text_writer_model(model: str | None = None) -> str:
    """返回自由文本生成使用的逻辑模型名，供结果与审计记录使用。

    用户显式选定的模型优先（与执行身份一致）；留空回落 env 解析链。
    """
    model_text = str(model or "").strip()
    if model_text:
        return model_text
    from novelvideo.config import get_newapi_text_model_name

    return get_newapi_text_model_name(
        "FREEZONE_TEXT_WRITER_MODEL",
        FREEZONE_TEXT_WRITER_MODEL,
    )


def resolve_freezone_story_script_model(model: str | None) -> dict[str, str]:
    model_text = str(model or "").strip()
    if not model_text:
        return dict(FREEZONE_STORY_SCRIPT_MODEL)
    if model_text == FREEZONE_STORY_SCRIPT_MODEL["id"]:
        return dict(FREEZONE_STORY_SCRIPT_MODEL)
    if model_text.casefold() == FREEZONE_STORY_SCRIPT_MODEL["label"].casefold():
        return dict(FREEZONE_STORY_SCRIPT_MODEL)
    if model_text in LEGACY_FREEZONE_STORY_SCRIPT_MODEL_IDS:
        return dict(FREEZONE_STORY_SCRIPT_MODEL)
    # [aipdd fork delta] 画布脚本节点支持用户从文本模型候选里自选：非白名单的
    # 模型名不再拒绝，透传给 newapi 文本网关（capability 仍按 freezone.text.generate
    # 计费/路由）。显式选择的模型优先级高于 env 默认（model_name_override）。
    return {
        "id": model_text,
        "provider": "newapi",
        "model": model_text,
        "label": model_text,
        "explicit": "1",
    }


def create_freezone_story_script_agent(model: str | None = None) -> Agent:
    """创建故事脚本生成 Agent。"""
    from novelvideo.api.schemas import FreezoneStoryScriptGenerateData
    from novelvideo.config import (
        get_newapi_structured_output_model_settings,
        get_newapi_text_pydantic_model,
    )

    resolved = resolve_freezone_story_script_model(model)
    llm_model = get_newapi_text_pydantic_model(
        "FREEZONE_STORY_SCRIPT_MODEL",
        resolved["model"],
        # 用户显式选了模型时不让 env 默认反向覆盖；白名单内仍维持原行为。
        model_name_override=resolved["model"] if resolved.get("explicit") else None,
        capability="freezone.text.generate",
    )
    return Agent(
        llm_model,
        system_prompt=freezone_story_script_system_prompt(),
        model_settings=get_newapi_structured_output_model_settings(),
        output_type=FreezoneStoryScriptGenerateData,
        # 结构化脚本表字段多、且 shot_no/duration 是严格 int，模型偶尔会把时长写成
        # "2-5"/"3秒" 之类而过不了校验。默认 output_retries=1 只给一次纠正机会不够，
        # 抛 "Exceeded maximum output retries (1)"。对齐本仓其它复杂结构化 agent
        # (episode_planner / content_rewriter)提到 3，让模型按回喂的校验错误自我修正。
        output_retries=model_gateway_output_retries(3),
        name="Freezone Story Script Generator",
    )


def get_freezone_story_script_agent(model: str | None = None) -> Agent:
    """获取故事脚本生成 Agent；按解析后的模型 id 分键缓存（[aipdd fork delta]：
    原来是全局单例，用户选了第二个模型会命中第一个模型的缓存 Agent）。"""
    global _story_script_agent
    _refresh_prompt_agents()
    resolved = resolve_freezone_story_script_model(model)
    context = current_model_gateway_context()
    if context is not None and context.is_organization:
        return create_freezone_story_script_agent(resolved["id"])
    agent_key = str(resolved["id"])
    cached = _story_script_agents.get(agent_key)
    if cached is not None:
        return cached
    agent = create_freezone_story_script_agent(resolved["id"])
    _story_script_agents[agent_key] = agent
    if agent_key == str(FREEZONE_STORY_SCRIPT_MODEL["id"]):
        _story_script_agent = agent
    return agent


def build_freezone_translation_task(
    *,
    text: str,
    node_type: Literal["generic", "image", "video", "audio", "text"],
) -> str:
    """构建翻译任务。"""
    node_label = FREEZONE_NODE_TYPE_LABELS[node_type]

    parts = [
        f"Translate the following {node_label}.",
        "You must decide whether the dominant natural language is Simplified Chinese or English.",
        "If dominant language is English, translate into Simplified Chinese.",
        "If dominant language is Simplified Chinese, translate into English.",
        "Do not copy the original prose when translating between different languages.",
        "Preserve IDs, file names, bracket tags, color codes, ratios, and model names exactly, but translate the surrounding natural-language instructions.",
        "Keep it directly usable as a creative prompt.",
    ]
    parts.append(f"Source text:\n{text.strip()}")
    return "\n\n".join(parts)


async def translate_freezone_text(
    *,
    text: str,
    node_type: Literal["generic", "image", "video", "audio", "text"] = "generic",
    egress_context: TrustedEgressContext | None = None,
) -> tuple[str, Literal["zh", "en"], Literal["zh", "en"]]:
    """执行 Freezone 中英互译。"""
    if not text or not text.strip():
        return "", "zh", "en"

    task = build_freezone_translation_task(
        text=text,
        node_type=node_type,
    )
    from novelvideo.model_gateway_runtime import model_gateway_request_scope

    with model_gateway_request_scope(egress_context):
        response = await get_freezone_translation_agent().run(task)
    result = response.output
    target_language: Literal["zh", "en"] = result.target_language
    if target_language == result.source_language:
        target_language = "zh" if result.source_language == "en" else "en"
    return (
        result.translated_text.strip(),
        result.source_language,
        target_language,
    )


async def generate_freezone_text(
    *,
    prompt: str,
    model: str | None = None,
    egress_context: TrustedEgressContext | None = None,
) -> tuple[str, str]:
    """根据用户指令生成自由文本，返回逻辑模型名与最终文本。**会出网**。

    形参与 `model_gateway_request_scope` 都照 `translate_freezone_text` 写：
    `runners/freezone.py:FREEZONE_LEAF_EGRESS` 判本函数为 NETWORK 的依据就是它。

    [aipdd fork delta] ``model`` 为用户显式选定的文本模型（如工具页「一句话
    短视频」的文本步骤，模型目录 id）；留空走 env 默认链。显式模型会用于执行
    并写进返回的模型名（结果与审计展示口径）。
    """
    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        raise ValueError("prompt is required")
    model_text = str(model or "").strip()

    from novelvideo.model_gateway_runtime import model_gateway_request_scope

    with model_gateway_request_scope(egress_context):
        writer = get_freezone_text_writer_agent(model_text or None)
        response = await writer.run(clean_prompt)
    generated_text = str(response.output or "").strip()
    if not generated_text:
        raise ValueError("text generation returned empty output")
    return resolve_freezone_text_writer_model(model_text or None), generated_text


_STORY_SCRIPT_COMMON_RULES = (
    "输出字段必须覆盖：镜号、时长、画面描述、角色1、角色描述1、角色图1、角色2、角色描述2、"
    "角色图2、参考、景别、角色动作、情绪、场景标签、光影氛围、音效、对白、分镜提示词、视频运动提示词。",
    "如果用户给了额外要求，也必须一起遵守。",
    "请严格按照影视制片表格思路输出，不要输出散文摘要。",
    "请让分镜提示词和视频运动提示词都采用括号分段 + 号连接的格式。",
    "缺失对白时写 `无`。",
    "角色图1、角色图2、参考三个字段一律输出空字符串——它们由后端回填素材 URL，"
    "不要写 `无`，也不要编造文件名或链接。",
    "分镜提示词必须像高质量图像生成提示词，视频运动提示词必须像高质量视频运动提示词，而不是简单一句概括。",
)

_STORY_SCRIPT_STYLE_HINT = (
    "参考风格要点：\n"
    "- 镜号连续递增\n"
    "- 时长大多 2-5 秒\n"
    "- 景别写法类似 `近景 / 特写`、`中景 / 仰视`\n"
    "- 角色描述尽量写成 `[角色ID: ...]` 形式\n"
    "- 同一个人物在所有行里必须用完全一致的角色名，方便回填角色参考图\n"
    "- 一镜里出现两个角色时，第二个填进角色2 / 角色描述2\n"
    "- 分镜提示词最好严格按 8 段写：构图、角色卡/主体描述、空间关系、微表情/状态、环境与道具、光影几何、视觉风格、技术参数\n"
    "- 如果存在角色1，分镜提示词第二段尽量直接使用或轻改角色描述1，不要换成模糊代称\n"
    "- 分镜提示词中的技术参数段尽量保留，不要省略\n"
    "- 视频运动提示词最好严格按 6 段写：运镜轨迹、主体动作、环境动态、音效氛围、对白语气、时长\n"
    "- 视频运动提示词里的主体动作必须是可见物理动作，不要只写情绪变化"
)


def _character_ref_block(
    character_refs: Sequence[Mapping[str, Any]] | None,
) -> str | None:
    """把角色参考图渲染成任务里的角色卡清单。

    只给模型「名字 + 描述 + 定位」，不给 URL —— 图片本身通过多模态附件传，
    URL 由后端在生成之后按角色名回填，避免模型把链接抄错或凭空编造。
    """
    if not character_refs:
        return None
    lines: list[str] = []
    for index, ref in enumerate(character_refs, start=1):
        name = str(ref.get("name") or "").strip() or f"角色{index}"
        description = str(ref.get("description") or "").strip()
        role = str(ref.get("role") or "").strip()
        detail = "，".join(part for part in (role, description) if part)
        lines.append(f"{index}. {name}" + (f"（{detail}）" if detail else ""))
    return (
        "已提供的角色参考（按顺序对应随附的角色参考图）：\n"
        + "\n".join(lines)
        + "\n请在生成的角色1 / 角色2 字段里使用上面完全一致的角色名，"
        "这样后端才能把对应的角色参考图回填进去。"
    )


def build_freezone_story_script_task(
    *,
    source_text: str,
    prompt: str,
    character_refs: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """构建故事脚本生成任务。"""
    parts = [
        "根据以下上传剧本内容生成一个完整的故事脚本表。",
        *_STORY_SCRIPT_COMMON_RULES,
    ]
    if prompt.strip():
        parts.append(f"用户要求：\n{prompt.strip()}")
    character_block = _character_ref_block(character_refs)
    if character_block:
        parts.append(character_block)
    parts.append(_STORY_SCRIPT_STYLE_HINT)
    parts.append(f"源剧本内容：\n{source_text.strip()}")
    return "\n\n".join(parts)


def build_freezone_video_story_script_task(
    *,
    frame_count: int,
    prompt: str,
    duration_sec: float | None = None,
    character_refs: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """构建「视频参考生成分镜脚本」任务。

    与文本模式共用同一张输出表，区别是源素材换成了按时间顺序抽取的关键帧，
    并要求模型给出 ``keyframe_index`` 以便后端回填每一镜的参考图。
    """
    duration_hint = (
        f"该视频总时长约 {duration_sec:.2f} 秒，所有镜头时长加起来应接近这个值。"
        if duration_sec and duration_sec > 0
        else "视频总时长未知，请按关键帧的疏密给出合理时长。"
    )
    parts = [
        f"下面按时间顺序给你 {frame_count} 张从参考视频里抽取的关键帧，"
        "请把这段视频拆解成一张完整的故事脚本表。",
        "这是视频拆解任务，不是原创任务：表格内容必须如实描述这些关键帧里真实出现的"
        "主体、场景、动作和风格。严禁套用系统提示词里的示例角色或示例场景。",
        "先通读全部关键帧判断这究竟是什么内容（人物？动物？动画？实拍？），"
        "再决定角色名和角色描述。主体不是人时，就照实写成该动物 / 物体 / 形象，不要替换成人物。",
        duration_hint,
        "把连续的关键帧归纳成若干叙事镜头，不要逐帧机械罗列。",
        "每一行都必须给出 keyframe_index：最能代表这一镜的输入关键帧序号"
        f"（1 到 {frame_count} 之间的整数）。后端靠它回填这一镜的参考图。",
        *_STORY_SCRIPT_COMMON_RULES,
    ]
    if prompt.strip():
        parts.append(f"用户额外要求：\n{prompt.strip()}")
    character_block = _character_ref_block(character_refs)
    if character_block:
        parts.append(character_block)
    parts.append(_STORY_SCRIPT_STYLE_HINT)
    return "\n\n".join(parts)


def build_freezone_character_story_script_task(
    *,
    image_count: int,
    prompt: str,
    source_text: str = "",
    character_refs: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """构建「角色参考图生成分镜脚本」任务。

    这一路没有参考视频，只有角色参考图：剧情来自用户提示词（和可选的源剧本），
    图片只负责钉死角色长相，所以 ``keyframe_index`` 一律为 0。
    """
    parts = [
        f"下面按顺序给你 {image_count} 张角色参考图，请据此生成一张完整的故事脚本表。",
        "这是角色参考模式，没有参考视频：角色1 / 角色2 的外貌、服饰、年代、气质"
        "必须照着对应的角色参考图写，不要描述图里没有的人。",
        "剧情本身来自用户要求（以及可选的源剧本），不要凭空照搬系统提示词里的示例剧情。",
        "每一行的 keyframe_index 一律填 0：本模式没有关键帧可以回填。",
        *_STORY_SCRIPT_COMMON_RULES,
    ]
    if prompt.strip():
        parts.append(f"用户要求：\n{prompt.strip()}")
    else:
        parts.append("用户没有额外要求，请围绕这些角色自行编排一段结构完整的短剧。")
    character_block = _character_ref_block(character_refs)
    if character_block:
        parts.append(character_block)
    parts.append(_STORY_SCRIPT_STYLE_HINT)
    if source_text.strip():
        parts.append(f"源剧本内容：\n{source_text.strip()}")
    return "\n\n".join(parts)


async def generate_freezone_story_script(
    *,
    source_text: str,
    prompt: str = "",
    model: str | None = None,
    character_refs: Sequence[Mapping[str, Any]] | None = None,
    egress_context: TrustedEgressContext | None = None,
):
    """执行故事脚本生成（文本 / 角色图模式）。"""
    if not source_text or not source_text.strip():
        raise ValueError("source_text is required")

    task = build_freezone_story_script_task(
        source_text=source_text,
        prompt=prompt,
        character_refs=character_refs,
    )
    from novelvideo.model_gateway_runtime import model_gateway_request_scope

    with model_gateway_request_scope(egress_context):
        response = await get_freezone_story_script_agent(model).run(task)
    return response.output


def create_freezone_video_story_script_agent(model: str | None = None) -> Agent:
    """创建「视频参考生成分镜脚本」的视觉 Agent。

    走 ``FREEZONE_VISION_MODEL``（``DC-freezone-vision-LLM``）而不是纯文本的
    story-script 别名 —— 带图请求只有视觉渠道能接。

    [aipdd fork delta] ``model`` 为用户显式选定的视觉模型（工具页「视频反推
    剧本」），留空走 env 默认链；显式选择时不让 env 默认反向覆盖。
    """
    from novelvideo.api.schemas import FreezoneStoryScriptGenerateData
    from novelvideo.config import (
        get_newapi_structured_output_model_settings,
        get_newapi_text_pydantic_model,
    )
    from novelvideo.official_defaults import DEFAULT_FREEZONE_VISION_MODEL

    model_text = str(model or "").strip()
    return Agent(
        get_newapi_text_pydantic_model(
            "FREEZONE_VISION_MODEL",
            DEFAULT_FREEZONE_VISION_MODEL,
            timeout_seconds_override=300.0,
            capability="vision.analyze",
            # [aipdd fork delta] 用户显式选了视觉模型时不让 env 默认反向覆盖
            # （与文本 writer / 脚本节点同口径）。
            model_name_override=model_text or None,
            # [aipdd fork delta] 带帧/角色图的多模态请求，隔离到
            # VISION_MODEL_FALLBACK，避免被 TEXT_MODEL_FALLBACK 的纯文本模型
            # 打成 400「当前模型不支持请求所需的图片能力」。
            fallback_env="VISION_MODEL_FALLBACK",
        ),
        system_prompt=freezone_story_script_system_prompt(with_vision=True),
        model_settings=get_newapi_structured_output_model_settings(),
        output_type=FreezoneStoryScriptGenerateData,
        output_retries=model_gateway_output_retries(3),
        name="Freezone Video Story Script Generator",
    )


def get_freezone_video_story_script_agent(model: str | None = None) -> Agent:
    """获取视频分镜脚本 Agent；按模型分键缓存（[aipdd fork delta]：原来是全局
    单例，用户自选视觉模型会命中上一个模型的缓存 Agent）。组织上下文每次新建，
    与翻译/脚本节点出网口径一致。"""
    global _video_story_script_agent
    _refresh_prompt_agents()
    model_text = str(model or "").strip()
    context = current_model_gateway_context()
    if context is not None and context.is_organization:
        return create_freezone_video_story_script_agent(model_text or None)
    agent_key = model_text
    cached = _video_story_script_agents.get(agent_key)
    if cached is not None:
        return cached
    agent = create_freezone_video_story_script_agent(model_text or None)
    _video_story_script_agents[agent_key] = agent
    if not agent_key:
        _video_story_script_agent = agent
    return agent


async def generate_freezone_story_script_with_vision(
    *,
    frame_paths: Sequence[str | Path] | None = None,
    character_image_paths: Sequence[str | Path] | None = None,
    source_text: str = "",
    prompt: str = "",
    duration_sec: float | None = None,
    character_refs: Sequence[Mapping[str, Any]] | None = None,
    egress_context: TrustedEgressContext | None = None,
    model: str | None = None,
):
    """带图的分镜脚本生成：视频关键帧 / 角色参考图 → 结构化脚本表。

    覆盖两种入口：

    - 「视频参考生成分镜脚本」：``frame_paths`` 是抽出来的关键帧，走视频拆解任务书。
    - 「角色生成分镜脚本」：只有 ``character_image_paths``，走角色参考任务书 ——
      剧情来自 ``prompt``（和可选的 ``source_text``），角色图只负责钉死角色长相。
      这一路不要求 ``source_text``：前端挂了素材时只会发提示词。
    """
    from pydantic_ai import BinaryContent

    from novelvideo.freezone.vision_gateway import load_compact_vision_inputs

    frames = [Path(path) for path in (frame_paths or []) if Path(path).exists()]
    character_images = [
        Path(path) for path in (character_image_paths or []) if Path(path).exists()
    ]
    if not frames and not character_images:
        raise ValueError(
            "vision story script requires at least one keyframe or character image"
        )

    if frames:
        task = build_freezone_video_story_script_task(
            frame_count=len(frames),
            prompt=prompt,
            duration_sec=duration_sec,
            character_refs=character_refs,
        )
    else:
        # 只有角色图：剧情从提示词 / 可选源剧本来，图片只钉角色长相。
        # 这里不能要求 source_text —— 前端在挂了素材时就只发提示词。
        task = build_freezone_character_story_script_task(
            image_count=len(character_images),
            prompt=prompt,
            source_text=source_text,
            character_refs=character_refs,
        )

    vision_inputs = await load_compact_vision_inputs((*frames, *character_images))
    attachments: list[Any] = [
        BinaryContent(data=image.data, media_type=image.media_type)
        for image in vision_inputs
    ]
    from novelvideo.model_gateway_runtime import model_gateway_request_scope

    with model_gateway_request_scope(egress_context):
        response = await get_freezone_video_story_script_agent(model).run(
            [task, *attachments]
        )
    return response.output


def bind_story_script_assets(
    data: Any,
    *,
    frame_urls: Sequence[str] | None = None,
    character_refs: Sequence[Mapping[str, Any]] | None = None,
) -> Any:
    """把关键帧 / 角色参考图的 URL 回填进生成好的脚本行。

    模型只负责写角色名和 ``keyframe_index``，素材 URL 一律由这里补齐 ——
    这样模型没有机会编造出 404 的链接（issue #207 里角色图列恒为空的另一半原因）。
    """
    frames = [url for url in (frame_urls or []) if url]
    by_name: dict[str, str] = {}
    for ref in character_refs or []:
        name = str(ref.get("name") or "").strip()
        image_url = str(ref.get("image_url") or "").strip()
        if name and image_url:
            by_name[name.casefold()] = image_url
    ordered_images = [
        str(ref.get("image_url") or "").strip()
        for ref in character_refs or []
        if str(ref.get("image_url") or "").strip()
    ]

    def _match(name: str) -> str:
        clean = str(name or "").strip()
        if not clean:
            return ""
        folded = clean.casefold()
        if folded in by_name:
            return by_name[folded]
        # 模型常把角色名写成 `沈昭昭_现代` 这类带状态后缀的稳定 ID，
        # 精确匹配不到时退到包含匹配，仍匹配不到才放弃。
        for candidate, url in by_name.items():
            if candidate in folded or folded in candidate:
                return url
        return ""

    for index, row in enumerate(getattr(data, "rows", []) or []):
        keyframe_index = int(getattr(row, "keyframe_index", 0) or 0)
        if not (1 <= keyframe_index <= len(frames)):
            # 模型没给或给错序号时退回按行号顺序取帧，保证参考列不至于整列为空。
            keyframe_index = index + 1 if index < len(frames) else 0
        row.reference = frames[keyframe_index - 1] if keyframe_index else ""
        row.keyframe_index = keyframe_index

        row.character_image_1 = _match(getattr(row, "character_1", ""))
        row.character_image_2 = _match(getattr(row, "character_2", ""))
        # 只有一张角色图、且模型没写出可匹配的角色名时，直接绑定唯一那张，
        # 否则「角色生成分镜脚本」在模型改写角色名后又会退化成空列。
        if not row.character_image_1 and len(ordered_images) == 1:
            row.character_image_1 = ordered_images[0]

    return data
