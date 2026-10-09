from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_plugin_module():
    # P5 薄壳化：.hermes/plugins/dramaclaw 现在是薄壳（Hermes 发现面），
    # 先验证其可独立加载、工具表可用。
    shell_path = (
        Path(__file__).resolve().parents[1]
        / ".hermes" / "plugins" / "dramaclaw" / "__init__.py"
    )
    shell_spec = importlib.util.spec_from_file_location("test_dramaclaw_shell", shell_path)
    assert shell_spec is not None and shell_spec.loader is not None
    shell = importlib.util.module_from_spec(shell_spec)
    shell_spec.loader.exec_module(shell)
    assert shell.TOOLS

    # 实现真身（与 MCP 桥同一份源码）：按路径独立装载为新模块对象，并把
    # tool_result/tool_error 还原为 identity 语义（历史上由 tools.registry
    # shim 注入；mock 成直通便于直接观测结构化结果）。
    impl_path = (
        Path(__file__).resolve().parents[1]
        / "src" / "novelvideo" / "chat" / "dramaclaw_tools.py"
    )
    spec = importlib.util.spec_from_file_location("test_dramaclaw_plugin", impl_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.tool_result = lambda value: value
    module.tool_error = lambda value: value
    return module


def test_dramaclaw_plugin_adds_chat_error_without_replacing_task_error():
    plugin = _load_plugin_module()
    raw_error = "Content filter triggered. Finish reason: 'content_filter'"

    result = plugin._with_chat_error_hints(
        {
            "ok": True,
            "data": [
                {
                    "status": "failed",
                    "error": raw_error,
                    "metadata": {"provider_response_id": "resp_123"},
                }
            ],
        }
    )

    task = result["data"][0]
    assert task["error"] == raw_error
    assert task["chat_error"] == plugin.TEXT_CONTENT_FILTER_CHAT_ERROR
    assert "Do not quote the raw provider JSON" in task["agent_instruction"]


def test_dramaclaw_plugin_adds_voice_prereq_chat_error():
    plugin = _load_plugin_module()
    raw_error = "Beat 03 解说声线缺失：项目解说人声线未配置，请上传或录制解说人音频"

    result = plugin._with_chat_error_hints(
        {
            "status_code": 200,
            "ok": False,
            "code": "voice_prereq_required",
            "error": raw_error,
        }
    )

    assert result["error"] == raw_error
    assert "配音任务没有成功启动" in result["chat_error"]
    assert "资产" in result["chat_error"]
    assert raw_error in result["chat_error"]
    assert "Do not start another tool" in result["agent_instruction"]


def test_dramaclaw_plugin_adds_render_prereq_chat_error():
    plugin = _load_plugin_module()
    raw_error = (
        "Render 重生未生成可用图片（mode=1x1_2-3, beats=[1, 2, 3]）："
        "Render 模式需要草图但未找到覆盖 beat 1-1 的草图"
    )

    result = plugin._with_chat_error_hints(
        {
            "ok": True,
            "data": [
                {
                    "status": "failed",
                    "error": raw_error,
                }
            ],
        }
    )

    task = result["data"][0]
    assert task["error"] == raw_error
    assert "Render 任务没有生成可用图片" in task["chat_error"]
    assert "资产" in task["chat_error"]
    assert raw_error in task["chat_error"]
    assert "Do not start another tool" in task["agent_instruction"]
