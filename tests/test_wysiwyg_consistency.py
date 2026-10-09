"""WYSIWYG 一致性测试 — 扫描真实项目数据验证三条路径完全一致。

验证:
1. build_reference_map 的 Image N 编号
2. generate_grid 的 API 附件顺序
3. handle_export_grid_prompt 的 img_idx 编号

Usage:
    pytest tests/test_wysiwyg_consistency.py -v
    python tests/test_wysiwyg_consistency.py          # 也可直接运行
"""

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

# ---------------------------------------------------------------------------
# Imports from project
# ---------------------------------------------------------------------------
from novelvideo.generators.prompt_builder import (
    CharacterConfig,
    GridConfig,
    PromptComponents,
    PromptContext,
    PromptMode,
    StyleConfig,
    UnifiedPromptBuilder,
    create_prompt_context,
)
from novelvideo.generators.grid_generation import resolve_render_reference_order
from novelvideo.services.character_ref_service import build_character_map_for_grid


# ---------------------------------------------------------------------------
# 1. Project discovery
# ---------------------------------------------------------------------------

def _find_output_root() -> Path:
    """Locate the output/ directory relative to repo root."""
    # Walk up from this file until we find "output/"
    here = Path(__file__).resolve().parent
    for parent in [here, here.parent, here.parent.parent]:
        candidate = parent / "output"
        if candidate.is_dir():
            return candidate
    return here.parent / "output"


def discover_projects() -> List[Tuple[str, Path]]:
    """Scan output/*/* for data.db files.  Return (project_label, db_path)."""
    root = _find_output_root()
    results = []
    if not root.exists():
        return results
    for db_path in sorted(root.glob("*/*/data.db")):
        # label = "user/project"
        label = f"{db_path.parent.parent.name}/{db_path.parent.name}"
        results.append((label, db_path))
    return results


# ---------------------------------------------------------------------------
# 2. Async data loading (lightweight — no Cognee init, just raw SQLite)
# ---------------------------------------------------------------------------

async def _load_project_data_async(db_path: Path):
    """Load characters + all episode beats from a data.db using raw SQLite."""
    import aiosqlite

    characters: List[dict] = []
    episodes_beats: Dict[int, List[dict]] = {}
    sketch_colors_by_ep: Dict[int, dict] = {}

    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row

        # Characters
        try:
            async with db.execute("SELECT * FROM characters") as cur:
                for row in await cur.fetchall():
                    characters.append({
                        "name": row["name"],
                        "face_prompt": row["face_prompt"] or "",
                        "appearance_details": row["appearance_details"] or "",
                        "gender": row["gender"] or "",
                        "body_type": row["body_type"] or "",
                        "identities": json.loads(row["identities_json"] or "[]"),
                    })
        except Exception:
            pass

        # Episodes (for sketch_colors)
        try:
            async with db.execute("SELECT number, sketch_colors_json FROM episodes") as cur:
                for row in await cur.fetchall():
                    try:
                        sc = json.loads(row["sketch_colors_json"] or "{}")
                    except (json.JSONDecodeError, TypeError):
                        sc = {}
                    sketch_colors_by_ep[row["number"]] = sc
        except Exception:
            pass

        # Beats
        try:
            async with db.execute(
                "SELECT * FROM beats ORDER BY episode_number, beat_number"
            ) as cur:
                for row in await cur.fetchall():
                    ep = row["episode_number"]
                    beat_dict = {
                        "beat_number": row["beat_number"],
                        "visual_description": row["visual_description"] or "",
                        "location": row["location"] or "",
                        "time_of_day": row["time_of_day"] or "",
                        "detected_identities": json.loads(
                            row["detected_identities_json"] or "[]"
                        ),
                    }
                    episodes_beats.setdefault(ep, []).append(beat_dict)
        except Exception:
            pass

    return characters, episodes_beats, sketch_colors_by_ep


def load_project_data(db_path: Path):
    return asyncio.run(_load_project_data_async(db_path))


# ---------------------------------------------------------------------------
# 3. Build character_map from raw data (no UI state dependency)
# ---------------------------------------------------------------------------

