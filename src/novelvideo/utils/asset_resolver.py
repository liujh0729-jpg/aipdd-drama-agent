"""Unified asset resolver for render-time references.

This keeps render prompt construction on explicit asset types:
- character/identity
- scene
- prop

Scene/prop resolution is intentionally local-first for 2.0:
- prefer explicit beat fields
- then fall back to filesystem-backed references
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from novelvideo.director_world.paths import safe_name
from novelvideo.models import (
    build_scene_effective_prompt,
    extract_prop_ids_from_markers,
    real_detected_props,
    resolve_scene_record_name,
    resolve_scene_plate,
    resolve_scene_plate_from_records,
)
from novelvideo.utils.background_anchor import background_anchor_label, background_anchor_path
from novelvideo.utils.path_resolver import (
    PathResolver,
    compute_prop_reference_path,
    compute_scene_master_path,
)


@dataclass
class ResolvedAssetRef:
    asset_type: str
    base_id: str
    variant_id: str | None
    image_paths: list[str]
    text_description: str
    source_level: str
    character_tag: str = ""
    face_prompt: str = ""
    reference_mode: str = "prompt_only"
    gender: str = ""
    body_type: str = ""
    lighting: str = ""
    atmosphere: str = ""
    owner: str = ""
    scope: str = ""
    time_baked: bool = False


PANO_VIEW_REF_ORDER = (
    ("front", "front.png", "pano_front.jpg"),
    ("right", "right.png", "pano_right.jpg"),
    ("back", "back.png", "pano_back.jpg"),
    ("left", "left.png", "pano_left.jpg"),
)


def _normalize_name(value: str) -> str:
    text = (value or "").strip()
    text = re.sub(r"[·•．。,:：，、／/（）()\\-]+", "", text)
    return text.lower()


def _pick_text(*values: str) -> str:
    for value in values:
        if value and str(value).strip():
            return str(value).strip()
    return ""


def _latest_cubemap_faces_dir(stage_dir: Path) -> Path | None:
    runs_dir = stage_dir / "pano_sharp_runs"
    if not runs_dir.exists():
        return None
    candidates = [
        run / "cubemap_faces"
        for run in sorted(runs_dir.iterdir(), reverse=True)
        if (run / "cubemap_faces").exists()
    ]
    return candidates[0] if candidates else None


def _model_ref_jpeg_for_path(
    path: Path,
    *,
    quality: int = 72,
    max_edge: int = 768,
) -> Path:
    """Create a compressed JPEG sidecar for image-model references."""
    path = Path(path)
    out = path.with_name(f"{path.stem}_ref.jpg")
    try:
        if out.exists() and out.stat().st_mtime >= path.stat().st_mtime:
            return out
        from PIL import Image

        with Image.open(path) as img:
            rgb = img.convert("RGB")
            edge = max(rgb.size)
            if edge > max_edge:
                scale = max_edge / edge
                size = (
                    max(1, int(round(rgb.size[0] * scale))),
                    max(1, int(round(rgb.size[1] * scale))),
                )
                rgb = rgb.resize(size, Image.LANCZOS)
            rgb.save(out, format="JPEG", quality=quality, optimize=True, progressive=True)
    except Exception:
        return path
    return out


def _get_attr_or_key(value: Any, name: str, default: Any = "") -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


class AssetResolver:
    """Resolve scene/prop references for a beat.

    The resolver prefers explicit asset IDs and on-disk reference images.
    It can work with optional in-memory scene/prop records, but does not require
    store access for the first integration step.
    """

    def __init__(
        self,
        project_dir: Path,
        *,
        episode_number: int | None = None,
        scenes: Iterable[Any] | None = None,
        props: Iterable[Any] | None = None,
        scene_menu: Iterable[Any] | None = None,
        prop_menu: Iterable[Any] | None = None,
        scene_reference_kind: str = "render",
        use_director_refs: bool = False,
        include_pano_view_refs: bool = False,
        director_ref_beat_numbers: Iterable[int] | None = None,
        director_control_frames_dir: str | Path | None = None,
        allow_beat_background_anchor: bool | None = None,
    ):
        self.project_dir = Path(project_dir)
        self.episode_number = episode_number
        self.scenes = list(scenes or [])
        self.props = list(props or [])
        self.scene_menu = list(scene_menu or [])
        self.prop_menu = list(prop_menu or [])
        self.scene_reference_kind = scene_reference_kind
        self.use_director_refs = use_director_refs
        self.include_pano_view_refs = include_pano_view_refs
        self.director_control_frames_dir = (
            Path(director_control_frames_dir) if director_control_frames_dir else None
        )
        self.allow_beat_background_anchor = allow_beat_background_anchor
        self.director_ref_beat_numbers = (
            {int(bn) for bn in director_ref_beat_numbers if bn is not None}
            if director_ref_beat_numbers is not None
            else None
        )
        self._scene_dirs = self._list_asset_dirs(self.project_dir / "assets" / "scenes")
        self._prop_dirs = self._list_asset_dirs(self.project_dir / "assets" / "props")

    def _allows_director_ref(self, beat: dict) -> bool:
        if not self.use_director_refs:
            return False
        beat_num = int(beat.get("beat_number") or 0)
        if beat_num <= 0:
            return False
        if self.director_ref_beat_numbers is not None:
            return beat_num in self.director_ref_beat_numbers
        return True

    def _director_scene_ref_path(self, beat: dict) -> str:
        if not self.episode_number:
            return ""
        if not self._allows_director_ref(beat):
            return ""
        # Director/control frames are only valid for the control-frame -> sketch
        # conversion. Final render/colorization must consume the generated sketch,
        # not the original 3GS/combined frame.
        if self.scene_reference_kind != "sketch":
            return ""
        beat_num = int(beat.get("beat_number") or 0)
        if self.director_control_frames_dir:
            director_render = (
                self.director_control_frames_dir
                / f"ep{int(self.episode_number):03d}"
                / f"beat_{beat_num:02d}"
                / "combined.png"
            )
        else:
            director_render = PathResolver(
                str(self.project_dir), int(self.episode_number)
            ).director_render(beat_num)
        if director_render.exists():
            return str(director_render)
        return ""

    @staticmethod
    def _list_asset_dirs(base_dir: Path) -> list[str]:
        if not base_dir.exists():
            return []
        return sorted([p.name for p in base_dir.iterdir() if p.is_dir()])

    def _match_scene_name(self, scene_id: str) -> tuple[str, Any | None]:
        if not scene_id:
            return "", None
        norm_scene_id = _normalize_name(scene_id)

        for scene in self.scenes:
            candidates = [getattr(scene, "name", "")]
            candidates.extend(getattr(scene, "aliases", []) or [])
            for candidate in candidates:
                if candidate and _normalize_name(candidate) == norm_scene_id:
                    return getattr(scene, "name", candidate), scene

        for scene_name in self._scene_dirs:
            if _normalize_name(scene_name) == norm_scene_id:
                return scene_name, None

        for scene in self.scenes:
            candidates = [getattr(scene, "name", "")]
            candidates.extend(getattr(scene, "aliases", []) or [])
            for candidate in candidates:
                norm_candidate = _normalize_name(candidate)
                if norm_candidate and (
                    norm_candidate in norm_scene_id or norm_scene_id in norm_candidate
                ):
                    return getattr(scene, "name", candidate), scene

        for scene_name in self._scene_dirs:
            norm_scene = _normalize_name(scene_name)
            if norm_scene and (norm_scene in norm_scene_id or norm_scene_id in norm_scene):
                return scene_name, None

        return scene_id.strip(), None

    def _find_scene_by_name(self, scene_name: str) -> tuple[str, Any | None]:
        if not scene_name:
            return "", None
        norm_scene_name = _normalize_name(scene_name)
        for scene in self.scenes:
            candidates = [getattr(scene, "name", "")]
            candidates.extend(getattr(scene, "aliases", []) or [])
            for candidate in candidates:
                if candidate and _normalize_name(candidate) == norm_scene_name:
                    return getattr(scene, "name", candidate), scene
        for known_name in self._scene_dirs:
            if _normalize_name(known_name) == norm_scene_name:
                return known_name, None
        return scene_name.strip(), None

    def _pano_view_refs_for_scene(self, scene_name: str) -> list[ResolvedAssetRef]:
        """Return 360-derived view references for free sketch scene consistency.

        Real-scene/director sketch mode does not call this path. That mode uses
        the 3GS combined image as the locked base and scene master as detail ref.
        """
        if not scene_name:
            return []
        stage_root = self.project_dir / "director_worlds" / safe_name(scene_name) / "v1"
        cubemap_dir = _latest_cubemap_faces_dir(stage_root)
        refs: list[ResolvedAssetRef] = []
        for view_name, cubemap_filename, _refs_filename in PANO_VIEW_REF_ORDER:
            path = cubemap_dir / cubemap_filename if cubemap_dir is not None else Path()
            source = "pano_cubemap_face"
            if not path.exists():
                continue
            path = _model_ref_jpeg_for_path(path)
            refs.append(
                ResolvedAssetRef(
                    asset_type="scene",
                    base_id=scene_name,
                    variant_id=view_name,
                    image_paths=[str(path)],
                    text_description=(
                        f"Latest 360-derived {view_name} cubemap view. Secondary "
                        "environment repair reference only; do not override the "
                        "3GS Director Render camera."
                    ),
                    source_level=source,
                )
            )
        return refs

    def _explicit_scene_anchor_path(
        self, scene_name: str, scene_ref: dict, beat: dict
    ) -> tuple[str, str]:
        """Resolve a beat-selected background reference image.

        Render treats the result as one generic scene anchor regardless of how
        the image was produced.
        """
        for key in (
            "render_anchor_path",
            "anchor_path",
            "background_ref_path",
            "image_path",
            "reference_path",
        ):
            raw_path = str(scene_ref.get(key, "") or "").strip()
            if not raw_path:
                continue
            if raw_path.startswith("/@fs"):
                raw_path = raw_path[4:]
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                candidate = self.project_dir / raw_path
            if candidate.exists():
                return str(candidate), candidate.stem

        for key in (
            "render_anchor_id",
            "anchor_id",
            "background_ref_id",
            "shot_id",
        ):
            anchor_id = str(scene_ref.get(key, "") or "").strip()
            if not anchor_id:
                continue
            beat_num = int(beat.get("beat_number") or 0)
            path = background_anchor_path(
                self.project_dir,
                scene_name,
                episode=self.episode_number,
                beat_num=beat_num,
                anchor_id=anchor_id,
            )
            if path:
                return path, background_anchor_label(anchor_id)

        return "", ""

    def resolve_scenes_for_beat(
        self,
        beat: dict,
        *,
        allow_explicit_scene_anchor: bool = True,
    ) -> list[ResolvedAssetRef]:
        scene_ref = beat.get("scene_ref") or {}
        scene_id = str(scene_ref.get("scene_id", "") or "").strip()
        if not scene_id:
            return []

        scene_record_name, time_baked = resolve_scene_plate_from_records(
            scene_id,
            str(scene_ref.get("variant_id", "") or "").strip(),
            str(beat.get("time_of_day", "") or "").strip(),
            self.scenes,
        )
        if self._scene_dirs:
            dir_scene_record_name, dir_time_baked = resolve_scene_plate(
                scene_id,
                str(scene_ref.get("variant_id", "") or "").strip(),
                str(beat.get("time_of_day", "") or "").strip(),
                self._scene_dirs,
            )
            dir_master_path = compute_scene_master_path(self.project_dir, dir_scene_record_name)
            if dir_master_path and (
                not scene_record_name
                or dir_time_baked
                or (scene_record_name == scene_id and dir_scene_record_name != scene_record_name)
            ):
                scene_record_name = dir_scene_record_name
                time_baked = dir_time_baked
        scene_name, scene_obj = self._find_scene_by_name(scene_record_name)
        base_scene_obj = None
        if scene_obj is not None:
            base_scene_id = str(getattr(scene_obj, "base_scene_id", "") or "").strip()
            if base_scene_id:
                _base_scene_name, base_scene_obj = self._find_scene_by_name(base_scene_id)
        variant_name = ""
        variant_description = ""
        lighting = ""
        atmosphere = ""

        image_paths: list[str] = []
        extra_refs: list[ResolvedAssetRef] = []
        source_level = "base_text"
        text_description = ""
        suppress_text_description = False
        selected_plate_master_path = compute_scene_master_path(self.project_dir, scene_name)

        explicit_anchor_path = ""
        explicit_anchor_label = ""
        if allow_explicit_scene_anchor:
            explicit_anchor_path, explicit_anchor_label = self._explicit_scene_anchor_path(
                scene_name, scene_ref, beat
            )
        if explicit_anchor_path:
            image_paths = [explicit_anchor_path]
            source_level = "selected_background_image"
            variant_name = explicit_anchor_label or None

        director_path = self._director_scene_ref_path(beat)
        if director_path:
            image_paths = [director_path]
            source_level = "director_image"
            variant_name = ""
            lighting = ""
            atmosphere = ""
            text_description = ""
            suppress_text_description = True
            master_path = compute_scene_master_path(self.project_dir, scene_name)
            include_director_master_ref = (
                self.scene_reference_kind != "sketch"
                or os.environ.get("DIRECTOR_CONTROL_INCLUDE_MASTER_REF") == "1"
            )
            if master_path and include_director_master_ref:
                extra_refs.append(
                    ResolvedAssetRef(
                        asset_type="scene",
                        base_id=scene_name,
                        variant_id=None,
                        image_paths=[master_path],
                        text_description="",
                        source_level="scene_master_detail",
                    )
                )

        if not image_paths:
            # Default storyboard/render generation is the stable scene path:
            # attach only the scene front master. Reverse/360/upload/director env
            # are beat-level explicit choices for single-beat refinement.
            base_path = selected_plate_master_path
            variant_id = str(scene_ref.get("variant_id", "") or "").strip()
            if not base_path and variant_id:
                variant_record_name = resolve_scene_record_name(
                    scene_id,
                    variant_id,
                    self._scene_dirs,
                )
                if variant_record_name != scene_name:
                    base_path = compute_scene_master_path(self.project_dir, variant_record_name)
            if not base_path:
                base_path = compute_scene_master_path(self.project_dir, scene_id)
            if base_path:
                image_paths = [base_path]
            if image_paths and source_level in {"base_text", "variant_text"}:
                source_level = "base_image"
        time_baked = bool(
            time_baked
            and selected_plate_master_path
            and image_paths
            and image_paths[0] == selected_plate_master_path
        )

        if not text_description and not suppress_text_description:
            text_description = _pick_text(
                build_scene_effective_prompt(scene_obj, base_scene_obj) if scene_obj else "",
                variant_description,
                "，".join(v for v in [lighting, atmosphere] if v),
                beat.get("time_of_day", "") or "",
                scene_name,
            )

        refs = [
            ResolvedAssetRef(
                asset_type="scene",
                base_id=scene_name,
                variant_id=variant_name or None,
                image_paths=image_paths,
                text_description=text_description,
                source_level=source_level,
                lighting=lighting,
                atmosphere=atmosphere,
                time_baked=time_baked,
            )
        ]
        refs.extend(extra_refs)
        return refs

    def _candidate_prop_names(self, beat: dict) -> list[str]:
        if self.scene_reference_kind == "render":
            return [
                str(prop_id or "").strip()
                for prop_id in real_detected_props(beat.get("detected_props") or [])
                if str(prop_id or "").strip()
            ]
        visual_desc = beat.get("visual_description") or ""
        return extract_prop_ids_from_markers(visual_desc, strict=False)

    def _prop_menu_metadata(self, prop_name: str) -> dict[str, Any]:
        norm_name = _normalize_name(prop_name)
        from novelvideo.models import build_prop_menu

        for item in build_prop_menu(prop_menu=list(self.prop_menu or [])):
            prop_id = str(_get_attr_or_key(item, "prop_id", "") or "").strip()
            base_id = str(_get_attr_or_key(item, "base_id", "") or "").strip()
            if norm_name not in {_normalize_name(prop_id), _normalize_name(base_id)}:
                continue
            return {
                "scope": str(_get_attr_or_key(item, "scope", "") or "").strip(),
                "prop_type": str(_get_attr_or_key(item, "prop_type", "") or "").strip(),
                "description": str(_get_attr_or_key(item, "description", "") or "").strip(),
                "owner_identity_id": str(
                    _get_attr_or_key(item, "owner_identity_id", "") or ""
                ).strip(),
            }
        return {}

    def _find_prop_obj(self, prop_name: str) -> Any | None:
        norm_name = _normalize_name(prop_name)
        for prop in self.props:
            candidates = [getattr(prop, "name", "")]
            candidates.extend(getattr(prop, "aliases", []) or [])
            for candidate in candidates:
                if candidate and _normalize_name(candidate) == norm_name:
                    return prop
        return None

    def resolve_props_for_beat(self, beat: dict) -> list[ResolvedAssetRef]:
        refs: list[ResolvedAssetRef] = []
        for prop_name in self._candidate_prop_names(beat):
            prop_obj = self._find_prop_obj(prop_name)
            base_id = getattr(prop_obj, "name", prop_name) if prop_obj else prop_name
            menu_meta = self._prop_menu_metadata(base_id) or self._prop_menu_metadata(prop_name)

            image_paths: list[str] = []
            source_level = "base_text"
            text_description = _pick_text(
                menu_meta.get("description") or "",
                getattr(prop_obj, "visual_prompt", "") if prop_obj else "",
                getattr(prop_obj, "description", "") if prop_obj else "",
                base_id,
            )

            base_path = compute_prop_reference_path(self.project_dir, base_id)
            if base_path:
                image_paths = [base_path]
                source_level = "base_image"

            refs.append(
                ResolvedAssetRef(
                    asset_type="prop",
                    base_id=base_id,
                    variant_id=None,
                    image_paths=image_paths,
                    text_description=text_description,
                    source_level=source_level,
                    owner=getattr(prop_obj, "owner", "") if prop_obj else "",
                    scope="global" if prop_obj else "",
                )
            )
        return refs

    def resolve_all_for_beats(
        self, beats: list[dict]
    ) -> tuple[dict[int, list[ResolvedAssetRef]], dict[int, list[ResolvedAssetRef]]]:
        scene_refs: dict[int, list[ResolvedAssetRef]] = {}
        prop_asset_refs: dict[int, list[ResolvedAssetRef]] = {}
        allow_explicit_scene_anchor = (
            len(beats) <= 1
            if self.allow_beat_background_anchor is None
            else bool(self.allow_beat_background_anchor)
        )
        for idx, beat in enumerate(beats, start=1):
            scenes = self.resolve_scenes_for_beat(
                beat,
                allow_explicit_scene_anchor=allow_explicit_scene_anchor,
            )
            props = self.resolve_props_for_beat(beat)
            if scenes:
                if (
                    self.scene_reference_kind == "sketch"
                    and self.include_pano_view_refs
                    and not self.use_director_refs
                ):
                    scene_name = str(getattr(scenes[0], "base_id", "") or "").strip()
                    pano_refs = self._pano_view_refs_for_scene(scene_name)
                    if pano_refs:
                        scenes = pano_refs
                scene_refs[idx] = scenes
            if props:
                prop_asset_refs[idx] = props
        return scene_refs, prop_asset_refs
