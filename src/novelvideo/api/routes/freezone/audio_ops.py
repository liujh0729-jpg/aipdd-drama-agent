"""freezone.audio_ops 子模块（自 api/routes/freezone.py 拆分）。"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import Form
from fastapi import HTTPException
from fastapi import UploadFile
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone as _freezone
from novelvideo.api.schemas import FreezoneAudioMusicRequest
from novelvideo.api.schemas import FreezoneAudioSeparateRequest
from novelvideo.api.schemas import FreezoneAudioSpeechRequest
# [aipdd fork delta req46] 工具页「音乐创作」与两步翻唱预处理的请求体。
from novelvideo.api.schemas import FreezoneMusicCreateRequest
from novelvideo.api.schemas import FreezoneMusicCoverPreprocessRequest
# [aipdd fork delta] 工具页「人声复刻 → 替换人声」请求体。
from novelvideo.api.schemas import FreezoneVoiceReplaceRequest
# [aipdd fork delta] 工具页「视频配音」请求体。
from novelvideo.api.schemas import FreezoneVideoDubbingRequest
from novelvideo.api.schemas import FreezoneJobAcceptedResponse
from novelvideo.api.schemas import FreezoneVideoComposeRequest
from novelvideo.api.schemas import FreezoneVideoEraseRequest
from novelvideo.api.schemas import FreezoneVideoUpscaleRequest
from novelvideo.i18n_message import lmsg
from novelvideo.freezone.audio_node import VOICE_FILE_UNREADABLE_MESSAGE
from novelvideo.freezone.audio_node import VoicePrerequisiteError
from novelvideo.freezone.audio_node import create_user_audio_voice
from novelvideo.freezone.audio_node import is_readable_audio_file
from novelvideo.freezone.route_helpers import accepted_job_response as _accepted_job_response
from novelvideo.utils.async_ops import spawn_background

from ._common import (TAG_FREEZONE_AUDIO, TAG_FREEZONE_VIDEO, logger)

router = APIRouter()


def _start_freezone_video_compose_task(
    *,
    username: str,
    project: str,
    project_dir: Path,
    job_id: str,
    body: FreezoneVideoComposeRequest,
    resolved_tracks: list[dict],
) -> None:
    task_type = "freezone_video_compose"
    task_manager = _freezone.get_task_manager()
    task_manager.create_task(
        task_type, username, project, episode=0, scope=job_id, status="starting"
    )

    async def _runner() -> None:
        try:
            task_manager.update_progress(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                progress=0.05,
                current_task="validating_timeline",
                logs=["开始合成视频时间线"],
            )
            from novelvideo.freezone.jobs import run_freezone_video_compose

            output_path = await run_freezone_video_compose(
                project_dir=project_dir,
                job_id=job_id,
                title=body.title,
                canvas_id=body.canvas_id,
                resolution=body.resolution,
                fps=body.fps,
                background_color=body.background_color,
                keep_original_audio=body.keep_original_audio,
                tracks=resolved_tracks,
            )
            task_manager.complete_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                result={"output_format": "mp4", "output_path": str(output_path)},
                current_task="completed",
                logs=["视频合成完成"],
            )
        except Exception as exc:
            task_manager.fail_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                error=str(exc),
                current_task="failed",
                logs=[f"错误: {exc}"],
            )

    spawn_background(_runner())


def _start_freezone_video_erase_task(
    *,
    username: str,
    project: str,
    project_dir: Path,
    job_id: str,
    source_path: Path,
    body: FreezoneVideoEraseRequest,
) -> None:
    task_type = "freezone_video_erase"
    task_manager = _freezone.get_task_manager()
    task_manager.create_task(
        task_type, username, project, episode=0, scope=job_id, status="starting"
    )

    async def _runner() -> None:
        try:
            task_manager.update_progress(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                progress=0.05,
                current_task="analyzing_video",
                logs=["开始视频擦除处理"],
            )
            from novelvideo.freezone.jobs import run_freezone_video_erase

            output_path, meta = await run_freezone_video_erase(
                project_dir=project_dir,
                job_id=job_id,
                source_path=str(source_path),
                mode=body.mode,
                engine=body.engine,
                box_x=body.box_x,
                box_y=body.box_y,
                box_width=body.box_width,
                box_height=body.box_height,
            )
            task_manager.complete_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                result={
                    "output_format": "mp4",
                    "output_path": str(output_path),
                    "meta": meta,
                },
                current_task="completed",
                logs=["视频擦除完成"],
            )
        except Exception as exc:
            task_manager.fail_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                error=str(exc),
                current_task="failed",
                logs=[f"错误: {exc}"],
            )

    spawn_background(_runner())


def _start_freezone_video_upscale_task(
    *,
    username: str,
    project: str,
    project_id: str,
    project_dir: Path,
    job_id: str,
    source_path: Path,
    body: FreezoneVideoUpscaleRequest,
) -> None:
    task_type = "freezone_video_upscale"
    task_manager = _freezone.get_task_manager()
    task_manager.create_task(
        task_type,
        username,
        project,
        episode=0,
        scope=job_id,
        status="starting",
    )

    async def _runner() -> None:
        try:
            task_manager.update_progress(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                progress=0.05,
                current_task="upscaling_video",
                logs=["开始视频高清处理"],
            )
            from novelvideo.freezone.jobs import run_freezone_video_upscale

            output_path, meta = await run_freezone_video_upscale(
                project_dir=project_dir,
                job_id=job_id,
                source_path=str(source_path),
                resolution=body.resolution,
                frame_interpolation=body.frame_interpolation,
                denoise_strength=body.denoise_strength,
            )
            rel = output_path.relative_to(project_dir).as_posix()
            task_manager.complete_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                result={
                    "output_format": "mp4",
                    "output_url": _freezone.project_static_url(project_id, rel, local_path=output_path),
                    "meta": meta,
                },
                current_task="completed",
                logs=["视频高清处理完成"],
            )
        except Exception as exc:
            task_manager.fail_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                error=str(exc),
                current_task="failed",
                logs=[f"错误: {exc}"],
            )

    spawn_background(_runner())


def _start_freezone_audio_separate_task(
    *,
    username: str,
    project: str,
    project_dir: Path,
    job_id: str,
    source_path: Path,
    target_episode: int | None = None,
    target_beat: int | None = None,
) -> None:
    task_type = "freezone_audio_separate"
    task_manager = _freezone.get_task_manager()
    task_manager.create_task(
        task_type, username, project, episode=0, scope=job_id, status="starting"
    )

    async def _runner() -> None:
        try:
            task_manager.update_progress(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                progress=0.05,
                current_task="separating_audio_video",
                logs=["开始音视频分离"],
            )
            from novelvideo.freezone.jobs import run_freezone_audio_separate

            outputs = await run_freezone_audio_separate(
                project_dir=project_dir,
                job_id=job_id,
                source_path=str(source_path),
            )
            result = {"job_id": job_id}
            if target_episode and target_beat and outputs.get("audio_path"):
                result["pushable"] = True
                result["slot_target"] = {
                    "kind": "beat_audio",
                    "episode": int(target_episode),
                    "beat": int(target_beat),
                }
            task_manager.complete_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                result=result,
                current_task="completed",
                logs=["音视频分离完成"],
            )
        except Exception as exc:
            task_manager.fail_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                error=str(exc),
                current_task="failed",
                logs=[f"错误: {exc}"],
            )

    spawn_background(_runner())


def _start_freezone_audio_speech_task(
    *,
    username: str,
    project: str,
    account_voice_username: str | None,
    project_id: str,
    project_dir: Path,
    job_id: str,
    body: FreezoneAudioSpeechRequest,
) -> None:
    task_type = "freezone_audio_speech"
    task_manager = _freezone.get_task_manager()
    task_manager.create_task(
        task_type, username, project, episode=0, scope=job_id, status="starting"
    )

    async def _runner() -> None:
        store = None
        try:
            task_manager.update_progress(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                progress=0.05,
                current_task="preparing_audio_speech",
                logs=["开始文本生成语音"],
            )
            task_manager.update_progress(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                progress=0.2,
                current_task="calling_tts_provider",
                logs=["正在调用 TTS 服务"],
            )
            voice_ref_payload = body.voice_ref.model_dump() if body.voice_ref else None
            store = await _freezone.make_sqlite_store(username, project)
            # 同进程执行也走同一条投射路径，好让两个入口读到的项目态形状一致。
            projector = _freezone._installed_task_projector()
            projection = None
            if projector is not None:
                built = await _freezone._build_task_projection(
                    projector,
                    store=store,
                    username=username,
                    project_name=project,
                    episode=int(body.target_episode or 0),
                    task_type=task_type,
                    extra_config={"voice_ref": voice_ref_payload},
                )
                if built is not None:
                    from novelvideo.task_backend.projection import read_projection

                    projection = read_projection({"projection": built})
            result = await _freezone.generate_freezone_audio_speech(
                store=store,
                username=username,
                project=project,
                account_voice_username=account_voice_username,
                project_dir=project_dir,
                job_id=job_id,
                text=body.text,
                emotion_prompt=body.emotion_prompt,
                voice_ref=voice_ref_payload,
                projection=projection,
                # [aipdd fork delta] 同 ctx 分支：路由已按 audio 目录校验过，这里
                # 直接透传目录 id（audio 条目的 apiModel 就是目录 id 本身）。
                model=str(getattr(body, "model", "") or "").strip(),
            )
            rel = result.audio_path.relative_to(project_dir).as_posix()
            audio_url = _freezone.project_static_url(project_id, rel, local_path=result.audio_path)
            result_payload = {
                "url": audio_url,
                "audio_url": audio_url,
                "audio_size": result.audio_path.stat().st_size,
                "duration_ms": result.duration_ms,
                "mime_type": result.mime_type,
                "model": result.model,
                "voice_source": result.voice_source,
                "voice_sha256": result.voice_sha256,
            }
            if body.target_episode and body.target_beat:
                result_payload["pushable"] = True
                result_payload["slot_target"] = {
                    "kind": "beat_audio",
                    "episode": int(body.target_episode),
                    "beat": int(body.target_beat),
                }
            task_manager.complete_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                result=result_payload,
                current_task="completed",
                logs=["文本生成语音完成"],
            )
        except Exception as exc:
            task_manager.fail_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                error=str(exc),
                current_task="failed",
                logs=[f"错误: {exc}"],
            )
        finally:
            close = getattr(store, "close", None) if store is not None else None
            if close:
                await close()

    spawn_background(_runner())


FREEZONE_AUDIO_AGE_GROUP_LABELS = {
    "child": "幼年",
    "youth": "青年",
    "middle": "中年",
    "elder": "老年",
}


def _freezone_audio_ref_payload(
    *,
    username: str,
    project: str,
    project_id: str,
    project_dir: Path,
    scope: str,
    label: str,
    path: str,
    sha256: str = "",
    updated_at: str = "",
    character_name: str = "",
    identity_id: str = "",
    identity_name: str = "",
    slot: str = "",
    age_group: str = "",
) -> dict:
    rel_path = str(path or "").strip()
    abs_path = Path(rel_path)
    if rel_path and not abs_path.is_absolute():
        abs_path = project_dir / rel_path

    try:
        exists = bool(rel_path and is_readable_audio_file(abs_path))
    except OSError:
        exists = False
    url = ""
    if exists:
        try:
            rel = abs_path.relative_to(project_dir).as_posix()
            url = _freezone.project_static_url(project_id, rel, local_path=abs_path)
        except ValueError:
            url = ""

    return {
        "scope": scope,
        "label": label,
        "path": rel_path,
        "url": url,
        "exists": exists and bool(url),
        "sha256": str(sha256 or ""),
        "updated_at": str(updated_at or ""),
        "character_name": character_name,
        "identity_id": identity_id,
        "identity_name": identity_name,
        "slot": slot,
        "age_group": age_group,
    }


def _user_voice_media_url(project: str, voice_id: str) -> str:
    safe_project = str(project or "").strip()
    safe_voice_id = str(voice_id or "").strip()
    return f"/api/v1/projects/{safe_project}/freezone/audio/voices/{safe_voice_id}/media"


def _attach_user_voice_media_urls(project: str, voices: list[dict]) -> list[dict]:
    out: list[dict] = []
    for item in voices:
        voice = dict(item)
        voice_id = str(voice.get("voice_id") or "").strip()
        if voice_id and voice.get("exists"):
            voice["url"] = _user_voice_media_url(project, voice_id)
        else:
            voice["url"] = ""
        out.append(voice)
    return out


def _freezone_character_audio_refs(
    *,
    username: str,
    project: str,
    project_id: str,
    project_dir: Path,
    character,
) -> dict:
    character_name = str(getattr(character, "name", "") or "")
    voices = [
        _freezone_audio_ref_payload(
            username=username,
            project=project,
            project_id=project_id,
            project_dir=project_dir,
            scope="character_default",
            label=f"{character_name} · 默认声线",
            path=str(getattr(character, "reference_audio_path", "") or ""),
            sha256=str(getattr(character, "reference_audio_sha256", "") or ""),
            updated_at=str(getattr(character, "reference_audio_updated_at", "") or ""),
            character_name=character_name,
            slot="default",
            age_group=str(getattr(character, "age_group", "") or ""),
        )
    ]

    samples = getattr(character, "voice_samples_by_age_group", None) or {}
    if isinstance(samples, dict):
        for slot, slot_label in FREEZONE_AUDIO_AGE_GROUP_LABELS.items():
            entry = samples.get(slot)
            if not isinstance(entry, dict):
                entry = {}
            voices.append(
                _freezone_audio_ref_payload(
                    username=username,
                    project=project,
                    project_id=project_id,
                    project_dir=project_dir,
                    scope="character_age_group",
                    label=f"{character_name} · {slot_label}声线",
                    path=str(entry.get("path", "") or ""),
                    sha256=str(entry.get("sha256", "") or ""),
                    updated_at=str(entry.get("updated_at", "") or ""),
                    character_name=character_name,
                    slot=slot,
                    age_group=slot,
                )
            )

    identities = []
    for identity in list(getattr(character, "identities", None) or []):
        identity_id = str(getattr(identity, "identity_id", "") or "")
        identity_name = str(getattr(identity, "identity_name", "") or "")
        age_group = str(getattr(identity, "age_group", "") or "")
        direct = _freezone_audio_ref_payload(
            username=username,
            project=project,
            project_id=project_id,
            project_dir=project_dir,
            scope="identity",
            label=f"{character_name} · {identity_name or identity_id}声线",
            path=str(getattr(identity, "reference_audio_path", "") or ""),
            sha256=str(getattr(identity, "reference_audio_sha256", "") or ""),
            updated_at=str(getattr(identity, "reference_audio_updated_at", "") or ""),
            character_name=character_name,
            identity_id=identity_id,
            identity_name=identity_name,
            age_group=age_group,
        )
        resolved = _freezone.resolve_character_voice(
            project_dir=project_dir,
            character=character,
            identity=identity,
        )
        resolved_path = ""
        if resolved.audio_path is not None:
            try:
                resolved_path = resolved.audio_path.relative_to(project_dir).as_posix()
            except ValueError:
                resolved_path = str(resolved.audio_path)
        direct["resolved"] = (
            _freezone_audio_ref_payload(
                username=username,
                project=project,
                project_id=project_id,
                project_dir=project_dir,
                scope="identity_resolved",
                label=f"{character_name} · {identity_name or identity_id}实际声线",
                path=resolved_path,
                sha256=resolved.sha256,
                character_name=character_name,
                identity_id=identity_id,
                identity_name=identity_name,
                slot=resolved.tier or "",
                age_group=age_group,
            )
            if resolved.audio_path is not None
            else None
        )
        identities.append(direct)

    available_count = sum(1 for item in voices if item["exists"])
    for item in identities:
        if item["exists"]:
            available_count += 1
        resolved = item.get("resolved")
        if isinstance(resolved, dict) and resolved.get("exists"):
            available_count += 1

    return {
        "character_name": character_name,
        "is_main": bool(getattr(character, "is_main", False)),
        "age_group": str(getattr(character, "age_group", "") or ""),
        "voices": voices,
        "identities": identities,
        "available_count": available_count,
    }


@router.get(
    "/projects/{project}/freezone/audio/references",
    tags=[TAG_FREEZONE_AUDIO],
)
async def freezone_audio_references(
    project: str,
    user: dict = Depends(get_api_user),
):
    """获取 Freezone 音频节点可用的账号级音色、项目解说人与角色参考音频。"""
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user, required_role="viewer"
    )
    narrator_descriptor = _freezone.load_narrator_reference_audio_from_state_dir(ctx.state_dir)
    narration_style = _freezone.load_effective_narration_style_for_voice_from_state_dir(ctx.state_dir)
    requester_username = ctx.requester_username or username
    user_voices = _attach_user_voice_media_urls(
        project,
        await asyncio.to_thread(_freezone.list_user_audio_voices, requester_username),
    )

    store = (
        await _freezone.make_sqlite_store_for_context(ctx)
        if ctx is not None
        else await _freezone.make_sqlite_store(username, project_name)
    )
    try:
        characters = list(await store.list_characters())
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()

    narrator = await asyncio.to_thread(
        _freezone_audio_ref_payload,
        username=username,
        project=project_name,
        project_id=ctx.project_id,
        project_dir=project_dir,
        scope="project_narrator",
        label="项目解说人声线",
        path=narrator_descriptor.get("path", ""),
        sha256=narrator_descriptor.get("sha256", ""),
        updated_at=narrator_descriptor.get("updated_at", ""),
    )
    character_payloads = list(
        await asyncio.gather(
            *(
                asyncio.to_thread(
                    _freezone_character_audio_refs,
                    username=username,
                    project=project_name,
                    project_id=ctx.project_id,
                    project_dir=project_dir,
                    character=character,
                )
                for character in characters
            )
        )
    )
    available = [narrator] if narrator["exists"] else []
    available.extend(item for item in user_voices if item["exists"])
    for character in character_payloads:
        available.extend(item for item in character["voices"] if item["exists"])
        for item in character["identities"]:
            if item["exists"]:
                available.append(item)
            resolved = item.get("resolved")
            if isinstance(resolved, dict) and resolved.get("exists"):
                available.append(resolved)

    return {
        "ok": True,
        "data": {
            "narration_style": narration_style,
            "narrator": narrator,
            "characters": character_payloads,
            "user_voices": user_voices,
            "available": available,
        },
    }


@router.post(
    "/projects/{project}/freezone/audio/voices",
    tags=[TAG_FREEZONE_AUDIO],
)
async def create_freezone_audio_voice(
    project: str,
    file: Annotated[UploadFile, File(description="参考音频文件，支持 mp3/wav/m4a/aac/ogg/webm")],
    name: Annotated[str, Form(description="音色名称，用于音色选择弹窗展示")] = "",
    user: dict = Depends(get_api_user),
):
    """创建账号级“我的音色”。

    这个接口不会写入项目解说人、角色默认声线、年龄段声线或身份声线；
    它只把参考音频保存到账号级 Freezone 音色库。生成音频时传
    `voice_ref={"scope":"user_custom","voice_id":"..."}` 即可使用。
    """
    ctx, username, _project_name, _project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    username = ctx.requester_username if ctx is not None and ctx.requester_username else username
    content = await file.read()
    try:
        voice = create_user_audio_voice(
            username=username,
            name=name or Path(file.filename or "").stem,
            filename=file.filename,
            content=content,
            mime_type=file.content_type or "",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    voice = _attach_user_voice_media_urls(project, [voice])[0]

    return {"ok": True, "data": voice}


@router.get(
    "/projects/{project}/freezone/audio/voices/{voice_id}/media",
    tags=[TAG_FREEZONE_AUDIO],
)
async def get_freezone_audio_voice_media(
    project: str,
    voice_id: str,
    user: dict = Depends(get_api_user),
):
    ctx, username, _project_name, _project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user, required_role="viewer"
    )
    username = ctx.requester_username if ctx is not None and ctx.requester_username else username
    try:
        resolved = await asyncio.to_thread(_freezone.resolve_user_audio_voice, username, voice_id)
    except OSError as exc:
        raise HTTPException(404, VOICE_FILE_UNREADABLE_MESSAGE) from exc
    except RuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path=str(resolved.audio_path))


@router.post(
    "/projects/{project}/freezone/video/audio-separate",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_VIDEO],
)
async def freezone_audio_separate(
    project: str,
    body: FreezoneAudioSeparateRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：音视频分离。

    当前轻量版会同时产出：
    - 提取出的纯音频
    - 去掉音轨后的无声视频
    """
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    try:
        source_path = _freezone.resolve_static_url_to_path(body.source_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not source_path.exists():
        raise HTTPException(404, f"video source not found: {source_path}")

    try:
        job_id = _freezone._new_job_id()
        if ctx is not None:
            return await _freezone._enqueue_or_start_freezone_media_job(
                ctx=ctx,
                username=username,
                project=project_name,
                project_dir=project_dir,
                task_type="freezone_audio_separate",
                job_id=job_id,
                payload={
                    "source_path": source_path.as_posix(),
                    "target_episode": body.target_episode,
                    "target_beat": body.target_beat,
                },
            )
        _start_freezone_audio_separate_task(
            username=username,
            project=project_name,
            project_dir=project_dir,
            job_id=job_id,
            source_path=source_path,
            target_episode=body.target_episode,
            target_beat=body.target_beat,
        )
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error("failed to start freezone audio separate task", exc)
        raise HTTPException(503, f"failed to start freezone audio separate task: {exc}") from exc

    return _accepted_job_response(
        task_type="freezone_audio_separate",
        username=username,
        project=project_name,
        job_id=job_id,
    )


@router.post(
    "/projects/{project}/freezone/audio/speech",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_AUDIO],
)
async def freezone_audio_speech(
    project: str,
    body: FreezoneAudioSpeechRequest,
    user: dict = Depends(get_api_user),
):
    """Freezone 音频节点：文本生成语音。"""
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    account_voice_username = (
        ctx.requester_username if ctx is not None and ctx.requester_username else username
    )

    if not body.text.strip():
        raise HTTPException(400, "text is required")
    if len(body.text) > 10_000:
        raise HTTPException(400, "text must be <= 10000 characters")

    voice_ref_payload = body.voice_ref.model_dump() if body.voice_ref else None
    # [aipdd fork delta] 画布音频节点按类型选模型：传了 model 就必须命中后台
    # 「默认生成模型」的 audio 目录，提交前定型（与图片/视频的
    # _require_scoped_media_model 同一时机），避免执行期才发现选错。
    requested_audio_model = str(body.model or "").strip()
    selected_audio_model = ""
    if requested_audio_model:
        audio_catalog = await _freezone._scoped_media_model_catalog(
            "audio",
            requester_user_id=ctx.requester_user_id,
        ) or []
        audio_entry = next(
            (
                item
                for item in audio_catalog
                if requested_audio_model in _freezone._catalog_entry_identifiers(item)
            ),
            None,
        )
        if audio_entry is None:
            raise HTTPException(
                409,
                "音频模型未在后台「默认生成模型」的音频列表里配置或已停用，请刷新后重选",
            )
        selected_audio_model = str(
            audio_entry.get("apiModel") or audio_entry.get("api_model") or ""
        ).strip() or requested_audio_model
    store = await _freezone.make_sqlite_store_for_context(ctx)
    try:
        narration_style, speech_voice = await _freezone.resolve_speech_voice(
            store=store,
            username=username,
            project=project_name,
            account_voice_username=account_voice_username,
            project_dir=project_dir,
            voice_ref=voice_ref_payload,
        )
    except VoicePrerequisiteError as exc:
        logger.info(
            "freezone_audio_speech_voice_prereq_failed",
            extra={
                "project_id": ctx.project_id,
                "voice_ref_present": body.voice_ref is not None,
                "voice_ref_scope": str((voice_ref_payload or {}).get("scope") or "default"),
                "error_code": exc.error_code,
            },
        )
        return JSONResponse(
            status_code=409,
            content={"ok": False, "code": exc.error_code, "error": str(exc)},
        )
    finally:
        await store.close()

    logger.info(
        "freezone_audio_speech_voice_resolved",
        extra={
            "project_id": ctx.project_id,
            "narration_style": narration_style,
            "voice_ref_present": body.voice_ref is not None,
            "voice_ref_scope": str((voice_ref_payload or {}).get("scope") or "default"),
            "speech_voice_source": speech_voice.source,
        },
    )

    try:
        job_id = _freezone._new_job_id()
        if ctx is not None:

            # 音色解析要读的项目态在这里定型：投递方就是存放该项目的那台机器，
            # 执行方不必再回头读项目库。没装投射器时返回空片段，payload 逐字不变。
            projection_payload = await _freezone._task_projection_payload(
                ctx=ctx,
                username=username,
                project_name=project_name,
                episode=int(body.target_episode or 0),
                task_type="freezone_audio_speech",
                extra_config={"voice_ref": voice_ref_payload},
            )
            return await _freezone._enqueue_freezone_background_job(
                ctx=ctx,
                project_dir=project_dir,
                task_type="freezone_audio_speech",
                job_id=job_id,
                payload={
                    "text": body.text,
                    "emotion_prompt": body.emotion_prompt,
                    "voice_ref": voice_ref_payload,
                    # [aipdd fork delta] 选中的音频模型（newapi 内部名）；空=默认引擎。
                    "model": selected_audio_model,
                    "account_voice_username": account_voice_username,
                    "target_episode": body.target_episode,
                    "target_beat": body.target_beat,
                    # [aipdd fork delta] 工具页批量任务标记：产物完成后 runner 会
                    # 把它登记进资产库「待分类资产」。
                    **({"archive_to_library": True} if body.archive_to_library else {}),
                    # [aipdd fork delta] 工具页任务归属：浮现到 metadata.source_tool。
                    **({"source_tool": body.source_tool} if body.source_tool else {}),
                    **projection_payload,
                },
            )
        _start_freezone_audio_speech_task(
            username=username,
            project=project_name,
            account_voice_username=account_voice_username,
            project_id=ctx.project_id,
            project_dir=project_dir,
            job_id=job_id,
            body=body,
        )
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error("failed to start freezone audio speech task", exc)
        raise HTTPException(503, f"failed to start freezone audio speech task: {exc}") from exc

    return _accepted_job_response(
        task_type="freezone_audio_speech",
        username=username,
        project=project_name,
        job_id=job_id,
    )


