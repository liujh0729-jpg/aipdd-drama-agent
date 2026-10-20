"""MCP bridge for AIPDD Drama tools.

AIPDD Drama 工具实现收敛于 ``novelvideo.chat.dramaclaw_tools``（Hermes 插件
``.hermes/plugins/dramaclaw`` 是它的薄壳，两者共用同一份实现）。本模块把
同一工具集暴露为 stdio MCP 服务器，供 Claude、Codex 等 MCP 客户端调用；
不再需要 ``tools.registry`` 的 sys.modules shim，也不再按路径装载插件。

历史模块名 ``novelvideo.chat.dramaclaw_mcp`` 保留为兼容别名（re-export 本模块），
旧配置与历史测试无需迁移。
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from novelvideo.chat import dramaclaw_tools

# 兼容旧访问名：历史代码与测试以 ``PLUGIN`` 指代工具实现模块。
PLUGIN = dramaclaw_tools


def _tool_index(plugin: Any) -> dict[str, tuple[dict[str, Any], Any]]:
    index: dict[str, tuple[dict[str, Any], Any]] = {}
    for entry in getattr(plugin, "TOOLS", ()):
        if not isinstance(entry, tuple) or len(entry) != 3:
            continue
        name, schema, handler = entry
        if isinstance(name, str) and isinstance(schema, dict) and callable(handler):
            index[name] = (schema, handler)
    return index


TOOLS = _tool_index(PLUGIN)
SERVER = Server("aipdd-drama", version="0.1.0")


@SERVER.list_tools()
async def list_tools() -> list[types.Tool]:
    result: list[types.Tool] = []
    for name, (schema, _handler) in sorted(TOOLS.items()):
        parameters = schema.get("parameters") if isinstance(schema, dict) else None
        result.append(
            types.Tool(
                name=name,
                description=str(schema.get("description") or ""),
                inputSchema=parameters if isinstance(parameters, dict) else {"type": "object"},
            )
        )
    return result


@SERVER.call_tool(validate_input=True)
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    item = TOOLS.get(name)
    if item is None:
        raise ValueError(f"unknown AIPDD Drama tool: {name}")
    _schema, handler = item
    # 工具处理器是同步阻塞实现（urllib 请求，自愈可达 180s）。直接调用会在
    # 事件循环里串行化所有 MCP 请求，连取消/心跳都无法及时处理。
    text = await asyncio.to_thread(handler, arguments or {})
    text = dramaclaw_tools.hint_tool_output(text, tool_name=name)
    return [types.TextContent(type="text", text=str(text or ""))]


async def _main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await SERVER.run(
            read_stream,
            write_stream,
            SERVER.create_initialization_options(),
        )


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
