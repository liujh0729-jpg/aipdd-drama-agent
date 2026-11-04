import { writeFile } from 'node:fs/promises'

export class HttpError extends Error {
  readonly status: number
  readonly body: string

  constructor(message: string, status: number, body: string) {
    super(message)
    this.name = 'HttpError'
    this.status = status
    this.body = body
  }
}

export interface RequestOptions {
  method?: string
  headers?: Record<string, string>
  body?: unknown
  signal?: AbortSignal
  timeoutMs?: number
}

const DEFAULT_TIMEOUT_MS = 120_000
const MAX_ERROR_BODY = 800

export async function requestJson<T>(url: string, options: RequestOptions = {}): Promise<T> {
  const response = await request(url, options)
  const text = await response.text()
  try {
    return JSON.parse(text) as T
  } catch {
    throw new HttpError(
      `网关返回的不是 JSON（HTTP ${response.status}）：${truncate(text)}`,
      response.status,
      truncate(text),
    )
  }
}

export async function requestBuffer(url: string, options: RequestOptions = {}): Promise<Buffer> {
  const response = await request(url, options)
  const arrayBuffer = await response.arrayBuffer()
  return Buffer.from(arrayBuffer)
}

export async function requestRaw(url: string, options: RequestOptions = {}): Promise<Response> {
  return request(url, options)
}

async function request(url: string, options: RequestOptions): Promise<Response> {
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS
  const signals: AbortSignal[] = [AbortSignal.timeout(timeoutMs)]
  if (options.signal) signals.push(options.signal)
  const signal = signals.length === 1 ? signals[0] : AbortSignal.any(signals)

  const headers: Record<string, string> = { ...(options.headers || {}) }
  let body: BodyInit | undefined
  if (options.body instanceof FormData) {
    body = options.body
  } else if (options.body !== undefined) {
    headers['Content-Type'] = headers['Content-Type'] || 'application/json'
    body = JSON.stringify(options.body)
  }

  let response: Response
  try {
    response = await fetch(url, {
      method: options.method || (body ? 'POST' : 'GET'),
      headers,
      body,
      signal,
    })
  } catch (error) {
    throw wrapNetworkError(url, error, timeoutMs)
  }

  if (!response.ok) {
    const text = truncate(await response.text().catch(() => ''))
    throw new HttpError(
      `HTTP ${response.status} ${response.statusText} <- ${redactUrl(url)} ${text ? `: ${text}` : ''}`.trim(),
      response.status,
      text,
    )
  }
  return response
}

function wrapNetworkError(url: string, error: unknown, timeoutMs: number): Error {
  const name = (error as { name?: string } | null)?.name
  if (name === 'AbortError' || name === 'TimeoutError') {
    return new Error(`请求超时（${Math.round(timeoutMs / 1000)}s）：${redactUrl(url)}`)
  }
  const message = error instanceof Error ? error.message : String(error)
  return new Error(`请求失败：${redactUrl(url)}：${message}`)
}

function truncate(text: string): string {
  const clean = String(text || '').replace(/\s+/g, ' ').trim()
  return clean.length > MAX_ERROR_BODY ? `${clean.slice(0, MAX_ERROR_BODY)}...` : clean
}

function redactUrl(url: string): string {
  try {
    const parsed = new URL(url)
    for (const key of ['key', 'api_key', 'apiKey', 'token', 'access_token']) {
      if (parsed.searchParams.has(key)) parsed.searchParams.set(key, '***')
    }
    return parsed.toString()
  } catch {
    return url
  }
}

export async function downloadToFile(
  url: string,
  destPath: string,
  options: RequestOptions = {},
): Promise<string> {
  const buffer = await requestBuffer(url, options)
  await writeFile(destPath, buffer)
  return destPath
}

export function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new Error('操作已取消'))
      return
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    const onAbort = () => {
      clearTimeout(timer)
      reject(new Error('操作已取消'))
    }
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}
