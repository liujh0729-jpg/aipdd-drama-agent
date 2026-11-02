#!/usr/bin/env bash
# =============================================================================
# macos-pkg-appstore.sh —— Mac App Store 上架 pkg 打包（Track B 主脚本）
#
#   与直分发链的关系：本脚本【不】复用 mac-sign-notarize.sh。两条链产物分开、
#   证书不同、entitlements 不同，混跑会导致：
#     - 直分发 dmg 里带上 app-sandbox 装不上业务子进程；
#     - MAS pkg 里带上 disable-library-validation 直接被 App Review 拒。
#   两条链共用输入（同一个 cargo tauri build 出来的未签 .app），从这一步开始分道。
#
#   用法：
#     ./macos-pkg-appstore.sh <path/to/*.app> [<输出.dmg 无关，此处是 .pkg>]
#     ./macos-pkg-appstore.sh <path/to/*.app> /tmp/AIPDD.pkg
#
#   环境变量（全必填项都会 fail-fast）：
#     PROFILE           必填 · Mac App Store 类型的 .provisionprofile 绝对路径。
#                       没 profile 出的 pkg 上传 ASC 会被 Transporter 直接拒。
#     SIGN_APP_IDENTITY 默认 "3rd Party Mac Developer Application"（老命名；Apple
#                       新命名 "Apple Distribution" 也能用，把本变量覆盖即可）。
#     SIGN_PKG_IDENTITY 默认 "3rd Party Mac Developer Installer"（同上，新命名
#                       "Apple Installer"）。
#     ENTITLEMENTS      默认 ../src-tauri/entitlements.mas.plist（只给 App 本体用）
#     MAS_NESTED_ENTITLEMENTS
#                       默认空 · 嵌套件（.so/.dylib/可执行件）签名不带任何
#                       entitlements。这里不是偷懒：松散 Mach-O 装不下描述文件，
#                       一旦给它们加上 application-identifier 就必中 ITMS-90885。
#                       只有确为嵌套件准备了可内嵌描述文件的特殊件才需要覆盖。
#     PKG_IDENTIFIER    默认读 App CFBundleIdentifier（不加 .pkg 后缀，ASC 匹配用同一入口）
#     PKG_OUT           默认 <bundle>/macos-mas/<App名>_<版本>_<arch>.pkg
#
#   硬性拒绝的产物条件（在签名前就 fail-fast，防止出个必然被拒的 pkg 浪费上传额度）：
#     1) App 内仍有嵌套 .app（Chrome for Testing 之类）→ MAS 一律拒；
#     2) entitlements 里出现 disable-library-validation / allow-unsigned-executable-memory
#        / allow-dyld-environment-variables → 沙盒下必拒；
#     3) entitlements 里没有 com.apple.security.app-sandbox → 必拒；
#     4) 便携 Python 子树里非本 Team 签名件超过 0 → 沙盒下 dlopen 会 SIGKILL，
#        这里不做检测（Phase 2 会加，用 codesign -d 抽 Team ID 比对）；
#     5) 签名里出现 iOS 专用的平铺 application-identifier / bundle-identifier
#        → 必中 ITMS-90285，macOS 只认 com.apple.application-identifier；
#     6) 除主执行件以外的嵌套件签名里带 application-identifier → 必中 ITMS-90885。
#
#   Transporter 校验失败的 ITMS 对应的前置闸门（都在签名前后 fail-fast）：
#     ITMS-91109 → 步骤 -2：全量清理 com.apple.quarantine 等扩展属性。下载/解压出来的
#                  文件（便携 Python 的 crypto.py 是实测中招点）带隔离标记，上传必被拒；
#                  标记不在 Mach-O 签名覆盖范围里，清晚了（打完 pkg 再清）无效。
#     ITMS-90886 → 步骤 -1：profile 的 Bundle ID / application-identifier / 队列类型
#                  与 App 逐项比对，混进开发 / AdHoc profile 直接拒；签名后再验 App 的
#                  签名 entitlements 里必须含 com.apple.application-identifier 且前缀与 profile 一致。
#     ITMS-90285 → 步骤 -1 注入 + 签后回验：只写 macOS 形的 com.apple.application-identifier，
#                  不再补写 iOS 平铺键（2.3.2 实测：写了就被回「key 'application-identifier'
#                  in '…/Contents/MacOS/dramaclaw-desktop' is not supported」）。
#     ITMS-90885 → 步骤 1 嵌套件不套 entitlements（根源）+ 步骤 3 全树回验：任何带
#                  application-identifier 却没（也不可能有）描述文件的嵌套件直接拦下。
#     ITMS-91167 → 步骤 -1：App 内全部 Mach-O 必须含 arm64 切片；仅 arm64 或双架构放行，
#                  纯 x86_64 包拒收（ASC 已不接受无 arm64 的构建）。应急开关 REQUIRE_ARM64=0。
#
#   ⚠️ 密钥/凭据零落盘：证书走钥匙串、profile 走文件路径传参，脚本绝不打印内容。
# =============================================================================
set -euo pipefail

