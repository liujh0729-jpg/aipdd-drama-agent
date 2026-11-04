import assert from 'node:assert/strict'
import http from 'node:http'
import { mkdtemp, readFile, rm, stat } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import * as plugin from '../lib/index.js'

const PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nXQAAAAASUVORK5CYII='

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url || '/', 'http://127.0.0.1')
  const bodyText = await readBody(req)
  const body = bodyText ? JSON.parse(bodyText) : {}
  const origin = `http://127.0.0.1:${server.address().port}`

  const json = (value, status = 200) => {
    res.writeHead(status, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify(value))
  }
  const binary = (value) => {
    res.writeHead(200, { 'Content-Type': 'application/octet-stream' })
    res.end(value)
  }

  if (url.pathname === '/v1/models') {
    return json({
      data: [{ id: 'deepseek-chat' }, { id: 'seedream-3.0' }, { id: 'seedance-1.0' }, { id: 'tts-1' }],
    })
  }

  if (url.pathname === '/v1/chat/completions') {
    return json({ choices: [{ message: { content: JSON.stringify(chatReply(body)) } }] })
  }

  if (url.pathname === '/v1/images/generations' || url.pathname === '/v1/images/edits') {
    return json({ data: [{ b64_json: PNG_B64 }] })
  }

  if (url.pathname === '/v1/audio/speech') {
    return binary(Buffer.from('fake-mp3'))
  }

  if (url.pathname === '/v1/videos' && req.method === 'POST') {
    return json({ id: `task-${Date.now()}`, status: 'queued' })
  }

  if (url.pathname.startsWith('/v1/videos/') && req.method === 'GET') {
    return json({ status: 'completed', content_url: `${origin}/media/video.mp4` })
  }

  if (url.pathname === '/media/video.mp4') {
    return binary(Buffer.from('fake-mp4'))
  }

  json({ error: `unhandled ${req.method} ${url.pathname}` }, 404)
})

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
  return {}
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = []
    req.on('data', (chunk) => chunks.push(chunk))
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')))
    req.on('error', reject)
  })
}

function listen() {
  return new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
}

const workspaceDir = await mkdtemp(join(tmpdir(), 'drama-harness-e2e-'))
await listen()
const baseUrl = `http://127.0.0.1:${server.address().port}/v1`

const tools = []
const ctx = {
  tools: {
    register(definition) {
      tools.push(definition)
      return () => {}
    },
  },
  effect(body) {
    return body()
  },
}
const exec = { signal: new AbortController().signal }
const call = async (name, args) => {
  const tool = tools.find((item) => item.name === name)
  assert.ok(tool, `missing tool ${name}`)
  return tool.execute(args, exec)
}

try {
  plugin.apply(ctx, {
    apiKey: 'mock-key',
    gateway: 'aipdd',
    baseUrl,
    workspaceDir,
    concurrency: 2,
    videoTimeoutSec: 60,
  })

  const check = await call('drama_check', {})
  assert.equal(check.ok, true)
  assert.equal(check.models.text, 'deepseek-chat')
  assert.equal(check.models.video, 'seedance-1.0')

  const projectId = 'mock-drama'
  const init = await call('drama_init', { projectId, sourceText: '小明在街道遇到小红。' })
  assert.equal(init.characters, 1)
  assert.equal(init.chapters, 1)

  const plan = await call('drama_plan_episodes', { projectId, count: 1 })
  assert.equal(plan.total, 1)

  const script = await call('drama_script', { projectId, episode: 1, beats: 4 })
  assert.equal(script.beats, 2)

  const cast = await call('drama_cast', { projectId })
  assert.equal(cast.generated, 1)
  await stat(join(cast.projectDir, cast.items[0].file))

  const scenes = await call('drama_scenes', { projectId })
  assert.equal(scenes.generated, 1)
  await stat(join(scenes.projectDir, scenes.items[0].file))

  const frames = await call('drama_frames', { projectId, episode: 1 })
  assert.equal(frames.generated, 2)

  const videos = await call('drama_videos', { projectId, episode: 1 })
  assert.equal(videos.generated, 2)
  for (const file of videos.files) await stat(join(videos.projectDir, file))

  const audio = await call('drama_audio', { projectId, episode: 1 })
  assert.equal(audio.generated, 2)
  await stat(join(audio.projectDir, audio.srtFile))

  const compose = await call('drama_compose', { projectId, episode: 1 })
  assert.equal(compose.composed, false)
  assert.match(compose.reason, /ffmpeg/i)
  await stat(join(compose.projectDir, compose.manifestFile))

  const state = JSON.parse(await readFile(join(compose.projectDir, 'project.json'), 'utf8'))
  const episode = state.episodes[0]
  assert.equal(episode.stages.script, 'done')
  assert.equal(episode.stages.frames, 'done')
  assert.equal(episode.stages.videos, 'done')
  assert.equal(episode.stages.audio, 'done')
  assert.equal(episode.stages.compose, 'failed')
  assert.ok(episode.beats.every((beat) => beat.frameFile && beat.videoFile && beat.audioFile))

  const status = await call('drama_status', { projectId })
  assert.equal(status.projects.length, 1)
  assert.equal(status.projects[0].episodes[0].beats, 2)
  assert.match(status.projects[0].next, /drama_compose/)

  console.log('OK: mock gateway end-to-end pipeline')
  console.log(`workspace: ${workspaceDir}`)
} finally {
  await new Promise((resolve) => server.close(resolve))
  await rm(workspaceDir, { recursive: true, force: true })
}
