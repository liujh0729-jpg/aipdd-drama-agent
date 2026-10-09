"""Public runtime configuration for the frontend."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from ulid import ULID

from novelvideo.shared import runtime_env

router = APIRouter()

RuntimeEdition = Literal["ce", "ee"]
_INSTANCE_ID = str(ULID())


def _runtime_edition() -> RuntimeEdition:
    return "ce" if runtime_env.is_ce_effective() else "ee"


# [aipdd fork delta] WP-B: 别名映射自检结果挂到 /api/v1/config 健康位。
# 这个端点是登录前的公开端点（也是容器 HEALTHCHECK 目标），所以只挂汇总：
# 逐别名明细在登录后的 GET /api/v1/model-gateway/config 里取。
# 读的是进程内 TTL 缓存，不发远程调用；任何异常都降级成 pending，
# 绝不让健康检查因为自检数据缺失而失败。
def _alias_mapping_health_summary() -> dict[str, object]:
    try:
        from novelvideo.newapi_provisioner import peek_alias_mapping_health

        health = peek_alias_mapping_health()
    except Exception:  # noqa: BLE001 - 公开端点上的展示位，不参与判定
        return {"status": "pending", "checked": False}
    if not health.get("checked"):
        return {"status": str(health.get("status") or "pending"), "checked": False}
    return {
        "status": str(health.get("status") or "ok"),
        "checked": True,
        "source": str(health.get("source") or ""),
        "total": int(health.get("total") or 0),
        "unmapped": len(health.get("unmapped") or []),
        "staleTargets": len(health.get("staleTargets") or []),
        "capabilityMismatch": len(health.get("capabilityMismatch") or []),
        "noChannel": list(health.get("noChannel") or []),
        "degraded": list(health.get("degraded") or []),
    }


@router.get("/config")
async def get_runtime_config():
    edition = _runtime_edition()
    return JSONResponse(
        {
            "ok": True,
            "data": {
                "edition": edition,
                # [aipdd fork delta] CE 开启 ST_CE_MULTI_USER 后前端也需要登录门控
                "auth_required": edition == "ee" or runtime_env.ce_multi_user(),
                "instance_id": _INSTANCE_ID,
                "alias_mapping_health": _alias_mapping_health_summary(),
            },
        }
    )
