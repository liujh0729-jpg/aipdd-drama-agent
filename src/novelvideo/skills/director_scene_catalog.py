"""Scene catalog extraction for AI Director Skill.

Given a Director world.json, compute the structured catalog the AI agent
needs (seats, tables, camera presets, walkable bounds, key fixtures) so the
agent emits semantic ops referencing real anchors instead of guessing
coordinates.

Mirrors the editor's allSeatAnchors / seatAnchorForBlock / seatFacingYaw
logic from `BuilderGPT/app/viewer/supertale_director_stage.html`.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field


SeatType = Literal["chair", "bench"]


class Seat(BaseModel):
    id: str
    type: SeatType
    position: list[float]
    facing_yaw: float
    near_table_id: str | None = None


class Table(BaseModel):
    id: str
    position: list[float]
    size: list[int]


class CameraPreset(BaseModel):
    id: str
    label: str = ""
    position: list[float]
    target: list[float]
    fov: float = 58


class WalkableBounds(BaseModel):
    x: list[float]
    z: list[float]


class KeyFixture(BaseModel):
    type: str
    count: int
    sample_position: list[float] | None = None


class SceneCatalog(BaseModel):
    scene_id: str
    seats: list[Seat] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)
    camera_presets: list[CameraPreset] = Field(default_factory=list)
    walkable_bounds: WalkableBounds | None = None
    key_fixtures: list[KeyFixture] = Field(default_factory=list)


_BlockKey = tuple[int, int, int]


@dataclass(frozen=True)
class _Block:
    x: int
    y: int
    z: int
    type: str

    @property
    def key(self) -> _BlockKey:
        return (self.x, self.y, self.z)


def _iter_blocks(world: dict[str, Any]) -> list[_Block]:
    out: list[_Block] = []
    for raw in world.get("blocks") or []:
        try:
            x = int(raw["x"])
            y = int(raw["y"])
            z = int(raw["z"])
        except (KeyError, TypeError, ValueError):
            continue
        block_type = str(raw.get("type") or "").strip()
        if not block_type:
            continue
        out.append(_Block(x=x, y=y, z=z, type=block_type))
    return out


def _index_blocks(blocks: Iterable[_Block]) -> dict[_BlockKey, str]:
    return {block.key: block.type for block in blocks}


def _bench_axis(block_index: dict[_BlockKey, str], x: int, y: int, z: int) -> str:
    has_x = (
        block_index.get((x - 1, y, z)) == "bench"
        or block_index.get((x + 1, y, z)) == "bench"
    )
    has_z = (
        block_index.get((x, y, z - 1)) == "bench"
        or block_index.get((x, y, z + 1)) == "bench"
    )
    if has_x and not has_z:
        return "x"
    if has_z and not has_x:
        return "z"
    return "single"


def _seat_id(x: int, y: int, z: int) -> str:
    return f"seat:{x},{y},{z}"


def _bench_seat_id(x: int, y: int, z: int, axis: str, index: int) -> str:
    return f"bench:{x},{y},{z}:{axis}:{index}"


def _yaw_to_face(from_x: float, from_z: float, to_x: float, to_z: float) -> float:
    return math.atan2(to_x - from_x, to_z - from_z)


def _nearest_block_of_types(
    block_index: dict[_BlockKey, str],
    types: tuple[str, ...],
    x: int,
    z: int,
    *,
    max_distance: int = 4,
) -> tuple[int, int, int, str] | None:
    """Manhattan-nearest block in the given type set, with a small y bias to
    prefer floor-level fixtures."""
    best: tuple[float, tuple[int, int, int, str]] | None = None
    for (bx, by, bz), btype in block_index.items():
        if btype not in types:
            continue
        distance = abs(bx - x) + abs(bz - z) + max(0, by - 1) * 0.5
        if distance > max_distance:
            continue
        if best is None or distance < best[0]:
            best = (distance, (bx, by, bz, btype))
    return best[1] if best else None


def _build_table_lookup(tables: list[Table]) -> dict[tuple[int, int, int], str]:
    """Map (block_x, block_y, block_z) → table_id for any block belonging to a
    cluster. Keyed by full 3D coord so stacked clusters at same (x,z) are not
    conflated."""
    lookup: dict[tuple[int, int, int], str] = {}
    for table in tables:
        ax, ay, az = (int(v) for v in table.position)
        sx, sy, sz = table.size
        for dx in range(sx):
            for dy in range(sy):
                for dz in range(sz):
                    lookup[(ax + dx, ay + dy, az + dz)] = table.id
    return lookup


def enumerate_seats(
    blocks: list[_Block], tables: list[Table] | None = None
) -> list[Seat]:
    block_index = _index_blocks(blocks)
    table_lookup = _build_table_lookup(tables or [])
    seats: list[Seat] = []
    for block in blocks:
        if block.type not in {"chair", "bench"}:
            continue
        if block.type == "chair":
            seat_id = _seat_id(block.x, block.y, block.z)
        else:  # bench
            axis = _bench_axis(block_index, block.x, block.y, block.z)
            seat_id = _bench_seat_id(block.x, block.y, block.z, axis, 0)
        nearest_table_block = _nearest_block_of_types(
            block_index, ("table", "counter"), block.x, block.z, max_distance=4
        )
        if nearest_table_block:
            tx, ty, tz, ttype = nearest_table_block
            facing = _yaw_to_face(block.x, block.z, tx, tz)
            if ttype == "counter":
                near_table = f"counter:{tx},{ty},{tz}"
            else:
                near_table = table_lookup.get((tx, ty, tz)) or f"table:{tx},{ty},{tz}"
        else:
            facing = 0.0
            near_table = None
        seats.append(
            Seat(
                id=seat_id,
                type=block.type,  # type: ignore[arg-type]
                position=[float(block.x), float(block.y), float(block.z)],
                facing_yaw=float(facing),
                near_table_id=near_table,
            )
        )
    return seats


def enumerate_tables(blocks: list[_Block]) -> list[Table]:
    """Cluster adjacent table blocks (sharing y, neighbors in x or z) into one
    logical table. Each cluster gets the lower-left block's id as canonical."""
    table_blocks = [b for b in blocks if b.type == "table"]
    if not table_blocks:
        return []

    block_set = {b.key for b in table_blocks}
    seen: set[_BlockKey] = set()
    clusters: list[list[_Block]] = []

    for block in table_blocks:
        if block.key in seen:
            continue
        # BFS cluster
        cluster: list[_Block] = []
        stack: list[_BlockKey] = [block.key]
        while stack:
            key = stack.pop()
            if key in seen:
                continue
            if key not in block_set:
                continue
            seen.add(key)
            cluster.append(_Block(x=key[0], y=key[1], z=key[2], type="table"))
            x, y, z = key
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                stack.append((x + dx, y, z + dz))
        clusters.append(cluster)

    tables: list[Table] = []
    for cluster in clusters:
        xs = sorted({c.x for c in cluster})
        zs = sorted({c.z for c in cluster})
        ys = sorted({c.y for c in cluster})
        anchor = min(cluster, key=lambda b: (b.x, b.z))
        tables.append(
            Table(
                id=f"table:{anchor.x},{anchor.y},{anchor.z}",
                position=[float(anchor.x), float(anchor.y), float(anchor.z)],
                size=[
                    int(xs[-1] - xs[0] + 1),
                    int(ys[-1] - ys[0] + 1),
                    int(zs[-1] - zs[0] + 1),
                ],
            )
        )
    return tables


