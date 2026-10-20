# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] Agent 引擎：一次对话回合的执行循环。

流程（参考 nanobot 的 AgentRunner 循环，按本项目收敛为单文件）：
1. 组装系统提示（人设 + 长期记忆 + 技能摘要 + 项目上下文 + 当前时间）；
2. 重放近 N 条历史消息（user/assistant 纯文本）；
3. 调公共网关 chat/completions（流式，携带内置 + MCP 工具）；
4. 模型请求工具调用 → 执行 → 结果回填 → 继续下一轮（上限 MAX_TOOL_ROUNDS 轮）；
5. 全程通过 emit 回调发事件（delta / tool_start / tool_end / media / notice）；
6. 回合结束把 assistant 消息（文本 + 工具事件 + 媒体）落库。

取消：调用方把 run 包在 asyncio.Task 里；取消时先保存已产生的部分再向上抛。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from novelvideo.agent import gateway, mcp, memory, plans, skills, store, tools as agent_tools

logger = logging.getLogger("novelvideo.agent.engine")

MAX_TOOL_ROUNDS = 8  # 工具调用轮次上限（防死循环）
HISTORY_MESSAGE_LIMIT = 30  # 重放历史的条数上限
CANCEL_MARKER = "（已中断）"
TOOL_SUMMARY_MAX_CHARS = 300

EmitFn = Callable[[dict[str, Any]], None]

_TOOL_RULES = (
    "工作方式：\n"
    "- 优先直接回答与创作；需要平台数据、生成能力或技能指引时再调用工具。\n"
    "- 工具结果要消化后用自然语言回复用户；生图/生视频成功后请在回复里附上媒体地址。\n"
    "- 调用 agent_load_skill 加载技能后，严格按技能正文的指引工作。\n"
    "- 用户偏好、重要设定等值得长期记住的信息，调用 agent_save_memory 保存。\n"
    "- 用户提出短剧/制作类需求时，先用 agent_update_plan 把工作拆解成待办任务；"
    "制作类任务带上 stage（必要时带 episode），让计划可以被自动执行；"
    "每完成一项就立刻勾掉（complete），让「待办计划」面板始终反映真实进度。\n"
    "- 用户说「按计划执行 / 执行计划 / 自动完成计划」时，先调用 aipdd_drama_get_plan "
    "读取计划，再调用 aipdd_drama_run_plan 自动推进；它会按真实产物回勾任务，"
    "只推进到镜头首帧，不生成视频。\n"
    "- 涉及项目数据（角色、剧集、素材等）时，使用 MCP 工具查询真实数据，不要凭空编造。"
)

# 对话类型（mode）的回合引导：图片/视频类型时催促模型直接调用对应生成工具。
# [aipdd fork delta] 用户在输入区选过的媒体参数（分辨率 / 时长）由工具执行层自动
# 应用（优先于工具参数），这里明确告诉模型不必再重复指定。
_MODE_RULES = {
    "image": (
        "本回合用户选择的对话类型是「图片」：用户期望得到图片产物。"
        "若请求属于创作/生成类，请直接调用 agent_generate_image 生成，不要把生成推给下一轮。"
        "用户在输入区设定的生成参数（如分辨率）已由系统自动应用，工具参数无需重复指定。"
    ),
    "video": (
        "本回合用户选择的对话类型是「视频」：用户期望得到视频产物。"
        "若请求属于创作/生成类，请直接调用 agent_generate_video 生成，不要把生成推给下一轮。"
        "用户在输入区设定的生成参数（如分辨率、时长）已由系统自动应用，工具参数无需重复指定。"
    ),
}

_REFERENCE_MEDIA_LABELS = {"image": "图片", "video": "视频", "audio": "音频"}


