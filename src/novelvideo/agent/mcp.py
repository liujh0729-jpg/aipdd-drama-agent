# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] Agent MCP：服务器配置管理 + 工具桥接。

- 配置存于 ``<agent_root>/mcp_servers.json``（用户级，随用户隔离）；
- 内置四个服务器（都 builtin=true：不可编辑、不可删除、只可停用）：
  1. AIPDD Drama（id 历史名为 ``dramaclaw``，已迁为 ``aipdd-drama``；stdio：
     ``python -m novelvideo.chat.aipdd_drama_mcp``）——把本平台 API 作为 MCP 工具；
  2. Chrome DevTools（``chrome-devtools-mcp``，驱动客户本机已装 Chrome）；
  3. 本地 ComfyUI（``python -m novelvideo.chat.comfyui_mcp``）——参考官方
     comfy-mcp 的工具语义自建，只打「设置 → 模型与渠道 › 本地 ComfyUI」配的 ComfyUI；
  4. Blender（官方 ``uvx mcp-for-blender``）——驱动本机已装的 Blender（Blender 侧
     须装 BlenderMCP 插件并 Start MCP Server，默认监听 9876）；渲染的白模（clay）
     预览视频可喂给画布「白模视频」节点作为成片参考；
- 支持 stdio / streamable_http / sse 三种 transport（与 nanobot 对齐）；
- 每次对话回合按需连接、用完即关（每个服务器独立 AsyncExitStack），
  避免长驻子进程与陈旧连接。

按产品要求，「本系统的 MCP 都归 MCP 栏目」：本模块的配置与探测接口同时服务于
MCP 栏目页面（服务器增删改查 / 测试连接），Agent 页面只显示状态摘要。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import uuid
from contextlib import AsyncExitStack
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from novelvideo.agent import store
from novelvideo.utils.chrome_cdp import browsable_cdp_url, default_cdp_url, in_container

logger = logging.getLogger("novelvideo.agent.mcp")

BUILTIN_SERVER_ID = "aipdd-drama"
# [aipdd fork delta] 品牌改名：历史用户配置里内置条目的旧 id，仅用于迁移
# enabled 开关状态（load_config），不再对外展示。
LEGACY_BUILTIN_SERVER_ID = "dramaclaw"
# [aipdd fork delta] 第二个内置：Chrome MCP（客户自带 Chrome，默认写入、不可删除）。
# 经 chrome-devtools-mcp 连接客户已安装 Chrome 的调试端口（--browserUrl），
# 不下载任何浏览器二进制；本机/桌面部署直连 127.0.0.1，Docker 部署默认走
# host.docker.internal（compose 可用 ST_CHROME_CDP_URL 覆盖）。
CHROME_SERVER_ID = "chrome-devtools"
# [aipdd fork delta] 第三个内置：本地 ComfyUI MCP。官方 comfy-mcp（
# docs.comfy.org/zh/agent-tools/mcp）靠 comfy-cli 驱动「装了它的那台机器」上的
# ComfyUI，容器里没有 comfy-cli、ComfyUI 又跑在宿主机，照搬就是一个永远连不上的
# 默认项。这里参考它的工具语义自建同位服务器（``novelvideo.chat.comfyui_mcp``，
# stdio 同进程环境），只打「设置 → 模型与渠道 › 本地 ComfyUI」配的那个地址——与网关调用
# ComfyUI 用的是同一个真源，不引入第二份地址。
COMFY_SERVER_ID = "comfyui-local"
# [aipdd fork delta] 第四个内置：Blender MCP（官方 mcp-for-blender，接本机已装的
# Blender）。Blender 侧须装 BlenderMCP 插件并点「Start MCP Server」起 socket 服务
# （默认端口 9876）；MCP 服务器把指令转发到该 socket。目标地址按环境变量覆盖，Docker
# 部署默认走 host.docker.internal（与 Chrome 同法），本机部署可设 ST_BLENDER_HOST=localhost。
BLENDER_SERVER_ID = "blender-mcp"
BUILTIN_SERVER_IDS: tuple[str, ...] = (
    BUILTIN_SERVER_ID,
    CHROME_SERVER_ID,
    COMFY_SERVER_ID,
    BLENDER_SERVER_ID,
)
CHROME_CDP_URL_ENV = "ST_CHROME_CDP_URL"
BLENDER_HOST_ENV = "ST_BLENDER_HOST"
BLENDER_PORT_ENV = "ST_BLENDER_PORT"
DEFAULT_BLENDER_PORT = "9876"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
TOOL_NAME_MAX_LEN = 64  # OpenAI 工具名长度上限
CONNECT_TIMEOUT_SECONDS = 25.0
CALL_TIMEOUT_SECONDS = 180.0
_ID_RE = re.compile(r"[^a-z0-9_-]+")


