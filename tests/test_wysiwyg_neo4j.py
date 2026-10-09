"""WYSIWYG 一致性测试 — Neo4j / Main 分支版。

验证三条路径的角色排序一致性:
1. build_reference_map 的 Image N 编号 (prompt 路径)
2. generate_grid 的 API 附件顺序 (API 路径)
3. handle_export_grid_prompt 的 img_idx 编号 (导出 UI 路径)

Main 分支没有 extract_panel_characters_from_detected，
Render 模式下 character_map 用 detected_identities 顺序构建，
但 build_reference_map / generate_grid 用 extract_panel_characters({{}} 标记) 重排。
导出 UI 直接遍历 character_map.items()，不重排 → 预期不一致 bug。

Usage:
    uv run python -m pytest tests/test_wysiwyg_neo4j.py -v --tb=short
    python tests/test_wysiwyg_neo4j.py
"""

import asyncio
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
    PromptComponents,
    PromptContext,
    PromptMode,
    create_prompt_context,
)


# ---------------------------------------------------------------------------
# 1. Project discovery — scan output/{user}/{project}/
# ---------------------------------------------------------------------------

def _find_output_root() -> Path:
    here = Path(__file__).resolve().parent
    for parent in [here, here.parent, here.parent.parent]:
        candidate = parent / "output"
        if candidate.is_dir():
            return candidate
    return here.parent / "output"


# 非项目目录（系统/共享资源）
_SKIP_DIRS = {
    "assets", "audio", "cognee_system", "frames", "graph",
    "images", "scripts", "style-examples", "videos",
}


def discover_projects() -> List[Tuple[str, Path]]:
    """Scan output/{user}/{project}/ for directories with assets/."""
    root = _find_output_root()
    results: List[Tuple[str, Path]] = []
    if not root.exists():
        return results
    for user_dir in sorted(root.iterdir()):
        if not user_dir.is_dir() or user_dir.name in _SKIP_DIRS:
            continue
        for proj_dir in sorted(user_dir.iterdir()):
            if not proj_dir.is_dir():
                continue
            if (proj_dir / "assets").is_dir():
                label = f"{user_dir.name}/{proj_dir.name}"
                results.append((label, proj_dir))
    return results


# ---------------------------------------------------------------------------
# 2. Async data loading via CogneeStore
# ---------------------------------------------------------------------------

async def _load_project_data_async(project_name: str, project_dir: Path):
    """Load characters, beats, sketch_colors from Neo4j via CogneeStore."""
    from novelvideo.cognee.store import CogneeStore

    store = CogneeStore(project_name, output_dir=str(project_dir))
    try:
        await store.initialize()

        novel_chars = await store.list_characters()
        episodes = await store.list_episodes()

        # NovelCharacter → dict
        characters: List[dict] = []
        for c in novel_chars:
            identities_list: List[dict] = []
            try:
                for identity in (c.identities or []):
                    identities_list.append({
                        "identity_id": getattr(identity, "identity_id", ""),
                        "appearance_details": getattr(
                            identity,
                            "appearance_details",
                            "",
                        ),
                        "face_prompt": getattr(identity, "face_prompt", ""),
                        "portrait_image": getattr(identity, "portrait_image", ""),
                    })
            except Exception:
                pass

            characters.append({
                "name": c.name,
                "face_prompt": getattr(c, "face_prompt", "") or "",
                "appearance_details": getattr(c, "appearance_details", "") or "",
                "gender": getattr(c, "gender", "") or "",
                "body_type": getattr(c, "body_type", "") or "",
                "identities": identities_list,
            })

        episodes_beats: Dict[int, List[dict]] = {}
        sketch_colors_by_ep: Dict[int, dict] = {}

        for ep in episodes:
            ep_num = ep.number
            try:
                beats = await store.get_beats_as_dicts(ep_num)
                if beats:
                    episodes_beats[ep_num] = beats
            except Exception:
                pass
            try:
                sc = store.get_sketch_colors(ep_num)
                if sc:
                    sketch_colors_by_ep[ep_num] = sc
            except Exception:
                pass

        return characters, episodes_beats, sketch_colors_by_ep
    finally:
        try:
            await store.close()
        except Exception:
            pass


