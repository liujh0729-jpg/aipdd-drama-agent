// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useMemo } from "react";
import {
  DEFAULT_VIEW_TOGGLES,
  episodeWorkbenchScopeKey,
  useEpisodeWorkbenchStore,
  type BeatViewToggleId,
} from "@/stores/episode-workbench-store";

export type ViewToggleId = BeatViewToggleId;

export function useViewToggles(project: string, episode: number) {
  const scope = useMemo(() => ({ project, episode }), [episode, project]);
  const scopeKey = episodeWorkbenchScopeKey(scope);
  const persistedToggles = useEpisodeWorkbenchStore(
    useCallback(
      (s) => s.viewTogglesByScope[scopeKey] ?? DEFAULT_VIEW_TOGGLES,
      [scopeKey],
    ),
  );
  const setViewToggles = useEpisodeWorkbenchStore((s) => s.setViewToggles);
  const toggles = useMemo(
    () => new Set<ViewToggleId>(persistedToggles),
    [persistedToggles],
  );

  const toggle = useCallback((id: ViewToggleId) => {
    const next = new Set(toggles);
    if (next.has(id)) {
      // Prevent removing the last toggle — at least one must stay active.
      if (next.size <= 1) return;
      next.delete(id);
    } else {
      next.add(id);
    }
    setViewToggles(scope, next);
  }, [scope, setViewToggles, toggles]);

  return { toggles, toggle };
}
