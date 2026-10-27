// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [重构] 自 settings-dialog.tsx 物理拆分（纯移动，DOM/样式/i18n 不变）。
// 职责：模型网关设置区共享层：错误文案工具、能力/类型常量、ComfyUI 工具与官方站点链接派生。

// [aipdd fork delta] 模型名分类真源（前端）：guessMediaType / VISION_CAPABLE_RE 等，
// 与后端 media_model_discovery 镜像互钉。
import { type ModelKind } from "@/components/settings/model-classification";
import {
  type FeatureModelDef,
  type FeatureModelGroup,
} from "@/lib/feature-models";
// [aipdd fork delta] 渠道展示名与渠道类型兜底表都收敛在 @/lib/provider-labels
// （唯一真源，模型广场 / 画布渠道标注同用）。
import {
  FEATURE_PROVIDER_LABELS,
  forkBrandLabel,
} from "@/lib/provider-labels";
import {
  type GatewayMode,
  type NewApiChannelType,
  type SavedEmbeddingModelConfig,
  type SavedMediaModelConfig,
  type SavedProviderChannelConfig,
} from "@/lib/queries/model-gateway";
import { type FeatureModelProvider } from "@/stores/settingsStore";

const MODEL_CONFIGURATION_GUIDE_URL =
  "https://aiworkin.feishu.cn/wiki/QjAfwFtYbiqSAukMvU6cg5Kan2f?from=from_copylink";
// [aipdd fork delta] 外部 NewAPI 渠道的默认站点留空：自建网关 / 反向代理 / 云厂商
// 中转的地址各不相同，预填任何地址都等于替用户选了一个不属于他的站点。用户点卡上的
// 「推荐 NewAPI 站点」去官方整理的清单里自己挑（README 里给的是同一条链接）。
const NEWAPI_DEFAULT_BASE_URL = "";
const NEWAPI_RECOMMENDED_SITES_URL =
  "https://aiworkin.feishu.cn/wiki/F2WUwonOjikoDHkg1ERcDuPqnjf";
const COMFY_WORKFLOW_MANAGED_CONFIG_KEY = "_dcManagedByWorkflow";

// [aipdd fork delta] 去掉官方中转(RelayClaw)：设置页只暴露“自定义”网关（内置 dramaclaw-gateway），
// AIPDD / NewAPI(填 URL) / 本地 ComfyUI 作为它的上游渠道。official/hybrid 分支保留但不可达。
const GATEWAY_MODES: GatewayMode[] = ["custom"];

// [aipdd fork delta req41] 不再定义 DEFAULT_CUSTOM_NEWAPI_URL = "http://127.0.0.1:3000"：
// 网关在哪是部署方数据（compose 里是容器服务名、反代后是域名、源码跑又不同），
// 前端猜一个本机地址会在加载完成前把它填进表单，用户看到的是一个不属于他部署的
// “已填好”值。现在未配置就是空，真值只从 /config（服务端生效配置）来。

async function getRequestErrorMessage(
  error: unknown,
  fallback: string,
): Promise<string> {
  const response = (error as { response?: Response } | null)?.response;
  if (response) {
    const body = await response
      .clone()
      .json()
      .catch(() => null);
    if (body && typeof body === "object") {
      const data = body as {
        detail?: unknown;
        error?: unknown;
        message?: unknown;
      };
      for (const value of [data.detail, data.error, data.message]) {
        if (typeof value === "string" && value.trim()) return value.trim();
      }
    }
    const text = await response
      .clone()
      .text()
      .catch(() => "");
    if (text.trim()) return text.trim();
  }
  const message = (error as { message?: unknown } | null)?.message;
  return typeof message === "string" && message.trim()
    ? message.trim()
    : fallback;
}

