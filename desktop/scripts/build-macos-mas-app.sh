#!/usr/bin/env bash
# =============================================================================
# build-macos-mas-app.sh —— Mac App Store 上架用 .app 预备脚本（Track B 入口）
#
#   和 build-macos-dmg.sh（直分发）【共用】同一份 cargo tauri build 输出，之后
#   拷贝到独立目录 bundle/macos-mas/ 做 MAS 化裁剪，两条链互不影响。
#   ⚠️ 千万别就地裁剪 bundle/macos/ 那份——那是 dmg 链的输入，剥了 Chrome
#      之后直分发版换脸自动化会失效。
#
#   用法： ./build-macos-mas-app.sh [arm64|x64|universal]     默认 arm64
#   前置： 与 build-macos-dmg.sh 一致：
#         1) scripts/prepare-resources.sh <mac-arm64|mac-x64>
#         2) rustup + cargo install tauri-cli --version "^2" + Xcode CLT
#         universal 不走以上两步：源 .app 由 scripts/build-macos-universal.sh 合好，
#         本脚本只负责裁剪（universal 下自动等效 NO_BUILD=1，不重跑 cargo）。
#
#   环境变量：
#     MAS_STRIP_EXTRA   额外要剥掉的路径（相对 App 根），空格分隔；
#                       默认已内置剥掉 Chrome for Testing。
#     MAS_KEEP_NESTED   1 时不剥嵌套 .app（仅供本地实验，产物不能上传 ASC）。
#     NO_BUILD          1 时跳过 cargo tauri build，仅对已有 .app 做拷贝+裁剪
#                       （给反复调 pkg 脚本用，省一次 Rust 编译）。
#
#   产物：desktop/src-tauri/target/<triple>/release/bundle/macos-mas/AIPDD DramaClaw.app
#   架构要求（ITMS-91167）：ASC 拒收无 arm64 切片的构建，本脚本的产物必须用
#         arm64（或后续合并出的 universal）进 pkg 链；x64 仅适合本地调试，
#         macos-pkg-appstore.sh 会硬拦纯 x86_64 包（REQUIRE_ARM64=0 才放行）。
#   下一步：PROFILE=<.provisionprofile 路径> ./macos-pkg-appstore.sh <该 .app>
# =============================================================================
set -euo pipefail

ARCH="${1:-arm64}"
case "$ARCH" in
  arm64) TRIPLE="aarch64-apple-darwin";  RES_TARGET="mac-arm64" ;;
  x64)   TRIPLE="x86_64-apple-darwin";  RES_TARGET="mac-x64" ;;
  universal) TRIPLE="universal2-apple-darwin"; RES_TARGET="" ;;
  *) echo "架构只能是 arm64、x64 或 universal" >&2; exit 2 ;;
esac
# universal 的输入是 build-macos-universal.sh 合好的产物，本脚本不能再跑 cargo：
# 一跑就会退回单架构构建（且 resources 已被 prepare-resources 清成单一架构）。
if [[ "$ARCH" == "universal" ]]; then
  export NO_BUILD=1
fi
if [[ "$ARCH" == "x64" ]]; then
  echo "⚠ x64 产物无 arm64 切片，上传 ASC 必报 ITMS-91167：仅适合本机调试，"
  echo "  不能进 pkg 上传链（macos-pkg-appstore.sh 默认硬拦）。上架请用 arm64。"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RES="$DESKTOP_DIR/src-tauri/resources"
BUNDLE_DIR="$DESKTOP_DIR/src-tauri/target/$TRIPLE/release/bundle"
SRC_APP_DIR="$BUNDLE_DIR/macos"
MAS_APP_DIR="$BUNDLE_DIR/macos-mas"

log() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }

# ------------------------------------------------ 0) 构建（NO_BUILD=1 可跳过）
if [[ "${NO_BUILD:-0}" != "1" ]]; then
  command -v cargo >/dev/null || { echo "缺少 cargo（rustup 未装？）" >&2; exit 3; }
  cargo tauri --version >/dev/null 2>&1 || { echo "缺少 tauri-cli：cargo install tauri-cli --version \"^2\"" >&2; exit 3; }
  [[ -d "$RES/python" && -f "$RES/bin/nginx" && -f "$RES/bin/localapi" ]] || {
    echo "resources 未准备，请先： scripts/prepare-resources.sh $RES_TARGET" >&2; exit 4; }

  cd "$DESKTOP_DIR"
  log "0/3 cargo tauri build --target $TRIPLE --bundles app（复用直分发链的输入）"
  cargo tauri build --target "$TRIPLE" --bundles app
else
  log "0/3 跳过 cargo tauri build（NO_BUILD=1，直接用已产出的 $TRIPLE 产物）"
fi

