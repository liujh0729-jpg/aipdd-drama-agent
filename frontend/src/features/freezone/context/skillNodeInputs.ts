// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type {
  SkillDefinition,
  SkillInputAcceptSpec,
  SkillInputRole,
  SkillInputSpec,
  ResolvedSkillInput,
} from "./skillRoles.ts";
import { getCurrentBeatContextFromNode } from "./currentBeatContext.ts";

export interface SkillInputEdge {
  id?: string;
  source: string;
  target?: string;
  targetHandle?: string | null;
  data?: {
    role?: unknown;
    [key: string]: unknown;
  } | null;
}

export interface SkillInputNode {
  id: string;
  type?: string | null;
  data?: Record<string, unknown>;
}

function nonEmptyString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : undefined;
}

function roleFromEdge(
  edge: SkillInputEdge,
  allowedRoles: ReadonlySet<string>,
): SkillInputRole | null {
  const handleRole = nonEmptyString(edge.targetHandle);
  const normalizedHandleRole = handleRole?.split(":", 1)[0];
  if (normalizedHandleRole && allowedRoles.has(normalizedHandleRole)) {
    return normalizedHandleRole as SkillInputRole;
  }

  const dataRole = nonEmptyString(edge.data?.role);
  if (dataRole && allowedRoles.has(dataRole)) {
    return dataRole as SkillInputRole;
  }

  return null;
}

function groupEdgesByRole(
  skillSpec: SkillDefinition,
  incomingEdges: readonly SkillInputEdge[],
): Map<SkillInputRole, SkillInputEdge[]> {
  const allowedRoles = new Set(skillSpec.inputs.map((input) => input.role));
  const grouped = new Map<SkillInputRole, SkillInputEdge[]>();
  for (const edge of incomingEdges) {
    const role = roleFromEdge(edge, allowedRoles);
    if (!role) {
      continue;
    }
    const existing = grouped.get(role) ?? [];
    existing.push(edge);
    grouped.set(role, existing);
  }
  return grouped;
}

// 前后端节点类型命名不一致的归一表。
// 前端画布把「上传图 / 场景图 / 身份图 / 道具图 / 资产图 / freezone 图」统一建成同一个
// `uploadNode`（见 assetDrag.ts），靠 __freezone_source.role / slot_target 区分语义；
// 后端 skill 的 accepts.node_types 用的是更细的概念命名（uploadImageNode / sceneNode /
// identityNode / ...）。这里把一个前端节点 type 映射到它在后端词表里的全部等价别名，
// 让 node_types 门槛按语义匹配，而不是因命名差异把拖入的素材图一律拒掉。
const NODE_TYPE_ALIASES: Readonly<Record<string, readonly string[]>> = {
  uploadNode: [
    "uploadNode",
    "uploadImageNode",
    "assetImageNode",
    "freezoneImageNode",
    "sceneNode",
    "identityNode",
    "propNode",
  ],
  imageNode: ["imageNode", "imageEditNode"],
  imageGenNode: ["imageGenNode"],
  exportImageNode: ["exportImageNode", "imageNode"],
};

function nodeTypeMatchesAccepts(nodeType: string, acceptedTypes: readonly string[]): boolean {
  const aliases = NODE_TYPE_ALIASES[nodeType] ?? [nodeType];
  return aliases.some((alias) => acceptedTypes.includes(alias));
}

function inputValueForField(input: ResolvedSkillInput, field: string): unknown {
  return (input as unknown as Record<string, unknown>)[field];
}

function isEmptyInputField(value: unknown): boolean {
  return (
    value === undefined ||
    value === null ||
    value === "" ||
    (Array.isArray(value) && value.length === 0) ||
    (typeof value === "object" &&
      value !== null &&
      !Array.isArray(value) &&
      Object.keys(value).length === 0)
  );
}

function inputMediaKind(input: ResolvedSkillInput): string {
  return nonEmptyString(input.media_kind) ?? (input.image_url ? "image" : input.text ? "text" : "");
}

