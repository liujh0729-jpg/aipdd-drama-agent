from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path, PurePath
from typing import Any, Iterable

import aiosqlite
from pydantic import BaseModel, Field

from novelvideo.cognee.screenplay_normalizer import clean_scene_name_and_time


class SceneNameMigrationReport(BaseModel):
    dry_run: bool
    backup_path: str | None = None
    scene_renames: list[dict[str, Any]] = Field(default_factory=list)
    scene_merges: list[dict[str, Any]] = Field(default_factory=list)
    beat_updates: list[dict[str, Any]] = Field(default_factory=list)
    asset_copies: list[dict[str, Any]] = Field(default_factory=list)
    copied_assets: list[dict[str, Any]] = Field(default_factory=list)
    skipped_asset_copies: list[dict[str, Any]] = Field(default_factory=list)
    failed_asset_copies: list[dict[str, Any]] = Field(default_factory=list)
    field_conflicts: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


SCENE_FILL_FIELDS = (
    "environment_prompt",
    "description",
    "spatial_layout_image",
    "notes",
    "scene_type",
)
SCENE_REQUIRED_COLUMNS = {
    "name",
    "aliases_json",
    "scene_type",
    "environment_prompt",
    "description",
    "spatial_layout_image",
    "notes",
    "updated_at",
}
BEAT_REQUIRED_COLUMNS = {
    "episode_number",
    "beat_number",
    "scene_ref_json",
    "updated_at",
}


async def migrate_scene_names(
    project_dir: Path | str,
    *,
    asset_project_dir: Path | str | None = None,
    dry_run: bool = True,
) -> SceneNameMigrationReport:
    project_path = Path(project_dir)
    asset_project_path = Path(asset_project_dir) if asset_project_dir is not None else project_path
    db_path = project_path / "data.db"
    report = SceneNameMigrationReport(dry_run=dry_run)

    if not db_path.exists():
        report.warnings.append(f"data.db not found: {db_path}")
        return report

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        if not await _schema_is_valid(db, report):
            return report
        scenes = await _load_scenes(db, report)
        groups = _plan_scene_migrations(scenes, report)
        migrations = _flatten_migrations(groups)
        await _plan_beat_updates(db, migrations, report)

    _plan_asset_copies(asset_project_path, migrations, report)
    if not migrations:
        return report
    if dry_run:
        return report

    if not _copy_scene_assets(report):
        return report

    report.backup_path = str(_backup_database(db_path))
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN")
        try:
            for group in groups:
                await _apply_scene_group(db, group)
            await _apply_beat_updates(db, migrations)
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    return report


async def _schema_is_valid(
    db: aiosqlite.Connection,
    report: SceneNameMigrationReport,
) -> bool:
    scenes_columns = await _table_columns(db, "scenes")
    beats_columns = await _table_columns(db, "beats")
    missing_scenes = sorted(SCENE_REQUIRED_COLUMNS - scenes_columns)
    missing_beats = sorted(BEAT_REQUIRED_COLUMNS - beats_columns)
    if not missing_scenes and not missing_beats:
        return True
    if missing_scenes:
        report.warnings.append(f"schema missing scenes columns: {', '.join(missing_scenes)}")
    if missing_beats:
        report.warnings.append(f"schema missing beats columns: {', '.join(missing_beats)}")
    return False


async def _table_columns(db: aiosqlite.Connection, table_name: str) -> set[str]:
    cursor = await db.execute(f"PRAGMA table_info({table_name})")
    rows = await cursor.fetchall()
    await cursor.close()
    return {row[1] for row in rows}


async def _load_scenes(
    db: aiosqlite.Connection,
    report: SceneNameMigrationReport,
) -> dict[str, dict[str, Any]]:
    try:
        cursor = await db.execute("SELECT * FROM scenes ORDER BY name")
    except Exception as exc:
        report.warnings.append(f"failed to read scenes table: {exc}")
        return {}
    rows = await cursor.fetchall()
    await cursor.close()
    return {row["name"]: dict(row) for row in rows}


