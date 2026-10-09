// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import type { MainlineContext } from "@/features/freezone/context/mainlineContext";
import {
  buildContextPromptPalette,
  buildContextPromptPaletteForNode,
  contextPromptPaletteInsertionText,
} from "@/features/canvas/nodes/contextPromptPalette";

function beatContext(overrides: Partial<MainlineContext> = {}): MainlineContext {
  return {
    kind: "beat",
    projectId: "project-1",
    episode: 1,
    beat: 2,
    label: "EP1 / Beat 2",
    sketchColors: {
      "identity:面馆男青年_青年时期": "#FF00FF",
      "identity:面馆女青年_青年时期": "#00E5FF",
    },
    propMarkerColors: {
      "prop:纸箱": "#B71C1C",
    },
    ...overrides,
  };
}

describe("context prompt palette", () => {
  it("always returns the default palettes and decorates matching beat context colors", () => {
    const palette = buildContextPromptPalette([beatContext()]);

    expect(palette.actorEntries).toHaveLength(12);
    expect(palette.propEntries).toHaveLength(10);
    expect(palette.actorEntries[0]).toEqual({
      kind: "actor",
      id: "identity:面馆男青年_青年时期",
      label: "面馆男青年_青年时期",
      named: true,
      color: "#FF00FF",
    });
    expect(palette.actorEntries[1]).toEqual({
      kind: "actor",
      id: "actor:#00FFFF",
      label: "",
      named: false,
      color: "#00FFFF",
    });
    expect(palette.propEntries[0]).toEqual({
      kind: "prop",
      id: "prop:纸箱",
      label: "纸箱",
      named: true,
      color: "#B71C1C",
    });
  });

  it("uses anonymous color-only palettes without beat context colors", () => {
    const palette = buildContextPromptPalette([
      {
        kind: "scene",
        projectId: "project-1",
        sceneId: "兰州拉面馆",
      },
    ]);

    expect(palette.hasEntries).toBe(true);
    expect(palette.actorEntries[0]).toEqual({
      kind: "actor",
      id: "actor:#FF00FF",
      label: "",
      named: false,
      color: "#FF00FF",
    });
    expect(palette.propEntries[0]).toEqual({
      kind: "prop",
      id: "prop:#B71C1C",
      label: "",
      named: false,
      color: "#B71C1C",
    });
  });

  it("falls back to the only canvas beat context with colors", () => {
    const palette = buildContextPromptPaletteForNode(
      [
        {
          id: "beat-context-1",
          type: "beatContextNode",
          data: { mainline_context: [beatContext()] },
        },
        {
          id: "image-gen-1",
          type: "imageGenNode",
          data: { mainline_context: [] },
        },
      ],
      [],
      "image-gen-1",
    );

    expect(palette.hasEntries).toBe(true);
    expect(palette.actorEntries[0].label).toBe("面馆男青年_青年时期");
    expect(palette.propEntries[0].label).toBe("纸箱");
  });

  it("falls back to a standalone BeatContextNode beat_context palette", () => {
    const palette = buildContextPromptPaletteForNode(
      [
        {
          id: "standalone-beat-context",
          type: "beatContextNode",
          data: {
            context_scope: "standalone",
            beat_context: {
              schema: "beat_context.v1",
              source: "standalone",
              sketch_colors: {
                "女主_雨衣": "#FF00FF",
              },
              prop_marker_colors: {
                "红伞": "#B71C1C",
              },
            },
          },
        },
        {
          id: "image-gen-1",
          type: "imageGenNode",
          data: { mainline_context: [] },
        },
      ],
      [],
      "image-gen-1",
    );

    expect(palette.actorEntries[0].label).toBe("女主_雨衣");
    expect(palette.propEntries[0].label).toBe("红伞");
  });

  it("still exposes the default palettes when multiple canvas beat palettes exist", () => {
    const palette = buildContextPromptPaletteForNode(
      [
        {
          id: "beat-context-1",
          type: "beatContextNode",
          data: { mainline_context: [beatContext({ beat: 1 })] },
        },
        {
          id: "beat-context-2",
          type: "beatContextNode",
          data: { mainline_context: [beatContext({ beat: 2 })] },
        },
        {
          id: "image-gen-1",
          type: "imageGenNode",
          data: { mainline_context: [] },
        },
      ],
      [],
      "image-gen-1",
    );

    expect(palette.hasEntries).toBe(true);
    expect(palette.actorEntries).toHaveLength(12);
    expect(palette.propEntries).toHaveLength(10);
    expect(palette.actorEntries[0].named).toBe(false);
    expect(palette.actorEntries[0].label).toBe("");
    expect(palette.propEntries[0].named).toBe(false);
    expect(palette.propEntries[0].label).toBe("");
  });

  it("builds insertion text that preserves name and exact color", () => {
    expect(
      contextPromptPaletteInsertionText({
        kind: "actor",
        id: "identity:面馆男青年_青年时期",
        label: "面馆男青年_青年时期",
        named: true,
        color: "#FF00FF",
      }),
    ).toBe("#FF00FF 标记的人物「面馆男青年_青年时期」");

    expect(
      contextPromptPaletteInsertionText({
        kind: "prop",
        id: "prop:纸箱",
        label: "纸箱",
        named: true,
        color: "#B71C1C",
      }),
    ).toBe("#B71C1C 标记的道具「纸箱」");
  });

  it("builds insertion text without names for anonymous palette colors", () => {
    expect(
      contextPromptPaletteInsertionText({
        kind: "actor",
        id: "actor:#FF00FF",
        label: "",
        named: false,
        color: "#FF00FF",
      }),
    ).toBe("#FF00FF 标记的人物");

    expect(
      contextPromptPaletteInsertionText({
        kind: "prop",
        id: "prop:#B71C1C",
        label: "",
        named: false,
        color: "#B71C1C",
      }),
    ).toBe("#B71C1C 标记的道具");
  });
});
