# SPDX-License-Identifier: Elastic-2.0
"""MCP stdio 桥：同步工具处理器不得阻塞事件循环，失败仍要带自愈提示。"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from novelvideo.chat import aipdd_drama_mcp as bridge


def test_call_tool_runs_blocking_handler_off_event_loop(monkeypatch) -> None:
    main_thread = threading.get_ident()
    seen: dict[str, int] = {}

    def handler(arguments):
        seen["thread"] = threading.get_ident()
        return json.dumps({"ok": True, "data": arguments})

    monkeypatch.setitem(bridge.TOOLS, "_probe", ({"parameters": {}}, handler))

    result = asyncio.run(bridge.call_tool("_probe", {"value": 1}))

    assert seen["thread"] != main_thread
    assert json.loads(result[0].text) == {"ok": True, "data": {"value": 1}}


def test_call_tool_failure_gets_self_heal_hint(monkeypatch) -> None:
    def handler(_arguments):
        return json.dumps({"ok": False, "status_code": 500, "error": "boom"})

    monkeypatch.setitem(bridge.TOOLS, "_probe", ({"parameters": {}}, handler))

    result = asyncio.run(bridge.call_tool("_probe", {}))

    payload = json.loads(result[0].text)
    assert payload["self_heal"]["tool"] == "aipdd_drama_self_heal"


def test_call_tool_rejects_unknown_tool() -> None:
    with pytest.raises(ValueError, match="unknown AIPDD Drama tool"):
        asyncio.run(bridge.call_tool("_missing", {}))