@router.post(
    "/projects/{project}/freezone/audio/eleven-music",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_AUDIO],
)
async def freezone_audio_eleven_music(
    project: str,
    body: FreezoneAudioMusicRequest,
    user: dict = Depends(get_api_user),
):
    """Freezone 音频节点：文本生成音乐。"""
    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    prompt = body.input.strip()
    if not prompt:
        raise HTTPException(400, "input is required")
    if len(prompt) > 4100:
        raise HTTPException(400, "input must be <= 4100 characters")

    try:
        job_id = _freezone._new_job_id()
        if ctx is not None:

            return await _freezone._enqueue_freezone_background_job(
                ctx=ctx,
                project_dir=project_dir,
                task_type="freezone_audio_eleven_music",
                job_id=job_id,
                payload={
                    "input": prompt,
                    "model": body.model,
                    "response_format": body.response_format,
                    "music_length_ms": body.music_length_ms,
                    "force_instrumental": body.force_instrumental,
                    "respect_sections_durations": body.respect_sections_durations,
                    "output_format": body.output_format,
                },
            )
        _freezone._raise_project_context_required("freezone_audio_eleven_music")
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error("failed to start freezone audio music task", exc)
        raise HTTPException(503, f"failed to start freezone audio music task: {exc}") from exc

    return _accepted_job_response(
        task_type="freezone_audio_eleven_music",
        username=username,
        project=project_name,
        job_id=job_id,
    )


