import { join } from 'node:path'
import type { ResolvedConfig } from '../config.js'
import { generateImageToFile } from '../images.js'
import { chatJson } from '../llm.js'
import { framePrompt, portraitPrompt, scenePrompt, scenesMessages } from '../prompts.js'
import type { Beat, CharacterAsset, ProjectState, SceneAsset } from '../workspace.js'
import { projectDir, saveProject } from '../workspace.js'
import {
  createPersistQueue,
  deriveCharacters,
  mapLimit,
  normalizeName,
  pickEpisode,
  requireIngest,
  setStage,
} from './shared.js'

export interface CastReport {
  characters: number
  generated: number
  skipped: number
}

export async function runCast(
  cfg: ResolvedConfig,
  state: ProjectState,
  force: boolean,
  signal?: AbortSignal,
): Promise<CastReport> {
  const bible = requireIngest(state)
  if (state.characters.length === 0) state.characters = deriveCharacters(bible)
  if (state.characters.length === 0) throw new Error('设定集里没有人物，无法生成肖像')

  setStage({ stages: state.projectStages }, 'cast', 'running')
  await saveProject(cfg, state)

  try {
    let generated = 0
    let skipped = 0
    const persist = createPersistQueue(cfg, state)

    await mapLimit(state.characters, cfg.concurrency, async (character) => {
      if (!character.portraitPrompt) character.portraitPrompt = portraitPrompt(character, cfg.aspectRatio)
      const existing = character.portraitFile && !force ? character.portraitFile : ''
      if (existing) {
        skipped += 1
        return
      }
      const relativeBase = `characters/portraits/${fileSlug(character.name)}`
      const absolute = await generateImageToFile(
        cfg,
        character.portraitPrompt,
        join(projectDir(cfg, state.id), relativeBase),
        { size: cfg.imageSize, signal },
      )
      character.portraitFile = `${relativeBase}${absolute.slice(absolute.lastIndexOf('.'))}`
      generated += 1
      await persist()
    }, signal)

    setStage({ stages: state.projectStages }, 'cast', 'done')
    await saveProject(cfg, state)
    return { characters: state.characters.length, generated, skipped }
  } catch (error) {
    setStage({ stages: state.projectStages }, 'cast', 'failed')
    await saveProject(cfg, state).catch(() => undefined)
    throw error
  }
}

export interface ScenesReport {
  scenes: number
  generated: number
  skipped: number
}

export async function runScenes(
  cfg: ResolvedConfig,
  state: ProjectState,
  force: boolean,
  signal?: AbortSignal,
): Promise<ScenesReport> {
  const bible = requireIngest(state)
  if (state.episodes.every((episode) => episode.beats.length === 0)) {
    throw new Error('还没有任何剧本，请先对至少一集调用 drama_script。')
  }

  setStage({ stages: state.projectStages }, 'scenes', 'running')
  await saveProject(cfg, state)

  try {
    const sceneNames = new Set<string>()
    for (const episode of state.episodes) {
      for (const beat of episode.beats) {
        if (beat.scene) sceneNames.add(beat.scene)
      }
    }

    let planned: SceneAsset[] = []
    try {
      const payload = await chatJson<{ scenes?: unknown[] }>(
        cfg,
        scenesMessages(
          bible,
          state.episodes.map((episode) => `第 ${episode.index} 集《${episode.title}》：${episode.summary}`),
        ),
        { signal, temperature: 0.4 },
      )
      planned = normalizeScenes(payload.scenes)
    } catch {
      planned = []
    }

    const merged = new Map<string, SceneAsset>()
    for (const scene of planned) merged.set(scene.name, scene)
    for (const name of sceneNames) {
      if (!merged.has(name)) {
        const firstBeat = firstBeatWithScene(state, name)
        merged.set(name, {
          name,
          description: firstBeat?.action || name,
          imagePrompt: '',
          imageFile: '',
        })
      }
    }

    state.scenes = [...merged.values()]
    let generated = 0
    let skipped = 0
    const persist = createPersistQueue(cfg, state)

    await mapLimit(state.scenes, cfg.concurrency, async (scene) => {
      if (!scene.imagePrompt) scene.imagePrompt = scenePrompt(scene, cfg.aspectRatio)
      if (scene.imageFile && !force) {
        skipped += 1
        return
      }
      const relativeBase = `scenes/${fileSlug(scene.name)}`
      const absolute = await generateImageToFile(
        cfg,
        scene.imagePrompt,
        join(projectDir(cfg, state.id), relativeBase),
        { size: cfg.frameSize, signal },
      )
      scene.imageFile = `${relativeBase}${absolute.slice(absolute.lastIndexOf('.'))}`
      generated += 1
      await persist()
    }, signal)

    setStage({ stages: state.projectStages }, 'scenes', 'done')
    await saveProject(cfg, state)
    return { scenes: state.scenes.length, generated, skipped }
  } catch (error) {
    setStage({ stages: state.projectStages }, 'scenes', 'failed')
    await saveProject(cfg, state).catch(() => undefined)
    throw error
  }
}

