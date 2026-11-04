// 开发用 mock：OpenAI 兼容模型 + 图片/视频/配音接口。
// 用法：node scripts/dsh-mock-server.mjs <port> [check|full]
// 将 DSH profile 的 llm-pi-ai.providers.mock 与 drama-harness.baseUrl 指向本服务即可联调。
import { createServer } from 'node:http'

const port = Number(process.argv[2] || 18999)
const mode = process.argv[3] || 'check'
const PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nXQAAAAASUVORK5CYII='

const fullFlow = [
  { name: 'drama_init', arguments: { projectId: 'mock-drama', sourceText: '小明在街道遇到小红。' } },
  { name: 'drama_plan_episodes', arguments: { projectId: 'mock-drama', count: 1 } },
  { name: 'drama_script', arguments: { projectId: 'mock-drama', episode: 1, beats: 4 } },
  { name: 'drama_cast', arguments: { projectId: 'mock-drama' } },
  { name: 'drama_scenes', arguments: { projectId: 'mock-drama' } },
  { name: 'drama_frames', arguments: { projectId: 'mock-drama', episode: 1 } },
  { name: 'drama_videos', arguments: { projectId: 'mock-drama', episode: 1 } },
  { name: 'drama_audio', arguments: { projectId: 'mock-drama', episode: 1 } },
  { name: 'drama_compose', arguments: { projectId: 'mock-drama', episode: 1 } },
  { name: 'drama_status', arguments: { projectId: 'mock-drama' } },
]

const calls = new Map()

function json(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json' })
  res.end(JSON.stringify(body))
}

function binary(res, body) {
  res.writeHead(200, { 'Content-Type': 'application/octet-stream' })
  res.end(body)
}

function sse(res, chunks) {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
  })
  for (const chunk of chunks) res.write(`data: ${JSON.stringify(chunk)}\n\n`)
  res.write('data: [DONE]\n\n')
  res.end()
}

function completionChunk(model, delta, finishReason) {
  return {
    id: 'chatcmpl-mock',
    object: 'chat.completion.chunk',
    created: Math.floor(Date.now() / 1000),
    model: model || 'deepseek-chat',
    choices: [{ index: 0, delta, finish_reason: finishReason }],
  }
}

function pickAction(messages) {
  const toolResults = messages.filter((message) => message.role === 'tool').length
  if (mode === 'full') {
    if (toolResults < fullFlow.length) return fullFlow[toolResults]
    return null
  }
  if (toolResults === 0) return { name: 'drama_check', arguments: {} }
  if (!calls.has('drama_status')) return { name: 'drama_status', arguments: {} }
  return null
}