APP="${1:?用法: macos-pkg-appstore.sh <App.app> [pkg-out]}"
PKG_OUT_ARG="${2:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PROFILE="${PROFILE:?缺少 PROFILE：请传 Mac App Store 类型的 .provisionprofile 路径}"
ENTITLEMENTS="${ENTITLEMENTS:-$SCRIPT_DIR/../src-tauri/entitlements.mas.plist}"
SIGN_APP_IDENTITY="${SIGN_APP_IDENTITY:-3rd Party Mac Developer Application}"
SIGN_PKG_IDENTITY="${SIGN_PKG_IDENTITY:-3rd Party Mac Developer Installer}"
# ITMS-91167 闸门开关：默认 1（App 内 Mach-O 必须含 arm64）。仅当明确要出
# 不能上传 ASC 的 Intel 专用调试包时才允许置 0。
REQUIRE_ARM64="${REQUIRE_ARM64:-1}"

log() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
is_macho() { file -b "$1" 2>/dev/null | grep -q 'Mach-O'; }
# plutil -extract raw 对 array/dict 只输出元素个数，含点的平铺键（如
# com.apple.application-identifier）也拆不开——列表与含点键一律走 json dump + grep。
# json_get_str <json 串> <已转义点的键名片段>
json_get_str() {
  grep -oE "\"$2\":\"[^\"]*\"" <<<"$1" | head -1 | sed -E 's/^.*:"//; s/"$//' || true
}

# ------------------------------------------------ 前置 fail-fast
[[ -d "$APP" ]] || { echo "App 不存在：$APP" >&2; exit 2; }
[[ -f "$ENTITLEMENTS" ]] || { echo "缺少 MAS entitlements：$ENTITLEMENTS" >&2; exit 2; }
[[ -f "$PROFILE" ]] || { echo "Profile 不存在：$PROFILE" >&2; exit 2; }

# 证书快速失败：
#   · Application 证书走 codesign 策略，用 find-identity 验（需同时有私钥）。
#   · Installer 证书 Extended Key Usage 是 Apple Installer (OID 1.2.840.113635.100.4.9)，
#     不属 codesigning 策略——find-identity -p codesigning 不列它 是设计使然，不是缺私钥！
#     这里只能验公钥存在；私钥在不在交给 productbuild 自己报（它失败时会写
#     「identity cannot be used for signing code」或「no identity found」，信息更准）。
security find-identity -v -p codesigning | grep -qF -- "$SIGN_APP_IDENTITY" \
  || { echo "钥匙串缺 App 签名证书（或私钥不在本机）：${SIGN_APP_IDENTITY}（导入后用 SIGN_APP_IDENTITY 覆盖；Apple 新命名可写 Apple Distribution）" >&2; exit 3; }
security find-certificate -c "$SIGN_PKG_IDENTITY" >/dev/null 2>&1 \
  || { echo "钥匙串缺 Installer 证书（公钥都没有）：${SIGN_PKG_IDENTITY}（导 mac_installer.cer 或用 SIGN_PKG_IDENTITY 覆盖）" >&2; exit 3; }

# MAS 拒绝嵌套 .app（除官方允许的 *.Helper.app / XPC Service 模板，本 App 目前无这两类）。
NESTED_APPS="$(find "$APP/Contents" -mindepth 2 -type d -name '*.app' 2>/dev/null || true)"
if [[ -n "$NESTED_APPS" ]]; then
  echo "MAS 不接受 App 内嵌 .app，检出：" >&2
  printf '  %s\n' $NESTED_APPS >&2
  echo "先跑 build-macos-mas-app.sh 让它剥离禁用子树，或手工 rm -rf 后再签。" >&2
  exit 4
fi

# Entitlements 结构预检：不能包含沙盒禁用项、必须开启 sandbox。
ENT_PLAIN="$(plutil -convert xml1 -o - "$ENTITLEMENTS")"
for FORBID in \
  'com.apple.security.cs.disable-library-validation' \
  'com.apple.security.cs.allow-unsigned-executable-memory' \
  'com.apple.security.cs.allow-dyld-environment-variables'; do
  if grep -qF -- "$FORBID" <<<"$ENT_PLAIN"; then
    echo "MAS 沙盒禁止 ${FORBID}，但 ${ENTITLEMENTS} 里出现了。请改回非沙盒链使用。" >&2
    exit 5
  fi
