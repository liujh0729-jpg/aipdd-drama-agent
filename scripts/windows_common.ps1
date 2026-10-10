# [aipdd fork delta] Windows 一键安装 / 一键启动的公共层，被仓库根的两个 .bat 间接引用。
#
# 与 Mac 版（scripts/mac_common.sh）同一套设计约束：
#   1) 双击入口很薄，逻辑集中在这里，避免两个入口各抄一遍后漂移；
#   2) 就绪判定只打真实接口，不看容器 healthy，也不 curl 恒 200 的静态首页；
#   3) 失败必须停住给人看（Wait-Here），成功才让窗口自动关——cmd /c 在脚本结束时
#      会无条件关窗，所以「停住」只能在脚本内部用 Read-Host 实现。
#
# 本文件必须存成 **UTF-8 带 BOM**：Windows PowerShell 5.1 读无 BOM 的 UTF-8 会按本地
# 代码页解析，中文提示直接成乱码（这是 Windows 侧最常见的交付事故，不是风格问题）。
#
# 诚实声明：本层在 macOS 上编写，**未在真实 Windows 机器上执行验证**。所有外部命令的
# 参数面取自 Docker 官方文档《Install Docker Desktop on Windows》（Installer flags 一节），
# 逐条列在下面注释里；改动前请优先维持那些出处，不要凭手感加 flag。

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch { }

# 仓库根：本文件在 scripts/ 下，父目录即根（.bat 不必把路径传进来，避免引号转义链）。
$RepoRoot = Split-Path -Parent $PSScriptRoot

function Write-Step { param([string]$m) Write-Host ">> $m" -ForegroundColor Cyan }
function Write-Good { param([string]$m) Write-Host "OK $m" -ForegroundColor Green }
function Write-Note { param([string]$m) Write-Host "!  $m" -ForegroundColor Yellow }

# 失败出口：停在这里，让用户读完信息再关窗。
# 退出码 3 是与两个 .bat 的约定：bat 看到 >=3 就知道「脚本已经自己报完原因并等人按过回车」，
# 不再叠加一个 pause；只有未处理崩溃（非零且 <3）才走 bat 里的兼底暂停。写错这个值会让
# 失败时要求两次回车，或多一次窗口乱乱闪。
function Wait-Here {
    param([string]$m)
    Write-Host ''
    Write-Host "X  $m" -ForegroundColor Red
    Write-Host ''
    Read-Host '按回车关闭本窗口（把上面的信息截图发给我即可）' | Out-Null
    exit 3
}

function Test-Command {
    param([string]$name)
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

# .env 取值：端口与镜像 tag 一律以 .env 为准，脚本不另立一套（与 Mac 版同口径）。
function Get-FileEnvValue {
    param([string]$Key, [string]$Default = '')
    $file = Join-Path $RepoRoot '.env'
    if (-not (Test-Path $file)) { return $Default }
    $line = Select-String -Path $file -Pattern "^\s*$Key\s*=" -Encoding UTF8 | Select-Object -Last 1
    if (-not $line) { return $Default }
    $value = ($line.Line -split '=', 2)[1].Trim()
    $value = $value.Trim('"').Trim("'")
    if ($value) { return $value } else { return $Default }
}

# 版本兜底的唯一真源是 compose 编排里 DRAMACLAW_VERSION 的 :- 默认值；两个 Windows 入口不再
# 各写一份数字，改 compose 一处即可（与 Mac 入口同口径，避免多处默认值静默漂移）。取不到时回
# 空串交给调用方兜底，这里绝不另立版本常量。沿用 Get-FileEnvValue 的 Select-String 范式。
function Get-ComposeDefaultVersion {
    $file = Join-Path $RepoRoot 'docker-compose.aipdd.yml'
    if (-not (Test-Path $file)) { return '' }
    $line = Select-String -Path $file -Encoding UTF8 -Pattern 'DRAMACLAW_VERSION:-([^}]+)' | Select-Object -First 1
    if (-not $line) { return '' }
    return $line.Matches[0].Groups[1].Value.Trim()
}

# 找到 docker.exe 所在目录并加进**当前进程**的 PATH。
# 为什么必须做：per-user 安装把路径写进用户 PATH，但正在运行的这个窗口不会自动重读，
# 症状是「装完了却说没有 docker」——与 Mac 双击时 PATH 不含 /opt/homebrew/bin 同一类坑。
function Add-DockerToPath {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin'),
        (Join-Path ${env:ProgramFiles} 'Docker\Docker\resources\bin')
    )
    foreach ($dir in $candidates) {
        if (Test-Path (Join-Path $dir 'docker.exe')) {
            if (($env:Path -split ';') -notcontains $dir) { $env:Path = "$dir;$env:Path" }
            return $dir
        }
    }
    $cmd = Get-Command docker.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source | Split-Path -Parent }
    return $null
}

