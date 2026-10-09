// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import { inheritMainlineFields } from "@/features/canvas/domain/inheritMainlineFields";

describe("inheritMainlineFields", () => {
  it("marks derived nodes user-owned and strips projection management fields", () => {
    const result = inheritMainlineFields(
      {
        data: {
          mainline_context: [{ kind: "beat", projectId: "demo", episode: 1, beat: 4 }],
          slot_target: { kind: "frame", episode: 1, beat: 4 },
          committed_slot_url: "/static/frame.png",
        },
      },
      {
        displayName: "Edited frame",
        preset_managed: true,
        projection_key: "beat:1:4",
      },
    );

    expect(result.user_spawned).toBe(true);
    expect(result.mainline_context).toEqual([
      { kind: "beat", projectId: "demo", episode: 1, beat: 4 },
    ]);
    expect(result.slot_target).toEqual({ kind: "frame", episode: 1, beat: 4 });
    expect(result.committed_slot_url).toBe("/static/frame.png");
    expect(result).not.toHaveProperty("preset_managed");
    expect(result).not.toHaveProperty("projection_key");
    expect(result).toHaveProperty("source_projection_key", "beat:1:4");
  });
});
