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
[[ -d "$RES/python" && -f "$RES/bin/nginx" && -f "$RES/bin/localapi" ]] || {
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
下一步（三条链各自独立，产物目录互不覆盖）：

  A. 对外直分发（Developer ID → dmg → 公证）：
       NOTARIZE=1 NOTARY_PROFILE=<凭据名> ./mac-sign-notarize.sh <path/to/*.app> [输出.dmg]
       （公证开关 NOTARIZE 默认关闭，不加只本地签名、不向 Apple 提交；结尾自动跑出厂终检）
       证书对：Developer ID Application + Developer ID Installer
       授权清单：src-tauri/entitlements.plist（含 disable-library-validation，非沙盒）
       产物目录：src-tauri/target/<triple>/release/bundle/{macos,dmg}/

  B. Mac App Store 上架（3rd Party Mac Developer → pkg → Transporter）：
       ./build-macos-mas-app.sh [arm64|x64|universal]   # 拷贝到 bundle/macos-mas/ 并剥嵌套 .app
                                                        #   universal 需先跑 build-macos-universal.sh 合出双架构树
       PROFILE=<.provisionprofile> ./macos-pkg-appstore.sh <bundle/macos-mas/*.app>
       ./verify-release-pkg.sh <输出.pkg>
       证书对：3rd Party Mac Developer Application + 3rd Party Mac Developer Installer
                 （Apple 新命名 Apple Distribution / Apple Installer 也支持，用 SIGN_APP_IDENTITY 覆盖）
       授权清单：src-tauri/entitlements.mas.plist（开 app-sandbox，禁沙盒冲突项）
       产物目录：src-tauri/target/<triple>/release/bundle/macos-mas/（与 A 分道，A 的产物不受影响）

  C. 仅本机自测：直接 open 本脚本产出的 .app（未签名，仅本机能跑，勿分发）。

上架阻塞项（详见 docs/zh/guides/mac-app-store-release.md）：
  · 便携 Python 子树全部 .so 需本 Team 重签（沙盒 library validation）
  · 数据目录需容器化到 ~/Library/Containers/work.aipdd.dramaclaw/
  · Chrome for Testing 内嵌件在 MAS 版会被自动剥除，依赖它的能力（换脸浏览器自动化）
    在 MAS 版会失效——上架前须与产品对齐功能面收窄范围
EOF
