// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import { mentionDisplayLabel } from "@/features/canvas/nodes/PromptMentionEditor";

describe("mentionDisplayLabel", () => {
  it("strips the trailing number so 图片1/图片2/... all show as 图片", () => {
    expect(mentionDisplayLabel("图片1")).toBe("图片");
    expect(mentionDisplayLabel("图片2")).toBe("图片");
    expect(mentionDisplayLabel("图片10")).toBe("图片");
  });

  it("does the same for audio mentions", () => {
    expect(mentionDisplayLabel("音频1")).toBe("音频");
    expect(mentionDisplayLabel("音频3")).toBe("音频");
  });

  it("leaves names without a trailing number unchanged", () => {
    expect(mentionDisplayLabel("图片")).toBe("图片");
    expect(mentionDisplayLabel("音频")).toBe("音频");
  });

  it("falls back to the original name when stripping would empty it", () => {
    expect(mentionDisplayLabel("123")).toBe("123");
    expect(mentionDisplayLabel("")).toBe("");
  });
});
