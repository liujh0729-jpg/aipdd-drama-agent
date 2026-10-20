# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
# [aipdd fork delta] 回归：MCP 服务器连接/超时/取消路径不得泄漏取消状态。
#
# 背景：mcp.py 曾先后用 asyncio.wait_for / anyio.fail_after 包裹连接——
# wait_for 让连接跑在独立任务（跨任务退出 anyio scope 抛 "in a different task"）；
# fail_after 的取消 scope 与连接成功后仍存活的 stdio 上下文不满足 LIFO 退出
# （退出时抛 "Attempted to exit a cancel scope that isn't the current task's
# current cancel scope"），且 shield 关闭泄漏取消状态——两者都会让主任务
# 后续 await 被取消，整轮回合报「回合已中断」。
# 现实现：asyncio.timeout（当前任务内取消）+ 同任务 aclose。
import asyncio
import sys
import textwrap
from types import SimpleNamespace

from novelvideo.agent import mcp as agent_mcp

TINY_SERVER = textwrap.dedent(
    """
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("tiny")

    @mcp.tool()
    def echo(text: str) -> str:
        return text

    if __name__ == "__main__":
        mcp.run()
    """
)

SILENT_SERVER = "import time\ntime.sleep(60)\n"


def _stdio_server(server_id: str, script) -> dict:
    return {
        "id": server_id,
        "name": server_id,
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(script)],
        "enabled": True,
    }


async def test_mcp_hub_stdio_connect_clean(tmp_path) -> None:
    """成功连接：error 为空、工具就绪，且连接后主任务不残留取消状态。"""
    script = tmp_path / "tiny_mcp_server.py"
    script.write_text(TINY_SERVER, encoding="utf-8")
    hub = agent_mcp.McpToolHub([_stdio_server("tiny", script)], connect_timeout=20.0)
    try:
        await hub.start()
        assert hub.errors == []
        assert any(
            tool["qualified_name"].startswith("mcp__tiny__") for tool in hub.tools
        )
        # 旧实现（fail_after/shield）在这里会抛 CancelledError（回合已中断）。
        await asyncio.sleep(0.05)
        # 连接后的工具调用仍可用（会话未被提前关闭）。
        tool = next(
            item
            for item in hub.tools
            if item["remote_name"] == "echo"
        )
        text = await hub.call(tool["qualified_name"], {"text": "ping"})
        assert "ping" in text
    finally:
        await hub.close()


async def test_mcp_hub_stdio_timeout_no_cancel_leak(tmp_path) -> None:
    """连接超时：记录错误但不得泄漏取消状态，也不影响后续其它操作。"""
    script = tmp_path / "silent_server.py"
    script.write_text(SILENT_SERVER, encoding="utf-8")
    hub = agent_mcp.McpToolHub([_stdio_server("silent", script)], connect_timeout=1.5)
    try:
        await hub.start()
        assert [item["server_id"] for item in hub.errors] == ["silent"]
        assert "超时" in hub.errors[0]["error"]
        # 旧实现在超时清理后会泄漏取消状态，这里直接失败。
        await asyncio.sleep(0.05)
    finally:
        await hub.close()


async def test_mcp_hub_failed_spawn_records_error(tmp_path) -> None:
    """启动失败（命令不存在）：记录错误并可继续使用 hub。"""
    server = {
        "id": "broken",
        "transport": "stdio",
        "command": str(tmp_path / "not-exists-python"),
        "args": [],
        "enabled": True,
    }
    hub = agent_mcp.McpToolHub([server], connect_timeout=10.0)
    try:
        await hub.start()
        assert [item["server_id"] for item in hub.errors] == ["broken"]
        await asyncio.sleep(0.05)
    finally:
        await hub.close()


# ---------------------------------------------------------------------------
# 内置服务器可用性门控：外部命令缺失时不白起子进程
# ---------------------------------------------------------------------------


def test_builtin_definitions_mark_missing_commands(monkeypatch) -> None:
    monkeypatch.setattr(agent_mcp.shutil, "which", lambda _command: None)

    chrome = agent_mcp._chrome_server()
    blender = agent_mcp._blender_server()
    comfy = agent_mcp._comfy_server()

    assert chrome["available"] is False
    assert "chrome-devtools-mcp" in chrome["unavailable_reason"]
    assert blender["available"] is False
    assert "uvx" in blender["unavailable_reason"]
    # 其余内置只依赖随包 Python/配置，不因外部命令缺失而不可用。
    assert comfy["available"] is True


def test_builtin_definitions_mark_available_commands(monkeypatch) -> None:
    monkeypatch.setattr(agent_mcp.shutil, "which", lambda _command: "/usr/bin/fake")

    assert agent_mcp._chrome_server()["available"] is True
    assert agent_mcp._blender_server()["available"] is True


def test_load_config_exposes_availability(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NOVELVIDEO_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(agent_mcp.shutil, "which", lambda _command: None)

    servers = {item["id"]: item for item in agent_mcp.list_servers("alice")}

    assert servers["aipdd-drama"]["available"] is True
    assert servers["chrome-devtools"]["available"] is False
    assert servers["blender-mcp"]["available"] is False


def test_enabled_servers_skips_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_mcp,
        "list_servers",
        lambda _username: [
            {"id": "ok", "enabled": True, "available": True},
            {"id": "missing", "enabled": True, "available": False},
            {"id": "off", "enabled": False, "available": True},
        ],
    )

    assert [item["id"] for item in agent_mcp.enabled_servers("alice")] == ["ok"]


