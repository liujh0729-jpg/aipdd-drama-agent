// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/ops", () => ({
  fetchFreezoneAudioReferences: vi.fn(async () => ({ available: [] })),
  fetchFreezoneVideoCharacterLibrary: vi.fn(async () => []),
  uploadFreezoneImage: vi.fn(async () => ({ url: "/static/x", filename: "x", size: 1 })),
  submitFreezoneAudioSpeech: vi.fn(),
  submitFreezoneEdit: vi.fn(),
  submitFreezoneVideoClone: vi.fn(),
  submitFreezoneVideoErase: vi.fn(),
  submitFreezoneVideoEdit: vi.fn(),
  submitFreezoneVideoIdea: vi.fn(),
  submitFreezoneVideoMix: vi.fn(),
  // [aipdd fork delta] 白板动画视频：WhiteboardAnimationPanel 直接 import，mock 须提供。
  submitFreezoneWhiteboardAnimation: vi.fn(),
  // [aipdd fork delta] 讲解类视频台词草稿（两段式第一段）：两个面板都会用到。
  submitFreezoneExplainerScript: vi.fn(),
  // [aipdd fork delta] 科普讲解视频：ExplainerVideoPanel 直接 import，mock 须提供。
  submitFreezoneExplainerVideo: vi.fn(),
  submitFreezoneVideoOmniGen: vi.fn(),
  submitFreezoneMediaCopywrite: vi.fn(),
  fetchFreezoneMediaCopywriteModels: vi.fn(async () => []),
  submitFreezoneVideoScriptReverse: vi.fn(),
  fetchFreezoneVisionModels: vi.fn(async () => []),
  // [aipdd fork delta] 人声复刻→替换人声：VoiceClonePanel 直接 import，mock 须提供。
  submitFreezoneVoiceReplace: vi.fn(),
  // [aipdd fork delta] 视频配音：VideoDubbingPanel 直接 import，mock 须提供。
  submitFreezoneVideoDubbing: vi.fn(),
  // [aipdd fork delta req46] 音乐创作：MusicCreatePanel 直接 import，mock 须提供。
  submitFreezoneMusicCreate: vi.fn(),
  // [aipdd fork delta] 公众号文章：ArticleVideoPanel 直接 import 三个函数，mock 须提供。
  submitFreezoneArticleWrite: vi.fn(async () => ({
    task_type: "freezone_article_write",
    job_id: "article-job-1",
    task_key: "article-key",
  })),
  submitFreezoneArticlePublish: vi.fn(async () => ({
    status: "needs_config",
    published_ok: false,
    message: "未配置公众号凭证",
    guidance: "请到公众号后台获取 AppID / Secret",
  })),
  fetchFreezoneArticleResult: vi.fn(async () => ({
    title: "示例文章",
    style: "viewpoint",
    theme: "moyu-green",
    model: "test",
    markdown: "# 示例文章\n\n正文",
    html_url: "/static/freezone/article.html",
    span_leaf_count: 3,
    validation_warnings: [],
  })),
  // [aipdd fork delta Phase 2B] 渠道短视频：ChannelShortVideoPanel 直接 import，mock 须提供。
  submitFreezoneChannelShortVideo: vi.fn(async () => ({
    task_type: "freezone_channel_short_video",
    job_id: "channel-job-1",
    task_key: "channel-key",
  })),
  // [aipdd fork delta] 口播智能剪辑：NarrationSmartEditPanel 直接 import，mock 须提供。
  submitFreezoneNarrationSmartEdit: vi.fn(async () => ({
    task_type: "freezone_narration_smart_edit",
    job_id: "narration-job-1",
    task_key: "narration-key",
  })),
  // [aipdd fork delta] 短视频口播稿：OralScriptPanel 直接 import，mock 须提供。
  submitFreezoneOralScript: vi.fn(async () => ({
    task_type: "freezone_oral_script",
    job_id: "oral-job-1",
    task_key: "oral-key",
  })),
  // 两步翻唱第一步是同步接口：桩固定给一个特征 id + 抽出来的词。
  submitFreezoneMusicCoverPreprocess: vi.fn(async () => ({
    cover_feature_id: "cf-1",
    formatted_lyrics: "[Verse]\n抽出来的词",
  })),
  // [aipdd fork delta] 动效大片：面板 + 复用的案例图册 + 复用的剪映交付弹窗都从
  // @/api/ops 直连，mock 必须给全这些出口（画幅集合同一处口径，测试里照抄后端三种）。
  MOTION_ASPECT_RATIOS: new Set(["16:9", "9:16", "1:1"]),
  listFreezoneMotionCases: vi.fn(async () => ({
    assetBase: "",
    version: "test",
    categories: ["产品宣传片"],
    methods: [],
    modelHint: "",
    renderContract: "渲染契约",
    source: "",
    cases: [],
  })),
  fetchFreezoneMotionPrompt: vi.fn(async () => "分镜规格 + 渲染契约"),
  // 画风名录与案例名录共用这一个图册组件，Hooks 也各拉一份：mock 少给这一个出口，
  // 面板一挂载就会在控制台报 fetch failed（走 EMPTY 兜底，断言不受影响，但噪声会掩盖真故障）。
  listFreezoneMotionStyles: vi.fn(async () => ({
    assetBase: "",
    version: "test",
    categories: [],
    styles: [],
  })),
  fetchFreezoneMotionStylePrompt: vi.fn(async () => "画风规格 + 渲染契约"),
  submitFreezoneMotionRender: vi.fn(async () => ({
    task_type: "freezone_motion_render",
    job_id: "motion-job-1",
    task_key: "motion-render-key",
  })),
  listFreezoneMotionRenders: vi.fn(async () => []),
  submitFreezoneMotionStills: vi.fn(async () => ({
    task_type: "freezone_motion_stills",
    job_id: "motion-stills-1",
    task_key: "motion-stills-key",
  })),
  submitFreezoneMotionDraft: vi.fn(async () => ({
    task_type: "freezone_motion_draft",
    job_id: "motion-draft-1",
    task_key: "motion-draft-key",
  })),
}));

// 静帧预检的结果区从任务列表读「最近一次完成的 freezone_motion_stills」，
// 默认空列表，用例里按需注入已完成任务（其余面板同样只读 data）。
const tasksMock = vi.hoisted(() => ({
  current: [] as Array<Record<string, unknown>>,
}));

vi.mock("@/lib/queries/tasks", () => ({
  useTasks: () => ({ data: { ok: true, data: tasksMock.current } }),
}));

// 视频/音频目录默认空，可按测试注入具体模型（如声明 video_edit 的 seedance-2.0-mini、
// 音频默认模型 ap-indextts-2）；ModelPicker 会同时调用三类目录 hook（非当前 kind
// 传 null 关闭），三个都要 mock。
const videoModelsMock = vi.hoisted(() => ({
  current: [] as Array<Record<string, unknown>>,
}));
const audioModelsMock = vi.hoisted(() => ({
  current: [] as Array<Record<string, unknown>>,
}));
const imageModelsMock = vi.hoisted(() => ({
  current: [] as Array<Record<string, unknown>>,
}));
const textModelsMock = vi.hoisted(() => ({
  current: [] as Array<Record<string, unknown>>,
}));
// 音视频提取文案面板的 ASR 候选目录（网关模型 + 内置 FunASR / 豆包语音）。
const mediaModelsMock = vi.hoisted(() => ({
  current: [] as Array<Record<string, unknown>>,
}));
// 视频反推剧本面板的视觉模型候选（/model-gateway/vision-models）。
const visionModelsMock = vi.hoisted(() => ({
  current: [] as Array<Record<string, unknown>>,
}));
// [aipdd fork delta req46] 音乐创作面板 + ModelPicker 共读 music 目录（/freezone/music/models）。
const musicModelsMock = vi.hoisted(() => ({
  current: [] as Array<Record<string, unknown>>,
}));

vi.mock("@/features/canvas/hooks/useFreezoneVideoModels", () => ({
  useFreezoneVideoModels: () => ({
    models: videoModelsMock.current,
    isLoading: false,
    isFallback: false,
    error: null,
  }),
}));
vi.mock("@/features/canvas/hooks/useFreezoneImageModels", () => ({
  useFreezoneImageModels: () => ({
    models: imageModelsMock.current,
    isLoading: false,
    isFallback: false,
    error: null,
  }),
}));
vi.mock("@/features/canvas/hooks/useFreezoneAudioModels", () => ({
  useFreezoneAudioModels: () => ({
    models: audioModelsMock.current,
    isLoading: false,
    isFallback: false,
    error: null,
  }),
}));
// 一句话短视频面板另读文本（LLM）模型目录；目录为空时下拉只留「跟随系统默认」
// 一项（留空 = 走后端默认文本模型链）。
vi.mock("@/features/canvas/hooks/useFreezoneTextModels", () => ({
  useFreezoneTextModels: () => ({
    models: textModelsMock.current,
    isLoading: false,
    isFallback: false,
    error: null,
  }),
}));
vi.mock("@/features/canvas/hooks/useFreezoneMediaCopywriteModels", () => ({
  useFreezoneMediaCopywriteModels: () => ({
    models: mediaModelsMock.current,
    isLoading: false,
    isFallback: false,
    error: null,
  }),
}));
// 视频反推剧本面板另读视觉模型候选；候选为空时下拉只留「跟随系统默认」
// 一项（留空 = 走后端默认视觉链），不阻断提交。
vi.mock("@/features/canvas/hooks/useFreezoneVisionModels", () => ({
  useFreezoneVisionModels: () => ({
    models: visionModelsMock.current,
    isLoading: false,
    isFallback: false,
    error: null,
  }),
}));
// [aipdd fork delta req46] 音乐目录不写死兜底：面板与 ModelPicker 都只认这里注入的条目。
vi.mock("@/features/canvas/hooks/useFreezoneMusicModels", () => ({
  useFreezoneMusicModels: () => ({
    models: musicModelsMock.current,
    isLoading: false,
    isFallback: false,
    error: null,
  }),
}));

// MediaKit 配置状态：工具页用它区分「火山 AI-MediaKit」可用性提示（默认未配置）。
const mediakitConfiguredMock = vi.hoisted(() => ({ current: false }));

vi.mock("@/lib/queries/model-gateway", () => ({
  useModelGatewayConfig: () => ({
    data: {
      ok: true,
      data: { videoEnhance: { configured: mediakitConfiguredMock.current } },
    },
    isLoading: false,
  }),
}));

import {
  submitFreezoneAudioSpeech,
  // [aipdd fork delta] 科普讲解视频提交。
  submitFreezoneExplainerVideo,
  // [aipdd fork delta] 公众号文章：面板提交/发布/预览回读。
  submitFreezoneArticleWrite,
  submitFreezoneArticlePublish,
  fetchFreezoneArticleResult,
  // [aipdd fork delta Phase 2B] 渠道短视频提交。
  submitFreezoneChannelShortVideo,
  // [aipdd fork delta] 口播智能剪辑提交。
  submitFreezoneNarrationSmartEdit,
  // [aipdd fork delta] 短视频口播稿提交。
  submitFreezoneOralScript,
  submitFreezoneMediaCopywrite,
  // [aipdd fork delta] 动效大片：案例图册清单、提示词回读、渲染、拍点预检与交付提交。
  listFreezoneMotionCases,
  submitFreezoneMotionRender,
  submitFreezoneMotionStills,
  // [aipdd fork delta req46] 音乐创作：五模式提交 + 两步翻唱的抽取歌词。
  submitFreezoneMusicCreate,
  submitFreezoneMusicCoverPreprocess,
  submitFreezoneVideoClone,
  submitFreezoneVideoEdit,
  submitFreezoneVideoErase,
  submitFreezoneVideoIdea,
  submitFreezoneVideoMix,
  // [aipdd fork delta req42] 「替换视频人物 / 产品」「对口型」走全能参考提交。
  submitFreezoneVideoOmniGen,
  submitFreezoneVideoScriptReverse,
  submitFreezoneWhiteboardAnimation,
  // [aipdd fork delta] 讲解类视频台词草稿提交。
  submitFreezoneExplainerScript,
  submitFreezoneVoiceReplace,
  submitFreezoneVideoDubbing,
} from "@/api/ops";
import { ToolsPage } from "@/features/tools/ToolsPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToolsPage project="demo" />
    </QueryClientProvider>,
  );
}

// [aipdd fork delta] 默认面板现在是工具栏首项「短视频口播稿」，要测其它面板就先点菜单。
// 走真点击而非改组件入参：顺带验住菜单本身能通到那个面板。
async function renderPageOn(toolName: string) {
  const view = renderPage();
  await userEvent.click(screen.getByRole("button", { name: toolName }));
  return view;
}

