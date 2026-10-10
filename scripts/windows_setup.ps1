# [aipdd fork delta] Windows 一键环境安装的实际逻辑，由仓库根的《安装环境-Setup.bat》调用。
#
# 双击后的 7 步：系统自检 → WSL → Docker Desktop → 等引擎 → compose 与宿主路径 →
# 运行配置 .env → 首次镜像构建。走完直接返回，.bat 结束即关窗（用户要的「装好自动退出」）。
#
# 外部命令参数的出处（改动前先看，别凭手感）：
#   Docker 官方文档《Install Docker Desktop on Windows》Installer flags：
#     --user（装到 %LOCALAPPDATA%\Programs\DockerDesktop，**不需要管理员**）
#     --quiet、--accept-license（同时接受订阅协议，首启不再弹框）、--backend=wsl-2
#   同一文档明确：装完 Docker Desktop 不会自动启动，必须自己拉起来再等引擎。
#   Microsoft《Install WSL》：wsl --install / wsl --update，可能要求重启。
#
# 本脚本在 macOS 上编写，未在真实 Windows 上执行验证；口径与限制见 需求/环境说明.md §8。

. (Join-Path $PSScriptRoot 'windows_common.ps1')
Set-Location $RepoRoot          # compose 会从当前目录读 .env 做插值，必须先落到仓库根

Write-Host ''
Write-Host 'AIPDD DramaClaw · 环境安装（Windows）' -ForegroundColor White
Write-Note '全程会自动完成，不要休眠；下载体积约几百 MB 到 1 GB，慢网请留足时间。'

# ────────────────────────── [1/7] 系统自检 ──────────────────────────
Write-Step '[1/7] 系统自检'
# 不能用 $IsWindows：那是 PowerShell 6+ 才有的变量，Windows PowerShell 5.1 上它是 $null，
# `-not $null` 为真 → 在一台好机器上直接误判成「非 Windows」而中止。两侧都走得用平台枚举。
if ('Win32NT' -ne [Environment]::OSVersion.Platform) {
    Wait-Here "本脚本只给 Windows 用（当前平台 $([Environment]::OSVersion.VersionString)）。macOS 请双击《安装环境-Setup.command》。"
}
$arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
if (-not (Test-Path (Join-Path $RepoRoot 'docker-compose.aipdd.yml'))) {
    Wait-Here "本文件所在目录里找不到 docker-compose.aipdd.yml：
  $RepoRoot
请把两个 .bat 放在仓库根目录，整仓挪位置后也要仍在根目录里双击它。"
}
Write-Good "PowerShell $($PSVersionTable.PSVersion) / 架构 $arch"
Write-Good "仓库目录：$RepoRoot"
# 右键“以管理员运行”会把后面的安装带偏：Docker Desktop 走 --user 模式，装进的是
# **当前进程所属账户**的 %LOCALAPPDATA%；管理员窗口里装的，普通用户双击启动时找不到。
# 本脚本全程不需要提权（只有第 2 步 WSL 会自己弹一次系统授权框）。
try {
    $principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Note '当前是管理员权限窗口，但 Docker Desktop 会装到这个管理员账户的个人目录下，'
        Write-Note '  之后普通用户双击《启动系统-Start.bat》就找不到 docker.exe。请改用普通双击（不要“以管理员身份运行”）。'
    }
} catch { }   # 探不到就少这一句提示，不影响主流程

# ─────────────────────────── [2/7] WSL 2 ───────────────────────────
Write-Step '[2/7] 检查 WSL 2（Docker Desktop 的 Linux 引擎底座）'
function Get-WslVersion {
    if (-not (Test-Command 'wsl')) { return $null }
    # 就绪不押在单一信号上：旧逻辑把「解析不到 wsl --version」直接等同「inbox 版/未就绪」，
    # 结果用户机上 wsl --version 明明正常打印却被判死、卡在第 2 步。逐条拆掉脆弱点：
    #   · 不再因为 $LASTEXITCODE 非 0 就单独判未就绪（部分 Windows 的 wsl 会带告警返回非 0，但版本已打印出来）；
    #   · 非英文 Windows 标签本地化，只从输出抓版本号数字，正则放宽到两段及以上；
    #   · 原生调用包 try，本地化/编码下写 stderr 也不至于把整步崩掉；
    #   · --version 拿不到时，再用 wsl -l -v 是否存在 VERSION 2 的发行版兜底（Docker 的 wsl-2 后端要的就是它）。
    try { $out = ((& wsl --version 2>$null) -join ' ') } catch { $out = '' }
    if ($out -match '(\d+\.\d+(?:\.\d+){0,2})') { return $Matches[1] }
    $rows = @()
    try { $rows = @(& wsl -l -v 2>$null) } catch { $rows = @() }
    foreach ($row in $rows) {
        if (("$row").TrimEnd() -match '\s2$') { return 'WSL2' }
    }
    return $null
}
$wslVersion = Get-WslVersion
if ($wslVersion) {
    Write-Good "WSL 已就绪（版本 $wslVersion）"
} else {
    Write-Note '没探到可用的 WSL 2（wsl --version 与 wsl -l -v 都没给出可用信号）。正在安装/更新，会弹一次管理员授权框。'
    try {
        # 启用 WSL 系统组件必须提权，这一跳 UAC 在 Windows 上无法回避；后面全部走
        # per-user 安装，不再需要管理员。
        Start-Process -FilePath 'wsl' -ArgumentList '--install', '--no-launch' -Verb RunAs -Wait
    } catch {
        Write-Note "wsl --install 没成功：$($_.Exception.Message)"
    }
    $wslVersion = Get-WslVersion
    if (-not $wslVersion) {
        Wait-Here 'WSL 2 仍不可用。多为「未启用虚拟机平台 / BIOS 未开虚拟化 / 刚装完要重启」：
  1) 需要重启就先重启，再回来双击本文件（每步幂等，会从当前进度续上）
  2) 管理员 PowerShell 执行：Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -NoRestart
  3) 任务管理器 → 性能 → CPU → 「虚拟化：已启用」；若是「已禁用」进 BIOS 打开 VT-x/AMD-V
  4) 公司机被组策略挡了微软商店：去 github.com/microsoft/WSL/releases 下 msi 直接装'
    }
    Write-Good "WSL 就绪（版本 $wslVersion）"
}

