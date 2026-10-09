// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { apiCall } from "./client";
import type { PushTarget } from "./push";
import type { MainlineContext } from "@/features/freezone/context/mainlineContext";
import type { SceneAsset } from "@/types/scene";

// SuperTale `/api/v1/projects` returns a list of project summaries belonging
// to the authenticated user. Shape based on
// SuperTale/src/novelvideo/api/routes/projects.py:27-44.

export interface SupertaleProjectSummary {
  id: string;
  name: string;
  display_name?: string;
  created_at?: string;
  updated_at?: string;
  episode_count?: number;
  [key: string]: unknown;
}

export async function listSupertaleProjects(): Promise<SupertaleProjectSummary[]> {
  return await apiCall<SupertaleProjectSummary[]>("projects");
}

export interface SupertaleProjectDetail extends SupertaleProjectSummary {
  config?: Record<string, unknown>;
}

export async function getSupertaleProject(projectId: string): Promise<SupertaleProjectDetail> {
  return await apiCall<SupertaleProjectDetail>(
    `projects/${encodeURIComponent(projectId)}`,
  );
}

// ---------- Characters ---------- //

export interface SupertaleIdentity {
  id: string;
  identity_id?: string;
  identity_name?: string;
  name?: string;
  url?: string;
  image_url?: string;
  portrait_image_url?: string;
  costume_image_url?: string;
  [key: string]: unknown;
}

export interface SupertaleCharacter {
  name: string;
  display_name?: string;
  portrait_url?: string;
  identities?: SupertaleIdentity[];
  [key: string]: unknown;
}

export async function listCharacters(projectId: string): Promise<SupertaleCharacter[]> {
  return await apiCall<SupertaleCharacter[]>(
    `projects/${encodeURIComponent(projectId)}/characters`,
  );
}

export async function listCharacterIdentities(
  projectId: string,
  character: string,
): Promise<SupertaleIdentity[]> {
  return await apiCall<SupertaleIdentity[]>(
    `projects/${encodeURIComponent(projectId)}/characters/${encodeURIComponent(character)}/identities`,
  );
}

// ---------- Scenes ---------- //

export async function listScenes(projectId: string): Promise<SceneAsset[]> {
  return await apiCall<SceneAsset[]>(
    `projects/${encodeURIComponent(projectId)}/scenes`,
  );
}

// ---------- Episodes ---------- //

export interface SupertaleEpisodeSummary {
  episode_num: number;
  /** 后端实际返回的集数字段是 `number`;listEpisodes 会归一到 episode_num。 */
  number?: number;
  title?: string;
  [key: string]: unknown;
}

export async function listEpisodes(projectId: string): Promise<SupertaleEpisodeSummary[]> {
  const episodes = await apiCall<SupertaleEpisodeSummary[]>(
    `projects/${encodeURIComponent(projectId)}/episodes`,
  );
  // 后端集数字段名是 `number`(见 types/episode.ts 的 Episode.number),历史类型却写成
  // `episode_num`。这里统一归一,保证 episode_num 始终是有效数字,避免下游(CommitDialog /
  // ImportPanel)拿到 undefined → Number(undefined)=NaN → 请求 /episodes/NaN/beats。
  return episodes.map((ep) => {
    const resolved =
      typeof ep.episode_num === "number"
        ? ep.episode_num
        : typeof ep.number === "number"
          ? ep.number
          : ep.episode_num;
    return { ...ep, episode_num: resolved };
  });
}

// ---------- Beats ---------- //

export interface SupertaleBeat {
  beat_index?: number;
  beat_number?: number;
  narration_segment?: string;
  visual_description?: string;
  scene_ref?: { scene_id?: string; variant_id?: string };
  time_of_day?: string;
  detected_identities?: string[];
  detected_props?: string[];
  speaker?: string;
  frame_url?: string;
  video_url?: string;
  audio_url?: string;
  [key: string]: unknown;
}

export async function listBeats(
  projectId: string,
  episodeNum: number,
): Promise<SupertaleBeat[]> {
  return await apiCall<SupertaleBeat[]>(
    `projects/${encodeURIComponent(projectId)}/episodes/${episodeNum}/beats`,
  );
}

export interface BeatUpdatePayload {
  visual_description?: string;
  scene_ref?: { scene_id?: string; variant_id?: string };
  time_of_day?: string;
  detected_identities?: string[];
  detected_props?: string[];
}

export async function updateBeat(
  projectId: string,
  episodeNum: number,
  beatNum: number,
  payload: BeatUpdatePayload,
): Promise<SupertaleBeat> {
  return await apiCall<SupertaleBeat>(
    `projects/${encodeURIComponent(projectId)}/episodes/${episodeNum}/beats/${beatNum}`,
    {
      method: "PATCH",
      json: payload,
    },
  );
}

