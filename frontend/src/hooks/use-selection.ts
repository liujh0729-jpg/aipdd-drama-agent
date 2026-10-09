// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useMemo, useState } from "react";
import {
  DEFAULT_BEAT_SELECTION,
  episodeWorkbenchScopeKey,
  useEpisodeWorkbenchStore,
  type EpisodeWorkbenchScope,
  type PersistedBeatSelection,
} from "@/stores/episode-workbench-store";

export type SelectionState =
  | { mode: "none" }
  | { mode: "single"; beatNum: number }
  | { mode: "multi"; checked: Set<number> };

export interface SelectionActions {
  activeBeat: number | null;
  handleCardClick: (beatNum: number) => void;
  toggleCheck: (beatNum: number) => void;
  selectSingle: (beatNum: number) => void;
  selectMany: (beatNumbers: number[]) => void;
  clearSelection: () => void;
}

function toSelectionState(selection: PersistedBeatSelection): SelectionState {
  if (selection.mode === "single") {
    return { mode: "single", beatNum: selection.beatNum };
  }
  if (selection.mode === "multi") {
    return { mode: "multi", checked: new Set(selection.checked) };
  }
  return { mode: "none" };
}

export function useSelection(
  scope?: EpisodeWorkbenchScope,
): { state: SelectionState } & SelectionActions {
  const scopeKey = scope ? episodeWorkbenchScopeKey(scope) : null;
  const persistedSelection = useEpisodeWorkbenchStore(
    useCallback(
      (s) =>
        scopeKey
          ? s.beatSelectionByScope[scopeKey] ?? DEFAULT_BEAT_SELECTION
          : DEFAULT_BEAT_SELECTION,
      [scopeKey],
    ),
  );
  const setPersistedSelection = useEpisodeWorkbenchStore(
    (s) => s.setBeatSelection,
  );

  const [state, setState] = useState<SelectionState>({ mode: "none" });
  const [activeBeat, setActiveBeat] = useState<number | null>(null);
  const effectiveState = useMemo(
    () => (scope ? toSelectionState(persistedSelection) : state),
    [persistedSelection, scope, state],
  );
  const effectiveActiveBeat = scope ? persistedSelection.activeBeat : activeBeat;

  const selectSingle = useCallback((beatNum: number) => {
    if (scope) {
      setPersistedSelection(scope, {
        mode: "single",
        beatNum,
        activeBeat: beatNum,
      });
      return;
    }
    setActiveBeat(beatNum);
    setState({ mode: "single", beatNum });
  }, [scope, setPersistedSelection]);

  const toggleCheck = useCallback((beatNum: number) => {
    if (scope) {
      const prevChecked =
        persistedSelection.mode === "multi"
          ? new Set(persistedSelection.checked)
          : new Set<number>();
      const next = new Set(prevChecked);
      if (next.has(beatNum)) next.delete(beatNum);
      else next.add(beatNum);
      if (next.size === 0) {
        setPersistedSelection(scope, {
          mode: "none",
          activeBeat: null,
        });
        return;
      }
      setPersistedSelection(scope, {
        mode: "multi",
        checked: [...next],
        activeBeat: null,
      });
      return;
    }
    setActiveBeat(null);
    setState((prev) => {
      const prevChecked = prev.mode === "multi" ? prev.checked : new Set<number>();
      const next = new Set(prevChecked);
      if (next.has(beatNum)) next.delete(beatNum);
      else next.add(beatNum);
      if (next.size === 0) return { mode: "none" };
      return { mode: "multi", checked: next };
    });
  }, [persistedSelection, scope, setPersistedSelection]);

  const handleCardClick = useCallback((beatNum: number) => {
    if (scope) {
      setPersistedSelection(scope, {
        mode: "single",
        beatNum,
        activeBeat: beatNum,
      });
      return;
    }
    setActiveBeat(beatNum);
    setState({ mode: "single", beatNum });
  }, [scope, setPersistedSelection]);


  // [aipdd fork delta] 批量选中（全选/取消全选）：给非空编号→multi，空编号→none。
  const selectMany = useCallback((beatNumbers: number[]) => {
    if (scope) {
      if (beatNumbers.length === 0) {
        setPersistedSelection(scope, { mode: "none", activeBeat: null });
        return;
      }
      setPersistedSelection(scope, {
        mode: "multi",
        checked: [...beatNumbers],
        activeBeat: null,
      });
      return;
    }
    setActiveBeat(null);
    if (beatNumbers.length === 0) {
      setState({ mode: "none" });
      return;
    }
    setState({ mode: "multi", checked: new Set(beatNumbers) });
  }, [scope, setPersistedSelection]);

  const clearSelection = useCallback(() => {
    if (scope) {
      setPersistedSelection(scope, {
        mode: "none",
        activeBeat: null,
      });
      return;
    }
    setActiveBeat(null);
    setState({ mode: "none" });
  }, [scope, setPersistedSelection]);

  return {
    state: effectiveState,
    activeBeat: effectiveActiveBeat,
    handleCardClick,
    toggleCheck,
    selectSingle,
    selectMany,
    clearSelection,
  };
}
