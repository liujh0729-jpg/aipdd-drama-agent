#!/usr/bin/env bash
# =============================================================================
# prepare-resources.sh —— 在“对应架构构建机”上把全套后端运行时准备进 Tauri 资源目录。
#
#   用法： ./prepare-resources.sh <mac-arm64|mac-x64|win-x64>
#   产物： desktop/src-tauri/resources/{python,bin,app/dist,app/src,nginx}
#
# 关键约束（与需求一致）：
#   - 便携 Python 用 python-build-standalone（astral-sh 官方发布），禁用 Anaconda/Miniconda。
#   - 打包期离线预装全部依赖进 site-packages，用户端绝不执行 pip。
#   - CE 基础依赖不含 torch（torch 只在可选 [world] extra，Mac 无 GPU 默认不装）。
#     需要 3DGS/SHARP 特性时显式 INSTALL_WORLD=1，体积/风险自负（见 desktop/README）。
#   - 三套构建产物互不复用；便携 Python 不能跨架构。
#
# 大量下载/编译，只在干净的构建机上跑；本机若缺 rust/go 等会明确报错退出。
# =============================================================================
set -euo pipefail

TARGET="${1:?用法: prepare-resources.sh <mac-arm64|mac-x64|win-x64>}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$DESKTOP_DIR/.." && pwd)"
RES="$DESKTOP_DIR/src-tauri/resources"

# ------------------------------------------------------------------ 目标三元组
PY="3.12"                     # 与 pyproject requires-python(>=3.11,<3.13) 对齐；仅小版本，patch 由 API 解析
case "$TARGET" in
  mac-arm64) TRIPLE="aarch64-apple-darwin";  GOOS=darwin; GOARCH=arm64; CFT="Mac-x64"; TAURI_TARGET="aarch64-apple-darwin" ;;
  mac-x64)   TRIPLE="x86_64-apple-darwin";   GOOS=darwin; GOARCH=amd64; CFT="Mac-x64"; TAURI_TARGET="x86_64-apple-darwin" ;;
  win-x64)   TRIPLE="x86_64-pc-windows-msvc"; GOOS=windows; GOARCH=amd64; CFT="Win64";  TAURI_TARGET="x86_64-pc-windows-msvc" ;;
  *) echo "未知目标：$TARGET" >&2; exit 2 ;;
esac
# Chrome-for-Testing 的 mac 变体按 arch 细分
if [[ "$TARGET" == "mac-arm64" ]]; then CFT="Mac-arm64"; fi

EXE=""; [[ "$GOOS" == windows ]] && EXE=".exe"

log() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "缺少命令：$1（见 desktop/README 构建机准备）" >&2; exit 3; }; }

# 只重建生成物子目录；保留随包提交的静态配置（nginx.conf / model-manifest.json / nginx/mime.types）。
rm -rf "$RES/bin" "$RES/python" "$RES/app"
mkdir -p "$RES"/{bin,python,nginx,app/dist}
APP="$RES/app"

# ------------------------------------------------------------- 1) 便携 Python
log "1/6 便携 Python（python-build-standalone ${PY} / ${TRIPLE}）"
need curl
need tar
# 资源名形如 cpython-3.12.15+<date>-<triple>-install_only.tar.gz：含具体 patch 版本，
# 标签是日期会变——不能写死。可用 PBS_URL 直接指定；否则查 GitHub API 自动解析。
# 可选 PBS_RELEASE=<日期标签> 固定某次发布（否则取 latest）。
if [[ -z "${PBS_URL:-}" ]]; then
  if [[ -n "${PBS_RELEASE:-}" ]]; then
    API_JSON="https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/${PBS_RELEASE}"
  else
    API_JSON="https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
  fi
  # 容忍 set -o pipefail：各 grep 无匹配不中断；不用 head（避免 SIGPIPE）；可选带 GITHUB_TOKEN 提高限额。
  PBS_URL="$( { curl -fsSL ${GITHUB_TOKEN:+-H "Authorization: Bearer $GITHUB_TOKEN"} "$API_JSON" || true; } \
    | { grep -oE '"browser_download_url": *"[^"]+"' || true; } \
    | sed -E 's/.*"(https[^"]*)"/\1/' \
    | { grep -E "cpython-${PY}\.[0-9]+\+.*-${TRIPLE}-install_only\.tar\.gz$" || true; } )"
  PBS_URL="${PBS_URL%%$'\n'*}"
  [[ -n "$PBS_URL" ]] || { echo "无法从 GitHub 解析 PBS ${PY}/${TRIPLE} install_only 资源（可能触发 API 限流）；请设 PBS_URL=<直链> 或 PBS_RELEASE=<日期标签>+GITHUB_TOKEN 重试" >&2; exit 4; }
