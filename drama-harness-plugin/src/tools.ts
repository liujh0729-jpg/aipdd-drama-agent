import { readFile, stat } from 'node:fs/promises'
import { isAbsolute, join, resolve } from 'node:path'
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { resolveConfig, resolveWorkspaceDir, type PluginConfig, type ResolvedConfig } from './config.js'
import { ffmpegAvailable, runCompose } from './compose.js'
import { analyzeProjectStages, registerAll } from './tools-support.js'
import { resolveAllModels } from './models.js'
import { runCast, runFrames, runScenes } from './pipeline/assets.js'
import { runPlanEpisodes } from './pipeline/episodes.js'
import { runIngest } from './pipeline/ingest.js'
import { runAudio, runVideos } from './pipeline/render.js'
import { runScript } from './pipeline/script.js'
import { pickEpisode } from './pipeline/shared.js'
import type { ProjectState } from './workspace.js'
import {
  absoluteProjectPath,
  createProject,
  exists,
  listProjects,
  loadProject,
  projectDir,
  sanitizeId,
} from './workspace.js'

const projectIdParam = {
  type: 'string',
  description: '项目 id，例如 my-drama；会作为工作区子目录名',
  required: true,
} as const

const episodeParam = {
  type: 'integer',
  description: '集数，从 1 开始（对应 ep001、ep002…）',
  required: true,
} as const

const beatNumbersParam = {
  type: 'array',
  items: { type: 'integer' },
  description: '只处理指定 beat 序号；省略表示整集',
} as const

const forceParam = {
  type: 'boolean',
  description: 'true 表示覆盖已有产物/状态；默认 false 跳过已完成项',
} as const

const text = (value: string): Array<{ type: 'text'; text: string }> => [{ type: 'text', text: value }]

const errorText = (error: unknown): string => (error instanceof Error ? error.message : String(error))

function configOf(raw: PluginConfig): ResolvedConfig {
  return resolveConfig(raw)
}