# 用户自装 stdio 服务器只继承运行所需的基础变量，不把本进程的模型 Key、
# 数据库口令等环境变量传给第三方子进程；需要额外变量时在服务器配置的 env 里显式写。
_CHILD_ENV_ALLOWLIST = frozenset(
    {
        "PATH", "PATHEXT", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
        "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR",
        "LANG", "LC_ALL", "LC_CTYPE", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
        "CONDA_PREFIX", "NODE_PATH", "NODE_OPTIONS", "NPM_CONFIG_PREFIX",
        "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
    }
)


def _child_env(server: dict[str, Any]) -> dict[str, str]:
    """stdio 子进程环境：内置服务器继承全量（要读本系统配置），自定义只给基础变量。"""
    if server.get("builtin"):
        return dict(os.environ)
    return {
        key: value
        for key, value in os.environ.items()
        if key.upper() in _CHILD_ENV_ALLOWLIST
    }


def _resolve_launch(command: str) -> tuple[str, list[str], bool]:
    """解析 stdio 启动命令 →（可执行文件、前缀参数、是否可用）。

    Windows 上 npm 全局安装的是 ``chrome-devtools-mcp.cmd``（uv 的 ``uvx`` 也可能
    是批处理包装）；``CreateProcess`` 不能直接拉起 ``.cmd/.bat``，必须经
    ``cmd.exe /d /c``，否则子进程起不来、每轮只报一个含糊的连接失败。
    POSIX 下 ``shutil.which`` 的结果原样作为可执行文件。
    """
    if not command:
        return command, [], False
    resolved = shutil.which(command)
    if not resolved:
        return command, [], False
    if os.name == "nt" and resolved.lower().endswith((".cmd", ".bat")):
        comspec = os.environ.get("COMSPEC", "").strip() or "cmd.exe"
        return comspec, ["/d", "/c", resolved], True
    return resolved, [], True


def _loopback_url(url: str) -> str:
    """把内部 API 地址收敛到回环主机（保留端口与路径）。

    内置 MCP 以 ``DRAMACLAW_CE_OWNER=1`` 无令牌回访平台，子进程侧
    ``_enforce_ce_owner_target`` 只接受回环地址；若把 LAN 地址原样传下去，
    每次工具调用都会被拒。端口跟着原地址走，不假设 8780。
    """
    cleaned = (url or "").strip()
    if not cleaned:
        return cleaned
    try:
        parsed = urlsplit(cleaned)
        host = (parsed.hostname or "").strip().lower()
        port = parsed.port
    except ValueError:
        return cleaned
    if host in _LOOPBACK_HOSTS:
        return cleaned
    netloc = f"127.0.0.1:{port}" if port is not None else "127.0.0.1"
    return urlunsplit((parsed.scheme or "http", netloc, parsed.path, parsed.query, parsed.fragment))


class McpConfigError(ValueError):
    """MCP 配置非法（消息面向用户可读）。"""


class McpServerError(RuntimeError):
    """MCP 连接或调用失败。"""