fi
log "下载 $PBS_URL"
curl -fL --retry 3 -o /tmp/pbs.tar.gz "$PBS_URL"
tar -xzf /tmp/pbs.tar.gz -C "$RES/python" --strip-components=1
PYBIN="$RES/python/bin/python${EXE}"
[[ -x "$PYBIN" ]] || { echo "便携 Python 解压后找不到解释器：$PYBIN" >&2; exit 4; }

# --------------------------------------------------- 2) 离线预装 CE 后端依赖
log "2/6 按 uv.lock 冻结预装 CE 后端依赖（无 pip 暴露给用户）"
need uv
# 关键：不能 `uv pip install "."`——那会重新解版本，解出不一致的 pydantic/typing-inspection
# 导致运行期 import 崩。必须按锁安装：uv export --frozen → pip install -r → 本体 --no-deps。
EXTRA_ARGS=()
[[ "${INSTALL_WORLD:-0}" == "1" ]] && EXTRA_ARGS+=(--extra world) && log "启用 [world] extra（含 torch，体积显著增大）"
REQS="/tmp/aipdd-desktop-reqs.txt"
( cd "$ROOT_DIR" && uv export --frozen --no-dev --no-hashes --no-emit-project ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} -o "$REQS" )
uv pip install --python "$PYBIN" --no-cache -r "$REQS"
# 项目本体（novelvideo 入口）单独装；--no-deps 不触发版本重解。
uv pip install --python "$PYBIN" --no-cache --no-deps "$ROOT_DIR"
# 依赖装进 $RES/python/lib/python*/site-packages，随包只读分发。

# ----------------------------------------------------------- 3) 前端 dist
log "3/6 构建 React/Vite 前端 → resources/app/dist"
need node
# 前端以 pnpm 管理（见 frontend/pnpm-lock.yaml + packageManager: pnpm@11）；
# 直接 npm ci 会因 peer 依赖（dramaclaw-spec-render↔lucide-react）冲突失败。
FE_PNPM=""
if command -v pnpm >/dev/null 2>&1; then
  FE_PNPM="pnpm"
elif command -v corepack >/dev/null 2>&1; then
  mkdir -p "$HOME/.local/bin"
  corepack enable pnpm --install-directory "$HOME/.local/bin" >/dev/null 2>&1 || true
  export PATH="$HOME/.local/bin:$PATH"
  command -v pnpm >/dev/null 2>&1 && FE_PNPM="pnpm"
fi
if [[ -n "$FE_PNPM" ]]; then
  ( cd "$ROOT_DIR/frontend" && pnpm install --frozen-lockfile && pnpm build )
else
  log "无 pnpm：回退 npm ci --legacy-peer-deps（容忍 peer 冲突）"
  ( cd "$ROOT_DIR/frontend" && npm ci --legacy-peer-deps && npm run build )
fi
cp -R "$ROOT_DIR/frontend/dist/." "$APP/dist/"

# -------------------------------------------------------- 4) 后端源码副本
log "4/6 拷贝后端源码 → resources/app/src（PYTHONPATH 目标）"
mkdir -p "$APP/src"
cp -R "$ROOT_DIR/src/novelvideo" "$APP/src/novelvideo"
# VectCut（Python 服务，optional）：连同其 requirements 一并放入，运行期同解释器执行。
if [[ -d "$ROOT_DIR/services/vectcut" ]]; then
  mkdir -p "$APP/services"
  cp -R "$ROOT_DIR/services/vectcut" "$APP/services/vectcut"
  ( cd "$ROOT_DIR/services/vectcut" && uv pip install --python "$PYBIN" --no-cache -r requirements.txt ) || \
    log "VectCut 依赖安装失败：将作为 optional 服务在运行期优雅降级"
fi

# ------------------------------------------------------ 5) Go 网关静态编译
log "5/6 编译 newapi 网关（先 bun 构建内嵌 web/dist，再 CGO_ENABLED=0 go build）→ resources/bin/new-api${EXE}"
need go
need bun
GW="$ROOT_DIR/third_party/dramaclaw-gateway"
# 关键：网关源码用 //go:embed web/dist（自带管理后台），不先产出该目录则 go build
# 直接失败（`pattern web/dist: no matching files found`）。与 Dockerfile 一致：先构建 web。
( cd "$GW/web" && bun install --frozen-lockfile && \
  DISABLE_ESLINT_PLUGIN='true' VITE_REACT_APP_VERSION="$(cat "$GW/VERSION" 2>/dev/null || echo dev)" bun run build )
[[ -f "$GW/web/dist/index.html" ]] || { echo "网关 web/dist 未生成：go:embed 会失败" >&2; exit 6; }
# GOEXPERIMENT=nogreenteagc / GOWORK=off 与 Dockerfile 对齐；relaykit 为 replace 指向的本地子模块。
( cd "$GW" && CGO_ENABLED=0 GOOS="$GOOS" GOARCH="$GOARCH" GOEXPERIMENT=nogreenteagc GOWORK=off \
  go build -trimpath -ldflags "-s -w -X 'github.com/QuantumNous/new-api/common.Version=$(cat VERSION 2>/dev/null || echo dev)'" \
  -o "$RES/bin/new-api${EXE}" . )