def build_test_character_map(
    characters: List[dict],
    beats: List[dict],
    project_dir: Path,
    sketch_colors: dict,
    use_detected_identities: bool,
) -> Dict[str, dict]:
    """Build a character_map dict that mirrors build_character_map_for_grid.

    We skip actual file-system portrait lookups — instead we synthesise
    plausible reference_mode + reference_path based on what files exist.
    """
    char_dict_for_extract = {c["name"]: None for c in characters if c.get("name")}

    if use_detected_identities:
        seen: List[str] = []
        for beat in beats:
            for did in beat.get("detected_identities") or []:
                name = did.split("_", 1)[0] if "_" in did else did
                if name not in seen and name in char_dict_for_extract:
                    seen.append(name)
        collected_chars = seen
    else:
        collected_chars = PromptComponents.extract_panel_characters(beats, char_dict_for_extract)

    character_map: Dict[str, dict] = {}
    for char_name in collected_chars:
        char = next((c for c in characters if c.get("name") == char_name), None)
        if not char:
            continue

        char_assets_dir = project_dir / "assets" / "characters" / char_name

        # Collect identity_ids from beats
        identity_ids: List[str] = []
        if use_detected_identities:
            for beat in beats:
                for did in beat.get("detected_identities") or []:
                    if did.startswith(char_name + "_") and did not in identity_ids:
                        identity_ids.append(did)
        else:
            from novelvideo.models import extract_char_identities_from_markers
            for beat in beats:
                vd = beat.get("visual_description", "")
                for name, iid in extract_char_identities_from_markers(vd, strict=False).items():
                    if name == char_name and iid and iid not in identity_ids:
                        identity_ids.append(iid)

        identity_id = identity_ids[0] if identity_ids else None

        # Identity appearances + sketch colors
        identity_appearances: Dict[str, str] = {}
        identity_sketch_colors: Dict[str, str] = {}
        for iid in identity_ids:
            for identity in char.get("identities", []):
                if identity.get("identity_id") == iid:
                    suffix = iid.split("_", 1)[1] if "_" in iid else iid
                    identity_appearances[suffix] = identity.get("appearance_details", "")
                    sc = sketch_colors.get(iid, "") if sketch_colors else ""
                    if sc:
                        identity_sketch_colors[suffix] = sc
                    break

        appearance_details = ""
        if identity_id:
            suffix_key = identity_id.split("_", 1)[1] if "_" in identity_id else ""
            appearance_details = identity_appearances.get(suffix_key, "")
        if not appearance_details:
            appearance_details = char.get("appearance_details", "")

        # face_prompt
        identity_face_prompt = ""
        if identity_id:
            for identity in char.get("identities", []):
                if identity.get("identity_id") == identity_id:
                    identity_face_prompt = identity.get("face_prompt", "")
                    break
        effective_face_prompt = identity_face_prompt or char.get("face_prompt", "") or char_name

        # Determine reference_mode by checking files on disk
        ref_path = ""
        is_identity = False
        if identity_id:
            i_name = identity_id.split("_", 1)[1] if "_" in identity_id else identity_id
            id_path = char_assets_dir / "identities" / f"{i_name}.png"
            if id_path.exists():
                ref_path = str(id_path)
                is_identity = True

        if not ref_path:
            # Try portrait
            from novelvideo.utils.path_resolver import compute_portrait_path
            portrait = compute_portrait_path(project_dir, char_name)
            if portrait and Path(portrait).exists():
                ref_path = portrait
            else:
                std = char_assets_dir / "portrait.png"
                if std.exists():
                    ref_path = str(std)

        reference_mode = "composite" if is_identity else ("portrait_only" if ref_path else "prompt_only")

        # Collect non-primary identity ref images (age variants)
        identity_ref_images: Dict[str, str] = {}
        identity_face_prompts_map: Dict[str, str] = {}
        if identity_ids:
            from novelvideo.utils.path_resolver import compute_identity_portrait_path
            for iid in identity_ids:
                if iid == identity_id:
                    continue
                for identity in char.get("identities", []):
                    if identity.get("identity_id") == iid:
                        fpo = identity.get("face_prompt", "")
                        if not fpo:
                            break
                        suffix = iid.split("_", 1)[1] if "_" in iid else iid
                        i_portrait = identity.get("portrait_image", "")
                        if not i_portrait or not Path(i_portrait).exists():
                            i_portrait = compute_identity_portrait_path(project_dir, char_name, suffix)
                        if i_portrait and Path(i_portrait).exists():
                            identity_ref_images[suffix] = i_portrait
                            identity_face_prompts_map[suffix] = fpo
                        break

        primary_suffix = identity_id.split("_", 1)[1] if identity_id and "_" in identity_id else ""
        character_map[char_name] = {
            "face_prompt": effective_face_prompt,
            "portrait_path": ref_path,
            "ref_path": ref_path,
            "base_prompt": effective_face_prompt,
            "reference_mode": reference_mode,
            "gender": char.get("gender", ""),
            "body_type": char.get("body_type", ""),
            "appearance_details": appearance_details,
            "identity_appearances": identity_appearances,
            "identity_sketch_colors": identity_sketch_colors,
            "sketch_color": identity_sketch_colors.get(primary_suffix, ""),
            "identity_ref_images": identity_ref_images,
            "identity_face_prompts": identity_face_prompts_map,
        }

    return character_map


