import type { ChatMessage } from './llm.js'
import type { Beat, CharacterAsset, IngestBible, SceneAsset } from './workspace.js'

const JSON_RULE = '只输出一个合法 JSON 对象，不要 Markdown 代码块，不要解释。'

export function chunkSummaryMessages(chunk: string, index: number, total: number): ChatMessage[] {
  return [
    {
      role: 'system',
      content:
        '你是资深短剧编剧与小说编辑。阅读小说片段，提取对改编有用的客观事实：人物（姓名、外貌、性格）、地点、关键事件。' +
        '保持原文人名与事实，不要编造。' +
        JSON_RULE,
    },
    {
      role: 'user',
      content:
        `这是第 ${index + 1}/${total} 段小说。\n\n${chunk}\n\n` +
        '输出 JSON：{"summary":"本段摘要","characters":[{"name":"","appearance":"","personality":""}],"events":["关键事件"]}',
    },
  ]
}

export function bibleMessages(summaries: string[], sourceTitle: string): ChatMessage[] {
  return [
    {
      role: 'system',
      content:
        '你是短剧总编剧。把小说片段摘要合并成一份可用于改编的设定集。' +
        '人物必须同名合并，外貌与性格取最一致的信息；不要新增原文没有的核心人物。' +
        JSON_RULE,
    },
    {
      role: 'user',
      content:
        `作品名：${sourceTitle}\n\n片段摘要：\n${summaries.join('\n---\n')}\n\n` +
        '输出 JSON：{"title":"","logline":"一句话故事","genre":"","setting":"时代与世界观",' +
        '"characters":[{"name":"","role":"主角/反派/配角","age":"","appearance":"","personality":""}],' +
        '"chapters":[{"title":"","summary":""}]}',
    },
  ]
}

export function episodesMessages(bible: IngestBible, count: number): ChatMessage[] {
  const characterBrief = bible.characters.map((item) => `${item.name}（${item.role}）：${item.personality}`).join('\n')
  return [
    {
      role: 'system',
      content:
        '你是竖屏短剧主编。把故事拆成有强钩子的连续短剧集：每集 60-90 秒，3-5 个关键事件，开场 3 秒抓人，结尾留悬念。' +
        '不得新增设定集之外的核心人物。' +
        JSON_RULE,
    },
    {
      role: 'user',
      content:
        `设定集：\n${JSON.stringify(bible, null, 2)}\n\n人物速览：\n${characterBrief}\n\n` +
        `请拆成恰好 ${count} 集。输出 JSON：{"episodes":[{"title":"","summary":"本集剧情","keyEvents":[""],"hook":"结尾悬念"}]}`,
    },
  ]
}

export function scriptMessages(
  bible: IngestBible,
  episodeTitle: string,
  episodeSummary: string,
  keyEvents: string[],
  characters: CharacterAsset[],
  existingScenes: SceneAsset[],
  targetBeats: number,
): ChatMessage[] {
  const characterBrief = characters
    .map((item) => `${item.name}：${item.appearance || '外貌未定'}；${item.personality || ''}`)
    .join('\n')
  const sceneBrief = existingScenes.map((item) => `${item.name}：${item.description}`).join('\n')
  return [
    {
      role: 'system',
      content:
        '你是短剧分镜编剧。把一集剧情写成可直接生产的连续镜头（beat）。要求：' +
        '1) 只使用给定人物与场景，台词口语化、短促；' +
        '2) 每个 beat 是一个连续镜头，时长 3-8 秒；' +
        '3) videoPrompt 写给图生视频模型：主体动作、镜头运动、光线、景别、画风、人物外观一致性，不写字幕/文字/水印；' +
        '4) 画面必须能承接上一镜头，避免无法拍摄的抽象描述。' +
        JSON_RULE,
    },
    {
      role: 'user',
      content:
        `设定集：${JSON.stringify({ setting: bible.setting, genre: bible.genre, logline: bible.logline })}\n\n` +
        `本集：${episodeTitle}\n剧情：${episodeSummary}\n关键事件：${keyEvents.join('；')}\n\n` +
        `人物：\n${characterBrief || '（按设定集）'}\n\n` +
        `已有场景：\n${sceneBrief || '（可新增，但名称要稳定）'}\n\n` +
        `请写 ${targetBeats} 个 beat。输出 JSON：` +
        '{"beats":[{"title":"","scene":"场景名","characters":["人物名"],"action":"画面动作",' +
        '"dialogue":[{"speaker":"","text":""}],"narration":"可选旁白","videoPrompt":"","durationSec":5}]}',
    },
  ]
}

export function scenesMessages(bible: IngestBible, episodeSummaries: string[]): ChatMessage[] {
  return [
    {
      role: 'system',
      content:
        '你是短剧美术指导。根据剧本提取可复用的场景清单，合并同义场景，保持名称稳定。' +
        '每个场景给出适合文生图的中文提示词：地点、时间、光线、材质、氛围、风格，不出现人物特写、不出现文字。' +
        JSON_RULE,
    },
    {
      role: 'user',
      content:
        `设定：${bible.setting}；类型：${bible.genre}\n\n各集剧情：\n${episodeSummaries.join('\n')}\n\n` +
        '输出 JSON：{"scenes":[{"name":"","description":"","imagePrompt":""}]}（6-20 个）',
    },
  ]
}

export function portraitPrompt(character: CharacterAsset, aspectRatio = '9:16'): string {
  return (
    `${orientationOf(aspectRatio)}电影感角色定妆照，${character.name}，${character.appearance || '外貌按剧情设定'}，` +
    `${character.personality || ''}。正面半身，干净纯色背景，柔和电影打光，写实风格，` +
    '五官清晰，服装质感真实，无文字、无水印、无夸张道具。'
  )
}

export function scenePrompt(scene: SceneAsset, aspectRatio = '9:16'): string {
  return (
    `${orientationOf(aspectRatio)}电影感场景参考图，${scene.name}，${scene.description || ''}。` +
    '无人物特写，空间结构清晰，光线氛围统一，写实电影风格，无文字、无水印。'
  )
}

export function framePrompt(
  beat: Beat,
  scene: SceneAsset | undefined,
  characters: CharacterAsset[],
  aspectRatio = '9:16',
): string {
  const cast = beat.characters
    .map((name) => characters.find((item) => item.name === name))
    .filter((item): item is CharacterAsset => Boolean(item))
    .map((item) => `${item.name}：${item.appearance || ''}`)
    .join('；')
  return (
    `${orientationOf(aspectRatio)}电影感首帧。场景：${scene?.description || beat.scene}。` +
    `画面动作：${beat.action}。出场人物：${cast || beat.characters.join('、') || '无'}。` +
    '要求：人物长相、服装与参考图一致，场景结构与参考图一致，构图适合该画幅，' +
    '电影级光线，无文字、无字幕、无水印。'
  )
}

export function videoPromptText(beat: Beat, aspectRatio = '9:16'): string {
  return `${beat.videoPrompt || beat.action}。${orientationOf(aspectRatio)}，单镜头连续运动，人物外观一致，光线自然，无字幕、无水印。`
}

function orientationOf(aspectRatio: string): string {
  const value = String(aspectRatio || '9:16').trim()
  if (value === '16:9') return '横屏 16:9'
  if (value === '1:1') return '方形 1:1'
  if (value === '4:3') return '横屏 4:3'
  if (value === '3:4') return '竖屏 3:4'
  if (value === '4:5') return '竖屏 4:5'
  if (value === '21:9') return '宽银幕 21:9'
  return '竖屏 9:16'
}
