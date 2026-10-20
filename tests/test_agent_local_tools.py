# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 本地 Agent 工具接入回归测试（MCP 栏目「本地 Agent 工具」）。

覆盖 ``novelvideo.agent.local_tools``（安装检测 / JSON 与 TOML 读写 / 条目形态）与
``novelvideo.agent.routes`` 的 local-tools 端点（列表 / 连接 / 断开）。

隔离约定：
- ``HOST_HOME_PATH`` → per-test 临时 home（模拟 compose 挂载的宿主目录）；
- ``NOVELVIDEO_AGENT_DATA_DIR`` → per-test 临时目录（连接状态文件落这里）；
- conftest 已把 ``config.STATE_DIR`` 指到临时目录（统一密钥落在隔离 auth.db）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from novelvideo.agent import local_tools as agent_local_tools
from novelvideo.agent import routes as agent_routes
from novelvideo.agent import store as agent_store
from novelvideo.agent.routes import require_api_user, require_interactive_user
from novelvideo.ports.local.users import LocalUserStore, reset_local_user_store_for_tests

USER = {"id": "u-alice", "username": "alice"}

# 简化条目：只关心「原样写入 / 原样移除」，真实形态由 connection_entry 用例覆盖。
ENTRY = {"command": "docker", "args": ["exec", "-i", "aipdd-drama-api"]}


@pytest.fixture()
def host_home(tmp_path, monkeypatch) -> Path:
    """模拟挂进 api 容器的宿主 home（HOST_HOME_PATH 指向临时目录）。"""
    home = tmp_path / "host-home"
    home.mkdir()
    monkeypatch.setenv("HOST_HOME_PATH", str(home))
    return home


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    """挂上 agent router 的测试客户端（交互式会话与用户名走依赖覆盖）。"""
    monkeypatch.setenv("NOVELVIDEO_AGENT_DATA_DIR", str(tmp_path / "agent-data"))
    # 固定 docker 形态，避免测试结果随宿主是否为容器而变化。
    monkeypatch.setenv("AIPDD_DRAMA_MCP_TRANSPORT", "docker")
    reset_local_user_store_for_tests()
    app = FastAPI()
    app.include_router(agent_routes.router, prefix="/api/v1")
    app.dependency_overrides[require_api_user] = lambda: dict(USER)
    app.dependency_overrides[require_interactive_user] = lambda: dict(USER)
    return TestClient(app)


def tool_by_id(tools: list[dict], tool_id: str) -> dict:
    return next(item for item in tools if item["id"] == tool_id)


def token_from_args(args: list[str]) -> str:
    return next(
        str(item).split("=", 1)[1]
        for item in args
        if str(item).startswith("DRAMACLAW_AGENT_TOKEN=")
    )


# ---------------------------------------------------------------------------
# local_tools：安装检测
# ---------------------------------------------------------------------------


def test_scan_reports_installed_and_connected_from_host_files(host_home):
    (host_home / ".workbuddy").mkdir()
    (host_home / ".claude.json").write_text("{}", encoding="utf-8")
    (host_home / ".cursor").mkdir()
    agent_local_tools.json_write_server(
        host_home / ".cursor" / "mcp.json",
        agent_local_tools.connection_entry(raw_key="st-cursor"),
    )
    (host_home / ".codex").mkdir()
    agent_local_tools.toml_write_server(
        host_home / ".codex" / "config.toml",
        agent_local_tools.connection_entry(raw_key="st-codex"),
    )

    tools = {item["id"]: item for item in agent_local_tools.scan()}

    assert set(tools) == {"workbuddy", "claude", "cursor", "codex"}
    assert all(item["installed"] for item in tools.values())
    # 只有配置痕迹（未建应用本体）：证据降级为 "config"，UI 不再显示「已安装」。
    assert all(item["evidence"] == "config" for item in tools.values())
    assert tools["workbuddy"]["connected"] is False
    assert tools["claude"]["connected"] is False
    assert tools["cursor"]["connected"] is True
    assert tools["codex"]["connected"] is True
    # 展示路径是宿主视角（容器挂载点在 UI 上不可见）。
    assert tools["workbuddy"]["configPath"] == "~/.workbuddy/mcp.json"
    assert tools["codex"]["configPath"] == "~/.codex/config.toml"


