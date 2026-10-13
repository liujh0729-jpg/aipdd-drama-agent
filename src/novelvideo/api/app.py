"""Standalone ASGI app for the 2.0 REST API."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections import Counter
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from novelvideo.api import OPENAPI_TAGS, api_router, register_verification_routes
from novelvideo.api.auth import get_api_user
from novelvideo.api.routes.files import preview_project_media_file
from novelvideo.model_slots import ModelSlotNotConfigured
from novelvideo.ports.authz import AuthzError, authz_error_payload
from novelvideo.shared.billing_errors import (
    BILLING_RULE_NOT_CONFIGURED_MESSAGE,
    BillingError,
    BillingRuleNotConfiguredError,
    billing_error_payload,
    billing_rule_not_configured_payload,
)
from novelvideo.shared.api_coverage import mount_api_coverage_middleware
from novelvideo.task_backend.limit_logging import log_task_limit_rejection
from novelvideo.task_backend.limits import (
    ChannelTaskLimitExceeded,
    GlobalLaneQueueLimitExceeded,
    ProjectTaskLimitExceeded,
    ProjectUserTaskLimitExceeded,
    UserTaskLimitExceeded,
)
from novelvideo.utils.upload_safety import MAX_PROJECT_UPLOAD_BYTES

logger = logging.getLogger("novelvideo.api.app")

# Per durability plan §N: reject oversized request bodies before they
# reach a handler. 5 MB covers the largest legitimate freezone canvas
# (50k nodes × ~80 bytes JSON each) with comfortable headroom; anything
# bigger is almost certainly a runaway client / DoS attempt.
MAX_REQUEST_BODY_BYTES = 5 * 1024 * 1024
MAX_UPLOAD_REQUEST_BODY_BYTES = MAX_PROJECT_UPLOAD_BYTES
_RESOURCE_REQUEST_COUNTS: Counter[str] = Counter()
_RESOURCE_REQUEST_TOTAL = 0
_RESOURCE_REQUEST_LOCK = threading.Lock()

# [A4] 自伺服（无 nginx）模式下后端直接伺服 SPA+API 时补齐的最小安全响应头。
# Docker/EE 部署由 frontend/docker/nginx.conf.template 统一注入这些头，不设
# ``AIPDD_DRAMA_FRONTEND_DIST`` 时本中间件不注册，行为逐字节不变。
# 刻意不含 CSP：nginx 模板的 CSP 含前端 index.html 内联脚本的 sha256 哈希，与构建强耦
# 合，写错直接白屏；后端自伺服的 CSP 需与前端共享真值源后再添加。
# HSTS 也不在此：它只在 TLS 终止后生效，自伺服常为 http://localhost，交由部署层决定。
_SPA_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": (
        "accelerometer=(), autoplay=(), camera=(), clipboard-read=(), "
        "clipboard-write=(self), display-capture=(), fullscreen=(self), "
        "geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), "
        "picture-in-picture=(self), usb=(), xr-spatial-tracking=()"
    ),
}


def _request_body_limit(request: Request) -> int:
    content_type = request.headers.get("content-type", "").lower()
    if (
        request.url.path.startswith("/api/v1/projects/")
        and (
            request.url.path.endswith("/upload")
            or request.url.path.endswith("/reference-file-upload")
        )
        and "multipart/form-data" in content_type
    ):
        return MAX_UPLOAD_REQUEST_BODY_BYTES
    # [aipdd fork delta] 统一模型入口透传上游：多模态请求（图片 base64）常超 5MB，
    # 与上传额度一致放宽；鉴权与转发都在路由内，body 按流透传、不落盘。
    if request.url.path.startswith("/api/v1/openai/"):
        return MAX_UPLOAD_REQUEST_BODY_BYTES
    return MAX_REQUEST_BODY_BYTES


def _is_freezone_audio_voice_upload(request: Request) -> bool:
    return (
        request.method.upper() == "POST"
        and request.url.path.startswith("/api/v1/projects/")
        and request.url.path.endswith("/freezone/audio/voices")
    )


_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


def _rejects_undeclared_chunked_body(request: Request) -> bool:
    """小 JSON 类接口是否应拒无 ``Content-Length`` 的 chunked 请求体。

    chunked 请求体不携带 Content-Length，会绕过只认长度头的守栏。小
    JSON 类 handler（FastAPI ``await request.json()`` / Pydantic 解析）把 body
    一次性读进内存，无长度声明的 chunked 请求可让客户端迫使本进程缓存无界
    body——DoS。因此要求显式长度（合法 JSON 客户端——浏览器 fetch/XHR、httpx、
    curl——都会发 Content-Length）。流式路径（``/upload``/``reference-file-upload``
    multipart 和 ``/openai`` 透传）属大 limit 类、各自按流/落盘限流，不拒。
    """

    if request.method.upper() not in _BODY_METHODS:
        return False
    if "chunked" not in request.headers.get("transfer-encoding", "").lower():
        return False
    return _request_body_limit(request) == MAX_REQUEST_BODY_BYTES


def _resource_request_key(path: str) -> str | None:
    if path.startswith("/static/"):
        return path

    if not path.startswith("/api/v1/projects/"):
        return None

    parts = path.split("/")
    if len(parts) >= 6 and parts[4] in {"media", "files"}:
        return path
    return None


#: 查询串里不能原文落盘的一部分。SSE（EventSource 不能带 header）会
#: 把访问令牌放在 query；静态资源的预签名 URL 又把签名/密钥放在 query。两者都是凭证。
_SENSITIVE_QUERY_KEY_MARKERS = (
    "token",
    "key",
    "sig",
    "secret",
    "auth",
    "credential",
    "password",
    "apikey",
)


def _redact_query(query: str) -> str:
    """把查询串里的敏感参数值打码，只留键名与长度，防凭证进访问/代理日志。"""

    if not query:
        return ""
    parts: list[str] = []
    for key, value in parse_qsl(query, keep_blank_values=True):
        marker = key.lower().replace("-", "").replace("_", "")
        if any(m in marker for m in _SENSITIVE_QUERY_KEY_MARKERS):
            parts.append(f"{key}=[redacted:{len(value)}]")
        else:
            parts.append(f"{key}={value}")
    return "&".join(parts)


def _record_resource_request(resource_key: str) -> tuple[int, int]:
    global _RESOURCE_REQUEST_TOTAL
    with _RESOURCE_REQUEST_LOCK:
        _RESOURCE_REQUEST_TOTAL += 1
        _RESOURCE_REQUEST_COUNTS[resource_key] += 1
        return _RESOURCE_REQUEST_TOTAL, _RESOURCE_REQUEST_COUNTS[resource_key]


# [aipdd fork delta] WP-A/WP-B: 启动后台跑两件不阻断启动的事：
# 1) 把网关 channels.model_mapping 一次性反向导入项目自有真源；
# 2) 算一次别名映射一致性自检（悬空别名 / 失效目标 / 能力错配），结果缓存供面板横幅。
#
# backfill 的等待窗口必须收窄：它跑在 asyncio.to_thread 里、线程不可取消，默认
# 2 分钟（NEWAPI_PROVISIONER_INIT_TIMEOUT_MS）的 wait_for_newapi 重试循环会在
# 网关未就绪（本机开发 / 首启）时拖住事件循环关闭（TestClient 退出、进程重启
# 都卡满两分钟），所以在启动路径传 5 秒秒级预算：探测不到就放弃，标记不写，
# 下次启动重试（同一「秒级探测预算，失败静默回退，下次再试」的约定）。
_ALIAS_BACKFILL_PROBE_TIMEOUT_MS = 5000


async def _backfill_model_alias_mappings() -> None:
    from novelvideo.newapi_provisioner import (
        backfill_model_alias_mappings_from_gateway,
        build_alias_mapping_health,
        rename_gateway_openai_channel_to_newapi,
    )

    # [aipdd fork delta] 渠道重命名（openai→newapi，五渠道收敛）：先迁本库真源
    # （纯本地、幂等、零远程），再把网关里的 DC-openai 渠道改名（远程，同一
    # 秒级探测预算）。任一步失败都只记日志，下次启动重试。
    try:
        from novelvideo.model_gateway_settings import (
            migrate_provider_openai_to_newapi,
        )

        migration = await asyncio.to_thread(migrate_provider_openai_to_newapi)
        if migration.get("migrated"):
            logger.info("provider rename openai->newapi applied: %s", migration)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - 真源迁移失败不应影响 API 可用性
        logger.warning(
            "provider rename openai->newapi migration failed (non-fatal)",
            exc_info=True,
        )

    try:
        renamed = await asyncio.to_thread(
            rename_gateway_openai_channel_to_newapi,
            init_timeout_ms=_ALIAS_BACKFILL_PROBE_TIMEOUT_MS,
        )
        if renamed.get("renamed"):
            logger.info(
                "gateway channel rename DC-openai->DC-newapi done: %s", renamed
            )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - 网关未就绪不应影响 API 可用性
        logger.warning(
            "gateway channel rename DC-openai->DC-newapi failed (non-fatal)",
            exc_info=True,
        )

    try:
        result = await asyncio.to_thread(
            backfill_model_alias_mappings_from_gateway,
            init_timeout_ms=_ALIAS_BACKFILL_PROBE_TIMEOUT_MS,
        )
        if result.get("skipped"):
            logger.debug("model alias mapping backfill skipped: %s", result)
        else:
            logger.info(
                "model alias mapping backfill imported=%s total=%s",
                result.get("imported"),
                result.get("total"),
            )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - 网关未就绪不应影响 API 可用性
        logger.warning(
            "model alias mapping backfill failed (non-fatal); "
            "project-owned alias store stays empty until the next gateway save",
            exc_info=True,
        )

    # [aipdd fork delta] 拍平迁移（2026-10-01）：本地清理真源备援字段 + 渠道全量
    # 重投影为拍平注册表（复合名 + 内部绑定裸名 + 单渠道唯一裸名）。升级前的渠道
    # mapping 是旧注册口径（无复合名），不收敛则用户按复合名选模型会路由不到。
    # 投影需网关就绪，同用秒级探测预算；任一渠道失败不写完成标记，下次启动重试。
    try:
        from novelvideo.model_gateway_settings import (
            prune_legacy_alias_fallback_fields,
        )
        from novelvideo.newapi_provisioner import project_flat_registrations_migration

        pruned = await asyncio.to_thread(prune_legacy_alias_fallback_fields)
        if pruned.get("pruned"):
            logger.info(
                "flat migration pruned legacy fallback fields: %s",
                pruned.get("pruned"),
            )
        result = await asyncio.to_thread(
            project_flat_registrations_migration,
            init_timeout_ms=_ALIAS_BACKFILL_PROBE_TIMEOUT_MS,
        )
        if result.get("skipped"):
            logger.debug("flat registration migration skipped: %s", result)
        else:
            logger.info(
                "flat registration migration migrated=%s providers=%s failed=%s",
                result.get("migrated"),
                result.get("providers"),
                len(result.get("failed") or []),
            )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - 网关未就绪不应影响 API 可用性
        logger.warning(
            "flat registration migration failed (non-fatal); will retry on next startup",
            exc_info=True,
        )

    try:
        health = await asyncio.to_thread(build_alias_mapping_health)
        logger.info(
            "model alias mapping health status=%s unmapped=%s stale=%s mismatch=%s",
            health.get("status"),
            len(health.get("unmapped") or []),
            len(health.get("staleTargets") or []),
            len(health.get("capabilityMismatch") or []),
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - 自检只告警，不阻断启动
        logger.warning("model alias mapping health check failed (non-fatal)", exc_info=True)


@asynccontextmanager
async def _lifespan(application: FastAPI):
    """ASGI lifespan：取代已弃用的 ``on_event("startup"/"shutdown")``。

    yield 前是启动钩子、yield 后（置于 ``finally``）是关停钩子——即使启动后
    有请求处理抛异常也能回收后台任务。行为与原 ``on_event`` 等价。
    """
    try:
        from novelvideo.ports.registry import ensure_bootstrap, get_port

        ensure_bootstrap()

        # [aipdd fork delta] CE 最小多用户：只在显式配了 ST_LOCAL_PASSWORD 时才自动
        # 建引导账户。没配就保持空库，登录页查 setup-status 后给初始化表单，
        # 让用户自己定用户名和初始密码——启动时无条件 bootstrap 会烧掉这个入口。
        from novelvideo.shared.runtime_env import ce_multi_user

        if ce_multi_user():
            from novelvideo.api.routes.auth import bootstrap_if_env_password

            await bootstrap_if_env_password()

        await get_port("lifecycle").on_startup(register_as_worker=True)

        from novelvideo.sqlite_pragmas import litestream_enabled

        if litestream_enabled():
            from novelvideo.backup.wal_migrator import migrate_state_tree
            from novelvideo.config import STATE_DIR

            try:
                await asyncio.to_thread(migrate_state_tree, Path(STATE_DIR))
            except Exception:
                logger.exception("WAL migration sweep failed (non-fatal)")

        from novelvideo.catalog.official_media_catalog_remote import (
            run_official_media_catalog_updater,
        )
        from novelvideo.shared.runtime_env import is_ce_effective

        if is_ce_effective():
            application.state.official_media_catalog_updater = asyncio.create_task(
                run_official_media_catalog_updater(),
                name="official-media-catalog-updater",
            )

            # [aipdd fork delta] WP-A: 升级后一次性把网关 channels.model_mapping
            # 反向导入项目自有真源。后台跑：网关未就绪 / 拉取失败都只记日志，
            # 不会阻断启动（真源为空时投影仍会按现有写路径逐步建立）。
            application.state.alias_mapping_backfill = asyncio.create_task(
                _backfill_model_alias_mappings(),
                name="model-alias-mapping-backfill",
            )
    except Exception:
        logger.exception("API startup failed while connecting to control-plane")
        raise

    try:
        yield
    finally:
        from novelvideo.ports.registry import PortNotRegistered, get_port

        updater = getattr(application.state, "official_media_catalog_updater", None)
        if updater is not None:
            updater.cancel()
            with suppress(asyncio.CancelledError):
                await updater
        backfill = getattr(application.state, "alias_mapping_backfill", None)
        if backfill is not None and not backfill.done():
            backfill.cancel()
            with suppress(asyncio.CancelledError):
                await backfill

        try:
            lifecycle = get_port("lifecycle")
        except PortNotRegistered:
            lifecycle = None
        if lifecycle is not None:
            await lifecycle.on_shutdown()


def create_app() -> FastAPI:
    register_verification_routes()

    application = FastAPI(
        title="NovelVideo API", openapi_tags=OPENAPI_TAGS, lifespan=_lifespan
    )
    mount_api_coverage_middleware(application)

    @application.exception_handler(ProjectTaskLimitExceeded)
    async def _project_task_limit_exceeded(
        request: Request,
        exc: ProjectTaskLimitExceeded,
    ) -> JSONResponse:
        _ = request
        log_task_limit_rejection(exc, limit_scope="project")
        return JSONResponse(
            status_code=429,
            content={
                "ok": False,
                "error": f"当前项目 {exc.queue_kind} 队列任务已满，请等待已有任务完成后再提交",
                "data": {
                    "project_id": exc.project_id,
                    "queue_kind": exc.queue_kind,
                    "limit": exc.limit,
                    "active": exc.active,
                    "limit_scope": "project",
                },
            },
        )

    @application.exception_handler(ProjectUserTaskLimitExceeded)
    async def _project_user_task_limit_exceeded(
        request: Request,
        exc: ProjectUserTaskLimitExceeded,
    ) -> JSONResponse:
        _ = request
        log_task_limit_rejection(exc, limit_scope="user")
        return JSONResponse(
            status_code=429,
            content={
                "ok": False,
                "error": (
                    f"你在当前项目 {exc.queue_kind} 队列任务已满，"
                    "请等待自己的任务完成后再提交"
                ),
                "data": {
                    "project_id": exc.project_id,
                    "requester_user_id": exc.requester_user_id,
                    "queue_kind": exc.queue_kind,
                    "limit": exc.limit,
                    "active": exc.active,
                    "limit_scope": "user",
                },
            },
        )

    @application.exception_handler(GlobalLaneQueueLimitExceeded)
    async def _global_lane_queue_limit_exceeded(
        request: Request,
        exc: GlobalLaneQueueLimitExceeded,
    ) -> JSONResponse:
        _ = request
        log_task_limit_rejection(exc, limit_scope="global_lane_queue")
        return JSONResponse(
            status_code=429,
            content={
                "ok": False,
                "error": f"当前节点 {exc.queue_kind} 队列已满，请稍后再提交",
                "data": {
                    "project_id": exc.project_id,
                    "queue_kind": exc.queue_kind,
                    "limit": exc.limit,
                    "queued": exc.queued,
                    "limit_scope": "global_lane_queue",
                },
            },
        )

    @application.exception_handler(ChannelTaskLimitExceeded)
    async def _channel_task_limit_exceeded(
        request: Request,
        exc: ChannelTaskLimitExceeded,
    ) -> JSONResponse:
        _ = request
        limit_scope = "platform" if exc.scope_kind == "platform" else "channel"
        log_task_limit_rejection(exc, limit_scope=limit_scope)
        if exc.scope_kind == "platform":
            # 共享池(``org_id is None``)拦下的是不归属任何渠道的请求,对他们
            # 说"渠道"没有对应概念;而池子是全平台共享的,"等待已有任务完成"
            # 也无从等起 —— 撞闸的人自己可能一个任务都没在跑。
            message = f"当前平台 {exc.queue_kind} 队列任务已满，请稍后再试"
        else:
            message = (
                f"当前渠道 {exc.queue_kind} 队列任务已满，请等待已有任务完成后再提交"
            )
        return JSONResponse(
            status_code=429,
            content={
                "ok": False,
                "error": message,
                "data": {
                    "scope_kind": exc.scope_kind,
                    "org_id": exc.org_id,
                    "queue_kind": exc.queue_kind,
                    "limit": exc.limit,
                    "active": exc.active,
                    "limit_scope": limit_scope,
                },
            },
        )

    @application.exception_handler(UserTaskLimitExceeded)
    async def _user_task_limit_exceeded(
        request: Request,
        exc: UserTaskLimitExceeded,
    ) -> JSONResponse:
        _ = request
        log_task_limit_rejection(exc, limit_scope="user")
        return JSONResponse(
            status_code=429,
            content={
                "ok": False,
                "error": (
                    f"你在 {exc.queue_kind} 队列的在途任务已满，"
                    "请等待自己的任务完成后再提交"
                ),
                "data": {
                    "requester_user_id": exc.requester_user_id,
                    "queue_kind": exc.queue_kind,
                    "limit": exc.limit,
                    "active": exc.active,
                    "limit_scope": "user",
                },
            },
        )

    # [aipdd fork delta] WP-D: 额度体系已拆除，不再渲染「积分不足」的 402；
    # 上游账户配额不足由 task_backend.run_core 转成 UPSTREAM_QUOTA_EXCEEDED 任务失败。

    # [aipdd fork delta req40] 模型槽位解析不到（没同步过目录 / 功能位没绑 / 绑的名字
    # 已从渠道下架）属于**配置缺口**，不是故障：必须 400 中文并指到「设置 → 模型与渠道」，
    # 既不能 500 把成因糊掉，也不能让任务带着空模型名打到网关再撞 404（req39 那次跑到
    # 55% 才死就是这条路）。它继承 RuntimeError，所以既有的兜底分支依旧接得住。
    @application.exception_handler(ModelSlotNotConfigured)
    async def _model_slot_not_configured(
        request: Request,
        exc: ModelSlotNotConfigured,
    ) -> JSONResponse:
        logger.warning(
            "model slot unresolved: slot=%s reason=%s method=%s path=%s",
            exc.slot,
            exc.reason,
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": exc.message,
                "detail": exc.message,
                "data": {"slot": exc.slot, "reason": exc.reason},
            },
        )

    @application.exception_handler(BillingRuleNotConfiguredError)
    async def _billing_rule_not_configured(
        request: Request,
        exc: BillingRuleNotConfiguredError,
    ) -> JSONResponse:
        _ = request
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "error": BILLING_RULE_NOT_CONFIGURED_MESSAGE,
                "data": billing_rule_not_configured_payload(exc),
            },
        )

    @application.exception_handler(BillingError)
    async def _billing_error(
        request: Request,
        exc: BillingError,
    ) -> JSONResponse:
        _ = request
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "ok": False,
                "error": exc.user_message,
                "data": billing_error_payload(exc),
            },
        )

    @application.exception_handler(AuthzError)
    async def _authz_error(
        request: Request,
        exc: AuthzError,
    ) -> JSONResponse:
        # Without this handler the denial escaped to Starlette's
        # ServerErrorMiddleware, which answers text/plain 500 — no machine code
        # for the frontend and, because nothing on this path logged, no trace
        # for whoever triages it.
        logger.warning(
            "organization authorization denied: code=%s status=%s method=%s path=%s",
            exc.code,
            exc.http_status,
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "ok": False,
                "error": exc.user_message,
                "data": authz_error_payload(exc),
            },
        )

    @application.middleware("http")
    async def _limit_body_size(request: Request, call_next):
        limit = _request_body_limit(request)
        cl = request.headers.get("content-length")
        if cl:
            try:
                if int(cl) > limit:
                    if _is_freezone_audio_voice_upload(request):
                        return JSONResponse(
                            status_code=200,
                            content={
                                "ok": False,
                                "error": "参考音频超过 5MB 上限，请压缩或裁剪后重新上传",
                                "data": {
                                    "code": "freezone_audio_voice_too_large",
                                    "field": "file",
                                    "limit": limit,
                                    "got": int(cl),
                                },
                            },
                        )
                    return JSONResponse(
                        status_code=413,
                        content={
                            "detail": {
                                "code": "canvas_payload_too_large",
                                "field": "body",
                                "limit": limit,
                                "got": int(cl),
                            }
                        },
                    )
            except ValueError:
                pass
        elif _rejects_undeclared_chunked_body(request):
            return JSONResponse(
                status_code=411,
                content={
                    "detail": {
                        "code": "content_length_required",
                        "field": "body",
                        "message": "该接口不接受无 Content-Length 的分块（chunked）请求体",
                    }
                },
            )
        return await call_next(request)

    @application.middleware("http")
    async def _log_resource_requests(request: Request, call_next):
        resource_key = _resource_request_key(request.url.path)
        if resource_key is None:
            return await call_next(request)

        started_at = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - started_at) * 1000
        total_count, same_resource_count = _record_resource_request(resource_key)

        query = _redact_query(request.url.query)
        range_header = request.headers.get("range", "")
        content_length = response.headers.get("content-length", "")
        content_type = response.headers.get("content-type", "")
        logger.info(
            "resource request total=%s same_resource=%s method=%s status=%s "
            "duration_ms=%.1f path=%s query=%s range=%s bytes=%s content_type=%s",
            total_count,
            same_resource_count,
            request.method,
            response.status_code,
            duration_ms,
            resource_key,
            query or "-",
            range_header or "-",
            content_length or "-",
            content_type or "-",
        )
        return response

    @application.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    application.include_router(api_router)

    @application.get(
        "/static/projects/{project}/{file_path:path}", include_in_schema=False
    )
    async def static_project_media(
        project: str,
        file_path: str,
        request: Request,
        st_thumb: str | None = None,
        user: dict = Depends(get_api_user),
    ):
        return await preview_project_media_file(
            project, file_path, user, st_thumb=st_thumb, request=request
        )

    @application.get("/static/{legacy_path:path}", include_in_schema=False)
    async def legacy_static_media(legacy_path: str):
        _ = legacy_path
        return PlainTextResponse(
            "legacy static path; use /static/projects/<project_id>/...\n",
            status_code=410,
        )

    # 原生/便携部署(无 nginx)时由后端直接伺服 SPA:设 AIPDD_DRAMA_FRONTEND_DIST
    # （旧名 DRAMACLAW_FRONTEND_DIST 继续生效）指向前端构建产物目录才挂载;
    # Docker/EE 路径不设该变量,行为不变。
    # 挂在所有路由之后,/api、/healthz、/static 仍然优先命中。
    frontend_dist = (
        os.environ.get("AIPDD_DRAMA_FRONTEND_DIST", "").strip()
        or os.environ.get("DRAMACLAW_FRONTEND_DIST", "").strip()
    )
    if frontend_dist and Path(frontend_dist).is_dir():
        from fastapi.staticfiles import StaticFiles
        from starlette.exceptions import HTTPException as _StarletteHTTPException

        class _SpaStaticFiles(StaticFiles):
            """SPA fallback: unknown extensionless paths serve index.html.

            StaticFiles 未命中时 raise HTTPException(404)(仅 dist 含 404.html
            时才返回 404 响应),回落必须捕获异常;带扩展名的缺失资产照常 404。
            """

            async def get_response(self, path: str, scope):  # type: ignore[override]
                try:
                    return await super().get_response(path, scope)
                except _StarletteHTTPException as exc:
                    if exc.status_code == 404 and "." not in Path(path).name:
                        return await super().get_response("index.html", scope)
                    raise

        application.mount(
            "/", _SpaStaticFiles(directory=frontend_dist, html=True), name="spa"
        )

        # 仅在自伺服（无 nginx）分支注册：给 SPA+API 补上最小安全头。用 setdefault
        # 不覆盖处理程序已显式设置的更特定头。
        @application.middleware("http")
        async def _spa_security_headers(request: Request, call_next):
            response = await call_next(request)
            for _header_key, _header_value in _SPA_SECURITY_HEADERS.items():
                response.headers.setdefault(_header_key, _header_value)
            return response

    return application


app = create_app()
