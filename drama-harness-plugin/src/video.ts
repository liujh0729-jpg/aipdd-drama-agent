import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, extname } from 'node:path'
import type { ResolvedConfig } from './config.js'
import { requestBuffer, requestJson, sleep } from './http.js'
import { authHeaders, resolveModel } from './models.js'

export interface VideoRequest {
  prompt: string
  firstFramePath?: string
  durationSec?: number
  aspectRatio?: string
  resolution?: string
  model?: string
  signal?: AbortSignal
}

export interface VideoResult {
  file: string
  url: string
  taskId: string
  api: ResolvedConfig['videoApi']
}

interface TaskResponse {
  id?: string
  task_id?: string
  status?: string
  data?: TaskResponse
  content?: { video_url?: string } | Array<{ video_url?: string }>
  content_url?: string
  video_url?: string
  url?: string
  metadata?: { url?: string; video_url?: string }
  output?: { video_url?: string; url?: string }
  error?: unknown
  message?: unknown
}

const SUCCESS = new Set(['completed', 'succeeded', 'success', 'done'])
const FAILURE = new Set(['failed', 'error', 'cancelled', 'canceled'])
const POLL_INTERVAL_MS = 5000

export async function generateVideo(
  cfg: ResolvedConfig,
  request: VideoRequest,
  destWithoutExtension: string,
): Promise<VideoResult> {
  const model = request.model || (await resolveModel(cfg, 'video', request.signal))
  const duration = clampDuration(request.durationSec ?? 5)
  const ratio = request.aspectRatio || cfg.aspectRatio
  const resolution = request.resolution || '720p'
  const firstFrame = request.firstFramePath ? await fileToDataUrl(request.firstFramePath) : ''

  const submitted = await submit(cfg, { model, prompt: request.prompt, duration, ratio, resolution, firstFrame, signal: request.signal })
  const taskId = taskIdOf(submitted)
  if (!taskId) {
    throw new Error(`视频任务没有返回 task_id，响应字段：${Object.keys(unwrap(submitted)).join(', ')}`)
  }

  const url = await poll(cfg, taskId, request.signal)
  const file = await download(cfg, url, destWithoutExtension)
  return { file, url, taskId, api: cfg.videoApi }
}

async function submit(
  cfg: ResolvedConfig,
  input: {
    model: string
    prompt: string
    duration: number
    ratio: string
    resolution: string
    firstFrame: string
    signal?: AbortSignal
  },
): Promise<TaskResponse> {
  if (cfg.videoApi === 'ark') {
    const content: Array<Record<string, unknown>> = [{ type: 'text', text: input.prompt }]
    if (input.firstFrame) {
      content.push({ type: 'image_url', image_url: { url: input.firstFrame }, role: '首帧' })
    }
    return requestJson<TaskResponse>(`${cfg.baseUrl}/contents/generations/tasks`, {
      method: 'POST',
      headers: authHeaders(cfg),
      body: {
        model: input.model,
        content,
        duration: input.duration,
        ratio: input.ratio,
        resolution: input.resolution,
        watermark: false,
      },
      signal: input.signal,
      timeoutMs: 60_000,
    })
  }

  if (cfg.videoApi === 'aipdd-videos') {
    const body: Record<string, unknown> = {
      model: input.model,
      prompt: input.prompt,
      duration: input.duration,
      duration_seconds: input.duration,
      resolution: input.resolution,
      video_resolution: input.resolution,
      ratio: input.ratio,
    }
    if (input.firstFrame) body.first_frame = input.firstFrame
    return requestJson<TaskResponse>(`${cfg.baseUrl}/videos`, {
      method: 'POST',
      headers: authHeaders(cfg),
      body,
      signal: input.signal,
      timeoutMs: 60_000,
    })
  }

  const body: Record<string, unknown> = {
    model: input.model,
    prompt: input.prompt,
    duration: input.duration,
    aspect_ratio: input.ratio,
    size: input.resolution,
  }
  if (input.firstFrame) body.image = input.firstFrame
  return requestJson<TaskResponse>(`${cfg.baseUrl}/video/generations`, {
    method: 'POST',
    headers: authHeaders(cfg),
    body,
    signal: input.signal,
    timeoutMs: 60_000,
  })
}

