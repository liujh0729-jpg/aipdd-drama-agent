# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 视频配音：工具页「视频配音」的编排执行叶。

与 ``voice_replace.run_freezone_voice_replace`` 同族，前三步逐字复用（不改 TTS / ASR
解析签名），只在末端多一步「音轨回填」，把产物从纯音频升级为配好音的视频：

1. 源视频 → 本地音频（视频先经 ``run_freezone_audio_separate`` 抽出音轨，纯本地 ffmpeg）。
2. 本地音频 → ASR 取原始台词文本（候选选择同口径：本地音频无公网直链，FunASR 不参与；
   非组织优先豆包语音直连、其次网关 multipart，组织只走网关）。
3. 参考音频（已在提交路由注册成账号级 ``user_custom`` 声线）→ ``generate_freezone_audio_speech``
   零样本合成目标音色新声；``language`` 作发音提示透传（支持的渠道认、不支持的忽略）。
4. **新增**：新声回填 → 用配音音轨**替换原视频整条音轨** → mp4 成片，落 ``_outputs/freezone_video_dubbing``。

出网说明（runner 记为 NETWORK / EG-15a 同族）：ASR 经网关 multipart 上传本地音频、
TTS 经网关 / aipdd 直连；音轨回填是纯本地 ffmpeg、不出网。FunASR / 豆包直连使用平台侧
凭据，组织任务须改选网关 ASR。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from novelvideo.egress_context import TrustedEgressContext
from novelvideo.freezone.paths import outputs_dir


async def run_freezone_video_dubbing(
    *,
    store: Any,
    username: str,
    project: str,
    account_voice_username: str,
    project_dir: Path,
    job_id: str,
    source_path: str,
    source_kind: str,
    voice_ref: dict | None,
    asr_model: str,
    language: str = "",
    model: str = "",
    egress_context: TrustedEgressContext | None = None,
) -> dict[str, Any]:
    """编排 解封装→ASR→零样本合成→音轨回填，返回 ``{video_path, text, duration_ms, ...}``。

    与抖音 / 音视频文案 / voice_replace 叶同为「单叶内部多跳出网」：runner 经
    ``_call_freezone_leaf`` 把受信 ``egress_context`` 注入本叶，本叶再透传给 ASR 网关
    与 TTS 调用。解封装与音轨回填两段纯本地 ffmpeg 不经出网。
    """
    from novelvideo.freezone.audio_node import generate_freezone_audio_speech
    from novelvideo.freezone.douyin_copywrite import pick_asr_candidate_for_local_audio
    from novelvideo.freezone.jobs import (
        replace_video_audio_track,
        run_freezone_audio_separate,
    )
    from novelvideo.freezone.media_copywrite import _transcribe

    is_organization = egress_context is not None and bool(egress_context.is_organization)

    if not voice_ref:
        raise ValueError("video_dubbing 需要参考音色的 voice_ref")

    # ① 源 → 本地音频。视频先解封装抽音轨（纯本地），音频直接用。
    if str(source_kind or "").strip().lower() == "video":
        separated = await run_freezone_audio_separate(
            project_dir=project_dir,
            job_id=job_id,
            source_path=str(source_path),
        )
        source_audio_path = separated.get("audio_path")
        if source_audio_path is None:
            raise RuntimeError("源视频没有可用音轨，无法配音")
    else:
        source_audio_path = Path(source_path)
        if not source_audio_path.exists():
            raise OSError(f"源音频文件不存在: {source_audio_path}")

    # ② 本地音频 → 台词文本。候选选择只走统一口径（本地音频无公网直链）：
    # FunASR 不参与；显式指定的候选不可用 / 未指定时自动选（非组织：豆包语音→网关，
    # 组织：只网关），直连候选在组织任务下会被自动过滤成网关，而不是报错。
    candidate = pick_asr_candidate_for_local_audio(
        asr_model, is_organization=is_organization
    )
    if candidate is None:
        raise RuntimeError(
            "没有可用的 ASR 通道：本地音频需要一个能接收上传的候选（网关 ASR 或豆包语音）。"
            "请在「设置 → 模型与渠道」开通 ASR 模型渠道，或配置火山语音（豆包 ASR）凭证后重试"
        )
    text, source_label = await _transcribe(
        candidate,
        audio_path=Path(source_audio_path),
        audio_url="",
        job_id=job_id,
        egress_context=egress_context,
    )
    text = str(text or "").strip()
    if not text:
        raise RuntimeError("未能识别原始台词：ASR 未返回有效语音内容")

    # ③ 参考音色零样本合成新声（语言作发音提示透传）。
    speech = await generate_freezone_audio_speech(
        store=store,
        username=username,
        project=project,
        account_voice_username=account_voice_username,
        project_dir=project_dir,
        job_id=job_id,
        text=text,
        voice_ref=voice_ref,
        egress_context=egress_context,
        model=str(model or "").strip(),
        language=str(language or "").strip(),
    )

    # ④ 音轨回填：用配音新声替换原视频整条音轨 → mp4 成片（原声为音频时，视频输入
    # 仍是原始 source_path；纯本地 ffmpeg）。
    final_dir = outputs_dir(project_dir, "freezone_video_dubbing")
    final_dir.mkdir(parents=True, exist_ok=True)
    final_path = final_dir / f"{job_id}.mp4"
    await replace_video_audio_track(
        source_video_path=str(source_path),
        dub_audio_path=speech.audio_path,
        output_path=final_path,
    )

    return {
        "video_path": final_path,
        "text": text,
        # 实际生效的 ASR 候选展示名（可能与提交时选的不一致：不可用时会自动退回）。
        "asr_source": str(source_label or candidate.get("display") or ""),
        "duration_ms": speech.duration_ms,
        "mime_type": "video/mp4",
        "model": speech.model,
        "voice_source": speech.voice_source,
        "voice_sha256": speech.voice_sha256,
    }
