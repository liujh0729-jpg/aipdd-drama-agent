from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Dict


REGISTRY_PATH = Path(__file__).with_name("object_type_registry.json")


def load_object_type_registry(path: Path | None = None) -> Dict[str, Dict[str, str]]:
    registry_path = path or REGISTRY_PATH
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"object type registry must be an object: {registry_path}")
    registry: Dict[str, Dict[str, str]] = {}
    for raw_type, raw_meta in data.items():
        block_type = normalize_registry_key(raw_type)
        if not isinstance(raw_meta, dict):
            raise ValueError(f"registry entry must be an object: {raw_type}")
        meta = {str(key): str(value) for key, value in raw_meta.items()}
        meta.setdefault("color", custom_block_color(block_type))
        meta.setdefault("label", f"{block_type} object class")
        meta.setdefault("object_class", block_type)
        meta.setdefault("sketch_color_policy", "environment_to_grayscale")
        registry[block_type] = meta
    return registry


def normalize_registry_key(block_type: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_]+", "_", str(block_type or "").strip().lower())
    clean = re.sub(r"_+", "_", clean).strip("_")
    if not clean:
        raise ValueError("empty registry block type")
    return clean


def custom_block_color(block_type: str) -> str:
    digest = hashlib.sha1(block_type.encode("utf-8")).digest()
    # Keep custom fixture colors readable but non-fluorescent; marker colors are reserved.
    channels = [72 + (value % 116) for value in digest[:3]]
    return f"#{channels[0]:02x}{channels[1]:02x}{channels[2]:02x}"


# SuperTale voxel colors are object-class labels, not material colors.
# A "table" is always table-colored even if the final artwork may make it wood,
# metal, plastic, or marble. Real material/color comes later from style refs.
VOXEL_SEMANTIC_PALETTE: Dict[str, Dict[str, str]] = load_object_type_registry()


# Compatibility only. These names are deprecated because they describe materials
# or visual treatments instead of object classes.
DEPRECATED_BLOCK_TYPE_ALIASES: Dict[str, str] = {
    "floor_tile": "floor",
    "floor_tile_alt": "floor",
    "white_wall_tile": "wall",
    "glass": "window",
    "glass_door": "door",
    "asphalt": "road",
    "street": "road",
    "pavement": "sidewalk",
    "side_walk": "sidewalk",
    "building_facade": "building",
    "sign": "shop_sign",
    "signage": "shop_sign",
    "lamp_post": "street_light",
    "streetlamp": "street_light",
    "traffic_light": "traffic_signal",
    "car": "vehicle",
    "bus": "vehicle",
    "train": "vehicle",
    "fence": "railing",
    "wood": "table",
    "wood_light": "chair",
    "steel": "appliance",
    "menu_red": "menu_board",
    "menu_yellow": "menu_board",
    "lantern_red": "decoration",
    "plant_green": "plant",
}


def global_object_type_registry() -> Dict[str, Dict[str, str]]:
    return deepcopy(VOXEL_SEMANTIC_PALETTE)


def normalize_block_type(block_type: str) -> str:
    clean = normalize_registry_key(block_type)
    return DEPRECATED_BLOCK_TYPE_ALIASES.get(clean, clean)


def custom_block_meta(block_type: str) -> Dict[str, str]:
    return {
        "color": custom_block_color(block_type),
        "label": f"{block_type} custom fixed fixture object class",
        "object_class": block_type,
        "sketch_color_policy": "environment_to_grayscale",
        "marker_role": "custom_environment_fixture",
        "custom": "true",
    }


def ensure_block_type(block_type: str, palette: Dict[str, Dict[str, str]] | None = None) -> str:
    palette = palette if palette is not None else VOXEL_SEMANTIC_PALETTE
    normalized = normalize_block_type(block_type)
    if normalized in palette:
        return normalized
    if normalized.startswith(("actor_", "prop_")):
        raise ValueError(f"Unsupported entity marker type: {normalized}")
    palette[normalized] = custom_block_meta(normalized)
    return normalized


def palette_prompt_text(palette: Dict[str, Dict[str, str]] | None = None) -> str:
    palette = palette if palette is not None else VOXEL_SEMANTIC_PALETTE
    lines = []
    for block_type, meta in palette.items():
        marker_role = meta.get("marker_role", "environment_or_fixture")
        lines.append(
            f"- {block_type}: {meta['label']}; object class {meta['object_class']}; "
            f"semantic label color {meta['color']}; "
            f"sketch policy {meta['sketch_color_policy']}; marker role {marker_role}"
        )
    return "\n".join(lines)
