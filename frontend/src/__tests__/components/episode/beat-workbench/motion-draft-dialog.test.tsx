// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider, initReactI18next } from "react-i18next";
import i18next from "i18next";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { FreezoneMotionRenderClip } from "@/api/ops";
import { MotionDraftDialog } from "@/components/episode/beat-workbench/motion-draft-dialog";
import { zhTranslation } from "../../../helpers/i18n-fixtures";
import type { Beat } from "@/types/episode";

// mock 工厂会被提升到模块顶部，共享替身必须走 vi.hoisted 才能在工厂里引用。
const listRenders = vi.hoisted(() => vi.fn());
const submitDraft = vi.hoisted(() => vi.fn());
vi.mock("@/api/ops", () => ({
  listFreezoneMotionRenders: (...args: unknown[]) =>
    listRenders(...(args as [string, number])),
  submitFreezoneMotionDraft: (...args: unknown[]) =>
    submitDraft(...(args as [string, never])),
}));

const awaitCompletion = vi.hoisted(() => vi.fn());
vi.mock("@/api/tasks", () => ({
  awaitTaskCompletion: (...args: unknown[]) =>
    awaitCompletion(...(args as [string, string, unknown])),
}));

const toast = vi.hoisted(() => ({ info: vi.fn(), success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast }));

// 音频素材库只用到「有哪些音频条目」，替身给一份可控的列表。
const libraryState = vi.hoisted(() => ({ items: [] as unknown[] }));
vi.mock("@/lib/queries/freezone", () => ({
  useFreezoneAssetLibrary: () => ({ data: libraryState.items, isLoading: false }),
}));

const aspectState = vi.hoisted(() => ({ orientation: "portrait" as "portrait" | "landscape" }));
vi.mock("@/stores/aspect-ratio-store", () => ({
  useProjectAspectRatio: () => ({
    orientation: aspectState.orientation,
    spec: { renderAspect: "9:16", sketchAspect: "2:3" },
  }),
}));

const i18n = i18next.createInstance();

beforeAll(async () => {
  // 直接吃 public/locales/zh/translation.json：断言钉真实文案，key 打错当场红。
  await i18n.use(initReactI18next).init({
    lng: "zh",
    fallbackLng: "zh",
    interpolation: { escapeValue: false },
    resources: { zh: { translation: zhTranslation } },
  });
});

function makeClip(overrides: Partial<FreezoneMotionRenderClip> = {}): FreezoneMotionRenderClip {
  return {
    jobId: "job_1",
    name: "开场",
    episode: 7,
    beatNumber: 1,
    caseId: "M43",
    durationSeconds: 2.4,
    width: 720,
    height: 1280,
    fps: 30,
    aspectRatio: "9:16",
    beats: [{ t: 0.5, type: "hit" }],
    hasSidecar: true,
    url: "/freezone/_outputs/freezone_motion_render/job_1.mp4",
    ...overrides,
  };
}

const BEATS = [
  {
    beat_number: 1,
    visual_description: "黑底白字开场",
    audio_url: "/static/audio/ep007/beat_01.mp3",
    audio_duration_seconds: 2.2,
  },
  { beat_number: 2, visual_description: "产品旋转", audio_url: "" },
] as unknown as Beat[];

function renderDialog(open = true) {
  return render(
    <I18nextProvider i18n={i18n}>
      <MotionDraftDialog
        open={open}
        onOpenChange={vi.fn()}
        project="p1"
        episode={7}
        beats={BEATS}
      />
    </I18nextProvider>,
  );
}

// 逐镜字幕的源头是分镜里那段口播文本，不是配音文件：只有台词没有音频的镜头也该能上字幕。
const CAPTION_BEATS = [
  { ...BEATS[0], narration_segment: "开场那句词" },
  { ...BEATS[1], narration_segment: "" },
] as unknown as Beat[];