describe("ToolsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    videoModelsMock.current = [];
    audioModelsMock.current = [];
    imageModelsMock.current = [];
    textModelsMock.current = [];
    mediaModelsMock.current = [];
    visionModelsMock.current = [];
    musicModelsMock.current = [];
    mediakitConfiguredMock.current = false;
    tasksMock.current = [];
  });

  it("renders the page title and every registered menu entry", () => {
    renderPage();
    expect(screen.getByText("工具")).toBeInTheDocument();
    // 菜单是「入口」名册：白板动画 / 科普讲解已合并成一条「讲解类视频」（排最后），
    // 它们的表单在入口内切换，不在菜单里各占一项（分组行为见下面那条用例）。
    for (const name of [
      // [aipdd fork delta] 短视频口播稿：工具栏首项。
      "短视频口播稿",
      "视频复刻",
      "混剪短视频",
      "一句话生成短视频",
      "人声复刻",
      // [aipdd fork delta] 视频配音：音频族，排人声复刻之后。
      "视频配音",
      // [aipdd fork delta req46] 音乐创作：音频族，排人声复刻之后。
      "音乐创作",
      "对口型视频",
      "替换视频人物",
      "替换视频产品",
      "视频去水印",
      "图片去水印",
      "音视频提取文案",
      "视频反推剧本",
      // [aipdd fork delta] 动效大片：案例图册 → 本机渲染成片 → 剪映交付。
      "动效大片",
      "讲解类视频",
      // [aipdd fork delta] 内容创作：公众号文章编写与排版（排菜单最后）。
      "内容创作",
    ]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
  });

  it("defaults to the oral-script panel (first tool) with the situation fields", () => {
    renderPage();
    // 开页就是菜单首项：收「这条想讲什么 / 我是谁 / 素材」这份处境。
    expect(screen.getByText("这条想讲什么")).toBeInTheDocument();
    expect(screen.getByText("我是谁")).toBeInTheDocument();
    expect(screen.getByText("素材")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "炼口播稿" })).toBeDisabled();
    // 蒸馏来源与许可进页就提示：上游技能包是 CC BY-NC 4.0（不可商用）。
    expect(screen.getByText(/不可商用/)).toBeInTheDocument();
  });

  it("oral-script submits the situation with the picked formula, duration and title count", async () => {
    renderPage();
    fireEvent.change(screen.getByPlaceholderText("如：我为什么不再给客户做全案"), {
      target: { value: "  我为什么不再做全案  " },
    });
    fireEvent.change(
      screen.getByPlaceholderText("如：做了八年后期，现在只接短视频代运营"),
      { target: { value: "做了八年后期" } },
    );
    fireEvent.change(
      screen.getByPlaceholderText("手写要点、粘贴的文案或事实材料都行（不超过 12000 字）"),
      { target: { value: "去年退了三个全案客户" } },
    );
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "结构" }), "contrarian");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "目标时长" }), "30");
    fireEvent.change(screen.getByLabelText("标题候选条数"), { target: { value: "5" } });

    await userEvent.click(screen.getByRole("button", { name: "炼口播稿" }));
    await waitFor(() => expect(submitFreezoneOralScript).toHaveBeenCalledTimes(1));
    // 文本模型留空不塞 key：后端走默认文本链。素材/处境字段要 trim 后原样透传。
    expect(vi.mocked(submitFreezoneOralScript).mock.calls[0][1]).toEqual({
      topic: "我为什么不再做全案",
      persona: "做了八年后期",
      material: "去年退了三个全案客户",
      audience: "",
      scene: "",
      tone: "",
      formula: "contrarian",
      durationS: 30,
      titleCount: 5,
      rewriteHardHits: true,
      modelText: undefined,
      sourceTool: "oral-script",
    });
  });

  it("oral-script renders the newest finished draft from the task list", () => {
    tasksMock.current = [
      {
        task_type: "freezone_oral_script",
        status: "completed",
        created_at: "2026-10-19T01:00:00Z",
        result: {
          title: "我为什么不再做全案",
          formula: "contrarian",
          sections: [
            // 后端带稳定 ASCII id + 原始节名：面板按 id 取自己那份词条，节名只做兜底。
            { id: "core", title: "一、内核", body: "把退全案讲成一次止损" },
            { id: "opening", title: "二、开头", body: "去年我退了三个全案客户" },
            { id: "script", title: "三、口播逐字稿", body: "【镜头】坐着讲" },
            { id: "why", title: "四、为什么这么排", body: "先给结论再拆原因" },
            { id: "titles", title: "五、标题候选", body: "1. 我为什么不再做全案" },
            { id: "shoot", title: "六、拍之前", body: "只需要一个固定机位" },
          ],
          script: "【镜头】坐着讲\n去年我退了三个全案客户。",
          spoken_text: "去年我退了三个全案客户。",
          titles: [{ title: "我为什么不再做全案", recommended: true, note: "反差开头" }],
          word_count: 152,
          estimated_seconds: 32,
          txt_url: "/static/freezone/_outputs/freezone_oral_script/oral-1.txt",
          rewritten: false,
          rewrite_reason: "",
          ai_tell: { passed: true, hard_count: 0, soft_count: 0, hard: [], soft: [] },
        },
      },
    ];

    renderPage();
    expect(screen.getByText("净台词 152 字 · 念下来约 32 秒")).toBeInTheDocument();
    // 提词器用的是净台词，镜头提示不混在里面。
    expect(screen.getByText("去年我退了三个全案客户。")).toBeInTheDocument();
    expect(screen.getByText("推荐")).toBeInTheDocument();
    expect(screen.getByText("机械预检没发现假对比这类硬伤。")).toBeInTheDocument();
    // 六节完整成稿在 details 里，按 id 命中的是前端词条而非后端节名。
    expect(screen.getByText("完整成稿（含镜头提示与六节说明）")).toBeInTheDocument();
    expect(screen.getByText("为什么这么排")).toBeInTheDocument();
    expect(screen.getByText("拍之前")).toBeInTheDocument();
    expect(screen.getByText("【镜头】坐着讲")).toBeInTheDocument();
  });

  it("oral-script degrades to the empty hint when no task has finished", () => {
    renderPage();
    expect(screen.getByText("还没有已完成的口播稿任务。")).toBeInTheDocument();
    expect(screen.queryByText("去 AI 味体检")).not.toBeInTheDocument();
  });

  // [aipdd fork delta] 重写轮跑过但没交出合格稿：交付的是初稿，文案不能说成「已重写」。
  it("oral-script tells the user the first draft was kept when the rewrite round failed", () => {
    tasksMock.current = [
      {
        task_type: "freezone_oral_script",
        status: "completed",
        created_at: "2026-10-19T01:00:00Z",
        result: {
          title: "我为什么不再做全案",
          script: "（坐着对镜头）\n这不是接单纠纷，是我把验收标准写错了。",
          spoken_text: "这不是接单纠纷，是我把验收标准写错了。",
          rewritten: false,
          rewrite_attempted: true,
          rewrite_reason: "ai_tell",
          ai_tell: {
            passed: false,
            hard_count: 1,
            soft_count: 0,
            hard: [
              {
                kind: "hard",
                rule: "不是X而是Y",
                line: 1,
                text: "这不是接单纠纷，是我把验收标准写错了。",
              },
            ],
            soft: [],
          },
        },
      },
    ];

    renderPage();
    expect(
      screen.getByText(
        "重写那一轮没交出能用的稿子，已保留初稿；体检报告里的硬伤需要你自己再看一眼。",
      ),
    ).toBeInTheDocument();
    // 「已定向重写」那两句话不能说，否则用户会以为质检已经过了。
    expect(
      screen.queryByText(
        "本地检查发现假对比这类硬伤，已只针对这些句子重写了一轮。",
      ),
    ).not.toBeInTheDocument();
  });

  // [aipdd fork delta] 跑完却没正文：说清楚要重试，不能伪装成「还没开始」。
  it("oral-script surfaces an empty body instead of pretending no task ran", () => {
    tasksMock.current = [
      {
        task_type: "freezone_oral_script",
        status: "completed",
        created_at: "2026-10-19T01:00:00Z",
        result: {
          script: "",
          spoken_text: "",
          ai_tell: { passed: true, hard_count: 0, soft_count: 0, hard: [], soft: [] },
        },
      },
    ];

    renderPage();
    expect(
      screen.getByText(
        "任务已完成，但成稿里没有「口播逐字稿」正文。请补充素材或换一个角度重新提交。",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("还没有已完成的口播稿任务。")).not.toBeInTheDocument();
  });

  it("video-clone panel shows the frames reference after switching to it", () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "视频复刻" }));
    expect(screen.getByText("参考方式")).toBeInTheDocument();
    expect(screen.getByLabelText("原帧参考")).toBeChecked();
    expect(screen.queryByLabelText("纯文本重制")).not.toBeChecked();
    expect(screen.getByLabelText("模型生成音")).toBeChecked();
    // frames 模式按 all_reference 过滤目录：视频目录为空时提示去配置。
    expect(
      screen.getByText("没有可用模型，请先到「设置 → 模型与渠道」配置该类型模型。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "原帧参考或含参考内容时需模型支持参考输入（all_reference）；纯文本重制可留空使用默认模型。",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("原帧参考：原视频关键帧作为逐镜参考图；纯文本重制：仅按分镜描述重新生成。"),
    ).toBeInTheDocument();
    // 参考内容区（图片）已就位，上限文案与 schema max_length 对齐。
    expect(screen.getByText("参考内容（可选，最多 4 张）")).toBeInTheDocument();
  });

  it("video-clone submits with frames mode, the catalog model id and the archive flag", async () => {
    videoModelsMock.current = [
      {
        id: "seedance-2.0-fast",
        catalogId: "cat-fast",
        providerId: "newapi",
        apiModel: "doubao-seedance-2-0-fast",
        label: "Seedance 2.0 Fast",
        supportedModes: ["all_reference"],
      },
    ];
    const { container } = await renderPageOn("视频复刻");
    // frames 模式按 all_reference 能力过滤并预选目录首项；触发器同时标注渠道胶囊。
    const modelTrigger = await screen.findByRole("combobox", { name: "模型" });
    expect(modelTrigger).toHaveTextContent("Seedance 2.0 Fast");
    expect(within(modelTrigger).getByText("LocalAPI")).toBeInTheDocument();

    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(fileInput, {
      target: { files: [new File(["x"], "ref.mp4", { type: "video/mp4" })] },
    });
    await screen.findByText("已选 1 项");

    await userEvent.click(screen.getByRole("button", { name: "批量执行（1）" }));
    await waitFor(() => expect(submitFreezoneVideoClone).toHaveBeenCalledTimes(1));
    expect(submitFreezoneVideoClone).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        videoUrl: "/static/x",
        referenceMode: "frames",
        audioMode: "generate",
        model: "cat-fast",
        archiveToLibrary: true,
      }),
    );
  });

  it("video-clone uploads reference content and submits its urls with the prompt", async () => {
    videoModelsMock.current = [
      {
        id: "seedance-2.0-fast",
        catalogId: "cat-fast",
        providerId: "newapi",
        apiModel: "doubao-seedance-2-0-fast",
        label: "Seedance 2.0 Fast",
        supportedModes: ["all_reference"],
      },
    ];
    const { container } = await renderPageOn("视频复刻");
    expect(await screen.findByRole("combobox", { name: "模型" })).toHaveTextContent(
      "Seedance 2.0 Fast",
    );

    const fileInputs = container.querySelectorAll('input[type="file"]');
    // 第 1 个输入区：参考视频；第 2 个输入区：参考内容（图片）。
    fireEvent.change(fileInputs[0], {
      target: { files: [new File(["x"], "ref.mp4", { type: "video/mp4" })] },
    });
    fireEvent.change(fileInputs[1], {
      target: { files: [new File(["y"], "style.png", { type: "image/png" })] },
    });
    // 两个输入区各自计数。
    await waitFor(() => expect(screen.getAllByText("已选 1 项")).toHaveLength(2));

    fireEvent.change(screen.getByPlaceholderText("可选：补充复刻风格或内容要求"), {
      target: { value: "更冷冽" },
    });
    await userEvent.click(screen.getByRole("button", { name: "批量执行（1）" }));
    await waitFor(() => expect(submitFreezoneVideoClone).toHaveBeenCalledTimes(1));
    expect(submitFreezoneVideoClone).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        videoUrl: "/static/x",
        prompt: "更冷冽",
        referenceUrls: ["/static/x"],
        referenceMode: "frames",
        model: "cat-fast",
      }),
    );
  });

  it("video-clone offers the reference priority switch only with reference content", async () => {
    videoModelsMock.current = [
      {
        id: "seedance-2.0-fast",
        catalogId: "cat-fast",
        providerId: "newapi",
        apiModel: "doubao-seedance-2-0-fast",
        label: "Seedance 2.0 Fast",
        supportedModes: ["all_reference"],
      },
    ];
    const { container } = await renderPageOn("视频复刻");
    expect(await screen.findByRole("combobox", { name: "模型" })).toHaveTextContent(
      "Seedance 2.0 Fast",
    );
    // 没参考内容时两种取值行为相同，不该多一个开关。
    expect(screen.queryByText("参考优先级")).not.toBeInTheDocument();

    const fileInputs = container.querySelectorAll('input[type="file"]');
    fireEvent.change(fileInputs[0], {
      target: { files: [new File(["x"], "ref.mp4", { type: "video/mp4" })] },
    });
    fireEvent.change(fileInputs[1], {
      target: { files: [new File(["y"], "style.png", { type: "image/png" })] },
    });
    await waitFor(() => expect(screen.getAllByText("已选 1 项")).toHaveLength(2));

    // 默认主体替换优先：带参考内容时期望的是「换成参考内容」。
    expect(screen.getByLabelText("主体替换优先")).toBeChecked();
    expect(screen.getByLabelText("忠实构图优先")).not.toBeChecked();

    fireEvent.click(screen.getByLabelText("忠实构图优先"));
    await userEvent.click(screen.getByRole("button", { name: "批量执行（1）" }));
    await waitFor(() => expect(submitFreezoneVideoClone).toHaveBeenCalledTimes(1));
    expect(submitFreezoneVideoClone).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        referenceMode: "frames",
        referencePriority: "composition",
      }),
    );
  });

  it("video-clone sends the aspect ratio declared by the catalog model", async () => {
    videoModelsMock.current = [
      {
        id: "minimax-h3",
        catalogId: "cat-h3",
        providerId: "newapi",
        apiModel: "MiniMax H3",
        label: "MiniMax H3",
        supportedModes: ["all_reference"],
        ratioOptions: ["16:9", "9:16", "1:1"],
      },
    ];
    const { container } = await renderPageOn("视频复刻");
    // 画幅只列目录声明的档位（竖屏参考视频可选 9:16，不再被固定成横屏）。
    const aspect = await screen.findByLabelText("成片画幅");
    expect(
      within(aspect).getAllByRole("option").map((option) => option.textContent),
    ).toEqual(["16:9", "9:16", "1:1"]);
    fireEvent.change(aspect, { target: { value: "9:16" } });

    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(fileInput, {
      target: { files: [new File(["x"], "ref.mp4", { type: "video/mp4" })] },
    });
    await screen.findByText("已选 1 项");
    await userEvent.click(screen.getByRole("button", { name: "批量执行（1）" }));
    await waitFor(() => expect(submitFreezoneVideoClone).toHaveBeenCalledTimes(1));
    expect(submitFreezoneVideoClone).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({ aspectRatio: "9:16" }),
    );
  });

  it("video-clone hides the aspect selector when the model declares no ratios", async () => {
    videoModelsMock.current = [
      {
        id: "seedance-2.0-fast",
        catalogId: "cat-fast",
        providerId: "newapi",
        apiModel: "doubao-seedance-2-0-fast",
        label: "Seedance 2.0 Fast",
        supportedModes: ["all_reference"],
      },
    ];
    await renderPageOn("视频复刻");
    expect(await screen.findByRole("combobox", { name: "模型" })).toHaveTextContent(
      "Seedance 2.0 Fast",
    );
    // 目录没说过就不给选择器：界面不能列出一排模型其实不接受的档位。
    expect(screen.queryByLabelText("成片画幅")).not.toBeInTheDocument();
  });

  it("video-clone explains the dialogue transcription only when the model voices it", async () => {
    const dialogueHint = /模型生成音会先转写参考视频的人声为台词/;
    await renderPageOn("视频复刻");
    expect(screen.getByText(dialogueHint)).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("静音"));
    expect(screen.queryByText(dialogueHint)).not.toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("保留原声"));
    expect(screen.queryByText(dialogueHint)).not.toBeInTheDocument();
  });

  it("video-clone: custom LLM planning model shows bare name with channel badge but submits the prefixed id", async () => {
    textModelsMock.current = [
      {
        // 真实目录口径：id 是「渠道::模型」复合调用名，展示层须剥掉渠道前缀。
        id: "aipdd::ap-glm-5.2",
        providerId: "newapi",
        apiModel: "ap-glm-5.2",
        label: "aipdd::ap-glm-5.2",
        channel: "aipdd",
        isDefault: true,
      },
    ];
    const { container } = await renderPageOn("视频复刻");

    // 复刻规划（LLM）默认项回显目录默认模型与渠道（t7 语义：不再只说「跟随系统默认」）；
    // 展示层去渠道前缀（displayModelName 唯一真源），来源由旁的渠道胶囊表达。
    const planTrigger = screen.getByRole("combobox", { name: "复刻规划模型（LLM）" });
    expect(planTrigger).toHaveTextContent("默认（ap-glm-5.2）");
    expect(planTrigger).not.toHaveTextContent("aipdd::");
    await userEvent.click(planTrigger);
    // 默认项（label 以「默认（…）」开头）与候选项原名模型各一条：锚定开头精确取候选。
    const planOption = await screen.findByRole("option", {
      name: /^ap-glm-5.2/,
    });
    expect(planOption).not.toHaveTextContent("aipdd::");
    expect(within(planOption).getByText("AIPDD")).toBeInTheDocument();
    await userEvent.click(planOption);
    expect(planTrigger).toHaveTextContent("ap-glm-5.2");
    expect(within(planTrigger).getByText("AIPDD")).toBeInTheDocument();

    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(fileInput, {
      target: { files: [new File(["x"], "ref.mp4", { type: "video/mp4" })] },
    });
    await screen.findByText("已选 1 项");
    // 纯文本重制不需要视频模型（目录为空也能提交），LLM 选型随载荷透传：
    // 展示去前缀，但提交值仍是完整复合名（后端可执行性不变）。
    await userEvent.click(screen.getByLabelText("纯文本重制"));
    await userEvent.click(screen.getByRole("button", { name: "批量执行（1）" }));
    await waitFor(() => expect(submitFreezoneVideoClone).toHaveBeenCalledTimes(1));
    expect(submitFreezoneVideoClone).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        referenceMode: "text",
        modelText: "aipdd::ap-glm-5.2",
        archiveToLibrary: true,
      }),
    );
  });

  it("switching to video-mix shows the material pool, topic field and subtitle default", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "混剪短视频" }));
    expect(await screen.findByText("素材池（图片/视频，按顺序循环）")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("如：春日露营的三个理由")).toBeInTheDocument();
    // 默认 AI 配音；字幕默认不烧录（用户勾选），未勾选时不显示字号选择。
    expect(screen.getByLabelText("AI 配音（推荐）")).toBeChecked();
    expect(screen.getByLabelText("烧录字幕")).not.toBeChecked();
    expect(screen.queryByRole("combobox", { name: "字幕大小" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成混剪成片" })).toBeInTheDocument();
    // 文案自动生成（LLM）可自选：默认跟随系统默认（空值项不标渠道）。
    expect(screen.getByRole("combobox", { name: "文案生成模型（LLM）" })).toHaveTextContent(
      "跟随系统默认",
    );
  });

  it("video-mix submits the pool as one film with the audio model and archive flag", async () => {
    audioModelsMock.current = [
      {
        id: "ap-indextts-2",
        catalogId: "ap-indextts-2",
        providerId: "newapi",
        apiModel: "ap-indextts-2",
        label: "ap-indextts-2",
      },
    ];
    const { container } = renderPage();
    await userEvent.click(screen.getByRole("button", { name: "混剪短视频" }));
    // 配音目录就绪后 ModelPicker 预选首项（与 voice-clone 同源）。
    expect(await screen.findByRole("combobox", { name: "模型" })).toHaveTextContent(
      "ap-indextts-2",
    );

    // 混剪面板首个 file input 是素材池入口（BGM 上传在更后面）。
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(fileInput, {
      target: { files: [new File(["x"], "a.mp4", { type: "video/mp4" })] },
    });
    await screen.findByText("已选 1 项");

    fireEvent.change(screen.getByPlaceholderText("如：春日露营的三个理由"), {
      target: { value: "春日露营" },
    });
    // 勾选烧录字幕后出现字号选择，用户选择「大」随载荷提交。
    await userEvent.click(screen.getByLabelText("烧录字幕"));
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "字幕大小" }),
      "large",
    );
    await userEvent.click(screen.getByRole("button", { name: "生成混剪成片" }));

    // 一次提交只产出一条成片（素材池 ≠ 逐素材批量）。
    await waitFor(() => expect(submitFreezoneVideoMix).toHaveBeenCalledTimes(1));
    expect(submitFreezoneVideoMix).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        materialUrls: ["/static/x"],
        subject: "春日露营",
        audioMode: "generate",
        subtitleEnabled: true,
        subtitleFontSize: "large",
        model: "ap-indextts-2",
        archiveToLibrary: true,
      }),
    );
  });

  it("merges whiteboard and explainer into one explainer-class entry (now not the last)", async () => {
    renderPage();
    // 一个菜单入口（讲解类视频），两个类型切换项；入口不再各占一行。
    expect(screen.getAllByRole("button", { name: "讲解类视频" })).toHaveLength(1);
    // 白板动画 / 科普讲解不在菜单里（只在入口内作为类型出现）。
    expect(screen.queryByRole("button", { name: "白板动画视频" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "科普讲解视频" })).not.toBeInTheDocument();
    // 菜单首项是短视频口播稿，内容创作组退到第二位；音乐创作仍排在最后。
    const nav = screen.getByRole("button", { name: "视频复刻" }).parentElement;
    expect(nav?.firstElementChild?.textContent).toBe("短视频口播稿");
    expect(nav?.firstElementChild?.nextElementSibling?.textContent).toBe("内容创作");
    expect(nav?.lastElementChild?.textContent).toBe("音乐创作");

    await userEvent.click(screen.getByRole("button", { name: "讲解类视频" }));
    // 默认展开组内第一个类型（白板动画），并只展示那一份表单。
    expect(
      screen.getByPlaceholderText("粘贴要朗读的白板动画旁白全文（1-10000 字）"),
    ).toBeInTheDocument();
    // 切到第二个类型：表单跟着换，口播文案输入区不再存在。
    await userEvent.click(screen.getByRole("tab", { name: "科普讲解视频" }));
    expect(
      screen.getByPlaceholderText("粘贴要讲解的主题说明、文章或文档正文（1-12000 字）"),
    ).toBeInTheDocument();
    expect(
      screen.queryByPlaceholderText("粘贴要朗读的白板动画旁白全文（1-10000 字）"),
    ).not.toBeInTheDocument();
  });

  it("switching to whiteboard-animation shows the script field, three model areas and render defaults", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "讲解类视频" }));
    await userEvent.click(screen.getByRole("tab", { name: "白板动画视频" }));
    expect(
      screen.getByPlaceholderText("粘贴要朗读的白板动画旁白全文（1-10000 字）"),
    ).toBeInTheDocument();
    // 三类模型都可自选：分镜 LLM 默认「跟随系统默认」（空值走后端默认链）。
    expect(
      screen.getByRole("combobox", { name: "分镜规划模型（LLM）" }),
    ).toHaveTextContent("跟随系统默认");
    // 线稿与配音两条目录为空时各自提示去网关配置（不阻断面板其余项）。
    expect(
      await screen.findAllByText("没有可用模型，请先到「设置 → 模型与渠道」配置该类型模型。"),
    ).toHaveLength(2);
    // 逐笔揭示的笔尖光标默认开、字幕默认不烧录（未勾选时不显示字号）。
    expect(screen.getByLabelText("显示笔尖光标")).toBeChecked();
    expect(screen.getByLabelText("烧录字幕")).not.toBeChecked();
    expect(screen.queryByRole("combobox", { name: "字幕大小" })).not.toBeInTheDocument();
    // 白板默认横屏 16:9 / 720p，幕数上限 8；未填文案时提交不可用。
    expect(screen.getByLabelText("分镜幕数上限")).toHaveValue(8);
    expect(screen.getByLabelText("画面比例")).toHaveValue("16:9");
    expect(screen.getByLabelText("清晰度")).toHaveValue("720p");
    expect(screen.getByRole("button", { name: "生成白板动画" })).toBeDisabled();
  });

  it("whiteboard-animation submits the copy as one film with all three models", async () => {
    imageModelsMock.current = [
      {
        id: "seedream-4.0",
        catalogId: "cat-image",
        providerId: "volcengine",
        apiModel: "doubao-seedream-4.0",
        label: "Seedream 4.0",
      },
    ];
    audioModelsMock.current = [
      {
        id: "ap-indextts-2",
        catalogId: "ap-indextts-2",
        providerId: "newapi",
        apiModel: "ap-indextts-2",
        label: "ap-indextts-2",
      },
    ];
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "讲解类视频" }));
    await userEvent.click(screen.getByRole("tab", { name: "白板动画视频" }));
    // 两条目录就绪后 ModelPicker 各自预选首项（线稿走 image 目录、配音走 audio 目录）。
    expect(await screen.findByRole("combobox", { name: "线稿出图模型" })).toHaveTextContent(
      "Seedream 4.0",
    );
    expect(await screen.findByRole("combobox", { name: "配音模型" })).toHaveTextContent(
      "ap-indextts-2",
    );

    fireEvent.change(
      screen.getByPlaceholderText("粘贴要朗读的白板动画旁白全文（1-10000 字）"),
      { target: { value: "  为什么你的视频留不住人？  " } },
    );
    // 勾选烧录字幕后出现字号选择，用户选「大」随载荷提交。
    await userEvent.click(screen.getByLabelText("烧录字幕"));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "字幕大小" }), "large");
    await userEvent.click(screen.getByRole("button", { name: "生成白板动画" }));

    // 一次提交只产出一条成片（逐笔揭示全在本地渲染，不逐幕拆任务）。
    await waitFor(() => expect(submitFreezoneWhiteboardAnimation).toHaveBeenCalledTimes(1));
    expect(submitFreezoneWhiteboardAnimation).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        script: "为什么你的视频留不住人？",
        modelImage: "cat-image",
        model: "ap-indextts-2",
        maxScenes: 8,
        penCursor: true,
        subtitleEnabled: true,
        subtitleFontSize: "large",
        bgmUrl: "",
        bgmVolume: null,
        aspectRatio: "16:9",
        resolution: "720p",
        archiveToLibrary: true,
        sourceTool: "whiteboard-animation",
      }),
    );
    // 分镜规划模型未显式改选 → 不发 model_text，走后端口径的默认文本链。
    const payload = vi.mocked(submitFreezoneWhiteboardAnimation).mock.calls[0][1];
    expect(payload.modelText).toBeUndefined();
  });

  it("whiteboard-animation asks for a narration draft without rendering a film", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "讲解类视频" }));
    await userEvent.click(screen.getByRole("tab", { name: "白板动画视频" }));

    fireEvent.change(
      screen.getByPlaceholderText("粘贴要朗读的白板动画旁白全文（1-10000 字）"),
      { target: { value: "  第一幕旁白。第二幕旁白。  " } },
    );
    await userEvent.click(screen.getByRole("button", { name: "生成台词草稿" }));

    // 草稿只跑文本：入参是文案本身，不带任何出片参数。
    await waitFor(() => expect(submitFreezoneExplainerScript).toHaveBeenCalledTimes(1));
    expect(submitFreezoneExplainerScript).toHaveBeenCalledWith("demo", {
      kind: "whiteboard",
      script: "第一幕旁白。第二幕旁白。",
      maxScenes: 8,
      modelText: undefined,
      sourceTool: "whiteboard-animation",
    });
    expect(submitFreezoneWhiteboardAnimation).not.toHaveBeenCalled();
  });

  it("whiteboard-animation renders the finished draft as locked_script without a source script", async () => {
    tasksMock.current = [
      {
        task_type: "freezone_explainer_script",
        task_id: "draft-1",
        status: "completed",
        created_at: "2026-01-01T00:00:00Z",
        metadata: { source_tool: "whiteboard-animation" },
        result: {
          draft: {
            draft_version: 1,
            kind: "whiteboard",
            estimated_seconds: 6,
            scenes: [
              { text: "第一幕旁白。", keyText: "第一幕", scenePrompt: "画一条时间线" },
              { text: "第二幕旁白。", keyText: "", scenePrompt: "画两个方块" },
            ],
          },
        },
      },
    ];
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "讲解类视频" }));
    await userEvent.click(screen.getByRole("tab", { name: "白板动画视频" }));

    // 有草稿就不必再填源文案（那是生成草稿的输入，不是出片的输入）。
    expect(await screen.findByText(/共 2 幕/)).toBeInTheDocument();
    const submit = screen.getByRole("button", { name: "生成白板动画" });
    expect(submit).toBeEnabled();
    await userEvent.click(submit);

    await waitFor(() => expect(submitFreezoneWhiteboardAnimation).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(submitFreezoneWhiteboardAnimation).mock.calls[0][1];
    // 旁白由草稿逐幕文案拼回，同时整份草稿当 locked_script 回传：后端只认这一份台词。
    expect(payload.script).toBe("第一幕旁白。第二幕旁白。");
    expect(payload.lockedScript).toEqual({
      draft_version: 1,
      kind: "whiteboard",
      scenes: [
        { text: "第一幕旁白。", keyText: "第一幕", scenePrompt: "画一条时间线" },
        { text: "第二幕旁白。", keyText: "", scenePrompt: "画两个方块" },
      ],
    });
    // 草稿已经给过台词，成片任务不再需要模型重新切幕。
    expect(submitFreezoneExplainerScript).not.toHaveBeenCalled();
  });

  it("switching to explainer-video shows the reference field, planner model and render defaults", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "讲解类视频" }));
    await userEvent.click(screen.getByRole("tab", { name: "科普讲解视频" }));
    expect(
      screen.getByPlaceholderText("粘贴要讲解的主题说明、文章或文档正文（1-12000 字）"),
    ).toBeInTheDocument();
    // 解说词与分镜共用一个文本模型：目录为空时下拉只留「跟随系统默认」。
    expect(
      screen.getByRole("combobox", { name: "讲解规划模型（LLM）" }),
    ).toHaveTextContent("跟随系统默认");
    // 画面逐帧本地绘制，只有配音需要模型目录；目录为空时提示去网关配置。
    expect(
      await screen.findByText("没有可用模型，请先到「设置 → 模型与渠道」配置该类型模型。"),
    ).toBeInTheDocument();
    // 默认 180 秒 / 点阵波幕底 / 720p 横屏；未填参考资料时提交不可用。
    expect(screen.getByLabelText("目标时长（秒）")).toHaveValue(180);
    expect(screen.getByLabelText("幕底样式")).toHaveValue("dots");
    expect(screen.getByLabelText("清晰度")).toHaveValue("720p");
    expect(screen.getByRole("button", { name: "生成讲解视频" })).toBeDisabled();
  });

  it("explainer-video submits the reference as one film with the chosen models", async () => {
    audioModelsMock.current = [
      {
        id: "ap-indextts-2",
        catalogId: "ap-indextts-2",
        providerId: "newapi",
        apiModel: "ap-indextts-2",
        label: "ap-indextts-2",
      },
    ];
    textModelsMock.current = [
      { id: "glm-4.7", label: "GLM 4.7", channel: "zhipu", isDefault: false },
    ];
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "讲解类视频" }));
    await userEvent.click(screen.getByRole("tab", { name: "科普讲解视频" }));
    expect(await screen.findByRole("combobox", { name: "配音模型" })).toHaveTextContent(
      "ap-indextts-2",
    );

    fireEvent.change(
      screen.getByPlaceholderText("粘贴要讲解的主题说明、文章或文档正文（1-12000 字）"),
      { target: { value: "  RAG 先把文档切块入库，检索时取回最相关的几段拼进提示词。  " } },
    );
    fireEvent.change(screen.getByPlaceholderText("如：RAG 检索增强生成"), {
      target: { value: "RAG 是什么" },
    });
    fireEvent.change(screen.getByLabelText("目标时长（秒）"), { target: { value: "240" } });
    // ModelSelect 是自定义 combobox（原生 select 渲染不了渠道胶囊），点开后选候选。
    const planTrigger = screen.getByRole("combobox", { name: "讲解规划模型（LLM）" });
    await userEvent.click(planTrigger);
    await userEvent.click(await screen.findByRole("option", { name: /^GLM 4.7/ }));
    await userEvent.selectOptions(screen.getByLabelText("幕底样式"), "stars");
    await userEvent.click(screen.getByRole("button", { name: "生成讲解视频" }));

    // 一次提交只产出一条成片（逐帧绘制与混音全部在本地渲染）。
    await waitFor(() => expect(submitFreezoneExplainerVideo).toHaveBeenCalledTimes(1));
    expect(submitFreezoneExplainerVideo).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        referenceContent: "RAG 先把文档切块入库，检索时取回最相关的几段拼进提示词。",
        title: "RAG 是什么",
        durationS: 240,
        modelText: "glm-4.7",
        model: "ap-indextts-2",
        background: "stars",
        resolution: "720p",
        bgmUrl: "",
        bgmVolume: null,
        archiveToLibrary: true,
        sourceTool: "explainer-video",
      }),
    );
  });

  it("explainer-video keeps the reference gate but sends the confirmed draft as locked_script", async () => {
    tasksMock.current = [
      {
        task_type: "freezone_explainer_script",
        task_id: "draft-2",
        status: "completed",
        created_at: "2026-01-01T00:00:00Z",
        metadata: { source_tool: "explainer-video" },
        result: {
          draft: {
            draft_version: 1,
            kind: "explainer",
            title: { big: "RAG", rest: "检索增强生成", en: "", tagline: "" },
            duration_s: 180,
            estimated_seconds: 12,
            shots: [
              {
                chapter: "什么是 RAG",
                sentences: ["RAG 先把文档切块入库。"],
                primitive: "flow",
                headline: "四步走",
                items: ["切块", "入库"],
                numbers: [],
                emphasis: 0,
                motion: "travel",
              },
              {
                chapter: "为什么有效",
                sentences: ["答案可以追溯到出处。"],
                primitive: "list",
                headline: "可溯源",
                items: ["可溯源"],
                numbers: [],
                emphasis: -1,
                motion: "lightup",
              },
            ],
          },
        },
      },
    ];
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "讲解类视频" }));
    await userEvent.click(screen.getByRole("tab", { name: "科普讲解视频" }));

    expect(await screen.findByText(/共 2 镜/)).toBeInTheDocument();
    // 参考资料仍是必填：它决定入参校验与结果溯源（这条片子解释了哪份材料）。
    expect(screen.getByRole("button", { name: "生成讲解视频" })).toBeDisabled();

    fireEvent.change(
      screen.getByPlaceholderText("粘贴要讲解的主题说明、文章或文档正文（1-12000 字）"),
      { target: { value: "RAG 资料正文" } },
    );
    await userEvent.click(screen.getByRole("button", { name: "生成讲解视频" }));

    await waitFor(() => expect(submitFreezoneExplainerVideo).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(submitFreezoneExplainerVideo).mock.calls[0][1];
    expect(payload.referenceContent).toBe("RAG 资料正文");
    expect(payload.lockedScript).toMatchObject({
      kind: "explainer",
      shots: [
        { chapter: "什么是 RAG", sentences: ["RAG 先把文档切块入库。"] },
        { chapter: "为什么有效", sentences: ["答案可以追溯到出处。"] },
      ],
    });
  });

  it("switching to video-idea shows the idea field, dual model pickers and portrait default", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "一句话生成短视频" }));
    expect(
      screen.getByPlaceholderText("如：一只流浪猫在雨夜救下迷路的小女孩"),
    ).toBeInTheDocument();
    // 双模型目录（图片=肖像与帧、视频=逐镜）：目录为空时两处都提示去网关配置。
    expect(
      await screen.findAllByText("没有可用模型，请先到「设置 → 模型与渠道」配置该类型模型。"),
    ).toHaveLength(2);
    // LLM 文本模型默认「跟随系统默认」（空值走后端默认链，不静默改行为）。
    expect(
      screen.getByRole("combobox", { name: "文本模型（故事与分镜）" }),
    ).toHaveTextContent("跟随系统默认");
    // 默认三视图肖像 + 模型生成音频；提交按钮为创意成片口径。
    expect(screen.getByLabelText("三视图（一致性更好）")).toBeChecked();
    expect(screen.getByLabelText("模型生成音频（对白/音效）")).toBeChecked();
    expect(screen.getByRole("button", { name: "生成一句话短视频" })).toBeInTheDocument();
    // 数字输入只保留「总时长（秒）」：分场/分镜/单镜时长交给 AI 规划。
    expect(screen.getByLabelText("总时长（秒）")).toHaveValue(60);
    expect(screen.queryByText("场景数上限")).not.toBeInTheDocument();
    expect(screen.queryByText("每场景镜头数")).not.toBeInTheDocument();
    expect(screen.queryByText("单镜时长（秒）")).not.toBeInTheDocument();
  });

  it("video-idea submits the idea with both catalog models and the archive flag", async () => {
    videoModelsMock.current = [
      {
        id: "seedance-2.0-fast",
        catalogId: "cat-video",
        providerId: "newapi",
        apiModel: "doubao-seedance-2-0-fast",
        label: "Seedance 2.0 Fast",
      },
    ];
    imageModelsMock.current = [
      {
        id: "seedream-4.0",
        catalogId: "cat-image",
        providerId: "volcengine",
        apiModel: "doubao-seedream-4.0",
        label: "Seedream 4.0",
      },
    ];
    textModelsMock.current = [
      {
        id: "DC-freezone-text-writer-LLM",
        providerId: "newapi",
        apiModel: "DC-freezone-text-writer-LLM",
        label: "DC-freezone-text-writer-LLM",
      },
    ];
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "一句话生成短视频" }));
    // 两条目录就绪后 ModelPicker 各自预选首项（图片触发器同时渲染渠道胶囊）。
    expect(await screen.findByRole("combobox", { name: "图片模型（角色与画面）" })).toHaveTextContent(
      "Seedream 4.0",
    );
    expect(await screen.findByRole("combobox", { name: "视频模型（逐镜生成）" })).toHaveTextContent(
      "Seedance 2.0 Fast",
    );
    expect(
      within(screen.getByRole("combobox", { name: "图片模型（角色与画面）" })).getByText(
        "VolcEngine",
      ),
    ).toBeInTheDocument();

    // 文本模型默认项回显目录默认模型（t7 语义）；打开下拉显式改选后随载荷提交。
    const textTrigger = screen.getByRole("combobox", { name: "文本模型（故事与分镜）" });
    expect(textTrigger).toHaveTextContent("默认（DC-freezone-text-writer-LLM）");
    await userEvent.click(textTrigger);
    // 默认项与候选同名：锚定开头精确点候选（否则同时匹配到两条 option）。
    await userEvent.click(
      await screen.findByRole("option", { name: /^DC-freezone-text-writer-LLM/ }),
    );
    expect(textTrigger).toHaveTextContent("DC-freezone-text-writer-LLM");

    fireEvent.change(screen.getByPlaceholderText("如：一只流浪猫在雨夜救下迷路的小女孩"), {
      target: { value: "一只流浪猫在雨夜救下迷路的小女孩" },
    });
    await userEvent.click(screen.getByRole("button", { name: "生成一句话短视频" }));

    // 一次提交只产出一条成片（创意 ≠ 逐素材批量）。
    await waitFor(() => expect(submitFreezoneVideoIdea).toHaveBeenCalledTimes(1));
    expect(submitFreezoneVideoIdea).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        idea: "一只流浪猫在雨夜救下迷路的小女孩",
        portraitMode: "three",
        totalDuration: 60,
        modelText: "DC-freezone-text-writer-LLM",
        modelImage: "cat-image",
        modelVideo: "cat-video",
        aspectRatio: "16:9",
        resolution: "720p",
        generateAudio: true,
        archiveToLibrary: true,
      }),
    );
  });

  it("switching to replace-person shows the model area and empty-model hint", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "替换视频人物" }));
    expect(
      await screen.findByText("没有可用模型，请先到「设置 → 模型与渠道」配置该类型模型。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "建议选择支持全能参考的视频模型（如 Seedance 2.0 / 2.5、MiniMax H3）；列表已按该能力过滤。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("源视频")).toBeInTheDocument();
    expect(screen.getByText("人物参考图")).toBeInTheDocument();
  });

  it("switching to replace-product shows the product fields and model hint", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "替换视频产品" }));
    expect(
      await screen.findByText("没有可用模型，请先到「设置 → 模型与渠道」配置该类型模型。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "建议选择支持全能参考的视频模型（如 Seedance 2.0 / 2.5、MiniMax H3）；列表已按该能力过滤。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("源视频")).toBeInTheDocument();
    expect(screen.getByText("产品参考图")).toBeInTheDocument();
  });

  // [aipdd fork delta req42] 「替换视频产品」的产物必须以源视频为画面基准：曾经
  // 线上只把参考图下发（源视频整个丢掉）+ 固定 16:9，出片与原视频毫不相干。
  it("replace-product submits the source video as the reference and lets the aspect follow it", async () => {
    videoModelsMock.current = [
      {
        id: "ap-minimax-h3",
        catalogId: "cat-h3",
        providerId: "aipdd",
        apiModel: "ap-minimax-h3",
        label: "MiniMax H3",
        supportedModes: ["all_reference"],
      },
    ];
    const { container } = renderPage();
    await userEvent.click(screen.getByRole("button", { name: "替换视频产品" }));

    const modelTrigger = await screen.findByRole("combobox", { name: "模型" });
    expect(modelTrigger).toHaveTextContent("MiniMax H3");

    const [sourceInput, driverInput] = Array.from(
      container.querySelectorAll('input[type="file"]'),
    ) as HTMLInputElement[];
    fireEvent.change(sourceInput, {
      target: { files: [new File(["v"], "source.mp4", { type: "video/mp4" })] },
    });
    await screen.findByText("已选 1 项");
    fireEvent.change(driverInput, {
      target: { files: [new File(["p"], "product.png", { type: "image/png" })] },
    });
    // 参考图上传是异步的：等到「已选择」回显再提交，否则 driverUrl 还是 null。
    await screen.findByText("已选择");

    await userEvent.click(screen.getByRole("button", { name: "批量执行（1）" }));
    await waitFor(() => expect(submitFreezoneVideoOmniGen).toHaveBeenCalledTimes(1));
    expect(submitFreezoneVideoOmniGen).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        // 源视频排在第一位（它是画面基准），参考产品图紧随其后。
        references: [
          { type: "video", url: "/static/x" },
          { type: "image", url: "/static/x" },
        ],
        genMode: "allReference",
        // 画幅交回后端按源视频探测，不在前端拍脑袋定 16:9。
        aspectRatio: "auto",
        model: "cat-h3",
        archiveToLibrary: true,
        sourceTool: "replace-product",
      }),
    );
  });

  it("switching to lip-sync shows the driving-audio field and model hint", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "对口型视频" }));
    expect(await screen.findByText("底板（图/视频）")).toBeInTheDocument();
    expect(screen.getByText("驱动音频")).toBeInTheDocument();
    expect(
      screen.getByText(
        "建议选择支持全能参考的视频模型（如 Seedance 2.0 / 2.5、MiniMax H3）；列表已按该能力过滤。",
      ),
    ).toBeInTheDocument();
  });

  it("switching to video-watermark shows the three engines and erase modes", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "视频去水印" }));
    // 引擎三选一：本地引擎 / 火山 AI-MediaKit 恒定可选（空目录也保留）；
    // AI 视频编辑模型在 video 目录有 video_edit 模型时平铺出现（需求 6：无分组标题）。
    const engineTrigger = screen.getByRole("combobox", { name: "模型" });
    expect(engineTrigger).toHaveTextContent("本地引擎");
    await userEvent.click(engineTrigger);
    expect(screen.getByRole("option", { name: "本地引擎" })).toBeInTheDocument();
    // 该选项带渠道胶囊「火山 AI MediaKit」（子产品线名，渠道归属 VolcEngine），accessible name 含徽标文本。
    const mediakitOption = screen.getByRole("option", { name: /火山 AI-MediaKit/ });
    expect(within(mediakitOption).getByText("火山 AI MediaKit")).toBeInTheDocument();
    // 需求 6：目录模型不套分组标题，空目录下更不存在「Seedance 系列」组。
    expect(screen.queryByRole("group", { name: "Seedance 系列" })).not.toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    // 擦除引擎初始为本地（未挂 Seedance 模型）：显示智能识别 / 框选单选与本地 hint。
    expect(await screen.findByText("智能识别")).toBeInTheDocument();
    expect(screen.getByText("框选区域")).toBeInTheDocument();
    expect(
      screen.getByText("本机 FFmpeg 擦除：不出网、无额外费用，复杂背景下效果一般。"),
    ).toBeInTheDocument();
    // 未上传视频前展示拖拽框选的占位提示。
    expect(screen.getByText("上传视频后即可在预览上拖拽框选")).toBeInTheDocument();
  });

  it("media-copywrite: link source submits with explicit ASR model", async () => {
    mediaModelsMock.current = [
      {
        id: "funasr:aipdd:aipdd_funasr_transcribe",
        kind: "funasr",
        provider: "aipdd",
        channel: "aipdd",
        model: "aipdd_funasr_transcribe",
        label: "FunASR",
        // [aipdd fork delta req40] 输入形态由后端按通道能力下发，前端不再拿 kind 反推。
        acceptsLocalAudio: false,
      },
      {
        id: "gateway:volc:doubao-seed-asr-1.0",
        kind: "gateway",
        provider: "volc",
        channel: "volc",
        model: "doubao-seed-asr-1.0",
        label: "doubao-seed-asr-1.0",
        acceptsLocalAudio: true,
      },
    ];
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "音视频提取文案" }));

    // 默认来源为「链接下载」：展示链接输入框 + 平台下拉。
    const linkBox = screen.getByPlaceholderText(
      "粘贴视频分享链接或整段分享文案（含链接即可）",
    );
    expect(linkBox).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "平台" })).toBeInTheDocument();

    // 需求 7：无「默认（FunASR 内置通道）」项，触发器显示占位而非目录首项。
    const trigger = screen.getByRole("combobox", { name: "ASR 模型" });
    expect(trigger).toHaveTextContent("请选择模型");
    await userEvent.click(trigger);
    expect(screen.queryByRole("option", { name: /默认/ })).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: /FunASR/ })).toBeInTheDocument();

    // 选择 FunASR 候选：hint 换成直连通道说明。
    await userEvent.click(screen.getByRole("option", { name: /FunASR/ }));
    // [aipdd fork delta req40] hint 由后端下发的 acceptsLocalAudio 触发，文案按通道名插值。
    expect(screen.getByText(/aipdd 通道只接受公网音频直链/)).toBeInTheDocument();

    // 提交载荷携带来源类型 / 链接 / 平台 / 显式选中的候选 id / 工具归属。
    await userEvent.type(linkBox, "https://www.youtube.com/watch?v=abc123");
    await userEvent.click(screen.getByRole("button", { name: "提取文案" }));
    await waitFor(() => expect(submitFreezoneMediaCopywrite).toHaveBeenCalledTimes(1));
    expect(submitFreezoneMediaCopywrite).toHaveBeenCalledWith("demo", {
      sourceType: "link",
      url: "https://www.youtube.com/watch?v=abc123",
      platform: "douyin",
      model: "funasr:aipdd:aipdd_funasr_transcribe",
      sourceTool: "media-copywrite",
    });
  });

  it("media-copywrite: submitting without an ASR model is blocked", async () => {
    mediaModelsMock.current = [
      {
        id: "funasr:aipdd:aipdd_funasr_transcribe",
        kind: "funasr",
        provider: "aipdd",
        channel: "aipdd",
        model: "aipdd_funasr_transcribe",
        label: "FunASR",
        // [aipdd fork delta req40] 输入形态由后端按通道能力下发，前端不再拿 kind 反推。
        acceptsLocalAudio: false,
      },
    ];
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "音视频提取文案" }));
    // 未选模型：hint 为「不设默认模型」引导，提交被拦下。
    expect(screen.getByText(/ASR 不设默认模型/)).toBeInTheDocument();

    await userEvent.type(
      screen.getByPlaceholderText("粘贴视频分享链接或整段分享文案（含链接即可）"),
      "https://www.bilibili.com/video/BV1xx",
    );
    await userEvent.click(screen.getByRole("button", { name: "提取文案" }));
    expect(submitFreezoneMediaCopywrite).not.toHaveBeenCalled();
  });

  it("media-copywrite: unconfigured ASR credential warns near the submit button", async () => {
    // 火山语音 ASR 凭证与网关 API Key 不通用：选中 ready=false 的候选时，
    // 提交按钮上方出现「去设置」提醒；切回就绪候选即消失。
    mediaModelsMock.current = [
      {
        id: "doubao_asr:openspeech:seedasr",
        kind: "doubao_asr",
        provider: "volcengine",
        channel: "volcengine",
        model: "seedasr",
        label: "豆包语音 · 录音文件识别",
        ready: false,
      },
      {
        id: "gateway:volc:doubao-seed-asr-1.0",
        kind: "gateway",
        provider: "volc",
        channel: "volc",
        model: "doubao-seed-asr-1.0",
        label: "doubao-seed-asr-1.0",
        acceptsLocalAudio: true,
        ready: true,
      },
    ];
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "音视频提取文案" }));
    // 未选模型：不提醒（提醒只针对已选中的未就绪候选）。
    expect(screen.queryByText(/凭证未配置/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("combobox", { name: "ASR 模型" }));
    await userEvent.click(screen.getByRole("option", { name: /豆包语音/ }));
    expect(
      screen.getByText(/VolcEngine ASR）凭证未配置/),
    ).toBeInTheDocument();
    // 切到就绪的网关候选：提醒消失。
    await userEvent.click(screen.getByRole("combobox", { name: "ASR 模型" }));
    await userEvent.click(screen.getByRole("option", { name: /doubao-seed-asr-1.0/ }));
    expect(screen.queryByText(/凭证未配置/)).not.toBeInTheDocument();
  });

  it("media-copywrite: upload sources swap the link/platform inputs for an upload control", async () => {
    mediaModelsMock.current = [
      {
        id: "gateway:volc:doubao-seed-asr-1.0",
        kind: "gateway",
        provider: "volc",
        channel: "volc",
        model: "doubao-seed-asr-1.0",
        label: "doubao-seed-asr-1.0",
        acceptsLocalAudio: true,
      },
    ];
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "音视频提取文案" }));
    // 链接模式下有平台下拉；切到「上传音频」后平台下拉与链接框消失，改为上传入口。
    expect(screen.getByRole("combobox", { name: "平台" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "上传音频" }));
    expect(screen.queryByRole("combobox", { name: "平台" })).not.toBeInTheDocument();
    expect(
      screen.queryByPlaceholderText("粘贴视频分享链接或整段分享文案（含链接即可）"),
    ).not.toBeInTheDocument();

    // 上传走 /freezone/upload（mock 返回静态地址），提交携带 upload_audio + 该 url，
    // 上传来源不带 platform。
    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]');
    expect(fileInput).not.toBeNull();
    fireEvent.change(fileInput as HTMLInputElement, {
      target: { files: [new File(["audio"], "voice.mp3", { type: "audio/mpeg" })] },
    });
    await waitFor(() =>
      expect(
        screen.getByRole("combobox", { name: "ASR 模型" }),
      ).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole("combobox", { name: "ASR 模型" }));
    await userEvent.click(screen.getByRole("option", { name: /doubao-seed-asr-1.0/ }));
    await userEvent.click(screen.getByRole("button", { name: "提取文案" }));
    await waitFor(() => expect(submitFreezoneMediaCopywrite).toHaveBeenCalledTimes(1));
    expect(submitFreezoneMediaCopywrite).toHaveBeenCalledWith("demo", {
      sourceType: "upload_audio",
      url: "/static/x",
      model: "gateway:volc:doubao-seed-asr-1.0",
      sourceTool: "media-copywrite",
    });
  });

  it("video-watermark: picking AI-MediaKit submits erase with engine=mediakit", async () => {
    const { container } = renderPage();
    await userEvent.click(screen.getByRole("button", { name: "视频去水印" }));

    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(fileInput, {
      target: { files: [new File(["x"], "clip.mp4", { type: "video/mp4" })] },
    });
    await screen.findByRole("button", { name: "播放" });

    // 未配置 MediaKit Key：hint 如实提示「将回退本地」。
    await userEvent.click(screen.getByRole("combobox", { name: "模型" }));
    await userEvent.click(screen.getByRole("option", { name: /火山 AI-MediaKit/ }));
    expect(
      screen.getByText(
        "未配置 MediaKit API Key，提交后将自动回退本地擦除；可在「设置 → 模型与渠道」配置。",
      ),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "批量执行（1）" }));
    await waitFor(() => expect(submitFreezoneVideoErase).toHaveBeenCalledTimes(1));
    expect(submitFreezoneVideoErase).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        sourceUrl: "/static/x",
        engine: "mediakit",
        archiveToLibrary: true,
      }),
    );
  });

  it("video-watermark: picking a video-edit model switches to the AI editing path", async () => {
    videoModelsMock.current = [
      {
        id: "seedance-2.0-mini",
        catalogId: "cat-mini",
        providerId: "newapi",
        apiModel: "doubao-seedance-2-0-mini",
        label: "Seedance 2.0 Mini",
        supportedModes: ["video_edit"],
      },
    ];
    const { container } = renderPage();
    await userEvent.click(screen.getByRole("button", { name: "视频去水印" }));

    // 目录就绪后打开下拉：AI 视频编辑模型平铺出现（video_edit 能力过滤）且模型带渠道胶囊。
    // 需求 6：下拉不再有「Seedance 系列」归类，模型与本地下拉同列。
    await userEvent.click(screen.getByRole("combobox", { name: "模型" }));
    expect(screen.queryByRole("group", { name: "Seedance 系列" })).not.toBeInTheDocument();
    const editOption = await screen.findByRole("option", { name: /Seedance 2.0 Mini/ });
    expect(within(editOption).getByText("LocalAPI")).toBeInTheDocument();

    // 上传一个视频：批量输入统一走 uploadFreezoneImage 拿到静态地址。
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    expect(fileInput).not.toBeNull();
    fireEvent.change(fileInput, {
      target: { files: [new File(["x"], "clip.mp4", { type: "video/mp4" })] },
    });
    // 上传完成后出现可播放的预览控件（用于定位水印）。
    await screen.findByRole("button", { name: "播放" });

    // 选定支持 video_edit 的模型 → AI 视频编辑路径：出现处理指令，隐藏擦除模式单选。
    await userEvent.click(editOption);
    expect(await screen.findByText("处理指令")).toBeInTheDocument();
    expect(screen.queryByText("智能识别")).not.toBeInTheDocument();

    // 提交后走 submitFreezoneVideoEdit：保留原声、带目录 id，并开启产物自动入库。
    await userEvent.click(screen.getByRole("button", { name: "批量执行（1）" }));
    await waitFor(() => expect(submitFreezoneVideoEdit).toHaveBeenCalledTimes(1));
    expect(submitFreezoneVideoEdit).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        videoUrl: "/static/x",
        genMode: "videoEdit",
        audioSetting: "origin",
        model: "cat-mini",
        archiveToLibrary: true,
      }),
    );
  });

  it("voice-clone submits every line with the archive flag", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "人声复刻" }));

    const textarea = screen.getByRole("textbox");
    fireEvent.change(textarea, { target: { value: "第一句台词\n第二句台词" } });
    await userEvent.click(screen.getByRole("button", { name: "批量执行（2）" }));

    await waitFor(() => expect(submitFreezoneAudioSpeech).toHaveBeenCalledTimes(2));
    // 工具页产物统一开启自动入库「待分类资产」。
    for (const call of vi.mocked(submitFreezoneAudioSpeech).mock.calls) {
      expect(call[1]).toMatchObject({ archiveToLibrary: true });
    }
  });

  it("voice-clone: audio catalog preselects the default model and submits its apiModel", async () => {
    audioModelsMock.current = [
      {
        id: "ap-indextts-2",
        catalogId: "ap-indextts-2",
        providerId: "newapi",
        apiModel: "ap-indextts-2",
        label: "ap-indextts-2",
      },
    ];
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "人声复刻" }));

    // 目录就绪后预选首项（=「设置 → 默认生成模型」里的音频默认模型），触发器即显示其名称。
    const modelTrigger = await screen.findByRole("combobox", { name: "模型" });
    expect(modelTrigger).toHaveTextContent("ap-indextts-2");
    // 打开下拉：候选中只有真实目录条目，没有合成的「默认引擎」占位。
    await userEvent.click(modelTrigger);
    expect(screen.getByRole("option", { name: /ap-indextts-2/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "默认引擎" })).not.toBeInTheDocument();
    await userEvent.keyboard("{Escape}");

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "你好" } });
    await userEvent.click(screen.getByRole("button", { name: "批量执行（1）" }));

    await waitFor(() => expect(submitFreezoneAudioSpeech).toHaveBeenCalledTimes(1));
    // 显式提交目录里的 apiModel，与下拉显示保持一致（不再隐含走后端内置引擎）。
    expect(submitFreezoneAudioSpeech).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({ text: "你好", model: "ap-indextts-2" }),
    );
  });

  // [aipdd fork delta] 人声复刻→替换人声：切到替换模式后上传原始素材 + 参考音频，
  // 两者齐备才提交；提交一次一条任务，携带两个静态地址与工具归属。
  it("voice-clone replace mode uploads source + reference and submits once", async () => {
    const { container } = renderPage();
    await userEvent.click(screen.getByRole("button", { name: "人声复刻" }));
    // 默认文本合成模式：切到替换人声。
    await userEvent.click(screen.getByLabelText("替换人声"));

    // 替换人声提交按钮在未上传前 disabled。
    const submitBtn = screen.getByRole("button", { name: "开始替换人声" });
    expect(submitBtn).toBeDisabled();

    const fileInputs = container.querySelectorAll('input[type="file"]');
    // 第 1 个：原始音频/视频；第 2 个：参考音频（目标音色）。
    fireEvent.change(fileInputs[0], {
      target: { files: [new File(["o"], "original.mp4", { type: "video/mp4" })] },
    });
    // 仅上传一项时提交仍被拦下。
    await waitFor(() => expect(fileInputs.length).toBeGreaterThanOrEqual(2));
    expect(screen.getByRole("button", { name: "开始替换人声" })).toBeDisabled();
    expect(submitFreezoneVoiceReplace).not.toHaveBeenCalled();

    fireEvent.change(fileInputs[1], {
      target: { files: [new File(["r"], "timbre.mp3", { type: "audio/mpeg" })] },
    });

    await userEvent.click(screen.getByRole("button", { name: "开始替换人声" }));
    await waitFor(() => expect(submitFreezoneVoiceReplace).toHaveBeenCalledTimes(1));
    expect(submitFreezoneVoiceReplace).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        sourceUrl: "/static/x",
        referenceAudioUrl: "/static/x",
        referenceName: "timbre.mp3",
        archiveToLibrary: true,
        sourceTool: "voice-clone",
      }),
    );
  });

  // [aipdd fork delta] 视频配音：上传源视频 + 参考音频、选语言后提交一次；
  // 两者未齐备时提交 disabled；提交携带两个静态地址、language 与工具归属。
  it("video-dubbing uploads source + reference, picks a language and submits once", async () => {
    const { container } = renderPage();
    await userEvent.click(screen.getByRole("button", { name: "视频配音" }));

    // 未上传前提交按钮 disabled。
    expect(screen.getByRole("button", { name: "开始视频配音" })).toBeDisabled();

    const fileInputs = container.querySelectorAll('input[type="file"]');
    // 第 1 个：源视频；第 2 个：参考音频（目标音色）。
    fireEvent.change(fileInputs[0], {
      target: { files: [new File(["v"], "clip.mp4", { type: "video/mp4" })] },
    });
    // 只上传一项时提交仍被拦下。
    expect(screen.getByRole("button", { name: "开始视频配音" })).toBeDisabled();
    expect(submitFreezoneVideoDubbing).not.toHaveBeenCalled();

    fireEvent.change(fileInputs[1], {
      target: { files: [new File(["r"], "timbre.mp3", { type: "audio/mpeg" })] },
    });

    // 选一个语种（面板里唯一的原生 <select>：含 value="vi" 选项）。
    const languageSelect = Array.from(container.querySelectorAll("select")).find((s) =>
      Array.from(s.options).some((o) => o.value === "vi"),
    );
    expect(languageSelect).toBeTruthy();
    await userEvent.selectOptions(languageSelect!, "vi");

    await userEvent.click(screen.getByRole("button", { name: "开始视频配音" }));
    await waitFor(() => expect(submitFreezoneVideoDubbing).toHaveBeenCalledTimes(1));
    expect(submitFreezoneVideoDubbing).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        sourceUrl: "/static/x",
        referenceAudioUrl: "/static/x",
        language: "vi",
        archiveToLibrary: true,
        sourceTool: "video-dubbing",
      }),
    );
  });

  // [aipdd fork delta] 视频配音：两个上传未齐备时不得提交。
  it("video-dubbing keeps submit disabled until both uploads are ready", async () => {
    const { container } = renderPage();
    await userEvent.click(screen.getByRole("button", { name: "视频配音" }));

    const submitBtn = screen.getByRole("button", { name: "开始视频配音" });
    expect(submitBtn).toBeDisabled();

    const fileInputs = container.querySelectorAll('input[type="file"]');
    fireEvent.change(fileInputs[0], {
      target: { files: [new File(["v"], "clip.mp4", { type: "video/mp4" })] },
    });
    expect(submitBtn).toBeDisabled();
    expect(submitFreezoneVideoDubbing).not.toHaveBeenCalled();
  });

  // [aipdd fork delta] req32：替换人声喂的是本地音频，FunASR 只吃公网音频直链，
  // 下拉按后端下发的 acceptsLocalAudio（渠道目录声明的转写端点）把它挡掉；显式
  // 选中的候选随 asr_model 一起下发。
  it("voice-clone replace: ASR picker hides FunASR and submits the picked candidate", async () => {
    mediaModelsMock.current = [
      {
        id: "funasr:aipdd:aipdd_funasr_transcribe",
        kind: "funasr",
        provider: "aipdd",
        channel: "aipdd",
        model: "aipdd_funasr_transcribe",
        label: "FunASR",
        // [aipdd fork delta req40] 输入形态由后端按通道能力下发，前端不再拿 kind 反推。
        acceptsLocalAudio: false,
      },
      {
        id: "doubao_asr:openspeech:seedasr",
        kind: "doubao_asr",
        provider: "volcengine",
        channel: "volcengine",
        model: "seedasr",
        label: "豆包语音 · 录音文件识别",
        ready: true,
        acceptsLocalAudio: true,
      },
      {
        id: "gateway:aipdd:ap-funasr",
        kind: "gateway",
        provider: "aipdd",
        channel: "aipdd",
        model: "ap-funasr",
        label: "ap-funasr",
        acceptsLocalAudio: true,
      },
    ];
    const { container } = renderPage();
    await userEvent.click(screen.getByRole("button", { name: "人声复刻" }));
    await userEvent.click(screen.getByLabelText("替换人声"));

    // 默认「自动选择」：不显式挑候选也能提交（后端在执行时只挑就绪通道）。
    const asrPicker = screen.getByRole("combobox", { name: "台词识别（ASR）" });
    expect(asrPicker).toHaveTextContent("自动选择（优先已就绪通道）");

    await userEvent.click(asrPicker);
    expect(screen.queryByRole("option", { name: /FunASR/ })).not.toBeInTheDocument();
    await userEvent.click(await screen.findByRole("option", { name: /豆包语音/ }));
    expect(asrPicker).toHaveTextContent("豆包语音");

    const fileInputs = container.querySelectorAll('input[type="file"]');
    fireEvent.change(fileInputs[0], {
      target: { files: [new File(["o"], "original.mp4", { type: "video/mp4" })] },
    });
    await waitFor(() => expect(fileInputs.length).toBeGreaterThanOrEqual(2));
    fireEvent.change(fileInputs[1], {
      target: { files: [new File(["r"], "timbre.mp3", { type: "audio/mpeg" })] },
    });
    await userEvent.click(screen.getByRole("button", { name: "开始替换人声" }));
    await waitFor(() => expect(submitFreezoneVoiceReplace).toHaveBeenCalledTimes(1));
    expect(submitFreezoneVoiceReplace).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        sourceUrl: "/static/x",
        referenceAudioUrl: "/static/x",
        asrModel: "doubao_asr:openspeech:seedasr",
      }),
    );
  });

  // [aipdd fork delta] req32：显式选中未就绪的候选（火山语音凭证没配 / 网关侧
  // 没开 ASR 渠道）时，在提交按钮附近如实提醒；切回就绪候选或自动选择即消失。
  it("voice-clone replace: an unready ASR candidate warns near the submit button", async () => {
    mediaModelsMock.current = [
      {
        id: "doubao_asr:openspeech:seedasr",
        kind: "doubao_asr",
        provider: "volcengine",
        channel: "volcengine",
        model: "seedasr",
        label: "豆包语音 · 录音文件识别",
        ready: false,
      },
      {
        id: "gateway:aipdd:whisper-1",
        kind: "gateway",
        provider: "aipdd",
        channel: "aipdd",
        model: "whisper-1",
        label: "whisper-1",
        ready: true,
      },
    ];
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "人声复刻" }));
    await userEvent.click(screen.getByLabelText("替换人声"));

    const asrPicker = screen.getByRole("combobox", { name: "台词识别（ASR）" });
    expect(screen.queryByText(/尚未就绪/)).not.toBeInTheDocument();

    await userEvent.click(asrPicker);
    await userEvent.click(await screen.findByRole("option", { name: /豆包语音/ }));
    expect(screen.getByText(/所选 ASR 通道尚未就绪/)).toBeInTheDocument();

    await userEvent.click(asrPicker);
    await userEvent.click(await screen.findByRole("option", { name: /whisper-1/ }));
    expect(screen.queryByText(/尚未就绪/)).not.toBeInTheDocument();

    await userEvent.click(asrPicker);
    await userEvent.click(await screen.findByRole("option", { name: /自动选择/ }));
    expect(asrPicker).toHaveTextContent("自动选择（优先已就绪通道）");
  });

  it("switching to image-watermark shows the model picker and hint", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "图片去水印" }));
    expect(
      await screen.findByText("没有可用模型，请先到「设置 → 模型与渠道」配置该类型模型。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "建议选择支持图生图的图像模型（如 GPT Image 2、Seedream 4.5）；不确定就保持目录首项。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("处理指令")).toBeInTheDocument();
  });

  // [aipdd fork delta] 视频反推剧本：上传视频 + 选定视觉模型提交；候选为空时
  // 仍可按「跟随系统默认」提交（不带 model key）；未上传视频时阻断。
  it("video-reverse-script: uploads a video and submits with the chosen vision model", async () => {
    visionModelsMock.current = [
      {
        id: "volc::doubao-seed-1.6-vision",
        providerId: "newapi",
        apiModel: "doubao-seed-1.6-vision",
        label: "doubao-seed-1.6-vision",
        channel: "volc",
      },
    ];
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "视频反推剧本" }));
    // 未上传前提交被阻断。
    await userEvent.click(screen.getByRole("button", { name: "反推剧本" }));
    expect(submitFreezoneVideoScriptReverse).not.toHaveBeenCalled();

    // 上传走 /freezone/upload（mock 返回静态地址）。
    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]');
    expect(fileInput).not.toBeNull();
    fireEvent.change(fileInput as HTMLInputElement, {
      target: { files: [new File(["video"], "clip.mp4", { type: "video/mp4" })] },
    });
    await userEvent.click(screen.getByRole("combobox", { name: "视觉模型" }));
    await userEvent.click(
      screen.getByRole("option", { name: /doubao-seed-1\.6-vision/ }),
    );
    await userEvent.click(screen.getByRole("button", { name: "反推剧本" }));
    await waitFor(() => expect(submitFreezoneVideoScriptReverse).toHaveBeenCalledTimes(1));
    expect(submitFreezoneVideoScriptReverse).toHaveBeenCalledWith("demo", {
      videoUrl: "/static/x",
      model: "volc::doubao-seed-1.6-vision",
    });
  });

  it("video-reverse-script: submits without a model key when following the system default", async () => {
    // 候选目录为空不阻断：默认「跟随系统默认」提交，后端回退默认视觉链。
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "视频反推剧本" }));
    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]');
    expect(fileInput).not.toBeNull();
    fireEvent.change(fileInput as HTMLInputElement, {
      target: { files: [new File(["video"], "clip.mp4", { type: "video/mp4" })] },
    });
    await userEvent.click(screen.getByRole("button", { name: "反推剧本" }));
    await waitFor(() => expect(submitFreezoneVideoScriptReverse).toHaveBeenCalledTimes(1));
    expect(submitFreezoneVideoScriptReverse).toHaveBeenCalledWith("demo", {
      videoUrl: "/static/x",
    });
  });
  // [aipdd fork delta req46] 音乐创作：五模式各自的必填拦截 + 提交载荷逐模式形状。
  // 这里只钉「面板决定发什么」；模式 → 上游字段的拼装在后端 build_music_payload
  // 一处收口，前端不重复实现一遍，免得两处各说一套。
  it("music-create: appears in the menu, defaults to one-sentence and blocks an empty description", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "音乐创作" }));
    expect(screen.getByText("创作模式")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "一句话生成" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await userEvent.click(screen.getByRole("button", { name: "生成音乐" }));
    expect(submitFreezoneMusicCreate).not.toHaveBeenCalled();
  });

  it("music-create: one-sentence submits the picked model with the archive flag and never a lyrics key", async () => {
    musicModelsMock.current = [
      {
        id: "minimax::music-3.0",
        catalogId: "cat-music",
        providerId: "newapi",
        apiModel: "music-3.0",
        label: "music-3.0",
        channel: "minimax",
      },
    ];
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "音乐创作" }));
    // 目录就绪后预选首项（= 设置里的音乐默认模型），提交带 apiModel。
    expect(await screen.findByRole("combobox", { name: "音乐模型" })).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText(/轻快的夏日民谣/), {
      target: { value: "轻快的夏日民谣，副歌带合唱" },
    });
    await userEvent.click(screen.getByRole("button", { name: "生成音乐" }));
    await waitFor(() => expect(submitFreezoneMusicCreate).toHaveBeenCalledTimes(1));
    expect(submitFreezoneMusicCreate).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        mode: "one_sentence",
        prompt: "轻快的夏日民谣，副歌带合唱",
        model: "music-3.0",
        sampleRate: 44100,
        bitrate: 256000,
        audioFormat: "mp3",
        archiveToLibrary: true,
        sourceTool: "music-create",
      }),
    );
    const sent = (submitFreezoneMusicCreate as ReturnType<typeof vi.fn>).mock.calls[0][1];
    // 一句话模式的歌词由上游按描述生成：前端不发 lyrics，也没有参考音频。
    expect(sent).not.toHaveProperty("lyrics");
    expect(sent).not.toHaveProperty("referenceAudio");
  });

  it("music-create: lyrics mode requires the lyrics and keeps the description optional", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "音乐创作" }));
    await userEvent.click(screen.getByRole("tab", { name: "按歌词生成" }));
    const promptBox = screen.getByPlaceholderText(/鼓点干净/);
    fireEvent.change(promptBox, { target: { value: "流行摇滚" } });
    // 只填描述不填词：歌词模式的必填项是「词」，拦下来。
    await userEvent.click(screen.getByRole("button", { name: "生成音乐" }));
    expect(submitFreezoneMusicCreate).not.toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText(/\[Verse\]/), {
      target: { value: "[Verse]\n主歌一行\n\n[Chorus]\n副歌一行" },
    });
    // 描述留空 = 不发 prompt key（后端按选填处理，歌词模式可以只要词）。
    fireEvent.change(promptBox, { target: { value: "" } });
    await userEvent.click(screen.getByRole("button", { name: "生成音乐" }));
    await waitFor(() => expect(submitFreezoneMusicCreate).toHaveBeenCalledTimes(1));
    const sent = (submitFreezoneMusicCreate as ReturnType<typeof vi.fn>).mock.calls[0][1];
    expect(sent.mode).toBe("lyrics");
    expect(sent.lyrics).toContain("[Chorus]");
    expect(sent).not.toHaveProperty("prompt");
  });

  it("music-create: instrumental needs a description, hides the lyrics box and never sends lyrics", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "音乐创作" }));
    await userEvent.click(screen.getByRole("tab", { name: "纯音乐" }));
    await userEvent.click(screen.getByRole("button", { name: "生成音乐" }));
    expect(submitFreezoneMusicCreate).not.toHaveBeenCalled();
    // 纯音乐不给歌词输入（is_instrumental 下上游不看词，填了也只会被丢掉）。
    expect(screen.queryByPlaceholderText(/\[Verse\]/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText(/钢琴与弦乐的纯音乐/), {
      target: { value: "钢琴与弦乐的纯音乐，节奏舒缓" },
    });
    await userEvent.click(screen.getByRole("button", { name: "生成音乐" }));
    await waitFor(() => expect(submitFreezoneMusicCreate).toHaveBeenCalledTimes(1));
    const sent = (submitFreezoneMusicCreate as ReturnType<typeof vi.fn>).mock.calls[0][1];
    expect(sent.mode).toBe("instrumental");
    expect(sent).not.toHaveProperty("lyrics");
  });

  it("music-create: cover needs a reference audio and a 10+ character style description", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "音乐创作" }));
    await userEvent.click(screen.getByRole("tab", { name: "翻唱" }));
    const promptBox = screen.getByPlaceholderText(/唱成摇滚版本/);
    fireEvent.change(promptBox, { target: { value: "把这首歌唱成摇滚版本，鼓点更重" } });
    // 缺参考音频：拦。
    await userEvent.click(screen.getByRole("button", { name: "生成音乐" }));
    expect(submitFreezoneMusicCreate).not.toHaveBeenCalled();

    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]');
    expect(fileInput).not.toBeNull();
    fireEvent.change(fileInput as HTMLInputElement, {
      target: { files: [new File(["audio"], "origin.mp3", { type: "audio/mpeg" })] },
    });
    await waitFor(() => expect(screen.getByText("origin.mp3")).toBeInTheDocument());

    // 描述短于 10 字：上游会按参数错误拒掉，前端先拦（延续「详细诊断」口径）。
    fireEvent.change(promptBox, { target: { value: "改成摇滚" } });
    await userEvent.click(screen.getByRole("button", { name: "生成音乐" }));
    expect(submitFreezoneMusicCreate).not.toHaveBeenCalled();

    fireEvent.change(promptBox, { target: { value: "把这首歌唱成摇滚版本，鼓点更重" } });
    await userEvent.click(screen.getByRole("button", { name: "生成音乐" }));
    await waitFor(() => expect(submitFreezoneMusicCreate).toHaveBeenCalledTimes(1));
    const sent = (submitFreezoneMusicCreate as ReturnType<typeof vi.fn>).mock.calls[0][1];
    expect(sent.mode).toBe("cover");
    expect(sent.referenceAudio).toBe("/static/x");
    expect(sent.sourceName).toBe("origin.mp3");
    // 没抽过翻唱特征 = 一步翻唱：上游自己抽词，不带 cover_feature_id。
    expect(sent).not.toHaveProperty("coverFeatureId");
  });

  it("music-create: humming extracts the lyrics first, then submits the two-step cover", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "音乐创作" }));
    await userEvent.click(screen.getByRole("tab", { name: "哼唱成歌" }));
    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]');
    expect(fileInput).not.toBeNull();
    fireEvent.change(fileInput as HTMLInputElement, {
      target: { files: [new File(["hum"], "hum.wav", { type: "audio/wav" })] },
    });
    await waitFor(() => expect(screen.getByText("hum.wav")).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText(/哼唱做成/), {
      target: { value: "把这段哼唱做成轻快的民谣翻唱版本" },
    });
    await userEvent.click(screen.getByRole("button", { name: "抽取歌词（两步翻唱）" }));
    await waitFor(() => expect(submitFreezoneMusicCoverPreprocess).toHaveBeenCalledTimes(1));
    expect(submitFreezoneMusicCoverPreprocess).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({ referenceAudio: "/static/x" }),
    );
    // 抽出来的词回填输入框；用户改词后提交 = 带 coverFeatureId、不再带参考音频
    // （特征 id 与参考音频上游只认一个，重复交原曲只会白跑一遍预处理）。
    fireEvent.change(screen.getByPlaceholderText(/\[Verse\]/), {
      target: { value: "[Verse]\n改过的词" },
    });
    await userEvent.click(screen.getByRole("button", { name: "生成音乐" }));
    await waitFor(() => expect(submitFreezoneMusicCreate).toHaveBeenCalledTimes(1));
    const sent = (submitFreezoneMusicCreate as ReturnType<typeof vi.fn>).mock.calls[0][1];
    expect(sent.mode).toBe("humming");
    expect(sent.coverFeatureId).toBe("cf-1");
    expect(sent.lyrics).toBe("[Verse]\n改过的词");
    expect(sent).not.toHaveProperty("referenceAudio");
  });

  it("music-create: a single site channel offers no switch at all", async () => {
    musicModelsMock.current = [
      {
        id: "minimax::music-3.0",
        providerId: "newapi",
        apiModel: "music-3.0",
        label: "music-3.0",
        channel: "minimax",
      },
    ];
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "音乐创作" }));
    // 只有一个站点：不给切换器（两边 Key 不通用，多一个没用的开关只会误导）。
    await screen.findByRole("combobox", { name: "音乐模型" });
    expect(screen.queryByRole("combobox", { name: "站点渠道" })).not.toBeInTheDocument();
  });

  it("music-create: two site channels expose the switch, following the model by default", async () => {
    musicModelsMock.current = [
      {
        id: "minimax::music-3.0",
        providerId: "newapi",
        apiModel: "music-3.0",
        label: "music-3.0",
        channel: "minimax",
      },
      {
        id: "minimax_cn::music-3.0",
        providerId: "newapi",
        apiModel: "music-3.0",
        label: "music-3.0",
        channel: "minimax_cn",
      },
    ];
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "音乐创作" }));
    const trigger = await screen.findByRole("combobox", { name: "站点渠道" });
    // 默认「跟随所选模型」= 不发 provider，由后端从目录条目取所属站点。
    expect(trigger).toHaveValue("");
    expect(within(trigger).getByRole("option", { name: "跟随所选模型（默认）" })).toBeInTheDocument();
    expect(within(trigger).getByRole("option", { name: "MiniMax 国内站" })).toBeInTheDocument();
  });

  it("content-creation article panel submits, previews and degrades publish without credentials", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "内容创作" }));
    // 只有主题或素材之一非空时才能提交。
    expect(screen.getByRole("button", { name: "撰写并排版" })).toBeDisabled();
    await userEvent.type(
      screen.getByPlaceholderText("如：RAG 检索增强生成"),
      "RAG 检索增强生成",
    );
    await userEvent.click(screen.getByRole("button", { name: "撰写并排版" }));
    await waitFor(() => expect(submitFreezoneArticleWrite).toHaveBeenCalledTimes(1));
    expect(submitFreezoneArticleWrite).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        topic: "RAG 检索增强生成",
        style: "viewpoint",
        theme: "moyu-green",
        archiveToLibrary: true,
        sourceTool: "article-video",
      }),
    );
    // 提交后出现预览区：载入预览回读排版产物。
    await userEvent.click(await screen.findByRole("button", { name: "载入预览" }));
    await waitFor(() =>
      expect(fetchFreezoneArticleResult).toHaveBeenCalledWith("demo", "article-job-1"),
    );
    // 打开「排版后发布到公众号」开关：内联展示公众号侧需配置提示。
    await userEvent.click(
      await screen.findByRole("checkbox", { name: "排版后发布到公众号" }),
    );
    expect(screen.getByText(/发布需要使用你自己的公众号凭证/)).toBeInTheDocument();
    // 发布降级：未配置凭证时返回 needs_config，不抛错。
    await userEvent.click(screen.getByRole("button", { name: "创建草稿 / 发布" }));
    await waitFor(() =>
      expect(submitFreezoneArticlePublish).toHaveBeenCalledWith(
        "demo",
        expect.objectContaining({ articleJobId: "article-job-1" }),
      ),
    );
  });

  it("content-creation channel short video panel switches tab and submits per channel", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "内容创作" }));
    // 内容创作组默认落在第一项（文章）：切 tab 到「渠道短视频」。
    await userEvent.click(await screen.findByRole("tab", { name: "渠道短视频" }));
    // 无源时提交禁用；填参考素材后可提交。
    const submitBtn = screen.getByRole("button", { name: "生成渠道短视频" });
    expect(submitBtn).toBeDisabled();
    await userEvent.type(
      screen.getByPlaceholderText("粘贴要做成短视频的主题说明或文章正文（1-12000 字）"),
      "RAG 检索增强生成",
    );
    await userEvent.click(submitBtn);
    await waitFor(() =>
      expect(submitFreezoneChannelShortVideo).toHaveBeenCalledTimes(1),
    );
    expect(submitFreezoneChannelShortVideo).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        channel: "douyin",
        referenceContent: "RAG 检索增强生成",
        archiveToLibrary: true,
        sourceTool: "channel-short-video",
      }),
    );
  });

  it("narration smart edit panel uploads a source and submits with defaults", async () => {
    const { container } = renderPage();
    await userEvent.click(screen.getByRole("button", { name: "口播智能剪辑" }));
    // 未选源时提交禁用。
    const submitBtn = screen.getByRole("button", { name: "开始智能剪辑" });
    expect(submitBtn).toBeDisabled();
    // 源入口是面板内唯一的 file input（SingleRefInput）；上传桩回 /static/x 并回显「已选择」。
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(fileInput, {
      target: { files: [new File(["x"], "talk.mp4", { type: "video/mp4" })] },
    });
    await screen.findByText("已选择");
    await userEvent.click(submitBtn);
    await waitFor(() => expect(submitFreezoneNarrationSmartEdit).toHaveBeenCalledTimes(1));
    expect(submitFreezoneNarrationSmartEdit).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        videoUrl: "/static/x",
        outputMode: "both",
        aspect: "9:16",
        cutSilence: true,
        cutFillers: true,
        planBroll: true,
        genBgm: false,
        captions: true,
        archiveToLibrary: true,
        sourceTool: "narration-smart-edit",
      }),
    );
  });

  it("motion-blockbuster: picks a gallery case and renders it as one local shot", async () => {
    // [aipdd fork delta] 工具页复用案例图册 + 渲染端点：一条案例进来，出去一镜成片。
    // 没选案例不许提交（后端会 400），选了就只送 case_id/subject —— 页面源码由
    // 任务里服务端拼好的同一份契约提示词生成，前端不经手拼装。
    const motionCase = {
      id: "M43",
      label: "苹果风产品宣传片",
      category: "产品宣传片",
      brief: "把产品界面做成发布片",
      tags: [],
      prompt_cn: "分镜规格",
      cover: "M43/poster.webp",
      posterReady: false,
      previewReady: false,
      previewVersion: "",
      videoReady: false,
      resolution: "2880x2160",
      durationSeconds: 40,
      author: "x",
      mediaFile: "",
      mediaBytes: 0,
    };
    vi.mocked(listFreezoneMotionCases).mockResolvedValue({
      assetBase: "",
      version: "test",
      categories: ["产品宣传片"],
      methods: [],
      modelHint: "",
      renderContract: "渲染契约",
      source: "",
      cases: [motionCase],
    });

    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "动效大片" }));
    expect(screen.getByText("① 渲染一镜成片")).toBeInTheDocument();
    expect(screen.getByText("② 交付一盘剪映草稿")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "打开动效图册" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "渲染这一镜" }));
    expect(submitFreezoneMotionRender).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "打开动效图册" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "查看苹果风产品宣传片的动效规格" }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "用这条" }));

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "打开动效图册" })).not.toBeInTheDocument(),
    );
    expect(screen.getByText("苹果风产品宣传片")).toBeInTheDocument();
    // 提示词预览与任务用的是服务端拼好的同一份文本。
    expect(screen.getByText("查看这条案例的完整提示词")).toBeInTheDocument();

    await userEvent.type(
      screen.getByPlaceholderText("例：把 Distilbook 的阅读界面做成 40 秒发布片"),
      "阅读界面做成 40 秒发布片",
    );
    fireEvent.click(screen.getByRole("button", { name: "渲染这一镜" }));

    await waitFor(() =>
      expect(submitFreezoneMotionRender).toHaveBeenCalledWith("demo", {
        caseId: "M43",
        subject: "阅读界面做成 40 秒发布片",
        // 片名留空 → 回退案例名，资产库条目名不至于空着。
        title: "苹果风产品宣传片",
        modelText: undefined,
        aspectRatio: "16:9",
        resolution: "720p",
        fps: 30,
        durationS: null,
        archiveToLibrary: true,
        sourceTool: "motion-blockbuster",
      }),
    );
  });

  it("motion-blockbuster: exports beat stills before polishing the film", async () => {
    const motionCase = {
      id: "M43",
      label: "苹果风产品宣传片",
      category: "产品宣传片",
      brief: "把产品发布做成一页代码动画",
      tags: [],
      prompt_cn: "分镜规格",
      cover: "M43/poster.webp",
      posterReady: false,
      previewReady: false,
      previewVersion: "",
      videoReady: false,
      resolution: "2880x2160",
      durationSeconds: 40,
      author: "x",
      mediaFile: "",
      mediaBytes: 0,
    };
    vi.mocked(listFreezoneMotionCases).mockResolvedValue({
      assetBase: "",
      version: "test",
      categories: ["产品宣传片"],
      methods: [],
      modelHint: "",
      renderContract: "渲染契约",
      source: "",
      cases: [motionCase],
    });

    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "动效大片" }));

    // 渲染契约第 4 条排在成片之前：预检按钮就在「渲染这一镜」旁边。
    fireEvent.click(screen.getByRole("button", { name: "先按拍点出静帧" }));
    expect(submitFreezoneMotionStills).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "打开动效图册" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "查看苹果风产品宣传片的动效规格" }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "用这条" }));
    // 等图册关闭（案例真正落进面板状态）再提交，否则预检仍按「没选案例」被拦下。
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "打开动效图册" }),
      ).not.toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "先按拍点出静帧" }));

    await waitFor(() =>
      expect(submitFreezoneMotionStills).toHaveBeenCalledWith("demo", {
        caseId: "M43",
        subject: "",
        modelText: undefined,
        // 画幅/清晰度沿用同一份选择：预检要检的就是这一镜成片会用的画布。
        aspectRatio: "16:9",
        resolution: "720p",
        maxStills: 24,
        sourceTool: "motion-blockbuster",
      }),
    );
  });

  it("motion-blockbuster: shows the latest beat pre-check inside the panel", async () => {
    tasksMock.current = [
      {
        task_type: "freezone_motion_stills",
        status: "completed",
        created_at: "2026-10-19T01:00:00Z",
        result: {
          // 页面没写 window.EV：面板必须把「这张图对的是网格」说出来。
          beat_grid: true,
          page_url: "/static/freezone/_outputs/freezone_motion_stills/job-1/page.html",
          stills: [
            { at_seconds: 0, event_type: "open", url: "/static/beat-01.png" },
            { at_seconds: 1.5, event_type: "", url: "/static/beat-02.png" },
          ],
        },
      },
    ];

    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "动效大片" }));

    expect(screen.getByText("最近一次拍点预检（2 张）")).toBeInTheDocument();
    expect(screen.getByText(/等间隔网格/)).toBeInTheDocument();
    expect(screen.getByText("0.00s · open")).toBeInTheDocument();
    expect(screen.getByText("1.50s")).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: "拍点 0.00s 的静帧" }),
    ).toHaveAttribute("src", "/static/beat-01.png");
    expect(screen.getByRole("link", { name: "在浏览器打开这一页" })).toHaveAttribute(
      "href",
      "/static/freezone/_outputs/freezone_motion_stills/job-1/page.html",
    );
  });

  it("motion-blockbuster: renders the pasted page itself, with no case and no model rewrite", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "动效大片" }));

    const box = screen.getByPlaceholderText(
      "粘贴完整单文件 HTML：自带 window.render(t) 与 window.DUR",
    );
    // 两边都空才算没来源：既没选案例也没粘页面时不许提交。
    fireEvent.click(screen.getByRole("button", { name: "渲染这一镜" }));
    expect(submitFreezoneMotionRender).not.toHaveBeenCalled();

    const page = "<!doctype html><html><body>裸页面</body></html>";
    await userEvent.type(box, page);
    fireEvent.click(screen.getByRole("button", { name: "渲染这一镜" }));

    await waitFor(() =>
      expect(submitFreezoneMotionRender).toHaveBeenCalledWith("demo", {
        html: page,
        // 粘了页面就不再传案例：任务里那一步「让模型写这页」整个跳过，不出网。
        caseId: undefined,
        subject: "",
        // 没选案例就没拿得到案例名：片名留空，由后端按任务命名。
        title: "",
        modelText: undefined,
        aspectRatio: "16:9",
        resolution: "720p",
        fps: 30,
        durationS: null,
        archiveToLibrary: true,
        sourceTool: "motion-blockbuster",
      }),
    );

    // 预检读同一份来源：检的必须就是待会儿要渲的那一页。
    fireEvent.click(screen.getByRole("button", { name: "先按拍点出静帧" }));
    await waitFor(() =>
      expect(submitFreezoneMotionStills).toHaveBeenCalledWith("demo", {
        html: page,
        caseId: undefined,
        subject: "",
        modelText: undefined,
        aspectRatio: "16:9",
        resolution: "720p",
        maxStills: 24,
        sourceTool: "motion-blockbuster",
      }),
    );
  });

  it("motion-blockbuster: loads the newest page source into the editor for a second pass", async () => {
    tasksMock.current = [
      {
        task_type: "freezone_motion_render",
        status: "completed",
        created_at: "2026-10-19T02:00:00Z",
        result: {
          // 载入的是 .source.html（不带引导脚本），不是 Chrome 实际加载的那份。
          page_source_url:
            "/static/freezone/_outputs/freezone_motion_render/mg-1.source.html",
        },
      },
    ];
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response("<html>改过的页面</html>", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "动效大片" }));
    fireEvent.click(screen.getByRole("button", { name: "载入最近一次页面源码" }));

    await waitFor(() =>
      expect(
        screen.getByPlaceholderText(
          "粘贴完整单文件 HTML：自带 window.render(t) 与 window.DUR",
        ),
      ).toHaveValue("<html>改过的页面</html>"),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/static/freezone/_outputs/freezone_motion_render/mg-1.source.html",
      { cache: "no-store" },
    );
    vi.unstubAllGlobals();
  });

});
