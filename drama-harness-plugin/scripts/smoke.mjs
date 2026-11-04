import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import * as plugin from '../lib/index.js'

const EXPECTED = [
  'drama_check',
  'drama_init',
  'drama_plan_episodes',
  'drama_script',
  'drama_cast',
  'drama_scenes',
  'drama_frames',
  'drama_videos',
  'drama_audio',
  'drama_compose',
  'drama_status',
]

assert.equal(plugin.name, 'drama-harness')
assert.deepEqual(plugin.inject, ['tools'])
assert.equal(typeof plugin.apply, 'function')
assert.equal('default' in plugin, false, 'plugin must not have a default export')

const defaults = plugin.Config({})
assert.equal(defaults.gateway, 'aipdd')
assert.equal(defaults.apiKey, '')
assert.equal(defaults.ttsVoice, 'alloy')
assert.equal(defaults.concurrency, 2)

const workspaceDir = await mkdtemp(join(tmpdir(), 'drama-harness-smoke-'))
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

try {
  plugin.apply(ctx, { apiKey: 'smoke-key', gateway: 'aipdd', workspaceDir })

  assert.deepEqual(
    tools.map((tool) => tool.name),
    EXPECTED,
    'all drama tools must be registered',
  )

  for (const tool of tools) {
    assert.equal(tool.parameters.type, 'object', `${tool.name} parameters must be an object root`)
    assert.equal(tool.output.schema.type, 'object', `${tool.name} output must be an object root`)
    assert.ok(tool.description.length > 20, `${tool.name} needs a useful description`)
    const rendered = tool.output.render({}, {})
    assert.ok(Array.isArray(rendered), `${tool.name} render must return content blocks`)
  }

  const status = tools.find((tool) => tool.name === 'drama_status')
  const report = await status.execute({}, { signal: new AbortController().signal })
  assert.deepEqual(report.projects, [])
  assert.equal(report.workspaceDir, workspaceDir)
  const rendered = status.output.render({}, report)
  assert.match(rendered[0].text, /还没有项目/)

  const videos = tools.find((tool) => tool.name === 'drama_videos')
  await assert.rejects(() => videos.execute({}, { signal: new AbortController().signal }), /projectId|Expected|required/i)
} finally {
  await rm(workspaceDir, { recursive: true, force: true })
}

console.log(`OK: ${tools.length} tools registered`)
console.log(tools.map((tool) => `- ${tool.name}`).join('\n'))
