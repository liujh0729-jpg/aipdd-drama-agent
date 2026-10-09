# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 人声替换：工具页「人声复刻 → 替换人声」的编排执行叶。

设计（全部复用既有能力，不改 TTS / ASR 解析签名）：
1. 源音频/视频 → 本地音频（视频先经 ``run_freezone_audio_separate`` 抽出音轨，
   纯本地 ffmpeg；该函数只做音视频解封装，不做人声/背景分离）。
2. 本地音频 → ASR 取原始台词文本（走统一候选选择：本地音频无公网直链，FunASR
   不参与；非组织任务优先豆包语音直连，其次网关 multipart，组织任务只走网关）。
3. 参考音频在提交路由里已注册成账号级 ``user_custom`` 声线 → 这里带
   ``voice_ref={"scope":"user_custom","voice_id":...}`` 走
   ``generate_freezone_audio_speech`` 零样本合成：IndexTTS2 以参考音频做音色 /
   韵律克隆，把原始台词用目标音色重说一遍。
4. 产物是「目标音色的新声」纯音频，落到 ``_outputs/freezone_voice_replace`` 目录。

出网说明（runner 记为 NETWORK / EG-15a 同族）：ASR 经网关 multipart 上传本地
音频（``_transcribe_via_gateway`` 走 ``begin_trusted_audio_operation``）；TTS 经
网关 / aipdd 直连。FunASR / 豆包直连使用平台侧凭据，组织任务须改选网关 ASR。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from novelvideo.egress_context import TrustedEgressContext
from novelvideo.freezone.paths import outputs_dir


async def run_freezone_voice_replace(
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
    model: str = "",
    egress_context: TrustedEgressContext | None = None,
) -> dict[str, Any]:
    """编排分离→ASR→零样本合成，返回 ``{audio_path, text, duration_ms, model, voice_source}``。

    与抖音 / 音视频文案叶同为「单叶内部多跳出网」的组织方式：runner 经
    ``_call_freezone_leaf`` 把受信 ``egress_context`` 注入本叶，本叶再把它透传给
    ASR 网关与 TTS 调用。纯本地的解封装（视频抽音轨）不经出网。
    """
    from novelvideo.freezone.audio_node import generate_freezone_audio_speech
    from novelvideo.freezone.douyin_copywrite import pick_asr_candidate_for_local_audio
    from novelvideo.freezone.jobs import run_freezone_audio_separate
    from novelvideo.freezone.media_copywrite import _transcribe

    is_organization = egress_context is not None and bool(egress_context.is_organization)

    if not voice_ref:
        raise ValueError("voice_replace 需要参考音色的 voice_ref")

    # ① 源 → 本地音频。视频先解封装抽音轨（纯本地），音频直接用。
    if str(source_kind or "").strip().lower() == "video":
        separated = await run_freezone_audio_separate(
            project_dir=project_dir,
            job_id=job_id,
            source_path=str(source_path),
        )
        audio_path = separated.get("audio_path")
        if audio_path is None:
            raise RuntimeError("源视频没有可用音轨，无法替换人声")
    else:
        audio_path = Path(source_path)
        if not audio_path.exists():
            raise OSError(f"源音频文件不存在: {audio_path}")

    # ② 本地音频 → 台词文本。候选选择只走统一口径（本地音频无公网直链）：
    # FunASR 不参与；显式指定的候选不可用 / 未指定时自动选「非组织：豆包语音→网关，
    # 组织：只网关」。直连候选在组织任务下会被自动过滤成网关，而不是报错。
    candidate = pick_asr_candidate_for_local_audio(
        asr_model, is_organization=is_organization
    )
    if candidate is None:
        raise RuntimeError(
            "没有可用的 ASR 通道：本地音频需要一个能接收上传的候选（网关 ASR 或豆包语音）。"
            "请在「设置 → 模型网关」开通 ASR 模型渠道，或配置火山语音（豆包 ASR）凭证后重试"
        )
    text, source_label = await _transcribe(
        candidate,
        audio_path=Path(audio_path),
        audio_url="",
        job_id=job_id,
        egress_context=egress_context,
    )
    text = str(text or "").strip()
    if not text:
        raise RuntimeError("未能识别原始台词：ASR 未返回有效语音内容")

    # ③ 参考音色零样本合成新声（IndexTTS2 以参考音频克隆音色 / 韵律）。
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
    )

    # ④ 归一到本任务专属产物路径（TTS 写在 audio_speech 目录，此处搬过来）。
    final_dir = outputs_dir(project_dir, "freezone_voice_replace")
    final_dir.mkdir(parents=True, exist_ok=True)
    final_path = final_dir / f"{job_id}.mp3"
    shutil.move(str(speech.audio_path), str(final_path))

    return {
        "audio_path": final_path,
        "text": text,
        # 实际生效的 ASR 候选展示名（可能与提交时选的不一致：不可用时会自动退回）。
        "asr_source": str(source_label or candidate.get("display") or ""),
        "duration_ms": speech.duration_ms,
        "mime_type": speech.mime_type,
        "model": speech.model,
        "voice_source": speech.voice_source,
        "voice_sha256": speech.voice_sha256,
    }
