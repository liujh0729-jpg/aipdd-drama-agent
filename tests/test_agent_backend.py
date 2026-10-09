# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] Agent 后端回归测试：存储 / 技能 / 记忆 / MCP 配置 / API 端点。

覆盖 ``src/novelvideo/agent`` 的本地功能面——不发真实网络请求（上游网关的
chat_stream / generate_image 不在本文件覆盖范围）。数据目录通过
``NOVELVIDEO_AGENT_DATA_DIR`` 隔离到 tmp_path，不碰真实部署状态。
"""

from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.agent import mcp as agent_mcp
from novelvideo.agent import memory as agent_memory
from novelvideo.agent import skills as agent_skills
from novelvideo.agent import store as agent_store
from novelvideo.agent.routes import require_api_user

USER = {"id": "u-alice", "username": "alice"}

SKILL_MD = """---
name: storyboard-helper
description: 分镜草图相关的创作建议
---

# 分镜助手

按照镜头语言给出分镜建议。
"""


@pytest.fixture()
def agent_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVELVIDEO_AGENT_DATA_DIR", str(tmp_path / "agent-data"))
    return tmp_path


@pytest.fixture()
def agent_client(agent_env):
    from novelvideo.agent.routes import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_api_user] = lambda: dict(USER)
    return TestClient(app)


# ---------------------------------------------------------------------------
# store：会话 / 消息 / 设置
# ---------------------------------------------------------------------------


def test_store_conversation_and_message_lifecycle(agent_env) -> None:
    username = "alice"
    conversation = agent_store.create_conversation(username, project="proj-a", title="第一场讨论")
    assert conversation["id"].startswith("aconv_")
    assert conversation["title"] == "第一场讨论"

    other = agent_store.create_conversation(username, project="proj-b", title="别的项目")
    assert {item["id"] for item in agent_store.list_conversations(username, "proj-a")} == {
        conversation["id"]
    }
    assert {item["id"] for item in agent_store.list_conversations(username)} == {
        conversation["id"],
        other["id"],
    }

    updated = agent_store.update_conversation(username, conversation["id"], title="改名后")
    assert updated is not None and updated["title"] == "改名后"
    assert agent_store.update_conversation(username, "aconv_missing", title="x") is None

    agent_store.append_message(username, conversation["id"], role="user", content="你好")
    agent_store.append_message(
        username,
        conversation["id"],
        role="assistant",
        content="收到",
        tool_events=[{"name": "agent_generate_image", "ok": True}],
        media=[{"kind": "image", "url": "/api/v1/agent/media/a.png"}],
    )
    messages = agent_store.list_messages(username, conversation["id"])
    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert messages[1]["tool_events"][0]["name"] == "agent_generate_image"
    assert messages[1]["media"][0]["url"] == "/api/v1/agent/media/a.png"

    assert agent_store.clear_messages(username, conversation["id"]) == 2
    assert agent_store.list_messages(username, conversation["id"]) == []

    assert agent_store.delete_conversation(username, conversation["id"]) is True
    assert agent_store.delete_conversation(username, conversation["id"]) is False
    remaining = {item["id"] for item in agent_store.list_conversations(username)}
    assert remaining == {other["id"]}


def test_store_settings_kv(agent_env) -> None:
    username = "alice"
    assert agent_store.get_setting(username, "text_model", "fallback") == "fallback"
    agent_store.set_setting(username, "text_model", "gw-text")
    assert agent_store.get_setting(username, "text_model") == "gw-text"
    assert agent_store.get_json_setting(username, "list_key", ["default"]) == ["default"]
    agent_store.set_json_setting(username, "list_key", ["a", "b"])
    assert agent_store.get_json_setting(username, "list_key", []) == ["a", "b"]


def test_store_safe_username_guards_paths(agent_env) -> None:
    # 目录穿越/特殊字符用户名的收敛（哈希兜底不含路径分隔符）
    assert "/" not in agent_store.safe_username("../../etc")
    assert "\\" not in agent_store.safe_username("a\\b")
    fallback = agent_store.safe_username("..")
    assert fallback.startswith("u") and "/" not in fallback


# ---------------------------------------------------------------------------
# skills：安装 / 启停 / 摘要 / 删除
# ---------------------------------------------------------------------------


def test_skill_install_from_markdown_and_toggle(agent_env) -> None:
    username = "alice"
    result = agent_skills.install_skill(username, filename="note.md", content=SKILL_MD.encode())
    assert result == {"name": "storyboard-helper", "updated": False}

    listed = agent_skills.list_skills(username)
    assert [item["name"] for item in listed] == ["storyboard-helper"]
    assert listed[0]["enabled"] is True
    assert listed[0]["description"] == "分镜草图相关的创作建议"

    detail = agent_skills.get_skill(username, "storyboard-helper")
    assert detail is not None
    assert "分镜" in detail["body"]
    assert detail["truncated"] is False

    summary = agent_skills.build_skills_summary(username)
    assert "storyboard-helper" in summary
    body = agent_skills.read_skill_body(username, "storyboard-helper")
    assert body is not None and body[0] == "storyboard-helper"

    disabled = agent_skills.set_skill_enabled(username, "storyboard-helper", False)
    assert disabled is not None and disabled["enabled"] is False
    assert "storyboard-helper" not in agent_skills.build_skills_summary(username)
    assert agent_skills.read_skill_body(username, "storyboard-helper") is None
    assert agent_skills.set_skill_enabled(username, "missing", True) is None

    # 重新安装 = 更新，并自动重新启用
    again = agent_skills.install_skill(username, filename="note.md", content=SKILL_MD.encode())
    assert again["updated"] is True
    assert agent_skills.get_skill(username, "storyboard-helper")["enabled"] is True

    assert agent_skills.delete_skill(username, "storyboard-helper") is True
    assert agent_skills.list_skills(username) == []
    assert agent_skills.get_skill(username, "storyboard-helper") is None
    assert agent_skills.delete_skill(username, "storyboard-helper") is False


def test_skill_install_from_zip_with_attachment(agent_env) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("bundle/SKILL.md", SKILL_MD)
        archive.writestr("bundle/reference/storyboard.md", "附件正文")
    result = agent_skills.install_skill("alice", filename="bundle.zip", content=buffer.getvalue())
    assert result["name"] == "storyboard-helper"

    attachment = (
        agent_store.skills_root("alice") / "storyboard-helper" / "reference" / "storyboard.md"
    )
    assert attachment.read_text(encoding="utf-8") == "附件正文"


def test_skill_install_rejects_bad_input(agent_env) -> None:
    with pytest.raises(agent_skills.SkillError, match="上传内容为空"):
        agent_skills.install_skill("alice", filename="empty.md", content=b"")
    with pytest.raises(agent_skills.SkillError, match="缺少 name"):
        agent_skills.install_skill("alice", filename="SKILL.md", content=b"no frontmatter")
    with pytest.raises(agent_skills.SkillError, match="技能名"):
        agent_skills.install_skill(
            "alice", filename="bad.md", content="---\nname: !!!\n---\nbody".encode()
        )
    with pytest.raises(agent_skills.SkillError, match="不是有效的 zip"):
        agent_skills.install_skill("alice", filename="broken.zip", content=b"not a zip")


def test_skill_zip_slip_rejected(agent_env) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("SKILL.md", SKILL_MD)
        archive.writestr("../evil.md", "x")
    with pytest.raises(agent_skills.SkillError, match="非法路径"):
        agent_skills.install_skill("alice", filename="evil.zip", content=buffer.getvalue())


def test_skill_name_normalization(agent_env) -> None:
    # 文件名兜底 + 大写/空格归一化成目录名
    result = agent_skills.install_skill(
        "alice",
        filename="My Skill Note.md",
        content=b"---\ndescription: d\n---\nbody",
    )
    assert result["name"] == "my-skill-note"
    assert agent_skills.resolve_skill("alice", "MY-SKILL-NOTE") == "my-skill-note"


# ---------------------------------------------------------------------------
# memory
# ---------------------------------------------------------------------------


def test_memory_append_dedup_and_edit(agent_env) -> None:
    username = "alice"
    assert agent_memory.read_memory(username) == ""
    assert agent_memory.build_memory_block(username) == ""

    stored, added = agent_memory.append_memory(username, "  主角喜欢下雨天  ")
    assert added is True
    assert "主角喜欢下雨天" in stored

    stored, added = agent_memory.append_memory(username, "主角喜欢下雨天")
    assert added is False
    assert stored.count("主角喜欢下雨天") == 1

    edited = agent_memory.write_memory(username, " 新的记忆 ")
    assert edited == "新的记忆"
    assert agent_memory.read_memory(username) == "新的记忆"
    assert "新的记忆" in agent_memory.build_memory_block(username)


# ---------------------------------------------------------------------------
# MCP：配置管理 / 内置保护 / 工具名
# ---------------------------------------------------------------------------


def test_mcp_config_builtin_and_servers(agent_env) -> None:
    username = "alice"
    servers = agent_mcp.list_servers(username)
    assert [item["id"] for item in servers] == list(agent_mcp.BUILTIN_SERVER_IDS)
    builtin = servers[0]
    assert builtin["builtin"] is True and builtin["enabled"] is True
    assert builtin["args"] == ["-m", "novelvideo.chat.aipdd_drama_mcp"]
    assert builtin["env"]["DRAMACLAW_CE_OWNER"] == "1"

    # [aipdd fork delta] 第二内置：Chrome MCP（连接客户自带 Chrome；默认写入、
    # 默认启用、不可编辑/删除，只可启停）。
    chrome = servers[1]
    assert chrome["id"] == agent_mcp.CHROME_SERVER_ID
    assert chrome["builtin"] is True and chrome["enabled"] is True
    assert chrome["command"] == "chrome-devtools-mcp"
    assert chrome["args"][0] == "--browserUrl" and chrome["args"][1].startswith("http")

    # [aipdd fork delta] 第三内置：本地 ComfyUI MCP（参考官方 comfy-mcp 的工具语义
    # 自建，只打渠道 baseUrl；同样默认写入、不可编辑/删除，只可启停）。
    comfy = servers[2]
    assert comfy["id"] == agent_mcp.COMFY_SERVER_ID
    assert comfy["builtin"] is True and comfy["enabled"] is True
    assert comfy["transport"] == "stdio"
    assert comfy["args"] == ["-m", "novelvideo.chat.comfyui_mcp"]
    assert "COMFYUI_BASE_URL" in comfy["env"]

    # [aipdd fork delta] 第四内置：Blender MCP（官方 mcp-for-blender，接本机已装
    # Blender；默认写入、不可编辑/删除，只可启停）。
    blender = servers[3]
    assert blender["id"] == agent_mcp.BLENDER_SERVER_ID
    assert blender["builtin"] is True and blender["enabled"] is True
    assert blender["transport"] == "stdio"
    assert blender["command"] == "uvx"
    assert blender["args"] == ["mcp-for-blender"]
    assert set(blender["env"]) == {"BLENDER_HOST", "BLENDER_PORT"}

    with pytest.raises(agent_mcp.McpConfigError, match="内置服务器不可编辑"):
        agent_mcp.upsert_server(username, {"id": agent_mcp.BUILTIN_SERVER_ID, "command": "x"})
    with pytest.raises(agent_mcp.McpConfigError, match="内置服务器不可编辑"):
        agent_mcp.upsert_server(username, {"id": agent_mcp.CHROME_SERVER_ID, "command": "x"})
    with pytest.raises(agent_mcp.McpConfigError, match="内置服务器不可编辑"):
        agent_mcp.upsert_server(username, {"id": agent_mcp.COMFY_SERVER_ID, "command": "x"})
    with pytest.raises(agent_mcp.McpConfigError, match="内置服务器不可编辑"):
        agent_mcp.upsert_server(username, {"id": agent_mcp.BLENDER_SERVER_ID, "command": "x"})
    with pytest.raises(agent_mcp.McpConfigError, match="必须填写 command"):
        agent_mcp.upsert_server(username, {"id": "no-command", "transport": "stdio"})
    with pytest.raises(agent_mcp.McpConfigError, match="必须填写 url"):
        agent_mcp.upsert_server(username, {"id": "no-url", "transport": "http"})
    with pytest.raises(agent_mcp.McpConfigError, match="不支持的 transport"):
        agent_mcp.upsert_server(username, {"id": "bad", "transport": "grpc"})

    created = agent_mcp.upsert_server(
        username,
        {
            "id": "filesystem",
            "name": "文件系统",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "server-filesystem"],
        },
    )
    assert created["id"] == "filesystem" and created["enabled"] is True
    remote = agent_mcp.upsert_server(
        username,
        {"id": "remote", "transport": "streamable_http", "url": "http://127.0.0.1:9999/mcp"},
    )
    assert remote["transport"] == "http"
    assert len(agent_mcp.enabled_servers(username)) == 6

    toggled = agent_mcp.set_server_enabled(username, "filesystem", False)
    assert toggled is not None and toggled["enabled"] is False
    assert agent_mcp.set_server_enabled(username, "missing", True) is None

    # 更新已有条目时字段合并（缺 command 沿用旧值）
    updated = agent_mcp.upsert_server(username, {"id": "filesystem", "description": "本地文件"})
    assert updated["command"] == "npx"
    assert updated["description"] == "本地文件"

    # 内置服务器只允许启停：Chrome MCP 先禁用再恢复。
    toggled_chrome = agent_mcp.set_server_enabled(username, agent_mcp.CHROME_SERVER_ID, False)
    assert toggled_chrome is not None and toggled_chrome["enabled"] is False
    assert agent_mcp.set_server_enabled(username, agent_mcp.CHROME_SERVER_ID, True)["enabled"] is True

    # [aipdd fork delta] ComfyUI MCP 也只是一样可停不可改：停用后重启回启用。
    toggled_comfy = agent_mcp.set_server_enabled(username, agent_mcp.COMFY_SERVER_ID, False)
    assert toggled_comfy is not None and toggled_comfy["enabled"] is False
    assert agent_mcp.COMFY_SERVER_ID not in [
        item["id"] for item in agent_mcp.enabled_servers(username)
    ]
    assert (
        agent_mcp.set_server_enabled(username, agent_mcp.COMFY_SERVER_ID, True)["enabled"]
        is True
    )

    with pytest.raises(agent_mcp.McpConfigError, match="内置服务器不可删除"):
        agent_mcp.delete_server(username, agent_mcp.BUILTIN_SERVER_ID)
    with pytest.raises(agent_mcp.McpConfigError, match="内置服务器不可删除"):
        agent_mcp.delete_server(username, agent_mcp.CHROME_SERVER_ID)
    with pytest.raises(agent_mcp.McpConfigError, match="内置服务器不可删除"):
        agent_mcp.delete_server(username, agent_mcp.COMFY_SERVER_ID)
    with pytest.raises(agent_mcp.McpConfigError, match="内置服务器不可删除"):
        agent_mcp.delete_server(username, agent_mcp.BLENDER_SERVER_ID)
    assert agent_mcp.delete_server(username, "remote") is True
    assert agent_mcp.delete_server(username, "remote") is False

    remaining = agent_mcp.reset_servers(username)
    assert [item["id"] for item in remaining] == list(agent_mcp.BUILTIN_SERVER_IDS)
    assert agent_mcp.get_server(username, "filesystem") is None


def test_mcp_builtin_comfyui_server_address_follows_channel(agent_env, monkeypatch) -> None:
    """[aipdd fork delta] 内置 ComfyUI MCP 的地址只认 comfyui 渠道 baseUrl（不写死）。"""
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.get_newapi_provider_channel",
        lambda provider: (
            {"baseUrl": "http://comfy.example:8188/"} if provider == "comfyui" else {}
        ),
    )
    assert (
        agent_mcp._comfy_server()["env"]["COMFYUI_BASE_URL"] == "http://comfy.example:8188"
    )


def test_mcp_builtin_blender_server_address_follows_env(agent_env, monkeypatch) -> None:
    """[aipdd fork delta] 内置 Blender MCP 的目标地址随 ST_BLENDER_HOST/PORT 覆盖。"""
    monkeypatch.setenv("ST_BLENDER_HOST", "127.0.0.1")
    monkeypatch.setenv("ST_BLENDER_PORT", "9877")
    server = agent_mcp._blender_server()
    assert server["env"]["BLENDER_HOST"] == "127.0.0.1"
    assert server["env"]["BLENDER_PORT"] == "9877"

    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.get_newapi_provider_channel",
        lambda provider: {},
    )
    assert agent_mcp._comfy_server()["env"]["COMFYUI_BASE_URL"] == ""


def test_mcp_builtin_comfyui_tool_declarations(agent_env) -> None:
    """工具面与官方 comfy-mcp 语义对齐，且 schema 可直接交给 MCP 校验。"""
    from novelvideo.chat import comfyui_mcp

    assert set(comfyui_mcp.TOOLS) == {
        "server_info",
        "list_workflows",
        "get_workflow",
        "search_nodes",
        "search_models",
        "validate_workflow",
        "run_workflow",
    }
    for name, (schema, handler) in comfyui_mcp.TOOLS.items():
        assert schema["description"], name
        assert schema["parameters"]["type"] == "object", name
        assert callable(handler), name


async def test_mcp_comfyui_tools_report_unconfigured_address(agent_env, monkeypatch) -> None:
    """[aipdd fork delta] 地址没配时工具不连网、不崩：返回带出路的可读错误。"""
    from novelvideo.chat import comfyui_mcp

    monkeypatch.delenv("COMFYUI_BASE_URL", raising=False)
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.get_newapi_provider_channel",
        lambda provider: {},
    )
    info = await comfyui_mcp.server_info()
    assert info["configured"] is False
    assert "本地 ComfyUI" in info["error"]
    with pytest.raises(comfyui_mcp.ComfyUIMcpError, match="地址未配置"):
        await comfyui_mcp.list_workflows()


def test_mcp_qualified_tool_name_shortening() -> None:
    assert agent_mcp.qualified_tool_name("aipdd-drama", "get_project") == "mcp__aipdd-drama__get_project"
    long_name = agent_mcp.qualified_tool_name("s" * 60, "t" * 60)
    assert len(long_name) <= agent_mcp.TOOL_NAME_MAX_LEN
    assert long_name.startswith("mcp__")


def test_mcp_builtin_tool_catalog(agent_env) -> None:
    """内置工具目录与 stdio 服务器同源：清单完整且字段 schema 带必填数组。"""
    catalog = agent_mcp.builtin_tool_catalog()
    names = [item["name"] for item in catalog]
    assert len(catalog) >= 50
    assert "aipdd_drama_get" in names
    assert any(name.startswith("vectcut_") for name in names)
    sample = next(item for item in catalog if item["name"] == "aipdd_drama_get")
    assert sample["input_schema"]["type"] == "object"
    assert "path" in sample["input_schema"]["properties"]
    assert sample["input_schema"]["required"] == ["path"]


async def test_probe_server_returns_tool_schema(monkeypatch) -> None:
    """「测试连接」返回的工具带 input_schema（前端展开字段与必填用）。"""

    class _FakeHub:
        def __init__(self, _servers, **_kwargs) -> None:
            self.errors: list[dict[str, str]] = []
            self.tools = [
                {
                    "server_id": "fake",
                    "server_name": "Fake",
                    "remote_name": "echo",
                    "qualified_name": "mcp__fake__echo",
                    "description": "echo input",
                    "input_schema": {
                        "type": "object",
                        "properties": {"x": {"type": "string"}},
                        "required": ["x"],
                    },
                }
            ]

        async def start(self) -> None:
            return None

        async def close(self) -> None:
            return None

    monkeypatch.setattr(agent_mcp, "McpToolHub", _FakeHub)
    result = await agent_mcp.probe_server({"id": "fake", "name": "Fake"})
    assert result["ok"] is True
    assert result["tools"][0]["input_schema"]["required"] == ["x"]


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------


def test_api_builtin_mcp_tools(agent_client) -> None:
    """MCP 栏目「工具」页数据源：内置工具目录端点。"""
    response = agent_client.get("/api/v1/agent/mcp/builtin-tools")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["server_id"] == "aipdd-drama"
    names = {item["name"] for item in data["tools"]}
    assert {"aipdd_drama_get", "vectcut_health"} <= names
    sample = next(item for item in data["tools"] if item["name"] == "aipdd_drama_get")
    assert sample["input_schema"]["required"] == ["path"]


def test_api_status_and_settings(agent_client) -> None:
    response = agent_client.get("/api/v1/agent/status")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["skills_total"] == 0
    assert data["mcp_servers_total"] == len(agent_mcp.BUILTIN_SERVER_IDS)

    response = agent_client.put(
        "/api/v1/agent/settings",
        json={
            "text_model": "gw-text-1",
            "image_model": "gw-image-1",
            "video_model": "gw-video-1",
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["text_model"] == "gw-text-1" and data["image_model"] == "gw-image-1"
    assert data["video_model"] == "gw-video-1"
    assert agent_client.get("/api/v1/agent/status").json()["data"]["text_model"] == "gw-text-1"


def test_api_turn_forwards_dialog_options(agent_client, monkeypatch) -> None:
    """回合请求的对话类型 / 视频模型 / 参考素材透传引擎，参考素材按上限与媒介白名单清洗。"""
    from novelvideo.agent import routes as agent_routes

    captured: dict = {}

    class _CapturingEngine:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        async def run(self, _content, emit):
            emit({"type": "delta", "text": "ok"})
            return {
                "id": "am_test",
                "conversation_id": "",
                "role": "assistant",
                "content": "ok",
                "tool_events": [],
                "media": [],
                "created_at": "2026-09-30T00:00:00Z",
            }

    monkeypatch.setattr(agent_routes.agent_engine, "AgentEngine", _CapturingEngine)

    conversation = agent_client.post("/api/v1/agent/conversations", json={}).json()["data"]
    references = [
        {"name": "参考图", "url": "https://cdn.example/a.png", "media": "image"},
        {"name": "坏媒介", "url": "https://cdn.example/b.bin", "media": "binary"},
        {"name": "", "url": "", "media": "image"},
    ] + [
        {"name": f"n{i}", "url": f"https://cdn.example/{i}.png", "media": "video"}
        for i in range(20)
    ]
    response = agent_client.post(
        f"/api/v1/agent/conversations/{conversation['id']}/messages",
        json={
            "content": "hi",
            "model": "gw-text-1",
            "mode": "video",
            "video_model": "gw-video-1",
            "references": references,
        },
    )
    assert response.status_code == 200
    assert captured["mode"] == "video"
    assert captured["video_model"] == "gw-video-1"
    cleaned = captured["references"]
    # 上限 12 条；非法媒介收敛为 image；空条目丢弃（12 条原始输入 - 1 条空）。
    assert len(cleaned) == 11
    assert cleaned[0] == {
        "name": "参考图",
        "url": "https://cdn.example/a.png",
        "media": "image",
    }
    assert cleaned[1] == {
        "name": "坏媒介",
        "url": "https://cdn.example/b.bin",
        "media": "image",
    }
    assert cleaned[2]["media"] == "video"
    assert all(item["media"] in {"image", "video", "audio"} for item in cleaned)


def test_api_conversations_crud(agent_client) -> None:
    created = agent_client.post(
        "/api/v1/agent/conversations", json={"title": "新会话", "project": "p1"}
    )
    assert created.status_code == 200
    conversation_id = created.json()["data"]["id"]

    listed = agent_client.get("/api/v1/agent/conversations", params={"project": "p1"})
    assert [item["id"] for item in listed.json()["data"]] == [conversation_id]

    renamed = agent_client.patch(
        f"/api/v1/agent/conversations/{conversation_id}", json={"title": "改名"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["data"]["title"] == "改名"

    assert agent_client.get(f"/api/v1/agent/conversations/{conversation_id}/messages").status_code == 200
    assert agent_client.patch("/api/v1/agent/conversations/missing", json={"title": "x"}).status_code == 404
    assert agent_client.post(
        "/api/v1/agent/conversations/missing/messages", json={"content": "hi"}
    ).status_code == 404
    assert agent_client.post(
        f"/api/v1/agent/conversations/{conversation_id}/messages", json={"content": "   "}
    ).status_code == 400

    cleared = agent_client.delete(f"/api/v1/agent/conversations/{conversation_id}/messages")
    assert cleared.status_code == 200
    assert cleared.json()["data"]["removed"] == 0

    # 没有运行中的回合：cancel 返回 False
    cancelled = agent_client.post(f"/api/v1/agent/conversations/{conversation_id}/cancel")
    assert cancelled.json()["data"]["cancelled"] is False
    assert agent_client.post("/api/v1/agent/conversations/missing/cancel").status_code == 404

    deleted = agent_client.delete(f"/api/v1/agent/conversations/{conversation_id}")
    assert deleted.json()["data"]["deleted"] is True
    assert agent_client.delete(f"/api/v1/agent/conversations/{conversation_id}").status_code == 404


def test_api_turn_failure_event_carries_i18n_code(agent_client, monkeypatch) -> None:
    """回合执行失败：SSE error 帧同时带中文兜底与 message_code（i18n_message 约定）。"""
    from novelvideo.agent import routes as agent_routes

    class _FailingEngine:
        def __init__(self, **_kwargs) -> None:
            pass

        async def run(self, _content, _emit):
            raise RuntimeError("boom")

    monkeypatch.setattr(agent_routes.agent_engine, "AgentEngine", _FailingEngine)

    conversation = agent_client.post("/api/v1/agent/conversations", json={}).json()["data"]
    response = agent_client.post(
        f"/api/v1/agent/conversations/{conversation['id']}/messages",
        json={"content": "hi", "model": "gw-text-1"},
    )
    assert response.status_code == 200
    events = [
        json.loads(line[5:])
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    error = next(item for item in events if item["type"] == "error")
    assert error["message_code"] == "agent.error.turnFailed"
    assert error["message"].startswith("回合执行失败：")
    assert error["message_params"] == {"detail": "boom"}


def test_api_turn_without_text_model_explains_the_root_cause(agent_client, monkeypatch) -> None:
    """文本模型解析不到：400 要带出功能位成因与就地出路。

    旧文案只说「请在设置中配置默认文本模型」，但图片/视频回合并不提供这一位的入口，
    而 ``get_effective_default_text_model()`` 按 req40 口径把 ``ModelSlotNotConfigured``
    吞成空串——用户既看不到真实成因，也找不到可点的地方。本用例钉住：底层成因不许被吞。
    """
    from novelvideo import config as app_config
    from novelvideo.agent import routes as agent_routes
    from novelvideo.model_slots import REASON_UNBOUND, ModelSlotNotConfigured

    monkeypatch.setattr(agent_routes, "_default_text_model", lambda: "")

    def boom(*_args, **_kwargs):
        raise ModelSlotNotConfigured(
            "DC-freezone-text-writer-LLM（FREEZONE_TEXT_WRITER_MODEL）", REASON_UNBOUND
        )

    monkeypatch.setattr(app_config, "get_newapi_text_model_name", boom)

    conversation = agent_client.post("/api/v1/agent/conversations", json={}).json()["data"]
    response = agent_client.post(
        f"/api/v1/agent/conversations/{conversation['id']}/messages",
        json={"content": "hi", "mode": "video"},
    )
    assert response.status_code == 400
    # detail 必须是字符串：前端 agentErrorMessage 只读字符串 detail，换成 dict 会退化成「HTTP 400」。
    detail = response.json()["detail"]
    assert isinstance(detail, str)
    assert "FREEZONE_TEXT_WRITER_MODEL" in detail
    assert "该功能位还没有绑定可用模型" in detail
    # 讲清必需性（不分模式）+ 就地可执行的动作。
    assert "图片" in detail and "视频" in detail
    assert "「文本模型」下拉" in detail


def test_api_turn_without_text_model_keeps_actionable_hint_without_a_cause(
    agent_client, monkeypatch
) -> None:
    """探不到具体成因（槽位解析反而成功）时也不能只留一句空话：出路照给。"""
    from novelvideo.agent import routes as agent_routes

    monkeypatch.setattr(agent_routes, "_default_text_model", lambda: "")
    monkeypatch.setattr(agent_routes, "_default_text_model_cause", lambda: "")

    conversation = agent_client.post("/api/v1/agent/conversations", json={}).json()["data"]
    response = agent_client.post(
        f"/api/v1/agent/conversations/{conversation['id']}/messages",
        json={"content": "hi", "mode": "image"},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "既没有选中文本模型" in detail
    assert "「文本模型」下拉" in detail


def test_api_memory_roundtrip(agent_client) -> None:
    assert agent_client.get("/api/v1/agent/memory").json()["data"]["content"] == ""
    response = agent_client.put("/api/v1/agent/memory", json={"content": "记住：主角怕水"})
    assert response.status_code == 200
    assert response.json()["data"]["content"] == "记住：主角怕水"
    assert agent_client.get("/api/v1/agent/memory").json()["data"]["content"] == "记住：主角怕水"


def test_api_skills_install_toggle_delete(agent_client) -> None:
    response = agent_client.post(
        "/api/v1/agent/skills/install",
        files={"file": ("note.md", SKILL_MD.encode(), "text/markdown")},
    )
    assert response.status_code == 200
    assert response.json()["data"]["skill"]["name"] == "storyboard-helper"

    bad = agent_client.post(
        "/api/v1/agent/skills/install", files={"file": ("SKILL.md", b"no name", "text/markdown")}
    )
    assert bad.status_code == 400

    listed = agent_client.get("/api/v1/agent/skills").json()["data"]
    assert [item["name"] for item in listed] == ["storyboard-helper"]

    detail = agent_client.get("/api/v1/agent/skills/storyboard-helper")
    assert detail.status_code == 200 and "分镜" in detail.json()["data"]["body"]

    toggled = agent_client.patch(
        "/api/v1/agent/skills/storyboard-helper", json={"enabled": False}
    )
    assert toggled.status_code == 200 and toggled.json()["data"]["enabled"] is False
    assert agent_client.patch("/api/v1/agent/skills/missing", json={"enabled": True}).status_code == 404
    assert agent_client.get("/api/v1/agent/skills/missing").status_code == 404

    assert agent_client.delete("/api/v1/agent/skills/storyboard-helper").status_code == 200
    assert agent_client.delete("/api/v1/agent/skills/storyboard-helper").status_code == 404


def test_api_mcp_servers_management(agent_client) -> None:
    servers = agent_client.get("/api/v1/agent/mcp/servers").json()["data"]
    assert [item["id"] for item in servers] == list(agent_mcp.BUILTIN_SERVER_IDS)

    created = agent_client.post(
        "/api/v1/agent/mcp/servers",
        json={"id": "demo", "name": "演示", "transport": "stdio", "command": "echo"},
    )
    assert created.status_code == 200 and created.json()["data"]["id"] == "demo"

    assert agent_client.post(
        "/api/v1/agent/mcp/servers", json={"id": "bad", "transport": "stdio"}
    ).status_code == 400
    assert agent_client.post(
        "/api/v1/agent/mcp/servers", json={"id": agent_mcp.BUILTIN_SERVER_ID, "command": "x"}
    ).status_code == 400
    # [aipdd fork delta] 第二内置 Chrome MCP 同样不可编辑/删除。
    assert agent_client.post(
        "/api/v1/agent/mcp/servers", json={"id": agent_mcp.CHROME_SERVER_ID, "command": "x"}
    ).status_code == 400

    toggled = agent_client.patch("/api/v1/agent/mcp/servers/demo", json={"enabled": False})
    assert toggled.status_code == 200 and toggled.json()["data"]["enabled"] is False
    assert agent_client.patch(
        "/api/v1/agent/mcp/servers/missing", json={"enabled": True}
    ).status_code == 404

    assert agent_client.delete(
        f"/api/v1/agent/mcp/servers/{agent_mcp.BUILTIN_SERVER_ID}"
    ).status_code == 400
    assert agent_client.delete(
        f"/api/v1/agent/mcp/servers/{agent_mcp.CHROME_SERVER_ID}"
    ).status_code == 400
    assert agent_client.delete("/api/v1/agent/mcp/servers/demo").json()["data"]["deleted"] is True
    assert agent_client.delete("/api/v1/agent/mcp/servers/demo").status_code == 404

    reset = agent_client.post("/api/v1/agent/mcp/servers/reset")
    assert [item["id"] for item in reset.json()["data"]] == list(agent_mcp.BUILTIN_SERVER_IDS)
    assert agent_client.post("/api/v1/agent/mcp/servers/missing/probe").status_code == 404


def test_api_media_serving(agent_client) -> None:
    assert agent_client.get("/api/v1/agent/media/none.png").status_code == 404
    media_dir = agent_store.agent_root("alice") / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "pic.png").write_bytes(b"\x89PNG\r\n")
    served = agent_client.get("/api/v1/agent/media/pic.png")
    assert served.status_code == 200
    assert served.content == b"\x89PNG\r\n"


def test_api_library_save_validation_and_success(agent_client, agent_env, monkeypatch) -> None:
    project_dir = agent_env / "proj"
    (project_dir / "freezone").mkdir(parents=True)

    import novelvideo.api.deps as api_deps

    async def fake_resolve(project, user, *, required_role="viewer", media_read=False):
        return SimpleNamespace(project_dir=project_dir)

    monkeypatch.setattr(api_deps, "resolve_project_scope", fake_resolve)

    base = {"project": "demo", "url": "/api/v1/agent/media/pic.png", "name": "素材一"}
    assert agent_client.post("/api/v1/agent/library/save", json={**base, "project": ""}).status_code == 400
    assert agent_client.post("/api/v1/agent/library/save", json={**base, "name": ""}).status_code == 400
    assert agent_client.post("/api/v1/agent/library/save", json={**base, "media": "text"}).status_code == 400
    assert agent_client.post("/api/v1/agent/library/save", json={**base, "url": ""}).status_code == 400
    assert agent_client.post("/api/v1/agent/library/save", json={**base, "folder": "mainline"}).status_code == 400
    assert agent_client.post("/api/v1/agent/library/save", json={**base, "folder": "nope"}).status_code == 404
    assert agent_client.post(
        "/api/v1/agent/library/save", json={**base, "url": "ftp://host/a.png"}
    ).status_code == 400
    # 媒体文件尚不存在：404
    assert agent_client.post("/api/v1/agent/library/save", json=base).status_code == 404

    media_dir = agent_store.agent_root("alice") / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "pic.png").write_bytes(b"\x89PNG\r\n")

    response = agent_client.post("/api/v1/agent/library/save", json=base)
    assert response.status_code == 200
    item = response.json()["data"]
    assert item["name"] == "素材一"
    uploads = list((project_dir / "freezone" / "_uploads").glob("agent_*.png"))
    assert len(uploads) == 1
    # 登记的地址是项目内静态 URL（不是 agent 全局媒体地址）
    assert "agent_" in json.dumps(item, ensure_ascii=False)
    assert "/api/v1/agent/media/" not in json.dumps(item, ensure_ascii=False)


def test_api_library_save_stores_under_folder(agent_client, agent_env, monkeypatch) -> None:
    """指定用户自建文件夹时条目落在该文件夹下。"""
    project_dir = agent_env / "proj2"
    (project_dir / "freezone").mkdir(parents=True)
    (project_dir / "freezone" / "video_character_folders.json").write_text(
        json.dumps([{"id": "folder-1", "name": "参考图"}], ensure_ascii=False),
        encoding="utf-8",
    )

    import novelvideo.api.deps as api_deps

    async def fake_resolve(project, user, *, required_role="viewer", media_read=False):
        return SimpleNamespace(project_dir=project_dir)

    monkeypatch.setattr(api_deps, "resolve_project_scope", fake_resolve)

    media_dir = agent_store.agent_root("alice") / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "pic2.png").write_bytes(b"\x89PNG\r\n")

    response = agent_client.post(
        "/api/v1/agent/library/save",
        json={
            "project": "demo",
            "url": "/api/v1/agent/media/pic2.png",
            "name": "参考图一",
            "folder": "folder-1",
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["folder"] == "folder-1"