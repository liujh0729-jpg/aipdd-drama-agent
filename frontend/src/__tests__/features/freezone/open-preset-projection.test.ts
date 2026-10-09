// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/stores/auth-store";
import { registerFreezoneCanvasRuntime } from "@/features/freezone/canvasSyncRuntime";
import { openPresetProjectionInMyCanvas } from "@/features/freezone/openPresetProjection";
import { personalCanvasIdForUsername } from "@/features/freezone/projections";

const buildProjectionFromPreset = vi.fn();

vi.mock("@/api/canvas", async () => {
  const actual = await vi.importActual<typeof import("@/api/canvas")>("@/api/canvas");
  return {
    ...actual,
    buildProjectionFromPreset: (...args: unknown[]) => buildProjectionFromPreset(...args),
  };
});

describe("openPresetProjectionInMyCanvas", () => {
  function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((res) => {
      resolve = res;
    });
    return { promise, resolve };
  }

  beforeEach(() => {
    useAuthStore.setState({ username: "Alice", role: "admin" });
    buildProjectionFromPreset.mockResolvedValue({
      projection_key: "beat:3:2",
      facts_signature: "sig",
      nodes: [],
      edges: [],
      metadata: null,
    });
    window.history.pushState(null, "", "/projects/proj-a/episodes/3/script");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("opens the freezone canvas without hard reloading the page", async () => {
    const onPopState = vi.fn();
    window.addEventListener("popstate", onPopState);

    const canvasId = await openPresetProjectionInMyCanvas("proj-a", {
      scope: "beat",
      episode: 3,
      beat: 2,
      primary_slot: "sketch",
    });

    expect(window.location.pathname).toBe("/projects/proj-a/freezone");
    expect(window.location.search).toBe(`?canvas=${encodeURIComponent(canvasId)}`);
    expect(onPopState).toHaveBeenCalledTimes(1);
    window.removeEventListener("popstate", onPopState);
  });

  it("does not return to freezone when the user navigates away while projection is pending", async () => {
    const pendingProjection = deferred<{
      projection_key: string;
      facts_signature: string;
      nodes: unknown[];
      edges: unknown[];
      metadata: null;
    }>();
    buildProjectionFromPreset.mockReturnValueOnce(pendingProjection.promise);
    window.history.pushState(null, "", "/projects/proj-a/freezone?canvas=old");

    const promise = openPresetProjectionInMyCanvas("proj-a", {
      scope: "beat",
      episode: 3,
      beat: 2,
      primary_slot: "sketch",
    });

    await vi.waitFor(() => expect(buildProjectionFromPreset).toHaveBeenCalled());
    window.history.pushState(null, "", "/projects/proj-a/episodes");
    pendingProjection.resolve({
      projection_key: "beat:3:2",
      facts_signature: "sig",
      nodes: [],
      edges: [],
      metadata: null,
    });

    await promise;

    expect(window.location.pathname).toBe("/projects/proj-a/episodes");
    expect(window.location.search).toBe("");
  });

  it("applies the built projection group to the currently open canvas runtime as a local edit", async () => {
    const canvasId = personalCanvasIdForUsername("Alice");
    const applyRemote = vi.fn();
    const applyLocal = vi.fn((_payload?: unknown) => true);
    const unregister = registerFreezoneCanvasRuntime(
      "proj-a",
      canvasId,
      applyRemote,
      undefined,
      applyLocal,
    );
    buildProjectionFromPreset.mockResolvedValueOnce({
      projection_key: "beat:3:2",
      facts_signature: "sig",
      nodes: [
        {
          id: "projection_group_beat_3_2",
          type: "groupNode",
          position: { x: 0, y: 0 },
          data: { projection_key: "beat:3:2" },
        },
      ],
      edges: [],
      metadata: { projections: { "beat:3:2": { projection_key: "beat:3:2" } } },
    });
    window.history.pushState(
      null,
      "",
      `/projects/proj-a/freezone?canvas=${encodeURIComponent(canvasId)}`,
    );

    await openPresetProjectionInMyCanvas("proj-a", {
      scope: "beat",
      episode: 3,
      beat: 2,
      primary_slot: "sketch",
    });

    expect(applyRemote).not.toHaveBeenCalled();
    expect(applyLocal).toHaveBeenCalledTimes(1);
    expect(applyLocal.mock.calls[0][0]).toMatchObject({
      projectionKey: "beat:3:2",
      nodes: [
        expect.objectContaining({
          id: "projection_group_beat_3_2",
        }),
      ],
      metadata: {
        projections: {
          "beat:3:2": {
            projection_key: "beat:3:2",
            request: {
              scope: "beat",
              episode: 3,
              beat: 2,
              primary_slot: "render",
            },
          },
        },
      },
    });

    unregister();
  });
});
