// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  parseBeatParam,
  parseBeatsSubParam,
} from "@/hooks/use-beats-workbench-param";

describe("beats workbench URL params", () => {
  it("parses a positive integer beat param", () => {
    expect(parseBeatParam("3")).toBe(3);
    expect(parseBeatParam(4)).toBe(4);
    expect(parseBeatParam("0")).toBeNull();
    expect(parseBeatParam("1.5")).toBeNull();
  });

  it("accepts only supported sub section params", () => {
    expect(parseBeatsSubParam("sketch")).toBe("sketch");
    expect(parseBeatsSubParam("render")).toBe("render");
    expect(parseBeatsSubParam("audio")).toBe("audio");
    expect(parseBeatsSubParam("video")).toBe("video");
    expect(parseBeatsSubParam("text")).toBeNull();
    expect(parseBeatsSubParam("unknown")).toBeNull();
  });
});
