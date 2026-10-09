// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import i18next from "i18next";
import { I18nextProvider, initReactI18next } from "react-i18next";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { NarratorVoicePanel as AssetsNarratorVoicePanel } from "@/components/assets/narrator-voice-panel";
import { NarratorVoicePanel as WorkbenchNarratorVoicePanel } from "@/components/episode/beat-workbench/narrator-voice-panel";

const runtimeState = vi.hoisted(() => ({ isCeRuntime: true }));
const toastErrorMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/runtime-config", () => ({
  isCeRuntime: () => runtimeState.isCeRuntime,
}));

vi.mock("sonner", () => ({
  toast: {
    error: toastErrorMock,
    success: vi.fn(),
  },
}));

vi.mock("@/lib/queries/video", () => ({
  useNarratorVoiceStatus: () => ({
    data: {
      ok: true,
      data: {
        narration_style: "third_person",
        source: "project_narrator",
        reference_path: "assets/narrator/voice.mp3",
        reference_url: "/static/demo/assets/narrator/voice.mp3",
        heading: "Narrator voice",
        detail: "",
        explanation: "Project narrator voice.",
        is_first_person: false,
      },
    },
    isLoading: false,
    isError: false,
  }),
  useNarratorVoiceSources: () => ({ data: { ok: true, data: { options: [] } } }),
  useUploadNarratorVoice: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRecordNarratorVoice: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCopyProjectNarratorVoice: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useTrimNarratorVoice: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteNarratorVoice: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

const i18n = i18next.createInstance();

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "en",
    fallbackLng: "en",
    interpolation: { escapeValue: false },
    resources: {
      en: {
        translation: {
          common: {
            cancel: "Cancel",
            error: "Error",
            stop: "Stop",
          },
          episode: {
            workbench: {
              video: {
                seedance2Ready: "Ready",
                narratorVoice: "Narrator voice",
                narratorVoiceMissing: "Missing",
                narratorVoiceUpload: "Upload",
                narratorVoiceRecord: "Record",
                narratorVoiceProjectAudio: "Project audio",
                narratorVoiceTrim: "Trim",
                narratorVoiceDelete: "Delete",
                narratorVoiceRecordTitle: "Record narrator voice",
                narratorVoiceRecordHint: "Record a voice sample.",
                narratorVoiceRecordReady: "Ready to record",
                narratorVoiceRecordUnavailable: "Recording unavailable",
                narratorVoiceRequestingMic: "Requesting microphone",
                narratorVoiceRecorded: "Recorded {{seconds}}s",
                narratorVoiceRecordFailed: "Recording failed",
                narratorVoiceRecording: "Recording",
                narratorVoiceSaveRecording: "Save recording",
                narratorVoiceProjectAudioTitle: "Project audio",
                narratorVoiceSourcesLoading: "Loading sources",
                narratorVoiceNoSources: "No sources",
                narratorVoiceUse: "Use",
                narratorVoiceTrimTitle: "Trim narrator voice",
                narratorVoiceTrimHint: "Keep a short voice sample.",
                narratorVoiceTrimStart: "Start",
                narratorVoiceTrimDuration: "Duration",
                narratorVoiceTrimApply: "Apply trim",
                narratorVoiceTrimInvalid: "Invalid trim",
              },
            },
          },
        },
      },
    },
  });
});

function classNameContains(container: HTMLElement, token: string) {
  return Array.from(container.querySelectorAll("*")).some((node) =>
    String(node.getAttribute("class") ?? "").includes(token),
  );
}

describe("NarratorVoicePanel CE generation credit gating", () => {
  beforeEach(() => {
    runtimeState.isCeRuntime = true;
    toastErrorMock.mockClear();
  });

  it("keeps both narrator voice entry points free of credit UI, credit styling, and credit errors", () => {
    const { container } = render(
      <I18nextProvider i18n={i18n}>
        <AssetsNarratorVoicePanel project="demo" />
        <WorkbenchNarratorVoicePanel project="demo" />
      </I18nextProvider>,
    );

    expect(screen.getAllByText("Narrator voice")).not.toHaveLength(0);
    expect(screen.getAllByRole("button", { name: "Upload" })).not.toHaveLength(0);

    expect(screen.queryByText(/credits?/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/积分|额度/)).not.toBeInTheDocument();
    expect(classNameContains(container, "#007A87")).toBe(false);
    expect(toastErrorMock).not.toHaveBeenCalledWith(
      expect.stringMatching(/积分不足|credit|insufficient/i),
    );
  });
});
