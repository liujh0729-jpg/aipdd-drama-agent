# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 品牌改名（DramaClaw → AIPDD Drama）迁移回归测试。

覆盖四条兼容红线：
1. MCP 工具不再带 ``dramaclaw_`` 前缀（95 个工具全部 ``aipdd_drama_``/``vectcut_``），
   且注册表结构自洽；
2. 环境变量别名：``AIPDD_DRAMA_*`` 优先、历史 ``DRAMACLAW_*``/``SUPERTALE_*`` 回退，
   两者皆缺时报错文案用新名；
3. Agent 内置 MCP 服务器：新 id/显示名，历史 mcp_servers.json 里的 ``dramaclaw``
   条目 enabled=false 状态迁移到新 id；
4. 本地客户端接入（local_tools）：写入新 key 时清理旧 ``dramaclaw`` 条目，
   旧条目视同已连接，断开同时移除两代 key；TOML 段落同样双清。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from novelvideo.agent import local_tools as lt
from novelvideo.agent import mcp as agent_mcp

_REPO_ROOT = Path(__file__).resolve().parents[1]

_PLUGIN_ENV_VARS = (
    "AIPDD_DRAMA_API_URL",
    "AIPDD_DRAMA_AGENT_TOKEN",
    "AIPDD_DRAMA_PROJECT_ID",
    "AIPDD_DRAMA_PROJECT_OUTPUT_DIR",
    "DRAMACLAW_API_URL",
    "DRAMACLAW_AGENT_TOKEN",
    "DRAMACLAW_PROJECT_ID",
    "DRAMACLAW_PROJECT_OUTPUT_DIR",
    "SUPERTALE_PROJECT_OUTPUT_DIR",
)


def _load_tools_module():
    impl_path = _REPO_ROOT / "src" / "novelvideo" / "chat" / "dramaclaw_tools.py"
    spec = importlib.util.spec_from_file_location("test_brand_rename_tools", impl_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ─── 1. 工具名册 ──────────────────────────────────────────────────────────────


def test_no_tool_keeps_dramaclaw_prefix() -> None:
    module = _load_tools_module()
    names = [entry[0] for entry in module.TOOLS]
    assert names, "TOOLS 不应为空"
    assert not any(name.startswith("dramaclaw_") for name in names)
    assert all(name.startswith(("aipdd_drama_", "vectcut_")) for name in names)
    # [aipdd fork delta] 白板动画视频（aipdd_drama_whiteboard_animation）注册，名册 95 → 96。
    # [aipdd fork delta] 科普讲解视频（aipdd_drama_explainer_video）注册，名册 96 → 97。
    # [aipdd fork delta req46] 音乐创作（aipdd_drama_music_create）注册，名册 97 → 98。
    # [aipdd fork delta B+C] Blender 白模视频落片（aipdd_drama_commit_white_model_video）注册，名册 98 → 99。
    # [aipdd fork delta] 视频配音（aipdd_drama_video_dubbing）注册，名册 99 → 100。
    assert len(names) == len(set(names)) == 100
    # 通用 REST 面与两个一键自动化工具都在新前缀下。
    assert {"aipdd_drama_get", "aipdd_drama_post", "aipdd_drama_auto_storyboard", "aipdd_drama_video_range"} <= set(names)
    # [aipdd fork delta] 工具页白板动画在新前缀下。
    assert "aipdd_drama_whiteboard_animation" in set(names)
    # [aipdd fork delta] 工具页科普讲解视频在新前缀下。
    assert "aipdd_drama_explainer_video" in set(names)
    # [aipdd fork delta] 编排层四工具（run_pipeline / get_run_status / cancel_run / suggest_config）已注册。
    assert {
        "aipdd_drama_run_pipeline",
        "aipdd_drama_get_run_status",
        "aipdd_drama_cancel_run",
        "aipdd_drama_suggest_config",
    } <= set(names)


def test_legacy_mcp_bridge_alias_still_works() -> None:
    """旧模块名继续可导入并与新桥同源（历史外部配置 ``-m novelvideo.chat.dramaclaw_mcp``）。"""
    from novelvideo.chat import aipdd_drama_mcp, dramaclaw_mcp

    assert dramaclaw_mcp.TOOLS is aipdd_drama_mcp.TOOLS
    assert dramaclaw_mcp.SERVER is aipdd_drama_mcp.SERVER
    assert "aipdd_drama_get" in dramaclaw_mcp.TOOLS


# ─── 2. 环境变量别名 ─────────────────────────────────────────────────────────


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch):
    for name in _PLUGIN_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_env_alias_prefers_new_names(clean_env: pytest.MonkeyPatch) -> None:
    module = _load_tools_module()
    clean_env.setenv("DRAMACLAW_API_URL", "http://legacy:8780")
    clean_env.setenv("AIPDD_DRAMA_API_URL", "http://new:8780")
    assert module._base_url() == "http://new:8780"


