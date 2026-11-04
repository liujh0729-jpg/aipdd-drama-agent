import { spawn } from 'node:child_process'
import { mkdir, rm, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import type { ResolvedConfig } from './config.js'
import type { Beat, EpisodeState, ProjectState } from './workspace.js'
import { episodeDir, projectDir, saveProject, writeJsonFile, exists } from './workspace.js'
import { pickEpisode } from './pipeline/shared.js'

export interface ComposeOptions {
  force?: boolean
  embedSubtitles?: boolean
  signal?: AbortSignal
}

export interface ComposeReport {
  episode: number
  composed: boolean
  outputFile: string
  manifestFile: string
  srtFile: string
  ffmpeg: string
  reason: string
  segments: number
}

export async function runCompose(
  cfg: ResolvedConfig,
  state: ProjectState,
  episodeNumber: number,
  options: ComposeOptions = {},
): Promise<ComposeReport> {
  const episode = pickEpisode(state, episodeNumber)
  if (episode.beats.length === 0) {
    throw new Error(`第 ${episode.index} 集还没有剧本，请先调用 drama_script。`)
  }

  const missingVideos = episode.beats.filter((beat) => !beat.videoFile).map((beat) => beat.index)
  if (missingVideos.length > 0) {
    throw new Error(`以下 beat 还没有视频：${missingVideos.join(', ')}。请先调用 drama_videos。`)
  }

  const epFolder = `ep${String(episode.index).padStart(3, '0')}`
  const finalFile = `${epFolder}/final.mp4`
  const manifestFile = `${epFolder}/compose.json`
  const srtFile = episode.srtFile || ''
  const outputAbsolute = join(projectDir(cfg, state.id), finalFile)
  const manifestAbsolute = join(projectDir(cfg, state.id), manifestFile)
  const manifest = buildManifest(cfg, state, episode, finalFile, srtFile)

  if ((await exists(outputAbsolute)) && !options.force) {
    episode.stages.compose = 'done'
    episode.finalFile = finalFile
    await writeJsonFile(manifestAbsolute, manifest)
    await saveProject(cfg, state)
    return {
      episode: episode.index,
      composed: true,
      outputFile: finalFile,
      manifestFile,
      srtFile,
      ffmpeg: '',
      reason: '成片已存在，跳过合成；force=true 可重新生成',
      segments: episode.beats.length,
    }
  }

  episode.stages.compose = 'running'
  await saveProject(cfg, state)

  const ffmpeg = await findFfmpeg(cfg)
  manifest.ffmpeg = ffmpeg
  manifest.note = ffmpeg
    ? '已用 ffmpeg 合成成片；compose/ 中间文件在成功后删除。'
    : '未找到 ffmpeg，未合成 mp4；本清单可用于手动合成或安装 ffmpeg 后重跑 drama_compose。'
  await writeJsonFile(manifestAbsolute, manifest)

  if (!ffmpeg) {
    episode.stages.compose = 'failed'
    await saveProject(cfg, state)
    return {
      episode: episode.index,
      composed: false,
      outputFile: '',
      manifestFile,
      srtFile,
      ffmpeg: '',
      reason:
        '未找到 ffmpeg。请设置插件配置 ffmpegPath，或把 ffmpeg 加入 PATH 后重跑 drama_compose；' +
        '逐 beat 视频、音频和字幕已经保存在项目目录。',
      segments: episode.beats.length,
    }
  }

  const workDir = join(episodeDir(cfg, state.id, episode.index), 'compose')
  await mkdir(workDir, { recursive: true })

  try {
    const segments: string[] = []
    for (let i = 0; i < episode.beats.length; i += 1) {
      const beat = episode.beats[i] as Beat
      const segment = join(workDir, `seg_${String(i + 1).padStart(3, '0')}.mp4`)
      const video = join(projectDir(cfg, state.id), beat.videoFile as string)
      const audio = beat.audioFile ? join(projectDir(cfg, state.id), beat.audioFile) : ''
      await renderSegment(ffmpeg, video, audio, beat.durationSec, cfg.aspectRatio, segment, options.signal)
      segments.push(segment)
    }

    const concatFile = join(workDir, 'concat.txt')
    await writeConcatList(concatFile, segments)
    const srtAbsolute = srtFile ? join(projectDir(cfg, state.id), srtFile) : ''
    const embed = Boolean(options.embedSubtitles) && Boolean(srtAbsolute) && (await exists(srtAbsolute))

    await concatSegments(ffmpeg, concatFile, embed ? srtAbsolute : '', outputAbsolute, options.signal)

    episode.stages.compose = 'done'
    episode.finalFile = finalFile
    await saveProject(cfg, state)
    await rm(workDir, { recursive: true, force: true }).catch(() => undefined)

    return {
      episode: episode.index,
      composed: true,
      outputFile: finalFile,
      manifestFile,
      srtFile,
      ffmpeg,
      reason: embed ? '已合成 mp4（内嵌软字幕）' : '已合成 mp4',
      segments: segments.length,
    }
  } catch (error) {
    episode.stages.compose = 'failed'
    await saveProject(cfg, state).catch(() => undefined)
    throw error
  }
}

interface ComposeManifest {
  episode: number
  createdAt: string
  output: string
  srtFile: string
  ffmpeg: string
  note: string
  segments: Array<{
    index: number
    title: string
    durationSec: number
    videoFile: string
    audioFile: string
  }>
}

function buildManifest(
  cfg: ResolvedConfig,
  state: ProjectState,
  episode: EpisodeState,
  output: string,
  srtFile: string,
): ComposeManifest {
  return {
    episode: episode.index,
    createdAt: new Date().toISOString(),
    output,
    srtFile,
    ffmpeg: '',
    note: '',
    segments: episode.beats.map((beat) => ({
      index: beat.index,
      title: beat.title,
      durationSec: beat.durationSec,
      videoFile: beat.videoFile ? join(projectDir(cfg, state.id), beat.videoFile) : '',
      audioFile: beat.audioFile ? join(projectDir(cfg, state.id), beat.audioFile) : '',
    })),
  }
}

async function findFfmpeg(cfg: ResolvedConfig): Promise<string> {
  const candidates = [cfg.ffmpegPath, 'ffmpeg'].filter(Boolean)
  for (const candidate of candidates) {
    try {
      await runFfmpeg(candidate, ['-version'], undefined, 5_000)
      return candidate
    } catch {
      // try the next candidate
    }
  }
  return ''
}

async function renderSegment(
  ffmpeg: string,
  video: string,
  audio: string,
  durationSec: number,
  aspectRatio: string,
  output: string,
  signal?: AbortSignal,
): Promise<void> {
  const { width, height } = targetDimensions(aspectRatio)
  const args = ['-y', '-hide_banner', '-loglevel', 'error', '-i', video]
  if (audio) {
    args.push('-i', audio)
  } else {
    args.push('-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000')
  }
  args.push(
    '-t',
    String(Math.max(1, durationSec)),
    '-map',
    '0:v:0',
    '-map',
    '1:a:0',
    '-vf',
    `scale=${width}:${height}:force_original_aspect_ratio=decrease,pad=${width}:${height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30`,
    '-c:v',
    'libx264',
    '-preset',
    'veryfast',
    '-crf',
    '23',
    '-pix_fmt',
    'yuv420p',
    '-c:a',
    'aac',
    '-b:a',
    '128k',
    '-ar',
    '48000',
    '-ac',
    '2',
    '-af',
    'apad',
    '-movflags',
    '+faststart',
    output,
  )
  await runFfmpeg(ffmpeg, args, signal)
}

async function concatSegments(
  ffmpeg: string,
  concatFile: string,
  srtFile: string,
  output: string,
  signal?: AbortSignal,
): Promise<void> {
  const args = ['-y', '-hide_banner', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', concatFile]
  if (srtFile) args.push('-i', srtFile)
  args.push('-map', '0:v:0', '-map', '0:a:0?')
  if (srtFile) {
    args.push('-map', '1:0', '-c:s', 'mov_text', '-metadata:s:s:0', 'language=chi')
  }
  args.push('-c:v', 'copy', '-c:a', 'copy', '-movflags', '+faststart', output)
  await runFfmpeg(ffmpeg, args, signal)
}

async function writeConcatList(path: string, files: string[]): Promise<void> {
  const lines = files.map((file) => `file '${file.replace(/\\/g, '/').replace(/'/g, "'\\''")}'`)
  await writeFile(path, `${lines.join('\n')}\n`, 'utf8')
}

function targetDimensions(aspectRatio: string): { width: number; height: number } {
  const value = String(aspectRatio || '9:16').trim()
  const table: Record<string, { width: number; height: number }> = {
    '9:16': { width: 720, height: 1280 },
    '16:9': { width: 1280, height: 720 },
    '1:1': { width: 720, height: 720 },
    '4:3': { width: 960, height: 720 },
    '3:4': { width: 720, height: 960 },
    '4:5': { width: 720, height: 900 },
    '21:9': { width: 1280, height: 548 },
  }
  return table[value] ?? { width: 720, height: 1280 }
}

function runFfmpeg(
  binary: string,
  args: string[],
  signal?: AbortSignal,
  timeoutMs = 30 * 60 * 1000,
): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new Error('操作已取消'))
      return
    }

    const child = spawn(binary, args, { stdio: ['ignore', 'ignore', 'pipe'], windowsHide: true })
    let stderr = ''
    let settled = false

    const finish = (error?: Error) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
      if (error) reject(error)
      else resolve()
    }

    const onAbort = () => {
      child.kill('SIGKILL')
      finish(new Error('操作已取消'))
    }

    const timer = setTimeout(() => {
      child.kill('SIGKILL')
      finish(new Error(`ffmpeg 执行超时（${Math.round(timeoutMs / 1000)}s）`))
    }, timeoutMs)

    signal?.addEventListener('abort', onAbort, { once: true })

    child.stderr?.on('data', (chunk: Buffer) => {
      stderr += chunk.toString('utf8')
      if (stderr.length > 8000) stderr = stderr.slice(-8000)
    })
    child.on('error', (error) => finish(new Error(`无法执行 ffmpeg（${binary}）：${error.message}`)))
    child.on('close', (code) => {
      if (code === 0) finish()
      else finish(new Error(`ffmpeg 失败（exit ${code}）：${stderr.trim().slice(-2000) || '无输出'}`))
    })
  })
}

export async function ffmpegAvailable(cfg: ResolvedConfig): Promise<string> {
  return findFfmpeg(cfg)
}