def _internal_api_url() -> str:
    """本平台内部 API 基址（内置 MCP 服务器通过它回访平台）。

    解析顺序与 chat/hermes_pool 保持一致，但独立实现，避免模块耦合。
    """
    explicit = (
        os.environ.get("AIPDD_DRAMA_API_URL", "").strip()
        or os.environ.get("DRAMACLAW_API_URL", "").strip()
    )
    if explicit:
        return explicit.rstrip("/")
    dedicated = os.environ.get("NOVELVIDEO_API_URL", "").strip()
    if dedicated:
        return dedicated.rstrip("/")
    api_port = os.environ.get("NOVELVIDEO_API_PORT", "").strip()
    if api_port:
        host = os.environ.get("NOVELVIDEO_API_HOST", "127.0.0.1").strip() or "127.0.0.1"
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        return f"http://{host}:{api_port}"
    return "http://127.0.0.1:8780"


def _builtin_server() -> dict[str, Any]:
    """内置 AIPDD Drama 服务器定义（运行时字段随当前进程动态生成）。"""
    api_url = _loopback_url(_internal_api_url())
    return {
        "id": BUILTIN_SERVER_ID,
        "name": "AIPDD Drama MCP",
        "description": "内置：把本平台能力（项目/素材/生成任务等）作为 MCP 工具提供给 Agent。",
        "transport": "stdio",
        "command": sys.executable or "python",
        "args": ["-m", "novelvideo.chat.aipdd_drama_mcp"],
        "env": {
            "AIPDD_DRAMA_API_URL": api_url,
            "DRAMACLAW_API_URL": api_url,
            "DRAMACLAW_CE_OWNER": "1",
        },
        "url": "",
        "headers": {},
        "enabled": True,
        "builtin": True,
        "available": True,
        "unavailable_reason": "",
        "created_at": "",
        "updated_at": "",
    }


def _chrome_cdp_url() -> str:
    """客户自带 Chrome 的调试端口地址（Chrome MCP 与抖音文案提取共用）。"""
    raw = os.environ.get(CHROME_CDP_URL_ENV, "").strip() or default_cdp_url()
    # [aipdd fork delta] Chrome 只接受 IP / localhost 的 Host 头，容器里
    # host.docker.internal 这类主机名会被直接拒（HTTP 500），统一解析成 IP。
    return browsable_cdp_url(raw)


def _chrome_server() -> dict[str, Any]:
    """内置 Chrome MCP 服务器定义（运行时地址随当前进程动态生成）。

    连接客户本机已安装的 Chrome（需以 --remote-debugging-port=9222 启动），
    地址按 ST_CHROME_CDP_URL 解析；容器缺省 host.docker.internal:9222，
    本机部署缺省 127.0.0.1:9222。
    """
    command = "chrome-devtools-mcp"
    executable, prefix_args, available = _resolve_launch(command)
    return {
        "id": CHROME_SERVER_ID,
        "name": "Chrome MCP",
        "description": (
            "内置：通过 Chrome DevTools 协议驱动你本机已装的 Chrome，"
            "让 Agent 直接读写网页。需以 --remote-debugging-port=9222 "
            "启动 Chrome；连接地址可用环境变量 ST_CHROME_CDP_URL 覆盖。"
        ),
        "transport": "stdio",
        "command": executable,
        "args": [*prefix_args, "--browserUrl", _chrome_cdp_url()],
        "env": {},
        "url": "",
        "headers": {},
        "enabled": True,
        "builtin": True,
        "available": available,
        "unavailable_reason": ""
        if available
        else (
            "未检测到 chrome-devtools-mcp 命令：请先安装 Node.js 20+，"
            "再执行 npm install -g chrome-devtools-mcp"
        ),
        "created_at": "",
        "updated_at": "",
    }


def _comfyui_base_url() -> str:
    """本地 ComfyUI 地址：直接读 comfyui 渠道的 baseUrl（唯一真源，不另开 env）。

    地址只在「设置 → 模型与渠道 › 本地 ComfyUI」一处维护；子进程自己也能从 settings.db
    解析，因此这里解析不到（渠道还没配）也不会让 MCP 起不来——工具调用时会返回
    「地址未配置 + 去哪儿配」的可读错误。
    """
    try:
        from novelvideo.model_gateway_settings import get_newapi_provider_channel

        channel = get_newapi_provider_channel("comfyui") or {}
        return str(channel.get("baseUrl") or "").strip().rstrip("/")
    except Exception:  # noqa: BLE001 - 解析失败按未配置处理
        return ""


