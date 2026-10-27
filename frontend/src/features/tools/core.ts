// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 项目「工具」栏目：可批量执行的功能。全部复用现有
// freezone 提交函数与任务中心，不新增上游模型、不新建独立任务存储。
// 左侧菜单条目由 TOOLS 现算（TOOL_NAV）：同类功能可以合并成一个入口、在入口内
// 切换提交类型（见 TOOL_GROUPS），菜单顺序完全跟随 TOOLS，不维护第二份顺序清单。

/** 工具标识。 */
export type ToolKind =
  // [aipdd fork delta] 短视频口播稿创作（菜单首项）：处境 + 素材 → 能直接念的逐字稿。
  // 方法论蒸馏自开源技能包「太上老君·AI炼丹炉」（CC BY-NC 4.0，不可商用）。
  | "oral-script"
  | "video-clone"
  | "video-mix"
  // [aipdd fork delta] 口播智能剪辑：一条口播视频→本地静音剪 + 网关 ASR/文本瑕疵剪
  // 与分镜/B-roll 判定 + 生图/生乐→直出 MP4 或 VectCut 剪映草稿（或两者）。
  | "narration-smart-edit"
  // [aipdd fork delta] 白板动画：口播文案→分镜→逐幕线稿→逐笔揭示成片（纯本地渲染）。
  | "whiteboard-animation"
  // [aipdd fork delta] 科普讲解视频：参考资料→分章解说词→分镜 DSL→逐帧 MG 成片。
  | "explainer-video"
  // [aipdd fork delta] 内容创作·公众号文章：主题/素材→风格化 Markdown→公众号合规 HTML（+ 可选发布）。
  // 该工具是「文章编写 + 渠道短视频」组合面板的第一步（Phase 2A 只做文章）。
  | "article-video"
  // [aipdd fork delta] 内容创作·渠道短视频：文章/素材→复用讲解视频渲染管线，按渠道
  // 定制画幅/时长/字幕/封面（Phase 2B）；产出该渠道规格 MP4 + 封面 + 文案建议，不自动上传。
  | "channel-short-video"
  | "video-idea"
  | "voice-clone"
  // [aipdd fork delta] 视频配音：源视频→原语言重声（参考音色）→回填替换整条音轨。
  | "video-dubbing"
  // [aipdd fork delta req46] 音乐创作：MiniMax Music 3.0 五模式（一句话/歌词/纯音乐/翻唱/哼唱）。
  | "music-create"
  | "lip-sync"
  | "replace-person"
  | "replace-product"
  | "video-watermark"
  | "image-watermark"
  // [aipdd fork delta] req21 泛化：音视频提取文案（链接 / 上传视频 / 上传音频）。
  | "media-copywrite"
  // [aipdd fork delta] 视频反推剧本：上传视频 → 视觉模型（用户可选）→ txt 剧本。
  | "video-reverse-script"
  // [aipdd fork delta] 换脸直播：本机边车引擎（摄像头→inswapper 换脸→系统虚拟摄像头→OBS），
  // 面板只做上传人脸/改参/启停的 localhost 控制，Web 不推流、无批量任务。
  | "face-swap-live"
  // [aipdd fork delta] 动效大片：案例图册/自贴页面 → 随包 Chrome 逐帧渲染 MG 成片 →
  // 逐镜成片导出剪映 10.2 草稿。渲染与编码全在本机，不出网。
  | "motion-blockbuster"
  // 旧 kind 常量保留：历史任务的 metadata.source_tool / 过滤仍可能引用。
  | "douyin-copywrite";

/** 归一化（0–1）框选区域，对应 freezone_video_erase 的 box。 */
export interface NormalizedBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** 框选最小边长（归一化），小于此值视为误点不保留。 */
export const MIN_BOX_SIZE = 0.01;

export function clamp01(value: number): number {
  return Math.min(1, Math.max(0, value));
}

/** 以两个归一化点为对角生成包围盒（裁剪到 0–1）。 */
export function rectFromCorners(
  a: { x: number; y: number },
  b: { x: number; y: number },
): NormalizedBox {
  const left = clamp01(Math.min(a.x, b.x));
  const top = clamp01(Math.min(a.y, b.y));
  const right = clamp01(Math.max(a.x, b.x));
  const bottom = clamp01(Math.max(a.y, b.y));
  return {
    x: left,
    y: top,
    width: Math.max(0, right - left),
    height: Math.max(0, bottom - top),
  };
}

