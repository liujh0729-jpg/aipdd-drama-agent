"""Sketch failure-mode registry — phase 2 with DB split.

Storage boundary (see `plans/frolicking-hopping-karp.md`):
- **Definitions** live in the user-shared `verification.db` under the
  `sketch_failure_mode_defs` table. This is the single source of truth
  for `registry_version` computation and the sole feed for negative
  prompt clauses.
- **Per-project hits** live in the project's own `data.db` under
  `sketch_failure_mode_hits`. Each project tracks its own usage stats;
  hits are not shared across projects.
- **Legacy fallback**: old project DBs still have the phase-1
  `sketch_failure_modes` table (mixed def + hit columns). The one-shot
  `seed_mirror_once` CLI copies those definitions into `verification.db`.
  Until then, `load_negative_clause_for_project` can optionally merge
  legacy rows for runtime compatibility — but those merged rows do NOT
  contribute to `registry_version` (that only hashes canonical defs).

Primary API (all async, all take the `verification.db` connection):
- `ensure_seeded(defs_db)` — UPSERT the seed list
- `list_active(defs_db, ...)` — select active defs
- `get_by_code(defs_db, code)` — single def lookup
- `upsert(defs_db, code, **fields)` — add / edit a def
- `build_negative_prompt_clause(defs_db, layer)` — bullets for prompt injection

Project hits:
- `bump_hit(project_db, code, episode)` — writes `sketch_failure_mode_hits`

Facade:
- `load_negative_clause_for_project(project_dir, layer)` — resolves the
  shared verification.db, seeds if needed, returns the clause. Safe to
  call from sync-heavy pipeline code (`grid_generation`,
  `sketch_edit_execute`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite

from novelvideo.verification.global_registry_db import (
    ensure_defs_seeded,
    open_defs_db,
)


_SEED_FAILURE_MODES: list[dict[str, Any]] = [
    {
        "code": "dialogue_text_overlay",
        "layer": "generator",
        "gate_enabled": 1,
        "detection": (
            "Is there any English dialogue bubble, a 'DIALOGUE' label, a "
            "speaker code such as MGNQN_xxxx, or other prompt-leak English "
            "text rendered inside the storyboard panel?"
        ),
        "prevention_rule": (
            "Sketch 生成 prompt 不得把 audio_type / speaker 等字段名当成画面内容；"
            "dialogue beat 的 panel 不应该出现对话气泡或文字。"
        ),
        "correction_template": (
            "删除画面内所有英文对话气泡、DIALOGUE 标签和 speaker 代号文字，"
            "保留人物动作和身份色。"
        ),
        "negative_prompt_clause": (
            "Do not render any English text, dialogue bubble, 'DIALOGUE' label, "
            "or speaker code (e.g. MGNQN_xxxx) inside the image. Audio metadata "
            "such as audio_type is not visual content."
        ),
    },
    {
        "code": "panel_index_text_overlay",
        "layer": "correction",
        "gate_enabled": 1,
        "detection": (
            "Is there a text label like 'beat N', 'Panel N', 'eat N', or any "
            "grid index number rendered directly inside the panel image?"
        ),
        "prevention_rule": (
            "Edit prompt 不得让模型把 panel 索引 / beat 编号写进图内。"
        ),
        "correction_template": (
            "删除画面内的 'beat N' / 'Panel N' / 数字索引 / 网格标签等文字。"
        ),
        "negative_prompt_clause": (
            "Do not render panel index numbers, 'beat N', 'Panel N', 'eat N', "
            "grid labels, or any numeric/alphabetic tag inside the panel image."
        ),
    },
    {
        "code": "style_drift_to_rendered",
        "layer": "correction",
        "gate_enabled": 1,
        "detection": (
            "Has the panel clearly drifted OUT OF the minimal stick-figure "
            "storyboard style INTO a rendered illustration? The intended "
            "stick-figure style ALLOWS tiny simple-line eye dots and a "
            "single-line mouth for expression, a flat colored body block, "
            "and a round head. It is only a drift if you see MULTIPLE of: "
            "realistic hair strands, shaded/modeled faces with nose and "
            "detailed mouth, clothing texture / folds, rendered body "
            "proportions and muscles, gradient shading, or semi-realistic "
            "illustration polish. If the image is still a clearly cartoony "
            "stick figure with simple line features, answer 'no'."
        ),
        "prevention_rule": (
            "保持 stick-figure 线稿故事板风格：圆头、单线躯干、色块身体；"
            "不画发型 / 五官细节 / 衣物材质 / 阴影。"
        ),
        "correction_template": (
            "改回最小 stick-figure 线稿：圆头、单线躯干、纯色身体块；"
            "删除发型、五官、衣物材质、阴影。"
        ),
        "negative_prompt_clause": (
            "Keep every panel as a minimal stick-figure storyboard: round head, "
            "single-line torso, flat colored body block only. No facial features, "
            "no hair detail, no clothing folds, no fabric texture, no muscles, "
            "no shading, no rendered illustration style, no semi-realistic human "
            "forms."
        ),
    },
    {
        "code": "empty_cell_overflow",
        "layer": "correction",
        "gate_enabled": 0,
        "detection": (
            "Does this cell contain hallucinated filler content that looks "
            "duplicated from another panel or unrelated to any coherent "
            "storyboard beat, as if the model guessed at an intentionally-"
            "empty grid slot?"
        ),
        "prevention_rule": (
            "Edit grid 中空出的 cell 必须提示模型留白，不得自由补画。"
        ),
        "correction_template": (
            "空 cell 保持纯白或 solid blank；不得包含任何人物 / 道具 / 背景。"
        ),
        "negative_prompt_clause": (
            "If any panel slot is marked EMPTY in the instructions, render it as "
            "a solid blank panel (white or neutral). Do not duplicate content "
            "from other panels into an empty slot. Do not invent new content for "
            "empty slots."
        ),
    },
    {
        "code": "generic_image_text",
        "layer": "correction",
        "gate_enabled": 1,
        "detection": (
            "Does the panel show any readable text, letters, numbers, labels, "
            "watermarks, or signage baked into the image (other than content "
            "that is intrinsically part of the scene such as a wall clock's "
            "numerals)?"
        ),
        "prevention_rule": (
            "画面内不得出现拟声词、英文气泡、标签、水印、标牌、或任何非场景内文字。"
        ),
        "correction_template": (
            "删除画面内所有文字 / 标签 / 水印 / 标牌。"
        ),
        "negative_prompt_clause": (
            "Do not render any text, letters, numerals, labels, captions, "
            "watermarks, signage, or sound-effect words inside the image. "
            "Exception: diegetic text that is genuinely part of the depicted "
            "object (e.g., wall-clock numerals) is allowed."
        ),
    },
    {
        "code": "closeup_with_environment",
        "layer": "correction",
        "gate_enabled": 0,
        "detection": (
            "Is this beat explicitly a close-up / head-and-shoulders / dialogue "
            "close-up, yet the panel drags in tables, bowls, chopsticks, "
            "shelves, full-room depth, or corridor perspective that doesn't "
            "belong in a close-up?"
        ),
        "prevention_rule": (
            "近景 / 特写 / 台词头肩景 beat 必须收紧到人物，背景保持空或简单色块。"
        ),
        "correction_template": (
            "改成纯头肩近景 / 胸上景，主体占画面主体；删除桌面碗筷和背景环境。"
        ),
        "negative_prompt_clause": (
            "For close-up / head-and-shoulders / dialogue close-up panels, keep "
            "the shot tight on the character. Do not import environmental props "
            "(tables, bowls, shelves, corridors) that do not belong in a close-up."
        ),
    },
    {
        "code": "duplicate_identity_color",
        "layer": "correction",
        "gate_enabled": 0,
        "detection": (
            "Are there two or more visible figures that share the same named "
            "identity color within a single panel, causing identity ambiguity?"
        ),
        "prevention_rule": (
            "同一画面内，每个命名身份色最多对应一个人物；多余同色人物要删除或改为灰色。"
        ),
        "correction_template": (
            "删除指令必须带空间锚 + 计数断言，否则模型会保留原图全部主体。"
            "模板：\"删除画面 <空间锚：最左侧 / 右桌 / 后景 等> 的 <颜色> 身份色人物。"
            "画面必须恰好剩 <N> 个人物：<N 个身份 + hex 色>。"
            "不得保留任何多余 <颜色> 身份色人物。\" "
            "尤其在重复主体紧贴排列且没有天然空间分隔时，走 1x1 polish 单格模式效果最好。"
        ),
        "negative_prompt_clause": (
            "Within one panel, each named identity color may appear on at most "
            "one figure. If two figures share the same identity color, one must "
            "be re-colored neutral gray (unnamed extra) or removed."
        ),
    },
    {
        "code": "cross_beat_blocking_drift",
        "layer": "director",
        "gate_enabled": 0,
        "detection": (
            "Compared to visible continuity evidence such as prior accepted sketches "
            "or scene references, has the "
            "character seating / table assignment / spatial layout flipped or "
            "shifted in a way that breaks continuity within the same scene?"
        ),
        "prevention_rule": (
            "同场景内 beat 应参考已接受草图、场景主图、360/cubemap，"
            "保持明显的座位、桌位、前后景、机位轴线连续性。"
        ),
        "correction_template": (
            "参照可见连续性来源还原本 beat 的座位 / 桌位 / 前后景关系。"
        ),
        "negative_prompt_clause": (
            "When editing a beat in a continuing scene, preserve visible continuity "
            "from accepted sketches or scene references: seating, table assignment, "
            "near/far depth order, left/right split, and camera axis when they are "
            "readable. Do not silently re-stage the scene."
        ),
    },
    {
        "code": "scene_anchor_drift",
        "layer": "director",
        "gate_enabled": 0,
        "detection": (
            "Does this panel depart from the visible established camera axis "
            "or scene continuity, suggesting the scene has been "
            "re-staged from a new angle?"
        ),
        "prevention_rule": (
            "director 层编辑不应无理由翻 180 度轴线；camera axis / 主视点方向应参考可见连续性来源。"
        ),
        "correction_template": (
            "恢复可见连续性来源里的机位侧 / 主视点方向；不要无理由跨 180 度轴线。"
        ),
        "negative_prompt_clause": (
            "Preserve the scene's established camera axis and main viewpoint "
            "direction. Do not flip across the 180-degree line unless the "
            "instruction explicitly allows a reverse shot."
        ),
    },
    {
        "code": "shot_scale_angle_mismatch",
        "layer": "director",
        "gate_enabled": 0,
        "detection": (
            "Given the scene's established blocking (who sits where, which "
            "table belongs to whom, camera axis) and this beat's shot scale "
            "(close-up / medium / wide), does any anchor character appear in "
            "the frame who — by the scene's 180° line and camera axis — "
            "should be behind the camera or off-screen at this framing? "
            "Also flag when the shot scale the script asks for (【特写】/"
            "【近景】) contradicts the framing actually rendered."
        ),
        "prevention_rule": (
            "写 edit_instruction 前必须根据当前草图、前后已接受草图、场景主图和 "
            "360/cubemap 判断本 beat 的 shot scale 下哪些人物应可见。"
            "凡按可见连续性应不可见的人物，edit_instruction 必须明写"
            "\"不要渲染 <该人物>——本角度他在相机后方 / 轴线另一侧\"。"
        ),
        "correction_template": (
            "参照可见连续性来源：<identity> 位于 <空间位置>；本 beat 的 "
            "<shot_scale> / <angle_tag> 下他不可见。"
            "修法：\"删除画面中的 <identity>；本角度 <他的 seat_tag> "
            "位于相机后方 / 180° 线另一侧，不得渲染。\" "
            "保留当前 beat 要求可见的其他人物 + 可见连续性元素（桌位、道具、背景轮廓）。"
        ),
        "negative_prompt_clause": (
            "Respect the scene's camera axis and 180° line. Do not render "
            "anchor characters whose seat is, for the current beat's shot "
            "scale and viewpoint, behind the camera or across the 180° line. "
            "Close-up framing must not smuggle background characters into the "
            "frame just because the script mentions them — if they are "
            "geometrically out of frame at this scale, omit them."
        ),
    },
]


# --------------------------------------------------------------------- #
# Defs layer (verification.db)                                           #
# --------------------------------------------------------------------- #

async def ensure_seeded(defs_db: aiosqlite.Connection) -> None:
    """Seed verification.db with `_SEED_FAILURE_MODES` (idempotent UPSERT)."""
    await ensure_defs_seeded(defs_db, _SEED_FAILURE_MODES)


async def list_active(
    defs_db: aiosqlite.Connection,
    layer: str | None = None,
    gate_only: bool = False,
) -> list[dict[str, Any]]:
    """Read active defs from `sketch_failure_mode_defs`.

    Does NOT fall back to legacy project tables — callers that need
    legacy compatibility should use `load_negative_clause_for_project`,
    which performs its own targeted fallback but never pollutes
    `registry_version`.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if layer is not None:
        clauses.append("layer = ?")
        params.append(layer)
    if gate_only:
        clauses.append("gate_enabled = 1")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"SELECT * FROM sketch_failure_mode_defs {where} ORDER BY code"
    async with defs_db.execute(query, params) as cursor:
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_by_code(defs_db: aiosqlite.Connection, code: str) -> dict[str, Any] | None:
    async with defs_db.execute(
        "SELECT * FROM sketch_failure_mode_defs WHERE code = ?",
        (code,),
    ) as cursor:
        row = await cursor.fetchone()
    return dict(row) if row else None