def _comfy_server() -> dict[str, Any]:
    """内置本地 ComfyUI MCP 服务器定义（地址随当前渠道配置动态生成）。"""
    return {
        "id": COMFY_SERVER_ID,
        "name": "ComfyUI MCP",
        "description": (
            "内置：驱动你本地那台 ComfyUI（读写工作流、查已装节点与模型、"
            "校验与跑一次任务）。目标地址就是「设置 → 模型与渠道 › 本地 ComfyUI」里的"
            " ComfyUI 地址；ComfyUI 没启动或未配地址时，工具会直接告诉你要去哪配。"
            "本系统不代管 ComfyUI 的安装与启动。"
        ),
        "transport": "stdio",
        "command": sys.executable or "python",
        "args": ["-m", "novelvideo.chat.comfyui_mcp"],
        "env": {"COMFYUI_BASE_URL": _comfyui_base_url()},
        "url": "",
        "headers": {},
        "enabled": True,
        "builtin": True,
        "available": True,
        "unavailable_reason": "",
        "created_at": "",
        "updated_at": "",
    }


def _blender_connection() -> tuple[str, str]:
    """本机 Blender socket 地址：ST_BLENDER_HOST / ST_BLENDER_PORT 覆盖。

    容器默认 host.docker.internal:9876；本机部署（exe/dmg）默认 127.0.0.1:9876。
    """
    default_host = "host.docker.internal" if in_container() else "127.0.0.1"
    host = os.environ.get(BLENDER_HOST_ENV, "").strip() or default_host
    port = os.environ.get(BLENDER_PORT_ENV, "").strip() or DEFAULT_BLENDER_PORT
    return host, port


def _blender_server() -> dict[str, Any]:
    """内置 Blender MCP 服务器定义（连接本机 Blender，地址随环境动态生成）。"""
    host, port = _blender_connection()
    command = "uvx"
    executable, prefix_args, available = _resolve_launch(command)
    return {
        "id": BLENDER_SERVER_ID,
        "name": "Blender MCP",
        "description": (
            "内置：驱动你本机已安装的 Blender（官方 mcp-for-blender）。需在 Blender "
            "里装 BlenderMCP 插件并点「Start MCP Server」（默认监听 9876）；建模、"
            "材质、渲染等由 AI 直接操作 Blender，渲染出的白模（clay）预览视频可喂给"
            "画布「白模视频」节点作为成片参考。连接地址可用 ST_BLENDER_HOST / "
            "ST_BLENDER_PORT 覆盖（Docker 默认 host.docker.internal:9876，本机部署设 "
            "localhost）。本系统不代管 Blender 的安装与启动。"
        ),
        "transport": "stdio",
        "command": executable,
        "args": [*prefix_args, "mcp-for-blender"],
        "env": {"BLENDER_HOST": host, "BLENDER_PORT": port},
        "url": "",
        "headers": {},
        "enabled": True,
        "builtin": True,
        "available": available,
        "unavailable_reason": ""
        if available
        else (
            "未检测到 uvx 命令：请先安装 uv（https://docs.astral.sh/uv/），"
            "mcp-for-blender 由 uvx 启动"
        ),
        "created_at": "",
        "updated_at": "",
    }


def _clean_id(raw: str) -> str:
    cleaned = _ID_RE.sub("-", str(raw or "").strip().lower()).strip("-_")
    return cleaned[:48]


