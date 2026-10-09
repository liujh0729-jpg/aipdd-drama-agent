"""像素级草图相似度检测。"""

import logging
from pathlib import Path

from PIL import Image as PILImage

from novelvideo.models import beat_scene_id
from .models import SimilarityPair, SimilarityResult
from .utils import find_sketch_for_beat, load_all_beats

logger = logging.getLogger(__name__)


def _compute_phash(image_path: str, hash_size: int = 16) -> list[bool]:
    img = PILImage.open(image_path)
    if img.mode != "L":
        img = img.convert("L")
    img = img.resize((hash_size, hash_size), PILImage.LANCZOS)
    pixels = list(img.getdata())
    avg = sum(pixels) / len(pixels)
    return [pixel > avg for pixel in pixels]


def _hamming_similarity(hash_a: list[bool], hash_b: list[bool]) -> float:
    if len(hash_a) != len(hash_b):
        return 0.0
    matches = sum(a == b for a, b in zip(hash_a, hash_b))
    return matches / len(hash_a)


async def detect_similarity(
    project_dir: Path,
    episode_num: int,
    similarity_threshold: float = 0.85,
    sqlite_store=None,
) -> SimilarityResult:
    """检测整集选定草图的相似度。"""
    project_dir = Path(project_dir)
    beats = await load_all_beats(project_dir, episode_num, sqlite_store=sqlite_store)

    beat_data: list[tuple[int, str, str]] = []
    for idx, beat in enumerate(beats):
        beat_num = beat.get("beat_number") or (idx + 1)
        sketch_path = find_sketch_for_beat(project_dir, episode_num, beat_num)
        if sketch_path:
            beat_data.append((beat_num, str(sketch_path), beat_scene_id(beat)))

    if len(beat_data) < 2:
        return SimilarityResult()

    hashes: dict[int, list[bool]] = {}
    for beat_num, sketch_path, _ in beat_data:
        try:
            hashes[beat_num] = _compute_phash(sketch_path)
        except Exception as exc:
            logger.warning("Failed to compute pHash for beat %d: %s", beat_num, exc)

    pairs: list[SimilarityPair] = []
    duplicate_beats: set[int] = set()
    beat_nums = sorted(hashes.keys())
    beat_scenes = {beat_num: scene_id for beat_num, _, scene_id in beat_data}

    for i, beat_a in enumerate(beat_nums):
        for beat_b in beat_nums[i + 1 :]:
            similarity = _hamming_similarity(hashes[beat_a], hashes[beat_b])
            if similarity < similarity_threshold:
                continue

            same_scene = (
                beat_scenes.get(beat_a, "") == beat_scenes.get(beat_b, "")
                and beat_scenes.get(beat_a, "") != ""
            )
            is_adjacent = abs(beat_a - beat_b) <= 2
            warning = not (same_scene and is_adjacent)

            pairs.append(
                SimilarityPair(
                    beat_a=beat_a,
                    beat_b=beat_b,
                    similarity=round(similarity, 3),
                    warning=warning,
                )
            )
            if warning:
                duplicate_beats.add(beat_a)
                duplicate_beats.add(beat_b)

    return SimilarityResult(
        pairs=pairs,
        duplicate_beats=sorted(duplicate_beats),
        overall_passed=(len(duplicate_beats) == 0),
    )
