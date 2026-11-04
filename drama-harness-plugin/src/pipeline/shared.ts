import type { Beat, CharacterAsset, EpisodeState, IngestBible, ProjectState, StageStatus } from '../workspace.js'
import type { ResolvedConfig } from '../config.js'
import { saveProject } from '../workspace.js'

export function createPersistQueue(cfg: ResolvedConfig, state: ProjectState): () => Promise<void> {
  let chain = Promise.resolve()
  return () => {
    chain = chain.then(() => saveProject(cfg, state))
    return chain
  }
}

export async function mapLimit<T, R>(
  items: T[],
  limit: number,
  worker: (item: T, index: number) => Promise<R>,
  signal?: AbortSignal,
): Promise<R[]> {
  const results = new Array<R>(items.length)
  let next = 0

  const run = async (): Promise<void> => {
    while (true) {
      if (signal?.aborted) throw new Error('操作已取消')
      const index = next
      next += 1
      if (index >= items.length) return
      results[index] = await worker(items[index] as T, index)
    }
  }

  const workers = Array.from({ length: Math.max(1, Math.min(limit, items.length || 1)) }, () => run())
  await Promise.all(workers)
  return results
}

export function requireIngest(state: ProjectState): IngestBible {
  if (!state.ingest) {
    throw new Error('项目还没有完成小说解析，请先调用 drama_init（或重跑解析）。')
  }
  return state.ingest
}

export function pickEpisode(state: ProjectState, episode: number): EpisodeState {
  const found = state.episodes.find((item) => item.index === episode)
  if (!found) {
    const available = state.episodes.map((item) => item.index).join(', ') || '（无）'
    throw new Error(`第 ${episode} 集不存在。当前已规划集数：${available}`)
  }
  return found
}

export function setStage<K extends string>(
  target: { stages: Record<K, StageStatus> },
  key: K,
  status: StageStatus,
): void {
  target.stages[key] = status
}

export function deriveCharacters(ingest: IngestBible): CharacterAsset[] {
  const seen = new Map<string, CharacterAsset>()
  for (const item of ingest.characters) {
    const name = normalizeName(item.name)
    if (!name) continue
    const existing = seen.get(name)
    if (!existing) {
      seen.set(name, {
        name,
        role: String(item.role || '').trim(),
        appearance: String(item.appearance || '').trim(),
        personality: String(item.personality || '').trim(),
        portraitPrompt: '',
        portraitFile: '',
      })
      continue
    }
    existing.appearance = existing.appearance || String(item.appearance || '').trim()
    existing.personality = existing.personality || String(item.personality || '').trim()
    existing.role = existing.role || String(item.role || '').trim()
  }
  return [...seen.values()]
}

export function normalizeName(value: unknown): string {
  return String(value || '').replace(/\s+/g, ' ').trim()
}

export function normalizeBeat(raw: unknown, index: number): Beat {
  const source = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  const dialogue = Array.isArray(source.dialogue)
    ? source.dialogue
        .map((line) => {
          const item = (line && typeof line === 'object' ? line : {}) as Record<string, unknown>
          return { speaker: normalizeName(item.speaker), text: normalizeName(item.text) }
        })
        .filter((line) => line.text)
    : []
  const characters = Array.isArray(source.characters)
    ? source.characters.map((name) => normalizeName(name)).filter(Boolean)
    : []
  const action = normalizeName(source.action)
  const duration = Number(source.durationSec)

  return {
    index,
    title: normalizeName(source.title) || `Beat ${index}`,
    scene: normalizeName(source.scene) || '主场景',
    characters,
    action,
    dialogue,
    narration: normalizeName(source.narration),
    videoPrompt: normalizeName(source.videoPrompt) || action,
    durationSec: Number.isFinite(duration) ? Math.min(8, Math.max(3, Math.round(duration))) : 5,
  }
}

export function beatSpeechText(beat: Beat): string {
  const parts: string[] = []
  if (beat.narration) parts.push(beat.narration)
  for (const line of beat.dialogue) {
    if (!line.text) continue
    parts.push(line.speaker ? `${line.speaker}：${line.text}` : line.text)
  }
  return parts.join('\n').trim()
}

export function beatVoiceText(beat: Beat): string {
  const parts: string[] = []
  if (beat.narration) parts.push(beat.narration)
  for (const line of beat.dialogue) {
    if (line.text) parts.push(line.text)
  }
  return parts.join('\n').trim()
}

export function buildSrt(beats: Beat[]): string {
  let cursor = 0
  let sequence = 0
  const blocks: string[] = []
  for (const beat of beats) {
    const start = cursor
    cursor += beat.durationSec
    const text = beatSpeechText(beat).replace(/\n/g, ' ')
    if (!text) continue
    sequence += 1
    blocks.push(`${sequence}\n${timestamp(start)} --> ${timestamp(cursor)}\n${text}\n`)
  }
  return blocks.join('\n')
}

function timestamp(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))
  const hh = String(Math.floor(total / 3600)).padStart(2, '0')
  const mm = String(Math.floor((total % 3600) / 60)).padStart(2, '0')
  const ss = String(total % 60).padStart(2, '0')
  return `${hh}:${mm}:${ss},000`
}

export function relativePath(from: string, to: string): string {
  return to.startsWith(from) ? to.slice(from.length).replace(/^[\\/]+/, '').replace(/\\/g, '/') : to
}