function renderDialogWithBeats(beats: Beat[]) {
  return render(
    <I18nextProvider i18n={i18n}>
      <MotionDraftDialog
        open
        onOpenChange={vi.fn()}
        project="p1"
        episode={7}
        beats={beats}
      />
    </I18nextProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  libraryState.items = [];
  aspectState.orientation = "portrait";
  listRenders.mockResolvedValue([
    // 同一镜重渲过两次：清单按落盘时间升序回来，默认只能选最新那条。
    makeClip({ jobId: "job_old", url: "/static/a/old.mp4", name: "开场（旧）" }),
    makeClip({ jobId: "job_1", url: "/static/a/new.mp4", name: "开场" }),
    makeClip({
      jobId: "job_2",
      url: "/static/a/b2.mp4",
      name: "产品旋转",
      beatNumber: 2,
      durationSeconds: 3.6,
      beats: [],
    }),
  ]);
  submitDraft.mockResolvedValue({
    task_key: "freezone_motion_draft|p1|0|draft_job",
    task_type: "freezone_motion_draft",
    job_id: "draft_job",
  });
  awaitCompletion.mockResolvedValue({
    status: "completed",
    result: {
      draft_dir: "E:/out/freezone/_outputs/freezone_motion_draft/draft_job",
      deployed_dir: "",
      clips: 2,
      duration_seconds: 6,
    },
    error: null,
  });
});