done
grep -qF -- 'com.apple.security.app-sandbox' <<<"$ENT_PLAIN" \
  || { echo "MAS 必需 com.apple.security.app-sandbox=true，$ENTITLEMENTS 缺失。" >&2; exit 5; }

# ------------------------------------------------ -2) 清除隔离属性（ITMS-91109 闸门）
# 必须在注入 profile / 签名 / productbuild 之前执行：ditto/cargo build 拷贝会把下载
# 解压源文件的 com.apple.quarantine 一路带进 App 子树（实测 crypto.py 中招），
# 任何一个文件带标记整个 pkg 都被 ASC 拒；打完 pkg 再清无效。
log "-2) 清理 App 内 com.apple.quarantine 等扩展属性（ITMS-91109）"
QUARANTINED="$(xattr -sr "$APP" 2>/dev/null | grep -oE '^[^:]+: (com\.apple\.)?quarantine$' | cut -d: -f1 || true)"
if [[ -n "$QUARANTINED" ]]; then
  echo "  检出 $(printf '%s\n' "$QUARANTINED" | wc -l | tr -d ' ') 个带隔离属性的文件，全量清理：" >&2
  printf '  %s\n' "$QUARANTINED" | head -20 >&2
fi
xattr -cr "$APP" || { echo "xattr -cr 清理失败：$APP" >&2; exit 9; }
if [[ -n "$(xattr -sr "$APP" 2>/dev/null | grep -E ': com\.apple\.quarantine' || true)" ]]; then
  echo "隔离属性清理后仍残留（文件只读/SIP 限制），继续打包必被 ITMS-91109 拒。" >&2
  exit 9
fi
echo "  ✓ 扩展属性已清空（无 com.apple.quarantine 残留）"

# ------------------------------------------------ 元数据抽取
PB="/usr/libexec/PlistBuddy"
PLIST="$APP/Contents/Info.plist"
APP_BASE="$(basename "$APP")"
APP_NAME="${APP_BASE%.app}"
CF_BUNDLE_ID="$("$PB" -c 'Print :CFBundleIdentifier' "$PLIST" 2>/dev/null || echo work.aipdd.dramaclaw)"
CF_VERSION="$("$PB" -c 'Print :CFBundleShortVersionString' "$PLIST" 2>/dev/null || echo 0.0.0)"
MAIN_BIN="$(find "$APP/Contents/MacOS" -maxdepth 1 -type f -perm -u+x 2>/dev/null | head -1)"
[[ -n "$MAIN_BIN" ]] || { echo "找不到主执行件（$APP/Contents/MacOS 下无可执行文件），这包不可能是完整构建。" >&2; exit 2; }
MAIN_ARCH="$(lipo -archs "$MAIN_BIN" 2>/dev/null || true)"
case "$MAIN_ARCH" in
  *arm64*x86_64*|*x86_64*arm64*) ARCH_TAG=universal ;;
  *arm64*)  ARCH_TAG=aarch64 ;;
  *x86_64*) ARCH_TAG=x64 ;;
  *)        ARCH_TAG=unknown ;;
esac
PKG_ID="${PKG_IDENTIFIER:-$CF_BUNDLE_ID}"

# ------------------------------------------------ -1) profile 身份与架构闸门（ITMS-90886 / ITMS-91167）
log "-1) 校验 provisionprofile 与 App 身份匹配、Mach-O 含 arm64"
PROFILE_PLIST="$(mktemp)"
trap 'rm -f "$PROFILE_PLIST"' EXIT
security cms -D -i "$PROFILE" >"$PROFILE_PLIST" 2>/dev/null \
  || { echo "PROFILE 不是合法的 .provisionprofile（CMS 解析失败）：$PROFILE" >&2; exit 10; }