function inputMatchesAccepts(
  input: ResolvedSkillInput,
  node: SkillInputNode,
  accepts: SkillInputAcceptSpec,
): boolean {
  if (accepts.node_types?.length && !nodeTypeMatchesAccepts(node.type ?? "", accepts.node_types)) {
    return false;
  }

  for (const field of accepts.has_field ?? []) {
    if (isEmptyInputField(inputValueForField(input, field))) {
      return false;
    }
  }

  if (accepts.media_kinds?.length && !accepts.media_kinds.includes(inputMediaKind(input))) {
    return false;
  }

  const provenanceRequired = Boolean(
    accepts.canonical_slot_kinds?.length || accepts.candidate_origin_skill_ids?.length,
  );
  if (!provenanceRequired) {
    return true;
  }

  const slotKind =
    typeof input.slot_target?.kind === "string" ? input.slot_target.kind : "";
  const originSkillId =
    typeof input.candidate_origin?.skill_id === "string"
      ? input.candidate_origin.skill_id
      : "";
  const hasSlotMatch = Boolean(
    accepts.canonical_slot_kinds?.length && accepts.canonical_slot_kinds.includes(slotKind),
  );
  const hasCandidateMatch = Boolean(
    accepts.candidate_origin_skill_ids?.length &&
      accepts.candidate_origin_skill_ids.includes(originSkillId),
  );
  const hasPlainMediaMatch = Boolean(accepts.media_kinds?.length && inputMediaKind(input));
  return hasSlotMatch || hasCandidateMatch || hasPlainMediaMatch;
}

export function inputAcceptsNode(inputSpec: SkillInputSpec, node: SkillInputNode): boolean {
  return inputMatchesAccepts(resolveInputSnapshot(inputSpec.role, node), node, inputSpec.accepts);
}

function edgeAcceptedForInput(
  input: SkillInputSpec,
  edge: SkillInputEdge,
  nodesById?: ReadonlyMap<string, SkillInputNode>,
): boolean {
  if (!nodesById) {
    return true;
  }
  const sourceNode = nodesById.get(edge.source);
  return sourceNode ? inputAcceptsNode(input, sourceNode) : false;
}

function edgesForInput(
  skillSpec: SkillDefinition,
  incomingEdges: readonly SkillInputEdge[],
  nodesById?: ReadonlyMap<string, SkillInputNode>,
): SkillInputEdge[] {
  const grouped = groupEdgesByRole(skillSpec, incomingEdges);
  const ordered: SkillInputEdge[] = [];
  for (const input of skillSpec.inputs) {
    const edges = (grouped.get(input.role) ?? []).filter((edge) =>
      edgeAcceptedForInput(input, edge, nodesById),
    );
    if (input.cardinality === "single") {
      const latest = edges[edges.length - 1];
      if (latest) {
        ordered.push(latest);
      }
      continue;
    }
    ordered.push(...edges);
  }
  return ordered;
}

