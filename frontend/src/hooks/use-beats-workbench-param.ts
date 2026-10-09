// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useCallback } from "react";

export function parseBeatParam(raw: unknown): number | null {
  if (raw === undefined || raw === null) return null;
  const n = typeof raw === "number" ? raw : Number(raw);
  if (!Number.isFinite(n) || !Number.isInteger(n) || n < 1) return null;
  return n;
}

export type BeatsSubParam = "sketch" | "render" | "audio" | "video";

const BEATS_SUB_PARAMS = new Set<BeatsSubParam>([
  "sketch",
  "render",
  "audio",
  "video",
]);

export function parseBeatsSubParam(raw: unknown): BeatsSubParam | null {
  if (typeof raw !== "string") return null;
  return BEATS_SUB_PARAMS.has(raw as BeatsSubParam)
    ? (raw as BeatsSubParam)
    : null;
}

/**
 * URL-backed beat selection for deep-linking.
 * Reads `?beat=N&sub=sketch|render|audio|video`.
 *
 * `focusBeat` (`?focusBeat=N`) is a one-shot "force select & scroll to this
 * beat" signal used by explicit jumps (e.g. the compose tab). Unlike `beat`,
 * it deliberately overrides restored/persisted selection, and is consumed +
 * cleared on arrival so it never lingers as a stale deep-link.
 */
export function useBeatsWorkbenchParam() {
  const search = useSearch({ strict: false }) as Record<string, unknown>;
  const navigate = useNavigate();
  const beat = parseBeatParam(search.beat);
  const sub = parseBeatsSubParam(search.sub);
  const focusBeat = parseBeatParam(search.focusBeat);

  const setBeat = useCallback(
    (n: number | null) => {
      navigate({
        search: ((prev: Record<string, unknown>) => {
          const next = { ...prev };
          if (n === null) delete next.beat;
          else next.beat = n;
          // Clean up v1 params
          delete next.mode;
          return next;
        }) as never,
        replace: true,
      });
    },
    [navigate],
  );

  const clearFocusBeat = useCallback(() => {
    navigate({
      search: ((prev: Record<string, unknown>) => {
        const next = { ...prev };
        delete next.focusBeat;
        return next;
      }) as never,
      replace: true,
    });
  }, [navigate]);

  return { beat, sub, focusBeat, setBeat, clearFocusBeat };
}