export interface FramesReport {
  episode: number
  total: number
  generated: number
  skipped: number
}

export async function runFrames(
  cfg: ResolvedConfig,
  state: ProjectState,
  episodeNumber: number,
  beatNumbers: number[] | undefined,
  force: boolean,
  signal?: AbortSignal,
): Promise<FramesReport> {
  const episode = pickEpisode(state, episodeNumber)
  if (episode.beats.length === 0) {
    throw new Error(`第 ${episode.index} 集还没有剧本，请先调用 drama_script。`)
  }

  if (state.characters.some((item) => !item.portraitFile)) {
    await runCast(cfg, state, false, signal)
  }
  if (state.scenes.length === 0) {
    await runScenes(cfg, state, false, signal)
  }

  const selected = selectBeats(episode.beats, beatNumbers)
  setStage(episode, 'frames', 'running')
  await saveProject(cfg, state)

  try {
    let generated = 0
    let skipped = 0
    const persist = createPersistQueue(cfg, state)
    const epFolder = `ep${String(episode.index).padStart(3, '0')}`

    await mapLimit(selected, cfg.concurrency, async (beat) => {
      if (beat.frameFile && !force) {
        skipped += 1
        return
      }
      const scene = state.scenes.find((item) => item.name === beat.scene)
      if (!scene?.imageFile) {
        throw new Error(`Beat ${beat.index} 引用的场景「${beat.scene}」还没有参考图，请先调用 drama_scenes。`)
      }

      const references = [join(projectDir(cfg, state.id), scene.imageFile)]
      for (const name of beat.characters.slice(0, 3)) {
        const character = state.characters.find((item) => item.name === name)
        if (character?.portraitFile) references.push(join(projectDir(cfg, state.id), character.portraitFile))
      }

      const relativeBase = `${epFolder}/frames/beat_${String(beat.index).padStart(3, '0')}`
      const absolute = await generateImageToFile(
        cfg,
        framePrompt(beat, scene, state.characters, cfg.aspectRatio),
        join(projectDir(cfg, state.id), relativeBase),
        { size: cfg.frameSize, referencePaths: references, signal },
      )
      beat.frameFile = `${relativeBase}${absolute.slice(absolute.lastIndexOf('.'))}`
      generated += 1
      await persist()
    }, signal)

    setStage(episode, 'frames', 'done')
    await saveProject(cfg, state)
    return { episode: episode.index, total: selected.length, generated, skipped }
  } catch (error) {
    setStage(episode, 'frames', 'failed')
    await saveProject(cfg, state).catch(() => undefined)
    throw error
  }
}

export function selectBeats(beats: Beat[], numbers?: number[]): Beat[] {
  if (!numbers || numbers.length === 0) return beats
  const wanted = new Set(numbers)
  const selected = beats.filter((beat) => wanted.has(beat.index))
  if (selected.length !== wanted.size) {
    const missing = [...wanted].filter((number) => !beats.some((beat) => beat.index === number))
    throw new Error(`指定了不存在的 beat：${missing.join(', ')}`)
  }
  return selected
}

function normalizeScenes(raw: unknown[] | undefined): SceneAsset[] {
  if (!Array.isArray(raw)) return []
  return raw
    .map((item) => {
      const entry = (item && typeof item === 'object' ? item : {}) as Record<string, unknown>
      return {
        name: normalizeName(entry.name),
        description: normalizeName(entry.description),
        imagePrompt: normalizeName(entry.imagePrompt),
        imageFile: '',
      }
    })
    .filter((scene) => scene.name)
}

function firstBeatWithScene(state: ProjectState, sceneName: string): Beat | undefined {
  for (const episode of state.episodes) {
    const hit = episode.beats.find((beat) => beat.scene === sceneName)
    if (hit) return hit
  }
  return undefined
}

function fileSlug(name: string): string {
  return name
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, '-')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^[.\s]+|[.\s]+$/g, '')
    .slice(0, 60) || 'item'
}
