"""组织出网闸门（prepare / complete / abandon）与 claim 助手（B3 拆包自单文件 grid_generation.py）。"""

from dataclasses import dataclass
from typing import Any

from novelvideo.egress_context import (
    TrustedEgressContext,
    ambient_organization_egress_context,
)
from novelvideo.ports.authz import AdmissionContext
from novelvideo.ports.egress import EgressError
from novelvideo.ports.egress_operations import (
    OperationClaimResult,
    HandleKind,
    OperationSpec,
    canonical_request_digest,
)
from novelvideo.ports.model_credentials import ModelCredentialError, RequestCredential


@dataclass(frozen=True, slots=True)
class _OrganizationImageEgress:
    credential: RequestCredential
    operation_port: Any
    claim: OperationClaimResult


def _validate_egress_context(
    egress_context: TrustedEgressContext | None,
) -> TrustedEgressContext | None:
    if egress_context is not None and type(egress_context) is not TrustedEgressContext:
        raise TypeError("egress_context must be a TrustedEgressContext")
    return egress_context


async def _prepare_organization_image_egress(
    *,
    egress_context: TrustedEgressContext | None,
    provider: str,
    capability: str,
    request: dict[str, object],
    business_task_id: str | None = None,
) -> _OrganizationImageEgress | None:
    context = _validate_egress_context(egress_context)
    if context is None:
        context = ambient_organization_egress_context()
    if context is None or not context.is_organization:
        return None
    if provider != "newapi":
        raise EgressError("ORG_EGRESS_DENIED")

    from novelvideo.ports import get_egress_operation_port, get_model_credentials

    admission = AdmissionContext(
        requester_user_id=context.requester_user_id,
        billing_principal=context.billing_principal,
        credential=context.credential,
        admission_id=context.admission_id,
        root_task_id=context.root_task_id,
        admitted_at=context.admitted_at,
        membership_id=context.membership_id,
        authz_version=context.authz_version,
    )
    operation_port = get_egress_operation_port()
    claim = await operation_port.claim(
        spec=OperationSpec(
            organization_id=context.billing_principal.id,
            project_id=context.project_id,
            root_task_id=context.root_task_id,
            business_task_id=business_task_id or context.envelope_id,
            capability=capability,
            credential_id=context.credential.credential_id,
            credential_version=context.credential.key_version,
            request_digest=canonical_request_digest(request),
            handle_kind=HandleKind.PROVIDER_JOB,
        )
    )
    if not claim.won:
        raise EgressError("EGRESS_OPERATION_REPLAYED")
    try:
        credential = await get_model_credentials().resolve(admission)
        if (
            type(credential) is not RequestCredential
            or credential.reference != context.credential
        ):
            raise ModelCredentialError("ORG_CREDENTIAL_VERSION_MISMATCH")
    except Exception:
        await operation_port.mark_rejected_before_submit(
            operation_id=claim.operation.operation_id,
            transition_token=claim.transition_token,
            expected_version=claim.operation.version,
        )
        raise
    return _OrganizationImageEgress(
        credential=credential,
        operation_port=operation_port,
        claim=claim,
    )


async def _complete_organization_image_egress(
    state: _OrganizationImageEgress | None,
    *,
    trace: dict[str, str],
    result_ref: str,
) -> None:
    if state is None:
        return
    provider_job_id = str(
        trace.get("request_id") or trace.get("response_id") or ""
    ).strip()
    if not provider_job_id:
        await state.operation_port.mark_unknown(
            operation_id=state.claim.operation.operation_id,
            transition_token=state.claim.transition_token,
            expected_version=state.claim.operation.version,
        )
        raise RuntimeError("gateway response is missing a request id")
    accepted = await state.operation_port.mark_accepted(
        operation_id=state.claim.operation.operation_id,
        transition_token=state.claim.transition_token,
        expected_version=state.claim.operation.version,
        provider_job_id=provider_job_id,
    )
    await state.operation_port.mark_completed(
        operation_id=state.claim.operation.operation_id,
        transition_token=state.claim.transition_token,
        expected_version=accepted.version,
        result_ref=result_ref,
    )


async def _abandon_organization_image_egress(
    state: _OrganizationImageEgress | None,
) -> None:
    """Close out a claim whose outcome we cannot observe.

    Deliberately `mark_unknown` rather than `mark_rejected_before_submit`: once the
    credential is out of our hands we cannot prove the request was never submitted,
    and only the ledger's unknown state is honest about that.
    """

    if state is None:
        return
    await state.operation_port.mark_unknown(
        operation_id=state.claim.operation.operation_id,
        transition_token=state.claim.transition_token,
        expected_version=state.claim.operation.version,
    )


def _newapi_request_id_from_headers(headers: Any) -> str:
    if not headers:
        return ""
    return (
        headers.get("x-request-id")
        or headers.get("x-newapi-request-id")
        or headers.get("x-oneapi-request-id")
        or ""
    )
