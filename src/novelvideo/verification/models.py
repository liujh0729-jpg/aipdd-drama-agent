"""验证系统数据模型。"""

from enum import Enum

from pydantic import BaseModel, Field


class VerificationIssue(BaseModel):
    type: str  # "character_count_mismatch", "action_mismatch", "scene_mismatch", etc.
    severity: str  # "critical", "warning", "info"
    description: str
    confidence: float  # 0.0-1.0


class VerificationResult(BaseModel):
    """LLM 输出结构（用作 Agent 的 output_type）。"""

    passed: bool
    score: float  # 0-10
    issues: list[VerificationIssue]
    summary: str
    suggested_action: str  # "none" | "regenerate" | "edit_script"
    edit_suggestion: str = ""


# --- Legacy ConsistencyResult (kept for backward compat) ---


class ConsistencyIssue(BaseModel):
    type: str  # "character_appearance_inconsistency", "costume_mismatch"
    severity: str  # "critical", "warning", "info"
    beats: list[int]
    character: str
    description: str
    confidence: float


# --- Enhanced Consistency Models ---


class ConsistencyDimension(BaseModel):
    """单个一致性维度的评估。"""

    dimension: str = Field(
        description="face, hair, skin_tone, gender, clothing_style, clothing_color, accessories, body_type"
    )
    score: float = Field(ge=0, le=10, description="0-10 评分")
    severity: str = Field(description="critical, warning, info")
    description: str = Field(description="问题描述")


class CharacterConsistencyReport(BaseModel):
    """单个角色的一致性报告。"""

    character: str
    identity_id: str
    beats_checked: list[int]
    dimensions: list[ConsistencyDimension]
    face_score: float = Field(
        ge=0, le=10, description="脸部综合评分 (face + hair + skin_tone 均值)"
    )
    clothing_score: float = Field(
        ge=0, le=10, description="服装综合评分 (clothing_style + clothing_color + accessories 均值)"
    )
    passed: bool = Field(description="face_score >= 7.0 AND clothing_score >= 7.0 AND 无 critical")


class ConsistencyResult(BaseModel):
    """全局一致性验证结果（增强版，分维度评分）。"""

    characters: list[CharacterConsistencyReport] = Field(default_factory=list)
    summary: str
    overall_passed: bool = Field(default=True)


# --- Color Verification Models (Step 12.4) ---


class ColorMismatch(BaseModel):
    """单个颜色不匹配条目。"""

    identity_id: str
    color_hex: str
    color_name: str
    issue_type: str  # "missing" | "extra"


class ColorVerifyBeatResult(BaseModel):
    """单个 beat 的颜色验证结果。"""

    beat_number: int
    status: str  # "pass" | "fail" | "warn"
    expected: list[str]  # 剧本中预期的 identity_ids
    detected: list[str]  # 像素检测到的 identity_ids
    missing: list[ColorMismatch]  # 预期有但没检测到
    extra: list[ColorMismatch]  # 检测到但不该出现
    sketch_path: str = ""  # 草图文件路径（用于失败时定位）


class ColorVerifyResult(BaseModel):
    """整集颜色验证结果。"""

    total_beats: int
    passed_beats: int
    failed_beats: int
    warned_beats: int
    failed_beat_numbers: list[int]
    beat_results: list[ColorVerifyBeatResult]
    overall_passed: bool


# --- T3: Content Matching Score Models ---


class ObjectiveScore(BaseModel):
    """T3 内容匹配评分结果（LLM 输出结构）。"""

    script_match: float = Field(ge=0, le=10, description="剧本匹配度 0-10")
    identity_clarity: float = Field(ge=0, le=10, description="角色辨识度 0-10")
    total: float = Field(ge=0, le=10, description="均值")


# --- T4: Comparative Selection Models ---


class CandidateRanking(BaseModel):
    """单个候选的排名。"""

    pool_id: str
    rank: int
    reason: str


class CompareResult(BaseModel):
    """T4 对比选择结果（LLM 输出结构）。"""

    selected_index: int = Field(description="选中的候选序号（从 1 开始）")
    ranking: list[CandidateRanking] = Field(default_factory=list)
    comparison_summary: str = ""


