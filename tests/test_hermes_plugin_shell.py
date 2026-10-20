"""Hermes 插件薄壳装载链路测试（真机复核的静态替身）。

模拟 Hermes 平台加载 ``.hermes/plugins/dramaclaw/__init__.py`` 的完整链路：
1. 仓库路径装载（平台按文件/包方式加载薄壳）；
2. workspace 软链装载（Hermes 经 ``hermes_workspace`` 软链引用插件目录）；
3. 命名空间兼容（薄壳把 ``dramaclaw_tools`` 全部公共/私有名拉平）；
4. ``register(ctx)`` 注册面（60 工具 × 单 toolset，vectcut 独立凭证检查）；
5. 负路径：拷贝而非软链的布局应拒绝装载（RuntimeError 提示软链目标）。

真机安装（Hermes 平台解析 plugin.yaml / 调用 ctx 协议）仍需部署时复核，
本测试覆盖装载链路中所有仓库侧可验证的合同。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SHELL_PATH = REPO_ROOT / ".hermes" / "plugins" / "dramaclaw" / "__init__.py"

_load_seq = 0


def _load_shell(path: Path) -> types.ModuleType:
    """以独立模块名装载薄壳，避免多次装载在 sys.modules 中互相覆盖。"""
    global _load_seq
    _load_seq += 1
    spec = importlib.util.spec_from_file_location(
        f"dramaclaw_plugin_shell_{_load_seq}", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shell_loads_from_repo_path():
    from novelvideo.chat import dramaclaw_tools as impl

    shell = _load_shell(SHELL_PATH)
    # 命名空间兼容：关键名与实现模块同对象（薄壳只做转发，不复制语义）。
    assert shell.register is impl.register
    assert shell.TOOLS is impl.TOOLS
    assert shell.REGISTER_TOOLSETS is impl.REGISTER_TOOLSETS
    assert shell.tool_result is impl.tool_result
    assert shell.tool_error is impl.tool_error
    # src 引导生效：仓库 src 目录在 sys.path 中。
    assert str(REPO_ROOT / "src") in sys.path


def test_shell_loads_via_workspace_symlink(tmp_path):
    """Hermes workspace 以软链引用插件目录，resolve() 必须回到仓库真实路径。"""
    ws_plugins = tmp_path / "workspace" / "plugins"
    ws_plugins.mkdir(parents=True)
    link = ws_plugins / "dramaclaw"
    link.symlink_to(SHELL_PATH.parent)

    shell = _load_shell(link / "__init__.py")
    from novelvideo.chat import dramaclaw_tools as impl

    assert shell.register is impl.register


def test_shell_register_registers_all_tools():
    from novelvideo.chat import dramaclaw_tools as impl

    shell = _load_shell(SHELL_PATH)

    class _Ctx:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def register_tool(self, **kwargs) -> None:
            self.calls.append(kwargs)

    ctx = _Ctx()
    shell.register(ctx)
    assert len(ctx.calls) == len(impl.TOOLS) * len(impl.REGISTER_TOOLSETS)

    toolsets = {call["toolset"] for call in ctx.calls}
    assert toolsets == set(impl.REGISTER_TOOLSETS)

    for call in ctx.calls:
        assert call["description"] == call["schema"]["description"]
        if call["name"].startswith("vectcut_"):
            # VectCut 工具组不依赖主 API 凭证（服务可单独调用）。
            assert call["requires_env"] == []
            assert call["check_fn"] is impl._vectcut_available
        else:
            assert call["requires_env"] == ["DRAMACLAW_API_URL", "DRAMACLAW_AGENT_TOKEN"]
            assert call["check_fn"] is impl._available


def test_shell_rejects_copied_layout(tmp_path):
    """拷贝（而非软链）到非仓库布局时，parents[3]/src 不存在 → 拒绝装载。"""
    copied = tmp_path / "a" / "b" / "c" / "plugins" / "dramaclaw"
    copied.mkdir(parents=True)
    shell_copy = copied / "__init__.py"
    shell_copy.write_text(SHELL_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(RuntimeError, match="软链目标"):
        _load_shell(shell_copy)