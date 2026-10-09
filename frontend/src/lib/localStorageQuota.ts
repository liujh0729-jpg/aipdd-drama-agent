// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * Shared `localStorage` write that survives a `QuotaExceededError`.
 *
 * The per-origin localStorage budget (~5MB in Chrome) is easy to exhaust: the
 * Freezone canvas keeps per-canvas draft / undo-history snapshots, and once the
 * origin is full ANY write throws — including a tiny, unrelated one like
 * `settings-storage`, which is what surfaced the original crash. When a write
 * blows the quota we run the registered reclaimers (which prune stale freezone
 * canvas keys) and retry once, so a bloated canvas can't permanently wedge an
 * unrelated small write.
 */

type StorageReclaimer = () => void;

const reclaimers = new Set<StorageReclaimer>();

/**
 * Register a callback that frees `localStorage` space when a write hits the
 * quota. Returns an unregister function. Modules that own prunable keys call
 * this at import time so the reclaimer is available even before their UI mounts.
 */
export function registerStorageReclaimer(reclaim: StorageReclaimer): () => void {
  reclaimers.add(reclaim);
  return () => {
    reclaimers.delete(reclaim);
  };
}

function runStorageReclaimers(): void {
  for (const reclaim of reclaimers) {
    try {
      reclaim();
    } catch {
      // A failing reclaimer must not block the others or the retry.
    }
  }
}

/**
 * Whether an error thrown by `setItem` is a storage-quota overflow. Browsers
 * report it under several names/codes, so match broadly.
 */
export function isQuotaExceededError(error: unknown): boolean {
  if (typeof DOMException !== "undefined" && error instanceof DOMException) {
    return (
      error.name === "QuotaExceededError" ||
      error.name === "NS_ERROR_DOM_QUOTA_REACHED" ||
      // Legacy numeric codes: Safari 22, Firefox 1014.
      error.code === 22 ||
      error.code === 1014
    );
  }
  return error instanceof Error && error.name === "QuotaExceededError";
}

/**
 * A `StateStorage`-shaped wrapper over `localStorage` whose writes survive a
 * quota overflow (prune via reclaimers + retry). Hand it to zustand's
 * `createJSONStorage` so every persisted store degrades gracefully instead of
 * throwing when the origin is full:
 *
 *   storage: createJSONStorage(() => quotaSafeStateStorage)
 */
export const quotaSafeStateStorage = {
  getItem: (name: string): string | null => {
    try {
      return localStorage.getItem(name);
    } catch {
      return null;
    }
  },
  setItem: (name: string, value: string): void => {
    safeLocalStorageSet(name, value);
  },
  removeItem: (name: string): void => {
    try {
      localStorage.removeItem(name);
    } catch {
      // Best-effort: restricted/unavailable storage contexts.
    }
  },
};

/**
 * Write to `localStorage`, recovering from a quota overflow by pruning stale
 * keys (via the registered reclaimers) and retrying once. Returns whether the
 * value was ultimately persisted. Never throws.
 */
export function safeLocalStorageSet(key: string, value: string): boolean {
  try {
    localStorage.setItem(key, value);
    return true;
  } catch (error) {
    if (!isQuotaExceededError(error)) {
      // Non-quota failure (storage unavailable, etc.) — best-effort, give up.
      return false;
    }
    runStorageReclaimers();
    try {
      localStorage.setItem(key, value);
      return true;
    } catch {
      // Reclaimers couldn't free enough (or none cover this key). Last resort:
      // drop this key's own previous value to reclaim its space, then retry. A
      // store that is itself the bloat can still self-heal even when no
      // registered reclaimer prunes it.
      try {
        localStorage.removeItem(key);
        localStorage.setItem(key, value);
        return true;
      } catch {
        return false;
      }
    }
  }
}

/**
 * Whether a millisecond timestamp is past its TTL relative to `now`. A
 * timestamp in the future (clock skew, or a corrupt/overflowed write) is also
 * treated as stale so such entries can't linger forever past the age check.
 */
export function isStaleByTtl(updatedAt: number, now: number, ttlMs: number): boolean {
  return updatedAt > now || now - updatedAt > ttlMs;
}

/**
 * Walk every `localStorage` key matching `match` and remove the ones
 * `shouldReclaim` rejects. Keys are collected before removal so mutating the
 * store mid-walk can't shift the index and skip entries. Never throws.
 */
export function pruneLocalStorageByPrefix(
  match: string | ((key: string) => boolean),
  shouldReclaim: (key: string, raw: string) => boolean,
): void {
  const matches = typeof match === "function" ? match : (key: string) => key.startsWith(match);
  try {
    const keys: string[] = [];
    for (let i = 0; i < localStorage.length; i += 1) {
      const key = localStorage.key(i);
      if (key && matches(key)) {
        keys.push(key);
      }
    }
    for (const key of keys) {
      const raw = localStorage.getItem(key);
      if (raw == null) continue;
      if (shouldReclaim(key, raw)) {
        localStorage.removeItem(key);
      }
    }
  } catch {
    // Quota / unavailable storage / malformed JSON cleanup is best-effort.
  }
}
