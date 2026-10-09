"""[aipdd fork delta] 一句话生成短视频（``freezone_video_idea``）的纯逻辑原语。

参考 ViMax idea2video 流水线：故事扩写 → 角色提取 → 角色三视图 → 分场景脚本 →
分镜设计 → 镜头分解 → 机位树 → 帧生成 → 逐镜视频 → 拼接成片。
本模块只放不碰 ffmpeg、不出网的规划与文本逻辑（可单测）：prompt 构建、LLM
输出解析与校验、机位拓扑排序、参考图筛选口径；实际生成在
``task_backend/runners/freezone.py::_run_freezone_video_idea_async``。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

# 角色上限（防御性：每个角色要生成 1-3 张三视图并参与逐帧选参考）。
MAX_IDEA_CHARACTERS = 6
# 场景数上限（每个场景独立跑分镜 → 帧 → 视频全链，成本随场景数线性增长）。
MAX_IDEA_SCENES = 6
DEFAULT_IDEA_SCENES = 3
# 单场景镜头数上限。
MAX_IDEA_SHOTS_PER_SCENE = 10
DEFAULT_IDEA_SHOTS_PER_SCENE = 5
# 单镜生成时长的钳制范围（秒）。
MIN_IDEA_SHOT_SECONDS = 3
MAX_IDEA_SHOT_SECONDS = 10
DEFAULT_IDEA_SHOT_SECONDS = 5
# 总时长口径（工具页唯一入参）：成片目标总时长范围（秒）与「每场景承载片长」
# 推导基线（场景数上限 = ceil(总时长 / IDEA_SCENE_SPAN_SECONDS)）。
MIN_IDEA_TOTAL_SECONDS = 5
MAX_IDEA_TOTAL_SECONDS = 300
IDEA_SCENE_SPAN_SECONDS = 10.0
# 每次选参考图的上限（ViMax 口径：≤8 张）。
MAX_IDEA_REFERENCE_IMAGES = 8
# 帧生成画幅（ViMax 用 1600x900 = 16:9 横幅）。
IDEA_FRAME_ASPECT_RATIO = "16:9"
# 过渡视频里检测「新机位」的画面突变阈值（与抽帧同口径）。
IDEA_TRANSITION_SCENE_THRESHOLD = 0.3
# 相机无父机位时的默认描述（ViMax 里 root 相机的 reason）。
DEFAULT_IDEA_STYLE = "Cinematic, realistic, film-like."


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _strip_code_fence(text: str) -> str:
    """剥离 ``` 包裹（```json / ``` 开头的行成对过滤）。"""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    return "\n".join(
        line for line in stripped.splitlines() if not line.strip().startswith("```")
    ).strip()


def _span_json_candidate(text: str) -> str | None:
    """从混有说明文字的文本里截出 JSON 根对象 / 数组子串。

    模型偶发在 JSON 前后夹带「好的，以下是……」这类说明文字；直接
    ``json.loads`` 会失败。这里按最先出现的根符号（``{`` 优先于同位置自然
    顺序）截取到最后一个闭合符号；找不到闭合（输出被截断）时返回 None，
    由调用方走空结果分支。
    """
    best: tuple[int, str] | None = None
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        end = text.rfind(closer)
        if end > start and (best is None or start < best[0]):
            best = (start, text[start : end + 1])
    return best[1] if best is not None else None


def _load_json(raw_text: str) -> Any:
    """解析模型 JSON 输出；失败返回 None（调用方走空结果分支）。

    依次尝试：原文 → 剥离 code fence → 截取 JSON 子串（容忍前后说明文字）。
    """
    text = str(raw_text or "").strip()
    if not text:
        return None
    fenced = _strip_code_fence(text)
    candidates = [text, fenced]
    span = _span_json_candidate(fenced)
    if span is not None:
        candidates.append(span)
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def clamp_idea_shot_seconds(value: Any) -> float:
    """单镜时长归一化到允许范围。"""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = float(DEFAULT_IDEA_SHOT_SECONDS)
    return float(max(MIN_IDEA_SHOT_SECONDS, min(seconds, MAX_IDEA_SHOT_SECONDS)))


def clamp_idea_total_seconds(value: Any) -> float | None:
    """总时长（秒）归一化；缺省/非法返回 None（回退旧参数口径）。"""
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds <= 0:
        return None
    return float(max(MIN_IDEA_TOTAL_SECONDS, min(seconds, MAX_IDEA_TOTAL_SECONDS)))


def derive_idea_scene_cap(total_seconds: float) -> int:
    """总时长口径下的场景数上限：每场景至少承载约 10 秒片长。"""
    span = math.ceil(float(total_seconds) / IDEA_SCENE_SPAN_SECONDS)
    return int(max(1, min(MAX_IDEA_SCENES, span)))


# --------------------------------------------------------------------------- #
# 数据结构（与 ViMax interfaces 一一对应）
# --------------------------------------------------------------------------- #


@dataclass
class IdeaCharacter:
    """剧本里一个角色（ViMax CharacterInScene 的去 idx 依赖版）。"""

    idx: int
    identifier: str
    is_visible: bool = True
    static_features: str = ""
    dynamic_features: str = ""


@dataclass
class IdeaShotBrief:
    """分镜阶段的镜头简述（ViMax ShotBriefDescription）。"""

    idx: int
    cam_idx: int
    visual_desc: str
    audio_desc: str = ""
    is_last: bool = False
    # [aipdd fork delta] AI 为该镜头规划的在屏时长（秒，3-10）：总时长口径下由
    # 分镜 prompt 产出；缺省 None = 回退 shot_duration 基线。
    duration_seconds: float | None = None