# [aipdd fork delta] 替换人声：源素材按后缀判定「视频需先解封装抽音轨 / 音频直接用」。
_VOICE_REPLACE_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".m4v", ".mkv", ".avi", ".flv"}


# [aipdd fork delta] 未指定 ASR 时不在提交时猜候选：候选可用性取决于「能不能吃本地
# 音频 + 组织/个人身份」，而只有执行时才能确定组织标志。空串透给叶，由
# ``douyin_copywrite.pick_asr_candidate_for_local_audio`` 统一自动选（豆包→网关）。


@router.post(
    "/projects/{project}/freezone/audio/voice-replace",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_AUDIO],
)
async def freezone_voice_replace(
    project: str,
    body: FreezoneVoiceReplaceRequest,
    user: dict = Depends(get_api_user),
):
    """[aipdd fork delta] 工具页「人声复刻 → 替换人声」。

    上传原始音频/视频（提取台词）+ 参考音频（目标音色）：把参考音频注册成账号级
    ``user_custom`` 声线，再投递 ``freezone_voice_replace`` 任务，由 runner 编排
    解封装→ASR→IndexTTS2 零样本合成目标音色新声。
    """
    from novelvideo.freezone.douyin_copywrite import resolve_douyin_asr_candidate

    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    account_voice_username = (
        ctx.requester_username if ctx is not None and ctx.requester_username else username
    )

    # ① 原始素材 → 项目内绝对路径 + 判定音频/视频。
    try:
        source_path = _freezone.resolve_static_url_to_path(body.source_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not source_path.exists():
        raise HTTPException(404, f"voice replace source not found: {source_path}")
    source_kind = (
        "video" if source_path.suffix.lower() in _VOICE_REPLACE_VIDEO_SUFFIXES else "audio"
    )

    # ② 参考音频（目标音色）→ 读字节 → 注册账号级 user_custom 声线 → voice_ref。
    try:
        reference_path = _freezone.resolve_static_url_to_path(
            body.reference_audio_url, project_dir
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not reference_path.exists():
        raise HTTPException(404, f"reference audio not found: {reference_path}")
    content = await asyncio.to_thread(reference_path.read_bytes)
    if not is_readable_audio_file(reference_path):
        raise HTTPException(400, "参考音频不可读或不是有效音频文件")
    try:
        voice = await asyncio.to_thread(
            create_user_audio_voice,
            username=account_voice_username,
            name=body.reference_name or reference_path.stem,
            filename=reference_path.name,
            content=content,
            mime_type="",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    voice_ref = {"scope": "user_custom", "voice_id": str(voice["voice_id"])}

    # ③ ASR 通道：为空时不猜（运行时由叶自动选）；显式指定则按候选清单校验存在性。
    asr_model = str(body.asr_model or "").strip()
    if asr_model:
        try:
            resolve_douyin_asr_candidate(asr_model)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(409, f"未知的 ASR 模型：{asr_model}") from exc

    # ④ 音频模型（可选）：与 audio_speech 同口径按 audio 目录校验。
    requested_audio_model = str(body.model or "").strip()
    selected_audio_model = ""
    if requested_audio_model:
        audio_catalog = await _freezone._scoped_media_model_catalog(
            "audio",
            requester_user_id=ctx.requester_user_id,
        ) or []
        audio_entry = next(
            (
                item
                for item in audio_catalog
                if requested_audio_model in _freezone._catalog_entry_identifiers(item)
            ),
            None,
        )
        if audio_entry is None:
            raise HTTPException(
                409,
                "音频模型未在后台「默认生成模型」的音频列表里配置或已停用，请刷新后重选",
            )
        selected_audio_model = str(
            audio_entry.get("apiModel") or audio_entry.get("api_model") or ""
        ).strip() or requested_audio_model

    try:
        job_id = _freezone._new_job_id()
        if ctx is not None:
            return await _freezone._enqueue_freezone_background_job(
                ctx=ctx,
                project_dir=project_dir,
                task_type="freezone_voice_replace",
                job_id=job_id,
                payload={
                    "source_path": source_path.as_posix(),
                    "source_kind": source_kind,
                    "voice_ref": voice_ref,
                    "asr_model": asr_model,
                    "model": selected_audio_model,
                    "account_voice_username": account_voice_username,
                    "target_episode": body.target_episode,
                    "target_beat": body.target_beat,
                    **({"archive_to_library": True} if body.archive_to_library else {}),
                    **({"source_tool": body.source_tool} if body.source_tool else {}),
                },
            )
        _freezone._raise_project_context_required("freezone_voice_replace")
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error(
            "failed to start freezone voice replace task", exc
        )
        raise HTTPException(503, f"failed to start freezone voice replace task: {exc}") from exc

    return _accepted_job_response(
        task_type="freezone_voice_replace",
        username=username,
        project=project_name,
        job_id=job_id,
    )


@router.post(
    "/projects/{project}/freezone/audio/video-dubbing",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_AUDIO],
)
async def freezone_video_dubbing(
    project: str,
    body: FreezoneVideoDubbingRequest,
    user: dict = Depends(get_api_user),
):
    """[aipdd fork delta] 工具页「视频配音」。

    上传原始视频（提取台词）+ 参考音频（目标音色）：把参考音频注册成账号级
    ``user_custom`` 声线，再投递 ``freezone_video_dubbing`` 任务，由 runner 编排
    解封装→ASR→零样本合成目标音色新声（``language`` 作发音提示不翻译）→音轨回填。
    """
    from novelvideo.freezone.douyin_copywrite import resolve_douyin_asr_candidate

    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    account_voice_username = (
        ctx.requester_username if ctx is not None and ctx.requester_username else username
    )

    # ① 原始素材 → 项目内绝对路径 + 判定音频/视频。
    try:
        source_path = _freezone.resolve_static_url_to_path(body.source_url, project_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not source_path.exists():
        raise HTTPException(404, f"video dubbing source not found: {source_path}")
    source_kind = (
        "video" if source_path.suffix.lower() in _VOICE_REPLACE_VIDEO_SUFFIXES else "audio"
    )

    # ② 参考音频（目标音色）→ 读字节 → 注册账号级 user_custom 声线 → voice_ref。
    try:
        reference_path = _freezone.resolve_static_url_to_path(
            body.reference_audio_url, project_dir
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not reference_path.exists():
        raise HTTPException(404, f"reference audio not found: {reference_path}")
    content = await asyncio.to_thread(reference_path.read_bytes)
    if not is_readable_audio_file(reference_path):
        raise HTTPException(400, "参考音频不可读或不是有效音频文件")
    try:
        voice = await asyncio.to_thread(
            create_user_audio_voice,
            username=account_voice_username,
            name=body.reference_name or reference_path.stem,
            filename=reference_path.name,
            content=content,
            mime_type="",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    voice_ref = {"scope": "user_custom", "voice_id": str(voice["voice_id"])}

    # ③ ASR 通道：为空时不猜（运行时由叶自动选）；显式指定则按候选清单校验存在性。
    asr_model = str(body.asr_model or "").strip()
    if asr_model:
        try:
            resolve_douyin_asr_candidate(asr_model)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(409, f"未知的 ASR 模型：{asr_model}") from exc

    # ④ 音频模型（可选）：与 audio_speech 同口径按 audio 目录校验。
    requested_audio_model = str(body.model or "").strip()
    selected_audio_model = ""
    if requested_audio_model:
        audio_catalog = await _freezone._scoped_media_model_catalog(
            "audio",
            requester_user_id=ctx.requester_user_id,
        ) or []
        audio_entry = next(
            (
                item
                for item in audio_catalog
                if requested_audio_model in _freezone._catalog_entry_identifiers(item)
            ),
            None,
        )
        if audio_entry is None:
            raise HTTPException(
                409,
                "音频模型未在后台「默认生成模型」的音频列表里配置或已停用，请刷新后重选",
            )
        selected_audio_model = str(
            audio_entry.get("apiModel") or audio_entry.get("api_model") or ""
        ).strip() or requested_audio_model

    language = str(body.language or "").strip()

    try:
        job_id = _freezone._new_job_id()
        if ctx is not None:
            return await _freezone._enqueue_freezone_background_job(
                ctx=ctx,
                project_dir=project_dir,
                task_type="freezone_video_dubbing",
                job_id=job_id,
                payload={
                    "source_path": source_path.as_posix(),
                    "source_kind": source_kind,
                    "voice_ref": voice_ref,
                    "asr_model": asr_model,
                    "language": language,
                    "model": selected_audio_model,
                    "account_voice_username": account_voice_username,
                    "target_episode": body.target_episode,
                    "target_beat": body.target_beat,
                    **({"archive_to_library": True} if body.archive_to_library else {}),
                    **({"source_tool": body.source_tool} if body.source_tool else {}),
                },
            )
        _freezone._raise_project_context_required("freezone_video_dubbing")
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error(
            "failed to start freezone video dubbing task", exc
        )
        raise HTTPException(503, f"failed to start freezone video dubbing task: {exc}") from exc

    return _accepted_job_response(
        task_type="freezone_video_dubbing",
        username=username,
        project=project_name,
        job_id=job_id,
    )


# ============================================================
# [aipdd fork delta req46] 工具页「音乐创作」：MiniMax Music 3.0 直连
# ============================================================

_MUSIC_PUBLIC_URL_PREFIXES = ("http://", "https://")


def _resolve_music_reference_audio(
    source: str, project_dir: Path
) -> tuple[str, Path | None]:
    """参考音频来源 → ``(公网直链, 本地文件)``（上游两者只认一个）。

    同源静态地址 / 项目内相对路径 → 本地文件（转 base64 上交）；http(s) 公网直链
    原样透传（不先下载，避开一段无意义的往返）。两条路都走不通就是地址写错了，
    当场 400，不留到任务里报「音频不存在」。
    """

    clean = str(source or "").strip()
    if not clean:
        return "", None
    if clean.lower().startswith(_MUSIC_PUBLIC_URL_PREFIXES):
        return clean, None
    try:
        path = _freezone.resolve_static_url_to_path(clean, project_dir)
    except ValueError as exc:
        raise HTTPException(400, f"参考音频地址无法解析：{exc}") from exc
    if not path.is_file():
        raise HTTPException(404, f"参考音频文件不存在：{path.name}")
    return "", path


async def _resolve_music_model_selection(
    *,
    ctx,
    model: str,
    provider: str,
) -> tuple[str, str]:
    """音乐模型与站点渠道都只认设置：留空回落到「音乐默认模型」，两者皆空拦住。

    模型名不写在代码里（req40 口径）：候选只来自设置页「音乐」目录，条目自带的
    provider 就是站点——两边 MiniMax Key 不通用，拿错渠道的 Key 调用只会收到
    1004 鉴权失败，所以这里必须把条目上的渠道一起解出来。
    """

    from novelvideo.model_gateway_settings import resolve_default_media_model

    catalog = await _freezone._scoped_media_model_catalog(
        "music",
        requester_user_id=getattr(ctx, "requester_user_id", "") or "",
    ) or []
    wanted = str(model or "").strip()
    if not wanted:
        wanted = str(resolve_default_media_model("music") or "").strip()
    if not wanted:
        raise HTTPException(
            400,
            "还没有可用的音乐模型：请到设置 → 模型与渠道 › 默认生成模型 › 音乐添加音乐模型"
            "（翻唱 / 哼唱需要名字含 cover 的翻唱模型），没配渠道时先在设置里添加"
            " MiniMax 渠道；也可以在本工具的选择器里直接选一个",
        )
    entry = next(
        (
            item
            for item in catalog
            if wanted in _freezone._catalog_entry_identifiers(item)
        ),
        None,
    )
    if entry is None:
        raise _freezone._media_model_unavailable("music", catalog)
    api_model = str(
        entry.get("apiModel") or entry.get("api_model") or ""
    ).strip() or wanted
    resolved_provider = (
        str(provider or "").strip().lower()
        or str(entry.get("provider") or "").strip().lower()
    )
    return api_model, resolved_provider


async def _run_freezone_music_generate_leaf(payload: dict) -> dict:
    """执行一次音乐生成，返回任务结果字典（两条入队路径共用）。"""

    from novelvideo.freezone.minimax_music import (
        DEFAULT_AUDIO_FORMAT,
        run_freezone_music_generate,
    )

    project_dir = Path(str(payload.get("project_dir") or ""))
    audio_path_value = str(payload.get("audio_path") or "").strip()
    result = await run_freezone_music_generate(
        project_dir=project_dir,
        job_id=str(payload.get("job_id") or ""),
        mode=str(payload.get("mode") or ""),
        model=str(payload.get("model") or ""),
        provider=str(payload.get("provider") or ""),
        prompt=str(payload.get("prompt") or ""),
        lyrics=str(payload.get("lyrics") or ""),
        audio_url=str(payload.get("audio_url") or ""),
        audio_path=Path(audio_path_value) if audio_path_value else None,
        cover_feature_id=str(payload.get("cover_feature_id") or ""),
        sample_rate=int(payload.get("sample_rate") or 0),
        bitrate=int(payload.get("bitrate") or 0),
        audio_format=str(payload.get("audio_format") or DEFAULT_AUDIO_FORMAT),
    )
    project_id = str(payload.get("project_id") or "")
    rel = result.audio_path.relative_to(project_dir).as_posix()
    audio_url_value = (
        _freezone.project_static_url(project_id, rel, local_path=result.audio_path)
        if project_id
        else ""
    )
    return {
        "job_id": str(payload.get("job_id") or ""),
        "url": audio_url_value,
        "audio_url": audio_url_value,
        "audio_path": rel,
        "audio_size": result.size_bytes,
        "duration_ms": result.duration_ms,
        "mime_type": result.mime_type,
        "model": result.model,
        "provider": result.provider,
        "mode": result.mode,
        "lyrics": result.lyrics,
    }


def _start_freezone_music_generate_task(
    *,
    username: str,
    project: str,
    project_dir: Path,
    job_id: str,
    payload: dict,
) -> None:
    """无项目上下文时的同进程兜底（与音视频分离同形态）；正常工具页走后台队列。"""

    task_type = "freezone_music_generate"
    task_manager = _freezone.get_task_manager()
    task_manager.create_task(
        task_type, username, project, episode=0, scope=job_id, status="starting"
    )

    async def _runner() -> None:
        try:
            task_manager.update_progress(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                progress=0.05,
                current_task="calling_music_provider",
                logs=[lmsg("tasks.log.music.providerCalling", "正在调用音乐生成服务")],
            )
            result_payload = await _run_freezone_music_generate_leaf(
                {**payload, "project_dir": str(project_dir), "job_id": job_id}
            )
            task_manager.complete_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                result=result_payload,
                current_task="completed",
                logs=[lmsg("tasks.log.music.completed", "音乐生成完成")],
            )
        except Exception as exc:
            task_manager.fail_task(
                task_type,
                username,
                project,
                episode=0,
                scope=job_id,
                error=str(exc),
                current_task="failed",
                logs=[
                    lmsg("tasks.log.music.failed", f"错误: {exc}", detail=str(exc))
                ]
            )

    spawn_background(_runner())


@router.post(
    "/projects/{project}/freezone/audio/music-cover-preprocess",
    tags=[TAG_FREEZONE_AUDIO],
)
async def freezone_music_cover_preprocess(
    project: str,
    body: FreezoneMusicCoverPreprocessRequest,
    user: dict = Depends(get_api_user),
):
    """两步翻唱第一步：参考音频 → ``cover_feature_id`` + ASR 抽取的歌词。

    同步返回、不建任务：用户要在界面上看到并修改抽取的词，再带着 feature id
    提交生成（上游按 MD5 去重，同一段内容重复预处理拿到的是同一个 id）。
    """

    from novelvideo.freezone.minimax_api import (
        MiniMaxChannelUnavailable,
        MiniMaxError,
    )
    from novelvideo.freezone.minimax_music import (
        MusicRequestInvalid,
        cover_preprocess,
    )

    ctx, _username, _project_name, project_dir, _output_dir = (
        await _freezone._resolve_freezone_project(project, user)
    )
    model, provider = await _resolve_music_model_selection(
        ctx=ctx, model=body.model, provider=body.provider
    )
    audio_url, audio_path = _resolve_music_reference_audio(
        body.reference_audio, project_dir
    )
    if not audio_url and audio_path is None:
        raise HTTPException(400, "缺少参考音频：请上传本地音频或填写公网直链")
    try:
        preprocessed = await cover_preprocess(
            model=model,
            provider=provider,
            audio_url=audio_url,
            audio_path=audio_path,
        )
    except MusicRequestInvalid as exc:
        raise HTTPException(400, str(exc)) from exc
    except MiniMaxChannelUnavailable as exc:
        # 环境缺失不是生成失败：「去哪配」的原话必须原样递到界面。
        raise HTTPException(400, str(exc)) from exc
    except MiniMaxError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {
        "ok": True,
        "data": {
            "cover_feature_id": preprocessed.cover_feature_id,
            "formatted_lyrics": preprocessed.formatted_lyrics,
            "audio_duration": preprocessed.audio_duration,
            "model": preprocessed.model,
            "provider": preprocessed.provider,
        },
    }


@router.post(
    "/projects/{project}/freezone/audio/music-create",
    response_model=FreezoneJobAcceptedResponse,
    tags=[TAG_FREEZONE_AUDIO],
)
async def freezone_music_create(
    project: str,
    body: FreezoneMusicCreateRequest,
    user: dict = Depends(get_api_user),
):
    """[aipdd fork delta req46] 工具页「音乐创作」提交入口。

    上游约束在提交期就过一遍（:func:`build_music_payload` 是模式→字段的唯一收口
    点）：越界直接 400，不等任务跑失败再拼错误日志。产物 mp3/wav 落项目输出
    目录，由 runner 统一转静态 URL 并按需入库「待分类资产」。
    """

    from novelvideo.freezone.minimax_music import (
        MusicRequestInvalid,
        build_music_payload,
        probe_reference_audio_seconds,
        validate_reference_audio,
    )

    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    model, provider = await _resolve_music_model_selection(
        ctx=ctx, model=body.model, provider=body.provider
    )
    audio_url, audio_path = _resolve_music_reference_audio(
        body.reference_audio, project_dir
    )
    feature_id = str(body.cover_feature_id or "").strip()
    has_reference = bool(audio_url) or audio_path is not None

    duration_seconds: float | None = None
    if audio_path is not None and not feature_id:
        duration_seconds = await probe_reference_audio_seconds(audio_path)
    try:
        validate_reference_audio(
            audio_url=audio_url,
            audio_path=audio_path,
            duration_seconds=duration_seconds,
        )
        # 只校验不拼包（拼包在叶里再算一次，两处同一函数不会漂）：越界当场 400。
        build_music_payload(
            mode=body.mode,
            model=model,
            prompt=body.prompt,
            lyrics=body.lyrics,
            has_reference=has_reference,
            cover_feature_id=feature_id,
            sample_rate=body.sample_rate,
            bitrate=body.bitrate,
            audio_format=body.audio_format,
        )
    except MusicRequestInvalid as exc:
        raise HTTPException(400, str(exc)) from exc

    job_payload = {
        "mode": body.mode,
        "model": model,
        "provider": provider,
        "prompt": body.prompt,
        "lyrics": body.lyrics,
        "audio_url": audio_url,
        "audio_path": audio_path.as_posix() if audio_path is not None else "",
        "cover_feature_id": feature_id,
        "sample_rate": body.sample_rate,
        "bitrate": body.bitrate,
        "audio_format": body.audio_format,
        "source_name": body.source_name,
        **({"archive_to_library": True} if body.archive_to_library else {}),
        **({"source_tool": body.source_tool} if body.source_tool else {}),
    }

    try:
        job_id = _freezone._new_job_id()
        if ctx is not None:
            return await _freezone._enqueue_or_start_freezone_media_job(
                ctx=ctx,
                username=username,
                project=project_name,
                project_dir=project_dir,
                task_type="freezone_music_generate",
                job_id=job_id,
                payload=job_payload,
                queue_kind="default",
            )
        _start_freezone_music_generate_task(
            username=username,
            project=project_name,
            project_dir=project_dir,
            job_id=job_id,
            payload={**job_payload, "project_id": getattr(ctx, "project_id", "") or ""},
        )
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error(
            "failed to start freezone music generate task", exc
        )
        raise HTTPException(
            503, f"failed to start freezone music generate task: {exc}"
        ) from exc

    return _accepted_job_response(
        task_type="freezone_music_generate",
        username=username,
        project=project_name,
        job_id=job_id,
    )