# --- T6: Continuity Assessment Models ---


class ContinuityTransition(BaseModel):
    """单个过渡点评估。"""

    from_beat: int
    to_beat: int
    spatial_consistency: float = Field(ge=0, le=10, description="空间关系连续性")
    action_continuity: float = Field(ge=0, le=10, description="动作衔接")
    scene_transition: float = Field(ge=0, le=10, description="场景过渡合理性")
    total: float = Field(ge=0, le=10, description="三维均值")
    issues: list[str] = Field(default_factory=list, description="具体问题")


class ContinuityResult(BaseModel):
    """T6 连贯性评估结果（LLM 输出结构）。"""

    transitions: list[ContinuityTransition] = Field(default_factory=list)
    weak_transitions: list[int] = Field(
        default_factory=list, description="连贯性较差的过渡点 beat_number"
    )
    overall_score: float = Field(ge=0, le=10, default=10.0)


# --- T7: Similarity Detection Models ---


class SimilarityPair(BaseModel):
    """一对相似草图。"""

    beat_a: int
    beat_b: int
    similarity: float = Field(ge=0, le=1, description="相似度 0-1")
    warning: bool = Field(default=False, description="是否超过阈值")


class SimilarityResult(BaseModel):
    """T7 相似度检测结果（像素级，零 LLM）。"""

    pairs: list[SimilarityPair] = Field(default_factory=list)
    duplicate_beats: list[int] = Field(
        default_factory=list, description="与其他 beat 相似度 > 阈值的"
    )
    overall_passed: bool = Field(default=True)


# --- T8: Episode Overview Models ---


class EpisodeIssue(BaseModel):
    """全局分镜审片发现的问题。"""

    beat_number: int = Field(description="主要相关 beat")
    issue_type: str = Field(
        description="composition_repetition | style_drift | weak_climax | visual_monotony | pacing_imbalance | proportion_inconsistency"
    )
    severity: str = Field(description="critical | warning")
    description: str
    suggested_action: str = Field(description="swap_candidate | regenerate | info")
    related_beats: list[int] = Field(default_factory=list, description="相关 beat（上下文）")


class EpisodeOverviewResult(BaseModel):
    """T8 全局分镜审片结果（LLM 输出结构）。"""

    visual_rhythm: float = Field(ge=0, le=10, description="视觉节奏 0-10")
    composition_diversity: float = Field(ge=0, le=10, description="构图多样性 0-10")
    narrative_arc: float = Field(ge=0, le=10, description="叙事弧线视觉化 0-10")
    style_unity: float = Field(ge=0, le=10, description="风格统一性 0-10")
    total: float = Field(ge=0, le=10, description="四维均值")
    issues: list[EpisodeIssue] = Field(default_factory=list, description="最多 5 个")
    overall_passed: bool = Field(description="无 critical issue 且 total >= 6.0")
    summary: str = Field(description="一句话整体印象")


class EditDecision(str, Enum):
    """Teacher 对单张草图的顶层决策。"""

    keep = "keep"
    revise = "revise"


class SketchMainProblem(str, Enum):
    """导演视角的主问题分类。"""

    shot_scale_wrong = "shot_scale_wrong"
    composition_weak = "composition_weak"
    identity_color_mismatch = "identity_color_mismatch"
    staging_unclear = "staging_unclear"
    scene_mismatch = "scene_mismatch"
    character_count_wrong = "character_count_wrong"
    pose_action_wrong = "pose_action_wrong"


class SketchEditResult(BaseModel):
    """Teacher 生成的草图修正结果。"""

    decision: EditDecision
    main_problem: SketchMainProblem | None = Field(
        default=None,
        description="主问题类型；当 decision=keep 时可为 null。",
    )
    reasoning: str = Field(
        default="",
        description="1-3 句简洁解释，说明为什么保留或修正。",
        max_length=150,
    )
    edit_instruction: str = Field(
        default="",
        description=(
            "可直接执行的 Nanobanana 编辑提示词。提到身份时，使用精确 identity + hex color，"
            "不要使用模糊颜色称呼。"
        ),
        max_length=900,
    )
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
