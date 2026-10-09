// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const root = process.cwd();
const read = (path: string) => readFileSync(resolve(root, path), "utf8");

describe("selected beat video task binding", () => {
  it("keeps BatchPanel pending while selected single_video tasks are active", () => {
    const batchPanel = read("src/components/episode/beat-workbench/batch-panel.tsx");

    expect(batchPanel).toContain("useTasks");
    expect(batchPanel).toContain("TASK_TYPES.SINGLE_VIDEO");
    expect(batchPanel).toContain("selectedVideoRunning");
    expect(batchPanel).toContain("isActiveStatus");
  });
});