async def test_mcp_hub_skips_unavailable_without_spawn() -> None:
    hub = agent_mcp.McpToolHub(
        [
            {
                "id": "blender-mcp",
                "transport": "stdio",
                "command": "uvx",
                "args": ["mcp-for-blender"],
                "enabled": True,
                "available": False,
                "unavailable_reason": "未检测到 uvx 命令",
            }
        ],
        connect_timeout=1.0,
    )
    try:
        await hub.start()
        assert hub.errors == [{"server_id": "blender-mcp", "error": "未检测到 uvx 命令"}]
        assert hub.tools == []
        await asyncio.sleep(0.05)
    finally:
        await hub.close()


async def test_probe_unavailable_server_returns_reason() -> None:
    result = await agent_mcp.probe_server(
        {
            "id": "chrome-devtools",
            "transport": "stdio",
            "command": "chrome-devtools-mcp",
            "args": [],
            "enabled": True,
            "available": False,
            "unavailable_reason": "未检测到 chrome-devtools-mcp 命令",
        }
    )

    assert result == {"ok": False, "error": "未检测到 chrome-devtools-mcp 命令"}


# ---------------------------------------------------------------------------
# 启动命令解析：Windows 的 .cmd/.bat 需经 cmd.exe /c
# ---------------------------------------------------------------------------


def test_resolve_launch_wraps_windows_batch(monkeypatch) -> None:
    resolved = r"C:\Users\me\AppData\Roaming\npm\chrome-devtools-mcp.cmd"
    comspec = r"C:\Windows\System32\cmd.exe"
    monkeypatch.setattr(agent_mcp.shutil, "which", lambda _command: resolved)
    monkeypatch.setattr(
        agent_mcp,
        "os",
        SimpleNamespace(name="nt", environ={"COMSPEC": comspec}),
    )

    command, prefix, available = agent_mcp._resolve_launch("chrome-devtools-mcp")

    assert available is True
    assert command == comspec
    assert prefix == ["/d", "/c", resolved]


def test_chrome_server_wraps_windows_batch(monkeypatch) -> None:
    resolved = r"C:\npm\chrome-devtools-mcp.cmd"
    monkeypatch.setattr(agent_mcp.shutil, "which", lambda _command: resolved)
    monkeypatch.setattr(
        agent_mcp,
        "os",
        SimpleNamespace(name="nt", environ={"COMSPEC": "cmd.exe"}),
    )

    server = agent_mcp._chrome_server()

    assert server["available"] is True
    assert server["command"] == "cmd.exe"
    assert server["args"][:3] == ["/d", "/c", resolved]
    assert "--browserUrl" in server["args"]


def test_resolve_launch_posix_executable_is_direct(monkeypatch) -> None:
    monkeypatch.setattr(agent_mcp.shutil, "which", lambda _command: "/usr/bin/uvx")

    command, prefix, available = agent_mcp._resolve_launch("uvx")

    assert (command, prefix, available) == ("/usr/bin/uvx", [], True)


# ---------------------------------------------------------------------------
# 内部 API 地址：CE-owner 模式只允许回环
# ---------------------------------------------------------------------------


def test_loopback_url_rewrites_lan_host_preserving_port_and_path() -> None:
    assert (
        agent_mcp._loopback_url("http://192.168.1.9:8800/api/v1")
        == "http://127.0.0.1:8800/api/v1"
    )
    assert agent_mcp._loopback_url("http://192.168.1.9") == "http://127.0.0.1"
    assert agent_mcp._loopback_url("http://localhost:8780") == "http://localhost:8780"
    assert agent_mcp._loopback_url("http://[::1]:8780") == "http://[::1]:8780"
    assert agent_mcp._loopback_url("") == ""


def test_builtin_server_forces_loopback_api_url(monkeypatch) -> None:
    monkeypatch.setenv("AIPDD_DRAMA_API_URL", "http://192.168.1.9:8800")

    server = agent_mcp._builtin_server()

    assert server["env"]["AIPDD_DRAMA_API_URL"] == "http://127.0.0.1:8800"
    assert server["env"]["DRAMACLAW_API_URL"] == "http://127.0.0.1:8800"
    assert server["env"]["DRAMACLAW_CE_OWNER"] == "1"


# ---------------------------------------------------------------------------
# 默认地址随部署形态：容器走宿主别名，本机（exe/dmg）走回环
# ---------------------------------------------------------------------------


def test_blender_default_host_depends_on_container(monkeypatch) -> None:
    monkeypatch.delenv("ST_BLENDER_HOST", raising=False)

    monkeypatch.setattr(agent_mcp, "in_container", lambda: False)
    assert agent_mcp._blender_connection() == ("127.0.0.1", "9876")

    monkeypatch.setattr(agent_mcp, "in_container", lambda: True)
    assert agent_mcp._blender_connection() == ("host.docker.internal", "9876")


def test_chrome_default_url_depends_on_container(monkeypatch) -> None:
    monkeypatch.delenv("ST_CHROME_CDP_URL", raising=False)
    monkeypatch.setattr(agent_mcp, "browsable_cdp_url", lambda url: url)

    monkeypatch.setattr(agent_mcp, "default_cdp_url", lambda: "http://127.0.0.1:9222")
    assert agent_mcp._chrome_cdp_url() == "http://127.0.0.1:9222"

    monkeypatch.setattr(
        agent_mcp,
        "default_cdp_url",
        lambda: "http://host.docker.internal:9222",
    )
    assert agent_mcp._chrome_cdp_url() == "http://host.docker.internal:9222"