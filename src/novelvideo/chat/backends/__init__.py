"""DramaClaw 聊天后端适配器包（hermes / claude / codex）。

从 ``novelvideo.chat.service`` 拆出：各后端的「会话构建 + 流式回复」收敛为
独立模块，service 保留编排（运行锁、心跳、后端分派、display 后处理）。
适配器以 ``_service.`` 前缀引用 service 内共享助手（命名空间延迟解析，
保持 monkeypatch 语义）；service 侧以模块级 ``__getattr__`` 兼容旧访问名。
"""

from __future__ import annotations