@dataclass
class IdeaShot:
    """镜头分解结果（ViMax ShotDescription 子集）。"""

    idx: int
    cam_idx: int
    visual_desc: str
    audio_desc: str = ""
    ff_desc: str = ""
    lf_desc: str = ""
    motion_desc: str = ""
    variation_type: str = "small"
    ff_vis_char_idxs: list[int] = field(default_factory=list)
    lf_vis_char_idxs: list[int] = field(default_factory=list)
    is_last: bool = False

    @property
    def needs_last_frame(self) -> bool:
        """只有 medium / large 变体才需要生成尾帧（ViMax 口径）。"""
        return self.variation_type in {"medium", "large"}


@dataclass
class IdeaCamera:
    """一个机位（ViMax Camera）：归属镜头 + 父机位依赖。"""

    idx: int
    active_shot_idxs: list[int] = field(default_factory=list)
    parent_cam_idx: int | None = None
    parent_shot_idx: int | None = None
    is_parent_fully_covers_child: bool | None = None
    missing_info: str | None = None


# --------------------------------------------------------------------------- #
# 故事扩写（ViMax Screenwriter.develop_story）
# --------------------------------------------------------------------------- #


def build_idea_story_prompt(*, idea: str, user_requirement: str = "") -> str:
    """一句话创意 → 完整故事（纯文本，语言与输入一致）。"""
    clean_idea = _clean_text(idea)
    requirement = _clean_text(user_requirement)
    return f"""You are a seasoned creative story generation expert. You possess the following core skills:
- Idea Expansion and Conceptualization: The ability to expand a vague idea, a one-line inspiration, or a concept into a fleshed-out, logically coherent story world.
- Story Structure Design: Mastery of classic narrative models like the three-act structure, the hero's journey, etc., enabling you to construct engaging story arcs with a beginning, middle, and end, tailored to the story's genre.
- Character Development: Expertise in creating three-dimensional characters with motivations, flaws, and growth arcs, and designing complex relationships between them.
- Scene Depiction and Pacing: The skill to vividly depict various settings and precisely control the narrative rhythm, allocating detail appropriately based on the required number of scenes.
- Audience Adaptation: The ability to adjust the language style, thematic depth, and content suitability based on the target audience (e.g., children, teenagers, adults).
- Screenplay-Oriented Thinking: When the story is intended for short film or movie adaptation, you can naturally incorporate visual elements (e.g., scene atmosphere, key actions, dialogue) into the narrative, making the story more cinematic and filmable.

Your core task is to generate a complete, engaging story that conforms to the specified requirements, based on the user's provided "Idea" and "Requirements."

The user will provide an idea within <IDEA> and </IDEA> tags and a user requirement within <USER_REQUIREMENT> and </USER_REQUIREMENT> tags.

You must output a well-structured and clearly formatted story document as follows:
- Story Title: An engaging and relevant story name.
- Target Audience & Genre: Start by explicitly restating: "This story is targeted at [User-Specified Audience], in the [User-Specified Genre] genre."
- Story Outline/Summary: Provide a one-paragraph (100-200 words) summary of the entire story, covering the core plot, central conflict, and outcome.
- Main Characters Introduction: Briefly introduce the core characters, including their names, key traits, and motivations.
- Full Story Narrative: narrate the story naturally in paragraphs following the "Introduction - Development - Climax - Conclusion" structure. The narrative should be vivid and detailed, matching the specified genre and target audience.
- The output should begin directly with the story, without any extra words.

Guidelines:
- The language of output should be same as the input.
- Idea-Centric: Keep the user's core idea as the foundation; do not deviate from its essence. If the user's idea is vague, you can use creativity to make reasonable expansions.
- Logical Consistency: Ensure that event progression and character actions within the story have logical motives and internal consistency, avoiding abrupt or contradictory plots.
- Show, Don't Tell: Reveal characters' personalities and emotions through their actions, dialogues, and details, rather than stating them flatly. For example, use "He clenched his fist, nails digging deep into his palm" instead of "He was very angry."
- Originality & Compliance: Generate original content based on the user's idea, avoiding direct plagiarism of well-known existing works. The generated content must be positive, healthy, and comply with general content safety policies.

<IDEA>
{clean_idea}
</IDEA>

<USER_REQUIREMENT>
{requirement}
</USER_REQUIREMENT>
"""


# --------------------------------------------------------------------------- #
# 角色提取（ViMax CharacterExtractor）
# --------------------------------------------------------------------------- #


def build_idea_characters_prompt(
    *, story: str, limit: int = MAX_IDEA_CHARACTERS
) -> str:
    """故事 → 角色清单（JSON）。"""
    clean_story = str(story or "").strip()
    return f"""You are a top-tier movie script analysis expert.

Your task is to analyze the provided story and extract all relevant character information.

You will receive a story enclosed within <STORY> and </STORY>.

Output a single JSON object (no markdown, no explanation) with this schema:
{{
  "characters": [
    {{
      "idx": 0,
      "identifier_in_scene": "Alice",
      "is_visible": true,
      "static_features": "physical appearance and physique that rarely change",
      "dynamic_features": "attire, accessories and key items that may change"
    }}
  ]
}}

Guidelines:
- Ensure that the language of all output values (not include keys) matches that used in the story.
- Group all names referring to the same entity under one character. Select the most appropriate name as the character's identifier. If the person is a real famous person, the real person's name should be retained (e.g., Elon Musk, Bill Gates).
- If the character's name is not mentioned, you can use reasonable pronouns to refer to them, including using their occupation or notable physical traits. For example, "the young woman" or "the barista".
- For background characters, you do not need to consider them as individual characters.
- If a character's traits are not described or only partially outlined, you need to design plausible features based on the context to make their characteristics more complete and detailed, ensuring they are vivid and evocative.
- In static features, describe the character's physical appearance, physique, and other relatively unchanging features. In dynamic features, describe the character's attire, accessories, key items they carry, and other easily changeable features.
- Don't include any information about the character's personality, role, or relationships with others in either static or dynamic features.
- When designing character features, within reasonable limits, different character appearances should be made more distinct from each other.
- The description of characters should be detailed, avoiding the use of abstract terms. Instead, employ descriptions that can be visualized—such as specific clothing colors and concrete physical traits (e.g., large eyes, a high nose bridge).
- If the character is never shown on screen (e.g. a voice or chat-only character), set "is_visible" to false and leave the feature fields empty.
- Output at most {int(limit)} characters, ordered by importance.
- "idx" must start from 0 and increase by 1 for each character.

<STORY>
{clean_story}
</STORY>
"""