# a) ITMS-90886：profile 的 Bundle ID 必须覆盖 App 的 CFBundleIdentifier。
#    签名 payload 里的 Bundle ID 与 embedded.provisionprofile 对不上 = 上传必报
#    「missing application-identifier / bundle id mismatch」。
PROFILE_XML="$(plutil -convert xml1 -o - "$PROFILE_PLIST")"
PROF_ENT_JSON="$(plutil -extract Entitlements json -o - "$PROFILE_PLIST" 2>/dev/null || true)"
# profile 里新旧两代命名都见过，先取新键、退化到旧键。
PROF_APP_ID="$(json_get_str "$PROF_ENT_JSON" 'com\.apple\.application-identifier')"
[[ -n "$PROF_APP_ID" ]] || PROF_APP_ID="$(json_get_str "$PROF_ENT_JSON" 'application-identifier')"
PROF_BUNDLE_IDS="$(plutil -extract ApplicationPropertyBundleIDIdentifierFilenames json -o - "$PROFILE_PLIST" 2>/dev/null || true)"
if [[ -z "$PROF_BUNDLE_IDS" && -z "$PROF_APP_ID" ]]; then
  echo "profile 里抽不到 Bundle ID / application-identifier，不是 Mac App Store 发布型 profile（可能是开发描述文件）。" >&2
  exit 10
fi
if [[ -n "$PROF_BUNDLE_IDS" ]]; then
  # json 形式为 ["work.aipdd.dramaclaw"]；通配符写成 ["*"] 或 ["work.aipdd.*"]。
  # 这里拆成三条 grep 而不是合成一条正则：BSD grep -E 不接受「空分支」的写法，
  # 合成一条会直接报 empty subexpression，导致通配符 profile 被误拦。
  grep -q -- "\"$CF_BUNDLE_ID\"" <<<"$PROF_BUNDLE_IDS" \
    || grep -q '"\*"' <<<"$PROF_BUNDLE_IDS" \
    || grep -qE '"[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*\.\*"' <<<"$PROF_BUNDLE_IDS" \
    || { echo "ITMS-90886 风险：profile 的 Bundle ID（${PROF_BUNDLE_IDS}）≠ App 的 ${CF_BUNDLE_ID}。请重新生成 Mac App Store 类型、勾选本 Bundle ID 的 profile。" >&2; exit 10; }
fi
if [[ "$PROF_APP_ID" == *.* ]]; then
  # TEAM.xxx 或 TEAM.*：后缀是完整 Bundle ID 或通配符才放行
  [[ "${PROF_APP_ID##*.}" == "*" || "${PROF_APP_ID#*.}" == "$CF_BUNDLE_ID" ]] \
    || { echo "ITMS-90886 风险：profile 的 application-identifier（${PROF_APP_ID}）与 App Bundle ID（${CF_BUNDLE_ID}）不匹配，两者必须同源。" >&2; exit 10; }
fi

# b) ITMS-90886：profile 必须是 App Store 分发队列型，不能混开发 / AdHoc。
#    硬拦：含 ProvisioningDevices（设备白名单）一定是开发/AdHoc 型；
#    软提示：没抽到 application-identifier 时另 ASC 队列标记存疑，只警告不误杀。
if grep -q '<key>ProvisioningDevices</key>' <<<"$PROFILE_XML"; then
  echo "ITMS-90886 风险：profile 含 ProvisioningDevices，是开发/AdHoc 型描述文件；MAS 上传必须用 Mac App Store 类型。" >&2
  exit 10
fi
if [[ -z "$PROF_APP_ID" ]]; then
  echo "  ⚠ profile 未声明 application-identifier，请确认是「Mac App Store」类型而不是开发描述文件。" >&2
fi
echo "  ✓ profile 为 Mac App Store 类型且 Bundle ID 匹配：${CF_BUNDLE_ID}（application-identifier=${PROF_APP_ID:-未声明}）"

