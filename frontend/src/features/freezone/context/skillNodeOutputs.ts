// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  CANVAS_NODE_TYPES,
  type CanvasNodeData,
  type CanvasNodeType,
} from "../../canvas/domain/canvasNodes.ts";
import type { SkillRunOutput } from "../../../api/skills.ts";

function recordValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? value as Record<string, unknown> : null;
}

export function outputLabel(output: SkillRunOutput): string {
  const label = (output as { label?: unknown }).label;
  return typeof label === "string" && label.trim() ? label.trim() : output.role;
}

export function outputText(output: SkillRunOutput): string {
  if (typeof output.text === "string" && output.text.trim()) {
    return output.text;
  }
  if (output.json_value !== undefined) {
    return JSON.stringify(output.json_value, null, 2);
  }
  return JSON.stringify(output, null, 2);
}

function imageAspectRatioForOutput(output: SkillRunOutput): string {
  const aspectRatio = (output as { aspect_ratio?: unknown }).aspect_ratio;
  if (typeof aspectRatio === "string" && aspectRatio.trim()) {
    return aspectRatio.trim();
  }
  if (output.role === "scene_360_candidate") {
    return "2:1";
  }
  return "16:9";
}

export function nodeTypeForOutput(output: SkillRunOutput): CanvasNodeType {
  return output.media_type === "image" ? CANVAS_NODE_TYPES.imageGen : CANVAS_NODE_TYPES.textAnnotation;
}

export function nodeDataForOutput(
  output: SkillRunOutput,
  skillId: string,
  skillNodeId: string,
): Partial<CanvasNodeData> {
  const mainlineContext = Array.isArray(output.mainline_context)
    ? output.mainline_context
    : undefined;
  const directorControlBundle = recordValue(output.director_control_bundle);
  const common = {
    displayName: outputLabel(output),
    user_spawned: true,
    candidate_origin: { skill_id: skillId, skill_node_id: skillNodeId },
    output_role: output.role,
    media_kind: output.media_type,
    ...(output.slot_target ? { slot_target: output.slot_target } : {}),
    ...(mainlineContext ? { mainline_context: mainlineContext } : {}),
    ...(directorControlBundle ? { director_control_bundle: directorControlBundle } : {}),
  };

  if (output.media_type === "image") {
    return {
      ...common,
      imageUrl: output.image_url ?? null,
      previewImageUrl: output.image_url ?? null,
      aspectRatio: imageAspectRatioForOutput(output),
      committed_at: output.committed === true ? new Date().toISOString() : null,
      committed_slot_url:
        typeof output.committed_slot_url === "string" && output.committed_slot_url.trim()
          ? output.committed_slot_url
          : null,
    };
  }

  return {
    ...common,
    content: outputText(output),
    media_kind: output.media_type === "text" ? "text" : output.media_type,
  };
}
