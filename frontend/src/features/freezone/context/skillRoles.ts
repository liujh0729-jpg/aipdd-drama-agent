// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
export type SkillProvider = "freezone_mainline" | "agent" | "tool" | "workflow";
export const SKILL_SCHEMA_VERSION = "skill.v1";

export type SkillInputRole =
  | "beat_context"
  | "sketch"
  | "background"
  | "identity"
  | "prop"
  | "scene"
  | "scene_master"
  | "scene_reverse_master"
  | "director_combined"
  | "source_image"
  | "frame";

export type SkillOutputRole =
  | "current_sketch_candidate"
  | "current_frame_candidate"
  | "scene_360_candidate"
  | "selected_background"
  | "director_combined"
  | "review_report";

export type SkillCardinality = "single" | "multi";
export type SkillMediaType = "image" | "text" | "json" | "node_patch" | "graph_patch";

export interface SkillCapabilities {
  can_read_canvas?: boolean;
  can_read_project_state?: boolean;
  can_access_network?: boolean;
  can_propose_canvas_patch?: boolean;
  can_apply_canvas_patch?: boolean;
}

export interface SkillInputAcceptSpec {
  node_types?: string[];
  canonical_slot_kinds?: string[];
  candidate_origin_skill_ids?: string[];
  media_kinds?: string[];
  has_field?: string[];
}

export interface SkillInputSpec {
  schema_version?: string;
  role: SkillInputRole;
  label: string;
  accepts: SkillInputAcceptSpec;
  required: boolean;
  cardinality: SkillCardinality;
}

export interface SkillOutputSpec {
  schema_version?: string;
  role: SkillOutputRole;
  label: string;
  media_type: SkillMediaType;
  node_type: string;
  pushable: boolean;
  requires_apply?: boolean;
}

export interface SkillParameterSpec {
  type?: string;
  label?: string;
  default?: unknown;
  options?: unknown[];
  [key: string]: unknown;
}

export type SkillParameterDefinitions = Record<string, SkillParameterSpec>;

export interface SkillDefinition {
  schema_version?: string;
  id: string;
  provider: SkillProvider;
  capabilities?: SkillCapabilities;
  display_name: string;
  description: string;
  inputs: SkillInputSpec[];
  outputs: SkillOutputSpec[];
  parameters?: SkillParameterDefinitions;
}

export interface CandidateOrigin {
  skill_id?: string;
  skill_node_id?: string;
  [key: string]: unknown;
}

export interface ResolvedSkillInput {
  role: SkillInputRole;
  node_id: string;
  node_type: string;
  image_url?: string;
  slot_target?: Record<string, unknown>;
  reference_target?: Record<string, unknown>;
  candidate_origin?: CandidateOrigin;
  mainline_context?: Record<string, unknown>[];
  freezone_source?: Record<string, unknown>;
  beat_context?: Record<string, unknown>;
  media_kind?: string;
  text?: string;
}
