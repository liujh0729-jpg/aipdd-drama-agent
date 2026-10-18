#!/usr/bin/env bash
#
#  启动系统 · Start —— AIPDD DramaClaw（Mac 一键启动）
#
#  用法：**双击本文件**。它会自己把虚拟机拉起来 → 起服务 → 等接口真就绪 → 打开浏览器，
#  然后关闭本窗口（服务在后台继续跑，关窗口不影响）。
#
#  已经在跑的时候再双击也没关系：`up -d` 是幂等的，只会把缺的补起来。
#
#  开发者附加用法（不双击时用）：
#  bash 启动系统-Start.command --rebuild
#    → 启动前先做状态库备份（scripts/predeploy_backup.sh），再重建 api/web 镜像后起栈。
#    改过代码想让页面生效就走这条；默认路径**绝不**擅自重建，避免双击要等几十分钟。
#
set -uo pipefail

root_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/mac_common.sh
. "$root_dir/scripts/mac_common.sh"
cd "$root_dir"

# 变量引用一律带花括号：bash 5.1+ 允许非 ASCII 字符进变量名，`$arg（` 会被解析成
# 另一个（不存在的）变量，set -u 下直接把脚本打死——实测踩过，别省那两个字符。
rebuild=0
for arg in "$@"; do
  case "$arg" in
    --rebuild) rebuild=1 ;;
    *) warn "忽略未知参数：${arg}（支持的只有 --rebuild）" ;;
  esac
done

compose_file="$root_dir/docker-compose.aipdd.yml"
printf '\n\033[1m%s\033[0m\n\n' "AIPDD DramaClaw · 一键启动（Mac）"

# ────────────────────────── [1/5] 自检 ──────────────────────────
log "[1/5] 自检"
[ -f "$compose_file" ] || abort "找不到编排文件：$compose_file
请把本文件放在仓库根目录后再双击。"
[ -f "$root_dir/.env" ] || warn "仓库根没有 .env：会用编排文件的默认值起（安装环境那一步会自动生成一份）。"

if ! command -v docker >/dev/null 2>&1 || { ! command -v docker-compose >/dev/null 2>&1 && ! docker compose version >/dev/null 2>&1; }; then
  abort "这台 Mac 还没装好容器工具链（docker / compose 找不到）。
请先双击仓库根的《安装环境-Setup.command》，装完再回来双击本文件。"
fi
if command -v docker-compose >/dev/null 2>&1; then
  compose_cmd=(docker-compose)
else
  compose_cmd=(docker compose)   # 装了 Docker Desktop / compose 插件的机器走这条
fi

version="$(env_value DRAMACLAW_VERSION "$(compose_default_version "$root_dir/docker-compose.aipdd.yml")" "$root_dir/.env")"
web_port="$(env_value ST_WEB_PORT 8080 "$root_dir/.env")"
api_port="$(env_value ST_API_PORT 8780 "$root_dir/.env")"
# 登录口令只报「去哪拿」，绝不回显到屏幕（记忆与安全纪律：密钥不上屏）。
local_password="$(env_value ST_LOCAL_PASSWORD "" "$root_dir/.env")"
ok "编排 ${compose_cmd[*]} · 版本 ${version} · 站点端口 ${web_port}"

# ─────────────────── [2/5] 虚拟机与 Docker 守护进程 ───────────────────
log "[2/5] 启动 Docker 虚拟机"
command -v colima >/dev/null 2>&1 || abort "缺 colima（本项目的 Docker 引擎）。请先双击《安装环境-Setup.command》。"
ensure_colima_running || abort "Colima 虚拟机起不来。看原因：colima status、colima start --verbose"
wait_docker_daemon 120 || abort "Docker 守护进程连不上。看原因：docker context ls（应为 colima）、colima status"

# 端口被人占了就自动避让：目标 web 端口被外部进程监听（且不是本项目的 web 容器）时，
# 从该端口起连续探 10 个取第一个空闲的，仅本次生效（export 的 ST_WEB_PORT 覆盖 .env 插值，
# 不写回文件——下双击若原端口空了就自然回到默认）。gate_url / site_url 都引用 ${web_port}，自动跟随。
if lsof -nP -iTCP:"${web_port}" -sTCP:LISTEN >/dev/null 2>&1 \
   && [ -z "$(docker ps -q --filter 'name=web' 2>/dev/null)" ]; then
  orig_port="${web_port}"
  last_port="$((orig_port + 9))"
  occupier="$(lsof -nP -iTCP:"${orig_port}" -sTCP:LISTEN 2>/dev/null | tail -n +2 | head -n 1)"
  new_port=""
  for probe in $(seq "${orig_port}" "${last_port}"); do
    if ! lsof -nP -iTCP:"${probe}" -sTCP:LISTEN >/dev/null 2>&1; then
      new_port="${probe}"
      break
    fi
  done
  if [ -n "${new_port}" ]; then
    warn "端口 ${orig_port} 已被其它进程监听（${occupier}），本次站点自动改用 ${new_port}（仅本次生效）。"
    web_port="${new_port}"
    export ST_WEB_PORT="${web_port}"
  else
    abort "端口 ${orig_port}–${last_port} 全部被占用，无法自动避让。
