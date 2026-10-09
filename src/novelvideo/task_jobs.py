"""Helpers for skill-facing actor job orchestration via SQLite task_state."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from novelvideo.task_identity import task_config_scope
from novelvideo.task_state import get_task_manager
from novelvideo.verification.sketch_edit_execute import resolve_labels_jsonl
from novelvideo.verification.sketch_edit_label_validation import validate_labels_jsonl


def load_project_skill_env(project_dir: Path) -> dict[str, str]:
    """Load project-local skill env from .claude/settings.local.json or settings.json."""
    resolved = project_dir.expanduser().resolve()
    candidates = [
        resolved / ".claude" / "settings.local.json",
        resolved / ".claude" / "settings.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        env = payload.get("env")
        if isinstance(env, dict):
            return {str(k): str(v) for k, v in env.items() if v is not None}
    return {}

def resolve_user_project_id_from_context(project_dir: Path) -> tuple[str, str]:
    """Resolve username/project_id strictly from env or project-local settings."""
    fallback_env = load_project_skill_env(project_dir)
    username = (
        os.environ.get("SUPERTALE_USERNAME")
        or fallback_env.get("SUPERTALE_USERNAME")
        or ""
    ).strip()
    project_id = (
        os.environ.get("SUPERTALE_PROJECT_ID")
        or fallback_env.get("SUPERTALE_PROJECT_ID")
        or ""
    ).strip()
    if not username or not project_id:
        raise RuntimeError(
            "SUPERTALE_USERNAME / SUPERTALE_PROJECT_ID is not configured in env or "
            ".claude/settings.local.json"
        )
    return username, project_id


def resolve_project_id_from_context(project_dir: Path) -> str:
    """Resolve project_id strictly from env or project-local settings."""
    fallback_env = load_project_skill_env(project_dir)
    project_id = (
        os.environ.get("SUPERTALE_PROJECT_ID")
        or fallback_env.get("SUPERTALE_PROJECT_ID")
        or ""
    ).strip()
    if not project_id:
        raise RuntimeError(
            "SUPERTALE_PROJECT_ID is not configured in env or .claude/settings.local.json"
        )
    return project_id


def get_output_dir_for_project_dir(project_dir: Path) -> str:
    return str(project_dir.expanduser().resolve())


def task_state_to_dict(task_state: Any | None) -> dict[str, Any] | None:
    if task_state is None:
        return None
    return asdict(task_state)


def start_sketch_edit_execute_job(
    project_dir: Path,
    episode_num: int,
    config: dict[str, Any],
) -> dict[str, Any]:
    username, project = resolve_user_project_id_from_context(project_dir)
    labels_name = str(config.get("labels_name") or "labels.jsonl")
    labels_path = resolve_labels_jsonl(project_dir, episode_num, labels_name=labels_name)
    validate_labels_jsonl(labels_path)
    scope = task_config_scope("edit_execute", config)
    return {
        "ok": False,
        "error": "sketch edit execute 需要 project task backend（当前 runner: Celery）",
        "task_type": "sketch_edit_execute",
        "username": username,
        "project": project,
        "episode": episode_num,
        "scope": scope,
    }


def get_task_snapshot(
    *,
    task_type: str,
    username: str,
    project: str,
    episode: int,
    beat_num: int | None = None,
    scope: str | None = None,
) -> dict[str, Any] | None:
    manager = get_task_manager()
    task_state = manager.get_task(
        task_type,
        username,
        project,
        episode,
        beat_num=beat_num,
        scope=scope,
    )
    return task_state_to_dict(task_state)


def read_task_snapshot_or_raise(
    *,
    task_type: str,
    username: str,
    project: str,
    episode: int,
    beat_num: int | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    snapshot = get_task_snapshot(
        task_type=task_type,
        username=username,
        project=project,
        episode=episode,
        beat_num=beat_num,
        scope=scope,
    )
    if snapshot is None:
        raise FileNotFoundError(
            f"Task not found: {task_type}/{username}/{project}/ep{episode} scope={scope or '-'}"
        )
    return snapshot


def read_task_result(
    *,
    task_type: str,
    username: str,
    project: str,
    episode: int,
    beat_num: int | None = None,
    scope: str | None = None,
    require_terminal: bool = False,
) -> dict[str, Any]:
    snapshot = read_task_snapshot_or_raise(
        task_type=task_type,
        username=username,
        project=project,
        episode=episode,
        beat_num=beat_num,
        scope=scope,
    )
    if require_terminal and snapshot.get("status") not in {"completed", "failed"}:
        raise RuntimeError(
            f"Task not finished yet: {task_type}/{project}/ep{episode} "
            f"status={snapshot.get('status') or '-'}"
        )
    return snapshot


def wait_for_task_terminal(
    *,
    task_type: str,
    username: str,
    project: str,
    episode: int,
    beat_num: int | None = None,
    scope: str | None = None,
    timeout_seconds: float = 900.0,
    poll_interval: float = 2.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    while True:
        snapshot = get_task_snapshot(
            task_type=task_type,
            username=username,
            project=project,
            episode=episode,
            beat_num=beat_num,
            scope=scope,
        )
        if snapshot is not None and snapshot.get("status") in {"completed", "failed"}:
            return snapshot
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Timed out waiting for {task_type}/{project}/ep{episode} scope={scope or '-'}"
            )
        time.sleep(max(poll_interval, 0.2))