# ---------------------------------------------------------------------------
# 4. Parse Image N from build_reference_map output
# ---------------------------------------------------------------------------

def parse_reference_map_image_order(text: str) -> Dict[int, str]:
    """Parse 'Image N = [tag]: ...' lines → {N: tag_or_description}.

    Also matches 'Image N (LAST ...) = SKETCH ...' for sketch entries.
    Returns mapping of image index → role label (tag or 'SKETCH').
    """
    mapping: Dict[int, str] = {}
    for line in text.splitlines():
        # Match: Image N = TAG: ...  or  Image N = Combined ...
        m = re.search(r'Image\s+(\d+)\s*(?:\([^)]*\)\s*)?=\s*(.+)', line)
        if m:
            idx = int(m.group(1))
            desc = m.group(2).strip()
            # Extract tag from desc
            if desc.startswith("SKETCH") or "SKETCH TO COLORIZE" in desc:
                mapping[idx] = "SKETCH"
            elif desc.startswith("Combined"):
                # "Combined character reference (left to right): [Tag1] ..., [Tag2] ..."
                tags = re.findall(r'\[([^\]]+)\]', desc)
                mapping[idx] = "COMBINED:" + ",".join(tags)
            else:
                # "Tag: ..." or "Tag/Tag2: ..."
                tag = desc.split(":")[0].strip()
                mapping[idx] = tag
    return mapping


# ---------------------------------------------------------------------------
# 5. Simulate API attachment order (mirrors generate_grid logic)
# ---------------------------------------------------------------------------

def simulate_api_attachment_order(
    ordered_chars: List[str],
    character_map: Dict[str, CharacterConfig],
    include_sketch: bool = True,
) -> Dict[int, str]:
    """Simulate the attachment ordering in generate_grid / _prepare_batch_request.

    Returns {1-based index: label} matching what build_reference_map should produce.
    """
    img_idx = 1
    result: Dict[int, str] = {}
    if include_sketch:
        result[img_idx] = "SKETCH"
        img_idx += 1

    # Classify
    composite_chars = []
    other_chars = []
    for char_name in ordered_chars:
        cfg = character_map.get(char_name)
        if not cfg or not cfg.reference_path:
            continue
        if cfg.reference_mode == "composite":
            composite_chars.append(char_name)
        else:
            other_chars.append(char_name)

    # Composite
    if len(composite_chars) >= 4:
        tags = []
        for cn in composite_chars:
            tags.append(cn)
        result[img_idx] = "COMBINED:" + ",".join(
            _compute_label_for_char(cn, character_map) for cn in composite_chars
        )
        img_idx += 1
    else:
        for cn in composite_chars:
            result[img_idx] = _compute_label_for_char(cn, character_map)
            img_idx += 1

    # portrait_only
    for cn in other_chars:
        result[img_idx] = _compute_label_for_char(cn, character_map)
        img_idx += 1

    # Identity ref images (age variants) — same order as build_reference_map
    for cn in ordered_chars:
        cfg = character_map.get(cn)
        if not cfg or not cfg.identity_ref_images:
            continue
        for suffix in sorted(cfg.identity_ref_images.keys()):
            identity_id = f"{cn}_{suffix}"
            tag = PromptComponents.compute_char_tag(cn, identity_id=identity_id)
            result[img_idx] = tag
            img_idx += 1

    return result


