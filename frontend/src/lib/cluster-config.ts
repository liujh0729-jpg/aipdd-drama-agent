// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { z } from "zod";

export const ClusterMode = z.enum(["none", "multi-region"]);
export type ClusterMode = z.infer<typeof ClusterMode>;

const REGION_ID_RE = /^[a-z0-9][a-z0-9-]{0,31}$/;

export const Region = z.object({
  id: z.string().regex(REGION_ID_RE, "region id must match ^[a-z0-9][a-z0-9-]{0,31}$"),
  displayName: z.string().min(1),
});
export type Region = z.infer<typeof Region>;

const ResponseSchema = z.object({ regions: z.array(Region).min(1) }).superRefine((v, ctx) => {
  const seen = new Set<string>();
  for (const r of v.regions) {
    if (seen.has(r.id)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: `duplicate region id: ${r.id}` });
    }
    seen.add(r.id);
  }
});

export interface ClusterConfig {
  mode: ClusterMode;
  regions: Region[];
}

// Module-level mutable state. `clusterConfig.regions` is populated by
// `loadClusterConfig()` before React mounts. Every other consumer reads
// this object synchronously.
export const clusterConfig: ClusterConfig = {
  mode: ClusterMode.parse(import.meta.env.VITE_CLUSTER_MODE ?? "none"),
  regions: [],
};

const CACHE_KEY = "supertale-cluster-config-cache";
const CACHE_TTL_MS = 24 * 60 * 60 * 1000; // 24h

interface CacheShape {
  savedAt: number;
  regions: Region[];
}

function readCache(): Region[] | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as CacheShape;
    if (!parsed) return null;
    if (typeof parsed?.savedAt !== "number" || !Number.isFinite(parsed.savedAt)) return null;
    if (Date.now() - parsed.savedAt > CACHE_TTL_MS) return null;
    const valid = ResponseSchema.safeParse({ regions: parsed.regions });
    return valid.success ? valid.data.regions : null;
  } catch {
    return null;
  }
}

function writeCache(regions: Region[]): void {
  try {
    localStorage.setItem(
      CACHE_KEY,
      JSON.stringify({ savedAt: Date.now(), regions } satisfies CacheShape),
    );
  } catch {
    /* quota / SSR / private mode — best-effort */
  }
}

export async function loadClusterConfig(): Promise<void> {
  if (clusterConfig.mode === "none") {
    clusterConfig.regions = [];
    return;
  }

  try {
    const url = import.meta.env.VITE_CLUSTER_REGIONS_URL as string | undefined;
    if (!url) {
      throw new Error("VITE_CLUSTER_REGIONS_URL must be set when mode='multi-region'");
    }
    const res = await fetch(url, { credentials: "omit", cache: "default" });
    if (!res.ok) throw new Error(`GET ${url} → ${res.status}`);
    const body = await res.json();
    const parsed = ResponseSchema.safeParse(body);
    if (!parsed.success) throw new Error(`invalid regions payload: ${parsed.error.message}`);
    clusterConfig.regions = parsed.data.regions;
    writeCache(clusterConfig.regions);
    return;
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn("[cluster-config] load failed:", err);
    const cached = readCache();
    clusterConfig.regions = cached ?? [];
  }
}
