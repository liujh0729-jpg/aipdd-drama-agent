// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMemo } from "react";
import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";

export type SaveStatus = "idle" | "saving" | "saved" | "error";

export interface SaveState {
  status: SaveStatus;
  /** ms since epoch of the last successful save for this scope, or null. */
  lastSavedAt: number | null;
  /** Last error message, if any. */
  error?: string;
}

interface SaveStatusStore {
  /**
   * Leaf scopes only. Parent state is derived via {@link useSaveState} by
   * prefix-matching children. Writing to a "parent" scope key directly works
   * but is not the recommended pattern.
   */
  scopes: Record<string, SaveState>;
  /** Transition a leaf scope through the state machine. */
  setScopeStatus: (scope: string, status: SaveStatus, error?: string) => void;
  /**
   * Reset scope state. With no argument, empties every scope — used by the
   * central region-switch flow to drop region-scoped data. With a scope
   * argument, removes only that scope entry (legacy per-scope teardown).
   */
  reset: (scope?: string) => void;
}

const EMPTY: SaveState = { status: "idle", lastSavedAt: null };

export const useSaveStatusStore = create<SaveStatusStore>()((set) => ({
  scopes: {},
  setScopeStatus: (scope, status, error) =>
    set((s) => {
      const prev = s.scopes[scope] ?? EMPTY;
      let next: SaveState;
      if (status === "saved") {
        next = { status: "saved", lastSavedAt: Date.now(), error: undefined };
      } else if (status === "saving") {
        // Preserve a stale lastSavedAt so "Saved 5m ago" doesn't regress while
        // the next save is in flight.
        next = { status: "saving", lastSavedAt: prev.lastSavedAt };
      } else if (status === "error") {
        next = { status: "error", lastSavedAt: prev.lastSavedAt, error };
      } else {
        next = { ...EMPTY };
      }
      if (
        prev.status === next.status &&
        prev.lastSavedAt === next.lastSavedAt &&
        prev.error === next.error
      ) {
        return s;
      }
      return { scopes: { ...s.scopes, [scope]: next } };
    }),
  reset: (scope) =>
    set((s) => {
      if (scope === undefined) {
        // Full clear. Keep a stable empty-object identity when already empty
        // so selectors don't see a spurious reference change.
        if (Object.keys(s.scopes).length === 0) return s;
        return { scopes: {} };
      }
      if (!(scope in s.scopes)) return s;
      const next = { ...s.scopes };
      delete next[scope];
      return { scopes: next };
    }),
}));

// ─── aggregation ────────────────────────────────────────────────────────────

/**
 * Derive the effective {@link SaveState} for a scope by combining its own
 * leaf state (if any) with all descendant scopes that start with `scope + "."`.
 *
 * Aggregation rules ("most severe"):
 *   error   — wins unconditionally; carries the newest error message.
 *   saving  — if any child is in flight.
 *   saved   — if every touched child has saved; lastSavedAt = max child.
 *   idle    — otherwise.
 *
 * This is a true state projection: a sibling success cannot silently clear
 * another sibling's error, because the errored scope remains in the store
 * until it itself transitions.
 */
export function deriveSaveState(
  scopes: Record<string, SaveState>,
  scope: string,
): SaveState {
  const prefix = scope + ".";
  const own = scopes[scope];

  let hasError = false;
  let hasSaving = false;
  let latestSaved = 0;
  let anyChild = false;
  let errorMsg: string | undefined;
  let errorAt = 0;

  for (const key in scopes) {
    if (key !== scope && !key.startsWith(prefix)) continue;
    const state = scopes[key];
    if (key !== scope) anyChild = true;
    if (state.status === "error") {
      hasError = true;
      const at = state.lastSavedAt ?? 0;
      if (at >= errorAt) {
        errorAt = at;
        errorMsg = state.error;
      }
    } else if (state.status === "saving") {
      hasSaving = true;
    } else if (state.status === "saved") {
      if ((state.lastSavedAt ?? 0) > latestSaved) {
        latestSaved = state.lastSavedAt ?? 0;
      }
    }
  }

  if (!own && !anyChild) return EMPTY;
  if (hasError) {
    return { status: "error", lastSavedAt: latestSaved || null, error: errorMsg };
  }
  if (hasSaving) {
    return { status: "saving", lastSavedAt: latestSaved || own?.lastSavedAt || null };
  }
  if (latestSaved > 0) {
    return { status: "saved", lastSavedAt: latestSaved };
  }
  return own ?? EMPTY;
}

