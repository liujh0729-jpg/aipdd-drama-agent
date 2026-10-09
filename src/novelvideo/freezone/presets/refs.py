"""PresetRef 数据模型与基础工具（_safe_id / _make_url / preset_key_for_request）（B2 拆包自单文件 presets.py）。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from novelvideo.utils.static_urls import project_static_url


@dataclass
class PresetRef:
    kind: str
    role: str
    label: str
    rel_path: str | None = None
    url: str | None = None
    exists: bool = False
    media_type: str = "image"
    aspect_ratio: str = "1:1"
    meta: dict[str, Any] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        payload = {
            "kind": self.kind,
            "role": self.role,
            "label": self.label,
            "rel_path": self.rel_path,
            "url": self.url,
            "exists": self.exists,
            "media_type": self.media_type,
            "aspect_ratio": self.aspect_ratio,
            "meta": self.meta,
        }
        contexts = _mainline_context_for_ref(payload)
        if contexts:
            payload["mainline_context"] = contexts
        return payload


def _compact_context(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value not in (None, "", [])}


def _mainline_context_for_ref(ref: dict[str, Any]) -> list[dict[str, Any]]:
    role = str(ref.get("role") or "")
    kind = str(ref.get("kind") or "")
    meta = ref.get("meta") if isinstance(ref.get("meta"), dict) else {}
    label = str(ref.get("label") or "")
    url = ref.get("url")

    def base(context_kind: str, **extra: Any) -> dict[str, Any]:
        return {
            **_compact_context(
                {
                    "kind": context_kind,
                    "episode": meta.get("episode"),
                    "beat": meta.get("beat"),
                    "character": meta.get("character"),
                    "identityId": meta.get("identity_id"),
                    "sceneId": meta.get("scene_id") or meta.get("scene"),
                    "propId": meta.get("prop_id"),
                    "markerColor": meta.get("marker_color"),
                    "role": role,
                    "label": label,
                    "sourceUrl": url,
                    **extra,
                }
            )
        }

    if role in {
        "character_identity",
        "character_portrait",
        "identity_portrait",
        "identity_costume",
    }:
        return [base("identity")]
    if role in {"character_voice", "character_age_group_voice", "identity_voice"}:
        return [base("voice", audioRole="character_voice")]
    if kind == "scene" or role.startswith("scene_"):
        return [base("scene", plyKind=meta.get("ply_kind"))]
    if kind == "prop" or role.startswith("prop_"):
        return [base("prop")]
    if role == "current_sketch":
        return [base("sketch")]
    if role == "current_frame":
        return [base("frame")]
    if role == "current_video":
        return [base("video")]
    if role == "current_audio":
        return [base("audio", audioRole="beat_audio")]
    if role == "director_combined":
        return [base("director_combined")]
    if role == "selected_background":
        return [base("selected_background")]
    return []


def _safe_id(value: str, fallback: str = "item") -> str:
    safe = re.sub(r"[^a-zA-Z0-9_\-]+", "_", value).strip("_")
    if safe:
        return safe[:48]
    return f"{fallback}_{hashlib.sha1(value.encode('utf-8')).hexdigest()[:10]}"


def preset_key_for_request(
    *,
    scope: str,
    episode: int | None = None,
    beat: int | None = None,
    primary_slot: str | None = None,
    asset_kind: str | None = None,
    character: str | None = None,
    identity_id: str | None = None,
    asset_id: str | None = None,
) -> str:
    if scope == "episode":
        if episode is None:
            raise ValueError("episode preset requires episode")
        return f"episode:ep{episode:03d}"
    if scope == "beat":
        if episode is None or beat is None:
            raise ValueError("beat preset requires episode and beat")
        slot = primary_slot or "render"
        return f"beat:ep{episode:03d}:beat{beat:03d}:{slot}"
    if scope == "asset":
        parts = [
            "asset",
            asset_kind or "unknown",
            character or "",
            identity_id or "",
            asset_id or "",
        ]
        return ":".join(parts)
    return "blank"


def canvas_id_for_preset(preset_key: str) -> str:
    digest = hashlib.sha1(preset_key.encode("utf-8")).hexdigest()[:8]
    stem = _safe_id(preset_key.replace(":", "_"), fallback="preset")
    return f"{stem[:50]}_{digest}"[:64]


def _make_url(project_id: str, project_dir: Path, rel_path: str) -> str:
    if not project_id:
        raise ValueError("project_id is required for preset static URLs")
    return project_static_url(project_id, rel_path, local_path=project_dir / rel_path)


def _path_rel_if_inside(project_dir: Path, path: Path) -> str | None:
    try:
        return path.relative_to(project_dir).as_posix()
    except ValueError:
        return None


def _rel(project_dir: Path, path: Path) -> str:
    return path.relative_to(project_dir).as_posix()