/** 平移框，保证整框不超出 0–1。 */
export function shiftRect(
  box: NormalizedBox,
  dx: number,
  dy: number,
): NormalizedBox {
  return {
    ...box,
    x: Math.min(Math.max(0, box.x + dx), Math.max(0, 1 - box.width)),
    y: Math.min(Math.max(0, box.y + dy), Math.max(0, 1 - box.height)),
  };
}

/** 拖右下角手柄缩放：以 (right, bottom) 为新右下顶点，钳制在范围内。 */
export function resizeRectTo(
  box: NormalizedBox,
  right: number,
  bottom: number,
): NormalizedBox {
  return {
    ...box,
    width: Math.max(MIN_BOX_SIZE, Math.min(1 - box.x, right - box.x)),
    height: Math.max(MIN_BOX_SIZE, Math.min(1 - box.y, bottom - box.y)),
  };
}

/** 批量输入的单条素材。 */
export interface BatchItem {
  /** 后端可解析的静态地址（上传或素材库返回的 URL）。 */
  url: string;
  name: string;
  kind: "image" | "video" | "audio";
}

/** 单个工具能接收的批量素材类型。 */
export type AcceptKind = "image" | "video" | "audio" | "video-or-image";

/** 工具读取的模型目录类型；video / image / audio 三类真实目录。
 * [aipdd fork delta] "vision" = 网关视觉理解候选（/model-gateway/vision-models），
 * 由面板自带下拉消费，不走 ModelPicker 的媒体目录。 */
export type ToolModelKind = "video" | "image" | "audio" | "music" | "vision";

export interface ToolDef {
  kind: ToolKind;
  /** i18n key：标题。 */
  titleKey: string;
  /** i18n key：说明。 */
  descKey: string;
  /** 模型选择器读哪个目录（视频复刻 / 对口型 / 换人 / 视频去水印=video，人声复刻=audio，图片去水印=image）。 */
  modelKind: ToolModelKind;
  /** 批量输入接收的素材类型；voice-clone 以文本为主，无媒体批量输入。 */
  accept: AcceptKind | null;
  /** 本工具提交后产生的后端 task_type 集合，用于任务列表过滤。 */
  taskTypes: string[];
  /**
   * [aipdd fork delta] 所属菜单分组。挂了分组的工具**不再单独占一条菜单**，
   * 只在分组入口里作为「类型」出现；kind / taskTypes / sourceTool 全部保持独立，
   * 历史任务的归属过滤（后端 metadata.source_tool）与任务中心命名不受合并影响。
   */
  group?: ToolGroupId;
}

/**
 * 菜单分组：一个入口下并存的多种提交类型。
 *
 * 只有「同类不同产物」的功能才进来 —— 组内每种类型仍是独立 kind、独立任务类型、
 * 独立提交端点，合并的只是入口位置与命名层级；不是把两套参数并成一套表单。
 */
export type ToolGroupId = "explainer-class" | "content-creation";

export interface ToolGroupDef {
  id: ToolGroupId;
  /** i18n key：分组入口的菜单标题。 */
  titleKey: string;
  /** i18n key：分组说明（讲清「同一类成片的几种方案」，不重复各类型自己的 desc）。 */
  descKey: string;
  /** i18n key：组内类型切换器的无障碍标签（说清「在选哪一种」）。 */
  typeLabelKey: string;
  /** 首次点进分组时默认展开的类型。 */
  defaultKind: ToolKind;
}

export const TOOL_GROUPS: Record<ToolGroupId, ToolGroupDef> = {
  "explainer-class": {
    id: "explainer-class",
    titleKey: "tools.explainerClass.title",
    descKey: "tools.explainerClass.desc",
    typeLabelKey: "tools.explainerClass.typeLabel",
    defaultKind: "whiteboard-animation",
  },
  "content-creation": {
    id: "content-creation",
    titleKey: "tools.contentCreation.title",
    descKey: "tools.contentCreation.desc",
    typeLabelKey: "tools.contentCreation.typeLabel",
    defaultKind: "article-video",
  },
};