/**
 * Subscribe to the derived save state for a scope (leaf or parent). Uses a
 * shallow-equality selector over the relevant slice to avoid re-renders when
 * unrelated scopes change.
 */
export function useSaveState(scope: string): SaveState {
  const slice = useSaveStatusStore(
    useShallow((s) => {
      const prefix = scope + ".";
      const out: Record<string, SaveState> = {};
      for (const key in s.scopes) {
        if (key === scope || key.startsWith(prefix)) out[key] = s.scopes[key];
      }
      return out;
    }),
  );
  return useMemo(() => deriveSaveState(slice, scope), [slice, scope]);
}

/**
 * Imperative writer — for code paths that cannot use the react hook
 * (e.g. unmount cleanups that fire a last flush save).
 */
export function writeSaveStatus(
  scope: string,
  status: SaveStatus,
  error?: string,
): void {
  useSaveStatusStore.getState().setScopeStatus(scope, status, error);
}

/**
 * Wrap any async save operation so the store reflects its lifecycle. Survives
 * component unmount and navigation because the promise chain lives in the JS
 * engine, not in the React tree — unlike observer-bound mutation callbacks,
 * which TanStack Query suppresses once a component unmounts.
 *
 * Re-throws so callers can still handle errors locally (e.g. show a toast).
 *
 * @example
 *   await trackSave(beatTextScope, () => update.mutateAsync(args));
 */
export async function trackSave<T>(
  scope: string,
  op: () => Promise<T>,
): Promise<T> {
  writeSaveStatus(scope, "saving");
  try {
    const result = await op();
    writeSaveStatus(scope, "saved");
    return result;
  } catch (err) {
    writeSaveStatus(
      scope,
      "error",
      err instanceof Error ? err.message : undefined,
    );
    throw err;
  }
}

// ─── scope key helpers ──────────────────────────────────────────────────────

/**
 * Centralized scope key builders. Use these instead of hand-assembling
 * template strings — they encode components that may contain delimiters
 * (e.g. Chinese character names with dots) and keep the convention in one
 * place.
 */
export const saveScopes = {
  episodePage: (project: string, episode: number) =>
    `episode.${encode(project)}.${episode}`,
  episodeTitle: (project: string, episode: number) =>
    `${saveScopes.episodePage(project, episode)}.title`,
  episodeSource: (project: string, episode: number) =>
    `${saveScopes.episodePage(project, episode)}.source`,
  episodeIdentities: (project: string, episode: number) =>
    `${saveScopes.episodePage(project, episode)}.identities`,
  beatText: (project: string, episode: number, beatNumber: number) =>
    `${saveScopes.episodePage(project, episode)}.beat.${beatNumber}.text`,
  charactersPage: (project: string) => `characters.${encode(project)}`,
  characterDetails: (project: string, name: string) =>
    `${saveScopes.charactersPage(project)}.c.${encode(name)}.details`,
};

/**
 * Encode a path segment so delimiters (".", whitespace) can't collide.
 * `encodeURIComponent` does NOT escape ".", so a project id like "proj.a"
 * would still produce an ambiguous prefix match against "proj" — replace
 * dots explicitly.
 */
function encode(segment: string): string {
  return encodeURIComponent(segment).replace(/\./g, "%2E");
}
