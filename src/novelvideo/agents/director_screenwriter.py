"""[aipdd fork delta] 导演编剧：故事优化与故事转剧本。

提示词来自提示词库 director 组（设置 → 提示词库可覆盖）：
- director.story_to_script.system：故事 → 逐行剧本
- director.story_optimization.system：故事诊断与优化
- director.style_library.reference：9 种导演风格参考（随系统提示词注入）

故事转剧本的输出与内容改写（content_rewriter）同构：一行一 beat，
对白行 `角色名 + 说话方式 + 冒号 + 中文引号原句`，供 literal 工作流下游直接消费。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator
from pydantic_ai import Agent

from novelvideo.config import (
    get_newapi_structured_output_model_settings,
    get_newapi_text_pydantic_model,
)
from novelvideo.model_gateway_runtime import model_gateway_output_retries
from novelvideo.catalog.prompt_library import get_prompt_text, render_prompt

STORY_STYLE_AUTO = ""

# 与 director.style_library.reference 中的条目标识一一对应。
STORY_STYLE_OPTIONS: tuple[tuple[str, str], ...] = (
    (STORY_STYLE_AUTO, "自动推荐"),
    ("beat_punch", "卡点爽感流"),
    ("spectacle", "视听奇观流"),
    ("suspense", "悬疑压迫流"),
    ("emotional", "情绪共振流"),
    ("prestige", "权谋克制流"),
    ("realism", "都市写实流"),
    ("comedy", "轻喜沙雕流"),
    ("info_pacing", "信息解说流"),
    ("auteur", "高级文艺流"),
)

_STORY_STYLE_LABELS = dict(STORY_STYLE_OPTIONS)


def is_valid_story_style(style: str | None) -> bool:
    """空串（自动）与已知风格 key 合法，其余非法。"""
    return (style or "").strip() in _STORY_STYLE_LABELS


def story_style_prompt_value(style: str | None) -> str:
    """把风格 key 转成注入提示词的展示值；未知值退回“自动推荐”。

    注入格式与风格库条目一致（如“悬疑压迫流（suspense）”），便于模型精确对应。
    """
    key = (style or "").strip()
    label = _STORY_STYLE_LABELS.get(key)
    if not key or key == STORY_STYLE_AUTO or not label:
        return "自动推荐"
    return f"{label}（{key}）"


class StoryToScriptOutput(BaseModel):
    """故事转剧本的逐行输出。"""

    lines: list[str] = Field(
        default_factory=list,
        description="逐行剧本，每行供下游 literal 工作流生成一个 beat",
    )

    @model_validator(mode="after")
    def normalize_lines(self) -> "StoryToScriptOutput":
        normalized: list[str] = []
        for line in self.lines:
            text = str(line or "").strip()
            if text:
                normalized.append(text)
        if not normalized:
            raise ValueError("lines 不能为空")
        self.lines = normalized
        return self


class StoryOptimizationOutput(BaseModel):
    """故事优化输出：优化后的故事 + 优化说明。"""

    story: str = Field(default="", description="优化后的完整故事文本")
    notes: list[str] = Field(default_factory=list, description="本次优化动作清单")

    @model_validator(mode="after")
    def normalize_output(self) -> "StoryOptimizationOutput":
        text = str(self.story or "").strip()
        if not text:
            raise ValueError("story 不能为空")
        self.story = text
        self.notes = [
            str(note).strip() for note in self.notes if str(note or "").strip()
        ]
        return self


def _director_agent(system_prompt: str, output_type: type[BaseModel], name: str) -> Agent:
    return Agent(
        get_newapi_text_pydantic_model(
            "DIRECTOR_SCREENWRITER_MODEL",
            capability="text.generate.agent",
        ),
        system_prompt=system_prompt,
        output_type=output_type,
        output_retries=model_gateway_output_retries(3),
        model_settings=get_newapi_structured_output_model_settings(),
        name=name,
    )


async def story_to_script(
    story: str,
    *,
    project_title: str = "",
    episode_title: str = "",
    episode_number: int = 0,
    story_style: str = "",
    protagonist_name: str = "",
    target_beats: int = 18,
    beat_chars_range: tuple[int, int] = (14, 20),
) -> str:
    """把故事改编成逐行剧本（供 literal 下游消费）。"""
    source_text = (story or "").strip()
    if not source_text:
        return ""

    min_chars, max_chars = beat_chars_range
    system_prompt = render_prompt(
        "director.story_to_script.system",
        project_title=project_title or "未命名",
        episode_title=episode_title or "未命名",
        episode_number=episode_number,
        selected_style=story_style_prompt_value(story_style),
        style_library=get_prompt_text("director.style_library.reference"),
        protagonist_name=protagonist_name or "未绑定",
        target_beats=target_beats,
        min_chars=min_chars,
        max_chars=max_chars,
    )
    task = f"""## 故事正文
{source_text}

请输出改编后的逐行剧本。"""

    agent = _director_agent(system_prompt, StoryToScriptOutput, "导演剧本")
    result = await agent.run(task)
    output: StoryToScriptOutput = result.output
    return "\n".join(output.lines)


async def optimize_story(
    story: str,
    *,
    project_title: str = "",
    episode_title: str = "",
    episode_number: int = 0,
    story_style: str = "",
) -> tuple[str, list[str]]:
    """诊断并优化故事，返回（优化后的故事，优化说明清单）。"""
    source_text = (story or "").strip()
    if not source_text:
        return "", []

    system_prompt = render_prompt(
        "director.story_optimization.system",
        project_title=project_title or "未命名",
        episode_title=episode_title or "未命名",
        episode_number=episode_number,
        selected_style=story_style_prompt_value(story_style),
    )
    task = f"""## 故事原文
{source_text}