function chatReply(body) {
  const user = [...(body.messages || [])].reverse().find((message) => message.role === 'user')?.content || ''
  if (user.includes('输出 JSON：{"summary"')) {
    return {
      summary: '小明在街道遇到小红。',
      characters: [{ name: '小明', appearance: '黑色短发，白衬衫', personality: '勇敢直接' }],
      events: ['两人在街道相遇'],
    }
  }
  if (user.includes('"logline":"一句话故事"')) {
    return {
      title: '街头相遇',
      logline: '两个陌生人在街头相遇并改变彼此。',
      genre: '都市',
      setting: '现代城市街道',
      characters: [
        {
          name: '小明',
          role: '主角',
          age: '22',
          appearance: '黑色短发，白衬衫',
          personality: '勇敢直接',
        },
      ],
      chapters: [{ title: '第一章', summary: '街头相遇' }],
    }
  }
  if (user.includes('请拆成恰好')) {
    return {
      episodes: [
        {
          title: '街头相遇',
          summary: '小明在街道上遇到小红，两人发生争执。',
          keyEvents: ['相遇', '争执'],
          hook: '小红拿出一封信。',
        },
      ],
    }
  }
  if (user.includes('请写') && user.includes('beat')) {
    return {
      beats: [
        {
          title: '相遇',
          scene: '街道',
          characters: ['小明'],
          action: '小明沿街走来，停下脚步。',
          dialogue: [{ speaker: '小明', text: '你是谁？' }],
          narration: '傍晚的街道很安静。',
          videoPrompt: '小明沿街走来，镜头缓慢推进，傍晚侧光，竖屏电影感',
          durationSec: 3,
        },
        {
          title: '争执',
          scene: '街道',
          characters: ['小明'],
          action: '小明皱眉后退半步。',
          dialogue: [{ speaker: '小明', text: '把信给我。' }],
          narration: '',
          videoPrompt: '小明皱眉后退，手持镜头轻微晃动，竖屏电影感',
          durationSec: 3,
        },
      ],
    }
  }
  if (user.includes('"scenes"')) {
    return {
      scenes: [
        {
          name: '街道',
          description: '傍晚的城市街道，路灯刚亮，行人稀少',
          imagePrompt: '傍晚城市街道，路灯刚亮，行人稀少，电影感，无人物特写',
        },
      ],
    }
  }
  return null
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${port}`)
  const origin = `http://127.0.0.1:${port}`
  let raw = ''
  for await (const chunk of req) raw += chunk
  const body = raw ? JSON.parse(raw) : {}

  if (req.method === 'GET' && url.pathname === '/v1/models') {
    return json(res, 200, {
      data: [
        { id: 'deepseek-chat' },
        { id: 'gpt-image-1' },
        { id: 'seedance-1-0-pro' },
        { id: 'tts-1' },
      ],
    })
  }

  if (req.method === 'POST' && url.pathname === '/v1/chat/completions') {
    const messages = body.messages || []
    const hasDramaTools = (body.tools || []).some((tool) => tool?.function?.name?.startsWith('drama_'))
    const action = hasDramaTools ? pickAction(messages) : null
    console.error(
      `[mock] chat model=${body.model} stream=${!!body.stream} tools=${(body.tools || []).length} ` +
        `roles=${messages.map((message) => message.role).join(',')} action=${action?.name || (hasDramaTools ? 'text' : 'plugin-json')}`,
    )

    if (!action) {
      const reply = hasDramaTools ? null : chatReply(body)
      const content = reply ? JSON.stringify(reply) : '短剧制作'
      if (body.stream) {
        return sse(res, [
          completionChunk(body.model, { role: 'assistant' }, null),
          completionChunk(body.model, { content }, null),
          completionChunk(body.model, {}, 'stop'),
        ])
      }
      return json(res, 200, {
        choices: [{ message: { role: 'assistant', content }, finish_reason: 'stop' }],
      })
    }

    const count = (calls.get(action.name) || 0) + 1
    calls.set(action.name, count)
    console.error(`[mock] -> ${action.name} #${count}`)

    const toolCall = {
      index: 0,
      id: `call_${action.name}_${count}`,
      type: 'function',
      function: { name: action.name, arguments: JSON.stringify(action.arguments) },
    }

    if (body.stream) {
      return sse(res, [
        completionChunk(body.model, { role: 'assistant' }, null),
        completionChunk(body.model, { tool_calls: [toolCall] }, null),
        completionChunk(body.model, {}, 'tool_calls'),
      ])
    }
    return json(res, 200, {
      choices: [
        {
          message: { role: 'assistant', content: null, tool_calls: [toolCall] },
          finish_reason: 'tool_calls',
        },
      ],
    })
  }

  if (url.pathname === '/v1/images/generations' || url.pathname === '/v1/images/edits') {
    return json(res, 200, { data: [{ b64_json: PNG_B64 }] })
  }

  if (url.pathname === '/v1/audio/speech') {
    return binary(res, Buffer.from('fake-mp3'))
  }

  if (url.pathname === '/v1/videos' && req.method === 'POST') {
    return json(res, 200, { id: `task-${Date.now()}`, status: 'queued' })
  }

  if (url.pathname.startsWith('/v1/videos/') && req.method === 'GET') {
    return json(res, 200, { status: 'completed', content_url: `${origin}/media/video.mp4` })
  }

  if (url.pathname === '/media/video.mp4') {
    return binary(res, Buffer.from('fake-mp4'))
  }

  json(res, 404, { error: { message: `not found: ${req.method} ${url.pathname}` } })
})

server.listen(port, '127.0.0.1', () => {
  console.error(`[mock] listening on http://127.0.0.1:${port} mode=${mode}`)
})
