"""Claude 后端适配器（从 chat/service.py 拆出）。

负责 Claude Code SDK 会话构建与流式回复；共享助手一律经
``_service.`` 前缀访问（命名空间延迟解析，monkeypatch 语义不变）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from novelvideo.chat import service as _service
from novelvideo.chat.backend_sdk import ClaudeSdkClient


def ensure_user_claude_workspace(
    username: str, project: str, agent_token: str = ""
) -> None:
    workspace = _service._user_agent_workspace(username)
    claude_dir = workspace / ".claude"
    skills_dir = claude_dir / "skills"
    claude_dir.mkdir(parents=True, exist_ok=True)
    skills_dir.mkdir(parents=True, exist_ok=True)
    _service._write_user_skill_settings(username, project, agent_token)
    _service._sync_project_skills(skills_dir)


def _build_claude_env(
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


def _build_claude_thread(
    username: str, project: str, agent_token: str, *, egress_context=None
):
    ensure_user_claude_workspace(username, project, agent_token)
    workspace = _service._user_agent_workspace(username)
    client = ClaudeSdkClient(
        cli_path=_service._claude_cli_path(),
        cwd=workspace,
        env=_build_claude_env(
            username, project, agent_token, egress_context=egress_context
        ),
        model=_service._claude_model(),
    )
    session_id = _service._get_claude_session_id(username, project)
    return client.thread_resume(session_id) if session_id else client.thread_start()


async def _stream_assistant_reply_claude(
    username: str,
    project: str,
    prompt: str,
    on_event,
    *,
    project_dir: str | Path | None = None,
    project_state_dir: str | Path | None = None,
    egress_context=None,
) -> dict[str, Any]:
    try:
        agent_token = await _service._create_page_agent_session_token(
            username,
            project,
            agent_kind="claude",
        )
        thread = _build_claude_thread(
            username, project, agent_token, egress_context=egress_context
        )
        agent_prompt = _service._prompt_with_user_context(username, project, prompt)
        assistant_text = ""
        tool_text = ""
        async for event in thread.stream(agent_prompt):
            if event.type == "thread_started":
                thread_id = str(event.thread_id or "").strip() or None
                if thread_id:
                    _service._set_claude_session_id(username, project, thread_id)
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
                tool_text = str(event.text or "")
                await on_event({"type": "tool_update", "text": tool_text})
                continue
            if event.type == "complete":
                thread_id = str(event.thread_id or "").strip() or None
                if thread_id:
                    _service._set_claude_session_id(username, project, thread_id)
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
    except Exception:
        raise