def test_env_alias_falls_back_to_legacy(clean_env: pytest.MonkeyPatch) -> None:
    module = _load_tools_module()
    clean_env.setenv("DRAMACLAW_API_URL", "http://legacy:8780")
    clean_env.setenv("DRAMACLAW_AGENT_TOKEN", "st-legacy")
    clean_env.setenv("DRAMACLAW_PROJECT_ID", "p-legacy")
    assert module._base_url() == "http://legacy:8780"
    assert module._token() == "st-legacy"
    assert module._default_project_id() == "p-legacy"
    assert module._available() is True


def test_env_error_message_uses_new_name(clean_env: pytest.MonkeyPatch) -> None:
    module = _load_tools_module()
    with pytest.raises(ValueError, match="AIPDD_DRAMA_API_URL is not set"):
        module._base_url()
    with pytest.raises(ValueError, match="AIPDD_DRAMA_AGENT_TOKEN is not set"):
        module._token()


def test_output_dir_supertale_fallback(clean_env: pytest.MonkeyPatch) -> None:
    module = _load_tools_module()
    clean_env.setenv("SUPERTALE_PROJECT_OUTPUT_DIR", "/data/legacy-out")
    assert str(module._project_output_dir()) == "/data/legacy-out"
    clean_env.setenv("AIPDD_DRAMA_PROJECT_OUTPUT_DIR", "/data/new-out")
    assert str(module._project_output_dir()) == "/data/new-out"


# ─── 3. 内置服务器定义与历史状态迁移 ─────────────────────────────────────────


def test_builtin_server_renamed() -> None:
    server = agent_mcp._builtin_server()
    assert server["id"] == "aipdd-drama"
    assert server["name"] == "AIPDD Drama MCP"
    assert server["args"] == ["-m", "novelvideo.chat.aipdd_drama_mcp"]
    assert "AIPDD_DRAMA_API_URL" in server["env"]


def test_legacy_builtin_enabled_state_migrates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "mcp_servers.json"
    monkeypatch.setattr(agent_mcp.store, "mcp_servers_file", lambda username: state_file)
    # 历史配置：内置条目还叫 dramaclaw，且被用户停用。
    state_file.write_text(
        json.dumps(
            {
                "servers": [
                    {"id": "dramaclaw", "enabled": False, "created_at": "2026-10-01"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    servers = agent_mcp.load_config("alice")["servers"]
    builtin = next(item for item in servers if item["id"] == "aipdd-drama")
    assert builtin["enabled"] is False
    assert builtin["builtin"] is True
    # 旧 id 不会作为普通服务器重复出现。
    assert [item["id"] for item in servers].count("dramaclaw") == 0


# ─── 4. 本地客户端接入的双代 key 迁移 ────────────────────────────────────────


def _write_json_config(path: Path, servers: dict) -> None:
    path.write_text(
        json.dumps({"mcpServers": servers, "theme": "dark"}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_connection_entry_carries_both_env_generations() -> None:
    entry = lt.connection_entry(raw_key="st-x", project_id="p-1")
    args = [str(item) for item in entry["args"]]
    assert "AIPDD_DRAMA_API_URL=http://127.0.0.1:8780" in args
    assert "DRAMACLAW_API_URL=http://127.0.0.1:8780" in args
    assert "AIPDD_DRAMA_AGENT_TOKEN=st-x" in args
    assert "DRAMACLAW_AGENT_TOKEN=st-x" in args
    assert args[-4:] == ["aipdd-drama-api", "python", "-m", "novelvideo.chat.aipdd_drama_mcp"]


def test_json_connect_replaces_legacy_entry(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    _write_json_config(
        path,
        {
            "dramaclaw": {"command": "docker", "args": ["exec", "-i", "dramaclaw-api"]},
            "other": {"command": "other"},
        },
    )
    # 旧条目视同已连接。
    assert lt.json_has_server(path) is True

    lt.json_write_server(path, {"command": "docker", "args": ["exec", "-i", "aipdd-drama-api"]})
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "dramaclaw" not in data["mcpServers"]
    assert "aipdd-drama" in data["mcpServers"]
    assert data["mcpServers"]["other"] == {"command": "other"}
    assert data["theme"] == "dark"

    lt.json_remove_server(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mcpServers"] == {"other": {"command": "other"}}


def test_toml_connect_clears_legacy_section(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[mcp_servers.dramaclaw]\ncommand = "docker"\nargs = ["exec"]\n\n'
        "[other]\nkeep = true\n",
        encoding="utf-8",
    )
    assert lt.toml_has_server(path) is True

    lt.toml_write_server(path, {"command": "docker", "args": ["exec", "-i", "aipdd-drama-api"]})
    text = path.read_text(encoding="utf-8")
    assert "[mcp_servers.dramaclaw]" not in text
    assert text.count("[mcp_servers.aipdd-drama]") == 1
    assert "[other]" in text and "keep = true" in text

    lt.toml_remove_server(path)
    text = path.read_text(encoding="utf-8")
    assert "aipdd-drama" not in text and "dramaclaw" not in text
    assert "[other]" in text