// [aipdd fork delta] 菜单顺序即本数组顺序（TOOL_NAV 由此现算）：短视频口播稿置顶、
// 内容创作组次之、音视频提取文案第三，音乐创作摆在最后，其余保持原有相对顺序。
export const TOOLS: ToolDef[] = [
  // [aipdd fork delta] 短视频口播稿创作（菜单首项）：一次文本生成产出六节成稿 +
  // 净台词 txt，不入资产库（与 media-copywrite 的 txt 同口径）。写作走面板自管的
  // 文本模型下拉（modelKind=audio 不适用），无媒体批量输入，故 accept 置 null。
  {
    kind: "oral-script",
    titleKey: "tools.oralScript.title",
    descKey: "tools.oralScript.desc",
    modelKind: "audio",
    accept: null,
    taskTypes: ["freezone_oral_script"],
  },
  // [aipdd fork delta] 内容创作族（菜单第二项）：公众号文章编写与排版（Phase 2A）与
  // 后续渠道短视频（Phase 2B）共用一个「内容创作」菜单入口。文章产物为结果 JSON +
  // 公众号合规 HTML（可选发布到草稿箱）；面板内自管文本模型（modelKind=audio 不适用，
  // 走面板自管 LLM 下拉），无媒体批量输入，故 accept 置 null。
  {
    kind: "article-video",
    titleKey: "tools.articleVideo.title",
    descKey: "tools.articleVideo.desc",
    modelKind: "audio",
    accept: null,
    taskTypes: ["freezone_article_write"],
    group: "content-creation",
  },
  // [aipdd fork delta] Phase 2B 渠道短视频：与文章共用「内容创作」入口的第二步，
  // 走讲解视频同一 HyperFrames 渲染管线但按渠道定制成片规格；面板自管文本模型下拉、
  // 配音走 audio 目录，无媒体批量输入，accept 置 null。taskTypes/sourceTool 各自独立。
  {
    kind: "channel-short-video",
    titleKey: "tools.channelShortVideo.title",
    descKey: "tools.channelShortVideo.desc",
    modelKind: "audio",
    accept: null,
    taskTypes: ["freezone_channel_short_video"],
    group: "content-creation",
  },
  {
    kind: "media-copywrite",
    titleKey: "tools.mediaCopywrite.title",
    descKey: "tools.mediaCopywrite.desc",
    // [aipdd fork delta] req21 音视频提取文案（菜单第二项）：由抖音文案提取泛化而来。
    // 输入是链接或上传的视频/音频（非批量素材），ASR 模型来自专用候选端点（网关模型
    // list 过滤 + 内置 FunASR / 豆包），不走 ModelPicker 目录，也不拆批量任务——
    // 一次提交解析一条媒体。上传控件在面板内自管（按来源模式选 video/audio），
    // 不走共享批量输入，故 accept 置 null（与旧抖音卡一致）。
    modelKind: "audio",
    accept: null,
    taskTypes: ["freezone_media_copywrite", "freezone_douyin_copywrite"],
  },
  {
    kind: "video-clone",
    titleKey: "tools.videoClone.title",
    descKey: "tools.videoClone.desc",
    // 逐镜复刻生成走视频模型目录；frames 模式需模型支持图像参考。
    modelKind: "video",
    accept: "video",
    taskTypes: ["freezone_video_clone"],
  },
  {
    kind: "video-mix",
    titleKey: "tools.videoMix.title",
    descKey: "tools.videoMix.desc",
    // 素材池（图/视频）→ 文案 → 配音 → 字幕 → 本地 ffmpeg 混剪；模型仅配音用。
    modelKind: "audio",
    accept: "video-or-image",
    taskTypes: ["freezone_video_mix"],
  },
  // [aipdd fork delta] 口播智能剪辑：一条口播视频→多阶段流水线。
  // 静音剪本地、瑕疵剪与分镜/B-roll 走网关 ASR/文本、B-roll 生图与 BGM 生乐走网关，
  // 输出直出 MP4 / VectCut 剪映草稿。源为单条上传/项目视频（面板自管），无批量素材输入。
  {
    kind: "narration-smart-edit",
    titleKey: "tools.narrationSmartEdit.title",
    descKey: "tools.narrationSmartEdit.desc",
    modelKind: "video",
    accept: "video",
    taskTypes: ["freezone_narration_smart_edit"],
  },
  {
    kind: "video-idea",
    titleKey: "tools.videoIdea.title",
    descKey: "tools.videoIdea.desc",
    // [aipdd fork delta] 一句话生成短视频：创意→故事→角色→分镜→逐镜全自动成片。
    // 面板内双模型选择（肖像/帧读 image 目录、逐镜读 video 目录），无媒体批量输入。
    modelKind: "video",
    accept: null,
    taskTypes: ["freezone_video_idea"],
  },
  {
    kind: "voice-clone",
    titleKey: "tools.voiceClone.title",
    descKey: "tools.voiceClone.desc",
    modelKind: "audio",
    accept: null,
    taskTypes: ["freezone_audio_speech"],
  },
  // [aipdd fork delta] 视频配音：摆在人声复刻之后。源视频→抽原台词（不翻译）→
  // 参考音色原语言重声→回填替换整条音轨→ mp4 成片。合成走 audio 目录（与 voice-clone
  // 同口径），源视频/参考音频在面板内自管，不是批量素材，故 accept 置 null。
  {
    kind: "video-dubbing",
    titleKey: "tools.videoDubbing.title",
    descKey: "tools.videoDubbing.desc",
    modelKind: "audio",
    accept: null,
    taskTypes: ["freezone_video_dubbing"],
  },
  {
    kind: "lip-sync",
    titleKey: "tools.lipSync.title",
    descKey: "tools.lipSync.desc",
    modelKind: "video",
    accept: "video-or-image",
    // [aipdd fork delta] 后端 omni-gen 路由实际产出 freezone_video_gen（
    // "freezone_video_omni_gen" 是历史幽灵类型，按它会过滤不到任务记录）。
    taskTypes: ["freezone_video_gen"],
  },
  {
    kind: "replace-person",
    titleKey: "tools.replacePerson.title",
    descKey: "tools.replacePerson.desc",
    modelKind: "video",
    accept: "video",
    // [aipdd fork delta] 同 lip-sync：实际 task_type 是 freezone_video_gen。
    taskTypes: ["freezone_video_gen"],
  },
  {
    kind: "replace-product",
    titleKey: "tools.replaceProduct.title",
    descKey: "tools.replaceProduct.desc",
    modelKind: "video",
    accept: "video",
    // [aipdd fork delta] 同 replace-person：实际 task_type 是 freezone_video_gen。
    taskTypes: ["freezone_video_gen"],
  },
  {
    kind: "video-watermark",
    titleKey: "tools.videoWatermark.title",
    descKey: "tools.videoWatermark.desc",
    // [aipdd fork delta] 视频去水印双路径：默认引擎=本地擦除（freezone_video_erase）；
    // 选定具体模型=AI 视频编辑（freezone_video_gen），目录按 video_edit 能力过滤。
    modelKind: "video",
    accept: "video",
    taskTypes: ["freezone_video_erase", "freezone_video_gen"],
  },
  {
    kind: "image-watermark",
    titleKey: "tools.imageWatermark.title",
    descKey: "tools.imageWatermark.desc",
    modelKind: "image",
    accept: "image",
    // [aipdd fork delta] 图片工具实际产出 freezone_edit（"freezone_redraw" 是历史幽灵类型）。
    taskTypes: ["freezone_edit"],
  },
  {
    kind: "video-reverse-script",
    titleKey: "tools.videoReverseScript.title",
    descKey: "tools.videoReverseScript.desc",
    // [aipdd fork delta] 视频反推剧本：上传视频→抽帧→视觉模型反推纯文本剧本。
    // 视觉候选来自 /model-gateway/vision-models（面板自管下拉，留空 = 后端默认
    // 视觉链）；一次提交解析一条视频，上传控件在面板内自管，不走共享批量输入，
    // 故 accept 置 null（与 media-copywrite 同口径）。产物 txt 不覆盖故事脚本 JSON。
    modelKind: "vision",
    accept: null,
    taskTypes: ["freezone_video_script_reverse"],
  },
  // [aipdd fork delta] 换脸直播：本机边车引擎控制面板。无媒体批量输入、无后端 freezone
  // 任务（视频走 摄像头→边车→系统虚拟摄像头→OBS，不经浏览器/任务中心），故 accept 置
  // null、taskTypes 置空；modelKind 占位（面板不调 ModelPicker）。就绪门禁在面板内自管。
  {
    kind: "face-swap-live",
    titleKey: "tools.faceSwapLive.title",
    descKey: "tools.faceSwapLive.desc",
    modelKind: "video",
    accept: null,
    taskTypes: [],
  },
  // [aipdd fork delta] 动效大片：摆在讲解类视频之前（同一族「代码画出来的成片」，
  // 但 MG 走随包 Chrome 逐帧 + ffmpeg，讲解类走 HyperFrames 管线，故不合并进分组）。
  // 一次提交出一镜成片，面板内第二步把已渲染成片导成剪映草稿；页面由网关文本模型
  // 按图册渲染契约产出，画幅档位与后端 FreezoneMotionRenderRequest 同一份集合。
  // 无媒体批量输入（一次提交一条成片），modelKind 占位：文本模型由面板自管，
  // 与 video-reverse-script 的 vision 占位同口径。
  {
    kind: "motion-blockbuster",
    titleKey: "tools.motionBlockbuster.title",
    descKey: "tools.motionBlockbuster.desc",
    modelKind: "video",
    accept: null,
    taskTypes: [
      "freezone_motion_stills",
      "freezone_motion_render",
      "freezone_motion_draft",
    ],
  },
  // [aipdd fork delta] 讲解类视频：白板手绘逐笔揭示 与 分章 MG 动画是同一类讲解
  // 成片的两种画面方案，共用一个菜单入口（TOOL_GROUPS.explainer-class）、摆在菜单
  // 靠后（仅音乐创作在后）；两者 taskTypes / sourceTool 依旧各自独立，历史任务
  // 过滤与命名不受合并影响。
  {
    kind: "whiteboard-animation",
    titleKey: "tools.whiteboardAnimation.title",
    descKey: "tools.whiteboardAnimation.desc",
    // 白板动画：文本分镜 + 逐幕线稿出图 + 本地逐笔揭示 + 配音拼接。
    // 面板内三模型选择（分镜 LLM / 线稿 image 目录 / 配音 audio 目录），
    // 无媒体批量输入（一次提交只产出一条成片），modelKind 取配音目录。
    modelKind: "audio",
    accept: null,
    taskTypes: ["freezone_whiteboard_animation"],
    group: "explainer-class",
  },
  {
    kind: "explainer-video",
    titleKey: "tools.explainerVideo.title",
    descKey: "tools.explainerVideo.desc",
    // 科普讲解视频：参考资料→分章解说词→分镜 DSL→逐帧代码绘制 MG + 配音。
    // 画面不调图像/视频模型，只用网关的文本与音频两类能力；
    // 面板内双模型选择（文本 LLM / 配音 audio 目录），无媒体批量输入。
    modelKind: "audio",
    accept: null,
    taskTypes: ["freezone_explainer_video"],
    group: "explainer-class",
  },
  // [aipdd fork delta req46] 音乐创作：菜单最后一项。模型不经网关——候选来自设置页
  // mediaType=music 的目录，产物 mp3/wav 走 freezone_music_generate 任务并入库
  // 「待分类资产」。参考音频在面板内自管（上传或素材库单条），不是批量素材，
  // 故 accept 置 null（与 voice-clone 同口径）。
  {
    kind: "music-create",
    titleKey: "tools.musicCreate.title",
    descKey: "tools.musicCreate.desc",
    modelKind: "music",
    accept: null,
    taskTypes: ["freezone_music_generate"],
  },
];

