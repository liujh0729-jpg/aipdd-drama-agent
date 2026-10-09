# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] Agent 内置工具：生图 / 加载技能 / 保存记忆。

工具以 OpenAI function calling schema 描述；执行结果回填给模型，产生的媒体
（如生成图）通过 ``ToolResult.media`` 交给引擎推给前端，供预览与「存入资产库」。

落盘策略：
- 项目会话：图片存 ``<project>/freezone/agent_media/``，直接生成项目静态 URL，
  可进画布、可一键存入资产库；
- 全局会话（无项目）：图片存 ``<agent_root>/media/``，经 /api/v1/agent/media/ 访问，
  存入资产库时由保存端点负责复制到目标项目。
"""

from __future__ import annotations

import base64
import logging
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from novelvideo.agent import gateway, memory, plans, skills, store

logger = logging.getLogger("novelvideo.agent.tools")

AGENT_MEDIA_DIRNAME = "agent_media"

TOOL_GENERATE_IMAGE = "agent_generate_image"
TOOL_GENERATE_VIDEO = "agent_generate_video"
TOOL_LOAD_SKILL = "agent_load_skill"
TOOL_SAVE_MEMORY = "agent_save_memory"
TOOL_UPDATE_PLAN = "agent_update_plan"


@dataclass
class ToolResult:
    """一次工具执行的结果：给模型的文本 + 给前端的媒体。"""

    ok: bool
    content: str
    media: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ToolContext:
    """内置工具执行所需的会话上下文（由引擎组装）。"""

    username: str
    project: str = ""
    project_dir: Path | None = None
    api_key: str = ""
    base_url: str = ""
    image_model: str = ""
    video_model: str = ""
    # [aipdd fork delta] 输入区显式设定的回合媒体参数：执行生成工具时优先于
    # LLM 从 arguments 解析的同类参数（用户所见即所得）；未设定（空/None）则
    # 保持原行为，交由 LLM / 网关默认值决定。
    image_size: str = ""
    video_resolution: str = ""
    video_duration: int | None = None
    # [aipdd fork delta] 视频超清开关（req22）：非空 = 基础分辨率生成后再走高清
    # 增强（MediaKit 优先 / ffmpeg 兜底，与画布高清同一叶子），档位 1080p/2k/4k。
    video_upscale_resolution: str = ""


def builtin_tool_schemas() -> list[dict[str, Any]]:
    """内置工具的 OpenAI tools 列表。"""
    return [
        {
            "type": "function",
            "function": {
                "name": TOOL_GENERATE_IMAGE,
                "description": (
                    "生成一张图片（走平台公共网关的默认图片模型）。"
                    "当用户要求画图、生成图片/插画/概念图时调用。"
                    "调用成功后返回图片地址，直接在回复里给出该地址。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "详细的图片生成提示词（描述画面主体、风格、构图、光影）",
                        },
                        "size": {
                            "type": "string",
                            "description": (
                                "可选尺寸，如 1024x1024；不确定时留空。"
                                "用户在输入区已设定分辨率时由系统自动应用，无需为此传参"
                            ),
                        },
                    },
                    "required": ["prompt"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": TOOL_GENERATE_VIDEO,
                "description": (
                    "生成一段视频（走平台公共网关的默认视频模型）。"
                    "当用户要求生成视频/动画/动态画面时调用。"
                    "调用成功后返回视频地址，直接在回复里给出该地址。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "详细的视频生成提示词（描述画面主体、动作、镜头与风格）",
                        },
                        "duration_seconds": {
                            "type": "integer",
                            "description": (
                                "可选时长（秒），不确定时留空。"
                                "用户在输入区已设定时长时由系统自动应用，无需为此传参"
                            ),
                        },
                        "resolution": {
                            "type": "string",
                            "description": (
                                "可选分辨率（如 720p），不确定时留空。"
                                "用户在输入区已设定分辨率时由系统自动应用，无需为此传参"
                            ),
                        },
                        "ratio": {
                            "type": "string",
                            "description": "可选画幅（如 16:9 / 9:16），不确定时留空",
                        },
                    },
                    "required": ["prompt"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": TOOL_LOAD_SKILL,
                "description": (
                    "加载一个已安装技能（Skill）的完整说明。"
                    "系统提示的技能摘要里列出了可用技能；"
                    "在准备执行与该技能相关的任务前，先调用本工具读取技能全文，再按技能指引工作。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "技能名（技能摘要中的名字）"},
                    },
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": TOOL_SAVE_MEMORY,
                "description": (
                    "把值得长期记住的信息写入长期记忆："
                    "用户偏好、重要设定、反复出现的结论等。"
                    "内容要简洁（一条一句话），不要保存寒暄或临时内容。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "note": {"type": "string", "description": "一条简明信息"},
                    },
                    "required": ["note"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": TOOL_UPDATE_PLAN,
                "description": (
                    "维护当前项目的待办计划（制作任务清单，展示在「待办计划」面板）。"
                    "用户提出短剧/制作类需求时，先把工作拆解成任务用 add 写进计划；"
                    "完成一项就立刻用 complete 勾掉（传任务 id，也可用标题匹配）；"
                    "确认作废的任务用 remove。不要用它代替直接回答。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["add", "complete", "remove"],
                            "description": "add=新增任务；complete=标记完成；remove=删除任务",
                        },
                        "items": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "add 传任务标题列表；complete/remove 传任务 id"
                                "（也可传标题做匹配）"
                            ),
                        },
                    },
                    "required": ["action", "items"],
                },
            },
        },
    ]


def mcp_tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 MCP 工具列表转换成 OpenAI tools 结构（供模型选择调用）。"""
    converted: list[dict[str, Any]] = []
    for tool in tools:
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": tool["qualified_name"],
                    "description": str(tool.get("description") or "").strip()
                    or f"{tool.get('server_name') or 'MCP'} 提供的工具",
                    "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
        )
    return converted


