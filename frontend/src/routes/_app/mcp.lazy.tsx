// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 全局「MCP」接入指南：未进入项目（仪表盘 / 登录页 icon）时的落点。
// project 缺省 → 配置里的项目编号走占位符，页面顶部给「进入项目」快捷入口；
// 项目内 /projects/$project/mcp 仍传实参，复制出来即为开箱即用配置。
import { createLazyFileRoute } from "@tanstack/react-router";

import { McpPage } from "@/features/mcp/McpPage";

export const Route = createLazyFileRoute("/_app/mcp")({
  component: McpPage,
});