#!/usr/bin/env node

/**
 * CapCut MCP Server
 * 
 * A Model Context Protocol server for CapCut Pro video editing automation.
 * Provides tools for creating and editing video projects programmatically.
 */

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import express from 'express';
import { registerTools } from './tools/index.js';

// Initialize MCP server
const server = new McpServer({
  name: 'capcut-mcp-server',
  version: '1.0.0'
});

// Register all tools
registerTools(server);

// Server connection handlers
async function runStdio(): Promise<void> {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error('CapCut MCP server running on stdio');
}

async function runHTTP(): Promise<void> {
  const app = express();
  app.use(express.json());

  // Health check endpoint
  app.get('/health', (_req, res) => {
    res.json({ status: 'healthy', server: 'capcut-mcp-server', version: '1.0.0' });
  });

  // MCP endpoint
  app.post('/mcp', async (req, res) => {
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined,
      enableJsonResponse: true
    });
    
    res.on('close', () => transport.close());
    await server.connect(transport);
    await transport.handleRequest(req, res, req.body);
  });

  const port = parseInt(process.env.PORT || '3000');
  app.listen(port, () => {
    console.error(`CapCut MCP server running on http://localhost:${port}/mcp`);
  });
}

// Main execution
const transport = process.env.TRANSPORT || 'stdio';

if (process.argv.includes('--help') || process.argv.includes('-h')) {
  console.log(`
CapCut MCP Server - Video Editing Automation

USAGE:
  capcut-mcp-server [OPTIONS]

OPTIONS:
  --help, -h          Show this help message
  --version, -v       Show version information

ENVIRONMENT VARIABLES:
  TRANSPORT           Transport type: 'stdio' (default) or 'http'
  PORT                HTTP server port (default: 3000)
  CAPCUT_API_URL      CapCut API base URL (default: http://localhost:9001)

EXAMPLES:
  # Run with stdio (for local integration)
  capcut-mcp-server

  # Run with HTTP server
  TRANSPORT=http PORT=3000 capcut-mcp-server

CONFIGURATION:
  Add to your MCP client config (e.g., Claude Desktop):
  
  {
    "mcpServers": {
      "capcut": {
        "command": "node",
        "args": ["/path/to/capcut-mcp-server/dist/index.js"],
        "env": {
          "CAPCUT_API_URL": "http://localhost:9001"
        }
      }
    }
  }

PREREQUISITES:
  - CapCut API server must be running (VectCutAPI)
  - Install from: https://github.com/sun-guannan/VectCutAPI
  - Start server: python capcut_server.py

For more information, visit:
  https://github.com/sun-guannan/VectCutAPI
  `);
  process.exit(0);
}

if (process.argv.includes('--version') || process.argv.includes('-v')) {
  console.log('capcut-mcp-server v1.0.0');
  process.exit(0);
}

// Start server
if (transport === 'http') {
  runHTTP().catch(error => {
    console.error('Server error:', error);
    process.exit(1);
  });
} else {
  runStdio().catch(error => {
    console.error('Server error:', error);
    process.exit(1);
  });
}
