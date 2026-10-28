# =============================================================================
# build-windows.ps1 —— 在 Windows x64 构建机上产出 NSIS .exe 安装包。
#
#   用法（在 desktop/ 目录，PowerShell）：
#     powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1
#     powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1 -Prepare   # 顺带跑资源准备
#     powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1 -SkipChecks
#
#   前置（详见 scripts\build-windows.md）：
#     1) VS 2022 Build Tools（C++ 工作负载，提供 link.exe）
#     2) rustup MSVC 工具链 + cargo install tauri-cli --version "^2"
#     3) 已跑过 scripts\prepare-resources.sh win-x64（resources 就位）
#
#   本脚本只做「校验前置 → cargo tauri build --bundles nsis → 列出产物」，
#   不做签名；对外分发前再跑 scripts\win-sign.ps1（对应 mac 的 sign+notarize）。
# =============================================================================
[CmdletBinding()]
param(
    [switch]$Prepare,          # 先调用 prepare-resources.sh win-x64（需 Git Bash + 全套打包工具）
    [switch]$SkipChecks,       # 跳过前置资源自检（仅调试构建命令时用）
    [string]$Target = 'x86_64-pc-windows-msvc',
    [string]$InstallMode = ''  # 覆盖 tauri.conf 的 nsis installMode：currentUser | perMachine | both
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # 避免 Invoke 进度条拖慢大文件操作

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Die($msg) { Write-Host "`n[失败] $msg" -ForegroundColor Red; exit 1 }

$DesktopDir = Split-Path -Parent $PSScriptRoot
$ResDir = Join-Path $DesktopDir 'src-tauri\resources'
Set-Location $DesktopDir

# ------------------------------------------------------------- MSVC 链接器环境
# Git 自带的 usr/bin\link.exe 是 coreutils 的「创建硬链接」工具，会在 PATH 上遮住真正的
# MSVC 链接器；Rust 链接阶段一旦命中它就是无意义的语法错误（编译期不报，只有最终
# 生成 exe 时炸）。这里无论是否 -SkipChecks 都先把 vcvars64 的环境变量导进来，
# 让 link.exe / cl.exe 解析到 VS 的 Hostx64\x64。
function Get-MsvcLink { (Get-Command link.exe -ErrorAction SilentlyContinue).Source }
function Test-IsMsvcLink([string]$p) {
    if (-not $p) { return $false }
    return $p -match '\\VC\\Tools\\MSVC\\.*\\bin\\Host.*\\x64\\link\.exe$'
}
function Import-MsvcEnv {
    $cur = Get-MsvcLink
    if (Test-IsMsvcLink $cur) { Write-Host "链接器：$cur"; return }
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (-not (Test-Path $vswhere)) {
        Die '找不到 vswhere.exe —— 未安装 VS 2022（Build Tools 的「使用 C++ 的桌面开发」工作负载）。当前 link.exe 解析为：'"$cur"
    }
    $vsPath = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null
    if (-not $vsPath) { Die 'vswhere 未找到带 C++ 工具链的 VS 安装（请装/勾选「使用 C++ 的桌面开发」）' }
    $vcvars = Join-Path $vsPath 'VC\Auxiliary\Build\vcvars64.bat'
    if (-not (Test-Path $vcvars)) { Die "缺少 $vcvars" }
    Step "导入 MSVC 环境（vcvars64：$vsPath）"
    # cmd 里跑 vcvars64.bat 后 dump 环境变量回灌到当前 PowerShell 进程。
    cmd /c "`"$vcvars`" >nul 2>&1 && set" | ForEach-Object {
        if ($_ -match '^(.+?)=(.*)$') { Set-Item -Path ("env:{0}" -f $matches[1]) -Value $matches[2] }
    }
    $now = Get-MsvcLink
    if (-not (Test-IsMsvcLink $now)) { Die "导入后 link.exe 仍不是 MSVC 的：$now" }
    Write-Host "链接器：$now"
}
Import-MsvcEnv

# ------------------------------------------------------------------ 前置校验
Step '构建机自检'
if (-not $SkipChecks) {
    foreach ($tool in 'cargo', 'rustup') {
        if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { Die "缺少 $tool（见 scripts\build-windows.md §1）" }
    }
    # tauri-cli 既可能是 cargo 子命令，也可能是 npm 的 @tauri-apps/cli
    cargo tauri --version *> $null
    if ($LASTEXITCODE -ne 0) { Die '缺少 tauri-cli：cargo install tauri-cli --version "^2"' }

    # 便携 Python 的 Windows 布局是 python\python.exe（没有 bin/ 这一层，和 mac 不同）
    # 动效大片两份清单与海报在这里再拦一次：不加 -Prepare 直接复用旧 resources 时，
    # prepare-resources.sh 的出包自检根本不会跑，缺文件只会在装机后表现为图册空白。
    $required = @(
        'python\python.exe',
        'bin\nginx.exe',
        'bin\localapi.exe',
        'bin\uv.exe',
        'app\dist\index.html',
        'app\src\novelvideo',
        'app\src\novelvideo\freezone\motion_templates.json',
        'app\src\novelvideo\freezone\motion_styles.json',
        'app\src\novelvideo\freezone\motion_posters',
        'app\src\novelvideo\freezone\motion_previews',
        'app\services\faceswap\server.py',
        'app\.hermes\plugins\dramaclaw\plugin.yaml',
        'hermes\site-packages\hermes_cli\main.py'
    )
    foreach ($rel in $required) {
        if (-not (Test-Path (Join-Path $ResDir $rel))) {
            Die "资源缺失：src-tauri\resources\$rel —— 先跑 scripts\prepare-resources.sh win-x64（或加 -Prepare）"
        }
    }
    if (-not (Test-Path (Join-Path $ResDir 'bin\ffprobe.exe'))) {
        Write-Warning 'resources\bin\ffprobe.exe 缺失：后端 63 处 ffprobe 调用会在运行期失败。'
    }
    # cl.exe 由上面的 Import-MsvcEnv 从 vcvars64 导入；这里只作为信息回显。
    $cl = (Get-Command cl.exe -ErrorAction SilentlyContinue).Source
    if (-not $cl) { Write-Warning 'PATH 上没有 cl.exe（Import-MsvcEnv 未能导入 VS 环境，链接可能失败）。' }
    else { Write-Host "编译器：$cl" }
    Write-Host "资源目录：$ResDir"
}

if ($Prepare) {
    Step '准备 win-x64 便携资源（大下载/编译，可能数十分钟）'
    $bash = (Get-Command bash.exe -ErrorAction SilentlyContinue)
    if (-not $bash) { Die '-Prepare 需要 Git Bash（bash.exe），或手动执行 scripts\prepare-resources.sh win-x64' }
    & bash.exe "$DesktopDir\scripts\prepare-resources.sh" win-x64
    if ($LASTEXITCODE -ne 0) { Die "prepare-resources.sh 退出码 $LASTEXITCODE" }
}

# ------------------------------------------------------------------ 构建 NSIS
Step "构建 NSIS 安装包（target=$Target）"
# --bundles nsis 必须显式给：tauri.conf 的 bundle.targets 是 mac 的 ["app","dmg"]，
# 不带该参数时 Windows 上会去构建 dmg 而失败。
$buildArgs = @('tauri', 'build', '--target', $Target, '--bundles', 'nsis')
if ($InstallMode) {
    if ($InstallMode -notin @('currentUser', 'perMachine', 'both')) {
        Die "-InstallMode 只能是 currentUser / perMachine / both（收到：$InstallMode）"
    }
    # 不改仓库里的 tauri.conf.json，用 CLI 的 --config 覆盖（perMachine 需要管理员权限装 Program Files）。
    $override = "{`"bundle`":{`"windows`":{`"nsis`":{`"installMode`":`"$InstallMode`"}}}}"
    $buildArgs += @('--config', $override)
}
cargo @buildArgs
if ($LASTEXITCODE -ne 0) { Die "cargo tauri build 退出码 $LASTEXITCODE" }

# ------------------------------------------------------------------ 产物汇总
Step '产物'
$bundleRoot = Join-Path $DesktopDir "src-tauri\target\$Target\release\bundle"
$installers = Get-ChildItem -Path (Join-Path $bundleRoot 'nsis') -Filter '*-setup.exe' -ErrorAction SilentlyContinue
if (-not $installers) { Die "未找到 NSIS 安装包，检查 $bundleRoot\nsis" }

foreach ($f in $installers) {
    $mb = [math]::Round($f.Length / 1MB, 1)
    $sha = (Get-FileHash -Algorithm SHA256 -Path $f.FullName).Hash.ToLower()
    Write-Host ("{0}  {1} MB" -f $f.FullName, $mb)
    Write-Host ("  sha256 {0}" -f $sha)
    $sidecar = "$($f.FullName).json"
    # 供 win-sign.ps1 / CI 直接读取，不必再解析文件名
    @{ path = $f.FullName; size_bytes = $f.Length; sha256 = $sha } | ConvertTo-Json -Compress | Set-Content -Path $sidecar -Encoding utf8
}

Write-Host @"

下一步（对外分发前）：
  powershell -ExecutionPolicy Bypass -File scripts\win-sign.ps1 -Installer '<上面打印的 setup.exe 路径>'
  未签名的包在 Win10/11 会触发 SmartScreen「未知发布者」，本机自测可点「仍要运行」。
"@ -ForegroundColor Yellow
