// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const routeSource = readFileSync(
  "src/routes/_app/projects.$project/characters.lazy.tsx",
  "utf-8",
);
const characterTypes = readFileSync("src/types/character.ts", "utf-8");
const characterQueries = readFileSync("src/lib/queries/characters.ts", "utf-8");

describe("character workbench IndexTTS2 cutover", () => {
  it("does not expose legacy Fish voice controls in the character workbench", () => {
    expect(routeSource).not.toContain("VOICE_TYPE_OPTIONS");
    expect(routeSource).not.toContain("characters.voice.");
    expect(routeSource).not.toContain("voiceOverride");
    expect(routeSource).not.toContain("fish-audio-voice-id");
    expect(routeSource).not.toContain("fish_voice_id");
  });

  it("uses IndexTTS2 voice sample fields in frontend character types", () => {
    expect(characterTypes).not.toContain("fish_voice_id");
    expect(characterTypes).toContain("reference_audio_path");
    expect(characterTypes).toContain("reference_audio_url");
    expect(characterTypes).toContain("reference_audio_sha256");
    expect(characterTypes).toContain("reference_audio_updated_at");
    expect(characterTypes).toContain("voice_samples_by_age_group");
  });

  it("does not allow character query mutations to write fish_voice_id", () => {
    expect(characterQueries).not.toContain("fish_voice_id");
  });
});