function snapshotObject(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function resolveImageUrl(data: Record<string, unknown>): string | undefined {
  return (
    nonEmptyString(data.imageUrl) ??
    nonEmptyString(data.previewImageUrl) ??
    nonEmptyString(data.referenceImageUrl)
  );
}

function resolveText(data: Record<string, unknown>): string | undefined {
  return (
    nonEmptyString(data.text) ??
    nonEmptyString(data.content) ??
    nonEmptyString(data.prompt)
  );
}

function addObjectField(
  target: ResolvedSkillInput,
  key: "slot_target" | "reference_target" | "candidate_origin",
  value: unknown,
): void {
  const objectValue = snapshotObject(value);
  if (objectValue) {
    target[key] = objectValue;
  }
}

function referenceTargetFromHandle(
  role: SkillInputRole,
  targetHandle: unknown,
): Record<string, unknown> | undefined {
  const handle = nonEmptyString(targetHandle);
  if (!handle) return undefined;
  const separator = handle.indexOf(":");
  if (separator <= 0) return undefined;
  const handleRole = handle.slice(0, separator);
  const id = handle.slice(separator + 1).trim();
  if (!id || handleRole !== role) return undefined;
  if (
    (role === "identity" && id === "__NO_CHARACTER__") ||
    (role === "prop" && id === "__NO_PROP__")
  ) {
    return undefined;
  }
  if (role === "identity") {
    return { kind: "identity", identity_id: id };
  }
  if (role === "prop") {
    return { kind: "prop", prop_id: id };
  }
  return undefined;
}

function isNoReferenceEdge(edge: SkillInputEdge, role: SkillInputRole): boolean {
  if (role !== "identity" && role !== "prop") {
    return false;
  }
  const handle = nonEmptyString(edge.targetHandle);
  if (
    (role === "identity" && handle === "identity:__NO_CHARACTER__") ||
    (role === "prop" && handle === "prop:__NO_PROP__")
  ) {
    return true;
  }
  const referenceTarget = snapshotObject(edge.data?.reference_target);
  if (!referenceTarget) {
    return false;
  }
  return role === "identity"
    ? nonEmptyString(referenceTarget.identity_id) === "__NO_CHARACTER__"
    : nonEmptyString(referenceTarget.prop_id) === "__NO_PROP__";
}

function sourceRolePriority(node: SkillInputNode | undefined): number {
  const source = snapshotObject(node?.data?.__freezone_source);
  const role = nonEmptyString(source?.role) ?? "";
  if (role === "character_identity" || role === "prop_reference") {
    return 0;
  }
  if (role === "character_portrait") {
    return 1;
  }
  return 2;
}

function referenceInputKey(edge: SkillInputEdge, role: SkillInputRole): string | null {
  if (role !== "identity" && role !== "prop") {
    return null;
  }
  const referenceTarget =
    snapshotObject(edge.data?.reference_target) ?? referenceTargetFromHandle(role, edge.targetHandle);
  if (!referenceTarget) {
    return null;
  }
  const referenceId =
    role === "identity"
      ? nonEmptyString(referenceTarget.identity_id)
      : nonEmptyString(referenceTarget.prop_id);
  return referenceId ? `${role}:${referenceId}` : null;
}

function dedupeReferenceInputEdges(
  edges: SkillInputEdge[],
  allowedRoles: ReadonlySet<string>,
  nodesById: ReadonlyMap<string, SkillInputNode>,
): SkillInputEdge[] {
  const selectedIndexByKey = new Map<string, number>();
  const droppedIndexes = new Set<number>();
  for (const [index, edge] of edges.entries()) {
    const role = roleFromEdge(edge, allowedRoles);
    if (!role) {
      continue;
    }
    const key = referenceInputKey(edge, role);
    if (!key) {
      continue;
    }
    const existingIndex = selectedIndexByKey.get(key);
    if (existingIndex === undefined) {
      selectedIndexByKey.set(key, index);
      continue;
    }
    const currentPriority = sourceRolePriority(nodesById.get(edge.source));
    const existingPriority = sourceRolePriority(nodesById.get(edges[existingIndex].source));
    if (currentPriority < existingPriority) {
      droppedIndexes.add(existingIndex);
      selectedIndexByKey.set(key, index);
    } else {
      droppedIndexes.add(index);
    }
  }
  return edges.filter((_edge, index) => !droppedIndexes.has(index));
}

function objectList(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter(
      (item): item is Record<string, unknown> => Boolean(item) && typeof item === "object",
    )
    : [];
}

function synthesizeSlotTargetFromContext(
  contexts: readonly Record<string, unknown>[],
  source: Record<string, unknown> | null,
): Record<string, unknown> | undefined {
  for (const context of contexts) {
    const kind = nonEmptyString(context.kind);
    const role = nonEmptyString(context.role);
    const sceneId = nonEmptyString(context.sceneId) ?? nonEmptyString(context.scene_id);
    if (
      kind === "scene" &&
      sceneId &&
      (role === "scene_master" || role === "scene_reverse_master")
    ) {
      return { kind: role, scene_id: sceneId };
    }
    const identityId =
      nonEmptyString(context.identityId) ??
      nonEmptyString(context.identity_id) ??
      nonEmptyString(context.character);
    if (kind === "identity" && identityId) {
      return { kind: role === "portrait" ? "portrait" : "identity", identity_id: identityId };
    }
    const propId = nonEmptyString(context.propId) ?? nonEmptyString(context.prop_id);
    if (kind === "prop" && propId) {
      return { kind: "prop", prop_id: propId };
    }
    const episode = typeof context.episode === "number" ? context.episode : undefined;
    const beat = typeof context.beat === "number" ? context.beat : undefined;
    if (
      episode &&
      beat &&
      (
        kind === "sketch" ||
        kind === "frame" ||
        kind === "selected_background" ||
        kind === "director_combined"
      )
    ) {
      return { kind, episode, beat };
    }
  }

  const role = nonEmptyString(source?.role);
  const meta = snapshotObject(source?.meta);
  const sceneId =
    nonEmptyString(meta?.scene_id) ??
    nonEmptyString(meta?.scene) ??
    nonEmptyString(meta?.scene_name);
  if (sceneId && (role === "scene_master" || role === "scene_reverse_master")) {
    return { kind: role, scene_id: sceneId };
  }
  const identityId =
    nonEmptyString(meta?.identity_id) ??
    nonEmptyString(meta?.identityId) ??
    nonEmptyString(meta?.character);
  if (identityId && (role === "identity" || role === "portrait")) {
    return { kind: role, identity_id: identityId };
  }
  const propId = nonEmptyString(meta?.prop_id) ?? nonEmptyString(meta?.propId);
  if (propId && role === "prop") {
    return { kind: "prop", prop_id: propId };
  }
  return undefined;
}

function resolveInputSnapshot(
  role: SkillInputRole,
  node: SkillInputNode,
  edge?: SkillInputEdge,
): ResolvedSkillInput {
  const data = node.data ?? {};
  const resolved: ResolvedSkillInput = {
    role,
    node_id: node.id,
    node_type: node.type ?? "",
  };

  const imageUrl = resolveImageUrl(data);
  if (imageUrl) {
    resolved.image_url = imageUrl;
  }

  const text = resolveText(data);
  if (text) {
    resolved.text = text;
  }

  addObjectField(resolved, "slot_target", data.slot_target);
  const edgeReferenceTarget =
    snapshotObject(edge?.data?.reference_target) ??
    referenceTargetFromHandle(role, edge?.targetHandle);
  addObjectField(resolved, "reference_target", edgeReferenceTarget);
  addObjectField(resolved, "candidate_origin", data.candidate_origin);
  const provenanceContexts = objectList(data.mainline_context);
  // `mainline_context` may live on candidate/image nodes as provenance. Treat it
  // as executable Beat context only when the graph explicitly wires a
  // beat_context input; normal image/sketch/frame inputs must not silently carry
  // an old Beat into a later skill.
  const mainlineContexts = role === "beat_context" ? provenanceContexts : [];
  if (mainlineContexts.length > 0) {
    resolved.mainline_context = mainlineContexts;
  }
  const freezoneSource = snapshotObject(data.__freezone_source);
  if (freezoneSource) {
    resolved.freezone_source = freezoneSource;
  }
  if (!resolved.slot_target) {
    const inferredSlotTarget = synthesizeSlotTargetFromContext(
      provenanceContexts,
      freezoneSource ?? null,
    );
    if (inferredSlotTarget) {
      resolved.slot_target = inferredSlotTarget;
    }
  }

  const mediaKind =
    nonEmptyString(data.media_kind) ?? (imageUrl ? "image" : text ? "text" : undefined);
  if (mediaKind) {
    resolved.media_kind = mediaKind;
  }

  if (role === "beat_context") {
    const beatContext = getCurrentBeatContextFromNode(node);
    if (beatContext) {
      resolved.beat_context = beatContext;
    }
  }
  return resolved;
}

export function isSkillReadyToSubmit(
  skillSpec: SkillDefinition,
  incomingEdges: readonly SkillInputEdge[],
  nodesById?: ReadonlyMap<string, SkillInputNode>,
): boolean {
  const allowedRoles = new Set(skillSpec.inputs.map((input) => input.role));
  const grouped = new Map<SkillInputRole, SkillInputEdge[]>();
  for (const edge of edgesForInput(skillSpec, incomingEdges, nodesById)) {
    const role = roleFromEdge(edge, allowedRoles);
    if (!role) {
      continue;
    }
    const existing = grouped.get(role) ?? [];
    existing.push(edge);
    grouped.set(role, existing);
  }
  return skillSpec.inputs.every((input) => {
    if (!input.required) {
      return true;
    }
    return (grouped.get(input.role)?.length ?? 0) > 0;
  });
}

export function resolveInputsForSkill(
  skillSpec: SkillDefinition,
  skillNode: SkillInputNode,
  incomingEdges: readonly SkillInputEdge[],
  nodesById: ReadonlyMap<string, SkillInputNode>,
): ResolvedSkillInput[] {
  const targetEdges = incomingEdges.filter((edge) => !edge.target || edge.target === skillNode.id);
  const allowedRoles = new Set(skillSpec.inputs.map((input) => input.role));
  return dedupeReferenceInputEdges(
    edgesForInput(skillSpec, targetEdges, nodesById),
    allowedRoles,
    nodesById,
  )
    .map((edge) => {
      const role = roleFromEdge(edge, allowedRoles);
      const sourceNode = nodesById.get(edge.source);
      if (!role || !sourceNode) {
      return null;
      }
      if (isNoReferenceEdge(edge, role)) {
        return null;
      }
      return resolveInputSnapshot(role, sourceNode, edge);
    })
    .filter((item): item is ResolvedSkillInput => Boolean(item));
}