请腾出其中之一（若是在跑的旧栈：${compose_cmd[*]} -f docker-compose.aipdd.yml down），
或改 .env 的 ST_WEB_PORT=<空闲端口> 后重新双击。"
  fi
fi

# ─────────────────────── [3/5] 镜像：缺则建，--rebuild 强建 ───────────────────────
log "[3/5] 检查镜像"
need_build="$rebuild"
for repo in aipdd/dramaclaw-api aipdd/dramaclaw-web aipdd/dramaclaw-gateway; do
  if [ -n "$(docker images -q "${repo}:${version}" 2>/dev/null)" ]; then
    ok "${repo}:${version} 已在本地"
  else
    warn "${repo}:${version} 不在本地，需要构建"
    need_build=1
  fi
done

if [ "$need_build" = "1" ]; then
  # 重建前必做状态库备份：镜像本身无状态，但 up 会 recreate 容器，
  # 而部署侧曾出过「迁移/重建把项目数据打回空库」的事故，快照是唯一的后悔药。
  if docker inspect aipdd-drama-api >/dev/null 2>&1; then
    log "部署前备份状态库（scripts/predeploy_backup.sh）"
    if bash "$root_dir/scripts/predeploy_backup.sh" "launcher-$(date +%Y%m%d_%H%M%S)"; then
      ok "备份完成，快照在 backups/predeploy-launcher-*"
    else
      abort "状态库备份失败，已停止（不带着未备份的库去重建）。
排查上面输出后重试；确认可跳过时手动执行：
  ${compose_cmd[*]} -f docker-compose.aipdd.yml build api web newapi"
    fi
  else
    warn "api 容器还没建过（首次构建），无需备份"
  fi

  require_disk_for_build 8
  log "开始构建 api / web / 本地网关（命中缓存分钟级；慢网全量可能要几十分钟，窗口保持打开 ）"
  # 不传 --no-ansi：compose v2 的 build 不认子命令后的这个全局参数（实测 unknown flag）。
  "${compose_cmd[@]}" -f "$compose_file" build api web newapi \
    || abort "构建失败，见上方输出。空间问题看 需求/环境说明.md §3；网络问题直接再双击本文件重试（分层缓存会续用）。"
  ok "构建完成"
fi

# ────────────────────────── [4/5] 起栈 ──────────────────────────
log "[4/5] 启动服务栈（api + web + 本地网关）"
"${compose_cmd[@]}" -f "$compose_file" up -d --no-build \
  || abort "启动失败，见上方输出。常用排查：
  ${compose_cmd[*]} -f docker-compose.aipdd.yml ps
  ${compose_cmd[*]} -f docker-compose.aipdd.yml logs --tail=50 api"
ok "容器已拉起"
"${compose_cmd[@]}" -f "$compose_file" ps 2>/dev/null | sed 's/^/    /'

# ─────────────────── [5/5] 等真就绪并打开浏览器 ───────────────────
# 就绪判定走 web 的 /api/v1/config：它同时验证了 nginx、反代、后端三截，
# 比只看容器状态或只 curl 首页（静态文件恒 200）都更诚实。
gate_url="http://127.0.0.1:${web_port}/api/v1/config"
if wait_http_ready "$gate_url" 300 "站点"; then
  site_url="http://localhost:${web_port}"
  log "打开浏览器：$site_url"
  open "$site_url" || warn "自动打开浏览器失败，手动访问 $site_url"
  printf '\n\033[1;32m%s\033[0m\n' "已就绪。"
  log "站点      ${site_url}"
  if [ -n "$local_password" ]; then
    log "登录      账号 local，口令见 .env 的 ST_LOCAL_PASSWORD（本脚本不回显）"
  else
    # 没配 ST_LOCAL_PASSWORD 时后端不会替用户造账户（启动钩子与 login 同口径），
    # 空库 → /auth/setup-status 返回 needs_setup → 登录页直接给初始化表单。
    log '登录      首次打开请在页面里设置用户名与初始密码（完成初始化后才能登录）'
    log '          初始化只认第一次提交，超管建好后加人走「设置 → 用户管理」'
  fi
  log "后端      http://localhost:${api_port}/api/v1"
  log "停止      ${compose_cmd[*]} -f docker-compose.aipdd.yml stop"
  log "看日志    ${compose_cmd[*]} -f docker-compose.aipdd.yml logs -f api"
  log "改代码后  bash 启动系统-Start.command --rebuild"
  finish_success 8
else
  abort "服务起了但接口没就绪（后端首启要加载模型库，可能只是慢）。
再看一眼：
  ${compose_cmd[*]} -f docker-compose.aipdd.yml logs --tail=80 api
确认在启动中的话，稍后重新双击本文件即可。"
fi
