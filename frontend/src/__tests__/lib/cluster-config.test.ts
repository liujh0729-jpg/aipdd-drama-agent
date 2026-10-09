// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const CACHE_KEY = "supertale-cluster-config-cache";

describe("cluster-config", () => {
  const originalMode = import.meta.env.VITE_CLUSTER_MODE;
  const originalUrl = import.meta.env.VITE_CLUSTER_REGIONS_URL;

  beforeEach(() => {
    localStorage.clear();
    vi.resetModules();
    vi.unstubAllGlobals();
  });

  afterEach(() => {
    (import.meta.env as Record<string, unknown>).VITE_CLUSTER_MODE = originalMode;
    (import.meta.env as Record<string, unknown>).VITE_CLUSTER_REGIONS_URL = originalUrl;
  });

  it("mode defaults to 'none' with empty regions when env is unset", async () => {
    delete (import.meta.env as Record<string, unknown>).VITE_CLUSTER_MODE;
    delete (import.meta.env as Record<string, unknown>).VITE_CLUSTER_REGIONS_URL;
    const { clusterConfig, loadClusterConfig } = await import("@/lib/cluster-config");
    await loadClusterConfig();
    expect(clusterConfig.mode).toBe("none");
    expect(clusterConfig.regions).toEqual([]);
  });

  it("rejects unknown mode values at module import", async () => {
    (import.meta.env as Record<string, unknown>).VITE_CLUSTER_MODE = "bogus";
    await expect(import("@/lib/cluster-config")).rejects.toThrow();
  });

  it("graceful-degrades when VITE_CLUSTER_REGIONS_URL is missing in multi-region", async () => {
    (import.meta.env as Record<string, unknown>).VITE_CLUSTER_MODE = "multi-region";
    delete (import.meta.env as Record<string, unknown>).VITE_CLUSTER_REGIONS_URL;
    const { clusterConfig, loadClusterConfig } = await import("@/lib/cluster-config");
    await loadClusterConfig(); // must NOT throw
    expect(clusterConfig.regions).toEqual([]);
  });

  it("multi-region fetches the regions URL and populates clusterConfig.regions", async () => {
    (import.meta.env as Record<string, unknown>).VITE_CLUSTER_MODE = "multi-region";
    (import.meta.env as Record<string, unknown>).VITE_CLUSTER_REGIONS_URL = "/cluster-config.json";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ regions: [{ id: "cn-1", displayName: "华东一区" }] }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      ),
    );
    const { clusterConfig, loadClusterConfig } = await import("@/lib/cluster-config");
    await loadClusterConfig();
    expect(clusterConfig.mode).toBe("multi-region");
    expect(clusterConfig.regions).toEqual([{ id: "cn-1", displayName: "华东一区" }]);
    expect(JSON.parse(localStorage.getItem(CACHE_KEY) ?? "null")).toMatchObject({
      regions: [{ id: "cn-1" }],
    });
  });

  it("falls back to localStorage cache when fetch fails and cache is fresh", async () => {
    (import.meta.env as Record<string, unknown>).VITE_CLUSTER_MODE = "multi-region";
    (import.meta.env as Record<string, unknown>).VITE_CLUSTER_REGIONS_URL = "/cluster-config.json";
    localStorage.setItem(
      CACHE_KEY,
      JSON.stringify({
        savedAt: Date.now() - 60_000,
        regions: [{ id: "cn-1", displayName: "华东一区(cached)" }],
      }),
    );
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));
    const { clusterConfig, loadClusterConfig } = await import("@/lib/cluster-config");
    await loadClusterConfig();
    expect(clusterConfig.regions).toEqual([{ id: "cn-1", displayName: "华东一区(cached)" }]);
  });

  it("graceful degrades to empty regions when fetch fails and cache is stale/absent", async () => {
    (import.meta.env as Record<string, unknown>).VITE_CLUSTER_MODE = "multi-region";
    (import.meta.env as Record<string, unknown>).VITE_CLUSTER_REGIONS_URL = "/cluster-config.json";
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));
    const { clusterConfig, loadClusterConfig } = await import("@/lib/cluster-config");
    await loadClusterConfig();
    expect(clusterConfig.mode).toBe("multi-region");
    expect(clusterConfig.regions).toEqual([]);
  });

  it("rejects response with duplicate region ids", async () => {
    (import.meta.env as Record<string, unknown>).VITE_CLUSTER_MODE = "multi-region";
    (import.meta.env as Record<string, unknown>).VITE_CLUSTER_REGIONS_URL = "/cluster-config.json";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            regions: [
              { id: "cn-1", displayName: "A" },
              { id: "cn-1", displayName: "B" },
            ],
          }),
          { status: 200 },
        ),
      ),
    );
    const { clusterConfig, loadClusterConfig } = await import("@/lib/cluster-config");
    await loadClusterConfig();
    expect(clusterConfig.regions).toEqual([]);
  });

  it("treats a 404 response like a fetch failure and graceful-degrades", async () => {
    (import.meta.env as Record<string, unknown>).VITE_CLUSTER_MODE = "multi-region";
    (import.meta.env as Record<string, unknown>).VITE_CLUSTER_REGIONS_URL = "/cluster-config.json";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("not found", { status: 404 })),
    );
    const { clusterConfig, loadClusterConfig } = await import("@/lib/cluster-config");
    await loadClusterConfig();
    expect(clusterConfig.regions).toEqual([]);
  });
});