def _normalize_server(raw: dict[str, Any]) -> dict[str, Any]:
    """把一条服务器配置收敛成固定字段结构（未知字段丢弃）。"""
    transport = str(raw.get("transport") or "stdio").strip().lower()
    if transport in ("streamable_http", "streamable-http", "http"):
        transport = "http"
    if transport not in ("stdio", "http", "sse"):
        raise McpConfigError(f"不支持的 transport：{transport}")
    env = raw.get("env") if isinstance(raw.get("env"), dict) else {}
    headers = raw.get("headers") if isinstance(raw.get("headers"), dict) else {}
    args = raw.get("args") if isinstance(raw.get("args"), list) else []
    return {
        "id": _clean_id(str(raw.get("id") or "")) or ("mcp-" + uuid.uuid4().hex[:8]),
        "name": str(raw.get("name") or "").strip()[:64] or "MCP 服务器",
        "description": str(raw.get("description") or "").strip()[:300],
        "transport": transport,
        "command": str(raw.get("command") or "").strip(),
        "args": [str(item) for item in args][:32],
        "env": {str(key): str(value) for key, value in env.items()},
        "url": str(raw.get("url") or "").strip(),
        "headers": {str(key): str(value) for key, value in headers.items()},
        "enabled": bool(raw.get("enabled", True)),
        "builtin": bool(raw.get("builtin", False)),
        "created_at": str(raw.get("created_at") or ""),
        "updated_at": str(raw.get("updated_at") or ""),
    }


def _validate_transport_fields(server: dict[str, Any]) -> None:
    if server["transport"] == "stdio":
        if not server["command"]:
            raise McpConfigError("stdio 服务器必须填写 command")
    elif not server["url"]:
        raise McpConfigError(f"{server['transport']} 服务器必须填写 url")


def load_config(username: str) -> dict[str, Any]:
    """读用户 MCP 配置；内置条目强制存在，运行时字段每次刷新。"""
    path = store.mcp_servers_file(username)
    data: dict[str, Any] = {}
    if path.exists():
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                data = parsed
        except (OSError, ValueError):
            logger.warning("agent mcp config unreadable, falling back to defaults")
    raw_servers = data.get("servers") if isinstance(data.get("servers"), list) else []
    builtin_states: dict[str, dict[str, Any]] = {}
    servers: list[dict[str, Any]] = []
    for item in raw_servers:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        item_id = str(item.get("id"))
        if item_id == LEGACY_BUILTIN_SERVER_ID:
            # 品牌改名迁移：旧内置条目的 enabled 状态迁到新 id。
            builtin_states.setdefault(BUILTIN_SERVER_ID, item)
            continue
        if item_id in BUILTIN_SERVER_IDS:
            builtin_states[item_id] = item
            continue
        try:
            servers.append(_normalize_server(item))
        except McpConfigError:
            logger.warning("agent mcp server skipped: id=%s", item.get("id"))
    builtins: list[dict[str, Any]] = []
    for definition in (
        _builtin_server(),
        _chrome_server(),
        _comfy_server(),
        _blender_server(),
    ):
        state = builtin_states.get(definition["id"])
        if state is not None:
            definition["enabled"] = bool(state.get("enabled", definition["enabled"]))
            definition["created_at"] = str(state.get("created_at") or "")
            definition["updated_at"] = str(state.get("updated_at") or "")
        builtins.append(definition)
    return {"servers": [*builtins, *servers]}


def save_config(username: str, config: dict[str, Any]) -> None:
    path = store.mcp_servers_file(username)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_servers(username: str) -> list[dict[str, Any]]:
    return load_config(username)["servers"]


def get_server(username: str, server_id: str) -> dict[str, Any] | None:
    return next(
        (item for item in list_servers(username) if item["id"] == server_id),
        None,
    )


def enabled_servers(username: str) -> list[dict[str, Any]]:
    """本轮实际要连接的服务器：启用且本机可用（不可用者不白起子进程）。

    不可用状态随每次调用重新探测，用户装好外部命令后无需改配置即可生效。
    """
    return [
        item
        for item in list_servers(username)
        if item.get("enabled") and item.get("available", True)
    ]


def upsert_server(username: str, payload: dict[str, Any]) -> dict[str, Any]:
    """新增或更新一个服务器（内置条目只允许改 enabled，走 set_server_enabled）。"""
    target_id = _clean_id(str(payload.get("id") or ""))
    if target_id in BUILTIN_SERVER_IDS:
        raise McpConfigError("内置服务器不可编辑，只能启用/禁用")
    config = load_config(username)
    servers = config["servers"]
    existing = next((item for item in servers if item["id"] == target_id), None) if target_id else None
    now = store.now_iso()
    if existing is None:
        merged = _normalize_server({**payload, "id": target_id, "builtin": False})
        _validate_transport_fields(merged)
        merged["created_at"] = now
        merged["updated_at"] = now
        servers.append(merged)
        logger.info("agent mcp server created: id=%s transport=%s", merged["id"], merged["transport"])
    else:
        merged = _normalize_server({**existing, **payload, "id": existing["id"], "builtin": False})
        _validate_transport_fields(merged)
        merged["created_at"] = existing.get("created_at") or now
        merged["updated_at"] = now
        servers[servers.index(existing)] = merged
        logger.info("agent mcp server updated: id=%s", merged["id"])
    save_config(username, config)
    return merged


