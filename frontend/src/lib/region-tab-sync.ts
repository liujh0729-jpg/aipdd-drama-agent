// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useRegionStore } from "@/stores/region-store";

const CHANNEL_NAME = "region-switch";
const PERSIST_KEY = "supertale-region";
const RELOAD_DELAY_MS = 200;

type Msg = { type: "switching"; newRegionId: string };

let channel: BroadcastChannel | null = null;

function getChannel(): BroadcastChannel {
  if (!channel) channel = new BroadcastChannel(CHANNEL_NAME);
  return channel;
}

/**
 * Initializes cross-tab region-switch lockdown.
 *
 * Listens on two signals:
 *  1. BroadcastChannel("region-switch") — Tab B calls {@link broadcastSwitching}
 *     right before mutating its own region, so sibling tabs learn about it
 *     synchronously within the same browser instance.
 *  2. window "storage" event — fires in OTHER tabs when localStorage is
 *     mutated. We watch the Zustand persist key ("supertale-region") so that
 *     even without the BroadcastChannel signal we catch the switch.
 *
 * Either signal locks this tab (isLocked=true) and schedules a hard reload
 * so the user cannot issue another mutation against the stale region.
 *
 * @returns a cleanup function that removes both listeners.
 */
export function initRegionTabSync(): () => void {
  const ch = getChannel();

  const onMessage = (ev: MessageEvent<Msg>) => {
    if (ev.data?.type === "switching") {
      useRegionStore.getState().setLocked(true);
      setTimeout(() => window.location.reload(), RELOAD_DELAY_MS);
    }
  };
  ch.addEventListener("message", onMessage);

  const onStorage = (ev: StorageEvent) => {
    if (ev.key === PERSIST_KEY) {
      useRegionStore.getState().setLocked(true);
      setTimeout(() => window.location.reload(), RELOAD_DELAY_MS);
    }
  };
  window.addEventListener("storage", onStorage);

  return () => {
    ch.removeEventListener("message", onMessage);
    window.removeEventListener("storage", onStorage);
  };
}

/**
 * Broadcasts a region-switch to all other tabs sharing this origin. Call this
 * BEFORE actually mutating the persisted region so sibling tabs lock down
 * before their next in-flight mutation.
 */
export function broadcastSwitching(newRegionId: string): void {
  getChannel().postMessage({ type: "switching", newRegionId });
}