def test_scan_reports_all_uninstalled_on_empty_home(tmp_path):
    tools = agent_local_tools.scan(home=tmp_path)

    assert len(tools) == 4
    assert not any(item["installed"] for item in tools)
    assert all(item["evidence"] == "none" for item in tools)
    assert not any(item["connected"] for item in tools)


def test_scan_grades_app_evidence_for_user_level_installs(host_home):
    # 用户级应用本体（~/Applications/*.app、Windows 的 LOCALAPPDATA 安装目录）→ "app" 级。
    (host_home / "Applications" / "WorkBuddy.app").mkdir(parents=True)
    (host_home / ".cursor").mkdir()  # 仅残留配置 → 保持 "config" 级

    tools = {item["id"]: item for item in agent_local_tools.scan()}

    assert tools["workbuddy"]["evidence"] == "app"
    assert tools["cursor"]["evidence"] == "config"


def test_scan_grades_app_evidence_from_host_apps_mount(host_home, tmp_path, monkeypatch):
    # macOS 系统级安装：compose 把宿主 /Applications 只读挂进容器（HOST_APPS_PATH）。
    (host_home / ".cursor").mkdir()  # req20 场景：卸载后残留 ~/.cursor，应用本体已不在
    apps = tmp_path / "Applications"
    (apps / "WorkBuddy.app").mkdir(parents=True)
    monkeypatch.setenv("HOST_APPS_PATH", str(apps))

    tools = {item["id"]: item for item in agent_local_tools.scan()}

    assert tools["workbuddy"]["evidence"] == "app"
    # Cursor.app 不在挂载目录：证据降级为 config；连接闸门保持（config 级仍可连）。
    assert tools["cursor"]["evidence"] == "config"
    assert tools["cursor"]["installed"] is True


def test_scan_skips_app_mount_when_host_apps_path_missing(host_home, tmp_path, monkeypatch):
    monkeypatch.setenv("HOST_APPS_PATH", str(tmp_path / "missing-apps"))
    (host_home / ".codex").mkdir()
    (host_home / ".codex" / "config.toml").write_text("", encoding="utf-8")

    tools = {item["id"]: item for item in agent_local_tools.scan()}

    # 应用目录未挂载：该级证据自动跳过，退回 home 内候选与配置痕迹。
    assert tools["codex"]["evidence"] == "config"


# ---------------------------------------------------------------------------
# local_tools：JSON 读写
# ---------------------------------------------------------------------------


