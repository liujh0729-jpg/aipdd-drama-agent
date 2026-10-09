"""Codex 后端适配器（从 chat/service.py 拆出）。

负责 Codex app-server 会话构建（含 DramaClaw MCP 注入）与流式回复；
共享助手一律经 ``_service.`` 前缀访问（命名空间延迟解析，monkeypatch
语义不变）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from novelvideo.chat import service as _service
from novelvideo.chat.backend_sdk import CodexClient


def ensure_user_codex_workspace(
    username: str, project: str, agent_token: str = ""
) -> None:
    workspace = _service._user_agent_workspace(username)
    codex_dir = workspace / ".codex"
    skills_dir = codex_dir / "skills"
    codex_dir.mkdir(parents=True, exist_ok=True)
    skills_dir.mkdir(parents=True, exist_ok=True)
    _service._sync_project_skills(skills_dir)


def _build_codex_env(
    username: str,
    project: str,
    agent_token: str = "",
    *,
    egress_context=None,
) -> dict[str, str]:
    env = os.environ.copy()
    env["DRAMACLAW_USERNAME"] = username
    env["DRAMACLAW_AGENT_SCOPE"] = "user"
    env["SUPERTALE_USERNAME"] = username
    env["SUPERTALE_AGENT_SCOPE"] = "user"
    if project:
        env["DRAMACLAW_PROJECT_ID"] = project
        env["SUPERTALE_PROJECT_ID"] = project
    env["DRAMACLAW_API_URL"] = _service._load_api_url()
    env["SUPERTALE_API_URL"] = _service._load_api_url()
    env["DRAMACLAW_AGENT_TOKEN"] = agent_token
    env["SUPERTALE_AGENT_TOKEN"] = agent_token
    from novelvideo.task_backend.subprocesses import build_model_child_env

    return build_model_child_env(env, egress_context=egress_context)


def _aipdd_drama_mcp_servers() -> dict[str, dict[str, Any]]:
    return {
        "aipdd-drama": {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "novelvideo.chat.aipdd_drama_mcp"],
        }
    }


def _codex_mcp_config_overrides(
    mcp_servers: dict[str, dict[str, Any]],
) -> tuple[str, ...]:
    overrides: list[str] = []
    for name, server in sorted(mcp_servers.items()):
        if str(server.get("type") or "stdio") != "stdio":
            raise ValueError(
                f"unsupported Codex MCP server type for {name}: {server.get('type')}"
            )
        command = str(server.get("command") or "").strip()
        if not command:
            raise ValueError(f"Codex MCP server {name} is missing command")
        args = server.get("args") or []
        if not isinstance(args, list):
            raise ValueError(f"Codex MCP server {name} args must be a list")
        prefix = f"mcp_servers.{name}"
        overrides.append(f"{prefix}.command={json.dumps(command, ensure_ascii=False)}")
        overrides.append(
            f"{prefix}.args={json.dumps([str(arg) for arg in args], ensure_ascii=False, separators=(',', ':'))}"
        )
        overrides.append(f"{prefix}.enabled=true")
    return tuple(overrides)


def _build_codex_thread(
    username: str, project: str, agent_token: str, *, egress_context=None
):
    ensure_user_codex_workspace(username, project, agent_token)
    workspace = _service._user_agent_workspace(username)
    client = CodexClient(
        codex_bin=_service._codex_bin_path(),
        cwd=workspace,
        env=_build_codex_env(
            username, project, agent_token, egress_context=egress_context
        ),
        model=_service._codex_model(),
        config_overrides=_codex_mcp_config_overrides(_aipdd_drama_mcp_servers()),
    )
    thread_id = _service._get_codex_thread_id(username, project)
    return client.thread_resume(thread_id) if thread_id else client.thread_start()


async def _stream_assistant_reply_codex(
    username: str,
    project: str,
    prompt: str,
    on_event,
    *,
    project_dir: str | Path | None = None,
    project_state_dir: str | Path | None = None,
    egress_context=None,
) -> dict[str, Any]:
    assistant_text = ""
    tool_text = ""
    agent_token = await _service._create_page_agent_session_token(
        username,
        project,
        agent_kind="codex",
    )
    thread = _build_codex_thread(
        username, project, agent_token, egress_context=egress_context
    )
    agent_prompt = _service._prompt_with_user_context(username, project, prompt)
    async for event in thread.stream(agent_prompt):
        if event.type == "thread_started":
            thread_id = str(event.thread_id or "").strip() or None
            if thread_id:
                _service._set_codex_thread_id(username, project, thread_id)
            await on_event(
                {
                    "type": "thread_started",
                    "thread_id": thread_id,
                    "turn_id": str(event.turn_id or "").strip() or None,
                }
            )
            continue
        if event.type == "assistant_delta":
            assistant_text = _service._merge_stream_text(assistant_text, event.text)
            streamed_text = _service._redact_local_filesystem_paths(assistant_text)
            await on_event(
                {
                    "type": "assistant_delta",
                    "text": streamed_text,
                }
            )
            continue
        if event.type == "tool_update":
            tool_text += str(event.text or "")
            await on_event({"type": "tool_update", "text": tool_text})
            continue
        if event.type == "complete":
            thread_id = str(event.thread_id or "").strip() or None
            if thread_id:
                _service._set_codex_thread_id(username, project, thread_id)
            assistant_text = _service._completion_text_or_existing(
                event.text, assistant_text
            )

    assistant_text = assistant_text.strip() or "已执行，但没有返回正文。"
    assistant_text = _service._normalize_json_render_reply(assistant_text)
    if tool_text.strip():
        _service.add_trace_messages(
            username,
            project,
            _service._split_trace_contents(tool_text),
            project_dir=project_dir,
            project_state_dir=project_state_dir,
        )
    media = _service._extract_media(
        assistant_text, username, project, project_dir=project_dir
    )
    result_message = _service.add_assistant_message(
        username,
        project,
        assistant_text,
        media,
        project_dir=project_dir,
        project_state_dir=project_state_dir,
    )
    await on_event({"type": "done", "message": result_message})
    return result_message
