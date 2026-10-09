"""编排引擎：加载候选 → 调用 T1-T4 → 选择。

原子工具：对每个 beat 执行 T1 颜色预筛 → T2 事实核查 → T3 评分 → T4 对比，
输出可操作摘要供 Agent 做重生成决策。
"""

import logging
from pathlib import Path

from novelvideo.generators.pool_indexer import compute_beat_content_hash, is_pool_image_stale

from .image_verifier import ImageVerifier, resolve_verification_scene_context
from .models import ObjectiveScore
from .sketch_scorer import SketchScorer
from .sketch_comparer import SketchComparer

logger = logging.getLogger(__name__)


class BeatSelection:
    """单个 beat 的选择结果。"""

    def __init__(self, beat_num: int):
        self.beat_num = beat_num
        self.candidates: list[dict] = []  # [{pool_id, path, score, disqualified, reason}]
        self.selected_pool_id: str | None = None
        self.selected_reason: str = ""
        self.selected_score: float | None = None
        self.selected_passed_t1_t2: bool | None = None
        self.selected_is_provisional: bool = False
        self.selection_confidence: float = 0.0
        self.recommended_action: str = "regenerate"

    def to_dict(self) -> dict:
        return {
            "beat_number": self.beat_num,
            "candidates": self.candidates,
            "selected_pool_id": self.selected_pool_id,
            "selected_reason": self.selected_reason,
            "selected_score": self.selected_score,
            "selected_passed_t1_t2": self.selected_passed_t1_t2,
            "selected_is_provisional": self.selected_is_provisional,
            "selection_confidence": self.selection_confidence,
            "recommended_action": self.recommended_action,
        }