/** 左侧菜单的一条入口：单个工具，或一个分组（组内多类型在入口里切换）。 */
export type ToolNavEntry =
  | { type: "tool"; kind: ToolKind; titleKey: string }
  | { type: "group"; id: ToolGroupId; titleKey: string; members: ToolKind[] };

function buildToolNav(tools: ToolDef[]): ToolNavEntry[] {
  const out: ToolNavEntry[] = [];
  const emitted = new Set<ToolGroupId>();
  for (const tool of tools) {
    if (!tool.group) {
      out.push({ type: "tool", kind: tool.kind, titleKey: tool.titleKey });
      continue;
    }
    // 分组的成员在 TOOLS 里不相邻时，入口也只在该组第一个成员的位置出现一次。
    if (emitted.has(tool.group)) continue;
    emitted.add(tool.group);
    out.push({
      type: "group",
      id: tool.group,
      titleKey: TOOL_GROUPS[tool.group].titleKey,
      members: toolGroupMembers(tool.group),
    });
  }
  return out;
}

/** 菜单名册：由 TOOLS 现算，不手写第二份顺序清单（菜单要往下就改 TOOLS 的顺序）。 */
export const TOOL_NAV: ToolNavEntry[] = buildToolNav(TOOLS);

/** 该工具所属的菜单分组（无分组回 null）。 */
export function toolGroupOf(kind: ToolKind): ToolGroupDef | null {
  const group = toolByKind(kind).group;
  return group ? TOOL_GROUPS[group] : null;
}