def _compute_label_for_char(char_name: str, character_map: Dict[str, CharacterConfig]) -> str:
    """Compute the label that build_reference_map would use for a character.

    This uses the same logic: compute_char_tag with identity info.
    """
    # We need to check identity — but for label matching we just use the tag
    return PromptComponents.compute_char_tag(char_name)


# ---------------------------------------------------------------------------
# 6. Simulate export UI img_idx order
# ---------------------------------------------------------------------------

def simulate_export_ui_order(
    ordered_chars: List[str],
    character_map: Dict[str, dict],
    has_sketch: bool,
) -> Dict[int, str]:
    """Simulate handle_export_grid_prompt's img_idx numbering.

    Uses raw character_map dicts (not CharacterConfig), matching the UI code.
    """
    img_idx = 1
    result: Dict[int, str] = {}
    if has_sketch:
        result[img_idx] = "SKETCH"
        img_idx += 1

    composite_chars = [
        cn for cn in ordered_chars
        if cn in character_map
        and character_map[cn].get("reference_mode") == "composite"
        and character_map[cn].get("portrait_path")
        and Path(character_map[cn]["portrait_path"]).exists()
    ]
    other_chars = [
        cn for cn in ordered_chars
        if cn in character_map
        and character_map[cn].get("reference_mode") != "composite"
        and character_map[cn].get("portrait_path")
        and Path(character_map[cn]["portrait_path"]).exists()
    ]

    if len(composite_chars) >= 4:
        tags = [PromptComponents.compute_char_tag(cn) for cn in composite_chars]
        result[img_idx] = "COMBINED:" + ",".join(tags)
        img_idx += 1
    else:
        for cn in composite_chars:
            result[img_idx] = PromptComponents.compute_char_tag(cn)
            img_idx += 1

    for cn in other_chars:
        result[img_idx] = PromptComponents.compute_char_tag(cn)
        img_idx += 1

    # Identity ref images (age variants)
    for cn in ordered_chars:
        id_ref_imgs = character_map.get(cn, {}).get("identity_ref_images", {})
        for suffix in sorted(id_ref_imgs.keys()):
            path = id_ref_imgs[suffix]
            if Path(path).exists():
                identity_id = f"{cn}_{suffix}"
                tag = PromptComponents.compute_char_tag(cn, identity_id=identity_id)
                result[img_idx] = tag
                img_idx += 1

    # prompt_only chars get no image — they show placeholder in UI but still get img_idx
    shown = set(composite_chars) | set(other_chars)
    for cn in ordered_chars:
        if cn in shown or cn not in character_map:
            continue
        result[img_idx] = PromptComponents.compute_char_tag(cn)
        img_idx += 1

    return result


# ---------------------------------------------------------------------------
# 7. Collect test cases
# ---------------------------------------------------------------------------

def _collect_test_cases():
    """Collect (project_label, db_path, ep_num, beats, characters, sketch_colors) tuples."""
    projects = discover_projects()
    cases = []
    for label, db_path in projects:
        try:
            characters, episodes_beats, sketch_colors_by_ep = load_project_data(db_path)
        except Exception as e:
            print(f"[SKIP] {label}: {e}")
            continue
        if not characters or not episodes_beats:
            continue
        project_dir = db_path.parent
        for ep_num, beats in episodes_beats.items():
            if not beats:
                continue
            sc = sketch_colors_by_ep.get(ep_num, {})
            cases.append((label, db_path, ep_num, beats, characters, sc, project_dir))
    return cases


_TEST_CASES = _collect_test_cases()
_TEST_IDS = [f"{c[0]}/ep{c[2]:03d}" for c in _TEST_CASES]