def parse_idea_characters(
    raw_text: str, *, limit: int = MAX_IDEA_CHARACTERS
) -> list[IdeaCharacter]:
    """解析角色清单；idx 按输出顺序重编号，保证 0..N-1 连续。"""
    data = _load_json(raw_text)
    if isinstance(data, dict):
        entries = data.get("characters")
    elif isinstance(data, list):
        entries = data
    else:
        entries = None
    if not isinstance(entries, list):
        return []
    characters: list[IdeaCharacter] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        identifier = _clean_text(
            entry.get("identifier_in_scene") or entry.get("identifier")
        )
        if not identifier:
            continue
        characters.append(
            IdeaCharacter(
                idx=len(characters),
                identifier=identifier,
                is_visible=bool(entry.get("is_visible", True)),
                static_features=_clean_text(entry.get("static_features")),
                dynamic_features=_clean_text(entry.get("dynamic_features")),
            )
        )
        if len(characters) >= max(1, int(limit)):
            break
    return characters


# --------------------------------------------------------------------------- #
# 角色三视图（ViMax CharacterPortraitsGenerator）
# --------------------------------------------------------------------------- #


def build_idea_portrait_front_prompt(
    *,
    identifier: str,
    static_features: str,
    dynamic_features: str,
    style: str = DEFAULT_IDEA_STYLE,
) -> str:
    """角色正面全身像（三视图之首，side/back 以它为参考图）。"""
    features = f"(static) {_clean_text(static_features)}; (dynamic) {_clean_text(dynamic_features)}"
    return f"""Generate a full-body, front-view portrait of character {_clean_text(identifier)} based on the following description, with a pure white background. Use a wide 16:9 landscape canvas, not a vertical portrait canvas. The character should be centered in the image, occupying the middle of the wide frame with enough horizontal empty space. Gazing straight ahead. Standing with arms relaxed at sides. Natural expression.
Features: {features}
Style: {_clean_text(style) or DEFAULT_IDEA_STYLE}
"""


def build_idea_portrait_side_prompt(*, identifier: str) -> str:
    """角色侧面全身像（基于正面图生成，面朝左）。"""
    return f"""Generate a full-body, side-view portrait of character {_clean_text(identifier)} based on the provided front-view portrait, with a pure white background. Use a wide 16:9 landscape canvas, not a vertical portrait canvas. The character should be centered in the image, occupying the middle of the wide frame with enough horizontal empty space. Facing left. Standing with arms relaxed at sides.
"""


def build_idea_portrait_back_prompt(*, identifier: str) -> str:
    """角色背面全身像（基于正面图生成，看不到面部）。"""
    return f"""Generate a full-body, back-view portrait of character {_clean_text(identifier)} based on the provided front-view portrait, with a pure white background. Use a wide 16:9 landscape canvas, not a vertical portrait canvas. The character should be centered in the image, occupying the middle of the wide frame with enough horizontal empty space. No facial features should be visible.
"""


def portrait_description(*, identifier: str, view: str) -> str:
    """三视图在参考图列表里的描述文本（ViMax registry 口径）。"""
    clean = _clean_text(identifier)
    return f"A {view} view portrait of {clean}."


# --------------------------------------------------------------------------- #
# 分场景脚本（ViMax Screenwriter.write_script_based_on_story）
# --------------------------------------------------------------------------- #


def build_idea_scene_scripts_prompt(
    *,
    story: str,
    user_requirement: str = "",
    max_scenes: int = DEFAULT_IDEA_SCENES,
    total_duration_seconds: float | None = None,
) -> str:
    """故事 → 按场景切分的脚本列表（JSON 数组，每项一场）。"""
    clean_story = str(story or "").strip()
    requirement = _clean_text(user_requirement)
    scenes = max(1, int(max_scenes))
    total_seconds = clamp_idea_total_seconds(total_duration_seconds)
    duration_guideline = ""
    if total_seconds is not None:
        duration_guideline = (
            f"- Total Screen Time: The complete video targets about "
            f"{total_seconds:.0f} seconds of screen time in total; keep the story "
            f"scope and pacing suitable for that length.\n"
        )
    return f"""You are a professional AI script adaptation assistant skilled in adapting stories into scripts. You possess the following skills:
- Story Analysis Skills: Ability to deeply understand the story content, identify key plot points, character arcs, and themes.
- Scene Segmentation Skills: Ability to break down the story into logical scene units based on continuity of time and location.
- Script Writing Skills: Familiarity with script formats (e.g., for short films or movies), capable of crafting vivid dialogue, action descriptions, and stage directions.
- Adaptive Adjustment Skills: Ability to adjust the script's style, language, and content based on user requirements.
- Creative Enhancement Skills: Ability to appropriately add dramatic elements to enhance the script's appeal while remaining faithful to the original story.

Your task is to adapt the user's input story, along with optional requirements, into a script divided by scenes. The output should be a list of scripts, each representing a complete script for one scene. Each scene must be a continuous dramatic action unit occurring at the same time and location.

You will receive a story within <STORY> and </STORY> tags and a user requirement within <USER_REQUIREMENT> and </USER_REQUIREMENT> tags.

Output a single JSON object (no markdown, no explanation) with this schema:
{{
  "script": [
    "Full script text of scene 1 ...",
    "Full script text of scene 2 ..."
  ]
}}

Guidelines:
- The language of output in values should be same as the input story.
- Scene Division Principles: Each scene must be based on the same time and location. Start a new scene when the time or location changes. Divide the story into at most {scenes} scenes; fewer scenes are fine when the story is compact, but never more than {scenes}.
{duration_guideline}- Script Formatting Standards: Use standard script formatting: Scene headings, character names, dialogue, and action descriptions.
- Coherence and Fluidity: Ensure natural transitions between scenes and overall story flow. Avoid abrupt plot jumps.
- Visual Enhancement Principles: All descriptions must be "filmable". Use concrete actions instead of abstract emotions (e.g., "He turns away to avoid eye contact" instead of "He feels ashamed"). Describe rich environmental details including lighting, props, weather, etc., to enhance the atmosphere. Visualize character performances such as expressing internal states through facial expressions, gestures, and movements (e.g., "She bites her lip, her hands trembling" to imply nervousness).
- Consistency: Ensure dialogue and actions align with the original story's intent, without deviating from the core plot.

<STORY>
{clean_story}
</STORY>

<USER_REQUIREMENT>
{requirement}
</USER_REQUIREMENT>
"""


