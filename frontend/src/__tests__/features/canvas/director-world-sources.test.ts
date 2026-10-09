// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  directorPanoSourceFromCanvasNode,
  isPanoAspectRatio,
  mergeDirectorStageManifestSources,
  mergeDirectorSavedSceneMaps,
  mergeDirectorWorldSources,
  sourceFromImageTo3gsResult,
} from "@/features/canvas/domain/directorWorldSources";
import { CANVAS_NODE_TYPES, type CanvasNode } from "@/features/canvas/domain/canvasNodes";
import type { DirectorStageManifest } from "@/features/viewer-kit/three-d/directorManifest";

function node(type: CanvasNode["type"], data: Record<string, unknown>): CanvasNode {
  return {
    id: String(data.id ?? "node-1"),
    type,
    position: { x: 0, y: 0 },
    data,
  } as CanvasNode;
}

describe("directorWorldSources", () => {
  it("classifies legacy pano viewer nodes as raw pano360 sources", () => {
    const source = directorPanoSourceFromCanvasNode(
      node(CANVAS_NODE_TYPES.pano360Viewer, {
        id: "pano-viewer-1",
        imageUrl: "/static/demo/pano.png",
        displayName: "老 360 查看器",
      }),
    );

    expect(source).toMatchObject({
      id: "upstream-pano:pano-viewer-1",
      source_type: "pano360",
      source_kind: "pano",
      label: "老 360 查看器",
      pano_url: "/static/demo/pano.png",
      slot_kind: "scene_director_pano_360",
    });
  });

  it("classifies generated 360 image nodes as raw pano360 sources", () => {
    const source = directorPanoSourceFromCanvasNode(
      node(CANVAS_NODE_TYPES.exportImage, {
        id: "generated-360",
        imageUrl: "/static/demo/generated_360.png",
        output_role: "scene_360_candidate",
        media_kind: "pano360",
        aspectRatio: "2:1",
        displayName: "生成 360",
      }),
    );

    expect(source?.source_type).toBe("pano360");
    expect(source?.pano_url).toBe("/static/demo/generated_360.png");
  });

  it("does not classify ordinary 16:9 images as raw pano360 sources", () => {
    const source = directorPanoSourceFromCanvasNode(
      node(CANVAS_NODE_TYPES.exportImage, {
        id: "ordinary-image",
        imageUrl: "/static/demo/frame.png",
        aspectRatio: "16:9",
      }),
    );

    expect(source).toBeNull();
  });

  it("classifies unmarked 2:1 uploads as pano360 sources", () => {
    const upload = node(CANVAS_NODE_TYPES.upload, {
      id: "upload-2x1",
      imageUrl: "/static/demo/user-upload.png",
      aspectRatio: "2:1",
    });

    const source = directorPanoSourceFromCanvasNode(upload);

    expect(isPanoAspectRatio(upload)).toBe(true);
    expect(source).toMatchObject({
      source_type: "pano360",
      pano_url: "/static/demo/user-upload.png",
    });
  });

  it("can create a pano source from an explicitly marked panorama image even when aspect ratio is not 2:1", () => {
    const upload = node(CANVAS_NODE_TYPES.upload, {
      id: "upload-marked-pano",
      imageUrl: "/static/demo/user-upload.png",
      aspectRatio: "16:9",
      media_kind: "pano360",
    });

    const source = directorPanoSourceFromCanvasNode(upload);

    expect(source).toMatchObject({
      source_type: "pano360",
      pano_url: "/static/demo/user-upload.png",
    });
  });

  it("deduplicates sources by URL while preserving existing SOG source", () => {
    const sources = mergeDirectorWorldSources(
      [
        {
          id: "sog-master",
          source_type: "sog",
          source_kind: "master",
          label: "master",
          ply_url: "/static/demo/master.sog",
        },
      ],
      {
        id: "raw-pano",
        source_type: "pano360",
        source_kind: "pano",
        label: "360",
        pano_url: "/static/demo/pano.png",
      },
      {
        id: "raw-pano-copy",
        source_type: "pano360",
        source_kind: "pano",
        label: "360 copy",
        pano_url: "/static/demo/pano.png",
      },
    );

    expect(sources).toHaveLength(2);
    expect(sources.map((source) => source.source_type)).toEqual(["sog", "pano360"]);
  });

  it("deduplicates stable source ids when backend media versions change", () => {
    const sources = mergeDirectorWorldSources(
      [
        {
          id: "legacy:master:sog:/static/demo/master.sog",
          source_type: "sog",
          source_kind: "master",
          label: "master",
          ply_url: "/static/demo/master.sog?v=111",
          current: true,
        },
      ],
      {
        id: "legacy:master:sog:/static/demo/master.sog",
        source_type: "sog",
        source_kind: "master",
        label: "master",
        ply_url: "/static/demo/master.sog?v=222",
        url: "/static/demo/master.sog?v=222",
      },
    );

    expect(sources).toHaveLength(1);
    expect(sources[0]).toMatchObject({
      id: "legacy:master:sog:/static/demo/master.sog",
      ply_url: "/static/demo/master.sog?v=222",
      url: "/static/demo/master.sog?v=222",
      current: true,
    });
  });

  it("deduplicates versioned source URLs when ids are missing", () => {
    const sources = mergeDirectorWorldSources(
      [
        {
          source_type: "sog",
          source_kind: "master",
          ply_url: "/static/demo/master.sog?v=111",
        },
      ],
      {
        source_type: "sog",
        source_kind: "master",
        ply_url: "/static/demo/master.sog?v=222#etag",
      },
    );

    expect(sources).toHaveLength(1);
    expect(sources[0]?.ply_url).toBe("/static/demo/master.sog?v=222#etag");
  });

  it("normalizes image-to-3GS task results into SOG sources", () => {
    const source = sourceFromImageTo3gsResult(
      {
        output_url: "/static/demo/world.sog",
      },
      {
        id: "task-source",
        sourceKind: "pano",
        label: "360 生成世界",
      },
    );

    expect(source).toMatchObject({
      id: "task-source",
      source_type: "sog",
      source_kind: "pano",
      label: "360 生成世界",
      ply_url: "/static/demo/world.sog",
      url: "/static/demo/world.sog",
      current: true,
    });
  });

  it("merges canvas pano sources into a beat director manifest without dropping backend sources", () => {
    const manifest: DirectorStageManifest = {
      viewer_kind: "three_d_director",
      mode: "beat",
      project: "demo",
      scene_id: "scene-1",
      display_name: "导演世界",
      source: {
        source_type: "sog",
        source_kind: "master",
        ply_url: "/static/demo/master.sog",
      },
      palette: { actors: [], props: [], anonymous_colors: [], anonymous_prop_colors: [] },
      allowed_destinations: ["view"],
    };

    const merged = mergeDirectorStageManifestSources(manifest, [
      {
        id: "upstream-pano:pano-1",
        source_type: "pano360",
        source_kind: "pano",
        label: "360 图",
        pano_url: "/static/demo/pano.png",
      },
    ]);

    expect(merged.sources?.map((source) => source.source_type)).toEqual(["sog", "pano360"]);
    expect(merged.sources?.[1]).toMatchObject({
      id: "upstream-pano:pano-1",
      source_type: "pano360",
      pano_url: "/static/demo/pano.png",
    });
  });

  it("keeps scene source option ids aligned with saved director-world snapshots", () => {
    const manifest: DirectorStageManifest = {
      viewer_kind: "three_d_director",
      mode: "scene",
      project: "demo",
      scene_id: "Hall",
      display_name: "Hall",
      active_source_id: "legacy:master:sog:/static/demo/master.sog",
      source: {
        source_type: "sog",
        source_kind: "master",
        ply_url: "/static/demo/master.sog",
      },
      source_options: [
        {
          kind: "master",
          label: "master",
          source_type: "sog",
          ply_url: "/static/demo/master.sog",
        },
        {
          kind: "pano",
          label: "360",
          source_type: "pano360",
          pano_url: "/static/demo/pano.png",
          slot_kind: "scene_director_pano_360",
        },
      ],
      scenes_by_source_id: {
        "legacy:master:sog:/static/demo/master.sog": {
          schemaVersion: 1,
          savedAt: 1,
          actors: [],
          props: [],
          stagings: [],
          world: { activeSourceId: "legacy:master:sog:/static/demo/master.sog" },
        },
      },
      palette: { actors: [], props: [], anonymous_colors: [], anonymous_prop_colors: [] },
      allowed_destinations: ["view"],
    };

    const merged = mergeDirectorStageManifestSources(manifest, []);

    expect(merged.sources?.map((source) => source.id)).toEqual([
      "legacy:master:sog:/static/demo/master.sog",
      "scene-pano:Hall",
    ]);
    expect(merged.active_source_id).toBe("legacy:master:sog:/static/demo/master.sog");
    expect(merged.scenes_by_source_id).toHaveProperty(
      "legacy:master:sog:/static/demo/master.sog",
    );
  });

  it("drops legacy active source options so the picker shows canonical source names", () => {
    const manifest: DirectorStageManifest = {
      viewer_kind: "three_d_director",
      mode: "scene",
      project: "demo",
      scene_id: "Hall",
      display_name: "Hall",
      active_source_id: "legacy:master:sog:/static/demo/master.sog",
      source: {
        source_type: "sog",
        source_kind: "master",
        ply_url: "/static/demo/master.sog",
      },
      source_options: [
        {
          kind: "active",
          label: "active",
          source_type: "sog",
          ply_url: "/static/demo/master.sog",
          current: true,
        },
        {
          kind: "master",
          label: "master",
          source_type: "sog",
          ply_url: "/static/demo/master.sog",
        },
        {
          kind: "reverse",
          label: "reverse",
          source_type: "sog",
          ply_url: "/static/demo/reverse.sog",
        },
      ],
      palette: { actors: [], props: [], anonymous_colors: [], anonymous_prop_colors: [] },
      allowed_destinations: ["view"],
    };

    const merged = mergeDirectorStageManifestSources(manifest, []);

    expect(merged.sources?.map((source) => source.label)).toEqual(["master", "reverse"]);
    expect(merged.sources?.map((source) => source.id)).toEqual([
      "legacy:master:sog:/static/demo/master.sog",
      "legacy:reverse:sog:/static/demo/reverse.sog",
    ]);
  });

  it("derives stable scene source option ids from versioned static URLs", () => {
    const manifest: DirectorStageManifest = {
      viewer_kind: "three_d_director",
      mode: "scene",
      project: "demo",
      scene_id: "Hall",
      display_name: "Hall",
      active_source_id: "legacy:master:sog:/static/demo/master.sog",
      source: {
        source_type: "sog",
        source_kind: "master",
        ply_url: "/static/demo/master.sog?v=101",
      },
      source_options: [
        {
          kind: "master",
          label: "master",
          source_type: "sog",
          ply_url: "/static/demo/master.sog?v=202#etag",
        },
        {
          kind: "reverse",
          label: "reverse",
          source_type: "sog",
          ply_url: "/static/demo/reverse.sog?v=303",
        },
      ],
      palette: { actors: [], props: [], anonymous_colors: [], anonymous_prop_colors: [] },
      allowed_destinations: ["view"],
    };

    const merged = mergeDirectorStageManifestSources(manifest, []);

    expect(merged.sources?.map((source) => source.id)).toEqual([
      "legacy:master:sog:/static/demo/master.sog",
      "legacy:reverse:sog:/static/demo/reverse.sog",
    ]);
    expect(merged.sources?.[0]?.ply_url).toBe("/static/demo/master.sog?v=202#etag");
  });

  it("keeps manifest saved scenes when the node only has an empty local scene map", () => {
    const manifestScene = {
      schemaVersion: 1,
      savedAt: 1,
      actors: [{ id: "actor-1", label: "杜晨" }],
      props: [],
      stagings: [],
      world: { activeSourceId: "legacy:master:sog:/static/demo/master.sog" },
    };

    const merged = mergeDirectorSavedSceneMaps(
      {},
      { "legacy:master:sog:/static/demo/master.sog": manifestScene },
    );

    expect(merged).toEqual({
      "legacy:master:sog:/static/demo/master.sog": manifestScene,
    });
  });
});
