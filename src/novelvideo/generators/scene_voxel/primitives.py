"""Voxel primitives for AI-authored scene build scripts.

The codegen agent writes a build_script.py per scene that imports VoxelScene
from this module. Keep the API tight + obvious so the agent doesn't need to
guess; primitives.py is the contract.

5cm/voxel default. Y is up, X is right, Z is depth-forward from master cam.
"""

from __future__ import annotations

import random
import struct
from pathlib import Path
from typing import Iterable


MAX_GRID = 256
# Default voxel size = 3cm. Finer than v2's 5cm — gives each piece of furniture
# more voxels (chair seat = 7×7×2 voxels instead of 4×4×1) so things like
# chopstick jars, condiment bottles, door handles read clearly. At 3cm the room
# can be up to 7.5m wide before hitting MAX_GRID; scripts for larger scenes
# (street, big interior) should pass `vox_size_m=0.04` or `0.05` explicitly.
VOX_SIZE_M = 0.03


def make_rng(seed: int = 20260513) -> random.Random:
    """Seeded RNG. Use rng.random(), rng.randint() inside build scripts for
    deterministic weathering / variation."""
    return random.Random(seed)


class VoxelScene:
    """In-memory voxel grid with palette + .vox writer.

    Coordinate convention:
        x ∈ [0, gx)   right
        y ∈ [0, gy)   up
        z ∈ [0, gz)   depth (away from master camera)

    The grid is bounded by room dimensions in meters; gx/gy/gz are derived
    from vox_size_m. All `set_v` / `fill_*` calls silently clip out-of-bounds
    voxels so partial overdraw at edges is safe.
    """

    def __init__(
        self,
        room_w_m: float,
        room_d_m: float,
        room_h_m: float,
        vox_size_m: float = VOX_SIZE_M,
    ) -> None:
        self.vox_size = vox_size_m
        self.gx = max(2, int(round(room_w_m / vox_size_m)))
        self.gz = max(2, int(round(room_d_m / vox_size_m)))
        self.gy = max(2, int(round(room_h_m / vox_size_m)))
        if max(self.gx, self.gy, self.gz) >= MAX_GRID:
            raise ValueError(
                f"Room too large for single-model .vox ({MAX_GRID-1} voxel max per axis). "
                f"Got gx={self.gx}, gy={self.gy}, gz={self.gz}. "
                f"Reduce room dims or increase vox_size_m."
            )
        self.voxels: dict[tuple[int, int, int], int] = {}
        self._palette: dict[str, tuple[int, int, int]] = {}
        self._name_to_idx: dict[str, int] = {}

    # ----- palette ----------------------------------------------------------

    def add_color(self, name: str, rgb: tuple[int, int, int]) -> str:
        """Register palette entry. Idempotent — re-adding same name is a no-op.
        Returns the name (so you can inline calls)."""
        if name in self._palette:
            return name
        if len(self._palette) >= 254:
            raise ValueError(f"Palette limit (254) exceeded when adding {name!r}")
        r, g, b = (int(c) for c in rgb)
        r = max(0, min(255, r))
        g = max(0, min(255, g))
        b = max(0, min(255, b))
        self._palette[name] = (r, g, b)
        self._name_to_idx[name] = len(self._palette)
        return name

    def add_palette(self, palette: dict[str, tuple[int, int, int]]) -> None:
        """Bulk register colors."""
        for n, rgb in palette.items():
            self.add_color(n, rgb)

    @property
    def palette(self) -> dict[str, tuple[int, int, int]]:
        return dict(self._palette)

    # ----- drawing ----------------------------------------------------------

    def set_v(self, x: int, y: int, z: int, color_name: str) -> None:
        if 0 <= x < self.gx and 0 <= y < self.gy and 0 <= z < self.gz:
            idx = self._name_to_idx.get(color_name)
            if idx is None:
                raise KeyError(
                    f"Color {color_name!r} not registered. Call add_color() first."
                )
            self.voxels[(x, y, z)] = idx

    def fill_box(self, x0, y0, z0, x1, y1, z1, color_name: str) -> None:
        """Filled axis-aligned box, inclusive bounds. Order doesn't matter."""
        idx = self._name_to_idx.get(color_name)
        if idx is None:
            raise KeyError(
                f"Color {color_name!r} not registered. Call add_color() first."
            )
        xa, xb = min(x0, x1), max(x0, x1)
        ya, yb = min(y0, y1), max(y0, y1)
        za, zb = min(z0, z1), max(z0, z1)
        for x in range(xa, xb + 1):
            if not (0 <= x < self.gx):
                continue
            for y in range(ya, yb + 1):
                if not (0 <= y < self.gy):
                    continue
                for z in range(za, zb + 1):
                    if 0 <= z < self.gz:
                        self.voxels[(x, y, z)] = idx

    def fill_disk(
        self, cx: int, cy: int, cz: int, r: int, color_name: str, thick: int = 1
    ) -> None:
        """Filled disk in XZ plane at height cy, optionally extruded thick voxels in y."""
        idx = self._name_to_idx.get(color_name)
        if idx is None:
            raise KeyError(
                f"Color {color_name!r} not registered. Call add_color() first."
            )
        r2 = r * r
        for dx in range(-r, r + 1):
            for dz in range(-r, r + 1):
                if dx * dx + dz * dz <= r2:
                    for dy in range(thick):
                        x, y, z = cx + dx, cy + dy, cz + dz
                        if 0 <= x < self.gx and 0 <= y < self.gy and 0 <= z < self.gz:
                            self.voxels[(x, y, z)] = idx

    def fill_cylinder(
        self, cx: int, y0: int, cz: int, r: int, h: int, color_name: str
    ) -> None:
        """Vertical cylinder: stack of fill_disks from y0 to y0+h-1."""
        for dy in range(h):
            self.fill_disk(cx, y0 + dy, cz, r, color_name)

    def fill_sphere_random(
        self,
        cx: int,
        cy: int,
        cz: int,
        r: int,
        color_names: Iterable[str],
        rng: random.Random,
        density: float = 0.55,
    ) -> None:
        """Irregular sphere (foliage / cloud): random pick from color_names per voxel."""
        names = list(color_names)
        if not names:
            return
        r2 = r * r
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                for dz in range(-r, r + 1):
                    if dx * dx + dy * dy + dz * dz <= r2 and rng.random() < density:
                        self.set_v(cx + dx, cy + dy, cz + dz, rng.choice(names))

    def clear_box(self, x0, y0, z0, x1, y1, z1) -> None:
        """Remove voxels in a box (carve)."""
        xa, xb = min(x0, x1), max(x0, x1)
        ya, yb = min(y0, y1), max(y0, y1)
        za, zb = min(z0, z1), max(z0, z1)
        for x in range(xa, xb + 1):
            for y in range(ya, yb + 1):
                for z in range(za, zb + 1):
                    self.voxels.pop((x, y, z), None)

    # ----- write ------------------------------------------------------------

    @property
    def voxel_count(self) -> int:
        return len(self.voxels)

    def write_vox(self, path: Path | str) -> Path:
        """Serialize to MagicaVoxel format 150 (single model)."""
        path = Path(path)
        if not self.voxels:
            raise ValueError("Empty scene — no voxels to write")

        ordered = list(self._palette.keys())[:254]
        max_x = max(p[0] for p in self.voxels) + 1
        max_y = max(p[1] for p in self.voxels) + 1
        max_z = max(p[2] for p in self.voxels) + 1
        # MagicaVoxel uses (x, y, z) with z up; we use y up, so swap.
        sx, sy, sz = max_x, max_z, max_y

        def chunk(tag: bytes, content: bytes, children: bytes = b"") -> bytes:
            return tag + struct.pack("<ii", len(content), len(children)) + content + children

        size_chunk = chunk(b"SIZE", struct.pack("<iii", sx, sy, sz))
        xyzi_body = struct.pack("<i", len(self.voxels))
        for (x, y, z), cidx in self.voxels.items():
            xyzi_body += struct.pack("<BBBB", x, z, y, cidx)
        xyzi_chunk = chunk(b"XYZI", xyzi_body)

        rgba = bytearray(256 * 4)
        for i, name in enumerate(ordered):
            r, g, b = self._palette[name]
            rgba[i * 4 : i * 4 + 4] = bytes((r, g, b, 255))
        rgba_chunk = chunk(b"RGBA", bytes(rgba))

        pack_chunk = chunk(b"PACK", struct.pack("<i", 1))
        main = chunk(b"MAIN", b"", pack_chunk + size_chunk + xyzi_chunk + rgba_chunk)

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"VOX " + struct.pack("<i", 150) + main)
        return path


__all__ = ["VoxelScene", "make_rng", "VOX_SIZE_M", "MAX_GRID"]
