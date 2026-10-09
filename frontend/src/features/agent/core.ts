// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] Agent 前端共享小工具：错误提取 / 时间格式化 / 条目命名。
import { HTTPError } from "ky";

/** 流式回合的临时助手消息 id（未落库，仅用于渲染光标动画与 key）。 */
export const AGENT_STREAM_MESSAGE_ID = "__agent_streaming__";

/**
 * 资产树 → 对话框拖拽载荷的 MIME（载荷为 AgentTurnReference 的 JSON，见
 * AssetTreePanel 的 dragStart 与 Composer 的 drop 收口）。只认这一种自定义
 * 类型，外部页面拖进来的文本/URL 不会被误当成参考素材。
 */
export const AGENT_ASSET_DRAG_MIME = "application/x-supertale-agent-reference";

/** 从后端错误响应提取可读消息（FastAPI 的 detail 优先，退化为状态码）。 */
export async function agentErrorMessage(error: unknown): Promise<string> {
  if (error instanceof HTTPError) {
    try {
      const payload = (await error.response.json()) as { detail?: unknown };
      if (typeof payload?.detail === "string" && payload.detail) return payload.detail;
    } catch {
      // 非 JSON 响应体：保留状态码。
    }
    return `HTTP ${error.response.status}`;
  }
  return error instanceof Error ? error.message : String(error);
}

/** 会话列表的时间戳：当天显示时分，跨天显示月日（跟随浏览器语言）。 */
export function formatAgentTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** 从媒体名生成资产库条目默认名（去掉扩展名）。 */
export function mediaDisplayName(item: { name: string; url: string }): string {
  const base = item.name || item.url.split("/").pop() || "";
  const withoutExt = base.replace(/\.[a-z0-9]{2,5}$/i, "");
  return withoutExt || base;
}