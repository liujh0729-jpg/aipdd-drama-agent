import { mkdir, writeFile } from 'node:fs/promises'
import { dirname } from 'node:path'
import type { ResolvedConfig } from './config.js'
import { HttpError, requestBuffer } from './http.js'
import { authHeaders, resolveModel } from './models.js'

export interface SpeechOptions {
  model?: string
  voice?: string
  signal?: AbortSignal
}

export async function synthesizeSpeech(
  cfg: ResolvedConfig,
  text: string,
  destWithoutExtension: string,
  options: SpeechOptions = {},
): Promise<string> {
  const input = String(text || '').trim()
  if (!input) throw new Error('配音文本为空')
  if (input.length > 4000) {
    throw new Error(`单段配音文本过长（${input.length} 字），请控制在 4000 字以内`)
  }

  const model = options.model || (await resolveModel(cfg, 'tts', options.signal))
  const voice = options.voice || cfg.ttsVoice

  let buffer: Buffer
  try {
    buffer = await requestBuffer(`${cfg.baseUrl}/audio/speech`, {
      method: 'POST',
      headers: authHeaders(cfg),
      body: {
        model,
        input,
        voice,
        response_format: 'mp3',
      },
      signal: options.signal,
      timeoutMs: 3 * 60 * 1000,
    })
  } catch (error) {
    if (error instanceof HttpError && (error.status === 404 || error.status === 405)) {
      throw new Error(
        `网关没有开放 /audio/speech（HTTP ${error.status}）。` +
          `请换用支持 OpenAI 语音接口的网关，或在配置里关闭配音步骤。`,
      )
    }
    throw error
  }

  const dest = `${destWithoutExtension}.mp3`
  await mkdir(dirname(dest), { recursive: true })
  await writeFile(dest, buffer)
  return dest
}
