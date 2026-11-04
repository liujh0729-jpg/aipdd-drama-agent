import type { ResolvedConfig } from './config.js'
import { requestJson } from './http.js'

export type ModelKind = 'text' | 'image' | 'video' | 'tts'

interface ModelsResponse {
  data?: Array<{ id?: string; name?: string }>
  models?: Array<{ id?: string; name?: string } | string>
}

const cache = new Map<string, { at: number; models: string[] }>()
const CACHE_TTL_MS = 10 * 60 * 1000

export async function listModels(cfg: ResolvedConfig, signal?: AbortSignal): Promise<string[]> {
  const cached = cache.get(cfg.baseUrl)
  if (cached && Date.now() - cached.at < CACHE_TTL_MS) return cached.models

  let payload: ModelsResponse
  try {
    payload = await requestJson<ModelsResponse>(`${cfg.baseUrl}/models`, {
      headers: authHeaders(cfg),
      signal,
      timeoutMs: 30_000,
    })
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error)
    throw new Error(
      `无法读取模型列表（GET ${cfg.baseUrl}/models）：${detail}\n` +
        `请确认 baseUrl 指向 OpenAI 兼容网关根路径（通常以 /v1 结尾），或手动配置 textModel/imageModel/videoModel/ttsModel。`,
    )
  }

  const models = collectModels(payload)
  cache.set(cfg.baseUrl, { at: Date.now(), models })
  return models
}

export async function resolveModel(
  cfg: ResolvedConfig,
  kind: ModelKind,
  signal?: AbortSignal,
): Promise<string> {
  const override = overrideFor(cfg, kind)
  if (override) return override

  const models = await listModels(cfg, signal)
  const picked = pickModel(models, kind)
  if (picked) return picked

  const configKey = `${kind}Model` as const
  throw new Error(
    `网关没有返回可用的 ${kind} 模型（共 ${models.length} 个）。` +
      `请在插件配置里显式填写 ${configKey}；当前可用模型：${models.slice(0, 30).join(', ') || '（空）'}`,
  )
}

export async function resolveAllModels(
  cfg: ResolvedConfig,
  signal?: AbortSignal,
): Promise<Record<ModelKind, string | null>> {
  const overrides: Record<ModelKind, string> = {
    text: cfg.textModel,
    image: cfg.imageModel,
    video: cfg.videoModel,
    tts: cfg.ttsModel,
  }

  try {
    const models = await listModels(cfg, signal)
    return {
      text: overrides.text || pickModel(models, 'text'),
      image: overrides.image || pickModel(models, 'image'),
      video: overrides.video || pickModel(models, 'video'),
      tts: overrides.tts || pickModel(models, 'tts'),
    }
  } catch (error) {
    if (Object.values(overrides).some(Boolean)) {
      return {
        text: overrides.text || null,
        image: overrides.image || null,
        video: overrides.video || null,
        tts: overrides.tts || null,
      }
    }
    throw error
  }
}

function collectModels(payload: ModelsResponse): string[] {
  const seen = new Set<string>()
  const result: string[] = []
  const push = (value: unknown) => {
    const id = typeof value === 'string' ? value : String((value as { id?: string; name?: string } | null)?.id || (value as { name?: string } | null)?.name || '')
    const clean = id.trim()
    if (!clean || seen.has(clean)) return
    seen.add(clean)
    result.push(clean)
  }

  if (Array.isArray(payload?.data)) for (const entry of payload.data) push(entry)
  if (Array.isArray(payload?.models)) for (const entry of payload.models) push(entry)
  if (Array.isArray(payload)) for (const entry of payload as unknown[]) push(entry)
  return result
}

const TEXT_HINTS = [/deepseek-chat/i, /deepseek-reasoner/i, /gpt-4o/i, /claude/i, /qwen/i, /glm/i, /kimi/i, /doubao-pro/i, /moonshot/i]
const IMAGE_HINTS = [/nano-?banana/i, /seedream/i, /gpt-image/i, /dall-?e/i, /imagen/i, /flux/i, /image/i]
const VIDEO_HINTS = [/seedance/i, /kling/i, /veo/i, /sora/i, /hailuo/i, /minimax.*video/i, /wan.*video/i, /video/i]
const TTS_HINTS = [/tts/i, /speech/i, /audio.*synth/i, /voice/i]
const EXCLUDE_TEXT = /embed|rerank|whisper|transcri|tts|speech|audio|image|video|vision|ocr/i
const EXCLUDE_IMAGE = /video|tts|speech|embed|rerank|transcri/i
const EXCLUDE_VIDEO = /image|tts|speech|embed|rerank|transcri/i
const EXCLUDE_TTS = /image|video|embed|rerank/i

export function pickModel(models: string[], kind: ModelKind): string | null {
  const hints = kind === 'text' ? TEXT_HINTS : kind === 'image' ? IMAGE_HINTS : kind === 'video' ? VIDEO_HINTS : TTS_HINTS
  const excluded = kind === 'text' ? EXCLUDE_TEXT : kind === 'image' ? EXCLUDE_IMAGE : kind === 'video' ? EXCLUDE_VIDEO : EXCLUDE_TTS

  for (const regex of hints) {
    const hit = models.find((model) => regex.test(model) && !excluded.test(model))
    if (hit) return hit
  }
  if (kind === 'text') {
    const first = models.find((model) => !excluded.test(model))
    if (first) return first
  }
  return null
}

function overrideFor(cfg: ResolvedConfig, kind: ModelKind): string {
  if (kind === 'text') return cfg.textModel
  if (kind === 'image') return cfg.imageModel
  if (kind === 'video') return cfg.videoModel
  return cfg.ttsModel
}

export function authHeaders(cfg: ResolvedConfig): Record<string, string> {
  return { Authorization: `Bearer ${cfg.apiKey}` }
}
