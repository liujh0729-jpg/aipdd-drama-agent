// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  PETDEX_STATES,
  petdexStateForAction,
} from "@/features/companion/petdex/petdex-pets";

describe("petdexStateForAction", () => {
  it("maps task states to petdex rows: running→Running, success→Waving, failure→Failed", () => {
    expect(petdexStateForAction("typing")).toBe(PETDEX_STATES.running); // 任务进行中, row 7
    expect(petdexStateForAction("flag")).toBe(PETDEX_STATES.waving); // 任务成功, row 3
    expect(petdexStateForAction("repair")).toBe(PETDEX_STATES.failed); // 任务失败, row 5
  });

  it("falls back to idle for all regular/idle actions", () => {
    expect(petdexStateForAction("idle")).toBe(PETDEX_STATES.idle);
    expect(petdexStateForAction("count-stars")).toBe(PETDEX_STATES.idle);
    expect(petdexStateForAction("dragon-boat-paddle")).toBe(PETDEX_STATES.idle);
    expect(petdexStateForAction("peek")).toBe(PETDEX_STATES.idle);
    expect(petdexStateForAction("walk-by")).toBe(PETDEX_STATES.idle);
  });

  it("each state row/frames matches the petdex 9-state layout", () => {
    expect(PETDEX_STATES.idle).toEqual({ row: 0, frames: 6 });
    expect(PETDEX_STATES.runRight).toEqual({ row: 1, frames: 8 });
    expect(PETDEX_STATES.failed).toEqual({ row: 5, frames: 8 });
    expect(PETDEX_STATES.review).toEqual({ row: 8, frames: 6 });
  });
});