def test_json_write_merges_servers_backs_up_and_removes(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps({"mcpServers": {"other": {"command": "other"}}, "theme": "dark"}),
        encoding="utf-8",
    )

    agent_local_tools.json_write_server(path, ENTRY)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mcpServers"]["aipdd-drama"] == ENTRY
    # 别人的条目与无关顶层键不能动。
    assert data["mcpServers"]["other"] == {"command": "other"}
    assert data["theme"] == "dark"
    assert agent_local_tools.json_has_server(path) is True
    # 覆盖写前留一份 .bak（内容是写入前的原文）。
    backup = json.loads((tmp_path / "mcp.json.bak").read_text(encoding="utf-8"))
    assert "aipdd-drama" not in backup["mcpServers"]

    # 幂等重写：仍只有一份 aipdd-drama 条目。
    agent_local_tools.json_write_server(path, ENTRY)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert list(data["mcpServers"]).count("aipdd-drama") == 1

    agent_local_tools.json_remove_server(path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert "aipdd-drama" not in data["mcpServers"]
    assert data["mcpServers"]["other"] == {"command": "other"}
    assert agent_local_tools.json_has_server(path) is False
    # 已移除后再删是幂等空操作。
    agent_local_tools.json_remove_server(path)
    assert agent_local_tools.json_has_server(path) is False


def test_json_write_creates_missing_file_without_backup(tmp_path):
    path = tmp_path / "nested" / "mcp.json"

    agent_local_tools.json_write_server(path, ENTRY)

    assert agent_local_tools.json_has_server(path) is True
    # 首次创建没有可备份的原文件。
    assert not (path.parent / "mcp.json.bak").exists()


# ---------------------------------------------------------------------------
# local_tools：TOML 读写
# ---------------------------------------------------------------------------


def test_toml_write_preserves_other_sections_and_remove_restores(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[features]\nweb_search = true\n\n'
        '[mcp_servers.node_repl]\ncommand = "node"\nargs = ["repl"]\n',
        encoding="utf-8",
    )

    agent_local_tools.toml_write_server(path, ENTRY)

    text = path.read_text(encoding="utf-8")
    assert agent_local_tools.toml_has_server(path) is True
    assert text.count("[mcp_servers.aipdd-drama]") == 1
    # 其余段落原样保留；args 逐条渲染成合法 TOML。
    assert "[features]" in text and "web_search = true" in text
    assert "[mcp_servers.node_repl]" in text
    assert '"aipdd-drama-api"' in text

    # 幂等重写不产生第二段。
    agent_local_tools.toml_write_server(path, ENTRY)
    assert path.read_text(encoding="utf-8").count("[mcp_servers.aipdd-drama]") == 1

    agent_local_tools.toml_remove_server(path)

    text = path.read_text(encoding="utf-8")
    assert "[mcp_servers.aipdd-drama]" not in text
    assert "aipdd-drama" not in text
    assert "[features]" in text and "[mcp_servers.node_repl]" in text
    assert "\n\n\n" not in text  # 删段后不留连续空行

    # 已无该段时再删为幂等空操作（不改动文件）。
    before = path.read_text(encoding="utf-8")
    agent_local_tools.toml_remove_server(path)
    assert path.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# local_tools：条目形态与写入门禁
# ---------------------------------------------------------------------------


def test_connection_entry_wraps_docker_exec_with_credentials(monkeypatch):
    monkeypatch.setenv("AIPDD_DRAMA_MCP_TRANSPORT", "docker")
    entry = agent_local_tools.connection_entry(raw_key="st-abc", project_id="p-1")

    assert entry["command"] == "docker"
    args = entry["args"]
    assert args[0] == "exec"
    assert "AIPDD_DRAMA_AGENT_TOKEN=st-abc" in args
    assert "DRAMACLAW_AGENT_TOKEN=st-abc" in args
    assert "DRAMACLAW_PROJECT_ID=p-1" in args
    # 服务器在 api 容器内启动：容器名固定 + 模块与内置 server 同源。
    assert args[-4:] == ["aipdd-drama-api", "python", "-m", "novelvideo.chat.aipdd_drama_mcp"]

    # 未传项目号时不写 DRAMACLAW_PROJECT_ID（MCP 侧退回全局会话）。
    without_project = agent_local_tools.connection_entry(raw_key="st-abc")
    assert not any(
        str(item).startswith("DRAMACLAW_PROJECT_ID=") for item in without_project["args"]
    )


def test_connection_entry_uses_bundled_python_on_desktop(monkeypatch, tmp_path):
    """桌面版（native）：本机 Python 直启 MCP server，密钥走 env 而非 docker -e。"""
    import sys

    monkeypatch.setenv("AIPDD_DRAMA_MCP_TRANSPORT", "native")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "backend-src"))

    entry = agent_local_tools.connection_entry(raw_key="st-abc", project_id="p-1")

    assert entry["command"] == sys.executable
    assert entry["args"] == ["-m", "novelvideo.chat.aipdd_drama_mcp"]
    assert entry["env"]["AIPDD_DRAMA_AGENT_TOKEN"] == "st-abc"
    assert entry["env"]["DRAMACLAW_AGENT_TOKEN"] == "st-abc"
    assert entry["env"]["DRAMACLAW_PROJECT_ID"] == "p-1"
    assert entry["env"]["PYTHONPATH"] == str(tmp_path / "backend-src")
    assert "docker" not in entry["command"]


def test_connection_entry_omits_empty_pythonpath(monkeypatch):
    monkeypatch.setenv("AIPDD_DRAMA_MCP_TRANSPORT", "native")
    monkeypatch.delenv("PYTHONPATH", raising=False)

    entry = agent_local_tools.connection_entry(raw_key="st-abc")

    assert "PYTHONPATH" not in entry["env"]
    assert not any(key.startswith("DRAMACLAW_PROJECT_ID") for key in entry["env"])


def test_container_name_env_overrides_default(monkeypatch):
    monkeypatch.setenv("AIPDD_DRAMA_MCP_TRANSPORT", "docker")
    monkeypatch.setenv("AIPDD_DRAMA_MCP_CONTAINER", "drama-prod")

    entry = agent_local_tools.connection_entry(raw_key="st-abc")

    assert entry["args"][-4] == "drama-prod"
    assert agent_local_tools.container_name() == "drama-prod"