请输出优化后的故事与优化说明。"""

    agent = _director_agent(system_prompt, StoryOptimizationOutput, "故事优化师")
    result = await agent.run(task)
    output: StoryOptimizationOutput = result.output
    return output.story, output.notes


# ──────────────────────────────────────────────────────────────────────────────
# 项目级「故事」：整本故事 → 分集大纲 → 逐集剧本 [aipdd fork delta]
# L1 故事栏目「生成剧本」链路：先拆 N 集施工大纲，再逐集改编标准剧本，
# 产物写入 uploads/ 剧本库供「剧本」模块继续消费。
# ──────────────────────────────────────────────────────────────────────────────


class EpisodePlanItem(BaseModel):
    """分集大纲中的单集规划。"""

    number: int = Field(default=0, description="集号，从 1 连续")
    title: str = Field(default="", description="本集标题（4-12 字）")
    brief: str = Field(
        default="",
        description="本集施工大纲：事件链 + 关键场景 + 关键对白素材 + 本集落锤",
    )
    hook: str = Field(default="", description="结尾钩子一句话")

    @model_validator(mode="after")
    def normalize_item(self) -> "EpisodePlanItem":
        self.title = str(self.title or "").strip()
        self.brief = str(self.brief or "").strip()
        self.hook = str(self.hook or "").strip()
        if not self.brief:
            raise ValueError("brief 不能为空")
        return self


class NovelEpisodePlanOutput(BaseModel):
    """整本故事的分集大纲输出。"""

    protagonist: str = Field(default="", description="主角名称")
    logline: str = Field(default="", description="12 字以内的主爽点")
    episodes: list[EpisodePlanItem] = Field(
        default_factory=list, description="按顺序排列的分集规划列表"
    )

    @model_validator(mode="after")
    def normalize_episodes(self) -> "NovelEpisodePlanOutput":
        items = [item for item in self.episodes if item.brief]
        if not items:
            raise ValueError("episodes 不能为空")
        # 重编号，保证从 1 连续，与请求集数解耦。
        for index, item in enumerate(items, start=1):
            item.number = index
        self.episodes = items
        self.protagonist = str(self.protagonist or "").strip()
        self.logline = str(self.logline or "").strip()
        return self


class NovelEpisodeScreenplayOutput(BaseModel):
    """单集剧本输出。"""

    text: str = Field(default="", description="本集剧本正文（含集头、场次头与逐行内容）")

    @model_validator(mode="after")
    def normalize_text(self) -> "NovelEpisodeScreenplayOutput":
        text = str(self.text or "").strip()
        if not text:
            raise ValueError("text 不能为空")
        self.text = text
        return self


async def plan_novel_episodes(
    novel: str,
    *,
    project_title: str = "",
    episode_count: int = 10,
    story_style: str = "",
) -> NovelEpisodePlanOutput:
    """把整本故事拆成分集大纲（施工图）。"""
    source_text = (novel or "").strip()
    if not source_text:
        raise ValueError("故事内容为空，无法生成分集大纲")

    system_prompt = render_prompt(
        "director.novel_episode_plan.system",
        project_title=project_title or "未命名",
        episode_count=episode_count,
        selected_style=story_style_prompt_value(story_style),
    )
    task = f"""## 故事全文
{source_text}

请输出 {episode_count} 集的分集大纲。"""

    agent = _director_agent(system_prompt, NovelEpisodePlanOutput, "分集结构规划师")
    result = await agent.run(task)
    output: NovelEpisodePlanOutput = result.output
    return output


async def write_novel_episode_screenplay(
    *,
    project_title: str = "",
    episode_number: int,
    episode_count: int,
    episode_title: str = "",
    episode_brief: str = "",
    episode_hook: str = "",
    protagonist_name: str = "",
    story_style: str = "",
    prev_tail: str = "",
) -> str:
    """按分集大纲生成单集标准剧本（带集头 / 场次头 / 逐行内容）。"""
    brief_text = (episode_brief or "").strip()
    if not brief_text:
        raise ValueError("本集大纲为空，无法生成剧本")

    system_prompt = render_prompt(
        "director.novel_episode_screenplay.system",
        project_title=project_title or "未命名",
        episode_number=episode_number,
        episode_count=episode_count,
        episode_title=(episode_title or "").strip() or "未命名",
        episode_brief=brief_text,
        episode_hook=(episode_hook or "").strip() or "（未提供）",
        protagonist_name=protagonist_name or "未绑定",
        selected_style=story_style_prompt_value(story_style),
        style_library=get_prompt_text("director.style_library.reference"),
        prev_tail=(prev_tail or "").strip() or "（本集为第 1 集，无需衔接）",
    )
    task = f"""请输出第 {episode_number} 集的剧本正文。"""

    agent = _director_agent(system_prompt, NovelEpisodeScreenplayOutput, "单集剧本改编")
    result = await agent.run(task)
    output: NovelEpisodeScreenplayOutput = result.output
    return output.text


async def optimize_novel(
    novel: str,
    *,
    project_title: str = "",
    story_style: str = "",
) -> tuple[str, list[str]]:
    """整本故事诊断优化，返回（优化后的故事，优化说明清单）。"""
    source_text = (novel or "").strip()
    if not source_text:
        return "", []

    system_prompt = render_prompt(
        "director.novel_optimization.system",
        project_title=project_title or "未命名",
        selected_style=story_style_prompt_value(story_style),
    )
    task = f"""## 故事全文
{source_text}

请输出优化后的故事与优化说明。"""

    agent = _director_agent(system_prompt, StoryOptimizationOutput, "整本故事优化师")
    result = await agent.run(task)
    output: StoryOptimizationOutput = result.output
    return output.story, output.notes