def compute_walkable_bounds(blocks: list[_Block]) -> WalkableBounds | None:
    floor_blocks = [b for b in blocks if b.type == "floor"]
    if not floor_blocks:
        return None
    xs = [b.x for b in floor_blocks]
    zs = [b.z for b in floor_blocks]
    return WalkableBounds(
        x=[float(min(xs)), float(max(xs))],
        z=[float(min(zs)), float(max(zs))],
    )


_FIXTURE_TYPES = (
    "door",
    "window",
    "counter",
    "menu_board",
    "shop_sign",
    "shelf",
    "ceiling_fan",
    "noodle_pot",
    "appliance",
    "decoration",
)


def summarize_key_fixtures(blocks: list[_Block]) -> list[KeyFixture]:
    counts: dict[str, list[_Block]] = {}
    for block in blocks:
        if block.type in _FIXTURE_TYPES:
            counts.setdefault(block.type, []).append(block)
    out = []
    for fixture_type, items in sorted(counts.items(), key=lambda kv: -len(kv[1])):
        sample = items[0]
        out.append(
            KeyFixture(
                type=fixture_type,
                count=len(items),
                sample_position=[float(sample.x), float(sample.y), float(sample.z)],
            )
        )
    return out


def extract_camera_presets(world: dict[str, Any]) -> list[CameraPreset]:
    presets: list[CameraPreset] = []
    for raw in world.get("camera_presets") or []:
        try:
            position = [float(v) for v in raw.get("position") or []]
            target = [float(v) for v in raw.get("target") or []]
        except (TypeError, ValueError):
            continue
        if len(position) < 3 or len(target) < 3:
            continue
        presets.append(
            CameraPreset(
                id=str(raw.get("id") or "unnamed"),
                label=str(raw.get("label") or ""),
                position=position[:3],
                target=target[:3],
                fov=float(raw.get("fov") or 58),
            )
        )
    return presets


def extract_scene_catalog(world: dict[str, Any]) -> SceneCatalog:
    """Top-level: build a SceneCatalog from a world.json dict."""
    blocks = _iter_blocks(world)
    tables = enumerate_tables(blocks)
    return SceneCatalog(
        scene_id=str(world.get("scene_id") or "unnamed_scene"),
        seats=enumerate_seats(blocks, tables),
        tables=tables,
        camera_presets=extract_camera_presets(world),
        walkable_bounds=compute_walkable_bounds(blocks),
        key_fixtures=summarize_key_fixtures(blocks),
    )


def load_scene_catalog(world_path: Path) -> SceneCatalog:
    world = json.loads(Path(world_path).read_text(encoding="utf-8"))
    return extract_scene_catalog(world)
