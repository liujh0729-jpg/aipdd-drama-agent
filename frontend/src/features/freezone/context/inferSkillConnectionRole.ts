// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { SkillInputRole } from "./skillRoles.ts";

/**
 * 画布连线「落到技能节点的哪个输入口」推断。
 *
 * 背景：React Flow 里用户从素材节点拖一条线到技能节点时，`targetHandle` 可能是
 * 具体输入口 role（用户精确落在某个 handle 上），也可能是空 / 通用的 `target`
 * （落在节点本体）。后者拿不到 role，连线就会被 `applySkillRoleBindingConnection`
 * 当成「找不到输入口」静默丢弃 —— 表现为「场景图连不上技能节点」。
 *
 * 这里只在前端做意图推断：前端最清楚用户拖的是哪个源节点、目标是哪个技能。
 * 后端只拿到 resolved_inputs，已丢失拖线意图，不适合反推 role（见后端分析报告）。
 *
 * 规则只产出一个「建议 role」，最终是否成边仍由调用方用 skillSpec.inputs +
 * accepts 校验把关；推断不出来时返回 null，调用方保持默认行为、不误判。
 */

export interface SkillConnectionNode {
  id: string;
  type?: string | null;
  data?: Record<string, unknown> | null;
}

export interface InferSkillConnectionRoleArgs {
  sourceNode: SkillConnectionNode | null | undefined;
  targetNode: SkillConnectionNode | null | undefined;
  /** React Flow 给出的 targetHandle —— 可能是具体 role，也可能空 / "target"。 */
  requestedTargetHandle?: string | null;
}

/** 用户没有精确落到某个输入口时 React Flow 会给出的「通用」handle 值。 */
const AMBIGUOUS_HANDLES = new Set(["", "target", "source"]);

/**
 * 源资产的 role / slot kind 词表 → 技能输入口 role。
 * token 来自源节点的 `__freezone_source.role` 或 `slot_target.kind`，
 * 这两套词表与输入口 role 命名并不完全一致（如 `selected_background` → `background`、
 * `current_sketch` → `sketch`），所以需要一张显式映射表。
 */
const SOURCE_TOKEN_TO_INPUT_ROLE: Record<string, SkillInputRole> = {
  scene_master: "scene_master",
  scene_reverse_master: "scene_reverse_master",
  selected_background: "background",
  background: "background",
  background_candidate: "background",
  current_sketch: "sketch",
  sketch: "sketch",
  current_sketch_candidate: "sketch",
  sketch_candidate: "sketch",
  current_frame: "frame",
  frame: "frame",
  character_identity: "identity",
  portrait: "identity",
  identity: "identity",
  prop_reference: "prop",
  prop: "prop",
  director_combined: "director_combined",
  source_image: "source_image",
};

function nonEmpty(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/** 源节点身上能用来分类的 role/slot token，按优先级（role 先于 slot）排列。 */
function sourceRoleTokens(node: SkillConnectionNode): string[] {
  const data = node.data ?? {};
  const tokens: string[] = [];
  const freezoneSource = asRecord(data.__freezone_source);
  const role = nonEmpty(freezoneSource?.role) ?? nonEmpty(data.role);
  if (role) tokens.push(role);
  const slotKind = nonEmpty(asRecord(data.slot_target)?.kind);
  if (slotKind) tokens.push(slotKind);
  return tokens;
}

function sourceHasImage(node: SkillConnectionNode): boolean {
  const data = node.data ?? {};
  return Boolean(
    nonEmpty(data.imageUrl) ?? nonEmpty(data.previewImageUrl) ?? nonEmpty(data.referenceImageUrl),
  );
}

export function inferSkillConnectionRole({
  sourceNode,
  targetNode,
  requestedTargetHandle,
}: InferSkillConnectionRoleArgs): string | null {
  if (!sourceNode || !targetNode) return null;

  // 用户精确落在某个输入口 handle 上 —— 尊重用户意图，不再推断。
  const requested = nonEmpty(requestedTargetHandle);
  if (requested && !AMBIGUOUS_HANDLES.has(requested)) {
    return requested;
  }

  const skillId = nonEmpty(asRecord(targetNode.data)?.skill_id) ?? "";

  if (sourceNode.type === "beatContextNode") {
    return "beat_context";
  }

  // 技能特定规则：「设为当前背景」把任意图片当作 source_image。
  if (skillId === "freezone.set_selected_background" && sourceHasImage(sourceNode)) {
    return "source_image";
  }

  // 通用规则：源资产 role/slot 词表 → 输入口 role。
  for (const token of sourceRoleTokens(sourceNode)) {
    const mapped = SOURCE_TOKEN_TO_INPUT_ROLE[token];
    if (mapped) return mapped;
  }

  return null;
}
