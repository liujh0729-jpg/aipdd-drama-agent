// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { Connection } from '@xyflow/react';

import type { CanvasEdge, CanvasNode } from './canvasNodes';
import { isPresetManagedEdge } from './mainlineNodeFlags';
import { getCurrentBeatContextFromNode } from '../../freezone/context/currentBeatContext.ts';
import { inputAcceptsNode } from '../../freezone/context/skillNodeInputs.ts';
import { inferSkillConnectionRole } from '../../freezone/context/inferSkillConnectionRole.ts';
import type { SkillDefinition, SkillInputRole } from '../../freezone/context/skillRoles.ts';

export interface SkillRoleBindingEdgeData {
  edgeKind: 'role_binding';
  role: SkillInputRole;
  label?: string;
  [key: string]: unknown;
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : null;
}

function roleFromConnection(connection: Connection): string | null {
  return nonEmptyString(connection.targetHandle);
}

function inputRoleFromHandle(value: unknown): string | null {
  const role = nonEmptyString(value);
  return role ? role.split(':', 1)[0] : null;
}

function referenceTargetFromHandle(role: SkillInputRole, handle: unknown): Record<string, unknown> | null {
  const value = nonEmptyString(handle);
  if (!value) {
    return null;
  }
  const separator = value.indexOf(':');
  if (separator <= 0 || value.slice(0, separator) !== role) {
    return null;
  }
  const id = value.slice(separator + 1).trim();
  if (!id) {
    return null;
  }
  if (role === 'identity') {
    return { kind: 'identity', identity_id: id };
  }
  if (role === 'prop') {
    return { kind: 'prop', prop_id: id };
  }
  return null;
}

function isReferenceRole(role: SkillInputRole): role is 'identity' | 'prop' {
  return role === 'identity' || role === 'prop';
}

function referenceHandleId(role: 'identity' | 'prop', id: string): string {
  return `${role}:${id}`;
}

function isRealReferenceId(role: 'identity' | 'prop', id: string): boolean {
  return role === 'identity' ? id !== '__NO_CHARACTER__' : id !== '__NO_PROP__';
}

function edgeMatchesRole(edge: CanvasEdge, targetNodeId: string, role: string): boolean {
  if (edge.target !== targetNodeId) {
    return false;
  }
  if (inputRoleFromHandle(edge.targetHandle) === role) {
    return true;
  }
  return (edge.data as { role?: unknown } | undefined)?.role === role;
}

function isPresetManagedRoleEdge(edge: CanvasEdge, targetNodeId: string, role: string): boolean {
  return (
    edgeMatchesRole(edge, targetNodeId, role) &&
    isPresetManagedEdge(edge)
  );
}

function fallbackInputRoleForSource({
  skillSpec,
  sourceNode,
  edges,
  targetNodeId,
}: {
  skillSpec: SkillDefinition | null | undefined;
  sourceNode: CanvasNode | undefined;
  edges: readonly CanvasEdge[];
  targetNodeId: string;
}): SkillInputRole | null {
  if (!skillSpec || !sourceNode) {
    return null;
  }

  const candidates = skillSpec.inputs
    .map((input, index) => {
      if (!inputAcceptsNode(input, sourceNode)) {
        return null;
      }
      if (
        input.cardinality === 'single' &&
        edges.some((edge) => isPresetManagedRoleEdge(edge, targetNodeId, input.role))
      ) {
        return null;
      }
      const hasExistingEdge = edges.some((edge) => edgeMatchesRole(edge, targetNodeId, input.role));
      let priority = 5;
      if (input.required && input.cardinality === 'single' && !hasExistingEdge) {
        priority = 0;
      } else if (input.required && input.cardinality !== 'single') {
        priority = 1;
      } else if (!input.required && input.cardinality === 'single' && !hasExistingEdge) {
        priority = 2;
      } else if (!input.required && input.cardinality !== 'single') {
        priority = 3;
      } else if (input.required) {
        priority = 4;
      }
      return { input, index, priority };
    })
    .filter((item): item is NonNullable<typeof item> => item !== null)
    .sort((left, right) => left.priority - right.priority || left.index - right.index);

  return candidates[0]?.input.role ?? null;
}

function resolveBeatContextReferenceHandle({
  connection,
  role,
  nodes,
  edges,
  targetNodeId,
}: {
  connection: Connection;
  role: SkillInputRole;
  nodes: readonly CanvasNode[];
  edges: readonly CanvasEdge[];
  targetNodeId: string;
}): string | null {
  if (!isReferenceRole(role)) {
    return null;
  }
  const requestedHandle = nonEmptyString(connection.targetHandle);
  if (requestedHandle?.includes(':') && inputRoleFromHandle(requestedHandle) === role) {
    return requestedHandle;
  }
  if (requestedHandle && requestedHandle !== 'target') {
    return null;
  }
  const beatContextEdge = edges.find((edge) => edgeMatchesRole(edge, targetNodeId, 'beat_context'));
  const beatContextNode = beatContextEdge
    ? nodes.find((node) => node.id === beatContextEdge.source)
    : undefined;
  const beatContext = getCurrentBeatContextFromNode(beatContextNode);
  const referenceIds = (role === 'identity'
    ? beatContext?.detected_identities ?? []
    : beatContext?.detected_props ?? []).filter((id) => isRealReferenceId(role, id));
  if (referenceIds.length === 0) {
    return null;
  }

  const candidateHandles = referenceIds.map((id) => referenceHandleId(role, id));
  return (
    candidateHandles.find(
      (handle) => !edges.some((edge) => edge.target === targetNodeId && edge.targetHandle === handle),
    ) ??
    candidateHandles[0] ??
    null
  );
}

