// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  isExportImageNode,
  isImageEditNode,
  isImageGenNode,
  isPano360ViewerNode,
  isStoryboardGenNode,
  isUploadNode,
  type CanvasNode,
} from "@/features/canvas/domain/canvasNodes";
import type {
  DirectorStageManifest,
  DirectorStageSourceKind,
  DirectorStageSourceType,
  DirectorWorldSource,
} from "@/features/viewer-kit/three-d/directorManifest";

export function canvasNodeLabel(node: CanvasNode): string {
  const displayName = (node.data as { displayName?: unknown }).displayName;
  return typeof displayName === "string" && displayName.trim() ? displayName : node.type ?? node.id;
}

export function imageUrlFromCanvasNode(node: CanvasNode): string | null {
  const data = node.data as { imageUrl?: unknown; previewImageUrl?: unknown; referenceImageUrl?: unknown };
  if (typeof data.imageUrl === "string" && data.imageUrl.length > 0) return data.imageUrl;
  if (typeof data.previewImageUrl === "string" && data.previewImageUrl.length > 0) return data.previewImageUrl;
  if (typeof data.referenceImageUrl === "string" && data.referenceImageUrl.length > 0) return data.referenceImageUrl;
  return null;
}

export function isCanvasImageNode(node: CanvasNode): boolean {
  return (
    isImageGenNode(node) ||
    isUploadNode(node) ||
    isImageEditNode(node) ||
    isExportImageNode(node) ||
    isStoryboardGenNode(node)
  );
}

export function isPanoAspectRatio(node: CanvasNode): boolean {
  const data = node.data as { aspectRatio?: unknown };
  return data.aspectRatio === "2:1";
}

export function isPanoImageCanvasNode(node: CanvasNode): boolean {
  if (isPano360ViewerNode(node)) return true;
  const data = node.data as {
    output_role?: unknown;
    media_kind?: unknown;
  };
  const role = typeof data.output_role === "string" ? data.output_role : "";
  if (role === "scene_360" || role === "scene_360_candidate" || role === "scene_director_pano_360") {
    return true;
  }
  if (data.media_kind === "pano360") return true;
  return isPanoAspectRatio(node);
}

export function directorPanoSourceFromCanvasNode(node: CanvasNode): DirectorWorldSource | null {
  if (!isPanoImageCanvasNode(node)) return null;
  const url = imageUrlFromCanvasNode(node);
  if (!url) return null;
  return {
    id: `upstream-pano:${node.id}`,
    source_type: "pano360",
    source_kind: "pano",
    label: canvasNodeLabel(node),
    url,
    pano_url: url,
    slot_kind: "scene_director_pano_360",
  };
}

export function directorSourceIdentityUrl(url: string): string {
  const trimmed = url.trim();
  if (!trimmed) return "";
  const withoutHash = trimmed.split("#", 1)[0] ?? "";
  return withoutHash.split("?", 1)[0] ?? "";
}

export function directorSourceUrl(source: DirectorWorldSource): string | null {
  return source.pano_url ?? source.ply_url ?? source.url ?? null;
}

function directorSourceIdentityUrlForSource(source: DirectorWorldSource): string {
  const url = directorSourceUrl(source);
  return url ? directorSourceIdentityUrl(url) : "";
}

function isSameDirectorWorldSource(
  a: DirectorWorldSource,
  b: DirectorWorldSource,
): boolean {
  if (a.id && b.id && a.id === b.id) return true;
  const aUrl = directorSourceIdentityUrlForSource(a);
  const bUrl = directorSourceIdentityUrlForSource(b);
  return Boolean(aUrl && bUrl && aUrl === bUrl);
}

function mergeDirectorWorldSource(
  existing: DirectorWorldSource,
  incoming: DirectorWorldSource,
): DirectorWorldSource {
  const id = existing.id && incoming.id && existing.id !== incoming.id
    ? existing.id
    : incoming.id ?? existing.id;
  return {
    ...existing,
    ...incoming,
    id,
    current: existing.current || incoming.current || undefined,
    transform: incoming.transform ?? existing.transform,
  };
}

export function mergeDirectorWorldSources(
  existing: DirectorWorldSource[],
  ...incoming: Array<DirectorWorldSource | null | undefined>
): DirectorWorldSource[] {
  const next = existing.filter((source) => source.source_type !== "mesh");
  for (const source of incoming) {
    if (!source || source.source_type === "mesh") continue;
    const existingIndex = next.findIndex((item) => isSameDirectorWorldSource(item, source));
    if (existingIndex >= 0) {
      next[existingIndex] = mergeDirectorWorldSource(next[existingIndex], source);
      continue;
    }
    next.push(source);
  }
  return next;
}

type ManifestSourceLike = {
  id?: string;
  kind?: DirectorStageSourceKind;
  label?: string;
  source_type?: DirectorStageSourceType;
  source_kind?: DirectorStageSourceKind;
  ply_url?: string;
  url?: string;
  pano_url?: string;
  pano_fs?: string;
  collision_glb_url?: string;
  slot_kind?: "scene_director_pano_360" | "scene_360_candidate";
  fs?: string;
  current?: boolean;
};

function worldSourceFromManifestSource(
  source: ManifestSourceLike,
  fallbackId: string,
): DirectorWorldSource | null {
  if (source.kind === "active" || source.source_kind === "active") return null;
  const sourceType = source.source_type ?? "sog";
  const url = source.pano_url ?? source.ply_url ?? source.url;
  if (!url) return null;
  const sourceKind = source.source_kind ?? source.kind ?? "custom";
  return {
    id: source.id ?? fallbackId,
    source_type: sourceType,
    source_kind: sourceKind,
    label: source.label ?? sourceKind,
    ply_url: source.ply_url,
    url: source.url ?? url,
    pano_url: source.pano_url,
    pano_fs: source.pano_fs,
    collision_glb_url: source.collision_glb_url,
    slot_kind: source.slot_kind,
    fs: source.fs,
    current: source.current,
  };
}

