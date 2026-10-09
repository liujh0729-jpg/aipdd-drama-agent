// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 供应商渠道展示名统一 override（唯一真源）：
// 默认四渠道：aipdd→AIPDD、newapi→NewAPI（用户自配的 OpenAI 兼容外部
// 网关）、volcengine→VolcEngine、mediakit→火山 AI MediaKit（归属 VolcEngine 渠道，
// 只是 Base URL / API Key 独立一份；en/vi 界面按语言取词显示 Volcengine AI MediaKit，
// 见 localizedBrandName；与后端 model_plaza.py 的 _FORK_PROVIDER_LABELS
// 逐项对齐）；openai 是用户附加的「其它额外渠道」（不在默认四渠道之列）→ 显示
// OpenAI，与裸 provider id 相区分；其余渠道取其自身渠道名（channelTypes / 内置
// 映射）。设置面板（featureProviderLabel）与模型广场（ModelPlazaPage）等渲染点
// 都从这里取，后端 model_plaza.py 的 _FORK_PROVIDER_LABELS 与此表保持一致。

import i18n from "i18next";

export const FORK_PROVIDER_LABELS: Record<string, string> = {
  aipdd: "AIPDD",
  newapi: "NewAPI",
  // 「其它额外渠道」（用户附加在 NewAPI 网关上的非默认渠道）：独立显示
  // OpenAI，不与自配网关 newapi→NewAPI 混同。
  openai: "OpenAI",
  volcengine: "VolcEngine",
  // 中文界面用官方中文子产品线名；en/vi 按界面语言取词（见 localizedBrandName），
  // 取词失败时以此值为兜底，绝不把 raw key 漏到界面上。
  mediakit: "火山 AI MediaKit", // i18n-exempt 中文品牌兜底值，主取值走 locale `brand.mediakit`
  // [aipdd fork delta req46] MiniMax 双站：海外站是 ASCII 品牌名（不跟语言走），
  // 国内站要区分「同一家厂商的另一条渠道」，中文界面写「MiniMax 国内站」更直白，
  // 因此进 LOCALIZED_BRANDS 按界面语言取词（locale `brand.minimax_cn`）。
  minimax: "MiniMax",
  minimax_cn: "MiniMax CN", // 兜底值，主取值走 locale `brand.minimax_cn`
  // [aipdd fork delta req48] OpenAI / Claude / Gemini 三家官方渠道（纯 ASCII 品牌
  // 名，不跟界面语言走）。openai_official 展示 OpenAI，与上面那个「其它额外渠道」
  // 的裸 openai 分得开；Claude 是厂商面（provider key 仍是 anthropic）的产品名。
  // 与后端 channels.py 的渠道目录行名、model_plaza._FORK_PROVIDER_LABELS 逐项对齐。
  openai_official: "OpenAI",
  anthropic: "Claude",
  gemini: "Gemini",
};

// [aipdd fork delta] 需要按界面语言取词的品牌（其余 fork 渠道本来就是 ASCII 名，
// 不跟语言走）。只对表内渠道发起 t()，避免给 i18next 刷一堆 missing key 噪音。
const LOCALIZED_BRANDS: ReadonlySet<string> = new Set(["mediakit", "minimax_cn"]);

function brandText(provider: string, field: "name" | "description"): string {
  const raw = String(provider || "").trim().toLowerCase();
  if (!LOCALIZED_BRANDS.has(raw)) return "";
  // mediakit → brand.mediakit / brand.mediakitDescription
  const key =
    field === "name" ? `brand.${raw}` : `brand.${raw}Description`;
  const translated = i18n.t(key);
  // key 没翻（测试里的精简实例、或拼错）时 i18next 原样回显 key，必须判掉。
  if (typeof translated !== "string" || !translated || translated === key) return "";
  return translated;
}

/** 需要按界面语言覆盖的后端下发文本（如渠道名）：非本地化品牌返回空串。 */
export function localizedBrandName(provider: string): string {
  return brandText(provider, "name");
}