# ITMS-90886 / ITMS-90285 根源修复：codesign 不会从 profile 自动注入标识符，
# entitlements 模板里没有这个键 → 签出来的包「签名缺 application-identifier 但
# profile 里有 Bundle ID」，ASC 直接报 90886。这里从 profile 读出同源的标识符，
# 注入一份构建期临时 entitlements（绝不回写仓库里的模板文件）。
#
# ⚠️ macOS 上合法的键名**只有 com.apple.application-identifier**。早先为了「两种命名
# 都写全」还额外写了平铺的 application-identifier / bundle-identifier，那是 iOS 专用
# 键，实测 ASC 直接回 ITMS-90285：「key 'application-identifier' in …  is not
# supported」；对照本机 App Store 装机件（Lark / WPS / Quark）的主执行件签名，里面
# 也只有带 com.apple. 前缀的那一个键。所以现在只注入 macOS 认的键，并顺手把模板里
# 可能残留的 iOS 平铺键删掉。
if [[ -n "$PROF_APP_ID" ]]; then
  # 通配符 profile（TEAM.*）不能把星号直接签进包——签名里必须是具体标识符，
  # Xcode 的做法就是用真实 Bundle ID 拼上 profile 的 Team 前缀。
  if [[ "${PROF_APP_ID##*.}" == "*" ]]; then
    INJECT_APP_ID="${PROF_APP_ID%.*}.$CF_BUNDLE_ID"
  else
    INJECT_APP_ID="$PROF_APP_ID"
  fi
  ENT_BUILD="$(mktemp)"
  plutil -convert xml1 -o "$ENT_BUILD" "$ENTITLEMENTS" \
    || { echo "entitlements 转 xml 失败：$ENTITLEMENTS" >&2; exit 10; }
  # 含点的平铺键必须用 PlistBuddy + 反斜杠转义点：plutil -insert 会把键拆成嵌套 dict
  # （拆出来的结构 codesign 不认），反引号会被当成键名的一部分。值不包反引号。
  # Add 失败（键已存在）则 Set，反复跑幂等。
  pb_put() {  # $1=完整键名（未转义） $2=值
    local esc="${1//./\\.}"
    /usr/libexec/PlistBuddy -c "Add ${esc} string $2" "$ENT_BUILD" 2>/dev/null \
      || /usr/libexec/PlistBuddy -c "Set ${esc} string $2" "$ENT_BUILD" \
      || { echo "entitlements 注入 $1 失败" >&2; exit 10; }
  }
  pb_del() {  # iOS 专用平铺键：不存在就当作通过
    local esc="${1//./\\.}"
    /usr/libexec/PlistBuddy -c "Delete ${esc}" "$ENT_BUILD" 2>/dev/null || true
  }
  pb_del application-identifier
  pb_del bundle-identifier
  pb_put com.apple.application-identifier "$INJECT_APP_ID"
  ENTITLEMENTS="$ENT_BUILD"
  echo "  ✓ 已向构建期 entitlements 注入 com.apple.application-identifier=${INJECT_APP_ID}（ITMS-90886 根源，且不写 iOS 平铺键）"
fi

# c) ITMS-91167：App 内核验架构——ASC 拒收无 arm64 切片的构建（只编了 x86_64 的
#    单架构包会被报「missing arm64」）。扫描全部 Mach-O，不只主二进制。
if [[ "$REQUIRE_ARM64" == "1" ]]; then
  # arm64e（Apple Silicon 系统二进制常见）同样算含 arm64；BSD grep 的 -w 不可靠，用显式边界。
  has_arm64() { grep -qE '(^|[[:space:]])arm64([[:alnum:].]*|$)' <<<"$1"; }
  # universal 包带一份随包的「设计上就单架构」清单（build-macos-universal.sh 从两棵树
  # 差集机械生成：各架构 wheel 自带的不同版本依赖、架构专属可选加速件、overlays/<arch>
  # 副本）。列在里面的件豁免；清单外的缺 arm64 仍一律硬拦——那才是真的「资源拿错架构」。
  # 没这份清单只有两条路：把合法包误拒，或者逼人 REQUIRE_ARM64=0 把闸门拆掉。
  PERARCH_MANIFEST="$APP/Contents/Resources/resources/universal-per-arch.txt"
  EXEMPT=0
  NO_ARM64=()
  while IFS= read -r -d '' f; do
    is_macho "$f" || continue
    archs="$(lipo -archs "$f" 2>/dev/null || true)"
    [[ -n "$archs" ]] || continue
    if has_arm64 "$archs"; then continue; fi
    if [[ -f "$PERARCH_MANIFEST" ]]; then
      rel="${f#"$APP"/}"
      # 用 awk 做字面比较而不是拿路径去拼正则：这些路径里有 . ( ) 空格中文，正则转义更难写也更容易错。
      if awk -F'\t' -v p="$rel" '$1==p{found=1} END{exit !found}' "$PERARCH_MANIFEST" 2>/dev/null; then
        EXEMPT=$((EXEMPT + 1))
        continue
      fi
    fi
    NO_ARM64+=("$f (${archs})")
  done < <(find "$APP" -type f \( -perm -u+x -o -name '*.dylib' -o -name '*.so' -o -name '*.so.*' \) -print0)
  if [[ "$EXEMPT" -gt 0 ]]; then
    echo "  ✓ 豁免单架构件 ${EXEMPT} 个（按随包清单 universal-per-arch.txt，各架构各自引用自己那份）"
  fi
  if [[ "${#NO_ARM64[@]}" -gt 0 ]]; then
    printf '  ✗ 缺 arm64 切片：%s\n' "${NO_ARM64[@]}" >&2
    if [[ "$MAIN_ARCH" == *x86_64* && "$MAIN_ARCH" != *arm64* ]]; then
      echo "ITMS-91167 硬拦：主二进制是纯 x86_64。MAS 链请直接用 arm64 构建：" >&2
      echo "  ./build-macos-mas-app.sh arm64 && PROFILE=... ./macos-pkg-appstore.sh <aarch64 目录下的 .app>" >&2
      echo "（两架构分开各自出包即可上架；要单包双架构跑 ./build-macos-universal.sh，它串行构两棵树再把整棵 Mach-O 树 lipo 合并）" >&2
    else
      echo "ITMS-91167 硬拦：App 内存件无 arm64（多为 resources 拉了 x64 专用二进制），需从源头重建对应架构资源。" >&2
    fi
    echo "应急绕过（产物不可上传 ASC）：REQUIRE_ARM64=0 重跑" >&2
    exit 11
  fi
  echo "  ✓ 全部 Mach-O 含 arm64 切片（主二进制：${MAIN_ARCH:-未知}）"
