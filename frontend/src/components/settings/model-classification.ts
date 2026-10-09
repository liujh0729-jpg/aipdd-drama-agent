// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 模型名分类真源（前端侧）：设置页「默认生成模型」面板的归类
// 口径。后端唯一对应实现是 media_model_discovery.guess_model_kind /
// is_vision_capable（newapi_provisioner.model_supports_vision 也委托它）；两侧是
// 同一组启发式，修改时必须同步钉住镜像用例：
//   后端 tests/test_media_model_discovery_catalog.py
//   前端 src/__tests__/components/settings/model-classification.test.ts
// 任一侧漂移都会让两组用例失败。

// [aipdd fork delta] vision：多模态视觉理解模型（图片/视频输入），驱动所有
// requiresVision 功能的默认，与「文本」同走 gateway modelMapping 生效通道。
// [aipdd fork delta req46] music：音乐生成（Music 3.0 / suno 一族）。它与配音走不同
// 协议（厂商直连 music_generation，不经网关 audio/speech），必须独立成类，
// 否则「音频默认模型」会指向一个根本没法合成语音的条目。
export type ModelKind =
  | "image"
  | "video"
  | "audio"
  | "music"
  | "text"
  | "vision"
  | "embedding"
  | "other";

// [aipdd fork delta] 与后端 media_model_discovery.MEDIA_TYPE_PATTERNS 同步：
// 顺序即优先级（embedding → music → audio → video → image → text）。
export const MEDIA_TYPE_PATTERNS: Array<{ type: ModelKind; re: RegExp }> = [
  {
    type: "embedding",
    re: /(embedding|embed|bge[-_]|m3e|gte[-_]?embedding|jina[-_]?embed|voyage|text[-_]embedding|oe[-_]embedding)/i,
  },
  {
    // [aipdd fork delta req46] 排在 audio 之前：audio 的 \baudio\b 会抢走带 audio
    // 字样的音乐名（两侧镜像必须同一位置，否则 TAB 与后端存储会分家）。
    // [aipdd fork delta req48] 补 lyria：Gemini 清单里的 Lyria 系列是真正的音乐模型。
    type: "music",
    re: /(\bmusic|\bsuno\b|\blyrics?\b|\blyria\b|audiocraft)/i,
  },
  {
    // [aipdd fork delta req48] 补 whisper / transcri：OpenAI 官方清单里的
    // whisper-1 系转写模型过去一个都不命中 audio，只能落 other。
    type: "audio",
    re: /(tts|indextts|cosyvoice|fireredasr|fish[-_]?(speech)?|\baudio\b|\bspeech\b|\bvoice\b|elevenlabs|\bbark\b|doubao[-_]audio|whisper|transcri)/i,
  },
  {
    type: "video",
    re: /(seedance|sora|kling|vidu|pixverse|hailuo|cogvideo|animereels|mochi|runway|\bpika\b|hunyuan[-_]?video|\bveo\d?\b|\bltx\b|\bh3\b|wan[-_]?(i2v|t2v|2)|flf2v|image[-_]to[-_]video|text[-_]to[-_]video|\bi2v\b|\bt2v\b|\br2v\b|\bvideo\b)/i,
  },
  {
    type: "image",
    re: /(image|seedream|jimeng|dall[-_]?e|\bimagen\b|flux|sdxl|\bsd[-_]?3?\b|stable[-_]diffusion|midjourney|nano[-_]?banana|firefly|recraft|qwen[-_]?(image|vl)|grok[-_]?image|text[-_]to[-_]image|\bt2i\b|\bi2i\b)/i,
  },
  {
    type: "text",
    // [aipdd fork delta] minimax M 系列（M1/M2/M3…）是文本/LLM（多模态输入、文本输出），
    // 与视频侧的 minimax H 系列（Hailuo，走上面 \bh3\b）区分开，避免 ap-minimax-m3 被误判。
    re: /(gpt|chatgpt|claude|gemini|qwen|deepseek|llama|hermes|\bllm\b|mistral|\bglm\b|minimax[-_ ]?m\d|moonshot|kimi|\bdoubao\b|baichuan|internlm|ernie|wenxin|text[-_]generation|\bchat\b|completion|\bphi[-_\d]|command[-_]?r|senova|\byi[-_]?\d)/i,
  },
];

// [aipdd fork delta] 视觉候选资格（与后端 media_model_discovery.is_vision_capable
// 同步），含 doubao-seed 多模态系列——实测 ap-doubao-seed-2.1-turbo 能理解图像与
// 时序帧；漏掉它会让「视觉理解」TAB 选不到该模型，后端校验/自检也会误报。
export const VISION_CAPABLE_RE =
  /vision|\bvlm\b|\bqvq\b|video[-_]?understanding|multi[-_]?modal|\bomni\b|[-_]vl[-_\d]|[-_]vl\b|doubao[-_.]?seed(?![a-zA-Z])/i;

// [aipdd fork delta req48] 非生成类工具模型（OpenAI 官方清单里的
// omni-moderation-latest 等内容审核接口）：名字里的 omni 会命中上面的视觉资格正则，
// 把它们摆进「视觉理解」候选 —— 点下去只会拿到一个不接图审核接口。这类名字一律
// 落 other（不进任何 TAB）。与后端 media_model_discovery._UTILITY_ONLY_NAME 同步。
export const UTILITY_ONLY_RE = /moderation/i;

/** 视觉候选资格（与后端 is_vision_capable 同口径）：裸正则要先过工具模型护栏。 */
export function isVisionCapable(model: string): boolean {
  const s = String(model || "");
  if (UTILITY_ONLY_RE.test(s)) return false;
  return VISION_CAPABLE_RE.test(s);
}

export function guessMediaType(model: string): ModelKind {
  const s = String(model || "");
  // [aipdd fork delta] 视觉理解模型先行归类（doubao-*-vision / *-vl / vlm / qvq /
  // multimodal / omni 等），且早于下面的 LLM→text：使 DC-freezone-vision-LLM 这类
  // 带 LLM 标记的视觉 alias、以及 doubao-*-vision 这类被 text 的 \bdoubao\b 抢走的
  // 名字，都能正确落入「视觉理解」TAB。纯文本 LLM 无这些关键词，仍归 text。
  // 这里用不带 doubao-seed 的主分类正则（seed 系列主分类仍是 text，看后端
  // media_model_discovery._VISION_MODEL_NAME 注释）；候选资格另有 VISION_CAPABLE_RE。
  if (
    !UTILITY_ONLY_RE.test(s) &&
    /vision|\bvlm\b|\bqvq\b|video[-_]?understanding|multi[-_]?modal|\bomni\b|[-_]vl[-_\d]|[-_]vl\b/i.test(
      s,
    )
  )
    return "vision";
  // [aipdd fork delta] 与后端 media_model_discovery._TEXT_ONLY_NAME 同步：带 LLM 标记的
  // 名字一定是语言模型，先归 text，避免 DC-seedance2-prompt-composer-LLM /
  // DC-video-identity-detector-LLM 被下面的 video/image 关键词抢走分类。
  if (/llm|language[-_]model/i.test(s)) return "text";
  for (const { type, re } of MEDIA_TYPE_PATTERNS) {
    if (re.test(s)) return type;
  }
  return "other";
}