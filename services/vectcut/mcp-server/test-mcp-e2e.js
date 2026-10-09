import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';

async function main() {
  console.log('--- Starting CapCut MCP E2E Test ---');

  const transport = new StdioClientTransport({
    command: 'node',
    args: ['C:/Dev/capcut/capcut-mcp-server/dist/index.js'],
    env: {
      ...process.env,
      CAPCUT_API_URL: 'http://127.0.0.1:9001'
    }
  });

  const client = new Client(
    { name: 'capcut-test-client', version: '1.0.0' },
    { capabilities: {} }
  );

  console.log('Connecting to MCP Server...');
  await client.connect(transport);
  console.log('Connected successfully!');

  console.log('\n--- Discovering Tools ---');
  const toolsResult = await client.listTools();
  console.log(`Discovered ${toolsResult.tools.length} tools:`);
  toolsResult.tools.forEach((t, i) => console.log(`  ${i + 1}. ${t.name}: ${t.description.split('\n')[0]}`));

  console.log('\n--- Testing capcut_list_projects ---');
  const listRes = await client.callTool({
    name: 'capcut_list_projects',
    arguments: { response_format: 'json' }
  });
  console.log('List Projects Output:', JSON.stringify(listRes, null, 2));

  console.log('\n--- Testing capcut_create_draft (9:16) ---');
  const createRes = await client.callTool({
    name: 'capcut_create_draft',
    arguments: { width: 1080, height: 1920, fps: 30, ratio: '9:16', response_format: 'json' }
  });
  console.log('Create Draft Output:', JSON.stringify(createRes, null, 2));

  let draftId = createRes.structuredContent?.draft_id || createRes.content?.[0]?.text;
  if (typeof draftId === 'string' && draftId.startsWith('{')) {
    try {
      const parsed = JSON.parse(draftId);
      draftId = parsed.draft_id;
    } catch(e) {}
  }
  console.log('Extracted Draft ID:', draftId);

  if (draftId) {
    console.log('\n--- Testing capcut_add_text with bold, alignment, line_spacing ---');
    const textRes = await client.callTool({
      name: 'capcut_add_text',
      arguments: {
        draft_id: draftId,
        text: 'Antigravity CapCut Engine\nAutomated Subtitle Spacing',
        start: 0,
        end: 5,
        font_size: 56,
        font_color: '#FFD700',
        bold: true,
        alignment: 'center',
        line_spacing: 0.28,
        position_x: 0.5,
        position_y: 0.5,
        response_format: 'json'
      }
    });
    console.log('Add Text Output:', JSON.stringify(textRes, null, 2));

    console.log('\n--- Testing capcut_save_draft with auto-deploy ---');
    const saveRes = await client.callTool({
      name: 'capcut_save_draft',
      arguments: {
        draft_id: draftId,
        project_name: 'Antigravity_Test_V2',
        auto_deploy: true,
        response_format: 'json'
      }
    });
    console.log('Save Draft Output:', JSON.stringify(saveRes, null, 2));

    console.log('\n--- Testing capcut_read_project ---');
    const readRes = await client.callTool({
      name: 'capcut_read_project',
      arguments: {
        project_name: 'Antigravity_Test_V2',
        response_format: 'json'
      }
    });
    console.log('Read Project Output:', JSON.stringify(readRes, null, 2));
  }

  console.log('\n--- E2E Test Finished Successfully! ---');
  await client.close();
  process.exit(0);
}

main().catch(err => {
  console.error('Test failed with error:', err);
  process.exit(1);
});