function createRoleBindingEdge(
  connection: Connection,
  role: SkillInputRole,
  label?: string,
): CanvasEdge | null {
  if (!connection.source || !connection.target) {
    return null;
  }
  const edgeId = `e-${connection.source}-${connection.target}-${role}`;
  const referenceTarget = referenceTargetFromHandle(role, connection.targetHandle);
  const data: SkillRoleBindingEdgeData = label
    ? { edgeKind: 'role_binding', role, label }
    : { edgeKind: 'role_binding', role };
  if (referenceTarget) {
    data.reference_target = referenceTarget;
  }
  const targetHandle =
    inputRoleFromHandle(connection.targetHandle) === role
      ? nonEmptyString(connection.targetHandle) ?? role
      : role;
  return {
    id: edgeId,
    source: connection.source,
    target: connection.target,
    sourceHandle: nonEmptyString(connection.sourceHandle) ?? 'source',
    targetHandle,
    type: 'disconnectableEdge',
    data,
  };
}

function normalizeRoleBindingConnection(
  connection: Connection,
  nodes: readonly CanvasNode[],
  skillSpec: SkillDefinition | null | undefined,
): Connection | null {
  const targetNode = nodes.find((node) => node.id === connection.target);
  if (targetNode?.type === 'skillNode') {
    return connection;
  }

  const sourceNode = nodes.find((node) => node.id === connection.source);
  if (sourceNode?.type !== 'skillNode') {
    return null;
  }

  const role = inputRoleFromHandle(connection.sourceHandle);
  if (!role || skillSpec?.inputs.some((input) => input.role === role) !== true) {
    return null;
  }

  return {
    source: connection.target,
    sourceHandle: nonEmptyString(connection.targetHandle) ?? 'source',
    target: connection.source,
    targetHandle: nonEmptyString(connection.sourceHandle) ?? role,
  };
}

export function isSkillRoleConnection(
  connection: Connection,
  nodes: readonly CanvasNode[],
): boolean {
  const targetNode = nodes.find((node) => node.id === connection.target);
  if (targetNode?.type === 'skillNode' && Boolean(roleFromConnection(connection))) {
    return true;
  }
  const sourceNode = nodes.find((node) => node.id === connection.source);
  return sourceNode?.type === 'skillNode' && Boolean(nonEmptyString(connection.sourceHandle));
}

export function applySkillRoleBindingConnection({
  nodes,
  edges,
  connection,
  skillSpec,
}: {
  nodes: readonly CanvasNode[];
  edges: readonly CanvasEdge[];
  connection: Connection;
  skillSpec: SkillDefinition | null | undefined;
}): CanvasEdge[] {
  const normalizedConnection = normalizeRoleBindingConnection(connection, nodes, skillSpec);
  if (!normalizedConnection) {
    return edges as CanvasEdge[];
  }

  const targetNode = nodes.find((node) => node.id === normalizedConnection.target);
  if (targetNode?.type !== 'skillNode' || !normalizedConnection.target) {
    return edges as CanvasEdge[];
  }

  const sourceNode = nodes.find((node) => node.id === normalizedConnection.source);
  // targetHandle 可能是具体输入口 role，也可能空 / 通用 "target"（落在节点本体）。
  // 后者交给意图推断按「源节点 role/slot + 目标技能」选出真正的输入口；推断只产出
  // 建议 role，下面仍用 skillSpec.inputs + accepts 二次把关。
  const requestedRole = roleFromConnection(normalizedConnection);
  const inferredRole = inferSkillConnectionRole({
    sourceNode,
    targetNode,
    requestedTargetHandle: requestedRole,
  });
  const explicitRole = [inferredRole, requestedRole].map(inputRoleFromHandle).find(
    (candidate): candidate is string =>
      Boolean(candidate) && skillSpec?.inputs.some((input) => input.role === candidate) === true,
  );
  const role =
    explicitRole ??
    fallbackInputRoleForSource({
      skillSpec,
      sourceNode,
      edges,
      targetNodeId: normalizedConnection.target,
    });
  const inputSpec = role ? skillSpec?.inputs.find((input) => input.role === role) : undefined;
  if (!role || !inputSpec) {
    return edges as CanvasEdge[];
  }
  if (!sourceNode || !inputAcceptsNode(inputSpec, sourceNode)) {
    return edges as CanvasEdge[];
  }

  const referenceTargetHandle = resolveBeatContextReferenceHandle({
    connection: normalizedConnection,
    role: inputSpec.role,
    nodes,
    edges,
    targetNodeId: normalizedConnection.target,
  });
  const edgeConnection = referenceTargetHandle
    ? { ...normalizedConnection, targetHandle: referenceTargetHandle }
    : normalizedConnection;
  const newEdge = createRoleBindingEdge(edgeConnection, inputSpec.role, inputSpec.label);
  if (!newEdge) {
    return edges as CanvasEdge[];
  }

  if (
    inputSpec.cardinality === 'single' &&
    edges.some((edge) => isPresetManagedRoleEdge(edge, normalizedConnection.target!, inputSpec.role))
  ) {
    return edges as CanvasEdge[];
  }

  const baseEdges =
    inputSpec.cardinality === 'single'
      ? edges.filter((edge) => !edgeMatchesRole(edge, normalizedConnection.target!, inputSpec.role))
      : [...edges];
  return [...baseEdges.filter((edge) => edge.id !== newEdge.id), newEdge];
}
