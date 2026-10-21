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
#     NOTARIZE        公证开关：默认 0 仅本地签名；NOTARIZE=1 才向 Apple 提交公证
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
# ⚠️ 公证开关：默认关闭，跑脚本只做本地签名，不会向 Apple 提交公证（防止误触发历史堆积）。
#    需要对外分发包时显式开启：NOTARIZE=1 ./mac-sign-notarize.sh <App.app> [dmg]
NOTARIZE="${NOTARIZE:-0}"

log() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
is_macho() { file -b "$1" 2>/dev/null | grep -q 'Mach-O'; }

[[ -d "$APP" ]] || { echo "App 不存在：$APP" >&2; exit 2; }
[[ -f "$ENTITLEMENTS" ]] || { echo "缺少授权清单：$ENTITLEMENTS" >&2; exit 2; }
# 签名身份快速失败：缺证书就在最开始说清楚，而不是签第 200 个文件时报一句难懂的 codesign 错。
security find-identity -v -p codesigning | grep -qF -- "$SIGN_IDENTITY" \
  || { echo "钥匙串中没有可用的签名身份：${SIGN_IDENTITY}（请先导入 Developer ID 证书，或用 SIGN_IDENTITY 覆盖）" >&2; exit 3; }

# ------------------------------------------------ 1) 由内向外签所有嵌套 Mach-O
log "1/3 遍历并签名 App 内所有可执行文件 / .dylib / .so"
# 先签依赖库（.dylib/.so），再签可执行文件；App 本体在下一步最后签。
# 批量并行签名：便携 Python 子树有数百个 Mach-O，串行 codesign 是出包耗时大头 → 按核数并行。
# 用法：find ... -print0 | sign_files_parallel（stdin 收 NUL 分隔清单，非 Mach-O 自动跳过）。
NPROC="$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"
sign_files_parallel() {
  xargs -0 -P "$NPROC" -n1 /bin/bash -c '
    file -b "$2" 2>/dev/null | grep -q "Mach-O" || exit 0
    codesign --force --options runtime --timestamp --entitlements "$0" --sign "$1" "$2"
  ' "$ENTITLEMENTS" "$SIGN_IDENTITY"
}
# 1a) 动态库（含便携 Python 的 .so 扩展、framework 内 dylib）
#     Chrome for Testing 子树单独在下面 1c) 用 --deep 一次性重签——这里从循环里排除以免与深签冲突。
CHROME_EXCL=(-not -path '*/Google Chrome for Testing.app/*')
find "$APP" -type f \( -name '*.dylib' -o -name '*.so' -o -name '*.so.*' \) "${CHROME_EXCL[@]}" -print0 \
  | sign_files_parallel \
  || { echo "批量签名动态库（.dylib/.so）失败" >&2; exit 1; }
# 1b) 可执行文件（MacOS 主程序、bin/ 下 nginx/ffmpeg/new-api；同样排除 Chrome 子树，交给 1c 深签）
find "$APP" -type f -perm -u+x ! -name '*.dylib' ! -name '*.so' ! -name '*.so.*' "${CHROME_EXCL[@]}" -print0 \
  | sign_files_parallel \
  || { echo "批量签名可执行文件失败" >&2; exit 1; }