function getResponseErrorMessage(response: unknown, fallback: string): string {
  if (response && typeof response === "object") {
    const data = response as {
      detail?: unknown;
      error?: unknown;
      message?: unknown;
    };
    for (const value of [data.detail, data.error, data.message]) {
      if (typeof value === "string" && value.trim()) return value.trim();
    }
    const payload = (data as { data?: unknown }).data;
    if (payload && typeof payload === "object") {
      const result = (payload as { result?: unknown }).result;
      if (result && typeof result === "object") {
        const error = (result as { error?: unknown }).error;
        if (typeof error === "string" && error.trim()) return error.trim();
      }
      const results = (payload as { results?: unknown }).results;
      if (Array.isArray(results)) {
        const failed = results.find(
          (item) =>
            item &&
            typeof item === "object" &&
            typeof (item as { error?: unknown }).error === "string" &&
            Boolean(((item as { error?: string }).error ?? "").trim()),
        ) as { error?: unknown } | undefined;
        if (typeof failed?.error === "string" && failed.error.trim()) {
          return failed.error.trim();
        }
      }
    }
    if (Array.isArray(data.detail)) {
      const first = data.detail.find(
        (item) => item && typeof item === "object",
      ) as { msg?: unknown } | undefined;
      if (typeof first?.msg === "string" && first.msg.trim())
        return first.msg.trim();
    }
  }
  return fallback;
}

/** [aipdd fork delta] 官方注册入口不能写死域名。
 * 站点地址以后端配置为准（数据库 / LOCALAPI_BASE_URL，随 model-gateway/config 下发，
 * 形如 https://gateway.example/v1），这里只取协议 + 域名当作首页；
 * 地址缺失或非法就返回空串，由调用方决定不渲染链接。 */
export function officialSiteUrl(baseUrl: string | undefined): string {
  const trimmed = (baseUrl || "").trim();
  if (!trimmed) return "";
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "";
    return parsed.origin;
  } catch {
    return "";
  }
}

// [aipdd fork delta] 「默认生成模型」：不再用「固定模型 ID + 选渠道」的反向表格，
// 而是图片 / 视频 / 音频 / 音乐 / 文本 / Embedding / 其它 七个 TAB，每个 TAB 只列出该类型的模型。
// 分类以「模型名关键词」为权威（修复存储里 mediaType 被写错导致的错分类，如
// ap-jimeng-image-4.0 被标成 audio）；仅当名字无法识别时才回退到已存 mediaType。
// 图片 / 视频 / 音频 / 音乐四类点「设为默认」写入 mediaModels（sortOrder 0 + 顺延同类旧默认，
// 驱动画布默认）；文本以 fallback 语义批量填充未单独配置的非视觉功能；视觉理解
// 以同样的 fallback 语义批量重指向未单独配置的 requiresVision 功能；Embedding
// 需内联填写维度后写入 embeddingModel。其它 TAB 仅展示不归类到上六种类型的模型，
// 无「设为默认」概念（包括 ComfyUI 手动指定为“其它”的模型）。
// 分类实现已抽到 ./model-classification（前端真源，与后端
// media_model_discovery.guess_model_kind / is_vision_capable 镜像互钉）。

const MEDIA_DEFAULT_TABS: Array<{ key: ModelKind; labelKey: string }> = [
  { key: "image", labelKey: "settings.modelConfig.modelList.tabs.image" },
  { key: "video", labelKey: "settings.modelConfig.modelList.tabs.video" },
  { key: "audio", labelKey: "settings.modelConfig.modelList.tabs.audio" },
  // [aipdd fork delta req46] 音乐：Music 3.0 / suno 一族。与音频分开——两者协议
  // 不同（配音走网关 audio/speech，音乐走厂商直连），混在一个 TAB 会让人把
  // 音乐模型设成配音默认。
  { key: "music", labelKey: "settings.modelConfig.modelList.tabs.music" },
  { key: "text", labelKey: "settings.modelConfig.modelList.tabs.text" },
  // [aipdd fork delta] 视觉理解：多模态理解模型的默认栏目，与「文本」同走 gateway
  // modelMapping，只是只重指向 requiresVision 功能（未单独配置者共用）。
  { key: "vision", labelKey: "settings.modelConfig.modelList.tabs.vision" },
  // [aipdd fork delta] Embedding 作为「默认生成模型」第五栏目：走 embeddingConfig
  // 通道（需先填向量维度），与「默认模型组合」的默认 Embedding 槽位对应。
  { key: "embedding", labelKey: "settings.modelConfig.modelList.tabs.embedding" },
  // [aipdd fork delta] 其它：仅列出上六种类型都没匹配到的模型，无「设为默认」。
  // ComfyUI 手动选为“其它”的模型也归到这里。
  { key: "other", labelKey: "settings.modelConfig.modelList.tabs.other" },
];

