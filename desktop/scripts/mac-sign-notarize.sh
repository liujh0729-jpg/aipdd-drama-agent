#!/usr/bin/env bash
# =============================================================================
# mac-sign-notarize.sh —— 对外分发前：遍历 App 内所有内嵌可执行/动态库批量 codesign，
#   再对 .app 施加硬化运行时 + 授权清单签名，随后 notarytool 公证并 staple 票据。
#
#   用法：
#     ./mac-sign-notarize.sh <path/to/AIPDD DramaClaw.app>        # 签 App 并（若给 dmg）公证
#     ./mac-sign-notarize.sh <path/to/*.app> <path/to/*.dmg>      # 先签 App，再公证 dmg
#
#   环境变量：
#     SIGN_IDENTITY   默认 "Developer ID Application"（需已在钥匙串导入证书）
#     ENTITLEMENTS    默认 src-tauri/entitlements.plist（含 disable-library-validation 等，
#                     便携 Python 才能 dlopen 第三方 .so/.dylib）
#     公证凭据（二选一）：
#       NOTARY_PROFILE  已用 `xcrun notarytool store-credentials` 存好的凭据名
#       或 APPLE_ID + APPLE_APP_PWD + TEAM_ID
#   ⚠️ 密钥只从环境/钥匙串读取，绝不写进仓库或日志。
# =============================================================================
set -euo pipefail

APP="${1:?用法: mac-sign-notarize.sh <App.app> [dmg]}"
DMG="${2:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENTITLEMENTS="${ENTITLEMENTS:-$SCRIPT_DIR/../src-tauri/entitlements.plist}"
SIGN_IDENTITY="${SIGN_IDENTITY:-Developer ID Application}"

log() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
is_macho() { file -b "$1" 2>/dev/null | grep -q 'Mach-O'; }

[[ -d "$APP" ]] || { echo "App 不存在：$APP" >&2; exit 2; }
[[ -f "$ENTITLEMENTS" ]] || { echo "缺少授权清单：$ENTITLEMENTS" >&2; exit 2; }

# ------------------------------------------------ 1) 由内向外签所有嵌套 Mach-O
log "1/3 遍历并签名 App 内所有可执行文件 / .dylib / .so"
# 先签依赖库（.dylib/.so），再签可执行文件；App 本体在下一步最后签。
codesign_one() {
  codesign --force --options runtime --timestamp \
           --entitlements "$ENTITLEMENTS" \
           --sign "$SIGN_IDENTITY" \
           "$1"
}
# 1a) 动态库（含便携 Python 的 .so 扩展、framework 内 dylib）
while IFS= read -r -d '' f; do
  is_macho "$f" || continue
  codesign_one "$f"
done < <(find "$APP" -type f \( -name '*.dylib' -o -name '*.so' -o -name '*.so.*' \) -print0)
# 1b) 可执行文件（MacOS 主程序、bin/ 下 nginx/ffmpeg/new-api、Chrome 等）
while IFS= read -r -d '' f; do
  is_macho "$f" || continue
  codesign_one "$f"
done < <(find "$APP" -type f -perm -u+x ! -name '*.dylib' ! -name '*.so' ! -name '*.so.*' -print0)

# ------------------------------------------------ 2) 签 App 本体
log "2/3 对 App 本体签名（硬化运行时 + 授权清单）"
codesign --force --options runtime --timestamp \
         --entitlements "$ENTITLEMENTS" \
         --sign "$SIGN_IDENTITY" \
         --preserve-metadata=entitlements,requirements,flags,runtime \
         "$APP"

codesign --verify --deep --strict --verbose=2 "$APP"

# ------------------------------------------------ 3) 公证 + staple
TARGET_FOR_NOTARY="$APP"
if [[ -n "$DMG" ]]; then TARGET_FOR_NOTARY="$DMG"; fi

# 组装 notarytool 认证参数
AUTH=()
if [[ -n "${NOTARY_PROFILE:-}" ]]; then
  AUTH=( -keychain-profile "$NOTARY_PROFILE" )
elif [[ -n "${APPLE_ID:-}" && -n "${APPLE_APP_PWD:-}" && -n "${TEAM_ID:-}" ]]; then
  AUTH=( -apple-id "$APPLE_ID" -password "$APPLE_APP_PWD" -team-id "$TEAM_ID" )
else
  log "未配置公证凭据（NOTARY_PROFILE 或 APPLE_ID/APPLE_APP_PWD/TEAM_ID），跳过公证。"
  log "本机自测可用；对外分发必须先公证，否则 Gatekeeper 会拦截。"
  exit 0
fi

log "3/3 notarytool 提交公证：$TARGET_FOR_NOTARY"
# .app 不能直接公证，需 zip；.dmg 可直接公证。
if [[ "$TARGET_FOR_NOTARY" == *.app ]]; then
  ZIP="${APP%.app}-notary.zip"
  ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
  SUBMIT="$ZIP"
else
  SUBMIT="$TARGET_FOR_NOTARY"
fi

xcrun notarytool submit "$SUBMIT" "${AUTH[@]}" --wait

xcrun stapler staple "$TARGET_FOR_NOTARY"
[[ "$TARGET_FOR_NOTARY" != "$APP" ]] && xcrun stapler staple "$APP"
xcrun stapler validate "$TARGET_FOR_NOTARY" || true
spctl --assess --type execute --verbose "$APP" || true

log "完成：签名 + 公证 + staple 结束。"
[[ "${KEEP_NOTARY_ZIP:-0}" == "1" ]] || rm -f "${APP%.app}-notary.zip" 2>/dev/null || true