class AgentEngine:
    """单会话引擎实例：一次构造执行一个回合。"""

    def __init__(
        self,
        *,
        username: str,
        conversation_id: str,
        project: str = "",
        project_dir: Path | None = None,
        model: str = "",
        image_model: str = "",
        video_model: str = "",
        mode: str = "text",
        image_size: str = "",
        video_resolution: str = "",
        video_duration: int | None = None,
        video_upscale_resolution: str = "",
        references: list[dict[str, Any]] | None = None,
    ) -> None:
        self.username = username
        self.conversation_id = conversation_id
        self.project = str(project or "").strip()
        self.project_dir = Path(project_dir) if project_dir is not None else None
        self.model = str(model or "").strip()
        self.image_model = str(image_model or "").strip()
        self.video_model = str(video_model or "").strip()
        self.mode = mode if mode in _MODE_RULES else "text"
        # [aipdd fork delta] 输入区显式设定的回合媒体参数（未设定 = 空/None）。
        self.image_size = str(image_size or "").strip()
        self.video_resolution = str(video_resolution or "").strip()
        # [aipdd fork delta] 视频超清开关（req22）：非空 = 成片后再走高清增强。
        self.video_upscale_resolution = str(video_upscale_resolution or "").strip()
        self.video_duration = (
            video_duration
            if isinstance(video_duration, int) and video_duration > 0
            else None
        )
        self.references = list(references or [])

    async def run(self, user_text: str, emit: EmitFn) -> dict[str, Any]:
        """执行一个对话回合，返回落库后的 assistant 消息。"""
        text = str(user_text or "").strip()
        if not text:
            raise ValueError("消息内容不能为空")
        api_key, base_url = gateway.gateway_credentials()
        store.append_message(
            self.username, self.conversation_id, role="user", content=text
        )
        tool_context = agent_tools.ToolContext(
            username=self.username,
            project=self.project,
            project_dir=self.project_dir,
            api_key=api_key,
            base_url=base_url,
            image_model=self.image_model,
            video_model=self.video_model,
            image_size=self.image_size,
            video_resolution=self.video_resolution,
            video_duration=self.video_duration,
            video_upscale_resolution=self.video_upscale_resolution,
        )
        servers = mcp.enabled_servers(self.username)
        env_overrides: dict[str, dict[str, str]] = {}
        if self.project:
            # 内置 DramaClaw 服务器按会话项目工作：项目内对话默认作用于当前项目。
            env_overrides[mcp.BUILTIN_SERVER_ID] = {"DRAMACLAW_PROJECT_ID": self.project}
        produced_texts: list[str] = []
        tool_events: list[dict[str, Any]] = []
        media_items: list[dict[str, Any]] = []
        cancelled = False
        try:
            async with mcp.McpToolHub(servers, env_overrides=env_overrides) as hub:
                for error in hub.errors:
                    emit(
                        {
                            "type": "notice",
                            "text": f"MCP 服务器「{error['server_id']}」连接失败：{error['error']}",
                        }
                    )
                tools = agent_tools.builtin_tool_schemas() + agent_tools.mcp_tools_to_openai(
                    hub.tools
                )
                messages = self._build_messages()
                for _round in range(MAX_TOOL_ROUNDS):
                    round_text, tool_calls = await self._stream_round(
                        messages, tools, api_key, base_url, emit
                    )
                    if round_text.strip():
                        produced_texts.append(round_text.strip())
                    if not tool_calls:
                        break
                    messages.append(_assistant_tool_call_message(round_text, tool_calls))
                    for call in tool_calls:
                        result = await self._execute_tool_call(call, hub, tool_context, emit)
                        tool_events.append(
                            {
                                "name": call["name"],
                                "ok": result.ok,
                                "arguments": call["arguments"],
                                "summary": _truncate(result.content, TOOL_SUMMARY_MAX_CHARS),
                            }
                        )
                        media_items.extend(result.media)
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call["id"],
                                "content": result.content or "（无输出）",
                            }
                        )
                else:
                    # 到达工具轮次上限仍在调用工具：去掉工具再让模型收尾总结。
                    emit({"type": "notice", "text": "工具调用轮次已达上限，正在总结结果。"})
                    round_text, _ignored = await self._stream_round(
                        messages, None, api_key, base_url, emit
                    )
                    if round_text.strip():
                        produced_texts.append(round_text.strip())
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            if cancelled:
                self._persist_assistant(produced_texts, tool_events, media_items, cancelled=True)
        return self._persist_assistant(produced_texts, tool_events, media_items, cancelled=False)

    def _persist_assistant(
        self,
        produced_texts: list[str],
        tool_events: list[dict[str, Any]],
        media_items: list[dict[str, Any]],
        *,
        cancelled: bool,
    ) -> dict[str, Any]:
        content = "\n\n".join(item for item in produced_texts if item).strip()
        if cancelled:
            content = f"{content}\n\n{CANCEL_MARKER}".strip() if content else CANCEL_MARKER
        return store.append_message(
            self.username,
            self.conversation_id,
            role="assistant",
            content=content,
            tool_events=tool_events,
            media=media_items,
        )

    def _build_messages(self) -> list[dict[str, Any]]:
        history = store.list_messages(self.username, self.conversation_id)
        replayed: list[dict[str, Any]] = []
        for message in history[-HISTORY_MESSAGE_LIMIT:]:
            if message["role"] not in ("user", "assistant"):
                continue
            content = str(message.get("content") or "").strip()
            if content:
                replayed.append({"role": message["role"], "content": content})
        # 参考素材只注入本回合（最后一条 user 消息刚落库）——落库原文不变，
        # 历史重放时不携带；素材列表本身随后续消息自然过期。
        if self.references and replayed and replayed[-1]["role"] == "user":
            replayed[-1] = {
                "role": "user",
                "content": f"{replayed[-1]['content']}\n\n{self._references_block()}",
            }
        return [{"role": "system", "content": self._system_prompt()}, *replayed]

    def _references_block(self) -> str:
        lines = ["[本回合参考素材]（用户从项目资产库选择的素材，可直接作为创作/生成参考）"]
        for index, item in enumerate(self.references, start=1):
            label = _REFERENCE_MEDIA_LABELS.get(str(item.get("media") or ""), "素材")
            name = str(item.get("name") or "").strip() or f"素材{index}"
            line = f"{index}. {label}「{name}」"
            url = str(item.get("url") or "").strip()
            if url:
                line += f"：{url}"
            lines.append(line)
        return "\n".join(lines)

    def _system_prompt(self) -> str:
        lines: list[str] = [
            "你是 DramaClaw 平台内置的创作代理（Agent），在对话中帮用户完成内容创作："
            "构思创意、撰写文案、生成图片、查询与整理项目资料等。",
            "",
            _TOOL_RULES,
        ]
        if self.project:
            lines += [
                "",
                f"当前项目：{self.project}。MCP 工具默认在该项目内工作。",
            ]
        mode_rule = _MODE_RULES.get(self.mode)
        if mode_rule:
            lines += ["", mode_rule]
        lines += ["", f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}"]
        memory_block = memory.build_memory_block(self.username)
        if memory_block:
            lines += ["", memory_block]
        if self.project_dir is not None:
            plan_block = plans.build_plan_block(self.project_dir)
            if plan_block:
                lines += ["", plan_block]
        skills_summary = skills.build_skills_summary(self.username)
        if skills_summary:
            lines += ["", skills_summary]
        return "\n".join(lines)

    async def _stream_round(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        api_key: str,
        base_url: str,
        emit: EmitFn,
    ) -> tuple[str, list[dict[str, Any]]]:
        """一次网关流式调用：返回（本轮文本, 待执行的工具调用列表）。"""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        parts: list[str] = []
        call_slots: dict[int, dict[str, str]] = {}
        async for chunk in gateway.chat_stream(
            api_key=api_key, base_url=base_url, payload=payload
        ):
            choices = chunk.get("choices") or []
            if not choices or not isinstance(choices[0], dict):
                continue
            delta = choices[0].get("delta") or {}
            if not isinstance(delta, dict):
                continue
            content = delta.get("content")
            if isinstance(content, str) and content:
                parts.append(content)
                emit({"type": "delta", "text": content})
            calls = delta.get("tool_calls")
            if not isinstance(calls, list):
                continue
            for item in calls:
                if not isinstance(item, dict):
                    continue
                try:
                    index = int(item.get("index") or 0)
                except (TypeError, ValueError):
                    index = 0
                slot = call_slots.setdefault(index, {"id": "", "name": "", "arguments": ""})
                if item.get("id"):
                    slot["id"] = str(item["id"])
                function = item.get("function") or {}
                if function.get("name") and not slot["name"]:
                    slot["name"] = str(function["name"])
                if function.get("arguments"):
                    slot["arguments"] += str(function["arguments"])
        tool_calls: list[dict[str, Any]] = []
        for index in sorted(call_slots):
            slot = call_slots[index]
            if not slot["name"]:
                continue
            parsed_arguments, parse_error = _parse_arguments(slot["arguments"])
            tool_calls.append(
                {
                    "id": slot["id"] or f"call_{index}",
                    "name": slot["name"],
                    "arguments_raw": slot["arguments"],
                    "arguments": parsed_arguments,
                    "arguments_error": parse_error,
                }
            )
        return "".join(parts), tool_calls

    async def _execute_tool_call(
        self,
        call: dict[str, Any],
        hub: mcp.McpToolHub,
        tool_context: agent_tools.ToolContext,
        emit: EmitFn,
    ) -> agent_tools.ToolResult:
        name = str(call.get("name") or "")
        arguments = call.get("arguments")
        arguments_error = str(call.get("arguments_error") or "")
        matched = hub.resolve_tool(name)
        server_name = str(matched.get("server_name") or "") if matched else ""
        emit(
            {
                "type": "tool_start",
                "name": name,
                "server": server_name,
                "arguments": arguments if isinstance(arguments, dict) else {},
            }
        )
        if arguments_error:
            result = agent_tools.ToolResult(False, f"工具参数解析失败：{arguments_error}")
        elif name.startswith("mcp__"):
            try:
                text = await hub.call(name, arguments if isinstance(arguments, dict) else {})
                result = agent_tools.ToolResult(True, text or "（工具无输出）")
            except mcp.McpServerError as exc:
                result = agent_tools.ToolResult(False, f"MCP 工具调用失败：{exc}")
        else:
            executed = await agent_tools.execute_builtin_tool(
                name, arguments if isinstance(arguments, dict) else {}, tool_context
            )
            if executed is not None:
                result = executed
            elif matched is not None:
                # 技能文档/用户习惯写裸工具名；唯一命中 MCP 工具时按全名转发。
                try:
                    text = await hub.call(name, arguments if isinstance(arguments, dict) else {})
                    result = agent_tools.ToolResult(True, text or "（工具无输出）")
                except mcp.McpServerError as exc:
                    result = agent_tools.ToolResult(False, f"MCP 工具调用失败：{exc}")
            else:
                result = agent_tools.ToolResult(False, f"未知工具：{name}")
        for media in result.media:
            emit({"type": "media", "media": media})
        emit(
            {
                "type": "tool_end",
                "name": name,
                "server": server_name,
                "ok": result.ok,
                "summary": _truncate(result.content, TOOL_SUMMARY_MAX_CHARS),
            }
        )
        return result


def _parse_arguments(raw: str) -> tuple[dict[str, Any] | None, str]:
    text = str(raw or "").strip()
    if not text:
        return {}, ""
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        return None, str(exc)
    if not isinstance(parsed, dict):
        return None, "参数必须是 JSON 对象"
    return parsed, ""


def _assistant_tool_call_message(
    text: str, tool_calls: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": text or None,
        "tool_calls": [
            {
                "id": call["id"],
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": call.get("arguments_raw") or "{}",
                },
            }
            for call in tool_calls
        ],
    }


def _truncate(value: str, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"