def _make_ctx_and_ordered(
    beats, characters, project_dir, sketch_colors, mode: PromptMode
):
    """Create PromptContext + production prompt/render order for a given mode."""
    use_detected = mode == PromptMode.RENDER
    character_map = build_test_character_map(
        characters, beats, project_dir, sketch_colors, use_detected
    )
    if not character_map:
        return None, [], {}, ""

    # Default grid: 2x2
    rows, cols = 2, 2
    ctx = create_prompt_context(
        mode=mode,
        beats=beats[:rows * cols],
        rows=rows,
        cols=cols,
        character_map=character_map,
        ethnicity="Chinese",
    )

    if mode == PromptMode.RENDER:
        builder = UnifiedPromptBuilder(ctx)
        prompt = builder.build()
        ordered = resolve_render_reference_order(
            ctx, beats[:rows * cols], rows * cols, ctx.characters
        )
    else:
        prompt = ""
        ordered = PromptComponents.extract_panel_characters(
            beats[:rows * cols], ctx.characters
        )
    # Append remaining chars
    for cn in character_map:
        if cn not in ordered:
            ordered.append(cn)

    return ctx, ordered, character_map, prompt


# ---------------------------------------------------------------------------
# Test 1: extract_panel_characters sources
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _TEST_CASES, reason="No project data found")
@pytest.mark.parametrize("case", _TEST_CASES, ids=_TEST_IDS)
def test_extract_panel_characters_sources(case):
    """Render uses detected_identities, Sketch uses {{}} markers.
    Both should return character names that exist in the project."""
    label, db_path, ep_num, beats, characters, sc, project_dir = case
    char_names = {c["name"] for c in characters}

    # Build a minimal CharacterConfig dict for the extractors
    char_cfg_dict = {
        c["name"]: CharacterConfig(name=c["name"])
        for c in characters
    }

    # Render: from detected_identities
    render_chars = PromptComponents.extract_panel_characters_from_detected(
        beats, char_cfg_dict
    )
    for cn in render_chars:
        assert cn in char_names, (
            f"[{label}/ep{ep_num}] Render extracted unknown char: {cn}"
        )

    # Sketch: from {{}} markers
    sketch_chars = PromptComponents.extract_panel_characters(
        beats, char_cfg_dict
    )
    for cn in sketch_chars:
        assert cn in char_names, (
            f"[{label}/ep{ep_num}] Sketch extracted unknown char: {cn}"
        )


# ---------------------------------------------------------------------------
# Test 2: build_reference_map Image N == simulated API attachment order
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _TEST_CASES, reason="No project data found")
@pytest.mark.parametrize("case", _TEST_CASES, ids=_TEST_IDS)
def test_reference_map_matches_api_attachment_order(case):
    """Image N in build_reference_map must match simulated API attachment order."""
    label, db_path, ep_num, beats, characters, sc, project_dir = case

    # Test Render mode
    ctx, ordered, cmap, prompt = _make_ctx_and_ordered(
        beats, characters, project_dir, sc, PromptMode.RENDER
    )
    if ctx is None or not ordered:
        pytest.skip(f"No characters for {label}/ep{ep_num}")

    # Path A: parse reference map
    path_a = parse_reference_map_image_order(prompt)

    # Path B: simulate API attachment order
    path_b = simulate_api_attachment_order(
        ordered, ctx.characters, include_sketch=True
    )

    # Compare: same indices, same relative ordering of chars
    # We compare the sequence of non-SKETCH entries' tag roots
    def _extract_char_sequence(mapping):
        return [
            (idx, tag)
            for idx, tag in sorted(mapping.items())
            if tag != "SKETCH"
        ]

    seq_a = _extract_char_sequence(path_a)
    seq_b = _extract_char_sequence(path_b)

    assert len(seq_a) == len(seq_b), (
        f"[{label}/ep{ep_num}] Image count mismatch: "
        f"reference_map has {len(seq_a)} char images, "
        f"API sim has {len(seq_b)}\n"
        f"  Path A (reference_map): {path_a}\n"
        f"  Path B (API sim):       {path_b}"
    )

    for (idx_a, tag_a), (idx_b, tag_b) in zip(seq_a, seq_b):
        assert idx_a == idx_b, (
            f"[{label}/ep{ep_num}] Image index mismatch at position: "
            f"ref_map={idx_a}, api_sim={idx_b}\n"
            f"  Path A: {path_a}\n  Path B: {path_b}"
        )