# Compose 的调用形态：Docker Desktop 自带 `docker compose` 插件，独立 docker-compose.exe
# 不保证存在，所以优先走插件（官方口径），探不到才退回连字符命令。
function Get-ComposeLauncher {
    # 先确认 docker 本身在：$ErrorActionPreference='Stop' 下，对不存在的命令用 & 会直接抛
    # CommandNotFoundException，把「还没装 Docker」这种正常分支变成一屏红字崩溃。
    if (-not (Test-Command 'docker')) { return $null }
    & docker compose version --short *> $null
    if ($LASTEXITCODE -eq 0) { return @{ File = 'docker.exe'; Args = @('compose') } }
    if (Test-Command 'docker-compose.exe') { return @{ File = 'docker-compose.exe'; Args = @() } }
    return $null
}

# 只给人看的命令前缀：这台机器上到底是 `docker compose`（插件）还是 `docker-compose`
# （独立二进制），打印出来就得是哪一种；写死成其中一种，用户照抄另一种会报未知命令。
function Get-ComposeHint {
    $launcher = Get-ComposeLauncher
    if (-not $launcher) { return 'docker compose' }
    return ((@($launcher.File -replace '\.exe$', '') + @($launcher.Args)) -join ' ')
}

function Invoke-Compose {
    # 统一入口：跑完按**原生退出码**判成败——PowerShell 不会因为 docker.exe/compose 返回
    # 非零而抛异常，不显式看 $LASTEXITCODE 就会把失败静默吞掉。
    # 参数必须走【字符串数组】而不是 ValueFromRemainingArguments：后者会把 `-f` / `--no-build`
    # 当参数名去绑定，直接报「A parameter cannot be found that matches parameter name 'f'」。
    # 数组元素是值，不走破折号解析。调用形如：
    #   Invoke-Compose -ComposeArgs @('-f', $composeFile, 'up', '-d', '--no-build')
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string[]]$ComposeArgs)
    if (-not $Global:DramaClawComposeLauncher) {
        $Global:DramaClawComposeLauncher = Get-ComposeLauncher
    }
    $launcher = $Global:DramaClawComposeLauncher
    if (-not $launcher) { return $false }
    $all = @($launcher.Args) + @($ComposeArgs)
    & $launcher.File @all
    return ($LASTEXITCODE -eq 0)
}

# Windows 专用：给 compose 插值准备好两个「宿主路径」变量。
# 编排文件里这两处默认值是 Mac 路径（${HOME} 与 ${HOST_APPS_DIR:-/Applications}）；
# Windows 上 HOME 通常根本没定义、/Applications 不存在，直接把这种值交给 bind mount
# 会被解释成古怪的宿主路径并触发文件共享提示，所以这里显式给成真实存在的目录。
#   HOME           → 用户个人资料目录（容器内 /host-home，本地 Agent 工具写 MCP 配置）
#   HOST_APPS_DIR  → %LOCALAPPDATA%\Programs（Windows 的用户级应用安装目录，正是
#                    「应用本体」证据该看的地方；compose 注释也允许指向任一已存在目录）
function Set-WindowsComposeEnv {
    if (-not $env:HOME) { $env:HOME = $env:USERPROFILE }
    if (-not $env:HOST_APPS_DIR) { $env:HOST_APPS_DIR = (Join-Path $env:LOCALAPPDATA 'Programs') }
    if (-not (Test-Path $env:HOST_APPS_DIR)) {
        New-Item -ItemType Directory -Path $env:HOST_APPS_DIR -Force | Out-Null
    }
    Write-Good "compose 宿主路径：HOME=$env:HOME HOST_APPS_DIR=$env:HOST_APPS_DIR"
}