// 图片 / 视频 / 音频 / 音乐四类走 mediaModels 持久化；文本 / Embedding 走各自通道。
// [aipdd fork delta req46] music 同样走 mediaModels（设置页「音乐默认模型」），但
// **不经网关**：normalizeMediaConfig 不给它改写 endpoint（下面的分支只动 image/video），
// 调用走厂商直连协议（M3 的 music_generation）。
const MEDIA_SAVE_KINDS = ["image", "video", "audio", "music"] as const;
type MediaSaveKind = (typeof MEDIA_SAVE_KINDS)[number];
const isMediaSaveKind = (kind: ModelKind): kind is MediaSaveKind =>
  (MEDIA_SAVE_KINDS as readonly string[]).includes(kind);

// [aipdd fork delta] 修复 #3：把一个被错分类（存存储 mediaType / config.endpoint 与实际不符）
// 的模型设为某类型默认时，必须同步把 config.request.endpoint 改到匹配该类型，
// 否则后端 _build_media_model_channel_specs 会报「endpoint must be ...」并 400，
// 导致设默认默默失败、界面无回显。
function normalizeMediaConfig(
  config: Record<string, unknown> | undefined,
  mt: ModelKind,
): Record<string, unknown> {
  const next = { ...(config ?? {}) };
  if (mt !== "image" && mt !== "video") return next;
  const req =
    next.request && typeof next.request === "object" && !Array.isArray(next.request)
      ? { ...(next.request as Record<string, unknown>) }
      : {};
  req.endpoint = mt === "image" ? "images/generations" : "video/generations";
  if (!Array.isArray(req.parameters)) req.parameters = [];
  next.request = req;
  return next;
}

// [aipdd fork delta] 文本「默认模型」是全局兜底：功能未单独配置时使用的模型，
// 不是去覆盖各功能已设的模型。用一个保留 key 记录当前默认，避免把兜底
// 写进各功能行使其被当作「已配置」而再也改不动。
const TEXT_DEFAULT_KEY = "__TEXT_DEFAULT__";

// [aipdd fork delta] 视觉理解默认与文本默认同机制：全局兜底语义，用一个保留 key
// 记录当前默认视觉模型，避免把兜底写进各 requiresVision 功能行使其被锁死。
const VISION_DEFAULT_KEY = "__VISION_DEFAULT__";

// 功能模型映射每行的三列栅格：功能 | 供应商 | 模型名称。表头与行共用同一模板以对齐。
const FEATURE_ROW_GRID =
  "grid grid-cols-[minmax(0,1fr)_150px_minmax(0,1fr)] items-center gap-3";

function splitFeatureModelGroups(
  groups: readonly FeatureModelGroup[],
  predicate: (feature: FeatureModelDef) => boolean,
): FeatureModelGroup[] {
  return groups
    .map((group) => ({
      ...group,
      features: group.features.filter(predicate),
    }))
    .filter((group) => group.features.length > 0);
}

// [aipdd fork delta req40] 「媒体模型」预置行整表删除：以前前端自己写死一批厂商名当
// 行，渠道下架后这些行还在，用户照着配就得到指向死名的默认值。现在行清单只来自已保存
// 的媒体映射，映射里的名字只能从渠道目录（GET …/custom/newapi/provider-catalog）点选。

const MEDIA_ROW_GRID =
  "grid grid-cols-[70px_minmax(0,1fr)_130px_minmax(0,1fr)_80px] items-center gap-3";

const DEFAULT_EMBEDDING_DIMENSION = 1024;
const DEFAULT_EMBEDDING_BATCH_SIZE = 10;

function comfyNode(classType: string, inputs: Record<string, unknown>) {
  return { inputs, class_type: classType };
}