fi

# 输出目录：与直分发 dmg 目录同级、独立，避免任何脚本误伤。
BUNDLE_MACOS_DIR="$(cd "$(dirname "$APP")" && pwd)"
MAS_OUT_DIR="$BUNDLE_MACOS_DIR/../macos-mas"
mkdir -p "$MAS_OUT_DIR"
[[ -n "$PKG_OUT_ARG" ]] && PKG_OUT="$PKG_OUT_ARG" || PKG_OUT="$MAS_OUT_DIR/${APP_NAME}_${CF_VERSION}_${ARCH_TAG}.pkg"
[[ -n "$PKG_OUT" ]] && mkdir -p "$(dirname "$PKG_OUT")"

# ------------------------------------------------ 0) 注入 provisioning profile
log "0/4 注入 embedded.provisionprofile"
# MAS 提交要求 profile 落在 Contents/ 根（不是 Resources/）。反复构建时先清旧的再拷新的，
# 避免残留上一次 profile 的 team/bundle 不匹配。
rm -f "$APP/Contents/embedded.provisionprofile"
cp "$PROFILE" "$APP/Contents/embedded.provisionprofile"
# profile 几乎总是浏览器从 developer.apple.com 下载来的 —— 文件本身带着
# com.apple.quarantine（实测还带 kMDItemWhereFroms）。上面的 -2) 清的是 App 树，
# 而这次 cp 会把隔离属性重新写进 payload，等于自己把 ITMS-91109 请回来（实测踩过：
# 前置闸门全绿、终检才报 payload 内仍有隔离属性，而那个文件就是刚注入的 profile）。
# 所以注入后必须再清一次并全树复核 —— 顺带不把打包机的下载来源元数据发给 Apple。
xattr -c "$APP/Contents/embedded.provisionprofile" 2>/dev/null || true
if [[ -n "$(xattr -sr "$APP" 2>/dev/null | grep -E ': com\.apple\.quarantine' || true)" ]]; then
  echo "注入 profile 后仍检出 com.apple.quarantine，拒绝出货（ITMS-91109：任一个文件带标记就整包驳回）：" >&2
  xattr -sr "$APP" 2>/dev/null | grep -E ': com\.apple\.quarantine' | head -10 >&2
  exit 9
fi
echo "  ✓ 注入 profile 后隔离属性复核通过（ITMS-91109）"

# ------------------------------------------------ 1) 由内向外签所有嵌套 Mach-O
log "1/4 遍历并签名 App 内所有可执行文件 / .dylib / .so（用 ${SIGN_APP_IDENTITY}，嵌套件不带 entitlements）"
# ITMS-90885 根源修复：嵌套件**不能**带 application-identifier。
# ASC 的规则是「签名里有 application-identifier 的嵌套件必须自带匹配的 provisioning
# profile」，而松散的 Mach-O（python / nginx / *.so / *.dylib）根本无处放描述文件——
# 本机 App Store 装机件（Lark 等）里的 dylib / helper 也都不带 entitlements。旧做法
# 把 App 的 entitlements 原样套给几千个嵌套件，每个都带着标识符却没 profile，上传
# 就只能收到 90885。沙盒由父进程继承，嵌套件自己不需要 app-sandbox。
# 确需给嵌套件单独 entitlements 时用 MAS_NESTED_ENTITLEMENTS 指文件（默认空）。
MAS_NESTED_ENTITLEMENTS="${MAS_NESTED_ENTITLEMENTS:-}"
if [[ -n "$MAS_NESTED_ENTITLEMENTS" && ! -f "$MAS_NESTED_ENTITLEMENTS" ]]; then
  echo "MAS_NESTED_ENTITLEMENTS 指向的文件不存在：$MAS_NESTED_ENTITLEMENTS" >&2
  exit 2
