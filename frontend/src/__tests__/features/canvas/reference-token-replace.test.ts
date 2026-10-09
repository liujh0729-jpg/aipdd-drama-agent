// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  findReferenceTokenAtSelection,
  replaceReferenceToken,
} from "@/features/canvas/application/referenceTokenEditing";

describe("findReferenceTokenAtSelection", () => {
  const text = "让 @图1 和 @图2 同框"; // @图1 在 [2,5)，@图2 在 [8,11)

  it("finds the token whose range overlaps the double-click selection", () => {
    // 双击「图1」大致选中 [3,5)（@ 之后），应命中第一个 token。
    const hit = findReferenceTokenAtSelection(text, 3, 5, 2);
    expect(hit?.token).toBe("@图1");
    expect(hit?.start).toBe(2);
    expect(hit?.end).toBe(5);
  });

  it("finds the second token when the selection is on 图2", () => {
    const hit = findReferenceTokenAtSelection(text, 9, 11, 2);
    expect(hit?.token).toBe("@图2");
  });

  it("returns null when the selection is on plain text", () => {
    expect(findReferenceTokenAtSelection(text, 0, 1, 2)).toBeNull();
  });

  it("matches a collapsed caret sitting inside the token", () => {
    // 选区塌缩在 token 内部（图 与 1 之间）也应命中。
    const hit = findReferenceTokenAtSelection(text, 4, 4, 2);
    expect(hit?.token).toBe("@图1");
  });
});

describe("replaceReferenceToken", () => {
  it("replaces the token range with the new marker and returns the caret after it", () => {
    const text = "让 @图1 动起来";
    // @图1 在 [2,5)
    const { nextText, nextCursor } = replaceReferenceToken(
      text,
      { start: 2, end: 5 },
      "@图2",
    );
    expect(nextText).toBe("让 @图2 动起来");
    expect(nextCursor).toBe(2 + "@图2".length);
  });

  it("works for multi-digit markers", () => {
    const { nextText } = replaceReferenceToken("a @图1 b", { start: 2, end: 5 }, "@图10");
    expect(nextText).toBe("a @图10 b");
  });
});