function minimaxH3Fl2vaWorkflow(firstFrame: boolean) {
  const generationInputs: Record<string, unknown> = {
    prompt: "",
    width: ["115", 0],
    height: ["115", 1],
    length: ["105:107", 1],
    clip: ["105:13", 0],
    vae: ["105:11", 0],
  };
  if (firstFrame) generationInputs.first_frame = ["114", 0];
  return {
    "92": comfyNode("SaveVideo", {
      filename_prefix: "video/MiniMax_H3",
      format: "auto",
      codec: "auto",
      video: ["105:91", 0],
    }),
    ...(firstFrame
      ? {
          "114": comfyNode("LoadImage", {
            image: "transparent_rgb_gaming_mouse.png",
          }),
          "119": comfyNode("ImageScaleToTotalPixels", {
            upscale_method: "nearest-exact",
            megapixels: 1,
            resolution_steps: 32,
          }),
          "120": comfyNode("GetImageSize", { image: ["119", 0] }),
        }
      : {}),
    "115": comfyNode("ResolutionSelector", {
      aspect_ratio: firstFrame ? "1:1 (Square)" : "16:9 (Widescreen)",
      megapixels: 0.4,
      multiple: 32,
    }),
    "105:11": comfyNode("VAELoader", {
      vae_name: "minimax_h3_video_vae_fp16.safetensors",
    }),
    "105:24": comfyNode("VAELoader", {
      vae_name: "minimax_h3_audio_vae_fp32.safetensors",
    }),
    "105:23": comfyNode("VAEDecodeAudio", {
      samples: ["105:14", 0],
      vae: ["105:24", 0],
    }),
    "105:10": comfyNode("VAEDecode", {
      samples: ["105:14", 0],
      vae: ["105:11", 0],
    }),
    "105:17": comfyNode("KSamplerSelect", { sampler_name: "res_multistep" }),
    "105:9": comfyNode("BasicScheduler", {
      scheduler: "simple",
      steps: 20,
      denoise: 1,
      model: ["105:6", 0],
    }),
    "105:14": comfyNode("SamplerCustomAdvanced", {
      noise: ["105:15", 0],
      guider: ["105:16", 0],
      sampler: ["105:17", 0],
      sigmas: ["105:9", 0],
      latent_image: ["105:104", 1],
    }),
    "105:16": comfyNode("BasicGuider", {
      model: ["105:6", 0],
      conditioning: ["105:104", 0],
    }),
    "105:6": comfyNode("UNETLoader", {
      unet_name: "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
      weight_dtype: "default",
    }),
    "105:13": comfyNode("CLIPLoader", {
      clip_name: "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
      type: "minimax",
      device: "default",
    }),
    "105:15": comfyNode("RandomNoise", {
      noise_seed: firstFrame ? 168866841893410 : 556589502035082,
    }),
    "105:91": comfyNode("CreateVideo", {
      fps: 24,
      bit_depth: 8,
      images: ["105:10", 0],
      audio: ["105:23", 0],
    }),
    "105:104": comfyNode("MiniMaxH3ImageToVideo", generationInputs),
    "105:107": comfyNode("ComfyMathExpression", {
      expression:
        "max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17",
      "values.a": ["105:111", 0],
    }),
    "105:111": comfyNode("PrimitiveFloat", { value: 5 }),
  };
}

function minimaxH3ReferenceWorkflow() {
  return {
    "92": comfyNode("SaveVideo", {
      filename_prefix: "video/MiniMax_H3",
      format: "auto",
      codec: "auto",
      video: ["130", 0],
    }),
    "115": comfyNode("ResolutionSelector", {
      aspect_ratio: "16:9 (Widescreen)",
      megapixels: 0.4,
      multiple: 32,
    }),
    "119": comfyNode("VAELoader", {
      vae_name: "minimax_h3_video_vae_fp16.safetensors",
    }),
    "120": comfyNode("VAELoader", {
      vae_name: "minimax_h3_audio_vae_fp32.safetensors",
    }),
    "121": comfyNode("VAEDecodeAudio", {
      samples: ["125", 0],
      vae: ["120", 0],
    }),
    "122": comfyNode("VAEDecode", { samples: ["125", 0], vae: ["119", 0] }),
    "123": comfyNode("KSamplerSelect", { sampler_name: "res_multistep" }),
    "124": comfyNode("BasicScheduler", {
      scheduler: "simple",
      steps: 20,
      denoise: 1,
      model: ["127", 0],
    }),
    "125": comfyNode("SamplerCustomAdvanced", {
      noise: ["129", 0],
      guider: ["126", 0],
      sampler: ["123", 0],
      sigmas: ["124", 0],
      latent_image: ["136", 1],
    }),
    "126": comfyNode("BasicGuider", {
      model: ["127", 0],
      conditioning: ["136", 0],
    }),
    "127": comfyNode("UNETLoader", {
      unet_name: "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
      weight_dtype: "default",
    }),
    "128": comfyNode("CLIPLoader", {
      clip_name: "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
      type: "minimax",
      device: "default",
    }),
    "129": comfyNode("RandomNoise", { noise_seed: 157368968253448 }),
    "130": comfyNode("CreateVideo", {
      fps: 24,
      bit_depth: 8,
      images: ["122", 0],
      audio: ["121", 0],
    }),
    "131": comfyNode("ComfyMathExpression", {
      expression:
        "max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17",
      "values.a": ["132", 0],
    }),
    "132": comfyNode("PrimitiveFloat", { value: 5 }),
    "136": comfyNode("MiniMaxH3ReferenceToVideo", {
      prompt: ["138", 0],
      width: ["115", 0],
      height: ["115", 1],
      length: ["131", 1],
      ref_image_size: "match",
      clip: ["128", 0],
      vae: ["119", 0],
      audio_vae: ["120", 0],
      "ref_images.ref_image_0": ["137", 0],
      "ref_images.ref_image_1": ["139", 0],
    }),
    "137": comfyNode("LoadImage", { image: "red_superboy_on_city_roof.png" }),
    "138": comfyNode("PrimitiveStringMultiline", { value: "" }),
    "139": comfyNode("LoadImage", { image: "mecha_dragon_lightning.png" }),
  };
}

