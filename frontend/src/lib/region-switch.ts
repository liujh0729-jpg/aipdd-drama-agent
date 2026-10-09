// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { QueryClient } from "@tanstack/react-query";
import { tryAcquireNavLock } from "@/lib/nav-lock";
import { broadcastSwitching } from "@/lib/region-tab-sync";
import { regionAbortController, resetRegionAbortController } from "@/lib/region-abort";
import { resetRegionState } from "@/lib/reset-region-state";
import { setRegionCookie } from "@/lib/region-cookie";
import { useAuthStore } from "@/stores/auth-store";
import { useRegionStore } from "@/stores/region-store";

const LOGOUT_TIMEOUT_MS = 2000;

/**
 * Switches the active region. The order is load-bearing — each step depends on
 * state established by the one before it:
 *
 *  1. Acquire nav-lock. If another flow (e.g. the 401 handler) already holds
 *     it, back off silently rather than racing to the same destination.
 *  2. Flip the region store into the switching state so the UI can chrome
 *     itself down (spinners, disabled controls) for the rest of the flow.
 *  3. Broadcast to sibling tabs so they lock down in parallel, before we
 *     mutate any storage they could observe via the storage event.
 *  4. Abort in-flight ky + auth-store fetches and install a fresh controller
 *     so follow-up requests don't get aborted by the stale one.
 *  5. Dispatch a window "region-switch" event so EventSource listeners close
 *     their streams (the BE would 401 them after the cookie flips anyway).
 *  6. Attempt a logout against the CURRENT region cookie — it's still valid
 *     at this point. Race it against a 2s timeout so a hung BE can't stall
 *     the switch indefinitely; swallow errors/timeouts either way.
 *  7. Purge region-scoped client state (auth, save-status, seen-pool,
 *     task-center, query cache, localStorage sweep).
 *  8. Write the new region cookie. This HAS to come after the purge so the
 *     next request can't race the stale state using the new routing.
 *  9. Hard-reload into /login — the fresh page boots against the new cookie
 *     with a clean module graph (nav-lock, abort controller, etc.).
 */
export async function switchRegion(params: {
  newRegionId: string;
  queryClient: QueryClient;
}): Promise<void> {
  const { newRegionId, queryClient } = params;

  if (!tryAcquireNavLock()) return;

  useRegionStore.getState().setSwitching(true);

  try {
    broadcastSwitching(newRegionId);
  } catch {
    // BroadcastChannel unavailable (ancient browser) — siblings will still
    // pick up the switch via the "storage" event when we sweep localStorage.
  }

  regionAbortController().abort();
  resetRegionAbortController();

  window.dispatchEvent(new Event("region-switch"));

  await Promise.race([
    useAuthStore
      .getState()
      .logout()
      .catch(() => undefined),
    new Promise<void>((resolve) => setTimeout(resolve, LOGOUT_TIMEOUT_MS)),
  ]);

  resetRegionState({ queryClient });

  setRegionCookie(newRegionId);

  window.location.assign("/login");
}