# 1c) Chrome for Testing 子树：Google chrome-for-testing zip 里的 helpers/framework 主件都是 ad-hoc
#     linker-signed（无 Team ID、无时间戳、无 hardened runtime），Apple 公证对每个 Mach-O 都要求
#     Developer ID + 时间戳 + 硬化运行时，ad-hoc 一律拒绝。必须整棵子树重签。
#     ★ 关键结论（Chrome 156 实测）：保持 Google 原始 versioned framework 布局，不要做任何
#       「归一化」搬动。原始布局的 Libraries/ 与 Helpers/ 本就是 codesign 认可的 framework 嵌套
#       代码目录，--force --deep 能递归 seal 它们；历史上把 Libraries/Helpers 搬进 Resources、
#       删顶层 Info.plist 反而让 bundle 根出现「unsealed contents present in the root directory of
#       an embedded framework」导致公证反复 Invalid。entitlements 用我们已有的（allow-jit /
#       allow-unsigned-executable-memory / disable-library-validation / allow-dyld-environment-variables）
#       覆盖 Chrome 渲染进程 JIT 需求。
#
#     正确顺序（对原始结构）：
#       1c-ii)  就地签「松散 Mach-O」：framework 的 Versions/Current/Libraries/** 里的 .dylib，以及
#                Versions/Current/Helpers 顶层的裸可执行件（app_mode_loader / chrome_crashpad_handler /
#                web_app_shortcut_copier）。★ 这些既不是 bundle、又不在 --deep 递归签名的名单里，
#                必须显式就地签，否则全新构建会漏签导致公证 Invalid。
#                ⚠️ 谓词必须精确锁到 framework 的 Versions/Current/{Libraries,Helpers}，绝不能写成
#                   宽泛的 */Resources/*：Chrome 整体就装在宿主 App 的 Contents/Resources/… 下，宽谓词
#                   会命中子树里每一个文件——包括 framework/chrome/helper .app 主件；对这些 bundle 主件
#                   单独 codesign 会要求父 bundle 已签而报「code object is not signed at all / In
#                   subcomponent …Helper.app」直接失败（全新下载的 Chrome 必踩）。
#       1c-iii) 单步 --force --deep 签 Chrome 顶层 .app：在 bundle 上下文递归签 framework 主件、
#                Helpers 内的 .app，并把 1c-ii 签好的松散件哈希进 seal。顺序「先内后外、先签后 seal」
#                由 --deep 自动完成，无需再单独 deep 签 framework。
#       1c-iv)  显式 --verify --deep --strict 校验 Chrome 子树（外层 .app 的 --deep 不会递归进
#                Contents/Resources/**/Chrome.app，必须单独验，否则白跑一轮公证）。
while IFS= read -r -d '' chrome_root; do
  log "1c) 重签 Chrome 子树：$chrome_root"
  # 1c-ii) 就地签松散 Mach-O：framework 的 Versions/Current/Libraries/** + Helpers 顶层裸件（非 .app）。
  #        bundle 主件（chrome/framework/helper .app）留给下面 --force --deep 在 bundle 上下文签。
  fw_vc="$chrome_root/Contents/Frameworks/Google Chrome for Testing Framework.framework/Versions/Current"
  if [[ -d "$fw_vc" ]]; then
    { find "$fw_vc/Libraries" -type f -print0 2>/dev/null
      find "$fw_vc/Helpers" -maxdepth 1 -type f -print0 2>/dev/null
    } | sort -zu | sign_files_parallel \
      || { echo "Chrome 子树松散 Mach-O 批量签名失败：$chrome_root" >&2; exit 1; }
  fi
  # 1c-iii) 单步 --force --deep 签 Chrome 顶层 .app（保持原始结构，不搬动、不归一化）
  codesign --force --deep --options runtime --timestamp \
           --entitlements "$ENTITLEMENTS" \
           --sign "$SIGN_IDENTITY" "$chrome_root"
  # 1c-iv) ★ 显式校验 Chrome 子树：外层 .app 的 --verify --deep 不会递归进
  #         Contents/Resources/**/Chrome.app，故 Chrome 的 seal/签名问题必须这里单独验一遍。
  codesign --verify --deep --strict --verbose=2 "$chrome_root" \
    || { echo "Chrome 子树签名校验未通过：${chrome_root}（不要提交公证，先修此处）" >&2; exit 11; }
done < <(find "$APP" -type d -name 'Google Chrome for Testing.app' -print0)

# ------------------------------------------------ 2) 签 App 本体
log "2/3 对 App 本体签名（硬化运行时 + 授权清单）"
codesign --force --options runtime --timestamp \
         --entitlements "$ENTITLEMENTS" \
         --sign "$SIGN_IDENTITY" \
         --preserve-metadata=entitlements,requirements,flags,runtime \
         "$APP"

codesign --verify --deep --strict --verbose=2 "$APP"

# ------------------------------------------------ 2b) 提交前漏签预扫
# 把 App 内每个 Mach-O 单独验一遍：漏签 / seal 损坏 / 仍是 ad-hoc 的，全在本地一次性报出来，
# 不让「本地可发现」的问题进 Apple 队列（一轮公证 Invalid 要几小时才发现，等于重签重打包重提交整轮白跑）。
log "2b) 公证前漏签预扫（App 内全量 Mach-O）"
BAD=()
while IFS= read -r -d '' f; do
  is_macho "$f" || continue
  if ! codesign --verify --strict "$f" 2>/dev/null; then
    BAD+=("$f  （未签名或 seal 校验失败）")
  elif codesign -dvv "$f" 2>&1 | grep -q 'Signature=adhoc'; then
    BAD+=("$f  （仍是 ad-hoc 签名，公证必拒）")
  fi
