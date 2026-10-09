// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  DIRECTOR_WORLD_SOURCE_CAPABILITIES,
  directorPlacementSpaceForSource,
  isActorPlacement,
  isPropOrStagingPlacement,
} from "@/features/viewer-kit/three-d/directorManifest";
import type {
  DirectorPlacedObject,
  DirectorPlacement,
  DirectorStageManifest,
  DirectorWorldSource,
} from "@/features/viewer-kit/three-d/directorManifest";

const meshSource = { source_type: "mesh" } satisfies DirectorWorldSource;

const worldPlacement = {
  space: "world",
  position: [1, 0, -2],
  yawDeg: 45,
} satisfies DirectorPlacement;

const stageManifest = {
  viewer_kind: "three_d_director",
  mode: "scene",
  project: "demo",
  scene_id: "scene-1",
  display_name: "Scene 1",
  source: {
    source_type: "pano360",
    pano_url: "/static/demo/pano.webp",
    source_kind: "pano",
  },
  palette: {
    actors: [],
    props: [],
    anonymous_colors: [],
    anonymous_prop_colors: [],
  },
  allowed_destinations: ["view"],
} satisfies DirectorStageManifest;

const actorPlacedObject = {
  kind: "actor",
  placement: worldPlacement,
  pose: "standing",
} satisfies DirectorPlacedObject;

const propPlacedObject = {
  kind: "prop",
  placement: worldPlacement,
  shapeHint: "lantern",
} satisfies DirectorPlacedObject;

const stagingPlacedObject = {
  kind: "staging",
  placement: worldPlacement,
  shapeHint: "mark",
} satisfies DirectorPlacedObject;

describe("director world contract", () => {
  it("advertises the supported source capabilities", () => {
    expect(Object.keys(DIRECTOR_WORLD_SOURCE_CAPABILITIES).sort()).toEqual(["mesh", "pano360", "sog"]);

    for (const capability of Object.values(DIRECTOR_WORLD_SOURCE_CAPABILITIES)) {
      expect(capability.captureBundle).toEqual(["combined", "env_only", "frame_meta"]);
    }
  });

  it("maps source types to their placement spaces", () => {
    expect(directorPlacementSpaceForSource({ source_type: "pano360" })).toBe("world");
    expect(directorPlacementSpaceForSource({ source_type: "sog" })).toBe("world");
    expect(directorPlacementSpaceForSource(meshSource)).toBe("world");
  });

  it("identifies actor-only placement features", () => {
    expect(isActorPlacement("actor")).toBe(true);
    expect(isActorPlacement("prop")).toBe(false);
    expect(isActorPlacement("staging")).toBe(false);
  });

  it("identifies prop and staging placement features", () => {
    expect(isPropOrStagingPlacement("prop")).toBe(true);
    expect(isPropOrStagingPlacement("staging")).toBe(true);
    expect(isPropOrStagingPlacement("actor")).toBe(false);
  });

  it("keeps one object model with polymorphic placement", () => {
    expect(stageManifest.viewer_kind).toBe("three_d_director");
    expect(actorPlacedObject.placement.space).toBe("world");
    expect(propPlacedObject.placement.space).toBe("world");
    expect(stagingPlacedObject.placement.space).toBe("world");
  });
});