const HYBRID_COMFYUI_WORKFLOWS = {
  model: "MiniMax-H3-local",
  workflows: {
    minimax_h3_t2v: minimaxH3Fl2vaWorkflow(false),
    minimax_h3_i2v: minimaxH3Fl2vaWorkflow(true),
    minimax_h3_r2v: minimaxH3ReferenceWorkflow(),
  },
};

const CUSTOM_PROVIDER_CHANNEL_TYPES: Readonly<Record<string, number>> = {
  comfyui: 63,
};

function customProviderChannelType(provider: string): number | undefined {
  return CUSTOM_PROVIDER_CHANNEL_TYPES[provider];
}

type ChannelCapability =
  | "text"
  | "vision"
  | "embedding"
  | "rerank"
  | "image"
  | "video"
  | "audio"
  // [aipdd fork delta req46] 渠道能力位新增 music（MiniMax 两条渠道都声明了它）：
  // 不登记就滤不到渠道，设不了音乐默认模型。
  | "music";

export function providersSupportingCapability(
  configuredProviders: readonly FeatureModelProvider[],
  channelTypeByProvider: ReadonlyMap<string, NewApiChannelType>,
  capability: ChannelCapability,
  currentProvider?: FeatureModelProvider,
): FeatureModelProvider[] {
  const supported = configuredProviders.filter((provider) => {
    const channelType = channelTypeByProvider.get(provider);
    // Keep the existing behavior when channel metadata is unavailable. Once
    // metadata is present, capabilities are authoritative.
    return !channelType || channelType.capabilities.includes(capability);
  });
  if (
    currentProvider &&
    configuredProviders.includes(currentProvider) &&
    !supported.includes(currentProvider)
  ) {
    return [currentProvider, ...supported];
  }
  return supported;
}

// [aipdd fork delta] volcengine（火山方舟 Ark）渠道固定 Base URL：该渠道的媒体模型
// 是 Seedance 系视频与 Seedream 系生图（生图经生效网关做 Ark 协议转换）。
// [aipdd fork delta] 下面三个端点是**本产品固定**的：AIPDD 自建网关、火山方舟
// Ark、火山 AI MediaKit。它们不随部署环境变，因此设置页不再让用户填 Base URL
// （填错反而会把已跑通的渠道拉坏），只读展示即可。真源在后端：
// AIPDD → newapi_provisioner.py 的 base_url，MediaKit →
// model_gateway_settings.MEDIAKIT_DEFAULT_BASE_URL（留空时落回该默认值）。
const ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3";
const AIPDD_BASE_URL = "https://api.aipdd.work/v1";
const MEDIAKIT_BASE_URL = "https://mediakit.cn-beijing.volces.com/api/v1";

// [aipdd fork delta req46] MiniMax 两个站点的官方端点（海外 api.minimax.io / 国内
// api.minimax.cn）：同属「渠道定了端点就定了」的公共厂商地址，只作为连接卡里的
// **可改预填值**——官方另有 api.minimaxi.com / api.minimax.chat 同栈可达，且用户可能
// 走自建代理，所以这里不像 AIPDD 那样锁成只读。真源在后端 PROVIDER_PRESETS。
const MINIMAX_BASE_URL = "https://api.minimax.io/v1";
const MINIMAX_CN_BASE_URL = "https://api.minimax.cn/v1";

