# [aipdd fork delta] Windows 一键启动的实际逻辑，由仓库根的《启动系统-Start.bat》调用。
#
# 双击后的 5 步：自检 → 引擎就绪 → 镜像（缺则建）→ 起栈 → 等真就绪并打开浏览器。
# 走完直接返回，.bat 结束即关窗；容器与站点在后台继续跑（compose 是 detached）。
#
# 与 Mac 版一致的取舍：**默认路径绝不重建镜像**。要重建用
#   启动系统-Start.bat --rebuild
# （.bat 把它翻译成环境变量 AIPDD_REBUILD 传进来，避开 PowerShell 把 `--rebuild`
#   当参数名去绑定的坑；直接跑 ps1 时用 -Rebuild 开关也可以。）
#
# 本脚本在 macOS 上编写，未在真实 Windows 上执行验证；口径与限制见 需求/环境说明.md §8。

param([switch]$Rebuild)

. (Join-Path $PSScriptRoot 'windows_common.ps1')
Set-Location $RepoRoot          # compose 从当前目录读 .env 做插值

if ($env:AIPDD_REBUILD -eq '1') { $Rebuild = $true }
$composeFile = Join-Path $RepoRoot 'docker-compose.aipdd.yml'

Write-Host ''
Write-Host 'AIPDD DramaClaw · 一键启动（Windows）' -ForegroundColor White

# ────────────────────────── [1/5] 自检 ──────────────────────────
Write-Step '[1/5] 自检'
if (-not (Test-Path $composeFile)) {
    Wait-Here "找不到编排文件：$composeFile
请把本文件放在仓库根目录后再双击。"
}
$null = Add-DockerToPath
if (-not (Test-Command 'docker')) {
    Wait-Here '这台 Windows 还没装好容器工具链（找不到 docker.exe）。
请先双击仓库根的《安装环境-Setup.bat》，装完再回来双击本文件。'
}
if (-not (Get-ComposeLauncher)) {
    Wait-Here 'docker 在，但 compose 不可用。Docker Desktop 一般自带；先在开始菜单打开一次 Docker Desktop，
再重新双击本文件。'
}
# 后面所有“让用户照着打”的命令都用实际存在的那个前缀，不写死 docker/docker-compose。
$composeHint = Get-ComposeHint
if (-not (Test-Path (Join-Path $RepoRoot '.env'))) {
    Write-Note '仓库根没有 .env：会用编排文件的默认值起（安装环境那一步会自动生成一份）。'
}
$version = Get-FileEnvValue -Key 'DRAMACLAW_VERSION' -Default '2.0.99'
$webPort = [int](Get-FileEnvValue -Key 'ST_WEB_PORT' -Default '8080')
$apiPort = [int](Get-FileEnvValue -Key 'ST_API_PORT' -Default '8780')
$localPassword = Get-FileEnvValue -Key 'ST_LOCAL_PASSWORD'
Write-Good "版本 $version · 站点端口 $webPort"

# ────────────────────────── [2/5] 引擎 ──────────────────────────
Write-Step '[2/5] 确认 Docker 引擎在跑'
$engineReady = $false
try { & docker info *> $null; $engineReady = ($LASTEXITCODE -eq 0) } catch { $engineReady = $false }
if (-not $engineReady) {
    if (-not (Start-DockerDesktop)) {
        Wait-Here '找不到 Docker Desktop 主程序。请先双击《安装环境-Setup.bat》完成安装。'
    }
    $engineReady = Wait-DockerEngine 240
}
if (-not $engineReady) {
    Wait-Here 'Docker 引擎没就绪。看一眼 Docker Desktop 窗口的状态提示（首启要初始化 WSL，或还等你点一次「允许访问文件系统」）。'
} else {
    Write-Good 'Docker 引擎可用'
}
Set-WindowsComposeEnv

# 端口被人占了要先说清楚：本项目 web 容器不在跑却已有监听，起栈会失败或站点打到别的页面上。
$listeners = @()
try {
    $listeners = @(Get-NetTCPConnection -LocalPort $webPort -State Listen -ErrorAction SilentlyContinue)
} catch { }   # 老版本没有 Get-NetTCPConnection 就跳过这项检查，不影响启动
$ourWebRunning = (& docker ps --format '{{.Names}}' 2>$null) | Where-Object { $_ -match 'web' }
if ($listeners.Count -gt 0 -and -not $ourWebRunning) {
    $owners = ($listeners | ForEach-Object { $_.OwningProcess } | Select-Object -Unique | ForEach-Object {
        try { (Get-Process -Id $_ -ErrorAction Stop).ProcessName } catch { "PID $_" }
    }) -join ', '
    Write-Note "端口 $webPort 已被其它进程监听（$owners），本项目 web 容器又不在跑：起栈大概率会失败。"
    Write-Note "  腾出入口：改 .env 的 ST_WEB_PORT=<别的端口> 后重新双击；或先结束占用进程。"
}

