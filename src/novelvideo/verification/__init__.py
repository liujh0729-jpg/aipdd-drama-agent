"""AI 验证系统 — 草图/首帧事实核查 + 角色一致性检查 + 首帧渲染质量验证。"""

from .consistency_verifier import ConsistencyVerifier
from .continuity_verifier import ContinuityVerifier
from .episode_reviewer import EpisodeReviewer
from .frame_verifier import FrameVerifier
from .image_verifier import ImageVerifier
from .sketch_comparer import SketchComparer
from .sketch_color_verifier import verify_episode_sketch_colors
from .sketch_scorer import SketchScorer
from .sketch_selector import run_sketch_select
from .similarity_detector import detect_similarity

__all__ = [
    "ImageVerifier",
    "ConsistencyVerifier",
    "ContinuityVerifier",
    "EpisodeReviewer",
    "FrameVerifier",
    "SketchScorer",
    "SketchComparer",
    "verify_episode_sketch_colors",
    "detect_similarity",
    "run_sketch_select",
]
