// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { quotaSafeStateStorage } from "@/lib/localStorageQuota";

export interface EpisodeWorkbenchScope {
  project: string;
  episode: number;
}

export type BeatActionPanelSectionId =
  | "text"
  | "sketch"
  | "render"
  | "audio"
  | "video";
export type BeatViewToggleId = "text" | "sketch" | "render";

export type PersistedBeatSelection =
  | { mode: "none"; activeBeat: number | null }
  | { mode: "single"; beatNum: number; activeBeat: number }
  | { mode: "multi"; checked: number[]; activeBeat: number | null };

export const DEFAULT_BEAT_SELECTION: PersistedBeatSelection = {
  mode: "none",
  activeBeat: null,
};
// 默认展开「文案」区块：台词/类型/场景/画面描述等核心信息无需额外点击即可见
// (#21)。仅作用于未自定义过的 scope；用户手动折叠后会按 scope 持久化覆盖此默认。
export const DEFAULT_ACTION_PANEL_SECTIONS: readonly BeatActionPanelSectionId[] = [
  "text",
];
export const DEFAULT_VIEW_TOGGLES: readonly BeatViewToggleId[] = [
  "text",
  "sketch",
  "render",
];

const ACTION_PANEL_SECTION_IDS = new Set<BeatActionPanelSectionId>([
  "text",
  "sketch",
  "render",
  "audio",
  "video",
]);
const VIEW_TOGGLE_IDS = new Set<BeatViewToggleId>([
  "text",
  "sketch",
  "render",
]);

interface EpisodeWorkbenchState {
  lastEpisodeLocationByProject: Record<string, string>;
  beatSelectionByScope: Record<string, PersistedBeatSelection>;
  actionPanelSectionsByScope: Record<string, BeatActionPanelSectionId[]>;
  viewTogglesByScope: Record<string, BeatViewToggleId[]>;
  setLastEpisodeLocation: (project: string, location: string) => void;
  clearLastEpisodeLocation: (project: string) => void;
  setBeatSelection: (
    scope: EpisodeWorkbenchScope,
    selection: PersistedBeatSelection,
  ) => void;
  setActionPanelSections: (
    scope: EpisodeWorkbenchScope,
    sections: Iterable<BeatActionPanelSectionId>,
  ) => void;
  setViewToggles: (
    scope: EpisodeWorkbenchScope,
    toggles: Iterable<BeatViewToggleId>,
  ) => void;
  reset: () => void;
}

export function episodeWorkbenchScopeKey(scope: EpisodeWorkbenchScope): string {
  return `${scope.project}:${scope.episode}`;
}

export function normalizeLastEpisodeLocation(
  project: string,
  location: unknown,
): string | null {
  if (typeof location !== "string") return null;
  try {
    const url = new URL(location, "http://local");
    const match = url.pathname.match(/^\/projects\/([^/]+)\/episodes\/\d+(?:\/|$)/);
    if (!match) return null;
    if (decodeURIComponent(match[1]) !== project) return null;
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return null;
  }
}

function validBeatNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value > 0
    ? value
    : null;
}

function numberArray(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  return value
    .map(validBeatNumber)
    .filter((item): item is number => item !== null);
}

export function normalizeSelection(
  selection: unknown,
): PersistedBeatSelection {
  if (!selection || typeof selection !== "object") return DEFAULT_BEAT_SELECTION;
  const record = selection as Record<string, unknown>;
  if (record.mode === "single") {
    const beatNum = validBeatNumber(record.beatNum);
    if (beatNum === null) return { mode: "none", activeBeat: null };
    return {
      mode: "single",
      beatNum,
      activeBeat: beatNum,
    };
  }
  if (record.mode === "multi") {
    const checked = [...new Set(numberArray(record.checked))].sort((a, b) => a - b);
    if (checked.length === 0) return { mode: "none", activeBeat: null };
    return {
      mode: "multi",
      checked,
      activeBeat: null,
    };
  }
  return {
    mode: "none",
    activeBeat: null,
  };
}