fi
NPROC="$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"
# 与 mac-sign-notarize.sh 同构的并行签：批量 codesign + 3 次重试消除同 bundle 竞态。
# 注意 --options runtime（hardened runtime）在 MAS 下是必需项，不能省。
sign_files_parallel() {
  xargs -0 -P "$NPROC" -n1 /usr/bin/env bash -c '
    file -b "$2" 2>/dev/null | grep -q "Mach-O" || exit 0
    ent=()
    [[ -n "${1:-}" ]] && ent=( --entitlements "$1" )   # bash 3.2 下空数组不能直接展开
    for _attempt in 1 2 3; do
      codesign --force --options runtime --timestamp "${ent[@]+"${ent[@]}"}" --sign "$0" "$2" && exit 0
      sleep "$_attempt"
    done
    echo "codesign 失败（重试 3 次后）：$2" >&2; exit 1
  ' "$SIGN_APP_IDENTITY" "$MAS_NESTED_ENTITLEMENTS"
}
# 动态库先签（.dylib/.so/.so.*），再签可执行件，最后 App 本体——顺序不能反。
find "$APP" -type f \( -name '*.dylib' -o -name '*.so' -o -name '*.so.*' \) -print0 \
  | sign_files_parallel \
  || { echo "批量签名动态库失败" >&2; exit 6; }
find "$APP" -type f -perm -u+x ! -name '*.dylib' ! -name '*.so' ! -name '*.so.*' -print0 \
  | sign_files_parallel \
  || { echo "批量签名可执行文件失败" >&2; exit 6; }

# ------------------------------------------------ 2) 签 App 本体
log "2/4 对 App 本体签名（沙盒 + hardened runtime + profile）"
codesign --force --options runtime --timestamp \
         --entitlements "$ENTITLEMENTS" \
         --sign "$SIGN_APP_IDENTITY" \
         --preserve-metadata=entitlements,requirements,flags,runtime \
         "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

# ITMS-90886 / ITMS-90285 回验：签完的 App entitlements 里必须带 macOS 形的
# com.apple.application-identifier，且前缀（Team ID）与 profile 同源——「签名缺少
# application-identifier 但配置文件里有 Bundle ID」正是 90886 的成因；反过来写了
# iOS 专用的平铺 application-identifier / bundle-identifier 就是 90285 的成因。
# 含点键不能用 plutil -extract 直读（会把点当路径分隔符），走临时文件 + json。
SIGNED_ENT_TMP="$(mktemp)"
trap 'rm -f "$PROFILE_PLIST" "$SIGNED_ENT_TMP"' EXIT
codesign -d --entitlements :- "$APP" >"$SIGNED_ENT_TMP" 2>/dev/null || : >"$SIGNED_ENT_TMP"
SIGNED_ENT_JSON="$(plutil -convert json -o - "$SIGNED_ENT_TMP" 2>/dev/null || true)"
SIGNED_APP_ID="$(json_get_str "$SIGNED_ENT_JSON" 'com\.apple\.application-identifier')"
if [[ -z "$SIGNED_APP_ID" ]]; then
  echo "ITMS-90886 硬拦：签名后的 entitlements 里没有 com.apple.application-identifier（检查 $ENTITLEMENTS 是否漏了该键，且不得与 profile 冲突）。" >&2
  exit 10
fi
if [[ -n "$PROF_APP_ID" && "${SIGNED_APP_ID%%.*}" != "${PROF_APP_ID%%.*}" ]]; then
  echo "ITMS-90886 硬拦：签名 application-identifier 前缀（${SIGNED_APP_ID%%.*}）≠ profile 前缀（${PROF_APP_ID%%.*}），签名主体与描述文件不同源。" >&2
  exit 10
fi
echo "  ✓ 签名 com.apple.application-identifier=${SIGNED_APP_ID}（与 profile 同源）"
for IOS_ONLY in application-identifier bundle-identifier; do
  if grep -qE '(^|[,{])[[:space:]]*"'"$IOS_ONLY"'"[[:space:]]*:' <<<"$SIGNED_ENT_JSON"; then
    echo "ITMS-90285 硬拦：签名 entitlements 里还有 iOS 专用平铺键 $IOS_ONLY，macOS 只认 com.apple. 前缀版本，ASC 会回「key '$IOS_ONLY' … is not supported」。" >&2
    exit 10
  fi
done
echo "  ✓ 签名内无 iOS 专用平铺键（ITMS-90285）"