def parse_idea_scene_scripts(
    raw_text: str, *, max_scenes: int = MAX_IDEA_SCENES
) -> list[str]:
    """解析场景脚本列表；非空、按上限截断。"""
    data = _load_json(raw_text)
    if isinstance(data, dict):
        # 键名容错：模型偶发把 script 写成 scripts/scenes；列表值为空时继续
        # 尝试下一个别名，避免因单一键缺失整链失败。
        entries = data.get("script") or data.get("scripts") or data.get("scenes")
    elif isinstance(data, list):
        entries = data
    else:
        entries = None
    if not isinstance(entries, list):
        return []
    scenes = [_clean_text(item) for item in entries]
    scenes = [scene for scene in scenes if scene]
    return scenes[: max(1, int(max_scenes))]


# --------------------------------------------------------------------------- #
# 分镜设计（ViMax StoryboardArtist.design_storyboard）
# --------------------------------------------------------------------------- #


def format_idea_characters_block(characters: list[IdeaCharacter]) -> str:
    """角色清单块（storyboard / decompose prompt 共用）。"""
    lines: list[str] = []
    for index, character in enumerate(characters):
        lines.append(f"Character {index}: {character.identifier}")
        lines.append(f"static features: {character.static_features}")
        lines.append(f"dynamic features: {character.dynamic_features}")
    return "\n".join(lines)


def build_idea_storyboard_prompt(
    *,
    script: str,
    characters: list[IdeaCharacter],
    user_requirement: str = "",
    max_shots: int = DEFAULT_IDEA_SHOTS_PER_SCENE,
    target_duration_seconds: float | None = None,
) -> str:
    """单场景脚本 → 镜头简述列表（JSON）。"""
    script_str = str(script or "").strip()
    characters_str = format_idea_characters_block(characters)
    requirement = _clean_text(user_requirement)
    shots = max(1, int(max_shots))
    # [aipdd fork delta] 总时长口径：该场景的片长预算，AI 据此自定镜头数与
    # 每镜 duration_seconds（3-10s），使场景总长逼近预算。
    target_seconds: float | None = None
    if target_duration_seconds is not None:
        try:
            candidate = float(target_duration_seconds)
        except (TypeError, ValueError):
            candidate = 0.0
        if math.isfinite(candidate) and candidate > 0:
            target_seconds = max(float(MIN_IDEA_SHOT_SECONDS), candidate)
    duration_guideline = ""
    duration_schema_line = ""
    if target_seconds is not None:
        duration_guideline = (
            f"- Target Duration: This scene should yield about {target_seconds:.1f} "
            f"seconds of final video in total. Decide how many shots to use and set "
            f"each shot's \"duration_seconds\" (3-10 seconds each) so their sum stays "
            f"close to {target_seconds:.1f} seconds.\n"
        )
        duration_schema_line = '\n      "duration_seconds": 5,'
    return f"""You are a professional storyboard artist with the following core skills:
- Script Analysis: Ability to quickly interpret a script's text, identifying the setting, character actions, dialogue, emotions, and narrative pacing.
- Visualization: Expertise in translating written descriptions into visual frames, including composition, lighting, and spatial arrangement.
- Storyboarding: Proficiency in cinematic language, such as shot types (e.g., close-up, medium shot, wide shot), camera angles (e.g., high angle, eye-level), camera movements (e.g., zoom, pan), and transitions.
- Narrative Continuity: Ability to ensure the storyboard sequence is logically smooth, highlights key plot points, and maintains emotional consistency.
- Technical Knowledge: Understanding of basic storyboard formats and industry standards, such as using numbered shots and concise descriptions.

Your task is to design a complete storyboard based on a user-provided script (which contains only one scene). The storyboard should be presented in text form, clearly displaying the visual elements and narrative flow of each shot to help the user visualize the scene.

The user will provide the following input:
- Script: A complete scene script containing dialogue, action descriptions, and scene settings, enclosed within <SCRIPT> and </SCRIPT>.
- Characters List: A list describing basic information for each character, enclosed within <CHARACTERS> and </CHARACTERS>.
- User requirement (optional), enclosed within <USER_REQUIREMENT> and </USER_REQUIREMENT>.

Output a single JSON object (no markdown, no explanation) with this schema:
{{
  "storyboard": [
    {{
      "idx": 0,
      "cam_idx": 0,{duration_schema_line}
      "visual_desc": "A vivid and detailed visual description of the shot ...",
      "audio_desc": "[Sound Effect] ... / [Speaker] Alice (Happy): Hello"
    }}
  ]
}}

Guidelines:
- Ensure all output values (except keys) match the language used in the script.
- Each shot must have a clear narrative purpose—such as establishing the setting, showing character relationships, or highlighting reactions.
- Use cinematic language deliberately: close-ups for emotion, wide shots for context, and varied angles to direct audience attention.
- When designing a new shot, first consider whether it can be filmed using an existing camera position. Introduce a new one only if the shot size, angle, and focus differ significantly. If the camera undergoes significant movement, it cannot be used thereafter.
- Keep character names in visual descriptions and speaker fields consistent with the character list. In visual descriptions, enclose names in angle brackets (e.g., <Alice>), but not in dialogue or speaker fields.
- When describing visual elements, it is necessary to indicate the position of the element within the frame. For example, Character A is on the left side of the frame, facing toward the right, with a table in front of him. Ensure that invisible elements are not included. For instance, do not describe someone behind a closed door if they cannot be seen.
- Avoid unsafe content (violence, discrimination, etc.) in visual descriptions. Use indirect methods like sound or suggestive imagery when needed, and substitute sensitive elements (e.g., ketchup for blood).
- Assign at most one dialogue line per character per shot. Each line of dialogue should correspond to a shot.
- Each shot requires an independent description without reference to each other.
- When the shot focuses on a character, describe which specific body part the focus is on.
- When describing a character, it is necessary to indicate the direction they are facing.
- Design at most {shots} shots for this scene; fewer shots are fine when the scene is short.
{duration_guideline}- "idx" must start from 0 and increase by 1 for each shot. "cam_idx" is the index of the camera position used by that shot; reuse the same camera index whenever possible.
- The first shot must establish the overall scene environment, using the widest possible shot.

<SCRIPT>
{script_str}
</SCRIPT>

<CHARACTERS>
{characters_str}
</CHARACTERS>

<USER_REQUIREMENT>
{requirement}
</USER_REQUIREMENT>
"""


