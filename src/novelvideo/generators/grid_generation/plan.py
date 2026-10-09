"""PlanEntry / hash_plan / compute_input_fingerprint / SKETCH_GRID 配置（B3 拆包自单文件 grid_generation.py）。"""

import hashlib
import json
from dataclasses import dataclass
from typing import Callable, Optional

from novelvideo.models import beat_scene_id, real_detected_identities

from .newapi_protocol import _beat_display_sort_key
from .regen_modes import PLANNER_VERSION, REGEN_MODE_CONFIGS, SKETCH_DEFAULT_MODE_KEY


@dataclass(frozen=True)
class PlanEntry:
    """Single grid entry in a server-authoritative render plan."""

    mode_key: str
    rows: int
    cols: int
    beat_numbers: tuple[int, ...]
    location: str = ""
    padding_count: int = 0
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def hash_plan(plan: list[PlanEntry]) -> str:
    """Canonical SHA1 of plan shape, truncated for task identity."""
    payload = json.dumps(
        [(entry.mode_key, list(entry.beat_numbers)) for entry in plan],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def compute_input_fingerprint(
    beats: list[dict],
    character_map: dict,
    sketch_colors: dict,
    strategy: str,
    aspect_mode: str,
    force_one_by_one: bool,
    ref_image_hasher: Callable[[str], str],
) -> str:
    """Fingerprint planning inputs that must stay stable between plan and execute."""
    beats_by_num = sorted(beats, key=_beat_display_sort_key)
    beats_payload = [
        {
            "beat_number": beat.get("beat_number"),
            "location": beat_scene_id(beat) or beat.get("location") or "",
            "detected_identities": sorted(
                real_detected_identities(beat.get("detected_identities") or [])
            ),
            "visual_description": beat.get("visual_description") or "",
        }
        for beat in beats_by_num
    ]

    referenced_ids: set[str] = set()
    for beat in beats_by_num:
        for identity_id in real_detected_identities(beat.get("detected_identities") or []):
            referenced_ids.add(str(identity_id))

    character_payload = []
    for identity_id in sorted(referenced_ids):
        info = character_map.get(identity_id) or {}
        ref_path = info.get("ref_path") or info.get("primary_reference") or ""
        ref_mode = info.get("ref_mode") or info.get("mode") or ""
        content_hash = ref_image_hasher(ref_path) if ref_path else ""
        character_payload.append(
            {
                "id": identity_id,
                "ref_path": ref_path,
                "ref_mode": ref_mode,
                "content_hash": content_hash,
            }
        )

    sketch_color_payload = {
        identity_id: sketch_colors.get(identity_id, "") for identity_id in sorted(referenced_ids)
    }
    envelope = {
        "beats": beats_payload,
        "character_map": character_payload,
        "sketch_colors": sketch_color_payload,
        "strategy": strategy,
        "aspect_mode": aspect_mode,
        "force_one_by_one": bool(force_one_by_one),
        "planner_version": PLANNER_VERSION,
    }
    payload = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def cell_aspect_ratio(mode_key: str) -> Optional[str]:
    """计算 mode_key 中单个 cell 的实际宽高比字符串。

    公式: cell 比例 = (W × rows) : (H × cols)，其中 W:H 是 grid 整体比例。
    """
    from math import gcd

    cfg = REGEN_MODE_CONFIGS.get(mode_key)
    if not cfg or not cfg.get("aspect_ratio"):
        return None
    w, h = map(int, cfg["aspect_ratio"].split(":"))
    rows, cols = cfg["rows"], cfg["cols"]
    cell_w, cell_h = w * rows, h * cols
    g = gcd(cell_w, cell_h)
    return f"{cell_w // g}:{cell_h // g}"


def sketch_pass1_mode_key(target_mode_key: str) -> Optional[str]:
    """给定目标 sketch mode_key，返回同尺寸的 1:1 sketch mode_key（用于 two-pass）。

    已禁用 two-pass：所有比例（含 5x5 2:3）均直接 one-pass 生成。
    """
    return None


# 从 SKETCH_DEFAULT_MODE_KEY 派生（向后兼容）
_sketch_cfg = REGEN_MODE_CONFIGS[SKETCH_DEFAULT_MODE_KEY]
SKETCH_GRID_CONFIG = {
    "rows": _sketch_cfg["rows"],
    "cols": _sketch_cfg["cols"],
    "aspect_ratio": _sketch_cfg["aspect_ratio"],
    "image_size": _sketch_cfg["image_size"],
}
# 5x5 每张 25 beats，1 张即可覆盖整集
SKETCH_GRID_PLAN = [SKETCH_GRID_CONFIG] * 1

# 草图再生只允许 NxN + 1:1 模式
SKETCH_REGEN_MODE_KEYS = [
    "1x1_1-1",
    "1x1_1-1_sketch",
    "1x1_9-16_sketch",
    "1x1_2-3_sketch",
    "1x1_16-9_sketch",
    "1x2_4-3_sketch",
    "2x2_1-1",
    "2x2_2-3_sketch",
    "2x2_16-9_sketch",
    "2x2_9-16_sketch",
    "2x4_4-3_sketch",
    "3x2_2-3",
    "3x3_1-1_sketch",
    "3x3_9-16_sketch",
    "3x3_3-4_sketch",
    "3x3_2-3_sketch",
    "3x3_16-9_sketch",
    "4x3_3-4_sketch",
    "4x4_1-1_sketch",
    "4x4_16-9_sketch",
    "5x5_1-1_sketch",
    "5x5_2-3_sketch",
    "5x5_16-9_sketch",
    "5x5_9-16_sketch",
    "5x5_1-1",
]
