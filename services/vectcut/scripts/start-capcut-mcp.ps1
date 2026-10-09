# PowerShell script to start CapCut MCP Server
$ErrorActionPreference = "Stop"

$McpDir = "C:\Dev\capcut\capcut-mcp-server"
$McpEntry = "$McpDir\dist\index.js"
$Env:CAPCUT_API_URL = "http://127.0.0.1:9001"

Write-Host "Starting CapCut MCP Server (stdio mode)..." -ForegroundColor Cyan
Write-Host "Entry: $McpEntry" -ForegroundColor Gray
Write-Host "API URL: $Env:CAPCUT_API_URL" -ForegroundColor Gray

& node $McpEntry