# ------------------------------------------------ 3) 漏签 / ad-hoc / 嵌套件标识符预扫
log "3/4 漏签 / ad-hoc / 嵌套件 application-identifier 预扫"
BAD=()
IDENT_LEAK=()
while IFS= read -r -d '' f; do
  is_macho "$f" || continue
  if ! codesign --verify --strict "$f" 2>/dev/null; then
    BAD+=("$f  （未签名或 seal 校验失败）")
  elif codesign -dvv "$f" 2>&1 | grep -q 'Signature=adhoc'; then
    BAD+=("$f  （仍是 ad-hoc 签名，App Review 必拒）")
  fi
  # ITMS-90885 闸门：除主执行件外，任何嵌套件签名里都不许出现 application-identifier。
  # 松散 Mach-O 装不下描述文件，「带标识符却没 profile」在 ASC 预检里就是
  # Cannot be used with TestFlight 的直接成因（实测旧链几千个嵌套件全部命中）。
  if [[ "$f" != "$MAIN_BIN" ]]; then
    if codesign -d --entitlements :- "$f" 2>/dev/null | grep -q 'application-identifier'; then
      IDENT_LEAK+=("$f")
    fi
  fi
done < <(find "$APP" -type f \( -perm -u+x -o -name '*.dylib' -o -name '*.so' -o -name '*.so.*' \) -print0)
if [[ "${#BAD[@]}" -gt 0 ]]; then
  printf '  %s\n' "${BAD[@]}" >&2
  echo "漏签预扫未通过：${#BAD[@]} 个文件。先修签名再打 pkg。" >&2
  exit 7
fi
if [[ "${#IDENT_LEAK[@]}" -gt 0 ]]; then
  printf '  ✗ 嵌套件签名带 application-identifier：%s\n' "${IDENT_LEAK[@]:0:5}" >&2
  echo "ITMS-90885 硬拦：${#IDENT_LEAK[@]} 个嵌套件带标识符却不可能自带描述文件。" >&2
  echo "  十有八九是显式传了 MAS_NESTED_ENTITLEMENTS，或嵌套件签完后又被直分发链重签过。" >&2
  exit 12
fi
echo "  ✓ 无漏签 / ad-hoc；嵌套件签名均不含 application-identifier（ITMS-90885）"

# ------------------------------------------------ 4) productbuild 出 pkg
log "4/4 productbuild → ${PKG_OUT}"
# --component <path> <destpath>：把 App 装到目标机的 /Applications；
# --sign 用 Installer 证书（不是 Application 证书）；
# --identifier/--version 显式给（默认从 Info.plist 抽，但 ASC 上匹配用 pkg identifier 更稳）。
rm -f "$PKG_OUT"
productbuild \
  --component "$APP" /Applications \
  --sign "$SIGN_PKG_IDENTITY" \
  --identifier "$PKG_ID" \
  --version "$CF_VERSION" \
  "$PKG_OUT"

# pkgutil 快速自检（不替代 verify-release-pkg.sh 的完整终检）
pkgutil --check-signature "$PKG_OUT" >/tmp/pkg-sig.$$ 2>&1 \
  || { echo "pkgutil 签名校验未过：${PKG_OUT}（详见 /tmp/pkg-sig.$$）" >&2; exit 8; }
grep -qF -- "$SIGN_PKG_IDENTITY" /tmp/pkg-sig.$$ \
  || { echo "pkg 签名证书不是预期的 ${SIGN_PKG_IDENTITY}" >&2; cat /tmp/pkg-sig.$$ >&2; exit 8; }
rm -f /tmp/pkg-sig.$$

cat <<EOF

$(printf '\033[1;32m✔ MAS pkg 已就绪\033[0m')
  产物：$PKG_OUT
  大小：$(du -h "$PKG_OUT" | awk '{print $1}')
  Bundle：$CF_BUNDLE_ID · 版本 $CF_VERSION · 架构 $ARCH_TAG

下一步：
  1) 完整终检：bash $(dirname "$0")/verify-release-pkg.sh "$PKG_OUT"
  2) 上传 App Store Connect：用 Transporter.app 拖入本 pkg，或：
       xcrun altool --upload-app -f "$PKG_OUT" -t macos \\
         --apiKey <KEY_ID> --apiIssuer <ISSUER_ID>
     ⚠️ 严禁在没有 ASC App 条目的情况下反复上传——上传即占审核额度、堆积会锁账号。

  审核前的架构阻塞（Phase 2~4）未解时，本 pkg 大概率被拒；被拒后修复再重传即可。
EOF
