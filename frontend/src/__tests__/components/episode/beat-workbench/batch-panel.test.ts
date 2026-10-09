// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  getBatchPanelActionDisabled,
  createAutoSketchRegenQueueItems,
  createSketchRegenPlanItems,
  createSingleSketchRegenQueueItems,
  createSketchRegenQueueItem,
  findSketchRegenQueueTask,
  getLockedSketchRegenItemIds,
  getSketchRegenQueueConflict,
  getSketchRegenPreflight,
  getSketchRegenSceneIds,
  sketchModeCellAspect,
  sketchRegenModesForAspect,
  shouldShowSketchModeSpinner,
  sketchRegenUsageScope,
} from "@/components/episode/beat-workbench/batch-panel";
import { SKETCH_REGEN_MODES, bestFitMode } from "@/lib/regen-modes";
import type { Beat } from "@/types/episode";

function beat(overrides: Partial<Beat>): Beat {
  return {
    beat_number: 1,
    narration_segment: "n",
    visual_description: "v",
    ...overrides,
  };
}

describe("getSketchRegenSceneIds", () => {
  it("keeps sketch dispatch independent from audio task state", () => {
    const base = {
      count: 4,
      regenSketchesPending: false,
      saveSketchQueuePending: false,
      generateAudioPending: false,
      audioTaskStarted: false,
    };

    expect(
      getBatchPanelActionDisabled({
        ...base,
        sketchTaskStarted: true,
      }),
    ).toMatchObject({
      sketch: false,
    });
    expect(
      getBatchPanelActionDisabled({
        ...base,
        sketchTaskStarted: false,
        audioTaskStarted: true,
      }),
    ).toMatchObject({
      sketch: false,
      audio: true,
    });
    expect(
      getBatchPanelActionDisabled({
        ...base,
        regenSketchesPending: true,
        sketchTaskStarted: false,
      }),
    ).toMatchObject({
      sketch: true,
      audio: false,
    });
  });

  it("returns one scene id for selected beats in the same scene", () => {
    const scenes = getSketchRegenSceneIds(
      [
        beat({ beat_number: 1, scene_ref: { scene_id: "store_day" } }),
        beat({ beat_number: 2, scene_ref: { scene_id: "store_day" } }),
      ],
      [1, 2],
    );

    expect(scenes).toEqual(["store_day"]);
  });

  it("returns multiple scene ids for mixed-scene sketch regeneration", () => {
    const scenes = getSketchRegenSceneIds(
      [
        beat({ beat_number: 1, scene_ref: { scene_id: "store" } }),
        beat({ beat_number: 2, scene_ref: { scene_id: "street" } }),
      ],
      [1, 2],
    );

    expect(scenes).toEqual(["store", "street"]);
  });

  it("requires known scene bindings before multi-beat sketch regeneration", () => {
    const preflight = getSketchRegenPreflight(
      [
        beat({ beat_number: 1, scene_ref: null }),
        beat({ beat_number: 2, scene_ref: null }),
      ],
      [1, 2],
    );

    expect(preflight).toEqual({
      ok: false,
      reason: "missing_scene",
      sceneIds: [],
      missingBeatNumbers: [1, 2],
    });
  });

  it("rejects mixed explicit scenes before dispatch", () => {
    const preflight = getSketchRegenPreflight(
      [
        beat({ beat_number: 1, scene_ref: { scene_id: "store" } }),
        beat({ beat_number: 2, scene_ref: { scene_id: "street" } }),
      ],
      [1, 2],
    );

    expect(preflight).toEqual({
      ok: false,
      reason: "mixed_scene",
      sceneIds: ["store", "street"],
      missingBeatNumbers: [],
    });
  });

  it("exposes the NiceGUI sketch regen mode menu order instead of only five modes", () => {
    expect(SKETCH_REGEN_MODES.length).toBeGreaterThan(5);
    expect(SKETCH_REGEN_MODES.slice(0, 5).map((mode) => mode.key)).toEqual([
      "5x5_2-3_sketch",
      "1x1_2-3_sketch",
      "1x1_1-1_sketch",
      "2x2_2-3_sketch",
      "3x3_2-3_sketch",
    ]);
    expect(SKETCH_REGEN_MODES.map((mode) => mode.key)).toContain("1x1_16-9_sketch");
    expect(SKETCH_REGEN_MODES.map((mode) => mode.key)).toContain("5x5_9-16_sketch");
    expect(SKETCH_REGEN_MODES.map((mode) => mode.key)).toContain("5x5_1-1");
  });

  it("keeps recommendation based on smallest fitting capacity after adopting NiceGUI order", () => {
    expect(bestFitMode(SKETCH_REGEN_MODES, 2).key).toBe("1x2_4-3_sketch");
  });

  it("derives the actual per-cell aspect from sketch mode keys", () => {
    expect(sketchModeCellAspect("1x2_4-3_sketch")).toBe("2:3");
    expect(sketchModeCellAspect("2x2_16-9_sketch")).toBe("16:9");
    expect(sketchModeCellAspect("5x5_9-16_sketch")).toBe("9:16");
  });

  it("filters direct sketch regen modes to the current project sketch aspect", () => {
    const landscapeModes = sketchRegenModesForAspect(SKETCH_REGEN_MODES, "16:9");
    expect(landscapeModes.map((mode) => mode.key)).toContain("2x2_16-9_sketch");
    expect(landscapeModes.map((mode) => mode.key)).not.toContain("1x2_4-3_sketch");
    expect(bestFitMode(landscapeModes, 2).key).toBe("2x2_16-9_sketch");

    const portraitModes = sketchRegenModesForAspect(SKETCH_REGEN_MODES, "2:3");
    expect(portraitModes.map((mode) => mode.key)).toContain("1x2_4-3_sketch");
    expect(portraitModes.map((mode) => mode.key)).not.toContain("2x2_16-9_sketch");
  });

  it("creates a NiceGUI-style sketch regen dispatch card item", () => {
    const item = createSketchRegenQueueItem(
      [
        beat({ beat_number: 1, scene_ref: { scene_id: "store" } }),
        beat({ beat_number: 2, scene_ref: { scene_id: "store" } }),
      ],
      [2, 1],
      { key: "2x2_2-3_sketch", label: "2×2", capacity: 4 },
    );

    expect(item).toMatchObject({
      id: "2x2_2-3_sketch:1,2",
      modeKey: "2x2_2-3_sketch",
      modeLabel: "2×2",
      beatNumbers: [1, 2],
      sceneIds: ["store"],
    });
  });

  it("creates one 1x1 sketch regen queue item per beat for single redraw", () => {
    const items = createSingleSketchRegenQueueItems(
      [
        beat({ beat_number: 1, scene_ref: { scene_id: "store" } }),
        beat({ beat_number: 2, scene_ref: { scene_id: "street" } }),
      ],
      [1, 2],
      "16:9",
    );

    expect(items).toMatchObject([
      {
        modeKey: "1x1_16-9_sketch",
        beatNumbers: [1],
        sceneIds: ["store"],
      },
      {
        modeKey: "1x1_16-9_sketch",
        beatNumbers: [2],
        sceneIds: ["street"],
      },
    ]);
  });

  it("auto-combines sketch regen queue items by scene using current aspect", () => {
    const items = createAutoSketchRegenQueueItems(
      [
        beat({ beat_number: 1, scene_ref: { scene_id: "store" } }),
        beat({ beat_number: 2, scene_ref: { scene_id: "street" } }),
        beat({ beat_number: 3, scene_ref: { scene_id: "street" } }),
      ],
      [1, 2, 3],
      "2:3",
    );

    expect(items).toMatchObject([
      {
        modeKey: "1x1_2-3_sketch",
        beatNumbers: [1],
        sceneIds: ["store"],
      },
      {
        modeKey: "1x2_4-3_sketch",
        beatNumbers: [2, 3],
        sceneIds: ["street"],
      },
    ]);
  });

  it("builds sketch plan items by scene by default", () => {
    const items = createSketchRegenPlanItems(
      [
        beat({ beat_number: 1, scene_ref: { scene_id: "store" } }),
        beat({ beat_number: 2, scene_ref: { scene_id: "street" } }),
        beat({ beat_number: 3, scene_ref: { scene_id: "street" } }),
      ],
      [1, 2, 3],
      "2:3",
    );

    expect(items).toMatchObject([
      {
        modeKey: "1x1_2-3_sketch",
        modeLabel: "1×1_2:3 Sketch",
        beatNumbers: [1],
        sceneIds: ["store"],
      },
      {
        modeKey: "1x2_4-3_sketch",
        modeLabel: "1×2_4:3 Sketch",
        beatNumbers: [2, 3],
        sceneIds: ["street"],
      },
    ]);
  });

  it("auto-combines landscape sketch groups with 16:9 cell modes", () => {
    const items = createAutoSketchRegenQueueItems(
      [
        beat({ beat_number: 1, scene_ref: { scene_id: "store" } }),
        beat({ beat_number: 2, scene_ref: { scene_id: "store" } }),
      ],
      [1, 2],
      "16:9",
    );

    expect(items).toMatchObject([
      {
        modeKey: "2x2_16-9_sketch",
        beatNumbers: [1, 2],
        sceneIds: ["store"],
      },
    ]);
  });

  it("rejects duplicate and overlapping sketch regen dispatch cards", () => {
    const first = createSketchRegenQueueItem(
      [
        beat({ beat_number: 1, scene_ref: { scene_id: "store" } }),
        beat({ beat_number: 2, scene_ref: { scene_id: "store" } }),
      ],
      [1, 2],
      { key: "2x2_2-3_sketch", label: "2×2", capacity: 4 },
    );
    const duplicate = createSketchRegenQueueItem(
      [
        beat({ beat_number: 1, scene_ref: { scene_id: "store" } }),
        beat({ beat_number: 2, scene_ref: { scene_id: "store" } }),
      ],
      [1, 2],
      { key: "2x2_2-3_sketch", label: "2×2", capacity: 4 },
    );
    const overlap = createSketchRegenQueueItem(
      [
        beat({ beat_number: 2, scene_ref: { scene_id: "store" } }),
        beat({ beat_number: 3, scene_ref: { scene_id: "store" } }),
      ],
      [2, 3],
      { key: "3x3_2-3_sketch", label: "3×3", capacity: 9 },
    );

    expect(getSketchRegenQueueConflict([first], duplicate)).toEqual({
      type: "duplicate",
      beatNumbers: [1, 2],
    });
    expect(getSketchRegenQueueConflict([first], overlap)).toEqual({
      type: "overlap",
      beatNumbers: [2],
    });
  });

  it("derives the NiceGUI image usage scope for sketch dispatch protection", () => {
    expect(
      sketchRegenUsageScope({
        id: "1x1_2-3_sketch:3",
        modeKey: "1x1_2-3_sketch",
        modeLabel: "1×1",
        beatNumbers: [3],
        sceneIds: ["store"],
        createdAt: "2026-05-18T00:00:00.000Z",
      }),
    ).toBe("sketch_grid:1x1_2-3_sketch:3");
  });

  it("matches persisted sketch regen queue cards to their scoped backend task", () => {
    const item = {
      id: "1x1_2-3_sketch:3",
      modeKey: "1x1_2-3_sketch",
      modeLabel: "1×1",
      beatNumbers: [3],
      sceneIds: ["store"],
      createdAt: "2026-05-18T00:00:00.000Z",
      taskScope: "sketch_grid:1x1_2-3_sketch:3",
    };

    const task = findSketchRegenQueueTask(
      [
        {
          task_type: "sketch_regen",
          username: "u",
          project: "demo",
          episode: 1,
          scope: "other",
          status: "running",
          progress: 10,
        },
        {
          task_type: "sketch_regen",
          username: "u",
          project: "demo",
          episode: 1,
          scope: item.taskScope,
          status: "running",
          progress: 65,
          logs: ["queued", "drawing"],
        },
      ],
      item,
    );

    expect(task).toMatchObject({
      scope: item.taskScope,
      progress: 65,
      logs: ["queued", "drawing"],
    });
  });

  it("locks only sketch regen cards that match an active backend group", () => {
    const first = createSketchRegenQueueItem(
      [
        beat({ beat_number: 1, scene_ref: { scene_id: "store" } }),
        beat({ beat_number: 2, scene_ref: { scene_id: "store" } }),
        beat({ beat_number: 3, scene_ref: { scene_id: "street" } }),
      ],
      [1, 2],
      { key: "1x2_4-3_sketch", label: "1×2", capacity: 2 },
    );
    const second = createSketchRegenQueueItem(
      [
        beat({ beat_number: 1, scene_ref: { scene_id: "store" } }),
        beat({ beat_number: 2, scene_ref: { scene_id: "store" } }),
        beat({ beat_number: 3, scene_ref: { scene_id: "street" } }),
      ],
      [3],
      { key: "1x1_2-3_sketch", label: "1×1", capacity: 1 },
    );

    const locked = getLockedSketchRegenItemIds(
      [
        {
          task_type: "sketch_regen",
          username: "u",
          project: "demo",
          episode: 1,
          scope: "1x2_4-3_sketch__abc",
          status: "running",
          progress: 40,
          metadata: {
            mode_key: "1x2_4-3_sketch",
            selected_beat_numbers: [2, 1],
          },
        },
      ],
      [first, second],
    );

    expect([...locked]).toEqual([first.id]);
  });

  it("does not show every sketch mode as spinning when a queued sketch task is running", () => {
    expect(
      shouldShowSketchModeSpinner({
        regenerateRequestPending: false,
        sketchTaskStarted: true,
      }),
    ).toBe(false);
    expect(
      shouldShowSketchModeSpinner({
        regenerateRequestPending: true,
        sketchTaskStarted: false,
      }),
    ).toBe(true);
  });
});
