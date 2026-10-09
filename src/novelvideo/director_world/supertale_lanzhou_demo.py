from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Iterable, Tuple

try:
    from .supertale_voxel_palette import VOXEL_SEMANTIC_PALETTE, normalize_block_type
except ImportError:  # pragma: no cover - allows direct script execution
    from supertale_voxel_palette import VOXEL_SEMANTIC_PALETTE, normalize_block_type


Coord = Tuple[int, int, int]


PALETTE: Dict[str, Dict[str, str]] = VOXEL_SEMANTIC_PALETTE


class BlockWorld:
    def __init__(self, palette: Dict[str, Dict[str, str]] | None = None) -> None:
        self.palette = palette if palette is not None else PALETTE
        self.blocks: Dict[Coord, str] = {}

    def safe_set_block(self, x: int, y: int, z: int, block_type: str) -> None:
        block_type = normalize_block_type(block_type)
        if block_type not in self.palette:
            raise ValueError(f"Unknown block type: {block_type}")
        self.blocks[(int(x), int(y), int(z))] = block_type

    def safe_fill(
        self,
        x1: int,
        y1: int,
        z1: int,
        x2: int,
        y2: int,
        z2: int,
        block_type: str,
        *,
        mode: str = "replace",
    ) -> None:
        block_type = normalize_block_type(block_type)
        if block_type not in self.palette:
            raise ValueError(f"Unknown block type: {block_type}")
        min_x, max_x = sorted((int(x1), int(x2)))
        min_y, max_y = sorted((int(y1), int(y2)))
        min_z, max_z = sorted((int(z1), int(z2)))
        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                for z in range(min_z, max_z + 1):
                    if mode == "keep" and (x, y, z) in self.blocks:
                        continue
                    if mode == "hollow":
                        surface = x in (min_x, max_x) or y in (min_y, max_y) or z in (min_z, max_z)
                        if not surface:
                            continue
                    self.blocks[(x, y, z)] = block_type

    def remove(self, coords: Iterable[Coord]) -> None:
        for coord in coords:
            self.blocks.pop(coord, None)

    def sorted_blocks(self) -> list[dict[str, int | str]]:
        return [
            {"x": x, "y": y, "z": z, "type": block_type}
            for (x, y, z), block_type in sorted(self.blocks.items())
        ]

    def bounds(self) -> dict[str, list[int]]:
        if not self.blocks:
            return {"min": [0, 0, 0], "max": [0, 0, 0]}
        xs = [coord[0] for coord in self.blocks]
        ys = [coord[1] for coord in self.blocks]
        zs = [coord[2] for coord in self.blocks]
        return {"min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]}


def add_stool(world: BlockWorld, x: int, z: int) -> None:
    world.safe_set_block(x, 1, z, "chair")
    world.safe_set_block(x, 2, z, "chair")


def add_simple_actor(world: BlockWorld, x: int, z: int, block_type: str) -> None:
    world.safe_set_block(x, 1, z, block_type)
    world.safe_set_block(x, 2, z, block_type)
    world.safe_set_block(x, 3, z, block_type)


def build_lanzhou_noodle_shop() -> dict:
    world = BlockWorld()

    # Coordinate plan: x left/right, y up, z front/back. Back kitchen wall is z=7.
    min_x, max_x = -12, 11
    min_z, max_z = -8, 7
    wall_y1, wall_y2 = 1, 6

    for x in range(min_x, max_x + 1):
        for z in range(min_z, max_z + 1):
            world.safe_set_block(x, 0, z, "floor")

    world.safe_fill(min_x, wall_y1, max_z, max_x, wall_y2, max_z, "wall")
    world.safe_fill(min_x, wall_y1, min_z, max_x, wall_y2, min_z, "wall")
    world.safe_fill(min_x, wall_y1, min_z, min_x, wall_y2, max_z, "wall")
    world.safe_fill(max_x, wall_y1, min_z, max_x, wall_y2, max_z, "wall")

    # Left dining window wall.
    world.safe_fill(min_x, 2, -4, min_x, 5, 4, "window")
    world.safe_fill(min_x + 1, 2, -4, min_x + 1, 2, 4, "window")
    for z in (-4, -1, 2, 4):
        world.safe_fill(min_x, 2, z, min_x, 5, z, "window")

    # Right entrance glass door near the front.
    world.safe_fill(max_x, 1, -6, max_x, 4, -2, "door")
    world.safe_fill(max_x - 1, 1, -6, max_x - 1, 1, -2, "door")

    # Back kitchen counter and cooking hood.
    world.safe_fill(-8, 1, 5, 5, 2, 6, "counter")
    world.safe_fill(-8, 3, 5, 5, 3, 5, "counter")
    world.safe_fill(-3, 5, 5, 3, 5, 6, "appliance")
    world.safe_fill(-2, 4, 6, 2, 4, 6, "appliance")

    # Menu board on the kitchen wall.
    world.safe_fill(-7, 4, max_z, -2, 5, max_z, "menu_board")
    for x in (-6, -4, -2):
        world.safe_set_block(x, 5, max_z, "menu_board")
        world.safe_set_block(x, 4, max_z, "menu_board")

    # Shelves and kitchen objects.
    world.safe_fill(6, 2, 6, 9, 5, 6, "shelf")
    world.safe_fill(6, 3, 5, 9, 3, 5, "shelf")
    world.safe_fill(7, 1, 5, 8, 1, 5, "appliance")

    # Window bar table and stools.
    world.safe_fill(min_x + 2, 2, -5, min_x + 3, 2, 2, "table")
    for z in (-5, -3, -1, 1):
        add_stool(world, min_x + 4, z)

    # Square dining table near the window.
    world.safe_fill(-6, 2, 2, -4, 2, 4, "table")
    for coord in [(-7, 2), (-3, 2), (-7, 4), (-3, 4)]:
        add_stool(world, coord[0], coord[1])

    # Rear dining table.
    world.safe_fill(-3, 2, -6, 0, 2, -5, "table")
    for coord in [(-4, -6), (1, -6), (-4, -5), (1, -5)]:
        add_stool(world, coord[0], coord[1])

    # Checkout desk near entrance.
    world.safe_fill(7, 1, -5, 10, 2, -3, "counter")
    world.safe_set_block(8, 3, -4, "appliance")

    # Lanterns and wall fan.
    for x in (-8, 0, 8):
        world.safe_set_block(x, 6, 2, "decoration")
        world.safe_set_block(x, 5, 2, "decoration")
    world.safe_fill(min_x + 1, 5, 4, min_x + 1, 5, 5, "appliance")
    world.safe_set_block(min_x + 2, 5, 4, "appliance")

    # A few plants/props for child editing affordance.
    world.safe_fill(9, 1, 3, 10, 2, 4, "plant")
    world.safe_set_block(9, 3, 3, "plant")

    used_types = {block_type for block_type in world.blocks.values()}
    scene_palette = {
        block_type: meta for block_type, meta in world.palette.items() if block_type in used_types
    }

    return {
        "schema_version": "minecraft_scene_spec_v0",
        "scene_id": "lanzhou_noodle_shop",
        "display_name": "兰州拉面馆 - 手写测试场景",
        "generator": "novelvideo.director_world.supertale_lanzhou_demo",
        "grid": {
            "block_size_m": 0.45,
            "axes": "x_right_y_up_z_forward",
            "origin": "room_center_floor",
        },
        "palette": scene_palette,
        "local_type_registry": {},
        "bounds": world.bounds(),
        "blocks": world.sorted_blocks(),
        "camera_presets": [
            {
                "id": "front_dining_to_counter",
                "label": "前场看向柜台",
                "position": [0, 5, -11],
                "target": [0, 2.5, 4],
                "fov": 58,
            },
            {
                "id": "window_table_reverse",
                "label": "靠窗桌反打",
                "position": [-9, 4, 0],
                "target": [-4, 2.5, 3],
                "fov": 60,
            },
            {
                "id": "entrance_checkout",
                "label": "入口收银机位",
                "position": [13, 4, -8],
                "target": [7, 2, -4],
                "fov": 58,
            },
            {
                "id": "top_down",
                "label": "俯视布局",
                "position": [0, 20, 0],
                "target": [0, 0, 0],
                "fov": 45,
            },
        ],
        "notes": (
            "This is a Minecraft-style director stage seed, not an exact 360 reconstruction. "
            "It is built from deterministic safeFill/safeSetBlock operations so children and "
            "directors can edit the world before taking camera screenshots."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a block-world Lanzhou noodle shop MVP.")
    parser.add_argument(
        "--output",
        default="generated/lanzhou_noodle_shop_scene.json",
        help="Output scene spec path relative to the current working directory unless absolute.",
    )
    args = parser.parse_args()

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = (Path.cwd() / output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    scene = build_lanzhou_noodle_shop()
    output_path.write_text(json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {output_path}")
    print(f"blocks={len(scene['blocks'])} palette={len(scene['palette'])}")


if __name__ == "__main__":
    main()