async def upsert(
    defs_db: aiosqlite.Connection,
    code: str,
    **fields: Any,
) -> None:
    """Insert or update a failure-mode def in verification.db."""
    if not fields:
        raise ValueError("upsert needs at least one field besides code")
    existing = await get_by_code(defs_db, code)
    if existing is None:
        layer = fields.get("layer")
        detection = fields.get("detection")
        if not layer or not detection:
            raise ValueError(
                f"New failure mode {code} must specify at least layer and detection"
            )
        await defs_db.execute(
            """
            INSERT INTO sketch_failure_mode_defs (
                code, layer, detection, prevention_rule,
                correction_template, negative_prompt_clause,
                gate_enabled, fixture_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                code,
                layer,
                detection,
                fields.get("prevention_rule", ""),
                fields.get("correction_template", ""),
                fields.get("negative_prompt_clause", ""),
                int(fields.get("gate_enabled", 0)),
                fields.get("fixture_path", ""),
            ),
        )
    else:
        # Restrict updates to schema columns (protect the UPDATE from
        # arbitrary callers who may pass extra kwargs).
        allowed = {
            "layer", "detection", "prevention_rule", "correction_template",
            "negative_prompt_clause", "gate_enabled", "fixture_path",
        }
        columns = [k for k in fields if k in allowed]
        if not columns:
            return
        set_clause = ", ".join(f"{col} = ?" for col in columns)
        params = [fields[col] for col in columns]
        await defs_db.execute(
            f"UPDATE sketch_failure_mode_defs SET {set_clause}, updated_at = datetime('now') "
            "WHERE code = ?",
            (*params, code),
        )
    await defs_db.commit()


async def build_negative_prompt_clause(defs_db: aiosqlite.Connection, layer: str) -> str:
    """Return a multi-bullet negative-constraint string for the given layer."""
    rows = await list_active(defs_db, layer=layer)
    if not rows:
        return ""
    bullets = [f"- {row['negative_prompt_clause']}" for row in rows if row.get("negative_prompt_clause")]
    if not bullets:
        return ""
    header = f"NEGATIVE CONSTRAINTS ({layer} layer, registry-driven):"
    return "\n".join([header, *bullets])


def seed_codes() -> list[str]:
    return [entry["code"] for entry in _SEED_FAILURE_MODES]


# --------------------------------------------------------------------- #
# Hits layer (project data.db)                                           #
# --------------------------------------------------------------------- #

async def bump_hit(project_db: aiosqlite.Connection, code: str, episode: int) -> None:
    """Increment a project-local hit counter on `sketch_failure_mode_hits`."""
    await project_db.execute(
        """
        INSERT INTO sketch_failure_mode_hits (code, first_seen_episode, hit_count, last_seen_at)
        VALUES (?, ?, 1, datetime('now'))
        ON CONFLICT(code) DO UPDATE SET
            hit_count = hit_count + 1,
            first_seen_episode = CASE
                WHEN first_seen_episode = -1 THEN excluded.first_seen_episode
                ELSE first_seen_episode
            END,
            last_seen_at = datetime('now')
        """,
        (code, int(episode)),
    )
    await project_db.commit()


async def get_hits(project_db: aiosqlite.Connection, code: str | None = None) -> list[dict[str, Any]]:
    if code:
        async with project_db.execute(
            "SELECT * FROM sketch_failure_mode_hits WHERE code = ?", (code,)
        ) as cursor:
            rows = await cursor.fetchall()
    else:
        async with project_db.execute(
            "SELECT * FROM sketch_failure_mode_hits ORDER BY code"
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(row) for row in rows]


# --------------------------------------------------------------------- #
# Legacy fallback (only for seed_mirror_once)                            #
# --------------------------------------------------------------------- #

async def read_legacy_defs(project_db: aiosqlite.Connection) -> list[dict[str, Any]]:
    """Read phase-1 defs from project's legacy `sketch_failure_modes` table.

    Returns empty list if the legacy table is absent. **Does NOT participate
    in `registry_version` computation** — only used by the `seed_mirror_once`
    CLI to backfill verification.db from historical project state.
    """
    try:
        async with project_db.execute(
            """
            SELECT code, layer, detection, prevention_rule, correction_template,
                   negative_prompt_clause, gate_enabled, fixture_path
            FROM sketch_failure_modes
            ORDER BY code
            """
        ) as cursor:
            rows = await cursor.fetchall()
    except Exception:
        return []
    return [dict(row) for row in rows]


# --------------------------------------------------------------------- #
# Facade for sync-heavy pipeline code                                    #
# --------------------------------------------------------------------- #

def _resolve_user_project(project_dir: str | None) -> tuple[str, str] | None:
    if not project_dir:
        return None
    p = Path(project_dir).expanduser().resolve()
    parts = p.parts
    if len(parts) < 2:
        return None
    return parts[-2], parts[-1]


def _resolve_user_verification_db(project_dir: str | None) -> Path | None:
    resolved = _resolve_user_project(project_dir)
    if not resolved:
        return None
    user, project = resolved
    from novelvideo.utils.project_paths import ProjectPaths

    try:
        return ProjectPaths(user, project).global_shared_verification_db
    except Exception:
        return None


def _resolve_project_db_path(project_dir: str | None) -> Path | None:
    resolved = _resolve_user_project(project_dir)
    if not resolved:
        return None
    user, project = resolved
    from novelvideo.utils.project_paths import ProjectPaths

    try:
        paths = ProjectPaths(user, project)
    except Exception:
        return None
    candidate = paths.data_db
    if candidate.exists():
        return candidate
    # Legacy placement (data.db in output_dir) — tolerate for older checkouts.
    legacy = Path(project_dir) / "data.db"
    return legacy if legacy.exists() else None


async def load_negative_clause_for_project(
    project_dir: str | None, layer: str
) -> str:
    """Open the user-shared verification.db (seeded if missing) and build clause.

    Safe to call from any pipeline code. Returns "" on failure. The
    returned clause reflects only canonical verification.db defs — no
    silent merging from legacy project tables (that merging is the
    job of `seed_mirror_once`, not runtime).
    """
    db_path = _resolve_user_verification_db(project_dir)
    if not db_path:
        return ""
    try:
        db = await open_defs_db(db_path)
        try:
            await ensure_seeded(db)
            return await build_negative_prompt_clause(db, layer)
        finally:
            await db.close()
    except Exception:
        return ""


def load_negative_clause_for_project_sync(project_dir: str | None, layer: str) -> str:
    """Sync equivalent for UI preview/export code that cannot await.

    Keeps prompt export WYSIWYG with generation actors, which call the async
    facade before submitting prompts.
    """
    db_path = _resolve_user_verification_db(project_dir)
    if not db_path:
        return ""
    try:
        import sqlite3

        from novelvideo.sqlite_pragmas import configure_sqlite_connection
        from novelvideo.verification.global_registry_db import DEFS_SCHEMA_SQL

        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        configure_sqlite_connection(conn)
        try:
            conn.executescript(DEFS_SCHEMA_SQL)
            for entry in _SEED_FAILURE_MODES:
                conn.execute(
                    """
                    INSERT INTO sketch_failure_mode_defs (
                        code, layer, detection, prevention_rule,
                        correction_template, negative_prompt_clause,
                        gate_enabled, fixture_path,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, '', datetime('now'), datetime('now'))
                    ON CONFLICT(code) DO UPDATE SET
                        layer = excluded.layer,
                        detection = excluded.detection,
                        prevention_rule = excluded.prevention_rule,
                        correction_template = excluded.correction_template,
                        negative_prompt_clause = excluded.negative_prompt_clause,
                        gate_enabled = excluded.gate_enabled,
                        updated_at = datetime('now')
                    """,
                    (
                        entry["code"],
                        entry["layer"],
                        entry["detection"],
                        entry.get("prevention_rule", ""),
                        entry.get("correction_template", ""),
                        entry.get("negative_prompt_clause", ""),
                        int(entry.get("gate_enabled", 0)),
                    ),
                )
            conn.commit()
            rows = conn.execute(
                """
                SELECT negative_prompt_clause
                FROM sketch_failure_mode_defs
                WHERE layer = ?
                ORDER BY code
                """,
                (layer,),
            ).fetchall()
        finally:
            conn.close()
        bullets = [
            f"- {row['negative_prompt_clause']}"
            for row in rows
            if str(row["negative_prompt_clause"] or "").strip()
        ]
        if not bullets:
            return ""
        return "\n".join([f"NEGATIVE CONSTRAINTS ({layer} layer, registry-driven):", *bullets])
    except Exception:
        return ""


async def open_defs_db_for_project(project_dir: str | None) -> aiosqlite.Connection:
    """Open (and seed) the user-shared verification.db for this project's user.

    Unlike `load_negative_clause_for_project`, this returns the live
    connection for callers that need multiple reads. Caller owns close.
    """
    db_path = _resolve_user_verification_db(project_dir)
    if not db_path:
        raise RuntimeError(f"cannot resolve user verification.db from project_dir={project_dir}")
    db = await open_defs_db(db_path)
    await ensure_seeded(db)
    return db