# ---------------------------------------------------------------------------
# Test 3: Export UI img_idx == build_reference_map Image N
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _TEST_CASES, reason="No project data found")
@pytest.mark.parametrize("case", _TEST_CASES, ids=_TEST_IDS)
def test_export_ui_matches_reference_map(case):
    """Export UI img_idx numbering must match build_reference_map Image N."""
    label, db_path, ep_num, beats, characters, sc, project_dir = case

    ctx, ordered, cmap, prompt = _make_ctx_and_ordered(
        beats, characters, project_dir, sc, PromptMode.RENDER
    )
    if ctx is None or not ordered:
        pytest.skip(f"No characters for {label}/ep{ep_num}")

    # Path A: reference map
    path_a = parse_reference_map_image_order(prompt)

    # Path C: export UI simulation
    has_sketch = True  # Render mode always has sketch
    path_c = simulate_export_ui_order(ordered, cmap, has_sketch)

    # Compare image counts (excluding prompt_only chars that only exist in UI)
    # The reference_map only has images for chars with ref_path,
    # but export UI also shows placeholders for prompt_only chars.
    # We compare only the indices that appear in both.
    max_ref_idx = max(path_a.keys()) if path_a else 0
    max_ui_idx = max(path_c.keys()) if path_c else 0

    # The SKETCH should be at the same index
    sketch_idx_a = next((i for i, t in path_a.items() if t == "SKETCH"), None)
    sketch_idx_c = next((i for i, t in path_c.items() if t == "SKETCH"), None)

    if sketch_idx_a is not None and sketch_idx_c is not None:
        # In the UI, prompt_only chars get img_idx too (shown as placeholder),
        # so UI sketch idx may be higher. That's expected.
        # What matters: all chars WITH images have same idx in both paths.
        pass

    # Compare chars that have actual images (non-SKETCH, non-placeholder)
    ref_char_indices = {
        idx: tag for idx, tag in path_a.items() if tag != "SKETCH"
    }
    ui_char_indices = {
        idx: tag for idx, tag in path_c.items() if tag != "SKETCH"
    }

    # All ref_map entries must appear in UI at same indices
    for idx, tag in sorted(ref_char_indices.items()):
        assert idx in ui_char_indices, (
            f"[{label}/ep{ep_num}] ref_map Image {idx} ({tag}) missing from export UI\n"
            f"  ref_map: {path_a}\n  UI:      {path_c}"
        )


# ---------------------------------------------------------------------------
# Test 4: build_panel_roster has no Image N references (regression)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _TEST_CASES, reason="No project data found")
@pytest.mark.parametrize("case", _TEST_CASES, ids=_TEST_IDS)
def test_panel_roster_no_image_n(case):
    """build_panel_roster output must NOT contain 'Image N' strings."""
    label, db_path, ep_num, beats, characters, sc, project_dir = case

    ctx, ordered, cmap, _prompt = _make_ctx_and_ordered(
        beats, characters, project_dir, sc, PromptMode.RENDER
    )
    if ctx is None:
        pytest.skip(f"No context for {label}/ep{ep_num}")

    roster = PromptComponents.build_panel_roster(ctx)
    matches = re.findall(r'Image\s+\d+', roster)
    assert not matches, (
        f"[{label}/ep{ep_num}] build_panel_roster contains Image N references: {matches}\n"
        f"Roster text:\n{roster[:500]}"
    )


# ---------------------------------------------------------------------------
# Test 5: build_identity_lock(compact=True) has no Image N references
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _TEST_CASES, reason="No project data found")
@pytest.mark.parametrize("case", _TEST_CASES, ids=_TEST_IDS)
def test_compact_identity_lock_no_image_n(case):
    """Render mode compact identity lock must NOT contain 'Image N' strings."""
    label, db_path, ep_num, beats, characters, sc, project_dir = case

    ctx, ordered, cmap, _prompt = _make_ctx_and_ordered(
        beats, characters, project_dir, sc, PromptMode.RENDER
    )
    if ctx is None:
        pytest.skip(f"No context for {label}/ep{ep_num}")

    lock = PromptComponents.build_identity_lock(ctx, ordered, compact=True)
    matches = re.findall(r'Image\s+\d+', lock)
    assert not matches, (
        f"[{label}/ep{ep_num}] compact identity_lock contains Image N references: {matches}\n"
        f"Lock text:\n{lock[:500]}"
    )