// [aipdd fork delta req48] OpenAI / Claude / Gemini 三家官方端点：同属「渠道定了
// 端点就定了」的公共厂商地址，只作为连接卡里的**可改预填值**（与 MiniMax 同口径问
// 自建代理 / 镜像的用户可以改）。真源在后端 PROVIDER_PRESETS 与
// channels.py 的 _FORK_CHANNEL_TYPE_OVERRIDES。
// Gemini 的 REST 根路径是 /v1beta（不是 /v1）：后端 normalize_channel_base_url 也
// 按这个补，两边不一致就会拉到空目录。
const OPENAI_OFFICIAL_BASE_URL = "https://api.openai.com/v1";
const ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1";
const GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta";

// [aipdd fork delta] 用户常误把完整请求端点（如 …/api/v3/chat/completions）当作
// Base URL 填入。渠道 Base URL 只应到版本路径为止，网关会自行拼接
// /chat/completions、/embeddings 等；故保存前去掉这些后缀与尾部多余斜杠。
function normalizeProviderBaseUrl(url: string): string {
  return url
    .trim()
    .replace(
      /\/(chat\/completions|completions|embeddings|responses|images\/generations|video\/generations|videos\/generations)\/?$/i,
      "",
    )
    .replace(/\/+$/, "")
    .trim();
}

function featureModelBackendSnapshotKey(
  providerChannels: readonly SavedProviderChannelConfig[],
  mediaModels: Record<string, SavedMediaModelConfig>,
  embeddingModel: SavedEmbeddingModelConfig | undefined,
): string {
  return JSON.stringify({
    providerChannels,
    mediaModels,
    embeddingModel: embeddingModel ?? null,
  });
}

// [aipdd fork delta] 供应商渠道展示名 override：品牌名 / FEATURE_PROVIDER_LABELS
// 均来自 @/lib/provider-labels（唯一真源，模型广场与画布渠道标注同用），优先于
// NewAPI 渠道类型名。aipdd 统一显示 AIPDD；provider=openai 是用户附加的「其它
// 额外渠道」（非默认五家），显示 OpenAI。mediakit 按界面语言取词（zh 火山 AI
// MediaKit / en・vi Volcengine AI MediaKit）。其余渠道仍取其自身渠道名。
function featureProviderLabel(
  provider: string,
  channelTypes?: ReadonlyMap<string, NewApiChannelType>,
): string {
  return (
    forkBrandLabel(provider) ||
    channelTypes?.get(provider)?.name ||
    FEATURE_PROVIDER_LABELS[provider] ||
    provider
  );
}

function readComfyUIWorkflows(
  settings: Record<string, unknown>,
): Record<string, Record<string, unknown>> {
  const comfyui = settings.comfyui;
  if (!comfyui || typeof comfyui !== "object" || Array.isArray(comfyui))
    return {};
  const config = comfyui as Record<string, unknown>;
  const routes = config.workflow_routes;
  if (Array.isArray(routes)) {
    return Object.fromEntries(
      routes.flatMap((route) => {
        if (!route || typeof route !== "object" || Array.isArray(route))
          return [];
        const item = route as Record<string, unknown>;
        const id = typeof item.id === "string" ? item.id.trim() : "";
        const workflow = item.workflow;
        if (
          !id ||
          !workflow ||
          typeof workflow !== "object" ||
          Array.isArray(workflow)
        )
          return [];
        return [[id, workflow as Record<string, unknown>]];
      }),
    );
  }
  const workflows = config.workflow_by_model;
  if (!workflows || typeof workflows !== "object" || Array.isArray(workflows))
    return {};
  return Object.fromEntries(
    Object.entries(workflows).filter(
      (entry): entry is [string, Record<string, unknown>] =>
        Boolean(entry[0]) &&
        Boolean(entry[1]) &&
        typeof entry[1] === "object" &&
        !Array.isArray(entry[1]),
    ),
  );
}

