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
# CFT = Chrome-for-Testing 的**平台目录名**，官方只认小写（win64 / mac-x64 / mac-arm64）；
# 写成 Win64 会让 storage.googleapis.com 直接 404。
case "$TARGET" in
  mac-arm64) TRIPLE="aarch64-apple-darwin";  GOOS=darwin; GOARCH=arm64; CFT="mac-arm64"; TAURI_TARGET="aarch64-apple-darwin" ;;
  mac-x64)   TRIPLE="x86_64-apple-darwin";   GOOS=darwin; GOARCH=amd64; CFT="mac-x64";   TAURI_TARGET="x86_64-apple-darwin" ;;
  win-x64)   TRIPLE="x86_64-pc-windows-msvc"; GOOS=windows; GOARCH=amd64; CFT="win64";    TAURI_TARGET="x86_64-pc-windows-msvc" ;;
  *) echo "未知目标：$TARGET" >&2; exit 2 ;;
esac

EXE=""; [[ "$GOOS" == windows ]] && EXE=".exe"

log() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "缺少命令：$1（见 desktop/README 构建机准备）" >&2; exit 3; }; }
# win-x64 的 nginx/ffmpeg/Chromium 都以 zip 分发（Git Bash 自带 unzip；MSYS 缺失时提前报错）。
if [[ "$GOOS" == windows ]]; then need unzip; fi

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
  # 注意匹配 `+` 的两种写法：API 现在把资产名里的 `+` 百分号编码成 `%2B`（早期是字面 `+`），
  # 只写 `\+` 会解析不到任何 URL（曾表现为「疑似限流」的假报错）。
  PBS_URL="$( { curl -fsSL --retry 3 ${GITHUB_TOKEN:+-H "Authorization: Bearer $GITHUB_TOKEN"} "$API_JSON" || true; } \
    | { grep -oE '"browser_download_url": *"[^"]+"' || true; } \
    | sed -E 's/.*"(https[^"]*)"/\1/' \
    | { grep -E "cpython-${PY}\.[0-9]+(%2B|\+).*-${TRIPLE}-install_only\.tar\.gz$" || true; } )"
  PBS_URL="${PBS_URL%%$'\n'*}"
  [[ -n "$PBS_URL" ]] || { echo "无法从 GitHub 解析 PBS ${PY}/${TRIPLE} install_only 资源（可能触发 API 限流）；请设 PBS_URL=<直链> 或 PBS_RELEASE=<日期标签>+GITHUB_TOKEN 重试" >&2; exit 4; }
fi
log "下载 $PBS_URL"
curl -fL --retry 3 -o /tmp/pbs.tar.gz "$PBS_URL"
tar -xzf /tmp/pbs.tar.gz -C "$RES/python" --strip-components=1
# 解释器位置按平台不同：install_only 包在 Unix 是 python/bin/python3，
# Windows 没有 bin/ 这一层，是 python/python.exe + python/Lib/site-packages（env.rs::python() 同源）。
if [[ "$GOOS" == windows ]]; then
  PYBIN="$RES/python/python.exe"
else
  PYBIN="$RES/python/bin/python3"
fi
[[ -f "$PYBIN" ]] || { echo "便携 Python 解压后找不到解释器：$PYBIN" >&2; exit 4; }
"$PYBIN" -V || { echo "便携 Python 无法执行：$PYBIN" >&2; exit 4; }

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
# [aipdd fork delta] HyperFrames 渲染服务（引擎即服务）源：Dockerfile/package.json/README。
# 后端渲染客户端（novelvideo/renderer）已随上面的 src/novelvideo 一并打入；这里只额外
# 落地服务侧清单（固定 @hyperframes/producer 版本），供本地 producer 子进程与体积审计溯源。
if [[ -d "$ROOT_DIR/services/hyperframes-renderer" ]]; then
  mkdir -p "$APP/services"
  cp -R "$ROOT_DIR/services/hyperframes-renderer" "$APP/services/hyperframes-renderer"
fi

# ------------------------------------------------------ 5) Go 网关静态编译
if [[ -n "${NEWAPI_BIN:-}" ]]; then
  # 允许用外部已编译好的 win-x64/darwin 网关二进制出包（构建机缺 Go/bun 时的逃生门，与 NGINX_BIN 同思路）。
  [[ -f "$NEWAPI_BIN" ]] || { echo "NEWAPI_BIN 指向的文件不存在：$NEWAPI_BIN" >&2; exit 6; }
  cp "$NEWAPI_BIN" "$RES/bin/new-api${EXE}"
  log "5/6 使用外部网关二进制 $NEWAPI_BIN → resources/bin/new-api${EXE}（跳过 go/bun 构建）"
