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