"""Beat-level background anchor selection.

The persisted beat state stores only an anchor id. Paths are derived from
project/scene/episode/beat so generated output stays portable across machines.
"""

from __future__ import annotations

import shutil
import filecmp
from pathlib import Path

from novelvideo.utils.path_resolver import (
    canonical_beat_selected_background_path,
    compute_beat_director_env_only_path,
    compute_beat_selected_background_path,
    compute_scene_master_path,
    compute_scene_render_anchor_path,
    compute_scene_reverse_master_path,
)

ANCHOR_MASTER = "master"
ANCHOR_REVERSE = "reverse"
ANCHOR_SELECTED_BACKGROUND = "selected_background"
ANCHOR_DIRECTOR_ENV_ONLY = "director_env_only"

BACKGROUND_CROP_RATIOS = ("16:9", "9:16", "2:3", "1:1", "4:3", "3:4")

BACKGROUND_ANCHORS = (
    ANCHOR_MASTER,
    ANCHOR_REVERSE,
    ANCHOR_SELECTED_BACKGROUND,
    ANCHOR_DIRECTOR_ENV_ONLY,
)

BACKGROUND_ANCHOR_LABELS = {
    ANCHOR_MASTER: "master",
    ANCHOR_REVERSE: "reverse",
    ANCHOR_SELECTED_BACKGROUND: "外部参考",
    ANCHOR_DIRECTOR_ENV_ONLY: "director env_only",
}


def normalize_background_anchor_id(value: str | None) -> str:
    raw = str(value or "").strip()
    aliases = {
        "": ANCHOR_MASTER,
        "scene_master": ANCHOR_MASTER,
        "master": ANCHOR_MASTER,
        "scene_reverse": ANCHOR_REVERSE,
        "scene_reverse_master": ANCHOR_REVERSE,
        "reverse_master": ANCHOR_REVERSE,
        "reverse": ANCHOR_REVERSE,
        "selected_background": ANCHOR_SELECTED_BACKGROUND,
        "beat_selected_background": ANCHOR_SELECTED_BACKGROUND,
        "director_env_only": ANCHOR_DIRECTOR_ENV_ONLY,
    }
    if raw.startswith("director_env_only"):
        return ANCHOR_DIRECTOR_ENV_ONLY
    return aliases.get(raw, raw)


def background_anchor_label(anchor_id: str | None) -> str:
    anchor_id = normalize_background_anchor_id(anchor_id)
    return BACKGROUND_ANCHOR_LABELS.get(anchor_id, anchor_id or ANCHOR_MASTER)


def background_crop_ratio_choices(preferred: str | None = None) -> list[str]:
    """Return crop ratios with the preferred target ratio first."""
    preferred = str(preferred or "").strip()
    choices = list(BACKGROUND_CROP_RATIOS)
    if preferred and preferred in choices:
        return [preferred] + [ratio for ratio in choices if ratio != preferred]
    return choices


def background_anchor_path(
    project_dir: Path,
    scene_name: str,
    *,
    episode: int | None = None,
    beat_num: int | None = None,
    anchor_id: str | None = None,
) -> str:
    """Resolve a background anchor id to a concrete image path if it exists."""
    anchor_id = normalize_background_anchor_id(anchor_id)
    if anchor_id == ANCHOR_MASTER:
        return compute_scene_master_path(project_dir, scene_name)
    if anchor_id == ANCHOR_REVERSE:
        return compute_scene_reverse_master_path(project_dir, scene_name)
    if anchor_id == ANCHOR_SELECTED_BACKGROUND and episode and beat_num:
        return compute_beat_selected_background_path(project_dir, int(episode), int(beat_num))
    if anchor_id == ANCHOR_DIRECTOR_ENV_ONLY and episode and beat_num:
        return compute_beat_director_env_only_path(project_dir, int(episode), int(beat_num))

    # Legacy/experimental named scene-level slots. New UI should not persist these.
    if anchor_id:
        return compute_scene_render_anchor_path(project_dir, scene_name, anchor_id)
    return ""


def copy_to_beat_selected_background(
    project_dir: Path,
    episode: int,
    beat_num: int,
    source_path: str | Path,
) -> Path:
    """Copy a chosen still background into the beat-owned selected_background slot."""
    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(f"background source not found: {source}")
    target = canonical_beat_selected_background_path(project_dir, int(episode), int(beat_num))
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() == target.resolve():
        return target
    shutil.copyfile(source, target)
    return target


def infer_selected_background_source(
    project_dir: Path,
    scene_name: str,
    *,
    episode: int,
    beat_num: int,
) -> str:
    """Infer the source of selected_background.png for older persisted beats.

    Before render_anchor_source_id existed, master/reverse/director env-only
    were snapshotted into selected_background.png and the source was lost. If
    the frozen selected image still byte-matches a known source, recover that
    UI source without changing the actual render input.
    """
    selected = Path(
        background_anchor_path(
            project_dir,
            scene_name,
            episode=int(episode),
            beat_num=int(beat_num),
            anchor_id=ANCHOR_SELECTED_BACKGROUND,
        )
    )
    if not selected.exists():
        return ""

    for anchor_id in (ANCHOR_MASTER, ANCHOR_REVERSE, ANCHOR_DIRECTOR_ENV_ONLY):
        candidate_path = background_anchor_path(
            project_dir,
            scene_name,
            episode=int(episode),
            beat_num=int(beat_num),
            anchor_id=anchor_id,
        )
        if not candidate_path:
            continue
        candidate = Path(candidate_path)
        if not candidate.exists():
            continue
        try:
            if selected.resolve() != candidate.resolve() and filecmp.cmp(
                selected,
                candidate,
                shallow=False,
            ):
                return anchor_id
        except OSError:
            continue
    return ""


def crop_to_beat_selected_background(
    project_dir: Path,
    episode: int,
    beat_num: int,
    source_path: str | Path,
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    output_size: tuple[int, int] | None = None,
) -> Path:
    """Crop a chosen background still into the beat-owned selected_background slot."""
    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(f"background source not found: {source}")
    if int(width) <= 0 or int(height) <= 0:
        raise ValueError("crop width/height must be positive")

    target = canonical_beat_selected_background_path(project_dir, int(episode), int(beat_num))
    target.parent.mkdir(parents=True, exist_ok=True)

    from PIL import Image

    with Image.open(source) as img:
        if img.mode != "RGB":
            img = img.convert("RGB")
        crop_x = max(0, min(int(x), img.width - 1))
        crop_y = max(0, min(int(y), img.height - 1))
        right = min(crop_x + max(1, int(width)), img.width)
        bottom = min(crop_y + max(1, int(height)), img.height)
        cropped = img.crop((crop_x, crop_y, right, bottom)).copy()
        if output_size:
            out_w, out_h = int(output_size[0]), int(output_size[1])
            if out_w > 0 and out_h > 0:
                cropped = cropped.resize((out_w, out_h), Image.Resampling.LANCZOS)
        cropped.save(target, format="PNG")
    return target