done < <(find "$APP" -type f \( -perm -u+x -o -name '*.dylib' -o -name '*.so' -o -name '*.so.*' \) -print0)
if [[ "${#BAD[@]}" -gt 0 ]]; then
  printf '  %s\n' "${BAD[@]}" >&2
  echo "漏签预扫未通过：${#BAD[@]} 个文件。先修复签名，再提交公证。" >&2
  exit 12
fi
log "漏签预扫通过"

# ------------------------------------------------ 3) 公证 + staple
TARGET_FOR_NOTARY="$APP"
if [[ -n "$DMG" ]]; then TARGET_FOR_NOTARY="$DMG"; fi

if [[ "$NOTARIZE" != "1" ]]; then
  log "3/3 已跳过公证（NOTARIZE!=1）：仅完成本地签名，产物勿对外分发。"
  log "需要对外分发包时：NOTARIZE=1 $0 <App.app> [dmg]"
  exit 0
fi

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

# --output-format json 拿回结果：状态非 Valid 时按 id 自动拉公证日志打印，
# 闭环「Invalid 后还要手动找 ID 再查 log」的排错回路。
SUBMIT_OUT="$(xcrun notarytool submit "$SUBMIT" "${AUTH[@]}" --wait --output-format json 2>/tmp/notary-submit.err || true)"
[[ -n "$SUBMIT_OUT" ]] || SUBMIT_OUT='{}'
NOTARY_META="$(printf '%s' "$SUBMIT_OUT" | /usr/bin/python3 -c 'import json,sys
try:
    r = json.load(sys.stdin).get("result", {}) or {}
    print("%s\t%s" % (r.get("id", ""), r.get("status", "")))
except Exception:
    print("\t")')"
NOTARY_ID="${NOTARY_META%%$'\t'*}"
NOTARY_STATUS="${NOTARY_META#*$'\t'}"
if [[ "$NOTARY_STATUS" != "Valid" ]]; then
  echo "公证未通过（status=${NOTARY_STATUS:-提交失败}）：$SUBMIT" >&2
  [[ -s /tmp/notary-submit.err ]] && { echo '--- notarytool stderr ---' >&2; cat /tmp/notary-submit.err >&2; }
  if [[ -n "$NOTARY_ID" ]]; then
    log "自动拉取公证日志：$NOTARY_ID"
    xcrun notarytool log "$NOTARY_ID" "${AUTH[@]}" 2>/dev/null \
      || echo "（日志拉取失败；可手动重试：xcrun notarytool log ${NOTARY_ID}）" >&2
  fi
  exit 13
fi

xcrun stapler staple "$TARGET_FOR_NOTARY"
[[ "$TARGET_FOR_NOTARY" != "$APP" ]] && xcrun stapler staple "$APP"
# 终检失败不再无声吞掉：打印明确 WARNING，让「能否离线首启」在退出信息里可见。
xcrun stapler validate "$TARGET_FOR_NOTARY" 2>/dev/null \
  || echo "⚠️ WARNING：staple 终检未过——票据未钉入产物，离线首启仍会被 Gatekeeper 拦截；联网后可重试 stapler。" >&2
spctl --assess --type execute --verbose "$APP" 2>/dev/null \
  || echo "⚠️ WARNING：spctl 评估未过——对外分发前请在干净机器复核公证+staple 是否生效。" >&2

# 终检闭环：给了 dmg 就跑出厂终检（挂卷实测包内 App 的 Gatekeeper/签名/架构/版本），
# 让「本脚本跑绿」等价于「产物可分发」——dmg 里装的是不是刚公证的 App，在这里验而不是分发后才发现。
if [[ -n "$DMG" ]]; then
  bash "$SCRIPT_DIR/verify-release-dmg.sh" "$DMG" \
    || { echo "出厂终检未过：${DMG}（见上方 ✗ 项，修复后再分发）" >&2; exit 14; }
fi

log "完成：签名 + 公证 + staple 结束。"
[[ "${KEEP_NOTARY_ZIP:-0}" == "1" ]] || rm -f "${APP%.app}-notary.zip" 2>/dev/null || true