function Wait-DockerEngine {
    param([int]$TimeoutSec = 300)
    Write-Step "等待 Docker 引擎就绪（首启要初始化 WSL 发行版，可能要 1-3 分钟；最长等 $TimeoutSec 秒）"
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            & docker info *> $null
            if ($LASTEXITCODE -eq 0) {
                $v = (& docker version --format '{{.Server.Version}}' 2>$null)
                Write-Good "Docker 引擎就绪（server $v）"
                return $true
            }
        } catch {
            Start-Sleep -Seconds 3
            continue
        }
        Start-Sleep -Seconds 3
    }
    return $false
}

# 拉起 Docker Desktop 本体。装完不会自启（官方文档明确），引擎探测失败时要靠这一步。
function Start-DockerDesktop {
    $exes = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe'),
        (Join-Path ${env:ProgramFiles} 'Docker\Docker\Docker Desktop.exe')
    )
    foreach ($exe in $exes) {
        if (Test-Path $exe) {
            Write-Step "启动 Docker Desktop：$exe"
            Start-Process -FilePath $exe | Out-Null
            return $true
        }
    }
    return $false
}

function Wait-HttpReady {
    # PS 5.1 的 Invoke-WebRequest 默认要调 IE 引擎解析，服务化/无头环境会直接报错，
    # -UseBasicParsing 不是优化而是必需项。
    param([string]$Url, [int]$TimeoutSec = 300, [string]$Label = '服务')
    Write-Step "等待$Label 就绪（后端首启要加载模型库，最长等 $TimeoutSec 秒）"
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300) {
                Write-Good "$Label 就绪：$Url"
                return $true
            }
        } catch { }
        Start-Sleep -Seconds 3
    }
    Write-Note "$Label 在 $TimeoutSec 秒内没就绪：$Url"
    return $false
}

# C 盘余量提示：Docker 的 WSL 虚拟磁盘（%LOCALAPPDATA%\Docker\wsl 或 %USERPROFILE%\AppData\Local\Docker）
# 会长到十几 GB，Windows 侧没有 Mac 那种预分配配额可查，所以按宿主盘余量给门槛。
function Get-HostFreeGB {
    param([string]$Drive = 'C')
    try {
        $p = Get-PSDrive -Name $Drive
        return [math]::Round($p.Free / 1GB, 1)
    } catch { return $null }
}

function Test-DiskForBuild {
    # 两个盘都得看：Docker 的 WSL 虚拟磁盘长在**系统盘**（%LOCALAPPDATA%），而构建上下文与
    # compose 临时文件在**仓库所在盘**。只查 C 盘会在“项目在 D 盘、C 盘很空”时误拦，
    # 只查仓库盘又会漏掉真正把构建写满的那个盘。
    param([double]$MinFreeGB = 20)
    $drives = @('C', ((Split-Path -Qualifier $RepoRoot) -replace ':', '')) | Select-Object -Unique | Where-Object { $_ }
    foreach ($drive in $drives) {
        $free = Get-HostFreeGB -Drive $drive
        if ($null -eq $free) {
            Write-Note "读不到 $drive 盘余量，跳过该项闸门（构建若中途失败见 docs §8）"
            continue
        }
        if ($free -lt $MinFreeGB) {
            Wait-Here "$drive 盘只剩 $free GB（阈值 $MinFreeGB GB）。镜像 + 构建临时层会把它写满，先腾空间：
  1) docker system prune -a        # 不要动 volume，数据卷里有你的项目
  2) 清 %LOCALAPPDATA%\Docker 下的旧 vhdx（ Docker Desktop → Settings → Resources → Disk image size 可回收）
  3) 或在 Docker Desktop 里调小 wsl 数据盘后重启引擎"
            return $false
        }
        Write-Good "$drive 盘可用 $free GB（阈值 $MinFreeGB GB）"
    }
    return $true
}
