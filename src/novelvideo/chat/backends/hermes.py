"""Hermes 后端适配器（从 chat/service.py 拆出）。

负责 Hermes ACP 子进程后端的流式回复与启动授权；共享助手一律经
``_service.`` 前缀访问（命名空间延迟解析，monkeypatch 语义不变）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from novelvideo.chat import service as _service


async def authorize_hermes_launch(
    *,
    egress_context,
    username: str,
    requester_user_id: str | None,
    egress_project_id: str,
    prompt: str,
):
    """Turn this request's trusted egress context into a one-shot launch authorization.

    请求路径上 project 态与 home 态是两条独立实现（home 的流式循环在
    `api/routes/chat.py` 里，完全绕开本模块），但「怎么换取 authorization」
    必须只有一份。抽在这里而不是 `hermes_egress.py`：后者刻意用依赖注入收
    `credential_resolver` / `operation_port`，把端口查找塞进去会破坏那个设计。

    `egress_context` 为 `None`（平台／个人／CE local／灰度未开）时返回 `None`，
    调用方照传给 `get_for_user`，平台路径逐字节不变。

    `egress_project_id` 是**出网身份**，不是会话身份：project 态传真实 project id，
    home 态传 `HOME_SCOPE_EGRESS_PROJECT_ID` 哨兵。它必须与绑定时
    `request_egress_scope(project_id=...)` 用的值一致——`_strict_admission` 在
    `authorize_credentialed_hermes` 与 `build_hermes_child_env` 两处各比一次。
    """

    if egress_context is None:
        return None

    # 函数内局部导入：规避循环导入（本模块被 `hermes_pool` 一侧间接引用）。
    # 这是抽 helper 之前就有的写法，原样保留，不提到模块顶层。
    from novelvideo.chat.hermes_egress import (
        EgressBoundaryError,
        authorize_credentialed_hermes,
    )
    from novelvideo.ports import get_egress_operation_port, get_model_credentials

    # 身份判定只认 user_id。缺了就拒，不得回落成登录名 username——
    # 那是两个不同的值，回落会把坏口径固化成"看起来能用"。
    if not requester_user_id:
        raise EgressBoundaryError("TASK_ENVELOPE_INVALID")
    return await authorize_credentialed_hermes(
        context=egress_context,
        username=username,
        requester_user_id=requester_user_id,
        project_id=egress_project_id,
        prompt=prompt,
        credential_resolver=get_model_credentials(),
        operation_port=get_egress_operation_port(),
    )


async def _stream_assistant_reply_hermes(
    username: str,
    project: str,
    prompt: str,
    on_event,
    *,
    project_dir: str | Path | None = None,
    project_state_dir: str | Path | None = None,
    egress_context=None,
    requester_user_id: str | None = None,
) -> dict[str, Any]:
    """Stream via Hermes ACP subprocess (per-user, sandboxed).

    Differs from claude/codex paths:
    - Hermes is per-USER not per-(user, project). Project context is injected
      as a prompt prefix via `current_project=project`.
    - No per-project chat.db session id; HermesPool owns the thread lifecycle.
    """
    from novelvideo.chat.hermes_pool import pool as _hermes_pool

    authorization = await authorize_hermes_launch(
        egress_context=egress_context,
        username=username,
        requester_user_id=requester_user_id,
        egress_project_id=project,
        prompt=prompt,
    )

    agent_prompt = _service._prompt_with_user_context(username, project, prompt)
    thread = await _hermes_pool.get_for_user(
        username,
        scope_kind="project" if project else "home",
        project_id=project or None,
        # 出网身份与会话身份分开传。这两个必须来自调用方，不得从
        # `authorization.context` 自己取——那样 `build_hermes_child_env` 里的
        # 身份复核就退化成自证。home 态的出网 project 哨兵是 S5 的事，本片不碰。
        egress_project_id=project or None,
        requester_user_id=requester_user_id,
        authorization=authorization,
    )
    previous_assistant = (
        _service._assistant_history_contents(
            username,
            project,
            project_dir=project_dir,
            project_state_dir=project_state_dir,
        )
        if project
        else []
    )
    previous_trace = (
        _service._trace_history_contents(
            username,
            project,
            project_dir=project_dir,
            project_state_dir=project_state_dir,
        )
        if project
        else []
    )
    assistant_text = ""
    tool_text = ""
    tool_ui_specs: list[dict[str, Any]] = []
    fallback_tool_ui_specs: list[dict[str, Any]] = []
    fallback_token: str | None = None
    current_tool_name: str | None = None
    current_tool_hidden = False
    persisted_message: dict[str, Any] | None = None
    seen_display_calls: set[str] = set()
    seen_tool_chat_errors: set[str] = set()

    def persist_partial_reply() -> dict[str, Any] | None:
        nonlocal persisted_message, assistant_text, tool_text
        if persisted_message is not None:
            return persisted_message
        final_text = _service._strip_replayed_chat_response(
            assistant_text, previous_assistant, prompt
        ).strip()
        all_tool_ui_specs = _service._dedupe_tool_ui_specs(
            [*tool_ui_specs, *fallback_tool_ui_specs]
        )
        all_tool_ui_specs = _service._filter_tool_ui_specs_for_prompt(
            prompt, all_tool_ui_specs
        )
        final_text = _service._append_tool_ui_specs(final_text, all_tool_ui_specs)
        if not final_text:
            return None
        final_text = _service._normalize_json_render_reply(final_text)
        final_tool_text = _service._strip_replayed_assistant_prefix(
            tool_text, previous_trace
        )
        if final_tool_text.strip():
            _service.add_trace_messages(
                username,
                project,
                _service._split_trace_contents(final_tool_text),
                project_dir=project_dir,
                project_state_dir=project_state_dir,
            )
        media = _service._extract_media(
            final_text, username, project, project_dir=project_dir
        )
        persisted_message = _service.add_assistant_message(
            username,
            project,
            final_text,
            media,
            project_dir=project_dir,
            project_state_dir=project_state_dir,
        )
        return persisted_message

    try:
        async for event in thread.stream(agent_prompt, current_project=project or None):
            if event.type == "thread_started":
                await _service._emit_chat_event_best_effort(
                    on_event,
                    {
                        "type": "thread_started",
                        "thread_id": str(event.thread_id or "").strip() or None,
                        "turn_id": str(event.turn_id or "").strip() or None,
                    },
                )
                continue
            if event.type == "assistant_delta":
                assistant_text = _service._merge_stream_text(assistant_text, event.text)
                streamed_text = _service._strip_replayed_chat_response(
                    assistant_text,
                    previous_assistant,
                    prompt,
                    suppress_partial_replay=True,
                )
                streamed_text = _service._redact_local_filesystem_paths(streamed_text)
                await _service._emit_chat_event_best_effort(
                    on_event,
                    {
                        "type": "assistant_delta",
                        "text": streamed_text,
                    },
                )
                continue
            if event.type == "tool_update":
                if event.raw is not None:
                    tool_chat_error = _service._extract_tool_chat_error(event.raw)
                    if tool_chat_error and tool_chat_error not in seen_tool_chat_errors:
                        seen_tool_chat_errors.add(tool_chat_error)
                        assistant_text = _service._merge_stream_text(
                            assistant_text,
                            ("\n\n" if assistant_text.strip() else "")
                            + tool_chat_error,
                        )
                        await _service._emit_chat_event_best_effort(
                            on_event,
                            {
                                "type": "assistant_delta",
                                "text": _service._redact_local_filesystem_paths(
                                    tool_chat_error
                                ),
                            },
                        )
                    tool_ui_specs.extend(_service._extract_tool_ui_specs(event.raw))
                    display_call = _service._extract_display_tool_call(event.raw)
                    if display_call is not None:
                        tool_name, tool_args = display_call
                        display_call_key = _service._display_tool_call_key(
                            tool_name, tool_args
                        )
                        if display_call_key in seen_display_calls:
                            _service.logger.info(
                                "filtered duplicate hermes display fallback "
                                "turn_id=%s project=%s tool=%s args=%s raw_kind=%s",
                                event.turn_id,
                                project,
                                tool_name,
                                json.dumps(
                                    tool_args,
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    default=str,
                                )[:1000],
                                (
                                    event.raw.get("sessionUpdate")
                                    if isinstance(event.raw, dict)
                                    else None
                                ),
                            )
                        else:
                            seen_display_calls.add(display_call_key)
                            if fallback_token is None:
                                fallback_token = (
                                    await _service._create_page_agent_session_token(
                                        username,
                                        project,
                                        agent_kind="hermes-display-fallback",
                                    )
                                )
                            fallback_tool_ui_specs.extend(
                                await _service._fallback_display_tool_ui_specs(
                                    username,
                                    project,
                                    tool_name,
                                    tool_args,
                                    token=fallback_token,
                                    project_dir=project_dir,
                                )
                            )
                if event.name:
                    current_tool_name = event.name
                    current_tool_hidden = _service._is_hidden_chat_tool_event(
                        event.name, event.text
                    )
                if current_tool_hidden or _service._is_hidden_chat_tool_event(
                    current_tool_name, event.text
                ):
                    continue
                tool_text += str(event.text or "") + "\n"
                display_tool_text = _service._strip_replayed_assistant_prefix(
                    tool_text, previous_trace
                )
                if display_tool_text.strip():
                    await _service._emit_chat_event_best_effort(
                        on_event,
                        {
                            "type": "tool_update",
                            "text": display_tool_text,
                            "name": current_tool_name,
                        },
                    )
                continue
            if event.type == "complete":
                if seen_tool_chat_errors and assistant_text.strip():
                    continue
                assistant_text = _service._completion_text_or_existing(
                    event.text, assistant_text
                )

        if not assistant_text.strip():
            assistant_text = "(hermes returned no content)"
        if not tool_ui_specs and not fallback_tool_ui_specs:
            inferred_display_call = _service._infer_display_tool_call_from_text(
                prompt,
                assistant_text,
                previous_assistant,
            )
            if inferred_display_call is not None:
                tool_name, tool_args = inferred_display_call
                if fallback_token is None:
                    fallback_token = await _service._create_page_agent_session_token(
                        username,
                        project,
                        agent_kind="hermes-display-fallback",
                    )
                fallback_tool_ui_specs.extend(
                    await _service._fallback_display_tool_ui_specs(
                        username,
                        project,
                        tool_name,
                        tool_args,
                        token=fallback_token,
                        project_dir=project_dir,
                    )
                )
        result_message = persist_partial_reply()
        if result_message is None:
            result_message = _service.add_assistant_message(
                username,
                project,
                "(hermes returned no content)",
                [],
                project_dir=project_dir,
                project_state_dir=project_state_dir,
            )
            persisted_message = result_message
        await _service._emit_chat_event_best_effort(
            on_event,
            {"type": "assistant_message", "message": result_message},
        )
        await _service._emit_chat_event_best_effort(
            on_event, {"type": "done", "message": result_message}
        )
        return result_message
    except Exception:
        raise
    finally:
        persist_partial_reply()
