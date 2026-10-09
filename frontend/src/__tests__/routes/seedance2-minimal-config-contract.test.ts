// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const root = process.cwd();
const read = (path: string) => readFileSync(resolve(root, path), "utf8");

describe("Seedance2 minimal config alignment", () => {
  it("keeps seedance2_config_json on Beat and BeatUpdate", () => {
    const episodeTypes = read("src/types/episode.ts");
    const scriptTypes = read("src/types/script.ts");

    expect(episodeTypes).toContain("seedance2_config_json?: string");
    expect(scriptTypes).toContain("seedance2_config_json?: string");
  });

  it("shows and saves minimal Seedance2 config from the video pane", () => {
    const videoPane = read("src/components/episode/beat-workbench/video-pane.tsx");

    expect(videoPane).toContain("seedance2_config_json");
    expect(videoPane).toContain("final_prompt");
    expect(videoPane).toContain("useUpdateBeat");
    expect(videoPane).toContain("seedance2Prompt");
  });
});
