"""IndexTTS2 client for Seedance 2.0 dialogue audio preparation."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from novelvideo.media_archive_copy import copy_archived_result

from novelvideo.ports import get_usage_meter, update_current_model_call_log
from novelvideo.shared.upstream_quota_errors import is_fatal_upstream_quota_error
from novelvideo.egress_context import (
    TrustedEgressContext,
    ambient_organization_egress_context,
)
from novelvideo.generators.audio_gateway import (
    AudioGatewaySession,
    begin_trusted_audio_operation,
    is_organization_egress_context,
)
from novelvideo.generators.tts_generator import (
    TTSResult,
    complete_audio_operation,
    mark_audio_operation_unknown,
)


async def record_tts_model_call(
    *,
    model: str,
    provider_request_id: str = "",
    response_id: str = "",
) -> None:
    """[aipdd fork delta] WP-D: 只记一次上游调用，不做预约 / 退款。

    公开命名（无下划线）：freezone audio 节点跨模块复用同一观察位。
    """
    try:
        await get_usage_meter().bump_model_call(
            user_id=None,
            model=model,
            provider_request_id=provider_request_id,
            metadata={"response_id": response_id} if response_id else None,
        )
    except Exception:
        pass


def _extract_audio_url(payload: dict[str, Any]) -> str:
    audio = payload.get("audio")
    if isinstance(audio, str):
        return audio.strip()
    if isinstance(audio, dict):
        return str(audio.get("url") or "").strip()
    return ""


async def _audio_duration_seconds(audio_path: Path) -> float:
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
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


# [aipdd fork delta] AIPDD 原生音频任务协议（异步任务式）：与视频侧
# agent/gateway.py 同一轮询节奏（5s × 120 = 10 分钟）。
AIPDD_AUDIO_POLL_INTERVAL_SECONDS = 5.0
AIPDD_AUDIO_MAX_POLLS = 120

_AIPDD_AUDIO_FAILURE_STATUSES = {
    "failed",
    "failure",
    "error",
    "cancelled",
    "canceled",
}

_AIPDD_AUDIO_SOURCE_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac"}


def _audio_speech_suffix(model: str) -> str:
    """[aipdd fork delta req41] 语音合成的相对路径：先问渠道目录。

    AIPDD 在条目里声明了 ``audio/speech``（还额外声明了带任务号的子路径，那是
    轮询用的，不能拿来提交）；目录没说话时回到 OpenAI 通用的 ``audio/speech``。
    轮询拼的是 ``{endpoint}/{task_id}``，所以两条地址会跟着声明一起走。
    """
    from novelvideo.model_gateway_settings import catalog_endpoint_path
    from novelvideo.catalog.upstream_catalog import EP_AUDIO_SPEECH

    return (
        catalog_endpoint_path(str(model or ""), EP_AUDIO_SPEECH)
        or EP_AUDIO_SPEECH
    )


def _uses_aipdd_audio_api(base_url: str, model: str = "") -> bool:
    """[aipdd fork delta] 音频是否走 ``/audio/speech`` 的异步任务式原生协议。

    [aipdd fork delta req41] 与视频侧同一口径：先问渠道目录。上游把音频写成
    「提交 + 任务轮询」时会在 ``endpoints`` 里额外声明 ``audio/speech/{task_id}``
    这类子路径（AIPDD 就是这么声明的），命中即启用该协议；目录没说话才按 host
    子串兜底（默认 aipdd，可用 ``AIPDD_AUDIO_API_HOST_HINT`` 覆盖）。
    """
    from novelvideo.model_gateway_settings import catalog_endpoints
    from novelvideo.catalog.upstream_catalog import EP_AUDIO_SPEECH

    declared = catalog_endpoints(model) if str(model or "").strip() else []
    if declared:
        prefix = EP_AUDIO_SPEECH + "/"
        return any(str(path).startswith(prefix) for path in declared)
    hint = (os.environ.get("AIPDD_AUDIO_API_HOST_HINT") or "aipdd").strip().lower()
    if not hint:
        return False
    host = (urlsplit(str(base_url or "")).hostname or "").lower()
    return hint in host


def _resolve_audio_direct_channel(model: str) -> tuple[str, str] | None:
    """[aipdd fork delta] 选中语音模型绑定的 provider 渠道 ``(base, key)``。

    与视频/图片侧同一运行时入口 ``resolve_media_provider_override``：命中绑定
    渠道时逐组件直连；未命中返回 ``None``，调用方沿用生效网关兜底。
    """
    model_name = str(model or "").strip()
    if not model_name:
        return None
    try:
        from novelvideo.model_gateway_settings import (
            resolve_media_provider_override,
        )

        override = resolve_media_provider_override(model_name, media_type="audio")
    except Exception:
        return None
    if not override:
        return None
    base_url, api_key, _provider = override
    base_url = str(base_url or "").strip().rstrip("/")
    api_key = str(api_key or "").strip()
    if not base_url or not api_key:
        return None
    return base_url, api_key


def _resolve_audio_route(model: str) -> tuple[str, tuple[str, str] | None]:
    """[aipdd fork delta req40] 解析 ``(请求模型名, 直连渠道)``。

    调用名在构造阶段就已经定死：要么调用方显式传，要么从「设置 → 模型与渠道」的默认音频位
    解析出来（那一位的名字必然来自渠道目录）。这里只判断它有没有自带 base+key 的
    provider 渠道 —— 命中就逐组件直连，没命中沿用生效网关。旧的「内置默认名 → 面板
    默认条目」二次回退随硬编码名一起删除：代码里已经没有默认名可比了。
    """
    model_name = str(model or "").strip()
    return model_name, _resolve_audio_direct_channel(model_name)


async def _transcode_audio_to_mp3(source_path: Path, output_path: Path) -> None:
    """把上游交付的非 mp3 音频转成 mp3（与 freezone 分离音频同一编码约定）。"""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH; install via brew/apt")
    completed = await asyncio.to_thread(
        subprocess.run,
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source_path),
            "-vn",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(output_path),
        ],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"ffmpeg audio transcode failed: {str(completed.stderr or '')[-300:]}"
        )


class IndexTTS2Client:
    """IndexTTS2 client for ``audio/speech`` synthesis from a reference sample.

    默认经生效网关（LocalAPI）调用；模型绑定到自带 base+key 的 provider 渠道
    （如 aipdd）且未显式传凭据时按渠道路由——aipdd host 走原生异步任务协议
    （提交 → 轮询 → 下载 → 转 mp3），其余渠道保持 OpenAI 兼容协议。
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        endpoint: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        egress_context: TrustedEgressContext | None = None,
    ):
        from novelvideo.config import (
            INDEXTTS2_TIMEOUT_SECONDS,
            get_effective_newapi_gateway_config,
        )

        if egress_context is None:
            egress_context = ambient_organization_egress_context()
        self.egress_context = egress_context
        # [aipdd fork delta] 受信上下文一律不读平台网关配置：组织模式的凭据只能
        # 经租约解析（begin_trusted_audio_operation），非组织的受信上下文在
        # generate() 里先行 ORG_EGRESS_DENIED 拒绝。只有完全无上下文的直连模式
        # 才回退平台 api_key / base_url；且构造时显式传了凭据即整体覆盖渠道
        # 解析（与视频侧 backends/newapi.py 的 `api_key is None and not endpoint`
        # 判定同语义）。
        self._explicit_credentials = api_key is not None or endpoint is not None
        if type(egress_context) is TrustedEgressContext:
            self.api_key = ""
            self.endpoint = ""
        else:
            gateway = get_effective_newapi_gateway_config()
            self.api_key = api_key if api_key is not None else gateway.api_key
            self.endpoint = endpoint or gateway.base_url
        clean_model = str(model or "").strip()
        if not clean_model:
            # [aipdd fork delta req40] 代码里不再有内置音频模型名：没显式指定就必须
            # 从「设置 → 模型与渠道」的默认音频位解析出来（渠道目录内）。解析不到时构造阶段
            # 直接抛 ModelSlotNotConfigured —— 提交前拒绝，不会带着空模型名打到网关。
            from novelvideo.model_slots import resolve_media_slot

            clean_model = resolve_media_slot("audio")
        self.model = clean_model
        self.timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else INDEXTTS2_TIMEOUT_SECONDS
        )
        self._last_provider_request_id = ""
        self._last_provider_response_id = ""

    async def generate(
        self,
        *,
        prompt: str,
        audio_url: str,
        output_path: str | Path,
        emotion_prompt: str = "",
    ) -> TTSResult:
        """Generate dialogue audio from a reference sample and save it to ``output_path``.

        契约（失败面与既有行为逐字一致）：
        - 非组织受信上下文先于任何输入校验返回 ``ORG_EGRESS_DENIED``；
        - 无上下文的直连模式缺 key 返回 ``DramaClawAPI API key not set``；
        - 组织模式凭据只来自租约解析，平台配置绝不参与；
        - 已结算请求（重放）返回 ``audio operation already handled`` 且不再提交。
        """
        context = self.egress_context
        if context is not None and not is_organization_egress_context(context):
            return TTSResult(success=False, error="ORG_EGRESS_DENIED")
        if context is None and not self.api_key:
            return TTSResult(success=False, error="DramaClawAPI API key not set")
        prompt = str(prompt or "").strip()
        if not prompt:
            return TTSResult(success=False, error="IndexTTS2 prompt is empty")
        audio_url = str(audio_url or "").strip()
        if not audio_url:
            return TTSResult(success=False, error="IndexTTS2 audio_url is empty")

        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        session = AudioGatewaySession()
        if context is not None:
            session = await begin_trusted_audio_operation(
                context,
                capability="audio.tts.gateway",
                business_task_id=f"{context.task_type}:indextts2:{target.name}",
                request={
                    "model": self.model,
                    "prompt": prompt,
                    "audio_url": audio_url,
                    "emotion_prompt": str(emotion_prompt or "").strip(),
                },
            )
            if session.denial_code is not None:
                return TTSResult(success=False, error=session.denial_code)
        lease = session.lease
        request_credential = session.credential
        self._last_provider_request_id = ""
        self._last_provider_response_id = ""
        self._last_provider_response_payload: dict[str, Any] = {}
        # [aipdd fork delta] WP-D: 预约已拆除；这里只剩请求载荷的可观测性写入，
        # 它自身会吞异常，不能反过来决定任务成败。
        await update_current_model_call_log(
            request_payload={
                "model": self.model,
                "input": prompt,
                "metadata": {
                    "audio_url": audio_url,
                    "should_use_prompt_for_emotion": True,
                    **(
                        {"emotion_prompt": str(emotion_prompt).strip()}
                        if str(emotion_prompt or "").strip()
                        else {}
                    ),
                },
            },
        )

        try:
            result = await self._generate_via_newapi(
                prompt=prompt,
                audio_url=audio_url,
                output_path=target,
                emotion_prompt=emotion_prompt,
                api_key=(request_credential.api_key if request_credential else None),
                endpoint=(request_credential.base_url if request_credential else None),
            )
        except BaseException:
            if lease is not None:
                await mark_audio_operation_unknown(lease)
            raise
        if result.success:
            await update_current_model_call_log(
                response_payload=self._last_provider_response_payload,
            )
            if lease is not None:
                with target.open("rb") as audio_file:
                    digest = hashlib.file_digest(audio_file, "sha256").hexdigest()
                await complete_audio_operation(
                    lease,
                    result_ref=f"audio:sha256:{digest}",
                )
            await record_tts_model_call(
                model=self.model,
                provider_request_id=self._last_provider_request_id,
                response_id=self._last_provider_response_id,
            )
        else:
            await update_current_model_call_log(
                response_payload=self._last_provider_response_payload,
                error_message="tts_generation_failed",
            )
            if lease is not None:
                await mark_audio_operation_unknown(lease)
        return result

    async def _generate_via_newapi(
        self,
        *,
        prompt: str,
        audio_url: str,
        output_path: Path,
        emotion_prompt: str = "",
        api_key: str | None = None,
        endpoint: str | None = None,
    ) -> TTSResult:
        request_endpoint = str(endpoint or self.endpoint or "").rstrip("/")
        if not request_endpoint.endswith(f"/{_audio_speech_suffix(self.model)}"):
            request_endpoint = f"{request_endpoint}/{_audio_speech_suffix(self.model)}"
        request_api_key = api_key if api_key is not None else self.api_key
        request_model = self.model

        # [aipdd fork delta] 多网关并存 + aipdd 直连：无显式凭据（直连模式、
        # 未走租约）时按选中模型绑定的 provider 渠道路由。aipdd 渠道的
        # /audio/speech 原生协议是异步任务式；且 LocalAPI 代理会把 OpenAI
        # 风格音频请求 DTO 化重转发、丢掉参考音频与情绪字段（上游 400
        # 「缺少必填参数：audio」），故命中 aipdd host 时改走直连原生协议。
        if (
            api_key is None
            and not endpoint
            and not self._explicit_credentials
            and type(self.egress_context) is not TrustedEgressContext
        ):
            request_model, override = _resolve_audio_route(self.model)
            if override is not None:
                direct_base, direct_key = override
                if _uses_aipdd_audio_api(direct_base, request_model):
                    return await self._generate_via_aipdd_direct(
                        prompt=prompt,
                        audio_url=audio_url,
                        output_path=output_path,
                        base_url=direct_base,
                        api_key=direct_key,
                        emotion_prompt=emotion_prompt,
                        model=request_model,
                    )
                request_endpoint = direct_base
                speech_suffix = _audio_speech_suffix(request_model)
                if not request_endpoint.endswith(f"/{speech_suffix}"):
                    request_endpoint = f"{request_endpoint}/{speech_suffix}"
                request_api_key = direct_key

        metadata: dict[str, Any] = {
            "audio_url": audio_url,
            "should_use_prompt_for_emotion": True,
        }
        if str(emotion_prompt or "").strip():
            metadata["emotion_prompt"] = str(emotion_prompt).strip()
        body: dict[str, Any] = {
            "model": request_model,
            "input": prompt,
            "metadata": metadata,
        }

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, follow_redirects=True
            ) as client:
                response = await client.post(
                    request_endpoint,
                    headers={
                        "Authorization": f"Bearer {request_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                self._last_provider_request_id = (
                    response.headers.get("x-request-id")
                    or response.headers.get("x-newapi-request-id")
                    or response.headers.get("x-oneapi-request-id")
                    or ""
                )
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if "application/json" in content_type.lower():
                    payload = response.json()
                    self._last_provider_response_payload = payload
                    self._last_provider_request_id = (
                        self._last_provider_request_id
                        or str(
                            payload.get("request_id") or payload.get("requestId") or ""
                        ).strip()
                    )
                    self._last_provider_response_id = str(
                        payload.get("id") or ""
                    ).strip()
                    result_url = _extract_audio_url(payload)
                    if not result_url:
                        return TTSResult(
                            success=False,
                            error="DramaClawAPI IndexTTS2 response missing audio bytes or URL",
                        )
                    if not await copy_archived_result(
                        payload.get("archive"), output_path
                    ):
                        audio_response = await client.get(result_url)
                        audio_response.raise_for_status()
                        output_path.write_bytes(audio_response.content)
                else:
                    self._last_provider_response_payload = {
                        "content_type": content_type,
                        "content_length": len(response.content),
                    }
                    output_path.write_bytes(response.content)

            if not output_path.exists() or output_path.stat().st_size <= 0:
                return TTSResult(
                    success=False, error="IndexTTS2 audio file was not created"
                )

            return TTSResult(
                success=True,
                audio_path=str(output_path),
                duration_seconds=await _audio_duration_seconds(output_path),
            )
        except Exception as exc:
            if is_fatal_upstream_quota_error(exc):
                raise
            return TTSResult(
                success=False, error=f"{exc.__class__.__name__}: LocalAPI audio failed"
            )

    async def _generate_via_aipdd_direct(
        self,
        *,
        prompt: str,
        audio_url: str,
        output_path: Path,
        base_url: str,
        api_key: str,
        emotion_prompt: str = "",
        model: str = "",
    ) -> TTSResult:
        """[aipdd fork delta] AIPDD 原生音频协议（异步任务式）。

        实测契约：POST ``{base}/audio/speech``（顶层 ``audio`` = 参考音频
        data URL）→ 202 任务 id → 轮询 GET ``{base}/audio/speech/{id}`` →
        ``content_url``（上游实测交付 .wav，response_format 被忽略）→ 下载并
        统一转 mp3（与 freezone 分离音频同一编码约定）。
        """
        endpoint = f"{str(base_url or '').rstrip('/')}/{_audio_speech_suffix(model or self.model)}"
        metadata: dict[str, Any] = {"should_use_prompt_for_emotion": True}
        if str(emotion_prompt or "").strip():
            metadata["emotion_prompt"] = str(emotion_prompt).strip()
        body: dict[str, Any] = {
            "model": model or self.model,
            "input": prompt,
            "response_format": "mp3",
            "audio": audio_url,
            "metadata": metadata,
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, follow_redirects=True
            ) as client:
                response = await client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                self._last_provider_request_id = (
                    response.headers.get("x-request-id")
                    or response.headers.get("x-newapi-request-id")
                    or response.headers.get("x-oneapi-request-id")
                    or ""
                )
                response.raise_for_status()
                payload = response.json()
                self._last_provider_response_payload = (
                    payload if isinstance(payload, dict) else {"payload": payload}
                )
                task_id = ""
                if isinstance(payload, dict):
                    task_id = str(payload.get("id") or "").strip()
                    self._last_provider_request_id = (
                        self._last_provider_request_id
                        or str(
                            payload.get("request_id") or payload.get("requestId") or ""
                        ).strip()
                    )
                if not task_id:
                    return TTSResult(
                        success=False,
                        error="DramaClawAPI AIPDD audio response missing task id",
                    )
                self._last_provider_response_id = task_id

                content_url = ""
                for _ in range(AIPDD_AUDIO_MAX_POLLS):
                    await asyncio.sleep(AIPDD_AUDIO_POLL_INTERVAL_SECONDS)
                    try:
                        poll_response = await client.get(
                            f"{endpoint}/{task_id}",
                            headers={"Authorization": f"Bearer {api_key}"},
                            timeout=60.0,
                        )
                    except httpx.HTTPError:
                        continue  # 轮询瞬时错误容忍，等待下一轮
                    if poll_response.status_code >= 400:
                        continue
                    try:
                        task = poll_response.json()
                    except ValueError:
                        continue
                    if not isinstance(task, dict):
                        continue
                    self._last_provider_response_payload = task
                    status = str(task.get("status") or "").strip().lower()
                    content_url = str(task.get("content_url") or "").strip()
                    if status in _AIPDD_AUDIO_FAILURE_STATUSES:
                        return TTSResult(
                            success=False,
                            error=f"AIPDD audio task failed: {status}",
                        )
                    if content_url:
                        break
                if not content_url:
                    return TTSResult(
                        success=False,
                        error=f"AIPDD audio task timed out: task_id={task_id}",
                    )

                audio_response = await client.get(content_url)
                audio_response.raise_for_status()
                content_type = (
                    audio_response.headers.get("content-type") or ""
                ).lower()
                if "mpeg" in content_type or "mp3" in content_type:
                    output_path.write_bytes(audio_response.content)
                else:
                    suffix = Path(urlsplit(content_url).path).suffix.lower()
                    if suffix not in _AIPDD_AUDIO_SOURCE_SUFFIXES:
                        suffix = ".wav"
                    source_path = output_path.with_name(
                        f"{output_path.stem}.aipdd-src{suffix}"
                    )
                    try:
                        source_path.write_bytes(audio_response.content)
                        await _transcode_audio_to_mp3(source_path, output_path)
                    finally:
                        with contextlib.suppress(OSError):
                            source_path.unlink()

            if not output_path.exists() or output_path.stat().st_size <= 0:
                return TTSResult(
                    success=False, error="IndexTTS2 audio file was not created"
                )

            return TTSResult(
                success=True,
                audio_path=str(output_path),
                duration_seconds=await _audio_duration_seconds(output_path),
            )
        except Exception as exc:
            if is_fatal_upstream_quota_error(exc):
                raise
            detail = str(exc).strip()[:200]
            return TTSResult(
                success=False,
                error=(
                    f"{exc.__class__.__name__}: AIPDD audio failed ({detail})"
                    if detail
                    else f"{exc.__class__.__name__}: AIPDD audio failed"
                ),
            )
