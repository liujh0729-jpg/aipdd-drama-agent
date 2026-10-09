# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 本地 Agent 工具接入：把内置 AIPDD Drama MCP server 写进用户
本机安装的 AI 客户端配置（Workbuddy / Claude Code / Cursor / Codex）。

链路（本地 Docker 部署）：
- 宿主 home 由 compose 挂进 api 容器（``${HOME}:/host-home``），本模块只在
  ``HOST_HOME_PATH``（缺省 ``/host-home``）之下探测与读写目标工具配置；
- installed：目标工具在本机的安装证据，分两级——
  ``app``（应用本体 / CLI 命中：宿主 home 内的用户级安装位置，或宿主应用目录
  挂载 ``HOST_APPS_PATH`` 下的 *.app / 安装目录——compose 把 macOS 的
  /Applications 只读挂进来）；``config``（仅数据目录 / 配置文件存在，
  可能是卸载后的残留——旧口径误把残留当「已安装」，见 req20）。
  installed 为真 = 任一级命中（连接闸门不收紧：真装用户不因探测盲区被挡）；
  UI 按 evidence 分级展示（app →「已安装」；config →「检测到配置」）。
- connect：由路由层生成一把统一密钥（st-…，机器凭证），本模块向目标配置写入
  stdio 形态的 aipdd-drama 条目——``docker exec -i aipdd-drama-api python -m
  novelvideo.chat.aipdd_drama_mcp``，在 api 容器内启动 MCP server，API 地址取
  容器内 loopback；写入时同步清理品牌改名前的旧 ``dramaclaw`` 条目，避免双份；
- disconnect：仅移除配置条目（密钥吊销由路由层负责）；
- connected：目标配置文件里存在 aipdd-drama 条目（以文件为准，不另存状态；
  旧 dramaclaw 条目视同已连接）。

写入前一律 ``.bak`` 备份原文件；JSON 用原子替换，TOML 只增删本模块自己管理的
``[mcp_servers.aipdd-drama]``（含旧 ``[mcp_servers.dramaclaw]``）段落，不改动其余内容。
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

HOST_HOME_ENV = "HOST_HOME_PATH"
DEFAULT_HOST_HOME = "/host-home"
# 宿主应用目录在容器内的只读挂载点（macOS /Applications → /host-applications）。
# 未挂载 / 不存在时「应用本体」级证据自动跳过，退回 home 内候选与配置痕迹。
HOST_APPS_ENV = "HOST_APPS_PATH"
DEFAULT_HOST_APPS = "/host-applications"

# 写入各客户端配置的 server key（mcpServers.aipdd-drama / [mcp_servers.aipdd-drama]）。
SERVER_KEY = "aipdd-drama"
# [aipdd fork delta] 品牌改名前的旧 server key：连接时顺手清理，断开/检测兼容存量。
LEGACY_SERVER_KEY = "dramaclaw"
# compose 里 api 服务的 container_name（docker exec 目标）。
CONTAINER_NAME = "aipdd-drama-api"
# MCP server 与 api 同容器，容器内 loopback 即 api 自身。
API_INNER_URL = "http://127.0.0.1:8780"
# MCP server 的启动模块（与内置 aipdd-drama server 同源）。
MCP_MODULE = "novelvideo.chat.aipdd_drama_mcp"

# 每行一个工具定义：
# - config_rel：相对宿主 home 的配置路径；
# - format：json（mcpServers 对象）/ toml（[mcp_servers.x] 段落）；
# - detect：配置痕迹（任一存在 → "config" 级证据，("dir"|"file") 区分类型）；
# - apps：应用本体候选（相对宿主 home 的用户级安装位置 → "app" 级证据）；
# - apps_system：应用本体候选（相对宿主应用目录挂载，macOS 系统级 *.app）。
TOOL_DEFS: tuple[dict[str, Any], ...] = (
    {
        "id": "workbuddy",
        "name": "Workbuddy",
        "config_rel": ".workbuddy/mcp.json",
        "format": "json",
        "detect": ((".workbuddy", "dir"),),
        "apps": (("Applications/WorkBuddy.app", "dir"), ("AppData/Local/Programs/WorkBuddy", "dir")),
        "apps_system": (("WorkBuddy.app", "dir"),),
    },
    {
        "id": "claude",
        "name": "Claude Code",
        "config_rel": ".claude.json",
        "format": "json",
        "detect": ((".claude.json", "file"), (".claude", "dir")),
        # Claude Code 是 CLI：官方安装器落 ~/.local/bin/claude，旧安装器落 ~/.claude/local/claude。
        "apps": ((".local/bin/claude", "file"), (".claude/local/claude", "file")),
        "apps_system": (),
    },
    {
        "id": "cursor",
        "name": "Cursor",
        "config_rel": ".cursor/mcp.json",
        "format": "json",
        "detect": ((".cursor", "dir"),),
        "apps": (
            ("Applications/Cursor.app", "dir"),
            ("AppData/Local/Programs/cursor", "dir"),
            (".local/bin/cursor-agent", "file"),
        ),
        "apps_system": (("Cursor.app", "dir"),),
    },
    {
        "id": "codex",
        "name": "Codex (ChatGPT)",
        "config_rel": ".codex/config.toml",
        "format": "toml",
        "detect": ((".codex/config.toml", "file"),),
        "apps": (
            (".cache/codex-runtimes", "dir"),
            ("AppData/Local/Programs/codex", "dir"),
            (".local/bin/codex", "file"),
        ),
        "apps_system": (("Codex.app", "dir"),),
    },
)