def delete_server(username: str, server_id: str) -> bool:
    if server_id in BUILTIN_SERVER_IDS:
        raise McpConfigError("内置服务器不可删除，可以禁用")
    config = load_config(username)
    servers = config["servers"]
    kept = [item for item in servers if item["id"] != server_id]
    if len(kept) == len(servers):
        return False
    config["servers"] = kept
    save_config(username, config)
    return True


def set_server_enabled(username: str, server_id: str, enabled: bool) -> dict[str, Any] | None:
    config = load_config(username)
    target = next((item for item in config["servers"] if item["id"] == server_id), None)
    if target is None:
        return None
    target["enabled"] = bool(enabled)
    target["updated_at"] = store.now_iso()
    save_config(username, config)
    return target


def reset_servers(username: str) -> list[dict[str, Any]]:
    """恢复默认：只保留内置服务器（enabled 重置为 True）。"""
    config = {
        "servers": [
            _builtin_server(),
            _chrome_server(),
            _comfy_server(),
            _blender_server(),
        ]
    }
    save_config(username, config)
    return load_config(username)["servers"]


# ---------------------------------------------------------------------------
# 工具桥接：把启用的 MCP 服务器聚合成一次对话回合可用的工具集
# ---------------------------------------------------------------------------


def sanitize_tool_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "")).strip("_")
    return cleaned or "tool"


