import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, extname } from 'node:path'
import type { ResolvedConfig } from './config.js'
import { requestBuffer, requestJson, sleep } from './http.js'
import { HttpError } from './http.js'
import { authHeaders, resolveModel } from './models.js'

interface ImageItem {
  b64_json?: string
  url?: string
  image_url?: string
}

interface ImageResponse {
  id?: string
  task_id?: string
  status?: string
  data?: ImageItem[]
  image_url?: string
  url?: string
  error?: unknown
  message?: unknown
}

export interface GenerateImageOptions {
  size?: string
  model?: string
  referencePaths?: string[]
  signal?: AbortSignal
  timeoutMs?: number
}

export interface GeneratedImage {
  buffer: Buffer
  mime: string
  extension: string
}

const IMAGE_TASK_TIMEOUT_MS = 10 * 60 * 1000
const IMAGE_POLL_INTERVAL_MS = 3000
const SUCCESS = new Set(['completed', 'succeeded', 'success', 'done'])
const FAILURE = new Set(['failed', 'error', 'cancelled', 'canceled'])

export async function generateImage(
  cfg: ResolvedConfig,
  prompt: string,
  options: GenerateImageOptions = {},
): Promise<GeneratedImage> {
  const model = options.model || (await resolveModel(cfg, 'image', options.signal))
  const size = options.size || cfg.imageSize
  const references = options.referencePaths || []

  const payload: Record<string, unknown> = {
    model,
    prompt,
    n: 1,
    size,
    response_format: 'b64_json',
  }
  const geometry = parseSize(size)
  if (geometry) {
    payload.width = geometry.width
    payload.height = geometry.height
  }

  let path = '/images/generations'
  if (references.length > 0) {
    path = '/images/edits'
    payload.image = await Promise.all(references.map(fileToDataUrl))
  }

  const response = await requestJson<ImageResponse>(`${cfg.baseUrl}${path}`, {
    method: 'POST',
    headers: authHeaders(cfg),
    body: payload,
    signal: options.signal,
    timeoutMs: options.timeoutMs ?? 5 * 60 * 1000,
  })

  const resolved = await resolveImageResponse(cfg, path, response, options.signal)
  return decodeImage(resolved)
}

export async function generateImageToFile(
  cfg: ResolvedConfig,
  prompt: string,
  destWithoutExtension: string,
  options: GenerateImageOptions = {},
): Promise<string> {
  const image = await generateImage(cfg, prompt, options)
  const dest = `${destWithoutExtension}${image.extension}`
  await mkdir(dirname(dest), { recursive: true })
  await writeFile(dest, image.buffer)
  return dest
}

async function resolveImageResponse(
  cfg: ResolvedConfig,
  submitPath: string,
  response: ImageResponse,
  signal?: AbortSignal,
): Promise<ImageItem> {
  const immediate = pickImageItem(response)
  if (immediate) return immediate

  const taskId = String(response?.task_id || response?.id || '').trim()
  const status = normalizeStatus(response?.status)
  if (status && FAILURE.has(status)) throw imageTaskError(response, taskId)

  if (!taskId) {
    throw new Error(`图像接口没有返回图片或任务 ID，响应字段：${Object.keys(response || {}).join(', ')}`)
  }

  const deadline = Date.now() + IMAGE_TASK_TIMEOUT_MS
  let latest = response
  while (Date.now() < deadline) {
    if (signal?.aborted) throw new Error('操作已取消')
    await sleep(IMAGE_POLL_INTERVAL_MS, signal)

    latest = await requestJson<ImageResponse>(`${cfg.baseUrl}${submitPath}/${encodeURIComponent(taskId)}`, {
      headers: authHeaders(cfg),
      signal,
      timeoutMs: 60_000,
    })
    const item = pickImageItem(latest)
    if (item) return item
    const nextStatus = normalizeStatus(latest?.status)
    if (nextStatus && FAILURE.has(nextStatus)) throw imageTaskError(latest, taskId)
  }

  throw new Error(`图像任务超时（10 分钟）：task_id=${taskId}，最后状态=${normalizeStatus(latest?.status) || 'unknown'}`)
}

function pickImageItem(response: ImageResponse): ImageItem | null {
  const first = response?.data?.[0]
  if (first && (first.b64_json || first.url || first.image_url)) return first
  if (response?.image_url || response?.url) return { url: response.image_url || response.url }
  return null
}

async function decodeImage(item: ImageItem): Promise<GeneratedImage> {
  if (item.b64_json) {
    const clean = item.b64_json.includes(',') ? item.b64_json.split(',', 2)[1] || '' : item.b64_json
    return { buffer: Buffer.from(clean, 'base64'), mime: 'image/png', extension: '.png' }
  }

  const url = String(item.url || item.image_url || '').trim()
  if (!url) throw new Error('图像任务完成但没有返回图片数据')
  if (url.startsWith('data:')) {
    const match = url.match(/^data:([^;,]+);base64,(.*)$/s)
    if (!match) throw new Error('图像接口返回了无法解析的 data URL')
    const mime = match[1] || 'image/png'
    return { buffer: Buffer.from(match[2] || '', 'base64'), mime, extension: extensionForMime(mime) }
  }

  let buffer: Buffer
  try {
    buffer = await requestBuffer(url, { timeoutMs: 5 * 60 * 1000 })
  } catch (error) {
    if (error instanceof HttpError && (error.status === 401 || error.status === 403)) {
      throw new Error(`图片下载被拒绝（HTTP ${error.status}）：${error.body || '需要鉴权'}。请确认网关允许直接下载产物。`)
    }
    throw error
  }
  const mime = sniffImageMime(buffer)
  return { buffer, mime, extension: extensionForMime(mime) }
}

function imageTaskError(response: ImageResponse, taskId: string): Error {
  const detail = String(response?.error || response?.message || '').trim()
  return new Error(`图像任务失败：task_id=${taskId || '(none)'}${detail ? `：${detail}` : ''}`)
}

function normalizeStatus(value: unknown): string {
  return String(value || '').trim().toLowerCase()
}

async function fileToDataUrl(path: string): Promise<string> {
  const buffer = await readFile(path)
  const ext = extname(path).toLowerCase()
  const mime = ext === '.jpg' || ext === '.jpeg' ? 'image/jpeg' : ext === '.webp' ? 'image/webp' : 'image/png'
  return `data:${mime};base64,${buffer.toString('base64')}`
}

function parseSize(size: string): { width: number; height: number } | null {
  const match = String(size || '').match(/^(\d+)x(\d+)$/)
  if (!match) return null
  return { width: Number(match[1]), height: Number(match[2]) }
}

function sniffImageMime(buffer: Buffer): string {
  if (buffer.length >= 8 && buffer.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) {
    return 'image/png'
  }
  if (buffer.length >= 3 && buffer[0] === 0xff && buffer[1] === 0xd8 && buffer[2] === 0xff) return 'image/jpeg'
  if (buffer.length >= 12 && buffer.subarray(0, 4).toString('ascii') === 'RIFF' && buffer.subarray(8, 12).toString('ascii') === 'WEBP') {
    return 'image/webp'
  }
  return 'image/png'
}

function extensionForMime(mime: string): string {
  if (mime.includes('jpeg') || mime.includes('jpg')) return '.jpg'
  if (mime.includes('webp')) return '.webp'
  return '.png'
}
