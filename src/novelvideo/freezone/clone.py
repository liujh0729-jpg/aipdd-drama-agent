"""Video-clone planning helpers for the ``freezone_video_clone`` runner.

纯逻辑模块：prompt 构建 / 输出解析 / 关键帧映射 / 时长规整。不出网、不碰
ffmpeg、不导入任何 leaf 模块，因此不在 `FREEZONE_LEAF_EGRESS` 的分类范围内。
"""

from __future__ import annotations

import json
from typing import Any

# 解读阶段自身收敛到 3-12 个叙事镜头；这里只做兜底上限。
MAX_CLONE_SHOTS = 12

# 单镜目标时长的兜底与钳制范围（秒）。
DEFAULT_SHOT_SECONDS = 5.0
MIN_SHOT_SECONDS = 1.0
MAX_SHOT_SECONDS = 60.0

# frames 模式下每镜最多挂几张原帧参考图。
MAX_SHOT_REFERENCE_FRAMES = 2

# 交给「逐镜复刻规划」的解读字段。`voice_sound` / `background_music` 必须在列：
# 音画同步类视频模型（MiniMax H3 等）只按提示词发声，实测提示词里没有声音设计时
# 产物 ASR 直接转写为空（完全没有人声），写了台词则能逐字复现原句。漏掉这两个字段
# 就等于把解读阶段已经拿到的声音信息在规划环节吃掉。
_PLAN_FIELDS = (
    "visual_description",
    "narrative",
    "shot_size",
    "camera_movement",
    "lighting",
    "image_prompt",
    "motion_prompt",
    "voice_sound",
    "background_music",
)


def normalize_clone_shots(video_story: Any) -> list[dict[str, Any]]:
    """从 video_story 解析结果中取镜头列表；结构不符时返回空表。"""
    if not isinstance(video_story, dict):
        return []
    raw = video_story.get("shots")
    if not isinstance(raw, list):
        return []
    return [shot for shot in raw if isinstance(shot, dict)][:MAX_CLONE_SHOTS]