def test_host_home_falls_back_to_real_home_outside_container(monkeypatch, tmp_path):
    monkeypatch.delenv("HOST_HOME_PATH", raising=False)
    monkeypatch.setenv("AIPDD_DRAMA_MCP_TRANSPORT", "native")
    monkeypatch.setattr(agent_local_tools.Path, "home", classmethod(lambda cls: tmp_path))

    assert agent_local_tools.host_home() == tmp_path


def test_toml_renders_and_removes_env_subtable(tmp_path):
    """native 条目带 env：渲染成 [mcp_servers.aipdd-drama.env]，移除时一并清掉。"""
    path = tmp_path / "config.toml"
    path.write_text("[features]\nweb_search = true\n", encoding="utf-8")
    entry = {
        "command": "python",
        "args": ["-m", "novelvideo.chat.aipdd_drama_mcp"],
        "env": {"AIPDD_DRAMA_AGENT_TOKEN": "st-abc"},
    }

    agent_local_tools.toml_write_server(path, entry)

    text = path.read_text(encoding="utf-8")
    assert "[mcp_servers.aipdd-drama]" in text
    assert "[mcp_servers.aipdd-drama.env]" in text
    assert '"AIPDD_DRAMA_AGENT_TOKEN" = "st-abc"' in text
    assert agent_local_tools.toml_has_server(path) is True

    # 幂等重写：主段与 env 子段都只有一份。
    agent_local_tools.toml_write_server(path, entry)
    text = path.read_text(encoding="utf-8")
    assert text.count("[mcp_servers.aipdd-drama]") == 1
    assert text.count("[mcp_servers.aipdd-drama.env]") == 1

    agent_local_tools.toml_remove_server(path)

    text = path.read_text(encoding="utf-8")
    assert "aipdd-drama" not in text
    assert "AIPDD_DRAMA_AGENT_TOKEN" not in text
    assert "[features]" in text


def test_write_connection_guards_unknown_and_not_installed_tools(tmp_path):
    with pytest.raises(ValueError, match="unknown local agent tool"):
        agent_local_tools.write_connection("unknown", ENTRY, home=tmp_path)
    with pytest.raises(ValueError, match="tool not installed"):
        agent_local_tools.write_connection("cursor", ENTRY, home=tmp_path)

    (tmp_path / ".cursor").mkdir()
    agent_local_tools.write_connection("cursor", ENTRY, home=tmp_path)
    assert agent_local_tools.json_has_server(tmp_path / ".cursor" / "mcp.json") is True

    agent_local_tools.remove_connection("cursor", home=tmp_path)
    assert agent_local_tools.json_has_server(tmp_path / ".cursor" / "mcp.json") is False


def test_remove_connection_on_never_connected_tool_is_noop(tmp_path):
    (tmp_path / ".workbuddy").mkdir()

    agent_local_tools.remove_connection("workbuddy", home=tmp_path)

    # 没连接过：不创建空配置文件。
    assert not (tmp_path / ".workbuddy" / "mcp.json").exists()


# ---------------------------------------------------------------------------
# routes：鉴权边界
# ---------------------------------------------------------------------------


async def test_require_interactive_user_rejects_machine_credentials(monkeypatch):
    async def machine_user(request):
        return {"id": "u-alice", "username": "alice", "credential_kind": "api_key"}

    monkeypatch.setattr(agent_routes, "require_api_user", machine_user)
    with pytest.raises(HTTPException) as excinfo:
        await agent_routes.require_interactive_user(object())
    assert excinfo.value.status_code == 403

    async def browser_user(request):
        return {"id": "u-alice", "username": "alice", "credential_kind": ""}

    monkeypatch.setattr(agent_routes, "require_api_user", browser_user)
    user = await agent_routes.require_interactive_user(object())
    assert user["id"] == "u-alice"


async def test_require_owner_user_rejects_machine_credentials_and_members(monkeypatch):
    # 机器凭证即使带 role=owner 也拒：agent 会话 / 统一密钥不承载管理员能力。
    async def machine_user(request):
        return {
            "id": "u-alice",
            "username": "alice",
            "credential_kind": "api_key",
            "role": "owner",
        }

    monkeypatch.setattr(agent_routes, "require_api_user", machine_user)
    with pytest.raises(HTTPException) as excinfo:
        await agent_routes.require_owner_user(object())
    assert excinfo.value.status_code == 403

    async def member_user(request):
        return {"id": "u-bob", "username": "bob", "credential_kind": "", "role": "member"}

    monkeypatch.setattr(agent_routes, "require_api_user", member_user)
    with pytest.raises(HTTPException) as excinfo:
        await agent_routes.require_owner_user(object())
    assert excinfo.value.status_code == 403

    async def owner_user(request):
        return {"id": "u-alice", "username": "alice", "credential_kind": "", "role": "owner"}

    monkeypatch.setattr(agent_routes, "require_api_user", owner_user)
    user = await agent_routes.require_owner_user(object())
    assert user["id"] == "u-alice"