function readComfyUIModelName(settings: Record<string, unknown>): string {
  const comfyui = settings.comfyui;
  if (!comfyui || typeof comfyui !== "object" || Array.isArray(comfyui))
    return "";
  const config = comfyui as Record<string, unknown>;
  if (typeof config.model_name === "string" && config.model_name.trim()) {
    return config.model_name.trim();
  }
  if (Array.isArray(config.workflow_routes)) {
    for (const route of config.workflow_routes) {
      if (!route || typeof route !== "object" || Array.isArray(route)) continue;
      const match = (route as Record<string, unknown>).match;
      if (!match || typeof match !== "object" || Array.isArray(match)) continue;
      const models = (match as Record<string, unknown>).models;
      if (Array.isArray(models) && typeof models[0] === "string") {
        const model = models[0].trim();
        if (model) return model;
      }
    }
  }
  const legacyModels = Object.keys(readComfyUIWorkflows(settings));
  if (legacyModels.length === 1) return legacyModels[0];
  if (
    legacyModels.length > 1 &&
    legacyModels.every((model) => /^minimax[_-]h3(?:[_-]|$)/i.test(model))
  ) {
    return "MiniMax-H3-local";
  }
  return legacyModels[0] ?? "";
}

function buildComfyUIWorkflowRoutes(
  workflows: Record<string, Record<string, unknown>>,
): Record<string, unknown>[] {
  return Object.entries(workflows).map(([id, workflow]) => ({
    id,
    match: {},
    workflow,
  }));
}

function normalizeProviderChannelSettings(
  provider: string,
  settings: Record<string, unknown>,
): Record<string, unknown> {
  if (provider !== "comfyui") return settings;
  const comfyui =
    settings.comfyui &&
    typeof settings.comfyui === "object" &&
    !Array.isArray(settings.comfyui)
      ? (settings.comfyui as Record<string, unknown>)
      : {};
  if (Array.isArray(comfyui.workflow_routes)) return settings;
  const workflows = readComfyUIWorkflows(settings);
  const workflowIds = Object.keys(workflows);
  const isLegacyH3Group =
    workflowIds.length > 1 &&
    workflowIds.every((id) => /^minimax[_-]h3(?:[_-]|$)/i.test(id));
  if (workflowIds.length !== 1 && !isLegacyH3Group) return settings;
  const model = readComfyUIModelName(settings);
  if (!model) return settings;
  const normalized = { ...comfyui };
  delete normalized.workflow_by_model;
  delete normalized.workflow;
  normalized.model_name = model;
  normalized.workflow_routes = buildComfyUIWorkflowRoutes(workflows);
  return { ...settings, comfyui: normalized };
}

export {
  getRequestErrorMessage,
  getResponseErrorMessage,
  featureProviderLabel,
  GATEWAY_MODES,
  MEDIA_DEFAULT_TABS,
  MEDIA_SAVE_KINDS,
  isMediaSaveKind,
  normalizeMediaConfig,
  TEXT_DEFAULT_KEY,
  VISION_DEFAULT_KEY,
  FEATURE_ROW_GRID,
  splitFeatureModelGroups,
  MEDIA_ROW_GRID,
  DEFAULT_EMBEDDING_DIMENSION,
  DEFAULT_EMBEDDING_BATCH_SIZE,
  comfyNode,
  minimaxH3Fl2vaWorkflow,
  minimaxH3ReferenceWorkflow,
  HYBRID_COMFYUI_WORKFLOWS,
  CUSTOM_PROVIDER_CHANNEL_TYPES,
  customProviderChannelType,
  ARK_BASE_URL,
  AIPDD_BASE_URL,
  MEDIAKIT_BASE_URL,
  MINIMAX_BASE_URL,
  MINIMAX_CN_BASE_URL,
  OPENAI_OFFICIAL_BASE_URL,
  ANTHROPIC_BASE_URL,
  GEMINI_BASE_URL,
  normalizeProviderBaseUrl,
  featureModelBackendSnapshotKey,
  readComfyUIWorkflows,
  readComfyUIModelName,
  buildComfyUIWorkflowRoutes,
  normalizeProviderChannelSettings,
  COMFY_WORKFLOW_MANAGED_CONFIG_KEY,
  MODEL_CONFIGURATION_GUIDE_URL,
  NEWAPI_DEFAULT_BASE_URL,
  NEWAPI_RECOMMENDED_SITES_URL,
};
export type {
  MediaSaveKind,
  ChannelCapability,
};