_TOML_HEADER_RE = re.compile(r"(?m)^\[mcp_servers\.(?:aipdd-drama|dramaclaw)\]\s*$")
_TOML_SECTION_RE = re.compile(r"(?m)^\[")


def host_home() -> Path:
    """宿主 home 在容器内的挂载点（compose 挂载；测试可经 env 覆盖）。"""
    raw = os.environ.get(HOST_HOME_ENV, "").strip()
    return Path(raw) if raw else Path(DEFAULT_HOST_HOME)


def host_apps() -> Path:
    """宿主应用目录挂载点（macOS 的 /Applications 只读挂载；测试可经 env 覆盖）。"""
    raw = os.environ.get(HOST_APPS_ENV, "").strip()
    return Path(raw) if raw else Path(DEFAULT_HOST_APPS)


def _tool_config_path(home: Path, definition: dict[str, Any]) -> Path:
    return home / str(definition["config_rel"])


def _probe(root: Path, entries: Any) -> bool:
    for rel, kind in entries:
        target = root / rel
        if kind == "dir" and target.is_dir():
            return True
        if kind == "file" and target.is_file():
            return True
    return False


def _has_config_evidence(home: Path, definition: dict[str, Any]) -> bool:
    """数据目录 / 配置文件存在（可能为卸载残留——仅 "config" 级证据）。"""
    return _probe(home, definition["detect"])


def _has_app_evidence(home: Path, definition: dict[str, Any]) -> bool:
    """应用本体 / CLI 命中（用户级安装位置 + 宿主应用目录挂载）。"""
    if _probe(home, definition.get("apps") or ()):
        return True
    apps_root = host_apps()
    if apps_root.is_dir():
        return _probe(apps_root, definition.get("apps_system") or ())
    return False


def _install_evidence(home: Path, definition: dict[str, Any]) -> str:
    """"app" / "config" / "none"（见模块 docstring 的证据分级）。"""
    if _has_app_evidence(home, definition):
        return "app"
    if _has_config_evidence(home, definition):
        return "config"
    return "none"


def _display_path(definition: dict[str, Any]) -> str:
    """宿主视角的展示路径（``~/.workbuddy/mcp.json``）。"""
    return "~/" + str(definition["config_rel"])


def _read_text_safe(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None


def _backup(path: Path) -> None:
    if path.exists():
        try:
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        except OSError:
            pass


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ─── JSON（mcpServers 对象）────────────────────────────────────────────────────


def _read_json_object(path: Path) -> dict[str, Any]:
    raw = _read_text_safe(path)
    if raw is None or not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"config root is not an object: {path}")
    return data


def json_has_server(path: Path) -> bool:
    try:
        data = _read_json_object(path)
    except (OSError, ValueError):
        return False
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return False
    return SERVER_KEY in servers or LEGACY_SERVER_KEY in servers


