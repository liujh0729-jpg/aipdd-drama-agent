// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiCall } from "@/api/client";
import {
  fetchFreezoneAudioModels,
  fetchFreezoneTextModels,
  submitFreezoneAudioMusic,
  submitFreezoneAudioSpeech,
  submitFreezoneStoryScript,
} from "@/api/ops";

// [aipdd fork delta] 画布音频/脚本节点按类型选模型的契约回归：
// 1) 选了模型 → 请求体带 model；未选/选「默认引擎」→ 省略 key（不能发空串）；
// 2) audio/text 模型列表端点的防御式解析。
vi.mock("@/api/client", () => ({
  apiCall: vi.fn(),
  apiClient: {},
}));

describe("canvas audio speech/music model plumbing", () => {
  beforeEach(() => {
    vi.mocked(apiCall).mockReset();
    vi.mocked(apiCall).mockResolvedValue({ job_id: "job-1" });
  });

  it("sends the selected audio model on speech submit", async () => {
    await submitFreezoneAudioSpeech("p1", {
      text: "你好",
      model: "newapi_tts-1",
    });
    const [, options] = vi.mocked(apiCall).mock.calls[0];
    expect((options as { json: Record<string, unknown> }).json.model).toBe(
      "newapi_tts-1",
    );
  });

  it("omits the speech model key when no model is selected", async () => {
    await submitFreezoneAudioSpeech("p1", { text: "你好", model: "" });
    const [, options] = vi.mocked(apiCall).mock.calls[0];
    expect(
      "model" in (options as { json: Record<string, unknown> }).json,
    ).toBe(false);
  });

  it("sends the selected music model and omits it when unset", async () => {
    await submitFreezoneAudioMusic("p1", {
      prompt: "rain music",
      model: "Acme-MU-1",
    });
    const selected = vi.mocked(apiCall).mock.calls[0][1] as {
      json: Record<string, unknown>;
    };
    expect(selected.json.model).toBe("Acme-MU-1");

    await submitFreezoneAudioMusic("p1", { prompt: "rain music" });
    const fallback = vi.mocked(apiCall).mock.calls[1][1] as {
      json: Record<string, unknown>;
    };
    expect("model" in fallback.json).toBe(false);
  });
});

describe("story-script model plumbing", () => {
  beforeEach(() => {
    vi.mocked(apiCall).mockReset();
    vi.mocked(apiCall).mockResolvedValue({ job_id: "job-1" });
  });

  it("forwards the user-picked text model to story-script", async () => {
    await submitFreezoneStoryScript("p1", {
      sourceText: "剧本正文",
      model: "DC-deepseek-v3",
    });
    const [, options] = vi.mocked(apiCall).mock.calls[0];
    expect((options as { json: Record<string, unknown> }).json.model).toBe(
      "DC-deepseek-v3",
    );
  });

  it("omits the model key when nothing was picked", async () => {
    await submitFreezoneStoryScript("p1", { sourceText: "剧本正文", model: "" });
    const [, options] = vi.mocked(apiCall).mock.calls[0];
    expect(
      "model" in (options as { json: Record<string, unknown> }).json,
    ).toBe(false);
  });
});

describe("audio/text model list endpoints", () => {
  beforeEach(() => {
    vi.mocked(apiCall).mockReset();
  });

  it("parses the audio catalog entries from { ok, data }", async () => {
    vi.mocked(apiCall).mockResolvedValue({
      ok: true,
      data: [
        {
          catalogId: "tts-1",
          id: "tts-1",
          providerId: "newapi",
          apiModel: "tts-1",
          label: "TTS One",
        },
      ],
    });
    const models = await fetchFreezoneAudioModels("p1");
    expect(apiCall).toHaveBeenCalledWith(
      "projects/p1/freezone/audio/models",
    );
    expect(models).toHaveLength(1);
    expect(models[0]).toMatchObject({ id: "tts-1", apiModel: "tts-1", label: "TTS One" });
  });

  it("treats an authoritative empty audio catalog as a success, not a fallback", async () => {
    vi.mocked(apiCall).mockResolvedValue({ ok: true, data: [] });
    expect(await fetchFreezoneAudioModels("p1")).toEqual([]);
  });

  it("coerces text-model entries and tolerates raw strings", async () => {
    vi.mocked(apiCall).mockResolvedValue({
      ok: true,
      data: [
        { id: "DC-default", providerId: "newapi", apiModel: "DC-default", label: "DC-default" },
        "DC-extra",
      ],
    });
    const models = await fetchFreezoneTextModels();
    expect(apiCall).toHaveBeenCalledWith("model-gateway/text-models");
    expect(models.map((entry) => entry.id)).toEqual(["DC-default", "DC-extra"]);
    expect(models[1]).toMatchObject({ apiModel: "DC-extra", label: "DC-extra" });
  });
});
