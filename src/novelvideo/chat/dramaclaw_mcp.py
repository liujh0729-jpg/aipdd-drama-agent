"""[aipdd fork delta] 兼容别名：旧模块名 ``novelvideo.chat.dramaclaw_mcp``。

工具 MCP 桥的唯一实现已更名为 ``novelvideo.chat.aipdd_drama_mcp``（对外品牌
AIPDD Drama）。本模块仅 re-export 新模块的全部公共/私有名，历史代码与测试
仍可按旧名访问；``python -m novelvideo.chat.dramaclaw_mcp`` 继续可用。
"""

from __future__ import annotations

from novelvideo.chat import aipdd_drama_mcp as _impl

# 命名空间与真实模块保持一致（排除 dunder），保证旧访问名与装饰器注册态同源。
globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
del _impl

# ``python -m`` 场景：把本模块识别为真实实现，避免 stdio_server 双实例。
if __name__ == "__main__":
    from novelvideo.chat.aipdd_drama_mcp import main as _main

    _main()