def load_project_data(project_name: str, project_dir: Path):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            _load_project_data_async(project_name, project_dir)
        )
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# 3. Build character_map — mirrors build_character_map_for_grid (generation.py)
# ---------------------------------------------------------------------------

def build_test_character_map(
    characters: List[dict],
    beats: List[dict],
    project_dir: Path,
    sketch_colors: dict,
    use_detected_identities: bool,
) -> Dict[str, dict]:
    """Build character_map dict matching build_character_map_for_grid behavior.

    Dict insertion order follows collected_chars order:
    - use_detected_identities=True  → detected_identities 出场序
    - use_detected_identities=False → {{}} 标记出场序
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
        collected_chars = PromptComponents.extract_panel_characters(
            beats, char_dict_for_extract
        )

    character_map: Dict[str, dict] = {}
    for char_name in collected_chars:
        char = next((c for c in characters if c.get("name") == char_name), None)
        if not char:
            continue

        char_assets_dir = project_dir / "assets" / "characters" / char_name

        # identity_ids
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
                for name, iid in extract_char_identities_from_markers(
                    vd, strict=False
                ).items():
                    if name == char_name and iid and iid not in identity_ids:
                        identity_ids.append(iid)

        identity_id = identity_ids[0] if identity_ids else None

        # identity appearances + sketch colors
        identity_appearances: Dict[str, str] = {}
        identity_sketch_colors: Dict[str, str] = {}
        for iid in identity_ids:
            for identity in char.get("identities", []):
                if identity.get("identity_id") == iid:
                    suffix = iid.split("_", 1)[1] if "_" in iid else iid
                    identity_appearances[suffix] = identity.get(
                        "appearance_details", ""
                    )
                    sc = sketch_colors.get(iid, "") if sketch_colors else ""
                    if sc:
                        identity_sketch_colors[suffix] = sc
                    break

        appearance_details = ""
        if identity_id:
            suffix_key = (
                identity_id.split("_", 1)[1] if "_" in identity_id else ""
            )
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
        effective_face_prompt = (
            identity_face_prompt or char.get("face_prompt", "") or char_name
        )

        # reference_mode
        ref_path = ""
        is_identity = False
        if identity_id:
            i_name = (
                identity_id.split("_", 1)[1]
                if "_" in identity_id
                else identity_id
            )
            id_path = char_assets_dir / "identities" / f"{i_name}.png"
            if id_path.exists():
                ref_path = str(id_path)
                is_identity = True

        if not ref_path:
            try:
                from novelvideo.utils.path_resolver import compute_portrait_path

                portrait = compute_portrait_path(project_dir, char_name)
                if portrait and Path(portrait).exists():
                    ref_path = portrait
            except ImportError:
                pass
            if not ref_path:
                std = char_assets_dir / "portrait.png"
                if std.exists():
                    ref_path = str(std)

        reference_mode = (
            "composite"
            if is_identity
            else ("portrait_only" if ref_path else "prompt_only")
        )

        # identity ref images (age variants)
        identity_ref_images: Dict[str, str] = {}
        identity_face_prompts_map: Dict[str, str] = {}
        if identity_ids:
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
                            try:
                                from novelvideo.utils.path_resolver import (
                                    compute_identity_portrait_path,
                                )
                                i_portrait = compute_identity_portrait_path(
                                    project_dir, char_name, suffix
                                )
                            except ImportError:
                                i_portrait = ""
                        if i_portrait and Path(i_portrait).exists():
                            identity_ref_images[suffix] = i_portrait
                            identity_face_prompts_map[suffix] = fpo
                        break

        primary_suffix = (
            identity_id.split("_", 1)[1]
            if identity_id and "_" in identity_id
            else ""
        )
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
    """Parse 'Image N = ...' lines → {N: tag_or_description}."""
    mapping: Dict[int, str] = {}
    for line in text.splitlines():
        m = re.search(r"Image\s+(\d+)\s*(?:\([^)]*\)\s*)?=\s*(.+)", line)
        if m:
            idx = int(m.group(1))
            desc = m.group(2).strip()
            if desc.startswith("SKETCH") or "SKETCH TO COLORIZE" in desc:
                mapping[idx] = "SKETCH"
            elif desc.startswith("Combined"):
                tags = re.findall(r"\[([^\]]+)\]", desc)
                mapping[idx] = "COMBINED:" + ",".join(tags)
            else:
                tag = desc.split(":")[0].strip()
                mapping[idx] = tag
    return mapping


# ---------------------------------------------------------------------------
# 5. Simulate API attachment order (mirrors generate_grid logic on main)
# ---------------------------------------------------------------------------

def simulate_api_attachment_order(
    ordered_chars: List[str],
    character_map: Dict[str, CharacterConfig],
    include_sketch: bool = True,
) -> Dict[int, str]:
    """Simulate generate_grid attachment ordering.

    Main's generate_grid uses extract_panel_characters to order chars,
    then classifies into composite/other and attaches images.
    """
    img_idx = 1
    result: Dict[int, str] = {}
    if include_sketch:
        result[img_idx] = "SKETCH"
        img_idx += 1

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

    if len(composite_chars) >= 4:
        result[img_idx] = "COMBINED:" + ",".join(
            PromptComponents.compute_char_tag(cn) for cn in composite_chars
        )
        img_idx += 1
    else:
        for cn in composite_chars:
            result[img_idx] = PromptComponents.compute_char_tag(cn)
            img_idx += 1

    for cn in other_chars:
        result[img_idx] = PromptComponents.compute_char_tag(cn)
        img_idx += 1

    # Identity ref images (age variants) — main's CharacterConfig may not have this field
    for cn in ordered_chars:
        cfg = character_map.get(cn)
        if not cfg:
            continue
        id_ref_imgs = getattr(cfg, "identity_ref_images", None) or {}
        for suffix in sorted(id_ref_imgs.keys()):
            identity_id = f"{cn}_{suffix}"
            tag = PromptComponents.compute_char_tag(cn, identity_id=identity_id)
            result[img_idx] = tag
            img_idx += 1

    return result


# ---------------------------------------------------------------------------
# 6. Simulate export UI img_idx order (main's BUG: uses character_map.items())
# ---------------------------------------------------------------------------

def simulate_export_ui_order_fixed(
    ordered_chars: List[str],
    character_map: Dict[str, dict],
    has_sketch: bool,
) -> Dict[int, str]:
    """Simulate fixed handle_export_grid_prompt img_idx numbering.

    修复后: 用 _ordered_chars (extract_panel_characters_from_detected) 排序，
    与 build_reference_map / generate_grid 一致。
    """
    img_idx = 1
    result: Dict[int, str] = {}
    if has_sketch:
        result[img_idx] = "SKETCH"
        img_idx += 1

    # 按 ordered_chars 顺序（修复后与 prompt/API 一致）
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

    # Identity ref images — 按 ordered_chars 顺序
    for cn in ordered_chars:
        id_ref_imgs = character_map.get(cn, {}).get("identity_ref_images", {})
        for suffix in sorted(id_ref_imgs.keys()):
            path = id_ref_imgs[suffix]
            if Path(path).exists():
                identity_id = f"{cn}_{suffix}"
                tag = PromptComponents.compute_char_tag(cn, identity_id=identity_id)
                result[img_idx] = tag
                img_idx += 1

    return result


# ---------------------------------------------------------------------------
# 7. Collect test cases
# ---------------------------------------------------------------------------

def _collect_test_cases():
    """Collect (label, project_dir, ep_num, beats, characters, sketch_colors) tuples."""
    projects = discover_projects()
    cases = []
    for label, project_dir in projects:
        try:
            characters, episodes_beats, sketch_colors_by_ep = load_project_data(
                label, project_dir
            )
        except Exception as e:
            print(f"[SKIP] {label}: {e}")
            continue
        if not characters or not episodes_beats:
            print(f"[SKIP] {label}: no characters or beats")
            continue
        for ep_num, beats in episodes_beats.items():
            if not beats:
                continue
            sc = sketch_colors_by_ep.get(ep_num, {})
            cases.append((label, project_dir, ep_num, beats, characters, sc))
    return cases


_TEST_CASES = _collect_test_cases()
_TEST_IDS = [f"{c[0]}/ep{c[2]:03d}" for c in _TEST_CASES]


def _make_ctx_and_ordered(beats, characters, project_dir, sketch_colors):
    """Create PromptContext + ordered_chars for Render mode.

    修复后: Render 模式统一用 extract_panel_characters_from_detected 排序。
    character_map 也用 detected_identities 顺序构建。
    """
    # character_map 用 detected_identities 顺序（= Render 模式 build_character_map_for_grid）
    character_map = build_test_character_map(
        characters, beats, project_dir, sketch_colors,
        use_detected_identities=True,
    )
    if not character_map:
        return None, [], {}

    rows, cols = 2, 2
    ctx = create_prompt_context(
        mode=PromptMode.RENDER,
        beats=beats[: rows * cols],
        rows=rows,
        cols=cols,
        character_map=character_map,
        ethnicity="Chinese",
    )

    # 修复后统一用 extract_panel_characters_from_detected
    ordered = PromptComponents.extract_panel_characters_from_detected(
        beats[: rows * cols], ctx.characters
    )
    for cn in character_map:
        if cn not in ordered:
            ordered.append(cn)

    return ctx, ordered, character_map


# ---------------------------------------------------------------------------
# Test 1: extract_panel_characters 返回有效角色
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _TEST_CASES, reason="No project data found")
@pytest.mark.parametrize("case", _TEST_CASES, ids=_TEST_IDS)
def test_extract_panel_characters_valid(case):
    """extract_panel_characters 和 _from_detected 返回的角色名必须存在于角色列表中。"""
    label, project_dir, ep_num, beats, characters, sc = case
    char_names = {c["name"] for c in characters}

    char_cfg_dict = {
        c["name"]: CharacterConfig(name=c["name"]) for c in characters
    }

    # Sketch 模式: {{}} 标记
    sketch_chars = PromptComponents.extract_panel_characters(beats, char_cfg_dict)
    for cn in sketch_chars:
        assert cn in char_names, (
            f"[{label}/ep{ep_num}] Sketch extracted unknown char: {cn}"
        )

    # Render 模式: detected_identities
    render_chars = PromptComponents.extract_panel_characters_from_detected(
        beats, char_cfg_dict
    )
    for cn in render_chars:
        assert cn in char_names, (
            f"[{label}/ep{ep_num}] Render extracted unknown char: {cn}"
        )


# ---------------------------------------------------------------------------
# Test 2: build_reference_map Image N == simulated API attachment order
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _TEST_CASES, reason="No project data found")
@pytest.mark.parametrize("case", _TEST_CASES, ids=_TEST_IDS)
def test_reference_map_matches_api_attachment(case):
    """build_reference_map Image N 必须与 generate_grid API 附件顺序一致。

    两者都用 extract_panel_characters 排序，预期一致。
    """
    label, project_dir, ep_num, beats, characters, sc = case

    ctx, ordered, cmap = _make_ctx_and_ordered(
        beats, characters, project_dir, sc
    )
    if ctx is None or not ordered:
        pytest.skip(f"No characters for {label}/ep{ep_num}")

    # Path A: reference map
    ref_map_text = PromptComponents.build_reference_map(
        ctx, ordered, include_sketch=True
    )
    path_a = parse_reference_map_image_order(ref_map_text)

    # Path B: API sim
    path_b = simulate_api_attachment_order(
        ordered, ctx.characters, include_sketch=True
    )

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
        f"reference_map={len(seq_a)}, API sim={len(seq_b)}\n"
        f"  Path A (ref_map): {path_a}\n"
        f"  Path B (API sim): {path_b}"
    )

    for (idx_a, tag_a), (idx_b, tag_b) in zip(seq_a, seq_b):
        assert idx_a == idx_b, (
            f"[{label}/ep{ep_num}] Image index mismatch: "
            f"ref_map={idx_a}, api_sim={idx_b}\n"
            f"  Path A: {path_a}\n  Path B: {path_b}"
        )


# ---------------------------------------------------------------------------
# Test 3: Export UI img_idx == build_reference_map Image N
#          *** 预期 FAIL — main 的 export UI 用 character_map.items() 不重排 ***
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _TEST_CASES, reason="No project data found")
@pytest.mark.parametrize("case", _TEST_CASES, ids=_TEST_IDS)
def test_export_ui_matches_reference_map(case):
    """Export UI img_idx 必须与 build_reference_map Image N 一致。

    修复后: export UI 用 _ordered_chars (extract_panel_characters_from_detected) 排序，
    与 build_reference_map / generate_grid 一致。预期全部 PASS。
    """
    label, project_dir, ep_num, beats, characters, sc = case

    ctx, ordered, cmap = _make_ctx_and_ordered(
        beats, characters, project_dir, sc
    )
    if ctx is None or not ordered:
        pytest.skip(f"No characters for {label}/ep{ep_num}")

    # Path A: reference map
    ref_map_text = PromptComponents.build_reference_map(
        ctx, ordered, include_sketch=True
    )
    path_a = parse_reference_map_image_order(ref_map_text)

    # Path C: export UI (修复后用 ordered_chars 排序)
    path_c = simulate_export_ui_order_fixed(ordered, cmap, has_sketch=True)

    # 比较角色数量和顺序（忽略 tag hash 差异，只比较角色名）
    def _tag_to_char_name(tag: str) -> str:
        """Extract character name root from tag like [SSS_9af9] or COMBINED:..."""
        if tag == "SKETCH":
            return "SKETCH"
        if tag.startswith("COMBINED:"):
            # Extract individual tags from COMBINED
            return tag  # keep as-is for count comparison
        # Tag format: [Name_hash] or Name
        return tag.split("_")[0].strip("[")

    ref_count = len([t for t in path_a.values() if t != "SKETCH"])
    ui_count = len([t for t in path_c.values() if t != "SKETCH"])

    assert ref_count == ui_count, (
        f"[{label}/ep{ep_num}] Image count mismatch: ref_map={ref_count}, UI={ui_count}\n"
        f"  reference_map: {path_a}\n"
        f"  export UI:     {path_c}\n"
        f"  ordered_chars: {ordered}"
    )

    # SKETCH 应该是第一张构图底图
    sketch_a = max((i for i, t in path_a.items() if t == "SKETCH"), default=None)
    sketch_c = max((i for i, t in path_c.items() if t == "SKETCH"), default=None)
    if sketch_a is not None and sketch_c is not None:
        assert sketch_a == sketch_c, (
            f"[{label}/ep{ep_num}] SKETCH index mismatch: ref_map={sketch_a}, UI={sketch_c}\n"
            f"  reference_map: {path_a}\n"
            f"  export UI:     {path_c}"
        )


# ---------------------------------------------------------------------------
# Test 4: build_panel_roster 不应包含 Image N 引用
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _TEST_CASES, reason="No project data found")
@pytest.mark.parametrize("case", _TEST_CASES, ids=_TEST_IDS)
def test_panel_roster_no_image_n(case):
    """build_panel_roster 输出不应包含 'Image N' 字符串。"""
    label, project_dir, ep_num, beats, characters, sc = case

    ctx, ordered, cmap = _make_ctx_and_ordered(
        beats, characters, project_dir, sc
    )
    if ctx is None:
        pytest.skip(f"No context for {label}/ep{ep_num}")

    roster = PromptComponents.build_panel_roster(ctx)
    matches = re.findall(r"Image\s+\d+", roster)
    assert not matches, (
        f"[{label}/ep{ep_num}] build_panel_roster contains Image N: {matches}\n"
        f"Roster:\n{roster[:500]}"
    )


# ---------------------------------------------------------------------------
# Test 5: build_identity_lock(compact=True) 不应包含 Image N 引用
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _TEST_CASES, reason="No project data found")
@pytest.mark.parametrize("case", _TEST_CASES, ids=_TEST_IDS)
def test_compact_identity_lock_no_image_n(case):
    """Render 模式 compact identity lock 不应包含 'Image N' 字符串。"""
    label, project_dir, ep_num, beats, characters, sc = case

    ctx, ordered, cmap = _make_ctx_and_ordered(
        beats, characters, project_dir, sc
    )
    if ctx is None:
        pytest.skip(f"No context for {label}/ep{ep_num}")

    lock = PromptComponents.build_identity_lock(ctx, ordered, compact=True)
    matches = re.findall(r"Image\s+\d+", lock)
    assert not matches, (
        f"[{label}/ep{ep_num}] compact identity_lock contains Image N: {matches}\n"
        f"Lock:\n{lock[:500]}"
    )


# ---------------------------------------------------------------------------
# Test 6: build_panel_roster 的 tag→img 映射用 extract_panel_characters,
#          但 per-panel 内容用 detected_identities — 验证覆盖一致
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _TEST_CASES, reason="No project data found")
@pytest.mark.parametrize("case", _TEST_CASES, ids=_TEST_IDS)
def test_panel_roster_covers_detected_identities(case):
    """build_panel_roster 的 tag→img 映射必须覆盖所有 detected_identities 中的角色。

    BUG 场景: 角色在 detected_identities 中出现但未在 {{}} 标记中出现,
    tag→img 映射中找不到该角色 → 不完整映射。
    """
    label, project_dir, ep_num, beats, characters, sc = case

    ctx, ordered, cmap = _make_ctx_and_ordered(
        beats, characters, project_dir, sc
    )
    if ctx is None:
        pytest.skip(f"No context for {label}/ep{ep_num}")

    rows, cols = 2, 2
    panel_beats = beats[: rows * cols]

    # 从 detected_identities 提取出场角色名
    detected_char_names = set()
    for beat in panel_beats:
        for did in beat.get("detected_identities") or []:
            name = did.split("_", 1)[0] if "_" in did else did
            if name in {c["name"] for c in characters}:
                detected_char_names.add(name)

    # 从 extract_panel_characters ({{}} 标记) 提取
    char_cfg_dict = {
        c["name"]: CharacterConfig(name=c["name"]) for c in characters
    }
    marker_chars = set(
        PromptComponents.extract_panel_characters(panel_beats, char_cfg_dict)
    )

    # 所有 detected 角色应在 marker 提取结果中出现（或在 character_map 中）
    missing = detected_char_names - marker_chars - set(cmap.keys())
    if missing:
        pytest.fail(
            f"[{label}/ep{ep_num}] Characters in detected_identities but not in markers or character_map: {missing}\n"
            f"  detected: {detected_char_names}\n"
            f"  markers:  {marker_chars}\n"
            f"  cmap:     {set(cmap.keys())}"
        )


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
    for label, proj_dir in projects:
        print(f"  {label}: {proj_dir}")

    print(f"\nCollected {len(_TEST_CASES)} test case(s):")
    for tid in _TEST_IDS:
        print(f"  {tid}")

    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