# ---------------------------------------------------------------------------
# routes：local-tools 端点
# ---------------------------------------------------------------------------


def test_local_tools_endpoint_reports_host_home_availability(client, host_home):
    (host_home / ".cursor").mkdir()

    response = client.get("/api/v1/agent/local-tools")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["hostHomeAvailable"] is True
    assert data["hostHomePath"] == str(host_home)
    assert tool_by_id(data["tools"], "cursor")["installed"] is True
    assert tool_by_id(data["tools"], "claude")["installed"] is False
    # 前端据此生成 mcp.json 片段（容器版 = docker exec 目标名）。
    assert data["mcpRuntime"]["mode"] == "docker"
    assert data["mcpRuntime"]["containerName"] == "aipdd-drama-api"


def test_local_tools_endpoint_reports_native_runtime(client, host_home, monkeypatch):
    monkeypatch.setenv("AIPDD_DRAMA_MCP_TRANSPORT", "native")
    monkeypatch.setenv("AIPDD_DRAMA_MCP_CONTAINER", "ignored")

    data = client.get("/api/v1/agent/local-tools").json()["data"]

    runtime = data["mcpRuntime"]
    assert runtime["mode"] == "native"
    assert runtime["python"]
    assert runtime["containerName"] == "ignored"


def test_local_tools_endpoint_flags_missing_host_home(client, monkeypatch, tmp_path):
    monkeypatch.setenv("HOST_HOME_PATH", str(tmp_path / "missing-home"))

    data = client.get("/api/v1/agent/local-tools").json()["data"]

    assert data["hostHomeAvailable"] is False
    assert not any(item["installed"] for item in data["tools"])


def test_connect_and_disconnect_endpoints_manage_config_and_keys(client, host_home):
    (host_home / ".cursor").mkdir()
    config = host_home / ".cursor" / "mcp.json"

    connected = client.post(
        "/api/v1/agent/local-tools/connect",
        json={"tools": ["cursor"], "project": "p-1"},
    ).json()["data"]

    assert connected["results"] == [{"id": "cursor", "ok": True}]
    assert tool_by_id(connected["tools"], "cursor")["connected"] is True

    # 写进配置的条目带可用统一密钥 + 项目号（MCP 客户端开箱即用）。
    entry = json.loads(config.read_text(encoding="utf-8"))["mcpServers"]["aipdd-drama"]
    token = token_from_args(entry["args"])
    assert token.startswith("st-")
    assert "DRAMACLAW_PROJECT_ID=p-1" in entry["args"]

    # 状态文件记录密钥 id（断开时用于吊销）。
    state_path = agent_store.agent_root("alice") / "local_tools.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["cursor"]["api_key_id"]

    # 密钥当前有效（st- 前缀与记录一致，未吊销）。
    store = LocalUserStore()
    records = asyncio.run(store.list_api_keys("u-alice"))
    assert len(records) == 1
    assert records[0].revoked_at == ""
    assert token.startswith(records[0].key_prefix)

    disconnected = client.post(
        "/api/v1/agent/local-tools/disconnect",
        json={"tools": ["cursor"], "project": "p-1"},
    ).json()["data"]

    assert disconnected["results"] == [{"id": "cursor", "ok": True}]
    assert tool_by_id(disconnected["tools"], "cursor")["connected"] is False
    assert "aipdd-drama" not in json.loads(config.read_text(encoding="utf-8"))["mcpServers"]

    # 断开后密钥被吊销、状态清空。
    records = asyncio.run(store.list_api_keys("u-alice"))
    assert records and records[0].revoked_at != ""
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "cursor" not in state


def test_connect_endpoint_reports_not_installed_tool_as_failed(client, host_home):
    # Claude Code 未装：连接应被拒绝为 per-tool 失败，其余工具不受影响。
    response = client.post(
        "/api/v1/agent/local-tools/connect",
        json={"tools": ["claude"], "project": ""},
    ).json()["data"]

    assert response["results"][0]["id"] == "claude"
    assert response["results"][0]["ok"] is False
    assert "not installed" in response["results"][0]["error"]