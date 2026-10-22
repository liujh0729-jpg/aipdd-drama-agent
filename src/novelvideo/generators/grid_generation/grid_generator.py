"""GridGenerator 主类（18 方法；整类保留，见 B3 文档「已知残留」）（B3 拆包自单文件 grid_generation.py）。"""

import asyncio
import base64
import io
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from novelvideo.config import (
    IMAGE_DEFAULT_STYLE,
    get_grid_generation_config,
    get_style_preset,
)
from novelvideo.egress_context import ambient_organization_egress_context
from novelvideo.ports.egress import EgressError
from novelvideo.shared.upstream_quota_errors import is_fatal_upstream_quota_error
from novelvideo.generators.prompt_builder import (
    PromptComponents,
    PromptContext,
    PromptMode,
    UnifiedPromptBuilder,
    create_prompt_context,
)
from novelvideo.generators.render_identity_guard import render_ai_detection_error
from novelvideo.models import beat_scene_id
from novelvideo.services.style_service import StyleService
from novelvideo.usage.image_request_usage import (
    infer_episode_from_path,
    infer_project_output_dir,
    record_image_request,
    update_image_request_status,
)

from .models import GridGenerationResult
from .image_sizes import (
    _infer_project_dir,
    _resolve_scene_prop_asset_refs,
    _global_prop_marker_colors,
    normalize_image_size,
    resolve_openai_image_size,
    _InlineImagePart,
)
from .panels import (
    filter_character_map_for_beats,
    _has_director_image_ref,
    _prepare_director_blocking_refs,
    filter_character_map_by_precomputed,
    resolve_render_reference_order,
)
from .regen_modes import REGEN_MODE_CONFIGS
from .sketch_flow import find_sketch_for_beat_range
from .plan import cell_aspect_ratio, SKETCH_GRID_CONFIG
from .provider_newapi import _call_newapi_image_api_with_egress
from .crop_split import perfect_grid_split
from .packing import (
    _count_batch_composite_chars,
    MANY_CHARS_MAX_CAPACITY,
    MANY_CHARS_REF_THRESHOLD,
    _smart_repack_beats,
    DEFAULT_POOL_TEMPLATE,
    pad_to_aspect_ratio,
)
from .modes import get_shot_grid_config

from novelvideo.generators import grid_generation as _gg


