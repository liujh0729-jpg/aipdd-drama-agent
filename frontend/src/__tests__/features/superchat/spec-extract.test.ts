// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";
import { extractStructuredBlocks } from "@/features/superchat/spec-extract";

const specBody = JSON.stringify({
  type: "keyframe_video",
  root: "root",
  elements: {
    root: {
      type: "Card",
      props: { title: "Episode 1" },
      children: ["video_1"],
    },
    video_1: {
      type: "Video",
      props: {
        src: "/static/projects/demo/video.mp4",
        overlayTitle: "Beat 1",
      },
      children: [],
    },
  },
});

describe("extractStructuredBlocks", () => {
  it("parses ui-spec tags with attributes", () => {
    const result = extractStructuredBlocks({
      text: `<ui-spec type="keyframe_video">${specBody}</ui-spec>`,
      raw: null,
    });

    expect(result.displayText).toBe("");
    expect(result.blocks).toHaveLength(1);
    expect(result.blocks[0]?.label).toBe("ui-spec");
  });

  it("parses ui-spec tags inside json-render fences", () => {
    const result = extractStructuredBlocks({
      text: `\`\`\`json-render\n<ui-spec data-kind="video">${specBody}</ui-spec>\n\`\`\``,
      raw: null,
    });

    expect(result.displayText).toBe("");
    expect(result.blocks).toHaveLength(1);
    expect(result.blocks[0]?.label).toBe("ui-spec");
  });

  it("repairs a ui-spec JSON block missing one trailing object closer", () => {
    const malformed = specBody.slice(0, -1);
    const result = extractStructuredBlocks({
      text: `<ui-spec>${malformed}</ui-spec>`,
      raw: null,
    });

    expect(result.displayText).toBe("");
    expect(result.blocks).toHaveLength(1);
    expect(result.blocks[0]?.label).toBe("ui-spec");
  });

  it("recovers when prose mentions ui-spec before the real tag", () => {
    const result = extractStructuredBlocks({
      text: `当前环境支持 <ui-spec> JSON 块。真正内容：<ui-spec>${specBody}</ui-spec>`,
      raw: null,
    });

    expect(result.blocks).toHaveLength(1);
    expect(result.blocks[0]?.label).toBe("ui-spec");
  });

  it("parses multiple canonical specs from one media bundle tag", () => {
    const secondSpecBody = specBody.replace("video.mp4", "video-2.mp4");
    const result = extractStructuredBlocks({
      text: `<ui-spec type="media_bundle">[${specBody},${secondSpecBody}]</ui-spec>`,
      raw: null,
    });

    expect(result.displayText).toBe("");
    expect(result.blocks).toHaveLength(2);
    expect(result.blocks[0]?.label).toBe("ui-spec");
    expect(result.blocks[1]?.label).toBe("ui-spec");
  });
});
