// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 分镜脚本页「故事版」面板：要求 → 基础提示词拼接 → submitFreezoneGen
// → 轮询取图 → 存入资产库 storyVersion 类目。外部 API / 查询层全部打桩，不发真请求。
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider, initReactI18next } from "react-i18next";
import i18next from "i18next";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StoryVersionPanel } from "@/components/episode/story-version-panel";

const submitFreezoneGen = vi.fn();
const fetchFreezoneJobResult = vi.fn();
const submitFreezoneAddVideoCharacterLibraryItem = vi.fn();
const awaitTaskCompletion = vi.fn();
const { toastSuccess, toastError } = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("@/api/ops", () => ({
  submitFreezoneGen: (...args: unknown[]) => submitFreezoneGen(...args),
  fetchFreezoneJobResult: (...args: unknown[]) => fetchFreezoneJobResult(...args),
  submitFreezoneAddVideoCharacterLibraryItem: (...args: unknown[]) =>
    submitFreezoneAddVideoCharacterLibraryItem(...args),
}));

vi.mock("@/api/tasks", () => ({
  awaitTaskCompletion: (...args: unknown[]) => awaitTaskCompletion(...args),
  isTaskPollTimeoutError: () => false,
}));

vi.mock("@/features/canvas/application/errorDialog", () => ({
  notifyTaskStillRunning: vi.fn(),
}));

vi.mock("@/lib/api-errors", () => ({
  backendErrorToastMessage: (error: unknown) => String(error),
}));

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

// 提示词库清单：基础提示词模板含 {user_requirement} 占位，供面板按位插入。
const promptListState = vi.hoisted(() => ({
  current: {
    data: {
      data: {
        items: [
          {
            key: "freezone.story_version.system",
            value: "生成一张多格分镜板。\n\n创作要求：{user_requirement}",
            default: "生成一张多格分镜板。",
          },
        ],
      },
    },
  },
}));

vi.mock("@/lib/queries/prompt-library", () => ({
  usePromptLibrary: () => promptListState.current,
}));

const i18n = i18next.createInstance();

beforeEach(async () => {
  vi.clearAllMocks();
  await i18n.use(initReactI18next).init({
    lng: "zh",
    fallbackLng: "zh",
    interpolation: { escapeValue: false },
    resources: {
      zh: {
        translation: {
          episode: {
            storyVersion: {
              title: "故事版",
              description: "生成多格分镜板参考图",
              placeholder: "输入创作要求",
              generate: "生成故事版",
              requirementRequired: "请先输入创作要求",
              promptMissing: "缺少基础提示词",
              success: "故事版已生成并入库",
              preview: "查看预览",
              previewAlt: "故事版预览",
            },
          },
          canvas: {
            assetLibrary: {
              category: { storyVersion: "故事版" },
            },
          },
        },
      },
    },
  });
});

function renderPanel() {
  return render(
    <I18nextProvider i18n={i18n}>
      <StoryVersionPanel project="demo" episode={3} />
    </I18nextProvider>,
  );
}

describe("StoryVersionPanel", () => {
  it("generates a story board and archives it under the storyVersion category", async () => {
    submitFreezoneGen.mockResolvedValue({
      task_type: "freezone_gen",
      task_key: "freezone_gen:job-9",
      job_id: "job-9",
    });
    awaitTaskCompletion.mockResolvedValue(undefined);
    fetchFreezoneJobResult.mockResolvedValue({ url: "/static/story.png" });
    submitFreezoneAddVideoCharacterLibraryItem.mockResolvedValue({ ok: true });

    renderPanel();
    fireEvent.change(screen.getByPlaceholderText("输入创作要求"), {
      target: { value: "雨夜巷口的对峙" },
    });
    fireEvent.click(screen.getByRole("button", { name: "生成故事版" }));

    await waitFor(() => expect(submitFreezoneGen).toHaveBeenCalledTimes(1));
    // 用户要求按 {user_requirement} 占位拼进最终 prompt。
    expect(submitFreezoneGen).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        prompt: expect.stringContaining("雨夜巷口的对峙"),
        aspectRatio: "16:9",
        imageSize: "2K",
      }),
    );
    expect(fetchFreezoneJobResult).toHaveBeenCalledWith(
      "demo",
      "freezone_gen",
      "job-9",
    );
    // 入库携带 storyVersion 类目与图片媒介。
    await waitFor(() =>
      expect(submitFreezoneAddVideoCharacterLibraryItem).toHaveBeenCalledWith(
        "demo",
        expect.objectContaining({
          media: "image",
          imageUrls: ["/static/story.png"],
          category: "storyVersion",
        }),
      ),
    );
    expect(toastSuccess).toHaveBeenCalledWith("故事版已生成并入库");
  });

  it("blocks submit without a requirement and never calls the API", async () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "生成故事版" }));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("请先输入创作要求"));
    expect(submitFreezoneGen).not.toHaveBeenCalled();
  });
});