def parse_idea_storyboard(
    raw_text: str, *, max_shots: int = MAX_IDEA_SHOTS_PER_SCENE
) -> list[IdeaShotBrief]:
    """解析镜头简述列表；idx 重编号、尾镜标记 is_last、时长归一。"""
    data = _load_json(raw_text)
    if isinstance(data, dict):
        entries = data.get("storyboard")
    elif isinstance(data, list):
        entries = data
    else:
        entries = None
    if not isinstance(entries, list):
        return []
    briefs: list[IdeaShotBrief] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        visual = _clean_text(entry.get("visual_desc"))
        if not visual:
            continue
        raw_duration = entry.get("duration_seconds")
        duration_seconds: float | None = None
        if raw_duration is not None and _clean_text(raw_duration):
            duration_seconds = clamp_idea_shot_seconds(raw_duration)
        briefs.append(
            IdeaShotBrief(
                idx=len(briefs),
                cam_idx=max(0, _to_int(entry.get("cam_idx"), default=0)),
                visual_desc=visual,
                audio_desc=_clean_text(entry.get("audio_desc")),
                is_last=bool(entry.get("is_last", False)),
                duration_seconds=duration_seconds,
            )
        )
        if len(briefs) >= max(1, int(max_shots)):
            break
    if briefs:
        briefs[-1].is_last = True
    return briefs


# --------------------------------------------------------------------------- #
# 镜头分解（ViMax StoryboardArtist.decompose_visual_description）
# --------------------------------------------------------------------------- #


def build_idea_decompose_prompt(
    *, visual_desc: str, characters: list[IdeaCharacter]
) -> str:
    """镜头画面描述 → ff/lf/motion/variation 分解（JSON）。"""
    clean_desc = str(visual_desc or "").strip()
    characters_str = "\n".join(
        f"{character.identifier}: (static) {character.static_features}; "
        f"(dynamic) {character.dynamic_features}"
        for character in characters
    )
    return f"""You are a professional visual text analyst, proficient in cinematic language and shot narration. Your expertise lies in deconstructing a comprehensive shot description accurately into three core components: the static first frame, the static last frame, and the dynamic motion that connects them.

Your task is to dissect and rewrite a user-provided visual text description of a shot strictly and insightfully into three distinct parts:
- First Frame Description: Describe the static image at the very beginning of the shot. Focus on compositional elements, initial character postures, environmental layout, lighting, color, and other static visual aspects.
- Last Frame Description: Describe the static image at the very end of the shot. Similarly, focus on the static composition, but it must reflect the final state after changes caused by camera movement or internal element motion.
- Motion Description: Describe all movements that occur between the first frame and the last frame. This includes camera movement (e.g., static, push-in, pull-out, pan, track, follow, tilt, etc.) and movement of elements within the shot. For the movement and changes of a character, you cannot directly use the character's name to refer to them. Instead, refer to the character by their external features, especially noticeable ones like clothing characteristics.

The description is enclosed within <VISUAL_DESC> and </VISUAL_DESC>.
The character list is enclosed within <CHARACTERS> and </CHARACTERS>.

Output a single JSON object (no markdown, no explanation) with this schema:
{{
  "ff_desc": "A detailed description of the first frame of the shot ...",
  "ff_vis_char_idxs": [0],
  "lf_desc": "A detailed description of the last frame of the shot ...",
  "lf_vis_char_idxs": [0],
  "motion_desc": "Static camera. ...",
  "variation_type": "large | medium | small",
  "variation_reason": "The reason for the variation type of the shot."
}}

Guidelines:
- Ensure all output values (except keys) match the language used in the script.
- Ensure the first and last frame descriptions are pure "snapshots," containing no ongoing actions (e.g., "He is about to stand up" is unacceptable; it should be "He is sitting on the chair, leaning slightly forward").
- In the motion description, you must clearly distinguish between camera movement and on-screen movement. Use professional cinematic terminology (e.g., dolly shot, pan, zoom, etc.) as precisely as possible to describe camera movement.
- In the motion description, you cannot directly use character names to refer to characters; instead, refer to them by their visible characteristics (e.g., "Alice (short hair, wearing a green dress) is walking").
- The last frame description must be logically consistent with the first frame description and the motion description. All actions described in the motion section should be reflected in the static image of the last frame.
- If the input description is ambiguous about certain details, you may make reasonable inferences and additions based on the context. However, core elements must strictly adhere to the input text.
- Use accurate, concise, and professional descriptive language. Avoid overly literary rhetoric such as metaphors or emotional flourishes.
- The first and last frame descriptions should include details such as shot type, angle, composition, etc.
- Below are the three types of variation within a shot (not between two shots):
(1) 'large' cases typically involve the exaggerated transition shots which means a significant change in the composition and focus, such as smoothly changing from a wide shot to a close-up. It is usually accompanied by significant camera movement.
(2) 'medium' cases often involve the introduction of new characters and a character turns from the back to face the front (facing the camera).
(3) 'small' cases usually involve minor changes, such as expression changes, movement and pose changes of existing characters (e.g., walking, sitting down, standing up), moderate camera movements (e.g., pan, tilt, track).
- "ff_vis_char_idxs" / "lf_vis_char_idxs" list the indices of characters visible in the first / last frame, corresponding to the character list order (0-based). Use an empty list when no character is visible. All indices must be within 0..{max(0, len(characters) - 1)}.

<VISUAL_DESC>
{clean_desc}
</VISUAL_DESC>

<CHARACTERS>
{characters_str}
</CHARACTERS>
"""