/** 分组下的类型列表（菜单里的显示顺序 = TOOLS 里的声明顺序）。 */
export function toolGroupMembers(groupId: ToolGroupId): ToolKind[] {
  return TOOLS.filter((tool) => tool.group === groupId).map((tool) => tool.kind);
}

export function toolByKind(kind: ToolKind): ToolDef {
  const tool = TOOLS.find((t) => t.kind === kind);
  if (!tool) throw new Error(`unknown tool ${kind}`);
  return tool;
}

/** 依据 MIME 粗判上传文件的素材类型。 */
export function kindFromFile(file: File): BatchItem["kind"] | null {
  const type = (file.type || "").toLowerCase();
  if (type.startsWith("video/")) return "video";
  if (type.startsWith("image/")) return "image";
  if (type.startsWith("audio/") || /\.(mp3|wav|m4a|aac|ogg|flac)$/i.test(file.name)) {
    return "audio";
  }
  return null;
}

/** 以并发上限逐个跑提交，返回每个任务句柄（保持输入顺序）。 */
export async function runBatch<TIn, TOut>(
  items: TIn[],
  limit: number,
  worker: (item: TIn, index: number) => Promise<TOut>,
): Promise<TOut[]> {
  const results: TOut[] = new Array(items.length);
  let cursor = 0;
  const runners = Array.from({ length: Math.min(Math.max(limit, 1), items.length) }, async () => {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      results[index] = await worker(items[index], index);
    }
  });
  await Promise.all(runners);
  return results;
}