# ------------------------------------------------ 6) 随附二进制：nginx/ffmpeg/chromium
log "6/6 随附 nginx / ffmpeg / Chromium（官方预编译，注意可重定位性，见 README）"

# nginx：优先用现有静态二进制；否则 macOS 走 Homebrew、Windows 走官方 zip。
if [[ -n "${NGINX_BIN:-}" && -x "${NGINX_BIN%/*}/$NGINX_BIN" ]]; then
  cp "$NGINX_BIN" "$RES/bin/nginx$EXE"
elif [[ "$GOOS" == darwin ]]; then
  need brew
  brew list nginx >/dev/null 2>&1 || brew install nginx
  NGPREFIX="$(brew --prefix nginx)"
  cp "$NGPREFIX/bin/nginx" "$RES/bin/nginx"
  # 可重定位性风险：Homebrew nginx 链接 cellar 里的 pcre/openssl。
  # 若目标机可能缺这些 dylib，需把它们一并 vendor 进 resources/bin/lib 并在签名脚本重签。
  log "提示：nginx 依赖的动态库如非系统自带，请 vendor 到 resources/bin/lib 并加入重签清单。"
else
  NGINX_VER="${NGINX_VER:-1.27.0}"
  curl -fL -o /tmp/nginx.zip "https://nginx.org/download/nginx-${NGINX_VER}.zip"
  unzip -o /tmp/nginx.zip -d /tmp/nginxpkg
  cp "/tmp/nginxpkg/nginx-${NGINX_VER}/nginx.exe" "$RES/bin/nginx.exe"
  cp "/tmp/nginxpkg/nginx-${NGINX_VER}/conf/mime.types" "$RES/nginx/mime.types"
fi
# 确保 mime.types 就位（桌面 nginx.conf include 之）；mac 分支从 brew 或安装树补。
if [[ ! -f "$RES/nginx/mime.types" ]]; then
  for cand in "${NGPREFIX:-}/etc/nginx/mime.types" /opt/homebrew/etc/nginx/mime.types /usr/local/etc/nginx/mime.types; do
    [[ -f "$cand" ]] && { cp "$cand" "$RES/nginx/mime.types"; break; }
  done
fi
[[ -f "$RES/nginx/mime.types" ]] || { echo "缺少 resources/nginx/mime.types（桌面 nginx 必需）" >&2; exit 5; }

# ffmpeg：期望提供一个静态/自包含二进制；否则用 PATH 上的并告警。
if [[ -n "${FFMPEG_BIN:-}" ]]; then
  cp "$FFMPEG_BIN" "$RES/bin/ffmpeg$EXE"
elif command -v ffmpeg >/dev/null 2>&1; then
  cp "$(command -v ffmpeg)" "$RES/bin/ffmpeg$EXE"
  log "警告：从系统 PATH 拷贝的 ffmpeg 可能依赖系统 dylib，跨机分发前请确认其自包含或改提供静态构建。"
else
  log "警告：未提供 ffmpeg。yt-dlp 合流/抽音在运行期会失败；请设 FFMPEG_BIN=<静态ffmpeg> 重跑。"
fi

# Chromium（抖音 CDP 提取用）：Chrome for Testing 对应架构预编译 zip。
# 说明：当前 orchestrator 未将 Chromium 列为受管服务（无 :9222 CDP 服务），且
# missing_required_bins 不校它；如需可置 SKIP_CHROMIUM=1 略过这段大下载。
if [[ "${SKIP_CHROMIUM:-0}" == "1" ]]; then
  log "跳过 Chromium（SKIP_CHROMIUM=1）：抖音 CDP 提取需真实宿主 Chrome，接线见 README §12"
else
CFT_VER="${CFT_VER:-}"
if [[ -z "$CFT_VER" ]]; then
  CFT_VER="$(curl -fsSL "https://googlechromelabs.github.io/chrome-for-testing/LATEST_RELEASE_STABLE" || true)"
fi
if [[ -n "$CFT_VER" ]]; then
  curl -fL -o /tmp/chrome.zip "https://storage.googleapis.com/chrome-for-testing-public/${CFT_VER}/${CFT}/chrome-${CFT}.zip"
  unzip -oq /tmp/chrome.zip -d "$RES/bin/chrome"
  # 运行期后端连 CDP：需由编排器以 --remote-debugging-port 启动该 Chrome，并把它的路径
  # 通过环境变量交给后端。见 desktop/README「Chromium/CDP 接线」TODO。
  log "Chromium 已内置 resources/bin/chrome（CDP 接线见 README）"
else
  log "警告：未能确定 Chrome for Testing 版本；设 CFT_VER=<版本> 重跑以内置 Chromium。"
fi
fi

log "完成。下一步： cd desktop && cargo tauri build --target $TAURI_TARGET --bundles app,dmg"
echo "资源目录：$RES"
