// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { quotaSafeStateStorage } from "@/lib/localStorageQuota";

/**
 * Client-side "seen pool image" tracker.
 *
 * Used to decide whether a freshly-generated sketch/render candidate should
 * show a NEW badge. A pool image is considered NEW when:
 *
 *   (Date.now() - generated_at) < 10 minutes  AND  !isSeen(id)
 *
 * Calling `markSeen` on select clears the badge immediately; the 10-minute
 * window handles unseen images naturally once they age out.
 *
 * Scoped per (project, episode) so the set stays small and cross-episode
 * collisions are impossible.
 */
interface SeenPoolState {
  // Map key: `${project}:${episode}` → array of pool ids.
  seen: Record<string, string[]>;
  markSeen: (project: string, episode: number, id: string) => void;
  isSeen: (project: string, episode: number, id: string) => boolean;
  /**
   * Drop every tracked pool id. Called by the region-switch flow so badges
   * from the previous region don't bleed across after a cluster change.
   * The persist middleware will flush the empty state to localStorage.
   */
  reset: () => void;
}

const key = (project: string, episode: number) => `${project}:${episode}`;

// `markSeen` only ever appends, so cap each scope's id list to the most recent
// N. The NEW badge is gated by a 10-minute freshness window, so older ids are
// functionally dead weight — keeping them around just risks bloating storage.
const SEEN_IDS_PER_SCOPE_LIMIT = 500;

export const useSeenPoolStore = create<SeenPoolState>()(
  persist(
    (set, get) => ({
      seen: {},
      markSeen: (project, episode, id) =>
        set((state) => {
          const k = key(project, episode);
          const prev = state.seen[k] ?? [];
          if (prev.includes(id)) return state;
          const next = [...prev, id].slice(-SEEN_IDS_PER_SCOPE_LIMIT);
          return { seen: { ...state.seen, [k]: next } };
        }),
      isSeen: (project, episode, id) => {
        const arr = get().seen[key(project, episode)];
        return !!arr && arr.includes(id);
      },
      reset: () => set({ seen: {} }),
    }),
    {
      name: "supertale-seen-pools",
      storage: createJSONStorage(() => quotaSafeStateStorage),
    },
  ),
);
