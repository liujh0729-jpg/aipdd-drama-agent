# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 内置 Agent 模块（工具与 MCP 之间的对话式 Agent）。

与 DramaClaw 主线低耦合：本包不导入主线业务模块，只依赖
- 配置层（``novelvideo.config`` / ``novelvideo.model_gateway_settings``）取公共网关凭据；
- API 基建（``novelvideo.api.auth`` / ``deps``）做鉴权与项目解析；
- 资产库存储层（``novelvideo.freezone.video_node`` 的登记函数）承接「存入资产库」；
- ``novelvideo.chat.dramaclaw_mcp`` 作为可选的内置 MCP 服务器（stdio 子进程）。

功能：聊天（SSE 流式 + 工具循环）、Skill（SKILL.md 安装/启停）、记忆
（MEMORY.md）、MCP（内置 DramaClaw 工具集 + 用户自配服务器）。
"""

from novelvideo.agent.routes import router

__all__ = ["router"]