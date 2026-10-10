#!/usr/bin/env bash
# [aipdd fork delta] Mac 一键安装 / 一键启动的公共层，被仓库根的两个 .command 引用。
#
# 为什么单独抽一层：两个入口（安装环境、启动系统）都要做同一批判断——PATH 补齐、
# Colima 是否起着、Docker 守护进程是否可达、镜像盘够不够、api 是否就绪、成功/失败时
# 窗口怎么处置。两处各抄一遍必然漂移，而这类脚本的漂移是「双击后静默失败」级别。
#
# 三条硬约束（改脚本前先读）：
#   1) **双击环境的 PATH 与登录 shell 不同**：Finder 打开 Terminal 执行 .command 时不保证
#      有 /opt/homebrew/bin，这里显式补齐，否则「我终端里明明有 brew」而双击找不到。
#   2) **变量一律小写**：仓库的 env 棘轮 scripts/check_env_config.py 会把 shell 里
#      大写下划线形式的 ${NAME} 当作运维 env 键，要求登记进 .env.example。
#   3) **失败必须停住给人看，成功才自动关窗口**：一键脚本最糟的结果是「窗口一闪没了、
#      什么也没装」。abort() 会停在按回车，并把可直接复制的下一步命令打在屏幕上。
#
# 本机环境口径（版本、compose 是 v1、VM 磁盘、慢网耗时）见 需求/环境说明.md。
set -uo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/opt/homebrew/sbin:$PATH"

