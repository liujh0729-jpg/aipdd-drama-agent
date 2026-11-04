import type { Context } from '@deepseek-ai/cordis'
import type { ToolDefinition } from '@deepseek-ai/dsh-tools'
import type { ProjectState } from './workspace.js'

export function registerAll(ctx: Context, definitions: ToolDefinition[]): void {
  ctx.effect(() => {
    const disposers = definitions.map((definition) => ctx.tools.register(definition))
    return () => {
      for (const dispose of disposers) dispose()
    }
  }, 'drama-harness tools')
}

export function analyzeProjectStages(state: ProjectState): string {
  if (state.projectStages.ingest !== 'done') {
    return `drama_init(projectId="${state.id}")`
  }
  if (state.episodes.length === 0) {
    return `drama_plan_episodes(projectId="${state.id}")`
  }

  const unfinishedScript = state.episodes.find(
    (episode) => episode.stages.script !== 'done' || episode.beats.length === 0,
  )
  if (unfinishedScript) {
    return `drama_script(projectId="${state.id}", episode=${unfinishedScript.index})`
  }
  if (state.characters.some((character) => !character.portraitFile)) {
    return `drama_cast(projectId="${state.id}")`
  }
  if (state.scenes.length === 0 || state.scenes.some((scene) => !scene.imageFile)) {
    return `drama_scenes(projectId="${state.id}")`
  }

  for (const episode of state.episodes) {
    if (episode.stages.frames !== 'done') {
      return `drama_frames(projectId="${state.id}", episode=${episode.index})`
    }
    if (episode.stages.videos !== 'done') {
      return `drama_videos(projectId="${state.id}", episode=${episode.index})`
    }
    if (episode.stages.audio !== 'done') {
      return `drama_audio(projectId="${state.id}", episode=${episode.index})`
    }
    if (episode.stages.compose !== 'done') {
      return `drama_compose(projectId="${state.id}", episode=${episode.index})`
    }
  }

  return '全部剧集已完成；可用 drama_status 随时查看产物。'
}
