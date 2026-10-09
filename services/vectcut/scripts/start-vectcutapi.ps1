# PowerShell script to start VectCutAPI Backend
$ErrorActionPreference = "Stop"

$VectCutDir = "C:\Dev\capcut\VectCutAPI"
$PythonExe = "$VectCutDir\.venv\Scripts\python.exe"
$ServerScript = "$VectCutDir\capcut_server.py"
$Endpoint = "http://127.0.0.1:9001/get_video_character_effect_types"

Write-Host "Checking if VectCutAPI is already running..." -ForegroundColor Cyan

function Test-BackendAlive {
    try {
        $response = Invoke-RestMethod -Uri $Endpoint -Method Get -TimeoutSec 2 -ErrorAction SilentlyContinue
        return ($response -and $response.success)
    } catch {
        return $false
    }
}

if (Test-BackendAlive) {
    Write-Host "VectCutAPI is already running and responsive on http://127.0.0.1:9001" -ForegroundColor Green
    exit 0
}

Write-Host "Starting VectCutAPI backend server..." -ForegroundColor Yellow
$proc = Start-Process -FilePath $PythonExe -ArgumentList $ServerScript -WorkingDirectory $VectCutDir -PassThru -WindowStyle Hidden

Write-Host "Waiting for VectCutAPI to become responsive on port 9001..."
$maxRetries = 20
$isAlive = $false

for ($i = 1; $i -le $maxRetries; $i++) {
    Start-Sleep -Milliseconds 500
    if (Test-BackendAlive) {
        $isAlive = $true
        break
    }
}

if ($isAlive) {
    Write-Host "VectCutAPI successfully started! (PID: $($proc.Id))" -ForegroundColor Green
    Write-Host "Backend URL: http://127.0.0.1:9001" -ForegroundColor Cyan
} else {
    Write-Host "Failed to start VectCutAPI within timeout." -ForegroundColor Red
    exit 1
}
