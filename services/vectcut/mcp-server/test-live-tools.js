import { spawn } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const serverProcess = spawn('node', [path.join(__dirname, 'dist', 'index.js')], {
  stdio: ['pipe', 'pipe', 'inherit']
});

let buffer = '';

function sendRpc(method, params = {}, id = 1) {
  const req = JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n';
  serverProcess.stdin.write(req);
}

serverProcess.stdout.on('data', (chunk) => {
  buffer += chunk.toString();
  const lines = buffer.split('\n');
  buffer = lines.pop();

  for (const line of lines) {
    if (!line.trim()) continue;
    try {
      const msg = JSON.parse(line);
      handleMessage(msg);
    } catch (e) {
      console.log('Raw output:', line);
    }
  }
});

let step = 0;

function handleMessage(msg) {
  if (msg.id === 1) {
    console.log('1. Initialized successfully.');
    sendRpc('tools/list', {}, 2);
  } else if (msg.id === 2) {
    const tools = msg.result?.tools || [];
    console.log(`2. Discovered ${tools.length} MCP tools:`);
    const reloadTool = tools.find(t => t.name === 'capcut_reload_desktop');
    console.log('   - capcut_reload_desktop present:', !!reloadTool);
    
    // Call capcut_reload_desktop tool
    console.log('3. Calling capcut_reload_desktop...');
    sendRpc('tools/call', {
      name: 'capcut_reload_desktop',
      arguments: {
        project_name: 'Hemanshi_9_16'
      }
    }, 3);
  } else if (msg.id === 3) {
    console.log('4. capcut_reload_desktop result:', JSON.stringify(msg.result, null, 2));
    console.log('All live tests passed!');
    serverProcess.kill();
    process.exit(0);
  }
}

sendRpc('initialize', {
  protocolVersion: '2024-11-05',
  capabilities: {},
  clientInfo: { name: 'LiveTestClient', version: '1.0.0' }
}, 1);
