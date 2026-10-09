"""MiniMax Music 3.0 音乐适配器：五种创作模式 → 上游请求体在这一处收口。

[aipdd fork delta req46] 工具页「音乐创作」的执行叶。上游只有一个端点
``POST /v1/music_generation``（同步，产物是 ``data.audio`` 的 hex 音频），五种模式
的差别全在**字段组合**上，所以模式判定、必填项、长度上限都集中在
:func:`build_music_payload`——提交期就按官方约束拦住，越界直接 400，不等上游回
``2013``（参数错误）再猜是哪里不对。

五种模式（官方能力面，2026-08-20 起付费音乐接口不再面向新用户，界面按账号实际
权限如实报错）：

1. **一句话生成**：只给一句描述，歌词由上游按 ``lyrics_optimizer=true`` 自动写；
2. **歌词生成**：用户自己给歌词（1–3500，支持 ``[Verse]`` / ``[Chorus]`` 等结构
   标签），描述选填；
3. **纯音乐**：``is_instrumental=true``，此时 ``prompt`` 变成必填（1–2000）、
   ``lyrics`` 一律不发送；
4. **翻唱**：翻唱模型 + 参考音频（``audio_url`` 或 ``audio_base64``）+ 目标风格
   描述（10–300），歌词选填（不传由上游 ASR 抽取）；
5. **哼唱**：哼唱录音当参考音走**两步流程**——先
   ``POST /v1/music_cover_preprocess`` 拿 ``cover_feature_id`` +
   ``formatted_lyrics``，用户改词后再带 ``cover_feature_id`` + ``lyrics``
   （10–1000，此时必填）生成。同一段内容上游按 MD5 去重，返回同一个 feature id。

模型名不写死：文本类与翻唱类各来自设置页「音乐」目录（工具页所选条目 / 音乐默认
模型），这里只按名字判定「能不能做翻唱」（含 ``cover`` 的才是翻唱模型），并把
判定结果讲成一句可操作的中文，而不是把厂商型号抄进代码。

参考音频约束（官方）：时长 6 秒–6 分钟、≤50MB、常见格式（mp3/wav/flac）。本地
文件读成 base64，公网直链原样透传；时长用 ffprobe 尽力探测，探测不到（环境无
ffprobe）不拦用户，只按大小与格式判。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from novelvideo.egress_context import TrustedEgressContext
from novelvideo.freezone.minimax_api import (
    PATH_MUSIC_COVER_PREPROCESS,
    PATH_MUSIC_GENERATION,
    MiniMaxChannel,
    MiniMaxError,
    deny_organization_egress,
    encode_file_base64,
    resolve_minimax_channel,
    write_hex_audio,
)
from novelvideo.freezone.paths import outputs_dir

logger = logging.getLogger(__name__)

MUSIC_TASK_TYPE = "freezone_music_generate"

MODE_ONE_SENTENCE = "one_sentence"
MODE_LYRICS = "lyrics"
MODE_INSTRUMENTAL = "instrumental"
MODE_COVER = "cover"
MODE_HUMMING = "humming"
MUSIC_MODES: tuple[str, ...] = (
    MODE_ONE_SENTENCE,
    MODE_LYRICS,
    MODE_INSTRUMENTAL,
    MODE_COVER,
    MODE_HUMMING,
)

MODE_LABELS: dict[str, str] = {
    MODE_ONE_SENTENCE: "一句话生成",
    MODE_LYRICS: "歌词生成",
    MODE_INSTRUMENTAL: "纯音乐",
    MODE_COVER: "翻唱",
    MODE_HUMMING: "哼唱",
}

# 官方 audio_setting 枚举：界面上的下拉只能给这几档，越界在提交期就拦。
SAMPLE_RATES: tuple[int, ...] = (16000, 24000, 32000, 44100)
BITRATES: tuple[int, ...] = (32000, 64000, 128000, 256000)
AUDIO_FORMATS: tuple[str, ...] = ("mp3", "wav", "pcm")
DEFAULT_SAMPLE_RATE = 44100
DEFAULT_BITRATE = 256000
DEFAULT_AUDIO_FORMAT = "mp3"

PROMPT_MAX = 2000
PROMPT_COVER_MIN = 10
PROMPT_COVER_MAX = 300
LYRICS_MAX = 3500
# 翻唱族（含两步改词）的歌词窗口：不传由上游 ASR 抽取，传了就必须落在这个区间。
LYRICS_REF_MIN = 10
LYRICS_REF_MAX = 1000

_REFERENCE_SUFFIXES = {".mp3", ".wav", ".flac"}
_REFERENCE_MAX_BYTES = 50 * 1024 * 1024
_REFERENCE_MIN_SECONDS = 6.0
_REFERENCE_MAX_SECONDS = 360.0

_MIME_BY_FORMAT = {"mp3": "audio/mpeg", "wav": "audio/wav", "pcm": "audio/pcm"}


class MusicRequestInvalid(ValueError):
    """提交期参数越界：路由翻成 400，任务里也不该出现（入队前已拦过一遍）。"""


@dataclass(frozen=True)
class FreezoneMusicResult:
    """一次音乐生成的产物事实，交给 runner 转静态 URL 与归档。"""

    audio_path: Path
    model: str
    provider: str
    mode: str
    mime_type: str
    duration_ms: int
    size_bytes: int
    lyrics: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MusicCoverPreprocess:
    """两步翻唱的第一步结果：特征 id + ASR 抽取的分段歌词（供用户改词）。"""

    cover_feature_id: str
    formatted_lyrics: str
    audio_duration: float
    model: str
    provider: str


def is_cover_model(model: str) -> bool:
    """名字里含 cover 的才算翻唱模型（判定口径见模块 docstring）。"""

    return "cover" in str(model or "").strip().lower()


def clean_format(value: Any) -> str:
    fmt = str(value or "").strip().lower()
    return fmt if fmt in AUDIO_FORMATS else DEFAULT_AUDIO_FORMAT


def music_output_path(project_dir: Path, job_id: str, audio_format: str = "") -> Path:
    """产物落盘位置：``_outputs/freezone_music_generate/{job}.{ext}``。"""

    suffix = {
        "mp3": ".mp3",
        "wav": ".wav",
        "pcm": ".pcm",
    }.get(clean_format(audio_format), ".mp3")
    return outputs_dir(project_dir, MUSIC_TASK_TYPE) / f"{job_id}{suffix}"


def music_output_path_for_job(project_dir: Path, job_id: str) -> Path:
    """结果解析用：不知道用户选的格式，就按 mp3/wav/pcm 依次找已落盘的那一个。

    都找不到时返回 mp3 默认位——调用方只看 ``.exists()``，报错文案不受影响。
    """

    target_dir = outputs_dir(project_dir, MUSIC_TASK_TYPE)
    for fmt in AUDIO_FORMATS:
        candidate = music_output_path(project_dir, job_id, fmt)
        if candidate.exists():
            return candidate
    return target_dir / f"{job_id}.mp3"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MusicRequestInvalid(message)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _probe_duration_seconds_sync(path: Path) -> float | None:
    """尽力探测音频时长：环境没 ffprobe / 文件损坏都返回 ``None``（= 不知道）。

    故意不复用 ``utils.media_io.get_audio_duration``：它探测失败时返回 5.0（一个
    恰好合法的值），会把「测不出」变成静默「放行」（catalog 里的
    ``_probe_reference_audio_seconds`` 同一个推论）。None 与数字分得清，上限才能
    在拦与不拦之间说实话。
    """

    from novelvideo.seedance2_i2v.character_voice_storage import (
        probe_voice_sample_duration_seconds,
    )

    try:
        return float(probe_voice_sample_duration_seconds(path))
    except Exception:  # noqa: BLE001 - 探测工具缺失/超时/坏文件都只是「测不出」
        return None


async def probe_reference_audio_seconds(path: Path) -> float | None:
    """ffprobe 在子线程跑：本函数在异步路由与任务叶上都会被叫到。"""

    return await asyncio.to_thread(_probe_duration_seconds_sync, path)


def validate_reference_audio(
    *,
    audio_url: str = "",
    audio_path: Path | None = None,
    duration_seconds: float | None = None,
) -> None:
    """参考音频约束（官方）：mp3/wav/flac、≤50MB、6 秒–6 分钟。

    ``duration_seconds`` 由调用方先探测再传进来（探测函数是 async，校验保持纯）；
    传 ``None`` = 测不出，只按大小与格式判——环境没 ffprobe 不该把合法素材拦掉。
    公网直链的大小与时长在提交期拿不到，硬拦会误伤；上游报错时原文照透。
    """

    url = _text(audio_url)
    if url:
        return
    _require(audio_path is not None, "缺少参考音频：请上传本地音频或填写公网直链")
    assert audio_path is not None  # noqa: S101 - 上面 _require 已保证
    if not audio_path.is_file():
        raise MusicRequestInvalid(f"参考音频文件不存在或不可读: {audio_path}")
    if audio_path.suffix.lower() not in _REFERENCE_SUFFIXES:
        raise MusicRequestInvalid(
            f"参考音频格式不支持: {audio_path.suffix or '无扩展名'}"
            "（仅支持 mp3 / wav / flac）"
        )
    size = audio_path.stat().st_size
    if size > _REFERENCE_MAX_BYTES:
        raise MusicRequestInvalid(
            f"参考音频过大（{size / 1024 / 1024:.1f}MB），上限 50MB"
        )
    if not size:
        raise MusicRequestInvalid("参考音频是空文件，请重新录制或换一段")
    if (
        duration_seconds is not None
        and duration_seconds > 0
        and (
            duration_seconds < _REFERENCE_MIN_SECONDS
            or duration_seconds > _REFERENCE_MAX_SECONDS
        )
    ):
        raise MusicRequestInvalid(
            f"参考音频时长 {duration_seconds:.1f} 秒超出允许范围（6 秒–6 分钟）"
        )


def reference_audio_fields(
    *, audio_url: str = "", audio_path: Path | None = None
) -> dict[str, str]:
    """参考音频 → 上游字段：公网直链走 ``audio_url``，本地文件走 ``audio_base64``。"""

    url = _text(audio_url)
    if url:
        return {"audio_url": url}
    assert audio_path is not None and audio_path.is_file(), "参考音频不可用"
    return {"audio_base64": encode_file_base64(audio_path)}


def build_music_payload(
    *,
    mode: str,
    model: str,
    prompt: str = "",
    lyrics: str = "",
    has_reference: bool = False,
    cover_feature_id: str = "",
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    bitrate: int = DEFAULT_BITRATE,
    audio_format: str = DEFAULT_AUDIO_FORMAT,
) -> dict[str, Any]:
    """模式 → 请求体的唯一收口点：必填、长度、互斥都在这里判。

    ``has_reference`` / ``cover_feature_id`` 只影响翻唱族模式的取数来源；
    实际 ``audio_url`` / ``audio_base64`` 字段由调用方经
    :func:`reference_audio_fields` 合并进来（保持本函数是纯函数，好测）。
    """

    clean_mode = _text(mode)
    if clean_mode not in MUSIC_MODES:
        raise MusicRequestInvalid(
            f"未知的音乐创作模式: {clean_mode or '未指定'}"
            f"（可用: {', '.join(MUSIC_MODES)}）"
        )
    clean_model = _text(model)
    _require(bool(clean_model), "缺少音乐模型：请在设置页配置音乐模型或在选择器里选一个")

    clean_prompt = _text(prompt)
    clean_lyrics = _text(lyrics)
    feature_id = _text(cover_feature_id)
    payload: dict[str, Any] = {"model": clean_model}

    if clean_mode in {MODE_COVER, MODE_HUMMING}:
        _require(
            is_cover_model(clean_model),
            f"{MODE_LABELS[clean_mode]}需要翻唱音乐模型（模型名含 cover）："
            f"当前所选 {clean_model} 只能做文本生成音乐，请在设置 › 音乐里改选翻唱模型",
        )
        _require(
            PROMPT_COVER_MIN <= len(clean_prompt) <= PROMPT_COVER_MAX,
            f"{MODE_LABELS[clean_mode]}需要提供目标风格描述"
            f"（{PROMPT_COVER_MIN}–{PROMPT_COVER_MAX} 字），当前 {len(clean_prompt)} 字",
        )
        if feature_id:
            # 两步流程第二步：有了特征 id 就不能再带参考音频，歌词转为必填。
            _require(
                not has_reference,
                "已经拿到翻唱特征 id（cover_feature_id），不要再重复提供参考音频",
            )
            _require(
                LYRICS_REF_MIN <= len(clean_lyrics) <= LYRICS_REF_MAX,
                f"两步翻唱的改词长度需在 {LYRICS_REF_MIN}–{LYRICS_REF_MAX} 字之间，"
                f"当前 {len(clean_lyrics)} 字",
            )
            payload["cover_feature_id"] = feature_id
            payload["lyrics"] = clean_lyrics
        else:
            _require(
                has_reference,
                f"{MODE_LABELS[clean_mode]}需要一段参考音频（上传或公网直链）",
            )
            if clean_lyrics:
                _require(
                    LYRICS_REF_MIN <= len(clean_lyrics) <= LYRICS_REF_MAX,
                    f"翻唱歌词长度需在 {LYRICS_REF_MIN}–{LYRICS_REF_MAX} 字之间，"
                    f"当前 {len(clean_lyrics)} 字",
                )
                payload["lyrics"] = clean_lyrics
        payload["prompt"] = clean_prompt
        return _with_audio_setting(
            payload,
            sample_rate=sample_rate,
            bitrate=bitrate,
            audio_format=audio_format,
        )

    _require(
        not is_cover_model(clean_model),
        f"{MODE_LABELS[clean_mode]}需要文本生成音乐模型，当前所选 {clean_model} "
        "是翻唱模型（仅用于翻唱/哼唱）",
    )
    if len(clean_prompt) > PROMPT_MAX:
        raise MusicRequestInvalid(
            f"音乐描述过长（{len(clean_prompt)} 字），上限 {PROMPT_MAX} 字"
        )

    if clean_mode == MODE_INSTRUMENTAL:
        _require(
            1 <= len(clean_prompt) <= PROMPT_MAX,
            f"纯音乐必须提供描述（1–{PROMPT_MAX} 字），说清风格/乐器/节奏/氛围",
        )
        payload["prompt"] = clean_prompt
        payload["is_instrumental"] = True
        # 纯音乐不发送 lyrics：上游在该模式下不需要词，带上去只会引来意外人声。
        return _with_audio_setting(
            payload,
            sample_rate=sample_rate,
            bitrate=bitrate,
            audio_format=audio_format,
        )

    if clean_mode == MODE_ONE_SENTENCE:
        _require(
            1 <= len(clean_prompt) <= PROMPT_MAX,
            f"一句话生成需要一句描述（1–{PROMPT_MAX} 字），"
            "例如「轻快的流行摇滚，副歌开阔」",
        )
        payload["prompt"] = clean_prompt
        payload["lyrics_optimizer"] = True
        # 词由上游按描述生成：这里刻意不带 lyrics，否则 optimizer 不生效。
        return _with_audio_setting(
            payload,
            sample_rate=sample_rate,
            bitrate=bitrate,
            audio_format=audio_format,
        )

    # 歌词生成：词必填（1–3500，可含结构标签），描述选填。
    _require(
        1 <= len(clean_lyrics) <= LYRICS_MAX,
        f"歌词生成需要提供歌词（1–{LYRICS_MAX} 字，可用 [Verse] / [Chorus] "
        f"等结构标签分段），当前 {len(clean_lyrics)} 字",
    )
    payload["lyrics"] = clean_lyrics
    if clean_prompt:
        payload["prompt"] = clean_prompt
    return _with_audio_setting(
        payload,
        sample_rate=sample_rate,
        bitrate=bitrate,
        audio_format=audio_format,
    )


def _with_audio_setting(
    payload: dict[str, Any],
    *,
    sample_rate: int,
    bitrate: int,
    audio_format: str,
) -> dict[str, Any]:
    if sample_rate and sample_rate not in SAMPLE_RATES:
        raise MusicRequestInvalid(
            f"采样率 {sample_rate} 不在允许档位（{', '.join(str(v) for v in SAMPLE_RATES)}）"
        )
    if bitrate and bitrate not in BITRATES:
        raise MusicRequestInvalid(
            f"码率 {bitrate} 不在允许档位（{', '.join(str(v) for v in BITRATES)}）"
        )
    fmt = clean_format(audio_format)
    payload["output_format"] = "hex"
    payload["audio_setting"] = {
        "format": fmt,
        "sample_rate": sample_rate or DEFAULT_SAMPLE_RATE,
        "bitrate": bitrate or DEFAULT_BITRATE,
    }
    return payload


def resolved_music_channel(provider: str = "") -> MiniMaxChannel:
    """站点渠道：payload 指定优先，未指定按设置里已配置的渠道兜底。"""

    return resolve_minimax_channel(provider)


async def cover_preprocess(
    *,
    model: str,
    provider: str = "",
    audio_url: str = "",
    audio_path: Path | None = None,
    egress_context: TrustedEgressContext | None = None,
) -> MusicCoverPreprocess:
    """两步翻唱第一步：抽特征 + ASR 歌词（哼唱模式的「先出词、再改词」入口）。"""

    deny_organization_egress(egress_context, "音乐翻唱预处理")
    clean_model = _text(model)
    _require(bool(clean_model), "缺少翻唱音乐模型")
    _require(
        is_cover_model(clean_model),
        f"翻唱预处理需要翻唱音乐模型（模型名含 cover），当前所选 {clean_model} 不支持",
    )
    validate_reference_audio(
        audio_url=audio_url,
        audio_path=audio_path,
        duration_seconds=await _reference_duration(audio_url, audio_path),
    )
    channel = resolved_music_channel(provider)
    payload: dict[str, Any] = {
        "model": clean_model,
        **reference_audio_fields(audio_url=audio_url, audio_path=audio_path),
    }
    body = await _post(channel, PATH_MUSIC_COVER_PREPROCESS, payload)
    feature_id = _text(body.get("cover_feature_id"))
    if not feature_id:
        raise MiniMaxError(
            f"MiniMax 翻唱预处理没有返回 cover_feature_id：{_brief(body)}",
            endpoint=PATH_MUSIC_COVER_PREPROCESS,
        )
    duration_raw = body.get("audio_duration")
    try:
        duration = float(duration_raw or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return MusicCoverPreprocess(
        cover_feature_id=feature_id,
        formatted_lyrics=str(body.get("formatted_lyrics") or ""),
        audio_duration=duration,
        model=clean_model,
        provider=channel.provider,
    )


async def _post(channel: MiniMaxChannel, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    from novelvideo.freezone import minimax_api as _api

    return await _api.post_json(channel, path, payload)


async def _reference_duration(
    audio_url: str, audio_path: Path | None
) -> float | None:
    """本地参考音频先探一次时长再交给校验（公网直链不探，提交期拿不到）。"""

    if _text(audio_url) or audio_path is None:
        return None
    return await probe_reference_audio_seconds(audio_path)


def _brief(body: Any) -> str:
    import json

    try:
        return json.dumps(body, ensure_ascii=False)[:300]
    except Exception:  # noqa: BLE001 - 报错文案不能再抛异常
        return str(body)[:300]


async def generate_music(
    *,
    channel: MiniMaxChannel,
    payload: dict[str, Any],
    output_path: Path,
) -> tuple[int, dict[str, Any]]:
    """POST 音乐生成并把 hex 产物落盘；返回 ``(时长毫秒, extra_info)``。"""

    body = await _post(channel, PATH_MUSIC_GENERATION, payload)
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    audio_hex = _text(data.get("audio"))
    if not audio_hex:
        # 同步接口正常就该给 hex；没给就是上游变了形态，原文透出别装作成功。
        raise MiniMaxError(
            f"MiniMax 音乐生成没有返回音频数据（data.audio 为空）：{_brief(body)}",
            endpoint=PATH_MUSIC_GENERATION,
        )
    await write_hex_audio(audio_hex, output_path, endpoint=PATH_MUSIC_GENERATION)
    extra = body.get("extra_info") if isinstance(body.get("extra_info"), dict) else {}
    try:
        duration_ms = int(extra.get("music_duration") or 0)
    except (TypeError, ValueError):
        duration_ms = 0
    return duration_ms, extra


async def run_freezone_music_generate(
    *,
    project_dir: Path,
    job_id: str,
    mode: str,
    model: str,
    provider: str = "",
    prompt: str = "",
    lyrics: str = "",
    audio_url: str = "",
    audio_path: Path | None = None,
    cover_feature_id: str = "",
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    bitrate: int = DEFAULT_BITRATE,
    audio_format: str = DEFAULT_AUDIO_FORMAT,
    egress_context: TrustedEgressContext | None = None,
) -> FreezoneMusicResult:
    """执行叶：模式校验 → （哼唱）先预处理 → 音乐生成 → hex 落盘。

    出网说明（``FREEZONE_LEAF_EGRESS`` 记为 NETWORK）：直连使用平台侧配置的
    MiniMax 渠道凭据，组织任务在叶内 fail-closed（与 MediaKit / 豆包语音同一
    取舍：不得用平台凭据直连第三方主机）。
    """

    deny_organization_egress(egress_context, "音乐生成")
    clean_mode = _text(mode)
    clean_model = _text(model)
    feature_id = _text(cover_feature_id)
    has_reference = bool(_text(audio_url)) or audio_path is not None
    used_lyrics = _text(lyrics)
    if has_reference:
        validate_reference_audio(
            audio_url=audio_url,
            audio_path=audio_path,
            duration_seconds=await _reference_duration(audio_url, audio_path),
        )

    if clean_mode == MODE_HUMMING and not feature_id and has_reference:
        # 哼唱走两步：先抽特征与 ASR 词，再用（可能被用户改过的）词生成。
        # 带 cover_feature_id 进来时不再重复预处理——同一段内容上游按 MD5 复用
        # 同一个特征 id，多打一次只是白耗一次上游调用。
        preprocessed = await cover_preprocess(
            model=clean_model,
            provider=provider,
            audio_url=audio_url,
            audio_path=audio_path,
            egress_context=egress_context,
        )
        feature_id = preprocessed.cover_feature_id
        used_lyrics = used_lyrics or preprocessed.formatted_lyrics

    payload = build_music_payload(
        mode=clean_mode,
        model=clean_model,
        prompt=prompt,
        lyrics=used_lyrics,
        has_reference=has_reference and not feature_id,
        cover_feature_id=feature_id,
        sample_rate=sample_rate,
        bitrate=bitrate,
        audio_format=audio_format,
    )
    if not feature_id and clean_mode in {MODE_COVER, MODE_HUMMING}:
        payload.update(
            reference_audio_fields(audio_url=audio_url, audio_path=audio_path)
        )

    channel = resolved_music_channel(provider)
    output_path = music_output_path(project_dir, job_id, audio_format)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration_ms, extra = await generate_music(
        channel=channel, payload=payload, output_path=output_path
    )
    if not duration_ms:
        # 上游没给时长就本地探测：任务结果里的时长不能是假的 0。
        probed = await probe_reference_audio_seconds(output_path)
        duration_ms = int(probed * 1000) if probed else 0
    fmt = clean_format(audio_format)
    logger.info(
        "freezone music generated: mode=%s provider=%s job=%s bytes=%s",
        clean_mode,
        channel.provider,
        job_id,
        output_path.stat().st_size,
    )
    return FreezoneMusicResult(
        audio_path=output_path,
        model=clean_model,
        provider=channel.provider,
        mode=clean_mode,
        mime_type=_MIME_BY_FORMAT.get(fmt, "audio/mpeg"),
        duration_ms=duration_ms,
        size_bytes=int(output_path.stat().st_size),
        lyrics=used_lyrics,
        extra=extra,
    )


__all__ = [
    "AUDIO_FORMATS",
    "BITRATES",
    "MUSIC_MODES",
    "MUSIC_TASK_TYPE",
    "MODE_COVER",
    "MODE_HUMMING",
    "MODE_INSTRUMENTAL",
    "MODE_LABELS",
    "MODE_LYRICS",
    "MODE_ONE_SENTENCE",
    "FreezoneMusicResult",
    "MusicCoverPreprocess",
    "MusicRequestInvalid",
    "SAMPLE_RATES",
    "build_music_payload",
    "cover_preprocess",
    "generate_music",
    "is_cover_model",
    "music_output_path",
    "music_output_path_for_job",
    "probe_reference_audio_seconds",
    "reference_audio_fields",
    "resolved_music_channel",
    "run_freezone_music_generate",
    "validate_reference_audio",
]
