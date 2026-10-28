# =============================================================================
# win-sign.ps1 —— Windows 代码签名（对应 mac-sign-notarize.sh 的角色）
#
#   目的：降低 Defender / SmartScreen 误报。证书需单独采购（与 Apple Developer ID 两套）。
#
#   ⚠️ 顺序很关键：NSIS 安装包是资源的**副本**，出包后再签 resources 没有意义，
#      而 installer 里的文件也无法补签。因此必须两段式：
#        ① 构建前：-Stage Resources   （签 src-tauri\resources 内全部内嵌 PE）
#        ② 构建后：-Stage Installer    （签 *-setup.exe）
#      一条命令做完：先 -Stage Resources，再 build-windows.ps1，最后 -Stage Installer。
#      （也可把 tauri.conf 的 bundle.windows.signCommand 指到本脚本，让 tauri 在打包链路里自动签。）
#
#   用法（PowerShell，证书密码走环境变量，绝不写进脚本/仓库）：
#     $env:CODESIGN_PFX = 'D:\certs\aipdd-code.pfx'
#     $env:CODESIGN_PFX_PWD = '****'
#     powershell -ExecutionPolicy Bypass -File scripts\win-sign.ps1 -Stage Resources
#     powershell -ExecutionPolicy Bypass -File scripts\win-sign.ps1 -Stage Installer `
#       -Installer 'src-tauri\target\x86_64-pc-windows-msvc\release\bundle\nsis\AIPDD DramaClaw_2.1.7_x64-setup.exe'
#
#   或用已装在本地证书存储里的证书（免 PFX）：
#     powershell ... -Stage Resources -Thumbprint 'AABBCC...'
# =============================================================================
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Resources', 'Installer')]
    [string]$Stage,
    [string]$Installer = '',                       # -Stage Installer 必填
    [string]$Pfx = $env:CODESIGN_PFX,
    [string]$Thumbprint = '',
    [string]$TimestampUrl = 'http://timestamp.digicert.com',
    [string]$ResDir = '',
    [switch]$DryRun                                # 只列出待签文件，不动盘
)

$ErrorActionPreference = 'Stop'

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Die($msg) { Write-Host "`n[失败] $msg" -ForegroundColor Red; exit 1 }

$DesktopDir = Split-Path -Parent $PSScriptRoot
if (-not $ResDir) { $ResDir = Join-Path $DesktopDir 'src-tauri\resources' }

# ------------------------------------------------------------------ signtool
# 不在 PATH 时，从 Windows 10/11 SDK 常见位置找（VS Build Tools 会装到这里）。
$Signtool = (Get-Command signtool.exe -ErrorAction SilentlyContinue).Source
if (-not $Signtool) {
    $cands = Get-ChildItem 'C:\Program Files (x86)\Windows Kits\10\bin' -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        ForEach-Object { Join-Path $_.FullName 'x64\signtool.exe' } |
        Where-Object { Test-Path $_ }
    if ($cands) { $Signtool = @($cands)[0] }
}
if (-not $Signtool -and -not $DryRun) { Die '找不到 signtool.exe：装 VS 2022 的「Windows 11 SDK」或把它加进 PATH' }

# ------------------------------------------------------------------ 证书参数
$idArgs = @()
if ($Thumbprint) {
    $idArgs = @('/sha1', $Thumbprint)               # 本地证书存储（企业 CA / 云 HSM 常见）
} elseif ($Pfx) {
    if (-not (Test-Path $Pfx)) { Die "PFX 不存在：$Pfx" }
    $pwd = $env:CODESIGN_PFX_PWD
    if (-not $pwd) { Die '缺环境变量 CODESIGN_PFX_PWD（PFX 私钥口令，不要写进脚本或提交）' }
    $idArgs = @('/f', $Pfx, '/p', $pwd)
} elseif (-not $DryRun) {
    Die '需指定 -Thumbprint 或环境变量 CODESIGN_PFX（+ CODESIGN_PFX_PWD）'
}

# ------------------------------------------------------------------ 待签清单
$files = @()
if ($Stage -eq 'Resources') {
    if (-not (Test-Path $ResDir)) { Die "资源目录不存在：$ResDir（先跑 prepare-resources.sh win-x64）" }
    # 内嵌全部 PE：nginx/localapi/ffmpeg/chrome + 便携 Python 的 exe/dll/pyd。
    # 注意：外层 installer 必须在这些内层文件签好之后再构建，否则包内仍是未签名副本。
    $files = Get-ChildItem -Path $ResDir -Recurse -File -Include '*.exe', '*.dll', '*.pyd' -ErrorAction SilentlyContinue
    # 便携 Python 里的 test 目录 / pip wheel 缓存可能有历史签名的 PE，重签无害但耗时。
} else {
    if (-not $Installer) { Die '-Stage Installer 需要 -Installer <setup.exe 路径>' }
    $Installer = (Resolve-Path $Installer -ErrorAction SilentlyContinue).Path
    if (-not $Installer -or -not (Test-Path $Installer)) { Die "安装包不存在：$Installer" }
    $files = @(Get-Item $Installer)
}

Step "$Stage 待签文件 $($files.Count) 个"
if ($DryRun) {
    $files | Select-Object -First 40 | ForEach-Object { Write-Host "  $($_.FullName)" }
    if ($files.Count -gt 40) { Write-Host "  …（共 $($files.Count) 个，仅列前 40）" }
    Write-Host '（-DryRun：未执行签名）' -ForegroundColor Yellow
    exit 0
}
if ($files.Count -eq 0) { Die '待签清单为空，检查 -Stage 与目录' }

# ------------------------------------------------------------------ 逐个签名
$already = 0; $signed = 0; $failed = @()
foreach ($f in $files) {
    # 已有有效签名的跳过（内嵌 Python 的官方 DLL 已带 PSF 签名；重复签名会破坏原签名链）。
    & $Signtool verify /pa /q $f.FullName *> $null
    if ($LASTEXITCODE -eq 0) { $already++; continue }

    & $Signtool sign /fd SHA256 /tr $TimestampUrl /td SHA256 @idArgs $f.FullName *> $null
    if ($LASTEXITCODE -ne 0) { $failed += $f.FullName; Write-Host "  [签名失败] $($f.FullName)" -ForegroundColor Red; continue }

    & $Signtool verify /pa /q $f.FullName *> $null
    if ($LASTEXITCODE -ne 0) { $failed += $f.FullName; Write-Host "  [验签失败] $($f.FullName)" -ForegroundColor Red; continue }
    $signed++
    Write-Host ("  [OK {0}/{1}] {2}" -f ($signed + $already), $files.Count, $f.Name)
}

Step '签名结果'
Write-Host "新签 $signed，已签名跳过 $already，失败 $($failed.Count)"
if ($failed.Count) {
    $failed | ForEach-Object { Write-Host "  FAILED $_" -ForegroundColor Red }
    Die '存在签名/验签失败的文件，别把这份产物对外分发'
}
Write-Host '全部通过 signtool verify /pa。' -ForegroundColor Green
if ($Stage -eq 'Resources') {
    Write-Host '下一步：跑 build-windows.ps1 出包（会把已签名的资源打进去），再 -Stage Installer 签安装包。' -ForegroundColor Yellow
} else {
    Write-Host @"
验收：
  signtool verify /pa /v '$Installer'
  属性 → 数字签名 应显示你的证书主体；SmartScreen 信誉需累计下载量，新证书初期仍可能弹窗。
"@ -ForegroundColor Yellow
}
