import type { ResolvedConfig } from './config.js'
import { requestJson } from './http.js'
import { authHeaders, resolveModel } from './models.js'

export interface ChatMessage {
  role: 'system' | 'user' | 'assistant'
  content: string
}

interface ChatResponse {
  choices?: Array<{
    message?: {
      content?: string | Array<{ type?: string; text?: string }>
    }
    text?: string
  }>
}

export interface ChatOptions {
  temperature?: number
  signal?: AbortSignal
  timeoutMs?: number
}

export async function chat(
  cfg: ResolvedConfig,
  messages: ChatMessage[],
  options: ChatOptions = {},
): Promise<string> {
  const model = await resolveModel(cfg, 'text', options.signal)
  const payload = await requestJson<ChatResponse>(`${cfg.baseUrl}/chat/completions`, {
    method: 'POST',
    headers: authHeaders(cfg),
    body: {
      model,
      messages,
      temperature: options.temperature ?? 0.4,
      stream: false,
    },
    signal: options.signal,
    timeoutMs: options.timeoutMs ?? 10 * 60 * 1000,
  })

  const choice = payload?.choices?.[0]
  const content = choice?.message?.content ?? choice?.text ?? ''
  const text = typeof content === 'string' ? content : content.map((part) => part.text || '').join('')
  if (!text.trim()) {
    throw new Error(`模型 ${model} 返回了空内容`)
  }
  return text.trim()
}

export async function chatJson<T>(
  cfg: ResolvedConfig,
  messages: ChatMessage[],
  options: ChatOptions = {},
): Promise<T> {
  const text = await chat(cfg, messages, options)
  return parseJsonReply<T>(text)
}

export function parseJsonReply<T>(text: string): T {
  const raw = String(text || '').trim()
  if (!raw) throw new Error('模型返回为空，无法解析 JSON')

  const candidates: string[] = []
  const fenced = raw.match(/```(?:json)?\s*([\s\S]*?)```/i)
  if (fenced?.[1]) candidates.push(fenced[1].trim())
  candidates.push(raw)

  const balanced = extractBalanced(raw)
  if (balanced) candidates.push(balanced)

  for (const candidate of candidates) {
    try {
      return JSON.parse(candidate) as T
    } catch {
      // 继续尝试下一个候选片段
    }
  }

  throw new Error(`模型没有返回合法 JSON。原始内容前 400 字：${raw.slice(0, 400)}`)
}

function extractBalanced(text: string): string | null {
  const start = findFirstJsonStart(text)
  if (start < 0) return null
  const opener = text[start]
  const closer = opener === '{' ? '}' : ']'
  let depth = 0
  let inString = false
  let escaped = false

  for (let i = start; i < text.length; i += 1) {
    const char = text[i]
    if (inString) {
      if (escaped) escaped = false
      else if (char === '\\') escaped = true
      else if (char === '"') inString = false
      continue
    }
    if (char === '"') {
      inString = true
      continue
    }
    if (char === opener) depth += 1
    else if (char === closer) {
      depth -= 1
      if (depth === 0) return text.slice(start, i + 1)
    }
  }
  return null
}

function findFirstJsonStart(text: string): number {
  const objectStart = text.indexOf('{')
  const arrayStart = text.indexOf('[')
  if (objectStart < 0) return arrayStart
  if (arrayStart < 0) return objectStart
  return Math.min(objectStart, arrayStart)
}
