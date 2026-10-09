// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import { sceneNameToRef, sceneRefToName } from "@/lib/scene-ref";

describe("scene-ref helpers", () => {
  it("keeps plain scene names as base scenes", () => {
    expect(sceneNameToRef("卫生间")).toEqual({
      scene_id: "卫生间",
      variant_id: "",
      plate_time_of_day: "",
    });
  });

  it("splits underscore scene names into base and variant", () => {
    expect(sceneNameToRef("卫生间_漏水")).toEqual({
      scene_id: "卫生间",
      variant_id: "漏水",
      plate_time_of_day: "",
    });
  });

  it("splits a standard time suffix into plate metadata without composing it back", () => {
    expect(sceneNameToRef("卫生间_漏水_夜晚")).toEqual({
      scene_id: "卫生间",
      variant_id: "漏水",
      plate_time_of_day: "夜晚",
    });
  });

  it("prefers structured scene records over name parsing", () => {
    expect(
      sceneNameToRef("卫生间_漏水_夜晚", [
        {
          name: "卫生间_漏水_夜晚",
          base_scene_id: "卫生间",
          variant_id: "漏水",
          time_of_day: "夜晚",
        },
      ]),
    ).toEqual({
      scene_id: "卫生间",
      variant_id: "漏水",
      plate_time_of_day: "夜晚",
    });
  });

  it("composes canonical scene refs back to display names", () => {
    expect(sceneRefToName({ scene_id: "卫生间", variant_id: "漏水" })).toBe(
      "卫生间_漏水",
    );
    expect(sceneRefToName({ scene_id: "卫生间", variant_id: "" })).toBe("卫生间");
  });
});
