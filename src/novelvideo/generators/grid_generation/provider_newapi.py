"""NewAPI 图片 API 调用（含组织出网与参考图 relay）（B3 拆包自单文件 grid_generation.py）。"""

import asyncio
import base64
import hashlib
from pathlib import Path
from typing import Callable

from novelvideo.ports import update_current_model_call_log
from novelvideo.egress_context import (
    TrustedEgressContext,
    ambient_organization_egress_context,
)
from novelvideo.ports.egress import EgressError
from novelvideo.ports.egress_operations import canonical_request_digest
from novelvideo.shared import openai_compat
from novelvideo.shared.upstream_quota_errors import is_fatal_upstream_quota_error
from novelvideo.storage.media_relay import IMAGE_TRANSFORM_AI_REFERENCE_JPEG

from .egress import (
    _validate_egress_context,
    _newapi_request_id_from_headers,
    _complete_organization_image_egress,
)
from .image_sizes import (
    normalize_image_size,
    resolve_openai_image_size,
    _legacy_newapi_image_quality_default,
)
from .constants import logger, NEWAPI_MEDIA_INPUT_MIN_TTL_SECONDS
from .aipdd_geometry import _aipdd_image_geometry_endpoint, _apply_aipdd_image_geometry
from .newapi_protocol import (
    _newapi_safe_request_context,
    NEWAPI_IMAGE_HTTP_TIMEOUT_SECONDS,
    NEWAPI_IMAGE_MAX_ATTEMPTS,
    _is_newapi_transient_image_status,
    NEWAPI_IMAGE_RETRY_BASE_SECONDS,
    _newapi_image_output,
    _newapi_image_status,
    NEWAPI_IMAGE_MAX_POLLS,
    _NEWAPI_IMAGE_FAILURE_STATUSES,
    _newapi_image_error,
    _NEWAPI_IMAGE_SUCCESS_STATUSES,
    _NEWAPI_IMAGE_PENDING_STATUSES,
    NEWAPI_IMAGE_POLL_INTERVAL_SECONDS,
    _newapi_safe_header_summary,
    _newapi_context_for_error,
)

from novelvideo.generators import grid_generation as _gg