def qualified_tool_name(server_id: str, tool_name: str) -> str:
    """生成注入模型的工具名：``mcp__<server>__<tool>``，超长时用哈希缩短。"""
    sid = sanitize_tool_name(server_id)
    name = sanitize_tool_name(tool_name)
    candidate = f"mcp__{sid}__{name}"
    if len(candidate) <= TOOL_NAME_MAX_LEN:
        return candidate
    digest = hashlib.sha1(candidate.encode("utf-8")).hexdigest()[:10]
    budget = TOOL_NAME_MAX_LEN - len("mcp____") - len(digest)
    short_server = sid[: max(1, budget // 2)]
    short_tool = name[: max(1, budget - len(short_server))]
    return f"mcp__{short_server}__{short_tool}__{digest}"[:TOOL_NAME_MAX_LEN]


class McpToolHub:
    """一次对话回合内的 MCP 工具集：连接 → 列工具 → 调用 → 统一关闭。

    单个服务器连接失败只记入 ``errors``，不影响其它服务器与整轮对话；
    工具名冲突（sanitize 后撞名）时保留先连接的。
    """

    def __init__(
        self,
        servers: list[dict[str, Any]],
        *,
        env_overrides: dict[str, dict[str, str]] | None = None,
        connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
        call_timeout: float = CALL_TIMEOUT_SECONDS,
    ) -> None:
        self._servers = servers
        self._env_overrides = env_overrides or {}
        self._connect_timeout = connect_timeout
        self._call_timeout = call_timeout
        self._stacks: list[AsyncExitStack] = []
        self._sessions: dict[str, ClientSession] = {}
        self.tools: list[dict[str, Any]] = []
        self.errors: list[dict[str, str]] = []

    async def __aenter__(self) -> "McpToolHub":
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def start(self) -> None:
        for server in self._servers:
            if not server.get("enabled", True):
                continue
            if not server.get("available", True):
                # 可用性门控（直接构造 hub 的调用方也走同一闸门）。
                self.errors.append(
                    {
                        "server_id": str(server.get("id")),
                        "error": str(server.get("unavailable_reason") or "本机缺少启动命令"),
                    }
                )
                continue
            stack = AsyncExitStack()
            try:
                # [aipdd fork delta] 连接超时用 asyncio.timeout（当前任务内取消），
                # 不用 asyncio.wait_for / anyio.fail_after：
                # 1) wait_for 会把连接放进独立任务，成功建立的 anyio 上下文与
                #    主任务分离，取消/关闭时跨任务退出 cancel scope 会抛
                #    "Attempted to exit cancel scope in a different task"；
                # 2) fail_after 的取消 scope 与「连接成功后继续存活」的 stdio
                #    上下文不满足 LIFO 退出，退出时必抛 "Attempted to exit a
                #    cancel scope that isn't the current task's current cancel
                #    scope"——连接被误判失败，且随后的跨任务 shield 关闭会
                #    泄漏取消状态，整轮回合报「回合已中断」。
                # asyncio.timeout 无 scope 栈参与：超时取消落在本任务内，随后
                # 的同任务清理即完成收尾。
                async with asyncio.timeout(self._connect_timeout):
                    await self._connect_with_stack(stack, server)
            except TimeoutError:
                await self._close_stack_quiet(stack)
                message = f"连接超时（>{self._connect_timeout:g}s）"
                logger.warning(
                    "agent mcp server connect failed: id=%s error=%s",
                    server.get("id"),
                    message,
                )
                self.errors.append({"server_id": str(server.get("id")), "error": message})
            except asyncio.CancelledError:
                # 回合被外部取消：同任务尽力收尾后保持取消语义继续传播。
                await self._close_stack_quiet(stack)
                raise
            except Exception as exc:  # noqa: BLE001 - 单点失败不阻断整轮
                await self._close_stack_quiet(stack)
                logger.warning(
                    "agent mcp server connect failed: id=%s error=%s",
                    server.get("id"),
                    exc,
                )
                self.errors.append({"server_id": str(server.get("id")), "error": str(exc)})
            else:
                self._stacks.append(stack)

    async def _connect_with_stack(self, stack: AsyncExitStack, server: dict[str, Any]) -> None:
        server_id = str(server.get("id"))
        transport = str(server.get("transport") or "stdio")
        if transport == "stdio":
            env = _child_env(server)
            env.update(server.get("env") or {})
            env.update(self._env_overrides.get(server_id) or {})
            params = StdioServerParameters(
                command=str(server.get("command") or ""),
                args=list(server.get("args") or []),
                env=env,
            )
            read_stream, write_stream = await stack.enter_async_context(stdio_client(params))
        elif transport == "http":
            url = str(server.get("url") or "")
            headers = server.get("headers") or None
            read_stream, write_stream, _get_session_id = await stack.enter_async_context(
                streamablehttp_client(url, headers=headers)
            )
        else:  # sse
            url = str(server.get("url") or "")
            headers = server.get("headers") or None
            read_stream, write_stream = await stack.enter_async_context(
                sse_client(url, headers=headers)
            )
        session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
        await session.initialize()
        listing = await session.list_tools()
        self._sessions[server_id] = session
        known = {tool["qualified_name"] for tool in self.tools}
        for tool in listing.tools:
            qualified = qualified_tool_name(server_id, tool.name)
            if qualified in known:
                continue
            known.add(qualified)
            schema = tool.inputSchema if isinstance(tool.inputSchema, dict) else {"type": "object"}
            self.tools.append(
                {
                    "server_id": server_id,
                    "server_name": str(server.get("name") or server_id),
                    "remote_name": tool.name,
                    "qualified_name": qualified,
                    "description": str(tool.description or ""),
                    "input_schema": schema,
                }
            )
        logger.info(
            "agent mcp server connected: id=%s tools=%d",
            server_id,
            len(listing.tools),
        )

    def resolve_tool(self, name: str) -> dict[str, Any] | None:
        """按全名或唯一裸名解析工具。

        注入模型的工具名带 ``mcp__<server>__`` 前缀，而技能文档/用户习惯写裸名
        （``aipdd_drama_get_plan``）。裸名只在恰好命中一个工具时放行，避免不同
        服务器同名工具被误调。
        """
        tool = next((item for item in self.tools if item["qualified_name"] == name), None)
        if tool is not None:
            return tool
        matches = [item for item in self.tools if item["remote_name"] == name]
        return matches[0] if len(matches) == 1 else None

    async def call(self, qualified_name: str, arguments: dict[str, Any] | None) -> str:
        """调用一个 MCP 工具，返回文本结果（错误抛 McpServerError）。"""
        tool = self.resolve_tool(qualified_name)
        if tool is None:
            raise McpServerError(f"未知的 MCP 工具：{qualified_name}")
        session = self._sessions.get(tool["server_id"])
        if session is None:
            raise McpServerError(f"MCP 服务器未连接：{tool['server_id']}")
        try:
            result = await asyncio.wait_for(
                session.call_tool(tool["remote_name"], arguments or {}),
                timeout=self._call_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise McpServerError(f"MCP 工具调用超时：{tool['remote_name']}") from exc
        parts: list[str] = []
        for item in getattr(result, "content", []) or []:
            text = getattr(item, "text", None)
            if text is not None:
                parts.append(str(text))
        text = "\n".join(parts).strip()
        if getattr(result, "isError", False):
            raise McpServerError(text or f"MCP 工具返回错误：{tool['remote_name']}")
        return text

    async def _close_stack(self, stack: AsyncExitStack) -> None:
        try:
            await stack.aclose()
        except BaseException:  # noqa: BLE001 - 关闭失败不影响其它连接清理
            logger.debug("agent mcp stack close failed", exc_info=True)

    async def _close_stack_quiet(self, stack: AsyncExitStack) -> None:
        """[aipdd fork delta] 尽力关闭连接栈：取消/超时残留的一切异常都吸收。

        必须在「进入上下文的任务内」直接关闭 —— MCP SDK 的连接上下文基于
        anyio task group / cancel scope，跨任务关闭（asyncio.shield / wait_for
        等独立任务）会抛 "in a different task" 并泄漏取消状态，导致本任务
        后续 await 全被取消、整轮回合报「回合已中断」。
        """
        try:
            await stack.aclose()
        except BaseException:  # noqa: BLE001 - 关闭失败不影响其它连接清理
            pass

    async def close(self) -> None:
        self._sessions.clear()
        for stack in self._stacks:
            await self._close_stack(stack)
        self._stacks.clear()


# [aipdd fork delta] 内置工具目录：不启动子进程，直接读工具表（MCP 栏目「工具」页）。
def builtin_tool_catalog() -> list[dict[str, Any]]:
    """内置 AIPDD Drama 服务器的工具目录（清单 + 字段 schema）。

    与 stdio MCP 服务器（``aipdd_drama_mcp.list_tools``）同源：同取
    ``dramaclaw_tools.TOOLS`` 的 ``(name, schema, handler)`` 三元组，
    保证「工具」页展示与 Agent 实际可用工具一致。
    """
    from novelvideo.chat import dramaclaw_tools

    catalog: list[dict[str, Any]] = []
    for entry in getattr(dramaclaw_tools, "TOOLS", ()):
        if not isinstance(entry, tuple) or len(entry) != 3:
            continue
        name, schema, handler = entry
        if not (isinstance(name, str) and isinstance(schema, dict) and callable(handler)):
            continue
        parameters = schema.get("parameters")
        catalog.append(
            {
                "name": name,
                "description": str(schema.get("description") or ""),
                "input_schema": parameters if isinstance(parameters, dict) else {"type": "object"},
            }
        )
    return catalog


async def probe_server(
    server: dict[str, Any],
    *,
    env_overrides: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """探测单个服务器：连接并列出工具（MCP 栏目的「测试连接」）。"""
    hub = McpToolHub([server], env_overrides=env_overrides, connect_timeout=15.0)
    await hub.start()
    await hub.close()
    if hub.errors:
        return {"ok": False, "error": hub.errors[0].get("error", "连接失败")}
    return {
        "ok": True,
        "tools": [
            {
                "name": tool["remote_name"],
                "qualified_name": tool["qualified_name"],
                "description": tool["description"],
                "input_schema": tool["input_schema"],
            }
            for tool in hub.tools
        ],
    }