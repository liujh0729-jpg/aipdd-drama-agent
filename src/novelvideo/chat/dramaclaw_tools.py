"""[aipdd fork delta] DramaClaw 平台工具共享实现（Hermes 插件 / MCP 桥共用）。

由 ``.hermes/plugins/dramaclaw/__init__.py`` 机械抽取而来（工具实现唯一真身）：
- Hermes 平台经薄壳插件（同路径）加载本模块并 ``register(ctx)`` 注册工具集；
- ``novelvideo.chat.aipdd_drama_mcp``（旧名 ``novelvideo.chat.dramaclaw_mcp``
  保留为兼容别名）直接把本模块暴露为 stdio MCP 服务器；
- 刻意不触碰 terminal/shell/subprocess，只依赖 Python 标准库与 AIPDD agent
  环境注入变量；不再依赖平台 ``tools.registry``（``tool_result`` /
  ``tool_error`` 为本地等价实现，语义与 MCP 桥历史 shim 一致）。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

# ── 工具结果渲染（原平台 ``tools.registry`` 依赖的本地等价实现）──
# Hermes 插件此前从平台 ``tools.registry`` 取这两个函数；实现收敛进本模块后
# 自带等价实现，Hermes 插件与 MCP stdio 桥共用同一份。保持模块级全局名，
# 便于测试以 monkeypatch 替换（历史测试依赖 identity 语义观测原始结构）。


def tool_result(value: Any) -> str:
    """工具成功结果：序列化为 JSON 文本（与 MCP 桥历史 shim 一致）。"""
    return json.dumps(value, ensure_ascii=False)


def tool_error(message: Any) -> str:
    """工具错误结果：统一 ``{"ok": false, "error": ...}`` JSON 文本。"""
    return json.dumps({"ok": False, "error": str(message)}, ensure_ascii=False)

TOOLSET = "dramaclaw"
ACP_TOOLSET = "hermes-acp"
REGISTER_TOOLSETS = (ACP_TOOLSET,)
API_PREFIX = "/api/v1/"


def _env_first(*names: str) -> str:
    """按顺序读取第一个非空环境变量（品牌改名后的别名回退）。

    对外统一暴露 ``AIPDD_DRAMA_*``；历史配置 / 部署里已写入的 ``DRAMACLAW_*``
    与 ``SUPERTALE_*`` 名称继续生效，外部 MCP 客户端与既有编排无需同步改。
    """
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


try:
    DEFAULT_TIMEOUT_SECONDS = max(30, int(_env_first("AIPDD_DRAMA_API_TIMEOUT_SECONDS", "DRAMACLAW_API_TIMEOUT_SECONDS") or "120"))
except ValueError:
    DEFAULT_TIMEOUT_SECONDS = 120
SCRIPT_UPLOAD_EXTENSIONS = {".txt", ".md", ".doc", ".docx"}
INGEST_PATH_ERROR = (
    "invalid ingest API path: use /projects/{project}/ingest/upload or "
    "/projects/{project}/ingest/start; ingest_fast is a task_type, not an endpoint; "
    "do not infer /ingest/init, /ingest/setup, /ingest_script, or /ingest_fast."
)
TEXT_CONTENT_FILTER_CHAT_ERROR = (
    "模型内容安全过滤拦截了本次文本生成，请调整原文或改写稿中的敏感描述后重试。"
)
VOICE_PREREQ_CHAT_PREFIX = (
    "配音任务没有成功启动：当前缺少声线前置。请到「资产」上传或录制缺失的"
    "项目解说人声线/角色声线后，再回来继续生成配音。"
)
RENDER_PREREQ_CHAT_PREFIX = (
    "Render 任务没有生成可用图片：当前缺少必要草图前置。请先在「资产」生成或确认对应 "
    "Beat 的草图后，再重新生成 Render。"
)


def _has_text_content_filter(value: Any) -> bool:
    if isinstance(value, str):
        lowered = value.lower()
        return "content_filter" in lowered or "content filter triggered" in lowered
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() == "finish_reason" and str(item).lower() == "content_filter":
                return True
            if _has_text_content_filter(item):
                return True
        return False
    if isinstance(value, list):
        return any(_has_text_content_filter(item) for item in value)
    return False


def _voice_prereq_error_text(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip()
        if "voice_prereq_required" in text or "声线缺失" in text:
            return text[:1200]
        return ""
    if isinstance(value, dict):
        code = str(value.get("code") or "").strip()
        error = str(value.get("error") or value.get("detail") or value.get("message") or "").strip()
        if code == "voice_prereq_required":
            return error[:1200] if error else "voice_prereq_required"
        if "声线缺失" in error:
            return error[:1200]
        for item in value.values():
            found = _voice_prereq_error_text(item)
            if found:
                return found
        return ""
    if isinstance(value, list):
        for item in value:
            found = _voice_prereq_error_text(item)
            if found:
                return found
    return ""


def _render_prereq_error_text(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip()
        if "Render 模式需要草图" in text or "未生成可用图片" in text:
            return text[:1200]
        return ""
    if isinstance(value, dict):
        error = str(value.get("error") or value.get("detail") or value.get("message") or "").strip()
        if "Render 模式需要草图" in error or "未生成可用图片" in error:
            return error[:1200]
        for item in value.values():
            found = _render_prereq_error_text(item)
            if found:
                return found
        return ""
    if isinstance(value, list):
        for item in value:
            found = _render_prereq_error_text(item)
            if found:
                return found
    return ""


def _with_chat_error_hints(value: Any) -> Any:
    if isinstance(value, list):
        return [_with_chat_error_hints(item) for item in value]
    if not isinstance(value, dict):
        return value

    result = {key: _with_chat_error_hints(item) for key, item in value.items()}
    voice_error = _voice_prereq_error_text(value)
    if voice_error:
        result.setdefault(
            "chat_error",
            f"{VOICE_PREREQ_CHAT_PREFIX}\n\n缺失项：{voice_error}",
        )
        result.setdefault(
            "agent_instruction",
            (
                "Reply to the user with chat_error in natural Chinese. Make clear the audio task "
                "was not started. Tell the user they can go to 资产 to upload or record the missing "
                "voice lines, then continue. Do not start another tool in this turn."
            ),
        )
    render_error = _render_prereq_error_text(value)
    if render_error:
        result.setdefault(
            "chat_error",
            f"{RENDER_PREREQ_CHAT_PREFIX}\n\n错误原因：{render_error}",
        )
        result.setdefault(
            "agent_instruction",
            (
                "Reply to the user with chat_error in natural Chinese. Make clear the render "
                "task did not produce usable images because sketches are missing. Tell the user "
                "to generate or verify sketches in 资产 before retrying render. Do not start "
                "another tool in this turn."
            ),
        )
    if _has_text_content_filter(value):
        result.setdefault("chat_error", TEXT_CONTENT_FILTER_CHAT_ERROR)
        result.setdefault(
            "agent_instruction",
            (
                "Reply to the user with chat_error in natural Chinese. Do not quote the raw "
                "provider JSON or provider_response_id."
            ),
        )
    return result


def _ce_owner_mode() -> bool:
    """CE single-user mode: authenticate as the local owner without a token.

    An AIPDD CE instance trusts local requests as the owner (no
    ``Authorization`` header required), so an external MCP client such as
    Claude Code can call the API on the same machine without minting an
    agent-session token. This is opt-in via ``DRAMACLAW_CE_OWNER`` so it can
    never silently drop auth against an EE deployment.
    """
    raw = os.environ.get("DRAMACLAW_CE_OWNER", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


# Hosts trusted as the local CE owner in tokenless owner mode. ``urlparse``
# lowercases the hostname and strips the brackets from ``[::1]``, so the bare
# forms below cover the bracketed IPv6 literal too.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _ce_owner_allow_remote() -> bool:
    """Explicit, separately-named unsafe override for CE-owner mode.

    Tokenless CE-owner mode drops the ``Authorization`` header entirely, so it
    must only target a local CE the caller controls. Pointing it at a remote
    host would send owner-level, unauthenticated requests across the network;
    that is refused unless the operator opts in via
    ``DRAMACLAW_CE_OWNER_ALLOW_REMOTE`` (deliberately a different variable from
    ``DRAMACLAW_CE_OWNER`` so it cannot be enabled by accident).
    """
    raw = os.environ.get("DRAMACLAW_CE_OWNER_ALLOW_REMOTE", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _enforce_ce_owner_target(url: str) -> None:
    """Require a loopback ``DRAMACLAW_API_URL`` in tokenless CE-owner mode.

    Called only when owner mode is active and no bearer token is present. The
    host must be a loopback address (``localhost``/``127.0.0.1``/``::1``/
    ``[::1]``) unless the operator has set the explicit unsafe override
    ``DRAMACLAW_CE_OWNER_ALLOW_REMOTE=1``.
    """
    if _ce_owner_allow_remote():
        return
    host = (urlparse(url).hostname or "").strip().lower()
    if host in _LOOPBACK_HOSTS:
        return
    raise ValueError(
        "DRAMACLAW_CE_OWNER=1 refuses non-loopback DRAMACLAW_API_URL "
        f"(host {host!r}): tokenless owner mode sends unauthenticated, "
        "owner-level requests and must point at a local CE "
        "(localhost, 127.0.0.1, ::1). Set DRAMACLAW_CE_OWNER_ALLOW_REMOTE=1 "
        "to override (unsafe), or provide DRAMACLAW_AGENT_TOKEN."
    )


def _available() -> bool:
    if not _env_first("AIPDD_DRAMA_API_URL", "DRAMACLAW_API_URL"):
        return False
    return bool(_env_first("AIPDD_DRAMA_AGENT_TOKEN", "DRAMACLAW_AGENT_TOKEN")) or _ce_owner_mode()


def _base_url() -> str:
    value = _env_first("AIPDD_DRAMA_API_URL", "DRAMACLAW_API_URL")
    if not value:
        raise ValueError("AIPDD_DRAMA_API_URL is not set")
    return value.rstrip("/")


def _token() -> str:
    value = _env_first("AIPDD_DRAMA_AGENT_TOKEN", "DRAMACLAW_AGENT_TOKEN")
    if not value:
        raise ValueError("AIPDD_DRAMA_AGENT_TOKEN is not set")
    return value


def _default_project_id() -> str:
    return _env_first("AIPDD_DRAMA_PROJECT_ID", "DRAMACLAW_PROJECT_ID", "SUPERTALE_PROJECT_ID")


def _project_output_dir() -> Path | None:
    value = _env_first(
        "AIPDD_DRAMA_PROJECT_OUTPUT_DIR",
        "DRAMACLAW_PROJECT_OUTPUT_DIR",
        "SUPERTALE_PROJECT_OUTPUT_DIR",
    )
    return Path(value) if value else None


def _project_static_url(project: str, rel_path: str, local_path: Path | None = None) -> str:
    rel = quote(str(rel_path).lstrip("/"), safe="/")
    base = f"/static/projects/{quote(str(project), safe='')}/{rel}"
    if local_path is not None and local_path.exists():
        return f"{base}?v={local_path.stat().st_mtime_ns}"
    return base


def _normalize_api_path(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        raise ValueError("path is required")
    if raw.startswith("http://") or raw.startswith("https://") or raw.startswith("//"):
        raise ValueError("absolute URLs are not allowed; pass an AIPDD API path")
    if not raw.startswith("/"):
        raw = f"/{raw}"
    if raw.startswith("/projects/"):
        raw = f"/api/v1{raw}"
    if not raw.startswith(API_PREFIX):
        raise ValueError("path must start with /api/v1/ or /projects/")
    if any(part == ".." for part in raw.split("/")):
        raise ValueError("path traversal is not allowed")
    _validate_ingest_api_path(raw)
    return raw


def _validate_ingest_api_path(path: str) -> None:
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) < 3 or parts[:2] != ["api", "v1"]:
        return

    route = parts[2:]
    if route and route[0] in {"ingest", "ingest_fast", "ingest_script"}:
        raise ValueError(INGEST_PATH_ERROR)

    if len(route) < 3 or route[0] != "projects":
        return

    project_route = route[2:]
    if not project_route:
        return

    first = project_route[0]
    if first in {"ingest_fast", "ingest_script"}:
        raise ValueError(INGEST_PATH_ERROR)
    if first != "ingest":
        return
    if project_route not in (["ingest", "upload"], ["ingest", "start"]):
        raise ValueError(INGEST_PATH_ERROR)


def _query_string(params: Any) -> str:
    if not isinstance(params, dict) or not params:
        return ""
    cleaned: dict[str, Any] = {}
    for key, value in params.items():
        if value is None or value == "":
            continue
        cleaned[str(key)] = value
    return f"?{urlencode(cleaned, doseq=True)}" if cleaned else ""


# [aipdd fork delta] 统一 HTTP 入口：MCP 编排工具经本函数打真实 REST；流水线推进的
# “决策/发射”已下沉到服务端权威计划器（POST /pipeline/plan），这里不再有本地计划闸门。


def _request(method: str, path: str, *, query: Any = None, body: Any = None) -> dict[str, Any]:
    api_path = _normalize_api_path(path)
    url = f"{_base_url()}{api_path}{_query_string(query)}"
    payload = None
    headers = {
        "Accept": "application/json",
        "User-Agent": "aipdd-drama-plugin/0.1.0",
    }
    token = _env_first("AIPDD_DRAMA_AGENT_TOKEN", "DRAMACLAW_AGENT_TOKEN")
    if not token:
        if not _ce_owner_mode():
            _token()  # raise the standard "AIPDD_DRAMA_AGENT_TOKEN is not set" error
        # Tokenless owner mode drops Authorization entirely, so it must only
        # ever target a loopback CE unless explicitly overridden.
        _enforce_ce_owner_target(_base_url())
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = Request(url, data=payload, headers=headers, method=method.upper())
    try:
        with urlopen(req, timeout=DEFAULT_TIMEOUT_SECONDS) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return _with_chat_error_hints(_decode_response(resp.status, text))
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        return _with_chat_error_hints({
            "ok": False,
            "status_code": exc.code,
            "error": _response_error_text(text) or exc.reason,
            "data": _maybe_json(text),
        })
    except URLError as exc:
        return {"ok": False, "error": f"network_error: {exc.reason}"}


def _decode_response(status_code: int, text: str) -> dict[str, Any]:
    data = _maybe_json(text)
    if isinstance(data, dict):
        return {"status_code": status_code, **data}
    return {"ok": 200 <= status_code < 300, "status_code": status_code, "data": data}


def _maybe_json(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return stripped


def _response_error_text(text: str) -> str:
    data = _maybe_json(text)
    if isinstance(data, dict):
        for key in ("error", "message", "detail"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(data, str):
        return data[:500]
    return ""


def _project_from_args(args: dict[str, Any]) -> str:
    project = str(args.get("project_id") or args.get("project") or _default_project_id()).strip()
    if not project:
        raise ValueError("project_id is required and DRAMACLAW_PROJECT_ID is not set")
    return project


def _limit_items(items: list[dict[str, Any]], args: dict[str, Any], default: int) -> list[dict[str, Any]]:
    raw = args.get("limit")
    try:
        limit = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        limit = default
    limit = max(1, min(limit, default))
    try:
        offset = int(args.get("offset") or 0)
    except (TypeError, ValueError):
        offset = 0
    offset = max(0, offset)
    return items[offset : offset + limit]


def _requested_beats(args: dict[str, Any]) -> set[int] | None:
    raw = args.get("beat_indices") or args.get("beats")
    values: list[Any] = []
    if isinstance(raw, list):
        values.extend(raw)
    elif raw is not None:
        values.append(raw)
    for key in ("beat", "beat_num", "beat_number", "index"):
        if args.get(key) is not None:
            values.append(args[key])
    beats: set[int] = set()
    for value in values:
        try:
            beat = int(value)
        except (TypeError, ValueError):
            continue
        if beat > 0:
            beats.add(beat)
    return beats or None


def _requested_names(args: dict[str, Any]) -> set[str] | None:
    raw = args.get("names")
    values: list[Any] = []
    if isinstance(raw, list):
        values.extend(raw)
    elif raw is not None:
        values.append(raw)
    for key in ("name", "character"):
        if args.get(key) is not None:
            values.append(args[key])
    names = {str(value).strip() for value in values if str(value or "").strip()}
    return names or None


def _requested_queries(args: dict[str, Any]) -> set[str] | None:
    raw = args.get("queries") or args.get("keywords")
    values: list[Any] = []
    if isinstance(raw, list):
        values.extend(raw)
    elif raw is not None:
        values.append(raw)
    for key in ("query", "search", "keyword", "text", "identity_name"):
        if args.get(key) is not None:
            values.append(args[key])
    queries = {str(value).strip() for value in values if str(value or "").strip()}
    return queries or None


def _requested_scene_names(args: dict[str, Any]) -> set[str] | None:
    raw = args.get("names") or args.get("scene_names")
    values: list[Any] = []
    if isinstance(raw, list):
        values.extend(raw)
    elif raw is not None:
        values.append(raw)
    for key in ("name", "scene_name"):
        if args.get(key) is not None:
            values.append(args[key])
    names = {str(value).strip() for value in values if str(value or "").strip()}
    return names or None


def _requested_scene_indices(args: dict[str, Any]) -> set[int] | None:
    raw = args.get("scene_indices") or args.get("indices")
    values: list[Any] = []
    if isinstance(raw, list):
        values.extend(raw)
    elif raw is not None:
        values.append(raw)
    if args.get("index") is not None:
        values.append(args["index"])
    indices: set[int] = set()
    for value in values:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if index > 0:
            indices.add(index)
    return indices or None


def _matches_any_scene_name(scene_name: str, requested_names: set[str] | None) -> bool:
    if requested_names is None:
        return True
    haystack = str(scene_name or "").casefold()
    return any(needle.casefold() in haystack for needle in requested_names if needle)


def _matches_any_text(fields: list[Any], queries: set[str] | None) -> bool:
    if queries is None:
        return True
    haystack = "\n".join(_flatten_text_fields(fields)).casefold()
    return any(query.casefold() in haystack for query in queries if query)


def _flatten_text_fields(fields: list[Any]) -> list[str]:
    values: list[str] = []
    for field in fields:
        if isinstance(field, dict):
            values.extend(_flatten_text_fields(list(field.values())))
        elif isinstance(field, list):
            values.extend(_flatten_text_fields(field))
        elif field is not None:
            text = str(field).strip()
            if text:
                values.append(text)
    return values


def _media_ui_spec(spec_type: str, component_type: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    elements: dict[str, Any] = {
        "root": {
            "type": "Stack",
            "props": {
                "direction": "row",
                "wrap": "wrap",
                "spacing": 16,
                "alignItems": "flex-start",
                "width": "100%",
            },
            "children": [],
        }
    }
    for index, item in enumerate(items, start=1):
        src = str(item.get("src") or item.get("url") or "").strip()
        if not src:
            continue
        key = f"media_{index}"
        title = str(item.get("title") or item.get("label") or f"媒体 {index}").strip()
        description = str(item.get("description") or "").strip()
        props: dict[str, Any] = {
            "src": src,
            "alt": title,
            "title": title,
        }
        if description:
            props["description"] = description
        if component_type == "Image":
            props.update(
                {
                    "fit": item.get("fit") or "cover",
                    "aspectRatio": item.get("aspectRatio") or "3/4",
                    "overlayTitle": title,
                }
            )
            if description:
                props["overlayDescription"] = description
        elif component_type == "Video":
            props["poster"] = str(item.get("poster") or item.get("thumbnail") or "").strip()
            props["controls"] = True
        elif component_type == "Audio":
            props["controls"] = True

        elements[key] = {"type": component_type, "props": props, "children": []}
        elements["root"]["children"].append(key)
    return {"type": spec_type, "root": "root", "elements": elements}


def _image_ui_spec(spec_type: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return _media_ui_spec(spec_type, "Image", items)


def _video_ui_spec(items: list[dict[str, Any]]) -> dict[str, Any]:
    return _media_ui_spec("keyframe_video", "Video", items)


def _audio_ui_spec(items: list[dict[str, Any]]) -> dict[str, Any]:
    return _media_ui_spec("audio_list", "Audio", items)


def _handle_get(args: dict[str, Any], **_: Any) -> str:
    try:
        return tool_result(_request("GET", str(args.get("path") or ""), query=args.get("query")))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_post(args: dict[str, Any], **_: Any) -> str:
    try:
        return tool_result(_request("POST", str(args.get("path") or ""), query=args.get("query"), body=args.get("body")))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_patch(args: dict[str, Any], **_: Any) -> str:
    try:
        return tool_result(_request("PATCH", str(args.get("path") or ""), query=args.get("query"), body=args.get("body")))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_delete(args: dict[str, Any], **_: Any) -> str:
    try:
        return tool_result(_request("DELETE", str(args.get("path") or ""), query=args.get("query"), body=args.get("body")))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_pipeline_status(args: dict[str, Any], **_: Any) -> str:
    try:
        project = _project_from_args(args)
        query = {"episode": args.get("episode")}
        return tool_result(_request("GET", f"/api/v1/projects/{project}/pipeline/status", query=query))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_list_tasks(args: dict[str, Any], **_: Any) -> str:
    try:
        project = _project_from_args(args)
        query = {
            "episode": args.get("episode"),
            "task_type": args.get("task_type"),
            "status": args.get("status"),
        }
        return tool_result(_request("GET", f"/api/v1/projects/{project}/tasks", query=query))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_get_task(args: dict[str, Any], **_: Any) -> str:
    try:
        project = _project_from_args(args)
        task_type = str(args.get("task_type") or "").strip()
        episode = int(args.get("episode") or 0)
        if not task_type:
            raise ValueError("task_type is required")
        query = {
            "beat_num": args.get("beat_num") or args.get("beat"),
            "scope": args.get("scope"),
        }
        return tool_result(_request("GET", f"/api/v1/projects/{project}/tasks/{task_type}/{episode}", query=query))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_get_episode_script(args: dict[str, Any], **_: Any) -> str:
    try:
        project = _project_from_args(args)
        episode = int(args.get("episode") or 1)
        return tool_result(_request("GET", f"/api/v1/projects/{project}/episodes/{episode}/script"))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_list_ingest_uploads(args: dict[str, Any], **_: Any) -> str:
    """List project files already uploaded to the local ingest script directory."""
    try:
        project = _project_from_args(args)
        current_project = _default_project_id()
        if current_project and project != current_project:
            raise ValueError("can only list uploads for the current Hermes project scope")

        project_dir_raw = _env_first(
            "AIPDD_DRAMA_PROJECT_OUTPUT_DIR",
            "DRAMACLAW_PROJECT_OUTPUT_DIR",
            "SUPERTALE_PROJECT_OUTPUT_DIR",
        )
        if not project_dir_raw:
            return tool_result(
                {
                    "ok": True,
                    "data": {
                        "project_id": project,
                        "count": 0,
                        "files": [],
                        "upload_dir_available": False,
                        "message": "project upload directory is not available in this Hermes session",
                    },
                }
            )

        project_dir = Path(project_dir_raw).expanduser().resolve()
        upload_dir = (project_dir / "uploads").resolve()
        if not upload_dir.is_relative_to(project_dir):
            raise ValueError("invalid upload directory")
        if not upload_dir.exists():
            return tool_result(
                {
                    "ok": True,
                    "data": {
                        "project_id": project,
                        "count": 0,
                        "files": [],
                        "upload_dir_available": True,
                    },
                }
            )

        files = []
        for path in upload_dir.iterdir():
            if not path.is_file() or path.name.startswith("."):
                continue
            suffix = path.suffix.lower()
            if suffix not in SCRIPT_UPLOAD_EXTENSIONS:
                continue
            stat = path.stat()
            files.append(
                {
                    "filename": path.name,
                    "size": stat.st_size,
                    "modified_at": int(stat.st_mtime),
                    "extension": suffix,
                }
            )
        files.sort(key=lambda item: (item["modified_at"], item["filename"]), reverse=True)
        return tool_result(
            {
                "ok": True,
                "data": {
                    "project_id": project,
                    "count": len(files),
                    "files": files,
                    "upload_dir_available": True,
                },
            }
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_build_characters(args: dict[str, Any], **_: Any) -> str:
    """Trigger character extraction from the project's knowledge graph.

    Wraps POST /projects/{project}/characters/build (the async ``build_characters``
    task at episode 0) so the agent never has to guess the path. Requires ingest
    to be complete. Poll progress with
    ``aipdd_drama_get_task(task_type="build_characters", episode=0)`` and read
    results with ``aipdd_drama_get(path="/projects/{project}/characters")``.
    """
    try:
        project = _project_from_args(args)
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/characters/build")
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_plan_episodes(args: dict[str, Any], **_: Any) -> str:
    """Plan/generate episodes (分集规划) from the ingested story + characters.

    Wraps POST /projects/{project}/episodes/plan (the async ``build_episodes``
    task at episode 0) so the agent never has to guess the path. Requires ingest
    + character extraction to be complete. Poll with
    ``aipdd_drama_get_task(task_type="build_episodes", episode=0)`` and read results
    with ``aipdd_drama_get(path="/projects/{project}/episodes")``.
    """
    try:
        project = _project_from_args(args)
        body: dict[str, Any] = {}
        if args.get("target_episodes") is not None:
            body["target_episodes"] = int(args["target_episodes"])
        if args.get("planning_mode"):
            body["planning_mode"] = str(args["planning_mode"])
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/episodes/plan", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_generate_script(args: dict[str, Any], **_: Any) -> str:
    """Generate the screenplay for one episode (脚本生成, script_writer task).

    Wraps POST /projects/{project}/episodes/{episode}/script/generate. Requires
    the episode's character identities to be planned first; if not, the API
    returns {"ok": false, "code": "identity_plan_required"} — plan identities
    before retrying. Poll with aipdd_drama_get_task(task_type="script_writer",
    episode=N); read with aipdd_drama_get_episode_script(episode=N).
    """
    try:
        project = _project_from_args(args)
        episode = int(args.get("episode") or 0)
        if episode <= 0:
            raise ValueError("episode is required and must be a positive integer")
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/episodes/{episode}/script/generate",
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_get_episode_story(args: dict[str, Any], **_: Any) -> str:
    """Read one episode's story and director style (故事).

    Wraps GET /projects/{project}/episodes/{episode}/story. The story is the
    creative input that ``aipdd_drama_story_to_script`` adapts into a
    line-by-line script; story_style is the director-style key ('' = auto).
    """
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        return tool_result(
            _request("GET", f"/api/v1/projects/{project}/episodes/{episode}/story")
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_save_episode_story(args: dict[str, Any], **_: Any) -> str:
    """Save one episode's story and optionally its director style.

    Wraps PUT /projects/{project}/episodes/{episode}/story. Omitting
    story_style keeps the stored one; passing it replaces the director style.
    """
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        story = str(args.get("story") or "")
        if not story.strip():
            raise ValueError("story is required")
        body: dict[str, Any] = {"story": story}
        if args.get("story_style") is not None:
            body["story_style"] = str(args["story_style"])
        return tool_result(
            _request(
                "PUT",
                f"/api/v1/projects/{project}/episodes/{episode}/story",
                body=body,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_optimize_episode_story(args: dict[str, Any], **_: Any) -> str:
    """Diagnose and polish one episode's story without persisting it.

    Wraps POST /projects/{project}/episodes/{episode}/story/optimize. The
    response carries data.story (polished text) + data.notes (diagnosis).
    Persist an adopted draft with ``aipdd_drama_save_episode_story``.
    """
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        body: dict[str, Any] = {}
        if args.get("story") is not None:
            body["story"] = str(args["story"])
        if args.get("story_style") is not None:
            body["story_style"] = str(args["story_style"])
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/episodes/{episode}/story/optimize",
                body=body,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_story_to_script(args: dict[str, Any], **_: Any) -> str:
    """Adapt one episode's story into a line-by-line script and start the pipeline.

    Wraps POST /projects/{project}/episodes/{episode}/story/to-script. The
    generated script is imported into the episode (adapted_content +
    beat_source_text) and, unless autostart=false, the script_writer task
    starts automatically. When the episode has no identities yet the response
    carries identity_plan_required=true (script still imported) — plan
    identities first, then re-run. Poll with
    ``aipdd_drama_get_task(task_type="script_writer", episode=N)``; read with
    ``aipdd_drama_get_episode_script(episode=N)``.
    """
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        body: dict[str, Any] = {}
        for key in ("story", "story_style"):
            if args.get(key) is not None:
                body[key] = str(args[key])
        for key in ("target_beats", "beat_chars_min", "beat_chars_max"):
            if args.get(key) is not None:
                body[key] = int(args[key])
        if args.get("autostart") is not None:
            body["autostart"] = bool(args["autostart"])
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/episodes/{episode}/story/to-script",
                body=body,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _require_episode(args: dict[str, Any]) -> int:
    episode = int(args.get("episode") or 0)
    if episode <= 0:
        raise ValueError("episode is required and must be a positive integer")
    return episode


def _require_name(args: dict[str, Any]) -> str:
    name = str(args.get("name") or args.get("character") or "").strip()
    if not name:
        raise ValueError("name (character name) is required")
    return name


def _handle_update_character_face_prompt(args: dict[str, Any], **_: Any) -> str:
    """Update a character's face_prompt before portrait generation."""
    try:
        project = _project_from_args(args)
        name = _require_name(args)
        face_prompt = str(args.get("face_prompt") or "").strip()
        if not face_prompt:
            raise ValueError("face_prompt is required")
        return tool_result(
            _request(
                "PATCH",
                f"/api/v1/projects/{project}/characters/{quote(name, safe='')}",
                body={"face_prompt": face_prompt},
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _episode_post(args: dict[str, Any], suffix: str, *, body: Any = None) -> dict[str, Any]:
    project = _project_from_args(args)
    episode = _require_episode(args)
    return _request("POST", f"/api/v1/projects/{project}/episodes/{episode}/{suffix}", body=body)


def _handle_plan_identities(args: dict[str, Any], **_: Any) -> str:
    """Plan character identities for one episode (身份规划, identity_planner task).

    POST /projects/{project}/episodes/{episode}/identities/plan-async. Prerequisite
    for aipdd_drama_generate_script. Poll task_type="identity_planner", episode=N.
    """
    try:
        return tool_result(_episode_post(args, "identities/plan-async"))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_plan_scenes(args: dict[str, Any], **_: Any) -> str:
    """Plan an episode scene menu before sketch generation."""
    try:
        return tool_result(_episode_post(args, "scenes/plan"))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_plan_props(args: dict[str, Any], **_: Any) -> str:
    """Plan an episode prop menu before sketch generation."""
    try:
        return tool_result(_episode_post(args, "props/plan"))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_generate_scene_master(args: dict[str, Any], **_: Any) -> str:
    """Generate one scene's canonical master reference image."""
    try:
        project = _project_from_args(args)
        name = str(args.get("name") or args.get("scene_name") or "").strip()
        if not name:
            raise ValueError("name (scene name) is required")
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/scenes/{quote(name, safe='')}/master/generate-async",
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_generate_scene_reverse(args: dict[str, Any], **_: Any) -> str:
    """Generate one scene's reverse master reference image."""
    try:
        project = _project_from_args(args)
        name = str(args.get("name") or args.get("scene_name") or "").strip()
        if not name:
            raise ValueError("name (scene name) is required")
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/scenes/{quote(name, safe='')}/reverse/generate-async",
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_generate_sketches(args: dict[str, Any], **_: Any) -> str:
    """Generate beat sketches for one episode (草图生成, sketch_generation task).

    POST /projects/{project}/episodes/{episode}/sketches/assign-colors, then
    POST /projects/{project}/episodes/{episode}/sketches/generate with the
    canonical request body. Runs after the script exists. Poll
    task_type="sketch_generation", episode=N.
    """
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        body = {
            "model": "nanobanana",
            "grid_index": -1,
            "sketch_scene_grouping": True,
            "aspect_ratio": "2:3",
        }
        if isinstance(args.get("body"), dict):
            body.update({key: value for key, value in args["body"].items() if value is not None})
        for key in (
            "style",
            "model",
            "grid_index",
            "sketch_scene_grouping",
            "aspect_ratio",
            "image_generation_selection",
        ):
            if key in args and args[key] is not None:
                body[key] = args[key]

        if args.get("auto_assign_colors", True):
            colors = _request(
                "POST",
                f"/api/v1/projects/{project}/episodes/{episode}/sketches/assign-colors",
            )
            if not colors.get("ok"):
                return tool_result(
                    {
                        "ok": False,
                        "stage": "assign-colors",
                        "error": colors.get("error") or "assign-colors failed",
                        "data": colors,
                    }
                )

        result = _request(
            "POST",
            f"/api/v1/projects/{project}/episodes/{episode}/sketches/generate",
            body=body,
        )
        if isinstance(result, dict):
            result.setdefault("request_body", body)
        return tool_result(result)
    except Exception as exc:
        return tool_error(str(exc))


def _handle_detect_sketch_identities(args: dict[str, Any], **_: Any) -> str:
    """Run episode-wide sketch AI detection for identities and props."""
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        result = _request(
            "POST",
            f"/api/v1/projects/{project}/episodes/{episode}/sketches/detect-identities",
        )
        if isinstance(result, dict) and not result.get("ok"):
            error_text = str(result.get("error") or "").casefold()
            if "timed out" in error_text or "timeout" in error_text:
                result.setdefault("retryable", False)
                result.setdefault(
                    "agent_instruction",
                    "Stop retrying this tool in the same turn. Report that AI detection timed "
                    "out and ask the user to retry later or run it from the frontend.",
                )
        return tool_result(result)
    except Exception as exc:
        return tool_error(str(exc))


def _handle_optimize_video_global(args: dict[str, Any], **_: Any) -> str:
    """Run global video optimization for one episode (全局视频优化, global_optimize_video).

    POST /projects/{project}/episodes/{episode}/optimize/video-global.
    Poll task_type="global_optimize_video", episode=N.
    """
    try:
        return tool_result(_episode_post(args, "optimize/video-global"))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_generate_audio(args: dict[str, Any], **_: Any) -> str:
    """Generate episode audio via the current IndexTTS2 audio pipeline."""
    try:
        body: dict[str, Any] = {}
        for key in ("provider", "voice", "model", "rate", "mode", "beat_numbers"):
            if args.get(key) is not None:
                body[key] = args[key]
        return tool_result(_episode_post(args, "audio/generate", body=body))
    except Exception as exc:
        return tool_error(str(exc))


def _resolve_episode_beats(project: str, episode: int) -> list[int]:
    """Fetch the episode's beat numbers via GET /episodes/{ep}/beats."""
    resp = _request("GET", f"/api/v1/projects/{project}/episodes/{episode}/beats")
    items: Any = None
    if isinstance(resp, dict):
        for key in ("data", "beats", "items"):
            value = resp.get(key)
            if isinstance(value, list):
                items = value
                break
        if items is None and isinstance(resp.get("data"), dict):
            items = resp["data"].get("beats")
    return [
        int(b["beat_number"])
        for b in (items or [])
        if isinstance(b, dict) and b.get("beat_number") is not None
    ]


def _handle_get_sketches(args: dict[str, Any], **_: Any) -> str:
    """Get display-ready sketch URLs for an episode (to SHOW the user).

    Wraps GET /projects/{project}/episodes/{episode}/beats and returns, per beat,
    the servable ``sketch_url``. Use ``aipdd_drama_get_first_frames`` for first frames.
    Do NOT read
    the local ``sketch_path`` from a task result, and do NOT use vision_analyze —
    that only lets the agent look at the image, it does NOT show it to the user.
    """
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        media_kind = "sketch"
        resp = _request("GET", f"/api/v1/projects/{project}/episodes/{episode}/beats")
        items: Any = None
        if isinstance(resp, dict):
            for key in ("data", "beats", "items"):
                value = resp.get(key)
                if isinstance(value, list):
                    items = value
                    break
            if items is None and isinstance(resp.get("data"), dict):
                items = resp["data"].get("beats")
        sketches = []
        media_items = []
        requested_beats = _requested_beats(args)
        for b in items or []:
            if not isinstance(b, dict):
                continue
            beat_number = b.get("beat_number")
            try:
                beat_int = int(beat_number)
            except (TypeError, ValueError):
                beat_int = None
            if requested_beats is not None and beat_int not in requested_beats:
                continue
            sketch_url = b.get("sketch_url") or ""
            video_url = b.get("video_url") or ""
            sketches.append(
                {
                    "beat_number": beat_number,
                    "sketch_url": sketch_url,
                    "sketch_source": "sketch" if sketch_url else "",
                    "video_url": video_url,
                    "characters": b.get("character_names") or b.get("characters"),
                }
            )
            if sketch_url:
                media_items.append(
                    {
                        "src": sketch_url,
                        "title": f"Beat {beat_number} 草图",
                        "description": "草图",
                        "aspectRatio": "3/4",
                    }
                )
        limited_media = _limit_items(media_items, args, 12)
        return tool_result(
            {
                "ok": True,
                "episode": episode,
                "media_kind": media_kind,
                "count": len(sketches),
                "sketches": sketches,
                "ui_spec": _image_ui_spec("sketch_gallery", limited_media) if limited_media else None,
            }
        )
    except Exception as exc:
        return tool_result({"ok": False, "error": str(exc)})


def _handle_get_first_frames(args: dict[str, Any], **_: Any) -> str:
    """Get display-ready first-frame URLs for an episode (to SHOW the user).

    Wraps GET /projects/{project}/episodes/{episode}/beats and returns, per beat,
    the servable ``frame_url``. Use ``aipdd_drama_get_sketches`` for sketches.
    """
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        resp = _request("GET", f"/api/v1/projects/{project}/episodes/{episode}/beats")
        items: Any = None
        if isinstance(resp, dict):
            for key in ("data", "beats", "items"):
                value = resp.get(key)
                if isinstance(value, list):
                    items = value
                    break
            if items is None and isinstance(resp.get("data"), dict):
                items = resp["data"].get("beats")
        frames = []
        media_items = []
        requested_beats = _requested_beats(args)
        for b in items or []:
            if not isinstance(b, dict):
                continue
            beat_number = b.get("beat_number")
            try:
                beat_int = int(beat_number)
            except (TypeError, ValueError):
                beat_int = None
            if requested_beats is not None and beat_int not in requested_beats:
                continue
            frame_url = b.get("frame_url") or ""
            frames.append(
                {
                    "beat_number": beat_number,
                    "frame_url": frame_url,
                    "video_url": b.get("video_url") or "",
                    "characters": b.get("character_names") or b.get("characters"),
                }
            )
            if frame_url:
                media_items.append(
                    {
                        "src": frame_url,
                        "title": f"Beat {beat_number} 首帧",
                        "description": "首帧",
                        "aspectRatio": "3/4",
                    }
                )
        limited_media = _limit_items(media_items, args, 12)
        return tool_result(
            {
                "ok": True,
                "episode": episode,
                "media_kind": "frame",
                "count": len(frames),
                "frames": frames,
                "ui_spec": _image_ui_spec("sketch_gallery", limited_media) if limited_media else None,
            }
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_get_sketch_candidates(args: dict[str, Any], **_: Any) -> str:
    """Get display-ready sketch pool candidates for one beat."""
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        beat = int(args.get("beat") or args.get("beat_num") or args.get("beat_number") or 0)
        if beat <= 0:
            raise ValueError("beat is required")
        resp = _request(
            "GET",
            f"/api/v1/projects/{project}/episodes/{episode}/beats/{beat}/sketch-candidates",
        )
        data = resp.get("data") if isinstance(resp, dict) else None
        if not isinstance(data, dict):
            data = {}
        candidates = data.get("candidates") if isinstance(data.get("candidates"), list) else []
        media_items = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            src = str(candidate.get("url") or "").strip()
            if not src:
                continue
            stale = bool(candidate.get("stale"))
            media_items.append(
                {
                    "src": src,
                    "title": f"Beat {beat} 草图候选",
                    "description": "过期候选" if stale else "草图候选",
                    "aspectRatio": "3/4",
                }
            )
        limited_media = _limit_items(media_items, args, 12)
        return tool_result(
            {
                "ok": bool(resp.get("ok", True)) if isinstance(resp, dict) else True,
                "episode": episode,
                "beat": beat,
                "media_kind": "sketch_candidate",
                "current_sketch_url": data.get("current_sketch_url", ""),
                "candidate_count": int(data.get("candidate_count") or len(candidates)),
                "candidates": candidates,
                "ui_spec": _image_ui_spec("sketch_gallery", limited_media) if limited_media else None,
            }
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_get_scene_images(args: dict[str, Any], **_: Any) -> str:
    """Get display-ready scene image URLs for a project (to SHOW the user).

    Wraps GET /projects/{project}/scenes and returns only servable scene asset
    URLs. Do NOT use local ``*_path`` fields and do NOT synthesize URLs.
    """
    try:
        project = _project_from_args(args)
        include_reverse = bool(args.get("include_reverse", True))
        include_pano = bool(args.get("include_pano", False))
        include_custom = bool(args.get("include_custom", False))
        resp = _request("GET", f"/api/v1/projects/{project}/scenes")
        items: Any = None
        if isinstance(resp, dict):
            for key in ("data", "scenes", "items"):
                value = resp.get(key)
                if isinstance(value, list):
                    items = value
                    break
            if items is None and isinstance(resp.get("data"), dict):
                items = resp["data"].get("scenes")

        scenes = []
        media_items = []
        image_count = 0
        requested_names = _requested_scene_names(args)
        requested_indices = _requested_scene_indices(args)
        requested_type = str(args.get("scene_type") or "").strip()
        for scene_index, scene in enumerate(items or [], start=1):
            if not isinstance(scene, dict):
                continue
            scene_name = str(scene.get("name") or "").strip()
            scene_type = str(scene.get("scene_type") or "").strip()
            if requested_indices is not None and scene_index not in requested_indices:
                continue
            if not _matches_any_scene_name(scene_name, requested_names):
                continue
            if requested_type and scene_type != requested_type:
                continue
            images = []
            for kind, field, enabled in (
                ("master", "master_url", True),
                ("reverse_master", "reverse_master_url", include_reverse),
                ("pano", "pano_url", include_pano),
                ("custom_scene", "custom_scene_url", include_custom),
            ):
                url = str(scene.get(field) or "").strip()
                if enabled and url:
                    images.append({"kind": kind, "url": url})
                    media_items.append(
                        {
                            "src": url,
                            "title": f"{scene_name or '场景'} · {kind}",
                            "description": scene.get("description") or scene.get("environment_prompt") or "",
                            "aspectRatio": "16/9" if kind == "pano" else "3/4",
                        }
                    )
            image_count += len(images)
            scenes.append(
                {
                    "index": scene_index,
                    "name": scene_name,
                    "scene_type": scene_type,
                    "description": scene.get("description") or "",
                    "environment_prompt": scene.get("environment_prompt") or "",
                    "master_url": scene.get("master_url") or "",
                    "reverse_master_url": scene.get("reverse_master_url") or "",
                    "pano_url": scene.get("pano_url") or "",
                    "custom_scene_url": scene.get("custom_scene_url") or "",
                    "images": images,
                }
            )
        limited_media = _limit_items(media_items, args, 12)
        return tool_result(
            {
                "ok": True,
                "project_id": project,
                "count": len(scenes),
                "image_count": image_count,
                "scenes": scenes,
                "ui_spec": _image_ui_spec("sketch_gallery", limited_media) if limited_media else None,
            }
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_get_character_media(args: dict[str, Any], **_: Any) -> str:
    """Get display-ready character portrait/identity image URLs."""
    try:
        project = _project_from_args(args)
        media_kind = str(args.get("media_kind") or args.get("kind") or "all").strip().lower()
        if media_kind not in {"all", "portrait", "identity"}:
            media_kind = "all"
        include_identities = bool(args.get("include_identities", True)) and media_kind != "portrait"
        resp = _request("GET", f"/api/v1/projects/{project}/characters")
        items: Any = None
        if isinstance(resp, dict):
            for key in ("data", "characters", "items"):
                value = resp.get(key)
                if isinstance(value, list):
                    items = value
                    break
            if items is None and isinstance(resp.get("data"), dict):
                items = resp["data"].get("characters")

        characters = []
        media_items = []
        requested_names = _requested_names(args)
        requested_queries = _requested_queries(args)
        for character in items or []:
            if not isinstance(character, dict):
                continue
            name = str(character.get("name") or "").strip()
            role = str(character.get("role") or character.get("description") or "").strip()
            character_name_match = _matches_any_text(
                [name, character.get("aliases")],
                requested_names,
            )
            character_query_match = _matches_any_text(
                [
                    name,
                    role,
                    character.get("description"),
                    character.get("appearance"),
                    character.get("profile"),
                    character.get("aliases"),
                ],
                requested_queries,
            )
            character_match = character_name_match and character_query_match
            portrait_url = str(character.get("portrait_url") or "").strip()
            if portrait_url and character_match:
                if media_kind in {"all", "portrait"}:
                    media_items.append(
                        {
                            "src": portrait_url,
                            "title": name or "角色肖像",
                            "description": role,
                            "aspectRatio": "3/4",
                        }
                    )
            identity_items = []
            identities = character.get("identities") or character.get("identity_images") or []
            if include_identities:
                try:
                    identities_resp = _request(
                        "GET",
                        f"/api/v1/projects/{project}/characters/{quote(name, safe='')}/identities",
                    )
                    if isinstance(identities_resp, dict):
                        for key in ("data", "identities", "items"):
                            value = identities_resp.get(key)
                            if isinstance(value, list):
                                identities = value
                                break
                        if isinstance(identities_resp.get("data"), dict):
                            value = identities_resp["data"].get("identities")
                            if isinstance(value, list):
                                identities = value
                except Exception:
                    pass
            if include_identities and isinstance(identities, list):
                for identity in identities:
                    if not isinstance(identity, dict):
                        continue
                    image_url = str(
                        identity.get("image_url")
                        or identity.get("portrait_image_url")
                        or identity.get("costume_image_url")
                        or ""
                    ).strip()
                    if not image_url:
                        continue
                    title = str(
                        identity.get("identity_name")
                        or identity.get("name")
                        or identity.get("identity_id")
                        or name
                        or "身份图"
                    )
                    identity_name_match = _matches_any_text(
                        [
                            name,
                            character.get("aliases"),
                            title,
                            identity.get("identity_name"),
                            identity.get("name"),
                            identity.get("identity_id"),
                        ],
                        requested_names,
                    )
                    identity_query_match = _matches_any_text(
                        [
                            title,
                            identity.get("identity_name"),
                            identity.get("name"),
                            identity.get("identity_id"),
                            identity.get("description"),
                            identity.get("appearance_details"),
                            identity.get("prompt"),
                            identity.get("role"),
                            name,
                            role,
                        ],
                        requested_queries,
                    )
                    identity_match = identity_name_match and identity_query_match
                    if not identity_match:
                        continue
                    identity_items.append({"title": title, "image_url": image_url})
                    media_items.append(
                        {
                            "src": image_url,
                            "title": f"{name} · {title}" if name else title,
                            "description": role,
                            "aspectRatio": "3/4",
                        }
                    )
            if (requested_names is not None or requested_queries is not None) and not character_match and not identity_items:
                continue
            characters.append(
                {
                    "name": name,
                    "role": role,
                    "portrait_url": portrait_url,
                    "identities": identity_items,
                }
            )

        limited_media = _limit_items(media_items, args, 12)
        return tool_result(
            {
                "ok": True,
                "project_id": project,
                "count": len(characters),
                "media_count": len(media_items),
                "characters": characters,
                "ui_spec": _image_ui_spec("character_showcase", limited_media) if limited_media else None,
            }
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_get_episode_media(args: dict[str, Any], **_: Any) -> str:
    """Get display-ready episode video/audio URLs."""
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        media_type = str(args.get("media_type") or "video").strip().lower()
        resp = _request("GET", f"/api/v1/projects/{project}/episodes/{episode}/beats")
        items: Any = None
        if isinstance(resp, dict):
            for key in ("data", "beats", "items"):
                value = resp.get(key)
                if isinstance(value, list):
                    items = value
                    break
            if items is None and isinstance(resp.get("data"), dict):
                items = resp["data"].get("beats")

        video_items = []
        audio_items = []
        beats = []
        requested_beats = _requested_beats(args)
        requested_queries = _requested_queries(args)
        for beat in items or []:
            if not isinstance(beat, dict):
                continue
            beat_number = beat.get("beat_number")
            try:
                beat_int = int(beat_number)
            except (TypeError, ValueError):
                beat_int = None
            if requested_beats is not None and beat_int not in requested_beats:
                continue
            if not _matches_any_text(
                [
                    beat.get("title"),
                    beat.get("summary"),
                    beat.get("description"),
                    beat.get("visual_description"),
                    beat.get("image_prompt"),
                    beat.get("video_prompt"),
                    beat.get("narration"),
                    beat.get("voiceover"),
                    beat.get("dialogue"),
                    beat.get("audio_text"),
                    beat.get("speaker"),
                    beat.get("character_names"),
                    beat.get("characters"),
                    beat.get("scene_name"),
                    beat.get("location"),
                ],
                requested_queries,
            ):
                continue
            video_url = str(beat.get("video_url") or "").strip()
            audio_url = str(beat.get("audio_url") or "").strip()
            frame_url = str(beat.get("frame_url") or beat.get("sketch_url") or "").strip()
            beats.append({"beat_number": beat_number, "video_url": video_url, "audio_url": audio_url})
            if video_url:
                video_items.append(
                    {
                        "src": video_url,
                        "poster": frame_url,
                        "title": f"Beat {beat_number} 视频",
                    }
                )
            if audio_url:
                audio_items.append({"src": audio_url, "title": f"Beat {beat_number} 音频"})

        if media_type == "audio":
            limited = _limit_items(audio_items, args, 20)
            ui_spec = _audio_ui_spec(limited) if limited else None
        else:
            limited = _limit_items(video_items, args, 6)
            ui_spec = _video_ui_spec(limited) if limited else None
        return tool_result(
            {
                "ok": True,
                "project_id": project,
                "episode": episode,
                "media_type": media_type,
                "video_count": len(video_items),
                "audio_count": len(audio_items),
                "beats": beats,
                "ui_spec": ui_spec,
            }
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_render_first_frames(args: dict[str, Any], **_: Any) -> str:
    """Generate first frames for an episode (首帧生成, selected_regen task).

    Wraps POST /projects/{project}/episodes/{episode}/beats/regenerate with
    ``{"beat_indices": [...]}``. If ``beat_indices`` is omitted, ALL beats of the
    episode are resolved automatically (GET /episodes/{ep}/beats). Requires sketches
    to exist first. Poll aipdd_drama_get_task(task_type="selected_regen", episode=N).
    """
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        beats = args.get("beat_indices") or args.get("beats")
        if not isinstance(beats, list) or not beats:
            beats = _resolve_episode_beats(project, episode)
            if not beats:
                raise ValueError(
                    "could not resolve beats for this episode; generate sketches first "
                    "or pass beat_indices explicitly"
                )
        body: dict[str, Any] = {"beat_indices": [int(b) for b in beats]}
        if args.get("style"):
            body["style"] = str(args["style"])
        if args.get("model"):
            body["model"] = str(args["model"])
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/episodes/{episode}/beats/regenerate",
                body=body,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_compose_episode(args: dict[str, Any], **_: Any) -> str:
    """Compose/export the final video for one episode (合成导出, compose_episode task).

    POST /projects/{project}/episodes/{episode}/videos/compose.
    Poll task_type="compose_episode", episode=N.
    """
    try:
        return tool_result(_episode_post(args, "videos/compose"))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_get_final_video(args: dict[str, Any], **_: Any) -> str:
    """Get and display the composed final episode video when it exists."""
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        result = _request("GET", f"/api/v1/projects/{project}/episodes/{episode}/final")
        data = result.get("data") if isinstance(result, dict) else None
        video_url = ""
        if isinstance(data, dict) and data.get("exists"):
            video_url = str(data.get("video_url") or "").strip()
        if video_url and isinstance(result, dict):
            result["ui_spec"] = _video_ui_spec(
                [
                    {
                        "src": video_url,
                        "title": f"第 {episode} 集成片",
                        "description": "最终合成视频",
                    }
                ]
            )
        return tool_result(result)
    except Exception as exc:
        return tool_error(str(exc))


def _handle_generate_portrait(args: dict[str, Any], **_: Any) -> str:
    """Generate one character's portrait (肖像生成, character_portrait task).

    POST /projects/{project}/characters/{name}/portrait-async. Poll
    task_type="character_portrait" (per character). Read via
    aipdd_drama_get('/projects/{project}/characters').
    """
    try:
        project = _project_from_args(args)
        name = _require_name(args)
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/characters/{quote(name, safe='')}/portrait-async")
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_generate_identity_image(args: dict[str, Any], **_: Any) -> str:
    """Generate a character identity image (身份图生成, identity_image task).

    POST /projects/{project}/characters/{name}/identities/{identity_id}/generate-async.
    Needs both the character name and the identity_id (from the character's identity
    list). Poll task_type="identity_image".
    """
    try:
        project = _project_from_args(args)
        name = _require_name(args)
        identity_id = str(args.get("identity_id") or "").strip()
        if not identity_id:
            raise ValueError("identity_id is required")
        path = (
            f"/api/v1/projects/{project}/characters/{quote(name, safe='')}"
            f"/identities/{quote(identity_id, safe='')}/generate-async"
        )
        return tool_result(_request("POST", path))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_start_single_video(args: dict[str, Any], **_: Any) -> str:
    """Generate one beat's video (单 beat 视频, single_video task).

    POST /projects/{project}/episodes/{episode}/beats/{beat}/video. The beat's
    prompt is taken from its stored ``video_prompt`` (set by the script step) —
    you do NOT pass a prompt. Requires the beat's first frame to exist already
    (otherwise the API returns "首帧不存在") and the beat to have a non-empty
    video_prompt (otherwise the backend returns "prompt is required"). Only
    video_backend / duration / resolution / mode are accepted request fields.
    """
    try:
        project = _project_from_args(args)
        episode = int(args.get("episode") or 1)
        beat = int(args.get("beat") or args.get("beat_number") or 0)
        if beat <= 0:
            raise ValueError("beat must be a positive integer")
        body: dict[str, Any] = {}
        for key in ("video_backend", "duration", "resolution", "mode"):
            if args.get(key) is not None:
                body[key] = args[key]
        return tool_result(_request("POST", f"/api/v1/projects/{project}/episodes/{episode}/beats/{beat}/video", body=body))
    except Exception as exc:
        return tool_error(str(exc))


_PATH_PROPS = {
    "path": {
        "type": "string",
        "description": (
            "AIPDD relative API path. Must start with /api/v1/ or /projects/. "
            "Absolute URLs are rejected. Ingest routes are only "
            "/projects/{project}/ingest/upload and /projects/{project}/ingest/start; "
            "ingest_fast is a task_type, not an endpoint."
        ),
    },
    "query": {"type": "object", "description": "Optional query parameters."},
}


# ---------------------------------------------------------------------------
# [aipdd fork delta] VectCut 剪辑工具组
#
# VectCut（services/vectcut）是独立进程的剪辑草稿服务：create_draft 起一张
# 画布，add_* 往时间线放素材，save_draft 落盘为剪映/CapCut 草稿。此处把它
# 包装成 MCP 工具：媒体引用（本地路径 / Dramaclaw /static URL / 公网直链）
# 由服务的 /aipdd/resolve_media 统一解析，草稿默认落到部署方可控的目录，
# 调用方不需要关心路径换算、认证与草稿文件夹。
# ---------------------------------------------------------------------------

VECTCUT_DEFAULT_BASE_URL = ""
# [aipdd fork delta req41] 不兜底 ``http://127.0.0.1:9001``：VectCut 跑在哪是部署形态
# 数据（compose 注入 ``VECTCUT_BASE_URL=http://vectcut:9001``，宿主机直跑才是
# 127.0.0.1）。旧默认值在容器里指向 api 容器自己，注定连不上，还会把“失败”
# 伪装成“服务未启动”。没配置就直说没配置。
VECTCUT_NOT_CONFIGURED_HINT = (
    "VectCut service address is not configured: set VECTCUT_BASE_URL "
    "(docker compose injects http://vectcut:9001; a host-run service is usually "
    "http://127.0.0.1:9001). Start it with scripts/start-vectcut.sh or "
    "docker compose -f docker-compose.aipdd.yml --profile vectcut up -d vectcut."
)
try:
    VECTCUT_TIMEOUT_SECONDS = max(60, int(os.environ.get("VECTCUT_TIMEOUT_SECONDS", "300")))
except ValueError:
    VECTCUT_TIMEOUT_SECONDS = 300

VECTCUT_OPTION_ENDPOINTS: dict[str, str] = {
    "intro_animation": "/get_intro_animation_types",
    "outro_animation": "/get_outro_animation_types",
    "combo_animation": "/get_combo_animation_types",
    "transition": "/get_transition_types",
    "mask": "/get_mask_types",
    "audio_effect": "/get_audio_effect_types",
    "font": "/get_font_types",
    "text_intro": "/get_text_intro_types",
    "text_outro": "/get_text_outro_types",
    "text_loop": "/get_text_loop_anim_types",
    "video_scene_effect": "/get_video_scene_effect_types",
    "video_character_effect": "/get_video_character_effect_types",
}

VECTCUT_MEDIA_HINT = (
    "Media reference: a local file path on the VectCut host, a Dramaclaw "
    "/static/projects/... URL (auto-resolved), or a public http(s) URL."
)


def _vectcut_base_url() -> str:
    value = (os.environ.get("VECTCUT_BASE_URL") or "").strip()
    return (value or VECTCUT_DEFAULT_BASE_URL).strip().rstrip("/")


def _vectcut_available() -> bool:
    """VectCut tools are independent of the main API credentials; opt-out only."""
    raw = (os.environ.get("VECTCUT_ENABLED") or "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _vectcut_unreachable_hint() -> str:
    base = _vectcut_base_url()
    if not base:
        return VECTCUT_NOT_CONFIGURED_HINT
    return (
        f"cannot reach the VectCut service at {base}; start it with "
        "scripts/start-vectcut.sh (or docker compose -f docker-compose.aipdd.yml "
        "--profile vectcut up -d vectcut), "
        "or set VECTCUT_BASE_URL to the service address"
    )


def _vectcut_call(method: str, path: str, *, body: Any = None) -> dict[str, Any]:
    base = _vectcut_base_url()
    if not base:
        return {"success": False, "error": VECTCUT_NOT_CONFIGURED_HINT}
    url = f"{base}{path}"
    payload = None
    headers = {
        "Accept": "application/json",
        "User-Agent": "aipdd-drama-plugin/0.1.0",
    }
    # [aipdd fork delta] 可选服务间共享密钥：部署侧设了 VECTCUT_INTERNAL_TOKEN 才附带
    # （与 vectcut 侧同名变量）；未设时 vectcut 不校验，行为不变。
    _internal_token = (os.environ.get("VECTCUT_INTERNAL_TOKEN") or "").strip()
    if _internal_token:
        headers["X-VectCut-Token"] = _internal_token
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=payload, headers=headers, method=method.upper())
    try:
        with urlopen(req, timeout=VECTCUT_TIMEOUT_SECONDS) as resp:
            data = _maybe_json(resp.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        return {
            "success": False,
            "error": _response_error_text(text) or str(exc.reason),
            "status_code": exc.code,
        }
    except URLError as exc:
        return {"success": False, "error": f"{_vectcut_unreachable_hint()} ({exc.reason})"}
    if isinstance(data, dict):
        return data
    return {"success": True, "output": data}


def _vectcut_parse_media(value: str, project_dir: str | None, *, allow_raw: bool = False) -> str:
    """Resolve a media reference to a local path; allow_raw passes unrecognized values through."""
    body: dict[str, Any] = {"reference": value}
    if project_dir:
        body["project_dir"] = project_dir
    result = _vectcut_call("POST", "/aipdd/resolve_media", body=body)
    if result.get("success"):
        return str(result.get("path") or "")
    if allow_raw and result.get("code") == "invalid":
        return value
    raise ValueError(str(result.get("error") or f"cannot resolve media reference: {value}"))


def _vectcut_body(args: dict[str, Any], media_fields: tuple[str, ...] = (), *, allow_raw: tuple[str, ...] = ()) -> dict[str, Any]:
    """Build the upstream request body: resolve media fields, drop None/empty values."""
    project_dir = str(args.get("project_dir") or "").strip() or None
    body: dict[str, Any] = {}
    for key, value in args.items():
        if key == "project_dir" or value is None or value == "":
            continue
        if key in media_fields:
            body[key] = _vectcut_parse_media(str(value).strip(), project_dir, allow_raw=key in allow_raw)
        else:
            body[key] = value
    return body


def _vectcut_require(body: dict[str, Any], *fields: str) -> None:
    missing = [field for field in fields if body.get(field) in (None, "")]
    if missing:
        raise ValueError("missing required field(s): " + ", ".join(missing))


def _handle_vectcut_health(args: dict[str, Any], **_: Any) -> str:
    try:
        return tool_result(_vectcut_call("GET", "/aipdd/health"))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_vectcut_list_options(args: dict[str, Any], **_: Any) -> str:
    try:
        kind = str(args.get("kind") or "").strip().lower()
        endpoint = VECTCUT_OPTION_ENDPOINTS.get(kind)
        if not endpoint:
            raise ValueError(
                f"unknown kind {kind!r}; expected one of: " + ", ".join(sorted(VECTCUT_OPTION_ENDPOINTS))
            )
        return tool_result(_vectcut_call("GET", endpoint))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_vectcut_create_draft(args: dict[str, Any], **_: Any) -> str:
    try:
        return tool_result(_vectcut_call("POST", "/create_draft", body=_vectcut_body(args)))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_vectcut_add_video(args: dict[str, Any], **_: Any) -> str:
    try:
        body = _vectcut_body(args, ("video_url",))
        _vectcut_require(body, "video_url", "draft_id")
        return tool_result(_vectcut_call("POST", "/add_video", body=body))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_vectcut_add_image(args: dict[str, Any], **_: Any) -> str:
    try:
        body = _vectcut_body(args, ("image_url",))
        _vectcut_require(body, "image_url", "draft_id")
        return tool_result(_vectcut_call("POST", "/add_image", body=body))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_vectcut_add_audio(args: dict[str, Any], **_: Any) -> str:
    try:
        body = _vectcut_body(args, ("audio_url",))
        _vectcut_require(body, "audio_url", "draft_id")
        return tool_result(_vectcut_call("POST", "/add_audio", body=body))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_vectcut_add_text(args: dict[str, Any], **_: Any) -> str:
    try:
        body = _vectcut_body(args)
        _vectcut_require(body, "text", "draft_id")
        return tool_result(_vectcut_call("POST", "/add_text", body=body))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_vectcut_add_subtitle(args: dict[str, Any], **_: Any) -> str:
    try:
        body = _vectcut_body(args, ("srt",), allow_raw=("srt",))
        _vectcut_require(body, "srt", "draft_id")
        return tool_result(_vectcut_call("POST", "/add_subtitle", body=body))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_vectcut_add_effect(args: dict[str, Any], **_: Any) -> str:
    try:
        body = _vectcut_body(args)
        _vectcut_require(body, "effect_type", "draft_id")
        return tool_result(_vectcut_call("POST", "/add_effect", body=body))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_vectcut_add_sticker(args: dict[str, Any], **_: Any) -> str:
    try:
        body = _vectcut_body(args)
        _vectcut_require(body, "sticker_id", "start", "end", "draft_id")
        return tool_result(_vectcut_call("POST", "/add_sticker", body=body))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_vectcut_add_keyframe(args: dict[str, Any], **_: Any) -> str:
    try:
        body = _vectcut_body(args)
        _vectcut_require(body, "draft_id")
        if not (body.get("property_type") or body.get("property_types")):
            raise ValueError("missing required field(s): property_type (or property_types for batch mode)")
        return tool_result(_vectcut_call("POST", "/add_video_keyframe", body=body))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_vectcut_get_duration(args: dict[str, Any], **_: Any) -> str:
    try:
        body = _vectcut_body(args, ("video_url",))
        _vectcut_require(body, "video_url")
        return tool_result(_vectcut_call("POST", "/get_duration", body={"url": body["video_url"]}))
    except Exception as exc:
        return tool_error(str(exc))


def _archive_vectcut_draft(args: dict[str, Any], result: Any) -> None:
    """[aipdd fork delta] save_draft 成功后把剪映草稿登记进项目资产库「剪映草稿」。

    登记是保存草稿的附加动作：草稿已经落盘、save_draft 的结果不依赖这一步，项目
    上下文缺失（无项目会话）或登记失败都不能反过来把成功翻成失败，所以这里吞掉
    异常，只把登记结果并进工具返回（``draft_archived_to_library``）。
    """
    try:
        if not isinstance(result, dict) or not result.get("success"):
            return
        draft_id = str(args.get("draft_id") or "").strip()
        if not draft_id:
            return
        project_dir = _project_output_dir()
        if project_dir is None:
            return
        from novelvideo.freezone.video_node import register_vectcut_draft_item

        name = str(args.get("project_name") or "").strip() or draft_id
        item = register_vectcut_draft_item(project_dir, draft_id=draft_id, name=name)
        result["draft_archived_to_library"] = {
            "item_id": item.get("id"),
            "name": item.get("name"),
            "folder": "剪映草稿",
        }
    except Exception:
        # 登记失败不影响 save_draft 的结果；工具结果里不加任何噪声字段。
        return


def _handle_vectcut_save_draft(args: dict[str, Any], **_: Any) -> str:
    try:
        body = _vectcut_body(args)
        _vectcut_require(body, "draft_id")
        body.setdefault("auto_deploy", True)
        # [aipdd fork delta] 设置 → 剪映草稿目录：调用方未显式指定 draft_folder 时用
        # 项目设置里的草稿目录，与资产库的下载 / 上传 / 同步四个入口同一口径；未
        # 配置（空）时不注入，VectCut 侧回退部署默认（VECTCUT_DRAFT_DIR）。
        if not body.get("draft_folder"):
            try:
                from novelvideo.model_gateway_settings import get_vectcut_draft_dir

                configured = get_vectcut_draft_dir()
                if configured:
                    body["draft_folder"] = configured
            except Exception:  # noqa: BLE001 - 设置读取失败不阻断保存草稿
                pass
        result = _vectcut_call("POST", "/aipdd/save_draft", body=body)
        _archive_vectcut_draft(args, result)
        return tool_result(result)
    except Exception as exc:
        return tool_error(str(exc))


# ---------------------------------------------------------------------------
# [aipdd fork delta] 工具页 MCP 工具组（视频复刻 / 混剪短视频 / 一句话生成
# 短视频 / 人声复刻 / 对口型 / 换人 / 视频去水印 / 图片去水印 / 抖音文案提取）
#
# 与前端「工具」栏目九个面板一一对应：复用同一批 freezone 路由与任务队列。
# 除抖音文案提取外，产物默认登记进资产库「待分类资产」（archive_to_library=false
# 关闭）；抖音文案提取产物是文案 txt，素材库三类媒介无文本载体，不归档。
# 提交返回 {"ok": true, "data": {task_type, job_id, task_key}}；完成后用
# GET /projects/{project}/freezone/jobs/{task_type}/{job_id}/result 取产物 URL。
# ---------------------------------------------------------------------------


def _tools_page_archive_flag(args: dict[str, Any]) -> bool:
    """工具页口径：默认归档进资产库「待分类资产」，显式 false 才关闭。"""
    raw = args.get("archive_to_library")
    if isinstance(raw, str):
        return raw.strip().lower() not in {"0", "false", "no", "off"}
    return True if raw is None else bool(raw)


def _require_url_arg(args: dict[str, Any], key: str) -> str:
    value = str(args.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _omni_gen_body(args: dict[str, Any], references: list[dict[str, Any]]) -> dict[str, Any]:
    """omni-gen（allReference）公共请求体：对口型 / 换人 / 换产品共用。"""
    body: dict[str, Any] = {
        "prompt": str(args.get("prompt") or ""),
        "references": references,
        "gen_mode": "allReference",
        "archive_to_library": _tools_page_archive_flag(args),
    }
    for key in ("model", "resolution", "duration_seconds", "aspect_ratio", "generate_audio"):
        if args.get(key) is not None:
            body[key] = args[key]
    # [aipdd fork delta req42] 画幅默认跟随源视频：这三个入口的输入就是一条竖屏短视频，
    # 没显式给值时落到「16:9」会让产物和原视频看着完全无关（路由收到 auto 时按素材
    # 画面比例探测，并限制在渠道目录允许的档位里）。
    if not body.get("aspect_ratio"):
        body["aspect_ratio"] = "auto"
    if args.get("model"):
        # 与画布/工具页同口径：model 与 model_id 都传目录 id，后端 identifiers 都认。
        body["model_id"] = args["model"]
    return body


def _handle_video_clone(args: dict[str, Any], **_: Any) -> str:
    """视频复刻（全自动成片，freezone_video_clone）。

    参考视频 → 抽帧解读 → 逐镜复刻规划 → 逐镜生成 → 自动拼接成片。
    用户可提供 prompt（处理要求）与 reference_urls（参考内容图，≤4 张）。
    """
    try:
        project = _project_from_args(args)
        body: dict[str, Any] = {
            "video_url": _require_url_arg(args, "video_url"),
            "archive_to_library": _tools_page_archive_flag(args),
        }
        for key in (
            "prompt",
            "model",
            "reference_mode",
            "audio_mode",
            "aspect_ratio",
            "resolution",
            "max_frames",
            "scene_threshold",
            "human_review",
            "scene_optimize",
        ):
            if args.get(key) is not None:
                body[key] = args[key]
        # [aipdd fork delta] 参考内容：用户提供的参考图（≤4 张 /static URL），与提示词
        # 一起作用于对原视频的复刻（需模型支持 all_reference）。
        raw_references = args.get("reference_urls")
        if raw_references is not None:
            if not isinstance(raw_references, list):
                raise ValueError("reference_urls must be a list of /static image URLs")
            references = [
                str(item).strip() for item in raw_references if str(item or "").strip()
            ]
            if references:
                body["reference_urls"] = references
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/video/clone", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_video_mix(args: dict[str, Any], **_: Any) -> str:
    """混剪短视频（freezone_video_mix）：素材池 → 配音 → 字幕/BGM → 本地成片。

    素材按给定顺序切段、循环铺满配音时长；图片推拉、视频取段，原声丢弃。
    """
    try:
        project = _project_from_args(args)
        raw_materials = args.get("material_urls")
        if not isinstance(raw_materials, list):
            raise ValueError("material_urls is required (a list of 1-60 /static URLs)")
        materials = [
            str(item).strip() for item in raw_materials if str(item or "").strip()
        ]
        if not materials:
            raise ValueError("material_urls is required (a list of 1-60 /static URLs)")
        if len(materials) > 60:
            raise ValueError("material_urls accepts at most 60 entries")
        audio_mode = str(args.get("audio_mode") or "generate").strip().lower() or "generate"
        if audio_mode not in {"generate", "mute"}:
            raise ValueError("audio_mode must be generate or mute")
        subject = str(args.get("subject") or "").strip()
        script = str(args.get("script") or "").strip()
        if audio_mode == "generate" and not subject and not script:
            raise ValueError("subject or script is required when audio_mode=generate")
        body: dict[str, Any] = {
            "material_urls": materials,
            "subject": subject,
            "script": script,
            "audio_mode": audio_mode,
            "archive_to_library": _tools_page_archive_flag(args),
        }
        for key in (
            "subtitle_enabled",
            "subtitle_font_size",
            "bgm_url",
            "bgm_volume",
            "aspect_ratio",
            "resolution",
            "clip_duration",
            "transition",
            "fit_mode",
            "max_duration_seconds",
            "emotion_prompt",
            "model",
        ):
            if args.get(key) is not None:
                body[key] = args[key]
        voice_ref = {
            "scope": str(args.get("voice_scope") or "").strip(),
            "character_name": str(args.get("voice_character_name") or "").strip(),
            "identity_id": str(args.get("voice_identity_id") or "").strip(),
            "slot": str(args.get("voice_slot") or "").strip(),
            "voice_id": str(args.get("voice_id") or "").strip(),
        }
        if any(voice_ref.values()):
            if not voice_ref["scope"]:
                raise ValueError(
                    "voice_scope is required when any voice_* field is provided "
                    "(project_narrator/user_custom/character_default/character_age_group/identity/identity_resolved)"
                )
            body["voice_ref"] = voice_ref
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/video/mix", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_whiteboard_animation(args: dict[str, Any], **_: Any) -> str:
    """白板动画视频（freezone_whiteboard_animation）：口播文案 → 分镜 → 逐幕
    暖米黄线稿 → 逐笔揭示 → 拼接 + 配音（+ 可选字幕/BGM）成片。

    三类模型（文本分镜规划 / 图像线稿 / 配音）均可自选，留空走系统默认链；
    逐笔揭示与拼接/混音全部纯本地渲染（OpenCV + ffmpeg）。一次提交只产出一条成片。
    """
    try:
        project = _project_from_args(args)
        script = str(args.get("script") or "").strip()
        if not script:
            raise ValueError("script is required (the whiteboard narration text)")
        if len(script) > 10_000:
            raise ValueError("script must be <= 10000 characters")
        body: dict[str, Any] = {
            "script": script,
            "archive_to_library": _tools_page_archive_flag(args),
        }
        for key in (
            "model_text",
            "model_image",
            "image_model_params",
            "model",
            "aspect_ratio",
            "resolution",
            "max_scenes",
            "pen_cursor",
            "subtitle_enabled",
            "subtitle_font_size",
            "emotion_prompt",
            "bgm_url",
            "bgm_volume",
        ):
            if args.get(key) is not None:
                body[key] = args[key]
        voice_ref = {
            "scope": str(args.get("voice_scope") or "").strip(),
            "character_name": str(args.get("voice_character_name") or "").strip(),
            "identity_id": str(args.get("voice_identity_id") or "").strip(),
            "slot": str(args.get("voice_slot") or "").strip(),
            "voice_id": str(args.get("voice_id") or "").strip(),
        }
        if any(voice_ref.values()):
            if not voice_ref["scope"]:
                raise ValueError(
                    "voice_scope is required when any voice_* field is provided "
                    "(project_narrator/user_custom/character_default/character_age_group/identity/identity_resolved)"
                )
            body["voice_ref"] = voice_ref
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/freezone/whiteboard-animation",
                body=body,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_explainer_video(args: dict[str, Any], **_: Any) -> str:
    """科普讲解视频（freezone_explainer_video）：参考资料 → 分章解说词 → 分镜
    JSON → 本地按朗读权重分配时长 → 产出 HyperFrames 帧项目无头渲染成片（默认，
    DRAMACLAW_RENDERER_URL），渲染服务不可达时回退 Pillow 逐帧绘制 + ffmpeg。

    画面先渲染为静音，再在 api 侧 ffmpeg 混配音（+ 可选 BGM）；只用到网关的文本
    与音频两类能力，不调图像/视频模型；两者都可留空走系统默认链。一次提交一条成片。
    """
    try:
        project = _project_from_args(args)
        reference = str(args.get("reference_content") or "").strip()
        if not reference:
            raise ValueError("reference_content is required (the material to explain)")
        if len(reference) > 12_000:
            raise ValueError("reference_content must be <= 12000 characters")
        body: dict[str, Any] = {
            "reference_content": reference,
            "archive_to_library": _tools_page_archive_flag(args),
        }
        for key in (
            "title",
            "duration_s",
            "model_text",
            "model",
            "background",
            "resolution",
            "style_preset",
            "format",
            "captions",
            "emotion_prompt",
            "bgm_url",
            "bgm_volume",
        ):
            if args.get(key) is not None:
                body[key] = args[key]
        voice_ref = {
            "scope": str(args.get("voice_scope") or "").strip(),
            "character_name": str(args.get("voice_character_name") or "").strip(),
            "identity_id": str(args.get("voice_identity_id") or "").strip(),
            "slot": str(args.get("voice_slot") or "").strip(),
            "voice_id": str(args.get("voice_id") or "").strip(),
        }
        if any(voice_ref.values()):
            if not voice_ref["scope"]:
                raise ValueError(
                    "voice_scope is required when any voice_* field is provided "
                    "(project_narrator/user_custom/character_default/character_age_group/identity/identity_resolved)"
                )
            body["voice_ref"] = voice_ref
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/freezone/explainer-video",
                body=body,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_channel_short_video(args: dict[str, Any], **_: Any) -> str:
    """渠道短视频（freezone_channel_short_video）：文章/素材 → 复用讲解视频渲染管线，
    按渠道（douyin/kuaishou/xiaohongshu/shipinhao/bilibili/tiktok）定制画幅/时长/字幕/封面，
    产出该渠道规格 MP4 + 封面 + 文案建议；**不自动上传**。

    源可以是 ``reference_content``（参考正文），也可以直接指向一篇已排版的
    公众号文章（``article_job_id``，后端读其 Markdown 正文）。只走 HyperFrames 引擎（需
    配置 DRAMACLAW_RENDERER_URL，竖屏/方形无 Pillow 回退）；画幅由渠道决定，无需传 format。
    """
    try:
        project = _project_from_args(args)
        reference = str(args.get("reference_content") or "").strip()
        article_job_id = str(args.get("article_job_id") or "").strip()
        if not reference and not article_job_id:
            raise ValueError("reference_content or article_job_id is required")
        if len(reference) > 12_000:
            raise ValueError("reference_content must be <= 12000 characters")
        body: dict[str, Any] = {
            "archive_to_library": _tools_page_archive_flag(args),
        }
        if reference:
            body["reference_content"] = reference
        if article_job_id:
            body["article_job_id"] = article_job_id
        for key in (
            "channel",
            "title",
            "duration_s",
            "model_text",
            "model",
            "style_preset",
            "captions",
            "resolution",
            "emotion_prompt",
            "bgm_url",
            "bgm_volume",
        ):
            if args.get(key) is not None:
                body[key] = args[key]
        voice_ref = {
            "scope": str(args.get("voice_scope") or "").strip(),
            "character_name": str(args.get("voice_character_name") or "").strip(),
            "identity_id": str(args.get("voice_identity_id") or "").strip(),
            "slot": str(args.get("voice_slot") or "").strip(),
            "voice_id": str(args.get("voice_id") or "").strip(),
        }
        if any(voice_ref.values()):
            if not voice_ref["scope"]:
                raise ValueError(
                    "voice_scope is required when any voice_* field is provided "
                    "(project_narrator/user_custom/character_default/character_age_group/identity/identity_resolved)"
                )
            body["voice_ref"] = voice_ref
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/freezone/channel-short-video",
                body=body,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_narration_smart_edit(args: dict[str, Any], **_: Any) -> str:
    """口播智能剪辑（freezone_narration_smart_edit）：一条口播视频 →
    本地静音剪 + 网关 ASR/文本瑕疵剪与分镜/B-roll 判定 + 生图/生乐 →
    直出 MP4 或 VectCut 剪映草稿（或两者）。只调用模型网关与 VectCut，不引外部运行期。"""
    try:
        project = _project_from_args(args)
        video_url = str(args.get("video_url") or "").strip()
        if not video_url:
            raise ValueError("video_url is required (a same-origin upload path or /static asset URL)")
        body: dict[str, Any] = {
            "video_url": video_url,
            "archive_to_library": _tools_page_archive_flag(args),
        }
        for key in (
            "output_mode",
            "aspect",
            "cut_silence",
            "cut_fillers",
            "plan_broll",
            "gen_bgm",
            "captions",
            "noise_db",
            "min_silence_s",
            "broll_max",
            "asr_model",
            "text_model",
            "image_model",
            "music_model",
            "bgm_prompt",
            "bgm_gain",
            "title",
        ):
            if args.get(key) is not None:
                body[key] = args[key]
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/freezone/narration-smart-edit",
                body=body,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_video_idea(args: dict[str, Any], **_: Any) -> str:
    """一句话生成短视频（freezone_video_idea）：创意 → 故事 → 角色 → 分镜 → 逐镜成片。

    一次提交产出一条完整成片：故事扩写 → 角色提取与三视图 → 分场景脚本 →
    分镜/机位树/首尾帧 → 逐镜视频 → 拼接。肖像与逐镜帧走图片目录、逐镜视频
    走视频目录；两处模型都可留空使用后端默认。
    """
    try:
        project = _project_from_args(args)
        idea = str(args.get("idea") or "").strip()
        if not idea:
            raise ValueError("idea is required")
        if len(idea) > 2000:
            raise ValueError("idea must be <= 2000 characters")
        portrait_mode = str(args.get("portrait_mode") or "three").strip().lower() or "three"
        if portrait_mode not in {"three", "front"}:
            raise ValueError("portrait_mode must be three or front")
        body: dict[str, Any] = {
            "idea": idea,
            "portrait_mode": portrait_mode,
            "archive_to_library": _tools_page_archive_flag(args),
        }
        for key in (
            "style",
            "user_requirement",
            "max_scenes",
            "max_shots_per_scene",
            "shot_duration",
            "model_image",
            "model_video",
            "aspect_ratio",
            "resolution",
            "generate_audio",
            "human_review",
            "scene_optimize",
        ):
            if args.get(key) is not None:
                body[key] = args[key]
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/video/idea", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_voice_clone(args: dict[str, Any], **_: Any) -> str:
    """人声复刻（freezone_audio_speech）：按文本合成语音，可选声线引用。"""
    try:
        project = _project_from_args(args)
        text = str(args.get("text") or "").strip()
        if not text:
            raise ValueError("text is required")
        body: dict[str, Any] = {
            "text": text,
            "archive_to_library": _tools_page_archive_flag(args),
        }
        for key in ("emotion_prompt", "model", "target_episode", "target_beat"):
            if args.get(key) is not None:
                body[key] = args[key]
        voice_ref = {
            "scope": str(args.get("voice_scope") or "").strip(),
            "character_name": str(args.get("voice_character_name") or "").strip(),
            "identity_id": str(args.get("voice_identity_id") or "").strip(),
            "slot": str(args.get("voice_slot") or "").strip(),
            "voice_id": str(args.get("voice_id") or "").strip(),
        }
        if any(voice_ref.values()):
            if not voice_ref["scope"]:
                raise ValueError(
                    "voice_scope is required when any voice_* field is provided "
                    "(project_narrator/user_custom/character_default/character_age_group/identity/identity_resolved)"
                )
            body["voice_ref"] = voice_ref
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/audio/speech", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_video_dubbing(args: dict[str, Any], **_: Any) -> str:
    """[aipdd fork delta] 视频配音（freezone_video_dubbing）：原视频 + 参考音色
    → 抽取台词（不翻译）→零样本克隆原语言重声→回填替换整条音轨→ mp4 成片。"""
    try:
        project = _project_from_args(args)
        source_url = str(args.get("source_url") or "").strip()
        if not source_url:
            raise ValueError("source_url is required")
        reference_audio_url = str(args.get("reference_audio_url") or "").strip()
        if not reference_audio_url:
            raise ValueError("reference_audio_url is required")
        body: dict[str, Any] = {
            "source_url": source_url,
            "reference_audio_url": reference_audio_url,
            "archive_to_library": _tools_page_archive_flag(args),
            "source_tool": str(args.get("source_tool") or "video-dubbing"),
        }
        for key in (
            "reference_name",
            "language",
            "model",
            "asr_model",
            "target_episode",
            "target_beat",
        ):
            if args.get(key) not in (None, ""):
                body[key] = args[key]
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/freezone/audio/video-dubbing",
                body=body,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


# [aipdd fork delta req46] 工具页「音乐创作」的五个模式：与后端 Literal、前端面板同名。
# MCP 侧只拦枚举本身，模式 → 上游字段的收口仍在 minimax_music.build_music_payload（提交
# 路由里已经过一遍，越界当场 400），这里不重复实现一套规则免得两处各说一套。
MUSIC_CREATE_MODES = ("one_sentence", "lyrics", "instrumental", "cover", "humming")


def _handle_music_create(args: dict[str, Any], **_: Any) -> str:
    """音乐创作（freezone_music_generate）：MiniMax Music 3.0 五模式，直连厂商音乐协议。"""
    try:
        project = _project_from_args(args)
        mode = str(args.get("mode") or "").strip()
        if mode not in MUSIC_CREATE_MODES:
            raise ValueError("mode is required, one of " + "/".join(MUSIC_CREATE_MODES))
        body: dict[str, Any] = {
            "mode": mode,
            "archive_to_library": _tools_page_archive_flag(args),
            "source_tool": str(args.get("source_tool") or "music-create"),
        }
        for key in (
            "prompt",
            "lyrics",
            "model",
            "provider",
            "reference_audio",
            "cover_feature_id",
            "sample_rate",
            "bitrate",
            "audio_format",
            "source_name",
        ):
            if args.get(key) not in (None, ""):
                body[key] = args[key]
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/freezone/audio/music-create",
                body=body,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_lip_sync(args: dict[str, Any], **_: Any) -> str:
    """对口型（omni-gen allReference）：视频 + 驱动音频 → 口型同步视频。"""
    try:
        project = _project_from_args(args)
        references = [
            {"type": "video", "url": _require_url_arg(args, "video_url"), "role": "", "label": ""},
            {"type": "audio", "url": _require_url_arg(args, "audio_url"), "role": "", "label": ""},
        ]
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/freezone/video/omni-gen",
                body=_omni_gen_body(args, references),
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_replace_person(args: dict[str, Any], **_: Any) -> str:
    """换人（omni-gen allReference）：源视频 + 人物参考图 → 替换人物视频。"""
    try:
        project = _project_from_args(args)
        references = [
            {"type": "video", "url": _require_url_arg(args, "video_url"), "role": "", "label": ""},
            {"type": "image", "url": _require_url_arg(args, "image_url"), "role": "", "label": ""},
        ]
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/freezone/video/omni-gen",
                body=_omni_gen_body(args, references),
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_replace_product(args: dict[str, Any], **_: Any) -> str:
    """换产品（omni-gen allReference）：源视频 + 产品参考图 → 替换产品视频。"""
    try:
        project = _project_from_args(args)
        references = [
            {"type": "video", "url": _require_url_arg(args, "video_url"), "role": "", "label": ""},
            {"type": "image", "url": _require_url_arg(args, "image_url"), "role": "", "label": ""},
        ]
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/freezone/video/omni-gen",
                body=_omni_gen_body(args, references),
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_video_watermark(args: dict[str, Any], **_: Any) -> str:
    """视频去水印：local/mediakit 走擦除，seedance 走 AI 视频编辑生成式重绘。"""
    try:
        project = _project_from_args(args)
        source_url = _require_url_arg(args, "source_url")
        engine = str(args.get("engine") or "local").strip().lower() or "local"
        if engine not in {"local", "mediakit", "seedance"}:
            raise ValueError("engine must be one of local/mediakit/seedance")
        if engine == "seedance":
            model = str(args.get("model") or "").strip()
            if not model:
                raise ValueError("model is required when engine=seedance")
            edit_body: dict[str, Any] = {
                "video_url": source_url,
                "prompt": str(args.get("prompt") or "去除视频中的水印与台标，保持画面其余部分不变"),
                "audio_setting": "origin",
                "model": model,
                "model_id": model,
                "gen_mode": "videoEdit",
                "archive_to_library": _tools_page_archive_flag(args),
            }
            for key in ("resolution", "human_review"):
                if args.get(key) is not None:
                    edit_body[key] = args[key]
            return tool_result(
                _request(
                    "POST",
                    f"/api/v1/projects/{project}/freezone/video/video-edit",
                    body=edit_body,
                )
            )
        erase_body: dict[str, Any] = {
            "source_url": source_url,
            "mode": str(args.get("mode") or "smart_subtitle").strip() or "smart_subtitle",
            "engine": engine,
            "archive_to_library": _tools_page_archive_flag(args),
        }
        if erase_body["mode"] == "box":
            for key in ("box_x", "box_y", "box_width", "box_height"):
                if args.get(key) is not None:
                    erase_body[key] = args[key]
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/video/erase", body=erase_body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_image_watermark(args: dict[str, Any], **_: Any) -> str:
    """图片去水印（freezone_edit）：按处理指令重绘去除水印。"""
    try:
        project = _project_from_args(args)
        body: dict[str, Any] = {
            "base_url": _require_url_arg(args, "base_url"),
            "prompt": str(args.get("prompt") or "去除图片中的水印与台标，保持画面其余部分不变"),
            "aspect_ratio": str(args.get("aspect_ratio") or "original"),
            "archive_to_library": _tools_page_archive_flag(args),
        }
        for key in ("image_size", "provider", "model", "model_id", "gen_mode", "quality"):
            if args.get(key) is not None:
                body[key] = args[key]
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/edit", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_douyin_copywrite(args: dict[str, Any], **_: Any) -> str:
    """抖音文案提取（freezone_douyin_copywrite）：分享链接 → 解析视频 → ASR 转写。

    模型候选由 /freezone/douyin-copywrite/models 提供（网关拉取的模型 list 过滤
    ASR 同类 + 内置 FunASR），由用户显式指定，不按固定路由顺序。产物是文案
    txt（素材库三类媒介无文本载体，不归档）；文案全文随任务结果返回。
    """
    try:
        project = _project_from_args(args)
        video = str(args.get("video") or "").strip()
        if not video:
            raise ValueError("video is required (a Douyin share link or share text)")
        body: dict[str, Any] = {"video": video}
        model = str(args.get("model") or "").strip()
        if model:
            body["model"] = model
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/freezone/douyin-copywrite",
                body=body,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_image_generate(args: dict[str, Any], **_: Any) -> str:
    """生图（freezone_gen）：文本 + 可选参考图 → 一张/多张图片。

    与画布生图同源：产物落到输出目录，完成后用
    GET /projects/{project}/freezone/jobs/freezone_gen/{job_id}/result 取图片 URL。
    """
    try:
        project = _project_from_args(args)
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("prompt is required")
        body: dict[str, Any] = {"prompt": prompt}
        for key in ("aspect_ratio", "image_size", "provider", "model", "quality", "gen_mode", "model_id"):
            if args.get(key) is not None:
                body[key] = args[key]
        if args.get("model") and not body.get("model_id"):
            body["model_id"] = args["model"]
        raw_refs = args.get("reference_urls")
        if raw_refs is not None:
            if not isinstance(raw_refs, list):
                raise ValueError("reference_urls must be a list of /static image URLs")
            refs = [str(x).strip() for x in raw_refs if str(x or "").strip()]
            if refs:
                body["reference_urls"] = refs
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/gen", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_image_edit(args: dict[str, Any], **_: Any) -> str:
    """图片编辑（freezone_edit）：底图 + 指令 → 重绘结果，可带额外参考图。"""
    try:
        project = _project_from_args(args)
        body: dict[str, Any] = {
            "base_url": _require_url_arg(args, "base_url"),
            "prompt": str(args.get("prompt") or "").strip(),
            "archive_to_library": _tools_page_archive_flag(args),
        }
        if not body["prompt"]:
            raise ValueError("prompt is required")
        for key in ("aspect_ratio", "image_size", "provider", "model", "quality", "gen_mode", "model_id", "source_tool"):
            if args.get(key) is not None:
                body[key] = args[key]
        raw_refs = args.get("extra_reference_urls")
        if raw_refs is not None:
            if not isinstance(raw_refs, list):
                raise ValueError("extra_reference_urls must be a list of /static image URLs")
            refs = [str(x).strip() for x in raw_refs if str(x or "").strip()]
            if refs:
                body["extra_reference_urls"] = refs
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/edit", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_image_upscale(args: dict[str, Any], **_: Any) -> str:
    """图片高清放大（freezone_edit 路径，upscale 端点）。"""
    try:
        project = _project_from_args(args)
        body: dict[str, Any] = {"source_url": _require_url_arg(args, "source_url")}
        for key in ("model", "quality", "scale_factor", "image_size"):
            if args.get(key) is not None:
                body[key] = args[key]
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/upscale", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_image_outpaint(args: dict[str, Any], **_: Any) -> str:
    """扩图（outpaint，freezone_edit 路径）：把图片外扩到目标画幅。"""
    try:
        project = _project_from_args(args)
        body: dict[str, Any] = {"source_url": _require_url_arg(args, "source_url")}
        for key in ("target_aspect_ratio", "num_images", "image_size", "model", "quality"):
            if args.get(key) is not None:
                body[key] = args[key]
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/outpaint", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_image_relight(args: dict[str, Any], **_: Any) -> str:
    """重打光（relight，freezone_edit 路径）：按光照参考或参数改光源。"""
    try:
        project = _project_from_args(args)
        body: dict[str, Any] = {"source_url": _require_url_arg(args, "source_url")}
        for key in (
            "lighting_reference_url", "scope", "smart_mode", "brightness", "color_hex",
            "color_temperature_kelvin", "key_light_direction", "rim_light", "prompt",
            "image_size", "model", "quality",
        ):
            if args.get(key) is not None:
                body[key] = args[key]
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/relight", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_image_multi_view(args: dict[str, Any], **_: Any) -> str:
    """多视角（multi-view，freezone_edit 路径）：preset 或自定义 yaw/pitch/shot_size。"""
    try:
        project = _project_from_args(args)
        body: dict[str, Any] = {"source_url": _require_url_arg(args, "source_url")}
        for key in ("preset", "yaw_degrees", "pitch_degrees", "shot_size", "prompt", "image_size", "model", "quality"):
            if args.get(key) is not None:
                body[key] = args[key]
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/multi-view", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_image_template_edit(args: dict[str, Any], **_: Any) -> str:
    """模板化图片处理（template-edit，freezone_edit 路径）：九宫格/三视图等固定模板。"""
    try:
        project = _project_from_args(args)
        body: dict[str, Any] = {
            "source_url": _require_url_arg(args, "source_url"),
            "mode": _require_url_arg(args, "mode"),
        }
        for key in ("prompt", "image_size", "model", "catalog_id", "quality"):
            if args.get(key) is not None:
                body[key] = args[key]
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/template-edit", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_image_redraw(args: dict[str, Any], **_: Any) -> str:
    """局部重绘（redraw）：有 mask_url 走 freezone_mask_edit，否则走 freezone_edit。"""
    try:
        project = _project_from_args(args)
        body: dict[str, Any] = {"source_url": _require_url_arg(args, "source_url")}
        for key in ("mask_url", "aspect_ratio", "num_images", "prompt", "image_size", "model", "quality"):
            if args.get(key) is not None:
                body[key] = args[key]
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/redraw", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_image_reverse_prompt(args: dict[str, Any], **_: Any) -> str:
    """图片反推提示词（freezone_image_reverse_prompt）：图 → 提示词文本。"""
    try:
        project = _project_from_args(args)
        body: dict[str, Any] = {"source_url": _require_url_arg(args, "source_url")}
        if args.get("instruction") is not None:
            body["instruction"] = args["instruction"]
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/image/reverse-prompt", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_video_generate(args: dict[str, Any], **_: Any) -> str:
    """文生视频（freezone_video_gen）：提示词 → 一段视频。

    与画布生视频同源；model 从 /freezone/video/models 选，产物完成后用
    GET /projects/{project}/freezone/jobs/freezone_video_gen/{job_id}/result 取视频 URL。
    """
    try:
        project = _project_from_args(args)
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("prompt is required")
        body: dict[str, Any] = {"prompt": prompt}
        for key in (
            "camera_template_id", "character_ids", "marks", "aspect_ratio", "resolution",
            "duration_seconds", "generate_audio", "human_review", "scene_optimize",
            "model", "gen_mode",
        ):
            if args.get(key) is not None:
                body[key] = args[key]
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/video/gen", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_video_i2v(args: dict[str, Any], **_: Any) -> str:
    """图生视频（freezone_video_gen via /video/i2v）：首帧参考图 → 视频。"""
    try:
        project = _project_from_args(args)
        gen_mode = str(args.get("gen_mode") or "imageToVideo").strip()
        if gen_mode not in {"imageToVideo", "imageReference"}:
            raise ValueError("gen_mode must be imageToVideo or imageReference")
        body: dict[str, Any] = {"gen_mode": gen_mode}
        raw_imgs = args.get("image_urls")
        if raw_imgs is not None:
            if not isinstance(raw_imgs, list):
                raise ValueError("image_urls must be a list of /static image URLs")
            body["image_urls"] = [str(x).strip() for x in raw_imgs if str(x or "").strip()]
        for key in (
            "prompt", "camera_template_id", "marks", "aspect_ratio", "resolution",
            "duration_seconds", "generate_audio", "human_review", "scene_optimize", "model",
        ):
            if args.get(key) is not None:
                body[key] = args[key]
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/video/i2v", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_video_omni_gen(args: dict[str, Any], **_: Any) -> str:
    """全能参考生视频（omni-gen allReference）：任意图/视频/音频参考组合 → 视频。"""
    try:
        project = _project_from_args(args)
        raw_refs = args.get("references")
        if not isinstance(raw_refs, list) or not raw_refs:
            raise ValueError("references is required (a list of {type,url,role?,label?} references)")
        references = [item for item in raw_refs if isinstance(item, dict) and str(item.get("url") or "").strip()]
        if not references:
            raise ValueError("references must each carry a non-empty url")
        body = _omni_gen_body(args, references)
        if args.get("theme") is not None:
            body["theme"] = args["theme"]
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/video/omni-gen", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_video_upscale(args: dict[str, Any], **_: Any) -> str:
    """视频高清放大（freezone_video_upscale）。"""
    try:
        project = _project_from_args(args)
        body: dict[str, Any] = {"source_url": _require_url_arg(args, "source_url")}
        for key in ("resolution", "frame_interpolation", "denoise_strength"):
            if args.get(key) is not None:
                body[key] = args[key]
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/video/upscale", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_video_edit(args: dict[str, Any], **_: Any) -> str:
    """视频编辑（freezone_video_gen via /video/video-edit）：源视频 + 参考图/音频 → 改写视频。"""
    try:
        project = _project_from_args(args)
        body: dict[str, Any] = {
            "video_url": _require_url_arg(args, "video_url"),
            "archive_to_library": _tools_page_archive_flag(args),
        }
        for key in ("prompt", "audio_setting", "resolution", "generate_audio", "human_review", "model", "source_tool"):
            if args.get(key) is not None:
                body[key] = args[key]
        if args.get("model") and not body.get("model_id"):
            body["model_id"] = args["model"]
        for list_key in ("image_urls", "audio_urls"):
            raw = args.get(list_key)
            if raw is not None:
                if not isinstance(raw, list):
                    raise ValueError(f"{list_key} must be a list of /static URLs")
                body[list_key] = [str(x).strip() for x in raw if str(x or "").strip()]
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/video/video-edit", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_video_extend(args: dict[str, Any], **_: Any) -> str:
    """视频续写（freezone_video_gen via /video/video-extend）：末帧向后延长。"""
    try:
        project = _project_from_args(args)
        body: dict[str, Any] = {
            "video_url": _require_url_arg(args, "video_url"),
            "prompt": str(args.get("prompt") or "").strip(),
        }
        if not body["prompt"]:
            raise ValueError("prompt is required")
        for key in ("camera_template_id", "resolution", "duration_seconds", "generate_audio", "human_review", "model"):
            if args.get(key) is not None:
                body[key] = args[key]
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/video/video-extend", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_audio_separate(args: dict[str, Any], **_: Any) -> str:
    """人声分离（freezone_audio_separate）：视频 → 人声音轨 + 静音视频。"""
    try:
        project = _project_from_args(args)
        body: dict[str, Any] = {"source_url": _require_url_arg(args, "source_url")}
        for key in ("target_episode", "target_beat"):
            if args.get(key) is not None:
                body[key] = args[key]
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/video/audio-separate", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_audio_music(args: dict[str, Any], **_: Any) -> str:
    """生成音乐（freezone_audio_eleven_music）：文本描述 → 背景音乐。"""
    try:
        project = _project_from_args(args)
        body: dict[str, Any] = {"input": _require_url_arg(args, "input")}
        for key in ("model", "response_format", "music_length_ms", "force_instrumental"):
            if args.get(key) is not None:
                body[key] = args[key]
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/audio/eleven-music", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_text_generate(args: dict[str, Any], **_: Any) -> str:
    """文本生成（freezone_text_generate）：提示词 → 文本产物。"""
    try:
        project = _project_from_args(args)
        body: dict[str, Any] = {"prompt": str(args.get("prompt") or "").strip()}
        if not body["prompt"]:
            raise ValueError("prompt is required")
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/text/generate", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_text_translate(args: dict[str, Any], **_: Any) -> str:
    """文本翻译（freezone_text_translate）。"""
    try:
        project = _project_from_args(args)
        body: dict[str, Any] = {"text": str(args.get("text") or "").strip()}
        if not body["text"]:
            raise ValueError("text is required")
        if args.get("node_type") is not None:
            body["node_type"] = args["node_type"]
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/text/translate", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_article_write(args: dict[str, Any], **_: Any) -> str:
    """公众号文章编写与排版（freezone_article_write）：主题/素材 → 风格化
    Markdown → 合规公众号 HTML（异步任务）。

    写作只走网关文本模型这一处出网（EG-18a）：按所选写作风格 + 素材产出结构化
    Markdown，再由本地排版层套用主题组件生成公众号合规 HTML（每个中文文本节点包
    ``<span leaf="">``），最后过 validate 强门禁（ERROR=0 才算成功）。排版与校验都是
    纯本地、不出网；发布是用户自带凭证的公开 API 直连（另走 aipdd_drama_article_publish）。
    产物为结果 JSON + 一份排版 HTML 预览，topic 与 material 至少填一项。
    """
    try:
        project = _project_from_args(args)
        topic = str(args.get("topic") or "").strip()
        material = str(args.get("material") or "").strip()
        if not topic and not material:
            raise ValueError("topic or material is required")
        if material and len(material) > 12_000:
            raise ValueError("material must be <= 12000 characters")
        body: dict[str, Any] = {
            "topic": topic,
            "material": material,
            "archive_to_library": _tools_page_archive_flag(args),
        }
        for key in (
            "style",
            "theme",
            "target_words",
            "audience",
            "tone",
            "intro_quote",
            "author",
            "model_text",
        ):
            if args.get(key) is not None:
                body[key] = args[key]
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/article/write", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_article_publish(args: dict[str, Any], **_: Any) -> str:
    """发布已排版的公众号文章（freezone_article_publish，同步）：草稿箱 / 可选直发。

    凭证优先级 config.json > ~/.wechat_publish.json > 环境变量；未配置时**不抛错**，
    返回 status=needs_config 与「去公众号后台取 AppID/Secret + 配 IP 白名单」的指引，
    排版产物照常可下载。48001（未认证）自动降级为「API 建草稿 + 后台手动发布」。
    """
    try:
        project = _project_from_args(args)
        article_job_id = str(args.get("article_job_id") or "").strip()
        html_url = str(args.get("html_url") or "").strip()
        if not article_job_id and not html_url:
            raise ValueError("article_job_id or html_url is required")
        body: dict[str, Any] = {
            "article_job_id": article_job_id,
            "html_url": html_url,
        }
        for key in ("title", "author", "digest", "cover_url"):
            if args.get(key) is not None:
                body[key] = args[key]
        if args.get("publish_now") is not None:
            body["publish_now"] = bool(args["publish_now"])
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/article/publish", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_story_script_generate(args: dict[str, Any], **_: Any) -> str:
    """故事脚本生成（freezone_story_script）：剧本/视频 → 分镜故事脚本。"""
    try:
        project = _project_from_args(args)
        body: dict[str, Any] = {}
        for key in (
            "source_text", "source_url", "video_url", "duration_sec", "character_refs",
            "max_frames", "scene_threshold", "prompt", "model",
        ):
            if args.get(key) is not None:
                body[key] = args[key]
        if not any(k in body for k in ("source_text", "source_url", "video_url")):
            raise ValueError("one of source_text / source_url / video_url is required")
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/text/story-script", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_video_script_reverse(args: dict[str, Any], **_: Any) -> str:
    """视频反推剧本（freezone_video_script_reverse）：上传视频 → 视觉模型反推纯文本剧本。

    产物是 txt，不写进 /freezone/jobs 白名单；完成后用 aipdd_drama_get_task
    （task_type='freezone_video_script_reverse', episode=0, scope=job_id）读 task.result。
    """
    try:
        project = _project_from_args(args)
        body: dict[str, Any] = {"video_url": _require_url_arg(args, "video_url")}
        for key in ("prompt", "model", "duration_sec", "max_frames", "scene_threshold"):
            if args.get(key) is not None:
                body[key] = args[key]
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/video-script-reverse", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_media_copywrite(args: dict[str, Any], **_: Any) -> str:
    """多媒介文案提取（freezone_media_copywrite）：链接/视频/音频 → ASR 转写文案。

    产物是文案 txt（素材库无文本载体，不归档）；完成后用 aipdd_drama_get_task
    （task_type='freezone_media_copywrite', episode=0, scope=job_id）读 task.result。
    """
    try:
        project = _project_from_args(args)
        source_type = str(args.get("source_type") or "").strip()
        if source_type not in {"link", "upload_video", "upload_audio"}:
            raise ValueError("source_type must be link / upload_video / upload_audio")
        body: dict[str, Any] = {
            "source_type": source_type,
            "url": _require_url_arg(args, "url"),
        }
        for key in ("platform", "model", "source_tool"):
            if args.get(key) is not None:
                body[key] = args[key]
        return tool_result(
            _request("POST", f"/api/v1/projects/{project}/freezone/media-copywrite", body=body)
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_list_assets(args: dict[str, Any], **_: Any) -> str:
    """列出项目资产库（GET /freezone/assets）。"""
    try:
        project = _project_from_args(args)
        return tool_result(
            _request("GET", f"/api/v1/projects/{project}/freezone/assets", query=args.get("query"))
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_get_character_library(args: dict[str, Any], **_: Any) -> str:
    """列出角色库（GET /freezone/video/character-library）。"""
    try:
        project = _project_from_args(args)
        return tool_result(
            _request("GET", f"/api/v1/projects/{project}/freezone/video/character-library")
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_get_audio_references(args: dict[str, Any], **_: Any) -> str:
    """列出音色/声线参考（GET /freezone/audio/references）。"""
    try:
        project = _project_from_args(args)
        return tool_result(
            _request("GET", f"/api/v1/projects/{project}/freezone/audio/references")
        )
    except Exception as exc:
        return tool_error(str(exc))


_MODEL_GATEWAY_KIND_PATHS = {
    "image": "/freezone/image/models",
    "video": "/freezone/video/models",
    "audio": "/freezone/audio/models",
    "text": "/model-gateway/text-models",
    "vision": "/model-gateway/vision-models",
}


def _handle_get_models(args: dict[str, Any], **_: Any) -> str:
    """读模型目录（按 kind 聚合 image/video/audio/text/vision）。"""
    try:
        project = _project_from_args(args)
        kind = str(args.get("kind") or "").strip().lower()
        if kind not in _MODEL_GATEWAY_KIND_PATHS:
            raise ValueError("kind must be one of image/video/audio/text/vision")
        suffix = _MODEL_GATEWAY_KIND_PATHS[kind]
        if suffix.startswith("/model-gateway"):
            # 网关目录是全局的，不嵌项目前缀。
            path = f"/api/v1{suffix}"
        else:
            path = f"/api/v1/projects/{project}{suffix}"
        return tool_result(_request("GET", path))
    except Exception as exc:
        return tool_error(str(exc))


# ---------------------------------------------------------------------------
# [aipdd fork delta] 自动化编排的 MCP 薄壳：run_pipeline / auto_storyboard 均委托
# 服务端权威计划器 POST /pipeline/plan（单一逻辑源），本模块只负责 HTTP 发射与轮询。
# 阶段推进全部走 JSON 路由，绝不提交视频；视频由 aipdd_drama_video_range 一次性并发提交。
# ---------------------------------------------------------------------------

_PIPELINE_ACTIVE_STATUSES = {"submitting", "queued", "running"}


def _resp_rows(resp: Any, *list_keys: str) -> list[Any]:
    """宽松解析常见列表型返回（data=[...] / {data: {key: [...]}} / {key: [...]})。"""
    if not isinstance(resp, dict):
        return []
    for key in ("data", *list_keys):
        value = resp.get(key)
        if isinstance(value, list):
            return value
    data = resp.get("data")
    if isinstance(data, dict):
        for key in list_keys:
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def _episode_beat_rows(project: str, episode: int) -> list[dict[str, Any]]:
    resp = _request("GET", f"/api/v1/projects/{project}/episodes/{episode}/beats")
    return [row for row in _resp_rows(resp, "beats", "items") if isinstance(row, dict)]


def _project_task_rows(project: str) -> list[dict[str, Any]]:
    resp = _request("GET", f"/api/v1/projects/{project}/tasks")
    return [row for row in _resp_rows(resp, "tasks", "items") if isinstance(row, dict)]


def _parse_beat_range(value: Any) -> list[int]:
    """解析分镜编号/区间："1-8"、"1,3,5-8"（兼容全角连接符与逗号）或数字列表。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        numbers: list[int] = []
        for item in value:
            numbers.extend(_parse_beat_range(item))
        return sorted({n for n in numbers if n > 0})
    if isinstance(value, bool):
        return []
    if isinstance(value, int):
        return sorted({value}) if value > 0 else []
    text = str(value).strip()
    if not text:
        return []
    for ch in ("－", "–", "—", "~", "～"):
        text = text.replace(ch, "-")
    for ch in ("，", "、", ";", "；", " "):
        text = text.replace(ch, ",")
    numbers = []
    for part in filter(None, text.split(",")):
        if "-" in part:
            start_raw, _, end_raw = part.partition("-")
            try:
                start, end = int(start_raw), int(end_raw)
            except ValueError as exc:
                raise ValueError(f"invalid beat range: {part!r}") from exc
            if start > end:
                start, end = end, start
            numbers.extend(range(start, end + 1))
        else:
            try:
                numbers.append(int(part))
            except ValueError as exc:
                raise ValueError(f"invalid beat number: {part!r}") from exc
    return sorted({n for n in numbers if n > 0})


# 映射到服务端 /pipeline/plan 请求体的生成参数（故事版不由 MCP 请求，故不传
# include_story_version；target_beats 缺省时服务端按 seconds_per_episode 推导）。
_PLAN_ARG_KEYS = (
    "episode",
    "story_text",
    "story_style",
    "aspect_ratio",
    "model",
    "style",
    "episode_count",
    "target_beats",
    "seconds_per_episode",
    "total_seconds",
)


def _plan_body(args: dict[str, Any], *, mode: str, execute: bool) -> dict[str, Any]:
    """把 MCP 参数映射为 /pipeline/plan 请求体（mode 仅传语义，服务端不据此循环）。"""
    body: dict[str, Any] = {"mode": mode, "execute": execute}
    for key in _PLAN_ARG_KEYS:
        value = args.get(key)
        if value is not None and str(value).strip() != "":
            body[key] = value
    return body


def _plan_call(project: str, body: dict[str, Any]) -> dict[str, Any]:
    """委托服务端计划器（真实 HTTP），返回其 PlanStep（{ok,data:{...}} → data）。"""
    resp = _request("POST", f"/api/v1/projects/{project}/pipeline/plan", body=body)
    if not _resp_ok(resp):
        raise ValueError(_resp_error(resp) or "pipeline plan request failed")
    data = resp.get("data") if isinstance(resp, dict) else None
    return data if isinstance(data, dict) else (resp if isinstance(resp, dict) else {})


def _handle_auto_storyboard(args: dict[str, Any], **_: Any) -> str:
    """自动化分镜单段推进：委托服务端权威计划器，触发下一个未完成阶段（不生成视频）。

    每次调用向 POST /pipeline/plan 请求「下一步」并 execute=true 就地发射该段动作，
    返回 {stage, status, actions, fired, message, next_hint}；反复调用直到 stage="done"。
    长阶段是后台任务，稍后再调而非紧密轮询。绝不提交视频（视频用 aipdd_drama_video_range）。
    """
    try:
        project = _project_from_args(args)
        plan = _plan_call(project, _plan_body(args, mode="manual", execute=True))
        return tool_result(plan)
    except Exception as exc:
        return tool_error(str(exc))


def _handle_video_range(args: dict[str, Any], **_: Any) -> str:
    """按分镜编号/区间一次性并发提交镜头视频（aipdd_drama_video_range）。

    解析 "1-8"/"1,3,5-8"/列表形式的分镜号，先 GET /episodes/{ep}/beats 拿帧状态：
    首帧存在的 beat 立即逐个 POST /beats/{beat}/video，缺帧/不存在的 beat 记入
    skipped（不排队）。返回 submitted/skipped/errors 三张清单；agent 用
    aipdd_drama_get_task(task_type="single_video", episode=N, beat_num=B) 轮询。
    """
    try:
        project = _project_from_args(args)
        raw_episode = args.get("episode")
        episode = 1 if raw_episode is None else int(raw_episode)
        if episode <= 0:
            raise ValueError("episode must be a positive integer")
        raw_beats = args.get("beats")
        if raw_beats is None:
            raw_beats = args.get("beat_range", args.get("beat"))
        numbers = _parse_beat_range(raw_beats)
        if not numbers:
            raise ValueError("beats is required, e.g. '1-8', '1,3,5-8' or [1, 2, 3]")
        body: dict[str, Any] = {}
        backend = str(args.get("model") or args.get("video_backend") or "").strip()
        if backend:
            body["video_backend"] = backend
        for key in ("resolution", "duration", "mode", "ratio"):
            if args.get(key) is not None:
                body[key] = args[key]
        rows: dict[int, dict[str, Any]] = {}
        for row in _episode_beat_rows(project, episode):
            try:
                rows[int(row.get("beat_number"))] = row
            except (TypeError, ValueError):
                continue
        submitted: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for beat in numbers:
            row = rows.get(beat)
            if row is None:
                skipped.append({"beat_number": beat, "reason": "该分镜编号在本集不存在"})
                continue
            if not str(row.get("frame_url") or ""):
                skipped.append(
                    {
                        "beat_number": beat,
                        "reason": "缺镜头首帧，先用 aipdd_drama_auto_storyboard 或首帧生成补齐",
                    }
                )
                continue
            resp = _request(
                "POST", f"/api/v1/projects/{project}/episodes/{episode}/beats/{beat}/video", body=body
            )
            if isinstance(resp, dict) and resp.get("ok"):
                submitted.append(
                    {"beat_number": beat, "task_id": resp.get("task_id"), "task_key": resp.get("task_key")}
                )
            else:
                errors.append(
                    {
                        "beat_number": beat,
                        "error": resp.get("error") if isinstance(resp, dict) else str(resp),
                        "detail": resp,
                    }
                )
        return tool_result(
            {
                "ok": True,
                "episode": episode,
                "requested": numbers,
                "request_body": body,
                "submitted_count": len(submitted),
                "submitted": submitted,
                "skipped": skipped,
                "errors": errors,
                "next_action": (
                    f"逐个轮询 aipdd_drama_get_task(task_type='single_video', episode={episode}, "
                    "beat_num=N)；本调用不等待视频完成"
                ),
            }
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_regen_sketches(args: dict[str, Any], **_: Any) -> str:
    """[aipdd fork delta] 按镜头批量重生成草图（aipdd_drama_regen_sketches, sketch_regen task）。

    Wraps POST /projects/{project}/episodes/{episode}/sketches/regenerate with
    ``{"beat_indices": [...], "mode_key": "1x1_2-3_sketch"}``. Accepts beat_indices as a
    number list or a "1-8"/"1,3,5-8" range string. Omit to regenerate ALL beats of the
    episode (resolved automatically). Requires the storyboard script to exist first.
    Poll aipdd_drama_get_task(task_type="sketch_regen", episode=N).
    """
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        beats = args.get("beat_indices") or args.get("beats") or args.get("beat_range")
        numbers = _parse_beat_range(beats)
        if not numbers:
            numbers = _resolve_episode_beats(project, episode)
            if not numbers:
                raise ValueError(
                    "could not resolve beats for this episode; generate the storyboard "
                    "script first or pass beat_indices explicitly"
                )
        body: dict[str, Any] = {
            "beat_indices": numbers,
            "mode_key": str(args.get("mode_key") or "1x1_2-3_sketch"),
        }
        for key in ("style", "model", "image_generation_selection"):
            if args.get(key) is not None:
                body[key] = args[key]
        return tool_result(
            _request(
                "POST",
                f"/api/v1/projects/{project}/episodes/{episode}/sketches/regenerate",
                body=body,
            )
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_generate_video_prompts(args: dict[str, Any], **_: Any) -> str:
    """[aipdd fork delta] 按分镜编号/区间批量生成镜头视频提示词（aipdd_drama_generate_video_prompts）。

    解析 "1-8"/"1,3,5-8"/列表形式的分镜号，逐个 POST /beats/{beat}/video-prompt/generate
    （body {}）。提示词生成依赖镜头已有分镜脚本描述等前置内容：返回 409（前置缺失）或 404
    的 beat 记入 skipped，其它失败记入 errors，成功入队/落库的记入 submitted。EE 走
    beat_video_prompt 任务，agent 用 aipdd_drama_get_task(task_type="beat_video_prompt",
    episode=N, beat_num=B) 轮询；CE 同步返回。省略 beats 时对整集镜头逐个生成。
    """
    try:
        project = _project_from_args(args)
        raw_episode = args.get("episode")
        episode = 1 if raw_episode is None else int(raw_episode)
        if episode <= 0:
            raise ValueError("episode must be a positive integer")
        raw_beats = args.get("beats")
        if raw_beats is None:
            raw_beats = args.get("beat_range", args.get("beat"))
        numbers = _parse_beat_range(raw_beats)
        if not numbers:
            numbers = _resolve_episode_beats(project, episode)
        if not numbers:
            raise ValueError(
                "beats is required (e.g. '1-8', '1,3,5-8' or [1, 2, 3]); could not "
                "resolve beats for this episode"
            )
        submitted: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for beat in numbers:
            resp = _request(
                "POST",
                f"/api/v1/projects/{project}/episodes/{episode}/beats/{beat}/video-prompt/generate",
                body={},
            )
            if isinstance(resp, dict) and resp.get("ok"):
                submitted.append(
                    {
                        "beat_number": beat,
                        "task_type": resp.get("task_type"),
                        "task_id": resp.get("task_id"),
                        "task_key": resp.get("task_key"),
                    }
                )
            elif isinstance(resp, dict) and resp.get("status_code") in (404, 409):
                skipped.append(
                    {
                        "beat_number": beat,
                        "status_code": resp.get("status_code"),
                        "reason": resp.get("error") or "缺少生成视频提示词的前置内容",
                    }
                )
            else:
                errors.append(
                    {
                        "beat_number": beat,
                        "error": resp.get("error") if isinstance(resp, dict) else str(resp),
                        "detail": resp,
                    }
                )
        return tool_result(
            {
                "ok": True,
                "episode": episode,
                "requested": numbers,
                "submitted_count": len(submitted),
                "submitted": submitted,
                "skipped": skipped,
                "errors": errors,
                "next_action": (
                    f"逐个轮询 aipdd_drama_get_task(task_type='beat_video_prompt', "
                    f"episode={episode}, beat_num=N)；本调用不等待提示词生成完成"
                ),
            }
        )
    except Exception as exc:
        return tool_error(str(exc))


# ---------------------------------------------------------------------------
# [aipdd fork delta] MCP 编排层：run_pipeline / auto_storyboard 均委托服务端权威计划器
# POST /pipeline/plan（单一逻辑源），另供运行状态聚合 / 取消 / 智能默认配置。
# ---------------------------------------------------------------------------


def _resp_ok(resp: Any) -> bool:
    """宽松判定一次 _request 是否成功（_decode_response 对 2xx 不一定带 ok）。"""
    if isinstance(resp, dict):
        if resp.get("ok") is False:
            return False
        code = resp.get("status_code")
        if code is None:
            return True
        try:
            return 200 <= int(code) < 300
        except (TypeError, ValueError):
            return True
    return bool(resp)


def _resp_error(resp: Any) -> Any:
    return resp.get("error") if isinstance(resp, dict) else str(resp)


def _model_ids(resp: Any) -> list[str]:
    """从模型目录返回中提取可用的模型 id 列表（字符串或 {id|model|name} 对象）。"""
    ids: list[str] = []
    for row in _resp_rows(resp, "models", "items", "data"):
        if isinstance(row, str):
            ids.append(row)
        elif isinstance(row, dict):
            mid = row.get("id") or row.get("model") or row.get("name")
            if mid:
                ids.append(str(mid))
    return ids


def _suggest_config_payload(project: str, args: dict[str, Any]) -> dict[str, Any]:
    """读 image/video 模型目录 + 项目画幅，给出推荐生成配置（只读、可被 run_pipeline 复用）。"""
    image = _request("GET", f"/api/v1/projects/{project}/freezone/image/models")
    video = _request("GET", f"/api/v1/projects/{project}/freezone/video/models")
    image_ids = _model_ids(image)
    video_ids = _model_ids(video)
    aspect = str(args.get("aspect_ratio") or args.get("ratio") or "2:3")
    if aspect not in ("2:3", "16:9"):
        aspect = "2:3"
    default_resolution = "720x1280" if aspect == "2:3" else "1280x720"
    recommended = {
        "image_model": str(args.get("image_model") or (image_ids[0] if image_ids else "")),
        "video_model": str(args.get("video_model") or (video_ids[0] if video_ids else "")),
        "ratio": aspect,
        "resolution": str(args.get("resolution") or default_resolution),
        "duration": int(args.get("duration") or 5),
        "style": str(args.get("style") or ""),
        "voice": str(args.get("voice") or ""),
    }
    return {
        "ok": True,
        "recommended": recommended,
        "alternatives": {"image_models": image_ids[1:6], "video_models": video_ids[1:6]},
        "rationale": (
            "默认竖屏 2:3 短剧画幅；image/video 取各自渠道首个可用模型；"
            "未指定风格时留空，由后端按故事自动推荐。"
        ),
        "counts": {"image_models": len(image_ids), "video_models": len(video_ids)},
    }


def _handle_suggest_config(args: dict[str, Any], **_: Any) -> str:
    """智能默认配置推荐（只读）：给 Agent/用户先看推荐再生成。"""
    try:
        project = _project_from_args(args)
        return tool_result(_suggest_config_payload(project, args))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_get_run_status(args: dict[str, Any], **_: Any) -> str:
    """聚合整条流水线进度：pipeline/status + 任务行 + 指定集的镜头计数。"""
    try:
        project = _project_from_args(args)
        raw_episode = args.get("episode")
        episode = int(raw_episode) if raw_episode not in (None, "") else None
        status = _request(
            "GET", f"/api/v1/projects/{project}/pipeline/status", query={"episode": episode}
        )
        rows = _project_task_rows(project)
        running: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        completed: list[dict[str, Any]] = []
        for row in rows:
            stat = str(row.get("status") or "")
            summary = {
                "task_type": row.get("task_type"),
                "episode": row.get("episode"),
                "beat_num": row.get("beat_num"),
                "status": stat,
                "progress": row.get("progress"),
                "current_task": row.get("current_task"),
            }
            if stat in _PIPELINE_ACTIVE_STATUSES:
                running.append(summary)
            elif stat == "failed":
                summary["error"] = row.get("error")
                failed.append(summary)
            elif stat == "completed":
                completed.append(summary)
        counts = None
        if episode:
            beat_rows = _episode_beat_rows(project, episode)
            counts = {
                "total_beats": len(beat_rows),
                "beats_with_sketch": sum(1 for r in beat_rows if str(r.get("sketch_url") or "")),
                "beats_with_frame": sum(1 for r in beat_rows if str(r.get("frame_url") or "")),
                "beats_with_video": sum(
                    1 for r in beat_rows if str(r.get("video_url") or r.get("video") or "")
                ),
            }
        done = not running and not failed
        if failed:
            next_action = "存在失败任务，先看 failed 列表再决定重试或取消"
        elif running:
            next_action = "有任务在运行，稍后再次调用 aipdd_drama_get_run_status 轮询"
        else:
            next_action = "流水线空闲，可用 aipdd_drama_run_pipeline 继续推进分镜，或用 aipdd_drama_video_range 生成镜头视频"
        return tool_result(
            {
                "ok": True,
                "project": project,
                "episode": episode,
                "pipeline_status": status.get("data") if isinstance(status, dict) else status,
                "running": running,
                "failed": failed,
                "completed": completed[-20:],
                "counts": counts,
                "done": done,
                "next_recommended_action": next_action,
            }
        )
    except Exception as exc:
        return tool_error(str(exc))


def _handle_cancel_run(args: dict[str, Any], **_: Any) -> str:
    """取消本集在跑任务：包 DELETE /projects/{p}/tasks/{task_type}/{episode}。"""
    try:
        project = _project_from_args(args)
        episode = _require_episode(args)
        raw_types = args.get("task_types")
        if isinstance(raw_types, str):
            task_types = [t.strip() for t in raw_types.replace(",", " ").split() if t.strip()]
        else:
            task_types = [str(t).strip() for t in (raw_types or []) if str(t).strip()]
        beats = _parse_beat_range(args.get("beats"))
        scope = args.get("scope")
        auto_derived = not task_types
        if auto_derived:
            for row in _project_task_rows(project):
                if (
                    str(row.get("status") or "") in _PIPELINE_ACTIVE_STATUSES
                    and int(row.get("episode") or 0) == episode
                ):
                    tt = str(row.get("task_type") or "")
                    if tt:
                        task_types.append(tt)
        cancelled: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for tt in task_types:
            if tt == "single_video":
                target_beats = beats or _resolve_episode_beats(project, episode)
                for beat in target_beats:
                    resp = _request(
                        "DELETE",
                        f"/api/v1/projects/{project}/tasks/{tt}/{episode}",
                        query={"beat_num": beat, "force": True},
                    )
                    row = {"task_type": tt, "beat_num": beat}
                    if _resp_ok(resp):
                        cancelled.append(row)
                    else:
                        row["error"] = _resp_error(resp)
                        errors.append(row)
            else:
                query: dict[str, Any] = {"force": True}
                if scope:
                    query["scope"] = scope
                resp = _request(
                    "DELETE",
                    f"/api/v1/projects/{project}/tasks/{tt}/{episode}",
                    query=query,
                )
                row = {"task_type": tt}
                if _resp_ok(resp):
                    cancelled.append(row)
                else:
                    row["error"] = _resp_error(resp)
                    errors.append(row)
        return tool_result(
            {
                "ok": True,
                "episode": episode,
                "auto_derived_task_types": auto_derived,
                "cancelled": cancelled,
                "errors": errors,
                "next_action": "已取消的任务不再计入运行中；可用 aipdd_drama_get_run_status 复核",
            }
        )
    except Exception as exc:
        return tool_error(str(exc))


def _wait_until_idle(project: str, deadline: float, interval: float = 2.0) -> bool:
    """轮询项目任务直到无活跃任务返回 True；超过 deadline 返回 False（不阻塞死）。"""
    while True:
        active = any(
            str(row.get("status") or "") in _PIPELINE_ACTIVE_STATUSES
            for row in _project_task_rows(project)
        )
        if not active:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(interval, remaining))


def _handle_run_pipeline(args: dict[str, Any], **_: Any) -> str:
    """编排层：委托服务端权威计划器，把流水线推进包成手动/检查点/全自动三档模式。

    单一逻辑源在 POST /pipeline/plan：mode=checkpoint 且未 approve 时 execute=false，只返回
    下一步动作清单（零副作用）供确认；manual / checkpoint+approve 用 execute=true 单段发射；
    auto 在 budget_s 内循环「发射→轮询空闲→再发射」直到分镜完成。全部只推进到镜头首帧，
    绝不提交视频（视频用 aipdd_drama_video_range，下游状态用 aipdd_drama_get_run_status 轮询）。
    """
    try:
        project = _project_from_args(args)
        mode = str(args.get("mode") or "checkpoint").strip().lower()
        if mode not in ("manual", "checkpoint", "auto"):
            raise ValueError("mode must be 'manual', 'checkpoint' or 'auto'")
        if bool(args.get("use_suggested")):
            recommended = _suggest_config_payload(project, args).get("recommended", {})
            for src_key, dst_key in (
                ("image_model", "model"),
                ("ratio", "aspect_ratio"),
                ("style", "style"),
            ):
                if recommended.get(src_key) and not args.get(dst_key):
                    args[dst_key] = recommended[src_key]

        if mode == "checkpoint" and not bool(args.get("approve")):
            plan = _plan_call(project, _plan_body(args, mode=mode, execute=False))
            return tool_result(
                {
                    "ok": True,
                    "mode": mode,
                    "requires_approval": True,
                    "next_stage": plan.get("stage"),
                    "planned_requests": plan.get("actions", []),
                    "step": plan,
                    "next_action": "确认无误后带 approve=true 再次调用 aipdd_drama_run_pipeline 触发该阶段",
                }
            )

        advanced: list[Any] = []
        deadline = time.monotonic() + float(args.get("budget_s") or 45)
        guard = 0
        while True:
            guard += 1
            plan = _plan_call(project, _plan_body(args, mode=mode, execute=True))
            advanced.append(plan.get("stage"))
            if plan.get("status") == "failed":
                return tool_result(
                    {
                        "ok": False,
                        "mode": mode,
                        "stage": plan.get("stage"),
                        "error": plan.get("error"),
                        "next_action": plan.get("next_hint"),
                        "advanced": advanced,
                    }
                )
            if plan.get("stage") == "done":
                break
            if mode == "manual" or plan.get("status") == "requires_input":
                return tool_result({"ok": True, "mode": mode, "step": plan, "advanced": advanced})
            # auto：刚触发的后台阶段轮询到空闲再继续推进；超预算则回报 running。
            if not _wait_until_idle(project, deadline):
                return tool_result(
                    {
                        "ok": True,
                        "mode": mode,
                        "running": True,
                        "current_stage": plan.get("stage"),
                        "advanced": advanced,
                        "next_action": "预算内仍在跑，稍后再次调用 aipdd_drama_run_pipeline(mode='auto') 继续",
                    }
                )
            if guard > 50:
                return tool_result(
                    {
                        "ok": True,
                        "mode": mode,
                        "running": True,
                        "paused_after_steps": guard,
                        "advanced": advanced,
                        "next_action": "推进步数达到上限，稍后再次调用继续",
                    }
                )

        return tool_result(
            {
                "ok": True,
                "mode": mode,
                "storyboard_done": True,
                "advanced": advanced,
                "next_action": "分镜草图与镜头首帧已就绪；本流程不生成视频，按需另行用 aipdd_drama_video_range 生成镜头视频",
            }
        )
    except Exception as exc:
        return tool_error(str(exc))


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required or [],
        },
    }


# [aipdd fork delta] VectCut 工具共享参数。
_VECTCUT_DRAFT_PROP: dict[str, Any] = {
    "type": "string",
    "description": "Draft id returned by vectcut_create_draft.",
}
_VECTCUT_PROJECT_DIR_PROP: dict[str, Any] = {
    "type": "string",
    "description": (
        "Optional Dramaclaw project folder name (e.g. 'test') to disambiguate "
        "/static media URLs; auto-detected when omitted."
    ),
}
_VECTCUT_CANVAS_PROPS: dict[str, Any] = {
    "width": {"type": "integer", "description": "Canvas width (default 1080)."},
    "height": {"type": "integer", "description": "Canvas height (default 1920)."},
}
_VECTCUT_PLACEMENT_PROPS: dict[str, Any] = {
    "transform_x": {"type": "number", "description": "Horizontal offset (0 = center)."},
    "transform_y": {"type": "number", "description": "Vertical offset (0 = center)."},
    "scale_x": {"type": "number", "description": "Horizontal scale."},
    "scale_y": {"type": "number", "description": "Vertical scale."},
}
_VECTCUT_TIME_PROPS: dict[str, Any] = {
    "start": {"type": "number", "description": "Start time on the timeline (seconds)."},
    "end": {"type": "number", "description": "End time on the timeline (seconds)."},
}

# [aipdd fork delta] 工具页 MCP 工具组共享参数。
_TOOLS_PAGE_PROJECT_PROP: dict[str, Any] = {
    "type": "string",
    "description": "Project id. Defaults to DRAMACLAW_PROJECT_ID.",
}
_TOOLS_PAGE_ARCHIVE_PROP: dict[str, Any] = {
    "type": "boolean",
    "description": (
        "Register the produced media into the asset library '待分类资产' (default true). "
        "Pass false to skip archiving."
    ),
}

# [aipdd fork delta] 画布生图/生视频 MCP 工具组共享参数（与工具页、画布节点同源端点）。
_IMAGE_SOURCE_URL_PROP: dict[str, Any] = {
    "type": "string",
    "description": "Source image /static URL of this project (upload the file first if needed).",
}
_IMAGE_MODEL_PROPS: dict[str, Any] = {
    "model": {"type": "string", "description": "Image model id from /freezone/image/models (also fills model_id)."},
    "model_id": {"type": "string", "description": "Catalog entry id from /freezone/image/models."},
    "quality": {"type": "string", "enum": ["low", "medium", "high", "auto"], "description": "Quality tier (default medium)."},
    "image_size": {"type": "string", "description": "Output size tier (default 2K)."},
}
_VIDEO_MODEL_PROPS: dict[str, Any] = {
    "model": {"type": "string", "description": "Video model id from /freezone/video/models."},
    "resolution": {"type": "string", "description": "Output resolution tier (default 720p)."},
    "duration_seconds": {"type": "integer", "description": "Output duration in seconds (default 5)."},
    "generate_audio": {"type": "boolean", "description": "Let the model generate native audio (dialogue/SFX)."},
    "human_review": {"type": "boolean", "description": "Enable the HuiMeng real-person review flow for face content."},
}


def _handle_commit_white_model_video(args: dict[str, Any], **_: Any) -> str:
    """[aipdd fork delta B+C] 把 Blender 白模（clay）视频落到画布「白模视频」节点。

    from_blender=true 让后端直连本机 Blender 插件渲染导出；否则用 video_url（可访问
    外链或本项目直链）。两者都先入库成本项目静态直链，再写回 node_id 指定的 whiteModelVideoNode
    （canvas 需已存在该节点）；commit_slot=true 再在节点上打资产槽指针。均经既有 freezone 路由。
    """
    try:
        project = _project_from_args(args)
        canvas_id = str(args.get("canvas_id") or "").strip()
        node_id = str(args.get("node_id") or "").strip()
        if not canvas_id or not node_id:
            raise ValueError("canvas_id and node_id are required")
        commit_slot = bool(args.get("commit_slot"))
        if bool(args.get("from_blender")):
            path = f"/api/v1/projects/{project}/freezone/blender/pull-white-model-video"
            body: dict[str, Any] = {
                "canvas_id": canvas_id,
                "node_id": node_id,
                "commit_slot": commit_slot,
            }
        else:
            video_url = str(args.get("video_url") or "").strip()
            if not video_url:
                raise ValueError("either from_blender=true or video_url is required")
            path = f"/api/v1/projects/{project}/freezone/blender/ingest-white-model-video"
            body = {
                "canvas_id": canvas_id,
                "node_id": node_id,
                "url": video_url,
                "commit_slot": commit_slot,
            }
        return tool_result(_request("POST", path, body=body))
    except Exception as exc:
        return tool_error(str(exc))


def _handle_env_setup(args: dict[str, Any], **_: Any) -> str:
    """[aipdd fork delta req-automcp] 自动化安装环境：探测→待办计划→逐项安装。

    action='check'（默认）经 GET /admin/env-setup/plan 回环依赖/3D 世界/模型渠道的统一
    待办清单（ready/installable/missing）；action='install' 经 POST /admin/env-setup/install
    安装指定 item（当前为 'world'）。容器内装不了的系统级依赖只回指引，不谎称已装。
    """
    try:
        action = str(args.get("action") or "check").strip().lower()
        if action in ("check", "plan", "status"):
            return tool_result(_request("GET", "/api/v1/admin/env-setup/plan"))
        if action == "install":
            item = str(args.get("item") or "").strip()
            if not item:
                raise ValueError("item is required when action='install' (e.g. 'world')")
            return tool_result(_request("POST", "/api/v1/admin/env-setup/install", body={"item": item}))
        raise ValueError("action must be one of: check, install")
    except Exception as exc:
        return tool_error(str(exc))


TOOLS = (
    (
        "aipdd_drama_get",
        _schema("aipdd_drama_get", "Call an AIPDD GET API path without using curl.", _PATH_PROPS, ["path"]),
        _handle_get,
    ),
    (
        "aipdd_drama_post",
        _schema("aipdd_drama_post", "Call an AIPDD POST API path without using curl.", {**_PATH_PROPS, "body": {"type": "object"}}, ["path"]),
        _handle_post,
    ),
    (
        "aipdd_drama_patch",
        _schema("aipdd_drama_patch", "Call an AIPDD PATCH API path without using curl.", {**_PATH_PROPS, "body": {"type": "object"}}, ["path"]),
        _handle_patch,
    ),
    (
        "aipdd_drama_delete",
        _schema("aipdd_drama_delete", "Call an AIPDD DELETE API path without using curl.", {**_PATH_PROPS, "body": {"type": "object"}}, ["path"]),
        _handle_delete,
    ),
    (
        "aipdd_drama_env_setup",
        _schema(
            "aipdd_drama_env_setup",
            (
                "Detect the deployment environment and auto-install what is missing. "
                "action='check' (default) returns a to-do plan of runtime dependencies, the "
                "3D world runtime, and the model channel (status: ready/installable/missing). "
                "action='install' installs one plan item by id (currently 'world', installed "
                "into a persistent venv); items that cannot be installed inside the running "
                "container return guidance instead of a fake success."
            ),
            {
                "action": {
                    "type": "string",
                    "enum": ["check", "install"],
                    "description": "'check' builds the plan (default); 'install' runs a per-item install.",
                },
                "item": {
                    "type": "string",
                    "description": "Plan item id to install (e.g. 'world'). Required when action='install'.",
                },
            },
        ),
        _handle_env_setup,
    ),
    (
        "aipdd_drama_pipeline_status",
        _schema(
            "aipdd_drama_pipeline_status",
            "Get the current AIPDD project pipeline status.",
            {
                "project_id": {"type": "string", "description": "Project id. Defaults to DRAMACLAW_PROJECT_ID."},
                "episode": {"type": "integer", "description": "Optional episode number."},
            },
        ),
        _handle_pipeline_status,
    ),
    (
        "aipdd_drama_list_tasks",
        _schema(
            "aipdd_drama_list_tasks",
            "List AIPDD tasks for the current or specified project.",
            {
                "project_id": {"type": "string"},
                "episode": {"type": "integer"},
                "task_type": {"type": "string"},
                "status": {"type": "string"},
            },
        ),
        _handle_list_tasks,
    ),
    (
        "aipdd_drama_get_task",
        _schema(
            "aipdd_drama_get_task",
            "Get one AIPDD task status by task type, episode, and optional beat/scope.",
            {
                "project_id": {"type": "string"},
                "task_type": {"type": "string"},
                "episode": {"type": "integer"},
                "beat": {"type": "integer"},
                "beat_num": {"type": "integer"},
                "scope": {"type": "string"},
            },
            ["task_type", "episode"],
        ),
        _handle_get_task,
    ),
    (
        "aipdd_drama_get_episode_script",
        _schema(
            "aipdd_drama_get_episode_script",
            "Get one episode script for the current or specified project.",
            {
                "project_id": {"type": "string"},
                "episode": {"type": "integer"},
            },
            ["episode"],
        ),
        _handle_get_episode_script,
    ),
    (
        "aipdd_drama_list_ingest_uploads",
        _schema(
            "aipdd_drama_list_ingest_uploads",
            "List files already uploaded to the current project's ingest script directory. Use this when "
            "the user asks which files are currently uploaded, or before starting video/short-drama "
            "ingest from a previously uploaded script.",
            {
                "project_id": {"type": "string", "description": "Project id. Defaults to DRAMACLAW_PROJECT_ID."},
            },
        ),
        _handle_list_ingest_uploads,
    ),
    (
        "aipdd_drama_build_characters",
        _schema(
            "aipdd_drama_build_characters",
            "Extract characters from the project's knowledge graph (async build_characters "
            "task, episode 0). Requires ingest to be complete first. Use THIS instead of "
            "guessing a path. Poll with aipdd_drama_get_task(task_type='build_characters', "
            "episode=0); read results with aipdd_drama_get('/projects/{project}/characters').",
            {
                "project_id": {
                    "type": "string",
                    "description": "Project id. Defaults to DRAMACLAW_PROJECT_ID.",
                },
            },
        ),
        _handle_build_characters,
    ),
    (
        "aipdd_drama_plan_episodes",
        _schema(
            "aipdd_drama_plan_episodes",
            "Plan/generate episodes (分集规划, async build_episodes task, episode 0). Requires "
            "ingest + character extraction done first. Use THIS instead of guessing a path — the "
            "real endpoint is POST /projects/{project}/episodes/plan (NOT /episodes, /tasks/..., "
            "/build_episodes or /start_pipeline). Poll with aipdd_drama_get_task("
            "task_type='build_episodes', episode=0); read with aipdd_drama_get('/projects/{project}/episodes').",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "target_episodes": {"type": "integer", "description": "How many episodes to plan (default 10)."},
                "planning_mode": {"type": "string", "description": "Planning mode (default 'chapters')."},
            },
        ),
        _handle_plan_episodes,
    ),
    (
        "aipdd_drama_generate_script",
        _schema(
            "aipdd_drama_generate_script",
            "Generate the screenplay for one episode (脚本生成, script_writer task). Use THIS "
            "instead of guessing — the real endpoint is POST /projects/{project}/episodes/{episode}/"
            "script/generate. Requires the episode's character identities planned first (else returns "
            "code 'identity_plan_required'). Poll with aipdd_drama_get_task(task_type='script_writer', "
            "episode=N); read with aipdd_drama_get_episode_script(episode=N).",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "episode": {"type": "integer", "description": "Episode number (1-based, required)."},
            },
            ["episode"],
        ),
        _handle_generate_script,
    ),
    (
        "aipdd_drama_get_episode_story",
        _schema(
            "aipdd_drama_get_episode_story",
            "Read one episode's story and director style (故事). Real endpoint GET "
            "/projects/{project}/episodes/{episode}/story. The story is the creative input "
            "that aipdd_drama_story_to_script adapts into a line-by-line script; story_style is "
            "the director-style key ('' = auto; e.g. beat_punch/spectacle/suspense/emotional/"
            "prestige/realism/comedy/info_pacing/auteur).",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "episode": {"type": "integer", "description": "Episode number (1-based, required)."},
            },
            ["episode"],
        ),
        _handle_get_episode_story,
    ),
    (
        "aipdd_drama_save_episode_story",
        _schema(
            "aipdd_drama_save_episode_story",
            "Save one episode's story and optionally its director style (故事). Real endpoint PUT "
            "/projects/{project}/episodes/{episode}/story. Omitting story_style keeps the "
            "stored one; pass '' to reset the style to auto, or a style key "
            "(beat_punch/spectacle/suspense/emotional/prestige/realism/comedy/info_pacing/"
            "auteur).",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "episode": {"type": "integer", "description": "Episode number (1-based, required)."},
                "story": {"type": "string", "description": "Full story text for the episode (required)."},
                "story_style": {"type": "string", "description": "Director style key ('' = auto; omit to keep current)."},
            },
            ["episode", "story"],
        ),
        _handle_save_episode_story,
    ),
    (
        "aipdd_drama_optimize_episode_story",
        _schema(
            "aipdd_drama_optimize_episode_story",
            "Diagnose and polish one episode's story with the director/screenwriter prompts "
            "(故事优化). Real endpoint POST /projects/{project}/episodes/{episode}/story/"
            "optimize. The result is NOT saved — inspect data.story + data.notes, then persist "
            "the adopted text with aipdd_drama_save_episode_story before converting it to a script.",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "episode": {"type": "integer", "description": "Episode number (1-based, required)."},
                "story": {"type": "string", "description": "Story text to optimize; defaults to the stored story."},
                "story_style": {"type": "string", "description": "Director style key used for the diagnosis."},
            },
            ["episode"],
        ),
        _handle_optimize_episode_story,
    ),
    (
        "aipdd_drama_story_to_script",
        _schema(
            "aipdd_drama_story_to_script",
            "Adapt one episode's story into a line-by-line script and import it into the script "
            "template (故事转剧本). Real endpoint POST /projects/{project}/episodes/{episode}/"
            "story/to-script. The generated script is written to the episode (adapted_content + "
            "beat_source_text) and, unless autostart=false, the script_writer task starts "
            "automatically — the same task as aipdd_drama_generate_script. When the episode has "
            "no identities yet the response carries identity_plan_required=true (script still "
            "imported): plan them with aipdd_drama_plan_identities and re-run. Poll with "
            "aipdd_drama_get_task(task_type='script_writer', episode=N); read with "
            "aipdd_drama_get_episode_script(episode=N).",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "episode": {"type": "integer", "description": "Episode number (1-based, required)."},
                "story": {"type": "string", "description": "Story text to adapt; defaults to the stored story (also saved back when provided)."},
                "story_style": {"type": "string", "description": "Director style key ('' = auto)."},
                "target_beats": {"type": "integer", "description": "Target line count for the script (default 18)."},
                "beat_chars_min": {"type": "integer", "description": "Minimum characters per script line (default 14)."},
                "beat_chars_max": {"type": "integer", "description": "Maximum characters per script line (default 20)."},
                "autostart": {"type": "boolean", "description": "Start script_writer immediately after import (default true)."},
            },
            ["episode"],
        ),
        _handle_story_to_script,
    ),
    (
        "aipdd_drama_update_character_face_prompt",
        _schema(
            "aipdd_drama_update_character_face_prompt",
            "Set or repair one character's face_prompt (面部特征) before portrait generation. "
            "Use this after character extraction if a core character has an empty face_prompt, "
            "or when character_portrait fails with '请先设置面部特征 (face_prompt)'. Real endpoint "
            "PATCH /projects/{project}/characters/{name} with {face_prompt: ...}. After this "
            "succeeds, retry aipdd_drama_generate_portrait for that character.",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "name": {"type": "string", "description": "Character name (required)."},
                "character": {"type": "string", "description": "Alias of name."},
                "face_prompt": {
                    "type": "string",
                    "description": "Concrete facial features: hairstyle, face shape, eyes, skin tone, age cues; no clothing.",
                },
            },
            ["name", "face_prompt"],
        ),
        _handle_update_character_face_prompt,
    ),
    (
        "aipdd_drama_plan_identities",
        _schema(
            "aipdd_drama_plan_identities",
            "Plan character identities for one episode (身份规划, identity_planner task). Use THIS "
            "instead of guessing — real endpoint POST /projects/{project}/episodes/{episode}/identities/"
            "plan-async. This is a PREREQUISITE for aipdd_drama_generate_script. Poll aipdd_drama_get_task("
            "task_type='identity_planner', episode=N).",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "episode": {"type": "integer", "description": "Episode number (required)."},
            },
            ["episode"],
        ),
        _handle_plan_identities,
    ),
    (
        "aipdd_drama_plan_scenes",
        _schema(
            "aipdd_drama_plan_scenes",
            "Plan the scene menu for one episode (场景规划, episode_scene_planner task). Use THIS "
            "after script generation and before sketch generation when the pipeline needs scene "
            "context. Real endpoint POST /projects/{project}/episodes/{episode}/scenes/plan. Poll "
            "with aipdd_drama_get_task(task_type='episode_scene_planner', episode=N).",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "episode": {"type": "integer", "description": "Episode number (required)."},
            },
            ["episode"],
        ),
        _handle_plan_scenes,
    ),
    (
        "aipdd_drama_plan_props",
        _schema(
            "aipdd_drama_plan_props",
            "Plan the prop menu for one episode (道具规划, episode_prop_planner task). Use THIS "
            "after script generation and before sketch generation when the pipeline needs prop "
            "context. Real endpoint POST /projects/{project}/episodes/{episode}/props/plan. Poll "
            "with aipdd_drama_get_task(task_type='episode_prop_planner', episode=N).",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "episode": {"type": "integer", "description": "Episode number (required)."},
            },
            ["episode"],
        ),
        _handle_plan_props,
    ),
    (
        "aipdd_drama_generate_scene_master",
        _schema(
            "aipdd_drama_generate_scene_master",
            "Generate one scene's canonical master reference image (场景正向参考图, "
            "scene_reference_asset task). Real endpoint POST /projects/{project}/scenes/{name}/"
            "master/generate-async. Use scene names from aipdd_drama_get(path='/projects/{project}/"
            "scenes') or the episode scene menu. Poll with aipdd_drama_get_task(task_type="
            "'scene_reference_asset', episode=0, scope=<returned scope>).",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "name": {"type": "string", "description": "Scene name (required)."},
                "scene_name": {"type": "string", "description": "Alias of name."},
            },
            ["name"],
        ),
        _handle_generate_scene_master,
    ),
    (
        "aipdd_drama_generate_scene_reverse",
        _schema(
            "aipdd_drama_generate_scene_reverse",
            "Generate one scene's reverse master reference image (场景反向参考图, "
            "scene_reference_asset task). Real endpoint POST /projects/{project}/scenes/{name}/"
            "reverse/generate-async. Poll with aipdd_drama_get_task(task_type='scene_reference_asset', "
            "episode=0, scope=<returned scope>).",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "name": {"type": "string", "description": "Scene name (required)."},
                "scene_name": {"type": "string", "description": "Alias of name."},
            },
            ["name"],
        ),
        _handle_generate_scene_reverse,
    ),
    (
        "aipdd_drama_generate_portrait",
        _schema(
            "aipdd_drama_generate_portrait",
            "Generate one character's portrait (肖像生成, character_portrait task). Real endpoint "
            "POST /projects/{project}/characters/{name}/portrait-async. Call once per character. Poll "
            "aipdd_drama_get_task(task_type='character_portrait').",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "name": {"type": "string", "description": "Character name (required; from the character list)."},
            },
            ["name"],
        ),
        _handle_generate_portrait,
    ),
    (
        "aipdd_drama_generate_identity_image",
        _schema(
            "aipdd_drama_generate_identity_image",
            "Generate a character identity image (身份图生成, identity_image task). Real endpoint POST "
            "/projects/{project}/characters/{name}/identities/{identity_id}/generate-async. Poll "
            "aipdd_drama_get_task(task_type='identity_image').",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "name": {"type": "string", "description": "Character name (required)."},
                "identity_id": {"type": "string", "description": "Identity id from the character's identity list (required)."},
            },
            ["name", "identity_id"],
        ),
        _handle_generate_identity_image,
    ),
    (
        "aipdd_drama_generate_sketches",
        _schema(
            "aipdd_drama_generate_sketches",
            "Generate beat sketches for one episode (草图生成, sketch_generation task). Real endpoint "
            "POST /projects/{project}/episodes/{episode}/sketches/generate with a canonical body. "
            "This tool automatically runs assign-colors first by default and fills safe defaults: "
            "model='nanobanana', grid_index=-1 (all grids), sketch_scene_grouping=true, "
            "aspect_ratio='2:3'. Use THIS instead of aipdd_drama_post or guessing the body. Runs "
            "after the script exists. Poll aipdd_drama_get_task(task_type='sketch_generation', episode=N).",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "episode": {"type": "integer", "description": "Episode number (required)."},
                "style": {"type": "string", "description": "Optional visual style override."},
                "model": {"type": "string", "description": "Sketch model. Default: nanobanana."},
                "grid_index": {
                    "type": "integer",
                    "description": "Grid index to generate. Use -1 to generate all grids. Default: -1.",
                },
                "sketch_scene_grouping": {
                    "type": "boolean",
                    "description": "Group sketch grids by scene. Default: true.",
                },
                "aspect_ratio": {
                    "type": "string",
                    "enum": ["2:3", "16:9"],
                    "description": "Sketch aspect ratio. Default: 2:3.",
                },
                "image_generation_selection": {
                    "type": "string",
                    "description": "Optional backend/provider selection from sketch settings.",
                },
                "auto_assign_colors": {
                    "type": "boolean",
                    "description": "Run /sketches/assign-colors before generation. Default: true.",
                },
                "body": {
                    "type": "object",
                    "description": "Advanced override merged into the canonical generate body.",
                },
            },
            ["episode"],
        ),
        _handle_generate_sketches,
    ),
    (
        "aipdd_drama_detect_sketch_identities",
        _schema(
            "aipdd_drama_detect_sketch_identities",
            "Run AI detection on one episode's generated sketches and persist detected identities "
            "and props to each beat. Real endpoint POST /projects/{project}/episodes/{episode}/"
            "sketches/detect-identities. Requires sketches to exist and sketch colors to be assigned; "
            "if colors are missing, run aipdd_drama_generate_sketches or POST assign-colors first. Use "
            "THIS when the user asks to run AI 检测 / identity detection for sketches. If the tool "
            "returns a timeout or retryable=false, do not call it again in the same turn; report the "
            "timeout and stop.",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "episode": {"type": "integer", "description": "Episode number (required)."},
            },
            ["episode"],
        ),
        _handle_detect_sketch_identities,
    ),
    (
        "aipdd_drama_get_sketches",
        _schema(
            "aipdd_drama_get_sketches",
            "Get display-ready official sketch URLs for an episode, to SHOW the user. This tool "
            "returns only current sketch_url media. It does not fall back to "
            "grids/epNNN/sketch/beat_XX_t* pool candidates and never substitutes first frames. "
            "Use aipdd_drama_get_first_frames only when the user explicitly "
            "asks for 首帧/first frames. Do NOT read "
            "sketch_path from a task result, and do NOT use vision_analyze to 'show' images. "
            "After calling this tool, do not write markdown images, raw URLs, http/static paths, "
            "or HTML media tags.",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "episode": {"type": "integer", "description": "Episode number (required)."},
                "beat": {"type": "integer", "description": "Show only one beat's sketch."},
                "beat_indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Show only these beat numbers, in episode order.",
                },
                "offset": {
                    "type": "integer",
                    "description": "Zero-based media offset after beat filtering. Use with limit for paging.",
                },
                "limit": {"type": "integer", "description": "Maximum media items to return. Default/max: 12."},
            },
            ["episode"],
        ),
        _handle_get_sketches,
    ),
    (
        "aipdd_drama_get_first_frames",
        _schema(
            "aipdd_drama_get_first_frames",
            "Get display-ready first-frame URLs for an episode, to SHOW the user. This tool returns "
            "only frame_url media. Use this only when the user explicitly asks for 首帧/first frames. "
            "Use aipdd_drama_get_sketches for sketches. After calling this tool, do not write markdown "
            "images, raw URLs, http/static paths, or HTML media tags.",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "episode": {"type": "integer", "description": "Episode number (required)."},
                "beat": {"type": "integer", "description": "Show only one beat's first frame."},
                "beat_indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Show only these beat numbers, in episode order.",
                },
                "offset": {
                    "type": "integer",
                    "description": "Zero-based media offset after beat filtering. Use with limit for paging.",
                },
                "limit": {"type": "integer", "description": "Maximum media items to return. Default/max: 12."},
            },
            ["episode"],
        ),
        _handle_get_first_frames,
    ),
    (
        "aipdd_drama_get_sketch_candidates",
        _schema(
            "aipdd_drama_get_sketch_candidates",
            "Get display-ready sketch pool candidates for one beat. This tool shows "
            "grids/epNNN/sketch/beat_XX_t* candidates and is separate from current sketch_url. "
            "Use aipdd_drama_get_sketches when the user asks for the official/current sketch.",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "episode": {"type": "integer", "description": "Episode number (required)."},
                "beat": {"type": "integer", "description": "Beat number (required)."},
                "offset": {
                    "type": "integer",
                    "description": "Zero-based candidate offset. Use with limit for paging.",
                },
                "limit": {"type": "integer", "description": "Maximum media items to return. Default/max: 12."},
            },
            ["episode", "beat"],
        ),
        _handle_get_sketch_candidates,
    ),
    (
        "aipdd_drama_get_scene_images",
        _schema(
            "aipdd_drama_get_scene_images",
            "Get display-ready scene image URLs for a project, to SHOW the user. Returns per-scene "
            "servable master_url/reverse_master_url/pano_url/custom_scene_url and prepared media data. "
            "Do NOT use local *_path fields, task result paths, or synthesized download URLs. "
            "After calling this tool, do not write markdown images, raw URLs, http/static paths, "
            "or HTML media tags.",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "include_reverse": {
                    "type": "boolean",
                    "description": "Include reverse_master_url entries. Default: true.",
                },
                "include_pano": {
                    "type": "boolean",
                    "description": "Include pano_url entries. Default: false.",
                },
                "include_custom": {
                    "type": "boolean",
                    "description": "Include custom_scene_url entries. Default: false.",
                },
                "name": {"type": "string", "description": "Show scenes whose name contains this text."},
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Show scenes whose name contains any of these texts.",
                },
                "scene_name": {"type": "string", "description": "Alias of name; fuzzy contains match."},
                "scene_type": {"type": "string", "description": "Show only scenes with this scene_type."},
                "index": {
                    "type": "integer",
                    "description": "Show only the Nth scene from the API scene list, 1-based.",
                },
                "scene_indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Show only these 1-based scene indexes from the API scene list.",
                },
                "offset": {
                    "type": "integer",
                    "description": "Zero-based media offset after scene filtering. Use with limit for paging.",
                },
                "limit": {"type": "integer", "description": "Maximum media items to return. Default/max: 12."},
            },
        ),
        _handle_get_scene_images,
    ),
    (
        "aipdd_drama_get_character_media",
        _schema(
            "aipdd_drama_get_character_media",
            "Get display-ready character portrait/identity image URLs and prepared media data. "
            "After calling this tool, do not write markdown images, raw URLs, http/static paths, "
            "or HTML media tags; "
            "the backend renders the returned media automatically.",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "include_identities": {"type": "boolean", "description": "Include identity images. Default: true."},
                "media_kind": {
                    "type": "string",
                    "enum": ["all", "portrait", "identity"],
                    "description": "all=portraits plus identity images; portrait=only character portraits; identity=only identity images.",
                },
                "name": {
                    "type": "string",
                    "description": "Show character media whose character name, aliases, or identity name/id contains this text.",
                },
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Show character media whose character name, aliases, or identity name/id contains any of these texts.",
                },
                "query": {
                    "type": "string",
                    "description": "Broad fuzzy text query over character name, role/description, identity names, and identity descriptions.",
                },
                "identity_name": {
                    "type": "string",
                    "description": "Fuzzy text query over identity image names/ids.",
                },
                "offset": {
                    "type": "integer",
                    "description": "Zero-based media offset after character filtering. Use with limit for paging.",
                },
                "limit": {"type": "integer", "description": "Maximum media items to return. Default/max: 12."},
            },
        ),
        _handle_get_character_media,
    ),
    (
        "aipdd_drama_get_episode_media",
        _schema(
            "aipdd_drama_get_episode_media",
            "Get display-ready episode beat video/audio URLs and prepared media data. "
            "media_type='video' returns video previews; media_type='audio' returns audio items. "
            "After calling this tool, do not write markdown images, raw URLs, http/static paths, "
            "or HTML media tags; "
            "the backend renders the returned media automatically.",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "episode": {"type": "integer", "description": "Episode number (required)."},
                "media_type": {"type": "string", "enum": ["video", "audio"], "description": "Default: video."},
                "beat": {"type": "integer", "description": "Show only one beat's video/audio."},
                "beat_indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Show only these beat numbers, in episode order.",
                },
                "query": {
                    "type": "string",
                    "description": "Fuzzy text query over beat title, description, narration/dialogue, speaker, characters, and scene.",
                },
                "search": {
                    "type": "string",
                    "description": "Alias of query.",
                },
                "offset": {
                    "type": "integer",
                    "description": "Zero-based media offset after beat filtering. Use with limit for paging.",
                },
                "limit": {"type": "integer", "description": "Maximum media items to return. Video max 6; audio max 20."},
            },
            ["episode"],
        ),
        _handle_get_episode_media,
    ),
    (
        "aipdd_drama_render_first_frames",
        _schema(
            "aipdd_drama_render_first_frames",
            "Generate first frames for an episode (首帧生成, selected_regen task). Real endpoint POST "
            "/projects/{project}/episodes/{episode}/beats/regenerate with {beat_indices:[...]}. Omit "
            "beat_indices to render ALL beats of the episode (resolved automatically). Requires sketches "
            "first. Poll aipdd_drama_get_task(task_type='selected_regen', episode=N).",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "episode": {"type": "integer", "description": "Episode number (required)."},
                "beat_indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Beat numbers to render. Omit to render all beats of the episode.",
                },
                "style": {"type": "string", "description": "Optional visual style override."},
            },
            ["episode"],
        ),
        _handle_render_first_frames,
    ),
    (
        "aipdd_drama_generate_audio",
        _schema(
            "aipdd_drama_generate_audio",
            "Generate episode audio/voiceover using the current IndexTTS2 audio pipeline "
            "(音频生成, audio_generation_indextts2 task). Real endpoint POST /projects/{project}/"
            "episodes/{episode}/audio/generate. Use THIS instead of legacy /tts/generate, which "
            "has been removed. Poll aipdd_drama_get_task(task_type='audio_generation_indextts2', "
            "episode=N).",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "episode": {"type": "integer", "description": "Episode number (required)."},
                "mode": {
                    "type": "string",
                    "description": "Audio generation mode. Backend default is sync_changed.",
                },
                "beat_numbers": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Optional beat numbers for partial audio generation.",
                },
                "provider": {"type": "string", "description": "Optional provider override."},
                "voice": {"type": "string", "description": "Optional voice override."},
                "model": {"type": "string", "description": "Optional model override."},
                "rate": {"type": "string", "description": "Optional speech rate override."},
            },
            ["episode"],
        ),
        _handle_generate_audio,
    ),
    (
        "aipdd_drama_optimize_video_global",
        _schema(
            "aipdd_drama_optimize_video_global",
            "Run global video optimization for one episode (全局视频优化, global_optimize_video task). "
            "Real endpoint POST /projects/{project}/episodes/{episode}/optimize/video-global. Poll "
            "aipdd_drama_get_task(task_type='global_optimize_video', episode=N).",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "episode": {"type": "integer", "description": "Episode number (required)."},
            },
            ["episode"],
        ),
        _handle_optimize_video_global,
    ),
    (
        "aipdd_drama_compose_episode",
        _schema(
            "aipdd_drama_compose_episode",
            "Compose/export the final video for one episode (合成导出, compose_episode task). Real "
            "endpoint POST /projects/{project}/episodes/{episode}/videos/compose. Poll aipdd_drama_get_task("
            "task_type='compose_episode', episode=N).",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "episode": {"type": "integer", "description": "Episode number (required)."},
            },
            ["episode"],
        ),
        _handle_compose_episode,
    ),
    (
        "aipdd_drama_get_final_video",
        _schema(
            "aipdd_drama_get_final_video",
            "Get and display the composed final episode video (最终成片展示). Real endpoint GET "
            "/projects/{project}/episodes/{episode}/final. Use this after compose_episode completes "
            "or when the user asks for the final video. If no final video exists, report that state; "
            "do not synthesize file URLs.",
            {
                "project_id": {"type": "string", "description": "Defaults to DRAMACLAW_PROJECT_ID."},
                "episode": {"type": "integer", "description": "Episode number (required)."},
            },
            ["episode"],
        ),
        _handle_get_final_video,
    ),
    (
        "aipdd_drama_start_single_video",
        _schema(
            "aipdd_drama_start_single_video",
            "Generate one beat's video (单 beat 视频, single_video task), POST /episodes/{ep}/beats/"
            "{beat}/video. You do NOT pass a prompt — the beat's stored video_prompt is used. "
            "Prerequisites: the beat's first frame must exist AND the beat must have a non-empty "
            "video_prompt; if the API returns '首帧不存在' or 'prompt is required', that prerequisite "
            "is missing — report it, do NOT invent fixes. Compose only works after all beat videos exist.",
            {
                "project_id": {"type": "string"},
                "episode": {"type": "integer"},
                "beat": {"type": "integer", "description": "Beat number (required)."},
                "beat_number": {"type": "integer"},
                "video_backend": {"type": "string", "description": "Optional backend override."},
                "duration": {"type": "number", "description": "Optional seconds."},
            },
            ["episode", "beat"],
        ),
        _handle_start_single_video,
    ),
    (
        "vectcut_health",
        _schema(
            "vectcut_health",
            "Check the VectCut editing service (剪辑服务): returns its draft directory, media "
            "roots, and platform. Call this when vectcut_* tools fail or before a long edit.",
            {},
        ),
        _handle_vectcut_health,
    ),
    (
        "vectcut_list_options",
        _schema(
            "vectcut_list_options",
            "List VectCut option names (转场/蒙版/动画/特效/字体 等枚举) for one kind. Use the "
            "returned names in vectcut_add_* animation/transition/mask/effect parameters.",
            {
                "kind": {
                    "type": "string",
                    "description": (
                        "One of: intro_animation, outro_animation, combo_animation, transition, "
                        "mask, audio_effect, font, text_intro, text_outro, text_loop, "
                        "video_scene_effect, video_character_effect."
                    ),
                },
            },
            ["kind"],
        ),
        _handle_vectcut_list_options,
    ),
    (
        "vectcut_create_draft",
        _schema(
            "vectcut_create_draft",
            "Create a new VectCut draft (空白画布) and return draft_id. Pass the id to every later "
            "vectcut_add_* call and finish with vectcut_save_draft. Default canvas 1080x1920 "
            "(vertical short drama); override width/height for other ratios.",
            dict(_VECTCUT_CANVAS_PROPS),
        ),
        _handle_vectcut_create_draft,
    ),
    (
        "vectcut_add_video",
        _schema(
            "vectcut_add_video",
            "Add a video clip to the draft timeline (视频轨). " + VECTCUT_MEDIA_HINT,
            {
                "video_url": {"type": "string", "description": VECTCUT_MEDIA_HINT},
                "draft_id": _VECTCUT_DRAFT_PROP,
                **_VECTCUT_TIME_PROPS,
                "target_start": {"type": "number", "description": "Timeline position to place the clip at (seconds)."},
                "duration": {"type": "number", "description": "Optional clip duration in seconds."},
                "speed": {"type": "number", "description": "Playback speed (default 1.0)."},
                "volume": {"type": "number", "description": "Clip volume (default 1.0)."},
                "transition": {"type": "string", "description": "Transition name (see vectcut_list_options kind='transition')."},
                "transition_duration": {"type": "number", "description": "Transition duration in seconds (default 0.5)."},
                "track_name": {"type": "string", "description": "Timeline track name (default video_main)."},
                **_VECTCUT_PLACEMENT_PROPS,
                "mask_type": {"type": "string", "description": "Mask name (see vectcut_list_options kind='mask')."},
                "background_blur": {"type": "integer", "description": "Background blur level 1-4 (optional)."},
                "project_dir": _VECTCUT_PROJECT_DIR_PROP,
            },
            ["video_url", "draft_id"],
        ),
        _handle_vectcut_add_video,
    ),
    (
        "vectcut_add_image",
        _schema(
            "vectcut_add_image",
            "Add an image to the draft timeline (图片轨), e.g. a render frame or sketch. " + VECTCUT_MEDIA_HINT,
            {
                "image_url": {"type": "string", "description": VECTCUT_MEDIA_HINT},
                "draft_id": _VECTCUT_DRAFT_PROP,
                **_VECTCUT_TIME_PROPS,
                **_VECTCUT_CANVAS_PROPS,
                **_VECTCUT_PLACEMENT_PROPS,
                "track_name": {"type": "string", "description": "Timeline track name (default image_main)."},
                "intro_animation": {"type": "string", "description": "Entrance animation (see vectcut_list_options kind='intro_animation')."},
                "outro_animation": {"type": "string", "description": "Exit animation (see vectcut_list_options kind='outro_animation')."},
                "transition": {"type": "string", "description": "Transition name (see vectcut_list_options kind='transition')."},
                "transition_duration": {"type": "number", "description": "Transition duration in seconds (default 0.5)."},
                "mask_type": {"type": "string", "description": "Mask name (see vectcut_list_options kind='mask')."},
                "project_dir": _VECTCUT_PROJECT_DIR_PROP,
            },
            ["image_url", "draft_id"],
        ),
        _handle_vectcut_add_image,
    ),
    (
        "vectcut_add_audio",
        _schema(
            "vectcut_add_audio",
            "Add an audio track to the draft (音频轨): voice-over, BGM, or sound effect. " + VECTCUT_MEDIA_HINT,
            {
                "audio_url": {"type": "string", "description": VECTCUT_MEDIA_HINT},
                "draft_id": _VECTCUT_DRAFT_PROP,
                **_VECTCUT_TIME_PROPS,
                "target_start": {"type": "number", "description": "Timeline position to place the audio at (seconds)."},
                "duration": {"type": "number", "description": "Optional audio duration in seconds."},
                "volume": {"type": "number", "description": "Volume (default 1.0)."},
                "speed": {"type": "number", "description": "Playback speed (default 1.0)."},
                "track_name": {"type": "string", "description": "Timeline track name (default audio_main)."},
                "effect_type": {"type": "string", "description": "Audio effect name (see vectcut_list_options kind='audio_effect')."},
                "effect_params": {"type": "array", "description": "Audio effect parameter list (optional)."},
                "project_dir": _VECTCUT_PROJECT_DIR_PROP,
            },
            ["audio_url", "draft_id"],
        ),
        _handle_vectcut_add_audio,
    ),
    (
        "vectcut_add_text",
        _schema(
            "vectcut_add_text",
            "Add a text overlay to the draft (文本) with optional shadow/background/bubble styling.",
            {
                "text": {"type": "string", "description": "Text content."},
                "start": {"type": "number", "description": "Start time (seconds)."},
                "end": {"type": "number", "description": "End time (seconds)."},
                "draft_id": _VECTCUT_DRAFT_PROP,
                "font": {"type": "string", "description": "Font name (see vectcut_list_options kind='font')."},
                "font_color": {"type": "string", "description": "Font color, e.g. #FFFFFF."},
                "font_size": {"type": "number", "description": "Font size (default 8.0)."},
                "bold": {"type": "boolean", "description": "Bold text."},
                "italic": {"type": "boolean", "description": "Italic text."},
                "underline": {"type": "boolean", "description": "Underlined text."},
                "track_name": {"type": "string", "description": "Timeline track name (default text_main)."},
                "transform_x": {"type": "number", "description": "Horizontal offset (0 = center)."},
                "transform_y": {"type": "number", "description": "Vertical offset (0 = center)."},
                "shadow_enabled": {"type": "boolean", "description": "Enable text shadow."},
                "shadow_color": {"type": "string", "description": "Shadow color (default #000000)."},
                "background_color": {"type": "string", "description": "Text background color (optional)."},
                "intro_animation": {"type": "string", "description": "Entrance animation (see vectcut_list_options kind='text_intro')."},
                "outro_animation": {"type": "string", "description": "Exit animation (see vectcut_list_options kind='text_outro')."},
                "text_styles": {"type": "array", "description": "Multi-style ranges: [{start, end, style:{size,bold,italic,underline,color,alpha,align}, font}]."},
            },
            ["text", "start", "end", "draft_id"],
        ),
        _handle_vectcut_add_text,
    ),
    (
        "vectcut_add_subtitle",
        _schema(
            "vectcut_add_subtitle",
            "Add subtitles (字幕) from an SRT file path, URL, or raw SRT text. Paths under the "
            "Dramaclaw project are resolved like other media references.",
            {
                "srt": {"type": "string", "description": "SRT file path, URL, or raw SRT text content."},
                "draft_id": _VECTCUT_DRAFT_PROP,
                "time_offset": {"type": "number", "description": "Shift all subtitle times by this offset (seconds)."},
                "font": {"type": "string", "description": "Font name."},
                "font_size": {"type": "number", "description": "Font size (default 8.0)."},
                "font_color": {"type": "string", "description": "Font color (default #FFFFFF)."},
                "bold": {"type": "boolean", "description": "Bold text."},
                "italic": {"type": "boolean", "description": "Italic text."},
                "underline": {"type": "boolean", "description": "Underlined text."},
                "border_width": {"type": "number", "description": "Text border width (default 0 = none)."},
                "border_color": {"type": "string", "description": "Text border color (default #000000)."},
                "background_color": {"type": "string", "description": "Subtitle background color."},
                "background_alpha": {"type": "number", "description": "Background alpha 0-1 (default 0 = transparent)."},
                "transform_x": {"type": "number", "description": "Horizontal offset."},
                "transform_y": {"type": "number", "description": "Vertical offset (default -0.8 = bottom)."},
                "track_name": {"type": "string", "description": "Timeline track name (default subtitle)."},
                "project_dir": _VECTCUT_PROJECT_DIR_PROP,
            },
            ["srt", "draft_id"],
        ),
        _handle_vectcut_add_subtitle,
    ),
    (
        "vectcut_add_effect",
        _schema(
            "vectcut_add_effect",
            "Add a scene/character video effect (特效) to the draft.",
            {
                "effect_type": {"type": "string", "description": "Effect name (see vectcut_list_options kind='video_scene_effect' / 'video_character_effect')."},
                "draft_id": _VECTCUT_DRAFT_PROP,
                **_VECTCUT_TIME_PROPS,
                "effect_category": {"type": "string", "description": "'scene' (default) or 'character'."},
                "track_name": {"type": "string", "description": "Timeline track name (default effect_01)."},
                "params": {"type": "array", "description": "Effect parameter list (optional)."},
            },
            ["effect_type", "draft_id"],
        ),
        _handle_vectcut_add_effect,
    ),
    (
        "vectcut_add_sticker",
        _schema(
            "vectcut_add_sticker",
            "Add a sticker (贴纸) to the draft by resource id.",
            {
                "sticker_id": {"type": "string", "description": "Sticker resource id."},
                "draft_id": _VECTCUT_DRAFT_PROP,
                **_VECTCUT_TIME_PROPS,
                **_VECTCUT_PLACEMENT_PROPS,
                "alpha": {"type": "number", "description": "Opacity 0-1 (default 1.0)."},
                "rotation": {"type": "number", "description": "Rotation angle in degrees."},
                "track_name": {"type": "string", "description": "Timeline track name (default sticker_main)."},
            },
            ["sticker_id", "start", "end", "draft_id"],
        ),
        _handle_vectcut_add_sticker,
    ),
    (
        "vectcut_add_keyframe",
        _schema(
            "vectcut_add_keyframe",
            "Add keyframes (关键帧) to a video track for position/scale/rotation/alpha animations. "
            "Single mode: property_type+time+value; batch mode: property_types+times+values.",
            {
                "draft_id": _VECTCUT_DRAFT_PROP,
                "track_name": {"type": "string", "description": "Timeline track name (default video_main)."},
                "property_type": {
                    "type": "string",
                    "description": "One of: position_x, position_y, rotation, scale_x, scale_y, uniform_scale, alpha, saturation, contrast, brightness, volume.",
                },
                "time": {"type": "number", "description": "Keyframe time in seconds (single mode, default 0)."},
                "value": {"type": "string", "description": "Keyframe value (single mode)."},
                "property_types": {"type": "array", "description": "Batch mode property types."},
                "times": {"type": "array", "description": "Batch mode times."},
                "values": {"type": "array", "description": "Batch mode values."},
            },
            ["draft_id"],
        ),
        _handle_vectcut_add_keyframe,
    ),
    (
        "vectcut_get_duration",
        _schema(
            "vectcut_get_duration",
            "Get a media file duration (seconds) as the VectCut host sees it. " + VECTCUT_MEDIA_HINT,
            {
                "video_url": {"type": "string", "description": VECTCUT_MEDIA_HINT},
                "project_dir": _VECTCUT_PROJECT_DIR_PROP,
            },
            ["video_url"],
        ),
        _handle_vectcut_get_duration,
    ),
    (
        "vectcut_save_draft",
        _schema(
            "vectcut_save_draft",
            "Save the draft (保存草稿) to the server-side draft folder and return its path. With "
            "auto_deploy the draft is also copied into the local Jianying/CapCut draft box when "
            "available, so the user can open it in the editor. On success the draft is also "
            "archived into the project asset library folder 「剪映草稿」 (Jianying drafts), where "
            "the user can download the draft package as a zip. This is the final step of an edit.",
            {
                "draft_id": _VECTCUT_DRAFT_PROP,
                "project_name": {"type": "string", "description": "Optional draft display name."},
                "auto_deploy": {"type": "boolean", "description": "Copy into the local Jianying/CapCut draft box (default true)."},
                "draft_folder": {"type": "string", "description": "Optional explicit output folder on the VectCut host."},
            },
            ["draft_id"],
        ),
        _handle_vectcut_save_draft,
    ),
    # ---- [aipdd fork delta] 工具页 MCP 工具组（与前端「工具」栏目九面板对应）----
    (
        "aipdd_drama_video_clone",
        _schema(
            "aipdd_drama_video_clone",
            "Recreate a reference video end-to-end (视频复刻, task_type=freezone_video_clone): "
            "keyframe extraction → shot analysis → per-shot remake planning → shot generation → "
            "auto concatenation into one final video. reference_mode=frames (default) uses the "
            "source keyframes as per-shot reference images (the model must support all_reference); "
            "text remakes purely from shot descriptions. prompt (optional remake instructions) "
            "and reference_urls (optional reference-content images, max 4) act together on the "
            "remake (either requires all_reference support). audio_mode: generate (default) / "
            "mute / source (keep the reference audio). Poll the produced video with GET "
            "/projects/{project}/freezone/jobs/freezone_video_clone/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "video_url": {"type": "string", "description": "Reference video /static URL inside this project (upload the file first if needed)."},
                "prompt": {"type": "string", "description": "Optional extra remake instructions (style/subject details); acts together with reference_urls; empty = follow the reference video."},
                "reference_urls": {"type": "array", "items": {"type": "string"}, "description": "Optional reference-content image /static URLs (max 4): character/scene/style references that act together with the prompt on the remake (requires all_reference support)."},
                "model": {"type": "string", "description": "Video model id from /freezone/video/models (frames mode or reference content needs all_reference support)."},
                "reference_mode": {"type": "string", "enum": ["frames", "text"], "description": "frames = source keyframes as shot references (default); text = text-only remake."},
                "audio_mode": {"type": "string", "enum": ["generate", "mute", "source"], "description": "Audio strategy (default generate = model-generated audio)."},
                "aspect_ratio": {"type": "string", "description": "Output aspect ratio (default 16:9)."},
                "resolution": {"type": "string", "description": "Output resolution tier (default 720p)."},
                "max_frames": {"type": "integer", "description": "Max keyframes to extract for analysis/reference (3-50, default 12; 12-20 recommended)."},
                "scene_threshold": {"type": "number", "description": "FFmpeg scene-change threshold 0-1 (default 0.3)."},
                "human_review": {"type": "boolean", "description": "Enable the HuiMeng real-person review flow for face-containing sources."},
                "scene_optimize": {"type": "string", "enum": ["anime", "realistic"], "description": "Optional Seedance 2.0 style optimization."},
                "archive_to_library": _TOOLS_PAGE_ARCHIVE_PROP,
            },
            ["video_url"],
        ),
        _handle_video_clone,
    ),
    (
        "aipdd_drama_video_mix",
        _schema(
            "aipdd_drama_video_mix",
            "Mix short-video material into one narrated film (混剪短视频, task_type="
            "freezone_video_mix): a pool of asset-library images/videos is cut into clips "
            "in the given order, looped to fill the voice-over (or target) duration, with "
            "optional burned-in subtitles and background music — rendered locally with "
            "ffmpeg (source audio is dropped). audio_mode=generate (default) synthesizes "
            "the narration from subject/script via the TTS model; mute renders silently "
            "(optionally to max_duration_seconds). material_urls must be existing /static "
            "URLs of this project (upload first if needed; 1-60 items). Poll the produced "
            "video with GET /projects/{project}/freezone/jobs/freezone_video_mix/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "material_urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Asset /static URLs (images and/or videos) cut in this order and looped to fill the film; 1-60 items.",
                },
                "subject": {"type": "string", "description": "Video topic (e.g. '春日露营的三个理由'); required with audio_mode=generate unless script is given."},
                "script": {"type": "string", "description": "Optional handwritten narration; when set it is used instead of AI-generating from the subject."},
                "audio_mode": {"type": "string", "enum": ["generate", "mute"], "description": "generate = synthesized voice-over drives the edit (default); mute = silent film from the material pool."},
                "subtitle_enabled": {"type": "boolean", "description": "Burn subtitles from the narration (default false, generate mode only; set true to burn)."},
                "subtitle_font_size": {"type": "string", "enum": ["small", "medium", "large"], "description": "Subtitle font size tier (default medium; only when subtitle_enabled)."},
                "bgm_url": {"type": "string", "description": "Optional background-music /static URL; empty = no BGM."},
                "bgm_volume": {"type": "number", "description": "BGM volume 0-1; empty = auto (0.2 with voice-over, 1.0 as the only track in mute mode)."},
                "aspect_ratio": {"type": "string", "description": "Output aspect ratio (default 9:16)."},
                "resolution": {"type": "string", "description": "Output resolution tier (default 720p)."},
                "clip_duration": {"type": "number", "description": "Seconds per material clip, 1-10 (default 3)."},
                "transition": {"type": "string", "enum": ["fade", "none"], "description": "fade = per-clip fade in/out (default); none = hard cuts."},
                "fit_mode": {"type": "string", "enum": ["cover", "contain"], "description": "cover = crop to fill (default); contain = letterbox."},
                "max_duration_seconds": {"type": "number", "description": "Optional target duration (5-600s) for mute mode; clips loop to fill it."},
                "emotion_prompt": {"type": "string", "description": "Optional emotion hint for the narration, e.g. '轻松、亲切'."},
                "model": {"type": "string", "description": "Optional audio catalog id from /freezone/audio/models; empty = default voice engine."},
                "voice_scope": {"type": "string", "description": "Voice kind: project_narrator / user_custom / character_default / character_age_group / identity / identity_resolved. Required when any voice_* field is set."},
                "voice_character_name": {"type": "string", "description": "Character name for character_*/identity* scopes."},
                "voice_identity_id": {"type": "string", "description": "Identity id for identity/identity_resolved scopes."},
                "voice_slot": {"type": "string", "description": "Age-group slot (child/youth/middle/elder) for character_age_group scope."},
                "voice_id": {"type": "string", "description": "Account voice id for user_custom scope (from /freezone/audio/references)."},
                "archive_to_library": _TOOLS_PAGE_ARCHIVE_PROP,
            },
            ["material_urls"],
        ),
        _handle_video_mix,
    ),
    (
        "aipdd_drama_whiteboard_animation",
        _schema(
            "aipdd_drama_whiteboard_animation",
            "Turn a narration script into a whiteboard hand-drawn animation (白板动画, task_type="
            "freezone_whiteboard_animation): the text model plans the copy into scenes, each scene is "
            "generated as a warm-beige line drawing, then revealed stroke by stroke and stitched with the "
            "voice-over (plus optional burned-in subtitles and BGM). The reveal / stitching / mixing is "
            "rendered purely locally (OpenCV + ffmpeg). model_text picks the scene-planning LLM (from "
            "/model-gateway/text-models), model_image the line-art image catalog (from "
            "/freezone/image/models) and model the audio catalog for the narration (from "
            "/freezone/audio/models); every one of them may stay empty to use the system default chain. "
            "One call produces one finished MP4 — poll it with GET "
            "/projects/{project}/freezone/jobs/freezone_whiteboard_animation/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "script": {"type": "string", "description": "Narration copy read aloud by the whiteboard video (required, 1-10000 characters)."},
                "model_text": {"type": "string", "description": "Scene-planning text model id from /model-gateway/text-models; empty = system default text chain."},
                "model_image": {"type": "string", "description": "Line-art image catalog id from /freezone/image/models; empty = default image model."},
                "image_model_params": {"type": "object", "description": "Optional request-parameter overrides for the image model (paired with model_image)."},
                "model": {"type": "string", "description": "Audio catalog id for the narration from /freezone/audio/models; empty = default voice engine."},
                "aspect_ratio": {"type": "string", "description": "Output aspect ratio (default 16:9)."},
                "resolution": {"type": "string", "description": "Output resolution tier (default 720p)."},
                "max_scenes": {"type": "integer", "description": "Upper bound of storyboard scenes, 1-12 (default 8); the real count adapts to the copy length."},
                "pen_cursor": {"type": "boolean", "description": "Overlay a pen-tip cursor on the reveal frontier (default true)."},
                "subtitle_enabled": {"type": "boolean", "description": "Burn subtitles cut by the narration duration (default false)."},
                "subtitle_font_size": {"type": "string", "enum": ["small", "medium", "large"], "description": "Subtitle font size tier (default medium; only when subtitle_enabled)."},
                "emotion_prompt": {"type": "string", "description": "Optional emotion hint for the narration, e.g. '轻松、亲切'."},
                "voice_scope": {"type": "string", "description": "Voice kind: project_narrator / user_custom / character_default / character_age_group / identity / identity_resolved. Required when any voice_* field is set."},
                "voice_character_name": {"type": "string", "description": "Character name for character_*/identity* scopes."},
                "voice_identity_id": {"type": "string", "description": "Identity id for identity/identity_resolved scopes."},
                "voice_slot": {"type": "string", "description": "Age-group slot (child/youth/middle/elder) for character_age_group scope."},
                "voice_id": {"type": "string", "description": "Account voice id for user_custom scope (from /freezone/audio/references)."},
                "bgm_url": {"type": "string", "description": "Optional background-music /static URL; empty = no BGM."},
                "bgm_volume": {"type": "number", "description": "BGM volume 0-1; empty = auto (0.2 mixed under the voice-over)."},
                "archive_to_library": _TOOLS_PAGE_ARCHIVE_PROP,
            },
            ["script"],
        ),
        _handle_whiteboard_animation,
    ),

    (
        "aipdd_drama_explainer_video",
        _schema(
            "aipdd_drama_explainer_video",
            "Turn reference material into a narrated science-explainer motion-graphics video (科普讲解"
            "视频, task_type=freezone_explainer_video): the text model rewrites the material into chapter-"
            "marked narration, plans one shot per sentence on a restricted visual DSL (term / flow / "
            "compare / bars / bignumber / list / cycle / timeline primitives), then the shot timeline is "
            "emitted as a self-contained HTML frame project and rendered headlessly to MP4 by the "
            "HyperFrames producer service (engine-as-a-service, DRAMACLAW_RENDERER_URL); if that service is "
            "unreachable it falls back to local Pillow frame drawing + ffmpeg. The video picture is rendered "
            "silent and the voice-over (+ optional BGM) is mixed on the API side, so only the gateway text + "
            "audio capabilities are used - no image or video model. Shot durations follow the real "
            "voice-over length, chapters get a HUD capsule and a section card, subtitles ride the bottom. "
            "model_text picks the LLM for narration + storyboard (from /model-gateway/text-models), model "
            "the audio catalog for the voice-over (from /freezone/audio/models); either may stay empty to "
            "use the system default chain. style_preset / format / captions tune the HyperFrames look "
            "(color preset, canvas orientation, subtitle band). One call produces one finished MP4 (default "
            "16:9) - poll it with GET "
            "/projects/{project}/freezone/jobs/freezone_explainer_video/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "reference_content": {"type": "string", "description": "Material to explain (required, 1-12000 characters): a topic brief, an article or a document body. The narration is rewritten inside this material only - the model is told not to invent facts it does not contain."},
                "title": {"type": "string", "description": "Optional title hint for the opening card (e.g. 'RAG 检索增强生成'); empty = the model titles it."},
                "duration_s": {"type": "integer", "description": "Target finished length in seconds, clamped to 90-330 (default 180)."},
                "model_text": {"type": "string", "description": "Text model id used for both narration and storyboard from /model-gateway/text-models; empty = system default text chain."},
                "model": {"type": "string", "description": "Audio catalog id for the narration from /freezone/audio/models; empty = default voice engine."},
                "background": {"type": "string", "enum": ["dots", "stars"], "description": "Backdrop style: dots (animated dot grid, default) or stars."},
                "resolution": {"type": "string", "enum": ["720p", "1080p"], "description": "Output resolution tier (default 720p)."},
                "style_preset": {"type": "string", "enum": ["ink", "ocean", "sunset", "paper", "mono"], "description": "HyperFrames color/typography preset (ink = black MG default). Only affects the hyperframes engine; unknown values normalize to ink."},
                "format": {"type": "string", "enum": ["landscape", "portrait", "square"], "description": "Finished canvas orientation for the hyperframes engine: landscape 16:9 (default), portrait 9:16, square 1:1."},
                "captions": {"type": "boolean", "description": "Overlay the synced subtitle band (default true)."},
                "emotion_prompt": {"type": "string", "description": "Optional emotion hint for the narration, e.g. '沉稳、克制'."},
                "voice_scope": {"type": "string", "description": "Voice kind: project_narrator / user_custom / character_default / character_age_group / identity / identity_resolved. Required when any voice_* field is set."},
                "voice_character_name": {"type": "string", "description": "Character name for character_*/identity* scopes."},
                "voice_identity_id": {"type": "string", "description": "Identity id for identity/identity_resolved scopes."},
                "voice_slot": {"type": "string", "description": "Age-group slot (child/youth/middle/elder) for character_age_group scope."},
                "voice_id": {"type": "string", "description": "Account voice id for user_custom scope (from /freezone/audio/references)."},
                "bgm_url": {"type": "string", "description": "Optional background-music /static URL; empty = no BGM."},
                "bgm_volume": {"type": "number", "description": "BGM volume 0-1; empty = auto (0.2 mixed under the narration)."},
                "archive_to_library": _TOOLS_PAGE_ARCHIVE_PROP,
            },
            ["reference_content"],
        ),
        _handle_explainer_video,
    ),
    (
        "aipdd_drama_channel_short_video",
        _schema(
            "aipdd_drama_channel_short_video",
            "Turn an article or reference material into a channel-formatted short video (渠道短视频, "
            "task_type=freezone_channel_short_video): it reuses the science-explainer render pipeline "
            "(text model rewrites the source into chapter-marked narration + plans shots on the restricted "
            "visual DSL + HyperFrames producer renders the HTML frame project headlessly, voice-over and "
            "optional BGM mixed on the API side), but the target CHANNEL decides the finished spec - aspect "
            "ratio, duration band, subtitle default and cover - instead of a free-form format. channel is one "
            "of douyin (default, portrait 9:16) / kuaishou / xiaohongshu / shipinhao / bilibili (landscape "
            "16:9) / tiktok. The source is either reference_content (a topic brief / article / doc body) OR "
            "article_job_id pointing at a finished aipdd_drama_article_write job (its Markdown body is pulled "
            "as the source). It runs the HyperFrames engine ONLY (portrait/square has no Pillow fallback), so "
            "DRAMACLAW_RENDERER_URL must be configured. One call produces one MP4 plus a cover frame and "
            "per-channel copy suggestions (title / hashtags / hook) - it does NOT upload anywhere. Poll it "
            "with GET /projects/{project}/freezone/jobs/freezone_channel_short_video/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "channel": {"type": "string", "enum": ["douyin", "kuaishou", "xiaohongshu", "shipinhao", "bilibili", "tiktok"], "description": "Target channel; decides canvas orientation, duration band and cover. Unknown normalizes to douyin."},
                "reference_content": {"type": "string", "description": "Material to explain (0-12000 characters). Provide this OR article_job_id."},
                "article_job_id": {"type": "string", "description": "A finished freezone_article_write job id; its Markdown body becomes the video source. Provide this OR reference_content."},
                "title": {"type": "string", "description": "Optional title hint; empty = the model titles it (auto-filled from the article title when article_job_id is used)."},
                "duration_s": {"type": "integer", "description": "Target length in seconds; 0/empty = channel default, otherwise clamped to the channel's min-max band."},
                "model_text": {"type": "string", "description": "Text model id for narration + storyboard from /model-gateway/text-models; empty = system default text chain."},
                "model": {"type": "string", "description": "Audio catalog id for the narration from /freezone/audio/models; empty = default voice engine."},
                "style_preset": {"type": "string", "enum": ["ink", "ocean", "sunset", "paper", "mono"], "description": "HyperFrames color/typography preset (ink = black MG default); unknown normalizes to ink."},
                "captions": {"type": "boolean", "description": "Overlay the synced subtitle band; empty = channel default (all channels default to on)."},
                "resolution": {"type": "string", "enum": ["720p", "1080p"], "description": "Output resolution tier (default 720p)."},
                "emotion_prompt": {"type": "string", "description": "Optional emotion hint for the narration, e.g. '沉稳、克制'."},
                "voice_scope": {"type": "string", "description": "Voice kind: project_narrator / user_custom / character_default / character_age_group / identity / identity_resolved. Required when any voice_* field is set."},
                "voice_character_name": {"type": "string", "description": "Character name for character_*/identity* scopes."},
                "voice_identity_id": {"type": "string", "description": "Identity id for identity/identity_resolved scopes."},
                "voice_slot": {"type": "string", "description": "Age-group slot (child/youth/middle/elder) for character_age_group scope."},
                "voice_id": {"type": "string", "description": "Account voice id for user_custom scope (from /freezone/audio/references)."},
                "bgm_url": {"type": "string", "description": "Optional background-music /static URL; empty = no BGM."},
                "bgm_volume": {"type": "number", "description": "BGM volume 0-1; empty = auto (0.2 mixed under the narration)."},
                "archive_to_library": _TOOLS_PAGE_ARCHIVE_PROP,
            },
            [],
        ),
        _handle_channel_short_video,
    ),

    (
        "aipdd_drama_narration_smart_edit",
        _schema(
            "aipdd_drama_narration_smart_edit",
            "Smart-edit a talking-head narration video end-to-end (口播智能剪辑, "
            "task_type=freezone_narration_smart_edit). Borrows the methodology of auto-editor / "
            "video-use / erduo-broll but calls ONLY the model gateway + VectCut - no external "
            "Node/Nim runtime. Stages, each behind its own switch: (1) silence/pause cutting runs "
            "LOCALLY with ffmpeg silencedetect (cut_silence, tune noise_db/min_silence_s); "
            "(2) filler / misspeak / repetition cutting = gateway ASR + text model picks droppable "
            "spans (cut_fillers, asr_model/text_model); (3) content understanding + shot breakdown "
            "+ B-roll planning = text model, then B-roll images are generated via the gateway image "
            "model (plan_broll, image_model, broll_max caps the slots). Optional gateway-generated "
            "BGM (gen_bgm, music_model/bgm_prompt/bgm_gain). output_mode picks the deliverable: "
            "'video' = a local ffmpeg base MP4 (silence+filler cuts + BGM mixed, cover frame; NO "
            "B-roll/captions burned in); 'draft' = a JianYing/CapCut draft laid down through VectCut "
            "with the trimmed main track + B-roll on their tracks + subtitles + BGM (needs VECTCUT_BASE_URL "
            "and a configured draft dir); 'both' emits both. The source video_url must be a same-origin "
            "reference (an upload path or a /static asset URL) - public URLs are not downloaded. Poll with "
            "GET /projects/{project}/freezone/jobs/freezone_narration_smart_edit/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "video_url": {"type": "string", "description": "REQUIRED. Same-origin reference to the narration clip to edit: an upload path or a /static asset URL (a project video/asset). Resolved to a local path server-side; public URLs are rejected."},
                "output_mode": {"type": "string", "enum": ["draft", "video", "both"], "description": "Deliverable channel (default both). video = base MP4 only; draft = VectCut JianYing draft only (needs VectCut configured); both = both."},
                "aspect": {"type": "string", "enum": ["9:16", "16:9", "1:1"], "description": "Finished orientation for the draft canvas and B-roll (default 9:16)."},
                "cut_silence": {"type": "boolean", "description": "Cut silence/pauses locally with ffmpeg silencedetect (default true)."},
                "cut_fillers": {"type": "boolean", "description": "Cut fillers/misspeaks/repetitions via gateway ASR + text model (default true)."},
                "plan_broll": {"type": "boolean", "description": "Understand content, break into shots and plan/generate B-roll - laid down only in the draft (default true)."},
                "gen_bgm": {"type": "boolean", "description": "Generate BGM via the gateway music model (default false; degrades gracefully when no music channel is available)."},
                "captions": {"type": "boolean", "description": "Import the post-cut transcript as subtitles into the draft (default true; base MP4 has no burned captions)."},
                "noise_db": {"type": "number", "description": "silencedetect noise threshold in dB, more negative = more conservative (default -35)."},
                "min_silence_s": {"type": "number", "description": "Minimum silence length in seconds to cut (default 0.4, 0-5)."},
                "broll_max": {"type": "integer", "description": "Max B-roll image slots per run to protect the gateway (default 8, 0-20)."},
                "asr_model": {"type": "string", "description": "Transcription ASR model candidate id; empty = auto (Doubao speech -> gateway ASR)."},
                "text_model": {"type": "string", "description": "Text model id shared by filler-cut and shot planning from /model-gateway/text-models; empty = system default text chain."},
                "image_model": {"type": "string", "description": "Image catalog id for B-roll generation from /freezone/image/models; empty = image slot default."},
                "music_model": {"type": "string", "description": "Music generation model id for BGM; empty = default music channel."},
                "bgm_prompt": {"type": "string", "description": "BGM prompt; empty = a built-in 'light, low-intensity instrumental pad' default."},
                "bgm_gain": {"type": "number", "description": "BGM bed volume 0-1 mixed under the voice (default 0.2)."},
                "title": {"type": "string", "description": "Optional draft/film title; empty = the job id."},
                "archive_to_library": _TOOLS_PAGE_ARCHIVE_PROP,
            },
            ["video_url"],
        ),
        _handle_narration_smart_edit,
    ),

    (
        "aipdd_drama_video_idea",
        _schema(
            "aipdd_drama_video_idea",
            "Turn a one-line idea into a complete short video end-to-end (一句话生成"
            "短视频, task_type=freezone_video_idea): story expansion → character "
            "extraction with portrait sheets → per-scene scripts → storyboard / camera "
            "planning → per-shot first/last frames → shot videos → concatenation. "
            "model_image selects the image catalog used for portraits & frames (from "
            "/freezone/image/models), model_video the video catalog for shot generation "
            "(from /freezone/video/models); both default to the backend choice. One call "
            "produces one finished film. Poll with GET "
            "/projects/{project}/freezone/jobs/freezone_video_idea/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "idea": {"type": "string", "description": "One-line idea (≤2000 chars), e.g. '一只流浪猫在雨夜救下迷路的小女孩'."},
                "style": {"type": "string", "description": "Visual style used across portraits and shots (default 'Cinematic, realistic, film-like.')."},
                "user_requirement": {"type": "string", "description": "Optional extra requirements (audience / genre / pacing / ending)."},
                "max_scenes": {"type": "integer", "description": "Max scenes 1-6 (default 3; the story may naturally use fewer)."},
                "max_shots_per_scene": {"type": "integer", "description": "Max shots per scene 1-10 (default 5)."},
                "shot_duration": {"type": "number", "description": "Per-shot generation duration 3-10s (default 5)."},
                "portrait_mode": {"type": "string", "enum": ["three", "front"], "description": "three = front/side/back portrait sheets (default, better consistency); front = front view only."},
                "model_image": {"type": "string", "description": "Image catalog id from /freezone/image/models for portraits & shot frames; empty = backend default."},
                "model_video": {"type": "string", "description": "Video catalog id from /freezone/video/models for shot generation; empty = backend default."},
                "aspect_ratio": {"type": "string", "description": "Output aspect ratio (default 16:9)."},
                "resolution": {"type": "string", "description": "Shot generation resolution tier (default 720p)."},
                "generate_audio": {"type": "boolean", "description": "Let the model generate shot audio (dialogue/SFX, default true; auto-disabled when the model does not support it)."},
                "human_review": {"type": "boolean", "description": "Enable the HuiMeng real-person review flow for face-containing content."},
                "scene_optimize": {"type": "string", "enum": ["anime", "realistic"], "description": "Optional Seedance 2.0 style optimization."},
                "archive_to_library": _TOOLS_PAGE_ARCHIVE_PROP,
            },
            ["idea"],
        ),
        _handle_video_idea,
    ),
    (
        "aipdd_drama_voice_clone",
        _schema(
            "aipdd_drama_voice_clone",
            "Synthesize speech from text (人声复刻, task_type=freezone_audio_speech). Pass the "
            "voice identifiers returned by GET /freezone/audio/references; when all voice_* "
            "fields are empty the project's default narrator voice is used. Read the produced "
            "audio with GET /projects/{project}/freezone/jobs/freezone_audio_speech/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "text": {"type": "string", "description": "Text to synthesize (one line/paragraph per task)."},
                "emotion_prompt": {"type": "string", "description": "Optional emotion/style hint, e.g. '紧张、压低声音'."},
                "model": {"type": "string", "description": "Optional audio catalog id from /freezone/audio/models; empty = default voice engine."},
                "voice_scope": {"type": "string", "description": "Voice kind: project_narrator / user_custom / character_default / character_age_group / identity / identity_resolved. Required when any voice_* field is set."},
                "voice_character_name": {"type": "string", "description": "Character name for character_*/identity* scopes."},
                "voice_identity_id": {"type": "string", "description": "Identity id for identity/identity_resolved scopes."},
                "voice_slot": {"type": "string", "description": "Age-group slot (child/youth/middle/elder) for character_age_group scope."},
                "voice_id": {"type": "string", "description": "Account voice id for user_custom scope (from /freezone/audio/references)."},
                "target_episode": {"type": "integer", "description": "Optional target episode; enables beat_audio push targets in the result."},
                "target_beat": {"type": "integer", "description": "Optional target beat (with target_episode)."},
                "archive_to_library": _TOOLS_PAGE_ARCHIVE_PROP,
            },
            ["text"],
        ),
        _handle_voice_clone,
    ),
    (
        "aipdd_drama_video_dubbing",
        _schema(
            "aipdd_drama_video_dubbing",
            "[aipdd fork delta] Dub a video in its original language with a cloned voice "
            "(视频配音, task_type=freezone_video_dubbing). Upload the source video and a "
            "reference audio (target timbre) first to get their project /static/... urls; "
            "the backend extracts the original lines (NOT translated), re-voicing them with "
            "the reference timbre in the original language, then replaces the whole audio "
            "track and returns a dubbed mp4. 'language' is only a pronunciation hint passed "
            "to the synthesis upstream. Read the result with GET "
            "/projects/{project}/freezone/jobs/freezone_video_dubbing/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "source_url": {"type": "string", "description": "Source video static url (project /static/... from a prior upload) whose lines will be re-voiced and whose video track is kept."},
                "reference_audio_url": {"type": "string", "description": "Reference audio (target timbre) static url; registered as an account user_custom voice then used for zero-shot cloning."},
                "reference_name": {"type": "string", "description": "Optional display name for the registered voice; empty = the uploaded file name."},
                "language": {"type": "string", "description": "Optional language/pronunciation hint forwarded to the synthesis upstream; the lines are not translated. Empty = no hint."},
                "model": {"type": "string", "description": "Optional audio catalog id from /freezone/audio/models; empty = default voice engine."},
                "asr_model": {"type": "string", "description": "Optional ASR candidate id from /freezone/douyin-copywrite/models; empty = auto-pick a candidate that can handle local audio."},
                "target_episode": {"type": "integer", "description": "Optional target episode; enables beat_audio push targets in the result."},
                "target_beat": {"type": "integer", "description": "Optional target beat (with target_episode)."},
                "archive_to_library": _TOOLS_PAGE_ARCHIVE_PROP,
            },
            ["source_url", "reference_audio_url"],
        ),
        _handle_video_dubbing,
    ),
    (
        "aipdd_drama_music_create",
        _schema(
            "aipdd_drama_music_create",
            "Create a song with MiniMax Music 3.0 (音乐创作, task_type=freezone_music_generate) - five "
            "modes on one endpoint: one_sentence (description only, lyrics written by the model), "
            "lyrics (your lyrics, [Verse]/[Chorus] structure tags allowed), instrumental (no "
            "vocals, description required), cover (reference audio + a 10-300 char style "
            "description, lyrics optional), humming (two-step cover: POST "
            "/freezone/audio/music-cover-preprocess with the humming audio first to get "
            "cover_feature_id + extracted lyrics, edit them, then submit with cover_feature_id). "
            "model comes from /freezone/music/models (empty = the music default model in "
            "settings) and provider picks the site channel - minimax (overseas) or minimax_cn "
            "(domestic), whose keys are not interchangeable. Reference audio must be an existing "
            "/static URL / project-relative path / public URL, 6s-6min, <=50MB, mp3/wav/flac. "
            "Poll with GET /projects/{project}/freezone/jobs/freezone_music_generate/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "mode": {"type": "string", "enum": list(MUSIC_CREATE_MODES), "description": "Creation mode (required)."},
                "prompt": {"type": "string", "description": "Music description: required for one_sentence/instrumental (1-2000 chars), optional for lyrics, target-style hint for cover/humming (10-300 chars)."},
                "lyrics": {"type": "string", "description": "Lyrics: required for lyrics mode (1-3500 chars, [Verse]/[Chorus] tags allowed); optional for cover; required together with cover_feature_id (10-1000 chars). Never sent for instrumental."},
                "model": {"type": "string", "description": "Music model id from /freezone/music/models; empty = the music default model from settings. cover/humming need the cover-capable model."},
                "provider": {"type": "string", "description": "Site channel: minimax (overseas) or minimax_cn (domestic); empty = the channel the model entry belongs to."},
                "reference_audio": {"type": "string", "description": "Reference audio: project /static URL, project-relative path or public URL (6s-6min, <=50MB, mp3/wav/flac)."},
                "cover_feature_id": {"type": "string", "description": "Cover feature id returned by /freezone/audio/music-cover-preprocess (step 1 of the humming flow, valid 24h); when set do not send reference_audio again."},
                "sample_rate": {"type": "integer", "enum": [16000, 24000, 32000, 44100], "description": "Sample rate (default 44100)."},
                "bitrate": {"type": "integer", "enum": [32000, 64000, 128000, 256000], "description": "Bitrate, mp3 only (default 256000)."},
                "audio_format": {"type": "string", "enum": ["mp3", "wav", "pcm"], "description": "Output format; the produced file suffix follows it (default mp3)."},
                "source_name": {"type": "string", "description": "Optional source name used as the archived asset name prefix."},
                "source_tool": {"type": "string", "description": "Optional task attribution tag written into metadata.source_tool (default music-create)."},
                "archive_to_library": _TOOLS_PAGE_ARCHIVE_PROP,
            },
            ["mode"],
        ),
        _handle_music_create,
    ),
    (
        "aipdd_drama_lip_sync",
        _schema(
            "aipdd_drama_lip_sync",
            "Make the person in a video lip-sync to a separate audio track (对口型, task_type="
            "freezone_video_gen via omni-gen allReference). Both video_url and audio_url must be "
            "existing /static URLs of this project; the model must support mixed video+audio "
            "reference inputs. Poll with GET /projects/{project}/freezone/jobs/freezone_video_gen/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "video_url": {"type": "string", "description": "Base video /static URL."},
                "audio_url": {"type": "string", "description": "Driving audio /static URL."},
                "prompt": {"type": "string", "description": "Optional extra instructions."},
                "model": {"type": "string", "description": "Video model id from /freezone/video/models with all_reference support."},
                "resolution": {"type": "string", "description": "Output resolution tier (default 720p)."},
                "duration_seconds": {"type": "integer", "description": "Output duration in seconds (default 5)."},
                "generate_audio": {"type": "boolean", "description": "Whether the model also generates native audio (default false)."},
                "archive_to_library": _TOOLS_PAGE_ARCHIVE_PROP,
            },
            ["video_url", "audio_url"],
        ),
        _handle_lip_sync,
    ),
    (
        "aipdd_drama_replace_person",
        _schema(
            "aipdd_drama_replace_person",
            "Replace the person in a video with the character shown in a reference image (换人, "
            "task_type=freezone_video_gen via omni-gen allReference). video_url = source video, "
            "image_url = target person appearance; the model must support mixed video+image "
            "references. Poll with GET /projects/{project}/freezone/jobs/freezone_video_gen/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "video_url": {"type": "string", "description": "Source video /static URL."},
                "image_url": {"type": "string", "description": "Reference image /static URL of the replacement person."},
                "prompt": {"type": "string", "description": "Optional extra instructions."},
                "model": {"type": "string", "description": "Video model id from /freezone/video/models with all_reference support."},
                "resolution": {"type": "string", "description": "Output resolution tier (default 720p)."},
                "duration_seconds": {"type": "integer", "description": "Output duration in seconds (default 5)."},
                "generate_audio": {"type": "boolean", "description": "Whether the model also generates native audio (default false)."},
                "archive_to_library": _TOOLS_PAGE_ARCHIVE_PROP,
            },
            ["video_url", "image_url"],
        ),
        _handle_replace_person,
    ),
    (
        "aipdd_drama_replace_product",
        _schema(
            "aipdd_drama_replace_product",
            "Replace the product in a video with the product shown in a reference image (换产品, "
            "task_type=freezone_video_gen via omni-gen allReference). video_url = source video, "
            "image_url = target product appearance; the model must support mixed video+image "
            "references. Poll with GET /projects/{project}/freezone/jobs/freezone_video_gen/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "video_url": {"type": "string", "description": "Source video /static URL."},
                "image_url": {"type": "string", "description": "Reference image /static URL of the replacement product."},
                "prompt": {"type": "string", "description": "Optional extra instructions."},
                "model": {"type": "string", "description": "Video model id from /freezone/video/models with all_reference support."},
                "resolution": {"type": "string", "description": "Output resolution tier (default 720p)."},
                "duration_seconds": {"type": "integer", "description": "Output duration in seconds (default 5)."},
                "generate_audio": {"type": "boolean", "description": "Whether the model also generates native audio (default false)."},
                "archive_to_library": _TOOLS_PAGE_ARCHIVE_PROP,
            },
            ["video_url", "image_url"],
        ),
        _handle_replace_product,
    ),
    (
        "aipdd_drama_video_watermark",
        _schema(
            "aipdd_drama_video_watermark",
            "Remove a watermark or subtitles from a video (视频去水印). engine=local: local ffmpeg "
            "erase (task_type=freezone_video_erase, offline, no quota); engine=mediakit: "
            "Volcengine AI MediaKit cloud erase with automatic local fallback; engine=seedance: "
            "generative video edit (task_type=freezone_video_gen, requires model with video_edit "
            "support). mode=smart_subtitle (default) auto-detects the subtitle band; mode=box "
            "erases the given normalized box (box_x/box_y/box_width/box_height, 0-1). Poll with "
            "GET /projects/{project}/freezone/jobs/{task_type}/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "source_url": {"type": "string", "description": "Video /static URL to clean."},
                "engine": {"type": "string", "enum": ["local", "mediakit", "seedance"], "description": "Erase engine (default local)."},
                "mode": {"type": "string", "enum": ["smart_subtitle", "box"], "description": "Erase mode for local/mediakit (default smart_subtitle)."},
                "box_x": {"type": "number", "description": "Box left x, normalized 0-1 (mode=box)."},
                "box_y": {"type": "number", "description": "Box top y, normalized 0-1 (mode=box)."},
                "box_width": {"type": "number", "description": "Box width, normalized 0-1 (mode=box)."},
                "box_height": {"type": "number", "description": "Box height, normalized 0-1 (mode=box)."},
                "prompt": {"type": "string", "description": "Edit instruction for engine=seedance (default: remove watermark, keep the rest unchanged)."},
                "model": {"type": "string", "description": "video_edit-capable model id from /freezone/video/models (required for engine=seedance)."},
                "resolution": {"type": "string", "description": "Output resolution tier for engine=seedance."},
                "human_review": {"type": "boolean", "description": "Enable the HuiMeng real-person review flow (engine=seedance)."},
                "archive_to_library": _TOOLS_PAGE_ARCHIVE_PROP,
            },
            ["source_url"],
        ),
        _handle_video_watermark,
    ),
    (
        "aipdd_drama_image_watermark",
        _schema(
            "aipdd_drama_image_watermark",
            "Remove a watermark from an image via prompt-driven image editing (图片去水印, "
            "task_type=freezone_edit). base_url = the image /static URL; optionally pick model "
            "and model_id from /freezone/image/models. Poll with GET "
            "/projects/{project}/freezone/jobs/freezone_edit/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "base_url": {"type": "string", "description": "Image /static URL to clean."},
                "prompt": {"type": "string", "description": "Edit instruction (default: remove the watermark, keep the rest unchanged)."},
                "aspect_ratio": {"type": "string", "description": "Output aspect ratio; default 'original' keeps the source ratio."},
                "image_size": {"type": "string", "description": "Output size tier (default 2K)."},
                "model": {"type": "string", "description": "Provider API model from a /freezone/image/models entry (e.g. gpt-image-2)."},
                "model_id": {"type": "string", "description": "Catalog entry id from /freezone/image/models."},
                "gen_mode": {"type": "string", "description": "Generation mode (image_reference / all_reference ...)."},
                "quality": {"type": "string", "description": "Quality tier (for gpt-image-2: low/medium/high/auto)."},
                "archive_to_library": _TOOLS_PAGE_ARCHIVE_PROP,
            },
            ["base_url"],
        ),
        _handle_image_watermark,
    ),
    (
        "aipdd_drama_douyin_copywrite",
        _schema(
            "aipdd_drama_douyin_copywrite",
            "Extract the spoken script from a Douyin video (抖音文案提取, task_type="
            "freezone_douyin_copywrite): paste a Douyin share link and the backend "
            "parses the video detail, then transcribes the narration with the ASR "
            "model you pick. Choose the ASR model from "
            "GET /freezone/douyin-copywrite/models (the gateway model list filtered "
            "to ASR-family entries such as Doubao Seed ASR, plus the built-in FunASR "
            "channel) — the user picks explicitly, there is no fixed fallback order. "
            "The result carries the full script text plus a txt URL (text/text_url); "
            "the txt is NOT archived into the asset library (its media types have no "
            "text carrier). Poll with GET /projects/{project}/freezone/jobs/"
            "freezone_douyin_copywrite/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "video": {"type": "string", "description": "Douyin share link (a v.douyin.com short link, the full share text or a video URL all work)."},
                "model": {"type": "string", "description": "ASR candidate id from /freezone/douyin-copywrite/models, e.g. 'gateway:volc:doubao-seed-asr' or 'funasr:aipdd:aipdd_funasr_transcribe'; empty falls back to the built-in FunASR channel (the tool UI has no default; pick explicitly)."},
            },
            ["video"],
        ),
        _handle_douyin_copywrite,
    ),
    # ---- [aipdd fork delta] 画布生图 MCP 工具组（与画布/工具页图片处理同源）----
    (
        "aipdd_drama_image_generate",
        _schema(
            "aipdd_drama_image_generate",
            "Generate an image from a text prompt (生图, task_type=freezone_gen) with optional "
            "reference images and a chosen image model. Poll the result with GET "
            "/projects/{project}/freezone/jobs/freezone_gen/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "prompt": {"type": "string", "description": "Image prompt (required)."},
                "aspect_ratio": {"type": "string", "description": "Output aspect ratio (default 1:1)."},
                "reference_urls": {"type": "array", "items": {"type": "string"}, "description": "Optional reference image /static URLs for image-to-image / all-reference modes."},
                "gen_mode": {"type": "string", "description": "Generation mode (textToImage default; image_reference / all_reference when references given)."},
                **_IMAGE_MODEL_PROPS,
            },
            ["prompt"],
        ),
        _handle_image_generate,
    ),
    (
        "aipdd_drama_image_edit",
        _schema(
            "aipdd_drama_image_edit",
            "Edit an existing image with a prompt (图片编辑, task_type=freezone_edit): base_url is "
            "the source, optionally extra reference images guide the redraw. Result is archived into "
            "the asset library by default. Poll with GET "
            "/projects/{project}/freezone/jobs/freezone_edit/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "base_url": _IMAGE_SOURCE_URL_PROP,
                "prompt": {"type": "string", "description": "Edit instruction (required)."},
                "extra_reference_urls": {"type": "array", "items": {"type": "string"}, "description": "Optional extra reference image /static URLs."},
                "aspect_ratio": {"type": "string", "description": "Output aspect ratio (default 2:3, or 'original')."},
                "gen_mode": {"type": "string", "description": "Generation mode."},
                "source_tool": {"type": "string", "description": "Optional task attribution tag written into metadata.source_tool."},
                **_IMAGE_MODEL_PROPS,
                "archive_to_library": _TOOLS_PAGE_ARCHIVE_PROP,
            },
            ["base_url", "prompt"],
        ),
        _handle_image_edit,
    ),
    (
        "aipdd_drama_image_upscale",
        _schema(
            "aipdd_drama_image_upscale",
            "Upscale an image (图片高清放大, task_type=freezone_edit). Pick a scale factor of "
            "2, 4 or 6. Poll with GET /projects/{project}/freezone/jobs/freezone_edit/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "source_url": _IMAGE_SOURCE_URL_PROP,
                "scale_factor": {"type": "integer", "enum": [2, 4, 6], "description": "Upscale factor (default 2)."},
                "model": {"type": "string", "description": "Image model id from /freezone/image/models (default newapi_gpt_image2)."},
                "quality": {"type": "string", "description": "Quality tier."},
                "image_size": {"type": "string", "description": "Output size tier (default 2K)."},
            },
            ["source_url"],
        ),
        _handle_image_upscale,
    ),
    (
        "aipdd_drama_image_outpaint",
        _schema(
            "aipdd_drama_image_outpaint",
            "Outpaint an image to a wider aspect ratio (扩图, task_type=freezone_edit). Poll with "
            "GET /projects/{project}/freezone/jobs/freezone_edit/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "source_url": _IMAGE_SOURCE_URL_PROP,
                "target_aspect_ratio": {"type": "string", "enum": ["original", "1:1", "4:3", "3:4", "16:9", "9:16"], "description": "Target ratio (default original)."},
                "num_images": {"type": "integer", "description": "Number of candidates (default 1)."},
                "model": {"type": "string", "description": "Image model id (default newapi_gpt_image2)."},
                "quality": {"type": "string", "description": "Quality tier."},
                "image_size": {"type": "string", "description": "Output size tier (default 2K)."},
            },
            ["source_url"],
        ),
        _handle_image_outpaint,
    ),
    (
        "aipdd_drama_image_relight",
        _schema(
            "aipdd_drama_image_relight",
            "Relight an image (重打光, task_type=freezone_edit): drive with a lighting reference "
            "image or explicit light params. Poll with GET "
            "/projects/{project}/freezone/jobs/freezone_edit/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "source_url": _IMAGE_SOURCE_URL_PROP,
                "lighting_reference_url": {"type": "string", "description": "Optional lighting reference image /static URL."},
                "scope": {"type": "string", "enum": ["global", "local"], "description": "Global or local relighting (default global)."},
                "smart_mode": {"type": "boolean", "description": "Let the model auto-plan the light (default true)."},
                "brightness": {"type": "integer", "description": "Brightness 0-100 (default 50)."},
                "color_hex": {"type": "string", "description": "Light color hex (default #ffffff)."},
                "color_temperature_kelvin": {"type": "integer", "description": "Color temperature in Kelvin (optional)."},
                "key_light_direction": {"type": "string", "enum": ["left", "top", "right", "front", "bottom", "back"], "description": "Key light direction (default front)."},
                "rim_light": {"type": "boolean", "description": "Add a rim light (default false)."},
                "prompt": {"type": "string", "description": "Optional extra light instructions."},
                **_IMAGE_MODEL_PROPS,
            },
            ["source_url"],
        ),
        _handle_image_relight,
    ),
    (
        "aipdd_drama_image_multi_view",
        _schema(
            "aipdd_drama_image_multi_view",
            "Generate a new camera view of an image (多视角, task_type=freezone_edit): pick a preset "
            "or a custom yaw/pitch/shot_size. Poll with GET "
            "/projects/{project}/freezone/jobs/freezone_edit/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "source_url": _IMAGE_SOURCE_URL_PROP,
                "preset": {"type": "string", "enum": ["custom", "fisheye", "oblique", "front", "front_up", "full_body", "back"], "description": "View preset (default custom)."},
                "yaw_degrees": {"type": "number", "description": "Custom yaw in degrees (preset=custom)."},
                "pitch_degrees": {"type": "number", "description": "Custom pitch in degrees (preset=custom)."},
                "shot_size": {"type": "string", "enum": ["extreme_close_up", "close_up", "medium_close", "medium", "full_body", "wide", "extreme_wide"], "description": "Shot size (default medium)."},
                "prompt": {"type": "string", "description": "Optional extra instructions."},
                **_IMAGE_MODEL_PROPS,
            },
            ["source_url"],
        ),
        _handle_image_multi_view,
    ),
    (
        "aipdd_drama_image_template_edit",
        _schema(
            "aipdd_drama_image_template_edit",
            "Run a fixed image template transform (模板化图片处理, task_type=freezone_edit): nine-grid "
            "multi-camera, three-view sheets, storyboard grids, light correction, etc. Poll with GET "
            "/projects/{project}/freezone/jobs/freezone_edit/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "source_url": _IMAGE_SOURCE_URL_PROP,
                "mode": {"type": "string", "enum": ["multi_camera_nine_grid", "story_pitch_four_grid", "character_face_three_view", "product_three_view", "storyboard_25_grid", "cinematic_light_correction", "character_three_view_generation", "image_projection_after_3s", "image_projection_before_5s"], "description": "Template mode (required)."},
                "prompt": {"type": "string", "description": "Optional extra instructions."},
                "catalog_id": {"type": "string", "description": "Catalog entry id from /freezone/image/models."},
                "model": {"type": "string", "description": "Image model id (default newapi_gpt_image2)."},
                "quality": {"type": "string", "description": "Quality tier."},
                "image_size": {"type": "string", "description": "Output size tier (default 2K)."},
            },
            ["source_url", "mode"],
        ),
        _handle_image_template_edit,
    ),
    (
        "aipdd_drama_image_redraw",
        _schema(
            "aipdd_drama_image_redraw",
            "Redraw an image, optionally masked (局部重绘): with mask_url it runs as "
            "task_type=freezone_mask_edit, otherwise as freezone_edit. Poll with GET "
            "/projects/{project}/freezone/jobs/{task_type}/{job_id}/result (task_type from submit).",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "source_url": _IMAGE_SOURCE_URL_PROP,
                "mask_url": {"type": "string", "description": "Optional mask /static URL for inpainting a region."},
                "aspect_ratio": {"type": "string", "enum": ["original", "1:1", "4:3", "3:4", "16:9", "9:16"], "description": "Output ratio (default original)."},
                "num_images": {"type": "integer", "description": "Number of candidates (default 1)."},
                "prompt": {"type": "string", "description": "Redraw instruction."},
                **_IMAGE_MODEL_PROPS,
            },
            ["source_url"],
        ),
        _handle_image_redraw,
    ),
    (
        "aipdd_drama_image_reverse_prompt",
        _schema(
            "aipdd_drama_image_reverse_prompt",
            "Reverse-engineer a text prompt from an image (图片反推提示词, task_type="
            "freezone_image_reverse_prompt). The result text is returned directly by GET "
            "/projects/{project}/freezone/jobs/freezone_image_reverse_prompt/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "source_url": _IMAGE_SOURCE_URL_PROP,
                "instruction": {"type": "string", "description": "Optional focus for the reverse prompt."},
            },
            ["source_url"],
        ),
        _handle_image_reverse_prompt,
    ),
    # ---- [aipdd fork delta] 画布生视频/音频 MCP 工具组（与画布、工具页同源）----
    (
        "aipdd_drama_video_generate",
        _schema(
            "aipdd_drama_video_generate",
            "Generate a video from a text prompt (文生视频, task_type=freezone_video_gen). Pick a "
            "video model from /freezone/video/models. Poll with GET "
            "/projects/{project}/freezone/jobs/freezone_video_gen/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "prompt": {"type": "string", "description": "Video prompt (required)."},
                "aspect_ratio": {"type": "string", "description": "Output aspect ratio (default 16:9)."},
                "scene_optimize": {"type": "string", "enum": ["anime", "realistic"], "description": "Optional Seedance 2.0 style optimization."},
                "gen_mode": {"type": "string", "description": "Generation mode (default textToVideo)."},
                **_VIDEO_MODEL_PROPS,
            },
            ["prompt"],
        ),
        _handle_video_generate,
    ),
    (
        "aipdd_drama_video_i2v",
        _schema(
            "aipdd_drama_video_i2v",
            "Generate a video from one or more images (图生视频, task_type=freezone_video_gen via "
            "/video/i2v). gen_mode=imageToVideo uses the first frame; imageReference treats them as "
            "references. Poll with GET "
            "/projects/{project}/freezone/jobs/freezone_video_gen/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "gen_mode": {"type": "string", "enum": ["imageToVideo", "imageReference"], "description": "imageToVideo = first-frame animation (default); imageReference = reference images."},
                "image_urls": {"type": "array", "items": {"type": "string"}, "description": "Source image /static URLs."},
                "prompt": {"type": "string", "description": "Optional motion/scene instruction."},
                "aspect_ratio": {"type": "string", "description": "Output aspect ratio (default 16:9)."},
                "scene_optimize": {"type": "string", "enum": ["anime", "realistic"], "description": "Optional Seedance 2.0 style optimization."},
                **_VIDEO_MODEL_PROPS,
            },
            ["gen_mode"],
        ),
        _handle_video_i2v,
    ),
    (
        "aipdd_drama_video_omni_gen",
        _schema(
            "aipdd_drama_video_omni_gen",
            "Generate a video from an arbitrary mix of image/video/audio references (全能参考生视频, "
            "task_type=freezone_video_gen via omni-gen allReference). This is the general form behind "
            "对口型 / 换人 / 换产品. references is a list of {type:image|video|audio, url, role?, label?}. "
            "The model must support all_reference. Poll with GET "
            "/projects/{project}/freezone/jobs/freezone_video_gen/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "prompt": {"type": "string", "description": "Generation prompt (required)."},
                "references": {"type": "array", "items": {"type": "object"}, "description": "Reference list, each {type, url(/static), role?, label?}."},
                "theme": {"type": "string", "description": "Optional theme tag."},
                **_VIDEO_MODEL_PROPS,
                "archive_to_library": _TOOLS_PAGE_ARCHIVE_PROP,
            },
            ["prompt", "references"],
        ),
        _handle_video_omni_gen,
    ),
    (
        "aipdd_drama_video_upscale",
        _schema(
            "aipdd_drama_video_upscale",
            "Upscale a video (视频高清放大, task_type=freezone_video_upscale). resolution must be "
            "1080p, 2k or 4k. Poll with GET "
            "/projects/{project}/freezone/jobs/freezone_video_upscale/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "source_url": {"type": "string", "description": "Source video /static URL."},
                "resolution": {"type": "string", "enum": ["1080p", "2k", "4k"], "description": "Target resolution (default 1080p)."},
                "frame_interpolation": {"type": "string", "description": "Frame interpolation mode (default none)."},
                "denoise_strength": {"type": "string", "enum": ["none", "1x", "2x"], "description": "Denoise strength (default 1x)."},
            },
            ["source_url"],
        ),
        _handle_video_upscale,
    ),
    (
        "aipdd_drama_video_edit",
        _schema(
            "aipdd_drama_video_edit",
            "Edit a video with a prompt plus optional image/audio references (视频编辑, "
            "task_type=freezone_video_gen via /video/video-edit). audio_setting=origin keeps the "
            "source audio. The model must support video_edit. Poll with GET "
            "/projects/{project}/freezone/jobs/freezone_video_gen/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "video_url": {"type": "string", "description": "Source video /static URL (required)."},
                "prompt": {"type": "string", "description": "Edit instruction."},
                "image_urls": {"type": "array", "items": {"type": "string"}, "description": "Optional reference image /static URLs."},
                "audio_urls": {"type": "array", "items": {"type": "string"}, "description": "Optional reference audio /static URLs."},
                "audio_setting": {"type": "string", "enum": ["auto", "origin"], "description": "auto (default) or origin (keep source audio)."},
                "source_tool": {"type": "string", "description": "Optional task attribution tag."},
                **_VIDEO_MODEL_PROPS,
                "archive_to_library": _TOOLS_PAGE_ARCHIVE_PROP,
            },
            ["video_url"],
        ),
        _handle_video_edit,
    ),
    (
        "aipdd_drama_video_extend",
        _schema(
            "aipdd_drama_video_extend",
            "Extend a video from its last frame (视频续写, task_type=freezone_video_gen via "
            "/video/video-extend). Default model newapi_seedance-2.5. Poll with GET "
            "/projects/{project}/freezone/jobs/freezone_video_gen/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "video_url": {"type": "string", "description": "Source video /static URL (required)."},
                "prompt": {"type": "string", "description": "Continuation instruction (required)."},
                **_VIDEO_MODEL_PROPS,
            },
            ["video_url", "prompt"],
        ),
        _handle_video_extend,
    ),
    (
        "aipdd_drama_audio_separate",
        _schema(
            "aipdd_drama_audio_separate",
            "Separate the vocals from a video's audio (人声分离, task_type=freezone_audio_separate). "
            "The result carries both audio_url (isolated voice) and mute_video_url (video without "
            "audio). Poll with GET "
            "/projects/{project}/freezone/jobs/freezone_audio_separate/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "source_url": {"type": "string", "description": "Source video /static URL (required)."},
                "target_episode": {"type": "integer", "description": "Optional target episode for push metadata."},
                "target_beat": {"type": "integer", "description": "Optional target beat (with target_episode)."},
            },
            ["source_url"],
        ),
        _handle_audio_separate,
    ),
    (
        "aipdd_drama_audio_music",
        _schema(
            "aipdd_drama_audio_music",
            "Generate background music from a text description (生成音乐, "
            "task_type=freezone_audio_eleven_music). Poll with GET "
            "/projects/{project}/freezone/jobs/freezone_audio_eleven_music/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "input": {"type": "string", "description": "Music description prompt (required)."},
                "model": {
                    "type": "string",
                    "description": (
                        "Music model; omit to use the default audio model configured in "
                        "Settings > Models."
                    ),
                },
                "response_format": {"type": "string", "enum": ["mp3", "opus", "pcm", "ulaw", "alaw"], "description": "Audio format (default mp3)."},
                "music_length_ms": {"type": "integer", "description": "Target length in ms (default 30000)."},
                "force_instrumental": {"type": "boolean", "description": "Force instrumental only (default true)."},
            },
            ["input"],
        ),
        _handle_audio_music,
    ),
    # ---- [aipdd fork delta] 文本/脚本与资源目录读 MCP 工具组 ----
    (
        "aipdd_drama_text_generate",
        _schema(
            "aipdd_drama_text_generate",
            "Generate text from a prompt (文本生成, task_type=freezone_text_generate). The result "
            "text is returned directly by GET "
            "/projects/{project}/freezone/jobs/freezone_text_generate/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "prompt": {"type": "string", "description": "Text generation prompt (required)."},
            },
            ["prompt"],
        ),
        _handle_text_generate,
    ),
    (
        "aipdd_drama_text_translate",
        _schema(
            "aipdd_drama_text_translate",
            "Translate a text block (文本翻译, task_type=freezone_text_translate). The result is "
            "returned by GET /projects/{project}/freezone/jobs/freezone_text_translate/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "text": {"type": "string", "description": "Text to translate (required)."},
                "node_type": {"type": "string", "enum": ["generic", "image", "video", "audio", "text"], "description": "Content kind for translation context (default generic)."},
            },
            ["text"],
        ),
        _handle_text_translate,
    ),
    (
        "aipdd_drama_article_write",
        _schema(
            "aipdd_drama_article_write",
            "Write and lay out a WeChat-Official-Account article (公众号文章编写与排版, "
            "task_type=freezone_article_write). Provide topic and/or material (at least one). The gateway "
            "text model drafts structured Markdown in the chosen writing style inside the material only (it "
            "is told not to invent facts), then a deterministic local layout engine wraps it into "
            "platform-compliant Official-Account HTML (every Chinese text node is wrapped in a "
            "``<span leaf="">`` so styling survives paste) using the chosen theme component library, and a "
            "hard validation gate must pass with zero errors before the job succeeds. Layout + validation are "
            "fully local (no network); only the single text-model call is gateway-routed (EG-18a). Publishing "
            "is a separate call (aipdd_drama_article_publish). Produces a result JSON + a preview HTML - poll "
            "with GET /projects/{project}/freezone/jobs/freezone_article_write/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "topic": {"type": "string", "description": "Article topic / brief (e.g. 'RAG 检索增强生成'); at least one of topic / material is required."},
                "material": {"type": "string", "description": "Reference material (an article or document body, 1-12000 characters). The draft stays inside this material - the model is told not to invent facts it does not contain."},
                "style": {"type": "string", "enum": ["viewpoint", "tutorial", "emotional", "data", "story", "rational"], "description": "Writing style: viewpoint 观点深度 (default) / tutorial 教程清单 / emotional 情绪共鸣 / data 数据复盘 / story 叙事特稿 / rational 理性干货; unknown normalizes to viewpoint."},
                "theme": {"type": "string", "enum": ["moyu-green", "graphite-minimal", "red-white", "amber-orange", "terminal-geek", "zen-whitespace"], "description": "Layout theme (determines colors + authoritative underline CSS). Default moyu-green (摸鱼绿); unknown normalizes to it."},
                "target_words": {"type": "integer", "description": "Target body length in Chinese characters, clamped to 300-6000 (default 1500)."},
                "audience": {"type": "string", "description": "Optional target-reader persona (e.g. '一线工程师'); empty = the model judges."},
                "tone": {"type": "string", "description": "Optional tone hint (e.g. '克制、专业'); empty = follows the chosen style."},
                "intro_quote": {"type": "string", "description": "Optional opening pull-quote; empty = the layout omits the intro card."},
                "author": {"type": "string", "description": "Optional byline for the intro card and signature block; empty = unsigned."},
                "model_text": {"type": "string", "description": "Text (LLM) model id for the draft from /model-gateway/text-models; empty = system default text chain."},
                "archive_to_library": _TOOLS_PAGE_ARCHIVE_PROP,
            },
        ),
        _handle_article_write,
    ),
    (
        "aipdd_drama_article_publish",
        _schema(
            "aipdd_drama_article_publish",
            "Publish a laid-out Official-Account article to the draft box / optionally push live "
            "(公众号发布, synchronous). Point it at an aipdd_drama_article_write job via article_job_id or "
            "pass an html_url directly. Credentials resolve in this order: project config.json > "
            "~/.wechat_publish.json > env WECHAT_APPID/WECHAT_SECRET. When nothing is configured it does NOT "
            "fail the layout - it returns status=needs_config with guidance to fetch AppID/Secret from the "
            "Official-Account backend and add the server IP to the whitelist. An uncertified account (error "
            "48001) auto-degrades to 'create draft via API + publish manually from the backend'.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "article_job_id": {"type": "string", "description": "job_id of a completed freezone_article_write task whose layout HTML will be published (one of article_job_id / html_url)."},
                "html_url": {"type": "string", "description": "Or directly point at a layout HTML /static URL; takes priority over article_job_id."},
                "title": {"type": "string", "description": "Article title (default: inferred from the product / body)."},
                "author": {"type": "string", "description": "Author name (optional)."},
                "digest": {"type": "string", "description": "Digest / summary (default: first 120 chars of the body)."},
                "cover_url": {"type": "string", "description": "Cover image /static URL (required for a graphic-text draft; uploaded as a permanent material)."},
                "publish_now": {"type": "boolean", "description": "Also push live after creating the draft (certified service accounts only; subscription accounts get one broadcast/day - use with care). Default false."},
            },
        ),
        _handle_article_publish,
    ),
    (
        "aipdd_drama_story_script_generate",
        _schema(
            "aipdd_drama_story_script_generate",
            "Generate a storyboard script from a source script or video (故事脚本生成, "
            "task_type=freezone_story_script). Provide one of source_text / source_url / video_url. "
            "The JSON result is returned by GET "
            "/projects/{project}/freezone/jobs/freezone_story_script/{job_id}/result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "source_text": {"type": "string", "description": "Source script text (one of the three inputs)."},
                "source_url": {"type": "string", "description": "Source script file /static URL."},
                "video_url": {"type": "string", "description": "Source video /static URL to derive the script from."},
                "duration_sec": {"type": "number", "description": "Video total duration in seconds (optional)."},
                "character_refs": {"type": "array", "items": {"type": "object"}, "description": "Optional character reference list."},
                "max_frames": {"type": "integer", "description": "Max keyframes to sample (default 20)."},
                "scene_threshold": {"type": "number", "description": "Scene-change threshold 0-1 (default 0.3)."},
                "prompt": {"type": "string", "description": "Optional extra instruction."},
                "model": {"type": "string", "description": "Vision/text model id (default newapi_gemini_flash)."},
            },
        ),
        _handle_story_script_generate,
    ),
    (
        "aipdd_drama_video_script_reverse",
        _schema(
            "aipdd_drama_video_script_reverse",
            "Reverse-engineer a plain-text script from a video (视频反推剧本, task_type="
            "freezone_video_script_reverse): sample keyframes and let a vision model write the "
            "script. Model candidates come from GET /model-gateway/vision-models; empty uses the "
            "backend default chain. This txt job is NOT resolved via the /freezone/jobs/.../result "
            "whitelist — poll with aipdd_drama_get_task(task_type='freezone_video_script_reverse', "
            "episode=0, scope=<job_id>) and read the text from the task result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "video_url": {"type": "string", "description": "Uploaded video /static URL (required; upload via /freezone/upload first if needed)."},
                "prompt": {"type": "string", "description": "Optional extra requirements passed to the vision model."},
                "model": {"type": "string", "description": "Vision model id from /model-gateway/vision-models; empty = backend default."},
                "duration_sec": {"type": "number", "description": "Video total duration in seconds (optional)."},
                "max_frames": {"type": "integer", "description": "Max keyframes to sample (default 20)."},
                "scene_threshold": {"type": "number", "description": "Scene-change threshold 0-1 (default 0.3)."},
            },
            ["video_url"],
        ),
        _handle_video_script_reverse,
    ),
    (
        "aipdd_drama_media_copywrite",
        _schema(
            "aipdd_drama_media_copywrite",
            "Extract a written copy/script from media (文案提取, task_type=freezone_media_copywrite): "
            "a share link or an uploaded video/audio is transcribed with the ASR model you pick from "
            "GET /freezone/media-copywrite/models. This txt job is NOT resolved via the "
            "/freezone/jobs/.../result whitelist — poll with aipdd_drama_get_task("
            "task_type='freezone_media_copywrite', episode=0, scope=<job_id>) and read the text from "
            "the task result.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "source_type": {"type": "string", "enum": ["link", "upload_video", "upload_audio"], "description": "Media source kind (required)."},
                "url": {"type": "string", "description": "Share link or uploaded /static URL (required)."},
                "platform": {"type": "string", "description": "Optional platform hint (e.g. douyin)."},
                "model": {"type": "string", "description": "ASR candidate id from /freezone/media-copywrite/models."},
                "source_tool": {"type": "string", "description": "Optional task attribution tag."},
            },
            ["source_type", "url"],
        ),
        _handle_media_copywrite,
    ),
    (
        "aipdd_drama_list_assets",
        _schema(
            "aipdd_drama_list_assets",
            "List the project's asset library (查看资产库): GET /projects/{project}/freezone/assets. "
            "Use this to find existing image/video/audio /static URLs before feeding other tools.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "query": {"type": "object", "description": "Optional query filters passed through to the assets route."},
            },
        ),
        _handle_list_assets,
    ),
    (
        "aipdd_drama_get_character_library",
        _schema(
            "aipdd_drama_get_character_library",
            "List the project's character library (查看角色库): GET "
            "/projects/{project}/freezone/video/character-library. Returns character/identity entries "
            "with their ids and image URLs, used as character_ids in video generation.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
            },
        ),
        _handle_get_character_library,
    ),
    (
        "aipdd_drama_get_audio_references",
        _schema(
            "aipdd_drama_get_audio_references",
            "List available voices / narrator references (查看音色库): GET "
            "/projects/{project}/freezone/audio/references. Provides the voice ids used by "
            "aipdd_drama_voice_clone and the mix voice_* fields.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
            },
        ),
        _handle_get_audio_references,
    ),
    (
        "aipdd_drama_get_models",
        _schema(
            "aipdd_drama_get_models",
            "Read a model catalog by kind (查看模型目录): image/video/audio come from "
            "/freezone/{kind}/models, text/vision from /model-gateway/{kind}-models. Use the returned "
            "ids for the model/model_id arguments of the generation tools.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "kind": {"type": "string", "enum": ["image", "video", "audio", "text", "vision"], "description": "Catalog kind (required)."},
            },
            ["kind"],
        ),
        _handle_get_models,
    ),
    (
        "aipdd_drama_auto_storyboard",
        _schema(
            "aipdd_drama_auto_storyboard",
            "Automated storyboard driver (自动化分镜): each call detects the project's current "
            "progress and advances exactly ONE stage along story → multi-episode screenplay → "
            "ingest/characters/episodes → per-episode identities → storyboard script → sketches → "
            "shot first frames, returning {stage, action, next_action}. Keep calling it until "
            "stage=\"done\"; long stages are background tasks, so re-call after a while instead of "
            "tight polling. The FIRST call must pass story_text when the project has no story "
            "source yet. Optional settings: story_style ('' = auto-detect the style), "
            "episode_count or total_seconds/seconds_per_episode (the driver derives episode count "
            "and per-episode target beats ~3s/shot), aspect_ratio, model/style for sketches and "
            "frames. It NEVER submits video tasks — use aipdd_drama_video_range for videos.",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "story_text": {
                    "type": "string",
                    "description": "Raw story text imported as the project story source on the first call when none exists yet.",
                },
                "story_style": {
                    "type": "string",
                    "description": "Director style key for screenplay/script generation; '' or omitted = auto-recommend from the story.",
                },
                "episode_count": {
                    "type": "integer",
                    "description": "Screenplay episode count (1-60). Omit to derive from total_seconds/seconds_per_episode (fallback 10).",
                },
                "total_seconds": {
                    "type": "number",
                    "description": "Total series duration in seconds; used with seconds_per_episode to derive episode_count.",
                },
                "seconds_per_episode": {
                    "type": "number",
                    "description": "Target per-episode duration in seconds; derives episode_count and per-episode target_beats (~3s per shot).",
                },
                "aspect_ratio": {
                    "type": "string",
                    "enum": ["2:3", "16:9"],
                    "description": "Sketch/shot aspect ratio (default 2:3).",
                },
                "model": {
                    "type": "string",
                    "description": "Image model id for sketches and first frames ('' = configured default); see aipdd_drama_get_models(kind='image').",
                },
                "style": {
                    "type": "string",
                    "description": "Visual style name for sketches/frames (optional; backend default when omitted).",
                },
                "episode": {
                    "type": "integer",
                    "description": "Restrict the per-episode stages to one episode number; default scans all episodes from the earliest unfinished one.",
                },
            },
        ),
        _handle_auto_storyboard,
    ),
    (
        "aipdd_drama_video_range",
        _schema(
            "aipdd_drama_video_range",
            "Batch-generate shot videos by beat numbers / a range (按分镜区间生成视频): accepts "
            "'1-8', '1,3,5-8' or a number list, then IMMEDIATELY submits one "
            "POST /episodes/{ep}/beats/{beat}/video per beat whose first frame exists. Beats that "
            "are missing or lack a first frame are listed in skipped (NOT queued). Returns "
            "submitted/skipped/errors — this call does not wait; poll each submission with "
            "aipdd_drama_get_task(task_type='single_video', episode=N, beat_num=B). Pick the video "
            "model via the model argument (aipdd_drama_get_models kind=video).",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "episode": {
                    "type": "integer",
                    "description": "Episode number (default 1).",
                },
                "beats": {
                    "type": "string",
                    "description": "Beat numbers or range, e.g. '1-8', '1,3,5-8' (arrays are also accepted).",
                },
                "model": {
                    "type": "string",
                    "description": "Video backend/model id (e.g. newapi_seedance-1.0-pro-fast); omit for the configured default.",
                },
                "resolution": {
                    "type": "string",
                    "description": "Video resolution for each shot (e.g. '720x1280' or '720p').",
                },
                "duration": {
                    "type": "integer",
                    "description": "Override shot duration in seconds (default: derived from the beat/audio).",
                },
                "mode": {
                    "type": "string",
                    "description": "Video generation mode override for the beat.",
                },
                "ratio": {
                    "type": "string",
                    "description": "Aspect ratio override passed to the video backend.",
                },
            },
            ["beats"],
        ),
        _handle_video_range,
    ),
    (
        "aipdd_drama_regen_sketches",
        _schema(
            "aipdd_drama_regen_sketches",
            "Batch-regenerate shot sketches by beat numbers (按镜头批量重生成草图, sketch_regen "
            "task). Real endpoint POST /projects/{project}/episodes/{episode}/sketches/regenerate "
            "with {beat_indices:[...], mode_key:'1x1_2-3_sketch'}. Accepts beat_indices as a number "
            "list or a '1-8'/'1,3,5-8' range string. Omit to regenerate ALL beats of the episode "
            "(resolved automatically). Requires the storyboard script first. Poll "
            "aipdd_drama_get_task(task_type='sketch_regen', episode=N).",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "episode": {"type": "integer", "description": "Episode number (required)."},
                "beat_indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Beat numbers to regenerate. Omit to regenerate all beats of the episode.",
                },
                "mode_key": {
                    "type": "string",
                    "description": "Sketch regen mode key. Default '1x1_2-3_sketch'.",
                },
                "style": {"type": "string", "description": "Optional visual style override."},
                "model": {"type": "string", "description": "Image model id ('' = configured default)."},
                "image_generation_selection": {
                    "type": "string",
                    "description": "Optional backend/provider selection from sketch settings.",
                },
            },
            ["episode"],
        ),
        _handle_regen_sketches,
    ),
    (
        "aipdd_drama_generate_video_prompts",
        _schema(
            "aipdd_drama_generate_video_prompts",
            "Batch-generate shot video prompts by beat numbers / a range (按分镜区间批量生成视频"
            "提示词): accepts '1-8', '1,3,5-8' or a number list, then submits one "
            "POST /episodes/{ep}/beats/{beat}/video-prompt/generate per beat. Omit beats to run all "
            "beats of the episode. Beats missing prerequisites (HTTP 409/404) are listed in skipped "
            "(NOT generated); other failures go to errors. Returns submitted/skipped/errors — this "
            "call does not wait; poll each submission with aipdd_drama_get_task("
            "task_type='beat_video_prompt', episode=N, beat_num=B).",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "episode": {
                    "type": "integer",
                    "description": "Episode number (default 1).",
                },
                "beats": {
                    "type": "string",
                    "description": "Beat numbers or range, e.g. '1-8', '1,3,5-8' (arrays are also accepted). Omit to run all beats of the episode.",
                },
            },
        ),
        _handle_generate_video_prompts,
    ),
    (
        "aipdd_drama_run_pipeline",
        _schema(
            "aipdd_drama_run_pipeline",
            "[aipdd fork delta] 编排层：委托服务端权威计划器（POST /pipeline/plan）一键推进整条短剧"
            "流水线（自动化生产线），三档可选。mode='manual' 单段推进；mode='checkpoint'（默认）先只"
            "“决策不发射”，返回 planned_requests 动作清单，带 approve=true 再调才真正触发该阶段（系统"
            "主导+用户逐段确认）；mode='auto' 在 budget_s 内循环“发射→轮询空闲→再发射”自动推进到分镜"
            "完成。与 auto_storyboard 共用同一服务端逻辑源，只推进到镜头首帧，绝不提交视频（视频请用"
            " aipdd_drama_video_range）。use_suggested=true 时未指定的 model/ratio/style 用"
            " aipdd_drama_suggest_config 智能默认。长任务不阻塞到底：返回 running 时用"
            " aipdd_drama_get_run_status 轮询。",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "mode": {
                    "type": "string",
                    "enum": ["manual", "checkpoint", "auto"],
                    "description": "自动化档位（默认 checkpoint：阶段边界暂停等用户 approve）。",
                },
                "approve": {
                    "type": "boolean",
                    "description": "checkpoint 模式下确认闸门：true 才真正触发 next_stage 的写操作。",
                },
                "use_suggested": {
                    "type": "boolean",
                    "description": "未显式指定时，用智能默认注入 model/aspect_ratio/style。",
                },
                "budget_s": {
                    "type": "number",
                    "description": "auto 模式单次调用内最长阻塞轮询秒数（默认 45，防 stdio 超时）。",
                },
                "episode": {
                    "type": "integer",
                    "description": "逐集阶段限定集号；省略则从最早未完成的集起逐集扫描。",
                },
                "story_text": {"type": "string", "description": "首次调用且无故事源时写入的故事正文。"},
                "story_style": {"type": "string", "description": "导演风格 key（'' = 自动推荐）。"},
                "episode_count": {"type": "integer", "description": "分集集数（1-60）。"},
                "total_seconds": {"type": "number", "description": "总时长，与 seconds_per_episode 推导集数。"},
                "seconds_per_episode": {"type": "number", "description": "单集目标时长。"},
                "aspect_ratio": {
                    "type": "string",
                    "enum": ["2:3", "16:9"],
                    "description": "草图/镜头画幅（默认 2:3）。",
                },
                "model": {"type": "string", "description": "草图/首帧图像模型 id（'' = 配置默认）。"},
                "style": {"type": "string", "description": "视觉风格名（可选）。"},
            },
        ),
        _handle_run_pipeline,
    ),
    (
        "aipdd_drama_get_run_status",
        _schema(
            "aipdd_drama_get_run_status",
            "[aipdd fork delta] 聚合整条流水线进度：合并 GET /pipeline/status + 项目任务行 +"
            "（可选）指定集的镜头计数，返回 {running, failed, completed, counts, done,"
            " next_recommended_action}。纯只读，供 Agent/前端展示“流水线到哪了”。",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "episode": {"type": "integer", "description": "可选：附带该集镜头草图/首帧/视频计数。"},
            },
        ),
        _handle_get_run_status,
    ),
    (
        "aipdd_drama_cancel_run",
        _schema(
            "aipdd_drama_cancel_run",
            "[aipdd fork delta] 取消本集在跑任务（全自动跑飞了能一键停）：逐个调"
            " DELETE /projects/{p}/tasks/{task_type}/{episode}?beat_num=&force=true。"
            "省略 task_types 时自动取本集正在运行的任务类型；single_video 按 beat 逐个取消。"
            "返回 cancelled/errors。",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "episode": {"type": "integer", "description": "Episode number (required)."},
                "task_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要取消的任务类型（省略=本集正在运行的）。也可传逗号分隔字符串。",
                },
                "beats": {"type": "string", "description": "single_video 取消的 beat 区间（省略=本集全部）。"},
                "scope": {"type": "string", "description": "任务作用域（mode_key、grid_index 等，按需）。"},
            },
            ["episode"],
        ),
        _handle_cancel_run,
    ),
    (
        "aipdd_drama_suggest_config",
        _schema(
            "aipdd_drama_suggest_config",
            "[aipdd fork delta] 智能默认配置推荐（只读）：读 image/video 模型目录 + 项目画幅，"
            "返回 {recommended:{image_model,video_model,ratio,resolution,duration,style,voice},"
            " alternatives, rationale}，供用户/Agent 先看好推荐再生成（被 run_pipeline"
            "(use_suggested=true) 复用）。",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "aspect_ratio": {
                    "type": "string",
                    "enum": ["2:3", "16:9"],
                    "description": "目标画幅（默认 2:3 竖屏）。",
                },
                "image_model": {"type": "string", "description": "预选图像模型（省略=取首个可用）。"},
                "video_model": {"type": "string", "description": "预选视频模型（省略=取首个可用）。"},
                "resolution": {"type": "string", "description": "预选分辨率。"},
                "duration": {"type": "integer", "description": "预选镜头时长（秒）。"},
                "style": {"type": "string", "description": "预选风格名。"},
                "voice": {"type": "string", "description": "预选配音声线。"},
            },
        ),
        _handle_suggest_config,
    ),
    (
        "aipdd_drama_commit_white_model_video",
        _schema(
            "aipdd_drama_commit_white_model_video",
            "[aipdd fork delta] 把 Blender 白模（clay）视频落到画布「白模视频」节点（对话式落片）："
            "可先用内置 Blender MCP 建模/摆放，再 from_blender=true 让后端直连本机 Blender 渲染"
            "导出入库；或给 video_url（可访问外链/本项目直链）入库。两者都归一成本项目"
            "静态直链后写回 node_id 指定的 whiteModelVideoNode（画布需已存在该节点）；"
            "commit_slot=true 再在节点上打资产槽指针。Blender 不可达时返回 {status:'unreachable'}，"
            "不抛错。",
            {
                "project_id": _TOOLS_PAGE_PROJECT_PROP,
                "canvas_id": {"type": "string", "description": "目标画布 id。"},
                "node_id": {"type": "string", "description": "目标 whiteModelVideoNode 节点 id。"},
                "from_blender": {
                    "type": "boolean",
                    "description": "true=驱动本机 Blender 渲染导出；否则用 video_url。",
                },
                "video_url": {
                    "type": "string",
                    "description": "白模视频来源（可访问外链或本项目 /static 直链）；from_blender=false 时必填。",
                },
                "commit_slot": {
                    "type": "boolean",
                    "description": "是否在节点上打资产槽指针（committed_slot_url/slot_target）。",
                },
            },
            ["canvas_id", "node_id"],
        ),
        _handle_commit_white_model_video,
    ),
)


def register(ctx) -> None:
    for name, schema, handler in TOOLS:
        # [aipdd fork delta] VectCut 工具组不依赖主 API 凭证（服务可被单独调用），
        # 用独立检查：默认可用，VECTCUT_ENABLED=0 时隐藏。
        is_vectcut = name.startswith("vectcut_")
        for toolset in REGISTER_TOOLSETS:
            ctx.register_tool(
                name=name,
                toolset=toolset,
                schema=schema,
                handler=handler,
                check_fn=_vectcut_available if is_vectcut else _available,
                requires_env=[] if is_vectcut else ["DRAMACLAW_API_URL", "DRAMACLAW_AGENT_TOKEN"],
                description=schema["description"],
                emoji="",
            )