class GridGenerator:
    """NanoBananaPro 网格生成器。

    支持多种模式（统一使用批量生成，动态优化）:
    - "1x1": 单张生成（1K 分辨率）
    - "1x3": 横向三格
    - "2x2": 紧凑四格
    - "3x3": 分批生成多个 3x3 网格
    - "4x4": 中等平衡
    - "5x5": 批量生成，最大 25 个分镜（动态优化）

    使用 Google AI Studio 的 Gemini Pro Image 模型生成分镜网格。

    示例:
        >>> generator = GridGenerator()
        >>> # 3x3 模式批量生成
        >>> results = await generator.generate_grid_batch(
        ...     all_beats=beats_data,
        ...     character_map=char_map,
        ...     output_dir="output/grids"
        ... )
    """

    def __init__(self, api_key: Optional[str] = None, config: Optional[dict] = None):
        """初始化生成器。

        Args:
            api_key: Google AI API Key，默认从环境变量读取
        """
        config = config or get_grid_generation_config()
        self.provider = config.get("provider", "google")
        self.api_key = api_key or config["api_key"]
        self.model = config["model"]
        self.base_url = config.get("base_url", "")
        self.openai_image_quality = config.get("openai_image_quality", "medium")
        self.openai_sketch_image_quality = config.get(
            "openai_sketch_image_quality", "low"
        )
        self.huimeng_image_quality = config.get("huimeng_image_quality", "medium")
        self.default_image_size = config.get("image_size", "1K")
        self.newapi_request_schema = config.get("newapi_request_schema") or {}
        self.newapi_model_params = config.get("newapi_model_params") or {}
        self.mode = config.get("mode", "3x3")
        self.rows = config["rows"]
        self.cols = config["cols"]
        self.batch_size = config.get("batch_size", self.rows * self.cols)
        self.total_panels = config["total_panels"]

        if not self.api_key:
            if self.provider == "openrouter":
                key_name = "OPENROUTER_API_KEY"
            elif self.provider == "huimeng":
                key_name = "HUIMENGI_API_KEY"
            elif self.provider == "openai":
                key_name = "OPENAI_API_KEY"
            elif self.provider == "newapi":
                key_name = "LOCALAPI_API_KEY"
            else:
                key_name = "GOOGLE_AI_API_KEY"
            raise ValueError(f"API key not set. Set {key_name} environment variable.")

        # EG-09b（OI-52）：组织只许经网关出图。这条判决 `_prepare_organization_image_egress`
        # 早就有，但 grid 家族的调用点不经过它，于是对 `generate_grid` 这些入口从未生效。
        # 提到构造点是因为那 6 个入口共用 `self.provider` 选分支，一处判就够。
        # 只对组织身份生效——平台/个人走 direct provider 行为不变。
        if self.provider != "newapi" and ambient_organization_egress_context() is not None:
            raise EgressError("ORG_EGRESS_DENIED")

        print(f"[Grid] Provider: {self.provider}, Model: {self.model}")

    async def generate_grid(
        self,
        beats: List[dict],
        character_map: Dict[str, dict] = None,
        scene_menu: list[dict] | list | None = None,
        prop_menu: list[dict] | list | None = None,
        sketch_colors: Dict[str, str] = None,
        style: str = None,  # 默认使用全局风格配置
        output_path: Optional[str] = None,
        ethnicity: str = "Chinese",
        rows: int = None,  # 可配置行数，默认使用配置值
        cols: int = None,  # 可配置列数，默认使用配置值
        sketch: bool = False,  # 是否生成草图模式
        prompt_only: bool = False,  # Dry Run 模式：只生成提示词，不调用 API
        beat_start_index: int = 0,  # Render 模式：当前 grid 的 beat 起始索引（用于从 sketch 切片）
        total_episode_beats: int = 0,  # Render 模式：整集 beat 总数（用于计算 sketch 尺寸）
        location_beat_numbers: List[int] = None,  # 场景分组的原始 beat 编号（1-based）
        explicit_episode_number: Optional[
            int
        ] = None,  # 调用方已知的集数，避免从路径反推
        scene_refs_override: dict[int, list[Any]] | None = None,
        prop_refs_override: dict[int, list[Any]] | None = None,
        sketch_dir: str = "",  # 草图目录路径（由调用方通过 PathResolver 计算）
        aspect_ratio_override: Optional[
            str
        ] = None,  # 覆盖 aspect_ratio（再生模式使用）
        image_size_override: Optional[str] = None,  # 覆盖 image_size（再生模式使用）
        mode_key: Optional[str] = None,  # mode_key 查表取 aspect_ratio/image_size
        prompt_aspect_ratio: Optional[
            str
        ] = None,  # 覆盖 prompt 中的比例（two-pass: 图用 1:1，prompt 用 9:16）
        beat_sketch_paths: dict = None,  # {beat_num: full_path} 从图片池取的 per-beat 草图路径
        sketch_aspect_padding: bool = False,  # 草图补白到目标比例
        force_image_size: Optional[str] = None,  # 强制覆盖 image_size（如 "0.5K"）
        use_director_refs: bool = False,  # 是否优先使用 beat 级导演参考图
        director_sheet_path: Optional[str] = None,  # 当前 grid 的 DirectorWorld sheet
        director_ref_beat_numbers: Optional[
            List[int]
        ] = None,  # 仅这些 beat 使用导演参考
        director_control_frames_dir: str | Path | None = None,
    ) -> GridGenerationResult:
        """生成网格图。

        参考模式由上游 build_character_map_for_grid() 决定：
        - composite: 复合参考图（Portrait + Fullbody 拼接），锁脸 + 锁服装
        - portrait_only: 仅面部特写，锁脸，服装由 appearance_details 文字控制
        - prompt_only: 无参考图，完全由提示词控制

        Args:
            beats: Beats 数据列表（不足网格容量用黑色填充）
            character_map: 角色映射 {角色名: {
                'character_tag': ...,
                'base_prompt': ...,
                'appearance_details': ...,
                'portrait_path': ...,  # 面部特写图（用于锁脸）
                'ref_path': ...,  # 参考图路径
                'reference_mode': ...,  # composite / portrait_only / prompt_only
            }}
            style: 全局风格名称 (chinese_period_drama, anime, realistic)，
                   默认使用 IMAGE_DEFAULT_STYLE
            output_path: 输出路径
            ethnicity: 角色默认种族（默认 "Chinese"），用于确保生成正确的面部特征
            rows: 网格行数（默认使用配置值）
            cols: 网格列数（默认使用配置值）

        Returns:
            GridGenerationResult
        """
        # 使用传入的 rows/cols 或默认配置值
        rows = rows or self.rows
        cols = cols or self.cols
        grid_capacity = rows * cols
        start_time = time.time()
        character_map = character_map or {}
        previous_grid_path = None  # Render 模式会在内部设置草图路径

        # 使用全局默认风格
        if style is None:
            style = IMAGE_DEFAULT_STYLE
        print(f"[NanoBananaPro] 使用风格: {style}, 网格: {rows}x{cols}")

        if len(beats) < 1:
            return GridGenerationResult(
                success=False,
                error="需要至少 1 个 beat，当前没有 beats",
                generation_time=time.time() - start_time,
            )

        # 如果不足网格容量，后面会用黑色填充
        actual_beat_count = min(len(beats), grid_capacity)
        print(
            f"[NanoBananaPro] 有效 beats: {actual_beat_count}/{grid_capacity}，不足部分用黑色填充"
        )
        if not sketch and sketch_dir:
            detection_error = render_ai_detection_error(beats[:grid_capacity])
            if detection_error:
                print(f"[NanoBananaPro] ❌ {detection_error}")
                return GridGenerationResult(
                    success=False,
                    error=detection_error,
                    generation_time=time.time() - start_time,
                )

        try:
            types = None
            client = None
            # 初始化客户端（仅 Google 直连需要）
            if self.provider == "google":
                from google.genai import types
                from google import genai

                client = genai.Client(api_key=self.api_key)

            # 验证参考图存在 - 信任上游 reference_mode，只做文件存在性确认
            valid_character_map = {}
            for char_name, info in character_map.items():
                char_info = dict(info)
                ref_path = info.get("ref_path") or info.get("portrait_path")
                upstream_mode = info.get("reference_mode", "prompt_only")

                if (
                    upstream_mode == "composite" and ref_path and os.path.exists(ref_path)
                ):
                    char_info["reference_path"] = ref_path
                    char_info["reference_mode"] = "composite"
                    print(f"[NanoBananaPro] {char_name}: 复合图模式 -> {ref_path}")
                    valid_character_map[char_name] = char_info
                    continue

                if ref_path and os.path.exists(ref_path):
                    char_info["reference_path"] = ref_path
                    char_info["reference_mode"] = "portrait_only"
                    print(
                        f"[NanoBananaPro] {char_name}: Portrait 模式（仅锁脸）-> {ref_path}"
                    )
                    valid_character_map[char_name] = char_info
                    continue

                char_info["reference_path"] = None
                char_info["reference_mode"] = "prompt_only"
                print(f"[NanoBananaPro] {char_name}: 提示词模式（无参考图）")
                valid_character_map[char_name] = char_info

            # 1. 构建网格 Prompt
            # 分流：Sketch 模式 vs Render 模式
            # 统一使用 UnifiedPromptBuilder 以确保导出和生成使用相同的提示词
            is_render_mode = False  # 标记是否为 Render 模式
            project_dir = _infer_project_dir(output_path, sketch_dir)
            episode_number = (
                int(explicit_episode_number)
                if explicit_episode_number is not None
                else infer_episode_from_path(output_path) or infer_episode_from_path(sketch_dir)
            )
            scene_refs: dict[int, list[Any]] = {}
            prop_asset_refs: dict[int, list[Any]] = {}
            scene_refs, prop_asset_refs = _resolve_scene_prop_asset_refs(
                project_dir,
                beats[:grid_capacity],
                episode_number=episode_number,
                sketch=sketch,
                use_director_refs=use_director_refs,
                include_pano_view_refs=False,
                director_ref_beat_numbers=director_ref_beat_numbers,
                director_control_frames_dir=director_control_frames_dir,
                scene_menu=scene_menu,
                prop_menu=prop_menu,
                allow_beat_background_anchor=(
                    actual_beat_count == 1 and int(rows or 0) == 1 and int(cols or 0) == 1
                ),
            )
            if scene_refs_override is not None:
                scene_refs = {
                    int(panel_idx): list(refs or [])
                    for panel_idx, refs in scene_refs_override.items()
                }
            if prop_refs_override is not None:
                prop_asset_refs = {
                    int(panel_idx): list(refs or [])
                    for panel_idx, refs in prop_refs_override.items()
                }
            if (
                sketch
                and use_director_refs
                and director_sheet_path
                and os.path.exists(director_sheet_path)
            ):
                from novelvideo.utils.asset_resolver import ResolvedAssetRef

                director_sheet_ref = ResolvedAssetRef(
                    asset_type="scene",
                    base_id=beats[0].get("scene_id") or beats[0].get("scene") or "DirectorWorld",
                    variant_id=None,
                    image_paths=[director_sheet_path],
                    text_description="DirectorWorld blocking reference sheet",
                    source_level="director_sheet",
                )
                selected_director_beats = {
                    int(bn) for bn in (director_ref_beat_numbers or []) if bn is not None
                }
                for panel_idx in range(1, actual_beat_count + 1):
                    if selected_director_beats:
                        beat_num = beats[panel_idx - 1].get("beat_number", panel_idx)
                        if int(beat_num or 0) not in selected_director_beats:
                            continue
                    refs = scene_refs.setdefault(panel_idx, [])
                    refs.insert(0, director_sheet_ref)
            # 过滤为当前网格出场角色。scene_refs 先解析，后续可按参考图上下文收窄角色集。
            if sketch:
                valid_character_map = filter_character_map_for_beats(
                    valid_character_map,
                    beats[:grid_capacity],
                    scene_refs=scene_refs if use_director_refs else None,
                )
            if sketch and use_director_refs:
                has_director_sheet = bool(
                    director_sheet_path and os.path.exists(director_sheet_path)
                )
                if has_director_sheet:
                    print(
                        f"[DirectorSheet] 使用 DirectorWorld sheet: {director_sheet_path}"
                    )
                elif actual_beat_count != 1 or rows != 1 or cols != 1:
                    return GridGenerationResult(
                        success=False,
                        error=(
                            "导演参考图模式只支持单 beat 1x1；"
                            "批量草图请先导出对应 DirectorWorld 控制图。"
                        ),
                        generation_time=time.time() - start_time,
                    )
                if not has_director_sheet and not _has_director_image_ref(
                    scene_refs, panel_idx=1
                ):
                    return GridGenerationResult(
                        success=False,
                        error=(
                            "导演单镜缺少 beat 级 3GS control frame；"
                            "草图主线不再回退到旧场景参考图。"
                        ),
                        generation_time=time.time() - start_time,
                    )
                if not has_director_sheet:
                    _prepare_director_blocking_refs(
                        scene_refs=scene_refs,
                        beats=beats[:grid_capacity],
                        character_map=valid_character_map,
                    )
            style_family, animation_subtype = StyleService.get_style_branch(
                style or IMAGE_DEFAULT_STYLE,
                project_dir=project_dir,
            )

            if sketch:
                # Sketch 模式使用 UnifiedPromptBuilder（与导出逻辑一致）
                print("[NanoBananaPro] 进入 Sketch 模式")

                # 当前网格的全局 beat 范围 (1-based)
                grid_beat_start = beat_start_index + 1
                grid_beat_end = beat_start_index + grid_capacity

                # prompt_aspect_ratio 优先（two-pass 时图用 1:1 但 prompt 写 2:3）
                _prompt_ar = prompt_aspect_ratio or (
                    REGEN_MODE_CONFIGS[mode_key]["aspect_ratio"] if mode_key else None
                )
                # image_aspect_ratio = 实际输出比例（two-pass Pass1 时为 1:1，否则与 prompt_ar 相同）
                _image_ar = (
                    REGEN_MODE_CONFIGS[mode_key]["aspect_ratio"] if mode_key else ""
                )
                ctx = create_prompt_context(
                    mode=PromptMode.SKETCH,
                    beats=beats[:grid_capacity],
                    rows=rows,
                    cols=cols,
                    character_map=valid_character_map,
                    style=style,
                    ethnicity=ethnicity,
                    aspect_ratio=_prompt_ar,
                    image_aspect_ratio=_image_ar,
                    scene_refs=scene_refs,
                    prop_asset_refs=prop_asset_refs,
                    sketch_colors=sketch_colors or {},
                    prop_marker_colors=_global_prop_marker_colors(
                        beats[:grid_capacity],
                        prop_menu,
                        sketch_colors=sketch_colors or {},
                    ),
                    style_family=style_family,
                    animation_subtype=animation_subtype,
                    project_dir=str(project_dir) if project_dir else "",
                    image_provider=self.provider,
                    image_model=self.model,
                )
                from novelvideo.verification.failure_registry import (
                    load_negative_clause_for_project,
                )

                ctx.registry_negative_clause = await load_negative_clause_for_project(
                    str(project_dir) if project_dir else None, "generator"
                )
                builder = UnifiedPromptBuilder(ctx)
                prompt = builder.build()

            elif sketch_dir:
                # Render 模式：通过 find_sketch_for_beat_range 在草图目录中定位对应的草图
                sketch_dir_path = sketch_dir

                # 确定 beat 编号列表 — 始终从 beats 自身提取，避免与外部参数不同步
                actual_beats = beats[:grid_capacity]
                actual_beat_numbers = [
                    _gg._generation_beat_number(b, i) for i, b in enumerate(actual_beats)
                ]

                beat_range_start = min(actual_beat_numbers)
                beat_range_end = max(actual_beat_numbers)

                sketch_result = find_sketch_for_beat_range(
                    sketch_dir_path, beat_range_start, beat_range_end
                )
                has_all_pool_sketches = beat_sketch_paths and all(
                    bn in beat_sketch_paths for bn in actual_beat_numbers
                )
                if sketch_result is None and not has_all_pool_sketches:
                    print(
                        f"[Render] 警告：未找到覆盖 beat {beat_range_start}-{beat_range_end} 的草图"
                    )

                if sketch_result or has_all_pool_sketches:
                    print("[NanoBananaPro] 进入 Render 模式 (基于草图渲染)")
                    if has_all_pool_sketches:
                        print(
                            f"[Render] 使用图片池草图: {len(beat_sketch_paths)} 个 beat"
                        )
                    elif sketch_result:
                        sketch_file, s_rows, s_cols = sketch_result
                        print(f"[Render] 使用草图: {sketch_file} ({s_rows}x{s_cols})")
                    is_render_mode = True

                    # Render 模式：先切片草图，再用颜色检测过滤角色
                    if output_path:
                        temp_dir = Path(output_path).parent
                    else:
                        temp_dir = Path("output")
                    temp_dir.mkdir(parents=True, exist_ok=True)
                    sub_sketch_path = str(temp_dir / "temp_sub_sketch.jpg")

                    target_aspect = None
                    if sketch_aspect_padding and mode_key:
                        target_aspect = cell_aspect_ratio(mode_key)

                    sub_sketch_path = _gg.crop_sketch_panels(
                        sketch_path=sketch_dir_path,
                        beat_numbers=actual_beat_numbers,
                        target_rows=rows,
                        target_cols=cols,
                        output_path=sub_sketch_path,
                        beat_sketch_paths=beat_sketch_paths,
                        target_aspect=target_aspect,
                    )
                    print(
                        f"[Render] 草图切片: beat_numbers={actual_beat_numbers} -> {rows}x{cols}"
                    )
                    print(f"[Render] 子草图已保存: {sub_sketch_path}")

                    # 用切片后的草图作为参考
                    previous_grid_path = sub_sketch_path

                    # 读取预计算的 per-beat 身份检测结果（草图工作台已完成检测）
                    _panel_det = _gg.load_precomputed_panel_detected(
                        actual_beat_numbers, beats
                    )
                    valid_character_map = filter_character_map_by_precomputed(
                        valid_character_map, _panel_det
                    )

                    # 使用 UnifiedPromptBuilder（与导出逻辑一致）
                    ctx = create_prompt_context(
                        mode=PromptMode.RENDER,
                        beats=beats[:grid_capacity],
                        rows=rows,
                        cols=cols,
                        character_map=valid_character_map,
                        style=style,
                        ethnicity=ethnicity,
                        aspect_ratio=(
                            REGEN_MODE_CONFIGS[mode_key]["aspect_ratio"] if mode_key else None
                        ),
                        panel_detected_keys=_panel_det,
                        scene_refs=scene_refs,
                        prop_asset_refs=prop_asset_refs,
                        sketch_colors=sketch_colors or {},
                        style_family=style_family,
                        animation_subtype=animation_subtype,
                        project_dir=str(project_dir) if project_dir else "",
                    )
                    builder = UnifiedPromptBuilder(ctx)
                    prompt = builder.build()
                else:
                    # [aipdd fork delta] 无草图直出：未找到覆盖该 beat 范围的草图时不再报错终止，
                    # 改用当前选中的默认图像模型，基于 beat 画面描述 + 角色/场景/道具参考，
                    # 走 PromptMode.RENDER 的无底图分支（include_sketch_base=False）直出渲染图。
                    print(
                        f"[Render] ⚠️ 未找到覆盖 beat {beat_range_start}-{beat_range_end} 的草图，"
                        "按选中的默认模型无草图直出渲染图"
                    )
                    _panel_det = _gg.load_precomputed_panel_detected(
                        actual_beat_numbers, beats
                    )
                    valid_character_map = filter_character_map_by_precomputed(
                        valid_character_map, _panel_det
                    )
                    ctx = create_prompt_context(
                        mode=PromptMode.RENDER,
                        beats=beats[:grid_capacity],
                        rows=rows,
                        cols=cols,
                        character_map=valid_character_map,
                        style=style,
                        ethnicity=ethnicity,
                        aspect_ratio=(
                            REGEN_MODE_CONFIGS[mode_key]["aspect_ratio"] if mode_key else None
                        ),
                        panel_detected_keys=_panel_det,
                        scene_refs=scene_refs,
                        prop_asset_refs=prop_asset_refs,
                        sketch_colors=sketch_colors or {},
                        style_family=style_family,
                        animation_subtype=animation_subtype,
                        project_dir=str(project_dir) if project_dir else "",
                        include_sketch_base=False,
                    )
                    builder = UnifiedPromptBuilder(ctx)
                    prompt = builder.build()
            else:
                # 需要草图或草图目录
                msg = "generate_grid() 需要 sketch 或 sketch_dir 参数"
                print(f"[NanoBananaPro] ❌ {msg}")
                return GridGenerationResult(
                    success=False,
                    error=msg,
                )

            print(
                f"[NanoBananaPro] 构建 Prompt 完成，共 {len(beats[:grid_capacity])} 个分镜"
            )

            # 保存 prompt 到文件（审计用）
            # 目录结构: grids/ep001/2x2/prompts/grid_01.prompt.txt
            if output_path:
                grid_dir = Path(output_path).parent  # grids/ep001/2x2
                prompts_dir = grid_dir / "prompts"  # grids/ep001/2x2/prompts
                prompts_dir.mkdir(parents=True, exist_ok=True)
                grid_basename = Path(output_path).stem  # "grid_01"
                prompt_file = prompts_dir / f"{grid_basename}.prompt.txt"
                prompt_file.write_text(prompt, encoding="utf-8")
                print(f"[NanoBananaPro] Grid Prompt 已保存: {prompt_file}")

            # Prompt-Only 模式：只生成提示词，跳过 API 调用
            if prompt_only:
                print("[NanoBananaPro] Prompt-Only 模式，跳过 API 调用")
                # 在 Render 模式下，显示 sketch 切片信息（用于验证）
                if is_render_mode:
                    sketch_capacity = (
                        SKETCH_GRID_CONFIG["rows"] * SKETCH_GRID_CONFIG["cols"]
                    )
                    local_offset = beat_start_index % sketch_capacity
                    end_index = local_offset + len(beats[:grid_capacity])
                    print(
                        f"[NanoBananaPro] [Render 预览] 草图 {SKETCH_GRID_CONFIG['rows']}x{SKETCH_GRID_CONFIG['cols']}"
                    )
                    print(
                        f"[NanoBananaPro] [Render 预览] 本地切片: [{local_offset}:{end_index}] (共 {end_index - local_offset} panels)"
                    )
                return GridGenerationResult(
                    success=True,
                    grid_image_path=None,
                    error=None,
                    generation_time=time.time() - start_time,
                )

            usage_request_id = uuid.uuid4().hex
            project_output_dir = infer_project_output_dir(output_path or sketch_dir)
            usage_recorded = False
            scope_beat_numbers = [
                int(b) for b in (location_beat_numbers or []) if b is not None
            ]
            if not scope_beat_numbers:
                scope_beat_numbers = [
                    _gg._generation_beat_number(beat, beat_start_index + idx)
                    for idx, beat in enumerate(beats[:grid_capacity])
                ]
            first_beat_num = scope_beat_numbers[0] if scope_beat_numbers else None
            task_type = "sketch_grid" if sketch else "render_grid"
            scope = f"{task_type}:{mode_key or f'{rows}x{cols}'}:{'-'.join(str(b) for b in scope_beat_numbers)}"

            def _usage_fail(error_message: str) -> GridGenerationResult:
                if usage_recorded and project_output_dir:
                    update_image_request_status(
                        project_output_dir=project_output_dir,
                        request_id=usage_request_id,
                        status="failed",
                        error_message=error_message,
                    )
                return GridGenerationResult(
                    success=False,
                    error=error_message,
                    generation_time=time.time() - start_time,
                )

            def _usage_success(
                final_output_path: str | None, final_bytes: bytes | None
            ) -> GridGenerationResult:
                generation_time = time.time() - start_time
                if usage_recorded and project_output_dir:
                    update_image_request_status(
                        project_output_dir=project_output_dir,
                        request_id=usage_request_id,
                        status="completed",
                    )
                return GridGenerationResult(
                    success=True,
                    grid_image_path=final_output_path,
                    grid_image_bytes=final_bytes,
                    generation_time=generation_time,
                )

            if project_output_dir:
                record_image_request(
                    project_output_dir=project_output_dir,
                    request_id=usage_request_id,
                    provider=self.provider,
                    model_name=self.model,
                    task_type=task_type,
                    scope=scope,
                    episode=infer_episode_from_path(output_path),
                    beat_num=first_beat_num,
                )
                usage_recorded = True

            # =================================================================
            # Render 模式 / Sketch 模式（统一使用单次 API 调用）
            # =================================================================

            # 2. 准备参考图
            # 按照 Google 官方文档：prompt 在前，图像连续排列在后
            # https://ai.google.dev/gemini-api/docs/image-generation
            contents = [prompt]  # prompt 放在最前面
            submitted_refs: list[dict] = []

            # Render 模式：草图必须是 Image 1。它是唯一构图底图；角色/场景/道具只提供身份和材质。
            # Render 模式传角色/身份/场景/道具；Sketch 模式只传场景参考图。
            # 道具在草图阶段只保留名称和 marker 颜色，不传道具参考图，避免最终
            # 材质/三视图干扰 blocking。
            if not sketch and previous_grid_path and os.path.exists(previous_grid_path):
                previous_grid_image = self._load_image_as_part(previous_grid_path)
                if previous_grid_image:
                    contents.append(previous_grid_image)
                    submitted_refs.append(
                        {
                            "kind": "previous_grid",
                            "base_id": "sketch",
                            "path": previous_grid_path,
                            "bytes": (
                                os.path.getsize(previous_grid_path)
                                if os.path.exists(previous_grid_path)
                                else None
                            ),
                        }
                    )
                    print(
                        "[NanoBananaPro] 添加草图底图 (Image 1 composition lock): "
                        f"{previous_grid_path}"
                    )

            if sketch:
                if use_director_refs:
                    self._append_reference_parts_from_plan(
                        contents,
                        ctx,
                        [],
                        valid_character_map,
                        allowed_kinds={"scene"},
                        verbose=True,
                        audit_refs=submitted_refs,
                    )
                elif scene_refs or prop_asset_refs:
                    self._append_reference_parts_from_plan(
                        contents,
                        ctx,
                        [],
                        valid_character_map,
                        allowed_kinds={"scene"},
                        verbose=True,
                        audit_refs=submitted_refs,
                    )
            else:
                ordered_chars = resolve_render_reference_order(
                    ctx, beats, grid_capacity, valid_character_map
                )

                print(f"[NanoBananaPro] 角色参考图顺序: {ordered_chars}")
                self._append_reference_parts_from_plan(
                    contents,
                    ctx,
                    ordered_chars,
                    valid_character_map,
                    allowed_kinds=None,
                    verbose=True,
                    audit_refs=submitted_refs,
                )

            if output_path:
                grid_dir = Path(output_path).parent
                prompts_dir = grid_dir / "prompts"
                prompts_dir.mkdir(parents=True, exist_ok=True)
                grid_basename = Path(output_path).stem
                submitted_file = prompts_dir / f"{grid_basename}.submitted.json"
                submitted_payload = {
                    "provider": self.provider,
                    "model": self.model,
                    "prompt": prompt,
                    "reference_images": submitted_refs,
                }
                submitted_file.write_text(
                    json.dumps(submitted_payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(f"[NanoBananaPro] Submitted Prompt/Refs 已保存: {submitted_file}")

            # contents 结构:
            # - Render 模式: [prompt, char/scene/prop refs..., sketch(最后)]
            # - 普通 Sketch 模式: [prompt]
            # - Director Sketch 模式: [prompt, director scene/prop refs...]

            # 3. 调用 API
            # 从 mode_key 或 REGEN_MODE_CONFIGS 查找宽高比和分辨率
            if aspect_ratio_override:
                # 再生模式：使用显式指定的 aspect_ratio 和 image_size
                aspect_ratio = aspect_ratio_override
                image_size = image_size_override or "2K"
            elif mode_key:
                _cfg = REGEN_MODE_CONFIGS[mode_key]
                aspect_ratio = _cfg["aspect_ratio"]
                image_size = _cfg["image_size"]
            elif sketch:
                # Sketch 模式：使用独立配置
                aspect_ratio = SKETCH_GRID_CONFIG["aspect_ratio"]
                image_size = SKETCH_GRID_CONFIG["image_size"]
            else:
                # 从 (rows, cols) 在 REGEN_MODE_CONFIGS 中查找
                _found = False
                for _mk, _cfg in REGEN_MODE_CONFIGS.items():
                    if _cfg["rows"] == rows and _cfg["cols"] == cols:
                        aspect_ratio = _cfg["aspect_ratio"]
                        image_size = _cfg["image_size"]
                        _found = True
                        break
                if not _found:
                    if rows == cols:
                        aspect_ratio = "1:1"
                        image_size = "4K" if rows >= 4 else "2K"
                    elif rows > cols:
                        aspect_ratio = "9:16"
                        image_size = "4K"
                    else:
                        aspect_ratio = "21:9"
                        image_size = "2K"

            if force_image_size:
                image_size = force_image_size

            if self.provider == "openrouter":
                # ===== OpenRouter 分支 =====
                effective_image_size = normalize_image_size(
                    image_size, provider="openrouter"
                )
                print(
                    f"[NanoBananaPro] 调用 OpenRouter ({self.model}) 生成网格图 (分辨率: {effective_image_size}, 比例: {aspect_ratio})..."
                )
                prompt_text, ref_bytes = self._extract_ref_bytes_from_contents(contents)
                image_bytes, or_text, or_error = await _gg._call_openrouter_image_api(
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt_text,
                    reference_images=ref_bytes or None,
                    image_config={
                        "aspect_ratio": aspect_ratio, "image_size": effective_image_size,
                    },
                )
                if not image_bytes:
                    message = "OpenRouter API 未返回图像数据"
                    if or_error:
                        message = f"{message}: {or_error}"
                    return _usage_fail(message)
            elif self.provider == "huimeng":
                print(
                    f"[HuiMeng Images] 调用 {self.model} 生成网格图 (比例: {aspect_ratio})..."
                )
                prompt_text, ref_bytes = self._extract_ref_bytes_from_contents(contents)
                image_bytes, _text, huimeng_error = await _gg._call_huimeng_image_api(
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt_text,
                    reference_images=ref_bytes or None,
                    image_config={
                        "aspect_ratio": aspect_ratio,
                        "image_size": image_size,
                        "huimeng_image_quality": self.huimeng_image_quality,
                    },
                )
                if not image_bytes:
                    message = "HuiMeng Images 未返回图像数据"
                    if huimeng_error:
                        message = f"{message}: {huimeng_error}"
                    return _usage_fail(message)
            elif self.provider == "openai":
                # ===== OpenAI Image API 分支 =====
                openai_image_size = "1K" if sketch else image_size
                openai_size = resolve_openai_image_size(aspect_ratio, openai_image_size)
                print(
                    f"[NanoBananaPro] 调用 OpenAI Image API ({self.model}) 生成网格图 "
                    f"(size: {openai_size}, 比例: {aspect_ratio})..."
                )
                prompt_text, ref_bytes = self._extract_ref_bytes_from_contents(
                    contents,
                    include_mime=True,
                )
                image_bytes, _openai_text, openai_error = await _gg._call_openai_image_api(
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt_text,
                    reference_images=ref_bytes or None,
                    image_config={
                        "aspect_ratio": aspect_ratio,
                        "image_size": openai_image_size,
                        "quality": (
                            self.openai_sketch_image_quality
                            if sketch
                            else self.openai_image_quality
                        ),
                        "output_format": "png",
                    },
                )
                if not image_bytes:
                    message = "OpenAI Image API 未返回图像数据"
                    if openai_error:
                        message = f"{message}: {openai_error}"
                    return _usage_fail(message)
            elif self.provider == "newapi":
                effective_image_size = normalize_image_size(
                    image_size, provider="newapi"
                )
                print(
                    f"[DramaClawAPI Images] 调用 {self.model} 生成网格图 "
                    f"(分辨率: {effective_image_size}, 比例: {aspect_ratio})..."
                )
                prompt_text, ref_bytes = self._extract_ref_bytes_from_contents(
                    contents,
                    include_mime=True,
                )
                image_bytes, _text, newapi_error = await _call_newapi_image_api_with_egress(
                    capability="image.generate.grid",
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt_text,
                    reference_images=ref_bytes or None,
                    image_config={
                        "aspect_ratio": aspect_ratio,
                        "image_size": effective_image_size,
                        "quality": (
                            self.openai_sketch_image_quality
                            if sketch
                            else self.openai_image_quality
                        ),
                    "request_schema": self.newapi_request_schema,
                        "model_params": self.newapi_model_params,
                    },
                    base_url=self.base_url,
                    delivery_path=output_path,
                    delivery_state=(image_delivery_state := {}),
                )
                if not image_bytes:
                    message = "DramaClawAPI Images 未返回图像数据"
                    if newapi_error:
                        message = f"{message}: {newapi_error}"
                    return _usage_fail(message)
            else:
                # ===== Google 直连分支 =====
                # 根据模型选择配置：gemini-3 支持 image_size，gemini-2.5 不支持
                is_gemini3 = "gemini-3" in self.model
                if is_gemini3:
                    effective_image_size = normalize_image_size(
                        image_size, provider="google"
                    )
                    print(
                        f"[NanoBananaPro] 调用 {self.model} 生成网格图 (分辨率: {effective_image_size}, 比例: {aspect_ratio})..."
                    )
                    image_config = types.ImageConfig(
                        aspect_ratio=aspect_ratio,
                        image_size=effective_image_size,
                    )
                else:
                    # gemini-2.5-flash-image 不支持 image_size 参数
                    print(
                        f"[NanoBananaPro] 调用 {self.model} 生成网格图 (比例: {aspect_ratio})..."
                    )
                    image_config = types.ImageConfig(
                        aspect_ratio=aspect_ratio,
                    )

                # 配置 thinking（网页版默认开启，API 需要显式配置）
                # 注意: image-preview 模型不支持 thinking
                is_image_model = "image-preview" in self.model
                if is_image_model:
                    # image-preview 模型不支持 thinking
                    thinking_config = None
                elif is_gemini3:
                    # Gemini 3 用 thinking_level
                    thinking_config = types.ThinkingConfig(
                        thinking_level=types.ThinkingLevel.HIGH
                    )
                else:
                    # Gemini 2.5 模型使用 thinking_budget（最小 128）
                    thinking_config = types.ThinkingConfig(thinking_budget=1024)

                # 构建配置
                gen_config = types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                    image_config=image_config,
                )
                if thinking_config:
                    gen_config = types.GenerateContentConfig(
                        response_modalities=["IMAGE", "TEXT"],
                        image_config=image_config,
                        thinking_config=thinking_config,
                    )

                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=self.model,
                    contents=contents,
                    config=gen_config,
                )

                # 4. 提取图像数据
                image_bytes = None

                # 检查响应结构
                if not response.candidates:
                    print(f"[NanoBananaPro] API 响应无 candidates: {response}")
                    return _usage_fail(f"API 响应无 candidates: {response}")

                candidate = response.candidates[0]
                if not candidate.content:
                    print(
                        f"[NanoBananaPro] candidate 无 content: finish_reason={getattr(candidate, 'finish_reason', 'unknown')}"
                    )
                    # 打印安全评级（如果有）
                    if (
                        hasattr(candidate, "safety_ratings") and candidate.safety_ratings
                    ):
                        for rating in candidate.safety_ratings:
                            print(f"[NanoBananaPro] safety_rating: {rating}")
                    return _usage_fail(
                        f"API 响应无 content, finish_reason={getattr(candidate, 'finish_reason', 'unknown')}"
                    )

                if not candidate.content.parts:
                    print(
                        f"[NanoBananaPro] content 无 parts: {candidate.content}, finish_reason={getattr(candidate, 'finish_reason', 'unknown')}"
                    )
                    # 打印完整 candidate 对象以便调试
                    print(f"[NanoBananaPro] 完整 candidate: {candidate}")
                    return _usage_fail("API 响应 content 无 parts")

                text_parts = []
                for part in candidate.content.parts:
                    if hasattr(part, "inline_data") and part.inline_data:
                        image_bytes = part.inline_data.data
                    # 收集文本响应
                    if hasattr(part, "text") and part.text:
                        text_parts.append(part.text)
                        print(f"[NanoBananaPro] API 文本响应: {part.text[:500]}")

                if not image_bytes:
                    return _usage_fail("API 未返回图像数据")

            # 5. 保存文件
            if output_path:
                output_dir = os.path.dirname(output_path)
                if output_dir:
                    os.makedirs(output_dir, exist_ok=True)
                if not (self.provider == "newapi" and image_delivery_state.get("copied")):
                    with open(output_path, "wb") as f:
                        f.write(image_bytes)
                print(f"[NanoBananaPro] 网格图已保存: {output_path}")

                # 5.1 后处理：移除面板间缝隙并覆盖
                try:
                    from novelvideo.generators.grid_splitter import remove_grid_gaps
                    from PIL import Image as PILImage

                    grid_img = PILImage.open(output_path)
                    processed_grid = remove_grid_gaps(grid_img, rows, cols)
                    if processed_grid is not grid_img:
                        processed_grid.save(output_path)
                    # 更新 image_bytes 以保持返回值一致
                    with open(output_path, "rb") as f:
                        image_bytes = f.read()
                    print(f"[NanoBananaPro] Gap removal 后处理完成: {output_path}")
                except Exception as e:
                    print(f"[NanoBananaPro] Gap removal 失败，保留原图: {e}")

            generation_time = time.time() - start_time
            print(f"[NanoBananaPro] 生成完成，耗时 {generation_time:.1f}s")

            return _usage_success(output_path, image_bytes)

        except ImportError:
            return GridGenerationResult(
                success=False,
                error="请安装 google-genai: pip install google-genai",
                generation_time=time.time() - start_time,
            )
        except Exception as e:
            if (
                "usage_recorded" in locals()
                and usage_recorded
                and "project_output_dir" in locals()
                and project_output_dir
            ):
                update_image_request_status(
                    project_output_dir=project_output_dir,
                    request_id=usage_request_id,
                    status="failed",
                    error_message=str(e),
                )
            if is_fatal_upstream_quota_error(e):
                raise
            return GridGenerationResult(
                success=False,
                error=str(e),
                generation_time=time.time() - start_time,
            )

    async def generate_action_grid(
        self,
        action_description: str,
        character_map: Dict[str, dict] = None,
        style: str = None,
        output_path: Optional[str] = None,
        ethnicity: str = "Chinese",
        mode_key: str = "5x5_2-3_sketch",
    ) -> GridGenerationResult:
        """为 action beat 生成 5×5 连续分镜草图网格。

        与 generate_grid 的区别：
        - 所有 25 个 panel 是同一段动作的连续分镜序列（非不同 beat）
        - 使用 ACTION_STORYBOARD prompt 模式
        - 固定 5×5 网格

        Args:
            action_description: 动作描述（含 {{identity_id}} 标记）
            character_map: 角色映射（用于颜色编码）
            style: 风格名称
            output_path: 输出路径
            ethnicity: 角色种族
            mode_key: 网格模式（默认 5x5_2-3_sketch）

        Returns:
            GridGenerationResult
        """
        start_time = time.time()
        character_map = character_map or {}

        if style is None:
            style = IMAGE_DEFAULT_STYLE

        mode_cfg = REGEN_MODE_CONFIGS.get(mode_key, REGEN_MODE_CONFIGS["5x5_2-3_sketch"])
        rows = mode_cfg["rows"]
        cols = mode_cfg["cols"]
        print(f"[ActionGrid] 生成 {rows}x{cols} 动作分镜, 风格: {style}")

        # 构建伪 beat 列表（单个 action beat 扩展为 25 panel 占位）
        action_beat = {
            "beat_number": 1,
            "visual_description": action_description,
            "audio_type": "silence",
            "scene_id": "",
        }
        beats = [action_beat]

        # 过滤角色映射为动作描述中出场角色
        valid_character_map = filter_character_map_for_beats(character_map, beats)

        # 构建 ACTION_STORYBOARD prompt
        style_family, animation_subtype = StyleService.get_style_branch(
            style or IMAGE_DEFAULT_STYLE
        )
        ctx = create_prompt_context(
            mode=PromptMode.ACTION_STORYBOARD,
            beats=beats,
            rows=rows,
            cols=cols,
            character_map=valid_character_map,
            style=style,
            ethnicity=ethnicity,
            aspect_ratio=mode_cfg.get("aspect_ratio", "2:3"),
            style_family=style_family,
            animation_subtype=animation_subtype,
        )
        builder = UnifiedPromptBuilder(ctx)
        prompt = builder.build()

        # 保存 prompt
        if output_path:
            prompts_dir = Path(output_path).parent / "prompts"
            prompts_dir.mkdir(parents=True, exist_ok=True)
            prompt_file = prompts_dir / f"{Path(output_path).stem}.prompt.txt"
            prompt_file.write_text(prompt, encoding="utf-8")

        try:
            # 准备参考图（角色参考）
            contents = [prompt]
            for char_name, info in valid_character_map.items():
                ref_path = info.get("ref_path") or info.get("portrait_path")
                if ref_path and os.path.exists(ref_path):
                    img_part = self._load_image_as_part(ref_path)
                    if img_part:
                        contents.append(img_part)

            # 调用 API
            image_size = mode_cfg.get("image_size", "1K")
            aspect_ratio = mode_cfg.get("aspect_ratio", "2:3")
            if self.provider == "openrouter":
                prompt_text, ref_bytes = self._extract_ref_bytes_from_contents(contents)
                image_bytes, _, error_detail = await _gg._call_openrouter_image_api(
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt_text,
                    reference_images=ref_bytes or None,
                    image_config={
                        "aspect_ratio": aspect_ratio,
                        "image_size": image_size,
                        "huimeng_image_quality": self.huimeng_image_quality,
                    },
                )
                if not image_bytes:
                    return GridGenerationResult(
                        success=False,
                        error=f"OpenRouter API 未返回图片: {error_detail or ''}".strip(),
                        generation_time=time.time() - start_time,
                    )
            elif self.provider == "huimeng":
                prompt_text, ref_bytes = self._extract_ref_bytes_from_contents(contents)
                image_bytes, _, error_detail = await _gg._call_huimeng_image_api(
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt_text,
                    reference_images=ref_bytes or None,
                    image_config={"aspect_ratio": aspect_ratio, "image_size": image_size},
                )
                if not image_bytes:
                    return GridGenerationResult(
                        success=False,
                        error=f"HuiMeng Images 未返回图片: {error_detail or ''}".strip(),
                        generation_time=time.time() - start_time,
                    )
            elif self.provider == "openai":
                prompt_text, ref_bytes = self._extract_ref_bytes_from_contents(
                    contents,
                    include_mime=True,
                )
                image_bytes, _, error_detail = await _gg._call_openai_image_api(
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt_text,
                    reference_images=ref_bytes or None,
                    image_config={
                        "aspect_ratio": aspect_ratio,
                        "image_size": image_size,
                        "quality": self.openai_sketch_image_quality,
                        "output_format": "png",
                    },
                )
                if not image_bytes:
                    return GridGenerationResult(
                        success=False,
                        error=f"OpenAI Image API 未返回图片: {error_detail or ''}".strip(),
                        generation_time=time.time() - start_time,
                    )
            elif self.provider == "newapi":
                prompt_text, ref_bytes = self._extract_ref_bytes_from_contents(
                    contents,
                    include_mime=True,
                )
                image_bytes, _, error_detail = await _call_newapi_image_api_with_egress(
                    capability="image.generate.action_grid",
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt_text,
                    reference_images=ref_bytes or None,
                    image_config={
                        "aspect_ratio": aspect_ratio,
                        "image_size": image_size,
                        "quality": self.openai_sketch_image_quality,
                    },
                    base_url=self.base_url,
                    delivery_path=output_path,
                    delivery_state=(image_delivery_state := {}),
                )
                if not image_bytes:
                    return GridGenerationResult(
                        success=False,
                        error=f"DramaClawAPI Images 未返回图片: {error_detail or ''}".strip(),
                        generation_time=time.time() - start_time,
                    )
            else:
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=self.api_key)
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=self.model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_modalities=["TEXT", "IMAGE"],
                        image_generation_config=types.ImageGenerationConfig(
                            image_size=image_size,
                        ),
                    ),
                )

                # 提取图片
                image_bytes = None
                for part in response.candidates[0].content.parts:
                    if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                        image_bytes = part.inline_data.data
                        break

            if not image_bytes:
                return GridGenerationResult(
                    success=False,
                    error="API 未返回图片",
                    generation_time=time.time() - start_time,
                )

            # 保存网格图
            if output_path:
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                if not (self.provider == "newapi" and image_delivery_state.get("copied")):
                    with open(output_path, "wb") as f:
                        f.write(image_bytes)

                # Gap removal 后处理
                try:
                    from novelvideo.generators.grid_splitter import remove_grid_gaps
                    from PIL import Image as PILImage

                    grid_img = PILImage.open(output_path)
                    processed_grid = remove_grid_gaps(grid_img, rows, cols)
                    if processed_grid is not grid_img:
                        processed_grid.save(output_path)
                    with open(output_path, "rb") as f:
                        image_bytes = f.read()
                except Exception as e:
                    print(f"[ActionGrid] Gap removal 失败，保留原图: {e}")

            generation_time = time.time() - start_time
            print(f"[ActionGrid] 生成完成，耗时 {generation_time:.1f}s")

            return GridGenerationResult(
                success=True,
                grid_image_path=output_path,
                grid_image_bytes=image_bytes,
                generation_time=generation_time,
                grid_rows=rows,
                grid_cols=cols,
            )

        except Exception as e:
            return GridGenerationResult(
                success=False,
                error=str(e),
                generation_time=time.time() - start_time,
            )

    async def reformat_sketch(
        self,
        source_path: str,
        output_path: str,
        target_aspect: str = "9:16",
        target_size: str = "1K",
        rows: int = 0,
        cols: int = 0,
    ) -> GridGenerationResult:
        """Second pass: 保持分镜构图，转换宽高比（1:1 → 9:16）。

        读取 Pass 1 保存的完整提示词，连同草图一起发给 Gemini，
        确保模型理解每个 panel 的内容，只改比例不丢信息。

        Args:
            source_path: Pass 1 生成的 1:1 草图路径
            output_path: 输出 9:16 草图路径
            target_aspect: 目标宽高比
            target_size: 目标分辨率
            rows: 网格行数（0 = 从文件名推断）
            cols: 网格列数（0 = 从文件名推断）

        Returns:
            GridGenerationResult
        """
        start_time = time.time()
        try:
            # 使用传入的 rows/cols，否则从路径推断（格式: sketch_g0_5x5_pass1.jpg）
            if rows and cols:
                grid_rows, grid_cols = rows, cols
            else:
                _m = re.search(r"(\d+)x(\d+)", os.path.basename(source_path))
                grid_rows = int(_m.group(1)) if _m else 5
                grid_cols = int(_m.group(2)) if _m else 5

            # 读取 Pass 1 保存的完整提示词（generate_grid 自动存到 prompts/ 目录）
            source_dir = Path(source_path).parent
            source_stem = Path(source_path).stem  # e.g. "sketch_g0_5x5_pass1"
            prompt_file = source_dir / "prompts" / f"{source_stem}.prompt.txt"

            original_prompt = ""
            if prompt_file.exists():
                original_prompt = prompt_file.read_text(encoding="utf-8")
                print(
                    f"[Reformat] 读取 Pass 1 提示词: {prompt_file} ({len(original_prompt)} chars)"
                )

            # reformat_sketch 现在只处理 outpaint（1:1 → 2:3）
            # 9:16 已改为 one-pass 直接生成，不再走 two-pass
            reformat_instruction = (
                f"This is a {grid_rows}x{grid_cols} storyboard grid where each panel is 1:1 (square).\n"
                f"OUTPAINT every panel from 1:1 to {target_aspect} — extend each scene vertically "
                f"(add space above and below) while keeping the original content centered.\n"
                f"Do NOT crop, stretch, or rearrange. Just extend each panel's background/environment vertically."
            )
            if original_prompt:
                import re as _re

                structural_prompt = original_prompt
                cut_tail = _re.search(
                    r"\n(?:DIRECTING GUIDELINES|SCENE DESCRIPTIONS)",
                    structural_prompt,
                )
                if cut_tail:
                    structural_prompt = structural_prompt[: cut_tail.start()]
                structural_prompt = _re.sub(
                    r"\nROLE:.*?(?=\nSTYLE:|\nLAYOUT:)",
                    "",
                    structural_prompt,
                    flags=_re.DOTALL,
                )
                prompt = f"{reformat_instruction}\n\n{structural_prompt}"
                print(
                    f"[Reformat] Outpaint 模式，提示词精简: {len(original_prompt)} → {len(prompt)} chars"
                )
            else:
                prompt = reformat_instruction

            # 保存 Pass 2 prompt 到文件（审计用）
            output_stem = Path(output_path).stem  # e.g. "sketch_g0_5x5"
            pass2_prompt_file = source_dir / "prompts" / f"{output_stem}.prompt.txt"
            pass2_prompt_file.parent.mkdir(parents=True, exist_ok=True)
            pass2_prompt_file.write_text(prompt, encoding="utf-8")
            print(f"[Reformat] Pass 2 Prompt 已保存: {pass2_prompt_file} ({len(prompt)} chars)")

            ref_image = self._load_image_as_part(source_path)
            contents = [prompt, ref_image]

            if self.provider == "openrouter":
                # ===== OpenRouter 分支 =====
                print(f"[Reformat] 调用 OpenRouter ({self.model}) 转换 → {target_aspect} ...")
                prompt_text, ref_bytes = self._extract_ref_bytes_from_contents(contents)
                image_bytes, _or_text, _or_error = await _gg._call_openrouter_image_api(
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt_text,
                    reference_images=ref_bytes or None,
                    image_config={
                        "aspect_ratio": target_aspect,
                        "image_size": target_size,
                        "huimeng_image_quality": self.huimeng_image_quality,
                    },
                )
                if not image_bytes:
                    return GridGenerationResult(
                        success=False,
                        error=(
                            f"[Reformat] OpenRouter API 未返回图像数据: {_or_error}"
                            if _or_error
                            else "[Reformat] OpenRouter API 未返回图像数据"
                        ),
                        generation_time=time.time() - start_time,
                    )
            elif self.provider == "huimeng":
                print(f"[Reformat] 调用 HuiMeng Images ({self.model}) 转换 → {target_aspect} ...")
                prompt_text, ref_bytes = self._extract_ref_bytes_from_contents(contents)
                image_bytes, _text, huimeng_error = await _gg._call_huimeng_image_api(
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt_text,
                    reference_images=ref_bytes or None,
                    image_config={"aspect_ratio": target_aspect, "image_size": target_size},
                )
                if not image_bytes:
                    return GridGenerationResult(
                        success=False,
                        error=(
                            f"[Reformat] HuiMeng Images 未返回图像数据: {huimeng_error}"
                            if huimeng_error
                            else "[Reformat] HuiMeng Images 未返回图像数据"
                        ),
                        generation_time=time.time() - start_time,
                    )
            elif self.provider == "openai":
                # ===== OpenAI Image API 分支 =====
                print(f"[Reformat] 调用 OpenAI Image API ({self.model}) 转换 → {target_aspect} ...")
                prompt_text, ref_bytes = self._extract_ref_bytes_from_contents(
                    contents,
                    include_mime=True,
                )
                image_bytes, _openai_text, openai_error = await _gg._call_openai_image_api(
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt_text,
                    reference_images=ref_bytes or None,
                    image_config={
                        "aspect_ratio": target_aspect,
                        "image_size": target_size,
                        "output_format": "png",
                    },
                )
                if not image_bytes:
                    return GridGenerationResult(
                        success=False,
                        error=(
                            f"[Reformat] OpenAI Image API 未返回图像数据: {openai_error}"
                            if openai_error
                            else "[Reformat] OpenAI Image API 未返回图像数据"
                        ),
                        generation_time=time.time() - start_time,
                    )
            elif self.provider == "newapi":
                print(f"[Reformat] 调用 DramaClawAPI Images ({self.model}) 转换 → {target_aspect} ...")
                prompt_text, ref_bytes = self._extract_ref_bytes_from_contents(
                    contents,
                    include_mime=True,
                )
                image_bytes, _text, newapi_error = await _call_newapi_image_api_with_egress(
                    capability="image.edit.sketch_reformat",
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt_text,
                    reference_images=ref_bytes or None,
                    image_config={
                        "aspect_ratio": target_aspect,
                        "image_size": target_size,
                        "quality": self.openai_image_quality,
                    },
                    base_url=self.base_url,
                    delivery_path=output_path,
                    delivery_state=(image_delivery_state := {}),
                )
                if not image_bytes:
                    return GridGenerationResult(
                        success=False,
                        error=(
                            f"[Reformat] DramaClawAPI Images 未返回图像数据: {newapi_error}"
                            if newapi_error
                            else "[Reformat] DramaClawAPI Images 未返回图像数据"
                        ),
                        generation_time=time.time() - start_time,
                    )
            else:
                # ===== Google 直连分支 =====
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=self.api_key)

                is_gemini3 = "gemini-3" in self.model
                if is_gemini3:
                    image_config = types.ImageConfig(
                        aspect_ratio=target_aspect,
                        image_size=target_size,
                    )
                else:
                    image_config = types.ImageConfig(
                        aspect_ratio=target_aspect,
                    )

                gen_config = types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                    image_config=image_config,
                )

                print(f"[Reformat] 调用 {self.model} 转换 → {target_aspect} ...")
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=self.model,
                    contents=contents,
                    config=gen_config,
                )

                # 提取图像
                image_bytes = None
                if not response.candidates or not response.candidates[0].content:
                    return GridGenerationResult(
                        success=False,
                        error="[Reformat] API 响应无有效内容",
                        generation_time=time.time() - start_time,
                    )

                for part in response.candidates[0].content.parts:
                    if hasattr(part, "inline_data") and part.inline_data:
                        image_bytes = part.inline_data.data
                        break

                if not image_bytes:
                    return GridGenerationResult(
                        success=False,
                        error="[Reformat] API 未返回图像数据",
                        generation_time=time.time() - start_time,
                    )

            # 保存
            output_dir = os.path.dirname(output_path)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
            if not (self.provider == "newapi" and image_delivery_state.get("copied")):
                with open(output_path, "wb") as f:
                    f.write(image_bytes)

            generation_time = time.time() - start_time
            print(f"[Reformat] 完成 → {output_path}，耗时 {generation_time:.1f}s")

            return GridGenerationResult(
                success=True,
                grid_image_path=output_path,
                grid_image_bytes=image_bytes,
                generation_time=generation_time,
            )

        except Exception as e:
            return GridGenerationResult(
                success=False,
                error=f"[Reformat] {e}",
                generation_time=time.time() - start_time,
            )

    async def prepare_batch_request(
        self,
        beats: List[dict],
        character_map: Dict[str, dict] = None,
        scene_menu: list[dict] | list | None = None,
        prop_menu: list[dict] | list | None = None,
        sketch_colors: Dict[str, str] = None,
        style: str = None,
        output_path: str = "",
        ethnicity: str = "Chinese",
        rows: int = None,
        cols: int = None,
        sketch: bool = False,
        beat_start_index: int = 0,
        total_episode_beats: int = 0,
        location_beat_numbers: List[int] = None,
        sketch_dir: str = "",
        mode_key: Optional[str] = None,
        beat_sketch_paths: dict = None,
        sketch_aspect_padding: bool = False,
        force_image_size: Optional[str] = None,
        use_director_refs: bool = False,
        director_control_frames_dir: str | Path | None = None,
    ) -> dict:
        """准备单个 Batch API 请求（构建 prompt + contents，不调用 API）。

        复用 generate_grid() 的 prompt 构建和参考图逻辑，返回 dict 格式
        供 generate_batch_api() 批量提交。

        Returns:
            {
                "contents": [...],       # prompt + 参考图 Part 对象
                "rows": int,
                "cols": int,
                "aspect_ratio": str,
                "image_size": str,
                "output_path": str,
                "beat_start_index": int,
                "actual_beat_count": int,
            }
        """
        # 调用 generate_grid 的 prompt_only 模式来获取 prompt
        # 但我们需要完整的 contents，所以直接复用其逻辑
        rows = rows or self.rows
        cols = cols or self.cols
        grid_capacity = rows * cols
        character_map = character_map or {}

        if style is None:
            style = IMAGE_DEFAULT_STYLE

        actual_beat_count = min(len(beats), grid_capacity)
        if not sketch and sketch_dir:
            detection_error = render_ai_detection_error(beats[:grid_capacity])
            if detection_error:
                raise RuntimeError(detection_error)

        # 验证参考图
        valid_character_map = {}
        for char_name, info in character_map.items():
            char_info = dict(info)
            ref_path = info.get("ref_path") or info.get("portrait_path")
            upstream_mode = info.get("reference_mode", "prompt_only")

            if upstream_mode == "composite" and ref_path and os.path.exists(ref_path):
                char_info["reference_path"] = ref_path
                char_info["reference_mode"] = "composite"
                valid_character_map[char_name] = char_info
            elif ref_path and os.path.exists(ref_path):
                char_info["reference_path"] = ref_path
                char_info["reference_mode"] = "portrait_only"
                valid_character_map[char_name] = char_info
            else:
                char_info["reference_path"] = None
                char_info["reference_mode"] = "prompt_only"
                valid_character_map[char_name] = char_info

        # 构建 Prompt（复用 UnifiedPromptBuilder）
        previous_grid_path = None
        project_dir = _infer_project_dir(output_path, sketch_dir)
        episode_number = infer_episode_from_path(output_path) or infer_episode_from_path(sketch_dir)
        scene_refs: dict[int, list[Any]] = {}
        prop_asset_refs: dict[int, list[Any]] = {}
        scene_refs, prop_asset_refs = _resolve_scene_prop_asset_refs(
            project_dir,
            beats[:grid_capacity],
            episode_number=episode_number,
            sketch=sketch,
            use_director_refs=use_director_refs,
            include_pano_view_refs=False,
            director_control_frames_dir=director_control_frames_dir,
            scene_menu=scene_menu,
            prop_menu=prop_menu,
            allow_beat_background_anchor=(
                actual_beat_count == 1 and int(rows or 0) == 1 and int(cols or 0) == 1
            ),
        )
        if sketch and use_director_refs:
            if actual_beat_count != 1 or rows != 1 or cols != 1:
                raise RuntimeError(
                    "导演参考图模式只支持单 beat 1x1；批量草图请先导出对应 DirectorWorld 控制图。"
                )
            if not _has_director_image_ref(scene_refs, panel_idx=1):
                raise RuntimeError(
                    "导演单镜缺少 beat 级 3GS control frame；" "草图主线不再回退到旧场景参考图。"
                )
            _prepare_director_blocking_refs(
                scene_refs=scene_refs,
                beats=beats[:grid_capacity],
                character_map=valid_character_map,
            )
        style_family, animation_subtype = StyleService.get_style_branch(
            style or IMAGE_DEFAULT_STYLE,
            project_dir=project_dir,
        )

        if sketch:
            ctx = create_prompt_context(
                mode=PromptMode.SKETCH,
                beats=beats[:grid_capacity],
                rows=rows,
                cols=cols,
                character_map=valid_character_map,
                style=style,
                ethnicity=ethnicity,
                scene_refs=scene_refs,
                prop_asset_refs=prop_asset_refs,
                sketch_colors=sketch_colors or {},
                prop_marker_colors=_global_prop_marker_colors(
                    beats[:grid_capacity],
                    prop_menu,
                    sketch_colors=sketch_colors or {},
                ),
                style_family=style_family,
                animation_subtype=animation_subtype,
                project_dir=str(project_dir) if project_dir else "",
                image_provider=self.provider,
                image_model=self.model,
            )
            from novelvideo.verification.failure_registry import (
                load_negative_clause_for_project,
            )

            ctx.registry_negative_clause = await load_negative_clause_for_project(
                str(project_dir) if project_dir else None, "generator"
            )
            builder = UnifiedPromptBuilder(ctx)
            prompt = builder.build()
        elif sketch_dir:
            # 始终从 beats 自身提取 beat 编号，避免与外部参数不同步
            actual_beats = beats[:grid_capacity]
            actual_beat_numbers = [
                _gg._generation_beat_number(b, i) for i, b in enumerate(actual_beats)
            ]
            beat_range_start = min(actual_beat_numbers)
            beat_range_end = max(actual_beat_numbers)

            sketch_result = find_sketch_for_beat_range(sketch_dir, beat_range_start, beat_range_end)
            has_all_pool_sketches = beat_sketch_paths and all(
                bn in beat_sketch_paths for bn in actual_beat_numbers
            )

            if sketch_result or has_all_pool_sketches:
                # 先切割草图
                if output_path:
                    temp_dir = Path(output_path).parent
                else:
                    temp_dir = Path("output")
                temp_dir.mkdir(parents=True, exist_ok=True)
                sub_sketch_path = str(temp_dir / "temp_sub_sketch_batch.jpg")
                target_aspect_batch = None
                if sketch_aspect_padding and mode_key:
                    target_aspect_batch = cell_aspect_ratio(mode_key)

                sub_sketch_path = _gg.crop_sketch_panels(
                    sketch_path=sketch_dir,
                    beat_numbers=actual_beat_numbers,
                    target_rows=rows,
                    target_cols=cols,
                    output_path=sub_sketch_path,
                    beat_sketch_paths=beat_sketch_paths,
                    target_aspect=target_aspect_batch,
                )
                previous_grid_path = sub_sketch_path

                # 读取预计算的 per-beat 身份检测结果（草图工作台已完成检测）
                _panel_det = _gg.load_precomputed_panel_detected(actual_beat_numbers, beats)
                valid_character_map = filter_character_map_by_precomputed(
                    valid_character_map, _panel_det
                )

                ctx = create_prompt_context(
                    mode=PromptMode.RENDER,
                    beats=beats[:grid_capacity],
                    rows=rows,
                    cols=cols,
                    character_map=valid_character_map,
                    style=style,
                    ethnicity=ethnicity,
                    panel_detected_keys=_panel_det,
                    scene_refs=scene_refs,
                    prop_asset_refs=prop_asset_refs,
                    sketch_colors=sketch_colors or {},
                    style_family=style_family,
                    animation_subtype=animation_subtype,
                    project_dir=str(project_dir) if project_dir else "",
                )
                builder = UnifiedPromptBuilder(ctx)
                prompt = builder.build()
            else:
                raise RuntimeError(
                    f"Render 模式需要草图但未找到覆盖 beat {beat_range_start}-{beat_range_end} 的草图"
                )
        else:
            raise RuntimeError("prepare_batch_request() 需要 sketch 或 sketch_dir 参数")

        # 构建 contents
        contents = [prompt]

        if not sketch and previous_grid_path and os.path.exists(previous_grid_path):
            previous_grid_image = self._load_image_as_part(previous_grid_path)
            if previous_grid_image:
                contents.append(previous_grid_image)

        if sketch:
            if use_director_refs:
                self._append_reference_parts_from_plan(
                    contents,
                    ctx,
                    [],
                    valid_character_map,
                    allowed_kinds={"scene"},
                    verbose=True,
                )
            elif scene_refs or prop_asset_refs:
                self._append_reference_parts_from_plan(
                    contents,
                    ctx,
                    [],
                    valid_character_map,
                    allowed_kinds={"scene"},
                    verbose=True,
                )
        else:
            ordered_chars = resolve_render_reference_order(
                ctx, beats, grid_capacity, valid_character_map
            )
            self._append_reference_parts_from_plan(
                contents,
                ctx,
                ordered_chars,
                valid_character_map,
                allowed_kinds=None,
                verbose=True,
            )

        # 确定 aspect_ratio 和 image_size
        if mode_key:
            _cfg = REGEN_MODE_CONFIGS[mode_key]
            aspect_ratio = _cfg["aspect_ratio"]
            image_size = _cfg["image_size"]
        elif sketch:
            aspect_ratio = SKETCH_GRID_CONFIG["aspect_ratio"]
            image_size = SKETCH_GRID_CONFIG["image_size"]
        else:
            _found = False
            for _mk, _cfg in REGEN_MODE_CONFIGS.items():
                if _cfg["rows"] == rows and _cfg["cols"] == cols:
                    aspect_ratio = _cfg["aspect_ratio"]
                    image_size = _cfg["image_size"]
                    _found = True
                    break
            if not _found:
                if rows == cols:
                    aspect_ratio = "1:1"
                    image_size = "4K" if rows >= 4 else "2K"
                elif rows > cols:
                    aspect_ratio = "9:16"
                    image_size = "4K"
                else:
                    aspect_ratio = "21:9"
                    image_size = "2K"

        if force_image_size:
            image_size = force_image_size

        return {
            "contents": contents,
            "rows": rows,
            "cols": cols,
            "aspect_ratio": aspect_ratio,
            "image_size": image_size,
            "output_path": output_path,
            "beat_start_index": beat_start_index,
            "actual_beat_count": actual_beat_count,
        }

    async def generate_batch_api(
        self,
        requests: List[dict],
        poll_interval: int = 15,
        timeout: int = 3600,
        on_status_change: callable = None,
    ) -> List[GridGenerationResult]:
        """通过 Google Batch API 一次提交所有网格生成请求。

        费用为标准 API 的 50%，但需要等待异步处理（通常几分钟到几十分钟）。

        Args:
            requests: prepare_batch_request() 返回的 dict 列表
            poll_interval: 轮询间隔（秒）
            timeout: 最大等待时间（秒）
            on_status_change: 状态变化回调

        Returns:
            每个请求对应的 GridGenerationResult 列表
        """
        if self.provider != "google":
            raise NotImplementedError(
                "Google Batch API 只支持 google provider，请使用标准模式 (generate_grid) 或切换到 google provider。"
            )

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)

        # 构建 InlinedRequest 列表
        is_gemini3 = "gemini-3" in self.model
        is_image_model = "image-preview" in self.model

        batch_requests = []
        for req in requests:
            if is_gemini3:
                image_config = types.ImageConfig(
                    aspect_ratio=req["aspect_ratio"],
                    image_size=req["image_size"],
                )
            else:
                image_config = types.ImageConfig(
                    aspect_ratio=req["aspect_ratio"],
                )

            if is_image_model:
                thinking_config = None
            elif is_gemini3:
                thinking_config = types.ThinkingConfig(thinking_level=types.ThinkingLevel.HIGH)
            else:
                thinking_config = types.ThinkingConfig(thinking_budget=1024)

            gen_config_kwargs = {
                "response_modalities": ["IMAGE", "TEXT"],
                "image_config": image_config,
            }
            if thinking_config:
                gen_config_kwargs["thinking_config"] = thinking_config

            gen_config = types.GenerateContentConfig(**gen_config_kwargs)

            batch_requests.append(
                {
                    "contents": req["contents"],
                    "config": gen_config,
                }
            )

        print(f"[BatchAPI] 提交 {len(batch_requests)} 个请求到 Google Batch API...")

        # 提交 Batch
        batch_job = client.batches.create(
            model=self.model,
            src=batch_requests,
            config={"display_name": f"grid_batch_{int(time.time())}"},
        )

        print(f"[BatchAPI] Batch 已提交: {batch_job.name}")

        # 轮询等待
        elapsed = 0
        last_state = None
        while elapsed < timeout:
            batch = client.batches.get(name=batch_job.name)
            if batch.state != last_state:
                last_state = batch.state
                print(f"[BatchAPI] 状态: {batch.state}")
                if on_status_change:
                    on_status_change(batch.state)

            if batch.state == "JOB_STATE_SUCCEEDED":
                break
            elif batch.state in ("JOB_STATE_FAILED", "JOB_STATE_CANCELLED"):
                raise RuntimeError(f"Batch 失败: {batch.state}")

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        if elapsed >= timeout:
            raise RuntimeError(f"Batch 超时（{timeout}s）")

        # 提取结果
        results = []
        for i, response in enumerate(batch.dest.inlined_responses):
            req = requests[i]
            output_path = req["output_path"]

            if hasattr(response, "error") and response.error:
                results.append(
                    GridGenerationResult(
                        success=False,
                        error=str(response.error),
                    )
                )
                continue

            # 提取图像
            image_bytes = None
            if (
                response.response
                and response.response.candidates
                and response.response.candidates[0].content
            ):
                for part in response.response.candidates[0].content.parts:
                    if hasattr(part, "inline_data") and part.inline_data:
                        image_bytes = part.inline_data.data
                        break

            if not image_bytes:
                results.append(
                    GridGenerationResult(
                        success=False,
                        error=f"Batch 响应 {i} 未返回图像数据",
                    )
                )
                continue

            # 保存文件
            if output_path:
                output_dir = os.path.dirname(output_path)
                if output_dir:
                    os.makedirs(output_dir, exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(image_bytes)

                # 后处理：gap removal
                try:
                    from novelvideo.generators.grid_splitter import remove_grid_gaps
                    from PIL import Image as PILImage

                    grid_img = PILImage.open(output_path)
                    grid_img = remove_grid_gaps(grid_img, req["rows"], req["cols"])
                    grid_img.save(output_path)
                except Exception as e:
                    print(f"[BatchAPI] Grid {i} gap removal 失败: {e}")

            results.append(
                GridGenerationResult(
                    success=True,
                    grid_image_path=output_path,
                    grid_image_bytes=image_bytes,
                )
            )

        print(f"[BatchAPI] 完成: {sum(1 for r in results if r.success)}/{len(results)} 成功")
        return results

    async def generate_grid_batch(
        self,
        all_beats: List[dict],
        character_map: Dict[str, dict] = None,
        scene_menu: list[dict] | list | None = None,
        prop_menu: list[dict] | list | None = None,
        sketch_colors: Dict[str, str] = None,
        style: str = None,
        output_dir: str = None,
        ethnicity: str = "Chinese",
        grid_size: int = 9,  # 3x3
        on_grid_complete: callable = None,  # 每个网格完成时的回调
        prompt_only: bool = False,  # Dry Run 模式：只生成提示词，不调用 API
        scene_grid_plan: List[dict] = None,  # 场景分组模式：scene_grid_split() 的输出
        sketch_dir: str = "",  # 草图目录路径（由调用方通过 PathResolver 计算）
        force_image_size: Optional[str] = None,  # 强制覆盖 image_size（如 "0.5K"）
    ) -> List[GridGenerationResult]:
        """批量生成多个网格图。

        将所有 beats 分成多个批次，每批次生成一个网格。
        每个网格生成后立即切割，更稳定且支持失败重试。

        Render 模式：
        - 如果调用方传入 sketch_dir 且目录中有草图文件，自动进入 Render 模式
        - 将草图作为参考，仅添加颜色和纹理

        Args:
            all_beats: 所有 beats 数据
            character_map: 角色映射
            style: 风格名称
            output_dir: 输出目录（保存网格图）
            ethnicity: 角色种族
            grid_size: 每个网格的面板数（默认 9 = 3x3）
            on_grid_complete: 每个网格完成时的回调函数 (batch_idx, result) -> None
        Returns:
            List[GridGenerationResult] - 每个网格的生成结果
        """
        import math
        from pathlib import Path

        results = []
        character_map = character_map or {}

        # 计算最大网格尺寸（用于动态选择的上限）
        max_grid_rows = int(math.sqrt(grid_size))
        max_grid_cols = max_grid_rows

        total_beats = len(all_beats)
        print(
            f"[NanoBananaPro Batch] 共 {total_beats} 个 beats，最大网格: {max_grid_rows}x{max_grid_cols}"
        )
        print("[NanoBananaPro Batch] 动态网格优化已启用（最小化黑色填充）")

        # 确保输出目录存在
        if output_dir:
            Path(output_dir).mkdir(parents=True, exist_ok=True)

        # ===== 场景分组模式：使用预计算的 plan =====
        if scene_grid_plan:
            grid_plan_tuples = [
                e.get("mode_key", f"{e['rows']}x{e['cols']}_1-1") for e in scene_grid_plan
            ]
            plan_beats_list = [e["beats"] for e in scene_grid_plan]
            loc_labels = [e.get("scene_id", "") for e in scene_grid_plan]
            scene_grid_labels = [
                f"{REGEN_MODE_CONFIGS[mk]['rows']}x{REGEN_MODE_CONFIGS[mk]['cols']}({loc})"
                for mk, loc in zip(grid_plan_tuples, loc_labels)
            ]
            print(
                f"[NanoBananaPro Batch] 场景分组模式: "
                f"{' + '.join(scene_grid_labels)} "
                f"(共 {len(grid_plan_tuples)} 个网格)"
            )
        else:
            # ===== 默认分割：完美分割 =====
            grid_plan_tuples = perfect_grid_split(total_beats, max_grid=grid_size)
            plan_beats_list = None  # 标记：从 all_beats 顺序取

            # 铁律 post-process（仅有参考图 & 非 prompt_only 时）
            all_beats_list = list(all_beats)
            if character_map and not prompt_only:
                final_plan = []
                offset = 0
                for mk in grid_plan_tuples:
                    cap = REGEN_MODE_CONFIGS[mk]["capacity"]
                    batch = all_beats_list[offset : offset + cap]
                    n_comp = _count_batch_composite_chars(batch, character_map)
                    if n_comp >= MANY_CHARS_REF_THRESHOLD and cap > MANY_CHARS_MAX_CAPACITY:
                        # 智能拆分：按 composite 连续分组，≤2 的子组用完整 pool
                        final_plan.extend(
                            _smart_repack_beats(
                                batch,
                                character_map,
                                DEFAULT_POOL_TEMPLATE,
                            )
                        )
                    else:
                        final_plan.append(mk)
                    offset += cap
                grid_plan_tuples = final_plan

            grid_capacities = [REGEN_MODE_CONFIGS[mk]["capacity"] for mk in grid_plan_tuples]
            grid_labels = [
                f"{REGEN_MODE_CONFIGS[mk]['rows']}x{REGEN_MODE_CONFIGS[mk]['cols']}"
                for mk in grid_plan_tuples
            ]
            print(
                f"[NanoBananaPro Batch] 完美分割方案: {' + '.join(grid_labels)} "
                f"= {sum(grid_capacities)} (共 {len(grid_plan_tuples)} 个网格，0 填充)"
            )

        # 按照分割方案逐个生成网格
        all_beats_list = list(all_beats)  # 复制列表
        processed_beats = 0

        for batch_idx, mk in enumerate(grid_plan_tuples):
            cfg = REGEN_MODE_CONFIGS[mk]
            batch_rows, batch_cols = cfg["rows"], cfg["cols"]
            batch_capacity = cfg["capacity"]

            if plan_beats_list is not None:
                # 场景分组模式：使用 plan 中的 beats（可能非连续）
                batch_beats = plan_beats_list[batch_idx]
            else:
                # 默认：顺序取 beats
                batch_beats = all_beats_list[processed_beats : processed_beats + batch_capacity]

            # 记录实际 beat 数量（用于日志）
            actual_beat_count = len(batch_beats)
            start_idx = processed_beats + 1
            end_idx = processed_beats + actual_beat_count

            # 角色过滤交给 generate_grid 内部完成，避免批量阶段重复解析引用。
            batch_character_map = character_map
            print(
                f"[NanoBananaPro Batch] 网格 {batch_idx + 1} 候选角色: "
                f"{list(batch_character_map.keys())}"
            )

            # 完美分割不需要填充，直接使用 batch_beats
            # （如果因为某种原因 beat 数量不匹配，这里会有问题，但理论上不会发生）
            if len(batch_beats) != batch_capacity:
                print(
                    f"[NanoBananaPro Batch] 警告: 网格 {batch_idx + 1} beat 数量不匹配 ({len(batch_beats)} vs {batch_capacity})"
                )

            # 生成网格图路径
            output_path = None
            if output_dir:
                output_path = str(Path(output_dir) / f"grid_{batch_idx + 1:02d}.png")

            print(
                f"[NanoBananaPro Batch] 生成网格 {batch_idx + 1} (beats {start_idx}-{end_idx}, 网格: {batch_rows}x{batch_cols})"
            )

            # 调用单网格生成（使用过滤后的角色映射和动态网格尺寸）
            result = await self.generate_grid(
                beats=batch_beats,
                character_map=batch_character_map,
                scene_menu=scene_menu,
                prop_menu=prop_menu,
                sketch_colors=sketch_colors,
                style=style,
                output_path=output_path,
                ethnicity=ethnicity,
                rows=batch_rows,  # 使用动态计算的行数
                cols=batch_cols,  # 使用动态计算的列数
                prompt_only=prompt_only,
                beat_start_index=processed_beats,  # Render 模式：传递起始索引用于 sketch 切片
                total_episode_beats=total_beats,  # Render 模式：传递整集 beat 总数
                location_beat_numbers=(
                    scene_grid_plan[batch_idx]["beat_numbers"] if scene_grid_plan else None
                ),
                sketch_dir=sketch_dir,
                mode_key=scene_grid_plan[batch_idx].get("mode_key") if scene_grid_plan else mk,
                force_image_size=force_image_size,
            )

            # 更新计数器（batch_idx 由 enumerate 自动管理）
            processed_beats += actual_beat_count

            results.append(result)

            # 回调通知
            if on_grid_complete:
                try:
                    on_grid_complete(batch_idx, result)
                except Exception as e:
                    print(f"[NanoBananaPro Batch] 回调错误: {e}")

            if not result.success:
                print(f"[NanoBananaPro Batch] 网格 {batch_idx + 1} 生成失败: {result.error}")
                # 继续生成下一个网格，不中断整个流程

        successful = sum(1 for r in results if r.success)
        total_grids = len(results)
        print(f"[NanoBananaPro Batch] 批量生成完成: {successful}/{total_grids} 成功")

        return results

    async def regenerate_single_grid(
        self,
        all_beats: List[dict],
        grid_index: int,  # 0-based grid index
        character_map: Dict[str, dict] = None,
        scene_menu: list[dict] | list | None = None,
        prop_menu: list[dict] | list | None = None,
        sketch_colors: Dict[str, str] = None,
        style: str = None,
        output_dir: str = None,
        ethnicity: str = "Chinese",
        grid_size: int = 9,  # 3x3
        prompt_only: bool = False,
        scene_grid_plan: List[dict] = None,  # 场景分组模式：scene_grid_split() 的输出
        sketch_dir: str = "",  # 草图目录路径（由调用方通过 PathResolver 计算）
        beat_sketch_paths: dict = None,  # {beat_num: full_path} 从图片池取的 per-beat 草图路径
        sketch_aspect_padding: bool = False,  # 草图补白到目标比例
        force_image_size: Optional[str] = None,  # 强制覆盖 image_size（如 "0.5K"）
    ) -> GridGenerationResult:
        """重新生成指定索引的单个网格。

        使用与 generate_grid_batch 相同的动态分割逻辑计算 beat 范围，
        然后只重新生成指定索引的网格。

        Args:
            all_beats: 所有 beats 数据
            grid_index: 要重新生成的网格索引 (0-based)
            character_map: 角色映射
            sketch_colors: 草图角色颜色映射
            style: 风格名称
            output_dir: 输出目录
            ethnicity: 角色种族
            grid_size: 每个网格的面板数（默认 9 = 3x3）
            prompt_only: 只生成提示词，不调用 API
            scene_grid_plan: 场景分组模式预计算的 plan

        Returns:
            GridGenerationResult - 重新生成的网格结果
        """
        from pathlib import Path

        character_map = character_map or {}

        # ===== 场景分组模式 =====
        if scene_grid_plan:
            if grid_index >= len(scene_grid_plan):
                return GridGenerationResult(
                    success=False,
                    error=f"网格索引 {grid_index} 超出范围（最大 {len(scene_grid_plan) - 1}）",
                    generation_time=0,
                )
            entry = scene_grid_plan[grid_index]
            target_batch_rows = entry["rows"]
            target_batch_cols = entry["cols"]
            target_batch_beats = entry["beats"]
            batch_capacity = target_batch_rows * target_batch_cols
            total_beats = len(all_beats)
            target_start_idx = 0  # 场景分组无连续起始索引
            loc_name = entry.get("scene_id", "")
            print(
                f"[NanoBananaPro Regen] 场景分组: 网格 {grid_index + 1} ({loc_name}, "
                f"{len(target_batch_beats)} beats, "
                f"网格: {target_batch_rows}x{target_batch_cols})"
            )
        else:
            # ===== 默认分割 =====
            total_beats = len(all_beats)
            grid_plan = perfect_grid_split(total_beats, max_grid=grid_size)

            if grid_index >= len(grid_plan):
                return GridGenerationResult(
                    success=False,
                    error=f"网格索引 {grid_index} 超出范围（最大 {len(grid_plan) - 1}）",
                    generation_time=0,
                )

            _mk = grid_plan[grid_index]
            _cfg = REGEN_MODE_CONFIGS[_mk]
            target_batch_rows, target_batch_cols = _cfg["rows"], _cfg["cols"]
            batch_capacity = _cfg["capacity"]
            target_start_idx = sum(
                REGEN_MODE_CONFIGS[m]["capacity"] for m in grid_plan[:grid_index]
            )
            target_batch_beats = list(all_beats)[
                target_start_idx : target_start_idx + batch_capacity
            ]

        actual_beat_count = len(target_batch_beats)
        if not scene_grid_plan:
            print(
                f"[NanoBananaPro Regen] 重新生成网格 {grid_index + 1} "
                f"(beats {target_start_idx + 1}-{target_start_idx + actual_beat_count}, "
                f"网格: {target_batch_rows}x{target_batch_cols})"
            )

        # 角色过滤交给 generate_grid 内部完成，避免重生阶段重复解析引用。
        batch_character_map = character_map
        print(
            f"[NanoBananaPro Regen] 网格 {grid_index + 1} 候选角色: "
            f"{list(batch_character_map.keys())}"
        )

        # 完美分割不需要填充，但检查以防万一
        if len(target_batch_beats) != batch_capacity:
            print(
                f"[NanoBananaPro Regen] 警告: 网格 {grid_index + 1} beat 数量不匹配 "
                f"({len(target_batch_beats)} vs {batch_capacity})"
            )

        # 生成网格图路径
        output_path = None
        if output_dir:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            output_path = str(Path(output_dir) / f"grid_{grid_index + 1:02d}.png")

        # 调用单网格生成
        result = await self.generate_grid(
            beats=target_batch_beats,
            character_map=batch_character_map,
            scene_menu=scene_menu,
            prop_menu=prop_menu,
            sketch_colors=sketch_colors,
            style=style,
            output_path=output_path,
            ethnicity=ethnicity,
            rows=target_batch_rows,
            cols=target_batch_cols,
            prompt_only=prompt_only,
            beat_start_index=target_start_idx,  # Render 模式：传递起始索引用于 sketch 切片
            total_episode_beats=total_beats,  # Render 模式：传递整集 beat 总数
            location_beat_numbers=entry["beat_numbers"] if scene_grid_plan else None,
            sketch_dir=sketch_dir,
            beat_sketch_paths=beat_sketch_paths,
            mode_key=entry.get("mode_key") if scene_grid_plan else grid_plan[grid_index],
            sketch_aspect_padding=sketch_aspect_padding,
            force_image_size=force_image_size,
        )

        if result.success:
            print(f"[NanoBananaPro Regen] 网格 {grid_index + 1} 重新生成成功")
        else:
            print(f"[NanoBananaPro Regen] 网格 {grid_index + 1} 重新生成失败: {result.error}")

        # 添加额外的元数据到结果
        result.beat_start_index = target_start_idx
        result.beat_count = actual_beat_count
        result.grid_rows = target_batch_rows
        result.grid_cols = target_batch_cols

        return result

    def _load_image_as_part(
        self,
        image_path: str,
        compress_quality: int = 60,
        min_short_side: int = 0,
    ):
        """加载图像作为 Gemini API 的 Part（带 JPEG 压缩）。

        Args:
            image_path: 图像路径
            compress_quality: JPEG 压缩质量 (1-100)，设为 0 或 None 禁用压缩
            min_short_side: 提交前放大参考图，避免小尺寸空间图被模型读丢

        Returns:
            Gemini Part 对象
        """
        try:
            from PIL import Image
            import io

            # OpenAI director refs are line-art geometry anchors. JPEG compression can erase
            # subtle table/window/stool lines, so keep those references lossless.
            if self.provider == "openai" and Path(image_path).name in {
                "director_sketch_ref.png",
                "director_color_ref.png",
            }:
                compress_quality = 0

            # 加载图片
            img = Image.open(image_path)
            original_size = os.path.getsize(image_path)
            original_dimensions = img.size

            if min_short_side > 0:
                short_side = min(img.size)
                if 0 < short_side < min_short_side:
                    scale = min_short_side / short_side
                    new_size = (
                        max(1, int(round(img.size[0] * scale))),
                        max(1, int(round(img.size[1] * scale))),
                    )
                    img = img.resize(new_size, Image.Resampling.LANCZOS)
                    print(
                        f"[参考图放大] {os.path.basename(image_path)}: "
                        f"{original_dimensions[0]}x{original_dimensions[1]} → "
                        f"{new_size[0]}x{new_size[1]}"
                    )

            # 压缩为 JPEG（如果启用）
            if compress_quality and compress_quality > 0:
                # 转为 RGB（JPEG 不支持 alpha）
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")

                # 压缩到内存
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=compress_quality, optimize=True)
                image_data = buffer.getvalue()
                mime_type = "image/jpeg"

                compressed_size = len(image_data)
                ratio = (1 - compressed_size / original_size) * 100
                print(
                    f"[压缩] {os.path.basename(image_path)}: "
                    f"{original_size/1024:.0f}KB → {compressed_size/1024:.0f}KB "
                    f"({ratio:.0f}% 压缩)"
                )
            else:
                if img.size != original_dimensions:
                    buffer = io.BytesIO()
                    img.save(buffer, format="PNG")
                    image_data = buffer.getvalue()
                    mime_type = "image/png"
                else:
                    # 不压缩，直接读取原文件
                    with open(image_path, "rb") as f:
                        image_data = f.read()

                    if image_path.lower().endswith(".png"):
                        mime_type = "image/png"
                    elif image_path.lower().endswith(".webp"):
                        mime_type = "image/webp"
                    else:
                        mime_type = "image/jpeg"

            if self.provider != "google":
                return _InlineImagePart(image_data, mime_type)

            from google.genai import types

            try:
                return types.Part.from_bytes(
                    data=image_data,
                    mime_type=mime_type,
                    media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
                )
            except (TypeError, AttributeError):
                # Gemini 2.x SDK 不支持 media_resolution
                return types.Part.from_bytes(data=image_data, mime_type=mime_type)

        except Exception as e:
            print(f"[NanoBananaPro] 加载参考图失败: {image_path}, {e}")
            return None

    def _append_reference_parts_from_plan(
        self,
        contents: list,
        ctx: PromptContext,
        ordered_chars: list[str],
        valid_character_map: dict,
        *,
        allowed_kinds: set[str] | None = None,
        verbose: bool = False,
        audit_refs: list[dict] | None = None,
    ) -> None:
        """按 prompt 中的统一图片计划追加实际附件。"""
        plan = PromptComponents.build_reference_image_plan(ctx, ordered_chars)

        for entry in plan:
            kind = entry.get("kind")
            if allowed_kinds is not None and kind not in allowed_kinds:
                continue
            if kind == "combined_composite":
                sheets = []
                names = []
                try:
                    from PIL import Image

                    for item in entry.get("items", []):
                        char_name = item.get("char_name", "")
                        info = valid_character_map.get(char_name) or {}
                        ref_path = item.get("path") or info.get("reference_path")
                        if not ref_path or not os.path.exists(ref_path):
                            continue
                        sheet = Image.open(ref_path)
                        if sheet.mode in ("RGBA", "P"):
                            sheet = sheet.convert("RGB")
                        sheets.append(sheet)
                        names.append(char_name)
                        if verbose:
                            print(
                                f"[NanoBananaPro] 添加完整多视图参考 sheet: {char_name} -> "
                                f"{sheet.size[0]}x{sheet.size[1]}px"
                            )
                    if sheets:
                        merged_part = self._merge_character_panels(sheets)
                        if merged_part:
                            contents.append(merged_part)
                            if verbose:
                                print(f"[NanoBananaPro] 多人完整 sheet 合并参考图: {names}")
                finally:
                    for sheet in sheets:
                        try:
                            sheet.close()
                        except Exception:
                            pass
            elif kind in {"composite", "portrait_only", "identity_portrait"}:
                path = entry.get("path")
                if not path or not os.path.exists(path):
                    continue
                ref_image = self._load_image_as_part(path)
                if not ref_image:
                    continue
                contents.append(ref_image)
                if audit_refs is not None:
                    audit_refs.append(
                        {
                            "kind": kind,
                            "base_id": entry.get("char_name", ""),
                            "path": path,
                            "bytes": os.path.getsize(path) if os.path.exists(path) else None,
                        }
                    )
                if not verbose:
                    continue
                if kind == "composite":
                    print(
                        "[NanoBananaPro] 添加参考图 (复合图): "
                        f"{entry.get('char_name', '')} -> {path}"
                    )
                elif kind == "portrait_only":
                    print(
                        "[NanoBananaPro] 添加参考图 (Portrait): "
                        f"{entry.get('char_name', '')} -> {path}"
                    )
                else:
                    print(
                        "[NanoBananaPro] 添加身份级 Portrait (年龄变体): "
                        f"{entry.get('char_name', '')}/{entry.get('tag', '')}"
                    )
            elif kind in {"scene", "prop"}:
                ref = entry.get("ref")
                path = (getattr(ref, "image_paths", []) or [""])[0]
                if not path or not os.path.exists(path):
                    continue
                source_level = str(getattr(ref, "source_level", "") or "").strip()
                compress_quality = 0 if source_level == "scene_spatial_layout" else 60
                min_short_side = 720 if source_level == "scene_spatial_layout" else 0
                ref_image = self._load_image_as_part(
                    path,
                    compress_quality=compress_quality,
                    min_short_side=min_short_side,
                )
                if not ref_image:
                    continue
                contents.append(ref_image)
                if audit_refs is not None:
                    audit_refs.append(
                        {
                            "kind": kind,
                            "base_id": getattr(ref, "base_id", ""),
                            "variant_id": getattr(ref, "variant_id", ""),
                            "source_level": getattr(ref, "source_level", ""),
                            "path": path,
                            "bytes": os.path.getsize(path) if os.path.exists(path) else None,
                            "submitted_min_short_side": min_short_side or None,
                        }
                    )
                if verbose:
                    label = "场景" if kind == "scene" else "道具"
                    try:
                        size_bytes = os.path.getsize(path)
                    except OSError:
                        size_bytes = -1
                    print(
                        f"[NanoBananaPro][RefPlan] kind={kind} "
                        f"base_id={getattr(ref, 'base_id', '')} "
                        f"path={path} bytes={size_bytes}"
                    )
                    print(f"[NanoBananaPro] 添加{label}参考图: " f"{getattr(ref, 'base_id', '')}")

    def _extract_ref_bytes_from_contents(
        self, contents: list, *, include_mime: bool = False
    ) -> tuple:
        """从 contents 列表提取 prompt 文本和参考图 bytes（用于 OpenRouter）。"""
        prompt_text = ""
        ref_bytes = []
        for item in contents:
            if isinstance(item, str):
                prompt_text = item
            elif hasattr(item, "inline_data") and item.inline_data:
                if include_mime:
                    ref_bytes.append(
                        (
                            item.inline_data.data,
                            getattr(item.inline_data, "mime_type", "image/png") or "image/png",
                        )
                    )
                else:
                    ref_bytes.append(item.inline_data.data)
        return prompt_text, ref_bytes

    def _crop_center_panel(self, image_path: str):
        """从 3 面板 sheet 裁出中间面板（正面全身）。"""
        from PIL import Image

        img = Image.open(image_path)
        w, h = img.size
        panel_w = w // 3
        return img.crop((panel_w, 0, panel_w * 2, h))

    def _merge_character_panels(self, panels: list, compress_quality: int = 60):
        """将多个角色参考图水平拼接，压缩后返回 Gemini Part。"""
        from PIL import Image

        if not panels:
            return None

        # 统一高度（取最大），等比缩放。
        max_h = max(p.size[1] for p in panels)
        resized = []
        for p in panels:
            if p.size[1] != max_h:
                ratio = max_h / p.size[1]
                new_w = int(p.size[0] * ratio)
                p = p.resize((new_w, max_h), Image.LANCZOS)
            resized.append(p)

        # 水平拼接。
        total_w = sum(p.size[0] for p in resized)
        merged = Image.new("RGB", (total_w, max_h))
        x_offset = 0
        for p in resized:
            if p.mode in ("RGBA", "P"):
                p = p.convert("RGB")
            merged.paste(p, (x_offset, 0))
            x_offset += p.size[0]

        # JPEG 压缩。
        buffer = io.BytesIO()
        merged.save(buffer, format="JPEG", quality=compress_quality, optimize=True)
        image_data = buffer.getvalue()
        print(
            f"[NanoBananaPro] 合并参考图: {len(panels)} 角色, "
            f"{total_w}x{max_h}px, {len(image_data)/1024:.0f}KB"
        )

        if self.provider != "google":
            return _InlineImagePart(image_data, "image/jpeg")

        from google.genai import types

        try:
            return types.Part.from_bytes(
                data=image_data,
                mime_type="image/jpeg",
                media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
            )
        except (TypeError, AttributeError):
            return types.Part.from_bytes(data=image_data, mime_type="image/jpeg")

    async def _generate_render_from_sketch(
        self,
        sketch_path: str,
        prompt: str,
        beats: List[dict],
        character_map: Dict[str, dict],
        output_path: str,
        rows: int,
        cols: int,
        style: str,
        total_episode_beats: int = 0,
        beat_start_index: int = 0,
        mode_key: Optional[str] = None,
        sketch_aspect_padding: bool = False,
    ) -> GridGenerationResult:
        """渲染模式核心逻辑：切分草图 -> 并行渲染 -> 拼合网格。

        支持多草图模式：根据 beat_start_index 查找对应的草图文件，
        然后用本地偏移切出正确的 panel。
        """
        from PIL import Image

        # 1. 查找覆盖当前 beat 范围的草图文件
        try:
            beat_range_start = beat_start_index + 1  # 1-based
            beat_range_end = beat_start_index + len(beats)

            # 尝试从草图目录查找
            sketch_dir_path = (
                str(Path(sketch_path).parent) if os.path.isfile(sketch_path) else sketch_path
            )
            sketch_result = find_sketch_for_beat_range(
                sketch_dir_path, beat_range_start, beat_range_end
            )

            if sketch_result:
                actual_sketch_file, s_rows, s_cols = sketch_result
                file_start = int(Path(actual_sketch_file).stem.split("_b")[1].split("-")[0])
                local_offset = beat_start_index - (file_start - 1)
            else:
                # 回退：直接使用传入的 sketch_path
                actual_sketch_file = sketch_path
                s_rows = SKETCH_GRID_CONFIG["rows"]
                s_cols = SKETCH_GRID_CONFIG["cols"]
                local_offset = beat_start_index  # 单文件 fallback
                print(f"[Render] 回退：使用传入草图 {sketch_path}")

            sketch_img = Image.open(actual_sketch_file)
            sketch_w, sketch_h = sketch_img.size

            panel_w = sketch_w // s_cols
            panel_h = sketch_h // s_rows

            print(f"[Render] Sketch: {actual_sketch_file} ({s_rows}x{s_cols})")
            print(f"[Render] Panel 尺寸: {panel_w}x{panel_h}")

            # 2. 切分草图得到所有 panel
            all_panels = []
            for r in range(s_rows):
                for c in range(s_cols):
                    box = (c * panel_w, r * panel_h, (c + 1) * panel_w, (r + 1) * panel_h)
                    panel = sketch_img.crop(box)
                    all_panels.append(panel)

            # 3. 根据 local_offset 取对应的 panel
            panels = all_panels[local_offset : local_offset + len(beats)]

            print(
                f"[Render] 从 {len(all_panels)} 个 sketch panel 中取 [local {local_offset}:{local_offset + len(beats)}] = {len(panels)} panels"
            )

        except Exception as e:
            return GridGenerationResult(success=False, error_message=f"Failed to slice sketch: {e}")

        # 2. 准备并行任务
        if not output_path:
            # Default path if none provided
            output_path = "output/render_grid_temp.png"
            os.makedirs("output", exist_ok=True)

        temp_dir = os.path.dirname(output_path)
        if not temp_dir:
            temp_dir = "."
        os.makedirs(temp_dir, exist_ok=True)

        # 3. 准备并行任务 (使用 NanoBanana/Gemini)
        tasks = []

        for i, beat in enumerate(beats):
            if i >= len(panels):
                break

            panel_idx = i + 1
            panel_img = panels[i]

            # 草图补白到目标比例（sketch_aspect_padding）
            target_ar = None
            if sketch_aspect_padding and mode_key:
                target_ar = cell_aspect_ratio(mode_key)
                if target_ar:
                    panel_img = pad_to_aspect_ratio(panel_img, target_ar)

            # 保存切片临时文件
            slice_path = os.path.join(temp_dir, f"temp_sketch_slice_{panel_idx}.jpg")
            panel_img.convert("RGB").save(slice_path, "JPEG", quality=95)

            # 提取当前 panel 的角色及其参考图（核心：一致性）
            panel_char_refs = []  # 角色参考图路径列表
            char_descriptions = []  # 角色描述列表

            vis = beat.get("visual_description", "")
            from novelvideo.models import extract_char_identities_from_markers

            char_identities = extract_char_identities_from_markers(vis, strict=False)
            for char_name, info in character_map.items():
                # 检查角色是否出现在当前 panel（通过名字或标签）
                if char_name in vis:
                    # 收集角色参考图
                    ref_path = info.get("reference_path")
                    if ref_path and os.path.exists(ref_path):
                        panel_char_refs.append(ref_path)
                    # 收集角色描述（使用 [CharTag]）
                    from novelvideo.utils.identity_resolver import compute_char_tag

                    identity_id = char_identities.get(char_name, None)
                    tag = compute_char_tag(char_name, identity_id=identity_id)
                    base_prompt = info.get("base_prompt", char_name)
                    char_descriptions.append(f"{tag}: {base_prompt}")

            # 构建单张 Prompt（Render 模式简化版：草图已定义构图，只需角色+环境+风格）
            scene_id = beat_scene_id(beat) or "Scene"
            # 替换 {{}} 标记为 identity_id（兼容 {{identity_id}} 和 {{角色名}}）
            from novelvideo.utils.identity_resolver import (
                resolve_visual_description_markers,
                build_identity_to_char_map,
            )

            id_to_char = build_identity_to_char_map(character_map)
            visual_desc = resolve_visual_description_markers(
                vis, character_map, id_to_char, use_identity_id=True
            )

            # 构建提示词：草图参考 + 角色定义 + 场景 + 风格
            char_section = "; ".join(char_descriptions) if char_descriptions else ""
            scene_desc = f"{scene_id}. {visual_desc}"
            panel_project_dir = _infer_project_dir(output_path)
            style_finish = (
                "Dynamic cinematic lighting, stylized animated finish, high detail."
                if StyleService.is_animation_style(style, project_dir=panel_project_dir)
                else "Cinematic lighting, photorealistic, 8k."
            )
            simple_prompt = f"""Render this sketch into high-quality colored image.
CHARACTERS (match face references): {char_section}
SCENE: {scene_desc}
STYLE: {style}. {style_finish}
CRITICAL: Keep exact composition from sketch. Only add color, texture, and lighting."""

            # 输出路径
            panel_output_path = os.path.join(temp_dir, f"render_panel_{panel_idx}.png")

            print(
                f"[Render] Panel {panel_idx}: {len(panel_char_refs)} character refs, prompt: {simple_prompt[:60]}..."
            )

            # 使用 Gemini 渲染单张（传入角色参考图）
            task = self._render_single_panel_gemini(
                sketch_path=slice_path,
                prompt=simple_prompt,
                output_path=panel_output_path,
                character_refs=panel_char_refs,  # 核心：传入角色参考图
                target_aspect_ratio=target_ar,
            )
            tasks.append(task)

        # 4. 执行并行渲染 (Gemini 可能会有速率限制，建议用 semaphore 控制)
        # 简单起见，这里先全部并发，如果遇到资源耗尽再调整
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 5. 收集结果并拼合
        rendered_panels = []
        success_count = 0

        for idx, res in enumerate(results):
            if isinstance(res, Exception):
                print(f"[Render] Panel {idx+1} failed: {res}")
                rendered_panels.append(panels[idx])  # 回退到草图
            elif (
                not res
            ):  # res is success bool or path? _render_single_panel_gemini returns path if success
                print(f"[Render] Panel {idx+1} failed (Empty result)")
                rendered_panels.append(panels[idx])
            else:
                # 加载渲染好的图
                try:
                    img = Image.open(res)
                    rendered_panels.append(img)
                    success_count += 1
                except Exception:
                    rendered_panels.append(panels[idx])

        print(f"[Render] Completed {success_count}/{len(beats)} panels.")

        # 6. 拼合回网格（补白后 panel 尺寸可能变化）
        final_pw, final_ph = panel_w, panel_h
        if sketch_aspect_padding and mode_key:
            pad_ar = cell_aspect_ratio(mode_key)
            if pad_ar:
                sample = pad_to_aspect_ratio(Image.new("RGB", (panel_w, panel_h)), pad_ar)
                final_pw, final_ph = sample.size

        final_grid = Image.new("RGB", (final_pw * cols, final_ph * rows))
        for i, p_img in enumerate(rendered_panels):
            r = i // cols
            c = i % cols
            if p_img.size != (final_pw, final_ph):
                p_img = p_img.resize((final_pw, final_ph), Image.Resampling.LANCZOS)
            final_grid.paste(p_img, (c * final_pw, r * final_ph))

        final_grid.save(output_path)
        print(f"[Render] Final grid assembled: {output_path}")

        return GridGenerationResult(success=True, grid_image_path=output_path)

    async def _render_single_panel_gemini(
        self,
        sketch_path: str,
        prompt: str,
        output_path: str,
        character_refs: List[str] = None,  # 角色参考图路径列表
        target_aspect_ratio: str = None,  # 保留参数但不传 ImageConfig（edit 模式靠补白，不靠 resize）
    ) -> Optional[str]:
        """使用 Gemini 渲染单个分镜切片。

        Args:
            sketch_path: 草图切片路径
            prompt: 渲染提示词
            output_path: 输出路径
            character_refs: 当前 panel 出现的角色参考图路径列表（用于一致性）
            target_aspect_ratio: 保留参数（未来扩展），当前不使用
        """
        # 加载草图
        sketch_part = self._load_image_as_part(sketch_path)
        if not sketch_part:
            return None

        # 构建 contents: [prompt, sketch, character_refs...]
        # 顺序：prompt 在前，草图紧随，角色参考图在后
        contents = [prompt, sketch_part]

        # 添加角色参考图（核心：实现一致性）
        if character_refs:
            for ref_path in character_refs:
                ref_part = self._load_image_as_part(ref_path)
                if ref_part:
                    contents.append(ref_part)

        try:
            if self.provider == "openrouter":
                # ===== OpenRouter 分支 =====
                prompt_text, ref_bytes = self._extract_ref_bytes_from_contents(contents)
                image_bytes, _, _ = await _gg._call_openrouter_image_api(
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt_text,
                    reference_images=ref_bytes or None,
                )
                if image_bytes:
                    with open(output_path, "wb") as f:
                        f.write(image_bytes)
                    return output_path
                return None
            elif self.provider == "huimeng":
                prompt_text, ref_bytes = self._extract_ref_bytes_from_contents(contents)
                image_bytes, _, error_detail = await _gg._call_huimeng_image_api(
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt_text,
                    reference_images=ref_bytes or None,
                    image_config={
                        "aspect_ratio": target_aspect_ratio or "9:16",
                        "image_size": "1K",
                    },
                )
                if image_bytes:
                    with open(output_path, "wb") as f:
                        f.write(image_bytes)
                    return output_path
                if error_detail:
                    print(f"[HuiMeng Render] 失败: {error_detail}")
                return None
            elif self.provider == "openai":
                # ===== OpenAI Image API 分支 =====
                prompt_text, ref_bytes = self._extract_ref_bytes_from_contents(
                    contents,
                    include_mime=True,
                )
                image_bytes, _, error_detail = await _gg._call_openai_image_api(
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt_text,
                    reference_images=ref_bytes or None,
                    image_config={
                        "aspect_ratio": target_aspect_ratio or "9:16",
                        "image_size": "1K",
                        "output_format": "png",
                    },
                )
                if image_bytes:
                    with open(output_path, "wb") as f:
                        f.write(image_bytes)
                    return output_path
                if error_detail:
                    print(f"[OpenAI Render] 失败: {error_detail}")
                return None
            elif self.provider == "newapi":
                prompt_text, ref_bytes = self._extract_ref_bytes_from_contents(
                    contents,
                    include_mime=True,
                )
                image_bytes, _, error_detail = await _call_newapi_image_api_with_egress(
                    capability="image.generate.render_panel",
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt_text,
                    reference_images=ref_bytes or None,
                    image_config={
                        "aspect_ratio": target_aspect_ratio or "9:16",
                        "image_size": "1K",
                        "quality": self.openai_image_quality,
                    },
                    base_url=self.base_url,
                    delivery_path=output_path,
                    delivery_state=(image_delivery_state := {}),
                    read_copied_bytes=False,
                )
                if image_bytes or image_delivery_state.get("copied"):
                    if not image_delivery_state.get("copied"):
                        with open(output_path, "wb") as f:
                            f.write(image_bytes)
                    return output_path
                if error_detail:
                    print(f"[DramaClawAPI Render] 失败: {error_detail}")
                return None
            else:
                # ===== Google 直连分支 =====
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=self.api_key)
                model_name = self.model

                # 注意：edit 模式不传 ImageConfig(aspect_ratio)，
                # 否则 Gemini 会拉伸而非 outpaint。补白后的草图已经是目标比例。
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE"],
                    ),
                )

                if response.candidates:
                    # 提取图像
                    for part in response.candidates[0].content.parts:
                        if part.inline_data:
                            image_bytes = base64.b64decode(part.inline_data.data)
                            with open(output_path, "wb") as f:
                                f.write(image_bytes)
                            return output_path
                return None
        except Exception as e:
            print(f"Gemini Render Error: {e}")
            return None

    async def upscale_image(
        self,
        input_path: str,
        output_path: str,
        original_prompt: str,
        style: str = None,
        target_width: int = 720,
        target_height: int = 1280,
    ) -> Path:
        """使用 NanoBananaPro 做高清修复。

        将网格切割的小图(~819x819)转换为竖屏图(768x1376)，再缩放到目标尺寸。

        Args:
            input_path: 输入图片路径（网格分割后的小图）
            output_path: 输出图片路径
            original_prompt: 原始场景描述（用于指导生成）
            style: 风格名称，默认使用全局配置
            target_width: 目标宽度（默认 720）
            target_height: 目标高度（默认 1280）

        Returns:
            输出图片路径
        """
        from PIL import Image

        # 使用全局默认风格
        if style is None:
            style = IMAGE_DEFAULT_STYLE

        # 获取风格预设
        style_preset = get_style_preset(
            style, project_dir=str(_infer_project_dir(output_path, input_path) or "")
        )
        style_keywords = style_preset.get("style_instructions", "")

        # 加载原图作为参考
        ref_image = self._load_image_as_part(input_path)
        if not ref_image:
            raise ValueError(f"无法加载参考图: {input_path}")

        # 构建高清修复 Prompt
        prompt = f"""Based on this reference image, create a high-quality vertical (9:16) version.

REFERENCE IMAGE: The image I provided shows the scene to recreate.

REQUIREMENTS:
- Maintain the EXACT same composition, characters, and scene
- Keep all visual elements identical to the reference
- Output in portrait orientation (9:16)
- Style: {style_keywords}
- Quality: detailed, high quality

SCENE DESCRIPTION: {original_prompt}

CRITICAL: The output must look like a higher-resolution vertical crop/extension of the reference image, NOT a completely new image. Keep the same characters, poses, and scene elements.
"""

        try:
            print(f"[NanoBananaPro Upscale] 处理: {input_path}")

            if self.provider == "openrouter":
                # ===== OpenRouter 分支 =====
                # 提取参考图 bytes
                ref_bytes = []
                if hasattr(ref_image, "inline_data") and ref_image.inline_data:
                    ref_bytes.append(ref_image.inline_data.data)
                image_bytes, _, or_error = await _gg._call_openrouter_image_api(
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt,
                    reference_images=ref_bytes or None,
                    image_config={"aspect_ratio": "9:16", "image_size": "1K"},
                )
                if not image_bytes:
                    raise ValueError(
                        f"OpenRouter API 未返回图像数据: {or_error}"
                        if or_error
                        else "OpenRouter API 未返回图像数据"
                    )

                # 保存为临时文件并缩放
                temp_path = output_path + ".tmp.png"
                with open(temp_path, "wb") as f:
                    f.write(image_bytes)
                img = Image.open(temp_path)
                img = img.resize((target_width, target_height), Image.Resampling.LANCZOS)
                img.save(output_path)
                Path(temp_path).unlink()
                print(f"[NanoBananaPro Upscale] 完成: {output_path}")
                return Path(output_path)
            elif self.provider == "huimeng":
                ref_bytes = []
                if hasattr(ref_image, "inline_data") and ref_image.inline_data:
                    ref_bytes.append(ref_image.inline_data.data)
                image_bytes, _, huimeng_error = await _gg._call_huimeng_image_api(
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt,
                    reference_images=ref_bytes or None,
                    image_config={"aspect_ratio": "9:16", "image_size": "1K"},
                )
                if not image_bytes:
                    raise ValueError(
                        f"HuiMeng Images 未返回图像数据: {huimeng_error}"
                        if huimeng_error
                        else "HuiMeng Images 未返回图像数据"
                    )

                temp_path = output_path + ".tmp.png"
                with open(temp_path, "wb") as f:
                    f.write(image_bytes)
                img = Image.open(temp_path)
                img = img.resize((target_width, target_height), Image.Resampling.LANCZOS)
                img.save(output_path)
                Path(temp_path).unlink()
                print(f"[NanoBananaPro Upscale] 完成: {output_path}")
                return Path(output_path)
            elif self.provider == "openai":
                ref_bytes = []
                if hasattr(ref_image, "inline_data") and ref_image.inline_data:
                    ref_bytes.append(
                        (
                            ref_image.inline_data.data,
                            getattr(ref_image.inline_data, "mime_type", "image/png") or "image/png",
                        )
                    )
                image_bytes, _, openai_error = await _gg._call_openai_image_api(
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt,
                    reference_images=ref_bytes or None,
                    image_config={
                        "aspect_ratio": "9:16",
                        "image_size": "1K",
                        "output_format": "png",
                    },
                )
                if not image_bytes:
                    raise ValueError(
                        f"OpenAI Image API 未返回图像数据: {openai_error}"
                        if openai_error
                        else "OpenAI Image API 未返回图像数据"
                    )

                temp_path = output_path + ".tmp.png"
                with open(temp_path, "wb") as f:
                    f.write(image_bytes)
                img = Image.open(temp_path)
                img = img.resize((target_width, target_height), Image.Resampling.LANCZOS)
                img.save(output_path)
                Path(temp_path).unlink()
                print(f"[NanoBananaPro Upscale] 完成: {output_path}")
                return Path(output_path)
            elif self.provider == "newapi":
                ref_bytes = []
                if hasattr(ref_image, "inline_data") and ref_image.inline_data:
                    ref_bytes.append(
                        (
                            ref_image.inline_data.data,
                            getattr(ref_image.inline_data, "mime_type", "image/png") or "image/png",
                        )
                    )
                image_bytes, _, newapi_error = await _call_newapi_image_api_with_egress(
                    capability="image.edit.upscale",
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt,
                    reference_images=ref_bytes or None,
                    image_config={
                        "aspect_ratio": "9:16",
                        "image_size": "1K",
                        "quality": self.openai_image_quality,
                    },
                    base_url=self.base_url,
                    delivery_path=output_path + ".tmp.png",
                    delivery_state=(image_delivery_state := {}),
                    read_copied_bytes=False,
                )
                if not image_bytes and not image_delivery_state.get("copied"):
                    raise ValueError(
                        f"DramaClawAPI Images 未返回图像数据: {newapi_error}"
                        if newapi_error
                        else "DramaClawAPI Images 未返回图像数据"
                    )

                temp_path = output_path + ".tmp.png"
                if not image_delivery_state.get("copied"):
                    with open(temp_path, "wb") as f:
                        f.write(image_bytes)
                img = Image.open(temp_path)
                img = img.resize((target_width, target_height), Image.Resampling.LANCZOS)
                img.save(output_path)
                Path(temp_path).unlink()
                print(f"[NanoBananaPro Upscale] 完成: {output_path}")
                return Path(output_path)
            else:
                # ===== Google 直连分支 =====
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=self.api_key)

                # gemini-3 支持 image_size，gemini-2.5 不支持
                is_gemini3 = "gemini-3" in self.model
                if is_gemini3:
                    image_config = types.ImageConfig(
                        aspect_ratio="9:16",
                        image_size="1K",  # 768x1376 (接近目标 720x1280)
                    )
                else:
                    image_config = types.ImageConfig(
                        aspect_ratio="9:16",
                    )

                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=self.model,
                    contents=[prompt, ref_image],
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE", "TEXT"],
                        image_config=image_config,
                    ),
                )

                # 提取图像
                for part in response.candidates[0].content.parts:
                    if hasattr(part, "inline_data") and part.inline_data:
                        # 保存为临时文件
                        temp_path = output_path + ".tmp.png"
                        with open(temp_path, "wb") as f:
                            f.write(part.inline_data.data)

                        # 缩放到目标尺寸 (768x1376 → 720x1280)
                        img = Image.open(temp_path)
                        img = img.resize((target_width, target_height), Image.Resampling.LANCZOS)
                        img.save(output_path)

                        # 删除临时文件
                        Path(temp_path).unlink()

                        print(f"[NanoBananaPro Upscale] 完成: {output_path}")
                        return Path(output_path)

                raise ValueError("API 未返回图像数据")

        except Exception as e:
            print(f"[NanoBananaPro Upscale] 失败: {e}")
            raise

    async def generate_single_preview(
        self,
        prompt: str,
        style_config: dict,
        reference_images: List[str] = None,
        output_path: str = None,
    ) -> bytes:
        """生成单张预览图用于风格测试。

        使用 1x1 网格模式快速生成单张图片，用于风格实验室测试。

        Args:
            prompt: 场景描述（中文或英文）
            style_config: 完整风格配置字典，包含：
                - style_instructions: Gemini 风格指令
                - avoid_instructions: Gemini 避免指令
            reference_images: 参考图路径列表（可选）
            output_path: 输出路径（可选）

        Returns:
            生成的图像 bytes 数据
        """
        start_time = time.time()

        # 提取风格指令
        style_instructions = style_config.get("style_instructions", "")
        avoid_instructions = style_config.get("avoid_instructions", "")

        # 构建完整 Prompt
        full_prompt = f"""Generate a single portrait image (9:16 aspect ratio).

SCENE DESCRIPTION:
{prompt}

STYLE REQUIREMENTS:
{style_instructions}

AVOID:
{avoid_instructions}

OUTPUT: Single high-quality image, no watermarks, no text overlays.
"""

        try:
            # 准备内容
            contents = [full_prompt]

            # 添加参考图（如果有）
            if reference_images:
                for ref_path in reference_images:
                    if os.path.exists(ref_path):
                        ref_image = self._load_image_as_part(ref_path)
                        if ref_image:
                            contents.append(ref_image)
                            print(f"[StylePreview] 添加参考图: {ref_path}")

            if self.provider == "openrouter":
                # ===== OpenRouter 分支 =====
                print(f"[StylePreview] 调用 OpenRouter ({self.model}) 生成预览图...")
                prompt_text, ref_bytes = self._extract_ref_bytes_from_contents(contents)
                image_bytes, _, _ = await _gg._call_openrouter_image_api(
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt_text,
                    reference_images=ref_bytes or None,
                    image_config={"aspect_ratio": "9:16", "image_size": "1K"},
                )
            elif self.provider == "huimeng":
                print(f"[StylePreview] 调用 HuiMeng Images ({self.model}) 生成预览图...")
                prompt_text, ref_bytes = self._extract_ref_bytes_from_contents(contents)
                image_bytes, _, error_detail = await _gg._call_huimeng_image_api(
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt_text,
                    reference_images=ref_bytes or None,
                    image_config={"aspect_ratio": "9:16", "image_size": "1K"},
                )
                if not image_bytes and error_detail:
                    print(f"[StylePreview] HuiMeng 失败详情: {error_detail}")
            elif self.provider == "openai":
                # ===== OpenAI Image API 分支 =====
                print(f"[StylePreview] 调用 OpenAI Image API ({self.model}) 生成预览图...")
                prompt_text, ref_bytes = self._extract_ref_bytes_from_contents(
                    contents,
                    include_mime=True,
                )
                image_bytes, _, error_detail = await _gg._call_openai_image_api(
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt_text,
                    reference_images=ref_bytes or None,
                    image_config={
                        "aspect_ratio": "9:16",
                        "image_size": "1K",
                        "output_format": "png",
                    },
                )
                if not image_bytes and error_detail:
                    print(f"[StylePreview] OpenAI 失败详情: {error_detail}")
            elif self.provider == "newapi":
                print(f"[StylePreview] 调用 DramaClawAPI Images ({self.model}) 生成预览图...")
                prompt_text, ref_bytes = self._extract_ref_bytes_from_contents(
                    contents,
                    include_mime=True,
                )
                image_bytes, _, error_detail = await _call_newapi_image_api_with_egress(
                    capability="image.generate.preview",
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt_text,
                    reference_images=ref_bytes or None,
                    image_config={
                        "aspect_ratio": "9:16",
                        "image_size": "1K",
                        "quality": self.openai_image_quality,
                    },
                    base_url=self.base_url,
                    delivery_path=output_path,
                    delivery_state=(image_delivery_state := {}),
                )
                if not image_bytes and error_detail:
                    print(f"[StylePreview] DramaClawAPI 失败详情: {error_detail}")
            else:
                # ===== Google 直连分支 =====
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=self.api_key)

                # 调用 API - 使用 1x1 竖屏模式
                is_gemini3 = "gemini-3" in self.model
                if is_gemini3:
                    image_config = types.ImageConfig(
                        aspect_ratio="9:16",
                        image_size="1K",
                    )
                else:
                    image_config = types.ImageConfig(
                        aspect_ratio="9:16",
                    )

                print(f"[StylePreview] 调用 {self.model} 生成预览图...")

                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=self.model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE", "TEXT"],
                        image_config=image_config,
                    ),
                )

                # 提取图像数据
                image_bytes = None
                if response.candidates and response.candidates[0].content:
                    for part in response.candidates[0].content.parts:
                        if hasattr(part, "inline_data") and part.inline_data:
                            image_bytes = part.inline_data.data
                            break

            if not image_bytes:
                raise ValueError("API 未返回图像数据")

            # 保存文件（如果指定了输出路径）
            if output_path:
                output_dir = os.path.dirname(output_path)
                if output_dir:
                    os.makedirs(output_dir, exist_ok=True)
                if not (self.provider == "newapi" and image_delivery_state.get("copied")):
                    with open(output_path, "wb") as f:
                        f.write(image_bytes)
                print(f"[StylePreview] 预览图已保存: {output_path}")

            generation_time = time.time() - start_time
            print(f"[StylePreview] 生成完成，耗时 {generation_time:.1f}s")

            return image_bytes

        except Exception as e:
            print(f"[StylePreview] 生成失败: {e}")
            raise

    async def generate_shot_grid(
        self,
        shot_beats: List[dict],
        character_map: Dict[str, dict] = None,
        scene_menu: list[dict] | list | None = None,
        prop_menu: list[dict] | list | None = None,
        style: str = None,
        output_path: Optional[str] = None,
        ethnicity: str = "Chinese",
        sketch_dir: str = "",
        beat_sketch_paths: dict = None,
    ) -> GridGenerationResult:
        """生成 Shot 级 Grid（v2.0 Shot-Centric）。

        一个 Shot 内的 N 个 beats → 1 个 Grid 图，用作 Seedance 2.0 的 @图片 分镜参考。
        Grid 格式根据 beat 数自动选择：1→1x1, 2→1x2, 3→1x3, 4→2x2, 5→3x3(前5格填充)。

        该 Grid 直接作为整张图喂给 Seedance 2.0，不裁切。

        Args:
            shot_beats: Shot 内的 beats 列表（1-5 个）
            character_map: 角色映射
            style: 风格名称
            output_path: 输出路径
            ethnicity: 角色种族
            sketch_dir: 草图目录
            beat_sketch_paths: per-beat 草图路径

        Returns:
            GridGenerationResult
        """
        beat_count = len(shot_beats)
        cfg = get_shot_grid_config(beat_count)
        rows, cols = cfg["rows"], cfg["cols"]
        aspect_ratio = cfg["aspect_ratio"]
        image_size = cfg["image_size"]

        print(f"[ShotGrid] 生成 Shot Grid: {beat_count} beats → {rows}x{cols} ({aspect_ratio})")

        return await self.generate_grid(
            beats=shot_beats,
            character_map=character_map,
            scene_menu=scene_menu,
            prop_menu=prop_menu,
            style=style,
            output_path=output_path,
            ethnicity=ethnicity,
            rows=rows,
            cols=cols,
            sketch_dir=sketch_dir,
            aspect_ratio_override=aspect_ratio,
            image_size_override=image_size,
            beat_sketch_paths=beat_sketch_paths,
        )
