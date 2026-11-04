import { mkdir, readdir, readFile, rename, rm, stat, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import type { ResolvedConfig } from './config.js'

export type WorkspaceTarget = Pick<ResolvedConfig, 'workspaceDir'>

export type StageStatus = 'pending' | 'running' | 'done' | 'failed'
export type ProjectStage = 'ingest' | 'episodes' | 'cast' | 'scenes'
export type EpisodeStage = 'script' | 'frames' | 'videos' | 'audio' | 'compose'

export interface DialogueLine {
  speaker: string
  text: string
}

export interface Beat {
  index: number
  title: string
  scene: string
  characters: string[]
  action: string
  dialogue: DialogueLine[]
  narration: string
  videoPrompt: string
  durationSec: number
  frameFile?: string
  videoFile?: string
  audioFile?: string
}

export interface IngestBible {
  title: string
  logline: string
  genre: string
  setting: string
  characters: Array<{
    name: string
    role: string
    age: string
    appearance: string
    personality: string
  }>
  chapters: Array<{ title: string; summary: string }>
}

export interface CharacterAsset {
  name: string
  role: string
  appearance: string
  personality: string
  portraitPrompt: string
  portraitFile: string
}

export interface SceneAsset {
  name: string
  description: string
  imagePrompt: string
  imageFile: string
}

export interface EpisodeState {
  index: number
  title: string
  summary: string
  hook: string
  keyEvents: string[]
  scriptFile: string
  beats: Beat[]
  srtFile?: string
  finalFile?: string
  stages: Record<EpisodeStage, StageStatus>
}

export interface ProjectState {
  version: 1
  id: string
  title: string
  createdAt: string
  updatedAt: string
  sourceFile: string
  sourceChars: number
  projectStages: Record<ProjectStage, StageStatus>
  ingest: IngestBible | null
  characters: CharacterAsset[]
  scenes: SceneAsset[]
  episodes: EpisodeState[]
}

const STAGE_DEFAULTS: Record<EpisodeStage, StageStatus> = {
  script: 'pending',
  frames: 'pending',
  videos: 'pending',
  audio: 'pending',
  compose: 'pending',
}

export function defaultEpisodeStages(): Record<EpisodeStage, StageStatus> {
  return { ...STAGE_DEFAULTS }
}

export async function createProject(
  cfg: WorkspaceTarget,
  rawId: string,
  title: string,
  source: string,
): Promise<ProjectState> {
  const id = sanitizeId(rawId)
  const dir = projectDir(cfg, id)
  await mkdir(dir, { recursive: true })
  const sourceFile = 'source.txt'
  await writeText(join(dir, sourceFile), source)

  const now = new Date().toISOString()
  const state: ProjectState = {
    version: 1,
    id,
    title: title || rawId,
    createdAt: now,
    updatedAt: now,
    sourceFile,
    sourceChars: source.length,
    projectStages: { ingest: 'pending', episodes: 'pending', cast: 'pending', scenes: 'pending' },
    ingest: null,
    characters: [],
    scenes: [],
    episodes: [],
  }
  await saveProject(cfg, state)
  return state
}

export async function loadProject(cfg: WorkspaceTarget, rawId: string): Promise<ProjectState> {
  const id = sanitizeId(rawId)
  const file = join(projectDir(cfg, id), 'project.json')
  try {
    const raw = await readFile(file, 'utf8')
    const state = JSON.parse(raw) as ProjectState
    if (state.version !== 1 || !Array.isArray(state.episodes)) {
      throw new Error('project.json 结构不兼容')
    }
    return state
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error)
    throw new Error(`项目 ${id} 不存在或已损坏：${detail}。请先用 drama_init 创建项目。`)
  }
}

export async function saveProject(cfg: WorkspaceTarget, state: ProjectState): Promise<void> {
  state.updatedAt = new Date().toISOString()
  await writeText(join(projectDir(cfg, state.id), 'project.json'), `${JSON.stringify(state, null, 2)}\n`)
}

export function projectDir(cfg: WorkspaceTarget, id: string): string {
  return join(cfg.workspaceDir, sanitizeId(id))
}

export function episodeDir(cfg: WorkspaceTarget, projectId: string, episode: number): string {
  return join(projectDir(cfg, projectId), `ep${String(episode).padStart(3, '0')}`)
}

export function mediaDir(cfg: WorkspaceTarget, projectId: string, ...parts: string[]): string {
  return join(projectDir(cfg, projectId), ...parts)
}

export function ensureEpisodeStages(episode: EpisodeState): void {
  episode.stages = { ...STAGE_DEFAULTS, ...(episode.stages || {}) }
}

export async function readSource(cfg: WorkspaceTarget, state: ProjectState): Promise<string> {
  return readFile(join(projectDir(cfg, state.id), state.sourceFile), 'utf8')
}

export async function writeJsonFile(path: string, value: unknown): Promise<void> {
  await writeText(path, `${JSON.stringify(value, null, 2)}\n`)
}

export async function readJsonFile<T>(path: string): Promise<T> {
  return JSON.parse(await readFile(path, 'utf8')) as T
}

export async function writeText(path: string, content: string): Promise<void> {
  await mkdir(dirname(path), { recursive: true })
  const temp = `${path}.tmp-${process.pid}-${Date.now()}`
  await writeFile(temp, content, 'utf8')
  try {
    await rename(temp, path)
  } catch {
    await rm(path, { force: true })
    await rename(temp, path)
  }
}

export async function exists(path: string): Promise<boolean> {
  try {
    await stat(path)
    return true
  } catch {
    return false
  }
}

export function sanitizeId(raw: string): string {
  const value = String(raw || '').trim()
  if (!value) throw new Error('项目 id 不能为空')
  const cleaned = value
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, '-')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^[.\s]+|[.\s]+$/g, '')
    .slice(0, 80)
  if (!cleaned) throw new Error(`项目 id 不合法：${value}`)
  return cleaned
}

export async function listProjects(cfg: WorkspaceTarget): Promise<ProjectState[]> {
  let entries
  try {
    entries = await readdir(cfg.workspaceDir, { withFileTypes: true })
  } catch {
    return []
  }

  const projects: ProjectState[] = []
  for (const entry of entries) {
    if (!entry.isDirectory()) continue
    try {
      projects.push(await loadProject(cfg, entry.name))
    } catch {
      // ignore folders that are not valid project workspaces
    }
  }
  return projects.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
}

export function absoluteProjectPath(cfg: WorkspaceTarget, state: ProjectState): string {
  return resolve(projectDir(cfg, state.id))
}