describe("MotionDraftDialog", () => {
  it("lists this episode's renders and picks only the newest take per shot", async () => {
    renderDialog();
    await waitFor(() => expect(listRenders).toHaveBeenCalledWith("p1", 7));

    expect(screen.getByText("开场（旧）")).toBeInTheDocument();
    expect(screen.getByText("产品旋转")).toBeInTheDocument();
    expect(screen.getByText("已选 2 镜 · 约 6.0 秒 · 拍点 1 个")).toBeInTheDocument();
    expect(screen.getByText("画幅 9:16 · 720p · 30fps")).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "生成剪映草稿" }));

    expect(submitDraft).toHaveBeenCalledWith("p1", {
      title: "第7集 动效大片",
      // 旧那条没被选中，所以只带最新两条；镜号顺序由清单排序保证。
      clips: [
        {
          source: "/static/a/new.mp4",
          name: "开场",
          durationS: 2.4,
          beats: [{ t: 0.5, type: "hit" }],
          // 逐镜旁白不传时长：后端按该镜时长对齐，这正是不会压到下一镜的保证。
          narration: { source: "/static/audio/ep007/beat_01.mp3" },
          caption: "",
        },
        {
          source: "/static/a/b2.mp4",
          name: "产品旋转",
          durationS: 3.6,
          beats: [],
          narration: null,
          caption: "",
        },
      ],
      narration: null,
      bgm: null,
      // 字幕默认不给：既没有镜上字幕，也不该凭空落一条文字轨。
      subtitles: [],
      textStyle: null,
      aspectRatio: "9:16",
      resolution: "720p",
      fps: 30,
      deploy: true,
    });
    expect(awaitCompletion).toHaveBeenCalledWith(
      "freezone_motion_draft|p1|0|draft_job",
      "p1",
      { taskType: "freezone_motion_draft" },
    );
  });

  it("tells the user to render first when the episode has no clips yet", async () => {
    listRenders.mockResolvedValue([]);
    renderDialog();

    await waitFor(() => expect(listRenders).toHaveBeenCalled());
    expect(screen.getByText(/本集还没有已渲染的 MG 成片/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成剪映草稿" })).toBeDisabled();
    expect(submitDraft).not.toHaveBeenCalled();
  });

  it("offers a retry instead of an empty table when the listing fails", async () => {
    listRenders.mockRejectedValue(new Error("渲染目录读不到"));
    renderDialog();

    await waitFor(() => expect(screen.getByText("渲染目录读不到")).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "重新读取" }));
    await waitFor(() => expect(listRenders).toHaveBeenCalledTimes(2));
  });

  it("switches whole-film narration onto the request and drops the per-shot ones", async () => {
    libraryState.items = [
      { id: "vo_item", name: "整片配音.mp3", media: "audio", url: "/static/audio/vo.mp3" },
      { id: "bgm_item", name: "背景乐.mp3", media: "audio", url: "/static/audio/bgm.mp3" },
    ];
    renderDialog();
    await waitFor(() => expect(listRenders).toHaveBeenCalled());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "整片" }));
    await user.click(screen.getByRole("button", { name: "生成剪映草稿" }));

    await waitFor(() => expect(submitDraft).toHaveBeenCalled());
    const payload = submitDraft.mock.calls[0][1];
    // 整片旁白与逐镜旁白互斥：切成整片后镜上不许再带旁白，否则剪映打不开。
    expect(payload.narration).toEqual({ source: "/static/audio/vo.mp3" });
    expect(payload.clips[0].narration).toBeNull();
    expect(payload.clips.map((clip: { source: string }) => clip.source)).toEqual([
      "/static/a/new.mp4",
      "/static/a/b2.mp4",
    ]);
  });

  it("ducks the music under narration and keeps it at full gain otherwise", async () => {
    libraryState.items = [
      { id: "bgm_item", name: "背景乐.mp3", media: "audio", url: "/static/audio/bgm.mp3" },
    ];
    renderDialog();
    await waitFor(() => expect(listRenders).toHaveBeenCalled());

    const user = userEvent.setup();
    await user.click(screen.getByRole("combobox", { name: "背景音乐" }));
    await user.click(await screen.findByText("背景乐.mp3"));
    await user.click(screen.getByRole("button", { name: "生成剪映草稿" }));

    await waitFor(() => expect(submitDraft).toHaveBeenCalled());
    // 逐镜旁白是默认档，所以背景音乐要按 0.2 让位。
    expect(submitDraft.mock.calls[0][1].bgm).toEqual({ source: "/static/audio/bgm.mp3", gain: 0.2 });
  });

  it("reports where the draft landed once the task is done", async () => {
    awaitCompletion.mockResolvedValue({
      status: "completed",
      result: {
        draft_dir: "E:/out/draft_job",
        deployed_dir: "C:/Users/me/AppData/Local/JianyingPro/draft_job",
      },
      error: null,
    });
    renderDialog();
    await waitFor(() => expect(listRenders).toHaveBeenCalled());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "生成剪映草稿" }));

    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(toast.success).toHaveBeenCalledWith(
      "剪映草稿已生成：第7集 动效大片",
      expect.objectContaining({
        description:
          "已复制进剪映：C:/Users/me/AppData/Local/JianyingPro/draft_job",
      }),
    );
  });

  it("surfaces the backend reason when the draft task fails", async () => {
    awaitCompletion.mockResolvedValue({
      status: "failed",
      result: null,
      error: "旁白只能选一种给法：整片一条，或逐镜各一条，不能同时给",
    });
    renderDialog();
    await waitFor(() => expect(listRenders).toHaveBeenCalled());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "生成剪映草稿" }));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(toast.error).toHaveBeenCalledWith(
      "旁白只能选一种给法：整片一条，或逐镜各一条，不能同时给",
      expect.anything(),
    );
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("lays per-shot captions from each beat's own script line", async () => {
    renderDialogWithBeats(CAPTION_BEATS);
    await waitFor(() => expect(listRenders).toHaveBeenCalled());

    const user = userEvent.setup();
    const captionGroup = screen.getByRole("group", { name: "字幕" });
    // 逐镜台词一有，字幕开关就多出一档「逐镜」，且与旁白那组同名按钮互不干扰。
    expect(within(captionGroup).getByRole("button", { name: "逐镜" })).toBeInTheDocument();
    await user.click(within(captionGroup).getByRole("button", { name: "逐镜" }));
    await user.click(screen.getByRole("button", { name: "生成剪映草稿" }));

    await waitFor(() => expect(submitDraft).toHaveBeenCalled());
    const payload = submitDraft.mock.calls[0][1];
    expect(payload.clips.map((clip: { caption: string }) => clip.caption)).toEqual([
      "开场那句词",
      "",
    ]);
    // 逐镜字幕与手写互斥：选了逐镜就不可能同时带整片字幕。
    expect(payload.subtitles).toEqual([]);
    expect(payload.textStyle).toEqual({ size: 7, bold: true, yRatio: 0.75 });
  });

  it("collects hand-written caption rows and sends the chosen style", async () => {
    renderDialog();
    await waitFor(() => expect(listRenders).toHaveBeenCalled());

    const user = userEvent.setup();
    const captionGroup = screen.getByRole("group", { name: "字幕" });
    await user.click(within(captionGroup).getByRole("button", { name: "手写" }));
    await user.click(screen.getByRole("button", { name: "添加一条" }));
    await user.type(screen.getByLabelText("字幕文字"), "第一句");
    // 第二行默认接在第一行后面：起点该跟着上一行的末尾走。
    await user.click(screen.getByRole("button", { name: "添加一条" }));
    await user.type(screen.getAllByLabelText("字幕文字")[1], "第二句");
    const starts = screen.getAllByLabelText("起点（秒）");
    expect(starts[1]).toHaveValue("0");
    await user.clear(starts[1]);
    await user.type(starts[1], "2.4");
    const sizeInput = screen.getByLabelText("字幕字号");
    await user.clear(sizeInput);
    await user.type(sizeInput, "12");
    await user.click(screen.getByRole("button", { name: "上" }));
    await user.click(screen.getByRole("button", { name: "生成剪映草稿" }));

    await waitFor(() => expect(submitDraft).toHaveBeenCalled());
    const payload = submitDraft.mock.calls[0][1];
    expect(payload.subtitles).toEqual([
      { text: "第一句", startS: 0, durationS: null },
      { text: "第二句", startS: 2.4, durationS: null },
    ]);
    // 位置「上」= 距顶 20%，字号取输入框里的 12。
    expect(payload.textStyle).toEqual({ size: 12, bold: true, yRatio: 0.2 });
    expect(payload.clips[0].caption).toBe("");
  });

  it("drops a caption row again when the user deletes it", async () => {
    renderDialog();
    await waitFor(() => expect(listRenders).toHaveBeenCalled());

    const user = userEvent.setup();
    const captionGroup = screen.getByRole("group", { name: "字幕" });
    await user.click(within(captionGroup).getByRole("button", { name: "手写" }));
    await user.click(screen.getByRole("button", { name: "添加一条" }));
    await user.type(screen.getByLabelText("字幕文字"), "要删的那句");
    await user.click(screen.getByRole("button", { name: "删除这条字幕" }));
    expect(screen.queryByLabelText("字幕文字")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "生成剪映草稿" }));
    await waitFor(() => expect(submitDraft).toHaveBeenCalled());
    expect(submitDraft.mock.calls[0][1].subtitles).toEqual([]);
  });

  it("keeps a clip without metadata out of the default selection", async () => {
    listRenders.mockResolvedValue([
      makeClip({ jobId: "job_1", url: "/static/a/new.mp4", name: "开场" }),
      makeClip({
        jobId: "job_x",
        url: "/static/a/x.mp4",
        name: "缺元数据的那镜",
        beatNumber: 3,
        durationSeconds: null,
        hasSidecar: false,
        beats: [],
      }),
    ]);
    renderDialog();
    await waitFor(() => expect(listRenders).toHaveBeenCalled());

    expect(screen.getByText("缺元数据")).toBeInTheDocument();
    // 整行是个 <label>，base-ui 会把它解析成这个复选框的 aria-labelledby，
    // 所以可见名是整行文本；按镜名做部分匹配，别再挂一个会被盖掉的 aria-label。
    const brokenRow = screen.getByRole("checkbox", { name: /缺元数据的那镜/ });
    expect(brokenRow).not.toBeChecked();
    expect(screen.getByText("已选 1 镜 · 约 2.4 秒 · 拍点 1 个")).toBeInTheDocument();

    // 缺元数据只是默认不选，不是不能选：用户坚持要交，就该让后端的原话告诉他为什么不行。
    const user = userEvent.setup();
    await user.click(brokenRow);
    expect(screen.getByText("已选 2 镜 · 约 2.4 秒 · 拍点 1 个")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "生成剪映草稿" }));
    await waitFor(() => expect(submitDraft).toHaveBeenCalled());
    expect(submitDraft.mock.calls[0][1].clips[1]).toMatchObject({
      source: "/static/a/x.mp4",
      durationS: null,
    });
  });
});