def shot_target_duration(
    shot: dict[str, Any],
    *,
    default: float = DEFAULT_SHOT_SECONDS,
) -> float:
    """镜头目标时长（秒）：优先 duration，其次 end_time-start_time，最后默认值。"""
    value = 0.0
    try:
        value = float(shot.get("duration") or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0:
        try:
            start = float(shot.get("start_time") or 0.0)
            end = float(shot.get("end_time") or 0.0)
        except (TypeError, ValueError):
            start = end = 0.0
        value = end - start
    if value <= 0:
        value = default
    return max(MIN_SHOT_SECONDS, min(value, MAX_SHOT_SECONDS))


def shot_reference_paths(
    shot: dict[str, Any],
    frame_paths: list[Any],
    *,
    duration_sec: float | None = None,
    limit: int = MAX_SHOT_REFERENCE_FRAMES,
) -> list[str]:
    """镜头关键帧 → 帧文件路径。

    ``keyframes`` 是 1-based 帧序号（与解读 prompt 的约定一致）；越界或缺失时
    按 ``start_time / duration_sec`` 比例回退定位。结果保序去重、最多 ``limit`` 张。
    """
    total = len(frame_paths)
    if total <= 0 or limit <= 0:
        return []
    indexes: list[int] = []
    raw = shot.get("keyframes")
    if isinstance(raw, list):
        for value in raw:
            try:
                index = int(value) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= index < total:
                indexes.append(index)
    if not indexes:
        try:
            start = max(0.0, float(shot.get("start_time") or 0.0))
        except (TypeError, ValueError):
            start = 0.0
        if duration_sec and duration_sec > 0:
            index = int(start / duration_sec * total)
        else:
            index = int(start)
        indexes = [min(total - 1, max(0, index))]
    seen: set[int] = set()
    unique: list[int] = []
    for index in indexes:
        if index not in seen:
            seen.add(index)
            unique.append(index)
    return [str(frame_paths[index]) for index in unique[:limit]]


def _compact_shot_for_plan(
    shot: dict[str, Any],
    index: int,
    *,
    drop_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    compact: dict[str, Any] = {
        "shot": index,
        "duration": round(shot_target_duration(shot), 2),
    }
    for field in _PLAN_FIELDS:
        if field in drop_fields:
            continue
        value = str(shot.get(field) or "").strip()
        if value:
            compact[field] = value
    return compact


def build_video_clone_plan_prompt(
    *,
    shots: list[dict[str, Any]],
    frame_count: int,
    user_prompt: str = "",
    reference_mode: str = "frames",
    content_reference_count: int = 0,
    audio_mode: str = "generate",
    dialogue_text: str = "",
    subject_reference_only: bool = False,
) -> str:
    """构建「逐镜复刻规划」的一次性 LLM 调用 prompt。

    ``content_reference_count`` 是用户额外提供的「参考内容」图片张数（角色/场景/
    风格参照）：>0 时要求逐镜提示词融合参考内容的特征，与提示词一同构成对原视频
    的处理方向。

    ``audio_mode`` 只有 ``generate``（由模型生成音）才需要声音设计：成片音频完全
    由提示词驱动，所以要求逐镜写 ``Audio:`` 段；``mute`` / ``source`` 下写了也没人
    听，反而拉长提示词。``dialogue_text`` 是参考视频音频的 ASR 转写台词，逐字交给
    规划器分配到镜头——这是复刻能「说出原句」的唯一通道（实测 AIPDD 的
    ``/v1/videos`` 会静默忽略 ``audio_urls`` 等参考音频字段，不能靠传音频）。

    ``subject_reference_only`` 表示带参考内容时不再提交原视频关键帧（主体替换优
    先）。实测 MiniMax H3 同时收到关键帧与参考图时只会整体还原关键帧里的主体，
    所以这一档下必须禁止提示词重复描述原主体外观。
    """
    story_json = json.dumps(
        {
            "shots": [
                # 主体替换优先时丢掉解读阶段的 `image_prompt`：它是「图生视频」的完整
                # 画面描述，里面写死了原主体的长相/服装，留着就是让规划器一边「不要
                # 描述原主体」一边「以它为基础扩展」。`motion_prompt` 只讲运动，保留。
                _compact_shot_for_plan(
                    shot,
                    index,
                    drop_fields=(
                        ("image_prompt",)
                        if subject_reference_only
                        else ()
                    ),
                )
                for index, shot in enumerate(shots, start=1)
            ]
        },
        ensure_ascii=False,
        indent=2,
    )
    extra = (
        f"\n用户附加要求（必须遵守）：{user_prompt.strip()}\n"
        if str(user_prompt or "").strip()
        else ""
    )
    clean_dialogue = str(dialogue_text or "").strip()
    dialogue_block = (
        f"\n参考视频台词转写（按时间顺序，逐字保留原语言，不要翻译或改写）：\n"
        f"\"\"\"{clean_dialogue}\"\"\"\n"
        "分配规则：按镜头时长把台词拆给对应镜头，单镜台词要能在该镜 duration 内"
        "说完（英文约每 2 秒 5-6 词）；宁可某镜不带台词，也不要把它塞不下的一句"
        "硬写进去；转写里找不到归属的片段直接丢弃。\n" "\n"
        if clean_dialogue and str(audio_mode or "").strip().lower() == "generate"
        else ""
    )
    if str(audio_mode or "").strip().lower() == "generate":
        audio_hint = (
            "- 每个 video_prompt 末尾必须再补一行 `Audio:`，用英文写明该镜的声音设计："
            "谁在说话、说什么（台词逐字保留原语言，不要翻译）、语气与音色，以及环境声"
            "与背景音乐（参考镜头的 voice_sound / background_music 字段）。音画同步模型"
            "只按提示词发声，不写这一行成片就没有人声。\n"
        )
    else:
        audio_hint = (
            "- 本次成片不使用模型生成音，video_prompt 不要写 `Audio:` 或声音描述。\n"
        )
    if int(content_reference_count or 0) > 0 and subject_reference_only:
        content_reference_hint = (
            f"- 用户提供了 {int(content_reference_count)} 张「参考内容」图片，本次为"
            "**主体替换优先**：参考图是画面主体外观的唯一来源，且只提交参考图、"
            "不提交原视频关键帧。video_prompt 里不要描述原视频主体的长相、年龄、"
            "发色、服装等静态外观（那些会被模型当成要保留的人），只写动作、场景、"
            "运镜、景别、光线、风格，以及参考内容带来的新主体特征。\n"
        )
    elif int(content_reference_count or 0) > 0:
        content_reference_hint = (
            f"- 用户另外提供了 {int(content_reference_count)} 张「参考内容」图片"
            "（角色形象 / 场景 / 风格参照），逐镜提示词需在忠实参考视频动作与叙事的基础上，"
            "融入参考内容承载的主体外观、场景与风格特征；参考图会随每镜一并提交给视频模型，"
            "提示词中无需描述图片本身，聚焦如何用它们改写画面。\n"
        )
    else:
        content_reference_hint = ""
    if int(content_reference_count or 0) > 0 and subject_reference_only:
        fidelity_hint = (
            "- 忠实复刻参考视频的动作、叙事、场景、色调与节奏，不要发明新情节；"
            "但画面主体（人物/角色）的外观一律以参考内容为准。"
        )
    else:
        fidelity_hint = (
            "- 忠实复刻参考视频：保持主体外观、服装、场景、色调与节奏一致，"
            "不要发明新情节。"
        )
    base_prompt_hint = (
        "- 若镜头提供了 motion_prompt，以此为基础扩展，不要偏离原意。"
        if subject_reference_only and int(content_reference_count or 0) > 0
        else "- 若镜头提供了 image_prompt / motion_prompt，以此为基础扩展，不要偏离原意。"
    )
    mode_hint = (
        (
            "本次复刻只提交用户参考内容作为参考图，构图与场景由提示词承载，"
            "提示词需完整描述环境、景别与运镜。"
            if subject_reference_only
            else "本次复刻会携带原视频关键帧作为参考图，提示词聚焦动作、运镜、叙事与风格的"
            "还原，不要在提示词里重复描述参考图已经承载的静态外观。"
        )
        if str(reference_mode or "").strip().lower() == "frames"
        else "本次为纯文本重制，不携带参考图，提示词必须完整描述画面视觉细节。"
    )
    return f"""你是顶级 AI 视频导演，正在执行「视频复刻」：把下面这条参考视频的视频故事逐镜改写成可直接用于视频生成的英文提示词。

参考视频共抽取 {frame_count} 张关键帧，归纳为 {len(shots)} 个镜头。
{extra}{dialogue_block}要求：
- 为每个镜头输出一个对象，shot 编号与输入严格一致。
- video_prompt 使用英文，描述该镜的主体动作、环境、构图、景别、镜头运动、光线与画面风格。
{fidelity_hint}
{base_prompt_hint}
- {mode_hint}
{audio_hint}{content_reference_hint}- 严格输出 JSON 对象，不要 markdown，不要解释。

JSON schema:
{{
  "shots": [
    {{"shot": 1, "video_prompt": "English prompt for shot 1"}}
  ]
}}

视频故事（JSON）：
{story_json}
"""


def _fallback_video_prompt(shot: dict[str, Any], *, include_audio: bool = False) -> str:
    parts = [
        str(shot.get("image_prompt") or "").strip(),
        str(shot.get("motion_prompt") or "").strip(),
    ]
    if include_audio:
        # 规划 LLM 失败时的兜底也要带声音设计，否则成片依旧没人声（与一句话短视
        # 频 `motion_desc + audio_desc` 同一口径）。
        parts.extend(
            str(shot.get(field) or "").strip()
            for field in ("voice_sound", "background_music")
        )
    prompt = " ".join(part for part in parts if part)
    if prompt:
        return prompt
    return str(shot.get("visual_description") or "").strip() or "cinematic shot"


def parse_video_clone_plan(
    raw_text: str,
    shots: list[dict[str, Any]],
    *,
    include_audio: bool = False,
) -> list[dict[str, Any]]:
    """解析规划输出；逐镜回退到 motion/image prompt，保证每镜都有提示词。

    规划输入里的 shot 编号是 1..N 连续序号（`_compact_shot_for_plan` 统一生成），
    这里按同一序号匹配；解析失败或漏镜时回退到解读结果自带的提示词字段。
    """
    prompts: dict[int, str] = {}
    text = str(raw_text or "").strip()
    if text.startswith("```"):
        text = "\n".join(
            line for line in text.splitlines() if not line.strip().startswith("```")
        ).strip()
    data: Any = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    entries = None
    if isinstance(data, dict):
        entries = data.get("shots")
    elif isinstance(data, list):
        entries = data
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                number = int(entry.get("shot"))
            except (TypeError, ValueError):
                continue
            prompt_text = str(entry.get("video_prompt") or "").strip()
            if prompt_text and number > 0:
                prompts[number] = prompt_text
    plan: list[dict[str, Any]] = []
    for index, shot in enumerate(shots, start=1):
        plan.append(
            {
                "shot": index,
                "video_prompt": prompts.get(index)
                or _fallback_video_prompt(shot, include_audio=include_audio),
                "target_duration": shot_target_duration(shot),
            }
        )
    return plan