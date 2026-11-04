import { join } from 'node:path'
import type { ResolvedConfig } from '../config.js'
import { videoPromptText } from '../prompts.js'
import { synthesizeSpeech } from '../tts.js'
import { generateVideo } from '../video.js'
import type { Beat, EpisodeState, ProjectState } from '../workspace.js'
import { projectDir, saveProject, writeText } from '../workspace.js'
import {
  beatVoiceText,
  buildSrt,
  createPersistQueue,
  mapLimit,
  pickEpisode,
  setStage,
} from './shared.js'
import { selectBeats } from './assets.js'

export interface VideosReport {
  episode: number
  total: number
  generated: number
  skipped: number
  files: string[]
}

export async function runVideos(
  cfg: ResolvedConfig,
  state: ProjectState,
  episodeNumber: number,
  beatNumbers: number[] | undefined,
  force: boolean,
  signal?: AbortSignal,
): Promise<VideosReport> {
  const episode = pickEpisode(state, episodeNumber)
  if (episode.beats.length === 0) {
    throw new Error(`第 ${episode.index} 集还没有剧本，请先调用 drama_script。`)
  }

  const selected = selectBeats(episode.beats, beatNumbers)
  const missingFrames = selected.filter((beat) => !beat.frameFile).map((beat) => beat.index)
  if (missingFrames.length > 0) {
    throw new Error(`以下 beat 还没有首帧：${missingFrames.join(', ')}。请先调用 drama_frames。`)
  }

  setStage(episode, 'videos', 'running')
  await saveProject(cfg, state)

  try {
    let generated = 0
    let skipped = 0
    const files: string[] = []
    const persist = createPersistQueue(cfg, state)
    const epFolder = `ep${String(episode.index).padStart(3, '0')}`

    await mapLimit(selected, cfg.concurrency, async (beat) => {
      if (beat.videoFile && !force) {
        skipped += 1
        files.push(beat.videoFile)
        return
      }

      const relativeBase = `${epFolder}/videos/beat_${String(beat.index).padStart(3, '0')}`
      const result = await generateVideo(
        cfg,
        {
          prompt: videoPromptText(beat, cfg.aspectRatio),
          firstFramePath: join(projectDir(cfg, state.id), beat.frameFile as string),
          durationSec: beat.durationSec,
          aspectRatio: cfg.aspectRatio,
          signal,
        },
        join(projectDir(cfg, state.id), relativeBase),
      )
      const ext = result.file.slice(result.file.lastIndexOf('.'))
      beat.videoFile = `${relativeBase}${ext}`
      generated += 1
      files.push(beat.videoFile)
      await persist()
    }, signal)

    setStage(episode, 'videos', 'done')
    await saveProject(cfg, state)
    return { episode: episode.index, total: selected.length, generated, skipped, files }
  } catch (error) {
    setStage(episode, 'videos', 'failed')
    await saveProject(cfg, state).catch(() => undefined)
    throw error
  }
}

export interface AudioReport {
  episode: number
  total: number
  generated: number
  skipped: number
  srtFile: string
}

export async function runAudio(
  cfg: ResolvedConfig,
  state: ProjectState,
  episodeNumber: number,
  force: boolean,
  signal?: AbortSignal,
): Promise<AudioReport> {
  const episode = pickEpisode(state, episodeNumber)
  if (episode.beats.length === 0) {
    throw new Error(`第 ${episode.index} 集还没有剧本，请先调用 drama_script。`)
  }

  setStage(episode, 'audio', 'running')
  await saveProject(cfg, state)

  try {
    let generated = 0
    let skipped = 0
    const persist = createPersistQueue(cfg, state)
    const epFolder = `ep${String(episode.index).padStart(3, '0')}`
    const withSpeech = episode.beats.filter((beat) => beatVoiceText(beat))

    await mapLimit(withSpeech, cfg.concurrency, async (beat) => {
      if (beat.audioFile && !force) {
        skipped += 1
        return
      }
      const relativeBase = `${epFolder}/audio/beat_${String(beat.index).padStart(3, '0')}`
      const absolute = await synthesizeSpeech(
        cfg,
        beatVoiceText(beat),
        join(projectDir(cfg, state.id), relativeBase),
        { voice: cfg.ttsVoice, signal },
      )
      beat.audioFile = `${relativeBase}${absolute.slice(absolute.lastIndexOf('.'))}`
      generated += 1
      await persist()
    }, signal)

    const srtFile = `${epFolder}/subtitles.srt`
    await writeText(join(projectDir(cfg, state.id), srtFile), buildSrt(episode.beats))
    episode.srtFile = srtFile
    setStage(episode, 'audio', 'done')
    await saveProject(cfg, state)

    return { episode: episode.index, total: withSpeech.length, generated, skipped, srtFile }
  } catch (error) {
    setStage(episode, 'audio', 'failed')
    await saveProject(cfg, state).catch(() => undefined)
    throw error
  }
}

export function beatsOf(episode: EpisodeState): Beat[] {
  return episode.beats
}
