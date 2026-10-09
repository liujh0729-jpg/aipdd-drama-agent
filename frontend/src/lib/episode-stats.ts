// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { Episode } from "@/types/episode";

export interface EpisodeStats {
  totalEpisodes: number;
  totalIdentities: number;
  totalKeyEvents: number;
  totalScenes: number;
  totalProps: number;
}

export function deriveEpisodeStats(episodes: Episode[]): EpisodeStats {
  return {
    totalEpisodes: episodes.length,
    totalIdentities: episodes.reduce(
      (sum, episode) => sum + (episode.identity_ids?.length ?? 0),
      0,
    ),
    totalKeyEvents: episodes.reduce(
      (sum, episode) => sum + (episode.key_events?.length ?? 0),
      0,
    ),
    totalScenes: episodes.reduce(
      (sum, episode) => sum + (episode.scene_menu?.length ?? 0),
      0,
    ),
    totalProps: episodes.reduce(
      (sum, episode) => sum + (episode.prop_menu?.length ?? 0),
      0,
    ),
  };
}