def _plan_scene_migrations(
    scenes: dict[str, dict[str, Any]],
    report: SceneNameMigrationReport,
) -> list[dict[str, Any]]:
    dirty_by_canonical: dict[str, list[dict[str, Any]]] = {}
    for old, scene in scenes.items():
        canonical, _time_of_day = clean_scene_name_and_time(old, "")
        if not canonical or canonical == old:
            continue
        dirty_by_canonical.setdefault(canonical, []).append(scene)

    groups: list[dict[str, Any]] = []
    for canonical in sorted(dirty_by_canonical):
        dirty_scenes = dirty_by_canonical[canonical]
        unsafe_names = [
            name
            for name in [canonical, *(scene["name"] for scene in dirty_scenes)]
            if not _is_safe_scene_name(name)
        ]
        if unsafe_names:
            report.warnings.append(
                "unsafe scene name skipped; group not migrated: "
                + ", ".join(sorted(set(unsafe_names)))
            )
            continue
        canonical_scene = scenes.get(canonical)
        rename_source: dict[str, Any] | None = None
        merge_sources = dirty_scenes
        if canonical_scene is None:
            rename_source = dirty_scenes[0]
            merge_sources = dirty_scenes[1:]
            report.scene_renames.append({"old": rename_source["name"], "canonical": canonical})
        for scene in merge_sources:
            report.scene_merges.append({"old": scene["name"], "canonical": canonical})

        groups.append(
            {
                "canonical": canonical,
                "canonical_scene": canonical_scene,
                "rename_source": rename_source,
                "merge_sources": merge_sources,
                "final_scene": _build_final_scene(
                    canonical,
                    canonical_scene,
                    dirty_scenes,
                    report,
                ),
            }
        )
    return groups