function sourceOptionFallbackId(
  manifest: DirectorStageManifest,
  source: ManifestSourceLike,
): string {
  const sourceType = source.source_type ?? "sog";
  const url = directorSourceIdentityUrl(source.pano_url ?? source.ply_url ?? source.url ?? "");
  if (
    manifest.mode === "scene" &&
    sourceType === "pano360" &&
    source.slot_kind === "scene_director_pano_360"
  ) {
    return `scene-pano:${manifest.scene_id}`;
  }
  return `legacy:${source.kind ?? source.source_kind ?? "custom"}:${sourceType}:${url}`;
}

function manifestSourceFallbackId(source: ManifestSourceLike): string {
  const sourceType = source.source_type ?? "sog";
  const sourceKind = source.source_kind ?? source.kind ?? "custom";
  const url = directorSourceIdentityUrl(source.pano_url ?? source.ply_url ?? source.url ?? "");
  return `manifest-source:${sourceKind}:${sourceType}:${url}`;
}

export function directorWorldSourcesFromManifest(
  manifest: DirectorStageManifest,
): DirectorWorldSource[] {
  if (manifest.sources?.length) {
    return mergeDirectorWorldSources([], ...(manifest.sources ?? []));
  }
  if (manifest.source_options?.length) {
    return mergeDirectorWorldSources(
      [],
      ...(manifest.source_options ?? []).map((source) =>
        worldSourceFromManifestSource(source, sourceOptionFallbackId(manifest, source)),
      ),
    );
  }
  return mergeDirectorWorldSources(
    [],
    worldSourceFromManifestSource(manifest.source, manifestSourceFallbackId(manifest.source)),
  );
}

export function mergeDirectorStageManifestSources(
  manifest: DirectorStageManifest,
  incoming: Array<DirectorWorldSource | null | undefined>,
): DirectorStageManifest {
  const mergedSources = mergeDirectorWorldSources(
    directorWorldSourcesFromManifest(manifest),
    ...incoming,
  );
  if (mergedSources.length === 0) return manifest;
  return {
    ...manifest,
    sources: mergedSources,
    active_source_id:
      manifest.active_source_id ??
      mergedSources.find((source) => source.current)?.id ??
      mergedSources[0]?.id,
  };
}

export function mergeDirectorSavedSceneMaps<T>(
  localScenes: Record<string, T | null | undefined> | null | undefined,
  manifestScenes: Record<string, T | null | undefined> | null | undefined,
): Record<string, T> | null {
  const merged: Record<string, T> = {};
  for (const [sourceId, snapshot] of Object.entries(manifestScenes ?? {})) {
    if (snapshot) merged[sourceId] = snapshot;
  }
  for (const [sourceId, snapshot] of Object.entries(localScenes ?? {})) {
    if (snapshot) merged[sourceId] = snapshot;
  }
  return Object.keys(merged).length > 0 ? merged : null;
}

function collect3gsCandidates(value: unknown, depth: number, candidates: string[]): void {
  if (depth > 4) return;
  if (typeof value === "string") {
    if (/\.(ply|sog|splat|ksplat|spz)(\?|#|$)/i.test(value) || /scene_3gs|ply_fs|splat/i.test(value)) {
      candidates.push(value);
    }
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) collect3gsCandidates(item, depth + 1, candidates);
    return;
  }
  if (!value || typeof value !== "object") return;
  const obj = value as Record<string, unknown>;
  const preferredKeys = [
    "sog_url",
    "sogUrl",
    "sog_path",
    "sogPath",
    "splat_url",
    "splatUrl",
    "ply_url",
    "plyUrl",
    "master_ply_url",
    "masterPlyUrl",
    "scene_3gs_ply_fs",
    "scene_3gs_master_ply_fs",
    "output_url",
    "asset_url",
    "static_url",
    "url",
  ];
  for (const key of preferredKeys) {
    const candidate = obj[key];
    if (typeof candidate === "string" && candidate.length > 0) candidates.push(candidate);
  }
  for (const key in obj) {
    if (!preferredKeys.includes(key)) collect3gsCandidates(obj[key], depth + 1, candidates);
  }
}

export function pickDirectorSogUrl(result: unknown): string | null {
  const candidates: string[] = [];
  collect3gsCandidates(result, 0, candidates);
  return (
    candidates.find((candidate) => /\.sog(\?|#|$)/i.test(candidate)) ??
    candidates.find((candidate) => /\.(ksplat|splat|spz)(\?|#|$)/i.test(candidate)) ??
    candidates.find((candidate) => /\.ply(\?|#|$)/i.test(candidate)) ??
    candidates[0] ??
    null
  );
}

export function sourceFromImageTo3gsResult(
  result: unknown,
  input: {
    id: string;
    sourceKind: Exclude<DirectorStageSourceKind, "active">;
    label: string;
    collisionGlbUrl?: string | null;
  },
): DirectorWorldSource | null {
  const url = pickDirectorSogUrl(result);
  if (!url) return null;
  return {
    id: input.id,
    source_type: "sog",
    source_kind: input.sourceKind,
    label: input.label,
    ply_url: url,
    url,
    collision_glb_url: input.collisionGlbUrl ?? undefined,
    current: true,
  };
}
