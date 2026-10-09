// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 分类真源镜像契约：与后端 tests/test_media_model_discovery_catalog.py
// 的 test_guess_model_kind_matches_settings_dialog_media_classes /
// test_is_vision_capable_matches_settings_dialog 使用同一组用例。
// 任一侧分类口径漂移，都会让另一侧的用例失败。
import { describe, expect, it } from "vitest";

import {
  guessMediaType,
  isVisionCapable,
  VISION_CAPABLE_RE,
} from "@/components/settings/model-classification";

describe("guessMediaType（八分类真源）", () => {
  it("与后端 guess_model_kind 同口径（镜像用例）", () => {
    expect(guessMediaType("DC-freezone-vision-LLM")).toBe("vision");
    expect(guessMediaType("ap-qwen-vl-max")).toBe("vision");
    expect(guessMediaType("ap-doubao-seed-2.1-turbo")).toBe("text");
    expect(guessMediaType("DC-seedance2-prompt-composer-LLM")).toBe("text");
    expect(guessMediaType("ap-jimeng-image-4.0")).toBe("image");
    expect(guessMediaType("ap-minimax-h3")).toBe("video");
    expect(guessMediaType("ap-indextts-2")).toBe("audio");
    expect(guessMediaType("text-embedding-3")).toBe("embedding");
    expect(guessMediaType("brand-new-unknown")).toBe("other");
    // 渠道新增模型：无需登记清单，按名字启发式即可归类。
    expect(guessMediaType("ap-flux-9-turbo")).toBe("image");
    // [aipdd fork delta req46] 音乐独立成类，且不得抢走 TTS 名。
    expect(guessMediaType("music-3.0")).toBe("music");
    expect(guessMediaType("ap-music-cover")).toBe("music");
    expect(guessMediaType("speech-2.8-hd")).toBe("audio");
    // [aipdd fork delta req48] 三家官方渠道清单里的名字：Lyria 是音乐、whisper 系
    // 是转写（过去一个都不命中 audio）、omni-moderation 是内容审核工具（不进任何
    // TAB），Gemini / OpenAI 的文本与生图/视频名按官方命名命中。
    expect(guessMediaType("lyria-realtime-preview")).toBe("music");
    expect(guessMediaType("whisper-1")).toBe("audio");
    expect(guessMediaType("omni-moderation-latest")).toBe("other");
    expect(guessMediaType("gemini-2.5-flash-image-preview")).toBe("image");
    expect(guessMediaType("veo-3.0-generate-001")).toBe("video");
    expect(guessMediaType("claude-sonnet-4-5")).toBe("text");
    expect(guessMediaType("gpt-5-mini")).toBe("text");
    expect(guessMediaType("text-embedding-3-large")).toBe("embedding");
  });
});

describe("VISION_CAPABLE_RE（视觉候选资格）", () => {
  it("与后端 is_vision_capable 同口径（镜像用例）", () => {
    expect(VISION_CAPABLE_RE.test("ap-doubao-seed-2.1-turbo")).toBe(true);
    expect(VISION_CAPABLE_RE.test("ap-doubao-seedream-4.0")).toBe(false);
    expect(VISION_CAPABLE_RE.test("ap-qwen-vl-max")).toBe(true);
    expect(VISION_CAPABLE_RE.test("plain-text")).toBe(false);
  });
});

// [aipdd fork delta req48] 资格判定要走函数而非裸正则：审核工具名里的 omni 会命中
// 上面的视觉资格正则，点下去只会拿到一个不接图审核的接口。与后端
// media_model_discovery.is_vision_capable 同口径。
describe("isVisionCapable（工具模型护栏）", () => {
  it("裸正则命中的工具模型被护栏挡掉", () => {
    expect(VISION_CAPABLE_RE.test("omni-moderation-latest")).toBe(true);
    expect(isVisionCapable("omni-moderation-latest")).toBe(false);
    expect(isVisionCapable("ap-doubao-seed-2.1-turbo")).toBe(true);
    expect(isVisionCapable("gpt-5-mini")).toBe(false);
    expect(isVisionCapable("")).toBe(false);
  });
});