async function poll(cfg: ResolvedConfig, taskId: string, signal?: AbortSignal): Promise<string> {
  const path =
    cfg.videoApi === 'ark'
      ? '/contents/generations/tasks'
      : cfg.videoApi === 'aipdd-videos'
        ? '/videos'
        : '/video/generations'
  const deadline = Date.now() + cfg.videoTimeoutSec * 1000
  let lastStatus = ''

  while (Date.now() < deadline) {
    if (signal?.aborted) throw new Error('操作已取消')
    await sleep(POLL_INTERVAL_MS, signal)

    const payload = await requestJson<TaskResponse>(`${cfg.baseUrl}${path}/${encodeURIComponent(taskId)}`, {
      headers: authHeaders(cfg),
      signal,
      timeoutMs: 60_000,
    })
    const task = unwrap(payload)
    lastStatus = normalizeStatus(task.status)

    const url = videoUrlOf(task)
    if (url) return url
    if (lastStatus && FAILURE.has(lastStatus)) {
      const detail = String(task.error || task.message || '').trim()
      throw new Error(`视频任务失败：task_id=${taskId}${detail ? `：${detail}` : ''}`)
    }
    if (lastStatus && SUCCESS.has(lastStatus) && !url) {
      throw new Error(`视频任务已成功但响应里没有视频地址：task_id=${taskId}`)
    }
  }

  throw new Error(
    `视频任务超时（${cfg.videoTimeoutSec}s）：task_id=${taskId}，最后状态=${lastStatus || 'unknown'}。` +
      `可调大插件配置 videoTimeoutSec 后重试。`,
  )
}

async function download(cfg: ResolvedConfig, rawUrl: string, destWithoutExtension: string): Promise<string> {
  const url = new URL(rawUrl, `${cfg.baseUrl}/`).toString()
  const baseHost = new URL(cfg.baseUrl).hostname
  const sameHost = new URL(url).hostname === baseHost
  const buffer = await requestBuffer(url, {
    headers: sameHost ? authHeaders(cfg) : {},
    timeoutMs: 10 * 60 * 1000,
  })
  const ext = extensionFromUrl(url) || '.mp4'
  const dest = `${destWithoutExtension}${ext}`
  await mkdir(dirname(dest), { recursive: true })
  await writeFile(dest, buffer)
  return dest
}

function taskIdOf(response: TaskResponse): string {
  const task = unwrap(response)
  return String(task.task_id || task.id || '').trim()
}

function unwrap(response: TaskResponse): TaskResponse {
  return response?.data && typeof response.data === 'object' ? response.data : response
}

function videoUrlOf(task: TaskResponse): string {
  const content = task.content
  if (content && !Array.isArray(content) && content.video_url) return content.video_url
  if (Array.isArray(content)) {
    const hit = content.find((item) => item?.video_url)
    if (hit?.video_url) return hit.video_url
  }
  return String(
    task.content_url ||
      task.video_url ||
      task.url ||
      task.metadata?.video_url ||
      task.metadata?.url ||
      task.output?.video_url ||
      task.output?.url ||
      '',
  ).trim()
}

function normalizeStatus(value: unknown): string {
  return String(value || '').trim().toLowerCase()
}

function clampDuration(value: number): number {
  if (!Number.isFinite(value)) return 5
  return Math.min(12, Math.max(3, Math.round(value)))
}

async function fileToDataUrl(path: string): Promise<string> {
  const buffer = await readFile(path)
  const mime = mimeForExtension(extname(path).toLowerCase())
  return `data:${mime};base64,${buffer.toString('base64')}`
}

function mimeForExtension(ext: string): string {
  if (ext === '.jpg' || ext === '.jpeg') return 'image/jpeg'
  if (ext === '.webp') return 'image/webp'
  return 'image/png'
}

function extensionFromUrl(url: string): string {
  try {
    const ext = extname(new URL(url).pathname).toLowerCase()
    return ['.mp4', '.mov', '.webm', '.mkv'].includes(ext) ? ext : ''
  } catch {
    return ''
  }
}
