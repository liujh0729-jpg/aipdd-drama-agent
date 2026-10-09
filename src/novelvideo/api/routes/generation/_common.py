"""generation._common 子模块（自 api/routes/generation.py 拆分）。"""

import json
import logging
import os
import re
from contextlib import suppress
from pathlib import Path
from typing import Any

from fastapi.responses import FileResponse

from novelvideo.api.routes import generation as _generation
from novelvideo.api.schemas import PlanEntryOut
from novelvideo.task_backend.limit_logging import log_task_limit_rejection
from novelvideo.task_backend.limits import ChannelTaskLimitExceeded
from novelvideo.task_backend.limits import ProjectTaskLimitExceeded
from novelvideo.task_backend.limits import ProjectUserTaskLimitExceeded
from novelvideo.task_backend.limits import UserTaskLimitExceeded
from novelvideo.utils.path_resolver import PathResolver



class _TemporaryFileResponse(FileResponse):
    """Delete the response file after ASGI delivery, including failed or cancelled sends."""

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with suppress(OSError):
                Path(self.path).unlink(missing_ok=True)


logger = logging.getLogger(__name__)


def _single_render_mode_from_sketch(
    output_dir: str,
    episode: int,
    beat_indices: list[int],
) -> str | None:
    """Choose a single-render mode from the canonical upstream sketch."""
    if len(beat_indices) != 1:
        return None

    from PIL import Image

    sketch_path = PathResolver(output_dir, episode).sketch(int(beat_indices[0]))
    try:
        with Image.open(sketch_path) as image:
            width, height = image.size
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None

    ratio = width / height
    return "1x1_16-9" if abs(ratio - 16 / 9) < abs(ratio - 2 / 3) else "1x1_2-3"


async def _resolve_generation_project(project: str, user: dict, required_role: str = "editor"):
    return await _generation.resolve_project_scope(project, user, required_role=required_role)


def _requester_user_id(resolved: Any, user: dict) -> str:
    ctx = getattr(resolved, "ctx", None)
    return str(
        getattr(ctx, "requester_user_id", "")
        or user.get("id")
        or user.get("user_id")
        or user.get("username")
        or ""
    )


def _color_assignment_requires_full_sketch_clean(
    previous: dict[str, str] | None,
    current: dict[str, str] | None,
) -> bool:
    """Return whether recoloring invalidates all existing sketches."""
    current_colors = {
        str(key): str(value)
        for key, value in (current or {}).items()
        if str(key).strip() and str(value).strip()
    }
    if not current_colors:
        return False

    previous_colors = {
        str(key): str(value)
        for key, value in (previous or {}).items()
        if str(key).strip() and str(value).strip()
    }
    if not previous_colors:
        return True

    for key, old_value in previous_colors.items():
        new_value = current_colors.get(key)
        if new_value is not None and new_value != old_value:
            return True
    return False


def normalize_beat_indices(beat_indices: list[int]) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for beat_index in beat_indices:
        value = int(beat_index)
        if value in seen:
            continue
        normalized.append(value)
        seen.add(value)
    return normalized


def validate_beat_indices(all_beats: list[dict], beat_indices: list[int]) -> list[int]:
    valid_beat_numbers = {int(beat.get("beat_number", 0) or 0) for beat in all_beats}
    return [
        int(beat_index) for beat_index in beat_indices if int(beat_index) not in valid_beat_numbers
    ]


def _render_plan_feature_disabled() -> bool:
    return os.getenv("DISABLE_RENDER_PLAN_V2") in {"1", "true", "True", "yes"}


def _resolve_render_image_selection(
    project_config: dict,
    requested_selection: str | None = None,
) -> str:
    from novelvideo.config import (
        DEFAULT_RENDER_IMAGE_SELECTION,
        normalize_image_generation_selection,
    )

    return normalize_image_generation_selection(
        requested_selection or project_config.get("render_image_selection"),
        fallback=DEFAULT_RENDER_IMAGE_SELECTION,
    )