async def _call_newapi_image_api(
    *,
    api_key: str,
    model: str,
    prompt: str,
    reference_images: (
        list[bytes | tuple[bytes, str] | tuple[str, bytes, str]] | None
    ) = None,
    image_config: dict | None = None,
    base_url: str | None = None,
    trace: dict[str, str] | None = None,
    egress_context: TrustedEgressContext | None = None,
    delivery_path: str | Path | None = None,
    delivery_state: dict[str, bool | str] | None = None,
    before_delivery_copy: Callable[[], None] | None = None,
    read_copied_bytes: bool = True,
) -> tuple[bytes | None, str, str]:
    """Call newAPI's OpenAI-compatible Images API."""
    import httpx

    context = _validate_egress_context(egress_context)
    if not api_key:
        return None, "", "DramaClawAPI API key is missing"

    image_config = image_config or {}
    aspect_ratio = str(image_config.get("aspect_ratio") or "1:1").strip().lower() or "1:1"
    image_size = normalize_image_size(str(image_config.get("image_size") or "1K"), "newapi")
    request_schema = image_config.get("request_schema") or {}
    from novelvideo.catalog.media_model_request_schema import normalize_media_resolution_value

    resolution = normalize_media_resolution_value(image_size)

    metadata: dict[str, object] = {"resolution": resolution}
    payload: dict[str, object] = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "response_format": "url",
        "watermark": False,
        "metadata": metadata,
    }
    if aspect_ratio in {"auto", "adaptive"}:
        # Images use ``auto`` as the public follow-input ratio value. Keep
        # resolution independent and let NewAPI infer the geometry.
        metadata["ratio"] = "auto"
    else:
        # Fixed geometry keeps the selected ratio as semantic metadata while
        # width/height carry the pixel expectation. All three values come from
        # this same aspect-ratio/resolution pair.
        metadata["ratio"] = aspect_ratio
        try:
            size = resolve_openai_image_size(
                aspect_ratio,
                image_size,
                model,
                allow_dynamic_resolution=True,
                min_pixels=request_schema.get("minPixels"),
            )
        except ValueError as exc:
            return None, "", str(exc)
        width, height = (int(value) for value in size.split("x", 1))
        payload["width"] = width
        payload["height"] = height
    # [aipdd fork delta] 画质是否下发以媒体目录的声明为准（条目的 qualityOptions
    # 会被路由换成 request_schema.includeQuality）。原来这里是一层模型名白名单的 or：
    # 白名单里只有发行方网关那几个名字，自建渠道的模型（ap-image-2.0 等）永远不命中，
    # 用户在后台给模型配了画质档位也没用。
    include_quality = bool(request_schema.get("includeQuality")) or (
        not request_schema and _legacy_newapi_image_quality_default(model)
    )
    if include_quality and str(image_config.get("quality") or "").strip():
        quality = str(image_config["quality"]).strip()
        payload["quality"] = quality
    else:
        quality = ""

    output_format = str(image_config.get("output_format") or "").strip().lower()
    if output_format:
        payload["output_format"] = output_format
    input_fidelity = str(image_config.get("input_fidelity") or "").strip().lower()
    if input_fidelity and reference_images:
        payload["input_fidelity"] = input_fidelity

    if reference_images:
        try:
            relay_helper = _gg._relay_reference_images_for_newapi
            if relay_helper is _ORIGINAL_RELAY_REFERENCE_IMAGES_FOR_NEWAPI:
                payload["image"] = await relay_helper(
                    reference_images,
                    egress_context=context,
                )
            else:
                payload["image"] = await relay_helper(reference_images)
        except Exception as exc:
            if context is not None and context.is_organization:
                # The organization path hides `exc` because an arbitrary
                # exception may carry a signed URL or a key. The relay's own
                # three failures are secret-free by construction, though, and
                # they need three different fixes — a port registration, a
                # retry, an object-storage policy — so name which one fired
                # (OI-45). Anything else stays opaque.
                code = getattr(type(exc), "code", None)
                reason = getattr(exc, "reason", None)
                if not isinstance(code, str):
                    return None, "", "media relay upload failed"
                logger.warning(
                    "organization media relay failed: code=%s reason=%s "
                    "envelope=%s project=%s",
                    code,
                    reason or "-",
                    context.envelope_id,
                    context.project_id,
                )
                return None, "", f"media relay upload failed ({code})"
            return None, "", f"media relay upload failed: {exc}"
    # [aipdd fork delta req41] 文生图 / 图生图两条路径都先问渠道目录：AIPDD 把
    # ``images/generations`` 和 ``images/edits`` 都声明在条目里，上游要是改名或只开
    # 其中一条，重拉目录就跟进；目录没说话（自建网关常只给名字）才用通用 OpenAI 路径。
    from novelvideo.model_gateway_settings import catalog_endpoint_path
    from novelvideo.catalog.upstream_catalog import EP_IMAGES_EDITS, EP_IMAGES_GENERATIONS

    if reference_images:
        purpose = EP_IMAGES_EDITS
        fallback = "/images/edits"
    else:
        purpose = EP_IMAGES_GENERATIONS
        fallback = "/images/generations"
    declared_path = catalog_endpoint_path(str(model or ""), purpose)
    request_path = f"/{declared_path}" if declared_path else fallback

    from novelvideo.catalog.media_model_request_schema import (
        apply_media_request_schema,
        enforce_newapi_media_geometry_contract,
    )

    payload = apply_media_request_schema(
        payload,
        request_schema,
        image_config.get("model_params") or {},
    )
    payload = enforce_newapi_media_geometry_contract(payload, media_type="image")

    if base_url:
        endpoint = base_url.rstrip("/")
    else:
        from novelvideo.config import get_effective_newapi_gateway_config

        endpoint = get_effective_newapi_gateway_config().base_url.rstrip("/")
    # [aipdd fork delta] 直连 aipdd 渠道时把几何双写成平台方言（size+ratio）。
    # 仅 base_url 显式给出（resolve_media_provider_override 直连）时激活：走生效
    # 网关时协议转换由网关自己负责，不改内置协议请求。
    if base_url and _aipdd_image_geometry_endpoint(endpoint):
        payload = await _apply_aipdd_image_geometry(payload, model=model)
    request_context = _newapi_safe_request_context(
        endpoint=endpoint,
        request_path=request_path,
        model=model,
        payload=payload,
        prompt=prompt,
    )
    logger.info("DramaClawAPI image request: %s", request_context)
    headers = openai_compat.auth_headers(api_key)



    async def _record(
        provider_request_id: str = "",
        response_id: str = "",
        provider_task_id: str = "",
    ) -> None:
        """[aipdd fork delta] WP-D: 只记一次上游调用，不做预约 / 退款。"""
        try:
            await _gg.get_usage_meter().bump_model_call(
                user_id=None,
                model=model,
                provider_request_id=provider_request_id,
                provider_task_id=provider_task_id,
                metadata={"response_id": response_id} if response_id else None,
            )
        except Exception:  # noqa: BLE001
            pass

    def _record_trace(
        *,
        provider_request_id: str = "",
        response_id: str = "",
        provider_task_id: str = "",
    ) -> None:
        if trace is None:
            return
        if provider_request_id:
            trace["request_id"] = provider_request_id
        if response_id:
            trace["response_id"] = response_id
        if provider_task_id:
            trace["provider_task_id"] = provider_task_id

    provider_request_id = ""
    try:
        await update_current_model_call_log(
            request_payload=payload,
        )

        async with httpx.AsyncClient(
            timeout=NEWAPI_IMAGE_HTTP_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as client:
            # [aipdd fork delta] aipdd 网关把真实图片模型的偶发失败统一包成
            # HTTP 400「未通过上游校验」（实测约 1/4 概率抖动、重试即恢复）。单次
            # 直取会让这种瞬态失败直接变成用户可见的"生成失败"，故按指数退避重试
            # 瞬态状态（特定 400 / 408/409/425/429 / 5xx）；非瞬态 4xx 与传输异常
            # 不重试（前者是确定性拒绝，重试没有意义；后者沿用原有单次错误上报）。
            response = None
            for attempt in range(1, NEWAPI_IMAGE_MAX_ATTEMPTS + 1):
                logger.info(
                    "DramaClawAPI image POST start: %s (attempt %d/%d)",
                    request_context.get("endpoint"),
                    attempt,
                    NEWAPI_IMAGE_MAX_ATTEMPTS,
                )
                candidate = await client.post(
                    f"{endpoint}{request_path}",
                    headers=headers,
                    json=payload,
                )
                status_code = getattr(candidate, "status_code", 0)
                if status_code >= 400 and _is_newapi_transient_image_status(
                    status_code, candidate.text
                ):
                    if attempt < NEWAPI_IMAGE_MAX_ATTEMPTS:
                        wait_seconds = NEWAPI_IMAGE_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
                        logger.warning(
                            "DramaClawAPI image transient rejection status=%s (attempt %d/%d), "
                            "retrying in %.1fs: %s",
                            status_code,
                            attempt,
                            NEWAPI_IMAGE_MAX_ATTEMPTS,
                            wait_seconds,
                            (candidate.text or "")[:200],
                        )
                        await asyncio.sleep(wait_seconds)
                        continue
                response = candidate
                break
            logger.info(
                "DramaClawAPI image POST response: status=%s bytes=%s",
                getattr(response, "status_code", "?"),
                (getattr(response, "headers", None) or {}).get("content-length", "?"),
            )
            response.raise_for_status()
            response_headers = getattr(response, "headers", {}) or {}
            provider_request_id = _newapi_request_id_from_headers(response_headers)
            result = response.json()
            await update_current_model_call_log(
                response_payload=result,
            )
            logger.info(
                "DramaClawAPI image POST parsed: data_count=%d keys=%s",
                len(result.get("data") or []),
                sorted(result.keys())[:5],
            )
            provider_request_id = (
                provider_request_id
                or str(result.get("request_id") or result.get("requestId") or "").strip()
            )
            response_id = str(result.get("id") or "").strip()
            _record_trace(provider_request_id=provider_request_id, response_id=response_id)

            # [aipdd fork delta] 异步图片任务：提交响应带 task_id（或缺输出但带
            # 状态的 id）时，轮询 GET /images/generations/{task_id} 直到出图、失败
            # 或超时。这段轮询在迁移到 grid_generation 时丢失，队列式出图的模型
            # （FLUX-GGUF-T2I-V2 等）会直接掉进「missing data」。
            task_id = str(result.get("task_id") or "").strip()
            image_b64, image_url = _newapi_image_output(result)
            if not image_b64 and not image_url and not task_id:
                fallback_id = str(result.get("id") or "").strip()
                if fallback_id and _newapi_image_status(result):
                    task_id = fallback_id
            if task_id:
                _record_trace(
                    provider_request_id=provider_request_id,
                    response_id=response_id,
                    provider_task_id=task_id,
                )
            if not image_b64 and not image_url and task_id:
                logger.info(
                    "DramaClawAPI image task accepted: task_id=%s status=%s",
                    task_id,
                    _newapi_image_status(result) or "queued",
                )
                for poll_count in range(NEWAPI_IMAGE_MAX_POLLS):
                    status = _newapi_image_status(result)
                    if status in _NEWAPI_IMAGE_FAILURE_STATUSES:
                        return None, "", (
                            "DramaClawAPI image task failed: "
                            f"{_newapi_image_error(result)}"
                        )
                    if status in _NEWAPI_IMAGE_SUCCESS_STATUSES:
                        return None, "", (
                            "DramaClawAPI image task completed without image data: "
                            f"task_id={task_id}"
                        )
                    if poll_count > 0 or status not in _NEWAPI_IMAGE_PENDING_STATUSES:
                        logger.info(
                            "DramaClawAPI image task polling: task_id=%s status=%s poll=%d/%d",
                            task_id,
                            status or "queued",
                            poll_count + 1,
                            NEWAPI_IMAGE_MAX_POLLS,
                        )
                    if poll_count > 0 or status in _NEWAPI_IMAGE_PENDING_STATUSES:
                        await asyncio.sleep(NEWAPI_IMAGE_POLL_INTERVAL_SECONDS)

                    task_response = await client.get(
                        f"{endpoint}{request_path}/{task_id}",
                        headers=headers,
                    )
                    task_response.raise_for_status()
                    task_headers = getattr(task_response, "headers", {}) or {}
                    provider_request_id = (
                        _newapi_request_id_from_headers(task_headers) or provider_request_id
                    )
                    result = task_response.json()
                    image_b64, image_url = _newapi_image_output(result)
                    _record_trace(
                        provider_request_id=provider_request_id,
                        response_id=response_id,
                        provider_task_id=task_id,
                    )
                    if image_b64 or image_url:
                        break
                    status = _newapi_image_status(result)
                    if status in _NEWAPI_IMAGE_FAILURE_STATUSES:
                        return None, "", (
                            "DramaClawAPI image task failed: "
                            f"{_newapi_image_error(result)}"
                        )
                    if status in _NEWAPI_IMAGE_SUCCESS_STATUSES:
                        return None, "", (
                            "DramaClawAPI image task completed without image data: "
                            f"task_id={task_id}"
                        )
                else:
                    return None, "", (
                        "Timeout waiting for DramaClawAPI image task: "
                        f"task_id={task_id}"
                    )

            data = result.get("data") or []
            if not data and (image_b64 or image_url):
                # 嵌套形状出图（result.image_url / output.images[] 等）：包成
                # data[0] 复用同一条提取路径（含归档复制与 URL 二次 GET）。
                data = [{"b64_json": image_b64} if image_b64 else {"url": image_url}]
            if not data:
                return None, "", f"DramaClawAPI Images response missing data: {sorted(result.keys())}"

            first = data[0] or {}
            if delivery_path is not None:
                from novelvideo.media_archive_copy import copy_archived_result

                if await copy_archived_result(
                    first.get("archive"), delivery_path, before_copy=before_delivery_copy
                ):
                    await _record(
                        provider_request_id=provider_request_id,
                        response_id=response_id,
                        provider_task_id=task_id,
                    )
                    if not read_copied_bytes:
                        if delivery_state is not None:
                            delivery_state["copied"] = True
                            delivery_state["sha256"] = str(
                                first["archive"].get("sha256") or ""
                            )
                        return None, "", ""
                    image_bytes = Path(delivery_path).read_bytes()
                    if delivery_state is not None:
                        delivery_state["copied"] = True
                    return image_bytes, "", ""
            image_b64 = first.get("b64_json") or ""
            if image_b64:
                image_bytes = base64.b64decode(image_b64)
                await _record(
                    provider_request_id=provider_request_id,
                    response_id=response_id,
                    provider_task_id=task_id,
                )
                return image_bytes, "", ""

            image_url = first.get("url") or first.get("image_url") or ""
            if image_url.startswith("data:image"):
                _, b64_data = image_url.split(",", 1)
                image_bytes = base64.b64decode(b64_data)
                await _record(
                    provider_request_id=provider_request_id,
                    response_id=response_id,
                    provider_task_id=task_id,
                )
                return image_bytes, "", ""
            if image_url:
                # NewAPI 返 URL 而非 b64 时,要二次 GET 拉图。这个 await 是常见的
                # "newapi 已生成但任务还在 await" hang 点 —— 用单独的短 timeout
                # (60s),避免落入外层 client 的 600s global timeout 拖很久。
                # 加 phase log 让 hang 时能定位卡在哪。
                logger.info("DramaClawAPI image GET url start: %s", image_url[:120])
                async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as fetch:
                    image_response = await fetch.get(image_url)
                logger.info(
                    "DramaClawAPI image GET url done: status=%d bytes=%d",
                    image_response.status_code,
                    len(image_response.content),
                )
                image_response.raise_for_status()
                image_bytes = image_response.content
                await _record(
                    provider_request_id=provider_request_id,
                    response_id=response_id,
                    provider_task_id=task_id,
                )
                return image_bytes, "", ""

            return None, "", f"DramaClawAPI Images response missing b64_json/url: {first}"
    except httpx.HTTPStatusError as exc:
        body = (exc.response.text or "")[:2000]
        response_headers = getattr(exc.response, "headers", {}) or {}
        safe_headers = _newapi_safe_header_summary(response_headers)
        request_id = _newapi_request_id_from_headers(response_headers) or provider_request_id
        await update_current_model_call_log(
            response_payload={
                "status_code": exc.response.status_code,
                "headers": safe_headers,
                "body": body,
            },
            error_message=f"HTTP {exc.response.status_code}",
        )
        error_context = _newapi_context_for_error(request_context)
        header_context = (
            f"request_id={request_id}; headers={safe_headers}; "
            if request_id or safe_headers
            else ""
        )
        logger.warning(
            "DramaClawAPI image failed: status=%s; %s%s; body=%s",
            exc.response.status_code,
            header_context,
            error_context,
            body,
        )
        return (
            None,
            "",
            f"HTTP {exc.response.status_code}: {header_context}{error_context}; body={body}",
        )
    except Exception as exc:
        await update_current_model_call_log(
            error_message=type(exc).__name__,
        )
        if is_fatal_upstream_quota_error(exc):
            raise
        error_context = _newapi_context_for_error(request_context)
        detail = f"{type(exc).__name__}: {exc!r}; {error_context}"
        logger.warning("DramaClawAPI image request exception: %s", detail)
        return None, "", f"请求异常: {detail}"


def _reference_image_bytes(
    image_ref: bytes | tuple[bytes, str] | tuple[str, bytes, str],
) -> bytes:
    """Unpack the three reference-image shapes the newAPI path accepts."""
    if isinstance(image_ref, tuple):
        if len(image_ref) == 3:
            return bytes(image_ref[1])
        if len(image_ref) == 2:
            return bytes(image_ref[0])
        return bytes(image_ref[0])
    return bytes(image_ref)


async def _call_newapi_image_api_with_egress(
    *,
    capability: str,
    api_key: str,
    model: str,
    prompt: str,
    reference_images: (
        list[bytes | tuple[bytes, str] | tuple[str, bytes, str]] | None
    ) = None,
    image_config: dict | None = None,
    base_url: str | None = None,
    egress_context: TrustedEgressContext | None = None,
    delivery_path: str | Path | None = None,
    delivery_state: dict[str, bool | str] | None = None,
    read_copied_bytes: bool = True,
) -> tuple[bytes | None, str, str]:
    """grid 家族的出网闸门（OI-52）：把 `_generate_image` 已验证的形状装到叶子外围。

    闸门装在**调用点**而不是叶子内部，因为叶子复原不出 `capability`、归一化前的
    `image_size`、fallback 前的 `quality`，也拿不到结果引用；而 `_generate_image`
    与 `scene_reference_images.py` 已经在叶子上方 claim 过，叶子内再 claim 一次会以
    同键不同 digest 撞成 `EGRESS_OPERATION_CONFLICT`。

    非组织身份走逐字节透传：叶子收到的实参与没有这层包装时完全一致，
    `tests/test_newapi_image_gateway.py` 的直调因此不受影响。
    """

    context = _validate_egress_context(egress_context)
    if context is None:
        context = ambient_organization_egress_context()
    if context is None or not context.is_organization:
        return await _gg._call_newapi_image_api(
            api_key=api_key,
            model=model,
            prompt=prompt,
            reference_images=reference_images,
            image_config=image_config,
            base_url=base_url,
            delivery_path=delivery_path,
            delivery_state=delivery_state,
            read_copied_bytes=read_copied_bytes,
        )

    from novelvideo.model_gateway_runtime import next_model_gateway_business_task_id

    config = image_config or {}
    request = {
        "model": model,
        "prompt": prompt,
        "reference_sha256": [
            hashlib.sha256(_reference_image_bytes(item)).hexdigest()
            for item in (reference_images or [])
        ],
        "aspect_ratio": str(config.get("aspect_ratio") or ""),
        "image_size": str(config.get("image_size") or ""),
        "quality": str(config.get("quality") or ""),
    }
    # 同一 envelope 内可以有多次同 capability 的图像操作（`_render_single_panel_gemini`
    # 用 asyncio.gather 扇出 N 路），`envelope_id` 当 business_task_id 会把它们压成同一
    # 个操作键。这个 helper 带请求内序号，且在 envelope 重投递时可复现。
    state = await _gg._prepare_organization_image_egress(
        egress_context=context,
        provider="newapi",
        capability=capability,
        request=request,
        business_task_id=next_model_gateway_business_task_id(
            capability,
            request_digest=canonical_request_digest(request),
        ),
    )
    if state is None:
        raise EgressError("ORG_EGRESS_DENIED")

    async def _mark_unknown() -> None:
        await state.operation_port.mark_unknown(
            operation_id=state.claim.operation.operation_id,
            transition_token=state.claim.transition_token,
            expected_version=state.claim.operation.version,
        )

    trace: dict[str, str] = {}
    try:
        image_bytes, text, error = await _gg._call_newapi_image_api(
            api_key=state.credential.api_key,
            model=model,
            prompt=prompt,
            reference_images=reference_images,
            image_config=image_config,
            base_url=state.credential.base_url,
            trace=trace,
            egress_context=context,
            delivery_path=delivery_path,
            delivery_state=delivery_state,
            read_copied_bytes=read_copied_bytes,
        )
    except Exception:
        await _mark_unknown()
        raise
    if not image_bytes and not (delivery_state and delivery_state.get("copied")):
        # 叶子把传输失败压成 `(None, "", error)`，操作已经出过网但没有可用结果，
        # 只能落 unknown——不能当没发生过，否则重试会以同键再 claim 一次。
        await _mark_unknown()
        return image_bytes, text, error
    digest = (
        hashlib.sha256(image_bytes).hexdigest()
        if image_bytes
        else str(delivery_state.get("sha256") or "")
    )
    if len(digest) != 64 or any(
        char not in "0123456789abcdef" for char in digest.lower()
    ):
        with Path(delivery_path).open("rb") as copied_file:
            digest = hashlib.file_digest(copied_file, "sha256").hexdigest()
    await _complete_organization_image_egress(
        state,
        trace=trace,
        result_ref=f"image:sha256:{digest}",
    )
    return image_bytes, text, error


async def _relay_reference_images_for_newapi(
    reference_images: list[bytes | tuple[bytes, str] | tuple[str, bytes, str]],
    *,
    egress_context: TrustedEgressContext | None = None,
) -> list[str]:
    """Upload reference image bytes to OSS relay for URL-only upstream channels."""

    context = _validate_egress_context(egress_context)

    def _image_ext(image_ref) -> str:
        if isinstance(image_ref, tuple):
            if len(image_ref) == 3:
                filename = str(image_ref[0] or "")
                mime_type = str(image_ref[2] or "")
                suffix = Path(filename).suffix.lstrip(".")
                if suffix:
                    return suffix
                if mime_type.startswith("image/"):
                    return mime_type.split("/", 1)[1]
            if len(image_ref) == 2:
                hint = str(image_ref[1] or "")
                if hint.startswith("image/"):
                    return hint.split("/", 1)[1]
                suffix = Path(hint).suffix.lstrip(".")
                if suffix:
                    return suffix
        return "png"

    if context is not None and context.is_organization:
        urls: list[str] = []
        for index, image_ref in enumerate(reference_images):
            data = _reference_image_bytes(image_ref)
            content_digest = hashlib.sha256(data).hexdigest()
            object_id = (
                f"{context.envelope_id}:{context.project_id}:{index}:{content_digest}"
            )
            urls.append(
                await _gg.relay_tenant_image_bytes_from_context(
                    data,
                    object_id=object_id,
                    context=context,
                    ext=_image_ext(image_ref),
                    ttl=NEWAPI_MEDIA_INPUT_MIN_TTL_SECONDS,
                )
            )
        return urls

    def upload_all() -> list[str]:
        urls: list[str] = []
        relay_ttl = _gg.media_relay_ttl_seconds(
            minimum=NEWAPI_MEDIA_INPUT_MIN_TTL_SECONDS,
        )
        for image_ref in reference_images:
            urls.append(
                _gg.upload_image_bytes(
                    _reference_image_bytes(image_ref),
                    ext=_image_ext(image_ref),
                    ttl=relay_ttl,
                    image_transform=IMAGE_TRANSFORM_AI_REFERENCE_JPEG,
                )
            )
        return urls

    return await asyncio.to_thread(upload_all)


_ORIGINAL_RELAY_REFERENCE_IMAGES_FOR_NEWAPI = _relay_reference_images_for_newapi