def _flatten_migrations(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    migrations: list[dict[str, Any]] = []
    for group in groups:
        dirty_scenes = []
        if group["rename_source"] is not None:
            dirty_scenes.append(group["rename_source"])
        dirty_scenes.extend(group["merge_sources"])
        for scene in dirty_scenes:
            migrations.append(
                {
                    "old": scene["name"],
                    "canonical": group["canonical"],
                    "old_scene": scene,
                }
            )
    return migrations


def _build_final_scene(
    canonical: str,
    canonical_scene: dict[str, Any] | None,
    dirty_scenes: list[dict[str, Any]],
    report: SceneNameMigrationReport,
) -> dict[str, Any]:
    note_additions: list[str] = []
    if canonical_scene is not None:
        final_scene = dict(canonical_scene)
        alias_groups = [_load_aliases(canonical_scene)]
        for scene in dirty_scenes:
            alias_groups.extend([[scene["name"]], _load_aliases(scene)])
        final_scene["aliases_json"] = json.dumps(_merge_aliases(*alias_groups), ensure_ascii=False)
        for field in SCENE_FILL_FIELDS:
            kept_value = str(final_scene.get(field) or "")
            if not kept_value:
                kept_value = _first_nonempty(scene.get(field) for scene in dirty_scenes)
                final_scene[field] = kept_value
            _record_conflicts(
                report,
                note_additions,
                canonical,
                field,
                kept_value,
                dirty_scenes,
            )
        final_scene["spatial_layout_image"] = _rewrite_spatial_layout_image(
            final_scene.get("spatial_layout_image", ""),
            canonical,
            dirty_scenes,
        )
        final_scene["notes"] = _append_migration_notes(final_scene.get("notes", ""), note_additions)
        return final_scene

    final_scene = dict(dirty_scenes[0])
    final_scene["name"] = canonical
    alias_groups = [_load_aliases(dirty_scenes[0]), [dirty_scenes[0]["name"]]]
    for scene in dirty_scenes[1:]:
        alias_groups.extend([[scene["name"]], _load_aliases(scene)])
    final_scene["aliases_json"] = json.dumps(_merge_aliases(*alias_groups), ensure_ascii=False)
    for field in SCENE_FILL_FIELDS:
        kept_value = _first_nonempty(scene.get(field) for scene in dirty_scenes)
        final_scene[field] = kept_value
        _record_conflicts(
            report,
            note_additions,
            canonical,
            field,
            kept_value,
            dirty_scenes,
        )
    final_scene["spatial_layout_image"] = _rewrite_spatial_layout_image(
        final_scene.get("spatial_layout_image", ""),
        canonical,
        dirty_scenes,
    )
    final_scene["notes"] = _append_migration_notes(final_scene.get("notes", ""), note_additions)
    return final_scene


def _rewrite_spatial_layout_image(
    value: Any,
    canonical: str,
    dirty_scenes: list[dict[str, Any]],
) -> str:
    rewritten = str(value or "")
    for scene in dirty_scenes:
        rewritten = _rewrite_scene_asset_path(rewritten, str(scene["name"]), canonical)
    return rewritten


def _rewrite_scene_asset_path(path_value: str, old_scene_name: str, canonical: str) -> str:
    normalized_path = str(path_value or "").replace("\\", "/")
    old_prefix = f"assets/scenes/{old_scene_name}"
    canonical_prefix = f"assets/scenes/{canonical}"
    if normalized_path == old_prefix:
        return canonical_prefix
    if normalized_path.startswith(f"{old_prefix}/"):
        return f"{canonical_prefix}{normalized_path[len(old_prefix):]}"
    return str(path_value or "")


async def _plan_beat_updates(
    db: aiosqlite.Connection,
    migrations: list[dict[str, Any]],
    report: SceneNameMigrationReport,
) -> None:
    if not migrations:
        return

    rename_map = {migration["old"]: migration["canonical"] for migration in migrations}
    cursor = await db.execute(
        "SELECT episode_number, beat_number, scene_ref_json FROM beats "
        "ORDER BY episode_number, beat_number"
    )
    rows = await cursor.fetchall()
    await cursor.close()
    for row in rows:
        raw_ref = row["scene_ref_json"] or ""
        if not raw_ref:
            continue
        try:
            scene_ref = json.loads(raw_ref)
        except json.JSONDecodeError:
            report.warnings.append(
                f"invalid scene_ref_json at beat {row['episode_number']}.{row['beat_number']}"
            )
            continue
        if not isinstance(scene_ref, dict):
            continue

        old_values = {
            scene_ref.get("scene_id") if scene_ref.get("scene_id") in rename_map else None,
            scene_ref.get("base_id") if scene_ref.get("base_id") in rename_map else None,
        }
        old_values.discard(None)
        for old in sorted(old_values):
            report.beat_updates.append(
                {
                    "episode_number": row["episode_number"],
                    "beat_number": row["beat_number"],
                    "old": old,
                    "canonical": rename_map[old],
                }
            )


def _plan_asset_copies(
    project_path: Path,
    migrations: list[dict[str, Any]],
    report: SceneNameMigrationReport,
) -> None:
    scenes_asset_dir = (project_path / "assets" / "scenes").resolve()
    for migration in migrations:
        old_dir = _safe_scene_asset_dir(
            scenes_asset_dir,
            migration["old"],
            report,
            role="source",
        )
        canonical_dir = _safe_scene_asset_dir(
            scenes_asset_dir,
            migration["canonical"],
            report,
            role="destination",
        )
        if old_dir is None or canonical_dir is None:
            continue
        if not old_dir.exists():
            continue
        if not old_dir.is_dir():
            report.warnings.append(f"scene asset source is not a directory: {old_dir}")
            continue

        for source in sorted(old_dir.rglob("*")):
            relative = source.relative_to(old_dir)
            resolved_source = source.resolve()
            if not _is_within(resolved_source, scenes_asset_dir):
                report.warnings.append(
                    f"source asset path is outside assets/scenes, skipped: {resolved_source}"
                )
                continue
            if not _is_within(resolved_source, old_dir):
                report.warnings.append(
                    "source asset path is outside source scene directory, skipped: "
                    f"{resolved_source}"
                )
                continue
            destination = (canonical_dir / relative).resolve()
            if not _is_within(destination, scenes_asset_dir):
                report.warnings.append(
                    f"destination asset path is outside assets/scenes, skipped: {destination}"
                )
                continue
            if destination.exists():
                _record_skipped_asset_copy(
                    report,
                    resolved_source,
                    destination,
                    "destination exists",
                )
                continue
            if resolved_source.is_file():
                report.asset_copies.append(
                    {"source": str(resolved_source), "destination": str(destination)}
                )


def _backup_database(db_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = db_path.with_name(f"data.scene-name-migration-{timestamp}.db")
    suffix = 1
    while backup_path.exists():
        backup_path = db_path.with_name(f"data.scene-name-migration-{timestamp}-{suffix}.db")
        suffix += 1
    shutil.copy2(db_path, backup_path)
    return backup_path


async def _apply_scene_group(db: aiosqlite.Connection, group: dict[str, Any]) -> None:
    canonical = group["canonical"]
    final_scene = group["final_scene"]
    if group["canonical_scene"] is None:
        await _apply_scene_rename(db, group["rename_source"]["name"], canonical, final_scene)
    else:
        await _apply_scene_update(db, canonical, final_scene)
    for scene in group["merge_sources"]:
        await db.execute("DELETE FROM scenes WHERE name = ?", (scene["name"],))


async def _apply_scene_rename(
    db: aiosqlite.Connection,
    old: str,
    canonical: str,
    final_scene: dict[str, Any],
) -> None:
    await db.execute(
        """
        UPDATE scenes
        SET name = ?,
            aliases_json = ?,
            environment_prompt = ?,
            description = ?,
            spatial_layout_image = ?,
            notes = ?,
            scene_type = ?,
            updated_at = datetime('now')
        WHERE name = ?
        """,
        (
            canonical,
            final_scene["aliases_json"],
            final_scene.get("environment_prompt", ""),
            final_scene.get("description", ""),
            final_scene.get("spatial_layout_image", ""),
            final_scene.get("notes", ""),
            final_scene.get("scene_type", ""),
            old,
        ),
    )


async def _apply_scene_update(
    db: aiosqlite.Connection,
    canonical: str,
    final_scene: dict[str, Any],
) -> None:
    await db.execute(
        """
        UPDATE scenes
        SET aliases_json = ?,
            environment_prompt = ?,
            description = ?,
            spatial_layout_image = ?,
            notes = ?,
            scene_type = ?,
            updated_at = datetime('now')
        WHERE name = ?
        """,
        (
            final_scene["aliases_json"],
            final_scene.get("environment_prompt", ""),
            final_scene.get("description", ""),
            final_scene.get("spatial_layout_image", ""),
            final_scene.get("notes", ""),
            final_scene.get("scene_type", ""),
            canonical,
        ),
    )


async def _apply_beat_updates(
    db: aiosqlite.Connection,
    migrations: list[dict[str, Any]],
) -> None:
    if not migrations:
        return
    rename_map = {migration["old"]: migration["canonical"] for migration in migrations}
    cursor = await db.execute(
        "SELECT episode_number, beat_number, scene_ref_json FROM beats "
        "ORDER BY episode_number, beat_number"
    )
    rows = await cursor.fetchall()
    await cursor.close()
    for row in rows:
        raw_ref = row["scene_ref_json"] or ""
        if not raw_ref:
            continue
        try:
            scene_ref = json.loads(raw_ref)
        except json.JSONDecodeError:
            continue
        if not isinstance(scene_ref, dict):
            continue

        changed = False
        for key in ("scene_id", "base_id"):
            value = scene_ref.get(key)
            if value in rename_map:
                scene_ref[key] = rename_map[value]
                changed = True
        if changed:
            await db.execute(
                """
                UPDATE beats
                SET scene_ref_json = ?, updated_at = datetime('now')
                WHERE episode_number = ? AND beat_number = ?
                """,
                (
                    json.dumps(scene_ref, ensure_ascii=False),
                    row["episode_number"],
                    row["beat_number"],
                ),
            )


def _copy_scene_assets(report: SceneNameMigrationReport) -> bool:
    for item in report.asset_copies:
        source = Path(item["source"])
        destination = Path(item["destination"])
        if not source.exists() or not source.is_file():
            error = "source missing or not a file"
            report.failed_asset_copies.append({**item, "error": error})
            report.warnings.append(f"asset {error}, skipped: {source}")
            return False
        try:
            with source.open("rb"):
                pass
        except OSError as exc:
            report.failed_asset_copies.append({**item, "error": str(exc)})
            report.warnings.append(f"asset source is not readable, skipped: {source}: {exc}")
            return False
        if destination.exists():
            _record_skipped_asset_copy(report, source, destination, "destination exists")
            continue
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            report.copied_assets.append(item)
        except OSError as exc:
            report.failed_asset_copies.append({**item, "error": str(exc)})
            report.warnings.append(
                f"asset copy failed, database not migrated: {source} -> {destination}: {exc}"
            )
            return False
    return True


def _record_skipped_asset_copy(
    report: SceneNameMigrationReport,
    source: Path,
    destination: Path,
    reason: str,
) -> None:
    item = {
        "source": str(source),
        "destination": str(destination),
        "reason": reason,
    }
    if item not in report.skipped_asset_copies:
        report.skipped_asset_copies.append(item)
    warning_reason = "destination already exists" if reason == "destination exists" else reason
    report.warnings.append(f"asset {warning_reason}, skipped: {source} -> {destination}")


def _safe_scene_asset_dir(
    root: Path,
    scene_name: str,
    report: SceneNameMigrationReport,
    *,
    role: str,
) -> Path | None:
    resolved = (root / scene_name).resolve()
    if not _is_within(resolved, root):
        report.warnings.append(f"{role} scene asset path is outside assets/scenes: {resolved}")
        return None
    return resolved


def _is_safe_scene_name(scene_name: str) -> bool:
    name = str(scene_name or "")
    if not name or "/" in name or "\\" in name:
        return False
    path = PurePath(name)
    if path.is_absolute() or name in {".", ".."}:
        return False
    return all(part not in {"", ".", ".."} for part in path.parts)


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _load_aliases(scene: dict[str, Any]) -> list[str]:
    raw_aliases = scene.get("aliases_json") or "[]"
    try:
        aliases = json.loads(raw_aliases)
    except json.JSONDecodeError:
        return []
    if not isinstance(aliases, list):
        return []
    return [str(alias).strip() for alias in aliases if str(alias).strip()]


def _merge_aliases(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for alias in group:
            if alias and alias not in seen:
                merged.append(alias)
                seen.add(alias)
    return merged


def _first_nonempty(values: Iterable[Any]) -> str:
    for value in values:
        if value:
            return str(value)
    return ""


def _record_conflicts(
    report: SceneNameMigrationReport,
    note_additions: list[str],
    canonical: str,
    field: str,
    kept_value: str,
    dirty_scenes: list[dict[str, Any]],
) -> None:
    if not kept_value:
        return
    for scene in dirty_scenes:
        discarded_value = str(scene.get(field) or "")
        if not discarded_value or discarded_value == kept_value:
            continue
        conflict = {
            "canonical": canonical,
            "old": scene["name"],
            "field": field,
            "kept_value": kept_value,
            "discarded_value": discarded_value,
        }
        report.field_conflicts.append(conflict)
        note_additions.append(
            "[scene-name-migration conflict] "
            f"old={scene['name']} field={field} "
            f"discarded={_summarize_value(discarded_value)}"
        )


def _append_migration_notes(notes: str, additions: list[str]) -> str:
    if not additions:
        return str(notes or "")
    parts = [str(notes or "").strip(), *additions]
    return "\n".join(part for part in parts if part)


def _summarize_value(value: str, limit: int = 160) -> str:
    normalized = " ".join(str(value or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."