def json_write_server(path: Path, entry: dict[str, Any]) -> None:
    data = _read_json_object(path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
        data["mcpServers"] = servers
    # 品牌改名迁移：写入新条目的同时移除旧 dramaclaw 条目，避免客户端里双份。
    servers.pop(LEGACY_SERVER_KEY, None)
    servers[SERVER_KEY] = entry
    _backup(path)
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def json_remove_server(path: Path) -> None:
    data = _read_json_object(path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return
    changed = False
    for key in (SERVER_KEY, LEGACY_SERVER_KEY):
        if key in servers:
            servers.pop(key)
            changed = True
    if not changed:
        return
    _backup(path)
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


# ─── TOML（[mcp_servers.aipdd-drama] 段落）──────────────────────────────────


def _toml_remove_block(text: str) -> str:
    # 循环删除：品牌改名后旧 dramaclaw / 新 aipdd-drama 段落可能同时存在，
    # 必须全部清掉才能避免双份。
    while True:
        match = _TOML_HEADER_RE.search(text)
        if match is None:
            return text
        rest = text[match.end():]
        nxt = _TOML_SECTION_RE.search(rest)
        end = match.end() + (nxt.start() if nxt else len(rest))
        text = text[: match.start()] + text[end:]
        while "\n\n\n" in text:
            text = text.replace("\n\n\n", "\n\n")


def _toml_render_block(entry: dict[str, Any]) -> str:
    args_lines = ",\n".join(f"    {json.dumps(str(item))}" for item in entry.get("args") or [])
    return (
        f"[mcp_servers.{SERVER_KEY}]\n"
        f"command = {json.dumps(str(entry.get('command') or ''))}\n"
        f"args = [\n{args_lines},\n]\n"
    )


def toml_has_server(path: Path) -> bool:
    raw = _read_text_safe(path)
    return bool(raw) and _TOML_HEADER_RE.search(raw) is not None


def toml_write_server(path: Path, entry: dict[str, Any]) -> None:
    raw = _read_text_safe(path) or ""
    # 品牌改名迁移：先清掉新旧两种段落，再写入新段落。
    text = _toml_remove_block(raw)
    block = _toml_render_block(entry)
    text = f"{text.rstrip()}\n\n{block}" if text.strip() else block
    _backup(path)
    _atomic_write(path, text)


def toml_remove_server(path: Path) -> None:
    raw = _read_text_safe(path)
    if raw is None:
        return
    text = _toml_remove_block(raw)
    if text == raw:
        return
    _backup(path)
    # 删除插段后收掉我们写入时留下的尾部空行（TOML 尾部空行无语义）。
    _atomic_write(path, text.rstrip("\n") + "\n")


# ─── 连接条目 / 扫描 ──────────────────────────────────────────────────────────


def connection_entry(*, raw_key: str, project_id: str = "") -> dict[str, Any]:
    """写入客户端配置的 aipdd-drama stdio 条目（docker exec 进本 api 容器）。

    env 同时注入新名 ``AIPDD_DRAMA_*`` 与旧名 ``DRAMACLAW_*``：工具实现按
    新名优先、旧名回退读取，旧版镜像/容器里的 MCP 子进程也能继续工作。
    """
    args = [
        "exec",
        "-i",
        "-e",
        f"AIPDD_DRAMA_API_URL={API_INNER_URL}",
        "-e",
        f"DRAMACLAW_API_URL={API_INNER_URL}",
        "-e",
        f"AIPDD_DRAMA_AGENT_TOKEN={raw_key}",
        "-e",
        f"DRAMACLAW_AGENT_TOKEN={raw_key}",
    ]
    if project_id:
        args += [
            "-e",
            f"AIPDD_DRAMA_PROJECT_ID={project_id}",
            "-e",
            f"DRAMACLAW_PROJECT_ID={project_id}",
        ]
    args += [CONTAINER_NAME, "python", "-m", MCP_MODULE]
    return {"command": "docker", "args": args}


def _has_server(path: Path, fmt: str) -> bool:
    if fmt == "toml":
        return toml_has_server(path)
    return json_has_server(path)


def scan(home: Path | None = None) -> list[dict[str, Any]]:
    """扫描 4 个本地工具的安装 / 连接状态（以宿主文件为准）。

    ``installed`` = 有任一安装证据（连接闸门用）；``evidence`` 供 UI 分级
    展示：``app`` →「已安装」；``config`` →「检测到配置」（可能为残留）。
    """
    root = home or host_home()
    result: list[dict[str, Any]] = []
    for definition in TOOL_DEFS:
        path = _tool_config_path(root, definition)
        evidence = _install_evidence(root, definition)
        result.append(
            {
                "id": definition["id"],
                "name": definition["name"],
                "installed": evidence != "none",
                "evidence": evidence,
                "connected": _has_server(path, str(definition["format"])),
                "configPath": _display_path(definition),
            }
        )
    return result


def write_connection(tool_id: str, entry: dict[str, Any], home: Path | None = None) -> None:
    """把 aipdd-drama 条目写入目标工具配置（未安装/未知工具抛 ValueError）。"""
    root = home or host_home()
    definition = next((item for item in TOOL_DEFS if item["id"] == tool_id), None)
    if definition is None:
        raise ValueError(f"unknown local agent tool: {tool_id}")
    if _install_evidence(root, definition) == "none":
        raise ValueError(f"tool not installed: {tool_id}")
    path = _tool_config_path(root, definition)
    if definition["format"] == "toml":
        toml_write_server(path, entry)
    else:
        json_write_server(path, entry)


def remove_connection(tool_id: str, home: Path | None = None) -> None:
    """从目标工具配置移除 aipdd-drama 条目（含旧 dramaclaw；幂等；未安装/未知工具抛 ValueError）。"""
    root = home or host_home()
    definition = next((item for item in TOOL_DEFS if item["id"] == tool_id), None)
    if definition is None:
        raise ValueError(f"unknown local agent tool: {tool_id}")
    if _install_evidence(root, definition) == "none":
        raise ValueError(f"tool not installed: {tool_id}")
    path = _tool_config_path(root, definition)
    if definition["format"] == "toml":
        toml_remove_server(path)
    else:
        json_remove_server(path)