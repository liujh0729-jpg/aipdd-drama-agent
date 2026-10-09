"""受信音频出网的统一会话入口。

IndexTTS2 客户端与 freezone audio 节点原先各自实现同一段
「ambient 回退 → 组织校验 → claim 租约 → 解析凭据」协议，错误面互为镜像
（一个把它翻译成 ``TTSResult``，另一个翻译成 ``RuntimeError``）。本模块把这段
协议收敛为单点实现，调用方只负责：

1. 保留各自的前置校验顺序（例如 IndexTTS2 的 prompt 空值检查必须先于 claim，
   未授权上下文必须先于任何输入校验）；
2. 保留各自的传输与收尾语义 —— IndexTTS2 任何失败都 ``mark_unknown``；
   audio 节点按 ``transport_started`` 区分 ``reject`` / ``mark_unknown``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from novelvideo.egress_context import (
    TrustedEgressContext,
    ambient_organization_egress_context,
)
from novelvideo.generators.tts_generator import (
    AudioOperationLease,
    claim_audio_operation,
    reject_audio_operation,
    resolve_audio_gateway_credential,
)
from novelvideo.ports.model_credentials import (
    ModelCredentialError,
    RequestCredential,
)


def is_organization_egress_context(context: TrustedEgressContext | None) -> bool:
    """仅「受信实例且组织主体」允许出网；None 与其它实例一律 False。"""

    return type(context) is TrustedEgressContext and bool(context.is_organization)


@dataclass(frozen=True, slots=True)
class AudioGatewaySession:
    """一次受信音频出网操作的「租约 + 凭据」快照。

    契约（与两处原实现的既有语义逐字一致）：

    - ``denial_code`` 非空：操作被拒，调用方必须原样将其作为失败码返回/抛出，
      且不得发起传输；
    - ``denial_code`` 为空且 ``lease is None``：没有任何受信上下文，调用方
      自行回退直连 / 平台配置，本模块不替调用方做该回退；
    - ``denial_code`` 为空且 ``lease is not None``：``credential`` 必定已解析成功，
      传输后的收尾（complete / reject / mark_unknown）由调用方负责。
    """

    lease: AudioOperationLease | None = None
    credential: RequestCredential | None = None
    denial_code: str | None = None


async def begin_trusted_audio_operation(
    context: TrustedEgressContext | None,
    *,
    capability: str,
    business_task_id: str,
    request: dict[str, Any],
) -> AudioGatewaySession:
    """开启一次受信音频出网操作：ambient 回退 → 组织校验 → claim → 凭据解析。

    失败语义（错误码逐字保留）：

    - 上下文非组织：``denial_code = "ORG_EGRESS_DENIED"``，不 claim；
    - claim 未赢得提交权（重放）：``denial_code = "audio operation already handled"``；
    - 凭据解析失败：先归还提交权（``reject``），再以 ``ModelCredentialError.code``
      作为 ``denial_code``；
    - claim 自身的异常（如 ``EgressOperationError``）不在此吞掉，原样冒泡。
    """

    if context is None:
        context = ambient_organization_egress_context()
    if context is None:
        return AudioGatewaySession()
    if not is_organization_egress_context(context):
        return AudioGatewaySession(denial_code="ORG_EGRESS_DENIED")
    lease = await claim_audio_operation(
        context,
        capability=capability,
        business_task_id=business_task_id,
        request=request,
    )
    if lease.replay_error:
        return AudioGatewaySession(lease=lease, denial_code=lease.replay_error)
    try:
        credential = await resolve_audio_gateway_credential(context)
    except ModelCredentialError as exc:
        await reject_audio_operation(lease)
        return AudioGatewaySession(lease=lease, denial_code=exc.code)
    return AudioGatewaySession(lease=lease, credential=credential)