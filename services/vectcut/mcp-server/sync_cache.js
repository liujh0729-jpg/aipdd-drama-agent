import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import fs from 'fs';
import path from 'path';

async function updateCache() {
  const transport = new StdioClientTransport({
    command: 'node',
    args: ['C:/Dev/capcut/capcut-mcp-server/dist/index.js'],
    env: { ...process.env, CAPCUT_API_URL: 'http://127.0.0.1:9001' }
  });
  const client = new Client({ name: 'cache-updater', version: '1.0.0' }, { capabilities: {} });
  await client.connect(transport);
  const toolsRes = await client.listTools();
  const cacheDir = 'C:/Users/Hemanshi Makwana/.gemini/antigravity/mcp/capcut';
  for (const t of toolsRes.tools) {
    const file = path.join(cacheDir, t.name + '.json');
    const content = {
      name: t.name,
      description: t.description,
      parameters: t.inputSchema
    };
    fs.writeFileSync(file, JSON.stringify(content, null, 2), 'utf-8');
    console.log('Updated cache:', t.name);
  }
  await client.close();
  process.exit(0);
}
updateCache().catch(err => {
  console.error(err);
  process.exit(1);
});