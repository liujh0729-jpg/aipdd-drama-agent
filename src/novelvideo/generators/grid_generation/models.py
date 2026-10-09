"""GridGenerationRequest / GridGenerationResult 数据模型（B3 拆包自单文件 grid_generation.py）。"""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class GridGenerationRequest(BaseModel):
    """网格生成请求。

    参考模式由上游 build_character_map_for_grid() 决定：
    - composite: 复合参考图（Portrait + Fullbody 拼接），锁脸 + 锁服装
    - portrait_only: 仅面部特写，锁脸，服装由 appearance_details 文字控制
    - prompt_only: 无参考图，完全由提示词控制
    """

    beats: List[dict] = Field(description="Beats 数据列表（每张网格最多25个，不足留空）")
    character_map: Dict[str, dict] = Field(
        default_factory=dict,
        description="""角色映射 {角色名: {
            'character_tag': ...,
            'base_prompt': ...,
            'appearance_details': ...,
            'portrait_path': ...,  # 面部特写图（用于锁脸）
            'ref_path': ...,  # 参考图路径
            'reference_mode': ...,  # composite / portrait_only / prompt_only
        }}""",
    )
    style: str = Field(
        default=None,
        description="全局风格 (chinese_period_drama, anime, realistic)，默认使用 IMAGE_DEFAULT_STYLE",
    )
    episode: int = Field(description="集数")
    ethnicity: str = Field(
        default="Chinese",
        description="角色默认种族 (Chinese, Korean, Japanese, Western, etc.)",
    )


class GridGenerationResult(BaseModel):
    """网格生成结果。"""

    success: bool
    grid_image_path: Optional[str] = None
    grid_image_bytes: Optional[bytes] = None
    error: Optional[str] = None
    generation_time: float = 0.0
    # 用于单网格重新生成时的元数据
    beat_start_index: Optional[int] = None  # 该网格对应的起始 beat 索引 (0-based)
    beat_count: Optional[int] = None  # 该网格实际的 beat 数量（不含填充）
    grid_rows: Optional[int] = None  # 网格行数
    grid_cols: Optional[int] = None  # 网格列数