log() { printf '\033[1;36m▸\033[0m %s\n' "$*"; }
ok() { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m! \033[0m %s\n' "$*"; }

# 失败出口：停在这里，让用户读到信息（不自动关窗口）。
abort() {
  printf '\n\033[1;31m✗ %s\033[0m\n\n' "$*" >&2
  printf '本窗口不会自动关闭。把上面的信息截图或复制发给我即可。\n按回车关闭本窗口：' >&2
  read -r _ignored || true
  exit 1
}

# 成功出口：倒计时后自动关 Terminal 窗口（容器与站点在后台继续跑，关窗口不影响）。
finish_success() {
  local secs="${1:-6}"
  log "完成。${secs} 秒后自动关闭本窗口（后台服务继续运行，关窗口不影响）"
  while [ "$secs" -gt 0 ]; do
    printf '  %d…\r' "$secs"
    sleep 1
    secs=$((secs - 1))
  done
  printf '\n'
  close_terminal_window
}

close_terminal_window() {
  if [ "${TERM_PROGRAM:-}" = "Apple_Terminal" ]; then
    osascript -e 'tell application "Terminal" to close front window' >/dev/null 2>&1 || true
  fi
  # iTerm / 在终端里手动执行的情形：只退出脚本，窗口由用户自己处置。
  exit 0
}

# .env 里取一个键，取不到用默认值（端口与版本都以 .env 为准，脚本不另立一套）。
env_value() {
  local key="$1" default="${2:-}" file="${3:-.env}" line value
  [ -f "$file" ] || { printf '%s' "$default"; return; }
  line="$(grep -E "^[[:space:]]*${key}=" "$file" | tail -1 || true)"
  value="${line#*=}"
  value="${value%\"}"; value="${value#\"}"
  value="${value%\'}"; value="${value#\'}"
  if [ -n "$value" ]; then printf '%s' "$value"; else printf '%s' "$default"; fi
}

# 版本兜底的唯一真源是 compose 编排里 DRAMACLAW_VERSION 的 :- 默认值；两个 Mac 入口不再各
# 写一份数字，改 compose 一处即可（与 Windows 入口同口径，避免多处默认值静默漂移）。取不到
# （compose 里没有 :- 默认）时回空串，由调用方决定最后兜底，绝不在此另立版本常量。
compose_default_version() { # $1=compose 文件
  local f line
  f="${1:-docker-compose.aipdd.yml}"
  [ -f "$f" ] || return 0
  line="$(sed -n 's/.*DRAMACLAW_VERSION:-\([^}]*\)}.*/\1/p' "$f" 2>/dev/null | head -1)"
  printf '%s' "$line"
}

colima_running() { colima status >/dev/null 2>&1; }

# 把 Colima 虚拟机拉起来（已运行则直接返回）。不在此改动 VM 配置：磁盘是预分配文件，
# 静默调大配额等于要求 macOS 立刻让出等量空间，那必须是安装脚本显式第一步做的事。
ensure_colima_running() {
  if colima_running; then
    ok "Colima 虚拟机在运行"
    return 0
  fi
  log "启动 Colima 虚拟机（首次可能要 1-3 分钟，慢网更久）"
  colima start || return 1
  local waited=0
  until colima_running; do
    sleep 2
    waited=$((waited + 2))
    if [ "$waited" -ge 180 ]; then warn "等待 Colima 就绪超时（180s）"; return 1; fi
  done
  ok "Colima 已就绪"
}

# 守护进程可达性按 `docker info` 判定，不看进程存在与否（colima 起了但 socket 未换过来的窗口期很常见）。
wait_docker_daemon() {
  local timeout="${1:-120}" waited=0
  until docker info >/dev/null 2>&1; do
    sleep 2
    waited=$((waited + 2))
    if [ "$waited" -ge "$timeout" ]; then return 1; fi
  done
  ok "Docker 守护进程可达（context: $(docker context show 2>/dev/null || echo 未知)）"
}

# Docker 虚拟机可用空间（GB）。口径与 scripts/deploy_disk_guard.sh 一致：先问在跑的容器，
# 没有容器就直接进 VM——全新机器上一个容器都没有，只按容器探测的那套会误判成「空间未知」而拦住首次构建。
vm_free_gb() {
  local container=""
  container="$(docker ps --format '{{.Names}}' 2>/dev/null | head -1 || true)"
  if [ -n "$container" ]; then
    docker exec "$container" df -k / 2>/dev/null | tail -1 | awk '{printf "%d", $4/1024/1024}'
    return 0
  fi
  colima ssh -- df -k / 2>/dev/null | tail -1 | awk '{printf "%d", $4/1024/1024}'
}

# 构建前的空间闸门：一次 api/web 构建要 5 GB+ 临时层，VM 盘满时构建会烧到一半再
# `no space left on device`（req39 实测），所以宁可提前拒绝。
require_disk_for_build() {
  local min_gb="${1:-8}" free
  free="$(vm_free_gb)"
  if [ -z "$free" ]; then
    warn "读不到 Docker 虚拟机可用空间，跳过空间闸门（继续构建，若报 no space left 见 需求/环境说明.md §3）"
    return 0
  fi
  if [ "$free" -lt "$min_gb" ]; then
    abort "Docker 虚拟机只剩约 ${free} GB（阈值 ${min_gb} GB），继续构建大概率中途报 no space left on device。
按顺序做：
  1) docker builder prune -f
  2) docker system prune -a        # 不要加 --volumes，数据卷里有你的项目与产物
  3) 还不够就清 Exited 容器：docker ps -a
（镜像与中间层是构建缓存本体，别手抖删掉——见 需求/环境说明.md §3 的纪律）"
  fi
  ok "Docker 虚拟机可用约 ${free} GB（阈值 ${min_gb} GB）"
}

# 轮询一个 HTTP 地址直到 200。就绪判定一律打**真实接口**，不看容器 status 里的 `healthy`：
# api 首启要加载模型库（cognee 冷启动实测 87-250s），healthy 明显滞后于可用；反之
# 容器在跑但接口还在 502 也是常态。所以只能以接口返回为准。
wait_http_ready() { # $1=url $2=超时秒 $3=人话标签
  local url="$1" timeout="${2:-300}" label="${3:-服务}" waited=0
  log "等待${label}就绪（首启含模型库冷启动，最长等 ${timeout}s）"
  until curl -fsS --max-time 3 "$url" >/dev/null 2>&1; do
    sleep 3
    waited=$((waited + 3))
    if [ "$waited" -ge "$timeout" ]; then
      warn "${label}在 ${timeout}s 内没就绪：$url"
      return 1
    fi
  done
  ok "${label}就绪：$url"
}