function normalizeActionPanelSections(
  sections: Iterable<unknown> | undefined,
): BeatActionPanelSectionId[] {
  if (!sections) return [...DEFAULT_ACTION_PANEL_SECTIONS];
  return [...new Set(sections)].filter((id): id is BeatActionPanelSectionId =>
    ACTION_PANEL_SECTION_IDS.has(id as BeatActionPanelSectionId),
  );
}

function normalizeViewToggles(
  toggles: Iterable<unknown> | undefined,
): BeatViewToggleId[] {
  if (!toggles) return [...DEFAULT_VIEW_TOGGLES];
  const next = [...new Set(toggles)].filter((id): id is BeatViewToggleId =>
    VIEW_TOGGLE_IDS.has(id as BeatViewToggleId),
  );
  return next.length > 0 ? next : [...DEFAULT_VIEW_TOGGLES];
}

export const useEpisodeWorkbenchStore = create<EpisodeWorkbenchState>()(
  persist(
    (set) => ({
      lastEpisodeLocationByProject: {},
      beatSelectionByScope: {},
      actionPanelSectionsByScope: {},
      viewTogglesByScope: {},
      setLastEpisodeLocation: (project, location) =>
        set((state) => ({
          lastEpisodeLocationByProject: {
            ...state.lastEpisodeLocationByProject,
            [project]:
              normalizeLastEpisodeLocation(project, location) ??
              `/projects/${encodeURIComponent(project)}/episodes`,
          },
        })),
      clearLastEpisodeLocation: (project) =>
        set((state) => {
          if (!(project in state.lastEpisodeLocationByProject)) return state;
          const rest = { ...state.lastEpisodeLocationByProject };
          delete rest[project];
          return { lastEpisodeLocationByProject: rest };
        }),
      setBeatSelection: (scope, selection) =>
        set((state) => ({
          beatSelectionByScope: {
            ...state.beatSelectionByScope,
            [episodeWorkbenchScopeKey(scope)]: normalizeSelection(selection),
          },
        })),
      setActionPanelSections: (scope, sections) =>
        set((state) => ({
          actionPanelSectionsByScope: {
            ...state.actionPanelSectionsByScope,
            [episodeWorkbenchScopeKey(scope)]: normalizeActionPanelSections(sections),
          },
        })),
      setViewToggles: (scope, toggles) =>
        set((state) => ({
          viewTogglesByScope: {
            ...state.viewTogglesByScope,
            [episodeWorkbenchScopeKey(scope)]: normalizeViewToggles(toggles),
          },
        })),
      reset: () =>
        set({
          lastEpisodeLocationByProject: {},
          beatSelectionByScope: {},
          actionPanelSectionsByScope: {},
          viewTogglesByScope: {},
        }),
    }),
    {
      name: "supertale-episode-workbench",
      version: 1,
      storage: createJSONStorage(() => quotaSafeStateStorage),
      migrate: (persisted: unknown) => {
        const base = (persisted ?? {}) as Partial<EpisodeWorkbenchState>;
        const nextLocations: Record<string, string> = {};
        for (const [key, value] of Object.entries(
          base.lastEpisodeLocationByProject ?? {},
        )) {
          const location = normalizeLastEpisodeLocation(key, value);
          if (location) {
            nextLocations[key] = location;
          }
        }
        const nextSelection: Record<string, PersistedBeatSelection> = {};
        for (const [key, value] of Object.entries(base.beatSelectionByScope ?? {})) {
          nextSelection[key] = normalizeSelection(value);
        }
        const nextSections: Record<string, BeatActionPanelSectionId[]> = {};
        for (const [key, value] of Object.entries(
          base.actionPanelSectionsByScope ?? {},
        )) {
          nextSections[key] = normalizeActionPanelSections(value);
        }
        const nextToggles: Record<string, BeatViewToggleId[]> = {};
        for (const [key, value] of Object.entries(base.viewTogglesByScope ?? {})) {
          nextToggles[key] = normalizeViewToggles(value);
        }
        return {
          lastEpisodeLocationByProject: nextLocations,
          beatSelectionByScope: nextSelection,
          actionPanelSectionsByScope: nextSections,
          viewTogglesByScope: nextToggles,
        };
      },
    },
  ),
);
