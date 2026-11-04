import type { ResolvedConfig } from '../config.js'
import { chatJson } from '../llm.js'
import { bibleMessages, chunkSummaryMessages } from '../prompts.js'
import type { IngestBible, ProjectState } from '../workspace.js'
import { readSource, saveProject } from '../workspace.js'
import { deriveCharacters, normalizeName, requireIngest } from './shared.js'

const CHUNK_SIZE = 8000
const MAX_CHUNKS = 80

export interface IngestReport {
  title: string
  characters: number
  chapters: number
  chunks: number
  truncated: boolean
}

export async function runIngest(
  cfg: ResolvedConfig,
  state: ProjectState,
  signal?: AbortSignal,
): Promise<IngestReport> {
  state.projectStages.ingest = 'running'
  await saveProject(cfg, state)

  try {
    const source = await readSource(cfg, state)
    const chunks = chunkText(source, CHUNK_SIZE)
    const used = chunks.slice(0, MAX_CHUNKS)
    const summaries: string[] = []

    for (let i = 0; i < used.length; i += 1) {
      const summary = await chatJson<Record<string, unknown>>(
        cfg,
        chunkSummaryMessages(used[i] as string, i, used.length),
        { signal, temperature: 0.2 },
      )
      summaries.push(JSON.stringify(summary))
    }

    const bible = normalizeBible(
      await chatJson<Record<string, unknown>>(cfg, bibleMessages(summaries, state.title), {
        signal,
        temperature: 0.3,
      }),
      state.title,
    )

    state.ingest = bible
    state.characters = deriveCharacters(bible)
    if (!state.title || state.title === state.id) state.title = bible.title || state.title
    state.projectStages.ingest = 'done'
    await saveProject(cfg, state)

    return {
      title: bible.title,
      characters: bible.characters.length,
      chapters: bible.chapters.length,
      chunks: used.length,
      truncated: chunks.length > used.length,
    }
  } catch (error) {
    state.projectStages.ingest = 'failed'
    await saveProject(cfg, state).catch(() => undefined)
    throw error
  }
}

export function requireBible(state: ProjectState): IngestBible {
  return requireIngest(state)
}

export function chunkText(text: string, size: number): string[] {
  const clean = String(text || '').replace(/\r\n/g, '\n').trim()
  if (!clean) throw new Error('小说正文为空')
  if (clean.length <= size) return [clean]

  const chunks: string[] = []
  let cursor = 0
  while (cursor < clean.length) {
    let end = Math.min(clean.length, cursor + size)
    if (end < clean.length) {
      const paragraph = clean.lastIndexOf('\n\n', end)
      const sentence = clean.lastIndexOf('。', end)
      const boundary = Math.max(paragraph, sentence)
      if (boundary > cursor + Math.floor(size * 0.5)) end = boundary + 1
    }
    chunks.push(clean.slice(cursor, end).trim())
    cursor = end
  }
  return chunks.filter(Boolean)
}

function normalizeBible(raw: Record<string, unknown>, fallbackTitle: string): IngestBible {
  const characters = Array.isArray(raw.characters)
    ? raw.characters
        .map((item) => {
          const entry = (item && typeof item === 'object' ? item : {}) as Record<string, unknown>
          return {
            name: normalizeName(entry.name),
            role: normalizeName(entry.role),
            age: normalizeName(entry.age),
            appearance: normalizeName(entry.appearance),
            personality: normalizeName(entry.personality),
          }
        })
        .filter((item) => item.name)
    : []
  if (characters.length === 0) {
    throw new Error(`小说解析没有识别人物，无法继续。请确认正文完整，或换用能力更强的 textModel。`)
  }

  const chapters = Array.isArray(raw.chapters)
    ? raw.chapters
        .map((item) => {
          const entry = (item && typeof item === 'object' ? item : {}) as Record<string, unknown>
          return { title: normalizeName(entry.title), summary: normalizeName(entry.summary) }
        })
        .filter((item) => item.title || item.summary)
    : []

  return {
    title: normalizeName(raw.title) || fallbackTitle,
    logline: normalizeName(raw.logline),
    genre: normalizeName(raw.genre),
    setting: normalizeName(raw.setting),
    characters,
    chapters,
  }
}
