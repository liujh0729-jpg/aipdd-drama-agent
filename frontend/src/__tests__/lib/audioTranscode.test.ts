// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect } from "vitest";
import {
  AUDIO_DOWNLOAD_FORMATS,
  canProduceFormat,
  getAudioExtFromUrl,
} from "@/lib/audioTranscode";

describe("getAudioExtFromUrl", () => {
  it("parses a plain path extension", () => {
    expect(getAudioExtFromUrl("/static/u/p/freezone/abc.m4a")).toBe("m4a");
  });

  it("lower-cases the extension", () => {
    expect(getAudioExtFromUrl("/x/Y/SONG.MP3")).toBe("mp3");
  });

  it("ignores query string and hash", () => {
    expect(getAudioExtFromUrl("/a/b.wav?v=2#t=1")).toBe("wav");
  });

  it("returns empty string when there is no extension", () => {
    expect(getAudioExtFromUrl("/a/b/xxx_背景音")).toBe("");
  });
});

describe("canProduceFormat", () => {
  it("always allows mp3 and wav regardless of source", () => {
    for (const src of ["m4a", "mp3", "wav", "aac", ""]) {
      expect(canProduceFormat("mp3", src)).toBe(true);
      expect(canProduceFormat("wav", src)).toBe(true);
    }
  });

  it("allows m4a only when the source is an AAC/MP4 container", () => {
    expect(canProduceFormat("m4a", "m4a")).toBe(true);
    expect(canProduceFormat("m4a", "aac")).toBe(true);
    expect(canProduceFormat("m4a", "mp4")).toBe(true);
  });

  it("blocks m4a when the source is not AAC/MP4", () => {
    expect(canProduceFormat("m4a", "mp3")).toBe(false);
    expect(canProduceFormat("m4a", "wav")).toBe(false);
    expect(canProduceFormat("m4a", "")).toBe(false);
  });
});

describe("AUDIO_DOWNLOAD_FORMATS", () => {
  it("exposes mp3/m4a/wav in order", () => {
    expect([...AUDIO_DOWNLOAD_FORMATS]).toEqual(["mp3", "m4a", "wav"]);
  });
});