# ------------------------------------------------ 1) 定位源 .app，ditto 拷贝到 macos-mas/
[[ -d "$SRC_APP_DIR" ]] || { echo "源 App 目录不存在：${SRC_APP_DIR}（${ARCH} 链需要先产出 .app：单架构先跑一次不带 NO_BUILD 的构建，universal 先跑 build-macos-universal.sh）" >&2; exit 5; }
SRC_APP="$(find "$SRC_APP_DIR" -maxdepth 1 -type d -name '*.app' -print -quit)"
[[ -n "$SRC_APP" ]] || { echo "未在 $SRC_APP_DIR 下找到 .app" >&2; exit 5; }

mkdir -p "$MAS_APP_DIR"
MAS_APP="$MAS_APP_DIR/$(basename "$SRC_APP")"
# 反复构建：先清旧的 MAS 版，ditto 保留符号链接与权限，避免 tauri deref 那类坑重现。
if [[ -e "$MAS_APP" ]]; then
  log "1/3 清理旧的 MAS 产物：$MAS_APP"
  rm -rf "$MAS_APP"
fi
log "1/3 ditto 复制 $SRC_APP → $MAS_APP"
ditto "$SRC_APP" "$MAS_APP"

# ditto 会保留扩展属性：下载/解压出来的资源（便携 Python 的 crypto.py 实测中招）
# 携带的 com.apple.quarantine 会跟着进 MAS 包，ASC 校验报 ITMS-91109 直接拒。
# 在这里（裁剪前、签名前）就清掉，macos-pkg-appstore.sh 还会再带一道 fail-safe。
log "1b) 清理 App 内隔离属性（ITMS-91109）"
xattr -cr "$MAS_APP" 2>/dev/null || true

# ------------------------------------------------ 2) MAS 化裁剪：剥嵌套 .app / 已知禁用子树
if [[ "${MAS_KEEP_NESTED:-0}" == "1" ]]; then
  log "2/3 跳过裁剪（MAS_KEEP_NESTED=1）——产物不能上传 ASC，仅供本地实验"
else
  log "2/3 裁剪 MAS 禁用子树（Chrome for Testing + 所有嵌套 .app）"

  # 2a) Chrome for Testing 整棵剥掉。App Store 明确拒绝 bundle 内嵌另一个 .app（除官方
  #     允许的 Helper.app / XPC Service 模板），且 Chrome 的 JIT / unsigned-memory 需求
  #     与沙盒正面冲突。MAS 版的浏览器能力回落到系统 WKWebView（Tauri 本来就走 WKWebView）。
  rm -rf "$MAS_APP/Contents/Resources/resources/bin/chrome"

  # 2b) 兜底扫：任何 Resources 下没被上面覆盖到的嵌套 .app 一律删（含 .framework 里的
  #     Versions/*/Resources/*.app 之类结构）。宁可错删也不留被拒的风险点。
  find "$MAS_APP/Contents" -mindepth 2 -type d -name '*.app' -prune -exec rm -rf {} + 2>/dev/null || true

  # 2c) 用户可传的额外裁剪清单（例如未来剥 docker 子树）。相对 App 根，空格分隔。
  if [[ -n "${MAS_STRIP_EXTRA:-}" ]]; then
    for rel in $MAS_STRIP_EXTRA; do
      target="$MAS_APP/$rel"
      [[ -e "$target" ]] && { echo "  额外裁剪：$rel"; rm -rf "$target"; }
    done
  fi

  # 2d) 校验：确认没漏网。macos-pkg-appstore.sh 会再校验一次，这里早点报错省一次签全量。
  LEFT="$(find "$MAS_APP/Contents" -mindepth 2 -type d -name '*.app' 2>/dev/null || true)"
  [[ -z "$LEFT" ]] || { echo "裁剪后仍有嵌套 .app：\n$LEFT" >&2; exit 6; }
fi

# ------------------------------------------------ 3) 汇总
log "3/3 MAS 版 .app 就绪"
cat <<EOF
  MAS App 路径：$MAS_APP
  源直分发 App：${SRC_APP}（未动，dmg 链可继续用）

下一步：
  # 首次上架前先在 Apple Developer 后台建 Mac App Store 类型的 Provisioning Profile，
  # Bundle ID 与 tauri.conf.json 里的 identifier 一致（work.aipdd.dramaclaw），
  # 关联证书用「3rd Party Mac Developer Application: ...」（新命名：Apple Distribution）。
  PROFILE=<你的 xxx.provisionprofile 路径> \\
    ./scripts/macos-pkg-appstore.sh "$MAS_APP"

  反复调 pkg / entitlements 时可跳过 cargo 编译：
    NO_BUILD=1 ./scripts/macos-pkg-appstore.sh "$MAS_APP"

上架前的架构阻塞（Phase 2~4）：
  · 便携 Python 子树里 .so 需本 Team 全量重签（沙盒 library validation）；
  · 数据目录需容器化到 ~/Library/Containers/work.aipdd.dramaclaw/；
  · Chrome 换脸等依赖内嵌浏览器能力已剥离，MAS 版功能面收窄（可接受）。
  · 隔离属性（com.apple.quarantine）已在 1b) 清过一次，pkg 脚本签前还会再清并复核
    （ITMS-91109：任何一个文件带标记，整包驳回）。
EOF
