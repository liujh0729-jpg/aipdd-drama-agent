import { join } from 'node:path'
import type { ResolvedConfig } from '../config.js'
import { chatJson } from '../llm.js'
import { scriptMessages } from '../prompts.js'
import type { Beat, EpisodeState, ProjectState } from '../workspace.js'
import { projectDir, saveProject, writeJsonFile, writeText } from '../workspace.js'
import { deriveCharacters, normalizeBeat, normalizeName, pickEpisode, requireIngest } from './shared.js'

export interface ScriptReport {
  episode: number
  beats: number
  scriptFile: string
  markdownFile: string
}

export async function runScript(
  cfg: ResolvedConfig,
  state: ProjectState,
  episodeNumber: number,
  targetBeats: number,
  signal?: AbortSignal,
): Promise<ScriptReport> {
  const bible = requireIngest(state)
  const episode = pickEpisode(state, episodeNumber)
  if (state.characters.length === 0) state.characters = deriveCharacters(bible)

  episode.stages.script = 'running'
  await saveProject(cfg, state)

  try {
    const payload = await chatJson<{ beats?: unknown[] }>(
      cfg,
      scriptMessages(
        bible,
        episode.title,
        episode.summary,
        episode.keyEvents,
        state.characters,
        state.scenes,
        targetBeats,
      ),
      { signal, temperature: 0.6 },
    )

    const rawBeats = Array.isArray(payload.beats) ? payload.beats : []
    if (rawBeats.length === 0) {
      throw new Error('剧本没有返回任何 beat，请重试或换用能力更强的 textModel。')
    }

    const beats: Beat[] = rawBeats.map((item, index) => normalizeBeat(item, index + 1))

    episode.beats = beats
    episode.stages.frames = 'pending'
    episode.stages.videos = 'pending'
    episode.stages.audio = 'pending'
    episode.stages.compose = 'pending'
    episode.srtFile = undefined
    episode.finalFile = undefined
    const dir = join(projectDir(cfg, state.id), `ep${String(episode.index).padStart(3, '0')}`)
    const scriptFile = `${`ep${String(episode.index).padStart(3, '0')}`}/script.json`
    const markdownFile = `${`ep${String(episode.index).padStart(3, '0')}`}/script.md`

    await writeJsonFile(join(dir, 'script.json'), {
      episode: episode.index,
      title: episode.title,
      summary: episode.summary,
      beats,
    })
    await writeText(join(dir, 'script.md'), renderMarkdown(episode, beats))

    episode.scriptFile = scriptFile
    episode.stages.script = 'done'
    await saveProject(cfg, state)

    return { episode: episode.index, beats: beats.length, scriptFile, markdownFile }
  } catch (error) {
    episode.stages.script = 'failed'
    await saveProject(cfg, state).catch(() => undefined)
    throw error
  }
}

function renderMarkdown(episode: EpisodeState, beats: Beat[]): string {
  const lines = [`# 第 ${episode.index} 集 · ${episode.title}`, '', episode.summary, '']
  if (episode.hook) lines.push(`> 结尾钩子：${episode.hook}`, '')
  for (const beat of beats) {
    lines.push(`## Beat ${beat.index} · ${beat.title}（${beat.durationSec}s）`)
    lines.push(`- 场景：${normalizeName(beat.scene)}`)
    if (beat.characters.length > 0) lines.push(`- 人物：${beat.characters.join('、')}`)
    lines.push(`- 画面：${beat.action}`)
    for (const line of beat.dialogue) {
      lines.push(`- ${line.speaker ? `${line.speaker}：` : ''}${line.text}`)
    }
    if (beat.narration) lines.push(`- 旁白：${beat.narration}`)
    lines.push(`- 视频提示词：${beat.videoPrompt}`, '')
  }
  return `${lines.join('\n')}\n`
}