def _filter_char_idxs(value: Any, count: int) -> list[int]:
    """过滤越界/重复的角色索引（ViMax 是拒绝重试；这里保序过滤以避免整链失败）。"""
    if not isinstance(value, list) or count <= 0:
        return []
    seen: set[int] = set()
    result: list[int] = []
    for item in value:
        index = _to_int(item, default=-1)
        if 0 <= index < count and index not in seen:
            seen.add(index)
            result.append(index)
    return result


def parse_idea_decompose(
    raw_text: str, brief: IdeaShotBrief, *, character_count: int
) -> IdeaShot | None:
    """解析镜头分解输出；关键字段缺失时返回 None（调用方可重试一次）。"""
    data = _load_json(raw_text)
    if not isinstance(data, dict):
        return None
    ff_desc = _clean_text(data.get("ff_desc"))
    lf_desc = _clean_text(data.get("lf_desc"))
    motion_desc = _clean_text(data.get("motion_desc"))
    if not ff_desc or not motion_desc:
        return None
    variation = _clean_text(data.get("variation_type")).lower()
    if variation not in {"small", "medium", "large"}:
        variation = "small"
    if not lf_desc:
        lf_desc = ff_desc
    return IdeaShot(
        idx=brief.idx,
        cam_idx=brief.cam_idx,
        visual_desc=brief.visual_desc,
        audio_desc=brief.audio_desc,
        ff_desc=ff_desc,
        lf_desc=lf_desc,
        motion_desc=motion_desc,
        variation_type=variation,
        ff_vis_char_idxs=_filter_char_idxs(
            data.get("ff_vis_char_idxs"), character_count
        ),
        lf_vis_char_idxs=_filter_char_idxs(
            data.get("lf_vis_char_idxs"), character_count
        ),
        is_last=brief.is_last,
    )


# --------------------------------------------------------------------------- #
# 机位树（ViMax CameraImageGenerator.construct_camera_tree）
# --------------------------------------------------------------------------- #


def group_shots_into_cameras(shots: list[IdeaShot]) -> list[IdeaCamera]:
    """按 cam_idx 把镜头归组到机位（镜头顺序保持）。"""
    cameras_by_idx: dict[int, IdeaCamera] = {}
    for shot in shots:
        camera = cameras_by_idx.get(shot.cam_idx)
        if camera is None:
            camera = IdeaCamera(idx=shot.cam_idx, active_shot_idxs=[])
            cameras_by_idx[shot.cam_idx] = camera
        camera.active_shot_idxs.append(shot.idx)
    return list(cameras_by_idx.values())


def build_idea_camera_tree_prompt(
    *, cameras: list[IdeaCamera], shots: list[IdeaShot]
) -> str:
    """机位序列 → 机位树（JSON，parent 依赖）。"""
    shot_by_idx = {shot.idx: shot for shot in shots}
    lines: list[str] = ["<CAMERA_SEQ>"]
    for camera in cameras:
        lines.append(f"<CAMERA_{camera.idx}>")
        for shot_idx in camera.active_shot_idxs:
            shot = shot_by_idx.get(shot_idx)
            if shot is None:
                continue
            lines.append(f"Shot {shot_idx}: {shot.visual_desc}")
        lines.append(f"</CAMERA_{camera.idx}>")
    lines.append("</CAMERA_SEQ>")
    camera_seq_str = "\n".join(lines)
    return f"""You are a professional video editing expert specializing in multi-camera shot analysis and scene structure modeling. You have deep knowledge of cinematic language, enabling you to understand shot sizes (e.g., wide shot, medium shot, close-up) and content inclusion relationships. You can infer hierarchical structures between camera positions based on corresponding shot descriptions.

Your task is to analyze the input camera position data to construct a "camera position tree". This tree structure represents a relationship where a parent camera's content encompasses that of a child camera. Specifically, you need to identify the parent camera for each camera position (if one exists) and determine the dependent shot indices (i.e., the specific shots within the parent camera's footage that contain the child camera's content). If a camera position has no parent, output null.

The input is a sequence of cameras. The sequence will be enclosed within <CAMERA_SEQ> and </CAMERA_SEQ>.
Each camera contains a sequence of shots filmed by the camera, which will be enclosed within <CAMERA_N> and </CAMERA_N>, where N is the index of the camera.

Output a single JSON object (no markdown, no explanation) with this schema:
{{
  "camera_parent_items": [
    {{
      "parent_cam_idx": 0,
      "parent_shot_idx": 0,
      "reason": "The parent shot's field of view covers the child shot's field of view (from medium shot to close-up).",
      "is_parent_fully_covers_child": false,
      "missing_info": "The frontal view of Alice."
    }},
    null
  ]
}}
- "camera_parent_items" length must equal the number of cameras ({len(cameras)}); use null for a camera without a parent (e.g., the root camera).

Guidelines:
- The language of all output values (not include keys) should be consistent with the language of the input.
- Content Inclusion Check: The parent camera should as fully as possible contain the child camera's content in certain shots (e.g., a parent medium two-shot encompasses a child over-the-shoulder reverse shot). Analyze shot descriptions by comparing keywords (e.g., characters, actions, setting) to ensure the parent shot's field of view covers the child shot's.
- Transition Smoothness Priority: Larger shot size as parent camera is preferred, such as Wide Shot -> Medium Shot or Medium Shot -> Close-up. The shot sizes of adjacent parent and child nodes should be as similar as possible. A direct transition from a long shot to a close-up is not allowed unless absolutely necessary.
- Temporal Proximity: Each camera is described by its corresponding first shot, and the parent camera is located based on the description of the first shot. The shot index of the parent camera should be as close as possible to the first shot index of the child camera.
- Logical Consistency: The camera tree should be acyclic, avoid circular dependencies. If a camera is contained by multiple potential parents, select the best match (based on shot size and content). If there is no suitable parent camera, output null.
- When a broader perspective is not available, choose the shot with the largest overlapping field of view as the parent (the one with the most information overlap), or a shot can also serve as the parent of a reverse shot. When two cameras can be the parent of each other, choose the one with the smaller index as the parent of the camera with the larger index.
- Only one camera can exist without a parent.
- When describing the elements lost in a shot, carefully compare the details between the parent shot and the child shot. For example, the parent shot is a medium shot of Character A and Character B facing each other (both in profile to the camera), while the child shot is a close-up of Character A (with Character A facing the camera directly). In this case, the child shot lacks the frontal view information of Character A.
- The first camera must be the root of the camera tree.
- "parent_shot_idx" must be one of the shot indices that the parent camera can film (a shot index appearing under that camera in the input).

{camera_seq_str}
"""


