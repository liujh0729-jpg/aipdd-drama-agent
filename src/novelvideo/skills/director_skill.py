"""Director Skill HTTP CLI — utility commands for the AI agent (Claude /
Codex / OpenClaw) to drive the Director 3D editor through Bash.

Subcommands:
- catalog: dump scene catalog JSON for a world.json
- query: GET current state from /__dramaclaw/director-state-query
- push: POST a director command via /__dramaclaw/director-push
- save-render: extract render_base64 from a state record and write PNG to disk

The agent (Claude) does the loop reasoning and convergence judgment itself —
this script is just thin tooling around the editor's HTTP/SSE channel.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
import sys
import time
import uuid
from io import BytesIO
from pathlib import Path

import httpx

DEFAULT_BASE_URL = ""


def _default_base_url() -> str:
    """[aipdd fork delta req41] 导演台地址只认配置，不猜主机名 / 本机端口。

    旧实现兜底 ``http://127.0.0.1:9024``：在非本机 / 容器 / 反代部署下这个地址
    对着的是脚本自己所在的机器，而不是用户浏览器能打开的 viewer，agent 会拿到
    一整排假成功的调用。现在只读 ``DIRECTOR_VIEWER_URL``，没配置就让调用方显式
    传 ``--base-url``。
    """

    for env_name in ("DIRECTOR_VIEWER_URL", "VITE_DIRECTOR_VIEWER_URL"):
        raw = str(os.environ.get(env_name) or "").strip()
        if raw:
            return (raw if "://" in raw else f"http://{raw}").rstrip("/")
    return ""


def _fail(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def _query_state(base_url: str, session: str, frame_id: str = "") -> dict:
    params = {"session": session}
    if frame_id:
        params["frame_id"] = frame_id
    try:
        resp = httpx.get(
            f"{base_url}/__dramaclaw/director-state-query",
            params=params,
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        _fail(f"query failed: {exc}")


def _push_command(
    base_url: str,
    session: str,
    op: str,
    payload: dict,
    frame_id: str = "",
    wait: bool = True,
    wait_timeout_s: float = 3.0,
    poll_interval_s: float = 0.1,
) -> dict:
    fid = frame_id or f"f_{uuid.uuid4().hex[:12]}"
    body = {"session_id": session, "op": op, "payload": payload, "frame_id": fid}
    try:
        resp = httpx.post(
            f"{base_url}/__dramaclaw/director-push",
            json=body,
            timeout=15.0,
        )
        resp.raise_for_status()
        push_data = resp.json()
    except httpx.HTTPError as exc:
        _fail(f"push failed: {exc}")
    if not wait or not push_data.get("ok"):
        return push_data
    deadline = time.monotonic() + wait_timeout_s
    while time.monotonic() < deadline:
        try:
            q = _query_state(base_url, session, fid)
        except SystemExit:
            break
        record = q.get("record") if isinstance(q, dict) else None
        if isinstance(record, dict) and record.get("frame_id") == fid:
            push_data["frame_result"] = record.get("result")
            push_data["frame_error"] = record.get("error")
            push_data["frame_ts"] = record.get("ts")
            result = record.get("result") or {}
            push_data["handler_ok"] = bool(result.get("ok")) and not record.get("error")
            return push_data
        time.sleep(poll_interval_s)
    push_data["frame_timeout"] = wait_timeout_s
    return push_data


def _push_result_ok(data: dict) -> bool:
    return (
        bool(data.get("ok"))
        and data.get("handler_ok") is not False
        and not data.get("frame_error")
        and not data.get("frame_timeout")
    )


def _beat_number(value: object) -> int:
    try:
        return int(str(value).strip() or 0)
    except (TypeError, ValueError):
        return 0


def _beat_order(beat: dict) -> int:
    order = _beat_number(beat.get("shot_order"))
    return order or _beat_number(beat.get("beat")) * 10


def _local_path(value: str) -> Path:
    raw = str(value or "").strip()
    if raw.startswith("/@fs"):
        raw = raw[4:]
    elif raw.startswith("@fs"):
        raw = raw[3:]
    return Path(raw).expanduser()


def _is_seat_attachment(value: object) -> bool:
    return str(value or "").startswith("seat:")


def _project_dir_from_blockings_dir(blockings_dir: Path) -> Path:
    path = Path(blockings_dir)
    if path.name.startswith("ep") and path.parent.name == "director_blockings":
        return path.parent.parent
    _fail(f"cannot infer project dir from blockings dir: {path}")


def _episode_from_blockings_dir(blockings_dir: Path) -> int:
    name = Path(blockings_dir).name
    if name.startswith("ep"):
        try:
            return int(name[2:])
        except ValueError:
            pass
    _fail(f"cannot infer episode from blockings dir: {blockings_dir}")


def _camera_with_name(camera: dict, name: str = "") -> dict:
    result = dict(camera or {})
    if name:
        result["id"] = name
        result["label"] = name
    result.setdefault("id", "director_shot")
    result.setdefault("label", result["id"])
    return result


def _overlay_marker_palette(actors: list[dict], props: list[dict]) -> dict:
    palette: dict[str, dict] = {}
    for actor in actors:
        actor_type = str(actor.get("type") or "actor_neutral")
        if actor_type in palette:
            continue
        palette[actor_type] = {
            "color": actor.get("marker_color") or "#B6FF00",
            "label": f"{actor.get('identity_id') or actor_type} character marker",
            "object_class": actor_type,
            "sketch_color_policy": "preserve_marker_color",
            "marker_role": "character_identity",
        }
    for prop in props:
        if not _is_global_asset_prop(prop):
            continue
        prop_type = str(prop.get("type") or "prop_hero")
        if prop_type in palette:
            continue
        palette[prop_type] = {
            "color": prop.get("marker_color") or "#00FFD5",
            "label": f"{prop.get('prop_id') or prop_type} global asset prop marker",
            "object_class": prop_type,
            "sketch_color_policy": "preserve_marker_color",
            "marker_role": "global_asset_prop",
        }
    return palette


def _is_global_asset_prop(prop: dict) -> bool:
    return bool(
        prop.get("is_global_asset")
        or prop.get("asset_scope") == "global"
        or prop.get("preserve_marker_color")
        or prop.get("tracking") == "tracked_marker"
    )


def _decode_render_to_png(render_b64: str, out_path: Path) -> bool:
    if not render_b64:
        return False
    try:
        from PIL import Image

        out_path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.open(BytesIO(base64.b64decode(render_b64))).convert("RGB")
        image.save(out_path, format="PNG")
        return True
    except Exception as exc:  # pragma: no cover - best-effort diagnostic path
        print(f"warning: failed to write director render PNG: {exc}", file=sys.stderr)
        return False


def _json_loads(value: object, default):
    if value in (None, ""):
        return default
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _marker_hex(value: object) -> str:
    for token in str(value or "").replace(",", " ").split():
        if token.startswith("#") and len(token) >= 4:
            return token
    return ""


def _unique_strings(values: list[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _project_data_db_path(project_dir: Path) -> Path:
    from novelvideo.config import OUTPUT_DIR, STATE_DIR

    resolved = Path(project_dir).resolve()
    path_parts = resolved.parts
    if "output" in path_parts:
        output_index = path_parts.index("output")
        if len(path_parts) >= output_index + 3:
            repo_root = Path(*path_parts[:output_index])
            user = path_parts[output_index + 1]
            project = path_parts[output_index + 2]
            return repo_root / "state" / user / project / "data.db"
    try:
        relative_project = resolved.relative_to(Path(OUTPUT_DIR).resolve())
    except ValueError:
        raise FileNotFoundError(f"Project dir is not under OUTPUT_DIR: {resolved}")
    if len(relative_project.parts) < 2:
        raise FileNotFoundError(f"Cannot resolve project state db from: {resolved}")
    return Path(STATE_DIR).resolve().joinpath(*relative_project.parts) / "data.db"


def _db_scene_id(row: dict) -> str:
    scene_ref = _json_loads(row.get("scene_ref_json"), {})
    if isinstance(scene_ref, dict):
        return str(scene_ref.get("scene_id") or scene_ref.get("base_id") or "").strip()
    return ""


def _db_identity_ids(row: dict) -> list[str]:
    from novelvideo.models import extract_char_identities_from_markers

    explicit = _json_loads(row.get("detected_identities_json"), [])
    if not isinstance(explicit, list):
        explicit = []
    visual_description = str(row.get("visual_description") or "")
    marker_ids = list(
        extract_char_identities_from_markers(visual_description, strict=False).values()
    )
    speaker = str(row.get("speaker") or "").strip()
    speaker_kind = str(row.get("speaker_kind") or "character").strip()
    speaker_ids = [speaker] if speaker_kind == "character" and "_" in speaker else []
    return _unique_strings([*explicit, *marker_ids, *speaker_ids])


def _db_prop_markers(row: dict) -> list[dict]:
    from novelvideo.models import extract_prop_ids_from_markers

    visual_description = str(row.get("visual_description") or "")
    seen: set[str] = set()
    result: list[dict] = []
    for prop_id in extract_prop_ids_from_markers(visual_description, strict=False):
        if prop_id in seen:
            continue
        seen.add(prop_id)
        result.append({"prop_id": prop_id})
    return result


def _db_beat_payload(
    row: dict,
    *,
    db_path: Path,
    project_dir: Path,
    episode: int,
    sketch_colors: dict,
) -> dict:
    from novelvideo.director_world.paths import beat_blocking_path

    beat_num = _beat_number(row.get("beat_number"))
    shot_order = _beat_number(row.get("shot_order")) or beat_num * 10
    visual_description = str(row.get("visual_description") or "")
    actors = [
        {
            "id": f"actor_{identity_id}",
            "identity_id": identity_id,
            "name": identity_id,
            "type": "actor_neutral",
            "marker_color": _marker_hex(sketch_colors.get(identity_id, "")),
        }
        for identity_id in _db_identity_ids(row)
    ]
    props = [
        {
            "id": f"prop_{ref['prop_id']}",
            "prop_id": ref["prop_id"],
            "name": ref["prop_id"],
            "type": "prop_hero",
            "marker_color": _marker_hex(ref.get("marker_color", "")),
            "tracking": "tracked_marker",
            "asset_scope": "global",
            "is_global_asset": True,
            "preserve_marker_color": True,
        }
        for ref in _db_prop_markers(row)
    ]
    overlay_path = beat_blocking_path(project_dir, episode, beat_num)
    scene_id = _db_scene_id(row)
    return {
        "source": "db",
        "path": str(overlay_path) if overlay_path.exists() else "",
        "db_path": str(db_path),
        "overlay_path": str(overlay_path) if overlay_path.exists() else "",
        "has_overlay": overlay_path.exists(),
        "beat": beat_num,
        "shot_order": shot_order,
        "scene_id": scene_id,
        "visual_description": visual_description,
        "audio_type": row.get("audio_type") or "",
        "speaker": row.get("speaker") or "",
        "actor_count": len(actors),
        "prop_count": len(props),
        "actors": actors,
        "props": props,
        "beat_context": {
            "episode": episode,
            "beat": beat_num,
            "shot_order": shot_order,
            "visual_description": visual_description,
            "actors": actors,
            "global_props": props,
        },
    }


def _load_db_episode_payload(project_dir: Path, episode: int) -> dict:
    db_path = _project_data_db_path(project_dir)
    if not db_path.exists():
        return {"db_path": str(db_path), "beats": [], "sketch_colors": {}}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        episode_row = conn.execute(
            "SELECT sketch_colors_json FROM episodes WHERE number = ?",
            (int(episode),),
        ).fetchone()
        sketch_colors = _json_loads(episode_row["sketch_colors_json"], {}) if episode_row else {}
        if not isinstance(sketch_colors, dict):
            sketch_colors = {}
        rows = conn.execute(
            """
            SELECT
                beat_number,
                narration,
                visual_description,
                detected_identities_json,
                scene_ref_json,
                audio_type,
                speaker,
                speaker_kind,
                time_of_day,
                shot_order,
                duration_seconds,
                is_manual_shot
            FROM beats
            WHERE episode_number = ?
            ORDER BY COALESCE(shot_order, beat_number * 10), beat_number
            """,
            (int(episode),),
        ).fetchall()
    finally:
        conn.close()
    return {
        "db_path": str(db_path),
        "sketch_colors": sketch_colors,
        "beats": [
            _db_beat_payload(
                dict(row),
                db_path=db_path,
                project_dir=project_dir,
                episode=episode,
                sketch_colors=sketch_colors,
            )
            for row in rows
        ],
    }


def _load_db_beat_payload(project_dir: Path, episode: int, beat_num: int) -> dict | None:
    for beat in _load_db_episode_payload(project_dir, episode)["beats"]:
        if _beat_number(beat.get("beat")) == int(beat_num):
            return beat
    return None


def _list_blocking_file_beats(blockings_dir: Path, scene_id: str) -> list[dict]:
    beats = []
    if not blockings_dir.exists():
        return beats
    for path in sorted(blockings_dir.glob("beat_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if scene_id and str(data.get("scene_id") or "").strip() != scene_id:
            continue
        ctx = data.get("beat_context") or {}
        beats.append(
            {
                "source": "blocking_json",
                "path": str(path),
                "db_path": "",
                "overlay_path": str(path),
                "has_overlay": True,
                "beat": data.get("beat") or ctx.get("beat"),
                "shot_order": data.get("shot_order") or ctx.get("shot_order"),
                "scene_id": data.get("scene_id"),
                "visual_description": ctx.get("visual_description") or "",
                "actor_count": len(ctx.get("actors") or []),
                "prop_count": len(ctx.get("global_props") or []),
            }
        )
    return beats


def _scene_beat_summary(beat: dict) -> dict:
    return {
        "source": beat.get("source") or "",
        "path": beat.get("path") or "",
        "db_path": beat.get("db_path") or "",
        "overlay_path": beat.get("overlay_path") or "",
        "has_overlay": bool(beat.get("has_overlay")),
        "beat": beat.get("beat"),
        "shot_order": _beat_order(beat),
        "scene_id": beat.get("scene_id") or "",
        "visual_description": beat.get("visual_description") or "",
        "audio_type": beat.get("audio_type") or "",
        "speaker": beat.get("speaker") or "",
        "actor_count": int(beat.get("actor_count") or 0),
        "prop_count": int(beat.get("prop_count") or 0),
        "identity_ids": [
            actor.get("identity_id")
            for actor in beat.get("actors") or []
            if actor.get("identity_id")
        ],
        "prop_ids": [
            prop.get("prop_id") for prop in beat.get("props") or [] if prop.get("prop_id")
        ],
    }


def cmd_catalog(args: argparse.Namespace) -> None:
    from novelvideo.skills.director_scene_catalog import load_scene_catalog

    catalog = load_scene_catalog(_local_path(args.world))
    print(catalog.model_dump_json(indent=2))


def cmd_query(args: argparse.Namespace) -> None:
    data = _query_state(args.base_url, args.session, args.frame_id)
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_push(args: argparse.Namespace) -> None:
    payload_dict = {}
    if args.payload:
        try:
            payload_dict = json.loads(args.payload)
        except json.JSONDecodeError as exc:
            _fail(f"--payload must be valid JSON: {exc}")
    data = _push_command(args.base_url, args.session, args.op, payload_dict, args.frame_id)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if not _push_result_ok(data):
        sys.exit(1)


def cmd_save_render(args: argparse.Namespace) -> None:
    """Pull state record, decode render_base64, write to a PNG file.
    Use the saved PNG path with Claude's Read tool to see what the editor sees."""
    data = _query_state(args.base_url, args.session, args.frame_id)
    record = data.get("record")
    if not record:
        _fail("no state record yet (editor not connected or no commands applied)")
    render_b64 = record.get("render_base64") or ""
    if not render_b64:
        _fail("state record has no render_base64")
    out_path = Path(args.output).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(base64.b64decode(render_b64))
    print(
        json.dumps(
            {
                "ok": True,
                "saved": str(out_path),
                "frame_id": record.get("frame_id"),
                "ts": record.get("ts"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_list_scene_beats(args: argparse.Namespace) -> None:
    """List all beats in a project/episode that use a given scene_id, sorted
    by beat number. Used by the AI director to walk a scene end-to-end."""
    blockings_dir = _local_path(args.blockings_dir)
    project_dir = _project_dir_from_blockings_dir(blockings_dir)
    episode = _episode_from_blockings_dir(blockings_dir)
    scene_id = (args.scene_id or "").strip()

    db_payload = _load_db_episode_payload(project_dir, episode)
    if db_payload["beats"]:
        source = "db"
        beats = [
            beat
            for beat in db_payload["beats"]
            if not scene_id or str(beat.get("scene_id") or "").strip() == scene_id
        ]
    else:
        source = "blocking_json"
        beats = _list_blocking_file_beats(blockings_dir, scene_id)

    beats.sort(key=lambda b: (_beat_order(b), _beat_number(b["beat"])))
    if args.beat is not None:
        beats = [b for b in beats if _beat_number(b["beat"]) == args.beat]
    elif args.from_beat is not None:
        start_order = next(
            (_beat_order(b) for b in beats if _beat_number(b["beat"]) == args.from_beat),
            int(args.from_beat) * 10,
        )
        beats = [b for b in beats if _beat_order(b) >= start_order]
    elif args.beats:
        try:
            wanted = {int(item.strip()) for item in args.beats.split(",") if item.strip()}
        except ValueError as exc:
            _fail(f"--beats must be comma-separated integers: {exc}")
        beats = [b for b in beats if _beat_number(b["beat"]) in wanted]
    print(
        json.dumps(
            {
                "ok": True,
                "source": source,
                "scene_id": scene_id,
                "episode": episode,
                "db_path": db_payload.get("db_path", ""),
                "beats": [_scene_beat_summary(beat) for beat in beats],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_commit_beat_overlay(args: argparse.Namespace) -> None:
    from novelvideo.director_world.paths import director_blocking_ref_path
    from novelvideo.director_world.store import load_beat_blocking, save_beat_blocking

    blockings_dir = _local_path(args.blockings_dir)
    if not blockings_dir.exists():
        _fail(f"blockings dir not found: {blockings_dir}")
    project_dir = _project_dir_from_blockings_dir(blockings_dir)
    episode = args.episode or _episode_from_blockings_dir(blockings_dir)
    beat_num = int(args.beat)

    data = _query_state(args.base_url, args.session)
    record = data.get("record") or {}
    state = record.get("state") or {}
    if not state:
        _fail("no editor state record yet (enable AI Director first)")

    existing = load_beat_blocking(project_dir, episode, beat_num) or {}
    db_beat = _load_db_beat_payload(project_dir, episode, beat_num) or {}
    camera = state.get("camera") or {}
    if not camera:
        _fail("current editor state has no camera")

    shot_name = args.save_shot_name or f"ep{episode:02d}_b{beat_num:02d}_ai"
    shot = _camera_with_name(camera, shot_name)
    actors = list(state.get("actors") or [])
    props = list(state.get("props") or [])
    scene_id = args.scene_id or existing.get("scene_id") or db_beat.get("scene_id") or ""
    beat_context = existing.get("beat_context") or db_beat.get("beat_context") or {}

    command_log = list(existing.get("director_command_log") or [])
    command_log.append(
        {
            "index": len(command_log) + 1,
            "source": "director_skill",
            "op": "commit_beat_overlay",
            "payload": {
                "session_id": args.session,
                "beat": beat_num,
                "save_shot_name": shot_name,
            },
        }
    )

    payload = {
        **existing,
        "schema_version": existing.get("schema_version") or "minecraft_beat_overlay_v0",
        "scene_id": scene_id,
        "display_name": existing.get("display_name") or scene_id,
        "editor_scope": "beat",
        "episode": str(episode),
        "beat": str(beat_num),
        "base_world_role": "scene_world_base",
        "overlay_role": "beat_blocking_overlay",
        "palette": {
            **(existing.get("palette") or {}),
            **_overlay_marker_palette(actors, props),
        },
        "actors": actors,
        "props": props,
        "director_shots": [shot],
        "camera_presets": [shot],
        "current_camera": shot,
        "beat_context": beat_context,
        "director_command_log": command_log,
        "layer_contract": existing.get("layer_contract")
        or {
            "world_3d": (
                "当前场景 world.json 决定固定空间和固定场景物；"
                "本 overlay 只决定本 beat 的人物、全局资产道具和镜头。"
            ),
            "sketch": "决定表演动作、坐姿细节、手势、表情和动作线，但不能改根位置/镜头。",
            "render": "决定材质、上色、光照和最终美术风格，但不能改布局/动作。",
        },
        "notes": "Beat overlay committed from live AI Director state.",
    }
    overlay_path = save_beat_blocking(project_dir, episode, beat_num, payload)

    render_path = director_blocking_ref_path(project_dir, episode, beat_num)
    render_saved = _decode_render_to_png(record.get("render_base64") or "", render_path)
    print(
        json.dumps(
            {
                "ok": True,
                "beat_overlay": str(overlay_path),
                "director_blocking_ref": str(render_path) if render_saved else "",
                "render_saved": render_saved,
                "scene_id": scene_id,
                "episode": episode,
                "beat": beat_num,
                "actors": len(actors),
                "props": len(props),
                "shot": shot_name,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_place_actors_from_beat(args: argparse.Namespace) -> None:
    """Read DB beat roster and push actor placement commands.

    `--seat-override` lets the AI director adjust seats for cinema reasons while
    keeping marker_color/identity from DB. Existing beat_NN.json is only used as
    a placement overlay if it already exists.

    Replaces the error-prone "AI hand-crafts payload from visual_description".
    """
    from novelvideo.director_world.store import load_beat_blocking

    blockings_dir = _local_path(args.blockings_dir)
    project_dir = _project_dir_from_blockings_dir(blockings_dir)
    episode = args.episode or _episode_from_blockings_dir(blockings_dir)
    beat_num = int(args.beat)

    db_beat = _load_db_beat_payload(project_dir, episode, beat_num)
    blocking = load_beat_blocking(project_dir, episode, beat_num) or {}
    overlay_actors = list(
        blocking.get("actors") or (blocking.get("beat_context") or {}).get("actors") or []
    )
    overlay_by_identity = {
        str(actor.get("identity_id") or "").strip(): actor
        for actor in overlay_actors
        if str(actor.get("identity_id") or "").strip()
    }
    if db_beat:
        actors = []
        for db_actor in db_beat.get("actors") or []:
            identity_id = str(db_actor.get("identity_id") or "").strip()
            overlay_actor = overlay_by_identity.get(identity_id) or {}
            merged = dict(db_actor)
            for key in (
                "id",
                "actor_id",
                "type",
                "position",
                "yaw",
                "state",
                "attached_to",
                "seat_id",
            ):
                if overlay_actor.get(key) not in (None, "", []):
                    merged[key] = overlay_actor[key]
            if overlay_actor.get("marker_color"):
                merged["marker_color"] = overlay_actor["marker_color"]
            actors.append(merged)
        scene_id = db_beat.get("scene_id") or blocking.get("scene_id") or ""
        source = "db"
    elif overlay_actors:
        actors = overlay_actors
        scene_id = blocking.get("scene_id", "")
        source = "blocking_json"
    else:
        _fail(f"beat {beat_num} not found in DB or beat overlay")

    try:
        seat_overrides = json.loads(args.seat_override or "{}")
    except json.JSONDecodeError as exc:
        _fail(f"--seat-override must be valid JSON: {exc}")
    if not isinstance(seat_overrides, dict):
        _fail("--seat-override must be a JSON object {actor_id_or_identity_id: seat_id}")

    data = _query_state(args.base_url, args.session)
    state = (data.get("record") or {}).get("state") or {}
    current_by_identity = {
        str(actor.get("identity_id") or "").strip(): actor
        for actor in state.get("actors") or []
        if str(actor.get("identity_id") or "").strip()
    }

    commands: list[tuple[str, dict, dict]] = []
    skipped: list[dict] = []
    missing: list[str] = []
    for actor in actors:
        actor_id = (
            actor.get("id") or actor.get("actor_id") or f"actor_{actor.get('identity_id','x')}"
        )
        identity_id = actor.get("identity_id") or ""
        marker_color = actor.get("marker_color") or ""
        roster_seat = actor.get("attached_to") or actor.get("seat_id") or ""

        # Override key matches identity_id first (more stable), then actor_id.
        override_seat = seat_overrides.get(identity_id) or seat_overrides.get(actor_id)
        seat_id = override_seat or roster_seat
        from_override = bool(override_seat)

        if seat_id and str(seat_id).startswith(("seat:", "bench:")):
            payload = {
                "actor_id": actor_id,
                "identity_id": identity_id,
                "seat_id": seat_id,
            }
            if marker_color:
                payload["marker_color"] = marker_color
            op = "place_actor_at_seat"
        else:
            position = actor.get("position")
            if position in (None, "", []):
                current_actor = current_by_identity.get(identity_id)
                if current_actor and not from_override:
                    skipped.append(
                        {
                            "actor_id": actor_id,
                            "identity_id": identity_id,
                            "reason": "already_in_live_stage",
                        }
                    )
                    continue
                missing.append(identity_id or actor_id)
                continue
            payload = {
                "actor_id": actor_id,
                "identity_id": identity_id,
                "position": list(position),
                "yaw": actor.get("yaw", 0.0),
            }
            if marker_color:
                payload["marker_color"] = marker_color
            op = "place_actor_at_position"
        commands.append(
            (
                op,
                payload,
                {
                    "actor_id": actor_id,
                    "op": op,
                    "seat_id": seat_id or None,
                    "marker_color": marker_color,
                    "from_override": from_override,
                    "roster_seat": roster_seat or None,
                },
            )
        )

    if missing:
        _fail(
            "no placement available for identities: "
            + ", ".join(missing)
            + "; keep existing live stage placement, commit a beat overlay, or pass --seat-override"
        )

    pushed: list[dict] = []
    for op, payload, meta in commands:
        result = _push_command(args.base_url, args.session, op, payload)
        pushed.append(
            {
                **meta,
                "ok": _push_result_ok(result),
                "queued": bool(result.get("ok")),
                "handler_ok": result.get("handler_ok"),
                "frame_error": result.get("frame_error"),
                "frame_timeout": result.get("frame_timeout"),
            }
        )
    failures = [item for item in pushed if not item.get("ok")]

    print(
        json.dumps(
            {
                "ok": not failures,
                "beat": beat_num,
                "episode": episode,
                "scene_id": scene_id,
                "source": source,
                "placed": len(pushed),
                "failed": len(failures),
                "skipped": skipped,
                "actors": pushed,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if failures:
        sys.exit(1)


def cmd_pause(args: argparse.Namespace) -> None:
    try:
        resp = httpx.post(
            f"{args.base_url}/__dramaclaw/director-pause",
            json={"session_id": args.session, "paused": args.resume is False},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        _fail(f"pause toggle failed: {exc}")
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Director Skill HTTP CLI for the AI agent.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="导演台 viewer 地址（缺省读 DIRECTOR_VIEWER_URL；不兜底本机地址）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_catalog = sub.add_parser("catalog", help="Dump SceneCatalog JSON for a world.json")
    p_catalog.add_argument("--world", required=True)
    p_catalog.set_defaults(func=cmd_catalog)

    p_query = sub.add_parser("query", help="GET current editor state for a session")
    p_query.add_argument("--session", required=True)
    p_query.add_argument("--frame-id", default="")
    p_query.set_defaults(func=cmd_query)

    p_push = sub.add_parser("push", help="POST a director command to the editor")
    p_push.add_argument("--session", required=True)
    p_push.add_argument("--op", required=True)
    p_push.add_argument("--payload", default="{}", help="JSON payload string")
    p_push.add_argument("--frame-id", default="")
    p_push.set_defaults(func=cmd_push)

    p_save = sub.add_parser(
        "save-render", help="Save the latest (or specific frame) flat-control PNG to disk"
    )
    p_save.add_argument("--session", required=True)
    p_save.add_argument("--frame-id", default="")
    p_save.add_argument("--output", required=True, help="Output PNG path")
    p_save.set_defaults(func=cmd_save_render)

    p_list = sub.add_parser(
        "list-scene-beats",
        help="List beats in a project/episode that share a given scene_id, sorted by beat number",
    )
    p_list.add_argument(
        "--blockings-dir", required=True, help="e.g. output/<project>/director_blockings/ep001"
    )
    p_list.add_argument("--scene-id", default="", help="Filter by scene_id (empty = list all)")
    beat_filter = p_list.add_mutually_exclusive_group()
    beat_filter.add_argument("--beat", type=int, default=None, help="List one slate beat only")
    beat_filter.add_argument(
        "--from-beat", type=int, default=None, help="List beats from this beat onward"
    )
    beat_filter.add_argument("--beats", default="", help="Comma-separated pickup beats, e.g. 3,5,7")
    p_list.set_defaults(func=cmd_list_scene_beats)

    for name, help_text in (
        ("commit-beat-overlay", "Commit current live state to beat_NN.json and director ref PNG"),
        ("save-beat-overlay", "Alias for commit-beat-overlay"),
    ):
        p_commit = sub.add_parser(name, help=help_text)
        p_commit.add_argument("--session", required=True)
        p_commit.add_argument(
            "--blockings-dir",
            required=True,
            help="e.g. output/<project>/director_blockings/ep001",
        )
        p_commit.add_argument("--beat", type=int, required=True)
        p_commit.add_argument("--episode", type=int, default=0)
        p_commit.add_argument("--scene-id", default="")
        p_commit.add_argument("--save-shot-name", default="")
        p_commit.set_defaults(func=cmd_commit_beat_overlay)

    p_place = sub.add_parser(
        "place-actors-from-beat",
        help="Read DB beat roster and push place_actor commands with marker colors."
        " Existing beat_NN.json is used only as a placement overlay. Use this on"
        " cold start instead of hand-crafting place_actor payloads.",
    )
    p_place.add_argument("--session", required=True)
    p_place.add_argument("--blockings-dir", required=True)
    p_place.add_argument("--beat", type=int, required=True)
    p_place.add_argument("--episode", type=int, default=0)
    p_place.add_argument(
        "--seat-override",
        default="",
        help="JSON dict mapping identity_id (or actor_id) to a different seat_id, "
        'e.g. \'{"杜晨_中年时期":"seat:4,1,15"}\'. '
        "Use when narrative requires diagonal staging that roster does not provide.",
    )
    p_place.set_defaults(func=cmd_place_actors_from_beat)

    p_pause = sub.add_parser("pause", help="Pause or resume the AI Director session")
    p_pause.add_argument("--session", required=True)
    p_pause.add_argument("--resume", action="store_true", help="Resume instead of pause")
    p_pause.set_defaults(func=cmd_pause)

    args = parser.parse_args()
    args.base_url = str(args.base_url or _default_base_url()).rstrip("/")
    if not args.base_url and args.cmd != "catalog":
        _fail(
            "未指定导演台地址：请设 DIRECTOR_VIEWER_URL 环境变量，或给本命令传"
            " --base-url http://<viewer 主机>:<端口>（代码不兜底 127.0.0.1:9024）"
        )
    args.func(args)


if __name__ == "__main__":
    main()
