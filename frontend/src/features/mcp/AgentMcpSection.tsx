// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] MCP 栏目 → 「Agent MCP 服务器」管理区块（壳）。
//
// 管理能力本体在 AgentMcpServerManager（MCP 栏目与 Agent「连接器MCP」弹窗共用）；
// 这里保持栏目页原有的自带头部形态（showHeading 缺省）。
import { AgentMcpServerManager } from "./AgentMcpServerManager";

export function AgentMcpSection() {
  return <AgentMcpServerManager />;
}