/**
 * 从任务 result（后端 free-form）里尽力找出一个可打开的媒体地址。
 * 递归遍历对象/数组，返回第一个 http(s)/static 且带媒体扩展名的字符串。
 */
export function firstMediaUrlInResult(result: unknown): string | null {
  const seen = new Set<unknown>();
  const isUrl = (value: string): boolean => {
    if (!/^(https?:)?\/\//.test(value) && !value.startsWith("/static/")) return false;
    // [aipdd fork delta] txt = 抖音文案提取的文本产物；html = 公众号文章排版产物
    // （任务列表「打开结果」直接打开合规 HTML 预览）。
    return /\.(mp4|mov|webm|mp3|wav|m4a|png|jpe?g|webp|gif|txt|html)(\?|$)/i.test(value);
  };
  const walk = (node: unknown): string | null => {
    if (node == null || seen.has(node)) return null;
    if (typeof node === "string") return isUrl(node) ? node : null;
    if (typeof node !== "object") return null;
    seen.add(node);
    if (Array.isArray(node)) {
      for (const item of node) {
        const found = walk(item);
        if (found) return found;
      }
      return null;
    }
    for (const value of Object.values(node as Record<string, unknown>)) {
      const found = walk(value);
      if (found) return found;
    }
    return null;
  };
  return walk(result);
}

/**
 * 任务 result 里是否带工具页归档标记。
 *
 * 后端 runner 在「待分类资产」登记成功后写入 ``archived_to_library``；画布等
 * 未带归档标记的入口（即使 task_type 相同）不会命中，因此用它区分是否展示
 * 「已存入资产库」提示。
 */
export function wasArchivedToLibrary(result: unknown): boolean {
  if (result == null || typeof result !== "object" || Array.isArray(result)) {
    return false;
  }
  return (result as Record<string, unknown>).archived_to_library === true;
}
