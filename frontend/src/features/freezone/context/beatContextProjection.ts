// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  CANVAS_NODE_TYPES,
  type CanvasEdge,
  type CanvasNode,
} from "@/features/canvas/domain/canvasNodes";

const NO_CHARACTER_MARKER = "__NO_CHARACTER__";
const NO_PROP_MARKER = "__NO_PROP__";

type ReferenceRole = "identity" | "prop";

function dataRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function edgeIdPart(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 64) || "item";
}

function referenceHandleId(role: ReferenceRole, id: string): string {
  return `${role}:${id}`;
}

function referenceTargetFor(role: ReferenceRole, id: string): Record<string, unknown> {
  return role === "identity"
    ? { kind: "identity", identity_id: id }
    : { kind: "prop", prop_id: id };
}

function referenceIdFromEdge(edge: CanvasEdge, role: ReferenceRole): string {
  const data = dataRecord(edge.data);
  const target = dataRecord(data.reference_target);
  const targetId =
    role === "identity"
      ? String(target.identity_id || target.identityId || "").trim()
      : String(target.prop_id || target.propId || "").trim();
  if (targetId) {
    return targetId;
  }
  const handle = typeof edge.targetHandle === "string" ? edge.targetHandle.trim() : "";
  const prefix = `${role}:`;
  return handle.startsWith(prefix) ? handle.slice(prefix.length).trim() : "";
}

function edgeRole(edge: CanvasEdge): string {
  const handle = typeof edge.targetHandle === "string" ? edge.targetHandle.trim() : "";
  if (handle) {
    return handle.split(":", 1)[0] ?? "";
  }
  return String(dataRecord(edge.data).role || "").trim();
}

function looseMainlineContexts(node: { data?: unknown }): Record<string, unknown>[] {
  const data = dataRecord(node.data);
  const raw = data.mainline_context;
  const contexts = Array.isArray(raw)
    ? raw.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object"))
    : [];
  const source = dataRecord(data.__freezone_source);
  const meta = dataRecord(source.meta);
  if (Object.keys(source).length > 0 || Object.keys(meta).length > 0) {
    contexts.push({
      kind: source.kind,
      role: source.role,
      label: source.label,
      character: meta.character,
      identityId: meta.identity_id,
      propId: meta.prop_id,
    });
  }
  return contexts;
}

function nodeMatchesIdentity(node: { data?: unknown }, identityId: string): boolean {
  return looseMainlineContexts(node).some((context) => {
    const kind = String(context.kind || "");
    const role = String(context.role || "");
    const candidateIdentity = String(context.identityId || context.identity_id || "").trim();
    const character = String(context.character || "").trim();
    return (
      kind === "identity" ||
      ["character_identity", "character_portrait", "character_reference"].includes(role)
    ) && (
      candidateIdentity === identityId ||
      character === identityId ||
      Boolean(candidateIdentity && identityId.startsWith(`${candidateIdentity}_`)) ||
      Boolean(character && identityId.startsWith(`${character}_`))
    );
  });
}

function nodeMatchesProp(node: { data?: unknown }, propId: string): boolean {
  return looseMainlineContexts(node).some((context) => {
    const kind = String(context.kind || "");
    const role = String(context.role || "");
    const candidateProp = String(context.propId || context.prop_id || context.label || "").trim();
    return (kind === "prop" || role.startsWith("prop_")) && candidateProp === propId;
  });
}

function bindingKey(role: ReferenceRole, referenceId: string, sourceId: string): string {
  return `${role}:${referenceId}:${sourceId}`;
}

function edgeBindingKey(edge: CanvasEdge, role: ReferenceRole): string {
  return bindingKey(role, referenceIdFromEdge(edge, role), edge.source);
}

function createReferenceEdge(
  sourceId: string,
  targetId: string,
  role: ReferenceRole,
  referenceId: string,
  beatContextNodeId: string,
): CanvasEdge {
  const targetHandle = referenceHandleId(role, referenceId);
  return {
    id: `edge_${edgeIdPart(sourceId)}_to_${edgeIdPart(targetId)}_${role}_${edgeIdPart(referenceId)}`,
    source: sourceId,
    target: targetId,
    sourceHandle: "source",
    targetHandle,
    type: "disconnectableEdge",
    data: {
      edgeKind: "role_binding",
      propagates: true,
      role,
      label: referenceId,
      reference_target: referenceTargetFor(role, referenceId),
      beatContextNodeId,
      autoBeatContextProjection: true,
    },
  };
}

