# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] Agent API 路由：会话 / 消息（SSE）/ 记忆 / 技能 / MCP / 设置 / 入库。

挂载在 ``/api/v1/agent/*``，由 ``novelvideo.agent.router`` 暴露给 api/__init__.py 注册。
低耦合约定：只依赖既有稳定接口——api.auth（鉴权）、api.deps（项目解析）、
freezone.video_node（资产库登记）、freezone.paths（静态 URL 反解）、
utils.static_urls（静态 URL 生成）。

MCP 管理接口（/agent/mcp/servers*）同时服务于 MCP 栏目页面——按产品要求，
本系统所有 MCP 的配置管理都归 MCP 栏目，Agent 页只显示状态摘要。

SSE 协议（POST .../messages 的响应流）：每行 ``data: {json}``，事件类型：
- delta      正文增量 {"text"}
- tool_start 工具开始 {"name","server","arguments"}
- tool_end   工具结束 {"name","server","ok","summary"}
- media      产出媒体 {"media":{...}}（前端预览 + 右键存入资产库）
- notice     提示信息 {"text"}
- done       回合完成 {"message":{...落库消息}}
- error      失败 {"message","message_code"?,"message_params"?}（后两者见 i18n_message 约定）
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from novelvideo.agent import engine as agent_engine
from novelvideo.agent import gateway as agent_gateway
from novelvideo.agent import local_tools as agent_local_tools
from novelvideo.agent import mcp as agent_mcp
from novelvideo.agent import memory as agent_memory
from novelvideo.agent import plans as agent_plans
from novelvideo.agent import skills as agent_skills
from novelvideo.agent import store as agent_store
from novelvideo.i18n_message import MessageLike, lmsg, message_payload, message_text
from novelvideo.utils.net_guard import PublicUrlRejected, ensure_public_http_url

logger = logging.getLogger("novelvideo.agent.routes")

router = APIRouter(prefix="/agent")


async def require_api_user(request: Request) -> dict:
    """Agent 路由的鉴权依赖：转调 ``api.auth.get_api_user``。

    api/__init__.py 在包初始化期间执行 ``from novelvideo.agent import router``，
    而 ``novelvideo.api.auth`` 的模块级导入会反向触发 api 包初始化——直接导入
    会形成循环（先导入 novelvideo.agent.* 的一方失败）；改为本包装依赖后，
    api 包先完成初始化，请求期再解析真实实现，两条导入路径都成立。
    """
    from novelvideo.api.auth import get_api_user

    return await get_api_user(request)


async def require_interactive_user(request: Request) -> dict:
    """[aipdd fork delta] 本地工具接入的写操作依赖：只放行交互式会话。

    连接会为本机工具派生统一密钥，与 api_keys 端点同一边界：机器凭证
    （agent 会话 / API 密钥）不能派生新密钥，否则一把泄漏的 key 可无限派生。
    """
    user = await require_api_user(request)
    if str(user.get("credential_kind") or "") in {"agent_session", "api_key"}:
        raise HTTPException(status_code=403, detail="browser session required")
    return user


# 运行中的回合任务（会话 id → Task）。单进程部署假设：cancel 端点与 SSE 端点
# 位于同一个 API 进程；本地/CE 单 worker 场景成立。
_running_tasks: dict[str, asyncio.Task] = {}
_running_lock = asyncio.Lock()

_LIBRARY_SAVE_MAX_BYTES = 128 * 1024 * 1024
_MEDIA_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".mp4", ".webm", ".mov", ".mp3", ".wav", ".m4a", ".ogg",
}


def _username(user: dict) -> str:
    return str(user.get("username") or user.get("user_id") or user.get("id") or "anonymous")


def _default_text_model() -> str:
    try:
        from novelvideo.model_gateway_settings import get_effective_default_text_model

        return str(get_effective_default_text_model() or "").strip()
    except Exception:  # noqa: BLE001 - 状态展示尽力而为
        return ""


def _default_text_model_cause() -> str:
    """默认文本模型解析不到时的**成因原文**（不吞 ``ModelSlotNotConfigured``）。

    [aipdd fork delta] ``get_effective_default_text_model()`` 按 req40 口径只是状态回显，
    解析异常被吞成空串。Agent 提交前拒绝必须把真实成因（哪个功能位没绑 / 渠道没同步清单 /
    绑定目标已下架）带给用户，否则只剩「请在设置中配置默认文本模型」一句空话——而图片/视
    频回合根本不提供文本模型设置入口，用户按这句话找不到东西可配，就成了一条死路。
    """
    try:
        from novelvideo.config import get_newapi_text_model_name
        from novelvideo.official_defaults import DEFAULT_FREEZONE_TEXT_WRITER_MODEL

        get_newapi_text_model_name(
            "FREEZONE_TEXT_WRITER_MODEL", DEFAULT_FREEZONE_TEXT_WRITER_MODEL
        )
    except Exception as exc:  # noqa: BLE001 - 成因原文就是给用户看的
        return str(exc).strip()
    return ""


def _text_model_unresolved_detail() -> str:
    """文本模型解析不到时的 400 文案：讲清为什么必需 + 实际成因 + 就地可执行的动作。"""
    cause = _default_text_model_cause()
    head = (
        "文本模型未就绪：不论文字、图片还是视频回合，都先由文本模型理解需求并发起生成，"
        "所以它是必需项。"
    )
    if cause:
        head += f"默认文本模型解析失败：{cause}"
    else:
        head += "当前既没有选中文本模型，也没有解析到系统默认文本模型。"
    return head + "请直接在输入框左侧的「文本模型」下拉里选一个，或到「设置 → 模型与渠道」绑定文本功能位。"


def _default_image_model() -> str:
    try:
        from novelvideo.model_gateway_settings import get_effective_default_image_model

        return str(get_effective_default_image_model() or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _default_video_model() -> str:
    try:
        from novelvideo.model_gateway_settings import get_effective_default_video_model

        return str(get_effective_default_video_model() or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _sse(event: dict[str, Any]) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


def _error_event(message: MessageLike) -> dict[str, Any]:
    """失败事件的 SSE 载荷：中文兜底 + 可选 ``message_code`` / ``message_params``。

    带 code 时前端按词条本地化（与排队 toast 的 message_code 同约定），
    未迁移的旧前端直接回显 ``message`` 里的中文兜底。
    """
    event: dict[str, Any] = {"type": "error", "message": message_text(message)}
    payload = message_payload(message)
    if payload:
        event["message_code"] = payload["code"]
        if "params" in payload:
            event["message_params"] = payload["params"]
    return event


# ---------------------------------------------------------------------------
# 状态 / 设置
# ---------------------------------------------------------------------------


@router.get("/status")
async def agent_status(user: dict = Depends(require_api_user)) -> dict[str, Any]:
    """Agent 概览：网关配置、默认模型、技能与 MCP 计数。"""
    username = _username(user)
    gateway_configured = True
    gateway_error = ""
    try:
        agent_gateway.gateway_credentials()
    except agent_gateway.GatewayNotConfigured as exc:
        gateway_configured = False
        gateway_error = str(exc)
    skills_list = agent_skills.list_skills(username)
    servers = agent_mcp.list_servers(username)
    return {
        "ok": True,
        "data": {
            "gateway_configured": gateway_configured,
            "gateway_error": gateway_error,
            "default_text_model": _default_text_model(),
            "default_image_model": _default_image_model(),
            "default_video_model": _default_video_model(),
            "text_model": agent_store.get_setting(username, "text_model", ""),
            "image_model": agent_store.get_setting(username, "image_model", ""),
            "video_model": agent_store.get_setting(username, "video_model", ""),
            "skills_total": len(skills_list),
            "skills_enabled": sum(1 for item in skills_list if item.get("enabled")),
            "mcp_servers_total": len(servers),
            "mcp_servers_enabled": sum(1 for item in servers if item.get("enabled")),
        },
    }


class AgentSettingsBody(BaseModel):
    text_model: str | None = None
    image_model: str | None = None
    video_model: str | None = None


def _settings_payload(username: str) -> dict[str, Any]:
    return {
        "text_model": agent_store.get_setting(username, "text_model", ""),
        "image_model": agent_store.get_setting(username, "image_model", ""),
        "video_model": agent_store.get_setting(username, "video_model", ""),
        "default_text_model": _default_text_model(),
        "default_image_model": _default_image_model(),
        "default_video_model": _default_video_model(),
    }


@router.get("/settings")
async def agent_get_settings(user: dict = Depends(require_api_user)) -> dict[str, Any]:
    return {"ok": True, "data": _settings_payload(_username(user))}


@router.put("/settings")
async def agent_update_settings(
    body: AgentSettingsBody, user: dict = Depends(require_api_user)
) -> dict[str, Any]:
    username = _username(user)
    if body.text_model is not None:
        agent_store.set_setting(username, "text_model", body.text_model.strip())
    if body.image_model is not None:
        agent_store.set_setting(username, "image_model", body.image_model.strip())
    if body.video_model is not None:
        agent_store.set_setting(username, "video_model", body.video_model.strip())
    return {"ok": True, "data": _settings_payload(username)}


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------


class ConversationCreateBody(BaseModel):
    title: str = ""
    project: str = ""


class ConversationPatchBody(BaseModel):
    title: str | None = None
    project: str | None = None


def _require_conversation(username: str, conversation_id: str) -> dict[str, Any]:
    conversation = agent_store.get_conversation(username, conversation_id)
    if conversation is None:
        raise HTTPException(404, "会话不存在")
    return conversation


@router.get("/conversations")
async def agent_list_conversations(
    project: str = Query(""), user: dict = Depends(require_api_user)
) -> dict[str, Any]:
    data = agent_store.list_conversations(_username(user), project.strip() or None)
    return {"ok": True, "data": data}


@router.post("/conversations")
async def agent_create_conversation(
    body: ConversationCreateBody, user: dict = Depends(require_api_user)
) -> dict[str, Any]:
    conversation = agent_store.create_conversation(
        _username(user), project=body.project.strip(), title=body.title.strip()
    )
    return {"ok": True, "data": conversation}


@router.patch("/conversations/{conversation_id}")
async def agent_update_conversation(
    conversation_id: str,
    body: ConversationPatchBody,
    user: dict = Depends(require_api_user),
) -> dict[str, Any]:
    username = _username(user)
    _require_conversation(username, conversation_id)
    updated = agent_store.update_conversation(
        username,
        conversation_id,
        title=body.title,
        project=body.project,
    )
    return {"ok": True, "data": updated}


@router.delete("/conversations/{conversation_id}")
async def agent_delete_conversation(
    conversation_id: str, user: dict = Depends(require_api_user)
) -> dict[str, Any]:
    username = _username(user)
    _require_conversation(username, conversation_id)
    ok = agent_store.delete_conversation(username, conversation_id)
    return {"ok": True, "data": {"deleted": ok}}


@router.get("/conversations/{conversation_id}/messages")
async def agent_list_messages(
    conversation_id: str, user: dict = Depends(require_api_user)
) -> dict[str, Any]:
    username = _username(user)
    _require_conversation(username, conversation_id)
    return {"ok": True, "data": agent_store.list_messages(username, conversation_id)}


@router.delete("/conversations/{conversation_id}/messages")
async def agent_clear_messages(
    conversation_id: str, user: dict = Depends(require_api_user)
) -> dict[str, Any]:
    username = _username(user)
    _require_conversation(username, conversation_id)
    removed = agent_store.clear_messages(username, conversation_id)
    return {"ok": True, "data": {"removed": removed}}


# ---------------------------------------------------------------------------
# 对话回合（SSE）
# ---------------------------------------------------------------------------


class TurnReferenceBody(BaseModel):
    """回合参考素材（用户在输入区从资产库选择的条目）。"""

    name: str = ""
    url: str = ""
    media: str = "image"


class SendMessageBody(BaseModel):
    content: str
    project: str = ""
    model: str = ""
    image_model: str = ""
    video_model: str = ""
    # 对话类型：text（默认，LLM 对话）/ image / video——决定生成工具优先使用哪类模型。
    mode: str = "text"
    # [aipdd fork delta] 输入区显式设定的回合媒体参数（用户在 Composer 选了才带）：
    # 工具执行时优先于 LLM 自行解析的同类参数；未携带则保持原行为。
    image_size: str = ""
    video_resolution: str = ""
    video_duration: int | None = None
    # [aipdd fork delta] 视频超清开关（req22）：非空 = 基础分辨率成片后再走高清
    # 增强；只接受 1080p/2k/4k（与画布高清同档位），其余一律当未开启。
    video_upscale_resolution: str = ""
    references: list[TurnReferenceBody] = Field(default_factory=list)


# 超清档位白名单（小写比较；与 FreezoneVideoUpscaleRequest.resolution 同口径）。
_VIDEO_UPSCALE_RESOLUTIONS = {"1080p", "2k", "4k"}


def _clean_video_upscale_resolution(value: str) -> str:
    """超清档位归一：白名单外（含空）一律为空 = 未开启两步处理。"""
    cleaned = str(value or "").strip().lower()
    return cleaned if cleaned in _VIDEO_UPSCALE_RESOLUTIONS else ""


# 回合参考素材的安全上限：条数 / 字段长度（防注入超长文本）。
_REFERENCE_LIMIT = 12
_REFERENCE_NAME_MAX = 200
_REFERENCE_URL_MAX = 2000


def _clean_references(items: list[TurnReferenceBody]) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for item in items[:_REFERENCE_LIMIT]:
        name = str(item.name or "").strip()[:_REFERENCE_NAME_MAX]
        url = str(item.url or "").strip()[:_REFERENCE_URL_MAX]
        media = str(item.media or "image").strip().lower()
        if media not in ("image", "video", "audio"):
            media = "image"
        if not name and not url:
            continue
        cleaned.append({"name": name, "url": url, "media": media})
    return cleaned


async def _resolve_project_dir(project: str, user: dict) -> Path | None:
    """解析项目目录（editor 权限）；空项目返回 None。"""
    if not project:
        return None
    from novelvideo.api.deps import resolve_project_scope

    resolution = await resolve_project_scope(project, user, required_role="editor")
    return Path(resolution.project_dir)


@router.post("/conversations/{conversation_id}/messages")
async def agent_send_message(
    conversation_id: str,
    body: SendMessageBody,
    user: dict = Depends(require_api_user),
) -> StreamingResponse:
    """发送一条消息并流式返回回合事件（SSE）。"""
    username = _username(user)
    conversation = _require_conversation(username, conversation_id)
    content = str(body.content or "").strip()
    if not content:
        raise HTTPException(400, "消息内容不能为空")
    async with _running_lock:
        running = _running_tasks.get(conversation_id)
        if running is not None and not running.done():
            raise HTTPException(409, "当前会话有正在进行的回复，请先等待或停止")
    project = str(body.project or conversation.get("project") or "").strip()
    project_dir = await _resolve_project_dir(project, user)
    settings = _settings_payload(username)
    model = str(body.model or settings.get("text_model") or settings.get("default_text_model") or "").strip()
    image_model = str(
        body.image_model or settings.get("image_model") or settings.get("default_image_model") or ""
    ).strip()
    video_model = str(
        body.video_model or settings.get("video_model") or settings.get("default_video_model") or ""
    ).strip()
    mode = str(body.mode or "text").strip().lower()
    if mode not in ("text", "image", "video"):
        mode = "text"
    # 回合媒体参数：宽松清洗 —— 长度截断（stripped），时长只接受 1..600 的整数秒，
    # 越界一律当「未设定」。合法性精确到目录档位的校验由前端按目录声明完成。
    image_size = str(body.image_size or "").strip()[:64]
    video_resolution = str(body.video_resolution or "").strip()[:64]
    video_duration = body.video_duration
    if not isinstance(video_duration, int) or not 1 <= video_duration <= 600:
        video_duration = None
    # 超清档位：白名单外（含空）一律归一为空 = 未开启两步处理。
    video_upscale_resolution = _clean_video_upscale_resolution(body.video_upscale_resolution)
    references = _clean_references(list(body.references or []))
    if not model:
        # 架构上每个回合都由文本模型驱动（engine.py 的网关调用只吃 self.model），所以这里
        # 不分 mode 一律拦下是对的；文案必须给成因和出路，不能只把用户丢去设置页。
        raise HTTPException(400, _text_model_unresolved_detail())
    engine = agent_engine.AgentEngine(
        username=username,
        conversation_id=conversation_id,
        project=project,
        project_dir=project_dir,
        model=model,
        image_model=image_model,
        video_model=video_model,
        mode=mode,
        image_size=image_size,
        video_resolution=video_resolution,
        video_duration=video_duration,
        video_upscale_resolution=video_upscale_resolution,
        references=references,
    )

    async def event_source() -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        def emit(event: dict[str, Any]) -> None:
            queue.put_nowait(event)

        async def runner() -> None:
            try:
                message = await engine.run(content, emit)
                queue.put_nowait({"type": "done", "message": message})
            except agent_gateway.GatewayNotConfigured as exc:
                queue.put_nowait({"type": "error", "message": str(exc)})
            except agent_gateway.GatewayError as exc:
                queue.put_nowait({"type": "error", "message": str(exc)})
            except asyncio.CancelledError:
                queue.put_nowait(_error_event(lmsg("agent.error.interrupted", "回合已中断")))
            except Exception as exc:  # noqa: BLE001 - 任何执行错误都转为流内事件
                logger.exception("agent turn failed: conversation=%s", conversation_id)
                queue.put_nowait(
                    _error_event(
                        lmsg("agent.error.turnFailed", f"回合执行失败：{exc}", detail=str(exc))
                    )
                )
            finally:
                queue.put_nowait(None)

        task = asyncio.create_task(runner())
        async with _running_lock:
            _running_tasks[conversation_id] = task
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # 心跳注释行，避免中间代理断连
                    continue
                if event is None:
                    break
                yield _sse(event)
        finally:
            async with _running_lock:
                if _running_tasks.get(conversation_id) is task:
                    _running_tasks.pop(conversation_id, None)
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/conversations/{conversation_id}/cancel")
async def agent_cancel_turn(
    conversation_id: str, user: dict = Depends(require_api_user)
) -> dict[str, Any]:
    """中断当前回合（前端「停止生成」按钮）。"""
    username = _username(user)
    _require_conversation(username, conversation_id)
    async with _running_lock:
        task = _running_tasks.get(conversation_id)
    if task is None or task.done():
        return {"ok": True, "data": {"cancelled": False}}
    task.cancel()
    return {"ok": True, "data": {"cancelled": True}}


# ---------------------------------------------------------------------------
# 记忆
# ---------------------------------------------------------------------------


class MemoryBody(BaseModel):
    content: str = ""


@router.get("/memory")
async def agent_get_memory(user: dict = Depends(require_api_user)) -> dict[str, Any]:
    return {"ok": True, "data": {"content": agent_memory.read_memory(_username(user))}}


@router.put("/memory")
async def agent_put_memory(
    body: MemoryBody, user: dict = Depends(require_api_user)
) -> dict[str, Any]:
    content = agent_memory.write_memory(_username(user), body.content)
    return {"ok": True, "data": {"content": content}}


# ---------------------------------------------------------------------------
# 待办计划（项目级；模型用 agent_update_plan 工具维护，面板提供手动增删改）
# ---------------------------------------------------------------------------


class PlanItemCreateBody(BaseModel):
    project: str
    title: str


class PlanItemPatchBody(BaseModel):
    project: str
    title: str | None = None
    done: bool | None = None


async def _resolve_plan_dir(project: str, user: dict, *, required_role: str) -> Path:
    """解析计划所属的项目目录（读取 viewer / 修改 editor）。"""
    value = str(project or "").strip()
    if not value:
        raise HTTPException(400, "project is required")
    from novelvideo.api.deps import resolve_project_scope

    resolution = await resolve_project_scope(value, user, required_role=required_role)
    return Path(resolution.project_dir)


def _plan_payload(project_dir: Path) -> dict[str, Any]:
    return {
        "items": agent_plans.load_plan(project_dir),
        "max_items": agent_plans.PLAN_MAX_ITEMS,
    }


@router.get("/plan")
async def agent_get_plan(
    project: str = Query(""), user: dict = Depends(require_api_user)
) -> dict[str, Any]:
    project_dir = await _resolve_plan_dir(project, user, required_role="viewer")
    return {"ok": True, "data": _plan_payload(project_dir)}


@router.post("/plan/items")
async def agent_add_plan_item(
    body: PlanItemCreateBody, user: dict = Depends(require_api_user)
) -> dict[str, Any]:
    project_dir = await _resolve_plan_dir(body.project, user, required_role="editor")
    items, added = agent_plans.add_items(project_dir, [body.title], source="user")
    if not added:
        raise HTTPException(400, "任务标题为空或与未完成任务重复")
    return {"ok": True, "data": {"item": added[0], "items": items, "max_items": agent_plans.PLAN_MAX_ITEMS}}


@router.patch("/plan/items/{item_id}")
async def agent_update_plan_item(
    item_id: str, body: PlanItemPatchBody, user: dict = Depends(require_api_user)
) -> dict[str, Any]:
    if body.title is None and body.done is None:
        raise HTTPException(400, "title 或 done 至少提供一个")
    project_dir = await _resolve_plan_dir(body.project, user, required_role="editor")
    try:
        item = agent_plans.update_item(
            project_dir, item_id, title=body.title, done=body.done
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if item is None:
        raise HTTPException(404, "任务不存在")
    return {"ok": True, "data": {**_plan_payload(project_dir), "item": item}}


@router.delete("/plan/items/{item_id}")
async def agent_delete_plan_item(
    item_id: str, project: str = Query(""), user: dict = Depends(require_api_user)
) -> dict[str, Any]:
    project_dir = await _resolve_plan_dir(project, user, required_role="editor")
    if not agent_plans.remove_item(project_dir, item_id):
        raise HTTPException(404, "任务不存在")
    return {"ok": True, "data": _plan_payload(project_dir)}


# ---------------------------------------------------------------------------
# 技能
# ---------------------------------------------------------------------------


class SkillEnabledBody(BaseModel):
    enabled: bool


@router.get("/skills")
async def agent_list_skills(user: dict = Depends(require_api_user)) -> dict[str, Any]:
    return {"ok": True, "data": agent_skills.list_skills(_username(user))}


@router.post("/skills/install")
async def agent_install_skill(
    file: UploadFile = File(...), user: dict = Depends(require_api_user)
) -> dict[str, Any]:
    """安装技能：上传 SKILL.md（.md）或技能包（.zip）。"""
    username = _username(user)
    payload = await file.read()
    try:
        result = agent_skills.install_skill(
            username, filename=file.filename or "", content=payload
        )
    except agent_skills.SkillError as exc:
        raise HTTPException(400, str(exc)) from exc
    detail = agent_skills.get_skill(username, result["name"])
    return {"ok": True, "data": {"skill": detail, "updated": result["updated"]}}


@router.get("/skills/{name}")
async def agent_get_skill(name: str, user: dict = Depends(require_api_user)) -> dict[str, Any]:
    detail = agent_skills.get_skill(_username(user), name)
    if detail is None:
        raise HTTPException(404, "技能不存在")
    return {"ok": True, "data": detail}


@router.patch("/skills/{name}")
async def agent_set_skill_enabled(
    name: str, body: SkillEnabledBody, user: dict = Depends(require_api_user)
) -> dict[str, Any]:
    detail = agent_skills.set_skill_enabled(_username(user), name, bool(body.enabled))
    if detail is None:
        raise HTTPException(404, "技能不存在")
    return {"ok": True, "data": detail}


@router.delete("/skills/{name}")
async def agent_delete_skill(name: str, user: dict = Depends(require_api_user)) -> dict[str, Any]:
    deleted = agent_skills.delete_skill(_username(user), name)
    if not deleted:
        raise HTTPException(404, "技能不存在")
    return {"ok": True, "data": {"deleted": True}}


# ---------------------------------------------------------------------------
# MCP（配置管理归 MCP 栏目使用）
# ---------------------------------------------------------------------------


class McpServerBody(BaseModel):
    id: str = ""
    name: str = ""
    description: str = ""
    transport: str = "stdio"
    command: str = ""
    args: list[str] = []
    env: dict[str, str] = {}
    url: str = ""
    headers: dict[str, str] = {}
    enabled: bool = True


class McpEnabledBody(BaseModel):
    enabled: bool


@router.get("/mcp/servers")
async def agent_list_mcp_servers(user: dict = Depends(require_api_user)) -> dict[str, Any]:
    return {"ok": True, "data": agent_mcp.list_servers(_username(user))}


@router.get("/mcp/builtin-tools")
async def agent_builtin_mcp_tools(user: dict = Depends(require_api_user)) -> dict[str, Any]:
    """内置 DramaClaw 工具目录（MCP 栏目「工具」页：清单 + 字段 schema）。"""
    tools = await asyncio.to_thread(agent_mcp.builtin_tool_catalog)
    return {
        "ok": True,
        "data": {"server_id": agent_mcp.BUILTIN_SERVER_ID, "tools": tools},
    }


@router.post("/mcp/servers")
async def agent_upsert_mcp_server(
    body: McpServerBody, user: dict = Depends(require_api_user)
) -> dict[str, Any]:
    try:
        server = agent_mcp.upsert_server(_username(user), body.model_dump())
    except agent_mcp.McpConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "data": server}


@router.patch("/mcp/servers/{server_id}")
async def agent_set_mcp_server_enabled(
    server_id: str, body: McpEnabledBody, user: dict = Depends(require_api_user)
) -> dict[str, Any]:
    server = agent_mcp.set_server_enabled(_username(user), server_id, bool(body.enabled))
    if server is None:
        raise HTTPException(404, "MCP 服务器不存在")
    return {"ok": True, "data": server}


@router.delete("/mcp/servers/{server_id}")
async def agent_delete_mcp_server(
    server_id: str, user: dict = Depends(require_api_user)
) -> dict[str, Any]:
    try:
        deleted = agent_mcp.delete_server(_username(user), server_id)
    except agent_mcp.McpConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not deleted:
        raise HTTPException(404, "MCP 服务器不存在")
    return {"ok": True, "data": {"deleted": True}}


@router.post("/mcp/servers/{server_id}/probe")
async def agent_probe_mcp_server(
    server_id: str, user: dict = Depends(require_api_user)
) -> dict[str, Any]:
    """测试连接并列出该服务器的工具（MCP 栏目的「测试连接」按钮）。"""
    username = _username(user)
    server = agent_mcp.get_server(username, server_id)
    if server is None:
        raise HTTPException(404, "MCP 服务器不存在")
    result = await agent_mcp.probe_server(server)
    return {"ok": True, "data": result}


@router.post("/mcp/servers/reset")
async def agent_reset_mcp_servers(user: dict = Depends(require_api_user)) -> dict[str, Any]:
    """恢复默认 MCP 配置（只保留内置服务器：DramaClaw + Chrome MCP）。"""
    return {"ok": True, "data": agent_mcp.reset_servers(_username(user))}


# ---------------------------------------------------------------------------
# 本地 Agent 工具接入（Workbuddy / Claude Code / Cursor / Codex）
# ---------------------------------------------------------------------------


class LocalToolsBody(BaseModel):
    tools: list[str] = []
    project: str = ""


def _local_tools_state_file(username: str) -> Path:
    return agent_store.agent_root(username) / "local_tools.json"


def _load_local_tools_state(username: str) -> dict[str, Any]:
    path = _local_tools_state_file(username)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_local_tools_state(username: str, state: dict[str, Any]) -> None:
    path = _local_tools_state_file(username)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


@router.get("/local-tools")
async def agent_list_local_tools(user: dict = Depends(require_api_user)) -> dict[str, Any]:
    """本机已安装的 AI 客户端工具及其连接状态（宿主 home 经 compose 挂载探测）。"""
    home = agent_local_tools.host_home()
    return {
        "ok": True,
        "data": {
            "tools": await asyncio.to_thread(agent_local_tools.scan),
            "hostHomeAvailable": home.is_dir(),
            "hostHomePath": str(home),
        },
    }


@router.post("/local-tools/connect")
async def agent_connect_local_tools(
    body: LocalToolsBody, user: dict = Depends(require_interactive_user)
) -> dict[str, Any]:
    """把内置 DramaClaw MCP 写进勾选的本地工具配置（每工具生成一把统一密钥）。"""
    from novelvideo.ports.local.users import get_local_user_store

    username = _username(user)
    owner_id = str(user.get("user_id") or user.get("id") or "").strip()
    if not owner_id:
        raise HTTPException(400, "account not resolvable")
    store = get_local_user_store()
    state = _load_local_tools_state(username)
    results: list[dict[str, Any]] = []
    for raw_id in body.tools:
        tool_id = str(raw_id or "").strip()
        if not tool_id:
            continue
        try:
            old = state.get(tool_id)
            if isinstance(old, dict) and old.get("api_key_id"):
                try:
                    await store.revoke_api_key(str(old["api_key_id"]), user_id=owner_id)
                except Exception:  # noqa: BLE001 - 旧密钥吊销尽力而为，不阻断重连
                    logger.warning("local tool old key revoke failed: tool=%s", tool_id)
            record, raw_key = await store.create_api_key(
                owner_id, f"local-agent:{tool_id}"
            )
            entry = agent_local_tools.connection_entry(
                raw_key=raw_key, project_id=body.project
            )
            await asyncio.to_thread(
                agent_local_tools.write_connection, tool_id, entry
            )
            state[tool_id] = {
                "api_key_id": record.id,
                "api_key_prefix": record.key_prefix,
                "project_id": body.project,
                "connected_at": agent_store.now_iso(),
            }
            results.append({"id": tool_id, "ok": True})
        except Exception as exc:  # noqa: BLE001 - 单个工具失败不阻断其余
            logger.warning("local tool connect failed: tool=%s err=%s", tool_id, exc)
            results.append({"id": tool_id, "ok": False, "error": str(exc)})
    _save_local_tools_state(username, state)
    data = await asyncio.to_thread(agent_local_tools.scan)
    return {"ok": True, "data": {"tools": data, "results": results}}


@router.post("/local-tools/disconnect")
async def agent_disconnect_local_tools(
    body: LocalToolsBody, user: dict = Depends(require_interactive_user)
) -> dict[str, Any]:
    """移除勾选工具配置里的 dramaclaw 条目，并吊销对应统一密钥。"""
    from novelvideo.ports.local.users import get_local_user_store

    username = _username(user)
    owner_id = str(user.get("user_id") or user.get("id") or "").strip()
    store = get_local_user_store()
    state = _load_local_tools_state(username)
    results: list[dict[str, Any]] = []
    for raw_id in body.tools:
        tool_id = str(raw_id or "").strip()
        if not tool_id:
            continue
        try:
            await asyncio.to_thread(agent_local_tools.remove_connection, tool_id)
            old = state.pop(tool_id, None)
            if isinstance(old, dict) and old.get("api_key_id"):
                try:
                    await store.revoke_api_key(str(old["api_key_id"]), user_id=owner_id)
                except Exception:  # noqa: BLE001
                    logger.warning("local tool key revoke failed: tool=%s", tool_id)
            results.append({"id": tool_id, "ok": True})
        except Exception as exc:  # noqa: BLE001 - 单个工具失败不阻断其余
            logger.warning("local tool disconnect failed: tool=%s err=%s", tool_id, exc)
            results.append({"id": tool_id, "ok": False, "error": str(exc)})
    _save_local_tools_state(username, state)
    data = await asyncio.to_thread(agent_local_tools.scan)
    return {"ok": True, "data": {"tools": data, "results": results}}


# ---------------------------------------------------------------------------
# 媒体与「存入资产库」
# ---------------------------------------------------------------------------


@router.get("/media/{filename}")
async def agent_media(filename: str, user: dict = Depends(require_api_user)) -> FileResponse:
    """全局会话（无项目）生成的媒体文件访问端点。"""
    safe_name = Path(filename).name
    path = agent_store.agent_root(_username(user)) / "media" / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(path)


class LibrarySaveBody(BaseModel):
    project: str
    url: str
    name: str
    media: str = "image"
    folder: str = ""
    category: str = ""


def _media_suffix(media: str, url: str, content_type: str) -> str:
    content_type = str(content_type or "").lower()
    if "jpeg" in content_type:
        return ".jpg"
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"
    if "gif" in content_type:
        return ".gif"
    if media == "video" and "mp4" in content_type:
        return ".mp4"
    if media == "audio" and ("mpeg" in content_type or "mp3" in content_type):
        return ".mp3"
    guessed = Path(urlsplit(url).path).suffix.lower()
    if guessed in _MEDIA_SUFFIXES:
        return guessed
    return ".png" if media == "image" else ".bin"


async def _localize_media_url(
    url: str, *, project: str, project_dir: Path, username: str, media: str
) -> str:
    """把媒体地址收敛为项目内静态 URL，供资产库登记。

    1) 项目内已有地址（/static/...、/api/v1/projects/...）→ 原样返回；
    2) Agent 全局媒体（/api/v1/agent/media/...）→ 复制进项目 _uploads；
    3) http(s) 外链 → 下载进项目 _uploads；其它一律拒绝。
    """
    if url.startswith("/static/") or url.startswith("/api/v1/projects/"):
        from novelvideo.freezone.paths import resolve_static_url_to_path

        try:
            path = resolve_static_url_to_path(url, project_dir)
        except ValueError as exc:
            raise HTTPException(400, f"媒体地址无法解析：{exc}") from exc
        if not path.exists():
            raise HTTPException(404, "媒体文件不存在")
        return url
    payload: bytes | None = None
    content_type = ""
    if url.startswith("/api/v1/agent/media/"):
        filename = Path(urlsplit(url).path).name
        source = agent_store.agent_root(username) / "media" / filename
        if not source.exists() or not source.is_file():
            raise HTTPException(404, "媒体文件不存在")
        if source.stat().st_size > _LIBRARY_SAVE_MAX_BYTES:
            raise HTTPException(400, "文件过大，无法存入资产库")
        payload = source.read_bytes()
    elif url.startswith("http://") or url.startswith("https://"):
        # [aipdd fork delta] 用户可控外链 → 抓回字节入库成 /static 资产，同白模外链
        # 一类 SSRF + 数据外泄面：触网前先过公网守卫（拒私网/回环/链路本地/非 http(s)），
        # 并关闭重定向，避免一次合法外链 302 到内网元数据端点绕过前置校验。
        try:
            ensure_public_http_url(url)
        except PublicUrlRejected as exc:
            raise HTTPException(400, f"媒体外链地址不合法：{url[:120]}") from exc
        try:
            payload, content_type = await agent_gateway.download_media(
                url, follow_redirects=False
            )
        except agent_gateway.GatewayError as exc:
            raise HTTPException(400, f"媒体下载失败：{exc}") from exc
        if len(payload) > _LIBRARY_SAVE_MAX_BYTES:
            raise HTTPException(400, "文件过大，无法存入资产库")
    else:
        raise HTTPException(400, f"不支持的媒体地址：{url[:120]}")
    target_dir = project_dir / "freezone" / "_uploads"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"agent_{uuid.uuid4().hex[:12]}{_media_suffix(media, url, content_type)}"
    target.write_bytes(payload)
    from novelvideo.utils.static_urls import project_static_url

    rel = target.relative_to(project_dir).as_posix()
    return project_static_url(project, rel, local_path=target)


@router.post("/library/save")
async def agent_save_to_library(
    body: LibrarySaveBody, user: dict = Depends(require_api_user)
) -> dict[str, Any]:
    """把 Agent 生成/对话中的媒体存入项目资产库的指定文件夹（分类）。"""
    username = _username(user)
    project = str(body.project or "").strip()
    if not project:
        raise HTTPException(400, "project is required")
    name = str(body.name or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    media = str(body.media or "image").strip().lower()
    if media not in ("image", "video", "audio"):
        raise HTTPException(400, "media must be image/video/audio")
    url = str(body.url or "").strip()
    if not url:
        raise HTTPException(400, "url is required")
    folder = str(body.folder or "").strip()
    category = str(body.category or "").strip()

    from novelvideo.api.deps import resolve_project_scope

    resolution = await resolve_project_scope(project, user, required_role="editor")
    project_dir = Path(resolution.project_dir)

    from novelvideo.freezone.video_node import (
        LIBRARY_ITEM_NAME_MAX_LEN,
        MAINLINE_FOLDER_KEY,
        add_video_character_library_item,
        library_folder_keys,
    )

    if folder:
        if folder == MAINLINE_FOLDER_KEY:
            raise HTTPException(400, "不能存入主线文件夹")
        if folder not in library_folder_keys(project_dir):
            raise HTTPException(404, f"文件夹不存在：{folder}")
    final_url = await _localize_media_url(
        url, project=project, project_dir=project_dir, username=username, media=media
    )
    item = add_video_character_library_item(
        project_dir,
        name=name[:LIBRARY_ITEM_NAME_MAX_LEN],
        media=media,
        image_urls=[final_url] if media == "image" else None,
        video_url=final_url if media == "video" else None,
        audio_url=final_url if media == "audio" else None,
        category=category or None,
        folder=folder or None,
    )
    return {"ok": True, "data": item}