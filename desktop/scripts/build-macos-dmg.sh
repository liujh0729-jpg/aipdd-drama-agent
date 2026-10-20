#!/usr/bin/env bash
# =============================================================================
# build-macos-dmg.sh —— 在 macOS 构建机上产出 .app + .dmg（带 /Applications 替身拖拽安装）。
#   用法： ./build-macos-dmg.sh [arm64|x64]     默认 arm64（Apple Silicon）
#   前置： 1) 已跑过 prepare-resources.sh <mac-arm64|mac-x64>（resources 就位）
#         2) 已装 rustup + `cargo install tauri-cli --version "^2"` + Xcode Command Line Tools
#   开发期默认 ad-hoc 本地构建（不签名，仅本机可跑）；对外分发请再跑 mac-sign-notarize.sh。
# =============================================================================
set -euo pipefail

ARCH="${1:-arm64}"
case "$ARCH" in
  arm64) TRIPLE="aarch64-apple-darwin";  RES_TARGET="mac-arm64" ;;
  x64)   TRIPLE="x86_64-apple-darwin";  RES_TARGET="mac-x64" ;;
  *) echo "架构只能是 arm64 或 x64" >&2; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RES="$DESKTOP_DIR/src-tauri/resources"

command -v cargo >/dev/null || { echo "缺少 cargo（rustup 未装？）" >&2; exit 3; }
cargo tauri --version >/dev/null 2>&1 || { echo "缺少 tauri-cli：cargo install tauri-cli --version \"^2\"" >&2; exit 3; }
[[ -d "$RES/python" && -f "$RES/bin/nginx" && -f "$RES/bin/new-api" ]] || {
  echo "resources 未准备，请先： scripts/prepare-resources.sh $RES_TARGET" >&2; exit 4; }

cd "$DESKTOP_DIR"

# 未设签名身份时，Tauri 走本地/ad-hoc 构建（开发可用，不可对外分发）。
log() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }

log "构建 .app + .dmg（target=${TRIPLE}）"
cargo tauri build --target "$TRIPLE" --bundles app,dmg

BUNDLE_DIR="$DESKTOP_DIR/src-tauri/target/$TRIPLE/release/bundle"
log "产物目录：$BUNDLE_DIR"
find "$BUNDLE_DIR" -maxdepth 2 \( -name '*.app' -o -name '*.dmg' \) -print

cat <<'EOF'

下一步（对外分发前，二选一）：
  A. 已有 Apple Developer ID：  NOTARIZE=1 ./mac-sign-notarize.sh <path/to/*.app> <path/to/*.dmg>
     （公证开关 NOTARIZE 默认关闭，不加只会本地签名、不向 Apple 提交；结尾会自动跑出厂终检）
  B. 仅本机自测：直接 open 生成的 .dmg，拖入 Applications。
已公证过的 dmg 无需重复公证（公证记录绑 cdhash、永久有效）；只要产物字节变了才需重新提交。
随时复核存量产物： ./verify-release-dmg.sh <path/to/*.dmg> ...
EOF