def apply_idea_camera_tree(
    raw_text: str, cameras: list[IdeaCamera], shots: list[IdeaShot]
) -> bool:
    """把机位树 LLM 输出赋值到相机对象上；任何校验失败返回 False（全部保持 root）。

    校验口径与 ViMax 一致：输出长度匹配、parent 引用有效、禁止自环、禁止循环。
    """
    data = _load_json(raw_text)
    if not isinstance(data, dict):
        return False
    entries = data.get("camera_parent_items")
    if not isinstance(entries, list) or len(entries) != len(cameras):
        return False

    valid_cam_idxs = {camera.idx for camera in cameras}
    valid_shot_idxs = {shot.idx for shot in shots}
    resolved: list[tuple[int, int | None, int | None, bool | None, str | None]] = []
    parent_by_camera: dict[int, int] = {}
    for camera, entry in zip(cameras, entries):
        if entry is None:
            resolved.append((camera.idx, None, None, None, None))
            continue
        if not isinstance(entry, dict):
            return False
        parent_cam_idx = entry.get("parent_cam_idx")
        parent_shot_idx = entry.get("parent_shot_idx")
        if parent_cam_idx is None:
            resolved.append((camera.idx, None, None, None, None))
            continue
        parent_cam_idx = _to_int(parent_cam_idx, default=-1)
        if parent_cam_idx not in valid_cam_idxs or parent_cam_idx == camera.idx:
            return False
        parent_shot_idx = (
            _to_int(parent_shot_idx, default=-1)
            if parent_shot_idx is not None
            else None
        )
        if parent_shot_idx is not None and parent_shot_idx not in valid_shot_idxs:
            return False
        covers = entry.get("is_parent_fully_covers_child")
        missing_info = _clean_text(entry.get("missing_info")) or None
        resolved.append(
            (
                camera.idx,
                parent_cam_idx,
                parent_shot_idx,
                bool(covers) if covers is not None else None,
                missing_info,
            )
        )
        parent_by_camera[camera.idx] = parent_cam_idx

    # 循环检测（ViMax 口径：逐相机沿 parent 链走，重复经过即判定成环）。
    for camera in cameras:
        seen: set[int] = set()
        current = camera.idx
        while parent_by_camera.get(current) is not None:
            current = parent_by_camera[current]
            if current in seen:
                return False
            seen.add(current)

    for camera, item in zip(cameras, resolved):
        _, parent_cam_idx, parent_shot_idx, covers, missing_info = item
        camera.parent_cam_idx = parent_cam_idx
        camera.parent_shot_idx = parent_shot_idx
        camera.is_parent_fully_covers_child = covers
        camera.missing_info = missing_info
    return True


def camera_topological_order(cameras: list[IdeaCamera]) -> list[IdeaCamera]:
    """父机位排在子机位之前的处理顺序（DFS 后序，天然满足依赖）。"""
    by_idx = {camera.idx: camera for camera in cameras}
    ordered: list[IdeaCamera] = []
    placed: set[int] = set()

    def visit(camera: IdeaCamera, stack: set[int]) -> None:
        if camera.idx in placed or camera.idx in stack:
            return
        stack.add(camera.idx)
        parent = by_idx.get(camera.parent_cam_idx) if camera.parent_cam_idx is not None else None
        if parent is not None:
            visit(parent, stack)
        stack.discard(camera.idx)
        if camera.idx not in placed:
            placed.add(camera.idx)
            ordered.append(camera)

    for camera in cameras:
        visit(camera, set())
    return ordered


def camera_priority_shot_idxs(cameras: list[IdeaCamera]) -> set[int]:
    """被子机位依赖的镜头（其首帧要先于依赖它的机位产出）。"""
    return {
        camera.parent_shot_idx
        for camera in cameras
        if camera.parent_shot_idx is not None
    }


# --------------------------------------------------------------------------- #
# 参考图筛选（ViMax ReferenceImageSelector 的纯文本口径）
# --------------------------------------------------------------------------- #


