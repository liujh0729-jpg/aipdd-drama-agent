"""Freezone 视觉出网辅助（prepare / complete / abandon）（B2 拆包自单文件 presets.py）。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FreezoneVisionEgress:
    image_egress: Any
    transport_context: Any


async def prepare_freezone_vision_egress(
    *,
    egress_context,
    model_name: str,
    prompt: str,
    images: list[bytes],
    timeout_seconds: float,
) -> FreezoneVisionEgress | None:
    from novelvideo.egress_context import (
        TrustedEgressContext,
        ambient_organization_egress_context,
    )

    if egress_context is None:
        egress_context = ambient_organization_egress_context()
    if egress_context is None:
        return None
    if type(egress_context) is not TrustedEgressContext:
        raise TypeError("egress_context must be a TrustedEgressContext")
    if not egress_context.is_organization:
        return None

    from novelvideo import config
    from novelvideo.freezone.vision_gateway import VisionTransportContext
    from novelvideo.generators.grid_generation import _prepare_organization_image_egress

    image_egress = await _prepare_organization_image_egress(
        egress_context=egress_context,
        provider="newapi",
        capability="freezone.vision.analyze",
        request={
            "model": model_name,
            "prompt": prompt,
            "image_sha256": [hashlib.sha256(image).hexdigest() for image in images],
        },
    )
    if image_egress is None:
        return None
    transport_model = config._newapi_text_openai_model(
        model_name,
        api_key=image_egress.credential.api_key,
        base_url=image_egress.credential.base_url,
        timeout_seconds=timeout_seconds,
        profile=config._get_newapi_text_model_profile(model_name),
    )
    return FreezoneVisionEgress(
        image_egress=image_egress,
        transport_context=VisionTransportContext(
            model_name=model_name, model=transport_model
        ),
    )


async def complete_freezone_vision_egress(
    state: FreezoneVisionEgress | None,
    *,
    result: str,
) -> None:
    if state is None:
        return
    from novelvideo.generators.grid_generation import (
        _complete_organization_image_egress,
    )

    operation_id = state.image_egress.claim.operation.operation_id
    await _complete_organization_image_egress(
        state.image_egress,
        trace={"request_id": f"sync-{operation_id}"},
        result_ref=f"sha256:{hashlib.sha256(result.encode('utf-8')).hexdigest()}",
    )


async def abandon_freezone_vision_egress(
    state: FreezoneVisionEgress | None,
    *,
    submitted: bool,
) -> None:
    """出网失败时给 claim 一个终态，别让它停在 `dispatching`。

    `submitted` 为真表示请求已经交出去、结果不明——落 `unknown`，与 EE `0066`
    收割器同一口径；为假表示确未提交，落 `rejected`。形状对齐
    `audio_node.py:665-675`。
    """
    if state is None:
        return
    claim = state.image_egress.claim
    operation_port = state.image_egress.operation_port
    if submitted:
        await operation_port.mark_unknown(
            operation_id=claim.operation.operation_id,
            transition_token=claim.transition_token,
            expected_version=claim.operation.version,
        )
        return
    await operation_port.mark_rejected_before_submit(
        operation_id=claim.operation.operation_id,
        transition_token=claim.transition_token,
        expected_version=claim.operation.version,
    )

