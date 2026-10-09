"""freezone.text_ops 子模块（自 api/routes/freezone.py 拆分）。"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone as _freezone
from novelvideo.api.schemas import FreezoneJobAcceptedResponse
from novelvideo.api.schemas import FreezoneStoryScriptCharacterRef
from novelvideo.api.schemas import FreezoneStoryScriptGenerateRequest
from novelvideo.api.schemas import FreezoneTextGenerateRequest
from novelvideo.api.schemas import FreezoneTextTranslateRequest
from novelvideo.api.schemas import FreezoneVideoScriptReverseRequest
from novelvideo.freezone.history import append_generation_history
from novelvideo.freezone.history import build_node_history_record
from novelvideo.freezone.image_node import reverse_prompt_from_image
from novelvideo.freezone.paths import outputs_dir
from novelvideo.freezone.route_helpers import accepted_job_response as _accepted_job_response
from novelvideo.freezone.text_node import bind_story_script_assets
from novelvideo.freezone.text_node import generate_freezone_story_script
from novelvideo.freezone.text_node import generate_freezone_story_script_with_vision
from novelvideo.freezone.text_node import generate_freezone_text
from novelvideo.freezone.text_node import translate_freezone_text
from novelvideo.project_context import ProjectContext
from novelvideo.task_identity import project_task_state_key
from novelvideo.task_identity import task_state_key

from ._common import (TAG_FREEZONE_TEXT, logger)

router = APIRouter()


# ============================================================
# 文本工具：中英互译
# ============================================================


def _text_translate_output_path(project_dir: Path, job_id: str) -> Path:
    return outputs_dir(project_dir, "freezone_text_translate") / f"{job_id}.json"


def _text_generate_output_path(project_dir: Path, job_id: str) -> Path:
    return outputs_dir(project_dir, "freezone_text_generate") / f"{job_id}.json"


def _freezone_history_preview(text: str, limit: int = 240) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _record_freezone_node_history(
    *,
    ctx: ProjectContext | None = None,
    project_dir: Path,
    canvas_id: str | None,
    node_id: str | None,
    task_type: str,
    username: str,
    project: str,
    job_id: str,
    status: str,
    media_type: str,
    result: dict | None = None,
    error: str | None = None,
    prompt: str | None = None,
    **extra,
) -> dict | None:
    if not node_id:
        return None
    try:
        task_key = (
            project_task_state_key(task_type, ctx.project_id, 0, scope=job_id)
            if ctx is not None
            else task_state_key(task_type, username, project, episode=0, scope=job_id)
        )
        return append_generation_history(
            project_dir=project_dir,
            canvas_id=canvas_id or "default",
            node_id=node_id,
            record=build_node_history_record(
                task_type=task_type,
                job_id=job_id,
                task_key=task_key,
                status=status,
                media_type=media_type,
                result=result,
                error=error,
                prompt=prompt,
                extra=extra,
            ),
        )
    except Exception as exc:
        logger.warning("failed to record freezone node history: %s", exc)
        return None


def _start_freezone_text_translate_task(
    *,
    username: str,
    project: str,
    project_dir: Path,
    job_id: str,
    text: str,
    node_type: Literal["generic", "image", "video", "audio", "text"],
    canvas_id: str | None = None,
    node_id: str | None = None,
) -> None:
    task_type = "freezone_text_translate"
    task_manager = _freezone.get_task_manager()
    metadata = {
        "job_id": job_id,
        "canvas_id": canvas_id or "",
        "node_id": node_id or "",
        "node_type": node_type,
    }
    task_manager.create_task(
        task_type,
        username,
        project,
        episode=0,
        scope=job_id,
        status="starting",
        metadata=metadata,
    )

    async def _runner() -> None:
        logs = ["开始翻译文本"]
        try:
            task_manager.update_progress(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                progress=0.1,
                current_task="translating_text",
                logs=logs,
            )
            translated_text, source_language, target_language = await translate_freezone_text(
                text=text,
                node_type=node_type,
            )
            payload = {
                "translated_text": translated_text,
                "source_language": source_language,
                "target_language": target_language,
                "node_type": node_type,
            }
            out = _text_translate_output_path(project_dir, job_id)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            history_record = _record_freezone_node_history(
                project_dir=project_dir,
                canvas_id=canvas_id,
                node_id=node_id,
                task_type=task_type,
                username=username,
                project=project,
                job_id=job_id,
                status="completed",
                media_type="text",
                node_type=node_type,
                input_preview=_freezone_history_preview(text),
                prompt=text,
                result={"output_format": "json", **payload},
            )
            result = {"output_format": "json"}
            if history_record:
                result["generation_history_record"] = history_record
            task_manager.complete_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                result=result,
                current_task="completed",
                logs=["文本翻译完成"],
                metadata=metadata,
            )
        except Exception as exc:
            _record_freezone_node_history(
                project_dir=project_dir,
                canvas_id=canvas_id,
                node_id=node_id,
                task_type=task_type,
                username=username,
                project=project,
                job_id=job_id,
                status="failed",
                media_type="text",
                node_type=node_type,
                input_preview=_freezone_history_preview(text),
                prompt=text,
                error=str(exc),
            )
            task_manager.fail_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                error=str(exc),
                current_task="failed",
                logs=[f"错误: {exc}"],
                metadata=metadata,
            )

    asyncio.create_task(_runner())


def _start_freezone_text_generate_task(
    *,
    username: str,
    project: str,
    project_dir: Path,
    job_id: str,
    prompt: str,
    canvas_id: str | None = None,
    node_id: str | None = None,
) -> None:
    task_type = "freezone_text_generate"
    task_manager = _freezone.get_task_manager()
    metadata = {
        "job_id": job_id,
        "canvas_id": canvas_id or "",
        "node_id": node_id or "",
    }
    task_manager.create_task(
        task_type,
        username,
        project,
        episode=0,
        scope=job_id,
        status="starting",
        metadata=metadata,
    )

    async def _runner() -> None:
        try:
            task_manager.update_progress(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                progress=0.1,
                current_task="generating_text",
                logs=["开始生成文本"],
            )
            model, generated_text = await generate_freezone_text(prompt=prompt)
            payload = {"generated_text": generated_text, "model": model}
            out = _text_generate_output_path(project_dir, job_id)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            history_record = _record_freezone_node_history(
                project_dir=project_dir,
                canvas_id=canvas_id,
                node_id=node_id,
                task_type=task_type,
                username=username,
                project=project,
                job_id=job_id,
                status="completed",
                media_type="text",
                input_preview=_freezone_history_preview(prompt),
                prompt=prompt,
                model=model,
                result={"output_format": "json", **payload},
            )
            result = {"output_format": "json", **payload}
            if history_record:
                result["generation_history_record"] = history_record
            task_manager.complete_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                result=result,
                current_task="completed",
                logs=["文本生成完成"],
                metadata=metadata,
            )
        except Exception as exc:
            _record_freezone_node_history(
                project_dir=project_dir,
                canvas_id=canvas_id,
                node_id=node_id,
                task_type=task_type,
                username=username,
                project=project,
                job_id=job_id,
                status="failed",
                media_type="text",
                input_preview=_freezone_history_preview(prompt),
                prompt=prompt,
                error=str(exc),
            )
            task_manager.fail_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                error=str(exc),
                current_task="failed",
                logs=[f"错误: {exc}"],
                metadata=metadata,
            )

    asyncio.create_task(_runner())


@router.post(
    "/projects/{project}/freezone/text/generate",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_TEXT],
)
async def freezone_text_generate(
    project: str,
    body: FreezoneTextGenerateRequest,
    user: dict = Depends(get_api_user),
):
    """文本节点：根据用户要求生成可继续编辑的自由文本。"""
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(400, "prompt is required")

    try:
        job_id = _freezone._new_job_id()
        if ctx is not None:
            return await _freezone._enqueue_freezone_background_job(
                ctx=ctx,
                project_dir=project_dir,
                task_type="freezone_text_generate",
                job_id=job_id,
                payload={
                    "prompt": prompt,
                    "canvas_id": body.canvas_id or "",
                    "node_id": body.node_id or "",
                },
            )
        _freezone._start_freezone_text_generate_task(
            username=username,
            project=project_name,
            project_dir=project_dir,
            job_id=job_id,
            prompt=prompt,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
        )
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error("failed to start text generation task", exc)
        raise HTTPException(503, f"failed to start text generation task: {exc}") from exc

    return _accepted_job_response(
        task_type="freezone_text_generate",
        username=username,
        project=project_name,
        job_id=job_id,
    )


@router.post(
    "/projects/{project}/freezone/text/translate",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_TEXT],
)
async def freezone_text_translate(
    project: str,
    body: FreezoneTextTranslateRequest,
    user: dict = Depends(get_api_user),
):
    """文本工具：中英文互译，供各类节点编写提示词时直接调用。"""
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    if not body.text.strip():
        raise HTTPException(400, "text is required")

    try:
        job_id = _freezone._new_job_id()
        if ctx is not None:
            return await _freezone._enqueue_freezone_background_job(
                ctx=ctx,
                project_dir=project_dir,
                task_type="freezone_text_translate",
                job_id=job_id,
                payload={
                    "text": body.text,
                    "node_type": body.node_type,
                    "canvas_id": body.canvas_id or "",
                    "node_id": body.node_id or "",
                },
            )
        _freezone._start_freezone_text_translate_task(
            username=username,
            project=project_name,
            project_dir=project_dir,
            job_id=job_id,
            text=body.text,
            node_type=body.node_type,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
        )
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error("failed to start text translate task", exc)
        raise HTTPException(503, f"failed to start text translate task: {exc}") from exc

    return _accepted_job_response(
        task_type="freezone_text_translate",
        username=username,
        project=project_name,
        job_id=job_id,
    )


def _read_freezone_text_file(path: Path) -> str:
    """读取 Freezone 文本节点的源文本文件。"""
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise HTTPException(400, f"unsupported text encoding: {path.name}")


def _story_script_output_path(project_dir: Path, job_id: str) -> Path:
    return outputs_dir(project_dir, "freezone_story_script") / f"{job_id}.json"


def _image_reverse_prompt_output_path(project_dir: Path, job_id: str) -> Path:
    return outputs_dir(project_dir, "freezone_image_reverse_prompt") / f"{job_id}.json"


def _video_compose_output_path(project_dir: Path, job_id: str) -> Path:
    return outputs_dir(project_dir, "freezone_video_compose") / f"{job_id}.mp4"


def _video_erase_output_path(project_dir: Path, job_id: str) -> Path:
    return outputs_dir(project_dir, "freezone_video_erase") / f"{job_id}.mp4"


def _video_upscale_output_path(project_dir: Path, job_id: str) -> Path:
    return outputs_dir(project_dir, "freezone_video_upscale") / f"{job_id}.mp4"


def _audio_separate_audio_output_path(project_dir: Path, job_id: str) -> Path:
    output_dir = outputs_dir(project_dir, "freezone_audio_separate")
    audio_path = output_dir / f"{job_id}.mp3"
    legacy_path = output_dir / f"{job_id}.m4a"
    # 历史任务产出的是 .m4a，结果查询继续可用。
    return legacy_path if not audio_path.exists() and legacy_path.exists() else audio_path


def _audio_separate_mute_video_output_path(project_dir: Path, job_id: str) -> Path:
    return outputs_dir(project_dir, "freezone_audio_separate") / f"{job_id}_mute.mp4"


def _public_freezone_video_story_result(result: dict) -> dict:
    return {
        key: value for key, value in result.items() if key not in {"output_path", "frame_paths"}
    }


def _infer_image_to_3gs_scene_id(source_path: Path, project_dir: Path) -> str:
    """Best-effort label for Freezone image→3GS jobs; it no longer controls output path."""
    try:
        parts = source_path.resolve().relative_to(project_dir.resolve()).parts
    except ValueError:
        parts = source_path.parts
    for marker in ("scenes", "director_worlds"):
        if marker in parts:
            idx = parts.index(marker)
            if idx + 1 < len(parts):
                return str(parts[idx + 1]).strip()
    return source_path.stem or "freezone"


def _copy_image_matching_existing_target(source_path: Path, target: Path) -> dict:
    """Copy image to target, preserving the existing target canvas size when present.

    Freezone outputs may use a model-friendly ratio such as 2:3, while legacy
    NovelVideo beat sketches are often trimmed grid cells like 233x383.  On push
    back, keep the whole source image and letterbox it into the existing target
    dimensions instead of cropping.
    """
    if not target.exists():
        shutil.copy2(source_path, target)
        return {"adapted": False}

    from PIL import Image, ImageOps

    with Image.open(target) as target_img:
        target_size = target_img.size
        target_mode = target_img.mode
    with Image.open(source_path) as source_img:
        source = ImageOps.exif_transpose(source_img)
        source_size = source.size
        if source_size == target_size:
            shutil.copy2(source_path, target)
            return {
                "adapted": False,
                "source_size": list(source_size),
                "target_size": list(target_size),
            }

        if target_mode in {"RGBA", "LA"}:
            canvas_mode = "RGBA"
            background = (255, 255, 255, 0)
            source = source.convert("RGBA")
        else:
            canvas_mode = "RGB"
            background = (255, 255, 255)
            source = source.convert("RGB")

        fitted = ImageOps.contain(source, target_size, Image.Resampling.LANCZOS)
        canvas = Image.new(canvas_mode, target_size, background)
        offset = (
            (target_size[0] - fitted.size[0]) // 2,
            (target_size[1] - fitted.size[1]) // 2,
        )
        canvas.paste(fitted, offset, fitted if fitted.mode == "RGBA" else None)
        save_kwargs = {"format": "PNG"} if target.suffix.lower() == ".png" else {}
        canvas.save(target, **save_kwargs)
        return {
            "adapted": True,
            "source_size": list(source_size),
            "target_size": list(target_size),
            "fitted_size": list(fitted.size),
        }


def _resolve_story_script_character_refs(
    character_refs: list[FreezoneStoryScriptCharacterRef],
    project_dir: Path,
) -> tuple[list[dict], list[str]]:
    """把角色参考拆成「给模型的角色卡」和「本地图片路径」。

    外链或解析不出本地文件的图片仍然保留在角色卡里（回填 URL 时照样能用），
    只是不会作为附件送进视觉模型。
    """
    refs: list[dict] = []
    image_paths: list[str] = []
    for ref in character_refs:
        refs.append(ref.model_dump())
        image_url = (ref.image_url or "").strip()
        if not image_url:
            continue
        try:
            path = _freezone.resolve_static_url_to_path(image_url, project_dir)
        except ValueError:
            continue
        if path.exists():
            image_paths.append(path.as_posix())
    return refs, image_paths


def _start_freezone_story_script_task(
    *,
    username: str,
    project: str,
    project_dir: Path,
    job_id: str,
    source_text: str,
    prompt: str,
    model: str,
    canvas_id: str | None = None,
    node_id: str | None = None,
    character_refs: list[dict] | None = None,
    character_image_paths: list[str] | None = None,
) -> None:
    task_type = "freezone_story_script"
    task_manager = _freezone.get_task_manager()
    metadata = {
        "job_id": job_id,
        "canvas_id": canvas_id or "",
        "node_id": node_id or "",
        "model": model,
    }
    task_manager.create_task(
        task_type,
        username,
        project,
        episode=0,
        scope=job_id,
        status="starting",
        metadata=metadata,
    )

    async def _runner() -> None:
        logs = ["开始生成故事脚本"]
        try:
            task_manager.update_progress(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                progress=0.1,
                current_task="generating_story_script",
                logs=logs,
            )
            if character_image_paths:
                # 有角色参考图就走视觉模型，让角色描述照着图写；
                # 纯文本模式仍走原来的 story-script 文本别名。
                data = await generate_freezone_story_script_with_vision(
                    character_image_paths=character_image_paths,
                    source_text=source_text,
                    prompt=prompt,
                    character_refs=character_refs,
                )
            else:
                data = await generate_freezone_story_script(
                    source_text=source_text,
                    prompt=prompt,
                    model=model,
                    character_refs=character_refs,
                )
            bind_story_script_assets(data, character_refs=character_refs)
            out = _story_script_output_path(project_dir, job_id)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(data.model_dump(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            data_payload = data.model_dump()
            history_record = _record_freezone_node_history(
                project_dir=project_dir,
                canvas_id=canvas_id,
                node_id=node_id,
                task_type=task_type,
                username=username,
                project=project,
                job_id=job_id,
                status="completed",
                media_type="text",
                model=model,
                prompt=prompt,
                source_text_preview=_freezone_history_preview(source_text),
                row_count=len(data_payload.get("rows") or []),
                result={"output_format": "json", **data_payload},
            )
            result = {"output_format": "json"}
            if history_record:
                result["generation_history_record"] = history_record
            task_manager.complete_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                result=result,
                current_task="completed",
                logs=["故事脚本生成完成"],
                metadata=metadata,
            )
        except Exception as exc:
            _record_freezone_node_history(
                project_dir=project_dir,
                canvas_id=canvas_id,
                node_id=node_id,
                task_type=task_type,
                username=username,
                project=project,
                job_id=job_id,
                status="failed",
                media_type="text",
                model=model,
                prompt=prompt,
                source_text_preview=_freezone_history_preview(source_text),
                error=str(exc),
            )
            task_manager.fail_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                error=str(exc),
                current_task="failed",
                logs=[f"错误: {exc}"],
                metadata=metadata,
            )

    asyncio.create_task(_runner())


@router.post(
    "/projects/{project}/freezone/text/story-script",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_TEXT],
)
async def freezone_story_script_generate(
    project: str,
    body: FreezoneStoryScriptGenerateRequest,
    user: dict = Depends(get_api_user),
):
    """文本工具：根据上传剧本内容生成结构化故事脚本表。"""
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    source_text = body.source_text.strip()
    if not source_text and body.source_url:
        try:
            source_path = _freezone.resolve_static_url_to_path(body.source_url, project_dir)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not source_path.exists():
            raise HTTPException(404, f"source not found: {source_path}")
        source_text = _read_freezone_text_file(source_path).strip()

    video_path: Path | None = None
    if body.video_url:
        try:
            video_path = _freezone.resolve_static_url_to_path(body.video_url, project_dir)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not video_path.exists():
            raise HTTPException(404, f"video not found: {video_path}")

    character_refs, character_image_paths = _resolve_story_script_character_refs(
        body.character_refs, project_dir
    )

    # 视频 / 角色图本身就是主输入，这时不再强制要求剧本文本
    # （issue #207：以前只认 source_text，前端只好把提示词当剧本塞进来）。
    if not source_text and video_path is None and not character_image_paths:
        raise HTTPException(
            400, "source_text, source_url, video_url or character_refs is required"
        )

    try:
        job_id = _freezone._new_job_id()
        if ctx is not None:
            return await _freezone._enqueue_freezone_background_job(
                ctx=ctx,
                project_dir=project_dir,
                task_type="freezone_story_script",
                job_id=job_id,
                payload={
                    "source_text": source_text,
                    "prompt": body.prompt,
                    "model": body.model,
                    "canvas_id": body.canvas_id or "",
                    "node_id": body.node_id or "",
                    "video_path": video_path.as_posix() if video_path else "",
                    "duration_sec": body.duration_sec,
                    "max_frames": body.max_frames,
                    "scene_threshold": body.scene_threshold,
                    "character_refs": character_refs,
                    "character_image_paths": character_image_paths,
                },
            )
        if video_path is not None:
            # 抽帧要落盘并生成 /static 地址，没有 ProjectContext 就拼不出 URL。
            # 与 analyze-video-story 等视频任务保持一致的前置要求。
            _freezone._raise_project_context_required("freezone_story_script")
        _freezone._start_freezone_story_script_task(
            username=username,
            project=project_name,
            project_dir=project_dir,
            job_id=job_id,
            source_text=source_text,
            prompt=body.prompt,
            model=body.model,
            canvas_id=body.canvas_id or None,
            node_id=body.node_id or None,
            character_refs=character_refs,
            character_image_paths=character_image_paths,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error("failed to start story script task", exc)
        raise HTTPException(503, f"failed to start story script task: {exc}") from exc

    return _accepted_job_response(
        task_type="freezone_story_script",
        username=username,
        project=project_name,
        job_id=job_id,
    )


# [aipdd fork delta] 工具页「视频反推剧本」：上传视频 → 抽帧 → 用户可选视觉
# 模型反推纯文本剧本。独立任务类型，产物 txt 不覆盖 freezone_story_script 的
# JSON 故事脚本；未选模型时回退系统默认视觉链。
@router.post(
    "/projects/{project}/freezone/video-script-reverse",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_TEXT],
)
async def freezone_video_script_reverse(
    project: str,
    body: FreezoneVideoScriptReverseRequest,
    user: dict = Depends(get_api_user),
):
    """视频反推剧本：上传视频的静态 URL + 可选视觉模型 → 异步 txt 剧本任务。"""
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    try:
        video_path = _freezone.resolve_static_url_to_path(body.video_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not video_path.exists():
        raise HTTPException(404, f"video not found: {video_path}")

    if ctx is None:
        # 抽帧要落盘并生成 /static 地址，没有 ProjectContext 拼不出 URL。
        _freezone._raise_project_context_required("freezone_video_script_reverse")

    try:
        return await _freezone._enqueue_freezone_background_job(
            ctx=ctx,
            project_dir=project_dir,
            task_type="freezone_video_script_reverse",
            job_id=_freezone._new_job_id(),
            payload={
                "video_path": video_path.as_posix(),
                "prompt": body.prompt,
                "model": body.model,
                "duration_sec": body.duration_sec,
                "max_frames": body.max_frames,
                "scene_threshold": body.scene_threshold,
            },
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error(
            "failed to start video script reverse task", exc
        )
        raise HTTPException(503, f"failed to start video script reverse task: {exc}") from exc


def _start_freezone_image_reverse_prompt_task(
    *,
    username: str,
    project: str,
    project_dir: Path,
    job_id: str,
    source_path: Path,
    instruction: str = "",
    canvas_id: str | None = None,
    node_id: str | None = None,
) -> None:
    task_type = "freezone_image_reverse_prompt"
    task_manager = _freezone.get_task_manager()
    metadata = {
        "job_id": job_id,
        "canvas_id": canvas_id or "",
        "node_id": node_id or "",
        "source_path": source_path.as_posix(),
    }
    task_manager.create_task(
        task_type,
        username,
        project,
        episode=0,
        scope=job_id,
        status="starting",
        metadata=metadata,
    )

    async def _runner() -> None:
        logs = ["开始反推图片提示词"]
        try:
            task_manager.update_progress(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                progress=0.1,
                current_task="reverse_prompting_image",
                logs=logs,
            )
            prompt = await reverse_prompt_from_image(
                image_path=source_path,
                instruction=instruction,
            )
            out = _image_reverse_prompt_output_path(project_dir, job_id)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps({"prompt": prompt}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            history_record = _record_freezone_node_history(
                project_dir=project_dir,
                canvas_id=canvas_id,
                node_id=node_id,
                task_type=task_type,
                username=username,
                project=project,
                job_id=job_id,
                status="completed",
                media_type="text",
                source_path=str(source_path),
                result={"output_format": "json", "prompt": prompt},
            )
            result = {"output_format": "json"}
            if history_record:
                result["generation_history_record"] = history_record
            task_manager.complete_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                result=result,
                current_task="completed",
                logs=["图片提示词反推完成"],
                metadata=metadata,
            )
        except Exception as exc:
            _record_freezone_node_history(
                project_dir=project_dir,
                canvas_id=canvas_id,
                node_id=node_id,
                task_type=task_type,
                username=username,
                project=project,
                job_id=job_id,
                status="failed",
                media_type="text",
                source_path=str(source_path),
                error=str(exc),
            )
            task_manager.fail_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                error=str(exc),
                current_task="failed",
                logs=[f"错误: {exc}"],
                metadata=metadata,
            )

    asyncio.create_task(_runner())
