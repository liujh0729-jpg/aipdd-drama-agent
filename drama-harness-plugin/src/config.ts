import { homedir } from 'node:os'
import { isAbsolute, join, resolve } from 'node:path'

export type Gateway = 'aipdd' | 'newapi' | 'volcengine' | 'deepseek' | 'custom'
export type VideoApi = 'auto' | 'aipdd-videos' | 'newapi-jobs' | 'ark'

export interface PluginConfig {
  apiKey: string
  gateway: Gateway
  baseUrl?: string
  textModel?: string
  imageModel?: string
  videoModel?: string
  ttsModel?: string
  ttsVoice?: string
  videoApi?: VideoApi
  workspaceDir?: string
  ffmpegPath?: string
  concurrency?: number
  videoTimeoutSec?: number
  imageSize?: string
  frameSize?: string
  aspectRatio?: string
}

export interface ResolvedConfig {
  apiKey: string
  gateway: Gateway
  baseUrl: string
  textModel: string
  imageModel: string
  videoModel: string
  ttsModel: string
  ttsVoice: string
  videoApi: Exclude<VideoApi, 'auto'>
  workspaceDir: string
  ffmpegPath: string
  concurrency: number
  videoTimeoutSec: number
  imageSize: string
  frameSize: string
  aspectRatio: string
}

interface Preset {
  baseUrl: string
  videoApi: Exclude<VideoApi, 'auto'>
}

const PRESETS: Record<Gateway, Preset> = {
  aipdd: { baseUrl: 'https://api.aipdd.work/v1', videoApi: 'aipdd-videos' },
  newapi: { baseUrl: '', videoApi: 'newapi-jobs' },
  volcengine: { baseUrl: 'https://ark.cn-beijing.volces.com/api/v3', videoApi: 'ark' },
  deepseek: { baseUrl: 'https://api.deepseek.com/v1', videoApi: 'newapi-jobs' },
  custom: { baseUrl: '', videoApi: 'newapi-jobs' },
}

const GATEWAYS = new Set<string>(Object.keys(PRESETS))
const VIDEO_APIS = new Set<string>(['auto', 'aipdd-videos', 'newapi-jobs', 'ark'])
export const DEFAULT_WORKSPACE_DIR = join(homedir(), 'drama-projects')

export function resolveConfig(raw: PluginConfig): ResolvedConfig {
  if (!raw || typeof raw !== 'object') throw new Error('插件配置缺失')
  const apiKey = String(raw.apiKey || '').trim()
  if (!apiKey) {
    throw new Error('请在插件配置里填写 apiKey（AIPDD / NewAPI / 火山方舟等网关的 Key）')
  }

  const gateway = (raw.gateway || 'aipdd') as Gateway
  if (!GATEWAYS.has(gateway)) {
    throw new Error(`gateway 只能是 ${[...GATEWAYS].join(' / ')}，收到 ${String(gateway)}`)
  }

  const preset = PRESETS[gateway]
  const baseUrl = normalizeBaseUrl(raw.baseUrl || preset.baseUrl)
  if (!baseUrl) {
    throw new Error(`gateway=${gateway} 需要显式配置 baseUrl，例如 https://your-gateway.example.com/v1`)
  }

  let videoApi = raw.videoApi || 'auto'
  if (!VIDEO_APIS.has(videoApi)) {
    throw new Error(`videoApi 只能是 auto / aipdd-videos / newapi-jobs / ark，收到 ${String(videoApi)}`)
  }
  if (videoApi === 'auto') videoApi = preset.videoApi

  const workspaceInput = String(raw.workspaceDir || DEFAULT_WORKSPACE_DIR).trim() || DEFAULT_WORKSPACE_DIR
  const workspaceDir = resolveWorkspaceDir({ workspaceDir: workspaceInput })
  const concurrency = clampInt(raw.concurrency, 1, 8, 2)
  const videoTimeoutSec = clampInt(raw.videoTimeoutSec, 60, 7200, 1800)

  return {
    apiKey,
    gateway,
    baseUrl,
    textModel: clean(raw.textModel),
    imageModel: clean(raw.imageModel),
    videoModel: clean(raw.videoModel),
    ttsModel: clean(raw.ttsModel),
    ttsVoice: clean(raw.ttsVoice) || 'alloy',
    videoApi: videoApi as Exclude<VideoApi, 'auto'>,
    workspaceDir,
    ffmpegPath: clean(raw.ffmpegPath),
    concurrency,
    videoTimeoutSec,
    imageSize: clean(raw.imageSize) || '1024x1536',
    frameSize: clean(raw.frameSize) || '1024x1536',
    aspectRatio: clean(raw.aspectRatio) || '9:16',
  }
}

export function resolveWorkspaceDir(raw: { workspaceDir?: string }): string {
  const input = String(raw?.workspaceDir || DEFAULT_WORKSPACE_DIR).trim() || DEFAULT_WORKSPACE_DIR
  const expanded = input === '~' ? homedir() : input.startsWith('~/') || input.startsWith('~\\') ? join(homedir(), input.slice(2)) : input
  return isAbsolute(expanded) ? expanded : resolve(process.cwd(), expanded)
}

function normalizeBaseUrl(value: string): string {
  const clean = String(value || '').trim().replace(/\/+$/, '')
  if (!clean) return ''
  let parsed: URL
  try {
    parsed = new URL(clean)
  } catch {
    throw new Error(`baseUrl 不是合法 URL：${clean}`)
  }
  if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') {
    throw new Error(`baseUrl 只支持 http/https：${clean}`)
  }
  return clean
}

function clean(value: unknown): string {
  return String(value ?? '').trim()
}

function clampInt(value: unknown, min: number, max: number, fallback: number): number {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return fallback
  return Math.min(max, Math.max(min, Math.round(parsed)))
}

