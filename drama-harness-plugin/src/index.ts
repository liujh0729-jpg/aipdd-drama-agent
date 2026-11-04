import type { Context } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
import { DEFAULT_WORKSPACE_DIR, type PluginConfig } from './config.js'
import { registerTools } from './tools.js'

export const name = 'drama-harness'

export const inject = ['tools']

export const Config = z
  .object({
    apiKey: z
      .string()
      .default('')
      .description('BYOK 密钥：AIPDD / NewAPI / 火山方舟 / 其他 OpenAI 兼容网关的 API Key'),
    gateway: z
      .union([z.const('aipdd'), z.const('newapi'), z.const('volcengine'), z.const('deepseek'), z.const('custom')])
      .default('aipdd')
      .description('网关预设：aipdd | newapi | volcengine | deepseek | custom'),
    baseUrl: z.string().default('').description('网关根路径（通常以 /v1 结尾）；newapi/custom 必填'),
    textModel: z.string().default('').description('文本模型；留空自动探测'),
    imageModel: z.string().default('').description('图片模型；留空自动探测'),
    videoModel: z.string().default('').description('视频模型；留空自动探测'),
    ttsModel: z.string().default('').description('语音模型；留空自动探测'),
    ttsVoice: z.string().default('alloy').description('OpenAI 兼容语音音色'),
    videoApi: z
      .union([z.const('auto'), z.const('aipdd-videos'), z.const('newapi-jobs'), z.const('ark')])
      .default('auto')
      .description('视频接口协议；auto 跟随网关预设'),
    workspaceDir: z
      .string()
      .default(DEFAULT_WORKSPACE_DIR)
      .description('本地项目工作区，默认 ~/drama-projects；相对路径基于 DSH 启动目录'),
    ffmpegPath: z.string().default('').description('ffmpeg 可执行文件路径；留空则使用 PATH'),
    concurrency: z.natural().min(1).max(8).default(2).description('图片/视频/配音并发数'),
    videoTimeoutSec: z.natural().min(60).max(7200).default(1800).description('单个视频任务轮询超时（秒）'),
    imageSize: z.string().default('1024x1536').description('角色/场景参考图尺寸'),
    frameSize: z.string().default('1024x1536').description('分镜首帧尺寸'),
    aspectRatio: z.string().default('9:16').description('视频画幅，例如 9:16、16:9'),
  })
  .description('短剧全链路 BYOK 插件配置')

export function apply(ctx: Context, config: PluginConfig): void {
  registerTools(ctx, config)
}
