"""Celery/inline runner for full novel-to-screenplay adaptation.

[aipdd fork delta] Consumes the director screenwriter agent to turn an imported
story (project story source) into a multi-episode standard screenplay:

1. plan episode outlines (one LLM pass over the whole story),
2. write each episode as a standard screenplay (one LLM pass per episode),
3. persist the joined screenplay under the project ``uploads/`` folder so the
   ingest page (「剧本」 module) can preview and import it right away.

Progress is reported per phase / per episode so the story page can poll it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from novelvideo.i18n_message import MessageLike, lmsg
from novelvideo.model_gateway_runtime import model_gateway_scope_for_runner
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager


def run_novel_to_screenplay(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any] | None:
    with model_gateway_scope_for_runner(envelope):
        return asyncio.run(
            await_envelope_with_cancel_watch(
                _run_novel_to_screenplay(envelope, ctx),
                envelope,
                task_type="novel_to_screenplay",
            )
        )


def _tail_excerpt(text: str, limit: int = 300) -> str:
    """Take the last ~``limit`` characters on line boundaries for continuity."""
    lines = [line for line in (text or "").strip().splitlines() if line.strip()]
    picked: list[str] = []
    size = 0
    for line in reversed(lines):
        picked.append(line)
        size += len(line)
        if size >= limit:
            break
    return "\n".join(reversed(picked))


async def _run_novel_to_screenplay(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    from novelvideo.agents.director_screenwriter import (
        plan_novel_episodes,
        write_novel_episode_screenplay,
    )
    from novelvideo.api.chapter_preview import build_chapter_preview, load_novel_text
    from novelvideo.project_config import save_project_config_in_state_dir

    payload = envelope.get("payload") or {}
    story_path = Path(str(payload["story_path"]))
    output_path = Path(str(payload["output_path"]))
    episode_count = int(payload.get("episode_count") or 10)
    story_style = str(payload.get("story_style") or "")
    manager = get_task_manager()

    def update_progress(
        progress: float, task: MessageLike, *, logs: list[MessageLike] | None = None
    ) -> None:
        manager.update_progress_for_project(
            ctx,
            "novel_to_screenplay",
            0,
            progress=progress,
            current_task=task,
            logs=logs or [],
        )

    update_progress(0.02, lmsg("tasks.progress.screenplay.readingStory", "读取故事源..."))
    novel_text = load_novel_text(story_path).strip()
    if not novel_text:
        raise ValueError("故事源为空，无法生成剧本")

    update_progress(0.05, lmsg("tasks.progress.screenplay.planningOutline", "拆解分集大纲..."))
    plan = await plan_novel_episodes(
        novel_text,
        project_title=ctx.project_name,
        episode_count=episode_count,
        story_style=story_style,
    )
    total = max(1, len(plan.episodes))
    update_progress(
        0.12,
        lmsg(
            "tasks.progress.screenplay.outlineComplete",
            f"分集大纲完成：共 {total} 集，开始逐集生成剧本...",
            episodeCount=total,
        ),
        logs=[
            lmsg(
                "tasks.log.screenplay.outlinePlan",
                f"分集大纲：共 {total} 集（计划 {episode_count} 集）",
                episodeCount=total,
                plannedEpisodes=episode_count,
            )
        ],
    )

    parts: list[str] = []
    prev_tail = ""
    for index, item in enumerate(plan.episodes, start=1):
        update_progress(
            0.12 + ((index - 1) / total) * 0.80,
            lmsg(
                "tasks.progress.screenplay.writingEpisode",
                f"生成第 {index}/{total} 集剧本...",
                index=index,
                total=total,
            ),
        )
        text = await write_novel_episode_screenplay(
            project_title=ctx.project_name,
            episode_number=index,
            episode_count=total,
            episode_title=item.title,
            episode_brief=item.brief,
            episode_hook=item.hook,
            protagonist_name=plan.protagonist,
            story_style=story_style,
            prev_tail=prev_tail,
        )
        parts.append(text.strip())
        prev_tail = _tail_excerpt(text)
        update_progress(
            0.12 + (index / total) * 0.80,
            lmsg(
                "tasks.progress.screenplay.episodeDone",
                f"已完成第 {index}/{total} 集",
                index=index,
                total=total,
            ),
            logs=[
                lmsg(
                    "tasks.log.screenplay.episodeChars",
                    f"第 {index} 集完成：{len(text)} 字",
                    index=index,
                    charCount=len(text),
                )
            ],
        )

    screenplay = "\n\n\n".join(parts).strip() + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(screenplay, encoding="utf-8")

    preview = build_chapter_preview(screenplay)
    detected = int(preview.get("count") or 0)
    if detected == 0:
        update_progress(
            0.97,
            lmsg(
                "tasks.progress.screenplay.noMarkers",
                "剧本已生成，但未检测到分集标记，请在「剧本」模块复核",
            ),
            logs=[
                lmsg(
                    "tasks.log.screenplay.noMarkersWarn",
                    "警告：生成文本未检测到“第N集”分集标记，导入预览可能为空",
                )
            ],
        )

    save_project_config_in_state_dir(
        ctx.state_dir,
        config={
            "novel_screenplay_filename": output_path.name,
            "novel_screenplay_episodes": total,
        },
    )
    update_progress(
        1.0,
        lmsg(
            "tasks.progress.screenplay.saved",
            f"剧本已生成并保存到剧本库：{output_path.name}",
            filename=output_path.name,
        ),
    )

    return {
        "filename": output_path.name,
        "episodes": total,
        "requested_episodes": episode_count,
        "chars": len(screenplay),
        "detected_chapters": detected,
        "protagonist": plan.protagonist,
        "logline": plan.logline,
    }


register_project_task_runner("novel_to_screenplay", run_novel_to_screenplay)