def _resolve_sketch_image_selection(
    project_config: dict,
    requested_selection: str | None = None,
) -> str:
    from novelvideo.config import (
        DEFAULT_SKETCH_IMAGE_SELECTION,
        normalize_image_generation_selection,
    )

    return normalize_image_generation_selection(
        requested_selection or project_config.get("sketch_image_selection"),
        fallback=DEFAULT_SKETCH_IMAGE_SELECTION,
    )


def _resolve_render_bool_setting(
    project_config: dict,
    key: str,
    requested_value: bool | None,
    default: bool,
) -> bool:
    if requested_value is not None:
        return bool(requested_value)
    return bool(project_config.get(key, default))


def _render_settings_payload(username: str, project: str) -> dict:
    from novelvideo.config import image_generation_selection_options

    project_config = _generation.load_project_config(username, project)
    return {
        "render_image_selection": _resolve_render_image_selection(project_config),
        "options": image_generation_selection_options(),
        "sketch_aspect_padding": _resolve_render_bool_setting(
            project_config,
            "sketch_aspect_padding",
            None,
            True,
        ),
    }


def _sketch_settings_payload(username: str, project: str) -> dict:
    from novelvideo.config import image_generation_selection_options

    project_config = _generation.load_project_config(username, project)
    return {
        "sketch_image_selection": _resolve_sketch_image_selection(project_config),
        "options": image_generation_selection_options(),
    }


def _plan_entry_to_dict(entry: Any) -> dict:
    if isinstance(entry, dict):
        beat_numbers = entry.get("beat_numbers") or []
        reasons = entry.get("reasons") or []
        warnings = entry.get("warnings") or []
        return PlanEntryOut(
            mode_key=entry.get("mode_key", ""),
            rows=int(entry.get("rows", 0) or 0),
            cols=int(entry.get("cols", 0) or 0),
            beat_numbers=[int(beat) for beat in beat_numbers],
            location=str(entry.get("location") or ""),
            padding_count=int(entry.get("padding_count") or 0),
            reasons=[str(reason) for reason in reasons],
            warnings=[str(warning) for warning in warnings],
        ).model_dump()

    return PlanEntryOut(
        mode_key=entry.mode_key,
        rows=entry.rows,
        cols=entry.cols,
        beat_numbers=list(entry.beat_numbers),
        location=entry.location,
        padding_count=entry.padding_count,
        reasons=list(entry.reasons),
        warnings=list(entry.warnings),
    ).model_dump()


def _plan_to_dicts(plan) -> list[dict]:
    return [_plan_entry_to_dict(entry) for entry in plan]


def _parse_grid_beat_numbers(raw: str | None) -> list[int]:
    if not raw:
        return []
    text = raw.strip()
    if not text:
        return []
    if text.startswith("["):
        parsed = json.loads(text)
        values = parsed if isinstance(parsed, list) else []
    else:
        values = re.split(r"[,;\s]+", text)
    beat_numbers: list[int] = []
    seen: set[int] = set()
    for value in values:
        if value in ("", None):
            continue
        beat_num = int(value)
        if beat_num <= 0 or beat_num in seen:
            continue
        beat_numbers.append(beat_num)
        seen.add(beat_num)
    return beat_numbers


def _safe_grid_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.:-]+", "_", value.strip())
    return token.strip("._-") or "grid"


def _uploaded_grid_filename(
    grid_type: str, mode_key: str, beat_numbers: list[int], ext: str
) -> str:
    beats_slug = "-".join(str(beat) for beat in beat_numbers) or "manual"
    return (
        f"{_safe_grid_token(grid_type)}_{_safe_grid_token(mode_key)}_"
        f"{beats_slug}_grid_upload.{ext.lstrip('.')}"
    )


def _safe_grids_file(grids_dir: Path, relative_path: str) -> Path | None:
    if not relative_path:
        return None
    try:
        candidate = (grids_dir / relative_path).resolve()
        root = grids_dir.resolve()
    except Exception:
        return None
    if root == candidate or root not in candidate.parents:
        return None
    return candidate


