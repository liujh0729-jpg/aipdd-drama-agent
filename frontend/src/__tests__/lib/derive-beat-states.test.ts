// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect } from "vitest";
import { deriveBeatStates } from "@/lib/derive-beat-states";
import type { Beat } from "@/types/episode";
import type { Task } from "@/types/task";

function makeBeat(overrides: Partial<Beat>): Beat {
  return {
    beat_number: 1,
    narration_segment: "test",
    visual_description: "test",
    ...overrides,
  };
}

function makeTask(overrides: Partial<Task>): Task {
  return {
    task_type: "sketch_generation",
    project: "p",
    episode: 1,
    status: "running",
    created_at: "2026-04-14T00:00:00Z",
    ...overrides,
  } as Task;
}

describe("deriveBeatStates", () => {
  it("marks sketch ready when the canonical sketch URL is present", () => {
    const result = deriveBeatStates(
      [makeBeat({ beat_number: 1, sketch_url: "/sketch.png", frame_url: null })],
      [],
    );
    expect(result[1].sketch).toBe("ready");
  });

  it("does not mark sketch ready from a render frame URL", () => {
    const result = deriveBeatStates(
      [makeBeat({ beat_number: 1, sketch_url: null, frame_url: "/frame.png" })],
      [],
    );
    expect(result[1].sketch).toBe("missing");
  });

  it("marks missing when no asset and no task", () => {
    const result = deriveBeatStates(
      [makeBeat({ beat_number: 1, frame_url: null })],
      [],
    );
    expect(result[1].sketch).toBe("missing");
  });

  it("marks generating when a scoped task matches beat_num", () => {
    const result = deriveBeatStates(
      [makeBeat({ beat_number: 5, video_url: null })],
      [makeTask({ task_type: "single_video", beat_num: 5, status: "running" })],
    );
    expect(result[5].video).toBe("generating");
  });

  it("does NOT mark generating when scoped task targets a different beat", () => {
    const result = deriveBeatStates(
      [makeBeat({ beat_number: 1, video_url: null })],
      [makeTask({ task_type: "single_video", beat_num: 999, status: "running" })],
    );
    expect(result[1].video).toBe("missing");
  });

  it("marks ALL missing beats as generating when a batch task is active", () => {
    const result = deriveBeatStates(
      [
        makeBeat({ beat_number: 1, frame_url: null }),
        makeBeat({ beat_number: 2, sketch_url: "/sketch.png", frame_url: null }),
        makeBeat({ beat_number: 3, frame_url: null }),
      ],
      [makeTask({ task_type: "batch_sketch", status: "running" })],
    );
    expect(result[1].sketch).toBe("generating");
    expect(result[2].sketch).toBe("ready");
    expect(result[3].sketch).toBe("generating");
  });

  it("attributes failure to a specific beat when task.beat_num matches", () => {
    const result = deriveBeatStates(
      [makeBeat({ beat_number: 7, video_url: null })],
      [makeTask({ task_type: "single_video", beat_num: 7, status: "failed" })],
    );
    expect(result[7].video).toBe("failed");
  });

  it("does NOT inherit batch failures to individual beats", () => {
    const result = deriveBeatStates(
      [
        makeBeat({ beat_number: 1, frame_url: null }),
        makeBeat({ beat_number: 2, frame_url: null }),
      ],
      [makeTask({ task_type: "batch_sketch", status: "failed" })],
    );
    expect(result[1].sketch).toBe("missing");
    expect(result[2].sketch).toBe("missing");
  });

  it("evaluates ready before failed — existing asset never masked by old failure", () => {
    const result = deriveBeatStates(
      [makeBeat({ beat_number: 7, video_url: "/v.mp4" })],
      [makeTask({ task_type: "single_video", beat_num: 7, status: "failed" })],
    );
    expect(result[7].video).toBe("ready");
  });

  it("script: ready requires non-empty visual_description (not narration)", () => {
    const resultEmpty = deriveBeatStates(
      [makeBeat({ beat_number: 1, visual_description: "" })],
      [],
    );
    expect(resultEmpty[1].script).toBe("missing");

    const resultWhitespace = deriveBeatStates(
      [makeBeat({ beat_number: 2, visual_description: "   " })],
      [],
    );
    expect(resultWhitespace[2].script).toBe("missing");

    // Silent shot: has a visual description but no spoken line → still ready.
    const resultSilent = deriveBeatStates(
      [
        makeBeat({
          beat_number: 3,
          narration_segment: "",
          audio_type: "silence",
          visual_description: "海棠花开满墙头",
        }),
      ],
      [],
    );
    expect(resultSilent[3].script).toBe("ready");
  });

  it("ignores completed tasks (not active)", () => {
    const result = deriveBeatStates(
      [makeBeat({ beat_number: 1, frame_url: null })],
      [makeTask({ task_type: "batch_sketch", status: "completed" })],
    );
    expect(result[1].sketch).toBe("missing");
  });

  it("marks only the targeted beat as generating for sketch_regen", () => {
    const result = deriveBeatStates(
      [
        makeBeat({ beat_number: 1, frame_url: null }),
        makeBeat({ beat_number: 2, frame_url: null }),
      ],
      [makeTask({ task_type: "sketch_regen", beat_num: 2, status: "running" })],
    );
    expect(result[1].sketch).toBe("missing");
    expect(result[2].sketch).toBe("generating");
  });

  it("surfaces selected_regen in video stage", () => {
    const result = deriveBeatStates(
      [makeBeat({ beat_number: 3, video_url: null })],
      [makeTask({ task_type: "selected_regen", beat_num: 3, status: "running" })],
    );
    expect(result[3].video).toBe("generating");
  });

  it("handles many beats and tasks without regression", () => {
    const beats = Array.from({ length: 50 }, (_, i) =>
      makeBeat({ beat_number: i + 1, frame_url: null, video_url: null }),
    );
    const tasks = Array.from({ length: 20 }, (_, i) =>
      makeTask({ task_type: "sketch_generation", status: i < 5 ? "running" : "completed" }),
    );
    const result = deriveBeatStates(beats, tasks);
    expect(result[1].sketch).toBe("generating");
    expect(result[50].sketch).toBe("generating");
    expect(result[1].script).toBe("ready");
  });
});
