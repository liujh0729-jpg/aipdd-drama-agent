import type { ResolvedConfig } from '../config.js'
import { chatJson } from '../llm.js'
import { episodesMessages } from '../prompts.js'
import type { EpisodeState, ProjectState } from '../workspace.js'
import { defaultEpisodeStages, saveProject } from '../workspace.js'
import { normalizeName, requireIngest } from './shared.js'

export async function runPlanEpisodes(
  cfg: ResolvedConfig,
  state: ProjectState,
  targetCount: number,
  signal?: AbortSignal,
): Promise<EpisodeState[]> {
  const bible = requireIngest(state)
  state.projectStages.episodes = 'running'
  await saveProject(cfg, state)

  try {
    const payload = await chatJson<{ episodes?: unknown[] }>(
      cfg,
      episodesMessages(bible, targetCount),
      { signal, temperature: 0.5 },
    )
    const rawEpisodes = Array.isArray(payload.episodes) ? payload.episodes : []
    if (rawEpisodes.length === 0) {
      throw new Error('分集规划没有返回任何剧集，请重试或换用能力更强的 textModel。')
    }

    const episodes: EpisodeState[] = rawEpisodes.map((item, index) => {
      const entry = (item && typeof item === 'object' ? item : {}) as Record<string, unknown>
      const number = index + 1
      return {
        index: number,
        title: normalizeName(entry.title) || `第 ${number} 集`,
        summary: normalizeName(entry.summary),
        hook: normalizeName(entry.hook),
        keyEvents: Array.isArray(entry.keyEvents)
          ? entry.keyEvents.map((event) => normalizeName(event)).filter(Boolean)
          : [],
        scriptFile: `ep${String(number).padStart(3, '0')}/script.json`,
        beats: [],
        stages: defaultEpisodeStages(),
      }
    })

    state.episodes = episodes
    state.projectStages.episodes = 'done'
    await saveProject(cfg, state)
    return episodes
  } catch (error) {
    state.projectStages.episodes = 'failed'
    await saveProject(cfg, state).catch(() => undefined)
    throw error
  }
}
