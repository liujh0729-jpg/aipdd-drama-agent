// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import { splitLiteralSourceText } from "@/lib/literal-source-text";

describe("splitLiteralSourceText", () => {
  it("normalizes newlines, trims rows, and drops empty rows", () => {
    expect(splitLiteralSourceText(" 第一行\r\n\r\n第二行 \r 第三行\n   ")).toEqual([
      "第一行",
      "第二行",
      "第三行",
    ]);
  });
});