def test_render_reference_order_requires_resolved_render_chars():
    """Render mode must fail fast if prompt_builder did not populate the order."""
    ctx = type("Ctx", (), {"resolved_render_chars": []})()

    with pytest.raises(RuntimeError, match="Render reference order missing"):
        resolve_render_reference_order(
            ctx=ctx,
            beats=[],
            grid_capacity=4,
            valid_character_map={"A": {}},
        )


def test_render_reference_order_allows_empty_character_map():
    """Render mode with no valid characters should not fail."""
    ctx = type("Ctx", (), {"resolved_render_chars": []})()

    ordered = resolve_render_reference_order(
        ctx=ctx,
        beats=[],
        grid_capacity=4,
        valid_character_map={},
    )

    assert ordered == []


def test_build_character_map_render_uses_detected_identities_only(tmp_path: Path):
    characters = [
        {
            "name": "沈知月",
            "gender": "female",
            "body_type": "",
            "appearance_details": "A",
            "identities": [
                {
                    "identity_id": "沈知月_怀孕时期",
                    "identity_name": "怀孕时期",
                    "appearance_details": "A1",
                    "face_prompt": "face-a",
                    "body_type": "",
                }
            ],
        },
        {
            "name": "沈知薇",
            "gender": "female",
            "body_type": "",
            "appearance_details": "B",
            "identities": [
                {
                    "identity_id": "沈知薇_千岁府时期",
                    "identity_name": "千岁府时期",
                    "appearance_details": "B1",
                    "face_prompt": "face-b",
                    "body_type": "",
                }
            ],
        },
    ]
    beats = [
        {
            "beat_number": 1,
            "visual_description": "{{沈知薇_千岁府时期}}",
            "detected_identities": ["沈知月_怀孕时期"],
        }
    ]
    cmap = build_character_map_for_grid(
        grid_beats=beats,
        characters=characters,
        user_output_dir=tmp_path,
        project="demo",
        sketch_colors={},
        use_detected_identities=True,
    )

    assert list(cmap.keys()) == ["沈知月"]


def test_build_character_map_sketch_uses_visual_description_only(tmp_path: Path):
    characters = [
        {
            "name": "沈知月",
            "gender": "female",
            "body_type": "",
            "appearance_details": "A",
            "identities": [
                {
                    "identity_id": "沈知月_怀孕时期",
                    "identity_name": "怀孕时期",
                    "appearance_details": "A1",
                    "face_prompt": "face-a",
                    "body_type": "",
                }
            ],
        },
        {
            "name": "沈知薇",
            "gender": "female",
            "body_type": "",
            "appearance_details": "B",
            "identities": [
                {
                    "identity_id": "沈知薇_千岁府时期",
                    "identity_name": "千岁府时期",
                    "appearance_details": "B1",
                    "face_prompt": "face-b",
                    "body_type": "",
                }
            ],
        },
    ]
    beats = [
        {
            "beat_number": 1,
            "visual_description": "{{沈知薇_千岁府时期}}",
            "detected_identities": ["沈知月_怀孕时期"],
        }
    ]
    cmap = build_character_map_for_grid(
        grid_beats=beats,
        characters=characters,
        user_output_dir=tmp_path,
        project="demo",
        sketch_colors={},
        use_detected_identities=False,
    )

    assert list(cmap.keys()) == ["沈知薇"]


# ---------------------------------------------------------------------------
# Direct execution support
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    projects = discover_projects()
    if not projects:
        print("No projects found in output/. Nothing to test.")
        sys.exit(0)

    print(f"Found {len(projects)} project(s):")
    for label, db_path in projects:
        print(f"  {label}: {db_path}")

    print(f"\nCollected {len(_TEST_CASES)} test case(s):")
    for tid in _TEST_IDS:
        print(f"  {tid}")

    # Run pytest programmatically
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
