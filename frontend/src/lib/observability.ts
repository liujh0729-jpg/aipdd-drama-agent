// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useRegionStore } from "@/stores/region-store";

// Today's minimal implementation: a module-level tag object updated by a
// store subscription. When Sentry/Datadog lands, swap the body of `apply()`
// for `Sentry.setTag("region_id", id)` — the public API (init + getTags)
// stays stable.
let tags: { region_id: string | null } = { region_id: null };
let initialized = false;

export function initObservability(): void {
  const apply = () => {
    tags = { region_id: useRegionStore.getState().selectedRegionId };
    // eslint-disable-next-line no-console
    console.info("[observability] region_id =", tags.region_id);
  };
  apply();
  if (!initialized) {
    useRegionStore.subscribe(apply);
    initialized = true;
  }
}

export function getObservabilityTags(): { region_id: string | null } {
  return tags;
}