def build_idea_reference_selection_prompt(
    *, frame_description: str, available_texts: list[str]
) -> str:
    """帧描述 + 可用参考图描述清单 → 参考图索引 + 图像生成提示词（JSON）。

    ViMax 在 ≥8 张时先用纯文本模型筛选、再用多模态模型精选；本地文本 leaf
    只做纯文本一次筛选（相当于把 ViMax 的文本过滤当作最终选择），图片本体在
    帧生成时按索引作为参考图提交。
    """
    frame_desc = str(frame_description or "").strip()
    seq_lines = "\n".join(
        f"Image {index}: {text}" for index, text in enumerate(available_texts)
    )
    return f"""You are a professional visual creation assistant skilled in multimodal image analysis and reasoning.

Your core task is to intelligently select the most suitable reference images from a provided set of reference image descriptions (including multiple character reference images and existing scene images from prior frames) based on the user's text description (describing the target frame), ensuring that the subsequently generated image meets the following key consistencies:
- Character Consistency: The appearance (e.g. gender, ethnicity, age, facial features, hairstyle, body shape), clothing, expression, posture, etc., of the generated character should highly match the reference image descriptions.
- Environmental Consistency: The scene of the generated image (e.g., background, lighting, atmosphere, layout) should remain coherent with the existing image descriptions from prior frames.
- Style Consistency: The visual style of the generated image (e.g., realistic, cartoon, film-like, color tone) should harmonize with the reference image descriptions.

You will receive a text description of the target frame, along with a sequence of reference image descriptions.
- The text description of the target frame is enclosed within <FRAME_DESC> and </FRAME_DESC>.
- The sequence of reference image descriptions is enclosed within <SEQ_DESC> and </SEQ_DESC>. Each description is prefixed with its index, starting from 0.

Output a single JSON object (no markdown, no explanation) with this schema:
{{
  "ref_image_indices": [0, 2],
  "text_prompt": "Create an image following the given description: ... The man should reference Image 0. The landscape should reference Image 1."
}}

Guidelines:
- Ensure that the language of all output values (not include keys) matches that used in the frame description.
- The reference image descriptions may depict the same character from different angles, in different outfits, or in different scenes. Identify the description closest to the version described by the user.
- Prioritize image descriptions with similar compositions, i.e., shots taken by the same camera.
- The images from prior frames are arranged in chronological order. Give higher priority to more recent images (those closer to the end of the sequence).
- Choose reference image descriptions that are as concise as possible and avoid including duplicate information. For example, if Image 3 depicts the facial features of Bob from the front, and Image 1 also depicts Bob's facial features from the front-view portrait, then Image 1 is redundant and should not be selected.
- When a new character appears in the frame description, prioritize selecting their portrait image description (if available) to ensure accurate depiction of their appearance. Pay attention to whether the character is facing the camera from the front, side, or back. Choose the most suitable view as the reference image for the character.
- For character portraits, you can only select at most one image from multiple views (front, side, back). Choose the most appropriate one based on the frame description. For example, when depicting a character from the side, choose the side view of the character.
- Select at most {MAX_IDEA_REFERENCE_IMAGES} optimal reference image descriptions.
- In "text_prompt", refer to the selected reference images only in the format of "Image N", where N is the position in the "ref_image_indices" list (0-based), not the original index. Specify which elements in the generated image should reference which image.
- "text_prompt" must start by describing the image to be generated itself, then specify which elements should reference which image.

<FRAME_DESC>
{frame_desc}
</FRAME_DESC>

<SEQ_DESC>
{seq_lines}
</SEQ_DESC>
"""


def parse_idea_reference_selection(
    raw_text: str, *, total: int
) -> tuple[list[int], str]:
    """解析参考图选择：索引去重保序、越界过滤、≤8 截断；失败返回空选择。"""
    data = _load_json(raw_text)
    if not isinstance(data, dict):
        return [], ""
    raw_idxs = data.get("ref_image_indices")
    idxs: list[int] = []
    if isinstance(raw_idxs, list):
        seen: set[int] = set()
        for item in raw_idxs:
            index = _to_int(item, default=-1)
            if 0 <= index < max(0, int(total)) and index not in seen:
                seen.add(index)
                idxs.append(index)
    text_prompt = _clean_text(data.get("text_prompt"))
    return idxs[:MAX_IDEA_REFERENCE_IMAGES], text_prompt


def compose_idea_frame_prompt(
    *, reference_texts: list[str], text_prompt: str
) -> str:
    """帧生成 prompt 组装：``Image i: text`` 前缀 + 选择器输出的提示词。"""
    prefix = "".join(
        f"Image {index}: {text}\n" for index, text in enumerate(reference_texts)
    )
    return f"{prefix}\n{str(text_prompt or '').strip()}".strip()


# --------------------------------------------------------------------------- #
# 过渡视频与「新机位」参考帧（ViMax CameraImageGenerator）
# --------------------------------------------------------------------------- #


def build_idea_transition_video_prompt(
    *, parent_visual_desc: str, child_visual_desc: str
) -> str:
    """父镜头 → 子镜头的过渡视频提示词（ViMax 原文）。"""
    prompt = (
        "Two shots. The transition between the shots is a cut to. "
        "The style of the two shots should be consistent."
    )
    prompt += f"\nThe first shot description: {str(parent_visual_desc or '').strip()}."
    prompt += f"\nThe second shot description: {str(child_visual_desc or '').strip()}."
    return prompt


def build_idea_new_camera_text(*, missing_info: str | None) -> str:
    """新机位参考帧在参考图列表里的描述文本（ViMax 原文）。"""
    return (
        "The composition and background are correct but some elements may be wrong. "
        "The wrong elements should be replaced.\n"
        f"Wrong elements: {_clean_text(missing_info) or 'None'}.\n"
        "You must select this image as the main reference and replace the characters "
        "in the image with the provided character portraits. Don't change the background."
    )