# ────────────────────── [3/5] 镜像：缺则建，--rebuild 强建 ──────────────────────
Write-Step '[3/5] 检查镜像'
$needBuild = [bool]$Rebuild
foreach ($repo in @('aipdd/dramaclaw-api', 'aipdd/dramaclaw-web')) {
    $found = (& docker images -q "${repo}:$version" 2>$null) | Where-Object { $_ }
    if ($found) {
        Write-Good "${repo}:$version 已在本地"
    } else {
        Write-Note "${repo}:$version 不在本地，需要构建"
        $needBuild = $true
    }
}
if ($needBuild) {
    # 状态库的一致性快照要靠 scripts/predeploy_backup.sh（bash + 容器内 Python 的 sqlite
    # Backup API）。Windows 上没有 bash，这里**不假装**能在线备份，只把风险说清并要一次确认：
    # 双击默认路径不会走到这里，所以普通用户永远不会被问。
    & docker inspect aipdd-drama-api *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Note '本次重建不走部署前的状态库在线备份（该能力在 scripts/predeploy_backup.sh，需要 bash）。'
        $ans = Read-Host '确定要继续重建吗？回车取消，输入 y 继续'
        if ($ans -notmatch '^[yY]') {
            Write-Step '已取消重建。容器与站点保持原样。'
            exit 0
        }
    }
    if (-not (Test-DiskForBuild 20)) { exit 1 }
    Write-Step '开始构建 api / web（命中缓存分钟级；第一次可能要几十分钟，窗口保持打开）'
    if (-not (Invoke-Compose -ComposeArgs @('-f', $composeFile, 'build', 'api', 'web'))) {
        Wait-Here '构建失败，见上方输出。空间问题看第 3 步阈值；网络问题重新双击本文件重试（分层缓存会续用）。'
    }
    Write-Good '构建完成'
}

# ────────────────────────── [4/5] 起栈 ──────────────────────────
Write-Step '[4/5] 启动服务栈（api + web + 本地网关）'
if (-not (Invoke-Compose -ComposeArgs @('-f', $composeFile, 'up', '-d', '--no-build'))) {
    Wait-Here "启动失败，见上方输出。常用排查：
  $composeHint -f docker-compose.aipdd.yml ps
  $composeHint -f docker-compose.aipdd.yml logs --tail=50 api
若报挂载路径访问被拒：Docker Desktop → Settings → Resources → File sharing 里放行本仓库所在盘。"
}
Write-Good '容器已拉起'
$null = Invoke-Compose -ComposeArgs @('-f', $composeFile, 'ps')

# ─────────────────── [5/5] 等真就绪并打开浏览器 ───────────────────
# 就绪判定走 web 的 /api/v1/config：一枪同时验证 nginx、反代、后端三截；只看容器状态
# 或只 curl 首页（静态文件恒 200）都会假绿。
$gateUrl = "http://127.0.0.1:$webPort/api/v1/config"
if (Wait-HttpReady -Url $gateUrl -TimeoutSec 300 -Label '站点') {
    $siteUrl = "http://localhost:$webPort"
    Write-Step "打开浏览器：$siteUrl"
    try { Start-Process $siteUrl } catch { Write-Note "自动打开浏览器失败，手动访问 $siteUrl" }
    Write-Host ''
    Write-Good '已就绪。'
    Write-Step "站点      $siteUrl"
    if ($localPassword) {
        Write-Step '登录      账号 local，口令见 .env 的 ST_LOCAL_PASSWORD（本脚本不回显）'
    } else {
        # ST_CE_MULTI_USER=true 时后端启动就引导 local 账户；口令留空 → 随机生成并只打一次日志。
        # 用 ASCII 的 ST_LOCAL_PASSWORD 做 findstr 关键字：中文关键字在 GBK 控制台下匹配不到 UTF-8 日志。
        Write-Step '登录      账号 local，口令是首启随机生成的一次性口令，取回：'
        Write-Step "          $composeHint -f docker-compose.aipdd.yml logs api | findstr ST_LOCAL_PASSWORD"
    }
    Write-Step "后端      http://localhost:$apiPort/api/v1"
    Write-Step "停止      $composeHint -f docker-compose.aipdd.yml stop"
    Write-Step "看日志    $composeHint -f docker-compose.aipdd.yml logs -f api"
    Write-Step '改代码后  启动系统-Start.bat --rebuild'
    exit 0
} else {
    Wait-Here "服务起了但接口没就绪（后端首启要加载模型库，可能只是慢）。
再看一眼：$composeHint -f docker-compose.aipdd.yml logs --tail=80 api
确认还在启动中的话，稍后重新双击本文件即可。"
}