/** fork 品牌表的取词版：命中表内渠道返回品牌名，未收录返回空串。 */
export function forkBrandLabel(provider: string): string {
  const key = String(provider || "").trim().toLowerCase();
  return localizedBrandName(key) || FORK_PROVIDER_LABELS[key] || "";
}

/** 后端渠道目录里写死的中文描述，同理按界面语言覆盖。 */
export function localizedBrandDescription(provider: string): string {
  return brandText(provider, "description");
}

/** 仅做 fork override；设置面板的完整链路再叠加 channelTypes / 内置兜底映射。 */
export function forkProviderLabel(provider: string): string {
  return forkBrandLabel(provider) || provider;
}

// [aipdd fork delta] NewAPI 渠道类型名兜底表：设置面板「渠道 API Key」里按渠道
// 类型展示的英文名（自 NewAPI channel types 精简而来）。原在 settings-dialog.tsx
// 内部维护，画布模型选择器的渠道标注也要用同一张表，故收敛到本文件——渠道展示
// 名的唯一真源，勿再散落到各渲染点。
// i18n-exempt-start —— 服务商品牌名，不跟界面语言走
export const FEATURE_PROVIDER_LABELS: Record<string, string> = {
  openai: "OpenAI",
  midjourney: "Midjourney",
  azure: "Azure",
  ollama: "Ollama",
  midjourneyplus: "MidjourneyPlus",
  openaimax: "OpenAIMax",
  ohmygpt: "OhMyGPT",
  custom: "Custom",
  ails: "AILS",
  aiproxy: "AIProxy",
  palm: "PaLM",
  api2gpt: "API2GPT",
  aigc2d: "AIGC2D",
  anthropic: "Anthropic",
  baidu: "Baidu",
  zhipu: "Zhipu",
  ali: "Ali",
  xunfei: "Xunfei",
  "360": "360",
  openrouter: "OpenRouter",
  aiproxylibrary: "AIProxyLibrary",
  fastgpt: "FastGPT",
  tencent: "Tencent",
  gemini: "Gemini",
  moonshot: "Moonshot",
  zhipuv4: "ZhipuV4",
  perplexity: "Perplexity",
  lingyiwanwu: "LingYiWanWu",
  aws: "AWS",
  cohere: "Cohere",
  minimax: "MiniMax",
  sunoapi: "SunoAPI",
  dify: "Dify",
  jina: "Jina",
  cloudflare: "Cloudflare",
  siliconflow: "SiliconFlow",
  vertexai: "VertexAI",
  mistral: "Mistral",
  deepseek: "DeepSeek",
  mokaai: "MokaAI",
  volcengine: "VolcEngine",
  baiduv2: "BaiduV2",
  xinference: "Xinference",
  xai: "xAI",
  coze: "Coze",
  kling: "Kling",
  jimeng: "Jimeng",
  vidu: "Vidu",
  submodel: "Submodel",
  doubaovideo: "DoubaoVideo",
  doubao_audio: "DoubaoAudio",
  sora: "Sora",
  replicate: "Replicate",
  codex: "Codex",
};
// i18n-exempt-end

// [aipdd fork delta] 上面两张表没收录、但会出现在「默认生成模型」渠道字段里的自有
// 渠道品牌名（纯 ASCII，不跟界面语言走）。
export const CHANNEL_BRAND_LABELS: Record<string, string> = {
  huimeng: "HuiMeng",
  seedance: "Seedance",
  comfyui: "ComfyUI",
  eleven: "ElevenLabs",
  mureka: "Mureka",
};

/**
 * 画布渠道标注用展示名：fork override → NewAPI 渠道类型名 → 自有品牌名 →
 * 原样回显。传入空串返回空串（调用方据此不渲染标注）。
 */
export function providerChannelLabel(provider: string): string {
  const raw = String(provider || "").trim();
  if (!raw) return "";
  const key = raw.toLowerCase();
  return (
    forkBrandLabel(key) ||
    FEATURE_PROVIDER_LABELS[key] ||
    CHANNEL_BRAND_LABELS[key] ||
    raw
  );
}

