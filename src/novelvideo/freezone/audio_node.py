"""Freezone audio-node helpers backed by the project's IndexTTS2 flow."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novelvideo.config import OUTPUT_DIR, get_indextts2_record_model
from novelvideo.media_archive_copy import copy_archived_result
from novelvideo.generators.indextts2 import IndexTTS2Client
from novelvideo.egress_context import (
    TrustedEgressContext,
    ambient_organization_egress_context,
)
from novelvideo.generators.audio_gateway import begin_trusted_audio_operation
from novelvideo.generators.tts_generator import (
    complete_audio_operation,
    mark_audio_operation_unknown,
    reject_audio_operation,
)
from novelvideo.project_config import (
    load_effective_narration_style_for_voice_from_state_dir,
    load_narrator_reference_audio_from_state_dir,
)
from novelvideo.seedance2_i2v.voice_clone import (
    build_reference_audio_url,
    file_sha256,
    narration_style_prompt,
    resolve_character_voice,
    resolve_narrator_source,
)
from novelvideo.freezone.paths import outputs_dir
from novelvideo.freezone import minimax_api

USER_VOICE_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".webm"}
USER_VOICE_SCOPE = "user_custom"
VOICE_FILE_UNREADABLE_MESSAGE = "声线文件无法读取，请重新选择或检查文件是否完整"


@dataclass
class FreezoneAudioSpeechResult:
    audio_path: Path
    duration_ms: int
    mime_type: str
    model: str
    voice_source: str
    voice_sha256: str


@dataclass
class FreezoneVoiceRefResolution:
    audio_path: Path
    sha256: str
    source: str


class VoicePrerequisiteError(RuntimeError):
    error_code = "voice_prereq_required"


def freezone_audio_speech_output_path(project_dir: Path, job_id: str) -> Path:
    return outputs_dir(project_dir, "freezone_audio_speech") / f"{job_id}.mp3"


def freezone_audio_eleven_music_output_path(project_dir: Path, job_id: str) -> Path:
    return outputs_dir(project_dir, "freezone_audio_eleven_music") / f"{job_id}.mp3"


async def _record_music_model_call(
    *,
    model: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """[aipdd fork delta] WP-D: 只记一次上游调用，不做预约 / 退款。"""
    try:
        from novelvideo.ports import get_usage_meter

        await get_usage_meter().bump_model_call(
            user_id=None,
            model=model,
            metadata=metadata,
        )
    except Exception:
        pass


def user_audio_voices_dir(username: str) -> Path:
    return Path(OUTPUT_DIR) / username / "_account" / "freezone" / "audio" / "voices"


def user_audio_voices_index_path(username: str) -> Path:
    return user_audio_voices_dir(username) / "voices.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_voice_name(value: str) -> str:
    return re.sub(r"[\x00-\x1f]", "", str(value or "").strip())[:80] or "未命名音色"


def _safe_extension(filename: str | None) -> str:
    suffix = Path(str(filename or "")).suffix.lower()
    if suffix not in USER_VOICE_EXTENSIONS:
        raise ValueError("unsupported voice audio format; use mp3/wav/m4a/aac/ogg/webm")
    return suffix


def _load_user_voice_records(username: str) -> list[dict]:
    path = user_audio_voices_index_path(username)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        records = data.get("voices", [])
    else:
        records = data
    if not isinstance(records, list):
        return []
    return [item for item in records if isinstance(item, dict)]


def _write_user_voice_records(username: str, records: list[dict]) -> None:
    path = user_audio_voices_index_path(username)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"voices": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _user_voice_abs_path(username: str, record: dict) -> Path:
    return Path(OUTPUT_DIR) / username / str(record.get("path") or "")


def is_readable_audio_file(path: Path) -> bool:
    """Return whether ``path`` is a regular file that can actually be opened."""
    if not path.is_file():
        return False
    with path.open("rb") as stream:
        stream.read(1)
    return True


def public_user_voice_payload(username: str, record: dict) -> dict:
    voice_id = str(record.get("voice_id") or "")
    label = str(record.get("name") or record.get("label") or voice_id or "未命名音色")
    path = str(record.get("path") or "")
    abs_path = _user_voice_abs_path(username, record)
    try:
        exists = bool(path and is_readable_audio_file(abs_path))
    except OSError:
        exists = False
    return {
        "scope": USER_VOICE_SCOPE,
        "voice_id": voice_id,
        "label": label,
        "name": label,
        "path": path,
        "url": "",
        "exists": exists,
        "sha256": str(record.get("sha256") or ""),
        "duration_ms": int(record.get("duration_ms") or 0),
        "mime_type": str(record.get("mime_type") or ""),
        "created_at": str(record.get("created_at") or ""),
        "updated_at": str(record.get("updated_at") or ""),
        "source_filename": str(record.get("source_filename") or ""),
    }


def list_user_audio_voices(username: str) -> list[dict]:
    return [
        public_user_voice_payload(username, record)
        for record in _load_user_voice_records(username)
    ]


def create_user_audio_voice(
    *,
    username: str,
    name: str,
    filename: str | None,
    content: bytes,
    mime_type: str = "",
) -> dict:
    if not content:
        raise ValueError("voice audio file is empty")
    extension = _safe_extension(filename)
    voice_id = f"fv_{uuid.uuid4().hex[:16]}"
    rel_path = f"_account/freezone/audio/voices/{voice_id}/reference{extension}"
    abs_path = Path(OUTPUT_DIR) / username / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(content)

    now = _utc_now()
    record = {
        "voice_id": voice_id,
        "name": _safe_voice_name(name),
        "path": rel_path,
        "sha256": file_sha256(abs_path),
        "duration_ms": _duration_ms(abs_path),
        "mime_type": mime_type or "application/octet-stream",
        "source_filename": Path(str(filename or "reference")).name,
        "created_at": now,
        "updated_at": now,
    }
    records = _load_user_voice_records(username)
    records.append(record)
    _write_user_voice_records(username, records)
    return public_user_voice_payload(username, record)


def resolve_user_audio_voice(
    username: str, voice_id: str
) -> FreezoneVoiceRefResolution:
    target = str(voice_id or "").strip()
    if not target:
        raise RuntimeError("user_custom voice_id is required")
    for record in _load_user_voice_records(username):
        if str(record.get("voice_id") or "") != target:
            continue
        path = _user_voice_abs_path(username, record)
        if not is_readable_audio_file(path):
            raise RuntimeError(f"用户音色文件不存在: {target}")
        sha = str(record.get("sha256") or "") or file_sha256(path)
        return FreezoneVoiceRefResolution(path, sha, USER_VOICE_SCOPE)
    raise RuntimeError(f"用户音色不存在: {target}")


def _duration_ms(audio_path: Path) -> int:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return int(float(result.stdout.strip()) * 1000)
    except Exception:
        return 0


async def _project_path(project_dir: Path, stored_path: str) -> Path | None:
    value = str(stored_path or "").strip()
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = project_dir / path
    return path if await asyncio.to_thread(is_readable_audio_file, path) else None


@dataclass(frozen=True)
class _PathOnlyStore:
    """Stands in for the project store where only ``project_dir`` is ever read.

    ``resolve_narrator_source`` takes a store for exactly two things: the
    project directory, and -- if no character rows are handed to it -- a lookup
    of every character in the database.  Once the rows travel with the task the
    second use is gone, and passing this instead of a real store keeps that
    function unchanged while making the difference impossible to lose: any
    attribute other than ``project_dir`` fails loudly instead of quietly
    reopening project state.
    """

    project_dir: str


def _projected_character_rows(row: Any) -> list[Any]:
    """Rebuild the character rows the projection carried as plain JSON.

    The resolution below reads characters as model objects, so they are
    validated back into the model here rather than teaching four call sites to
    accept a row in two shapes.  An absent row becomes an empty list, never
    ``None``: ``None`` is what the callee reads as "go look them up yourself".
    """
    from novelvideo.models import NovelCharacter

    if not isinstance(row, dict):
        return []
    return [NovelCharacter.model_validate(dict(row))]


async def _resolve_voice_ref(
    *,
    store,
    username: str,
    account_voice_username: str | None = None,
    project_dir: Path,
    voice_ref: dict | None,
    characters: list[Any] | None = None,
) -> FreezoneVoiceRefResolution | None:
    if not isinstance(voice_ref, dict):
        return None

    scope = str(voice_ref.get("scope") or "").strip()
    character_name = str(voice_ref.get("character_name") or "").strip()
    identity_id = str(voice_ref.get("identity_id") or "").strip()
    slot = str(voice_ref.get("slot") or "").strip()

    if scope == USER_VOICE_SCOPE:
        return await asyncio.to_thread(
            resolve_user_audio_voice,
            account_voice_username or username,
            str(voice_ref.get("voice_id") or ""),
        )

    if characters is None:
        characters = list(await store.list_characters())
    else:
        characters = list(characters)

    def _find_character():
        return next(
            (
                item
                for item in characters
                if str(getattr(item, "name", "") or "") == character_name
            ),
            None,
        )

    if scope == "character_default":
        character = _find_character()
        path = await _project_path(
            project_dir,
            getattr(character, "reference_audio_path", "") if character else "",
        )
        if path is None:
            raise RuntimeError(f"角色默认声线不可用: {character_name or '<空>'}")
        sha = str(
            getattr(character, "reference_audio_sha256", "") or ""
        ) or await asyncio.to_thread(file_sha256, path)
        return FreezoneVoiceRefResolution(path, sha, "character_default")

    if scope == "character_age_group":
        character = _find_character()
        samples = (
            getattr(character, "voice_samples_by_age_group", None) or {}
            if character
            else {}
        )
        entry = samples.get(slot) if isinstance(samples, dict) else None
        path = await _project_path(
            project_dir, entry.get("path", "") if isinstance(entry, dict) else ""
        )
        if path is None:
            raise RuntimeError(
                f"角色年龄段声线不可用: {character_name or '<空>'}/{slot or '<空>'}"
            )
        sha = str(entry.get("sha256", "") or "") if isinstance(entry, dict) else ""
        return FreezoneVoiceRefResolution(
            path, sha or await asyncio.to_thread(file_sha256, path), "character_age_group"
        )

    if scope in {"identity", "identity_resolved"}:
        character = _find_character()
        identity = None
        if character is not None:
            identity = next(
                (
                    item
                    for item in list(getattr(character, "identities", None) or [])
                    if str(getattr(item, "identity_id", "") or "") == identity_id
                ),
                None,
            )
        if character is None or identity is None:
            raise RuntimeError(
                f"身份声线不可用: {character_name or '<空>'}/{identity_id or '<空>'}"
            )
        if scope == "identity":
            path = await _project_path(
                project_dir, getattr(identity, "reference_audio_path", "")
            )
            if path is None:
                raise RuntimeError(f"身份声线未配置: {identity_id}")
            sha = str(
                getattr(identity, "reference_audio_sha256", "") or ""
            ) or await asyncio.to_thread(file_sha256, path)
            return FreezoneVoiceRefResolution(path, sha, "identity")
        resolved = await asyncio.to_thread(
            resolve_character_voice,
            project_dir=project_dir,
            character=character,
            identity=identity,
        )
        if resolved.audio_path is None:
            raise RuntimeError(f"身份实际声线不可用: {identity_id}")
        if not await asyncio.to_thread(is_readable_audio_file, resolved.audio_path):
            raise RuntimeError(f"身份实际声线不可用: {identity_id}")
        return FreezoneVoiceRefResolution(
            resolved.audio_path,
            resolved.sha256
            or await asyncio.to_thread(file_sha256, resolved.audio_path),
            f"identity_resolved:{resolved.tier or 'unknown'}",
        )

    return None


async def resolve_speech_voice(
    *,
    store,
    username: str,
    project: str,
    account_voice_username: str | None = None,
    project_dir: Path,
    voice_ref: dict | None = None,
    projection: Any = None,
) -> tuple[str, FreezoneVoiceRefResolution]:
    """Resolve narration style + reference voice for one speech job.

    Without a projection this reads project-bound state exactly as it always
    has: ``load_effective_narration_style_for_voice`` /
    ``load_narrator_reference_audio`` read the project state directory and
    ``store.list_characters()`` reads the project database, all of which only
    exist on the machine that holds the project.

    With a projection those same values arrive with the task, pinned when it was
    submitted, and nothing project-bound is read here.  A projection that is
    present but missing a field raises rather than falling back to the database:
    a silent fallback would hide the very thing the projection exists to make
    visible.
    """
    if projection is None:
        narration_style = load_effective_narration_style_for_voice_from_state_dir(
            store.state_dir
        )
        voice_characters = None
        narrator_store = store
    else:
        narration_style = str(projection.require("narration_style") or "")
        voice_characters = _projected_character_rows(projection.require("voice_character"))
        narrator_store = _PathOnlyStore(str(project_dir))

    try:
        selected_voice = await _resolve_voice_ref(
            store=store,
            username=username,
            account_voice_username=account_voice_username,
            project_dir=project_dir,
            voice_ref=voice_ref,
            characters=voice_characters,
        )
    except OSError as exc:
        raise VoicePrerequisiteError(VOICE_FILE_UNREADABLE_MESSAGE) from exc
    except RuntimeError as exc:
        raise VoicePrerequisiteError(str(exc)) from exc
    if selected_voice is None:
        if projection is None:
            descriptor = load_narrator_reference_audio_from_state_dir(store.state_dir)
            characters = (
                await store.list_characters() if narration_style == "first_person" else None
            )
        else:
            descriptor = dict(projection.require("narrator_reference_audio") or {})
            characters = (
                _projected_character_rows(projection.require("narrator_main_character"))
                if narration_style == "first_person"
                else None
            )
        narrator_descriptor_present = bool(str(descriptor.get("path") or "").strip())
        try:
            voice = await asyncio.to_thread(
                resolve_narrator_source,
                store=narrator_store,
                narration_style=narration_style,
                project_narrator_stored_path=descriptor.get("path", ""),
                characters=characters,
            )
        except OSError as exc:
            raise VoicePrerequisiteError(VOICE_FILE_UNREADABLE_MESSAGE) from exc
        if voice.audio_path is not None:
            try:
                readable = await asyncio.to_thread(
                    is_readable_audio_file,
                    voice.audio_path,
                )
            except OSError as exc:
                raise VoicePrerequisiteError(VOICE_FILE_UNREADABLE_MESSAGE) from exc
            if not readable:
                raise VoicePrerequisiteError(VOICE_FILE_UNREADABLE_MESSAGE)
        if voice.audio_path is None:
            if voice.source == "project_narrator":
                message = (
                    "解说人声线文件无法读取，请检查文件是否完整"
                    if narrator_descriptor_present
                    else "项目解说人声线未配置，请上传或录制解说人音频"
                )
            else:
                message = voice.error or "解说声线缺失"
            raise VoicePrerequisiteError(message)
        selected_voice = FreezoneVoiceRefResolution(
            voice.audio_path,
            voice.sha256,
            voice.source or "project_narrator",
        )
    return narration_style, selected_voice


def _is_indextts2_model(model: str) -> bool:
    """[aipdd fork delta] 判断选中的音频模型是否仍是 IndexTTS2 引擎系。

    IndexTTS2 的复刻/情绪参数语义只在专用客户端里成立，命中时不走通用
    /audio/speech 分支。

    [aipdd fork delta] env ``INDEXTTS2_NEWAPI_MODEL`` 已退出解析链，代码里也不再有内置
    音频模型名：命中判定只看**实际调用名本身**是否带着 IndexTTS 字样（渠道目录里就是
    这么命名的）。比较前先抹掉连字符 / 下划线，``index-tts-2`` 这类写法也能命中。
    """
    clean = str(model or "").strip().casefold()
    if not clean:
        return True
    return "indextts" in re.sub(r"[^a-z0-9]", "", clean)


async def _generate_freezone_audio_speech_via_gateway(
    *,
    requested_model: str,
    clean_text: str,
    emotion_prompt: str,
    selected_voice: Any,
    output_path: Path,
    egress_context: TrustedEgressContext | None,
    job_id: str,
    language: str = "",
) -> FreezoneAudioSpeechResult:
    """[aipdd fork delta] 用用户在画布音频节点选中的 audio 模型走 newapi 通用
    /audio/speech 调用（与音乐同一出网点）。参考声线仍放进 metadata：支持复刻
    的渠道会认，不支持的渠道自然忽略。"""
    from novelvideo.generators.indextts2 import record_tts_model_call
    from novelvideo.ports import update_current_model_call_log

    metadata: dict[str, Any] = {
        "audio_url": build_reference_audio_url(selected_voice.audio_path),
        "should_use_prompt_for_emotion": True,
    }
    if str(emotion_prompt or "").strip():
        metadata["emotion_prompt"] = str(emotion_prompt).strip()
    # [aipdd fork delta] 视频配音的语种提示：非空才带上（支持复刻/多语的渠道会认，
    # 不支持的渠道自然忽略），与参考 audio_url 同处 metadata。
    if str(language or "").strip():
        metadata["language"] = str(language).strip()

    write_kwargs: dict[str, Any] = {
        "output_path": output_path,
        "model": requested_model,
        "input_text": clean_text,
        "response_format": "mp3",
        "metadata": metadata,
        "timeout_seconds": 600.0,
    }
    if egress_context is not None:
        write_kwargs.update(
            egress_context=egress_context,
            business_task_id=f"freezone-audio-speech:{job_id}",
        )
    try:
        await update_current_model_call_log(
            request_payload={
                "model": requested_model,
                "input": clean_text,
                "metadata": metadata,
            },
        )
        response_payload = await _write_newapi_audio_speech(**write_kwargs)
        await update_current_model_call_log(
            response_payload=response_payload,
        )
        if not output_path.exists() or output_path.stat().st_size <= 0:
            raise RuntimeError("NewAPI speech audio file was not created")
        # [aipdd fork delta] WP-D: 预约 / 确认 / 退款随 CE 额度体系删除，
        # 成功投递后只记一次上游调用观测位（失败仍原样冒泡，语义不变）。
        await record_tts_model_call(model=requested_model)
    except BaseException as exc:
        await update_current_model_call_log(error_message=type(exc).__name__)
        raise
    return FreezoneAudioSpeechResult(
        audio_path=output_path,
        duration_ms=_duration_ms(output_path),
        mime_type="audio/mpeg",
        model=requested_model,
        voice_source=selected_voice.source,
        voice_sha256=selected_voice.sha256,
    )


# ---------------------------------------------------------------------------
# [aipdd fork delta req46] MiniMax Speech 2.8 直连（POST /v1/t2a_v2）
#
# 这条协议与网关的 OpenAI 兼容 ``audio/speech`` 不是一回事：参数分成
# ``voice_setting`` / ``audio_setting`` 两组，产物在 ``data.audio`` 里以 **十六进制**
# 返回，而业务失败照样是 HTTP 200 + ``base_resp.status_code``（双形态错误由
# ``minimax_api`` 统一消化）。所以命中 MiniMax 时必须直连，其余语音模型继续走网关。
#
# 判定只看两件事：**模型在设置里绑定的渠道**（provider 或生效网关 host 指向 MiniMax）
# 与**模型名是不是 speech 族**（上游只有 ``speech-*`` 认 t2a_v2）。型号名一律不写死
# （req40 口径），由渠道目录自动发现即可选到。
#
# 本期边界：只支持系统音色。画布上配的解说人参考声线在这条链路上不生效（克隆音色要
# 走 upload → voice_id 两步、7 天过期，属后续需求）；结果里的 ``voice_source`` 写成
# ``minimax:{voice_id}``，任务详情一眼能看出用的是渠道默认音色而不是项目声线，
# 不假装复刻成功。
# ---------------------------------------------------------------------------

MINIMAX_T2A_TEXT_MAX_CHARS = 10_000
MINIMAX_T2A_SAMPLE_RATES = (8000, 16000, 22050, 24000, 32000, 44100)
MINIMAX_T2A_BITRATES = (32000, 64000, 128000, 256000)
MINIMAX_T2A_FORMATS = ("mp3", "pcm", "flac", "wav", "pcmu_raw", "pcmu_wav", "opus")
# 上游 voice_setting.emotion 只认枚举；现有语音参数传的是一句自由文本情绪描述
# （解说风格 / emotion_prompt）。这里只做同义映射，映射不上就留空让模型自己挑最
# 自然的语气 —— 硬套一个不相干的枚举比不给更容易出戏。
# ``fluent`` / ``whisper`` 两个值 speech-2.8 两条都不支持（上游文档明写只给 2.6），
# 所以不进映射表。
MINIMAX_T2A_EMOTIONS = (
    "happy",
    "sad",
    "angry",
    "fearful",
    "disgusted",
    "surprised",
    "calm",
)
_MINIMAX_EMOTION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("happy", ("开心", "高兴", "欢快", "喜悦", "upbeat", "happy")),
    ("sad", ("悲伤", "难过", "低沉", "哀", "sad")),
    ("angry", ("愤怒", "生气", "恼火", "angry")),
    ("fearful", ("恐惧", "害怕", "惊恐", "fearful")),
    ("disgusted", ("厌恶", "嫌弃", "disgusted")),
    ("surprised", ("惊讶", "吃惊", "surprised")),
    ("calm", ("平静", "沉稳", "舒缓", "温和", "冷静", "客观", "calm")),
)


def is_minimax_speech_model(model: str) -> bool:
    """speech 族判定：上游只有 ``speech-*`` 走 t2a_v2（名字来自目录，不写死）。"""

    return "speech" in str(model or "").strip().casefold()


def minimax_speech_emotion(text: str) -> str:
    """自由文本情绪描述 → 上游 emotion 枚举（映射不上返回空串，交给模型自己判断）。"""

    clean = str(text or "").strip().casefold()
    if not clean:
        return ""
    for emotion, keywords in _MINIMAX_EMOTION_KEYWORDS:
        if any(keyword.casefold() in clean for keyword in keywords):
            return emotion
    return ""


def minimax_speech_channel(model: str) -> minimax_api.MiniMaxChannel | None:
    """选中模型该不该走 MiniMax 直连合成；不该则返回 ``None``（一切照旧走网关）。

    渠道归属有两个来源，顺序不能反：设置页把该音频模型绑到了哪个 provider 渠道是
    **确定事实**（``resolve_media_provider_override``），绑不到才看生效网关的地址是
    不是 MiniMax 站点（纯 ``.env`` 直连部署）。判定成 MiniMax 之后，凭据与音色等
    扩展配置一律回到设置页那条渠道 —— 渠道卡里的 Key 才是真源。
    """

    if not is_minimax_speech_model(model):
        return None
    clean_model = str(model or "").strip()
    override: tuple[str, str, str] | None
    try:
        from novelvideo.model_gateway_settings import resolve_media_provider_override

        override = resolve_media_provider_override(clean_model, media_type="audio")
    except Exception:  # noqa: BLE001 - 设置库读不动时按「没绑渠道」处理，继续看网关
        override = None
    if override is not None:
        _base_url, _api_key, provider = override
        if not minimax_api.is_minimax_provider(provider):
            return None
        return minimax_api.resolve_minimax_channel(provider)

    from novelvideo.config import get_newapi_runtime_credentials

    try:
        api_key, gateway_base = get_newapi_runtime_credentials()
    except Exception:  # noqa: BLE001 - 没配网关就等于不直连
        return None
    if not minimax_api.is_minimax_host(str(gateway_base or "")):
        return None
    if minimax_api.configured_minimax_providers():
        # 设置页配过渠道就按渠道走：默认音色只存在渠道 settings 上。
        return minimax_api.resolve_minimax_channel("")
    return minimax_api.MiniMaxChannel(
        provider="",
        api_root=minimax_api.api_root_from_base_url(str(gateway_base or "")),
        api_key=str(api_key or "").strip(),
        base_url=str(gateway_base or "").strip(),
        settings={},
    )


def minimax_speech_voice_id(channel: minimax_api.MiniMaxChannel, explicit: str = "") -> str:
    """本轮用的系统音色：显式指定优先，否则取渠道卡里的「Speech 默认音色」。"""

    clean = str(explicit or "").strip()
    if clean:
        return clean
    extra = minimax_api.minimax_channel_extra(channel)
    return str(extra.get("voiceId") or "").strip()


def build_minimax_t2a_payload(
    *,
    model: str,
    text: str,
    voice_id: str,
    emotion: str = "",
    speed: float = 1.0,
    volume: float = 1.0,
    pitch: int = 0,
    sample_rate: int = 32000,
    bitrate: int = 128000,
    audio_format: str = "mp3",
) -> dict[str, Any]:
    """模式 → t2a_v2 请求体，**提交前**按上游约束校验（越界就地报错，不等上游回一屏）。"""

    clean_model = str(model or "").strip()
    if not is_minimax_speech_model(clean_model):
        raise ValueError(
            f"{clean_model or '未选择'} 不是 MiniMax 语音合成模型（上游只有 speech 族认 "
            "t2a_v2）：请在「设置 → 模型与渠道」选该渠道目录里发现的 speech 模型"
        )
    clean_text = str(text or "")
    if not clean_text.strip():
        raise ValueError("MiniMax 语音合成需要待朗读文本，当前为空")
    if len(clean_text) > MINIMAX_T2A_TEXT_MAX_CHARS:
        raise ValueError(
            f"文本 {len(clean_text)} 字，超过同步合成上限 {MINIMAX_T2A_TEXT_MAX_CHARS} 字："
            "请分段，或改用网关链路"
        )
    clean_voice = str(voice_id or "").strip()
    if not clean_voice:
        raise minimax_api.MiniMaxChannelUnavailable(
            "MiniMax 直连合成没有可用音色：请在「设置 → 模型与渠道」的 MiniMax 渠道卡里"
            "填「Speech 默认音色」（系统音色 ID，例如 Chinese (Mandarin)_Lyrical_Voice，"
            "完整清单见上游 System Voice ID List）。本期只支持系统音色，克隆音色走后续需求"
        )
    try:
        speed_value = float(speed)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"语速不是数字: {speed!r}") from exc
    if not 0.5 <= speed_value <= 2:
        raise ValueError(f"语速 {speed_value} 超出上游允许范围 [0.5, 2]")
    try:
        vol_value = float(volume)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"音量不是数字: {volume!r}") from exc
    if not 0 < vol_value <= 10:
        raise ValueError(f"音量 {vol_value} 超出上游允许范围 (0, 10]")
    try:
        pitch_value = int(pitch)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"语调不是整数: {pitch!r}") from exc
    if not -12 <= pitch_value <= 12:
        raise ValueError(f"语调 {pitch_value} 超出上游允许范围 [-12, 12]")
    fmt = str(audio_format or "mp3").strip().lower()
    if fmt not in MINIMAX_T2A_FORMATS:
        raise ValueError(
            f"音频格式 {fmt} 不在上游允许档位（{', '.join(MINIMAX_T2A_FORMATS)}）"
        )
    rate_value = int(sample_rate or 0)
    if rate_value not in MINIMAX_T2A_SAMPLE_RATES:
        raise ValueError(
            f"采样率 {rate_value or sample_rate} 不在上游允许档位"
            f"（{', '.join(str(v) for v in MINIMAX_T2A_SAMPLE_RATES)}）"
        )
    audio_setting: dict[str, Any] = {
        "format": fmt,
        "sample_rate": rate_value,
        "channel": 1,
    }
    # 码率只对 mp3 有意义（上游文档明写），别的格式带上只会收到参数错误。
    if fmt == "mp3":
        bitrate_value = int(bitrate or 0)
        if bitrate_value not in MINIMAX_T2A_BITRATES:
            raise ValueError(
                f"码率 {bitrate_value or bitrate} 不在上游允许档位"
                f"（{', '.join(str(v) for v in MINIMAX_T2A_BITRATES)}）"
            )
        audio_setting["bitrate"] = bitrate_value
    voice_setting: dict[str, Any] = {
        "voice_id": clean_voice,
        "speed": speed_value,
        "vol": vol_value,
        "pitch": pitch_value,
    }
    clean_emotion = str(emotion or "").strip().lower()
    if clean_emotion in MINIMAX_T2A_EMOTIONS:
        voice_setting["emotion"] = clean_emotion
    return {
        "model": clean_model,
        "text": clean_text,
        "stream": False,
        # 固定 hex：url 档只有 24 小时有效期，任务产物必须当场落进项目存储。
        "output_format": "hex",
        "voice_setting": voice_setting,
        "audio_setting": audio_setting,
    }


def _minimax_speech_response_log(body: dict[str, Any]) -> dict[str, Any]:
    """调用日志用的响应摘要：hex 音频体动辄几百 KB，只留诊断需要的字段。"""

    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    audio = str(data.get("audio") or "").strip()
    summary: dict[str, Any] = {
        "trace_id": str(body.get("trace_id") or ""),
        "base_resp": body.get("base_resp"),
        "extra_info": body.get("extra_info"),
        "data": {
            "status": data.get("status"),
            "audio_hex_length": len(audio),
        },
    }
    return {key: value for key, value in summary.items() if value not in (None, "")}


async def _generate_freezone_audio_speech_via_minimax_t2a(
    *,
    requested_model: str,
    channel: minimax_api.MiniMaxChannel,
    clean_text: str,
    emotion_prompt: str,
    output_path: Path,
    egress_context: TrustedEgressContext | None,
    voice_id: str = "",
    speed: float = 1.0,
    volume: float = 1.0,
    pitch: int = 0,
    response_format: str = "mp3",
    sample_rate: int = 32000,
    bitrate: int = 128000,
) -> FreezoneAudioSpeechResult:
    """MiniMax 直连合成：建单体 → POST /v1/t2a_v2 → hex 落盘 → 记一次上游调用。

    出网契约与音乐直连同一取舍（``FREEZONE_LEAF_EGRESS`` 记 NETWORK）：用的是平台侧
    设置的 MiniMax 渠道凭据，组织任务在叶内 fail-closed —— 不得拿平台凭据把组织数据
    外发给第三方。
    """

    from novelvideo.generators.indextts2 import record_tts_model_call
    from novelvideo.ports import update_current_model_call_log

    minimax_api.deny_organization_egress(egress_context, "语音合成")
    effective_voice_id = minimax_speech_voice_id(channel, voice_id)
    payload = build_minimax_t2a_payload(
        model=requested_model,
        text=clean_text,
        voice_id=effective_voice_id,
        emotion=minimax_speech_emotion(emotion_prompt),
        speed=speed,
        volume=volume,
        pitch=pitch,
        sample_rate=sample_rate,
        bitrate=bitrate,
        audio_format=response_format,
    )
    fmt = str(payload["audio_setting"]["format"])
    target_path = output_path
    if _audio_suffix(fmt) != output_path.suffix:
        target_path = output_path.with_suffix(_audio_suffix(fmt))
    try:
        await update_current_model_call_log(
            request_payload={
                **payload,
                "note": (
                    "MiniMax 直连合成使用渠道默认系统音色，项目解说参考声线本期不参与"
                    "（克隆音色走后续需求）"
                ),
            },
        )
        body = await minimax_api.post_json(
            channel, minimax_api.PATH_T2A, payload, timeout=600.0
        )
        await update_current_model_call_log(
            response_payload=_minimax_speech_response_log(body),
        )
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        audio_hex = str(data.get("audio") or "").strip()
        if not audio_hex:
            # 同步接口正常就该给 hex；没给就是上游变了形态，原文透出别装作成功。
            raise minimax_api.MiniMaxError(
                f"MiniMax 语音合成没有返回音频数据（data.audio 为空）: "
                f"{json.dumps(body, ensure_ascii=False, default=str)[:300]}",
                endpoint=minimax_api.PATH_T2A,
            )
        await minimax_api.write_hex_audio(
            audio_hex, target_path, endpoint=minimax_api.PATH_T2A
        )
        if not target_path.exists() or target_path.stat().st_size <= 0:
            raise RuntimeError("MiniMax speech audio file was not created")
        extra = body.get("extra_info") if isinstance(body.get("extra_info"), dict) else {}
        try:
            duration_ms = int(extra.get("audio_length") or 0)
        except (TypeError, ValueError):
            duration_ms = 0
        if not duration_ms:
            duration_ms = _duration_ms(target_path)
        await record_tts_model_call(
            model=requested_model,
            provider_request_id=str(body.get("trace_id") or ""),
        )
    except BaseException as exc:
        await update_current_model_call_log(error_message=type(exc).__name__)
        raise
    return FreezoneAudioSpeechResult(
        audio_path=target_path,
        duration_ms=duration_ms,
        mime_type=_audio_mime_type(fmt),
        model=requested_model,
        # 一眼能看出声线来自渠道系统音色，而不是项目解说人（本期不复刻）。
        voice_source=f"minimax:{effective_voice_id}",
        voice_sha256="",
    )


async def generate_freezone_audio_speech(
    *,
    store=None,
    username: str,
    project: str,
    account_voice_username: str | None = None,
    project_dir: Path,
    job_id: str,
    text: str,
    emotion_prompt: str = "",
    voice_ref: dict | None = None,
    projection: Any = None,
    egress_context: TrustedEgressContext | None = None,
    model: str = "",
    # [aipdd fork delta] 视频配音：语种/发音提示。仅通用网关 /audio/speech 分支把它
    # 放进 metadata（支持的渠道认、不支持的自然忽略）；IndexTTS2 / MiniMax 分支不消费。
    language: str = "",
) -> FreezoneAudioSpeechResult:
    """Generate standalone Freezone speech using the project narrator reference.

    ``projection`` is the payload projection pinned when the task was submitted
    (``task_backend.projection.read_projection``). When it is supplied nothing
    project-bound is read here and ``store`` may be ``None``; when it is absent
    the voice is resolved from project state exactly as before.
    """
    clean_text = str(text or "").strip()
    if not clean_text:
        raise ValueError("text is required")

    narration_style, selected_voice = await resolve_speech_voice(
        store=store,
        username=username,
        project=project,
        account_voice_username=account_voice_username,
        project_dir=project_dir,
        voice_ref=voice_ref,
        projection=projection,
    )

    output_path = freezone_audio_speech_output_path(project_dir, job_id)
    if egress_context is None:
        egress_context = ambient_organization_egress_context()

    # [aipdd fork delta] 画布音频节点按类型选模型：用户在后台「默认生成模型」
    # 面板配了 mediaType=audio 的模型时，路由到 newapi 通用 /audio/speech 调用；
    # IndexTTS2 系模型名仍走专用客户端（保留情绪/复刻参数语义）。
    # model 为空保持原默认链路，行为与之前逐字不变。
    requested_model = str(model or "").strip()
    if requested_model and not _is_indextts2_model(requested_model):
        # [aipdd fork delta req46] 命中 MiniMax 渠道 + speech 族 → 直连 t2a_v2；
        # 没命中一律保持原来的网关 audio/speech 路径（行为逐字不变）。
        minimax_channel = minimax_speech_channel(requested_model)
        if minimax_channel is not None:
            return await _generate_freezone_audio_speech_via_minimax_t2a(
                requested_model=requested_model,
                channel=minimax_channel,
                clean_text=clean_text,
                emotion_prompt=str(emotion_prompt or "").strip()
                or narration_style_prompt(narration_style),
                output_path=output_path,
                egress_context=egress_context,
            )
        return await _generate_freezone_audio_speech_via_gateway(
            requested_model=requested_model,
            clean_text=clean_text,
            emotion_prompt=str(emotion_prompt or "").strip()
            or narration_style_prompt(narration_style),
            selected_voice=selected_voice,
            output_path=output_path,
            egress_context=egress_context,
            job_id=job_id,
            language=str(language or "").strip(),
        )

    # 未选模型时保持原零参构造（测试/下游有按零参替换 Fake 客户端的契约）。
    client_kwargs: dict[str, Any] = {}
    if requested_model:
        client_kwargs["model"] = requested_model
    if egress_context is not None:
        client_kwargs["egress_context"] = egress_context
    generator = IndexTTS2Client(**client_kwargs)
    result = await generator.generate(
        prompt=clean_text,
        audio_url=build_reference_audio_url(selected_voice.audio_path),
        output_path=output_path,
        emotion_prompt=str(emotion_prompt or "").strip()
        or narration_style_prompt(narration_style),
    )
    if not result.success:
        raise RuntimeError(result.error or "IndexTTS2 generation failed")

    duration_ms = int((result.duration_seconds or 0) * 1000) or _duration_ms(
        output_path
    )
    return FreezoneAudioSpeechResult(
        audio_path=output_path,
        duration_ms=duration_ms,
        mime_type="audio/mpeg",
        model=get_indextts2_record_model(getattr(generator, "model", "")),
        voice_source=selected_voice.source,
        voice_sha256=selected_voice.sha256,
    )


class GatewayAudioUnavailable(RuntimeError):
    """环境缺失（网关地址未配置），不是生成失败。

    不参与下面「NewAPI audio generation failed」的归一化包装，直接透传，否则
    可执行的「去哪配」提示会被归成一句无信息量错误。
    """


def _newapi_audio_endpoint(base_url: str | None = None, *, model: str = "") -> str:
    if base_url is None:
        from novelvideo.config import get_newapi_runtime_credentials

        _api_key, resolved_base_url = get_newapi_runtime_credentials()
    else:
        resolved_base_url = base_url
    # [aipdd fork delta req41] 不再兜底到 ``http://localhost:3000/v1``：网关在哪是
    # 部署形态数据（容器内网 / 独立主机 / 反代域名），猜一个只会让请求打到不存
    # 在的监听上。没地址就是没配置。
    clean = str(resolved_base_url or "").strip().rstrip("/")
    if not clean:
        raise GatewayAudioUnavailable(
            "未配置模型网关地址，无法走网关 TTS：请到「设置 → 模型与渠道」连接网关，"
            "或在 .env 设 LOCALAPI_BASE_URL=<网关地址>。"
        )
    endpoint = clean
    # [aipdd fork delta req41] 合成路径照渠道目录：条目声明了什么就拼什么（AIPDD 写了
    # ``audio/speech``）；目录没说话才按通用 OpenAI 兼容路径拼。
    from novelvideo.model_gateway_settings import catalog_endpoint_path
    from novelvideo.catalog.upstream_catalog import EP_AUDIO_SPEECH

    suffix = (
        catalog_endpoint_path(str(model or ""), EP_AUDIO_SPEECH) or EP_AUDIO_SPEECH
    )
    if not endpoint.endswith(f"/{suffix}"):
        endpoint = f"{endpoint}/{suffix}"
    return endpoint


def _audio_mime_type(response_format: str) -> str:
    fmt = str(response_format or "mp3").strip().lower()
    return {
        "mp3": "audio/mpeg",
        "opus": "audio/opus",
        "pcm": "audio/L16",
        "ulaw": "audio/basic",
        "alaw": "audio/x-alaw-basic",
    }.get(fmt, "audio/mpeg")


def _audio_suffix(response_format: str) -> str:
    fmt = str(response_format or "mp3").strip().lower()
    return {
        "mp3": ".mp3",
        "opus": ".opus",
        "pcm": ".pcm",
        "ulaw": ".ulaw",
        "alaw": ".alaw",
    }.get(fmt, ".mp3")


async def _write_newapi_audio_speech(
    *,
    output_path: Path,
    model: str,
    input_text: str,
    response_format: str = "mp3",
    voice: str | None = None,
    metadata: dict[str, Any] | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout_seconds: float = 600.0,
    egress_context: TrustedEgressContext | None = None,
    business_task_id: str | None = None,
) -> dict[str, Any]:
    """POST 一条 newapi 通用 /audio/speech 请求并把音频写入 ``output_path``。

    出网契约（经 ``begin_trusted_audio_operation`` 单点实现，语义与既有一致）：

    - 有受信上下文时：非组织先于一切拒绝（``ORG_EGRESS_DENIED``）；已结算
      重放抛 ``audio operation already handled``；凭据解析失败先归还提交权
      再以错误码抛错；传输开始前的异常 ``reject`` 租约，传输开始后的异常
      ``mark_unknown``，成功后 ``complete(result_ref="audio:newapi:completed")``；
    - 无受信上下文时回落平台 ``get_newapi_runtime_credentials``
      （``api_key`` / ``base_url`` 参数允许显式覆盖）；
    - 失败最终归一化为 ``RuntimeError("NewAPI audio generation failed")``
      （``BaseException`` 原样冒泡；网关地址未配置的 ``GatewayAudioUnavailable``
      也原样冒泡，因为那是环境缺失而不是请求失败）。
    """
    import base64

    import httpx

    lease = None
    if egress_context is None:
        egress_context = ambient_organization_egress_context()
    if egress_context is not None:
        session = await begin_trusted_audio_operation(
            egress_context,
            capability="audio.tts.gateway",
            business_task_id=(
                str(business_task_id or "").strip()
                or f"{egress_context.task_type}:newapi-audio:{output_path.name}"
            ),
            request={
                "model": str(model or "").strip(),
                "input": str(input_text or ""),
                "response_format": str(response_format or "mp3").strip() or "mp3",
                "voice": str(voice or "").strip(),
                "metadata": metadata or {},
            },
        )
        if session.denial_code is not None:
            raise RuntimeError(session.denial_code)
        lease = session.lease
        credential = session.credential
        key = credential.api_key if credential else ""
        resolved_base_url = credential.base_url if credential else None
    else:
        from novelvideo.config import get_newapi_runtime_credentials

        key, resolved_base_url = get_newapi_runtime_credentials(
            api_key_override=api_key,
            base_url_override=base_url,
        )
    key = str(key or "").strip()
    if not key:
        raise RuntimeError("LOCALAPI_API_KEY is required for NewAPI audio generation")

    body: dict[str, Any] = {
        "model": str(model or "").strip(),
        "input": str(input_text or ""),
        "response_format": str(response_format or "mp3").strip() or "mp3",
    }
    clean_voice = str(voice or "").strip()
    if clean_voice:
        body["voice"] = clean_voice
    if metadata:
        body["metadata"] = metadata

    transport_started = False
    response_log_payload: dict[str, Any] = {}
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(
            timeout=timeout_seconds, follow_redirects=True
        ) as client:
            endpoint = _newapi_audio_endpoint(resolved_base_url, model=model)
            transport_started = True
            response = await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            response.raise_for_status()
            content_type = str(response.headers.get("content-type") or "").lower()
            if "application/json" not in content_type:
                response_log_payload = {
                    "content_type": content_type,
                    "content_length": len(response.content),
                }
                output_path.write_bytes(response.content)
            else:
                payload = response.json()
                response_log_payload = payload
                audio = (
                    payload.get("audio")
                    if isinstance(payload.get("audio"), dict)
                    else {}
                )
                result_url = str(
                    payload.get("url")
                    or payload.get("audio_url")
                    or payload.get("audioUrl")
                    or audio.get("url")
                    or ""
                ).strip()
                if not result_url:
                    data = payload.get("data")
                    if isinstance(data, list) and data and isinstance(data[0], dict):
                        first = data[0]
                        result_url = str(
                            first.get("url")
                            or first.get("audio_url")
                            or first.get("audioUrl")
                            or ""
                        ).strip()
                        audio_b64 = str(
                            first.get("b64_json") or first.get("audio") or ""
                        ).strip()
                    else:
                        audio_b64 = str(
                            payload.get("b64_json") or payload.get("audio") or ""
                        ).strip()
                    if audio_b64:
                        if audio_b64.startswith("data:") and "," in audio_b64:
                            audio_b64 = audio_b64.split(",", 1)[1]
                        output_path.write_bytes(base64.b64decode(audio_b64))
                if not output_path.exists() and not result_url:
                    raise RuntimeError(
                        "NewAPI audio response missing audio bytes or URL"
                    )
                if result_url:
                    if not await copy_archived_result(payload.get("archive"), output_path):
                        audio_response = await client.get(result_url)
                        audio_response.raise_for_status()
                        output_path.write_bytes(audio_response.content)
        if lease is not None:
            await complete_audio_operation(lease, result_ref="audio:newapi:completed")
        return response_log_payload
    except BaseException as exc:
        if lease is not None:
            if transport_started:
                await mark_audio_operation_unknown(lease)
            else:
                await reject_audio_operation(lease)
        if isinstance(exc, GatewayAudioUnavailable):
            raise
        if isinstance(exc, Exception):
            raise RuntimeError("NewAPI audio generation failed") from None
        raise


async def generate_freezone_audio_eleven_music(
    *,
    project_dir: Path,
    job_id: str,
    prompt: str,
    music_length_ms: int = 30_000,
    force_instrumental: bool = True,
    respect_sections_durations: bool = True,
    output_format: str = "mp3_44100_128",
    response_format: str = "mp3",
    model: str = "",
    egress_context: TrustedEgressContext | None = None,
) -> FreezoneAudioSpeechResult:
    """Generate standalone Freezone music through NewAPI's audio/speech endpoint."""
    from novelvideo.ports import update_current_model_call_log

    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        raise ValueError("prompt is required")
    length = int(music_length_ms or 0)
    if length < 3_000 or length > 600_000:
        raise ValueError("music_length_ms must be between 3000 and 600000")

    fmt = str(response_format or "mp3").strip() or "mp3"
    output_path = freezone_audio_eleven_music_output_path(project_dir, job_id)
    if _audio_suffix(fmt) != ".mp3":
        output_path = output_path.with_suffix(_audio_suffix(fmt))

    metadata: dict[str, Any] = {
        "music_length_ms": length,
        "force_instrumental": bool(force_instrumental),
        "respect_sections_durations": bool(respect_sections_durations),
        "output_format": str(output_format or "mp3_44100_128").strip()
        or "mp3_44100_128",
    }

    # [aipdd fork delta] 音乐模型不再内置厂商名：留空就走「设置 → 模型与渠道」的默认音频位
    # （与 IndexTTS2 同一口径），没配就提交前拒（中文成因），而不是带着一个渠道
    # 不认的名字去打 503 —— 上一个内置名（发行方私有别名）正是这样在网关上撞回 503 的。
    from novelvideo.model_slots import resolve_media_slot

    model_name = str(model or "").strip() or resolve_media_slot("audio")
    try:
        request_payload = {
            "model": model_name,
            "input": clean_prompt,
            "response_format": fmt,
            "metadata": metadata,
        }
        await update_current_model_call_log(
            request_payload=request_payload,
        )
        write_kwargs: dict[str, Any] = {
            "output_path": output_path,
            "model": model_name,
            "input_text": clean_prompt,
            "response_format": fmt,
            "metadata": metadata,
            "timeout_seconds": 900.0,
        }
        if egress_context is not None:
            write_kwargs.update(
                egress_context=egress_context,
                business_task_id=f"freezone-audio-music:{job_id}",
            )
        response_payload = await _write_newapi_audio_speech(
            **write_kwargs,
        )
        await update_current_model_call_log(
            response_payload=response_payload,
        )
        if not output_path.exists() or output_path.stat().st_size <= 0:
            raise RuntimeError("NewAPI music audio file was not created")
        await _record_music_model_call(
            model=model_name,
            metadata={"source": "freezone_audio_music", "music_length_ms": length},
        )
    except Exception as exc:
        await update_current_model_call_log(
            error_message=type(exc).__name__,
        )
        raise
    return FreezoneAudioSpeechResult(
        audio_path=output_path,
        duration_ms=_duration_ms(output_path) or length,
        mime_type=_audio_mime_type(fmt),
        model=model_name,
        voice_source=model_name,
        voice_sha256="",
    )
