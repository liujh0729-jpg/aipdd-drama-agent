#!/usr/bin/env bash
#
#  安装环境 · Setup —— AIPDD DramaClaw（Mac 一键环境安装）
#
#  用法：**双击本文件**。窗口里会自己走完 7 步，装完自动关闭。
#  装的是本系统跑起来所需的全部环境：Xcode 命令行工具 → Homebrew → Colima（Docker 虚拟机）
#  → Docker CLI → docker-compose → 按本项目口径初始化虚拟机 → 首次镜像构建。
#
#  为什么要脚本化这几步：本机不用 Docker Desktop，Docker 由 Colima 提供，而 Colima 的
#  虚拟机配置（内存/磁盘/挂载）一旦设错，症状是「构建时 vite 被 SIGKILL」或「容器里看不到
#  宿主 /Applications」这类离根因很远的问题（详见 需求/环境说明.md）。
#
#  失败时本窗口不会自动关；成功才关。需要重跑直接再双击一次——每步都是幂等的。
#
set -uo pipefail

root_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/mac_common.sh
. "$root_dir/scripts/mac_common.sh"
cd "$root_dir"

# —— 虚拟机口径（只在「首次初始化」时生效，已有虚拟机不会被静默改动）——
colima_cpu=4
colima_memory=8          # GB：低于 8 时前端 vite 构建与媒体任务会被 OOM 杀掉（实测 SIGKILL）
colima_disk=40           # GB：预分配文件，要求 macOS 立刻让出等量空间，别贪大
build_min_free_gb=8      # 首次构建前的空间闸门阈值

printf '\n\033[1m%s\033[0m\n\n' "AIPDD DramaClaw · 环境安装（Mac）"
log "安装过程会比较久（慢网下首包要下几十分钟），请保持本窗口打开、不要休眠。"

# ────────────────────────── [1/7] 自检 ──────────────────────────
log "[1/7] 自检系统与仓库位置"
[ "$(uname -s)" = "Darwin" ] || abort "本脚本只给 macOS 用（当前 $(uname -s)）。Linux 请按 需求/环境说明.md 用 docker-compose 直接起。"
[ -f "$root_dir/docker-compose.aipdd.yml" ] || abort "本文件所在目录里找不到 docker-compose.aipdd.yml：
  $root_dir
请把本 .command 放在仓库根目录，或整仓挪到别处后仍在根目录里双击它。"
ok "macOS $(sw_vers -productVersion) / $(uname -m)"
ok "仓库目录：$root_dir"

# ────────────────────── [2/7] Xcode 命令行工具 ──────────────────────
log "[2/7] 检查 Xcode 命令行工具（brew 编译安装依赖它）"
if xcode-select -p >/dev/null 2>&1; then
  ok "已安装：$(xcode-select -p)"
else
  warn "未安装，正在弹出系统安装框（点「安装」即可，无需 Xcode 完整 IDE）"
  xcode-select --install >/dev/null 2>&1 || true
  printf '等待安装完成（最长 30 分钟，装好后本脚本自动继续）'
  waited=0
  until xcode-select -p >/dev/null 2>&1; do
    sleep 10
    waited=$((waited + 10))
    printf '.'
    if [ "$waited" -ge 1800 ]; then
      abort "等了 30 分钟仍未检测到 Xcode 命令行工具。请在「系统设置 → 通用 → 软件更新」里确认安装，或手动执行：
  xcode-select --install
完成后重新双击本文件。"
    fi
  done
  printf '\n'
  ok "Xcode 命令行工具就绪"
fi

# ────────────────────────── [3/7] Homebrew ──────────────────────────
log "[3/7] 检查 Homebrew"
if ! command -v brew >/dev/null 2>&1; then
  warn "未检测到 Homebrew——Colima / Docker CLI / docker-compose 都靠它装。"
  printf '官方安装脚本会要求输入你的 Mac 登录密码（只在系统安装框里用，本脚本不碰、也不记录）。现在安装吗？[Y/n] '
  read -r answer || answer="n"
  case "${answer:-y}" in
    [nN]*) abort "已取消安装 Homebrew，环境装不齐。想手动装完再回来双击本文件：
  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"" ;;
  esac
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
    || abort "Homebrew 安装失败，见上方输出（多为网络问题）。恢复网络后重新双击本文件即可续上。"
  for prefix in /opt/homebrew /usr/local; do
    [ -x "$prefix/bin/brew" ] && eval "$("$prefix/bin/brew" shellenv)" && break
  done
fi
command -v brew >/dev/null 2>&1 || abort "Homebrew 装完了但当前窗口找不到 brew。请关掉本窗口重新双击本文件。"
ok "brew $(brew --version | head -1 | awk '{print $2}')（$(brew --prefix)）"

# ─────────────── [4/7] Colima / Docker CLI / docker-compose ───────────────
log "[4/7] 安装容器工具链（已装的自动跳过）"
install_formula() { # $1=formula 名 $2=人话说明
  if brew list "$1" >/dev/null 2>&1; then
    ok "$1 已安装（$(brew list --versions "$1" | awk '{print $2}')）"
    return 0
  fi
  log "brew install $1 —— $2"
  brew install "$1" || abort "安装 $1 失败，见上方输出。可单独重试：brew install $1，然后重新双击本文件。"
  ok "$1 安装完成"
}
install_formula colima "Docker 虚拟机（本项目的 Docker 引擎，不用 Docker Desktop）"
install_formula docker "docker 命令行"
install_formula docker-compose "compose 命令行"

