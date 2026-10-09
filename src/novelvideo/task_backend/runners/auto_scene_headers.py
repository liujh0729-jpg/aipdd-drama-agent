"""Celery/inline runner for automatic scene-header completion.

Consumes the "default text LLM" slot (via ``scene_header_autogen``) to turn a
headerless drama screenplay into an enhanced text that carries canonical scene
headers, so the import guardrail can accept it. The original upload is never
modified; the enhanced text is written alongside it under ``uploads/``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from novelvideo.model_gateway_runtime import model_gateway_scope_for_runner
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager


def run_auto_scene_headers(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any] | None:
    with model_gateway_scope_for_runner(envelope):
        return asyncio.run(
            await_envelope_with_cancel_watch(
                _run_auto_scene_headers(envelope, ctx),
                envelope,
                task_type="auto_scene_headers",
            )
        )


async def _run_auto_scene_headers(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    from novelvideo.utils.document_parsers import load_novel_text
    from novelvideo.utils.scene_header_autogen import autogen_scene_headers

    payload = envelope.get("payload") or {}
    novel_path = Path(str(payload["novel_path"]))
    manager = get_task_manager()

    def on_chapter_done(done: int, total: int, _message: str) -> None:
        progress = done / total if total else 1.0
        manager.update_progress_for_project(
            ctx,
            "auto_scene_headers",
            0,
            progress=progress,
            current_task=f"场景头自动补全：{done}/{total} 章",
        )

    source_text = load_novel_text(novel_path)
    result = await autogen_scene_headers(source_text, on_chapter_done=on_chapter_done)

    uploads_dir = novel_path.parent
    scenes_name = f"{novel_path.stem}.scenes.txt"
    scenes_path = uploads_dir / scenes_name
    scenes_path.write_text(result.text, encoding="utf-8")

    # Persist the enhanced filename so the ingest page can resume the confirm /
    # re-detect flow without forcing another upload.
    from novelvideo.project_config import save_project_config_in_state_dir

    save_project_config_in_state_dir(
        ctx.state_dir, config={"auto_scene_headers_filename": scenes_name}
    )

    return {
        "filename": scenes_name,
        "chapters_processed": result.chapters_processed,
        "scenes_generated": result.scenes_generated,
        "fallback_chapters": result.fallback_chapters,
        "detected_headers": result.detected_headers,
        "standard_headers": result.standard_headers,
        "scene_header_status": result.scene_header_status,
    }


register_project_task_runner("auto_scene_headers", run_auto_scene_headers)
