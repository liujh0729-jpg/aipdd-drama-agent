// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it, vi } from "vitest";

import { saveBeatDirectorControlFrame } from "@/api/viewerManifests";
import { commitDirectorRenderFromCanvasSource } from "@/features/freezone/commit/directorRenderCommit";

vi.mock("@/api/viewerManifests", () => ({
  saveBeatDirectorControlFrame: vi.fn(async () => ({
    rel_paths: {
      combined: "director_control_frames/ep001/beat_06/combined.png",
      env_only: "director_control_frames/ep001/beat_06/env_only.png",
      frame_meta: "director_control_frames/ep001/beat_06/frame_meta.json",
    },
    urls: {
      combined: "/static/p/director_control_frames/ep001/beat_06/combined.png",
      env_only: "/static/p/director_control_frames/ep001/beat_06/env_only.png",
      frame_meta: "/static/p/director_control_frames/ep001/beat_06/frame_meta.json",
    },
  })),
}));

describe("commitDirectorRenderFromCanvasSource", () => {
  beforeEach(() => {
    vi.mocked(saveBeatDirectorControlFrame).mockClear();
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.endsWith("frame_meta.json")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            schema_version: "director_frame_meta_v1",
            frame_aspect: "16:9",
            camera: { mode: "sog", frame_aspect: "16:9", state: {} },
          }),
        } as Response;
      }
      return {
        ok: true,
        status: 200,
        blob: async () => new window.Blob(["png"], { type: "image/png" }),
      } as Response;
    }));
  });

  it("wraps an ordinary canvas image as a manual director bundle", async () => {
    const result = await commitDirectorRenderFromCanvasSource("proj", {
      kind: "director_render",
      episode: 1,
      beat: 6,
    }, {
      sourceUrl: "/static/p/freezone/edit.png",
      sourceNodeId: "node-1",
      label: "改过的图",
    });

    expect(result.target_path).toBe("director_control_frames/ep001/beat_06/combined.png");
    expect(saveBeatDirectorControlFrame).toHaveBeenCalledTimes(1);
    const payload = vi.mocked(saveBeatDirectorControlFrame).mock.calls[0]?.[3] as Record<string, unknown>;
    expect(payload.frame_meta).toMatchObject({
      schema_version: "director_frame_meta_v1",
      source: {
        source_id: "manual_canvas_commit:node-1",
        source_kind: "custom",
      },
      layer: {
        actors: [],
        props: [],
        stagings: [],
      },
    });
    expect(payload.images).toMatchObject({
      combined: expect.stringMatching(/^data:image\/png;base64,/),
      env_only: expect.stringMatching(/^data:image\/png;base64,/),
    });
  });

  it("submits an existing complete bundle without downgrading it", async () => {
    await commitDirectorRenderFromCanvasSource("proj", {
      kind: "director_render",
      episode: 1,
      beat: 6,
    }, {
      sourceUrl: "/static/p/ignored.png",
      bundle: {
        schema_version: "director_control_bundle_v1",
        rel_paths: {
          combined: "director_control_frames/ep001/beat_06/combined.png",
          env_only: "director_control_frames/ep001/beat_06/env_only.png",
          frame_meta: "director_control_frames/ep001/beat_06/frame_meta.json",
        },
        urls: {
          combined: "/static/p/director_control_frames/ep001/beat_06/combined.png",
          env_only: "/static/p/director_control_frames/ep001/beat_06/env_only.png",
          frame_meta: "/static/p/director_control_frames/ep001/beat_06/frame_meta.json",
        },
      },
    });

    expect(fetch).toHaveBeenCalledWith("/static/p/director_control_frames/ep001/beat_06/combined.png", { cache: "no-store" });
    expect(fetch).toHaveBeenCalledWith("/static/p/director_control_frames/ep001/beat_06/env_only.png", { cache: "no-store" });
    expect(fetch).toHaveBeenCalledWith("/static/p/director_control_frames/ep001/beat_06/frame_meta.json", { cache: "no-store" });
  });
});
