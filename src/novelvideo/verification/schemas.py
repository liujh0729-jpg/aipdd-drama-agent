"""验证 API 请求/响应 Schema。"""

from pydantic import BaseModel, Field


class VerifyRequest(BaseModel):
    type: str = "sketch"  # "sketch" | "frame"


class ConsistencyVerifyRequest(BaseModel):
    verify_type: str = "sketch"  # "sketch" | "frame"


class ColorVerifyRequest(BaseModel):
    missing_threshold: float = 0.008  # 预期颜色的检测阈值
    extra_threshold: float = 0.015  # 非预期颜色的检测阈值（更高，减少背景噪声误报）


class SketchScoreRequest(BaseModel):
    """T3: 内容匹配评分请求。"""

    pool_id: str = ""  # 可选，不传则用最新版本
    type: str = "sketch"


class ScoreBatchRequest(BaseModel):
    """T3 批量: 批量评分请求。"""

    beat_numbers: list[int] = Field(default_factory=list, description="要评分的 beat 列表，空=全部")
    score_all_candidates: bool = Field(default=True, description="是否对所有候选打分")


class CompareRequest(BaseModel):
    """T4: 对比选择请求。"""

    pool_ids: list[str] = Field(..., description="要对比的候选 pool_id 列表")
    reference_pool_ids: list[str] = Field(default_factory=list, description="已选定的参考图（风格一致性）")


class ContinuityRequest(BaseModel):
    """T6: 连贯性评估请求。"""

    beat_range: list[int] = Field(default_factory=list, description="[start, end] 或空=全部")
    window_size: int = Field(default=2, ge=2, le=3, description="每次看几个连续 beat")


class SketchSelectRequest(BaseModel):
    """编排端点: 一站式择优请求。"""

    quality_threshold: float = Field(default=7.0, ge=0, le=10)
    score_gap_for_auto_select: float = Field(default=1.0, ge=0)
    color_prefilter: bool = True
    fact_check: bool = True
    promote_selected: bool = Field(
        default=False,
        description="Whether to copy accepted selections into sketches/epXXX. Defaults to false.",
    )


class SketchEditExecuteRequest(BaseModel):
    """Execute episode-level sketch edit batches from labels.jsonl."""

    labels_name: str = Field(
        default="labels.jsonl",
        description="JSONL file name written under verify_reports/epXXX/",
    )
