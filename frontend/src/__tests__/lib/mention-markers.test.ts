// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  extractIdentityMarkers,
  extractPropMarkers,
  mentionsToProgramMarkers,
  programMarkersToMentions,
} from "@/lib/mention-markers";

describe("mention marker helpers", () => {
  it("converts identity and prop @ mentions to backend marker syntax", () => {
    expect(
      mentionsToProgramMarkers("@陆辰_青年 拿起 @玉佩", {
        identities: ["陆辰_青年"],
        props: ["玉佩"],
      }),
    ).toBe("{{陆辰_青年}} 拿起 [[玉佩]]");
  });

  it("only converts known standalone mentions", () => {
    expect(
      mentionsToProgramMarkers("联系a@b.com，@未知 保留，@陆辰_青年 转换", {
        identities: ["陆辰_青年"],
        props: [],
      }),
    ).toBe("联系a@b.com，@未知 保留，{{陆辰_青年}} 转换");
  });

  it("resolves a known label even without a trailing space", () => {
    expect(
      mentionsToProgramMarkers("@青桐_少女时期哈哈哈", {
        identities: ["青桐_少女时期"],
        props: [],
      }),
    ).toBe("{{青桐_少女时期}}哈哈哈");
  });

  it("converts consecutive mentions that the user did not separate", () => {
    expect(
      mentionsToProgramMarkers("@青桐_少女时期哈哈 @二夫人_中年时期heihei", {
        identities: ["青桐_少女时期", "二夫人_中年时期"],
        props: [],
      }),
    ).toBe("{{青桐_少女时期}}哈哈 {{二夫人_中年时期}}heihei");
  });

  it("prefers the longest matching label", () => {
    expect(
      mentionsToProgramMarkers("@青桐_少女时期X", {
        identities: ["青桐_少女", "青桐_少女时期"],
        props: [],
      }),
    ).toBe("{{青桐_少女时期}}X");
  });

  it("resolves adjacent mentions whose labels end in digits", () => {
    // No look-behind on the char before `@`, so `@图片1@图片2` resolves both
    // even though `@图片2` directly follows the digit `1`.
    expect(
      mentionsToProgramMarkers("@图片1@图片2", {
        identities: [],
        props: ["图片1", "图片2"],
      }),
    ).toBe("[[图片1]][[图片2]]");
  });

  it("extracts unique identity and prop markers in first-seen order", () => {
    const text = "{{陆辰_青年}}拿起[[玉佩]]，{{陆辰_青年}}看向[[录音笔]]";

    expect(extractIdentityMarkers(text)).toEqual(["陆辰_青年"]);
    expect(extractPropMarkers(text)).toEqual(["玉佩", "录音笔"]);
  });

  it("converts stored backend markers back to user-facing @ mentions", () => {
    expect(programMarkersToMentions("{{陆辰_青年}}拿起[[玉佩]]")).toBe(
      "@陆辰_青年拿起@玉佩",
    );
  });
});
