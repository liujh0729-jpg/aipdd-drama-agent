// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect } from "vitest";
import { sampleTask } from "@/__mocks__/msw/handlers/tasks";
import { matchByType, matchByEpisode, matchBeat } from "@/task-center/matchers";

describe("matchByType", () => {
  const m = matchByType("sketch_regen");
  it("matches task_type", () => expect(m(sampleTask({ task_type: "sketch_regen" }))).toBe(true));
  it("rejects other types", () => expect(m(sampleTask({ task_type: "script_writer" }))).toBe(false));
});

describe("matchByEpisode", () => {
  const m = matchByEpisode("demo", 3);
  it("matches project + episode", () =>
    expect(m(sampleTask({ project: "demo", episode: 3 }))).toBe(true));
  it("rejects wrong project", () =>
    expect(m(sampleTask({ project: "other", episode: 3 }))).toBe(false));
  it("rejects wrong episode", () =>
    expect(m(sampleTask({ project: "demo", episode: 4 }))).toBe(false));
});

describe("matchBeat", () => {
  const m = matchBeat("demo", 3, 7);
  it("matches project + episode + beat", () =>
    expect(m(sampleTask({ project: "demo", episode: 3, beat_num: 7 }))).toBe(true));
  it("rejects wrong beat", () =>
    expect(m(sampleTask({ project: "demo", episode: 3, beat_num: 8 }))).toBe(false));
  it("rejects null beat", () =>
    expect(m(sampleTask({ project: "demo", episode: 3, beat_num: null }))).toBe(false));
});
