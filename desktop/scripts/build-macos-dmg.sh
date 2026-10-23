#!/usr/bin/env bash
# =============================================================================
# build-macos-dmg.sh —— 在 macOS 构建机上产出未签名 .app（并修复 tauri 对 Chrome 的 deref）。
#   用法： ./build-macos-dmg.sh [arm64|x64]     默认 arm64（Apple Silicon）
#   注：dmg 不在此产出——tauri 的 bundle=["app","dmg"] 会把「未签」的 app 封进 dmg，
#       事后重签外层散件 .app 不会更新 dmg 内那份。可分发的 dmg 统一由
#       mac-sign-notarize.sh 在「签好 App 之后」构建，保证 dmg 内是 Developer ID 签名版。
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

log "构建 .app（target=${TRIPLE}）"
cargo tauri build --target "$TRIPLE" --bundles app

BUNDLE_DIR="$DESKTOP_DIR/src-tauri/target/$TRIPLE/release/bundle"
APP="$BUNDLE_DIR/macos/$(basename "$(find "$BUNDLE_DIR/macos" -maxdepth 1 -name '*.app' -print -quit)")"

# tauri deref 修复：cargo build 拷贝资源时把 resources/bin/chrome 里 versioned framework 的
# Versions/Current 符号链接 deref 成真实目录，导致 codesign --deep 报「bundle format is
# ambiguous」、运行时双版本目录异常。用 pristine resources 以 ditto 覆盖 App 内 Chrome 子树
# 还原符号链接。幂等：Current 已是软链就跳过。
PRISTINE="$RES/bin/chrome/Google Chrome for Testing.app"
INAPP="$APP/Contents/Resources/resources/bin/chrome/Google Chrome for Testing.app"
CUR_LINK="$INAPP/Contents/Frameworks/Google Chrome for Testing Framework.framework/Versions/Current"
if [[ -d "$PRISTINE" && -d "$INAPP" && ! -L "$CUR_LINK" ]]; then
  log "修复 Chrome deref：ditto 以 pristine resources 还原 App 内 Chrome 子树"
  rm -rf "$INAPP"
  ditto "$PRISTINE" "$INAPP"
fi

log "产物目录：$BUNDLE_DIR"
find "$BUNDLE_DIR/macos" -maxdepth 1 -name '*.app' -print

cat <<'EOF'
下一步（二选一）：
  A. 对外分发（签名 → 由已签 App 打 dmg → 公证，dmg 由签名脚本构建）：
       NOTARIZE=1 NOTARY_PROFILE=<凭据名> ./mac-sign-notarize.sh <path/to/*.app> [输出.dmg]
       （公证开关 NOTARIZE 默认关闭，不加只本地签名、不向 Apple 提交；结尾自动跑出厂终检）
  B. 仅本机自测：直接 open 本脚本产出的 .app（未签名，仅本机能跑，勿分发）。
EOF