# 编排命令按本机实际有的来：本机历史上是独立 docker-compose；装了 compose 插件的机器也认。
if command -v docker-compose >/dev/null 2>&1; then
  compose_cmd=(docker-compose)
elif docker compose version >/dev/null 2>&1; then
  compose_cmd=(docker compose)
else
  abort "既没有 docker-compose 也没有 docker compose 子命令，无法继续。"
fi
ok "编排命令：${compose_cmd[*]}"

# ─────────────────── [5/7] 虚拟机：初始化 / 启动 / 挂载检查 ───────────────────
log "[5/7] 准备 Docker 虚拟机（Colima）"
if [ ! -d "$HOME/.colima" ]; then
  log "首次创建虚拟机：cpu=${colima_cpu} memory=${colima_memory}GB disk=${colima_disk}GB"
  log "并按本项目要求挂载宿主 home（本地 Agent 工具写 MCP 配置）与 /Applications（安装证据）"
  # --mount 是替换语义，两条必须同时给，否则会把默认的 home 共享顶掉（compose 文件里踩过）。
  colima start --cpu "$colima_cpu" --memory "$colima_memory" --disk "$colima_disk" \
    --mount "$HOME:w" --mount /Applications \
    || abort "Colima 虚拟机创建失败，见上方输出。常见原因是 macOS 磁盘空间不足（要预留 ${colima_disk} GB）。"
else
  log "已存在虚拟机，沿用现有配置（本脚本不会静默改动磁盘/内存配额）"
  ensure_colima_running || abort "Colima 启动失败，见上方输出。可手动看原因：colima start --verbose"
fi
wait_docker_daemon 120 || abort "Docker 守护进程连不上。手动排查：colima status / docker context ls（当前应为 colima）。"
ok "docker $(docker version --format '{{.Server.Version}}' 2>/dev/null)"

if ! colima ssh -- test -d /Applications >/dev/null 2>&1; then
  warn "虚拟机里看不到宿主 /Applications：MCP「本地 Agent 工具」的应用本体证据会退化成配置痕迹判断（其余功能不受影响）。"
  printf '  要补齐请手动执行一次（会重启虚拟机）：\n'
  printf '    colima stop && colima start --mount %s:w --mount /Applications\n\n' "$HOME"
fi

# ────────────────────────── [6/7] 配置文件 ──────────────────────────
log "[6/7] 检查运行配置 .env"
if [ ! -f "$root_dir/.env" ]; then
  if [ -f "$root_dir/.env.example" ]; then
    cp "$root_dir/.env.example" "$root_dir/.env"
    ok "已从 .env.example 生成 .env（可直接用默认值起，功能键后续在网页「设置」里填也行）"
  else
    warn "既没有 .env 也没有 .env.example：将只用编排文件里的默认值启动。"
  fi
fi
version="$(env_value DRAMACLAW_VERSION "$(compose_default_version "$root_dir/docker-compose.aipdd.yml")" "$root_dir/.env")"
web_port="$(env_value ST_WEB_PORT 8080 "$root_dir/.env")"
api_port="$(env_value ST_API_PORT 8780 "$root_dir/.env")"
if [ -z "$(env_value ST_LOCAL_PASSWORD "" "$root_dir/.env")" ]; then
  warn "登录口令未配置：系统不会替你造账户，首次打开网页会让你自己设置用户名和初始密码。"
  printf '    想固定它：在 .env 里写 ST_LOCAL_PASSWORD=<你的口令>（那样启动即建 local 账户，跳过初始化表单）\n\n'
fi
ok "版本 ${version} · 站点 http://localhost:${web_port} · 后端 ${api_port}"

# ──────────────────── [7/7] 首次构建镜像 + 预拉网关镜像 ────────────────────
log "[7/7] 准备镜像（api / web 源码构建，newapi 网关拉发布镜像）"
need_build=0
for repo in aipdd/dramaclaw-api aipdd/dramaclaw-web; do
  if [ -z "$(docker images -q "${repo}:${version}" 2>/dev/null)" ]; then
    need_build=1
  else
    ok "${repo}:${version} 已存在"
  fi
done

if [ "$need_build" = "1" ]; then
  require_disk_for_build "$build_min_free_gb"
  log "开始构建（慢网首全量可能 40-90 分钟；命中缓存则分钟级）。窗口别关，进度在下面。"
  # 不传 --no-ansi：本机 compose 已是 v2 独立二进制（5.5.1），它的 build 子命令不认这个位置的全局参数，
  # 传了会在 flag 解析阶段直接 unknown flag 失败（需求/环境说明.md 里那行旧写法是 v1 时代抄下的）。
  "${compose_cmd[@]}" -f "$root_dir/docker-compose.aipdd.yml" build api web \
    || abort "镜像构建失败，见上方最后一段输出。定位办法：
  1) 报 no space left → 见 需求/环境说明.md §3（清 Exited 容器，别删镜像层）
  2) 报网络超时 → 直接重试；三段式分层会续用已下载的层
  修好后重新双击本文件即可续上。"
  ok "api / web 镜像构建完成"
fi

log "预拉网关镜像（失败不阻断，启动时会自动再拉）"
"${compose_cmd[@]}" -f "$root_dir/docker-compose.aipdd.yml" pull --quiet newapi >/dev/null 2>&1 \
  && ok "网关镜像就绪" || warn "网关镜像预拉失败（多为网络），启动时会自动重试"

printf '\n\033[1;32m%s\033[0m\n' "环境安装完成。"
log "下一步：双击仓库根的《启动系统-Start.command》，站点会自动打开。"
finish_success 8