// 渠道配色：chart-1..5 是 DESIGN.md 批准的唯一数据序列色，全部经 index.css 的
// theme token 取色（不硬编码 hex）；未登记的渠道回落中性灰，绝不猜色。
const CHANNEL_BADGE_TONES: Record<string, string> = {
  // NewAPI 渠道及其它额外渠道（openai）：青
  newapi: "bg-chart-1/16 text-chart-1",
  openai: "bg-chart-1/16 text-chart-1",
  // AIPDD 直连渠道：天蓝
  aipdd: "bg-chart-4/16 text-chart-4",
  // 火山 / Seedance 系：绿
  volcengine: "bg-chart-5/16 text-chart-5",
  seedance: "bg-chart-5/16 text-chart-5",
  // 火山 AI MediaKit：钢蓝（渠道归属 VolcEngine，但作为子产品线保留自有色以便识别）
  mediakit: "bg-chart-3/16 text-chart-3",
  // 绘梦：青绿
  huimeng: "bg-chart-2/16 text-chart-2",
  // OpenRouter：钢蓝
  openrouter: "bg-chart-3/16 text-chart-3",
  // [aipdd fork delta req48] 三家官方渠道：OpenAI 与 newapi 同族（青），Claude 好
  // 绘梦同色（青绿），Gemini 走钢蓝——都只从 DESIGN.md 批准的 chart-1..5 取色。
  openai_official: "bg-chart-1/16 text-chart-1",
  anthropic: "bg-chart-2/16 text-chart-2",
  gemini: "bg-chart-3/16 text-chart-3",
  // ComfyUI：琥珀色警示
  comfyui: "bg-warning/16 text-warning",
};

const CHANNEL_BADGE_NEUTRAL_CLASS = "bg-white/[0.08] text-text-muted";

/** 渠道胶囊配色（淡色底 + 同色文字）；未登记的渠道走中性样式。 */
export function providerChannelBadgeClass(provider: string): string {
  const key = String(provider || "").trim().toLowerCase();
  return CHANNEL_BADGE_TONES[key] || CHANNEL_BADGE_NEUTRAL_CLASS;
}

// [aipdd fork delta] 渠道彩色胶囊的胶囊体样式，与展示名 / 配色同表（唯一真源）：
// 胶囊组件 ModelChannelBadge（components/model-picker）从这里取样式，画布与
// 工具页共用。
export const MODEL_CHANNEL_BADGE_CLASS =
  "inline-flex h-4 shrink-0 items-center rounded-full px-1.5 text-[10px] font-medium leading-none";

/** channel 缺省时回退 providerId（旧数据的执行身份可能是已重命名的 openai）。 */
export function channelForDisplay(model: {
  channel?: string;
  providerId: string;
}): string {
  return model.channel !== undefined ? model.channel : model.providerId;
}

// [aipdd fork delta] 展示用模型名去渠道前缀（唯一真源）：复合调用名
// （"aipdd::ap-glm-5.2"）只显示模型真名——渠道来源已有单独一栏（彩色胶囊 /
// 侧注），把 "aipdd::" 暴露给用户只会徒增噪音；调用值（id / apiModel）仍保留
// 完整复合名，后端可执行性不变。
// channel 传入时精确剥「channel::」前缀（大小写不敏感）；无渠道线索时退化为
// 「取最后一段」口径（历史行为：Agent 输入区早前实现）。
export function displayModelName(name: string, channel?: string | null): string {
  const raw = String(name ?? "").trim();
  if (!raw) return raw;
  const separator = "::";
  const known = String(channel ?? "").trim();
  if (known) {
    const prefix = `${known}${separator}`;
    if (raw.toLowerCase().startsWith(prefix.toLowerCase())) {
      const bare = raw.slice(prefix.length).trim();
      if (bare) return bare;
    }
  }
  const index = raw.lastIndexOf(separator);
  const bare = index >= 0 ? raw.slice(index + separator.length).trim() : raw;
  return bare || raw;
}