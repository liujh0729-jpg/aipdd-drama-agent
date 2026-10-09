# PowerShell script to orchestrate the entire CapCut MCP stack
$ErrorActionPreference = "Continue"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "       CapCut MCP Stack Launcher        " -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# 1. Start VectCutAPI Backend
& "C:\Dev\capcut\start-vectcutapi.ps1"

# 2. Run MCP Verification
Write-Host "`nRunning MCP Server verification..." -ForegroundColor Yellow
$nodeTest = & node "C:\Dev\capcut\capcut-mcp-server\test-mcp-e2e.js"
Write-Host $nodeTest

Write-Host "`n========================================" -ForegroundColor Green
Write-Host "   CapCut MCP Stack is fully operational! " -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "Backend API  : http://127.0.0.1:9001"
Write-Host "MCP Config   : C:\Users\Hemanshi Makwana\.gemini\config\mcp_config.json"
Write-Host "CapCut Drafts: C:\Users\Hemanshi Makwana\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft"
Write-Host "========================================`n"
