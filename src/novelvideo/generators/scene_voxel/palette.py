"""Palette extraction: master.png + reverse_master.png → 24-32 color palette.

K-means in Lab color space on combined pixels. Returns a dict[name, (r,g,b)]
that the voxel builder uses for the .vox palette indices.
"""

from __future__ import annotations

import colorsys
from pathlib import Path

import numpy as np
from PIL import Image


def _kmeans(points: np.ndarray, k: int, n_iter: int = 25, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """numpy k-means++ — returns (centroids, labels). Reused from BuilderGPT/extract_palette.py."""
    rng = np.random.default_rng(seed)
    k = min(k, len(points))
    # k-means++ init
    idx = [int(rng.integers(0, points.shape[0]))]
    for _ in range(1, k):
        # squared distance to nearest existing centroid
        existing = points[idx]
        # Sample to avoid O(N*k) on big inputs
        sample_size = min(20000, len(points))
        sample_idx = rng.choice(len(points), size=sample_size, replace=False)
        sample_points = points[sample_idx]
        dists = np.min(
            np.linalg.norm(sample_points[:, None, :] - existing[None, :, :], axis=2), axis=1
        ) ** 2
        s = dists.sum()
        if s <= 0:
            idx.append(int(rng.integers(0, points.shape[0])))
            continue
        probs = dists / s
        choice = int(rng.choice(sample_size, p=probs))
        idx.append(int(sample_idx[choice]))
    centroids = points[idx].copy().astype(np.float32)
    labels = np.zeros(len(points), dtype=np.int32)
    for _ in range(n_iter):
        d = np.linalg.norm(points[:, None, :] - centroids[None, :, :], axis=2)
        labels = d.argmin(axis=1).astype(np.int32)
        new_c = np.array(
            [
                points[labels == j].mean(axis=0) if (labels == j).any() else centroids[j]
                for j in range(k)
            ],
            dtype=np.float32,
        )
        if np.allclose(new_c, centroids, atol=1e-3):
            centroids = new_c
            break
        centroids = new_c
    return centroids, labels


def _rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """Approximate sRGB → CIE Lab. rgb is HxWx3 or Nx3 uint8."""
    arr = rgb.astype(np.float32) / 255.0
    # sRGB → linear
    mask = arr > 0.04045
    arr_lin = np.where(mask, ((arr + 0.055) / 1.055) ** 2.4, arr / 12.92)
    # linear sRGB → XYZ (D65)
    M = np.array(
        [
            [0.4124564, 0.3575761, 0.1804375],
            [0.2126729, 0.7151522, 0.0721750],
            [0.0193339, 0.1191920, 0.9503041],
        ],
        dtype=np.float32,
    )
    flat = arr_lin.reshape(-1, 3)
    xyz = flat @ M.T
    # normalize by D65 white
    white = np.array([0.95047, 1.00000, 1.08883], dtype=np.float32)
    xyz_n = xyz / white
    delta = 6 / 29
    f = np.where(xyz_n > delta ** 3, np.cbrt(xyz_n), xyz_n / (3 * delta * delta) + 4 / 29)
    L = 116 * f[:, 1] - 16
    a = 500 * (f[:, 0] - f[:, 1])
    b = 200 * (f[:, 1] - f[:, 2])
    return np.stack([L, a, b], axis=1).reshape(rgb.shape)


def _name_color(rgb: tuple[int, int, int]) -> str:
    """Heuristic color name: returns a short identifier like 'dark_teal', 'warm_wood'."""
    r, g, b = (c / 255.0 for c in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    # Tone (lightness)
    if v < 0.18:
        tone = "very_dark"
    elif v < 0.35:
        tone = "dark"
    elif v < 0.60:
        tone = "mid"
    elif v < 0.82:
        tone = "light"
    else:
        tone = "bright"
    # Hue family
    if s < 0.12:
        family = "gray"
    else:
        h_deg = h * 360
        if h_deg < 15 or h_deg >= 345:
            family = "red"
        elif h_deg < 45:
            family = "orange"
        elif h_deg < 70:
            family = "yellow"
        elif h_deg < 160:
            family = "green"
        elif h_deg < 200:
            family = "teal"
        elif h_deg < 250:
            family = "blue"
        elif h_deg < 290:
            family = "purple"
        else:
            family = "magenta"
    return f"{tone}_{family}"


def extract_palette(
    image_paths: list[Path],
    k: int = 28,
    max_pixels_per_image: int = 200_000,
    seed: int = 42,
) -> dict[str, tuple[int, int, int]]:
    """K-means in Lab on combined pixels from the given images.

    Returns dict {name: (r, g, b)}. Names are heuristically derived so duplicates
    get numbered suffixes (e.g. 'dark_teal', 'dark_teal_2').
    """
    pixels: list[np.ndarray] = []
    for p in image_paths:
        if not p or not Path(p).exists():
            continue
        img = Image.open(p).convert("RGB")
        arr = np.asarray(img)
        flat = arr.reshape(-1, 3)
        if len(flat) > max_pixels_per_image:
            rng = np.random.default_rng(seed)
            idx = rng.choice(len(flat), size=max_pixels_per_image, replace=False)
            flat = flat[idx]
        pixels.append(flat)

    if not pixels:
        # Fallback minimal palette
        return {
            "gray_floor": (80, 80, 80),
            "white_wall": (220, 220, 220),
            "shadow": (20, 20, 20),
        }

    combined = np.concatenate(pixels, axis=0)
    lab = _rgb_to_lab(combined)
    _centroids, labels = _kmeans(lab, k=k, seed=seed)
    # Map cluster centers back to RGB by taking median of cluster's actual pixels
    rgb_centers: list[tuple[int, int, int]] = []
    n_clusters = int(labels.max()) + 1 if len(labels) else 0
    for ci in range(n_clusters):
        mask = labels == ci
        if not mask.any():
            continue
        cluster_pixels = combined[mask]
        med = np.median(cluster_pixels, axis=0)
        rgb_centers.append((int(med[0]), int(med[1]), int(med[2])))

    # Name each color, dedupe with suffix
    name_counts: dict[str, int] = {}
    palette: dict[str, tuple[int, int, int]] = {}
    for rgb in rgb_centers:
        name = _name_color(rgb)
        if name in name_counts:
            name_counts[name] += 1
            suffix = name_counts[name]
            palette[f"{name}_{suffix}"] = rgb
        else:
            name_counts[name] = 1
            palette[name] = rgb

    return palette


def find_closest_color(
    palette: dict[str, tuple[int, int, int]],
    hint: str,
    fallback_rgb: tuple[int, int, int] | None = None,
) -> str:
    """Match a free-text color hint to the closest palette entry by name family."""
    hint_lc = (hint or "").lower().strip()
    if not hint_lc:
        # Pick a mid-tone gray-ish fallback
        return _pick_by_value(palette, "mid_gray", fallback_rgb)

    # Tokenize hint: try to find tone + family matches
    tone_kw = {
        "dark": "dark",
        "deep": "dark",
        "black": "very_dark",
        "shadow": "very_dark",
        "mid": "mid",
        "medium": "mid",
        "light": "light",
        "pale": "light",
        "bright": "bright",
        "neon": "bright",
        "white": "bright",
    }
    family_kw = {
        "red": "red",
        "crimson": "red",
        "orange": "orange",
        "amber": "orange",
        "tan": "orange",
        "yellow": "yellow",
        "gold": "yellow",
        "cream": "yellow",
        "green": "green",
        "forest": "green",
        "olive": "green",
        "mint": "green",
        "teal": "teal",
        "aqua": "teal",
        "cyan": "teal",
        "blue": "blue",
        "navy": "blue",
        "indigo": "blue",
        "purple": "purple",
        "violet": "purple",
        "magenta": "magenta",
        "pink": "magenta",
        "gray": "gray",
        "grey": "gray",
        "steel": "gray",
        "concrete": "gray",
        "stone": "gray",
        "brown": "orange",   # brown ≈ dark orange in HSV
        "wood": "orange",
        "tile": "gray",
    }

    tone = ""
    family = ""
    for kw, t in tone_kw.items():
        if kw in hint_lc:
            tone = t
            break
    for kw, f in family_kw.items():
        if kw in hint_lc:
            family = f
            break

    # Prefer exact tone_family match
    if tone and family:
        target = f"{tone}_{family}"
        for name in palette:
            if name.startswith(target):
                return name
    # Try family alone
    if family:
        for name in palette:
            if f"_{family}" in name or name.endswith(family):
                return name
    # Try tone alone
    if tone:
        for name in palette:
            if name.startswith(tone):
                return name

    return _pick_by_value(palette, "mid_gray", fallback_rgb)


def find_color_family(
    palette: dict[str, tuple[int, int, int]],
    base_name: str,
    step: int = 2,
) -> dict[str, str]:
    """Find tonal variations of `base_name` for shading / weathering.

    Sorts palette by luminance (Rec.601) and steps ±N positions to find
    darker / lighter neighbors. Returns dict with 'darker' and 'lighter'
    palette keys; both fall back to `base_name` if it isn't in the palette
    or there aren't enough neighbors.
    """
    if base_name not in palette:
        return {"darker": base_name, "lighter": base_name}
    items = sorted(
        palette.items(),
        key=lambda kv: 0.299 * kv[1][0] + 0.587 * kv[1][1] + 0.114 * kv[1][2],
    )
    keys = [k for k, _ in items]
    i = keys.index(base_name)
    return {
        "darker": keys[max(0, i - step)],
        "lighter": keys[min(len(keys) - 1, i + step)],
    }


def pick_global_anchor(
    palette: dict[str, tuple[int, int, int]],
    role: str,
) -> str:
    """Pick a palette entry that plays a scene-wide structural role.

    Roles:
        'shadow'      — darkest entry (for shadows, deep cavities)
        'steel'       — coolest mid-light gray (counter top, appliance)
        'warm_accent' — warmest light (light fixture, exterior glow)
        'wood'        — darkest warm (table apron, wood base)
    """
    if not palette:
        return ""
    items = list(palette.items())

    def luma(rgb: tuple[int, int, int]) -> float:
        return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]

    def warmth(rgb: tuple[int, int, int]) -> float:
        # +ve = warm (r+g > b), -ve = cool
        r, g, b = rgb
        return (r + g * 0.5) - b

    def saturation(rgb: tuple[int, int, int]) -> float:
        return max(rgb) - min(rgb)

    if role == "shadow":
        return min(items, key=lambda kv: luma(kv[1]))[0]
    if role == "steel":
        # cool, low saturation, mid-light
        cool_mid = [kv for kv in items if 80 <= luma(kv[1]) <= 200 and saturation(kv[1]) < 40]
        if cool_mid:
            return min(cool_mid, key=lambda kv: warmth(kv[1]))[0]
        return min(items, key=lambda kv: abs(luma(kv[1]) - 140))[0]
    if role == "warm_accent":
        warm_light = [kv for kv in items if luma(kv[1]) > 130]
        if warm_light:
            return max(warm_light, key=lambda kv: warmth(kv[1]))[0]
        return max(items, key=lambda kv: warmth(kv[1]))[0]
    if role == "wood":
        warm_dark = [kv for kv in items if luma(kv[1]) < 100 and warmth(kv[1]) > 0]
        if warm_dark:
            return max(warm_dark, key=lambda kv: warmth(kv[1]))[0]
        return next(iter(palette))
    return next(iter(palette))


def _pick_by_value(
    palette: dict[str, tuple[int, int, int]],
    preferred_name: str,
    fallback_rgb: tuple[int, int, int] | None,
) -> str:
    if preferred_name in palette:
        return preferred_name
    if fallback_rgb is not None:
        # Find palette entry with smallest squared RGB distance
        best = None
        best_d = float("inf")
        for name, rgb in palette.items():
            d = sum((rgb[i] - fallback_rgb[i]) ** 2 for i in range(3))
            if d < best_d:
                best_d = d
                best = name
        if best is not None:
            return best
    # Fallback: return any entry
    return next(iter(palette))