else
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
# CGO_ENABLED=0 → 纯 Go 交叉编译，Windows 构建机无需 C 工具链。
( cd "$GW" && CGO_ENABLED=0 GOOS="$GOOS" GOARCH="$GOARCH" GOEXPERIMENT=nogreenteagc GOWORK=off \
  go build -trimpath -ldflags "-s -w -X 'github.com/QuantumNous/new-api/common.Version=$(cat VERSION 2>/dev/null || echo dev)'" \
  -o "$RES/bin/new-api${EXE}" . )
fi

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

# ffmpeg + ffprobe：后端 63 处调用 ffprobe（探测时长/分辨率），两者必须同时随包。
# 首选显式提供 FFMPEG_BIN（其同目录下的 ffprobe 一并取）；Windows 无现成则下载 LGPL 静态构建。
copy_ffprobe_sibling() {
  local src_dir="$1"
  local cand="$src_dir/ffprobe$EXE"
  if [[ -f "$cand" ]]; then
    cp "$cand" "$RES/bin/ffprobe$EXE"
    return 0
  fi
  return 1
}
if [[ -n "${FFMPEG_BIN:-}" ]]; then
  cp "$FFMPEG_BIN" "$RES/bin/ffmpeg$EXE"
  copy_ffprobe_sibling "$(dirname "$FFMPEG_BIN")" \
    || log "警告：$FFMPEG_BIN 同目录未找到 ffprobe$EXE，请另设含 ffprobe 的构建。"
elif [[ "$GOOS" == windows ]]; then
  # BtbN/FFmpeg-Builds 的 win64 **LGPL** 变体（单文件静态，无外部 DLL）；
  # 选 LGPL 而非默认 GPL，避免 x264 等 GPL 组件带来的额外源码分发义务（见 licenses/README.md）。
  FFB_URL="${FFB_URL:-https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-lgpl.zip}"
  log "下载 FFmpeg（win64 LGPL）：$FFB_URL"
  curl -fL --retry 3 -o /tmp/ffmpeg.zip "$FFB_URL"
  unzip -oq /tmp/ffmpeg.zip -d /tmp/ffpkg
  FFROOT="$(find /tmp/ffpkg -maxdepth 1 -type d -name 'ffmpeg-*' | head -1)"
  [[ -f "$FFROOT/bin/ffmpeg.exe" ]] || { echo "FFmpeg 包内缺 bin/ffmpeg.exe：$FFROOT" >&2; exit 8; }
  cp "$FFROOT/bin/ffmpeg.exe"  "$RES/bin/ffmpeg.exe"
  cp "$FFROOT/bin/ffprobe.exe" "$RES/bin/ffprobe.exe"
  [[ -f "$FFROOT/LICENSE.txt" ]] && cp "$FFROOT/LICENSE.txt" "$RES/bin/ffmpeg-LICENSE.txt"
elif command -v ffmpeg >/dev/null 2>&1; then
  cp "$(command -v ffmpeg)" "$RES/bin/ffmpeg$EXE"
  copy_ffprobe_sibling "$(dirname "$(command -v ffmpeg)")" \
    || log "警告：系统 ffmpeg 同目录未找到 ffprobe，后端媒体探测会在运行期失败。"
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
  # 包内顶层是 chrome-<平台>/ 一层，剥掉它让 exe 直接落在 bin/chrome/ 下
  # （Windows: bin/chrome/chrome.exe；mac: bin/chrome/Google Chrome for Testing.app）。
  # 直接解到 bin/ 再同级改名：Git Bash 自带 unzip 6.00 没有 --strip-components，
  # 而「解到临时目录 + cp -R 跨盘搬运」在这套 MSYS cp 下要十几分钟（Chromium 约 700 文件）。
  rm -rf "$RES/bin/chrome" "$RES/bin/chrome-${CFT}"
  unzip -oq /tmp/chrome.zip -d "$RES/bin"
  [[ -d "$RES/bin/chrome-${CFT}" ]] || { echo "Chromium 包内未找到 chrome-${CFT}/：$(ls "$RES/bin")" >&2; exit 8; }
  mv "$RES/bin/chrome-${CFT}" "$RES/bin/chrome"
  # 运行期后端连 CDP：需由编排器以 --remote-debugging-port 启动该 Chrome，并把它的路径
  # 通过环境变量交给后端。见 desktop/README「Chromium/CDP 接线」TODO。
  log "Chromium $CFT_VER 已内置 resources/bin/chrome（CDP 接线见 README）"
else
  log "警告：未能确定 Chrome for Testing 版本；设 CFT_VER=<版本> 重跑以内置 Chromium。"
fi
fi

