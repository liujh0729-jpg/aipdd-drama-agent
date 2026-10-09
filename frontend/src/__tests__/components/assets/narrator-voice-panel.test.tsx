// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider, initReactI18next } from "react-i18next";
import i18next from "i18next";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { NarratorVoicePanel } from "@/components/assets/narrator-voice-panel";

const i18n = i18next.createInstance();

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "zh",
    fallbackLng: "zh",
    interpolation: { escapeValue: false },
    resources: {
      zh: {
        translation: {
          common: { cancel: "取消", error: "错误" },
          episode: {
            workbench: {
              video: {
                seedance2Ready: "已配置",
                narratorVoice: "解说声线",
                narratorVoiceMissing: "声线缺失",
                narratorVoiceMissingDetail: "第三人称项目解说声线未配置",
                narratorVoiceUpload: "上传",
                narratorVoiceRecord: "录音",
                narratorVoiceProjectAudio: "项目音频",
                narratorVoiceTrim: "裁剪",
                narratorVoiceDelete: "删除",
                narratorVoiceTrimTitle: "裁剪解说声线",
                narratorVoiceTrimHint: "Seedance 2.0 建议参考声线保留清晰单人声 3-5 秒。",
                narratorVoiceTrimStart: "起始秒",
                narratorVoiceTrimDuration: "保留秒数",
                narratorVoiceTrimApply: "裁剪到 3-5 秒",
                narratorVoiceTrimInvalid: "裁剪参数无效",
                narratorVoiceTrimmed: "解说声线已裁剪",
              },
            },
          },
        },
      },
    },
  });
});

const mutateTrim = vi.hoisted(() => vi.fn());
let mockHasVoice = false;

vi.mock("@/lib/queries/video", () => ({
  useNarratorVoiceStatus: () => ({
    data: {
      ok: true,
      data: {
        narration_style: "first_person",
        source: "protagonist_identity",
        reference_path: mockHasVoice ? "assets/narrator/voice.mp3" : "",
        reference_url: mockHasVoice ? "/static/demo/assets/narrator/voice.mp3" : "",
        heading: "第一人称解说声线",
        detail: "",
        explanation: "第一人称解说使用主角声线。",
        is_first_person: true,
      },
    },
    isLoading: false,
    isError: false,
  }),
  useNarratorVoiceSources: () => ({ data: { ok: true, data: { options: [] } } }),
  useUploadNarratorVoice: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRecordNarratorVoice: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCopyProjectNarratorVoice: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useTrimNarratorVoice: () => ({ mutateAsync: mutateTrim, isPending: false }),
  useDeleteNarratorVoice: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

function renderPanel(allowFirstPersonProjectVoice = false) {
  render(
    <I18nextProvider i18n={i18n}>
      <NarratorVoicePanel
        project="demo"
        allowFirstPersonProjectVoice={allowFirstPersonProjectVoice}
      />
    </I18nextProvider>,
  );
}

describe("NarratorVoicePanel", () => {
  beforeEach(() => {
    mockHasVoice = false;
    mutateTrim.mockReset();
    mutateTrim.mockResolvedValue({
      ok: true,
      data: {
        reference_path: "assets/narrator/voice.mp3",
      },
    });
  });

  it("hides project narrator upload actions for first-person projects by default", () => {
    renderPanel(false);

    expect(screen.queryByRole("button", { name: "上传" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "录音" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "项目音频" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "裁剪" })).not.toBeInTheDocument();
  });

  it("allows project narrator upload actions when first-person project voice is explicitly enabled", () => {
    renderPanel(true);

    expect(screen.getByRole("button", { name: "上传" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "录音" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "项目音频" })).toBeInTheDocument();
  });

  it("trims configured narrator voice from the assets voice panel", async () => {
    mockHasVoice = true;
    const user = userEvent.setup();
    renderPanel(true);

    await user.click(screen.getByRole("button", { name: "裁剪" }));

    expect(screen.getByRole("dialog", { name: "裁剪解说声线" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "裁剪到 3-5 秒" }));

    expect(mutateTrim).toHaveBeenCalledWith({
      startSeconds: 0,
      durationSeconds: 4,
    });
  });
});