# ────────────────────── [3/7] Docker Desktop（per-user） ──────────────────────
Write-Step '[3/7] 检查 Docker Desktop'
$null = Add-DockerToPath
if (Test-Command 'docker') {
    Write-Good "已安装 Docker CLI：$((Get-Command docker).Source)"
} else {
    Write-Note '许可口径一次说清：Docker Desktop 对个人、教育、以及「<250 人且年营收 <1000 万美元」的小企业免费；超出该门槛的商用需要付费订阅（政府单位一律需要）。要完全免费的路线可改用 Rancher Desktop / Podman Desktop，本脚本按 Docker Desktop 写。'
    $installed = $false
    if (Test-Command 'winget') {
        Write-Step '用 winget 安装 Docker.DockerDesktop（per-user、静默、已接受协议）'
        & winget install -e --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements `
            --override 'install --user --quiet --accept-license --backend=wsl-2'
        $installed = ($LASTEXITCODE -eq 0)
        if (-not $installed) { Write-Note "winget 安装未成功（退出码 $LASTEXITCODE），改用官方安装包直装" }
    } else {
        Write-Note '没有 winget（旧版 Windows 的「应用安装程序」未装），改用官方安装包直装。'
    }
    if (-not $installed) {
        $archSegment = if ($arch -eq 'ARM64') { 'arm64' } else { 'amd64' }
        $url = "https://desktop.docker.com/win/main/$archSegment/Docker%20Desktop%20Installer.exe"
        $pkg = Join-Path $env:TEMP 'DockerDesktopInstaller.exe'
        Write-Step "下载官方安装包：$url"
        try {
            # 进度条在 PS 5.1 上会把 Invoke-WebRequest 拖慢一个数量级，必须关掉。
            $ProgressPreference = 'SilentlyContinue'
            [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri $url -OutFile $pkg -UseBasicParsing
        } catch {
            Wait-Here "下载 Docker Desktop 失败：$($_.Exception.Message)
可用浏览器手动下载同地址的 Docker Desktop Installer.exe 并双击安装（选 WSL 2 后端），
装完再回来双击本文件即可续上——后面的步骤会自动检测到已装好的 docker.exe。"
        }
        Write-Step '运行安装（--user 模式不需要管理员）'
        Start-Process -FilePath $pkg -ArgumentList @('install', '--user', '--quiet', '--accept-license', '--backend=wsl-2') -Wait
    }
    $null = Add-DockerToPath
    if (-not (Test-Command 'docker')) {
        Wait-Here "Docker Desktop 装完了，但当前窗口找不到 docker.exe。
关掉本窗口，重新双击《安装环境-Setup.bat》即可续上（PATH 是这次写进用户环境的，新窗口才读得到）。
如果这一跳是在「以管理员身份运行」的窗口里装的 Docker，那它进的是管理员账户的目录，普通用户永远读不到：
用普通账户重新双击本文件再装一次即可（--user 模式不需要管理员）。"
    }
    Write-Good "Docker CLI：$((Get-Command docker).Source)"
}

# ──────────────────────── [4/7] 拉起引擎并等它可用 ────────────────────────
Write-Step '[4/7] 启动 Docker 引擎'
$engineReady = $false
try {
    & docker info *> $null
    $engineReady = ($LASTEXITCODE -eq 0)
} catch { $engineReady = $false }
if ($engineReady) {
    Write-Good 'Docker 引擎已在运行'
} else {
    if (-not (Start-DockerDesktop)) {
        Wait-Here '找不到 Docker Desktop 主程序（Docker Desktop.exe）。开始菜单搜 Docker 手动开一次，再回来双击本文件。'
    }
    $engineReady = Wait-DockerEngine 300
    if (-not $engineReady) {
        Wait-Here 'Docker 引擎 5 分钟内没就绪。先看一眼 Docker Desktop 窗口在等什么：
  1) 首次启动会自己初始化 WSL 发行版，可能还要多等一两分钟
  2) 弹了「需要访问文件系统 / 文件共享」的框就点允许（本项目要把用户目录挂进容器）
  3) 报 WSL 相关错误：管理员 PowerShell 跑 wsl --update 后重开 Docker Desktop'
    }
}

# ──────────────────── [5/7] compose 命令与宿主路径口径 ────────────────────
Write-Step '[5/7] 检查 compose 与挂载路径'
$launcher = Get-ComposeLauncher
if (-not $launcher) {
    Wait-Here "既没有 docker compose 插件也没有 docker-compose.exe。
Docker Desktop 一般自带 compose；若确实没有，打开 Docker Desktop → Settings → 确认安装完整后重试。"
}
# 后面所有“让用户照着打”的命令都用这个前缀，不写死 docker/docker-compose。
$composeHint = Get-ComposeHint
Write-Good "compose 命令：$composeHint"
Set-WindowsComposeEnv

# ────────────────────────── [6/7] 运行配置 ──────────────────────────
Write-Step '[6/7] 检查运行配置 .env'
$envFile = Join-Path $RepoRoot '.env'
if (-not (Test-Path $envFile)) {
    if (Test-Path (Join-Path $RepoRoot '.env.example')) {
        Copy-Item (Join-Path $RepoRoot '.env.example') $envFile
        Write-Good '已从 .env.example 生成 .env（默认值即可起站；渠道 Key 之后在网页「设置」里填也一样）'
    } else {
        Write-Note '既没有 .env 也没有 .env.example：将只用编排文件里的默认值启动。'
    }
}
$version = Get-FileEnvValue -Key 'DRAMACLAW_VERSION' -Default (Get-ComposeDefaultVersion)
$webPort = Get-FileEnvValue -Key 'ST_WEB_PORT' -Default '8080'
$apiPort = Get-FileEnvValue -Key 'ST_API_PORT' -Default '8780'
if (-not (Get-FileEnvValue -Key 'ST_LOCAL_PASSWORD')) {
    Write-Note '登录口令未配置：后端启动时会为账户 local 生成一次性口令（只打一次日志）。'
    # 抓 ASCII 的 `ST_LOCAL_PASSWORD` 而不是中文“一次性口令”：findstr 按当前代码页比对字节，
    # GBK 控制台下用中文去找 UTF-8 日志会匹配不到（同一条日志里两个 token 都在，拿一定在的那个）。
    Write-Host "    取回：$composeHint -f docker-compose.aipdd.yml logs api | findstr ST_LOCAL_PASSWORD"
    Write-Host '    想固定它：在 .env 里写 ST_LOCAL_PASSWORD=<你的口令>（留空即走上面的随机路径）'
}
Write-Good "版本 $version · 站点 http://localhost:$webPort · 后端 $apiPort"

# ────────────────────── [7/7] 首版镜像与网关预拉 ──────────────────────
Write-Step '[7/7] 准备镜像（api / web 源码构建，网关拉发布镜像）'
$composeFile = Join-Path $RepoRoot 'docker-compose.aipdd.yml'
$needBuild = $false
foreach ($repo in @('aipdd/dramaclaw-api', 'aipdd/dramaclaw-web')) {
    $found = (& docker images -q "${repo}:$version" 2>$null) | Where-Object { $_ }
    if ($found) {
        Write-Good "${repo}:$version 已存在"
    } else {
        $needBuild = $true
    }
}
if ($needBuild) {
    if (-not (Test-DiskForBuild 20)) { exit 1 }
    Write-Step '开始构建（第一次要下载基础镜像与依赖，可能 40-90 分钟；命中缓存则分钟级）。窗口保持打开。'
    $okBuild = Invoke-Compose -ComposeArgs @('-f', $composeFile, 'build', 'api', 'web')
    if (-not $okBuild) {
        Wait-Here "镜像构建失败，见上方输出。定位顺序：
  1) 报 no space left → 宿主盘满，先腾空间（本脚本第 7 步的阈值是 20 GB）
  2) 报网络超时 → 直接重新双击《安装环境-Setup.bat》，分层缓存会续用已下载的部分
  3) 报路径/权限 → Docker Desktop 里确认已允许访问 C 盘用户目录"
    }
    Write-Good 'api / web 镜像构建完成'
}

Write-Step '预拉网关镜像（失败不阻断，启动时会自动再拉）'
if (Invoke-Compose -ComposeArgs @('-f', $composeFile, 'pull', '--quiet', 'newapi')) {
    Write-Good '网关镜像就绪'
} else {
    Write-Note '网关镜像预拉失败（多为网络），启动时会自动重试'
}

Write-Host ''
Write-Good '环境安装完成。'
Write-Step '下一步：双击仓库根的《启动系统-Start.bat》，站点会自动打开。'
# 成功路径直接返回：.bat 结束 → cmd 关窗，即用户要的「装好以后自动退出」。
exit 0