async def run_sketch_select(
    project_dir: Path,
    episode_num: int,
    beats: list[dict],
    pool_index,
    sketch_colors: dict[str, str],
    quality_threshold: float = 7.0,
    score_gap_for_auto_select: float = 1.0,
    color_prefilter: bool = True,
    fact_check: bool = True,
) -> dict:
    """原子工具：对整集所有 beat 执行候选择优。

    Args:
        project_dir: 项目目录
        episode_num: 集数
        beats: beat 数据列表
        pool_index: PoolIndex 对象
        sketch_colors: {identity_id: "#HEX COLOR_NAME"} 颜色映射
        quality_threshold: 得分低于此值的 beat 列入 needs_regeneration
        score_gap_for_auto_select: top-2 分差超过此值自动选
        color_prefilter: 是否用 T1 颜色预筛
        fact_check: 是否用 T2 事实核查

    Returns:
        dict: {beat_results, needs_regeneration, no_candidates, all_disqualified, summary}
    """
    import re
    from novelvideo.models import extract_char_identities_from_markers
    from novelvideo.generators.sketch_color_detector import detect_sketch_colors as detect_colors

    grids_dir = project_dir / "grids" / f"ep{episode_num:03d}"
    scorer = SketchScorer()
    comparer = SketchComparer()
    verifier = ImageVerifier() if fact_check else None

    # 预计算 beat content hash（含配色）用于 stale 判断
    beat_hashes: dict[int, str] = {}
    for i, beat in enumerate(beats):
        bn = i + 1
        beat_hashes[bn] = compute_beat_content_hash(beat, sketch_colors=sketch_colors)

    beat_results: list[dict] = []
    selected_count = 0
    total_beats = len(beats)
    needs_regeneration: list[int] = []  # 选出但得分 < threshold
    no_candidates: list[int] = []       # 没有 fresh 候选
    all_disqualified: list[int] = []    # 有候选但全被 T1/T2 淘汰
    accepted_beats: list[int] = []
    provisional_beats: list[int] = []

    for i, beat in enumerate(beats):
        beat_num = i + 1
        visual_desc = beat.get("visual_description", "")

        selection = BeatSelection(beat_num)

        # 获取该 beat 的所有 sketch 候选
        pool_images = pool_index.filter_by_beat_and_type(beat_num, "sketch")
        if not pool_images:
            selection.selected_reason = "no_candidates"
            selection.recommended_action = "regenerate"
            no_candidates.append(beat_num)
            beat_results.append(selection.to_dict())
            continue

        # 构建候选列表（过滤 stale：与 API 使用相同的 is_pool_image_stale 逻辑）
        candidates: list[dict] = []
        for img in pool_images:
            if is_pool_image_stale(img, beat_hashes, None):
                continue
            cell_path = grids_dir / img.cell_path if img.cell_path else None
            if cell_path and cell_path.exists():
                candidates.append({
                    "pool_id": img.id,
                    "path": str(cell_path),
                    "disqualified": False,
                    "reason": "",
                    "score": None,
                })

        if not candidates:
            selection.selected_reason = "no_candidates"
            selection.recommended_action = "regenerate"
            no_candidates.append(beat_num)
            beat_results.append(selection.to_dict())
            continue

        # --- T1: 颜色预筛 ---
        if color_prefilter and sketch_colors:
            try:
                char_identities = extract_char_identities_from_markers(visual_desc, strict=False)
            except Exception:
                char_identities = {}

            expected_ids = set(char_identities.values())
            expected_color_map = {
                iid: sketch_colors[iid]
                for iid in expected_ids
                if iid in sketch_colors
            }

            if expected_color_map:
                for cand in candidates:
                    if cand["disqualified"]:
                        continue
                    detected = detect_colors(cand["path"], expected_color_map, threshold=0.008)
                    missing = expected_ids - detected
                    if missing:
                        cand["disqualified"] = True
                        cand["reason"] = f"color_missing:{','.join(sorted(missing))}"

        # --- T2: 事实核查 ---
        if fact_check and verifier:
            named_characters = re.findall(r"\{\{([^}]+)\}\}", visual_desc)
            time_of_day = beat.get("time_of_day", "")
            camera_context = beat.get("keyframe_prompt") or beat.get("video_prompt", "")
            scene_context = resolve_verification_scene_context(
                project_dir,
                beat,
                episode_number=episode_num,
            )

            for cand in candidates:
                if cand["disqualified"]:
                    continue
                try:
                    result = await verifier.verify_sketch(
                        cand["path"],
                        visual_desc,
                        named_characters,
                        scene_context["scene_id"],
                        time_of_day,
                        camera_context,
                        color_mapping=sketch_colors,
                        resolved_scene_name=scene_context["resolved_scene_name"],
                        time_baked=scene_context["time_baked"],
                        prompt_time_of_day=scene_context["prompt_time_of_day"],
                    )
                    if not result.passed:
                        cand["disqualified"] = True
                        cand["reason"] = f"fact_check_failed:score={result.score:.1f}"
                except Exception as e:
                    logger.warning("Fact check failed for %s: %s", cand["pool_id"], e)

        # --- 过滤出合格候选 ---
        qualified = [c for c in candidates if not c["disqualified"]]

        if not qualified:
            # 全部淘汰 → 记录到 all_disqualified，放宽标准继续选（Agent 可决定重生成）
            # 保留原始 disqualified/reason 供 Agent 诊断，仅标记 relaxed
            logger.warning("Beat %d: all candidates disqualified, using all", beat_num)
            all_disqualified.append(beat_num)
            qualified = candidates
            selection.selected_is_provisional = True
            for c in qualified:
                c["relaxed"] = True

        # --- T3: 评分 ---
        for cand in qualified:
            try:
                score = await scorer.score_sketch(
                    cand["path"],
                    visual_desc,
                    color_mapping=sketch_colors,
                )
                cand["score"] = {
                    "script_match": score.script_match,
                    "identity_clarity": score.identity_clarity,
                    "total": score.total,
                }
            except Exception as e:
                logger.warning("Score failed for %s: %s", cand["pool_id"], e)
                cand["score"] = {"script_match": 0, "identity_clarity": 0, "total": 0}

        # --- 选择 ---
        scored = sorted(
            qualified,
            key=lambda c: c["score"]["total"] if c["score"] else 0,
            reverse=True,
        )

        if len(scored) == 1:
            selected = scored[0]
            selection.selected_pool_id = selected["pool_id"]
            selection.selected_reason = "only_candidate"
            selection.selection_confidence = 0.75 if not selection.selected_is_provisional else 0.35
        elif len(scored) >= 2:
            top1 = scored[0]["score"]["total"] if scored[0]["score"] else 0
            top2 = scored[1]["score"]["total"] if scored[1]["score"] else 0
            gap = top1 - top2

            if gap > score_gap_for_auto_select:
                # 分差大，自动选最高分
                selection.selected_pool_id = scored[0]["pool_id"]
                selection.selected_reason = f"score_gap={gap:.1f}"
                selection.selection_confidence = min(
                    0.95,
                    0.7 + gap / max(score_gap_for_auto_select, 0.1) * 0.1,
                )
            else:
                # 分差小，用 T4 对比选择
                try:
                    compare_candidates = [
                        (c["pool_id"], c["path"]) for c in scored[:3]  # 最多对比 top3
                    ]
                    compare_result = await comparer.compare_sketches(
                        compare_candidates,
                        visual_desc,
                    )
                    idx = compare_result.selected_index
                    if 1 <= idx <= len(compare_candidates):
                        selection.selected_pool_id = compare_candidates[idx - 1][0]
                        selection.selected_reason = f"compare_selected:{compare_result.comparison_summary[:100]}"
                        selection.selection_confidence = 0.65 if not selection.selected_is_provisional else 0.3
                    else:
                        selection.selected_pool_id = scored[0]["pool_id"]
                        selection.selected_reason = "compare_fallback"
                        selection.selection_confidence = 0.45 if not selection.selected_is_provisional else 0.25
                except Exception as e:
                    logger.warning("Compare failed for beat %d: %s", beat_num, e)
                    selection.selected_pool_id = scored[0]["pool_id"]
                    selection.selected_reason = "compare_error_fallback"
                    selection.selection_confidence = 0.4 if not selection.selected_is_provisional else 0.2

        selection.candidates = candidates
        if selection.selected_pool_id:
            selected_count += 1
            # 检查选中的候选得分是否 < threshold
            selected_cand = next(
                (c for c in candidates if c["pool_id"] == selection.selected_pool_id), None
            )
            if selected_cand and selected_cand.get("score"):
                total_score = selected_cand["score"].get("total", 0)
                selection.selected_score = total_score
                selection.selected_passed_t1_t2 = not bool(selected_cand.get("disqualified"))
                if total_score < quality_threshold:
                    needs_regeneration.append(beat_num)

            if selection.selected_is_provisional or beat_num in all_disqualified:
                selection.recommended_action = "regenerate"
                provisional_beats.append(beat_num)
            elif beat_num in needs_regeneration:
                selection.recommended_action = "regenerate"
            else:
                selection.recommended_action = "accept"
                accepted_beats.append(beat_num)

        beat_results.append(selection.to_dict())
        logger.info(
            "Beat %d: selected=%s reason=%s",
            beat_num,
            selection.selected_pool_id,
            selection.selected_reason,
        )

    # Agent 可操作摘要（去重：一个 beat 可能同时在 all_disqualified 和 needs_regeneration）
    attention_beats = set(needs_regeneration) | set(no_candidates) | set(all_disqualified)
    attention_count = len(attention_beats)
    return {
        "total_beats": total_beats,
        "selected_count": selected_count,
        "beat_results": beat_results,
        "needs_regeneration": needs_regeneration,
        "no_candidates": no_candidates,
        "all_disqualified": all_disqualified,
        "accepted_beats": accepted_beats,
        "provisional_beats": provisional_beats,
        "summary": (
            f"{selected_count}/{total_beats} beats selected, "
            f"{len(accepted_beats)} accepted, {attention_count} need attention"
        ),
    }
