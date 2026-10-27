"""Celery runners for Image Freezone jobs."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from novelvideo.egress_context import (
    TRUSTED_EGRESS_CONTEXT_KEY,
    TrustedEgressContext,
    TrustedRunnerEnvelope,
)
from novelvideo.i18n_message import MessageLike, lmsg
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import (
    await_envelope_with_cancel_watch,
    await_with_cancel_watch as _await_with_cancel_watch,
)
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_backend.envelope import InvalidTaskEnvelope
from novelvideo.task_backend.projection import read_projection
from novelvideo.task_identity import project_task_state_key
from novelvideo.task_state import get_task_manager

logger = logging.getLogger(__name__)


def _run_cancellable(
    envelope: dict[str, Any],
    coro,
    *,
    task_type: str | None = None,
) -> dict[str, Any]:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            coro,
            envelope,
            task_type=task_type or str(envelope.get("task_type") or ""),
        )
    )


def _extract_trusted_egress_context(
    envelope: dict[str, Any],
) -> TrustedEgressContext | None:
    if type(envelope) is TrustedRunnerEnvelope:
        context = envelope.get(TRUSTED_EGRESS_CONTEXT_KEY)
        if type(context) is not TrustedEgressContext:
            raise InvalidTaskEnvelope() from None
        return context
    if type(envelope) is not dict or TRUSTED_EGRESS_CONTEXT_KEY in envelope:
        raise InvalidTaskEnvelope() from None
    return None


class LeafEgress(Enum):
    """leaf 会不会出网。这是分发判据本身，与它的签名长什么样无关。"""

    LOCAL = "local"
    """纯本地：ffmpeg/subprocess，不出网、不取凭证。组织一律放行。"""

    NETWORK = "network"
    """出网：必须声明并接收 `egress_context`，由本函数注入。"""

    DENIED = "denied"
    """已知出网但尚未声明契约：组织下拒。与「未列入表」同待遇，列出来只为留证据。"""


@dataclass(frozen=True, slots=True)
class LeafEgressRule:
    module: str
    egress: LeafEgress
    eg_id: str


# 键是**调用点显式给出的名字**，不是函数对象：19 个调用点的 import 都在 runner
# 函数体内，测试替换掉的是那个局部名，模块级建的对象表必然查不中；`leaf.__name__`
# 同理（假 leaf 叫别的名字）。名字由调用点提供，替身也就盖不住分类。
FREEZONE_LEAF_EGRESS: dict[str, LeafEgressRule] = {
    # EG-20a `tool.local.restricted`（egress-inventory.md:54，`service/local`）
    "run_freezone_extract_frames": LeafEgressRule(
        "novelvideo.freezone.jobs", LeafEgress.LOCAL, "EG-20a"
    ),
    "run_freezone_video_upscale": LeafEgressRule(
        # [aipdd fork delta] 高清优先走火山 MediaKit（直连第三方主机）：从
        # EG-20a 本地叶升为出网叶。个人/平台直连，组织 fail-closed 回退 ffmpeg。
        "novelvideo.freezone.jobs",
        LeafEgress.NETWORK,
        "EG-20b",
    ),
    "run_freezone_video_compose": LeafEgressRule(
        "novelvideo.freezone.jobs", LeafEgress.LOCAL, "EG-20a"
    ),
    "run_freezone_video_erase": LeafEgressRule(
        # [aipdd fork delta] 智能去字幕优先走火山 MediaKit（字幕擦除精细化版，
        # 直连第三方主机）：从 EG-20a 本地叶升为出网叶。个人/平台直连，
        # 组织 fail-closed 回退 ffmpeg delogo。
        "novelvideo.freezone.jobs",
        LeafEgress.NETWORK,
        "EG-20b",
    ),
    "run_freezone_audio_separate": LeafEgressRule(
        "novelvideo.freezone.jobs", LeafEgress.LOCAL, "EG-20a"
    ),
    # EG-18b `freezone.image.generate`（:52，`gateway-routed`）
    "run_freezone_gen": LeafEgressRule(
        "novelvideo.freezone.jobs", LeafEgress.NETWORK, "EG-18b"
    ),
    "run_freezone_edit": LeafEgressRule(
        "novelvideo.freezone.jobs", LeafEgress.NETWORK, "EG-18b"
    ),
    "run_freezone_mask_edit": LeafEgressRule(
        "novelvideo.freezone.jobs", LeafEgress.NETWORK, "EG-18b"
    ),
    "run_freezone_analyze_shots": LeafEgressRule(
        "novelvideo.freezone.jobs", LeafEgress.NETWORK, "EG-18b"
    ),
    "reverse_prompt_from_image": LeafEgressRule(
        "novelvideo.freezone.image_node", LeafEgress.NETWORK, "EG-18b"
    ),
    # EG-18a `freezone.text.generate` / `.structured`（:51，`gateway-routed`）
    "translate_freezone_text": LeafEgressRule(
        "novelvideo.freezone.text_node", LeafEgress.NETWORK, "EG-18a"
    ),
    "generate_freezone_text": LeafEgressRule(
        "novelvideo.freezone.text_node", LeafEgress.NETWORK, "EG-18a"
    ),
    "generate_freezone_story_script": LeafEgressRule(
        "novelvideo.freezone.text_node", LeafEgress.NETWORK, "EG-18a"
    ),
    "generate_freezone_story_script_with_vision": LeafEgressRule(
        "novelvideo.freezone.text_node", LeafEgress.NETWORK, "EG-18a"
    ),
    # EG-15a `audio.tts.gateway`（:46，`gateway-routed`）
    "generate_freezone_audio_speech": LeafEgressRule(
        "novelvideo.freezone.audio_node", LeafEgress.NETWORK, "EG-15a"
    ),
    # 音乐与语音是同一个出网点的两个一跳调用方：都经 `_write_newapi_audio_speech`，
    # claim 的 capability 也都是 `audio.tts.gateway`（`audio_node.py:558`）。
    "generate_freezone_audio_eleven_music": LeafEgressRule(
        "novelvideo.freezone.audio_node", LeafEgress.NETWORK, "EG-15a"
    ),
    # [aipdd fork delta] EG-15a 同族：替换人声叶内部含两类出网——ASR 经网关
    # multipart 上传本地音频（``_transcribe_via_gateway`` 走
    # ``begin_trusted_audio_operation``）+ IndexTTS2 零样本合成经网关/aipdd 直连；
    # 视频抽音轨是纯本地 ffmpeg，不出网。组织任务须选网关 ASR（直连通道在叶内
    # fail-closed），受信 ``egress_context`` 由分发器注入本叶再透传给 ASR/TTS。
    "run_freezone_voice_replace": LeafEgressRule(
        "novelvideo.freezone.voice_replace", LeafEgress.NETWORK, "EG-15a"
    ),
    # EG-15a 同族：视频配音（与 voice_replace 同编排，末端多一步本地音轨回填）。
    # ASR 网关 multipart + TTS 网关/aipdd 出走；解封装与音轨回填是纯本地 ffmpeg 不出网。
    "run_freezone_video_dubbing": LeafEgressRule(
        "novelvideo.freezone.dubbing", LeafEgress.NETWORK, "EG-15a"
    ),
    # EG-15b：抖音文案提取（与 EG-15a 相邻，主出网点同为音频转写经网关）。
    # [aipdd fork delta] 另含两类出网：抖音页面/CDN 的无凭据公开读取（个人与
    # 组织均放行，不涉平台凭据与组织数据外发）与 aipdd shared-tasks / 火山
    # openspeech 直连（组织 fail-closed，见 douyin_copywrite.run_freezone_douyin_copywrite）。
    "run_freezone_douyin_copywrite": LeafEgressRule(
        "novelvideo.freezone.douyin_copywrite", LeafEgress.NETWORK, "EG-15b"
    ),
    # EG-15c：req21 音视频提取文案（与 EG-15b 同族，复用同一批 ASR / 媒体出网）。
    # 出网构成：yt-dlp 拉取公开站点/CDN（无凭据、个人与组织均放行）+ 网关 ASR
    # （受信出网）+ FunASR / 豆包语音直连（组织 fail-closed，见 media_copywrite）。
    "run_freezone_media_copywrite": LeafEgressRule(
        "novelvideo.freezone.media_copywrite", LeafEgress.NETWORK, "EG-15c"
    ),
    # EG-15c 同族：视频复刻的参考视频台词转写。本地 ffmpeg 抽音轨不出网，转写复用
    # media_copywrite 的 ``_transcribe``（网关 multipart 受信出网 / 豆包直连在叶内的
    # 候选选择阶段就对组织排除）。与 voice_replace 同为「单叶内部多跳出网」。
    "transcribe_freezone_clone_dialogue": LeafEgressRule(
        "novelvideo.freezone.jobs", LeafEgress.NETWORK, "EG-15c"
    ),
    # EG-15d：req46 工具页「音乐创作」（MiniMax Music 3.0 直连，不经网关）。直连
    # 使用设置里配置的渠道 Key，平台侧凭据不得在组织任务下打第三方主机：叶内
    # ``deny_organization_egress`` fail-closed（与 MediaKit / 豆包语音同一取舍）。
    "run_freezone_music_generate": LeafEgressRule(
        "novelvideo.freezone.minimax_music", LeafEgress.NETWORK, "EG-15d"
    ),
    # EG-09a/09b：经 GridGenerator 出图，newapi 走网关，其余 provider 在
    # `grid_generation/egress.py:50` 对组织抛 ORG_EGRESS_DENIED。
    "convert_control_frame_to_sketch": LeafEgressRule(
        "novelvideo.director_world.control_frame_to_sketch",
        LeafEgress.NETWORK,
        "EG-09a",
    ),
}


def _call_freezone_leaf(
    envelope: dict[str, Any],
    leaf,
    leaf_name: str,
    /,
    **kwargs: Any,
):
    """按 leaf 是否出网分发。`leaf_name` 必填——漏传是 TypeError，不是静默放行。"""

    context = _extract_trusted_egress_context(envelope)
    rule = FREEZONE_LEAF_EGRESS.get(leaf_name)
    if rule is not None:
        if rule.egress is LeafEgress.LOCAL:
            return leaf(**kwargs)
        if rule.egress is LeafEgress.NETWORK:
            # 组织任务必须携带受信任的组织身份以走网关；平台与个人任务
            # 保持无组织上下文的既有直连语义，不能误触发组织出网闸门。
            if context is not None and context.is_organization:
                return leaf(egress_context=context, **kwargs)
            return leaf(**kwargs)
    # 未分类与 DENIED 同待遇：默认 fail-closed。
    if context is not None and context.is_organization:
        raise InvalidTaskEnvelope() from None
    return leaf(**kwargs)


def _update(
    ctx: ProjectContext,
    task_type: str,
    scope: str,
    progress: float,
    current_task: MessageLike,
    *,
    episode: int = 0,
) -> None:
    get_task_manager().update_progress_for_project(
        ctx,
        task_type,
        int(episode),
        scope=scope,
        progress=progress,
        current_task=current_task,
        logs=[current_task],
    )


def _append_node_history(
    *,
    ctx: ProjectContext,
    project_dir: Path,
    payload: dict[str, Any],
    task_type: str,
    job_id: str,
    media_type: str,
    result: dict[str, Any],
    error: str | None = None,
    episode: int = 0,
    beat_num: int | None = None,
    scope: str | None = None,
    **extra: Any,
) -> dict[str, Any] | None:
    node_id = str(payload.get("node_id") or "").strip()
    if not node_id:
        return None
    from novelvideo.freezone.history import (
        append_generation_history,
        build_node_history_record,
    )

    # Text/audio nodes carry the user text under "input"; image nodes use "prompt".
    record = build_node_history_record(
        task_type=task_type,
        job_id=job_id,
        task_key=project_task_state_key(
            task_type,
            ctx.project_id,
            int(episode),
            beat_num=beat_num,
            scope=scope or job_id,
        ),
        status="failed" if error else "completed",
        media_type=media_type,
        result=result,
        error=error,
        prompt=payload.get("prompt") or payload.get("input"),
        extra=extra,
    )

    return append_generation_history(
        project_dir=project_dir,
        canvas_id=str(payload.get("canvas_id") or "default"),
        node_id=node_id,
        record=record,
    )


def _history_model_mode_extra(payload: dict) -> dict:
    """记忆包：把生成请求里的注册表 model id / 生成模式映射到历史记录顶层字段。

    仅非空时写入，缺省省略（向后兼容，还原时回退默认）。
    """
    extra: dict[str, str] = {}
    model_id = payload.get("model_id")
    if model_id:
        extra["model"] = str(model_id)
    gen_mode = payload.get("gen_mode")
    if gen_mode:
        extra["gen_mode"] = str(gen_mode)
    return extra


# [aipdd fork delta] 工具页批量任务提交时带 archive_to_library=true，产物产出后
# 自动登记进资产库「待分类资产」；画布等其它入口不带该标记，行为逐字不变。
_TOOL_ARCHIVE_LABELS = {
    "freezone_edit": "图片处理",
    "freezone_mask_edit": "图片处理",
    "freezone_video_erase": "视频去水印",
    "freezone_video_gen": "视频生成",
    "freezone_audio_speech": "生成语音",
    # [aipdd fork delta] 替换人声：产物是目标音色新声的纯音频，同 audio 归档形态。
    "freezone_voice_replace": "替换人声",
    # [aipdd fork delta] 视频配音：产物是回填配音音轨的 mp4 视频，同 video 归档形态。
    "freezone_video_dubbing": "视频配音",
    "freezone_video_clone": "视频复刻",
    "freezone_video_mix": "混剪短视频",
    "freezone_video_idea": "一句话短视频",
    # [aipdd fork delta] 白板动画：产物是一条逐笔揭示的白板手绘成片。
    "freezone_whiteboard_animation": "白板动画",
    # [aipdd fork delta] 科普讲解视频：产物是一条逐帧代码绘制的横屏 MG 讲解成片。
    "freezone_explainer_video": "科普讲解视频",
    # [aipdd fork delta] 动效大片渲染：产物是一条按案例契约逐帧截图的 MG 成片。
    "freezone_motion_render": "动效大片",
    # [aipdd fork delta req46] 音乐创作：产物是一首 mp3/wav/pcm 音频。
    "freezone_music_generate": "音乐创作",
    # [aipdd fork delta] 口播智能剪辑：产物是剪后口播成片（或 VectCut 剪映草稿）。
    "freezone_narration_smart_edit": "口播智能剪辑",
}


def _archive_stem_from_text(text: Any) -> str:
    """音频归档取文本首行前 20 字（去掉换行/空白），没有就返回空串。"""
    line = next(
        (part.strip() for part in str(text or "").splitlines() if part.strip()), ""
    )
    return line[:20]


def _archive_tool_output_to_library(
    *,
    ctx: ProjectContext,
    payload: dict[str, Any],
    task_type: str,
    job_id: str,
    media: str,
    url: str,
    result: dict[str, Any],
    stem: str = "",
) -> dict[str, Any] | None:
    """按提交方声明的标记，把工具产物登记进资产库「待分类资产」。

    仅当 payload 带 ``archive_to_library`` 时执行；登记失败只记日志，不影响任务
    完成（产物与任务记录的「打开结果」链接都不依赖资产库写入）。条目名取来源
    素材名 + 工具标签，如「旅行照·视频去水印」；无来源名时用任务号后缀兜底。
    归档成功时给任务 ``result`` 附加 ``archived_to_library`` / ``library_item_id``
    标记——任务记录据此证明产物已入库，前端任务列表也靠它显示提示。
    """
    if not payload.get("archive_to_library"):
        return None
    from novelvideo.freezone.video_node import (
        TOOL_LIBRARY_ITEM_ID_PREFIX,
        archive_generated_library_item,
    )

    label = _TOOL_ARCHIVE_LABELS.get(task_type) or "工具生成"
    clean = str(stem or "").strip()[:40]
    name = f"{clean}·{label}" if clean else f"{label} {job_id[-6:]}"
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    item = archive_generated_library_item(
        project_dir,
        item_id=f"{TOOL_LIBRARY_ITEM_ID_PREFIX}{job_id}",
        name=name,
        media=media,
        url=url,
    )
    if item:
        result["archived_to_library"] = True
        result["library_item_id"] = str(item.get("id") or "")
    return item


async def _run_freezone_gen_async(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.jobs import ensure_freezone_dirs, run_freezone_gen

    payload = envelope.get("payload") or {}
    task_type = str(envelope.get("task_type") or "freezone_gen")
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(ctx, task_type, job_id, 0.1, "调用图像生成器...")
    out_path = await _await_with_cancel_watch(
        _call_freezone_leaf(
            envelope,
            run_freezone_gen,
            "run_freezone_gen",
            project_dir=project_dir,
            job_id=job_id,
            prompt=str(payload.get("prompt") or ""),
            aspect_ratio=str(payload.get("aspect_ratio") or "1:1"),
            image_size=str(payload.get("image_size") or "2K"),
            reference_paths=payload.get("reference_paths") or None,
            provider=payload.get("provider"),
            model=payload.get("model"),
            quality=payload.get("quality"),
            model_params=payload.get("model_params") or None,
            request_schema=payload.get("request_schema") or None,
            output_task_type=task_type,
        ),
        project_id=ctx.project_id,
        task_type=task_type,
        episode=0,
        task_id=str(envelope.get("__run_task_id") or ""),
        scope=job_id,
    )
    rel = out_path.relative_to(project_dir).as_posix()
    result = {
        "job_id": job_id,
        "output_path": str(out_path),
        "output_url": make_static_url_for_context(ctx, rel),
    }
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type=task_type,
        job_id=job_id,
        media_type="image",
        result=result,
        **_history_model_mode_extra(payload),
    )
    if history_record:
        result["generation_history_record"] = history_record
    return result


async def _run_freezone_edit_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.jobs import ensure_freezone_dirs, run_freezone_edit

    payload = envelope.get("payload") or {}
    task_type = str(envelope.get("task_type") or "freezone_edit")
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(ctx, task_type, job_id, 0.1, "调用图像编辑器...")
    out_path = await _await_with_cancel_watch(
        _call_freezone_leaf(
            envelope,
            run_freezone_edit,
            "run_freezone_edit",
            project_dir=project_dir,
            job_id=job_id,
            prompt=str(payload.get("prompt") or ""),
            base_path=str(payload["base_path"]),
            extra_reference_paths=payload.get("extra_reference_paths") or None,
            aspect_ratio=str(payload.get("aspect_ratio") or "1:1"),
            image_size=str(payload.get("image_size") or "2K"),
            provider=payload.get("provider"),
            model=payload.get("model"),
            quality=payload.get("quality"),
            model_params=payload.get("model_params") or None,
            request_schema=payload.get("request_schema") or None,
            output_task_type=task_type,
        ),
        project_id=ctx.project_id,
        task_type=task_type,
        episode=0,
        task_id=str(envelope.get("__run_task_id") or ""),
        scope=job_id,
    )
    rel = out_path.relative_to(project_dir).as_posix()
    result = {
        "job_id": job_id,
        "output_path": str(out_path),
        "output_url": make_static_url_for_context(ctx, rel),
    }
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type=task_type,
        job_id=job_id,
        media_type="image",
        result=result,
        **_history_model_mode_extra(payload),
    )
    if history_record:
        result["generation_history_record"] = history_record
    _archive_tool_output_to_library(
        ctx=ctx,
        payload=payload,
        task_type=task_type,
        job_id=job_id,
        media="image",
        url=str(result["output_url"]),
        result=result,
        stem=Path(str(payload.get("base_path") or "")).stem,
    )
    return result


async def _run_freezone_mask_edit_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.jobs import ensure_freezone_dirs, run_freezone_mask_edit

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    provider = str(payload.get("provider") or "newapi")
    _update(ctx, "freezone_mask_edit", job_id, 0.1, f"调用 {provider} 图片擦除...")
    out_path = await _call_freezone_leaf(
        envelope,
        run_freezone_mask_edit,
        "run_freezone_mask_edit",
        project_dir=project_dir,
        job_id=job_id,
        base_path=str(payload["base_path"]),
        mask_path=str(payload["mask_path"]),
        prompt=str(payload.get("prompt") or ""),
        aspect_ratio=str(payload.get("aspect_ratio") or "1:1"),
        image_size=str(payload.get("image_size") or "2K"),
        quality=str(payload.get("quality") or "medium"),
        provider=provider,
        model=str(payload.get("model") or ""),
    )
    rel = out_path.relative_to(project_dir).as_posix()
    return {
        "job_id": job_id,
        "output_path": str(out_path),
        "output_url": make_static_url_for_context(ctx, rel),
    }


async def _run_freezone_extract_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.jobs import (
        ensure_freezone_dirs,
        run_freezone_extract_frames,
    )

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(ctx, "freezone_extract", job_id, 0.1, "ffmpeg 抽帧中...")
    frame_paths = await _call_freezone_leaf(
        envelope,
        run_freezone_extract_frames,
        "run_freezone_extract_frames",
        project_dir=project_dir,
        job_id=job_id,
        video_path=Path(str(payload["video_path"])),
        max_frames=int(payload.get("max_frames") or 20),
        scene_threshold=float(payload.get("scene_threshold") or 0.3),
    )
    return {
        "job_id": job_id,
        "frame_count": len(frame_paths),
        "frame_urls": [
            make_static_url_for_context(ctx, path.relative_to(project_dir).as_posix())
            for path in frame_paths
        ],
        "frame_paths": [str(path) for path in frame_paths],
    }


async def _run_freezone_analyze_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.jobs import (
        ensure_freezone_dirs,
        run_freezone_analyze_shots,
    )

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    frame_paths = [str(path) for path in payload.get("frame_paths") or []]
    _update(
        ctx, "freezone_analyze", job_id, 0.1, f"Vision 分析 {len(frame_paths)} 帧..."
    )
    result = await _call_freezone_leaf(
        envelope,
        run_freezone_analyze_shots,
        "run_freezone_analyze_shots",
        project_dir=project_dir,
        job_id=job_id,
        frame_paths=frame_paths,
        provider=payload.get("provider"),
        model=payload.get("model"),
        analysis_mode=str(payload.get("analysis_mode") or "shots"),
        duration_sec=payload.get("duration_sec"),
    )
    output_path = Path(str(result["output_path"]))
    return {
        "job_id": job_id,
        "output_path": str(output_path),
        "output_url": make_static_url_for_context(
            ctx,
            output_path.relative_to(project_dir).as_posix(),
        ),
        "model": result.get("model"),
        "analysis_mode": result.get("analysis_mode"),
        "frame_count": result.get("frame_count"),
        "analyses": result.get("analyses"),
        "video_story": result.get("video_story"),
    }


async def _run_freezone_video_story_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.jobs import (
        ensure_freezone_dirs,
        run_freezone_analyze_shots,
        run_freezone_extract_frames,
    )

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(ctx, "freezone_video_story", job_id, 0.1, "ffmpeg 抽取关键帧...")
    frame_paths = await _call_freezone_leaf(
        envelope,
        run_freezone_extract_frames,
        "run_freezone_extract_frames",
        project_dir=project_dir,
        job_id=job_id,
        video_path=Path(str(payload["video_path"])),
        max_frames=int(payload.get("max_frames") or 20),
        scene_threshold=float(payload.get("scene_threshold") or 0.3),
    )
    frame_urls = [
        make_static_url_for_context(ctx, path.relative_to(project_dir).as_posix())
        for path in frame_paths
    ]
    _update(
        ctx,
        "freezone_video_story",
        job_id,
        0.55,
        f"Vision 解析 {len(frame_paths)} 帧为视频故事...",
    )
    result = await _call_freezone_leaf(
        envelope,
        run_freezone_analyze_shots,
        "run_freezone_analyze_shots",
        project_dir=project_dir,
        job_id=job_id,
        frame_paths=[str(path) for path in frame_paths],
        provider=payload.get("provider"),
        model=payload.get("model"),
        analysis_mode="video_story",
        duration_sec=payload.get("duration_sec"),
    )
    output_path = Path(str(result["output_path"]))
    return {
        "job_id": job_id,
        "output_url": make_static_url_for_context(
            ctx,
            output_path.relative_to(project_dir).as_posix(),
        ),
        "model": result.get("model"),
        "analysis_mode": "video_story",
        "frame_count": len(frame_paths),
        "frame_urls": frame_urls,
        "analyses": result.get("analyses"),
        "video_story": result.get("video_story"),
    }


def run_freezone_gen(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_gen_async(envelope, ctx))


def run_freezone_edit(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_edit_async(envelope, ctx))


def run_mainline_sketch_from_context(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(
        envelope, _run_mainline_sketch_from_context_async(envelope, ctx)
    )


def run_mainline_frame_from_context(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(
        envelope, _run_mainline_frame_from_context_async(envelope, ctx)
    )


async def _run_mainline_sketch_from_context_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.task_backend.runners.sketch import _run_sketch_generation_async

    payload = envelope.get("payload") or {}
    task_type = str(envelope.get("task_type") or "mainline_sketch_from_context")
    job_id = str(payload["job_id"])
    episode = int(envelope.get("episode") or payload.get("episode") or 0)
    beat_num = int(envelope.get("beat_num") or payload.get("beat_num") or 0)
    scope = str(envelope.get("scope") or job_id)
    project_dir = Path(
        str(payload.get("output_dir") or payload.get("project_dir") or ctx.output_dir)
    )

    result = await _run_sketch_generation_async(envelope, ctx)
    out_path = Path(str(result.get("sketch_path") or ""))
    if not out_path.exists():
        raise FileNotFoundError(f"mainline sketch output missing: {out_path}")
    rel = out_path.relative_to(project_dir).as_posix()
    response = {
        **result,
        "job_id": job_id,
        "output_path": str(out_path),
        "output_url": make_static_url_for_context(ctx, rel, local_path=out_path),
        "media_type": "image",
    }
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type=task_type,
        job_id=job_id,
        media_type="image",
        result=response,
        episode=episode,
        beat_num=beat_num,
        scope=scope,
    )
    if history_record:
        response["generation_history_record"] = history_record
    return response


async def _run_mainline_frame_from_context_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.task_backend.runners.render import _run_selected_regen_async

    payload = envelope.get("payload") or {}
    task_type = str(envelope.get("task_type") or "mainline_frame_from_context")
    job_id = str(payload["job_id"])
    episode = int(envelope.get("episode") or payload.get("episode") or 0)
    beat_num = int(envelope.get("beat_num") or payload.get("beat_num") or 0)
    scope = str(envelope.get("scope") or job_id)
    project_dir = Path(
        str(payload.get("output_dir") or payload.get("project_dir") or ctx.output_dir)
    )

    result = await _run_selected_regen_async(envelope, ctx, is_sketch=False)
    # Single-beat skill run (1x1): one grid → one rel path under project_dir.
    grid_paths = result.get("grid_paths") or {}
    rel = grid_paths.get(beat_num) or (
        next(iter(grid_paths.values())) if grid_paths else ""
    )
    if not rel:
        grid_results = result.get("grid_results") or []
        rel = str(grid_results[0].get("rel_path") or "") if grid_results else ""
    if not rel:
        raise FileNotFoundError("mainline frame output missing (no grid path)")
    out_path = (project_dir / rel).resolve()
    if not out_path.exists():
        raise FileNotFoundError(f"mainline frame output missing: {out_path}")
    rel = out_path.relative_to(project_dir).as_posix()
    response = {
        **result,
        "job_id": job_id,
        "output_path": str(out_path),
        "output_url": make_static_url_for_context(ctx, rel, local_path=out_path),
        "media_type": "image",
    }
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type=task_type,
        job_id=job_id,
        media_type="image",
        result=response,
        episode=episode,
        beat_num=beat_num,
        scope=scope,
    )
    if history_record:
        response["generation_history_record"] = history_record
    return response


async def _run_mainline_director_control_sketch_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.director_world.control_frame_to_sketch import (
        convert_control_frame_to_sketch,
    )
    from novelvideo.freezone.paths import output_path_for_job

    payload = envelope.get("payload") or {}
    task_type = str(envelope.get("task_type") or "mainline_director_control_sketch")
    job_id = str(payload["job_id"])
    episode = int(envelope.get("episode") or payload.get("episode") or 0)
    beat_num = int(envelope.get("beat_num") or payload.get("beat_num") or 0)
    scope = str(envelope.get("scope") or job_id)
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    state_dir = str(payload.get("state_dir") or ctx.state_dir)
    output_path = output_path_for_job(project_dir, task_type, job_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    _update(
        ctx,
        task_type,
        scope,
        0.05,
        f"开始 Beat {beat_num} 导演合成图转草图候选...",
        episode=episode,
    )
    result = await _await_with_cancel_watch(
        _call_freezone_leaf(
            envelope,
            convert_control_frame_to_sketch,
            "convert_control_frame_to_sketch",
            user=ctx.owner_username,
            project=ctx.project_name,
            episode=episode,
            beat=beat_num,
            mode_key=str(payload.get("mode_key") or ""),
            aspect_ratio=str(payload.get("aspect_ratio") or ""),
            output_dir=project_dir,
            state_dir=state_dir,
            control_frame_path=payload.get("control_frame_path") or None,
            require_control_frame_path=True,
            candidate_output_path=output_path,
            promote=False,
            projection=read_projection(payload),
        ),
        project_id=ctx.project_id,
        task_type=task_type,
        episode=episode,
        task_id=str(envelope.get("__run_task_id") or ""),
        beat_num=beat_num,
        scope=scope,
    )
    out_path = Path(str(result.get("output_path") or output_path))
    rel = out_path.relative_to(project_dir).as_posix()
    response = {
        **result,
        "job_id": job_id,
        "output_path": str(out_path),
        "output_url": make_static_url_for_context(ctx, rel, local_path=out_path),
        "media_type": "image",
    }
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type=task_type,
        job_id=job_id,
        media_type="image",
        result=response,
        episode=episode,
        beat_num=beat_num,
        scope=scope,
    )
    if history_record:
        response["generation_history_record"] = history_record
    _update(ctx, task_type, scope, 1.0, "导演合成图草图候选已生成", episode=episode)
    return response


def run_mainline_director_control_sketch(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(
        envelope, _run_mainline_director_control_sketch_async(envelope, ctx)
    )


def run_freezone_mask_edit(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_mask_edit_async(envelope, ctx))


def run_freezone_extract(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_extract_async(envelope, ctx))


def run_freezone_analyze(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_analyze_async(envelope, ctx))


def run_freezone_video_story(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_video_story_async(envelope, ctx))


async def _run_freezone_video_clone_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.clone import (
        build_video_clone_plan_prompt,
        normalize_clone_shots,
        parse_video_clone_plan,
        shot_reference_paths,
    )
    from novelvideo.freezone.jobs import (
        ensure_freezone_dirs,
        run_freezone_analyze_shots,
        run_freezone_extract_frames,
        stitch_freezone_video_clone_segments,
        transcribe_freezone_clone_dialogue,
    )
    from novelvideo.freezone.paths import outputs_dir
    from novelvideo.freezone.text_node import generate_freezone_text
    from novelvideo.freezone.video_node import run_trusted_freezone_video_gen
    from novelvideo.usage.video_duration import normalize_video_duration_for_backend

    payload = envelope.get("payload") or {}
    task_type = "freezone_video_clone"
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)

    video_path = Path(str(payload.get("video_path") or ""))
    if not video_path.exists():
        raise FileNotFoundError(f"reference video not found: {video_path}")
    try:
        duration_sec = float(payload.get("duration_sec") or 0.0)
    except (TypeError, ValueError):
        duration_sec = 0.0
    if duration_sec <= 0:
        from novelvideo.usage.video_billing import probe_video_duration_seconds

        duration_sec = await probe_video_duration_seconds(video_path.as_posix())
    reference_mode = str(payload.get("reference_mode") or "frames").strip().lower()
    audio_mode = str(payload.get("audio_mode") or "generate").strip().lower()
    if audio_mode not in {"generate", "mute", "source"}:
        audio_mode = "generate"
    backend = str(payload.get("backend") or "newapi_seedance-2.0-fast")
    # [aipdd fork delta] 逐镜复刻规划的用户显式选型（文本步骤）；空 = 系统默认链。
    model_text = str(payload.get("model_text") or "").strip()
    # [aipdd fork delta] 参考内容：用户提供的参考图（≤4 张），逐镜追加到 references。
    reference_paths = [
        str(path).strip()
        for path in (payload.get("reference_paths") or [])
        if str(path or "").strip()
    ]
    # [aipdd fork delta] 带参考内容时的优先级：subject 主体替换优先（不提交原视频
    # 关键帧——实测 MiniMax H3 同时收到关键帧与参考图时只会整体还原关键帧里的主体，
    # 排序救不了）；composition 忠实构图优先（关键帧与参考图一起提交，接受主体不变）。
    reference_priority = str(payload.get("reference_priority") or "subject").strip().lower()
    if reference_priority not in {"subject", "composition"}:
        reference_priority = "subject"
    subject_reference_only = bool(reference_paths) and reference_priority == "subject"
    generate_audio = bool(payload.get("generate_audio"))

    cancel_kwargs = {
        "project_id": ctx.project_id,
        "task_type": task_type,
        "episode": 0,
        "task_id": str(envelope.get("__run_task_id") or ""),
        "scope": job_id,
    }

    # 1) 抽帧（本地 ffmpeg leaf）。
    _update(ctx, task_type, job_id, 0.03, "ffmpeg 抽取参考视频关键帧...")
    frame_paths = await _await_with_cancel_watch(
        _call_freezone_leaf(
            envelope,
            run_freezone_extract_frames,
            "run_freezone_extract_frames",
            project_dir=project_dir,
            job_id=job_id,
            video_path=video_path,
            max_frames=int(payload.get("max_frames") or 12),
            scene_threshold=float(payload.get("scene_threshold") or 0.3),
        ),
        **cancel_kwargs,
    )

    # 2) 解读视频故事（视觉 leaf）。
    _update(
        ctx, task_type, job_id, 0.12, f"Vision 解读 {len(frame_paths)} 帧为视频故事..."
    )
    analysis = await _await_with_cancel_watch(
        _call_freezone_leaf(
            envelope,
            run_freezone_analyze_shots,
            "run_freezone_analyze_shots",
            project_dir=project_dir,
            job_id=job_id,
            frame_paths=[str(path) for path in frame_paths],
            analysis_mode="video_story",
            duration_sec=duration_sec or None,
        ),
        **cancel_kwargs,
    )
    shots = normalize_clone_shots(analysis.get("video_story"))
    if not shots:
        raise RuntimeError("video story analysis returned no shots")

    # 3) 台词转写：参考视频人声 → 逐字台词（只在由模型发声时必要）。
    # 音画同步类视频模型只按提示词发声，实测提示词里没有声音设计时产物 ASR 直接
    # 转写为空（完全没有人声），写入台词则逐字复现原句；而参考音频字段（audio_urls /
    # reference_audio_urls / audio_url）在 AIPDD /v1/videos 上会被静默忽略。台词叶是
    # best-effort：拿不到台词返回空串，复刻照跑。
    dialogue_text = ""
    if audio_mode == "generate" and generate_audio:
        _update(ctx, task_type, job_id, 0.16, "转写参考视频人声为台词...")
        dialogue_text = str(
            await _await_with_cancel_watch(
                _call_freezone_leaf(
                    envelope,
                    transcribe_freezone_clone_dialogue,
                    "transcribe_freezone_clone_dialogue",
                    project_dir=project_dir,
                    job_id=job_id,
                    video_path=video_path.as_posix(),
                ),
                **cancel_kwargs,
            )
            or ""
        ).strip()

    # 4) 复刻规划：一次文本调用改写全部镜头 prompt。
    _update(ctx, task_type, job_id, 0.22, "规划逐镜复刻提示词...")
    plan_prompt = build_video_clone_plan_prompt(
        shots=shots,
        frame_count=len(frame_paths),
        user_prompt=str(payload.get("prompt") or ""),
        reference_mode=reference_mode,
        content_reference_count=len(reference_paths),
        audio_mode=audio_mode,
        dialogue_text=dialogue_text,
        subject_reference_only=subject_reference_only,
    )
    _, plan_text = await _await_with_cancel_watch(
        _call_freezone_leaf(
            envelope,
            generate_freezone_text,
            "generate_freezone_text",
            prompt=plan_prompt,
            model=model_text or None,
        ),
        **cancel_kwargs,
    )
    plan = parse_video_clone_plan(
        plan_text,
        shots,
        include_audio=audio_mode == "generate" and generate_audio,
    )
    if not plan:
        raise RuntimeError("video clone plan is empty")

    # 5) 逐镜生成。org/local 带受信任出网上下文；platform 保持直连语义。
    trusted_context = _extract_trusted_egress_context(envelope)
    video_egress: Any = None
    if (
        trusted_context is not None
        and trusted_context.billing_principal.kind in {"organization", "local"}
    ):
        video_egress = trusted_context
    total_shots = len(plan)
    segment_paths: list[Path] = []
    for index, item in enumerate(plan, start=1):
        _update(
            ctx,
            task_type,
            job_id,
            round(0.30 + (index - 1) / total_shots * 0.58, 3),
            f"生成第 {index}/{total_shots} 个复刻镜头...",
        )
        references: list[dict[str, str]] = []
        if reference_mode == "frames" and not subject_reference_only:
            source_shot = shots[index - 1] if index - 1 < len(shots) else {}
            frame_refs = shot_reference_paths(
                source_shot,
                frame_paths,
                duration_sec=duration_sec or None,
            )
            references.extend(
                {"type": "image", "path": path, "role": "reference"}
                for path in frame_refs
            )
        # 用户参考内容（角色/场景/风格参照）随每一镜提交，role 与原帧参考区分。
        references.extend(
            {"type": "image", "path": path, "role": "参考内容"}
            for path in reference_paths
        )
        reference_items = references or None
        segment_path = await _await_with_cancel_watch(
            run_trusted_freezone_video_gen(
                project_dir=project_dir,
                job_id=f"{job_id}_s{index:02d}",
                prompt=str(item.get("video_prompt") or ""),
                egress_context=video_egress,
                task_type=task_type,
                episode=int(envelope.get("episode") or 0),
                beat_num=int(envelope.get("beat_num") or 0),
                scope=job_id,
                reference_items=reference_items,
                aspect_ratio=str(payload.get("aspect_ratio") or "16:9"),
                resolution=str(payload.get("resolution") or "720p"),
                duration_seconds=normalize_video_duration_for_backend(
                    backend, float(item.get("target_duration") or 5.0)
                ),
                generate_audio=generate_audio,
                human_review=bool(payload.get("human_review")),
                scene_optimize=payload.get("scene_optimize") or None,
                backend=backend,
                gen_mode="all_reference" if reference_items else None,
                model_params=payload.get("model_params") or None,
                request_schema=payload.get("request_schema") or None,
            ),
            **cancel_kwargs,
        )
        segment_paths.append(Path(segment_path))

    # 6) 按镜头原时长裁齐并拼接成片。
    _update(ctx, task_type, job_id, 0.92, "拼接逐镜复刻片段...")
    output_path = outputs_dir(project_dir, "freezone_video_clone") / f"{job_id}.mp4"
    await stitch_freezone_video_clone_segments(
        segment_paths=segment_paths,
        shot_durations=[float(item.get("target_duration") or 0.0) for item in plan],
        output_path=output_path,
        audio_mode=audio_mode,
        source_video_path=video_path,
    )
    _update(ctx, task_type, job_id, 0.97, "登记复刻成片...")
    rel = output_path.relative_to(project_dir).as_posix()
    result: dict[str, Any] = {
        "job_id": job_id,
        "output_path": str(output_path),
        "output_url": make_static_url_for_context(ctx, rel),
        "media_type": "video",
        "shot_count": total_shots,
        "reference_mode": reference_mode,
        "reference_count": len(reference_paths),
        "reference_priority": reference_priority,
        "subject_reference_only": subject_reference_only,
        "audio_mode": audio_mode,
        "dialogue_text": dialogue_text,
        "model": analysis.get("model"),
        "video_story": analysis.get("video_story"),
        "plan": [
            {
                "shot": index,
                "duration": round(float(item.get("target_duration") or 0.0), 3),
                "video_prompt": str(item.get("video_prompt") or ""),
            }
            for index, item in enumerate(plan, start=1)
        ],
        "segment_urls": [
            make_static_url_for_context(ctx, path.relative_to(project_dir).as_posix())
            for path in segment_paths
        ],
    }
    _archive_tool_output_to_library(
        ctx=ctx,
        payload=payload,
        task_type=task_type,
        job_id=job_id,
        media="video",
        url=str(result["output_url"]),
        result=result,
        stem=video_path.stem,
    )
    return result


def run_freezone_video_clone(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_video_clone_async(envelope, ctx))


async def _run_freezone_video_mix_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import (
        make_sqlite_store_for_context,
        make_static_url_for_context,
    )
    from novelvideo.freezone.audio_node import generate_freezone_audio_speech
    from novelvideo.freezone.jobs import (
        ensure_freezone_dirs,
        run_freezone_video_mix_render,
    )
    from novelvideo.freezone.mix import (
        MAX_MIX_DURATION_SECONDS,
        MIX_FPS,
        MixMaterial,
        build_mix_script_prompt,
        build_mix_subtitle_cues,
        format_srt,
        normalize_mix_aspect_ratio,
        normalize_mix_resolution,
        normalize_mix_subtitle_font_size,
        plan_mix_clips,
        resolve_mix_canvas,
    )
    from novelvideo.freezone.paths import outputs_dir
    from novelvideo.freezone.text_node import generate_freezone_text
    from novelvideo.usage.video_billing import probe_video_duration_seconds

    payload = envelope.get("payload") or {}
    task_type = "freezone_video_mix"
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)

    material_paths = [str(item) for item in (payload.get("material_paths") or [])]
    material_kinds = [
        "image" if str(item).strip().lower() == "image" else "video"
        for item in (payload.get("material_kinds") or [])
    ]
    if not material_paths or len(material_paths) != len(material_kinds):
        raise ValueError("mix materials are required")
    for path in material_paths:
        if not Path(path).exists():
            raise FileNotFoundError(f"mix material not found: {path}")

    audio_mode = str(payload.get("audio_mode") or "generate").strip().lower()
    if audio_mode not in {"generate", "mute"}:
        audio_mode = "generate"
    fit_mode = str(payload.get("fit_mode") or "cover").strip().lower()
    fit_mode = "contain" if fit_mode == "contain" else "cover"
    transition = str(payload.get("transition") or "fade").strip().lower()
    transition = "fade" if transition == "fade" else "none"
    aspect_ratio = normalize_mix_aspect_ratio(str(payload.get("aspect_ratio") or ""))
    resolution = normalize_mix_resolution(str(payload.get("resolution") or ""))
    canvas_width, canvas_height = resolve_mix_canvas(aspect_ratio, resolution)
    try:
        clip_cap = float(payload.get("clip_duration") or 3.0)
    except (TypeError, ValueError):
        clip_cap = 3.0
    clip_cap = max(1.0, min(10.0, clip_cap))

    cancel_kwargs = {
        "project_id": ctx.project_id,
        "task_type": task_type,
        "episode": 0,
        "task_id": str(envelope.get("__run_task_id") or ""),
        "scope": job_id,
    }

    # 1) 口播文案：手写优先；只给主题时先过一次文本 leaf。
    # [aipdd fork delta] 文案生成模型的用户显式选型（文本步骤）；空 = 系统默认链。
    model_text = str(payload.get("model_text") or "").strip()
    script = str(payload.get("script") or "").strip()
    subject = str(payload.get("subject") or "").strip()
    script_model = ""
    if audio_mode == "generate" and not script:
        if not subject:
            raise ValueError("subject or script is required for a voiced mix")
        _update(ctx, task_type, job_id, 0.05, "生成混剪口播文案...")
        script_model, script_text = await _await_with_cancel_watch(
            _call_freezone_leaf(
                envelope,
                generate_freezone_text,
                "generate_freezone_text",
                prompt=build_mix_script_prompt(subject=subject),
                model=model_text or None,
            ),
            **cancel_kwargs,
        )
        script = str(script_text or "").strip()
        if not script:
            raise RuntimeError("mix script generation returned empty text")

    # 2) 配音（generate 模式）；静音模式跳过。
    narration_path = ""
    narration_ms = 0
    narration_model = ""
    if audio_mode == "generate":
        _update(ctx, task_type, job_id, 0.15, "合成混剪配音...")
        # 音色所需项目态在投递时已定型放进 payload（与画布音频节点同一机制）。
        projection = read_projection(payload)
        store = None if projection is not None else await make_sqlite_store_for_context(ctx)
        try:
            speech = await _await_with_cancel_watch(
                _call_freezone_leaf(
                    envelope,
                    generate_freezone_audio_speech,
                    "generate_freezone_audio_speech",
                    store=store,
                    username=ctx.owner_username,
                    project=ctx.project_name,
                    account_voice_username=str(
                        payload.get("account_voice_username")
                        or ctx.requester_username
                        or ctx.owner_username
                    ),
                    project_dir=project_dir,
                    job_id=f"{job_id}_voice",
                    text=script,
                    emotion_prompt=str(payload.get("emotion_prompt") or ""),
                    voice_ref=payload.get("voice_ref"),
                    projection=projection,
                    model=str(payload.get("model") or ""),
                ),
                **cancel_kwargs,
            )
        finally:
            close = getattr(store, "close", None)
            if close:
                await close()
        narration_path = str(speech.audio_path)
        narration_ms = int(speech.duration_ms or 0)
        narration_model = str(speech.model or "")
        if narration_ms <= 0:
            raise RuntimeError("mix narration duration is invalid")

    # 3) 字幕：只在配音驱动且用户显式选择烧录时生成（静音没有时间轴）。
    srt_text = ""
    subtitle_font_size = normalize_mix_subtitle_font_size(
        str(payload.get("subtitle_font_size") or "")
    )
    if audio_mode == "generate" and bool(payload.get("subtitle_enabled", False)):
        srt_text = format_srt(build_mix_subtitle_cues(script, narration_ms))

    # 4) 素材探时长 → 顺序循环切段规划。
    _update(ctx, task_type, job_id, 0.30, "规划混剪素材切段...")
    materials: list[MixMaterial] = []
    for kind, path in zip(material_kinds, material_paths):
        duration = 0.0
        if kind == "video":
            try:
                duration = float(await probe_video_duration_seconds(path))
            except (OSError, ValueError) as exc:
                raise RuntimeError(
                    f"unable to read mix material duration: {path}"
                ) from exc
        materials.append(MixMaterial(kind=kind, path=path, duration=duration))

    target_seconds: float | None = None
    if audio_mode == "generate" and narration_ms > 0:
        # 与 MPT 一样由配音驱动成片时长，留 0.3s 画面余量。
        target_seconds = narration_ms / 1000.0 + 0.3
    else:
        raw_max = payload.get("max_duration_seconds")
        if raw_max is not None:
            try:
                target_seconds = max(
                    5.0, min(MAX_MIX_DURATION_SECONDS, float(raw_max))
                )
            except (TypeError, ValueError):
                target_seconds = None
    plan = plan_mix_clips(materials, clip_cap=clip_cap, target_seconds=target_seconds)

    # 5) 渲染成片（逐段→拼接→字幕/混音，纯本地 ffmpeg）。
    output_path = outputs_dir(project_dir, task_type) / f"{job_id}.mp4"
    bgm_volume = payload.get("bgm_volume")
    await run_freezone_video_mix_render(
        job_id=job_id,
        material_paths=material_paths,
        material_kinds=material_kinds,
        plan=[
            {
                "material_index": item.material_index,
                "source_start": item.source_start,
                "duration": item.duration,
                "loop": item.loop,
            }
            for item in plan
        ],
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        fps=MIX_FPS,
        fit_mode=fit_mode,
        transition=transition,
        audio_mode=audio_mode,
        narration_path=narration_path or None,
        bgm_path=str(payload.get("bgm_path") or "") or None,
        bgm_volume=(None if bgm_volume is None else float(bgm_volume)),
        subtitle_srt_text=srt_text,
        subtitle_font_size=subtitle_font_size,
        output_path=output_path,
        on_progress=lambda fraction, message: _update(
            ctx, task_type, job_id, round(0.35 + fraction * 0.55, 3), message
        ),
    )

    # 6) 归档与结果。
    _update(ctx, task_type, job_id, 0.97, "登记混剪成片...")
    rel = output_path.relative_to(project_dir).as_posix()
    result: dict[str, Any] = {
        "job_id": job_id,
        "output_path": str(output_path),
        "output_url": make_static_url_for_context(ctx, rel),
        "media_type": "video",
        "script": script,
        "subject": subject,
        "script_model": script_model,
        "audio_mode": audio_mode,
        "model": narration_model or script_model,
        "narration_duration_ms": narration_ms,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "fit_mode": fit_mode,
        "transition": transition,
        "clip_count": len(plan),
        "material_count": len(material_paths),
        "subtitle_enabled": bool(srt_text),
        "subtitle_font_size": subtitle_font_size,
    }
    _archive_tool_output_to_library(
        ctx=ctx,
        payload=payload,
        task_type=task_type,
        job_id=job_id,
        media="video",
        url=str(result["output_url"]),
        result=result,
        stem=_archive_stem_from_text(subject or script) or job_id,
    )
    return result


def run_freezone_video_mix(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_video_mix_async(envelope, ctx))


# --------------------------------------------------------------------------- #
# [aipdd fork delta] req 白板动画视频（freezone_whiteboard_animation）：粘贴口播
# 文案 → 分镜规划 → 逐幕暖米黄线稿出图 → 本地叠加重点文字 → 逐笔揭示单幕 clip
# → 复用混剪同源 ffmpeg 通道拼接 + 配音 + 字幕 → 成片入资产库。渲染/叠加/编码均
# 纯本地（OpenCV/numpy/Pillow/ffmpeg），三类生成叶（text/image/audio）经既有出网
# 分发；不新建独立任务存储、不复制上游模型。
# --------------------------------------------------------------------------- #
async def _run_freezone_whiteboard_animation_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import (
        make_sqlite_store_for_context,
        make_static_url_for_context,
    )
    from novelvideo.freezone.audio_node import generate_freezone_audio_speech
    from novelvideo.freezone.explainer_script import parse_whiteboard_script_draft
    from novelvideo.freezone.jobs import (
        ensure_freezone_dirs,
        run_freezone_gen,
        run_freezone_video_mix_render,
    )
    from novelvideo.freezone.mix import (
        MIX_FPS,
        build_mix_subtitle_cues,
        format_srt,
        normalize_mix_aspect_ratio,
        normalize_mix_resolution,
        normalize_mix_subtitle_font_size,
        resolve_mix_canvas,
    )
    from novelvideo.freezone.paths import outputs_dir
    from novelvideo.freezone.text_node import generate_freezone_text
    from novelvideo.freezone.whiteboard import (
        WHITEBOARD_MAX_SCENES,
        allocate_scene_durations_ms,
        build_whiteboard_script_prompt,
        compose_scene_image_prompt,
        overlay_key_text,
        parse_whiteboard_scenes,
        render_whiteboard_scene,
    )

    payload = envelope.get("payload") or {}
    task_type = "freezone_whiteboard_animation"
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    work_dir = outputs_dir(project_dir, task_type) / f"{job_id}_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    script = str(payload.get("script") or payload.get("idea") or "").strip()
    # [aipdd fork delta] 锁定稿（两段式第二段）：用户在台词草稿里逐幕改完后回传的
    # 整份草稿。旁白由逐幕文案拼回（TTS 仍是一次读完），时长按每幕实际字数分配；
    # 非法就失败并说清原因，不静默回退模型重新分幕。
    script_source = "model"
    draft = None
    locked = payload.get("locked_script")
    if isinstance(locked, dict) and locked:
        draft = parse_whiteboard_script_draft(locked)
        script = draft.script
        script_source = "draft"
    if not script:
        raise ValueError("whiteboard narration script is required")

    model_text = str(payload.get("model_text") or "").strip()
    aspect_ratio = normalize_mix_aspect_ratio(str(payload.get("aspect_ratio") or ""))
    resolution = normalize_mix_resolution(str(payload.get("resolution") or ""))
    canvas_width, canvas_height = resolve_mix_canvas(aspect_ratio, resolution)
    pen_cursor = bool(payload.get("pen_cursor", True))
    subtitle_enabled = bool(payload.get("subtitle_enabled", False))
    subtitle_font_size = normalize_mix_subtitle_font_size(
        str(payload.get("subtitle_font_size") or "")
    )
    try:
        max_scenes = int(payload.get("max_scenes") or 8)
    except (TypeError, ValueError):
        max_scenes = 8
    max_scenes = max(1, min(WHITEBOARD_MAX_SCENES, max_scenes))

    cancel_kwargs = {
        "project_id": ctx.project_id,
        "task_type": task_type,
        "episode": 0,
        "task_id": str(envelope.get("__run_task_id") or ""),
        "scope": job_id,
    }

    # 1) 分镜规划：文案切 N 幕 {scenePrompt, keyText, weight}。
    if draft is not None:
        _update(
            ctx,
            task_type,
            job_id,
            0.12,
            lmsg("tasks.progress.explainerDraft.locked", "按确认的台词草稿出片..."),
        )
        scenes = draft.as_scenes()
        _plan_model = "whiteboard-script-draft"
    else:
        _update(
            ctx,
            task_type,
            job_id,
            0.05,
            lmsg("tasks.progress.whiteboard.plan", "规划白板分镜..."),
        )
        _plan_model, plan_text = await _await_with_cancel_watch(
            _call_freezone_leaf(
                envelope,
                generate_freezone_text,
                "generate_freezone_text",
                prompt=build_whiteboard_script_prompt(script, max_scenes),
                model=model_text or None,
            ),
            **cancel_kwargs,
        )
        scenes = parse_whiteboard_scenes(plan_text)
        if not scenes:
            # 规划失败不致命：整段文案走单幕，画面交给线稿模型自由发挥。
            scenes = [{"scenePrompt": script[:120], "keyText": "", "weight": 1.0}]

    # 2) 配音：朗读全文，取总时长驱动逐幕时长分配。
    _update(ctx, task_type, job_id, 0.15, lmsg("tasks.progress.whiteboard.voice", "合成白板配音..."))
    projection = read_projection(payload)
    store = None if projection is not None else await make_sqlite_store_for_context(ctx)
    try:
        speech = await _await_with_cancel_watch(
            _call_freezone_leaf(
                envelope,
                generate_freezone_audio_speech,
                "generate_freezone_audio_speech",
                store=store,
                username=ctx.owner_username,
                project=ctx.project_name,
                account_voice_username=str(
                    payload.get("account_voice_username")
                    or ctx.requester_username
                    or ctx.owner_username
                ),
                project_dir=project_dir,
                job_id=f"{job_id}_voice",
                text=script,
                emotion_prompt=str(payload.get("emotion_prompt") or ""),
                voice_ref=payload.get("voice_ref"),
                projection=projection,
                model=str(payload.get("model") or ""),
            ),
            **cancel_kwargs,
        )
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()
    narration_path = str(speech.audio_path)
    narration_ms = int(speech.duration_ms or 0)
    narration_model = str(speech.model or "")
    if narration_ms <= 0:
        raise RuntimeError("whiteboard narration duration is invalid")

    durations = allocate_scene_durations_ms(scenes, narration_ms)

    # 3) 逐幕出图 → 本地叠加重点文字 → 逐笔揭示渲染单幕 clip。
    image_provider = payload.get("image_provider")
    image_model = payload.get("image_model")
    image_model_params = payload.get("image_model_params") or None
    image_request_schema = payload.get("image_request_schema") or None
    scene_clips: list[str] = []
    total = len(scenes)
    for index, (scene, scene_ms) in enumerate(zip(scenes, durations), start=1):
        _update(
            ctx,
            task_type,
            job_id,
            round(0.30 + 0.45 * (index - 1) / total, 3),
            f"生成白板线稿并逐笔揭示 {index}/{total}...",
        )
        gen = await _await_with_cancel_watch(
            _call_freezone_leaf(
                envelope,
                run_freezone_gen,
                "run_freezone_gen",
                project_dir=project_dir,
                job_id=f"{job_id}_s{index}",
                prompt=compose_scene_image_prompt(scene["scenePrompt"]),
                aspect_ratio=aspect_ratio,
                image_size="2K",
                provider=image_provider,
                model=image_model,
                model_params=image_model_params,
                request_schema=image_request_schema,
                output_task_type=task_type,
            ),
            **cancel_kwargs,
        )
        raw_scene = Path(gen)
        keyed = raw_scene
        if scene["keyText"]:
            keyed = work_dir / f"scene_{index:02d}.png"
            await asyncio.to_thread(
                overlay_key_text,
                image_path=raw_scene,
                out_path=keyed,
                text=scene["keyText"],
                width=canvas_width,
                height=canvas_height,
            )
        clip = work_dir / f"clip_{index:02d}.mp4"
        await asyncio.to_thread(
            render_whiteboard_scene,
            image_path=keyed,
            out_path=clip,
            duration_ms=scene_ms,
            width=canvas_width,
            height=canvas_height,
            fps=MIX_FPS,
            pen_cursor=pen_cursor,
        )
        scene_clips.append(str(clip))

    # 4) 字幕：仅在配音驱动且用户显式烧录时生成。
    srt_text = ""
    if subtitle_enabled:
        srt_text = format_srt(build_mix_subtitle_cues(script, narration_ms))

    # 5) 合成成片：复用混剪同源 ffmpeg 通道做拼接 + 配音混音 + 字幕烧录。
    _update(ctx, task_type, job_id, 0.80, lmsg("tasks.progress.whiteboard.assemble", "拼接白板动画成片..."))
    output_path = outputs_dir(project_dir, task_type) / f"{job_id}.mp4"
    bgm_volume = payload.get("bgm_volume")
    plan = [
        {
            "material_index": i,
            "source_start": 0.0,
            "duration": max(0.1, ms / 1000.0),
            "loop": False,
        }
        for i, ms in enumerate(durations)
    ]
    await run_freezone_video_mix_render(
        job_id=job_id,
        material_paths=scene_clips,
        material_kinds=["video"] * len(scene_clips),
        plan=plan,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        fps=MIX_FPS,
        fit_mode="contain",
        transition="none",
        audio_mode="generate",
        narration_path=narration_path,
        bgm_path=str(payload.get("bgm_path") or "") or None,
        bgm_volume=(None if bgm_volume is None else float(bgm_volume)),
        subtitle_srt_text=srt_text,
        subtitle_font_size=subtitle_font_size,
        output_path=output_path,
        on_progress=lambda fraction, message: _update(
            ctx, task_type, job_id, round(0.82 + fraction * 0.14, 3), message
        ),
    )

    # 6) 归档与结果。
    _update(ctx, task_type, job_id, 0.97, lmsg("tasks.progress.whiteboard.register", "登记白板动画成片..."))
    rel = output_path.relative_to(project_dir).as_posix()
    result: dict[str, Any] = {
        "job_id": job_id,
        "output_path": str(output_path),
        "output_url": make_static_url_for_context(ctx, rel),
        "media_type": "video",
        "script": script,
        "plan_model": _plan_model,
        "model": narration_model or _plan_model,
        # 台词来源：model = 本次模型切的幕；draft = 用户确认过的台词草稿。
        "script_source": script_source,
        "narration_duration_ms": narration_ms,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "scene_count": len(scenes),
        "pen_cursor": pen_cursor,
        "subtitle_enabled": bool(srt_text),
        "subtitle_font_size": subtitle_font_size,
    }
    _archive_tool_output_to_library(
        ctx=ctx,
        payload=payload,
        task_type=task_type,
        job_id=job_id,
        media="video",
        url=str(result["output_url"]),
        result=result,
        stem=_archive_stem_from_text(script) or job_id,
    )
    return result


def run_freezone_whiteboard_animation(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(
        envelope, _run_freezone_whiteboard_animation_async(envelope, ctx)
    )


# --------------------------------------------------------------------------- #
# [aipdd fork delta] 讲解类视频「台词草稿」（freezone_explainer_script）：两段式第一段。
# 只跑文本——科普两条（解说词 + 分镜）、白板一条（逐幕切分）——不出配音、不出图、不渲染。
# 产物是面板可编辑的纯 JSON；用户改完把它整份回传给成片任务（payload.locked_script）。
# 这样改一句台词不再等于整片重跑：重跑只发生在用户真的确认之后。
# --------------------------------------------------------------------------- #
async def _run_freezone_explainer_script_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    """讲解类视频：参考资料/文案 → 可逐镜编辑的台词草稿。

    出网只有文本叶（``generate_freezone_text``）：草稿阶段不碰配音与图像模型，
    所以它在 script 队列上跑、十几秒内回，用户不必等整片渲染才知道台词写了什么。

    草稿的形态由 ``freezone/explainer_script.py`` 定义并校验，本 runner 只负责“取
    模型产物 → 组装草稿 → 落盘”。锁定稿的合法性在成片任务那一侧再校一遍，因为
    中间隔着一次人工编辑，草稿文件本身不可信。
    """
    import json

    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone import explainer_script as draft_script
    from novelvideo.freezone.explainer import (
        ExplainerChapter,
        build_explainer_narration_prompt,
        build_explainer_storyboard_prompt,
        flatten_sentences,
        normalize_explainer_duration_s,
        parse_explainer_narration,
        parse_explainer_storyboard,
        resolve_reference_content,
    )
    from novelvideo.freezone.jobs import ensure_freezone_dirs
    from novelvideo.freezone.paths import outputs_dir
    from novelvideo.freezone.text_node import generate_freezone_text
    from novelvideo.freezone.whiteboard import (
        WHITEBOARD_MAX_SCENES,
        build_whiteboard_draft_prompt,
        parse_whiteboard_draft_scenes,
    )

    payload = envelope.get("payload") or {}
    task_type = "freezone_explainer_script"
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)

    kind = (
        str(payload.get("kind") or draft_script.EXPLAINER_DRAFT_KIND).strip().lower()
    )
    if kind not in (
        draft_script.EXPLAINER_DRAFT_KIND,
        draft_script.WHITEBOARD_DRAFT_KIND,
    ):
        raise ValueError(f"unsupported explainer script kind: {kind}")
    model_text = str(payload.get("model_text") or "").strip()
    title_hint = str(payload.get("title") or "").strip()

    if kind == draft_script.WHITEBOARD_DRAFT_KIND:
        script = str(payload.get("script") or "").strip()
        if not script:
            raise ValueError("白板台词草稿需要口播文案")
        try:
            max_scenes = int(payload.get("max_scenes") or WHITEBOARD_MAX_SCENES)
        except (TypeError, ValueError):
            max_scenes = WHITEBOARD_MAX_SCENES
        _update(
            ctx,
            task_type,
            job_id,
            0.1,
            lmsg("tasks.progress.explainerDraft.whiteboardScenes", "切分白板逐幕台词..."),
        )
        model, raw = await _call_freezone_leaf(
            envelope,
            generate_freezone_text,
            "generate_freezone_text",
            prompt=build_whiteboard_draft_prompt(script, max_scenes),
            **({"model": model_text} if model_text else {}),
        )
        draft = draft_script.build_whiteboard_script_draft(
            parse_whiteboard_draft_scenes(raw, script)
        )
        plan_model = model
        narration_model = ""
    else:
        duration_s = normalize_explainer_duration_s(payload.get("duration_s"))
        reference = resolve_reference_content(payload.get("reference_content"))

        # 1) 解说词（与成片任务同一 prompt，草稿阶段只是不接着配音）。
        _update(
            ctx,
            task_type,
            job_id,
            0.1,
            lmsg("tasks.progress.explainer.narration", "把参考资料改写成解说词..."),
        )
        narration_model, narration_raw = await _call_freezone_leaf(
            envelope,
            generate_freezone_text,
            "generate_freezone_text",
            prompt=build_explainer_narration_prompt(
                reference, duration_s=duration_s, title_hint=title_hint
            ),
            **({"model": model_text} if model_text else {}),
        )
        chapters = parse_explainer_narration(narration_raw)
        if not chapters:
            # 与成片同一口径：模型没按格式回时，把参考资料按行当解说词用，草稿照出。
            lines = tuple(
                part.strip() for part in reference.splitlines() if part.strip()
            )
            if not lines:
                raise RuntimeError("explainer narration is empty")
            chapters = [ExplainerChapter(name="讲解", sentences=lines[:80])]

        # 2) 分镜：模型只选图元/动效，帧号照样等配音回来再算。
        _update(
            ctx,
            task_type,
            job_id,
            0.5,
            lmsg("tasks.progress.explainer.storyboard", "规划讲解分镜..."),
        )
        flat = flatten_sentences(chapters)
        plan_model, storyboard_raw = await _call_freezone_leaf(
            envelope,
            generate_freezone_text,
            "generate_freezone_text",
            prompt=build_explainer_storyboard_prompt(
                [(index, text) for _, index, text in flat], title_hint=title_hint
            ),
            **({"model": model_text} if model_text else {}),
        )
        title, shots = parse_explainer_storyboard(storyboard_raw, chapters=chapters)
        draft = draft_script.build_explainer_script_draft(
            title, chapters, shots, duration_s=duration_s
        )

    _update(
        ctx,
        task_type,
        job_id,
        0.9,
        lmsg("tasks.progress.explainerDraft.collect", "整理台词草稿..."),
    )
    out = outputs_dir(project_dir, task_type) / f"{job_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    rel = out.relative_to(project_dir).as_posix()
    result: dict[str, Any] = {
        "job_id": job_id,
        "output_format": "json",
        "output_path": str(out),
        "output_url": make_static_url_for_context(ctx, rel),
        "kind": kind,
        "draft_version": draft_script.SCRIPT_DRAFT_VERSION,
        "estimated_seconds": draft["estimated_seconds"],
        # 整份草稿原样回传：前端编辑后把它当 locked_script 交回成片任务，
        # 不在前端重建第二套结构（两套结构迟早会走样）。
        "draft": draft,
        "model": plan_model or narration_model,
    }
    if kind == draft_script.WHITEBOARD_DRAFT_KIND:
        result["scene_count"] = len(draft["scenes"])
    else:
        result["shot_count"] = len(draft["shots"])
        # 章名按镜头顺序去重（章边界就是镜头上的 chapter 变化）。用集合去重会把
        # 章节顺序弄丢，而顺序正是进度条上的导航顺序。
        chapter_names: list[str] = []
        for shot in draft["shots"]:
            name = str(shot.get("chapter") or "")
            if name and (not chapter_names or chapter_names[-1] != name):
                chapter_names.append(name)
        result["chapter_names"] = chapter_names
    return result


def run_freezone_explainer_script(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(
        envelope, _run_freezone_explainer_script_async(envelope, ctx)
    )


# --------------------------------------------------------------------------- #
# [aipdd fork delta] 科普讲解视频（freezone_explainer_video）：用户交参考资料 →
# 网关文本模型改写成分章解说词 → 网关文本模型出受限分镜 JSON → 网关音频模型配音
# → 本地按朗读权重确定性分配帧号/字幕块 → Pillow 逐帧绘制 MG 画面 + ffmpeg 流式
# 编码成片。画面全部代码绘制：不引入浏览器、不引入前端渲染框架、不新增模型类型；
# 三个生成叶（text ×2 + audio ×1）经既有出网分发，不新建独立任务存储。
# --------------------------------------------------------------------------- #
async def _run_freezone_explainer_video_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import (
        make_sqlite_store_for_context,
        make_static_url_for_context,
    )
    from novelvideo.freezone.audio_node import generate_freezone_audio_speech
    from novelvideo.freezone.explainer import (
        EXPLAINER_FPS,
        ExplainerChapter,
        build_explainer_narration_prompt,
        build_explainer_storyboard_prompt,
        flatten_sentences,
        normalize_explainer_background,
        normalize_explainer_duration_s,
        parse_explainer_narration,
        parse_explainer_storyboard,
        plan_explainer_timeline,
        resolve_reference_content,
    )
    from novelvideo.freezone.explainer_motion import render_explainer_video
    from novelvideo.freezone.explainer_script import parse_explainer_script_draft
    from novelvideo.freezone.explainer_frames import (
        CANVAS_FORMATS,
        ENGINE_HYPERFRAMES,
        build_explainer_project,
        explainer_engine,
        mux_audio,
        normalize_canvas_format,
        normalize_style_preset,
    )
    from novelvideo.renderer.client import (
        RenderFailed,
        RendererNotConfigured,
    )
    from novelvideo.renderer.client import is_configured as renderer_configured
    from novelvideo.renderer.client import render_project
    from novelvideo.freezone.jobs import ensure_freezone_dirs
    from novelvideo.freezone.mix import normalize_mix_resolution, resolve_mix_canvas
    from novelvideo.freezone.paths import outputs_dir
    from novelvideo.freezone.text_node import generate_freezone_text

    payload = envelope.get("payload") or {}
    task_type = "freezone_explainer_video"
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    work_dir = outputs_dir(project_dir, task_type) / f"{job_id}_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    reference = resolve_reference_content(payload.get("reference_content"))
    model_text = str(payload.get("model_text") or "").strip()
    duration_s = normalize_explainer_duration_s(payload.get("duration_s"))
    title_hint = str(payload.get("title") or "").strip()
    background = normalize_explainer_background(payload.get("background"))
    resolution = normalize_mix_resolution(str(payload.get("resolution") or ""))
    # 讲解片固定横屏：图元的横向排版（五步流程、左右对比）是按 16:9 设计的。
    canvas_width, canvas_height = resolve_mix_canvas("16:9", resolution)

    cancel_kwargs = {
        "project_id": ctx.project_id,
        "task_type": task_type,
        "episode": 0,
        "task_id": str(envelope.get("__run_task_id") or ""),
        "scope": job_id,
    }

    # 1) 解说词：只允许用参考资料里的事实，分章标记随文本一起产出。
    #
    # [aipdd fork delta] 锁定稿（两段式第二段）：用户在台词草稿里逐镜改完后回传的
    # 整份草稿。它合法就跳过两次文本调用，直接进配音与渲染；不合法**不回退模型
    # 重写**——那等于把用户改过的稿子偷偷换掉，宁可失败并说清哪一项不对。
    script_source = "model"
    draft = None
    locked = payload.get("locked_script")
    if isinstance(locked, dict) and locked:
        draft = parse_explainer_script_draft(
            locked, fallback_duration_s=duration_s
        )
    if draft is not None:
        script_source = "draft"
        _update(
            ctx,
            task_type,
            job_id,
            0.12,
            lmsg("tasks.progress.explainerDraft.locked", "按确认的台词草稿出片..."),
        )
        chapters = draft.as_chapters()
        title = draft.title
        shots = draft.as_shots()
        _narration_model = "explainer-script-draft"
        _storyboard_model = "explainer-script-draft"
    else:
        _update(
            ctx,
            task_type,
            job_id,
            0.05,
            lmsg("tasks.progress.explainer.narration", "把参考资料改写成解说词..."),
        )
        _narration_model, narration_raw = await _await_with_cancel_watch(
            _call_freezone_leaf(
                envelope,
                generate_freezone_text,
                "generate_freezone_text",
                prompt=build_explainer_narration_prompt(
                    reference,
                    duration_s=duration_s,
                    title_hint=title_hint,
                ),
                model=model_text or None,
            ),
            **cancel_kwargs,
        )
        chapters = parse_explainer_narration(narration_raw)
        if not chapters:
            # 不致命：模型没按格式回时，把参考资料按行当解说词直接用，链路照旧出片。
            lines = tuple(
                part.strip() for part in reference.splitlines() if part.strip()
            )
            if not lines:
                raise RuntimeError("explainer narration is empty")
            chapters = [ExplainerChapter(name="讲解", sentences=lines[:80])]

        flat = flatten_sentences(chapters)

        # 2) 分镜：模型只能选词汇表内的图元/动效，帧号一律本地算。
        _update(
            ctx,
            task_type,
            job_id,
            0.16,
            lmsg("tasks.progress.explainer.storyboard", "规划讲解分镜..."),
        )
        _storyboard_model, storyboard_raw = await _await_with_cancel_watch(
            _call_freezone_leaf(
                envelope,
                generate_freezone_text,
                "generate_freezone_text",
                prompt=build_explainer_storyboard_prompt(
                    [(index, text) for _, index, text in flat],
                    title_hint=title_hint,
                ),
                model=model_text or None,
            ),
            **cancel_kwargs,
        )
        title, shots = parse_explainer_storyboard(storyboard_raw, chapters=chapters)

    flat = flatten_sentences(chapters)

    # 3) 配音：朗读整段解说词，总时长驱动全片帧号分配。
    narration_script = "".join(text for _, _, text in flat)
    _update(ctx, task_type, job_id, 0.26, lmsg("tasks.progress.explainer.voice", "合成讲解配音..."))
    projection = read_projection(payload)
    store = None if projection is not None else await make_sqlite_store_for_context(ctx)
    try:
        speech = await _await_with_cancel_watch(
            _call_freezone_leaf(
                envelope,
                generate_freezone_audio_speech,
                "generate_freezone_audio_speech",
                store=store,
                username=ctx.owner_username,
                project=ctx.project_name,
                account_voice_username=str(
                    payload.get("account_voice_username")
                    or ctx.requester_username
                    or ctx.owner_username
                ),
                project_dir=project_dir,
                job_id=f"{job_id}_voice",
                text=narration_script,
                emotion_prompt=str(payload.get("emotion_prompt") or ""),
                voice_ref=payload.get("voice_ref"),
                projection=projection,
                model=str(payload.get("model") or ""),
            ),
            **cancel_kwargs,
        )
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()
    narration_path = str(speech.audio_path)
    narration_ms = int(speech.duration_ms or 0)
    narration_model = str(speech.model or "")
    if narration_ms <= 0:
        raise RuntimeError("explainer narration duration is invalid")

    # 4) 时间轴：句/镜头/字幕块按朗读权重分摊，短镜头自动并框。
    timeline = plan_explainer_timeline(
        chapters=chapters,
        shots=shots,
        title=title,
        duration_ms=narration_ms,
        fps=EXPLAINER_FPS,
    )
    if not timeline.scenes:
        raise RuntimeError("explainer timeline is empty")

    bgm_path = str(payload.get("bgm_path") or "").strip() or None
    bgm_volume = (
        0.2 if payload.get("bgm_volume") is None else float(payload.get("bgm_volume"))
    )
    output_path = outputs_dir(project_dir, task_type) / f"{job_id}.mp4"

    async def _render_with_pillow() -> None:
        """原 Pillow 逐帧绘制链路（回退 / 无渲染服务时用）：线程内长调用 + 轮询上报。"""
        render_seed = lmsg("tasks.progress.explainer.renderingFrames", "逐帧绘制 MG 画面...")
        _update(ctx, task_type, job_id, 0.32, render_seed)
        progress_state: dict[str, Any] = {"fraction": 0.0, "message": render_seed}

        def _note_progress(fraction: float, message: MessageLike) -> None:
            progress_state["fraction"] = float(fraction)
            progress_state["message"] = message

        render_task = asyncio.create_task(
            asyncio.to_thread(
                render_explainer_video,
                timeline=timeline,
                out_path=output_path,
                width=canvas_width,
                height=canvas_height,
                fps=timeline.fps,
                background=background,
                narration_path=narration_path,
                bgm_path=bgm_path,
                bgm_volume=bgm_volume,
                on_progress=_note_progress,
            )
        )
        while not render_task.done():
            await asyncio.sleep(2.0)
            fraction = max(0.0, min(1.0, float(progress_state["fraction"])))
            _update(
                ctx,
                task_type,
                job_id,
                round(0.32 + 0.62 * fraction, 3),
                progress_state["message"],
            )
        await render_task

    engine = explainer_engine()
    canvas_format = normalize_canvas_format(payload.get("format")) or "landscape"
    hf_width, hf_height = CANVAS_FORMATS.get(
        canvas_format, CANVAS_FORMATS["landscape"]
    )
    style_preset = normalize_style_preset(payload.get("style_preset"))
    captions_on = bool(payload.get("captions", True))
    rendered_by = "pillow"

    # 5) 出片：默认走 HyperFrames 引擎即服务（产 HTML 帧项目→无头渲静音视频→
    # 本地 ffmpeg 混配音/BGM）；未配置渲染服务或渲染异常时回退 Pillow（不阻断出片）。
    if engine == ENGINE_HYPERFRAMES and renderer_configured():
        try:
            _update(ctx, task_type, job_id, 0.34, lmsg("tasks.progress.explainer.frames", "生成 HyperFrames 帧项目..."))
            hf_project_dir = work_dir / "hf_project"
            proj = await asyncio.to_thread(
                build_explainer_project,
                timeline,
                project_dir=hf_project_dir,
                width=hf_width,
                height=hf_height,
                style_preset=style_preset,
                captions=captions_on,
                canvas_format=canvas_format,
            )
            _update(ctx, task_type, job_id, 0.40, lmsg("tasks.progress.explainer.render", "调用渲染服务无头出片..."))
            silent_path = work_dir / f"{job_id}_silent.mp4"
            render_timeout_s = int(max(600, proj.duration_seconds * 20))
            render_result = await render_project(
                proj.project_dir,
                output_path=silent_path,
                fps=proj.fps,
                quality="high",
                canvas_format=canvas_format,
                entry_file=proj.entry_file,
                timeout_s=render_timeout_s,
            )
            _update(ctx, task_type, job_id, 0.84, lmsg("tasks.progress.explainer.mix", "混入配音与背景音乐..."))
            await asyncio.to_thread(
                mux_audio,
                Path(render_result.output_path),
                out_path=output_path,
                narration_path=narration_path,
                bgm_path=bgm_path,
                bgm_volume=bgm_volume,
                duration_seconds=proj.duration_seconds,
            )
            rendered_by = "hyperframes"
        except (RendererNotConfigured, RenderFailed) as exc:
            # 渲染服务不可达 / 出片失败：落回 Pillow 逐帧绘制，错误只作进度提示不致命。
            _update(
                ctx,
                task_type,
                job_id,
                0.32,
                f"HyperFrames 渲染不可用，回退本地绘制：{str(exc)[:120]}",
            )
            await _render_with_pillow()
    else:
        await _render_with_pillow()

    # 6) 归档与结果。
    _update(ctx, task_type, job_id, 0.97, lmsg("tasks.progress.explainer.register", "登记讲解视频成片..."))
    rel = output_path.relative_to(project_dir).as_posix()
    result: dict[str, Any] = {
        "job_id": job_id,
        "output_path": str(output_path),
        "output_url": make_static_url_for_context(ctx, rel),
        "media_type": "video",
        "narration": narration_script,
        "chapters": [chapter.name for chapter in chapters],
        "shot_count": len(timeline.scenes),
        "title": title.big or title.rest,
        "narration_model": _narration_model,
        "storyboard_model": _storyboard_model,
        "plan_model": _storyboard_model,
        "model": narration_model or _storyboard_model,
        # 台词来源：model = 本次模型现写；draft = 用户确认过的台词草稿（两段式第二段）。
        "script_source": script_source,
        "script_estimated_seconds": (
            draft.estimated_seconds if draft is not None else None
        ),
        "narration_duration_ms": narration_ms,
        "requested_duration_s": duration_s,
        "fps": timeline.fps,
        "duration_seconds": round(timeline.duration_seconds, 3),
        "background": background,
        "aspect_ratio": (
            {"portrait": "9:16", "square": "1:1"}.get(canvas_format, "16:9")
            if rendered_by == "hyperframes"
            else "16:9"
        ),
        "engine": rendered_by,
        "style_preset": style_preset if rendered_by == "hyperframes" else "",
        "resolution": resolution,
        "chapter_starts_seconds": [
            round(start / float(timeline.fps or EXPLAINER_FPS), 3)
            for start in timeline.chapter_starts
        ],
    }
    _archive_tool_output_to_library(
        ctx=ctx,
        payload=payload,
        task_type=task_type,
        job_id=job_id,
        media="video",
        url=str(result["output_url"]),
        result=result,
        stem=_archive_stem_from_text(title.rest or narration_script) or job_id,
    )
    return result


def run_freezone_explainer_video(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(
        envelope, _run_freezone_explainer_video_async(envelope, ctx)
    )


# --------------------------------------------------------------------------- #
# [aipdd fork delta] 动效大片渲染（freezone_motion_render）：两种入口，一段成片。
#  · 已经有页面：payload 带 html → 直接渲染。
#  · 只有案例 + 镜头文案：payload 带 case_id（html 留空）→ 先用图册里那条渲染契约
#    提示词让网关文本模型写页面（``generate_freezone_text``，出网口径已在
#    ``FREEZONE_LEAF_EGRESS`` 记为 NETWORK），再渲染。
#  · 只有风格 + 镜头文案：payload 带 style_id → 同上，但规格换成画风图册那条（观感
#    约束，不含分镜结构）；案例与风格同时给会被 _motion_page_source 拒掉。
# 渲染这一段本身一次网都不出：不调任何生成 leaf，Chrome 用 ``--host-resolver-rules``
# 打死域名解析（页面里残留的外链图片/字体既发不出去，也不会让同一条提示词今天能渲、
# 明天渲不出），ffmpeg 只读管道；画面全部是页面里的代码画的。
# --------------------------------------------------------------------------- #


def _motion_page_source(payload: dict[str, Any]) -> tuple[str, str]:
    """页面来源校验收在一处：贴好的源码、按案例生成、按风格生成，三条只能走一条。

    路由层同样挡一次，但排队的 envelope 可能绕过路由直接进任务（重跑、手工投递），
    所以这里必须自己再确认一遍，否则两个 id 同时给时会静默偏向其中一个。
    """
    case_id = str(payload.get("case_id") or "").strip()
    style_id = str(payload.get("style_id") or "").strip()
    if case_id and style_id:
        raise RuntimeError(
            "案例与风格只能选一个：案例给分镜结构，风格给画面观感，不要同时给"
        )
    if not str(payload.get("html") or "").strip() and not case_id and not style_id:
        raise RuntimeError(
            "MG 页面内容为空：请粘贴页面源码，或选择案例/风格并给出本镜头文案让模型生成"
        )
    return case_id, style_id


async def _generate_motion_page(
    envelope: dict[str, Any],
    ctx: ProjectContext,
    *,
    task_type: str,
    job_id: str,
    case_id: str,
    style_id: str,
    subject: str,
    model_text: str,
    width: int,
    height: int,
    fps: int,
    duration_seconds: float | None,
) -> tuple[str, str]:
    """让网关文本模型按图册规格写一页 MG 源码；返回 (页面源码, 实际模型名)。

    ``task_type`` 由调用方给：生成页面这一步的进度要报在**发起它的那一单**上，
    写死成渲染任务类型的话，静帧预检的进度会串到成片任务里去。

    ``case_id`` 与 ``style_id`` 二选一（路由已挡过）：案例给分镜结构，画风给画面观感。
    规格文字走 ``build_freezone_*``：它读提示词库的生效值，所以超管改过某条案例或
    某条风格，图册里复制到的与这一单实际发给模型的才是同一份文字。
    """
    from novelvideo.freezone.motion_render import build_motion_page_prompt
    from novelvideo.freezone.route_helpers import (
        build_freezone_motion_prompt,
        build_freezone_motion_style_prompt,
    )
    from novelvideo.freezone.text_node import generate_freezone_text

    if style_id:
        source_label = f"画风 {style_id}"
        builder = build_freezone_motion_style_prompt
        missing = "不在本机的画风图册清单里：请重新选择风格"
    else:
        source_label = f"动效案例 {case_id}"
        builder = build_freezone_motion_prompt
        missing = "不在本机的图册清单里：请重新选择案例"
    try:
        spec_prompt = builder(style_id if style_id else case_id)
    except KeyError:
        raise RuntimeError(f"{source_label}{missing}") from None
    _update(ctx, task_type, job_id, 0.04, "按渲染契约生成 MG 页面...")
    prompt = build_motion_page_prompt(
        spec_prompt,
        subject=subject,
        width=width,
        height=height,
        fps=fps,
        duration_seconds=duration_seconds,
    )
    model, raw = await _call_freezone_leaf(
        envelope,
        generate_freezone_text,
        "generate_freezone_text",
        prompt=prompt,
        **({"model": model_text} if model_text else {}),
    )
    from novelvideo.freezone.motion_render import extract_motion_page, motion_page_error

    page = extract_motion_page(str(raw or ""))
    complaint = motion_page_error(page)
    if complaint:
        # 把模型的原文留在日志里（只截前 2000 字符），否则"为什么这单失败了"只能猜。
        logger.warning(
            "[motion-render] 页面契约预检未通过 job=%s 来源=%s：%s；回复开头：%s",
            job_id,
            style_id or case_id,
            complaint,
            str(raw or "")[:2000],
        )
        raise RuntimeError(f"MG 页面生成失败：{complaint}")
    return page, str(model or "")


async def _attach_motion_score(
    payload: dict[str, Any],
    ctx: ProjectContext,
    *,
    job_id: str,
    output_path: Path,
    rendered: Any,
    case_id: str,
    style_id: str,
    cancel_kwargs: dict[str, Any],
) -> dict[str, Any]:
    """按渲染契约第 3/6 条给成片配一段程序生成的配乐，失败就交无声片。

    画面是这一单的主要产物：本机 ffmpeg 少某个能力、或这一镜短到排不出网格，都不该让整单
    判失败，所以这里只把原因写进结果（``engine="silent"`` + 中文说明），前端与 sidecar 都能
    看见「这一镜为什么没声」。取消不在这条兜底里——``TaskCancelled`` 必须照常往上抛。
    """
    if not payload.get("music", True):
        return {"engine": "off"}
    from novelvideo.freezone.motion_audio import MotionAudioError, score_and_attach
    from novelvideo.freezone.motion_media import get_case

    # 分类决定编曲风格（音乐MV→drive、科普→ambient），拿不到就用中性档位；
    # 按风格出片时这一镜没有案例，就取那条风格自己的分类。
    if style_id:
        from novelvideo.freezone.motion_styles import get_motion_style

        category = str((get_motion_style(style_id) or {}).get("category") or "")
    else:
        category = str((get_case(case_id) or {}).get("category") or "")
    _update(ctx, "freezone_motion_render", job_id, 0.95, "按拍点网格编曲并归一响度...")
    try:
        score = await _await_with_cancel_watch(
            score_and_attach(
                output_path,
                events=[dict(item) for item in rendered.events],
                duration_seconds=rendered.plan.duration_seconds,
                fps=rendered.plan.fps,
                case_category=category,
            ),
            **cancel_kwargs,
        )
    except (MotionAudioError, OSError) as exc:
        logger.warning("[motion-render] 配乐失败，交付无声片 job=%s：%s", job_id, exc)
        return {"engine": "silent", "error": str(exc)}
    return score.as_dict()


async def _run_freezone_motion_render_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.jobs import ensure_freezone_dirs
    from novelvideo.freezone.mix import (
        normalize_mix_aspect_ratio,
        normalize_mix_resolution,
        resolve_mix_canvas,
    )
    from novelvideo.freezone.motion_render import (
        MAX_PAGE_CHARS,
        clamp_fps,
        page_source_path,
        render_motion_video,
    )
    from novelvideo.freezone.motion_draft.project import pick_int
    from novelvideo.freezone.paths import outputs_dir

    payload = envelope.get("payload") or {}
    task_type = "freezone_motion_render"
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)

    html = str(payload.get("html") or "")
    case_id, style_id = _motion_page_source(payload)
    page_source = "pasted"
    page_model = ""
    aspect_ratio = normalize_mix_aspect_ratio(str(payload.get("aspect_ratio") or "16:9"))
    resolution = normalize_mix_resolution(str(payload.get("resolution") or ""))
    width, height = resolve_mix_canvas(aspect_ratio, resolution)
    fps = clamp_fps(payload.get("fps"))
    requested_duration = payload.get("duration_seconds")
    duration_seconds = (
        float(requested_duration)
        if str(requested_duration or "").strip() not in ("", "None")
        else None
    )
    if not html.strip():
        html, page_model = await _generate_motion_page(
            envelope,
            ctx,
            task_type=task_type,
            job_id=job_id,
            case_id=case_id,
            style_id=style_id,
            subject=str(payload.get("subject") or ""),
            model_text=str(payload.get("model_text") or ""),
            width=width,
            height=height,
            fps=fps,
            duration_seconds=duration_seconds,
        )
        page_source = "generated"
    if len(html) > MAX_PAGE_CHARS:
        raise RuntimeError(
            f"MG 页面过大（{len(html)} 字符，上限 {MAX_PAGE_CHARS}）："
            "渲染契约要求单文件自包含，请精简页面、外链改代码绘制"
        )
    output_path = outputs_dir(project_dir, task_type) / f"{job_id}.mp4"

    cancel_kwargs = {
        "project_id": ctx.project_id,
        "task_type": task_type,
        "episode": 0,
        "task_id": str(envelope.get("__run_task_id") or ""),
        "scope": job_id,
    }

    _update(ctx, task_type, job_id, 0.12, "启动渲染浏览器...")

    def _note_progress(done: int, total: int) -> None:
        fraction = done / float(max(1, total))
        _update(
            ctx,
            task_type,
            job_id,
            round(0.15 + 0.79 * min(1.0, max(0.0, fraction)), 3),
            f"逐帧渲染 MG 画面 {done}/{total}",
        )

    rendered = await _await_with_cancel_watch(
        render_motion_video(
            html,
            output_path,
            width=width,
            height=height,
            fps=fps,
            duration_seconds=duration_seconds,
            on_progress=_note_progress,
        ),
        **cancel_kwargs,
    )

    # 配乐排在成片之后、sidecar 之前：sidecar 要把「这一镜到底有没有声、什么 BPM/档位」
    # 一起落盘，导出剪映草稿时才知道嵌在画面里的这条分轨该不该给旁白让位。
    audio_info = await _attach_motion_score(
        payload,
        ctx,
        job_id=job_id,
        output_path=output_path,
        rendered=rendered,
        case_id=case_id,
        style_id=style_id,
        cancel_kwargs=cancel_kwargs,
    )

    _update(ctx, task_type, job_id, 0.97, "登记动效成片...")
    rel = output_path.relative_to(project_dir).as_posix()
    page_rel = output_path.with_suffix(".html").relative_to(project_dir).as_posix()
    source_rel = (
        page_source_path(output_path.with_suffix(".html")).relative_to(project_dir).as_posix()
    )
    # 拍点/画幅只活在这一单的结果里，任务记录过期后就查不到了；而「导出剪映草稿」要的
    # 正是这两样（打点落进剪映时间轴）。所以把渲染元数据同名落一份 .motion.json  sidecar，
    # 让草稿任务能离线拿到。写失败只告警：成片已出，草稿缺拍点仍可照常落轨。
    sidecar_path = output_path.with_suffix(".motion.json")
    try:
        sidecar_path.write_text(
            json.dumps(
                {
                    "job_id": job_id,
                    "case_id": str(payload.get("case_id") or ""),
                    # 按风格出片时没有案例，风格 id 是这一镜唯一的出处。
                    "style_id": str(payload.get("style_id") or ""),
                    # 片名/集号/镜号写进 sidecar：导出剪映草稿要把散落的成片按镜归位并
                    # 显示成认得出的名字，这三样只在这个任务的 payload 里有过。
                    "title": str(payload.get("title") or ""),
                    "episode": pick_int(payload.get("episode")),
                    "beat_number": pick_int(payload.get("beat_number")),
                    "video_path": str(output_path),
                    "page_path": str(output_path.with_suffix(".html")),
                    "width": rendered.width,
                    "height": rendered.height,
                    "fps": rendered.plan.fps,
                    "frames": rendered.plan.frames,
                    "duration_seconds": round(rendered.plan.duration_seconds, 6),
                    "aspect_ratio": aspect_ratio,
                    "beats": [dict(item) for item in rendered.events],
                    # 配乐口径也要能在草稿任务里离线读到：草稿靠它决定「画面里这条分轨
                    # 要不要给旁白让位」，无声片（silent/off）就不该去压低音量。
                    "audio": audio_info,
                },
                ensure_ascii=False,
                indent=2,
                # NaN/Infinity 不是合法 JSON：宁可这份 sidecar 不落盘（下面只告警），
                # 也不要写出一份草稿任务与前端都解析不了的坏文件。
                allow_nan=False,
            ),
            encoding="utf-8",
        )
    except (OSError, ValueError) as exc:
        logger.warning("[motion-render] 渲染元数据 sidecar 落盘失败 job=%s：%s", job_id, exc)
    result: dict[str, Any] = {
        "job_id": job_id,
        "output_path": str(output_path),
        "output_url": make_static_url_for_context(ctx, rel),
        # 页面与成片同名同目录：出片后仍能打开 .html 复现「这一帧为什么长这样」。
        "page_url": make_static_url_for_context(ctx, page_rel),
        # 同名还有一份不带引导脚本的原始源码：改完再渲就改这份。
        "page_source_url": make_static_url_for_context(ctx, source_rel),
        "sidecar_path": str(sidecar_path),
        "media_type": "video",
        "case_id": str(payload.get("case_id") or ""),
        "style_id": str(payload.get("style_id") or ""),
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "width": rendered.width,
        "height": rendered.height,
        "fps": rendered.plan.fps,
        "frames": rendered.plan.frames,
        "duration_seconds": round(rendered.plan.duration_seconds, 3),
        "page_duration_seconds": round(rendered.plan.page_duration_seconds, 3),
        "requested_duration_seconds": (
            round(float(duration_seconds), 3) if duration_seconds else None
        ),
        "beats": [dict(item) for item in rendered.events],
        # 配乐这一段的事实：BPM/档位/音符数/归一前响度，或降级成无声片的原因。
        "audio": audio_info,
        "engine": "chrome-cdp",
        # 页面来源要留痕：粘贴的页面和模型产出的页面，出问题时排查路径完全不同。
        "page_source": page_source,
        "model": page_model or str(payload.get("model") or ""),
    }
    score_path = Path(str(audio_info.get("score_path") or ""))
    if score_path.is_file() and score_path.is_relative_to(project_dir):
        # 分轨干声也要能直接播：配乐对不对拍点、要不要在剪映里单独拧音乐音量，
        # 听这一条就够了，不用先把整片下下来。
        result["score_url"] = make_static_url_for_context(
            ctx, score_path.relative_to(project_dir).as_posix()
        )
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type=task_type,
        job_id=job_id,
        media_type="video",
        result=result,
    )
    if history_record:
        result["generation_history_record"] = history_record
    _archive_tool_output_to_library(
        ctx=ctx,
        payload=payload,
        task_type=task_type,
        job_id=job_id,
        media="video",
        url=str(result["output_url"]),
        result=result,
        stem=str(payload.get("title") or payload.get("case_id") or payload.get("style_id") or ""),
    )
    return result


def run_freezone_motion_render(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(
        envelope, _run_freezone_motion_render_async(envelope, ctx)
    )


# --------------------------------------------------------------------------- #
# [aipdd fork delta] 动效大片拍点预检（freezone_motion_stills）：渲染契约第 4 条。
# 一页 MG 脚本 → 每个拍点一张静帧，先检查节奏、溢出与可读性，修好再精修整片。
# 与成片共用同一个 CDP 截图通道（`MotionPage.capture_at`），所以预检看到的就是成片
# 会有的画面；差别只在这里**不编码、不配乐**，因此连 ffmpeg 都不需要（预检只查 Chrome）。
# 页面来源与渲染任务同口径：带 html 就检这一页，留空则先按案例契约让文本模型写页面。
# --------------------------------------------------------------------------- #
async def _run_freezone_motion_stills_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.jobs import ensure_freezone_dirs
    from novelvideo.freezone.mix import (
        normalize_mix_aspect_ratio,
        normalize_mix_resolution,
        resolve_mix_canvas,
    )
    from novelvideo.freezone.motion_render import (
        MAX_PAGE_CHARS,
        clamp_fps,
        clamp_max_stills,
        page_source_path,
        render_motion_stills,
    )
    from novelvideo.freezone.paths import outputs_dir

    payload = envelope.get("payload") or {}
    task_type = "freezone_motion_stills"
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)

    html = str(payload.get("html") or "")
    case_id, style_id = _motion_page_source(payload)
    aspect_ratio = normalize_mix_aspect_ratio(str(payload.get("aspect_ratio") or "16:9"))
    resolution = normalize_mix_resolution(str(payload.get("resolution") or ""))
    width, height = resolve_mix_canvas(aspect_ratio, resolution)
    max_stills = clamp_max_stills(payload.get("max_stills"))

    page_source = "pasted"
    page_model = ""
    if not html.strip():
        # 预检同样要先把页面写出来：fps 用默认档，页面时长由它自报的 window.DUR 决定。
        html, page_model = await _generate_motion_page(
            envelope,
            ctx,
            task_type=task_type,
            job_id=job_id,
            case_id=case_id,
            style_id=style_id,
            subject=str(payload.get("subject") or ""),
            model_text=str(payload.get("model_text") or ""),
            width=width,
            height=height,
            fps=clamp_fps(None),
            duration_seconds=None,
        )
        page_source = "generated"
    if len(html) > MAX_PAGE_CHARS:
        raise RuntimeError(
            f"MG 页面过大（{len(html)} 字符，上限 {MAX_PAGE_CHARS}）："
            "渲染契约要求单文件自包含，请精简页面、外链改代码绘制"
        )

    out_dir = outputs_dir(project_dir, task_type) / job_id
    cancel_kwargs = {
        "project_id": ctx.project_id,
        "task_type": task_type,
        "episode": 0,
        "task_id": str(envelope.get("__run_task_id") or ""),
        "scope": job_id,
    }

    _update(ctx, task_type, job_id, 0.12, "启动预检浏览器...")

    def _note_progress(done: int, total: int) -> None:
        fraction = done / float(max(1, total))
        _update(
            ctx,
            task_type,
            job_id,
            round(0.15 + 0.79 * min(1.0, max(0.0, fraction)), 3),
            f"按拍点出静帧 {done}/{total}",
        )

    rendered = await _await_with_cancel_watch(
        render_motion_stills(
            html,
            out_dir,
            width=width,
            height=height,
            max_stills=max_stills,
            on_progress=_note_progress,
        ),
        **cancel_kwargs,
    )

    _update(ctx, task_type, job_id, 0.97, "整理静帧清单...")
    stills: list[dict[str, Any]] = []
    for still in rendered.stills:
        if not still.path.is_relative_to(project_dir):
            continue
        stills.append(
            {
                "at_seconds": round(still.at_seconds, 3),
                "event_type": still.event_type,
                "path": str(still.path),
                "url": make_static_url_for_context(
                    ctx, still.path.relative_to(project_dir).as_posix()
                ),
            }
        )
    if not stills:
        raise RuntimeError("静帧预检没有产出可显示的图：请确认本机 Chrome 正常后重试")
    page_path = out_dir / "page.html"
    source_path = page_source_path(page_path)
    return {
        "job_id": job_id,
        "media_type": "image",
        # 预检的产物是一目录 PNG，没有单一成片：列表给前端逐张显示，output_url 退到
        # 第一张（开场帧），这样按通用产物解析的地方也不会拿到空地址。
        "stills": stills,
        "output_url": stills[0]["url"],
        "output_path": stills[0]["path"],
        "count": len(stills),
        "page_url": (
            make_static_url_for_context(ctx, page_path.relative_to(project_dir).as_posix())
            if page_path.is_file() and page_path.is_relative_to(project_dir)
            else ""
        ),
        # 不带引导脚本的原始源码：预检看完不满意，就在这份上改完再提交。
        "page_source_url": (
            make_static_url_for_context(
                ctx, source_path.relative_to(project_dir).as_posix()
            )
            if source_path.is_file() and source_path.is_relative_to(project_dir)
            else ""
        ),
        "case_id": case_id,
        # 与渲染任务同一口径：预检也要留下是按哪条风格出的帧，否则改风格重预检无从对照。
        "style_id": style_id,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "width": width,
        "height": height,
        "max_stills": max_stills,
        "page_duration_seconds": round(rendered.page_duration_seconds, 3),
        # 页面没写 window.EV 时退成等间隔网格：这个事实必须说出来，否则用户会以为
        # 每张图都对的是拍点，而它其实只是按秒排的。
        "beat_grid": rendered.beat_grid,
        "beats": [dict(item) for item in rendered.events],
        "engine": "chrome-cdp",
        "page_source": page_source,
        "model": page_model or str(payload.get("model") or ""),
    }


def run_freezone_motion_stills(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(
        envelope, _run_freezone_motion_stills_async(envelope, ctx)
    )


# --------------------------------------------------------------------------- #
# [aipdd fork delta] 动效大片交付（freezone_motion_draft）：逐镜成片 → 一盘剪映草稿。
# MG 渲染一次只出一镜，N 个散落的 mp4 不等于交付。本任务把项目里已渲染的 MG 成片（含
# 同名 ``*.motion.json`` 里的画幅/时长/拍点）连旁白、BGM 一起排成多镜时间线，直接落成
# 剪映工程目录，可选再复制进本机剪映工程根。
# 全程不出网、不调模型、不引任何草稿库：拍点是页面自报的，剪映打开就是打好的点。
# --------------------------------------------------------------------------- #
async def _run_freezone_motion_draft_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.freezone.jobs import ensure_freezone_dirs
    from novelvideo.freezone.mix import (
        normalize_mix_aspect_ratio,
        normalize_mix_resolution,
        resolve_mix_canvas,
    )
    from novelvideo.freezone.motion_draft import (
        MICROSECOND,
        MotionDraftInputError,
        build_spec,
        read_clips,
        total_duration_us,
        write_motion_draft,
    )
    from novelvideo.freezone.motion_render import clamp_fps
    from novelvideo.freezone.paths import outputs_dir

    payload = envelope.get("payload") or {}
    task_type = "freezone_motion_draft"
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)

    aspect_ratio = normalize_mix_aspect_ratio(str(payload.get("aspect_ratio") or "16:9"))
    resolution = normalize_mix_resolution(str(payload.get("resolution") or ""))
    width, height = resolve_mix_canvas(aspect_ratio, resolution)
    fps = clamp_fps(payload.get("fps"))
    title = str(payload.get("title") or "").strip() or f"动效大片 {job_id}"

    _update(ctx, task_type, job_id, 0.08, "读取动效成片与拍点...")
    try:
        clips = read_clips(
            list(payload.get("clips") or []),
            project_dir=project_dir,
            fallback_width=width,
            fallback_height=height,
        )
        spec = build_spec(
            name=title,
            width=width,
            height=height,
            ratio=aspect_ratio,
            fps=fps,
            clips=clips,
            narration=payload.get("narration") or None,
            bgm=payload.get("bgm") or None,
            subtitles=list(payload.get("subtitles") or []),
            text_style=payload.get("text_style") or None,
            project_dir=project_dir,
        )
    except MotionDraftInputError as exc:
        raise RuntimeError(str(exc)) from None

    # 落盘根目录的优先级：本次请求指定 > 「设置 → 剪映草稿目录」> 项目输出目录。
    from novelvideo.model_gateway_settings import get_vectcut_draft_dir, normalize_vectcut_draft_dir

    try:
        configured = normalize_vectcut_draft_dir(
            str(payload.get("draft_dir") or "").strip() or get_vectcut_draft_dir()
        )
    except ValueError as exc:
        raise RuntimeError(f"剪映草稿落盘目录不合法：{exc}") from None
    draft_root = Path(configured) if configured else outputs_dir(project_dir, task_type)
    out_dir = draft_root / job_id

    _update(ctx, task_type, job_id, 0.4, "组装剪映草稿工程...")
    written = await asyncio.to_thread(
        write_motion_draft,
        spec,
        out_dir=out_dir,
        deploy=bool(payload.get("deploy")),
        project_name=title,
    )
    if not written.ok:
        raise RuntimeError(written.error or "剪映草稿生成失败")

    _update(ctx, task_type, job_id, 0.95, "草稿已落盘")
    result: dict[str, Any] = {
        "job_id": job_id,
        "media_type": "draft",
        "name": title,
        "draft_dir": written.draft_dir,
        "timeline_id": written.timeline_id,
        "deployed_dir": written.deployed_dir,
        "clips": len(spec.clips),
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "width": spec.width,
        "height": spec.height,
        "fps": spec.fps,
        "duration_seconds": round(total_duration_us(spec) / float(MICROSECOND), 3),
        "beat_count": sum(len(clip.beat_us) for clip in spec.clips),
        "has_narration": bool(spec.narration),
        "has_bgm": spec.bgm is not None,
        # 字幕条数：0 表示这次没落文字轨，前端用它判断草稿里有没有字幕。
        "text_clips": len(spec.texts),
        "files": written.files,
        "warning": written.error,
    }
    return result


def run_freezone_motion_draft(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(
        envelope, _run_freezone_motion_draft_async(envelope, ctx)
    )


def _extract_video_cover(video_path: Path, out_path: Path, *, at_second: float = 0.5) -> bool:
    """从成片抽一帧作封面（本地 ffmpeg，线程内调用；失败返回 False，不致命）。

    [aipdd fork delta] 渠道短视频交付附带一张封面，供用户发布时手动上传；不接
    渠道上传 API。取 ``at_second`` 处的一帧（避开首帧黑场），jpg 质量 2。
    """
    import subprocess

    if not video_path.exists():
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{max(0.0, float(at_second)):.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(out_path),
    ]
    proc = subprocess.run(command, capture_output=True, text=True)
    return proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0


async def _run_freezone_channel_short_video_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    """[aipdd fork delta] 渠道短视频：复用讲解视频的 HyperFrames 渲染管线，按渠道定制
    画幅/时长/字幕/封面，产出该渠道规格 MP4 + 封面 + 文案建议；**不自动上传**。

    与讲解视频唯一的结构差异：画幅/时长来自渠道预设（``renderer.presets``），且只走
    hyperframes 引擎——Pillow 回退是固定横屏，无法出竖屏/方形，故渲染服务未配置时直接
    失败并给出配置指引（不静默出错画幅）。出网口径与讲解视频逐字相同：解说词 + 分镜两次
    ``generate_freezone_text``（EG-18a）+ 配音一次 ``generate_freezone_audio_speech``；
    渲染走本地渲染服务、封面抽帧与混音是纯本地 ffmpeg，均不经分发器。
    """
    from novelvideo.api.deps import (
        make_sqlite_store_for_context,
        make_static_url_for_context,
    )
    from novelvideo.freezone.audio_node import generate_freezone_audio_speech
    from novelvideo.freezone.explainer import (
        EXPLAINER_FPS,
        ExplainerChapter,
        build_explainer_narration_prompt,
        build_explainer_storyboard_prompt,
        flatten_sentences,
        normalize_explainer_background,
        parse_explainer_narration,
        parse_explainer_storyboard,
        plan_explainer_timeline,
        resolve_reference_content,
    )
    from novelvideo.freezone.explainer_frames import (
        CANVAS_FORMATS,
        build_explainer_project,
        mux_audio,
        normalize_style_preset,
    )
    from novelvideo.renderer.client import (
        RenderFailed,
        RendererNotConfigured,
    )
    from novelvideo.renderer.client import is_configured as renderer_configured
    from novelvideo.renderer.client import render_project
    from novelvideo.renderer.presets import copy_suggestions, get_channel, resolve_duration
    from novelvideo.freezone.jobs import ensure_freezone_dirs
    from novelvideo.freezone.mix import normalize_mix_resolution
    from novelvideo.freezone.paths import outputs_dir
    from novelvideo.freezone.text_node import generate_freezone_text

    payload = envelope.get("payload") or {}
    task_type = "freezone_channel_short_video"
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    work_dir = outputs_dir(project_dir, task_type) / f"{job_id}_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    channel = get_channel(payload.get("channel"))
    reference = resolve_reference_content(payload.get("reference_content"))
    duration_s = resolve_duration(channel.key, payload.get("duration_s"))
    title_hint = str(payload.get("title") or "").strip()
    model_text = str(payload.get("model_text") or "").strip()
    resolution = normalize_mix_resolution(str(payload.get("resolution") or ""))
    background = normalize_explainer_background(payload.get("background"))
    canvas_format = channel.canvas_format
    hf_width, hf_height = CANVAS_FORMATS.get(canvas_format, CANVAS_FORMATS["landscape"])
    style_preset = normalize_style_preset(payload.get("style_preset"))
    captions_on = (
        bool(payload["captions"])
        if payload.get("captions") is not None
        else channel.captions_default
    )

    if not renderer_configured():
        raise RendererNotConfigured(
            "渠道短视频需要 HyperFrames 渲染服务（配置 DRAMACLAW_RENDERER_URL），"
            "当前未配置；竖屏/方形画幅无本地 Pillow 回退。"
        )

    cancel_kwargs = {
        "project_id": ctx.project_id,
        "task_type": task_type,
        "episode": 0,
        "task_id": str(envelope.get("__run_task_id") or ""),
        "scope": job_id,
    }

    # 1) 解说词：把时长下限压到渠道档、按渠道画幅改写片种提示，其余沿用讲解视频链。
    _update(ctx, task_type, job_id, 0.05, "把源内容改写成解说词...")
    _narration_model, narration_raw = await _await_with_cancel_watch(
        _call_freezone_leaf(
            envelope,
            generate_freezone_text,
            "generate_freezone_text",
            prompt=build_explainer_narration_prompt(
                reference,
                duration_s=duration_s,
                title_hint=title_hint,
                min_s=channel.duration_min_s,
                orientation_label=f"{channel.label} 渠道短视频（{channel.aspect}）",
            ),
            model=model_text or None,
        ),
        **cancel_kwargs,
    )
    chapters = parse_explainer_narration(narration_raw)
    if not chapters:
        lines = tuple(part.strip() for part in reference.splitlines() if part.strip())
        if not lines:
            raise RuntimeError("channel short video narration is empty")
        chapters = [ExplainerChapter(name="讲解", sentences=lines[:80])]
    flat = flatten_sentences(chapters)

    # 2) 分镜：模型只能选词汇表内的图元/动效，帧号一律本地算。
    _update(ctx, task_type, job_id, 0.16, "规划分镜...")
    _storyboard_model, storyboard_raw = await _await_with_cancel_watch(
        _call_freezone_leaf(
            envelope,
            generate_freezone_text,
            "generate_freezone_text",
            prompt=build_explainer_storyboard_prompt(
                [(index, text) for _, index, text in flat],
                title_hint=title_hint,
            ),
            model=model_text or None,
        ),
        **cancel_kwargs,
    )
    title, shots = parse_explainer_storyboard(storyboard_raw, chapters=chapters)

    # 3) 配音：朗读整段解说词，总时长驱动全片帧号分配。
    narration_script = "".join(text for _, _, text in flat)
    _update(ctx, task_type, job_id, 0.26, "合成配音...")
    projection = read_projection(payload)
    store = None if projection is not None else await make_sqlite_store_for_context(ctx)
    try:
        speech = await _await_with_cancel_watch(
            _call_freezone_leaf(
                envelope,
                generate_freezone_audio_speech,
                "generate_freezone_audio_speech",
                store=store,
                username=ctx.owner_username,
                project=ctx.project_name,
                account_voice_username=str(
                    payload.get("account_voice_username")
                    or ctx.requester_username
                    or ctx.owner_username
                ),
                project_dir=project_dir,
                job_id=f"{job_id}_voice",
                text=narration_script,
                emotion_prompt=str(payload.get("emotion_prompt") or ""),
                voice_ref=payload.get("voice_ref"),
                projection=projection,
                model=str(payload.get("model") or ""),
            ),
            **cancel_kwargs,
        )
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()
    narration_path = str(speech.audio_path)
    narration_ms = int(speech.duration_ms or 0)
    narration_model = str(speech.model or "")
    if narration_ms <= 0:
        raise RuntimeError("channel short video narration duration is invalid")

    # 4) 时间轴：句/镜头/字幕块按朗读权重分摊。
    timeline = plan_explainer_timeline(
        chapters=chapters,
        shots=shots,
        title=title,
        duration_ms=narration_ms,
        fps=EXPLAINER_FPS,
    )
    if not timeline.scenes:
        raise RuntimeError("channel short video timeline is empty")

    bgm_path = str(payload.get("bgm_path") or "").strip() or None
    bgm_volume = (
        0.2 if payload.get("bgm_volume") is None else float(payload.get("bgm_volume"))
    )
    output_path = outputs_dir(project_dir, task_type) / f"{job_id}.mp4"

    # 5) 出片：产 HTML 帧项目（渠道画幅）→ 无头渲静音视频 → 本地 ffmpeg 混配音/BGM。
    _update(ctx, task_type, job_id, 0.34, "生成 HyperFrames 帧项目...")
    hf_project_dir = work_dir / "hf_project"
    proj = await asyncio.to_thread(
        build_explainer_project,
        timeline,
        project_dir=hf_project_dir,
        width=hf_width,
        height=hf_height,
        style_preset=style_preset,
        captions=captions_on,
        canvas_format=canvas_format,
    )
    _update(ctx, task_type, job_id, 0.40, "调用渲染服务无头出片...")
    silent_path = work_dir / f"{job_id}_silent.mp4"
    render_timeout_s = int(max(600, proj.duration_seconds * 20))
    try:
        render_result = await render_project(
            proj.project_dir,
            output_path=silent_path,
            fps=proj.fps,
            quality="high",
            canvas_format=canvas_format,
            entry_file=proj.entry_file,
            timeout_s=render_timeout_s,
        )
    except (RendererNotConfigured, RenderFailed) as exc:
        raise RuntimeError(
            f"HyperFrames 渲染失败（渠道短视频无 Pillow 回退）：{str(exc)[:200]}"
        ) from exc

    _update(ctx, task_type, job_id, 0.84, "混入配音与背景音乐...")
    await asyncio.to_thread(
        mux_audio,
        Path(render_result.output_path),
        out_path=output_path,
        narration_path=narration_path,
        bgm_path=bgm_path,
        bgm_volume=bgm_volume,
        duration_seconds=proj.duration_seconds,
    )

    # 6) 封面抽帧 + 渠道文案建议（交付附件，不上传）。
    _update(ctx, task_type, job_id, 0.94, "抽取封面并生成渠道文案...")
    cover_path = outputs_dir(project_dir, task_type) / f"{job_id}_cover.jpg"
    cover_ok = await asyncio.to_thread(
        _extract_video_cover, output_path, cover_path, at_second=0.5
    )
    result_title = title.big or title.rest or title_hint or channel.label
    copy = copy_suggestions(channel.key, title=result_title, narration=narration_script)

    rel = output_path.relative_to(project_dir).as_posix()
    result: dict[str, Any] = {
        "job_id": job_id,
        "output_path": str(output_path),
        "output_url": make_static_url_for_context(ctx, rel),
        "media_type": "video",
        "channel": channel.key,
        "channel_label": channel.label,
        "aspect": channel.aspect,
        "cover_ratio": channel.cover_ratio,
        "narration": narration_script,
        "chapters": [chapter.name for chapter in chapters],
        "shot_count": len(timeline.scenes),
        "title": result_title,
        "narration_model": _narration_model,
        "storyboard_model": _storyboard_model,
        "plan_model": _storyboard_model,
        "model": narration_model or _storyboard_model,
        "narration_duration_ms": narration_ms,
        "requested_duration_s": duration_s,
        "fps": timeline.fps,
        "duration_seconds": round(timeline.duration_seconds, 3),
        "background": background,
        "engine": "hyperframes",
        "style_preset": style_preset,
        "canvas_format": canvas_format,
        "captions": captions_on,
        "resolution": resolution,
        "copy_suggestions": copy,
    }
    if cover_ok:
        cover_rel = cover_path.relative_to(project_dir).as_posix()
        result["cover_path"] = str(cover_path)
        result["cover_url"] = make_static_url_for_context(ctx, cover_rel)
    _archive_tool_output_to_library(
        ctx=ctx,
        payload=payload,
        task_type=task_type,
        job_id=job_id,
        media="video",
        url=str(result["output_url"]),
        result=result,
        stem=_archive_stem_from_text(title.rest or narration_script) or job_id,
    )
    return result


def run_freezone_channel_short_video(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(
        envelope, _run_freezone_channel_short_video_async(envelope, ctx)
    )


# [aipdd fork delta] 口播智能剪辑成片画幅：竖屏（默认）/ 横屏 / 方屏。
_NARRATION_CANVAS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
}


async def _run_freezone_narration_smart_edit_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    """[aipdd fork delta] 口播智能剪辑：把一条口播视频多阶段剪成成片 / 剪映草稿。

    阶段（各按 payload 开关短路）：本地 silencedetect 剪静音停顿（借鉴 auto-editor）
    → 网关 ASR 转写口播 → 文本模型挑口误废话整段删（借鉴 video-use）→ 文本模型理解
    内容拆镜并标 B-roll 槽（借鉴 erduo-broll）→ 生图叶出 B-roll → 音乐叶出 BGM →
    按 output_mode 出直出 MP4（本地 ffmpeg 裁接混）/ VectCut 剪映草稿（多轨落盘）。

    出网口径：转写叶 ``transcribe_freezone_clone_dialogue``（EG-15c）+ 两次
    ``generate_freezone_text``（瑕疵剪 / 分镜，EG-18a）+ 生图叶 ``run_freezone_gen``
    （EG-18b）+ 音乐叶 ``run_freezone_music_generate``（EG-15d），均经分发器。静音检测 /
    裁接 / 混音 / 封面 / 草稿组装都是本地直调，不经分发器（narration_edit / narration_draft
    为新模块，不入 leaf 分类表）。分段时间戳用「静音语音段骨架 + 文本按段时长比例
    对齐」，不依赖 ASR 回分段时间戳（计划允许的回退口径）。
    """
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone import narration_draft, narration_edit
    from novelvideo.freezone.jobs import (
        ensure_freezone_dirs,
        run_freezone_gen,
        transcribe_freezone_clone_dialogue,
    )
    from novelvideo.freezone.minimax_music import run_freezone_music_generate
    from novelvideo.freezone.paths import outputs_dir
    from novelvideo.freezone.text_node import generate_freezone_text
    from novelvideo.freezone.video_node import register_vectcut_draft_item

    payload = envelope.get("payload") or {}
    task_type = "freezone_narration_smart_edit"
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    out_dir = outputs_dir(project_dir, task_type)
    work_dir = out_dir / f"{job_id}_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    source_path = str(payload.get("source_path") or "").strip()
    if not source_path or not Path(source_path).is_file():
        raise RuntimeError(f"口播源视频未找到：{source_path!r}")

    cut_silence = bool(payload.get("cut_silence", True))
    cut_fillers = bool(payload.get("cut_fillers", True))
    plan_broll = bool(payload.get("plan_broll", True))
    gen_bgm = bool(payload.get("gen_bgm", False))
    captions_on = bool(payload.get("captions", True))
    output_mode = str(payload.get("output_mode") or "both").strip().lower()
    if output_mode not in {"draft", "video", "both"}:
        output_mode = "both"
    wants_draft = output_mode in {"draft", "both"}
    wants_video = output_mode in {"video", "both"}
    noise_db = float(payload.get("noise_db") if payload.get("noise_db") is not None else -35.0)
    min_silence_s = float(
        payload.get("min_silence_s") if payload.get("min_silence_s") is not None else 0.4
    )
    aspect = str(payload.get("aspect") or "9:16").strip()
    canvas = _NARRATION_CANVAS.get(aspect) or _NARRATION_CANVAS["9:16"]
    bgm_gain = float(payload.get("bgm_gain") if payload.get("bgm_gain") is not None else 0.2)
    bgm_prompt = str(payload.get("bgm_prompt") or "").strip()
    broll_max = int(payload.get("broll_max") or 8)
    asr_model = str(payload.get("asr_model") or "").strip()
    text_model = str(payload.get("text_model") or "").strip()
    image_model = str(payload.get("image_model") or "").strip()
    image_provider = payload.get("image_provider") or None
    image_model_params = payload.get("image_model_params") or None
    image_request_schema = payload.get("image_request_schema") or None
    music_model = str(payload.get("music_model") or "").strip()

    cancel_kwargs = {
        "project_id": ctx.project_id,
        "task_type": task_type,
        "episode": 0,
        "task_id": str(envelope.get("__run_task_id") or ""),
        "scope": job_id,
    }
    report: list[str] = []

    total = await asyncio.to_thread(narration_edit.probe_duration, source_path)
    if total <= 0.0:
        raise RuntimeError("无法探测源视频时长（ffmpeg/ffprobe 不可用或文件损坏）")

    # 1) 静音/停顿剪（本地 silencedetect）：先拿静音区间，再反推保留（有语音）段。
    _update(ctx, task_type, job_id, 0.05, lmsg("tasks.progress.narration.silence", "检测静音与停顿..."))
    silences = await asyncio.to_thread(
        narration_edit.detect_silences, source_path, noise_db=noise_db, min_silence_s=min_silence_s
    )
    if cut_silence:
        kept = narration_edit.kept_ranges(total, silences)
        report.append(f"静音剪：检出 {len(silences)} 处静音、保留 {len(kept)} 段")
    else:
        kept = [narration_edit.Interval(0.0, total)]
        report.append("静音剪：已关闭，整段保留")
    # 语音段骨架（供文本对齐 / 字幕）：无论是否剪静音，都用 kept 当骨架。
    skeleton = narration_edit.segments_from_ranges(kept)

    # 2) ASR 转写（分发 EG-15c）：best-effort，拿不到不影响成片。
    transcript = ""
    if cut_fillers or plan_broll or captions_on:
        _update(ctx, task_type, job_id, 0.14, lmsg("tasks.progress.narration.asr", "转写口播内容..."))
        transcript = str(
            await _await_with_cancel_watch(
                _call_freezone_leaf(
                    envelope,
                    transcribe_freezone_clone_dialogue,
                    "transcribe_freezone_clone_dialogue",
                    project_dir=project_dir,
                    job_id=job_id,
                    video_path=source_path,
                    asr_model=asr_model,
                ),
                **cancel_kwargs,
            )
            or ""
        ).strip()
    aligned = narration_edit.align_text_to_segments(transcript, skeleton)
    if not transcript:
        report.append("ASR：未拿到转写，字幕/B-roll/瑕疵剪退化为无文本驱动")

    # 3) 瑕疵剪（分发 EG-18a）：模型挑该整段删的保留段编号。
    if cut_fillers and aligned:
        _update(ctx, task_type, job_id, 0.30, lmsg("tasks.progress.narration.defect", "识别口误与废话..."))
        _defect_model, defect_raw = await _await_with_cancel_watch(
            _call_freezone_leaf(
                envelope,
                generate_freezone_text,
                "generate_freezone_text",
                prompt=narration_edit.build_defect_prompt(aligned),
                model=text_model or None,
            ),
            **cancel_kwargs,
        )
        drops = set(narration_edit.parse_defect_drops(defect_raw, len(kept)))
        if drops:
            kept = [seg for index, seg in enumerate(kept) if index not in drops]
            aligned = [seg for index, seg in enumerate(aligned) if index not in drops]
            report.append(f"瑕疵剪：整段剔除 {len(drops)} 段")
        else:
            report.append("瑕疵剪：未检出需剔除的整段")
    if not kept:
        raise RuntimeError("剪完无保留段（阈值过激），请下调静音阈值或关闭瑕疵剪")

    # 4) 最终时间轴：把源时间轴上的语音段整体映射到剪后拼接时间轴（供字幕与 B-roll 落位）。
    final_segs = [narration_edit.remap_segment_to_final(seg, kept) for seg in aligned]
    final_duration = narration_edit.final_duration(kept)

    # 5) 内容理解 + 分镜 + B-roll 判定（分发 EG-18a），仅 draft 输出才真生成 B-roll 图。
    broll_slots: list[dict[str, Any]] = []
    broll_images: list[dict[str, Any]] = []
    if plan_broll and final_segs and wants_draft:
        _update(ctx, task_type, job_id, 0.50, lmsg("tasks.progress.narration.storyboard", "拆解镜头与 B-roll..."))
        _board_model, board_raw = await _await_with_cancel_watch(
            _call_freezone_leaf(
                envelope,
                generate_freezone_text,
                "generate_freezone_text",
                prompt=narration_edit.build_storyboard_prompt(
                    final_segs, final_duration=final_duration, aspect=aspect
                ),
                model=text_model or None,
            ),
            **cancel_kwargs,
        )
        broll_slots = narration_edit.parse_broll_plan(
            board_raw, final_duration=final_duration, max_slots=broll_max
        )
        if broll_slots:
            _update(ctx, task_type, job_id, 0.64, lmsg("tasks.progress.narration.broll", "生成 B-roll 画面..."))
        for slot_index, slot in enumerate(broll_slots):
            try:
                image_path = await _await_with_cancel_watch(
                    _call_freezone_leaf(
                        envelope,
                        run_freezone_gen,
                        "run_freezone_gen",
                        project_dir=project_dir,
                        job_id=f"{job_id}_broll{slot_index}",
                        prompt=slot["prompt"],
                        aspect_ratio=aspect,
                        image_size="2K",
                        provider=image_provider,
                        model=image_model or None,
                        model_params=image_model_params,
                        request_schema=image_request_schema,
                        output_task_type="freezone_narration_smart_edit",
                    ),
                    **cancel_kwargs,
                )
            except Exception:  # noqa: BLE001 - 单张 B-roll 失败不致命
                report.append(f"B-roll 槽 #{slot_index} 生图失败，已跳过")
                continue
            if image_path is not None:
                broll_images.append(
                    {
                        "url": str(image_path),
                        "timeline_start": slot["t_start"],
                        "timeline_end": slot["t_end"],
                    }
                )
    elif plan_broll and not wants_draft:
        report.append("B-roll：仅在剪映草稿里落轨，直出 MP4 不烧录 B-roll")

    # 6) BGM（分发 EG-15d，best-effort）：组织任务下音乐叶 fail-closed，降级为无 BGM。
    bgm_path = ""
    if gen_bgm:
        _update(ctx, task_type, job_id, 0.72, lmsg("tasks.progress.narration.bgm", "生成背景音乐..."))
        try:
            music = await _await_with_cancel_watch(
                _call_freezone_leaf(
                    envelope,
                    run_freezone_music_generate,
                    "run_freezone_music_generate",
                    project_dir=project_dir,
                    job_id=f"{job_id}_bgm",
                    mode="prompt",
                    model=music_model or "music-3.0",
                    prompt=bgm_prompt or "轻快、低强度的无人声背景垫乐",
                ),
                **cancel_kwargs,
            )
            bgm_path = str(music.audio_path)
            report.append("BGM：已生成")
        except Exception as exc:  # noqa: BLE001 - 无可用音乐通道不致命（组织任务会命中这里）
            report.append(f"BGM：生成失败，降级为无 BGM（{str(exc)[:120]}）")

    result: dict[str, Any] = {
        "job_id": job_id,
        "media_type": "video",
        "output_mode": output_mode,
        "aspect": aspect,
        "source_duration": round(total, 3),
        "final_duration": final_duration,
        "transcript": transcript,
        "kept_ranges": [
            {"start": round(seg.start, 3), "end": round(seg.end, 3)} for seg in kept
        ],
        "broll_slots": broll_slots,
        "broll_count": len(broll_images),
        "report": report,
    }

    # 7a) 剪映草稿：VectCut 多轨落盘（正片逐段 + 字幕 + B-roll + BGM）。
    if wants_draft:
        srt_path = ""
        if captions_on:
            srt_text = narration_edit.build_srt(final_segs)
            if srt_text:
                srt_file = out_dir / f"{job_id}.srt"
                await asyncio.to_thread(
                    srt_file.write_text, srt_text, "utf-8"
                )
                srt_path = str(srt_file)
        cursor = 0.0
        video_clips: list[dict[str, Any]] = []
        for seg in kept:
            video_clips.append(
                {
                    "url": source_path,
                    "source_start": round(seg.start, 3),
                    "source_end": round(seg.end, 3),
                    "timeline_start": round(cursor, 3),
                    "duration": round(seg.duration, 3),
                }
            )
            cursor += seg.duration
        draft = await narration_draft.build_vectcut_draft(
            width=canvas[0],
            height=canvas[1],
            video_clips=video_clips,
            srt_path=srt_path,
            broll_images=broll_images,
            bgm_path=bgm_path,
            bgm_gain=bgm_gain,
            final_duration=final_duration,
            draft_folder=str(payload.get("draft_folder") or "").strip(),
            project_name=str(payload.get("title") or "").strip() or f"口播剪辑_{job_id}",
        )
        result["draft"] = draft.as_dict()
        if draft.ok and draft.draft_id:
            result["draft_id"] = draft.draft_id
            try:
                register_vectcut_draft_item(
                    project_dir,
                    draft_id=draft.draft_id,
                    name=str(payload.get("title") or "").strip() or f"口播剪辑_{job_id}",
                )
            except Exception:  # noqa: BLE001 - 登记不阻断草稿已落盘
                report.append("草稿已落盘，资产库登记失败（不致命）")
        elif draft.not_configured:
            report.append(draft.hint)
        else:
            report.append(f"草稿组装未完成：{draft.error or draft.hint}")

    # 7b) 直出 MP4：本地裁接 + 混 BGM + 封面（B-roll 不入基底）。
    if wants_video:
        _update(ctx, task_type, job_id, 0.88, lmsg("tasks.progress.narration.render", "裁接正片并混音..."))
        base_path = work_dir / f"{job_id}_base.mp4"
        cut_ok = await asyncio.to_thread(
            narration_edit.cut_and_concat,
            source_path,
            kept,
            str(base_path),
            work_dir=str(work_dir),
        )
        video_src = str(base_path) if cut_ok else source_path
        if not cut_ok:
            report.append("本地裁接失败，回退用原片作基底")
        output_path = out_dir / f"{job_id}.mp4"
        mixed = await asyncio.to_thread(
            narration_edit.mix_bgm, video_src, bgm_path, str(output_path), bgm_gain=bgm_gain
        )
        if not mixed:
            raise RuntimeError("成片输出失败（基底与混音均未能落盘）")
        rel = output_path.relative_to(project_dir).as_posix()
        result["output_path"] = str(output_path)
        result["output_url"] = make_static_url_for_context(ctx, rel)
        result["bgm_applied"] = bool(bgm_path)
        cover_path = out_dir / f"{job_id}_cover.jpg"
        cover_ok = await asyncio.to_thread(
            _extract_video_cover, output_path, cover_path, at_second=0.5
        )
        if cover_ok:
            cover_rel = cover_path.relative_to(project_dir).as_posix()
            result["cover_path"] = str(cover_path)
            result["cover_url"] = make_static_url_for_context(ctx, cover_rel)
        _archive_tool_output_to_library(
            ctx=ctx,
            payload=payload,
            task_type=task_type,
            job_id=job_id,
            media="video",
            url=str(result["output_url"]),
            result=result,
            stem=narration_edit.join_transcript(final_segs)[:20] or job_id,
        )
    _update(ctx, task_type, job_id, 1.0, lmsg("tasks.progress.narration.done", "口播智能剪辑完成"))
    return result


def run_freezone_narration_smart_edit(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(
        envelope, _run_freezone_narration_smart_edit_async(envelope, ctx)
    )



async def _run_freezone_video_idea_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    """一句话生成短视频（``freezone_video_idea``）：ViMax idea2video 口径。

    一句话创意 → 故事扩写 → 角色提取与三视图 → 分场景脚本 → 逐场景
    （分镜 → 镜头分解 → 机位树 → 逐帧 → 逐镜视频）→ 场景拼接 → 成片。
    帧/肖像走图片 leaf，过渡与逐镜视频走视频 leaf，拼接纯本地 ffmpeg。
    """
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.idea import (
        DEFAULT_IDEA_SCENES,
        DEFAULT_IDEA_SHOTS_PER_SCENE,
        DEFAULT_IDEA_STYLE,
        IDEA_FRAME_ASPECT_RATIO,
        MAX_IDEA_CHARACTERS,
        MAX_IDEA_SCENES,
        MAX_IDEA_SHOTS_PER_SCENE,
        MIN_IDEA_SHOT_SECONDS,
        IdeaShot,
        IdeaShotBrief,
        apply_idea_camera_tree,
        build_idea_camera_tree_prompt,
        build_idea_characters_prompt,
        build_idea_decompose_prompt,
        build_idea_new_camera_text,
        build_idea_portrait_back_prompt,
        build_idea_portrait_front_prompt,
        build_idea_portrait_side_prompt,
        build_idea_reference_selection_prompt,
        build_idea_scene_scripts_prompt,
        build_idea_story_prompt,
        build_idea_storyboard_prompt,
        build_idea_transition_video_prompt,
        camera_topological_order,
        clamp_idea_shot_seconds,
        clamp_idea_total_seconds,
        compose_idea_frame_prompt,
        derive_idea_scene_cap,
        group_shots_into_cameras,
        parse_idea_characters,
        parse_idea_decompose,
        parse_idea_reference_selection,
        parse_idea_scene_scripts,
        parse_idea_storyboard,
        portrait_description,
    )
    from novelvideo.freezone.jobs import (
        concat_freezone_video_segments,
        ensure_freezone_dirs,
        extract_idea_new_camera_frame,
        run_freezone_gen,
    )
    from novelvideo.freezone.paths import outputs_dir
    from novelvideo.freezone.text_node import generate_freezone_text
    from novelvideo.freezone.video_node import run_trusted_freezone_video_gen
    from novelvideo.task_backend.cancel import TaskCancelled, TaskTimedOut
    from novelvideo.usage.video_duration import normalize_video_duration_for_backend

    payload = envelope.get("payload") or {}
    task_type = "freezone_video_idea"
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)

    idea = str(payload.get("idea") or "").strip()
    if not idea:
        raise ValueError("idea is required")
    style = str(payload.get("style") or "").strip() or DEFAULT_IDEA_STYLE
    user_requirement = str(payload.get("user_requirement") or "").strip()
    # [aipdd fork delta] 文本步骤的用户显式选型（故事/角色/脚本/分镜等），
    # 空 = 走系统默认文本模型链。
    model_text = str(payload.get("model_text") or "").strip()

    def _int_in_range(value: Any, *, low: int, high: int, default: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return max(low, min(number, high))

    max_scenes = _int_in_range(
        payload.get("max_scenes"),
        low=1,
        high=MAX_IDEA_SCENES,
        default=DEFAULT_IDEA_SCENES,
    )
    max_shots = _int_in_range(
        payload.get("max_shots_per_scene"),
        low=1,
        high=MAX_IDEA_SHOTS_PER_SCENE,
        default=DEFAULT_IDEA_SHOTS_PER_SCENE,
    )
    shot_seconds = clamp_idea_shot_seconds(payload.get("shot_duration"))
    # [需求6] 总时长口径：工具页只给总时长，分场/分镜/单镜时长交给 AI——
    # 场景数上限按「每场景至少承载约 10 秒」推导，镜头数上限放满，分镜
    # prompt 再以「场景预算」约束 AI 自定的镜头数与每镜时长（3-10s）。
    total_duration = clamp_idea_total_seconds(payload.get("total_duration"))
    duration_governed = total_duration is not None
    if duration_governed:
        max_scenes = derive_idea_scene_cap(total_duration)
        max_shots = MAX_IDEA_SHOTS_PER_SCENE
    portrait_mode = str(payload.get("portrait_mode") or "three").strip().lower()
    portrait_mode = "front" if portrait_mode == "front" else "three"
    aspect_ratio = str(payload.get("aspect_ratio") or "").strip() or "16:9"
    resolution = str(payload.get("resolution") or "").strip() or "720p"
    generate_audio = bool(payload.get("generate_audio"))
    backend = str(payload.get("backend") or "newapi_seedance-2.0-fast")
    shot_video_seconds = normalize_video_duration_for_backend(backend, shot_seconds)
    human_review = bool(payload.get("human_review"))
    scene_optimize = payload.get("scene_optimize") or None
    model_params = payload.get("model_params") or None
    request_schema = payload.get("request_schema") or None
    image_provider = payload.get("image_provider")
    image_model = payload.get("image_model")
    image_model_params = payload.get("image_model_params") or None
    image_request_schema = payload.get("image_request_schema") or None

    # 视频 leaf 的 egress：org/local 带受信任上下文；platform 保持直连语义。
    trusted_context = _extract_trusted_egress_context(envelope)
    video_egress: Any = None
    if (
        trusted_context is not None
        and trusted_context.billing_principal.kind in {"organization", "local"}
    ):
        video_egress = trusted_context

    cancel_kwargs = {
        "project_id": ctx.project_id,
        "task_type": task_type,
        "episode": 0,
        "task_id": str(envelope.get("__run_task_id") or ""),
        "scope": job_id,
    }

    work_dir = outputs_dir(project_dir, task_type) / f"{job_id}_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    async def _text_leaf(prompt: str) -> str:
        _model, text = await _await_with_cancel_watch(
            _call_freezone_leaf(
                envelope,
                generate_freezone_text,
                "generate_freezone_text",
                prompt=prompt,
                model=model_text or None,
            ),
            **cancel_kwargs,
        )
        return str(text or "").strip()

    async def _image_leaf(
        *,
        sub_job: str,
        prompt: str,
        reference_paths: list[str] | None = None,
    ) -> Path:
        out = await _await_with_cancel_watch(
            _call_freezone_leaf(
                envelope,
                run_freezone_gen,
                "run_freezone_gen",
                project_dir=project_dir,
                job_id=sub_job,
                prompt=prompt,
                aspect_ratio=IDEA_FRAME_ASPECT_RATIO,
                image_size="2K",
                reference_paths=reference_paths or None,
                provider=image_provider,
                model=image_model,
                model_params=image_model_params,
                request_schema=image_request_schema,
                output_task_type=task_type,
            ),
            **cancel_kwargs,
        )
        return Path(out)

    async def _video_leaf(
        *,
        sub_job: str,
        prompt: str,
        reference_path: Path,
        last_frame_path: Path | None = None,
        duration_seconds: float | None = None,
    ) -> Path:
        out = await _await_with_cancel_watch(
            run_trusted_freezone_video_gen(
                project_dir=project_dir,
                job_id=sub_job,
                prompt=prompt,
                egress_context=video_egress,
                task_type=task_type,
                episode=int(envelope.get("episode") or 0),
                beat_num=int(envelope.get("beat_num") or 0),
                scope=job_id,
                reference_items=[
                    {"type": "image", "path": str(reference_path), "role": "首帧"}
                ],
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                duration_seconds=(
                    shot_video_seconds
                    if duration_seconds is None
                    else duration_seconds
                ),
                generate_audio=generate_audio,
                human_review=human_review,
                scene_optimize=scene_optimize,
                backend=backend,
                last_frame_path=str(last_frame_path) if last_frame_path else None,
                gen_mode="first_last_frame" if last_frame_path else "first_frame",
                model_params=model_params,
                request_schema=request_schema,
            ),
            **cancel_kwargs,
        )
        return Path(out)

    async def _selector_frame(
        *,
        sub_job: str,
        frame_desc: str,
        available_pairs: list[tuple[str, str]],
    ) -> Path:
        """ViMax 参考图选择器的纯文本口径 + 帧图片生成。

        文本 leaf 按帧描述从可用参考图里选图并改写提示词（ViMax ≥8 张时的
        纯文本过滤在这里即最终选择）；选择失败（网络/JSON 解析）降级为无
        参考图直生成；取消/超时照常上抛，不吞任务取消。
        """
        clean_desc = str(frame_desc or "").strip()
        reference_paths: list[str] = []
        final_prompt = clean_desc
        if available_pairs:
            try:
                selector_text = await _text_leaf(
                    build_idea_reference_selection_prompt(
                        frame_description=clean_desc,
                        available_texts=[text for _path, text in available_pairs],
                    )
                )
                indices, text_prompt = parse_idea_reference_selection(
                    selector_text, total=len(available_pairs)
                )
            except (TaskCancelled, TaskTimedOut):
                raise
            except Exception as exc:
                logger.warning("[freezone] idea frame selector failed: %s", exc)
                indices, text_prompt = [], ""
            if indices:
                selected = [available_pairs[index] for index in indices]
                reference_paths = [path for path, _text in selected]
                final_prompt = compose_idea_frame_prompt(
                    reference_texts=[text for _path, text in selected],
                    text_prompt=text_prompt or clean_desc,
                )
            elif text_prompt:
                final_prompt = text_prompt
        return await _image_leaf(
            sub_job=sub_job,
            prompt=final_prompt,
            reference_paths=reference_paths,
        )

    # 1) 故事扩写（ViMax Screenwriter.develop_story）。
    _update(ctx, task_type, job_id, 0.03, "扩写一句话创意为故事...")
    story_model, story_text = await _await_with_cancel_watch(
        _call_freezone_leaf(
            envelope,
            generate_freezone_text,
            "generate_freezone_text",
            prompt=build_idea_story_prompt(
                idea=idea, user_requirement=user_requirement
            ),
            model=model_text or None,
        ),
        **cancel_kwargs,
    )
    story = str(story_text or "").strip()
    if not story:
        raise RuntimeError("idea story generation returned empty text")
    (work_dir / "story.txt").write_text(story, encoding="utf-8")

    # 2) 角色提取（ViMax CharacterExtractor）。
    _update(ctx, task_type, job_id, 0.06, "提取故事角色...")
    characters = parse_idea_characters(
        await _text_leaf(
            build_idea_characters_prompt(story=story, limit=MAX_IDEA_CHARACTERS)
        ),
        limit=MAX_IDEA_CHARACTERS,
    )
    visible_characters = [
        character for character in characters if character.is_visible
    ]
    (work_dir / "characters.json").write_text(
        json.dumps(
            [
                {
                    "idx": character.idx,
                    "identifier": character.identifier,
                    "is_visible": character.is_visible,
                    "static_features": character.static_features,
                    "dynamic_features": character.dynamic_features,
                }
                for character in characters
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # 3) 角色三视图（ViMax CharacterPortraitsGenerator）：front 为基；
    #    three 模式追加 side/back（基于 front 生成，失败复制 front 兜底）。
    portrait_registry: dict[str, dict[str, dict[str, str]]] = {}
    portrait_total = len(visible_characters)
    for order, character in enumerate(visible_characters, start=1):
        _update(
            ctx,
            task_type,
            job_id,
            round(0.08 + (order - 1) / max(1, portrait_total) * 0.20, 3),
            f"生成角色「{character.identifier}」肖像 {order}/{portrait_total}...",
        )
        front_path = await _image_leaf(
            sub_job=f"{job_id}_c{order:02d}_front",
            prompt=build_idea_portrait_front_prompt(
                identifier=character.identifier,
                static_features=character.static_features,
                dynamic_features=character.dynamic_features,
                style=style,
            ),
        )
        views: dict[str, dict[str, str]] = {
            "front": {
                "path": str(front_path),
                "description": portrait_description(
                    identifier=character.identifier, view="front"
                ),
            }
        }
        if portrait_mode == "three":
            for view, prompt_builder in (
                ("side", build_idea_portrait_side_prompt),
                ("back", build_idea_portrait_back_prompt),
            ):
                try:
                    view_path = await _image_leaf(
                        sub_job=f"{job_id}_c{order:02d}_{view}",
                        prompt=prompt_builder(identifier=character.identifier),
                        reference_paths=[str(front_path)],
                    )
                except (TaskCancelled, TaskTimedOut):
                    raise
                except Exception as exc:
                    logger.warning(
                        "[freezone] idea portrait %s view failed for %s: %s",
                        view,
                        character.identifier,
                        exc,
                    )
                    fallback_path = work_dir / f"portrait_c{order:02d}_{view}.png"
                    shutil.copyfile(front_path, fallback_path)
                    view_path = fallback_path
                views[view] = {
                    "path": str(view_path),
                    "description": portrait_description(
                        identifier=character.identifier, view=view
                    ),
                }
        portrait_registry[character.identifier] = views

    # 4) 分场景脚本（ViMax Screenwriter.write_script_based_on_story）。
    #    模型 JSON 输出偶发波动（截断 / 夹带说明文字 / 键名偏差）时重试一次；
    #    仍失败才报错，并把原始输出落盘供事后诊断（报错信息带模型名与长度）。
    _update(ctx, task_type, job_id, 0.30, "把故事改编为分场景脚本...")
    scene_scripts: list[str] = []
    scene_scripts_raw = ""
    scene_scripts_model = ""
    for _attempt in range(2):
        scene_scripts_model, raw_text = await _await_with_cancel_watch(
            _call_freezone_leaf(
                envelope,
                generate_freezone_text,
                "generate_freezone_text",
                prompt=build_idea_scene_scripts_prompt(
                    story=story,
                    user_requirement=user_requirement,
                    max_scenes=max_scenes,
                    total_duration_seconds=total_duration,
                ),
                model=model_text or None,
            ),
            **cancel_kwargs,
        )
        scene_scripts_raw = str(raw_text or "")
        scene_scripts = parse_idea_scene_scripts(
            scene_scripts_raw, max_scenes=max_scenes
        )
        if scene_scripts:
            break
        logger.warning(
            "[freezone] idea scene scripts parse failed (attempt %s, model=%s, "
            "chars=%s, head=%r)",
            _attempt + 1,
            scene_scripts_model,
            len(scene_scripts_raw),
            scene_scripts_raw[:180],
        )
    if not scene_scripts:
        raw_path = work_dir / "scene_scripts_raw.txt"
        raw_path.write_text(scene_scripts_raw, encoding="utf-8")
        raise RuntimeError(
            "idea scene script generation returned no scenes "
            f"(model={scene_scripts_model or 'default'}, "
            f"output_chars={len(scene_scripts_raw)}, raw={raw_path.name})"
        )
    (work_dir / "script.json").write_text(
        json.dumps({"script": scene_scripts}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # [需求6] 总时长口径下每场景预算：均分总时长（下限为单镜最短时长）；
    # 分镜 prompt 以预算约束 AI 自定的镜头数与每镜时长。
    scene_budget_seconds: float | None = None
    if duration_governed and total_duration is not None:
        scene_budget_seconds = max(
            float(MIN_IDEA_SHOT_SECONDS), total_duration / len(scene_scripts)
        )

    # 5) 逐场景（ViMax Script2VideoPipeline）：分镜 → 镜头分解 → 机位树 →
    #    逐帧 → 逐镜视频 → 场景拼接。帧/肖像走图片 leaf，过渡与逐镜视频走
    #    视频 leaf，拼接纯本地 ffmpeg。
    scene_total = len(scene_scripts)
    shot_total = 0
    planned_seconds_total = 0.0
    scene_clip_paths: list[Path] = []
    scene_outputs: list[dict[str, Any]] = []
    idea_dir = outputs_dir(project_dir, task_type)

    def _available_char_pairs(char_idxs: list[int]) -> list[tuple[str, str]]:
        """角色索引 → 其肖像全部视图的 (path, text) 参考对（ViMax 口径）。"""
        pairs: list[tuple[str, str]] = []
        for char_idx in char_idxs:
            if not 0 <= char_idx < len(characters):
                continue
            views = portrait_registry.get(characters[char_idx].identifier)
            if not views:
                continue
            for view in ("front", "side", "back"):
                item = views.get(view)
                if item:
                    pairs.append((str(item["path"]), str(item["description"])))
        return pairs

    for scene_index, scene_script in enumerate(scene_scripts):
        scene_number = scene_index + 1
        scene_base = round(0.34 + scene_index / scene_total * 0.54, 3)
        scene_span = 0.54 / scene_total

        # 5.1 分镜（ViMax StoryboardArtist.design_storyboard）：与场景脚本同
        # 口径，模型偶发坏 JSON 时重试一次，避免单次波动打穿整链。
        _update(
            ctx,
            task_type,
            job_id,
            scene_base,
            f"场景 {scene_number}/{scene_total}：设计分镜...",
        )
        briefs: list[IdeaShotBrief] = []
        for _attempt in range(2):
            briefs = parse_idea_storyboard(
                await _text_leaf(
                    build_idea_storyboard_prompt(
                        script=scene_script,
                        characters=characters,
                        user_requirement=user_requirement,
                        max_shots=max_shots,
                        target_duration_seconds=scene_budget_seconds,
                    )
                ),
                max_shots=max_shots,
            )
            if briefs:
                break
        if not briefs:
            raise RuntimeError(
                f"idea storyboard generation returned no shots (scene {scene_number})"
            )
        # 镜头时长表：AI 规划值（已归一 3-10s）缺省回退 shot_duration 基线。
        brief_durations: dict[int, float] = {
            brief.idx: (
                brief.duration_seconds
                if brief.duration_seconds is not None
                else shot_seconds
            )
            for brief in briefs
        }

        # 5.2 镜头分解（每镜头最多 2 次尝试，ViMax tenacity retry 口径）。
        _update(
            ctx,
            task_type,
            job_id,
            round(scene_base + scene_span * 0.08, 3),
            f"场景 {scene_number}/{scene_total}：分解镜头描述...",
        )
        shots: list[IdeaShot] = []
        for brief in briefs:
            shot: IdeaShot | None = None
            for _attempt in range(2):
                shot = parse_idea_decompose(
                    await _text_leaf(
                        build_idea_decompose_prompt(
                            visual_desc=brief.visual_desc,
                            characters=characters,
                        )
                    ),
                    brief,
                    character_count=len(characters),
                )
                if shot is not None:
                    break
            if shot is None:
                raise RuntimeError(
                    "idea shot decomposition failed "
                    f"(scene {scene_number}, shot {brief.idx})"
                )
            shots.append(shot)
        shot_total += len(shots)
        shot_by_idx = {shot.idx: shot for shot in shots}

        # 5.3 机位树（最多 2 次尝试；失败降级为全部 root，镜头直挂）。
        _update(
            ctx,
            task_type,
            job_id,
            round(scene_base + scene_span * 0.16, 3),
            f"场景 {scene_number}/{scene_total}：构建机位树...",
        )
        cameras = group_shots_into_cameras(shots)
        tree_ok = False
        for _attempt in range(2):
            if apply_idea_camera_tree(
                await _text_leaf(
                    build_idea_camera_tree_prompt(cameras=cameras, shots=shots)
                ),
                cameras,
                shots,
            ):
                tree_ok = True
                break
        if not tree_ok:
            logger.warning(
                "[freezone] idea camera tree fallback to flat roots (scene %s)",
                scene_number,
            )
        (work_dir / f"scene_{scene_number:02d}.json").write_text(
            json.dumps(
                {
                    "scene": scene_number,
                    "script": scene_script,
                    "storyboard": [
                        {
                            "idx": brief.idx,
                            "cam_idx": brief.cam_idx,
                            "visual_desc": brief.visual_desc,
                            "audio_desc": brief.audio_desc,
                        }
                        for brief in briefs
                    ],
                    "shots": [
                        {
                            "idx": shot.idx,
                            "cam_idx": shot.cam_idx,
                            "ff_desc": shot.ff_desc,
                            "lf_desc": shot.lf_desc,
                            "motion_desc": shot.motion_desc,
                            "audio_desc": shot.audio_desc,
                            "variation_type": shot.variation_type,
                            "ff_vis_char_idxs": shot.ff_vis_char_idxs,
                            "lf_vis_char_idxs": shot.lf_vis_char_idxs,
                        }
                        for shot in shots
                    ],
                    "cameras": [
                        {
                            "idx": camera.idx,
                            "active_shot_idxs": camera.active_shot_idxs,
                            "parent_cam_idx": camera.parent_cam_idx,
                            "parent_shot_idx": camera.parent_shot_idx,
                            "is_parent_fully_covers_child": (
                                camera.is_parent_fully_covers_child
                            ),
                            "missing_info": camera.missing_info,
                        }
                        for camera in cameras
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        # 5.4 逐帧（ViMax generate_frames_for_single_camera）：机位按拓扑序
        #     处理（父机位先出帧），父镜头首帧先于子机位过渡视频。
        ff_paths: dict[int, Path] = {}
        lf_paths: dict[int, Path] = {}
        ordered_cameras = camera_topological_order(cameras)
        camera_total = max(1, len(ordered_cameras))
        for camera_order, camera in enumerate(ordered_cameras, start=1):
            _update(
                ctx,
                task_type,
                job_id,
                round(
                    scene_base
                    + scene_span * (0.22 + 0.56 * (camera_order - 1) / camera_total),
                    3,
                ),
                (
                    f"场景 {scene_number}/{scene_total}："
                    f"生成机位 {camera_order}/{camera_total} 画面帧..."
                ),
            )
            first_shot = shot_by_idx[camera.active_shot_idxs[0]]
            first_ff_path = (
                idea_dir / f"{job_id}_s{scene_number:02d}_f{first_shot.idx:02d}ff.png"
            )

            # 5.4.1 机位首镜首帧：父机位存在时先生成过渡视频并抽「新机位」
            #       参考帧；缺少信息时并入参考图集交给选择器，父机位完整
            #       覆盖时直接拷贝（ViMax 口径）。
            new_camera_path: Path | None = None
            if camera.parent_shot_idx is not None:
                parent_shot = shot_by_idx.get(camera.parent_shot_idx)
                parent_ff_path = ff_paths.get(camera.parent_shot_idx)
                if parent_shot is not None and parent_ff_path is not None:
                    transition_path = await _video_leaf(
                        sub_job=f"{job_id}_s{scene_number:02d}_t{first_shot.idx:02d}",
                        prompt=build_idea_transition_video_prompt(
                            parent_visual_desc=parent_shot.visual_desc,
                            child_visual_desc=first_shot.visual_desc,
                        ),
                        reference_path=parent_ff_path,
                    )
                    new_camera_path = await _await_with_cancel_watch(
                        extract_idea_new_camera_frame(
                            transition_video_path=transition_path,
                            output_path=(
                                idea_dir
                                / f"{job_id}_s{scene_number:02d}_f{first_shot.idx:02d}_cam.png"
                            ),
                        ),
                        **cancel_kwargs,
                    )
                else:
                    logger.warning(
                        "[freezone] idea parent frame missing; selector fallback "
                        "(scene %s, camera %s, parent shot %s)",
                        scene_number,
                        camera.idx,
                        camera.parent_shot_idx,
                    )
            available_pairs = _available_char_pairs(first_shot.ff_vis_char_idxs)
            if new_camera_path is not None:
                available_pairs.append(
                    (
                        str(new_camera_path),
                        build_idea_new_camera_text(missing_info=camera.missing_info),
                    )
                )
            if new_camera_path is None or camera.missing_info is not None:
                ff_paths[first_shot.idx] = await _selector_frame(
                    sub_job=f"{job_id}_s{scene_number:02d}_f{first_shot.idx:02d}ff",
                    frame_desc=first_shot.ff_desc,
                    available_pairs=available_pairs,
                )
            else:
                shutil.copyfile(new_camera_path, first_ff_path)
                ff_paths[first_shot.idx] = first_ff_path

            # 5.4.2 首镜尾帧与其余镜头帧：均以机位首镜首帧为锚参考（ViMax 口径）。
            anchor_pair = [(str(first_ff_path), first_shot.ff_desc)]
            if first_shot.needs_last_frame:
                lf_paths[first_shot.idx] = await _selector_frame(
                    sub_job=f"{job_id}_s{scene_number:02d}_f{first_shot.idx:02d}lf",
                    frame_desc=first_shot.lf_desc,
                    available_pairs=(
                        _available_char_pairs(first_shot.lf_vis_char_idxs) + anchor_pair
                    ),
                )
            for shot_idx in camera.active_shot_idxs[1:]:
                shot = shot_by_idx[shot_idx]
                ff_paths[shot_idx] = await _selector_frame(
                    sub_job=f"{job_id}_s{scene_number:02d}_f{shot_idx:02d}ff",
                    frame_desc=shot.ff_desc,
                    available_pairs=(
                        _available_char_pairs(shot.ff_vis_char_idxs) + anchor_pair
                    ),
                )
                if shot.needs_last_frame:
                    lf_paths[shot_idx] = await _selector_frame(
                        sub_job=f"{job_id}_s{scene_number:02d}_f{shot_idx:02d}lf",
                        frame_desc=shot.lf_desc,
                        available_pairs=(
                            _available_char_pairs(shot.lf_vis_char_idxs) + anchor_pair
                        ),
                    )

        # 5.5 逐镜视频（ViMax generate_video_for_single_shot）：
        #     prompt = motion_desc + "\n" + audio_desc；首帧必传，medium/large 追加尾帧。
        shot_clip_paths: list[Path] = []
        for shot_order, shot in enumerate(shots, start=1):
            _update(
                ctx,
                task_type,
                job_id,
                round(
                    scene_base
                    + scene_span * (0.80 + 0.17 * (shot_order - 1) / len(shots)),
                    3,
                ),
                (
                    f"场景 {scene_number}/{scene_total}："
                    f"生成镜头视频 {shot_order}/{len(shots)}..."
                ),
            )
            video_seconds = shot_video_seconds
            if duration_governed:
                video_seconds = normalize_video_duration_for_backend(
                    backend, brief_durations.get(shot.idx, shot_seconds)
                )
            planned_seconds_total += float(video_seconds)
            shot_clip_paths.append(
                await _video_leaf(
                    sub_job=f"{job_id}_s{scene_number:02d}_v{shot.idx:02d}",
                    prompt=f"{shot.motion_desc}\n{shot.audio_desc}",
                    reference_path=ff_paths[shot.idx],
                    last_frame_path=(
                        lf_paths.get(shot.idx) if shot.needs_last_frame else None
                    ),
                    duration_seconds=video_seconds,
                )
            )

        # 5.6 场景拼接（ViMax 场景内 concatenate_videoclips）。
        _update(
            ctx,
            task_type,
            job_id,
            round(scene_base + scene_span * 0.98, 3),
            f"场景 {scene_number}/{scene_total}：拼接场景画面...",
        )
        scene_clip_path = idea_dir / f"{job_id}_scene{scene_number:02d}.mp4"
        await _await_with_cancel_watch(
            concat_freezone_video_segments(
                segment_paths=shot_clip_paths, output_path=scene_clip_path
            ),
            **cancel_kwargs,
        )
        scene_clip_paths.append(scene_clip_path)
        scene_outputs.append(
            {
                "scene": scene_number,
                "shot_count": len(shots),
                "camera_count": len(cameras),
                "video_path": str(scene_clip_path),
                "video_url": make_static_url_for_context(
                    ctx, scene_clip_path.relative_to(project_dir).as_posix()
                ),
            }
        )

    # 6) 全片拼接（ViMax idea2video：场景成片按序相接）。
    _update(ctx, task_type, job_id, 0.90, "拼接全片...")
    output_path = outputs_dir(project_dir, task_type) / f"{job_id}.mp4"
    await _await_with_cancel_watch(
        concat_freezone_video_segments(
            segment_paths=scene_clip_paths, output_path=output_path
        ),
        **cancel_kwargs,
    )

    # 7) 归档与结果。
    _update(ctx, task_type, job_id, 0.97, "登记短视频成片...")
    rel = output_path.relative_to(project_dir).as_posix()
    result: dict[str, Any] = {
        "job_id": job_id,
        "output_path": str(output_path),
        "output_url": make_static_url_for_context(ctx, rel),
        "media_type": "video",
        "idea": idea,
        "style": style,
        "model": story_model,
        "characters": [
            {"idx": character.idx, "identifier": character.identifier}
            for character in characters
        ],
        "character_count": len(characters),
        "visible_character_count": len(visible_characters),
        "portrait_mode": portrait_mode,
        "scene_count": scene_total,
        "shot_count": shot_total,
        "scenes": scene_outputs,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "generate_audio": generate_audio,
        "shot_duration": shot_seconds,
        # [需求6] 目标总时长（未走总时长口径时为 None）与实际规划的镜头总长。
        "total_duration": total_duration,
        "planned_total_duration": round(planned_seconds_total, 3),
    }
    _archive_tool_output_to_library(
        ctx=ctx,
        payload=payload,
        task_type=task_type,
        job_id=job_id,
        media="video",
        url=str(result["output_url"]),
        result=result,
        stem=_archive_stem_from_text(idea) or job_id,
    )
    return result


def run_freezone_video_idea(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_video_idea_async(envelope, ctx))


async def _run_freezone_video_erase_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.jobs import ensure_freezone_dirs, run_freezone_video_erase

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(ctx, "freezone_video_erase", job_id, 0.1, "开始视频擦除处理...")
    output_path, meta = await _call_freezone_leaf(
        envelope,
        run_freezone_video_erase,
        "run_freezone_video_erase",
        project_dir=project_dir,
        job_id=job_id,
        source_path=str(payload["source_path"]),
        mode=str(payload.get("mode") or "smart_subtitle"),
        # [aipdd fork delta] 工具页引擎三选一（local / mediakit / auto）；
        # 缺省 auto 与旧行为一致（有 Key 走 MediaKit）。
        engine=str(payload.get("engine") or "auto"),
        box_x=payload.get("box_x"),
        box_y=payload.get("box_y"),
        box_width=payload.get("box_width"),
        box_height=payload.get("box_height"),
    )
    rel = output_path.relative_to(project_dir).as_posix()
    result = {
        "job_id": job_id,
        "output_format": "mp4",
        "output_path": str(output_path),
        "output_url": make_static_url_for_context(ctx, rel),
        "meta": meta,
    }
    _archive_tool_output_to_library(
        ctx=ctx,
        payload=payload,
        task_type="freezone_video_erase",
        job_id=job_id,
        media="video",
        url=str(result["output_url"]),
        result=result,
        stem=Path(str(payload.get("source_path") or "")).stem,
    )
    return result


async def _run_freezone_video_upscale_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.jobs import (
        ensure_freezone_dirs,
        run_freezone_video_upscale,
    )

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(ctx, "freezone_video_upscale", job_id, 0.1, "开始视频高清处理...")
    output_path, meta = await _call_freezone_leaf(
        envelope,
        run_freezone_video_upscale,
        "run_freezone_video_upscale",
        project_dir=project_dir,
        job_id=job_id,
        source_path=str(payload["source_path"]),
        resolution=str(payload.get("resolution") or "1080p"),
        frame_interpolation=str(payload.get("frame_interpolation") or "none"),
        denoise_strength=str(payload.get("denoise_strength") or "1x"),
    )
    rel = output_path.relative_to(project_dir).as_posix()
    return {
        "job_id": job_id,
        "output_format": "mp4",
        "output_path": str(output_path),
        "output_url": make_static_url_for_context(ctx, rel),
        "meta": meta,
    }


async def _run_freezone_audio_separate_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.jobs import (
        ensure_freezone_dirs,
        run_freezone_audio_separate,
    )

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(ctx, "freezone_audio_separate", job_id, 0.1, "开始音视频分离...")
    outputs = await _call_freezone_leaf(
        envelope,
        run_freezone_audio_separate,
        "run_freezone_audio_separate",
        project_dir=project_dir,
        job_id=job_id,
        source_path=str(payload["source_path"]),
    )
    audio_path = outputs.get("audio_path")
    mute_video_path = outputs.get("mute_video_path")
    audio_rel = audio_path.relative_to(project_dir).as_posix() if audio_path else ""
    mute_rel = (
        mute_video_path.relative_to(project_dir).as_posix() if mute_video_path else ""
    )
    response = {
        "job_id": job_id,
        "audio_url": make_static_url_for_context(ctx, audio_rel) if audio_rel else None,
        "mute_video_url": (
            make_static_url_for_context(ctx, mute_rel) if mute_rel else None
        ),
    }
    target_episode = payload.get("target_episode")
    target_beat = payload.get("target_beat")
    if audio_path and target_episode and target_beat:
        response["pushable"] = True
        response["slot_target"] = {
            "kind": "beat_audio",
            "episode": int(target_episode),
            "beat": int(target_beat),
        }
    return response


async def _run_freezone_video_compose_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.jobs import (
        ensure_freezone_dirs,
        run_freezone_video_compose,
    )

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(ctx, "freezone_video_compose", job_id, 0.1, "开始合成视频时间线...")
    output_path = await _call_freezone_leaf(
        envelope,
        run_freezone_video_compose,
        "run_freezone_video_compose",
        project_dir=project_dir,
        job_id=job_id,
        title=str(payload.get("title") or ""),
        canvas_id=str(payload.get("canvas_id") or ""),
        resolution=str(payload.get("resolution") or "1080p"),
        fps=int(payload.get("fps") or 30),
        background_color=str(payload.get("background_color") or "#000000"),
        keep_original_audio=bool(payload.get("keep_original_audio", True)),
        tracks=list(payload.get("tracks") or []),
    )
    rel = output_path.relative_to(project_dir).as_posix()
    return {
        "job_id": job_id,
        "output_format": "mp4",
        "output_path": str(output_path),
        "output_url": make_static_url_for_context(ctx, rel),
    }


def run_freezone_video_erase(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_video_erase_async(envelope, ctx))


def run_freezone_video_upscale(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_video_upscale_async(envelope, ctx))


def run_freezone_audio_separate(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_audio_separate_async(envelope, ctx))


def run_freezone_video_compose(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_video_compose_async(envelope, ctx))


async def _run_freezone_text_translate_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.jobs import ensure_freezone_dirs
    from novelvideo.freezone.paths import outputs_dir
    from novelvideo.freezone.text_node import translate_freezone_text

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    node_type = str(payload.get("node_type") or "generic")
    _update(ctx, "freezone_text_translate", job_id, 0.1, "开始翻译文本...")
    translated_text, source_language, target_language = await _call_freezone_leaf(
        envelope,
        translate_freezone_text,
        "translate_freezone_text",
        text=str(payload.get("text") or ""),
        node_type=node_type,
    )
    data = {
        "translated_text": translated_text,
        "source_language": source_language,
        "target_language": target_language,
        "node_type": node_type,
    }
    out = outputs_dir(project_dir, "freezone_text_translate") / f"{job_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    import json

    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    rel = out.relative_to(project_dir).as_posix()
    result = {
        "job_id": job_id,
        "output_format": "json",
        "output_path": str(out),
        "output_url": make_static_url_for_context(ctx, rel),
        **data,
    }
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type="freezone_text_translate",
        job_id=job_id,
        media_type="text",
        node_type=node_type,
        input_preview=str(payload.get("text") or "")[:240],
        result=result,
    )
    if history_record:
        result["generation_history_record"] = history_record
    return result


async def _run_freezone_text_generate_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.jobs import ensure_freezone_dirs
    from novelvideo.freezone.paths import outputs_dir
    from novelvideo.freezone.text_node import generate_freezone_text

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    prompt = str(payload.get("prompt") or "").strip()
    _update(ctx, "freezone_text_generate", job_id, 0.1, "开始生成文本...")
    model, generated_text = await _call_freezone_leaf(
        envelope,
        generate_freezone_text,
        "generate_freezone_text",
        prompt=prompt,
    )
    data = {"generated_text": generated_text, "model": model}
    out = outputs_dir(project_dir, "freezone_text_generate") / f"{job_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    import json

    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    rel = out.relative_to(project_dir).as_posix()
    result = {
        "job_id": job_id,
        "output_format": "json",
        "output_path": str(out),
        "output_url": make_static_url_for_context(ctx, rel),
        **data,
    }
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type="freezone_text_generate",
        job_id=job_id,
        media_type="text",
        input_preview=prompt[:240],
        prompt=prompt,
        model=model,
        result=result,
    )
    if history_record:
        result["generation_history_record"] = history_record
    return result


async def _run_freezone_article_write_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    """[aipdd fork delta] 公众号文章编写与排版：写作风格 → Markdown → 合规 HTML → 门禁。

    出网只有一处——``generate_freezone_text``（EG-18a，走既有文本网关分发）；排版
    （``layout.render_article``）与合规校验（``validate.assert_compliant``）都是纯本地
    确定性代码，不出网。排版产物落一份 HTML（预览/发布共用），结果 JSON 里给 Markdown、
    静态 HTML URL、标题、风格/主题与校验 warning。
    """
    import json

    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.jobs import ensure_freezone_dirs
    from novelvideo.freezone.paths import outputs_dir
    from novelvideo.freezone.text_node import generate_freezone_text
    from novelvideo.gzh import layout, validate, write
    from novelvideo.gzh.themes import normalize_theme
    from novelvideo.gzh.writer_styles import normalize_style

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)

    topic = str(payload.get("topic") or "").strip()
    material = str(payload.get("material") or "").strip()
    style = normalize_style(payload.get("style"))
    theme = normalize_theme(payload.get("theme"))
    target_words = int(payload.get("target_words") or 1500)
    audience = str(payload.get("audience") or "").strip()
    tone = str(payload.get("tone") or "").strip()
    intro_quote = str(payload.get("intro_quote") or "").strip()
    author = str(payload.get("author") or "").strip()
    model_text = str(payload.get("model_text") or "").strip()

    _update(ctx, "freezone_article_write", job_id, 0.1, "开始撰写文章...")
    prompt = write.build_write_prompt(
        topic=topic,
        material=material,
        style=style,
        target_words=target_words,
        audience=audience,
        tone=tone,
    )
    model, raw_md = await _call_freezone_leaf(
        envelope,
        generate_freezone_text,
        "generate_freezone_text",
        prompt=prompt,
        **( {"model": model_text} if model_text else {} ),
    )
    markdown = write.normalize_markdown(raw_md)
    if not write.has_markdown_structure(markdown):
        raise ValueError("模型未产出可排版的 Markdown 结构（缺标题/分节）")

    _update(ctx, "freezone_article_write", job_id, 0.7, "正在排版与合规校验...")
    html = layout.render_article(
        markdown,
        theme=theme,
        intro_quote=intro_quote,
        author=author,
    )
    # 强门禁：ERROR 非空直接判任务失败（产物不合规不可交付）。
    result_validation = validate.assert_compliant(html, name=f"{job_id}.html")
    title = layout.infer_title(markdown, fallback=topic or "未命名文章")

    out_dir = outputs_dir(project_dir, "freezone_article_write")
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"{job_id}.html"
    html_path.write_text(html, encoding="utf-8")
    html_rel = html_path.relative_to(project_dir).as_posix()

    data = {
        "title": title,
        "style": style,
        "theme": theme,
        "model": model,
        "markdown": markdown,
        "html_url": make_static_url_for_context(ctx, html_rel),
        "span_leaf_count": result_validation.span_leaf_count,
        "validation_warnings": result_validation.warnings,
    }
    out = out_dir / f"{job_id}.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    result = {
        "job_id": job_id,
        "output_format": "json",
        "output_path": str(out),
        "html_path": str(html_path),
        **data,
    }
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type="freezone_article_write",
        job_id=job_id,
        media_type="text",
        input_preview=(topic or material)[:240],
        prompt=prompt,
        model=model,
        result=result,
    )
    if history_record:
        result["generation_history_record"] = history_record
    return result


async def _run_freezone_oral_script_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    """[aipdd fork delta] 短视频口播稿创作（工具栏首项）：处境 + 素材 → 能直接念的逐字稿。

    方法论蒸馏自开源技能包「太上老君·AI炼丹炉」（CC BY-NC 4.0）：炉心七条底层判断、
    开炉的第一句与勾证衔接、改稿六刀与去 AI 味都一次压进单条写作指令，没多轮对话可用
    时靠指令自证。

    出网只有一处——``generate_freezone_text``（EG-18a，走既有文本网关分发），与公众号
    文章任务同一口径；章节切分、净台词提取与去 AI 味预检（``freezone/oral_script``）全部
    是纯本地确定性代码。硬拦（假对比这类观众一眼能认出的句子）命中时自动追加**一轮**
    定向重写——原方法论就限定「最多一轮有依据的修改」，不做无限重写，也不顺便换选题。
    """
    import json

    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.jobs import ensure_freezone_dirs
    from novelvideo.freezone.oral_script import deai, prompts as oral_prompts
    from novelvideo.freezone.oral_script import script as oral_script
    from novelvideo.freezone.paths import outputs_dir
    from novelvideo.freezone.text_node import generate_freezone_text

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)

    topic = str(payload.get("topic") or "").strip()
    persona = str(payload.get("persona") or "").strip()
    material = str(payload.get("material") or "").strip()
    audience = str(payload.get("audience") or "").strip()
    scene = str(payload.get("scene") or "").strip()
    tone = str(payload.get("tone") or "").strip()
    formula = oral_prompts.normalize_formula(payload.get("formula"))
    duration_s = oral_prompts.clamp_duration(payload.get("duration_s"))
    title_count = oral_prompts.clamp_title_count(payload.get("title_count"))
    rewrite_hard_hits = bool(payload.get("rewrite_hard_hits", True))
    model_text = str(payload.get("model_text") or "").strip()

    _update(
        ctx,
        "freezone_oral_script",
        job_id,
        0.1,
        lmsg("tasks.progress.oralScript.start", "开始炼口播稿..."),
    )
    prompt = oral_prompts.build_oral_script_prompt(
        topic=topic,
        persona=persona,
        material=material,
        audience=audience,
        scene=scene,
        tone=tone,
        formula=formula,
        duration_s=duration_s,
        title_count=title_count,
    )
    model, raw = await _call_freezone_leaf(
        envelope,
        generate_freezone_text,
        "generate_freezone_text",
        prompt=prompt,
        **( {"model": model_text} if model_text else {} ),
    )
    parsed = oral_script.parse_oral_script(raw)

    rewrite_done = False
    rewrite_attempted = False
    rewrite_reason = ""
    # 结构缺整节（没拿到逐字稿）与假对比硬拦都只补一轮；重试后仍不干净就带着体检
    # 报告交付，不判任务失败——初稿对用户仍然可用，遮住反而丢东西。门禁与交付报告
    # 取同一份文本（正文 + 「为什么这么排」）：假对比最爱藏在金句收尾和写给用户的
    # 理由里，只扫正文会出现「报告说有硬伤、却一次都没重写」的自相矛盾产物。
    report = deai.lint_script(oral_script.delivered_text(parsed))
    if not parsed.structure_ok:
        rewrite_reason = "structure"
    elif not report.passed and rewrite_hard_hits:
        rewrite_reason = "ai_tell"
    if rewrite_reason:
        _update(
            ctx,
            "freezone_oral_script",
            job_id,
            0.55,
            lmsg(
                "tasks.progress.oralScript.rewriteHard",
                "本地检查发现硬伤，正在只针对这些句子重写一轮...",
            ),
        )
        # 回喂给模型的命中句与报告同源：只重写报告里点名的那些行。
        hits = (
            []
            if rewrite_reason == "structure"
            else [hit.to_payload() for hit in report.hard]
        )
        rewrite_prompt = oral_prompts.build_rewrite_prompt(
            raw, hits, reason=rewrite_reason
        )
        rewrite_attempted = True
        try:
            model, rewritten = await _call_freezone_leaf(
                envelope,
                generate_freezone_text,
                "generate_freezone_text",
                prompt=rewrite_prompt,
                **({"model": model_text} if model_text else {}),
            )
        except Exception as exc:  # noqa: BLE001 - 重写是可选优化，失败必须保住初稿
            # 首轮已经交出可用成稿：可选的那一轮抖动（网关 502 / 超时）不能把成稿
            # 一起判死，退回「初稿 + 体检报告」。
            logger.warning("freezone oral script rewrite round failed: %s", exc)
            rewritten = ""
        retry_parsed = (
            oral_script.parse_oral_script(rewritten) if rewritten.strip() else None
        )
        if retry_parsed is not None and retry_parsed.structure_ok:
            # 重试把稿子弄得更糟（丢了正文）时保留初稿，不能为了过检查交白卷。
            raw = rewritten
            parsed = retry_parsed
            rewrite_done = True
        if rewrite_done:
            report = deai.lint_script(oral_script.delivered_text(parsed))

    _update(
        ctx,
        "freezone_oral_script",
        job_id,
        0.85,
        lmsg("tasks.progress.oralScript.lint", "正在提取净台词与做去 AI 味检查..."),
    )
    spoken_chars = sum(
        1 for ch in parsed.spoken_text if "\u4e00" <= ch <= "\u9fff" or ch.isalnum()
    )

    out_dir = outputs_dir(project_dir, "freezone_oral_script")
    out_dir.mkdir(parents=True, exist_ok=True)
    # 净台词 txt：括号里的镜头提示不进嘴，这一份能直接进提词器或配音。
    txt_path = out_dir / f"{job_id}.txt"
    txt_path.write_text(parsed.spoken_text or parsed.script, encoding="utf-8")
    txt_rel = txt_path.relative_to(project_dir).as_posix()

    data = {
        "title": parsed.recommended_title,
        "formula": formula,
        "duration_s": duration_s,
        "title_count": title_count,
        "model": model,
        # 导出带稳定 id 的章节记录：前端按 id 取自己的词条，不把中文章节日名写进界面代码。
        "sections": oral_script.section_records(parsed.sections),
        "script": parsed.script,
        "spoken_text": parsed.spoken_text,
        "lines": parsed.lines,
        "stage_notes": parsed.stage_notes,
        "titles": parsed.titles,
        "first_line": parsed.first_line,
        "word_count": spoken_chars,
        "estimated_seconds": oral_prompts.estimate_speech_seconds(parsed.spoken_text),
        "txt_url": make_static_url_for_context(ctx, txt_rel),
        "rewritten": rewrite_done,
        "rewrite_attempted": rewrite_attempted,
        "rewrite_reason": rewrite_reason,
        "ai_tell": report.to_payload(),
    }
    out = out_dir / f"{job_id}.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    result = {
        "job_id": job_id,
        "output_format": "json",
        "output_path": str(out),
        "txt_path": str(txt_path),
        **data,
    }
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type="freezone_oral_script",
        job_id=job_id,
        media_type="text",
        input_preview=(topic or material)[:240],
        prompt=prompt,
        model=model,
        result=result,
    )
    if history_record:
        result["generation_history_record"] = history_record
    return result


async def _run_freezone_story_script_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.jobs import (
        ensure_freezone_dirs,
        run_freezone_extract_frames,
    )
    from novelvideo.freezone.paths import outputs_dir
    from novelvideo.freezone.text_node import (
        bind_story_script_assets,
        generate_freezone_story_script,
        generate_freezone_story_script_with_vision,
    )

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)

    source_text = str(payload.get("source_text") or "")
    prompt = str(payload.get("prompt") or "")
    character_refs = list(payload.get("character_refs") or [])
    character_image_paths = [
        str(path) for path in payload.get("character_image_paths") or []
    ]
    video_path = str(payload.get("video_path") or "")
    duration_sec = payload.get("duration_sec")

    frame_paths: list[Path] = []
    frame_urls: list[str] = []
    if video_path:
        _update(ctx, "freezone_story_script", job_id, 0.1, "ffmpeg 抽取关键帧...")
        frame_paths = await _call_freezone_leaf(
            envelope,
            run_freezone_extract_frames,
            "run_freezone_extract_frames",
            project_dir=project_dir,
            job_id=job_id,
            video_path=Path(video_path),
            max_frames=int(payload.get("max_frames") or 20),
            scene_threshold=float(payload.get("scene_threshold") or 0.3),
        )
        frame_urls = [
            make_static_url_for_context(ctx, path.relative_to(project_dir).as_posix())
            for path in frame_paths
        ]

    if frame_paths or character_image_paths:
        _update(
            ctx,
            "freezone_story_script",
            job_id,
            0.55,
            (
                f"视觉模型解析 {len(frame_paths)} 帧为分镜脚本..."
                if frame_paths
                else "视觉模型读取角色参考图生成故事脚本..."
            ),
        )
        data = await _call_freezone_leaf(
            envelope,
            generate_freezone_story_script_with_vision,
            "generate_freezone_story_script_with_vision",
            frame_paths=[str(path) for path in frame_paths],
            character_image_paths=character_image_paths,
            source_text=source_text,
            prompt=prompt,
            duration_sec=float(duration_sec) if duration_sec else None,
            character_refs=character_refs,
        )
    else:
        _update(ctx, "freezone_story_script", job_id, 0.1, "开始生成故事脚本...")
        data = await _call_freezone_leaf(
            envelope,
            generate_freezone_story_script,
            "generate_freezone_story_script",
            source_text=source_text,
            prompt=prompt,
            model=str(payload.get("model") or ""),
            character_refs=character_refs,
        )

    bind_story_script_assets(data, frame_urls=frame_urls, character_refs=character_refs)
    out = outputs_dir(project_dir, "freezone_story_script") / f"{job_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    import json

    payload_data = data.model_dump()
    out.write_text(
        json.dumps(payload_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    rel = out.relative_to(project_dir).as_posix()
    result = {
        "job_id": job_id,
        "output_format": "json",
        "output_path": str(out),
        "output_url": make_static_url_for_context(ctx, rel),
        **payload_data,
    }
    if frame_urls:
        result["frame_urls"] = frame_urls
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type="freezone_story_script",
        job_id=job_id,
        media_type="text",
        model=str(payload.get("model") or ""),
        source_text_preview=str(payload.get("source_text") or "")[:240],
        row_count=len(payload_data.get("rows") or []),
        result=result,
    )
    if history_record:
        result["generation_history_record"] = history_record
    return result


async def _run_freezone_image_reverse_prompt_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.image_node import reverse_prompt_from_image
    from novelvideo.freezone.jobs import ensure_freezone_dirs
    from novelvideo.freezone.paths import outputs_dir

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    source_path = Path(str(payload["source_path"]))
    _update(ctx, "freezone_image_reverse_prompt", job_id, 0.1, "开始反推图片提示词...")
    prompt = await _call_freezone_leaf(
        envelope,
        reverse_prompt_from_image,
        "reverse_prompt_from_image",
        image_path=source_path,
        instruction=str(payload.get("instruction") or ""),
    )
    out = outputs_dir(project_dir, "freezone_image_reverse_prompt") / f"{job_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    import json

    out.write_text(
        json.dumps({"prompt": prompt}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    rel = out.relative_to(project_dir).as_posix()
    result = {
        "job_id": job_id,
        "output_format": "json",
        "output_path": str(out),
        "output_url": make_static_url_for_context(ctx, rel),
        "prompt": prompt,
    }
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type="freezone_image_reverse_prompt",
        job_id=job_id,
        media_type="text",
        source_path=str(source_path),
        result=result,
    )
    if history_record:
        result["generation_history_record"] = history_record
    return result


def run_freezone_text_translate(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_text_translate_async(envelope, ctx))


def run_freezone_text_generate(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_text_generate_async(envelope, ctx))


def run_freezone_story_script(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_story_script_async(envelope, ctx))


def run_freezone_article_write(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_article_write_async(envelope, ctx))


def run_freezone_oral_script(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_oral_script_async(envelope, ctx))


def run_freezone_image_reverse_prompt(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    return _run_cancellable(
        envelope, _run_freezone_image_reverse_prompt_async(envelope, ctx)
    )


async def _run_freezone_audio_speech_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import (
        make_sqlite_store_for_context,
        make_static_url_for_context,
    )
    from novelvideo.freezone.audio_node import generate_freezone_audio_speech
    from novelvideo.freezone.jobs import ensure_freezone_dirs

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(ctx, "freezone_audio_speech", job_id, 0.1, "开始文本生成语音...")
    # 音色所需的项目态已在投递时定型放进 payload 时，这里不再回头开项目 SQLite
    # —— 那一步经 `api/deps.py` 的 `require_project_home_node` 把本任务钉在
    # 存放该项目的那台机器上。payload 里没有投射时行为逐字不变。
    projection = read_projection(payload)
    store = None if projection is not None else await make_sqlite_store_for_context(ctx)
    try:
        result = await _call_freezone_leaf(
            envelope,
            generate_freezone_audio_speech,
            "generate_freezone_audio_speech",
            store=store,
            username=ctx.owner_username,
            project=ctx.project_name,
            account_voice_username=str(
                payload.get("account_voice_username")
                or ctx.requester_username
                or ctx.owner_username
            ),
            project_dir=project_dir,
            job_id=job_id,
            text=str(payload.get("text") or ""),
            emotion_prompt=str(payload.get("emotion_prompt") or ""),
            voice_ref=payload.get("voice_ref"),
            projection=projection,
            # [aipdd fork delta] 画布音频节点选中的音频模型（提交前已按目录校验）。
            model=str(payload.get("model") or ""),
        )
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()
    rel = result.audio_path.relative_to(project_dir).as_posix()
    audio_url = make_static_url_for_context(ctx, rel)
    response = {
        "job_id": job_id,
        "url": audio_url,
        "audio_url": audio_url,
        "audio_size": result.audio_path.stat().st_size,
        "duration_ms": result.duration_ms,
        "mime_type": result.mime_type,
        "model": result.model,
        "voice_source": result.voice_source,
        "voice_sha256": result.voice_sha256,
    }
    target_episode = payload.get("target_episode")
    target_beat = payload.get("target_beat")
    if target_episode and target_beat:
        response["pushable"] = True
        response["slot_target"] = {
            "kind": "beat_audio",
            "episode": int(target_episode),
            "beat": int(target_beat),
        }
    _archive_tool_output_to_library(
        ctx=ctx,
        payload=payload,
        task_type="freezone_audio_speech",
        job_id=job_id,
        media="audio",
        url=str(response["audio_url"]),
        result=response,
        stem=_archive_stem_from_text(payload.get("text")),
    )
    return response


def run_freezone_audio_speech(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(envelope, _run_freezone_audio_speech_async(envelope, ctx))


async def _run_freezone_voice_replace_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    """[aipdd fork delta] 替换人声 runner：分离→ASR→零样本合成新声→搬运产物。

    与 ``freezone_audio_speech`` 同族但无投射可用（多一步 ASR + 解封装），这里
    直接按 home-node 绑定建项目 SQLite store 供零样本合成解析 narration_style；
    产物纯音频落 ``_outputs/freezone_voice_replace``，可选归档进资产库。
    """
    from novelvideo.api.deps import (
        make_sqlite_store_for_context,
        make_static_url_for_context,
    )
    from novelvideo.freezone.jobs import ensure_freezone_dirs
    from novelvideo.freezone.voice_replace import run_freezone_voice_replace

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(ctx, "freezone_voice_replace", job_id, 0.1, "开始替换人声...")
    store = await make_sqlite_store_for_context(ctx)
    try:
        result = await _call_freezone_leaf(
            envelope,
            run_freezone_voice_replace,
            "run_freezone_voice_replace",
            store=store,
            username=ctx.owner_username,
            project=ctx.project_name,
            account_voice_username=str(
                payload.get("account_voice_username")
                or ctx.requester_username
                or ctx.owner_username
            ),
            project_dir=project_dir,
            job_id=job_id,
            source_path=str(payload.get("source_path") or ""),
            source_kind=str(payload.get("source_kind") or "audio"),
            voice_ref=payload.get("voice_ref"),
            asr_model=str(payload.get("asr_model") or ""),
            model=str(payload.get("model") or ""),
        )
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()
    audio_path = Path(str(result["audio_path"]))
    rel = audio_path.relative_to(project_dir).as_posix()
    audio_url = make_static_url_for_context(ctx, rel, local_path=audio_path)
    _update(ctx, "freezone_voice_replace", job_id, 0.9, "新声合成完成...")
    response: dict[str, Any] = {
        "job_id": job_id,
        "url": audio_url,
        "audio_url": audio_url,
        "audio_size": audio_path.stat().st_size,
        "duration_ms": result.get("duration_ms"),
        "mime_type": result.get("mime_type"),
        "model": result.get("model"),
        "text": result.get("text"),
        # 实际生效的 ASR 候选展示名（未指定 / 指定不可用时会被自动退回，产物里如实回报）。
        "asr_source": result.get("asr_source"),
        "voice_source": result.get("voice_source"),
        "voice_sha256": result.get("voice_sha256"),
    }
    target_episode = payload.get("target_episode")
    target_beat = payload.get("target_beat")
    if target_episode and target_beat:
        response["pushable"] = True
        response["slot_target"] = {
            "kind": "beat_audio",
            "episode": int(target_episode),
            "beat": int(target_beat),
        }
    _archive_tool_output_to_library(
        ctx=ctx,
        payload=payload,
        task_type="freezone_voice_replace",
        job_id=job_id,
        media="audio",
        url=str(response["audio_url"]),
        result=response,
        stem=_archive_stem_from_text(result.get("text")),
    )
    return response


def run_freezone_voice_replace(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(
        envelope, _run_freezone_voice_replace_async(envelope, ctx)
    )


async def _run_freezone_video_dubbing_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    """[aipdd fork delta] 视频配音 runner：解封装→ASR→零样本合成→音轨回填。

    与 ``freezone_voice_replace`` 同族但产物是视频（回填配音音轨后替换原视频整条
    音轨）；同样无投射可用（多一步 ASR + 解封装），这里直接按 home-node 绑定
    建项目 SQLite store 供零样本合成解析 narration_style；成片落
    ``_outputs/freezone_video_dubbing``，可选归档进资产库。
    """
    from novelvideo.api.deps import (
        make_sqlite_store_for_context,
        make_static_url_for_context,
    )
    from novelvideo.freezone.dubbing import run_freezone_video_dubbing
    from novelvideo.freezone.jobs import ensure_freezone_dirs

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(
        ctx,
        "freezone_video_dubbing",
        job_id,
        0.1,
        lmsg("tasks.progress.videoDubbing.start", "开始视频配音..."),
    )
    store = await make_sqlite_store_for_context(ctx)
    try:
        result = await _call_freezone_leaf(
            envelope,
            run_freezone_video_dubbing,
            "run_freezone_video_dubbing",
            store=store,
            username=ctx.owner_username,
            project=ctx.project_name,
            account_voice_username=str(
                payload.get("account_voice_username")
                or ctx.requester_username
                or ctx.owner_username
            ),
            project_dir=project_dir,
            job_id=job_id,
            source_path=str(payload.get("source_path") or ""),
            source_kind=str(payload.get("source_kind") or "video"),
            voice_ref=payload.get("voice_ref"),
            asr_model=str(payload.get("asr_model") or ""),
            language=str(payload.get("language") or ""),
            model=str(payload.get("model") or ""),
        )
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()
    video_path = Path(str(result["video_path"]))
    rel = video_path.relative_to(project_dir).as_posix()
    video_url = make_static_url_for_context(ctx, rel, local_path=video_path)
    _update(
        ctx,
        "freezone_video_dubbing",
        job_id,
        0.9,
        lmsg("tasks.progress.videoDubbing.done", "配音成片完成..."),
    )
    response: dict[str, Any] = {
        "job_id": job_id,
        "url": video_url,
        "video_url": video_url,
        "video_size": video_path.stat().st_size,
        "duration_ms": result.get("duration_ms"),
        "mime_type": result.get("mime_type"),
        "model": result.get("model"),
        "text": result.get("text"),
        # 实际生效的 ASR 候选展示名（未指定 / 指定不可用时会被自动退回，产物里如实回报）。
        "asr_source": result.get("asr_source"),
        "voice_source": result.get("voice_source"),
        "voice_sha256": result.get("voice_sha256"),
    }
    target_episode = payload.get("target_episode")
    target_beat = payload.get("target_beat")
    if target_episode and target_beat:
        response["pushable"] = True
        response["slot_target"] = {
            "kind": "beat_audio",
            "episode": int(target_episode),
            "beat": int(target_beat),
        }
    _archive_tool_output_to_library(
        ctx=ctx,
        payload=payload,
        task_type="freezone_video_dubbing",
        job_id=job_id,
        media="video",
        url=str(response["video_url"]),
        result=response,
        stem=_archive_stem_from_text(result.get("text")),
    )
    return response


def run_freezone_video_dubbing(
    envelope: dict[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    return _run_cancellable(
        envelope, _run_freezone_video_dubbing_async(envelope, ctx)
    )


async def _run_freezone_audio_eleven_music_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.audio_node import generate_freezone_audio_eleven_music
    from novelvideo.freezone.jobs import ensure_freezone_dirs

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(
        ctx,
        "freezone_audio_eleven_music",
        job_id,
        0.1,
        lmsg("tasks.progress.elevenMusic.start", "开始文本生成音乐..."),
    )
    result = await _call_freezone_leaf(
        envelope,
        generate_freezone_audio_eleven_music,
        "generate_freezone_audio_eleven_music",
        project_dir=project_dir,
        job_id=job_id,
        prompt=str(payload.get("input") or ""),
        # 留空 = 交 callee 解析默认音频位（代码里不再内置厂商名）。
        model=str(payload.get("model") or ""),
        response_format=str(payload.get("response_format") or "mp3"),
        music_length_ms=int(payload.get("music_length_ms") or 30_000),
        force_instrumental=bool(payload.get("force_instrumental", True)),
        respect_sections_durations=bool(
            payload.get("respect_sections_durations", True)
        ),
        output_format=str(payload.get("output_format") or "mp3_44100_128"),
    )
    rel = result.audio_path.relative_to(project_dir).as_posix()
    audio_url = make_static_url_for_context(ctx, rel)
    return {
        "job_id": job_id,
        "url": audio_url,
        "audio_url": audio_url,
        "audio_size": result.audio_path.stat().st_size,
        "duration_ms": result.duration_ms,
        "mime_type": result.mime_type,
        "model": result.model,
    }


def run_freezone_audio_eleven_music(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    return _run_cancellable(
        envelope, _run_freezone_audio_eleven_music_async(envelope, ctx)
    )

async def _run_freezone_music_generate_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    """[aipdd fork delta req46] 音乐创作 runner：MiniMax Music 3.0 直连出产物。

    与画布音频节点的 ``freezone_audio_eleven_music``（经网关）不是一条链路：本任务
    不经网关，直连用设置里配置的 MiniMax 渠道凭据，组织任务在叶内 fail-closed
    （``deny_organization_egress``）。runner 只管把落盘产物转静态 URL 与按需归档。
    """
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.jobs import ensure_freezone_dirs
    from novelvideo.freezone.minimax_music import run_freezone_music_generate

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(
        ctx,
        "freezone_music_generate",
        job_id,
        0.1,
        lmsg("tasks.progress.music.start", "开始生成音乐..."),
    )
    audio_path_value = str(payload.get("audio_path") or "").strip()
    result = await _call_freezone_leaf(
        envelope,
        run_freezone_music_generate,
        "run_freezone_music_generate",
        project_dir=project_dir,
        job_id=job_id,
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
        audio_format=str(payload.get("audio_format") or ""),
    )
    rel = result.audio_path.relative_to(project_dir).as_posix()
    audio_url = make_static_url_for_context(ctx, rel, local_path=result.audio_path)
    _update(
        ctx,
        "freezone_music_generate",
        job_id,
        0.9,
        lmsg("tasks.progress.music.done", "音乐生成完成..."),
    )
    response: dict[str, Any] = {
        "job_id": job_id,
        "url": audio_url,
        "audio_url": audio_url,
        "audio_size": result.size_bytes,
        "duration_ms": result.duration_ms,
        "mime_type": result.mime_type,
        "model": result.model,
        "provider": result.provider,
        "mode": result.mode,
        "lyrics": result.lyrics,
    }
    _archive_tool_output_to_library(
        ctx=ctx,
        payload=payload,
        task_type="freezone_music_generate",
        job_id=job_id,
        media="audio",
        url=audio_url,
        result=response,
        stem=str(payload.get("source_name") or ""),
    )
    return response


def run_freezone_music_generate(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    return _run_cancellable(
        envelope, _run_freezone_music_generate_async(envelope, ctx)
    )


async def _run_freezone_douyin_copywrite_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.douyin_copywrite import run_freezone_douyin_copywrite
    from novelvideo.freezone.jobs import ensure_freezone_dirs

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(ctx, "freezone_douyin_copywrite", job_id, 0.1, "开始解析抖音视频...")
    result = await _call_freezone_leaf(
        envelope,
        run_freezone_douyin_copywrite,
        "run_freezone_douyin_copywrite",
        project_dir=project_dir,
        job_id=job_id,
        video=str(payload.get("video") or ""),
        model=str(payload.get("model") or ""),
    )
    rel = result.text_path.relative_to(project_dir).as_posix()
    text_url = make_static_url_for_context(ctx, rel)
    # [aipdd fork delta] 产物是文案文本（txt）而非图/视频/音频媒体：素材库三类
    # 媒介无文本载体，故本工具不走 _archive_tool_output_to_library 归档；文案
    # 全文随任务结果返回，前端任务记录可直接读取与打开 txt。
    return {
        "job_id": job_id,
        "url": text_url,
        "text_url": text_url,
        "text": result.text,
        "char_count": len(result.text),
        "source": result.source,
        "model": result.model,
        "video_id": result.video_id,
        "title": result.title,
        "author": result.author,
        "duration_seconds": result.duration_seconds,
    }


def run_freezone_douyin_copywrite(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    return _run_cancellable(
        envelope, _run_freezone_douyin_copywrite_async(envelope, ctx)
    )


async def _run_freezone_media_copywrite_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.media_copywrite import run_freezone_media_copywrite
    from novelvideo.freezone.jobs import ensure_freezone_dirs
    from novelvideo.freezone.video_node import sync_mainline_assets_into_library

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)
    _update(ctx, "freezone_media_copywrite", job_id, 0.1, "开始下载/解析媒体…")

    # [aipdd fork delta] 把执行叶内部的每一步（yt-dlp / 回退 CDP / 拆音频 / ASR /
    # 写盘）回写到任务进度与日志。之前只有这里两句，中间过程与 yt-dlp 失败原因
    # 全落在进程 logger 里，前端任务详情看不到。
    def _report(progress: float, message: str) -> None:
        _update(ctx, "freezone_media_copywrite", job_id, progress, message)

    result = await _call_freezone_leaf(
        envelope,
        run_freezone_media_copywrite,
        "run_freezone_media_copywrite",
        project_dir=project_dir,
        job_id=job_id,
        source_type=str(payload.get("source_type") or ""),
        url=str(payload.get("url") or ""),
        platform=str(payload.get("platform") or ""),
        model=str(payload.get("model") or ""),
        on_status=_report,
    )
    rel = result.text_path.relative_to(project_dir).as_posix()
    text_url = make_static_url_for_context(ctx, rel)

    # [aipdd fork delta] req21：下载/上传视频与分离音频自动归档进资产库（稳定 id
    # 幂等 upsert）；纯音频来源无视频、下载失败无音频时相应跳过。
    _update(ctx, "freezone_media_copywrite", job_id, 0.9, "转写完成，归档素材…")
    assets: list[dict[str, Any]] = []
    display_name = (result.title or "提取素材")[:24]
    if result.video_path is not None and result.video_path.exists():
        video_rel = result.video_path.relative_to(project_dir).as_posix()
        assets.append(
            {
                "id": f"media_copywrite:{job_id}:video",
                "name": f"{display_name} · 视频",
                "media": "video",
                "source": "upload",
                "url": make_static_url_for_context(ctx, video_rel, local_path=result.video_path),
            }
        )
    if result.audio_path is not None and result.audio_path.exists():
        audio_rel = result.audio_path.relative_to(project_dir).as_posix()
        assets.append(
            {
                "id": f"media_copywrite:{job_id}:audio",
                "name": f"{display_name} · 音频",
                "media": "audio",
                "source": "upload",
                "url": make_static_url_for_context(ctx, audio_rel, local_path=result.audio_path),
            }
        )
    if assets:
        sync_mainline_assets_into_library(project_dir, assets=assets)

    return {
        "job_id": job_id,
        "url": text_url,
        "text_url": text_url,
        "text": result.text,
        "char_count": len(result.text),
        "source": result.source,
        "model": result.model,
        "video_id": result.video_id,
        "title": result.title,
        "author": result.author,
        "duration_seconds": result.duration_seconds,
        "archived_assets": len(assets),
    }


def run_freezone_media_copywrite(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    return _run_cancellable(
        envelope, _run_freezone_media_copywrite_async(envelope, ctx)
    )


# [aipdd fork delta] 「视频反推剧本」工具：抽帧 → 视觉模型（用户可选）→ 纯文本
# 剧本。产物写 outputs/freezone_video_script_reverse/{job}.txt，绝不覆盖
# freezone_story_script 的结构化 JSON 故事脚本。
_VIDEO_SCRIPT_REVERSE_TASK_TYPE = "freezone_video_script_reverse"


def _render_video_script_reverse_text(data: Any) -> str:
    """把结构化分镜行渲染成纯文本剧本（供下载/复制，不进脚本表 JSON）。"""
    title = str(getattr(data, "title", "") or "").strip()
    lines: list[str] = [f"# {title}" if title else "# 分镜剧本", ""]
    for row in getattr(data, "rows", []) or []:
        lines.append(
            f"【镜头 {getattr(row, 'shot_no', '')}】"
            f"时长 {getattr(row, 'duration', '')} 秒"
            + (f"｜景别：{row.shot}" if getattr(row, "shot", "") else "")
        )
        labeled = (
            ("画面", getattr(row, "visual_description", "")),
            ("角色1", " ".join(
                part for part in (
                    getattr(row, "character_1", ""),
                    getattr(row, "character_description_1", ""),
                ) if part
            )),
            ("角色2", " ".join(
                part for part in (
                    getattr(row, "character_2", ""),
                    getattr(row, "character_description_2", ""),
                ) if part
            )),
            ("动作", getattr(row, "character_action", "")),
            ("情绪", getattr(row, "emotion", "")),
            ("场景标签", getattr(row, "scene_tags", "")),
            ("光影氛围", getattr(row, "lighting_mood", "")),
            ("音效", getattr(row, "sound", "")),
            ("对白", getattr(row, "dialogue", "")),
            ("分镜提示词", getattr(row, "shot_prompt", "")),
            ("视频运动提示词", getattr(row, "video_motion_prompt", "")),
        )
        for label, value in labeled:
            clean = str(value or "").strip()
            if clean:
                lines.append(f"{label}：{clean}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


async def _run_freezone_video_script_reverse_async(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.jobs import (
        ensure_freezone_dirs,
        run_freezone_extract_frames,
    )
    from novelvideo.freezone.paths import outputs_dir
    from novelvideo.freezone.text_node import (
        generate_freezone_story_script_with_vision,
    )

    payload = envelope.get("payload") or {}
    job_id = str(payload["job_id"])
    project_dir = Path(str(payload.get("project_dir") or ctx.output_dir))
    ensure_freezone_dirs(project_dir)

    video_path = str(payload.get("video_path") or "")
    if not video_path:
        raise ValueError(f"{_VIDEO_SCRIPT_REVERSE_TASK_TYPE} requires video_path")
    prompt = str(payload.get("prompt") or "")
    duration_sec = payload.get("duration_sec")

    _update(ctx, _VIDEO_SCRIPT_REVERSE_TASK_TYPE, job_id, 0.1, "ffmpeg 抽取关键帧...")
    frame_paths = await _call_freezone_leaf(
        envelope,
        run_freezone_extract_frames,
        "run_freezone_extract_frames",
        project_dir=project_dir,
        job_id=job_id,
        video_path=Path(video_path),
        max_frames=int(payload.get("max_frames") or 20),
        scene_threshold=float(payload.get("scene_threshold") or 0.3),
    )
    if not frame_paths:
        raise ValueError("未能从视频抽取到关键帧，请确认视频文件可被 ffmpeg 读取")

    _update(
        ctx,
        _VIDEO_SCRIPT_REVERSE_TASK_TYPE,
        job_id,
        0.55,
        f"视觉模型反推剧本（{len(frame_paths)} 帧）...",
    )
    data = await _call_freezone_leaf(
        envelope,
        generate_freezone_story_script_with_vision,
        "generate_freezone_story_script_with_vision",
        frame_paths=[str(path) for path in frame_paths],
        prompt=prompt,
        duration_sec=float(duration_sec) if duration_sec else None,
        # [aipdd fork delta] 用户显式选定的视觉模型；留空走 env 默认链。
        model=str(payload.get("model") or "") or None,
    )

    text = _render_video_script_reverse_text(data)
    out = (
        outputs_dir(project_dir, _VIDEO_SCRIPT_REVERSE_TASK_TYPE)
        / f"{job_id}.txt"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    rel = out.relative_to(project_dir).as_posix()
    result = {
        "job_id": job_id,
        "output_format": "txt",
        "output_path": str(out),
        "output_url": make_static_url_for_context(ctx, rel),
        "title": str(getattr(data, "title", "") or ""),
        "text": text,
        "row_count": len(getattr(data, "rows", []) or []),
    }
    history_record = _append_node_history(
        ctx=ctx,
        project_dir=project_dir,
        payload=payload,
        task_type=_VIDEO_SCRIPT_REVERSE_TASK_TYPE,
        job_id=job_id,
        media_type="text",
        model=str(payload.get("model") or ""),
        row_count=result["row_count"],
        result=result,
    )
    if history_record:
        result["generation_history_record"] = history_record
    return result


def run_freezone_video_script_reverse(
    envelope: dict[str, Any],
    ctx: ProjectContext,
) -> dict[str, Any]:
    return _run_cancellable(
        envelope, _run_freezone_video_script_reverse_async(envelope, ctx)
    )


register_project_task_runner("freezone_gen", run_freezone_gen, requires_home_node=False)
register_project_task_runner("freezone_edit", run_freezone_edit, requires_home_node=False)
register_project_task_runner(
    "mainline_sketch_from_context",
    run_mainline_sketch_from_context,
    requires_home_node=False,
)
register_project_task_runner(
    "mainline_frame_from_context",
    run_mainline_frame_from_context,
    requires_home_node=False,
)
register_project_task_runner(
    "mainline_director_control_sketch",
    run_mainline_director_control_sketch,
    requires_home_node=False,
)
register_project_task_runner(
    "freezone_mask_edit", run_freezone_mask_edit, requires_home_node=False
)
register_project_task_runner(
    "freezone_extract", run_freezone_extract, requires_home_node=False
)
register_project_task_runner(
    "freezone_analyze", run_freezone_analyze, requires_home_node=False
)
register_project_task_runner(
    "freezone_video_story", run_freezone_video_story, requires_home_node=False
)
register_project_task_runner(
    "freezone_video_clone", run_freezone_video_clone, requires_home_node=False
)
register_project_task_runner(
    "freezone_video_mix", run_freezone_video_mix, requires_home_node=False
)
# [aipdd fork delta] 白板动画视频：渲染/叠加/编码纯本地，三类生成叶经既有出网分发。
register_project_task_runner(
    "freezone_whiteboard_animation",
    run_freezone_whiteboard_animation,
    requires_home_node=False,
)
# [aipdd fork delta] 科普讲解视频：画面逐帧本地绘制，text×2 + audio×1 经既有出网分发。
register_project_task_runner(
    "freezone_explainer_video",
    run_freezone_explainer_video,
    requires_home_node=False,
)
# [aipdd fork delta] 动效大片渲染：随包 Chrome 逐帧截图 + 随包 ffmpeg 编码，纯本地，
# 一个生成 leaf 都不调（页面是上一步文本节点产出的）。
register_project_task_runner(
    "freezone_motion_render",
    run_freezone_motion_render,
    requires_home_node=False,
)
# [aipdd fork delta] 动效大片拍点预检：一页 MG 脚本 → 每个拍点一张静帧（渲染契约第 4 条）。
# 与渲染同一个 Chrome 截图通道，但不编码不配乐，所以随包的 ffmpeg 完全用不上；页面若需要
# 现生成，则与渲染任务共用同一个文本 leaf 出网口径。
register_project_task_runner(
    "freezone_motion_stills",
    run_freezone_motion_stills,
    requires_home_node=False,
)
# [aipdd fork delta] 动效大片交付：把逐镜 MG 成片（+旁白/BGM）落成剪映工程目录。纯本地
# 文件读写，一个 leaf 都不出，也不依赖 VectCut 服务或任何第三方草稿库——桌面包形态下
# 这条是唯一的交付路径。
register_project_task_runner(
    "freezone_motion_draft",
    run_freezone_motion_draft,
    requires_home_node=False,
)
# [aipdd fork delta] Phase 2B 渠道短视频：复用讲解视频渲染链（text×2 + audio×1 + 本地
# 渲染/混音/封面抽帧），渠道只改变画幅/时长/字幕/文案，出网口径与讲解视频逐字相同。
register_project_task_runner(
    "freezone_channel_short_video",
    run_freezone_channel_short_video,
    requires_home_node=False,
)
register_project_task_runner(
    "freezone_video_idea", run_freezone_video_idea, requires_home_node=False
)
register_project_task_runner(
    "freezone_video_erase", run_freezone_video_erase, requires_home_node=False
)
register_project_task_runner(
    "freezone_video_upscale", run_freezone_video_upscale, requires_home_node=False
)
register_project_task_runner(
    "freezone_audio_separate", run_freezone_audio_separate, requires_home_node=False
)
register_project_task_runner(
    "freezone_video_compose", run_freezone_video_compose, requires_home_node=False
)
register_project_task_runner(
    "freezone_text_translate", run_freezone_text_translate, requires_home_node=False
)
register_project_task_runner(
    "freezone_text_generate", run_freezone_text_generate, requires_home_node=False
)
register_project_task_runner(
    "freezone_story_script", run_freezone_story_script, requires_home_node=False
)
# [aipdd fork delta] 公众号文章编写与排版：出网只有文本叶（EG-18a），排版/校验纯本地。
register_project_task_runner(
    "freezone_article_write", run_freezone_article_write, requires_home_node=False
)
# [aipdd fork delta] 短视频口播稿创作（工具栏首项）：出网只有文本叶（EG-18a），
# 章节切分 / 净台词提取 / 去 AI 味检查全部本地，与公众号文章同一口径。
register_project_task_runner(
    "freezone_oral_script", run_freezone_oral_script, requires_home_node=False
)
# [aipdd fork delta] 讲解类视频台词草稿（两段式第一段）：出网只有文本叶，不配音不渲染，
# 所以它在 default 队列上跑，不与成片抢渲染资源。
register_project_task_runner(
    "freezone_explainer_script", run_freezone_explainer_script, requires_home_node=False
)
register_project_task_runner(
    "freezone_image_reverse_prompt",
    run_freezone_image_reverse_prompt,
    requires_home_node=False,
)
register_project_task_runner(
    "freezone_audio_speech",
    run_freezone_audio_speech,
    requires_home_node=False,
)
register_project_task_runner(
    "freezone_audio_eleven_music",
    run_freezone_audio_eleven_music,
    requires_home_node=False,
)
# [aipdd fork delta req46] 工具页「音乐创作」（MiniMax Music 3.0 直连）。
register_project_task_runner(
    "freezone_music_generate",
    run_freezone_music_generate,
    requires_home_node=False,
)
register_project_task_runner(
    "freezone_douyin_copywrite",
    run_freezone_douyin_copywrite,
    requires_home_node=False,
)
register_project_task_runner(
    "freezone_media_copywrite",
    run_freezone_media_copywrite,
    requires_home_node=False,
)
# [aipdd fork delta] 视频反推剧本（工具页新增，产物 txt 不覆盖故事脚本 JSON）。
register_project_task_runner(
    "freezone_video_script_reverse",
    run_freezone_video_script_reverse,
    requires_home_node=False,
)
# [aipdd fork delta] 替换人声（工具页「人声复刻 → 替换人声」）。runner 无投射
# 可用、需建项目 SQLite store 解析 narration_style，故保持默认的 home-node
# 绑定（不入 PLACEMENT_FREE_TASKS），由投递方把任务路由到存放项目的那台机器。
register_project_task_runner("freezone_voice_replace", run_freezone_voice_replace)
# [aipdd fork delta] 视频配音（工具页新增）。与 voice_replace 同族：runner 无投射
# 可用、需建项目 SQLite store 解析 narration_style，故保持默认 home-node 绑定
# （不入 PLACEMENT_FREE_TASKS），由投递方把任务路由到存放项目的那台机器。
register_project_task_runner("freezone_video_dubbing", run_freezone_video_dubbing)
# [aipdd fork delta] 口播智能剪辑（工具页新增）。静音/瑕疵剪 + 分镜/B-roll/BGM 判定
# 走分发器；本地裁接/混音/封面/草稿组装直调。与 channel/explainer 一致：requires_home_node
# =False（草稿落盘由 VectCut 服务侧完成）。
register_project_task_runner(
    "freezone_narration_smart_edit",
    run_freezone_narration_smart_edit,
    requires_home_node=False,
)
