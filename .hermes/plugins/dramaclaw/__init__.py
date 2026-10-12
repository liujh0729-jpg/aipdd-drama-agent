"""[aipdd fork delta] DramaClaw Hermes 插件（薄壳）。

工具实现唯一真身已收敛到 ``src/novelvideo/chat/dramaclaw_tools.py``
（与 MCP stdio 桥 ``novelvideo.chat.dramaclaw_mcp`` 共用同一份）。

本文件只保留 Hermes 平台的发现入口：
- 以薄壳形式被 ``hermes_workspace`` 软链到用户 workspace 的 ``plugins/`` 下，
  由 Hermes 平台加载并调用 ``register(ctx)`` 注册工具集；
- 不再依赖平台 ``tools.registry``（实现自带等价 ``tool_result`` /
  ``tool_error``），也不再需要 MCP 桥侧的 sys.modules shim。

命名空间兼容：把实现模块的全部公共/私有名拉进本模块，历史消费者
（测试、平台侧调试）仍可按旧名访问。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 仓库布局：<repo>/.hermes/plugins/dramaclaw/__init__.py → parents[3] 为仓库根。
# Hermes workspace 通过软链引用本目录，resolve() 会回到仓库真实路径。
_SRC_DIR = Path(__file__).resolve().parents[3] / "src"
if not _SRC_DIR.is_dir():
    raise RuntimeError(
        f"DramaClaw plugin 需要仓库 src 布局，未找到: {_SRC_DIR} "
        "(请检查 .hermes/plugins/dramaclaw 软链目标)"
    )
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from novelvideo.chat import dramaclaw_tools as _impl  # noqa: E402

# 薄壳命名空间与旧实现模块保持一致（排除原模块的 __name__ 等 dunder）。
globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
del _impl