def _find_pool_grid_entry(
    pool: Any,
    *,
    grid_type: str,
    mode_key: str | None,
    beat_numbers: list[int],
    grid_index: int,
) -> Any | None:
    if pool is None:
        return None
    if mode_key and beat_numbers:
        entry = pool.find_grid(grid_type, mode_key, beat_numbers)
        if entry is not None:
            return entry

    image_grid_paths = {
        img.grid_path
        for img in getattr(pool, "images", [])
        if img.type == grid_type
        and img.grid_index == grid_index
        and (not beat_numbers or img.original_beat in beat_numbers)
        and img.grid_path
    }
    for entry in getattr(pool, "grids", []):
        if entry.type != grid_type:
            continue
        if mode_key and entry.mode_key != mode_key:
            continue
        if beat_numbers and set(entry.beat_nums) != set(beat_numbers):
            continue
        if not image_grid_paths or entry.grid_path in image_grid_paths:
            return entry
    return None


_FANOUT_ACTIVE_LIMIT_ERRORS = (
    ChannelTaskLimitExceeded,
    UserTaskLimitExceeded,
    ProjectTaskLimitExceeded,
    ProjectUserTaskLimitExceeded,
)


_FanoutLimitError = (
    ChannelTaskLimitExceeded
    | UserTaskLimitExceeded
    | ProjectTaskLimitExceeded
    | ProjectUserTaskLimitExceeded
)


def _task_limit_reason(exc: _FanoutLimitError) -> str:
    """撞闸异常 → 作用域词。

    分支必须与 ``api/app.py`` 的 ``limit_scope`` 逐字同源（``:226`` 渠道闸 /
    ``:206`` 与 ``:271`` 人闸）—— 两侧同算法是跨 EU 的漂移探测器，故三处扇出
    循环共用这一个 helper，而不是各自内联一份。
    """
    if isinstance(exc, ProjectTaskLimitExceeded):
        return "project"
    if isinstance(exc, ChannelTaskLimitExceeded):
        return "platform" if exc.scope_kind == "platform" else "channel"
    if isinstance(exc, (ProjectUserTaskLimitExceeded, UserTaskLimitExceeded)):
        return "user"
    # pragma: no cover - the precise catch tuple keeps this unreachable
    raise TypeError(f"unsupported fanout task-limit exception: {type(exc).__name__}")


def _log_partial_dispatch_rejection(exc: _FanoutLimitError) -> None:
    """已投出 k > 0 个后撞闸：异常就地被吞、响应是 200，走不到 ``api/app.py``
    的 handler，日志得在这里补 —— 否则「投了一半撞闸」在面板上不存在。

    一次撞闸记一行（按捕获到的那个异常），不按后面合成的每条 ``rejected`` 记。
    """
    log_task_limit_rejection(exc, limit_scope=_task_limit_reason(exc))


def _task_limit_rejection(scope: str, exc: _FanoutLimitError) -> dict[str, Any]:
    """把撞闸异常翻成扇出响应里的一条 ``rejected``（M8 §8.2 冻结形状）。"""
    return {
        "scope": scope,
        "reason": _task_limit_reason(exc),
        "limit": exc.limit,
        "active": exc.active,
    }


def _custom_render_plan_error(plan: list[Any], beat_indices: list[int]) -> str | None:
    flat: list[int] = []
    seen: set[int] = set()
    for entry in plan:
        beat_numbers = [int(beat) for beat in getattr(entry, "beat_numbers", [])]
        if not beat_numbers:
            return "empty_grid"
        if int(getattr(entry, "rows", 0)) * int(getattr(entry, "cols", 0)) < len(beat_numbers):
            return "grid_capacity"
        for beat in beat_numbers:
            if beat in seen:
                return "duplicate_beat"
            seen.add(beat)
            flat.append(beat)
    if set(flat) != set(beat_indices) or len(flat) != len(beat_indices):
        return "beat_mismatch"
    return None