function isFrameFromContextNode(node: CanvasNode): boolean {
  return (
    node.type === CANVAS_NODE_TYPES.skill &&
    dataRecord(node.data).skill_id === "freezone.frame_from_context"
  );
}

function resolveFrameSkillIds(
  beatContextNodeId: string,
  nodes: readonly CanvasNode[],
  edges: readonly CanvasEdge[],
): Set<string> {
  const frameSkillIds = new Set<string>();
  for (const edge of edges) {
    if (
      edge.source === beatContextNodeId &&
      dataRecord(edge.data).role === "beat_context" &&
      nodes.some((node) => node.id === edge.target && isFrameFromContextNode(node))
    ) {
      frameSkillIds.add(edge.target);
    }
  }
  if (frameSkillIds.size > 0) {
    return frameSkillIds;
  }
  for (const node of nodes) {
    if (isFrameFromContextNode(node)) {
      frameSkillIds.add(node.id);
    }
  }
  return frameSkillIds;
}

export function syncBeatContextMainlineEdges(
  beatContextNodeId: string,
  identities: readonly string[],
  props: readonly string[],
  nodes: readonly CanvasNode[],
  edges: readonly CanvasEdge[],
): CanvasEdge[] {
  const selectedIdentityIds = identities.filter((id) => id && id !== NO_CHARACTER_MARKER);
  const selectedPropIds = props.filter((id) => id && id !== NO_PROP_MARKER);
  const frameSkillIds = resolveFrameSkillIds(beatContextNodeId, nodes, edges);
  if (frameSkillIds.size === 0) {
    return edges as CanvasEdge[];
  }

  const identityBindings = selectedIdentityIds.flatMap((identityId) =>
    nodes
      .filter((node) => node.id !== beatContextNodeId && nodeMatchesIdentity(node, identityId))
      .map((node) => ({ id: identityId, node })),
  );
  const propBindings = selectedPropIds.flatMap((propId) =>
    nodes
      .filter((node) => node.id !== beatContextNodeId && nodeMatchesProp(node, propId))
      .map((node) => ({ id: propId, node })),
  );

  const desiredBindingKeys = new Set<string>();
  for (const binding of identityBindings) {
    desiredBindingKeys.add(bindingKey("identity", binding.id, binding.node.id));
  }
  for (const binding of propBindings) {
    desiredBindingKeys.add(bindingKey("prop", binding.id, binding.node.id));
  }

  let changed = false;
  const nextEdges = edges.filter((edge) => {
    if (!frameSkillIds.has(edge.target)) {
      return true;
    }
    const role = edgeRole(edge);
    if (role !== "identity" && role !== "prop") {
      return true;
    }
    const keep = desiredBindingKeys.has(edgeBindingKey(edge, role));
    if (!keep) {
      changed = true;
    }
    return keep;
  });

  const existingBindingKeys = new Set<string>();
  for (const edge of nextEdges) {
    const role = edgeRole(edge);
    if (role === "identity" || role === "prop") {
      existingBindingKeys.add(edgeBindingKey(edge, role));
    }
  }

  for (const targetId of frameSkillIds) {
    for (const binding of identityBindings) {
      const key = bindingKey("identity", binding.id, binding.node.id);
      if (existingBindingKeys.has(key)) continue;
      nextEdges.push(createReferenceEdge(binding.node.id, targetId, "identity", binding.id, beatContextNodeId));
      existingBindingKeys.add(key);
      changed = true;
    }
    for (const binding of propBindings) {
      const key = bindingKey("prop", binding.id, binding.node.id);
      if (existingBindingKeys.has(key)) continue;
      nextEdges.push(createReferenceEdge(binding.node.id, targetId, "prop", binding.id, beatContextNodeId));
      existingBindingKeys.add(key);
      changed = true;
    }
  }

  return changed ? nextEdges : edges as CanvasEdge[];
}