export function registerTools(ctx: Context, raw: PluginConfig): void {
  registerAll(ctx, [
    defineTool({
      name: 'drama_check',
      description:
        '检查短剧插件配置：验证 apiKey/baseUrl/gateway，列出网关可用模型并自动匹配文本、图片、视频、语音模型，同时检测 ffmpeg。' +
        '用户只配置了 API Key 时，先调用它确认哪些能力可用。',
      parameters: {},
      timeoutMs: 90_000,
      isConcurrencySafe: () => true,
      output: {
        schema: {
          type: 'object',
          additionalProperties: false,
          properties: {
            ok: { type: 'boolean' },
            gateway: { type: 'string' },
            baseUrl: { type: 'string' },
            workspaceDir: { type: 'string' },
            models: {
              type: 'object',
              additionalProperties: false,
              properties: {
                text: { type: 'string' },
                image: { type: 'string' },
                video: { type: 'string' },
                tts: { type: 'string' },
              },
            },
            ffmpeg: { type: 'string' },
            note: { type: 'string' },
          },
        },
        render: (_args, value) => {
          const models = value.models ?? { text: '', image: '', video: '', tts: '' }
          const lines = [
            `配置检查：${value.ok ? '可用' : '不可用'}`,
            `网关：${value.gateway || '（未配置）'}  ${value.baseUrl || ''}`.trim(),
            `工作区：${value.workspaceDir}`,
            `文本模型：${models.text || '（未匹配）'}`,
            `图片模型：${models.image || '（未匹配）'}`,
            `视频模型：${models.video || '（未匹配）'}`,
            `语音模型：${models.tts || '（未匹配）'}`,
            `ffmpeg：${value.ffmpeg || '未找到（不影响逐段视频，只影响最终合成）'}`,
            `说明：${value.note}`,
          ]
          return text(lines.join('\n'))
        },
      },
      execute: async (_args, exec) => {
        const workspaceDir = resolveWorkspaceDir(raw)
        let cfg: ResolvedConfig
        try {
          cfg = configOf(raw)
        } catch (error) {
          return {
            ok: false,
            gateway: '',
            baseUrl: '',
            workspaceDir,
            models: { text: '', image: '', video: '', tts: '' },
            ffmpeg: '',
            note: errorText(error),
          }
        }

        const notes: string[] = []
        const models = { text: '', image: '', video: '', tts: '' }
        try {
          const resolved = await resolveAllModels(cfg, exec.signal)
          models.text = resolved.text || ''
          models.image = resolved.image || ''
          models.video = resolved.video || ''
          models.tts = resolved.tts || ''
        } catch (error) {
          notes.push(errorText(error))
        }

        let ffmpeg = ''
        try {
          ffmpeg = await ffmpegAvailable(cfg)
        } catch {
          ffmpeg = ''
        }

        const ok = Boolean(models.text && models.image && models.video)
        if (ok) {
          notes.push('文本、图片、视频模型已就绪')
        } else {
          notes.push('缺少文本/图片/视频模型时，请在配置里手动填写 textModel/imageModel/videoModel')
        }
        if (!models.tts) notes.push('未匹配到语音模型；可配置 ttsModel，或跳过 drama_audio')
        if (!ffmpeg) notes.push('未找到 ffmpeg；drama_compose 会输出合成清单而不产出 mp4')

        return { ok, gateway: cfg.gateway, baseUrl: cfg.baseUrl, workspaceDir: cfg.workspaceDir, models, ffmpeg, note: notes.join('；') }
      },
    }),

    defineTool({
      name: 'drama_init',
      description:
        '创建（或复用）一个短剧项目，把小说正文保存到本地工作区并完成解析：生成故事设定集、人物表和章节摘要。' +
        '首次调用需要提供 sourceText（正文）或 sourcePath（本地 txt/md 路径）。解析失败后可不带正文再次调用以重试。' +
        '完成后用 drama_plan_episodes 规划分集。',
      parameters: {
        projectId: projectIdParam,
        title: { type: 'string', description: '项目标题；省略时使用解析出的小说名' },
        sourceText: { type: 'string', description: '小说正文（与 sourcePath 二选一）' },
        sourcePath: { type: 'string', description: '本地小说文件路径（与 sourceText 二选一）' },
        force: forceParam,
      },
      timeoutMs: 30 * 60 * 1000,
      output: {
        schema: {
          type: 'object',
          additionalProperties: false,
          properties: {
            projectId: { type: 'string' },
            title: { type: 'string' },
            sourceChars: { type: 'number' },
            characters: { type: 'number' },
            chapters: { type: 'number' },
            chunks: { type: 'number' },
            truncated: { type: 'boolean' },
            projectDir: { type: 'string' },
            note: { type: 'string' },
          },
        },
        render: (_args, value) =>
          text(
            [
              `项目已就绪：${value.projectId}《${value.title}》`,
              `目录：${value.projectDir}`,
              `正文 ${value.sourceChars} 字${(value.chunks ?? 0) > 0 ? `，解析 ${value.chunks} 段${value.truncated ? '（超出上限，已截断）' : ''}` : ''}`,
              `人物 ${value.characters} 个，章节 ${value.chapters} 个`,
              `下一步：drama_plan_episodes(projectId="${value.projectId}")`,
              value.note ? `备注：${value.note}` : '',
            ]
              .filter(Boolean)
              .join('\n'),
          ),
      },
      execute: async (args, exec) => {
        const cfg = configOf(raw)
        const projectId = sanitizeId(args.projectId)
        const dir = projectDir(cfg, projectId)
        const projectExists = await exists(join(dir, 'project.json'))

        if (args.sourceText && args.sourcePath) {
          throw new Error('sourceText 和 sourcePath 只能提供一个')
        }

        let state: ProjectState
        let note = ''

        if (!projectExists) {
          const source = await readSourceInput(args.sourceText, args.sourcePath)
          state = await createProject(cfg, projectId, args.title || projectId, source)
        } else {
          state = await loadProject(cfg, projectId)
          const hasSource = Boolean(args.sourceText || args.sourcePath)
          if (hasSource && !args.force) {
            throw new Error(`项目 ${projectId} 已存在。要替换正文请传 force=true；要重试解析请不要传 sourceText/sourcePath。`)
          }
          if (hasSource) {
            const source = await readSourceInput(args.sourceText, args.sourcePath)
            state = await createProject(cfg, projectId, args.title || state.title || projectId, source)
            note = '已按 force=true 覆盖项目正文'
          } else if (state.projectStages.ingest === 'done' && !args.force) {
            note = '项目已存在且解析完成，未重复调用模型；force=true 可重新解析'
          }
        }

        let report: Awaited<ReturnType<typeof runIngest>> | null = null
        if (!note) {
          report = await runIngest(cfg, state, exec.signal)
        }

        return {
          projectId: state.id,
          title: state.title,
          sourceChars: state.sourceChars,
          characters: report?.characters ?? state.ingest?.characters.length ?? 0,
          chapters: report?.chapters ?? state.ingest?.chapters.length ?? 0,
          chunks: report?.chunks ?? 0,
          truncated: report?.truncated ?? false,
          projectDir: absoluteProjectPath(cfg, state),
          note,
        }
      },
    }),

    defineTool({
      name: 'drama_plan_episodes',
      description:
        '根据已解析的设定集规划分集（标题、剧情摘要、结尾钩子、关键事件）。' +
        '会替换现有分集，因此已有分集时需要 force=true。完成后对每集调用 drama_script。',
      parameters: {
        projectId: projectIdParam,
        count: { type: 'integer', description: '规划集数，默认 10，范围 1-50' },
        force: forceParam,
      },
      timeoutMs: 10 * 60 * 1000,
      output: {
        schema: {
          type: 'object',
          additionalProperties: false,
          properties: {
            projectId: { type: 'string' },
            total: { type: 'number' },
            episodes: {
              type: 'array',
              items: {
                type: 'object',
                additionalProperties: false,
                properties: {
                  index: { type: 'number' },
                  title: { type: 'string' },
                  summary: { type: 'string' },
                  hook: { type: 'string' },
                },
              },
            },
            projectDir: { type: 'string' },
            note: { type: 'string' },
          },
        },
        render: (_args, value) => {
          const episodes = value.episodes ?? []
          const lines = [`已规划 ${value.total} 集：`]
          for (const episode of episodes) {
            lines.push(`第 ${episode.index} 集《${episode.title}》：${episode.summary || '（无摘要）'}`)
            if (episode.hook) lines.push(`  钩子：${episode.hook}`)
          }
          lines.push(`下一步：drama_script(projectId="${value.projectId}", episode=1)`)
          if (value.note) lines.push(value.note)
          return text(lines.join('\n'))
        },
      },
      execute: async (args, exec) => {
        const cfg = configOf(raw)
        const state = await loadProject(cfg, args.projectId)
        if (state.episodes.length > 0 && !args.force) {
          throw new Error(`项目已规划 ${state.episodes.length} 集；重新规划会清空现有剧本，请传 force=true。`)
        }
        const count = Math.min(50, Math.max(1, Math.round(args.count ?? 10)))
        const episodes = await runPlanEpisodes(cfg, state, count, exec.signal)
        return {
          projectId: state.id,
          total: episodes.length,
          episodes: episodes.map((episode) => ({
            index: episode.index,
            title: episode.title,
            summary: episode.summary,
            hook: episode.hook,
          })),
          projectDir: absoluteProjectPath(cfg, state),
          note: '',
        }
      },
    }),

    defineTool({
      name: 'drama_script',
      description:
        '把指定集写成可拍摄的 beat 列表（场景、人物、动作、对白、旁白、视频提示词、时长）。' +
        '已有剧本时需要 force=true 才会重写；重写会重置该集的参考帧、视频、配音和合成状态。',
      parameters: {
        projectId: projectIdParam,
        episode: episodeParam,
        beats: { type: 'integer', description: '目标 beat 数，默认 12，范围 4-40' },
        force: forceParam,
      },
      timeoutMs: 10 * 60 * 1000,
      output: {
        schema: {
          type: 'object',
          additionalProperties: false,
          properties: {
            projectId: { type: 'string' },
            episode: { type: 'number' },
            beats: { type: 'number' },
            scriptFile: { type: 'string' },
            markdownFile: { type: 'string' },
            projectDir: { type: 'string' },
            note: { type: 'string' },
          },
        },
        render: (_args, value) =>
          text(
            [
              `第 ${value.episode} 集剧本已完成，共 ${value.beats} 个 beat`,
              `目录：${value.projectDir}`,
              `分镜：${value.scriptFile}`,
              `可读稿：${value.markdownFile}`,
              `下一步：drama_frames(projectId="${value.projectId}", episode=${value.episode})`,
            ].join('\n'),
          ),
      },
      execute: async (args, exec) => {
        const cfg = configOf(raw)
        const state = await loadProject(cfg, args.projectId)
        const episode = pickEpisode(state, args.episode)
        if (episode.beats.length > 0 && !args.force) {
          throw new Error(`第 ${episode.index} 集已有 ${episode.beats.length} 个 beat；重写请传 force=true。`)
        }
        const target = Math.min(40, Math.max(4, Math.round(args.beats ?? 12)))
        const report = await runScript(cfg, state, args.episode, target, exec.signal)
        return {
          projectId: state.id,
          episode: report.episode,
          beats: report.beats,
          scriptFile: report.scriptFile,
          markdownFile: report.markdownFile,
          projectDir: absoluteProjectPath(cfg, state),
          note: '',
        }
      },
    }),

    defineTool({
      name: 'drama_cast',
      description:
        '为设定集里的人物生成角色定妆照（肖像参考图），保存在 characters/portraits/。' +
        '已经生成的角色默认跳过；force=true 全部重画。',
      parameters: {
        projectId: projectIdParam,
        force: forceParam,
      },
      timeoutMs: 30 * 60 * 1000,
      output: {
        schema: {
          type: 'object',
          additionalProperties: false,
          properties: {
            projectId: { type: 'string' },
            characters: { type: 'number' },
            generated: { type: 'number' },
            skipped: { type: 'number' },
            items: {
              type: 'array',
              items: {
                type: 'object',
                additionalProperties: false,
                properties: { name: { type: 'string' }, file: { type: 'string' } },
              },
            },
            projectDir: { type: 'string' },
            note: { type: 'string' },
          },
        },
        render: (_args, value) => {
          const items = value.items ?? []
          const lines = [
            `角色定妆照：共 ${value.characters} 人，新生成 ${value.generated}，跳过 ${value.skipped}`,
            `目录：${value.projectDir}`,
          ]
          for (const item of items) lines.push(`- ${item.name}：${item.file || '（未生成）'}`)
          lines.push('下一步：drama_scenes')
          return text(lines.join('\n'))
        },
      },
      execute: async (args, exec) => {
        const cfg = configOf(raw)
        const state = await loadProject(cfg, args.projectId)
        const report = await runCast(cfg, state, Boolean(args.force), exec.signal)
        return {
          projectId: state.id,
          characters: report.characters,
          generated: report.generated,
          skipped: report.skipped,
          items: state.characters.map((character) => ({ name: character.name, file: character.portraitFile })),
          projectDir: absoluteProjectPath(cfg, state),
          note: '',
        }
      },
    }),

    defineTool({
      name: 'drama_scenes',
      description:
        '根据剧本汇总场景并生成场景参考图，保存在 scenes/。' +
        '已生成的场景默认跳过；force=true 全部重画。',
      parameters: {
        projectId: projectIdParam,
        force: forceParam,
      },
      timeoutMs: 30 * 60 * 1000,
      output: {
        schema: {
          type: 'object',
          additionalProperties: false,
          properties: {
            projectId: { type: 'string' },
            scenes: { type: 'number' },
            generated: { type: 'number' },
            skipped: { type: 'number' },
            items: {
              type: 'array',
              items: {
                type: 'object',
                additionalProperties: false,
                properties: { name: { type: 'string' }, file: { type: 'string' } },
              },
            },
            projectDir: { type: 'string' },
            note: { type: 'string' },
          },
        },
        render: (_args, value) => {
          const items = value.items ?? []
          const lines = [
            `场景参考图：共 ${value.scenes} 个，新生成 ${value.generated}，跳过 ${value.skipped}`,
            `目录：${value.projectDir}`,
          ]
          for (const item of items) lines.push(`- ${item.name}：${item.file || '（未生成）'}`)
          lines.push('下一步：drama_frames(projectId="' + value.projectId + '", episode=1)')
          return text(lines.join('\n'))
        },
      },
      execute: async (args, exec) => {
        const cfg = configOf(raw)
        const state = await loadProject(cfg, args.projectId)
        const report = await runScenes(cfg, state, Boolean(args.force), exec.signal)
        return {
          projectId: state.id,
          scenes: report.scenes,
          generated: report.generated,
          skipped: report.skipped,
          items: state.scenes.map((scene) => ({ name: scene.name, file: scene.imageFile })),
          projectDir: absoluteProjectPath(cfg, state),
          note: '',
        }
      },
    }),

    defineTool({
      name: 'drama_frames',
      description:
        '为指定集的 beat 生成首帧参考图：以场景图和人物定妆照作为参考，保证人物与场景一致。' +
        '若角色或场景还没生成，会自动补齐。保存到 epXXX/frames/。已有首帧默认跳过；force=true 重画。',
      parameters: {
        projectId: projectIdParam,
        episode: episodeParam,
        beats: beatNumbersParam,
        force: forceParam,
      },
      timeoutMs: 60 * 60 * 1000,
      output: {
        schema: {
          type: 'object',
          additionalProperties: false,
          properties: {
            projectId: { type: 'string' },
            episode: { type: 'number' },
            total: { type: 'number' },
            generated: { type: 'number' },
            skipped: { type: 'number' },
            projectDir: { type: 'string' },
            note: { type: 'string' },
          },
        },
        render: (_args, value) =>
          text(
            [
              `第 ${value.episode} 集首帧：${value.total} 个 beat，新生成 ${value.generated}，跳过 ${value.skipped}`,
              `目录：${value.projectDir}`,
              `下一步：drama_videos(projectId="${value.projectId}", episode=${value.episode})`,
            ].join('\n'),
          ),
      },
      execute: async (args, exec) => {
        const cfg = configOf(raw)
        const state = await loadProject(cfg, args.projectId)
        const report = await runFrames(cfg, state, args.episode, args.beats, Boolean(args.force), exec.signal)
        return {
          projectId: state.id,
          episode: report.episode,
          total: report.total,
          generated: report.generated,
          skipped: report.skipped,
          projectDir: absoluteProjectPath(cfg, state),
          note: '',
        }
      },
    }),

    defineTool({
      name: 'drama_videos',
      description:
        '把指定集的 beat 首帧生成为视频片段，保存在 epXXX/videos/。视频接口是异步任务，本工具会轮询到完成或超时。' +
        '已有视频默认跳过；force=true 重新生成。单个 beat 失败会明确报错，已完成片段会保留，可重跑续做。',
      parameters: {
        projectId: projectIdParam,
        episode: episodeParam,
        beats: beatNumbersParam,
        force: forceParam,
      },
      timeoutMs: 2 * 60 * 60 * 1000,
      output: {
        schema: {
          type: 'object',
          additionalProperties: false,
          properties: {
            projectId: { type: 'string' },
            episode: { type: 'number' },
            total: { type: 'number' },
            generated: { type: 'number' },
            skipped: { type: 'number' },
            files: { type: 'array', items: { type: 'string' } },
            projectDir: { type: 'string' },
            note: { type: 'string' },
          },
        },
        render: (_args, value) => {
          const files = value.files ?? []
          const lines = [
            `第 ${value.episode} 集视频片段：${value.total} 个 beat，新生成 ${value.generated}，跳过 ${value.skipped}`,
            `目录：${value.projectDir}`,
          ]
          for (const file of files) lines.push(`- ${file}`)
          lines.push(`下一步：drama_audio(projectId="${value.projectId}", episode=${value.episode})`)
          return text(lines.join('\n'))
        },
      },
      execute: async (args, exec) => {
        const cfg = configOf(raw)
        const state = await loadProject(cfg, args.projectId)
        const report = await runVideos(cfg, state, args.episode, args.beats, Boolean(args.force), exec.signal)
        return {
          projectId: state.id,
          episode: report.episode,
          total: report.total,
          generated: report.generated,
          skipped: report.skipped,
          files: report.files,
          projectDir: absoluteProjectPath(cfg, state),
          note: '',
        }
      },
    }),

    defineTool({
      name: 'drama_audio',
      description:
        '为指定集的 beat 生成配音（旁白 + 对白），并写出 subtitles.srt。保存在 epXXX/audio/。' +
        '没有台词的 beat 会跳过。已有配音默认跳过；force=true 重新生成。',
      parameters: {
        projectId: projectIdParam,
        episode: episodeParam,
        force: forceParam,
      },
      timeoutMs: 60 * 60 * 1000,
      output: {
        schema: {
          type: 'object',
          additionalProperties: false,
          properties: {
            projectId: { type: 'string' },
            episode: { type: 'number' },
            total: { type: 'number' },
            generated: { type: 'number' },
            skipped: { type: 'number' },
            srtFile: { type: 'string' },
            projectDir: { type: 'string' },
            note: { type: 'string' },
          },
        },
        render: (_args, value) =>
          text(
            [
              `第 ${value.episode} 集配音：${value.total} 段，新生成 ${value.generated}，跳过 ${value.skipped}`,
              `字幕：${value.srtFile}`,
              `目录：${value.projectDir}`,
              `下一步：drama_compose(projectId="${value.projectId}", episode=${value.episode})`,
            ].join('\n'),
          ),
      },
      execute: async (args, exec) => {
        const cfg = configOf(raw)
        const state = await loadProject(cfg, args.projectId)
        const report = await runAudio(cfg, state, args.episode, Boolean(args.force), exec.signal)
        return {
          projectId: state.id,
          episode: report.episode,
          total: report.total,
          generated: report.generated,
          skipped: report.skipped,
          srtFile: report.srtFile,
          projectDir: absoluteProjectPath(cfg, state),
          note: '',
        }
      },
    }),

    defineTool({
      name: 'drama_compose',
      description:
        '把指定集的 beat 视频、配音和字幕合成为最终 mp4（epXXX/final.mp4）。' +
        '需要本机有 ffmpeg（可配置 ffmpegPath 或加入 PATH）；没有 ffmpeg 时会写出 compose.json 清单并保留全部逐段产物，不会自动安装任何东西。' +
        '已有成片默认跳过；force=true 重新合成。',
      parameters: {
        projectId: projectIdParam,
        episode: episodeParam,
        force: forceParam,
        embedSubtitles: { type: 'boolean', description: '是否把 SRT 作为软字幕嵌入 mp4，默认 true' },
      },
      timeoutMs: 60 * 60 * 1000,
      output: {
        schema: {
          type: 'object',
          additionalProperties: false,
          properties: {
            projectId: { type: 'string' },
            episode: { type: 'number' },
            composed: { type: 'boolean' },
            outputFile: { type: 'string' },
            manifestFile: { type: 'string' },
            srtFile: { type: 'string' },
            ffmpeg: { type: 'string' },
            reason: { type: 'string' },
            segments: { type: 'number' },
            projectDir: { type: 'string' },
            note: { type: 'string' },
          },
        },
        render: (_args, value) => {
          const dir = value.projectDir || ''
          return text(
            [
              value.composed ? `第 ${value.episode} 集成片已生成` : `第 ${value.episode} 集未合成 mp4`,
              value.outputFile ? `成片：${join(dir, value.outputFile)}` : '',
              `合成清单：${join(dir, value.manifestFile || '')}`,
              value.srtFile ? `字幕：${join(dir, value.srtFile)}` : '',
              `ffmpeg：${value.ffmpeg || '未找到'}`,
              `片段数：${value.segments}`,
              `说明：${value.reason}`,
            ]
              .filter(Boolean)
              .join('\n'),
          )
        },
      },
      execute: async (args, exec) => {
        const cfg = configOf(raw)
        const state = await loadProject(cfg, args.projectId)
        const report = await runCompose(cfg, state, args.episode, {
          force: Boolean(args.force),
          embedSubtitles: args.embedSubtitles ?? true,
          signal: exec.signal,
        })
        return {
          projectId: state.id,
          episode: report.episode,
          composed: report.composed,
          outputFile: report.outputFile,
          manifestFile: report.manifestFile,
          srtFile: report.srtFile,
          ffmpeg: report.ffmpeg,
          reason: report.reason,
          segments: report.segments,
          projectDir: absoluteProjectPath(cfg, state),
          note: '',
        }
      },
    }),

    defineTool({
      name: 'drama_status',
      description:
        '查看本地短剧工作区：项目列表、各阶段状态、人物/场景数量、每集剧本/视频/配音/成片情况和下一步建议。' +
        '不传 projectId 时列出全部项目；不需要 API Key，也不调用模型。',
      parameters: {
        projectId: { type: 'string', description: '只看某个项目；省略则列出工作区全部项目' },
      },
      timeoutMs: 30_000,
      isConcurrencySafe: () => true,
      output: {
        schema: {
          type: 'object',
          additionalProperties: false,
          properties: {
            workspaceDir: { type: 'string' },
            projects: {
              type: 'array',
              items: {
                type: 'object',
                additionalProperties: false,
                properties: {
                  id: { type: 'string' },
                  title: { type: 'string' },
                  projectDir: { type: 'string' },
                  updatedAt: { type: 'string' },
                  sourceChars: { type: 'number' },
                  stages: { type: 'json' },
                  characters: { type: 'number' },
                  scenes: { type: 'number' },
                  episodes: {
                    type: 'array',
                    items: {
                      type: 'object',
                      additionalProperties: false,
                      properties: {
                        index: { type: 'number' },
                        title: { type: 'string' },
                        beats: { type: 'number' },
                        stages: { type: 'json' },
                        srtFile: { type: 'string' },
                        finalFile: { type: 'string' },
                      },
                    },
                  },
                  next: { type: 'string' },
                },
              },
            },
            note: { type: 'string' },
          },
        },
        render: (_args, value) => {
          const projects = value.projects ?? []
          if (projects.length === 0) {
            return text(`工作区：${value.workspaceDir}\n还没有项目。先调用 drama_init(projectId, sourceText/sourcePath)。`)
          }
          const lines = [`工作区：${value.workspaceDir}`, '']
          for (const project of projects) {
            lines.push(`项目 ${project.id}《${project.title}》`)
            lines.push(`  目录：${project.projectDir}`)
            lines.push(`  更新：${project.updatedAt}`)
            lines.push(`  正文：${project.sourceChars} 字；人物 ${project.characters}；场景 ${project.scenes}`)
            lines.push(`  阶段：${JSON.stringify(project.stages)}`)
            for (const episode of project.episodes ?? []) {
              lines.push(
                `  第 ${episode.index} 集《${episode.title}》：${episode.beats} beats；` +
                  `帧/视频/配音/合成=${JSON.stringify(episode.stages)}` +
                  `${episode.finalFile ? `；成片=${episode.finalFile}` : ''}`,
              )
            }
            lines.push(`  下一步：${project.next}`)
            lines.push('')
          }
          if (value.note) lines.push(value.note)
          return text(lines.join('\n').trim())
        },
      },
      execute: async (args) => {
        const target = { workspaceDir: resolveWorkspaceDir(raw) }
        const projects = args.projectId
          ? [await loadProject(target, args.projectId)]
          : await listProjects(target)
        return {
          workspaceDir: target.workspaceDir,
          projects: projects.map((state) => ({
            id: state.id,
            title: state.title,
            projectDir: absoluteProjectPath(target, state),
            updatedAt: state.updatedAt,
            sourceChars: state.sourceChars,
            stages: { ...state.projectStages },
            characters: state.characters.length,
            scenes: state.scenes.length,
            episodes: state.episodes.map((episode) => ({
              index: episode.index,
              title: episode.title,
              beats: episode.beats.length,
              stages: { ...episode.stages },
              srtFile: episode.srtFile || '',
              finalFile: episode.finalFile || '',
            })),
            next: analyzeProjectStages(state),
          })),
          note: projects.length === 0 ? '工作区还没有项目。' : '',
        }
      },
    }),
  ])
}

async function readSourceInput(sourceText?: string, sourcePath?: string): Promise<string> {
  if (sourceText && sourceText.trim()) {
    if (sourceText.length > 2_000_000) throw new Error('小说正文超过 200 万字，请先拆分后再导入')
    return sourceText.replace(/^\uFEFF/, '')
  }
  if (!sourcePath) {
    throw new Error('请提供 sourceText（正文）或 sourcePath（本地 txt/md 文件）')
  }
  const absolute = isAbsolute(sourcePath) ? sourcePath : resolve(process.cwd(), sourcePath)
  const info = await stat(absolute)
  if (!info.isFile()) throw new Error(`sourcePath 不是文件：${absolute}`)
  if (info.size > 8 * 1024 * 1024) throw new Error(`sourcePath 超过 8MB：${absolute}`)
  const content = await readFile(absolute, 'utf8')
  if (!content.trim()) throw new Error(`sourcePath 是空文件：${absolute}`)
  return content.replace(/^\uFEFF/, '')
}