async def execute_builtin_tool(
    name: str,
    arguments: dict[str, Any],
    context: ToolContext,
) -> ToolResult | None:
    """执行内置工具；名字不匹配任何内置工具时返回 None（交由 MCP 分支处理）。"""
    if name == TOOL_GENERATE_IMAGE:
        return await _run_generate_image(arguments or {}, context)
    if name == TOOL_GENERATE_VIDEO:
        return await _run_generate_video(arguments or {}, context)
    if name == TOOL_LOAD_SKILL:
        return _run_load_skill(arguments or {}, context)
    if name == TOOL_SAVE_MEMORY:
        return _run_save_memory(arguments or {}, context)
    if name == TOOL_UPDATE_PLAN:
        return _run_update_plan(arguments or {}, context)
    return None


async def _run_generate_image(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    prompt = str(arguments.get("prompt") or "").strip()
    if not prompt:
        return ToolResult(False, "生成失败：prompt 不能为空")
    if not context.image_model:
        return ToolResult(False, "生成失败：平台未配置默认图片模型（请在设置→默认生成模型中选择）")
    # 用户输入区的显式设定优先于 LLM 参数（未设定时保持原解析）。
    size = (
        context.image_size
        or str(arguments.get("size") or "").strip()
        or None
    )
    try:
        image = await gateway.generate_image(
            api_key=context.api_key,
            base_url=context.base_url,
            model=context.image_model,
            prompt=prompt,
            size=size,
        )
        media = await _persist_image(context, image)
    except gateway.GatewayError as exc:
        logger.warning("agent generate image failed: %s", exc)
        return ToolResult(False, f"生成失败：{exc}")
    except OSError as exc:
        logger.warning("agent image persist failed: %s", exc, exc_info=True)
        return ToolResult(False, f"生成成功但保存失败：{exc}")
    applied = f"（用户设定尺寸 {context.image_size}）" if context.image_size else ""
    return ToolResult(
        True,
        f"图片已生成并保存{applied}。图片地址：{media['url']}\n"
        "请在回复中告诉用户已生成，并直接附上该地址。",
        [media],
    )


async def _run_generate_video(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    prompt = str(arguments.get("prompt") or "").strip()
    if not prompt:
        return ToolResult(False, "生成失败：prompt 不能为空")
    if not context.video_model:
        return ToolResult(False, "生成失败：平台未配置默认视频模型（请在设置→默认生成模型中选择）")
    # 用户输入区的显式设定优先于 LLM 参数（未设定时保持原解析）。
    resolution = (
        context.video_resolution
        or str(arguments.get("resolution") or "").strip()
        or None
    )
    ratio = str(arguments.get("ratio") or "").strip() or None
    duration: int | None = context.video_duration
    if duration is None:
        raw_duration = arguments.get("duration_seconds")
        if raw_duration not in (None, ""):
            try:
                duration = int(float(raw_duration))
            except (TypeError, ValueError):
                duration = None
    direct_base, direct_key = _video_direct_channel(context.video_model)
    try:
        video = await gateway.generate_video(
            api_key=direct_key or context.api_key,
            base_url=direct_base or context.base_url,
            model=context.video_model,
            prompt=prompt,
            resolution=resolution,
            duration=duration,
            ratio=ratio,
        )
        media = await _persist_video(context, video)
    except gateway.GatewayError as exc:
        logger.warning("agent generate video failed: %s", exc)
        return ToolResult(False, f"生成失败：{exc}")
    except OSError as exc:
        logger.warning("agent video persist failed: %s", exc, exc_info=True)
        return ToolResult(False, f"生成成功但保存失败：{exc}")
    applied_parts: list[str] = []
    if context.video_resolution:
        applied_parts.append(f"分辨率 {context.video_resolution}")
    if context.video_duration:
        applied_parts.append(f"时长 {context.video_duration} 秒")
    hd_note = ""
    # 两步处理（req22）：基础分辨率成片后再走高清增强，成功时只把超清终稿
    # 交给前端展示；失败不吞掉成片，退回基础分辨率并在回复里说明。
    if context.video_upscale_resolution:
        applied_parts.append(f"超清 {context.video_upscale_resolution}")
        try:
            media = await _upscale_video_media(context, media)
        except Exception as exc:  # noqa: BLE001 - 超清失败不阻断回合，保留基础成片
            logger.warning("agent video upscale failed: %s", exc)
            hd_note = "（超清处理失败，已返回基础分辨率视频）"
    applied = f"（用户设定：{'、'.join(applied_parts)}）" if applied_parts else ""
    return ToolResult(
        True,
        f"视频已生成并保存{applied}。{hd_note}视频地址：{media['url']}\n"
        "请在回复中告诉用户已生成，并直接附上该地址。",
        [media],
    )


async def _upscale_video_media(
    context: ToolContext, media: dict[str, Any]
) -> dict[str, Any]:
    """对已落盘的视频走高清增强，产物改名后放回同一媒体目录并重建访问地址。

    复用画布同一叶子 ``run_freezone_video_upscale``（有 Key 走 MediaKit，否则
    ffmpeg 兜底）；项目会话以项目目录为工作区，全局会话以 agent 目录为工作区。
    """
    from novelvideo.freezone.jobs import run_freezone_video_upscale

    src = Path(str(media.get("local_path") or ""))
    if not src.exists():
        raise OSError(f"video source not found: {src}")
    work_dir = (
        Path(context.project_dir)
        if context.project_dir is not None
        else store.agent_root(context.username)
    )
    job_id = uuid.uuid4().hex[:12]
    out, _meta = await run_freezone_video_upscale(
        project_dir=work_dir,
        job_id=job_id,
        source_path=str(src),
        resolution=context.video_upscale_resolution,
    )
    # 终稿搬到基础成片同目录（对外可访问位置），文件名带 _hd 标记。
    target = src.with_name(f"{src.stem}_hd{out.suffix}")
    shutil.move(str(out), target)
    hd: dict[str, Any] = dict(media)
    hd["name"] = target.name
    hd["local_path"] = str(target)
    if media.get("origin") == "project" and context.project_dir is not None:
        from novelvideo.utils.static_urls import project_static_url

        rel = target.relative_to(Path(context.project_dir)).as_posix()
        hd["url"] = project_static_url(context.project, rel, local_path=target)
    else:
        hd["url"] = f"/api/v1/agent/media/{target.name}"
    return hd


def _video_direct_channel(model: str) -> tuple[str, str]:
    """[aipdd fork delta] agent 视频直连渠道解析（与画布生成同机制）。

    LocalNewAPI 代理（dramaclaw-gateway）对 aipdd 视频的 /video/generations
    提交会包成 {code, message(任务JSON), data:null} 壳且不登记任务，轮询
    恒 task_not_exist——代理侧视频轮询不可用，Agent 视频此前必报
    「视频生成响应缺少任务编号」。命中绑定渠道（如 aipdd 默认视频模型
    ap-minimax-h3）时改用直连 base_url + key 让 gateway 走原生 /videos 协议；
    未命中返回 ("", "")，调用方沿用生效网关兜底。
    """
    try:
        from novelvideo.model_gateway_settings import (
            resolve_media_provider_override,
        )

        override = resolve_media_provider_override(model, media_type="video")
    except Exception:
        return "", ""
    if not override:
        return "", ""
    base_url, api_key, _provider = override
    return str(base_url or "").strip(), str(api_key or "").strip()


async def _persist_image(context: ToolContext, image: dict[str, str]) -> dict[str, Any]:
    """把网关返回的图片落盘为本地文件，返回 media 描述。"""
    b64 = str(image.get("b64") or "")
    remote_url = str(image.get("url") or "")
    if b64:
        try:
            payload = base64.b64decode(b64, validate=False)
        except (ValueError, TypeError) as exc:
            raise OSError(f"图片数据解码失败：{exc}") from exc
        suffix = ".png"
    elif remote_url:
        payload, content_type = await gateway.download_media(remote_url)
        suffix = ".jpg" if "jpeg" in content_type else ".png"
    else:
        raise OSError("网关没有返回图片数据")
    filename = f"img_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}{suffix}"
    return _write_media(
        context, payload, filename=filename, kind="image", provider_url=remote_url
    )


async def _persist_video(context: ToolContext, video: dict[str, str]) -> dict[str, Any]:
    """把网关返回的视频落盘为本地文件，返回 media 描述。"""
    remote_url = str(video.get("url") or "")
    if not remote_url:
        raise OSError("网关没有返回视频数据")
    payload, content_type = await gateway.download_media(remote_url, timeout=300.0)
    suffix = ".mp4"
    if "webm" in content_type:
        suffix = ".webm"
    elif "quicktime" in content_type:
        suffix = ".mov"
    filename = f"video_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}{suffix}"
    return _write_media(
        context, payload, filename=filename, kind="video", provider_url=remote_url
    )


def _write_media(
    context: ToolContext,
    payload: bytes,
    *,
    filename: str,
    kind: str,
    provider_url: str,
) -> dict[str, Any]:
    """把媒体字节落盘（项目会话进项目，全局会话进 agent 目录），返回 media 描述。"""
    if context.project_dir is not None:
        target_dir = Path(context.project_dir) / "freezone" / AGENT_MEDIA_DIRNAME
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        target.write_bytes(payload)
        from novelvideo.utils.static_urls import project_static_url

        rel = target.relative_to(Path(context.project_dir)).as_posix()
        return {
            "kind": kind,
            "url": project_static_url(context.project, rel, local_path=target),
            "name": filename,
            "local_path": str(target),
            "origin": "project",
            "provider_url": provider_url,
        }
    target_dir = store.agent_root(context.username) / "media"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    target.write_bytes(payload)
    return {
        "kind": kind,
        "url": f"/api/v1/agent/media/{filename}",
        "name": filename,
        "local_path": str(target),
        "origin": "agent",
        "provider_url": provider_url,
    }


def _run_load_skill(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    name = str(arguments.get("name") or "").strip()
    if not name:
        return ToolResult(False, "加载失败：name 不能为空")
    loaded = skills.read_skill_body(context.username, name)
    if loaded is None:
        available = [
            item["name"] for item in skills.list_skills(context.username) if item.get("enabled")
        ]
        hint = "、".join(available) if available else "（没有已启用的技能）"
        return ToolResult(False, f"技能不存在或未启用：{name}。可用技能：{hint}")
    resolved, description, body = loaded
    text = f"# 技能：{resolved}\n"
    if description:
        text += f"{description}\n\n"
    text += body
    return ToolResult(True, text)


def _run_save_memory(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    note = str(arguments.get("note") or "").strip()
    if not note:
        return ToolResult(False, "保存失败：note 不能为空")
    _content, added = memory.append_memory(context.username, note)
    if added:
        return ToolResult(True, "已写入长期记忆。")
    return ToolResult(True, "这条信息已在长期记忆中，未重复保存。")


def _run_update_plan(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    """维护项目待办计划（add / complete / remove）。"""
    if context.project_dir is None:
        return ToolResult(False, "维护失败：当前会话未绑定项目，待办计划只在项目会话中可用。")
    action = str(arguments.get("action") or "").strip().lower()
    raw_items = arguments.get("items")
    items = [str(item) for item in raw_items] if isinstance(raw_items, list) else []
    items = [item for item in items if item.strip()]
    if not items:
        return ToolResult(False, "维护失败：items 不能为空")
    project_dir = Path(context.project_dir)
    if action == "add":
        kept, added = plans.add_items(project_dir, items, source="agent")
        if not added:
            return ToolResult(
                True,
                "没有新增（标题为空或与未完成任务重复）。\n当前计划：\n"
                + plans.render_plan_items(kept),
            )
        titles = "、".join(f"「{item['title']}」" for item in added)
        return ToolResult(
            True,
            f"已新增 {len(added)} 项待办：{titles}\n当前计划：\n" + plans.render_plan_items(kept),
        )
    if action == "complete":
        kept, completed, missing = plans.complete_items(project_dir, items)
        if not completed and not missing:
            return ToolResult(True, "没有需要勾掉的任务。")
        parts: list[str] = []
        if completed:
            titles = "、".join(f"「{item['title']}」" for item in completed)
            parts.append(f"已勾掉 {len(completed)} 项：{titles}")
        if missing:
            parts.append(f"未匹配到任务：{'、'.join(missing)}")
        return ToolResult(True, "\n".join(parts) + "\n当前计划：\n" + plans.render_plan_items(kept))
    if action == "remove":
        kept, removed, missing = plans.remove_items(project_dir, items)
        if not removed and not missing:
            return ToolResult(True, "没有需要删除的任务。")
        parts = []
        if removed:
            titles = "、".join(f"「{item['title']}」" for item in removed)
            parts.append(f"已删除 {len(removed)} 项：{titles}")
        if missing:
            parts.append(f"未匹配到任务：{'、'.join(missing)}")
        return ToolResult(True, "\n".join(parts) + "\n当前计划：\n" + plans.render_plan_items(kept))
    return ToolResult(False, f"维护失败：不支持的 action「{action}」（可用 add/complete/remove）")