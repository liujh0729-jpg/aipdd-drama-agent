// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { apiCall } from "./client";

/**
 * Scene asset thumbnails for a given beat — populated lazily when the user
 * opens the "选源" popover on a `selected_background` slot node, or when
 * a `selected_background` slot is dragged into a canvas and the receiver
 * wants to spawn the full scene subgraph (double-click expand).
 *
 * Why this API rather than denormalizing into node.data:
 *   - Scene master / reverse / pano / PLY get re-rendered by other pipelines;
 *     storing URLs on the slot node would go stale.
 *   - Different consumers (popover vs subgraph expand) want different
 *     subsets — keeping resolution server-side is the single source of
 *     truth.
 *
 * Each URL may be `null` when the underlying canonical file doesn't exist
 * yet (e.g. user hasn't run scene-master generation). Callers should render
 * only the available sources.
 */
export interface SceneAssetsForBeat {
  scene_id: string | null;
  master_url: string | null;
  reverse_url: string | null;
  director_env_only_url: string | null;
  pano_360_url: string | null;
  ply_url: string | null;
}

export type SceneAssetsForBeatResult = SceneAssetsForBeat & {
  project: string;
  episode: number;
  beat: number;
};

export async function getSceneAssetsForBeat(
  project: string,
  episode: number,
  beat: number,
): Promise<SceneAssetsForBeatResult> {
  const qs = new URLSearchParams();
  qs.set("episode", String(episode));
  qs.set("beat", String(beat));
  return await apiCall<SceneAssetsForBeatResult>(
    `projects/${encodeURIComponent(project)}/freezone/scene-assets-for-beat?${qs.toString()}`,
  );
}

export async function syncDirectorEnvOnlyToSelectedBackground(
  project: string,
  episode: number,
  beat: number,
): Promise<{ synced: boolean; episode: number; beat: number }> {
  return await apiCall<{ synced: boolean; episode: number; beat: number }>(
    `projects/${encodeURIComponent(project)}/freezone/director-capture/sync-background?episode=${episode}&beat=${beat}`,
    { method: "POST" },
  );
}