// ---------- Static URL helpers ---------- //

// SuperTale serves user assets at `/static/<user>/<project>/...`. The backend
// embeds `<user>/<project>` in URLs it returns (frame_url, video_url, identity
// image_url, portrait_url). For assets the backend doesn't directly expose
// (sketch, director-render combined.png), we derive a URL by rewriting the
// path segment after the `/static/<u>/<p>/` prefix of an already-known asset.
// If no anchor URL is available (no frames yet), the asset can't be derived
// and ImportPanel skips it.
//
// F5 sprint may add a dedicated `/freezone/list-assets` endpoint, which would
// remove the need for these helpers.

function pad(n: number, width: number): string {
  const s = String(n);
  return s.length >= width ? s : "0".repeat(width - s.length) + s;
}

const STATIC_PREFIX_RE = /^(\/static\/[^/]+\/[^/]+\/)/;

export function staticPrefixOf(url: string | null | undefined): string | null {
  if (!url) return null;
  const m = STATIC_PREFIX_RE.exec(url);
  return m ? m[1] : null;
}

export function deriveSketchUrl(
  anchorUrl: string | null | undefined,
  episode: number,
  beatNum: number,
): string | null {
  const prefix = staticPrefixOf(anchorUrl);
  if (!prefix) return null;
  return `${prefix}sketches/ep${pad(episode, 3)}/beat_${pad(beatNum, 2)}.png`;
}

export function deriveDirectorRenderUrl(
  anchorUrl: string | null | undefined,
  episode: number,
  beatNum: number,
): string | null {
  const prefix = staticPrefixOf(anchorUrl);
  if (!prefix) return null;
  return `${prefix}director_control_frames/ep${pad(episode, 3)}/beat_${pad(beatNum, 2)}/combined.png`;
}

// ---------- Freezone Assets ---------- //

export type FreezoneAssetMediaType = "image" | "video" | "audio" | "text" | "file";

export interface FreezoneProjectAsset {
  id: string;
  tab: "beat" | "characters" | "scenes" | "props" | "director";
  kind: string;
  role: string;
  label: string;
  sublabel?: string;
  rel_path?: string;
  url?: string | null;
  exists?: boolean;
  media_type?: FreezoneAssetMediaType | string;
  aspect_ratio?: string;
  meta?: Record<string, unknown>;
  mainline_context?: MainlineContext[];
  /** Director combined assets carry the complete bundle, not only combined.png. */
  director_control_bundle?: Record<string, unknown> | null;
  /** 后端是否认为该资产可推送回主流程。 */
  pushable?: boolean;
  /**
   * 后端直接给出的 canonical 提交目标(免去前端按 kind/role 猜)。
   * 用 `PushTarget | null`;业务层用 `isSlotTarget` 校验后再用。
   */
  slot_target?: PushTarget | null;
}

export async function listFreezoneProjectAssets(
  projectId: string,
  options?: { signal?: AbortSignal },
): Promise<FreezoneProjectAsset[]> {
  return await apiCall<FreezoneProjectAsset[]>(
    `projects/${encodeURIComponent(projectId)}/freezone/assets`,
    options?.signal ? { signal: options.signal } : undefined,
  );
}

// ---------- Freezone Beat Context ---------- //

export interface FreezoneBeatContextBeat {
  episode: number;
  beat: number;
  label?: string;
  visual_description?: string;
  narration_segment?: string;
  scene_id?: string;
  scene_variant_id?: string;
  time_of_day?: string;
  detected_identities?: string[];
  detected_props?: string[];
  sketch_colors?: Record<string, string>;
  prop_marker_colors?: Record<string, string>;
  asset_count?: number;
  assets: FreezoneProjectAsset[];
}

export interface FreezoneBeatContextEpisode {
  episode: number;
  beats: FreezoneBeatContextBeat[];
}

export interface FreezoneBeatContextResponse {
  scope: {
    episode: number | null;
    beat: number | null;
  };
  episodes: FreezoneBeatContextEpisode[];
  assets: FreezoneProjectAsset[];
}

export async function listFreezoneBeatContext(
  projectId: string,
  opts?: { episode?: number; beat?: number; signal?: AbortSignal },
): Promise<FreezoneBeatContextResponse> {
  const params = new URLSearchParams();
  if (typeof opts?.episode === "number") {
    params.set("episode", String(opts.episode));
  }
  if (typeof opts?.beat === "number") {
    params.set("beat", String(opts.beat));
  }
  const qs = params.toString();
  const suffix = qs ? `?${qs}` : "";
  return await apiCall<FreezoneBeatContextResponse>(
    `projects/${encodeURIComponent(projectId)}/freezone/assets/beat-context${suffix}`,
    opts?.signal ? { signal: opts.signal } : undefined,
  );
}