# ---------------------------------------------------------------------------
# HyperFrames 本地 producer（引擎即服务，讲解视频 / 渠道短视频的渲染底座）。
# 与 Docker 侧同源：构建期固定 @hyperframes/producer 版本、运行期零外联，api 只按
# DRAMACLAW_RENDERER_URL 连它（服务地址只认配置，绝不猜本机，见 novelvideo/renderer/client.py）。
#
# 体积很大（便携 Node 运行时 + producer 依赖 + puppeteer 的 Chromium），故默认**不装**，
# 显式 INSTALL_RENDERER=1 才打。未装时后端竖屏/方形讲解与渠道短视频会因
# renderer_configured()==False 直接报错（横屏 MG 仍可回退 Pillow），桌面侧按需在 README 接线。
if [[ "${INSTALL_RENDERER:-0}" == "1" ]]; then
  log "附加 本地 HyperFrames producer（便携 Node + @hyperframes/producer，体积显著增大）"
  need node
  need npm
  RENDERER_SRC="$ROOT_DIR/services/hyperframes-renderer"
  [[ -d "$RENDERER_SRC" ]] || { echo "缺少渲染服务源：$RENDERER_SRC" >&2; exit 7; }
  PRODUCER_DIR="$APP/services/hyperframes-renderer"
  mkdir -p "$PRODUCER_DIR"
  # 只带 package.json 里的固定版本依赖进资源目录，不拷 Dockerfile（桌面走子进程不起镜像）。
  cp "$RENDERER_SRC/package.json" "$RENDERER_SRC/README.md" "$PRODUCER_DIR/"
  # 用打包机现成 node/npm 预装依赖（离线锁定同 Docker）；postinstall 会拉 puppeteer 的 Chrome。
  ( cd "$PRODUCER_DIR" && npm install --omit=dev --no-audit --no-fund ) || \
    { echo "@hyperframes/producer 依赖安装失败（检查打包机 node/npm 与网络镜像）" >&2; exit 7; }
  # 便携 Node 运行时：桌面运行期不能假设用户装了 node，需随包一份同架构 node 二进制。
  # 默认复用打包机当前 node 主版本，取官方 install-only 归档；NODE_URL 可直链覆盖。
  NODE_VER="${NODE_VER:-$(node -v | sed -E 's/^v//')}"
  case "$TARGET" in
    mac-arm64) NODE_PLATFORM="darwin-arm64" ;;
    mac-x64)   NODE_PLATFORM="darwin-x64" ;;
    win-x64)   NODE_PLATFORM="win-x64" ;;
  esac
  NODE_BASE="https://nodejs.org/dist/v${NODE_VER}"
  if [[ "$GOOS" == windows ]]; then
    NODE_ASSET="node-v${NODE_VER}-${NODE_PLATFORM}.zip"; NODE_EXT=zip
  else
    NODE_ASSET="node-v${NODE_VER}-${NODE_PLATFORM}.tar.gz"; NODE_EXT=targz
  fi
  NODE_URL="${NODE_URL:-$NODE_BASE/$NODE_ASSET}"
  log "下载便携 Node：$NODE_URL"
  if [[ "$NODE_EXT" == zip ]]; then
    curl -fL --retry 3 -o /tmp/node.zip "$NODE_URL"
    unzip -oq /tmp/node.zip -d /tmp/nodepkg
    mkdir -p "$RES/bin/node"
    cp -R "/tmp/nodepkg/node-v${NODE_VER}-${NODE_PLATFORM}/." "$RES/bin/node/"
  else
    curl -fL --retry 3 -o /tmp/node.tar.gz "$NODE_URL"
    mkdir -p "$RES/bin/node"
    tar -xzf /tmp/node.tar.gz -C "$RES/bin/node" --strip-components=1
  fi
  # 运行期：编排器（Rust 侧）以 `node <PRODUCER_DIR>/node_modules/@hyperframes/producer/dist/public-server.js`
  # 拉起子进程（端口 PRODUCER_PORT，默认 9847），并把 http://127.0.0.1:<port> 注入
  # 后端的 DRAMACLAW_RENDERER_URL。该接线属 desktop/README「本地渲染」TODO，本脚本只备齐物料。
  log "本地 producer 已内置：node → resources/bin/node，producer → resources/app/services/hyperframes-renderer"
else
  log "跳过本地 HyperFrames producer（INSTALL_RENDERER=1 才打包；体积大）"
fi

if [[ "$GOOS" == windows ]]; then
  log "完成。下一步： cd desktop && cargo tauri build --target $TAURI_TARGET --bundles nsis"
else
  log "完成。下一步： cd desktop && cargo tauri build --target $TAURI_TARGET --bundles app,dmg"
fi
echo "资源目录：$RES"
