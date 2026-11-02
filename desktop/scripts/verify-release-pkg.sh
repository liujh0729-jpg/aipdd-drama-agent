#!/usr/bin/env bash
# =============================================================================
# verify-release-pkg.sh —— MAS pkg 出厂终检（Track B 质量闸门）
#
#   回答一个问题：这个 pkg 能不能提交 App Store Connect 且不被自动化预检直接拒？
#   与直分发链的 verify-release-dmg.sh 独立，两条链各自的产物各自的门。
#
#   用法： ./verify-release-pkg.sh <path/to/*.pkg> [<path/to/*.pkg> ...]
#
#   环境变量：
#     EXPECTED_TEAM_ID           可选，6–10 位 Team ID；填了会比对 App 主签名的 TeamIdentifier。
#     EXPECTED_APP_IDENTITY      默认 "3rd Party Mac Developer Application"（Apple 新命名写 "Apple Distribution"）。
#     EXPECTED_INSTALLER_IDENTITY 默认 "3rd Party Mac Developer Installer"。
#
#   退出码：0 全部通过；21 存在未通过项（逐项打印 ✗ 与原因）。
#
#   与 Transporter 三大 ITMS 报错对应的回检（构建链 fail-fast 之后的最后一道确认）：
#     ITMS-90886 → k) profile Bundle ID / application-identifier 与签名 entitlements 同源比对；
#     ITMS-91167 → j) 主执行件与随包 Python 必须含 arm64（文件名 tag 为 _x64 的直接判拒）；
#     ITMS-91109 → l) payload 内 com.apple.quarantine 扫描（best-effort，pkg 展开未必保留 xattr，
#                    真闸门在 macos-pkg-appstore.sh 的签名前清理）。
# =============================================================================
set -uo pipefail

log() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
# plutil -extract raw 对 array/dict 只给元素个数，含点的平铺键也拆不开；
# 读列表项与含点键一律用 json dump + grep。
# json_get_str <json 串> <已转义点的键名片段>
json_get_str() {
  grep -oE "\"$2\":\"[^\"]*\"" <<<"$1" | head -1 | sed -E 's/^.*:"//; s/"$//' || true
}
# 架构串匹配：要求 arm64 时 arm64e / arm64e.x1 也算命中（grep -w 会漏）；
# 其余架构用显式词边界。
archs_contain() {  # $1=lipo 架构串 $2=要求的架构
  case "$2" in
    arm64) grep -qE '(^|[[:space:]])arm64([[:alnum:].]*|$)' <<<"$1" ;;
    *)     grep -qE "(^|[[:space:]])$2([[:space:]]|$)" <<<"$1" ;;
  esac
}
# 文件名承诺了多个架构（_universal）时逐个判。不能只查「含 arm64」：那会让一
# 个纯 arm64 包顶着 universal 的名字出厂，而文件名就是对外唯一承诺（与 dmg 终检同构）。
archs_cover_all() {  # $1=实测架构串 $2=要求列表（空格分隔）
  local have="$1" w
  for w in $2; do archs_contain "$have" "$w" || return 1; done
}

[[ $# -ge 1 ]] || { echo "用法: verify-release-pkg.sh <path/to/*.pkg> [...]" >&2; exit 2; }

EXPECTED_APP_IDENTITY="${EXPECTED_APP_IDENTITY:-3rd Party Mac Developer Application}"
EXPECTED_INSTALLER_IDENTITY="${EXPECTED_INSTALLER_IDENTITY:-3rd Party Mac Developer Installer}"

OVERALL=0
FAILED_LIST=()

for PKG in "$@"; do
  BN="$(basename "$PKG")"
  log "终检：$BN"
  BAD=0
  mark_fail() { echo "  ✗ $1" >&2; BAD=1; }
  mark_ok()   { echo "  ✓ $1"; }

  [[ -f "$PKG" ]] || { mark_fail "文件不存在：$PKG"; OVERALL=21; FAILED_LIST+=("${BN}（文件不存在）"); continue; }

  # a) pkg 外层 Installer 证书链
  # 新版 pkgutil --check-signature 不再写 "Signature 'OK'"，而是：
  #   Status: signed by a developer certificate issued by Apple (Development)
  #   Status: signed by a developer certificate issued by Apple for distribution
  # 两种都是合法签名。以“有 Certificate Chain 行 + 预期证书出现在链中”为准。
  SIGINFO="$(pkgutil --check-signature "$PKG" 2>&1 || true)"
  if grep -q "Status: signed" <<<"$SIGINFO" \
     && grep -qF -- "$EXPECTED_INSTALLER_IDENTITY" <<<"$SIGINFO"; then
    mark_ok "pkg 已用 ${EXPECTED_INSTALLER_IDENTITY} 签名且链可信"
  else
    mark_fail "pkg 签名不符预期：$(head -3 <<<"$SIGINFO" | tr '\n' ' ')"
  fi

  # b) 展开 payload 到临时目录
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  if ! pkgutil --expand-full "$PKG" "$TMP/expanded" >/dev/null 2>&1; then
    mark_fail "pkgutil --expand-full 失败（pkg 结构异常或权限不足）"
    OVERALL=21; FAILED_LIST+=("${BN}（无法展开）"); rm -rf "$TMP"; continue
  fi
  APP="$(find "$TMP/expanded" -maxdepth 4 -type d -name '*.app' 2>/dev/null | head -1)"
  if [[ -z "$APP" ]]; then
    mark_fail "pkg 内未找到 .app payload"
    OVERALL=21; FAILED_LIST+=("${BN}（无 App payload）"); rm -rf "$TMP"; continue
  fi
  mark_ok "pkg 内 App：$(basename "$APP")"

  # c) App 主签名：必须是 MAS Application 证书（不是 Developer ID）
  APP_SIG="$(codesign -dvv "$APP" 2>&1 || true)"
  if grep -qF -- "$EXPECTED_APP_IDENTITY" <<<"$APP_SIG"; then
    mark_ok "App 主签名为 ${EXPECTED_APP_IDENTITY}"
  else
    mark_fail "App 主签名不是 MAS 证书：$(grep -E 'Authority=|Signature=' <<<"$APP_SIG" | head -3 | tr '\n' ' ')"
  fi
  if [[ -n "${EXPECTED_TEAM_ID:-}" ]]; then
    if grep -q "TeamIdentifier=${EXPECTED_TEAM_ID}" <<<"$APP_SIG"; then
      mark_ok "Team ID 匹配：${EXPECTED_TEAM_ID}"
    else
      mark_fail "Team ID 不匹配（期望 ${EXPECTED_TEAM_ID}）：$(grep TeamIdentifier <<<"$APP_SIG" || echo '未打印 TeamIdentifier')"
    fi
  fi

  # d) deep 校验全量 Mach-O seal
  if codesign --verify --deep --strict "$APP" 2>/dev/null; then
    mark_ok "App deep 签名校验通过"
  else
    mark_fail "App deep 签名校验未过（沙盒下必然 SIGKILL）"
  fi

  # e) entitlements 硬约束：sandbox on + 三个禁用项 off
  ENT="$(codesign -d --entitlements :- "$APP" 2>/dev/null || true)"
  if [[ -z "$ENT" ]]; then
    mark_fail "读不到 App entitlements"
  else
    # plutil 归一化一次，避免 bool 简写与 xml 结构差异导致漏判
    ENT_N="$(plutil -convert xml1 -o - <<<"$ENT" 2>/dev/null || echo "$ENT")"
    if grep -qE '<key>com.apple.security.app-sandbox</key>[[:space:]]*<true/>' <<<"$ENT_N"; then
      mark_ok "已开启 com.apple.security.app-sandbox"
    else
      mark_fail "未开启 app-sandbox——App Review 一票否决"
    fi
    for FORBID in \
      'com.apple.security.cs.disable-library-validation' \
      'com.apple.security.cs.allow-unsigned-executable-memory' \
      'com.apple.security.cs.allow-dyld-environment-variables'; do
      if grep -A1 "<key>$FORBID</key>" <<<"$ENT_N" | grep -q '<true/>'; then
        mark_fail "沙盒禁用项仍开启：$FORBID"
      fi
    done
  fi

  # f) 无嵌套 .app（App Store 硬性拒绝，Helper.app / XPC 模板除外——本项目暂无此类）
  NESTED="$(find "$APP/Contents" -mindepth 2 -type d -name '*.app' 2>/dev/null || true)"
  if [[ -z "$NESTED" ]]; then
    mark_ok "无嵌套 .app"
  else
    mark_fail "检出嵌套 .app（ASC 自动预检会拒）：$(tr '\n' ' ' <<<"$NESTED")"
  fi

  # g) embedded.provisionprofile 存在且是合法 CMS（解出的明文同时给 k) 复用）
  PROFILE_PATH="$APP/Contents/embedded.provisionprofile"
  PROF_PLIST="$(mktemp)"
  PROF_OK=0
  if [[ -f "$PROFILE_PATH" ]] && security cms -D -i "$PROFILE_PATH" >"$PROF_PLIST" 2>/dev/null; then
    PROF_OK=1
    PROF_ENT="$(plutil -extract Entitlements json -o - "$PROF_PLIST" 2>/dev/null || true)"
    PROF_ENT_ID="$(json_get_str "$PROF_ENT" 'com\.apple\.application-identifier')"
    [[ -n "$PROF_ENT_ID" ]] || PROF_ENT_ID="$(json_get_str "$PROF_ENT" 'application-identifier')"
    if [[ -n "$PROF_ENT_ID" ]]; then
      mark_ok "embedded.provisionprofile 合法 · application-identifier=${PROF_ENT_ID}"
    else
      mark_ok "embedded.provisionprofile 合法（未抽出 application-identifier，非致命）"
    fi
  else
    mark_fail "embedded.provisionprofile 缺失或损坏——Transporter 上传必被拒"
  fi

  # h) Bundle ID 与 pkg identifier 一致
  # pkgutil --info 只能读已安装到系统的 pkg，对新产出的 pkg 文件不适用。
  # productbuild 把 identifier/version 写到展开后的 Distribution XML里：
  #   <product id="work.aipdd.dramaclaw" version="2.3.1" />
  DIST_XML="$TMP/expanded/Distribution"
  PKG_ID=""; PKG_VER=""
  if [[ -f "$DIST_XML" ]]; then
    PKG_ID="$(grep -oE '<product[^>]+id="[^"]+"' "$DIST_XML" | head -1 | sed -E 's/.*id="([^"]+)".*/\1/')"
    PKG_VER="$(grep -oE '<product[^>]+version="[^"]+"' "$DIST_XML" | head -1 | sed -E 's/.*version="([^"]+)".*/\1/')"
  fi
  APP_BUNDLE_ID="$(plutil -extract CFBundleIdentifier raw "$APP/Contents/Info.plist" 2>/dev/null || true)"
  if [[ -n "$APP_BUNDLE_ID" && -n "$PKG_ID" && "$APP_BUNDLE_ID" == "$PKG_ID" ]]; then
    mark_ok "Bundle ID == pkg identifier：$APP_BUNDLE_ID"
  else
    mark_fail "Bundle ID(${APP_BUNDLE_ID:-?}) ≠ pkg identifier(${PKG_ID:-?})"
  fi

  # i) 版本一致：pkg version ↔ App CFBundleShortVersionString
  APP_VER="$(plutil -extract CFBundleShortVersionString raw "$APP/Contents/Info.plist" 2>/dev/null || true)"
  if [[ -n "$APP_VER" && "$APP_VER" == "$PKG_VER" ]]; then
    mark_ok "版本一致（${APP_VER}）"
  else
    mark_fail "版本不符：pkg ${PKG_VER:-?}，App ${APP_VER:-?}"
  fi

  # j) 文件名架构 tag ↔ 主执行件 lipo 架构（与 dmg 终检同构，防跨架构缓存污染）
  MAIN_EXE="$(find "$APP/Contents/MacOS" -maxdepth 1 -type f 2>/dev/null | head -1)"
  if [[ "$BN" =~ _aarch64\.pkg$ ]]; then WANT_ARCH=arm64
  elif [[ "$BN" =~ _x64\.pkg$ ]]; then WANT_ARCH=x86_64
  elif [[ "$BN" =~ _universal\.pkg$ ]]; then WANT_ARCH="arm64 x86_64"
  else WANT_ARCH=""; fi
  # ITMS-91167：ASC 拒收无 arm64 切片的构建——只要是能上传的包，主执行件必须含 arm64。
  if [[ -n "$MAIN_EXE" ]]; then
    EXE_ARCHS="$(lipo -archs "$MAIN_EXE" 2>/dev/null || true)"
    if archs_contain "$EXE_ARCHS" arm64; then
      mark_ok "主执行件含 arm64（实测：${EXE_ARCHS:-?}）"
    else
      mark_fail "ITMS-91167：主执行件无 arm64 切片（实为 ${EXE_ARCHS:-未知}），该 pkg 不能上传 ASC"
    fi
  fi
  if [[ -n "$WANT_ARCH" && -n "$MAIN_EXE" ]]; then
    HAVE_ARCHS="$(lipo -archs "$MAIN_EXE" 2>/dev/null || true)"
    if archs_cover_all "$HAVE_ARCHS" "$WANT_ARCH"; then
      mark_ok "主执行件架构覆盖 ${WANT_ARCH}（实测：${HAVE_ARCHS:-?}）"
    else
      mark_fail "架构不符：文件名要求 ${WANT_ARCH}，实为 ${HAVE_ARCHS:-未知}"
    fi
    # 随包 Python 也验一遍（与 dmg 链同规则，防止「壳 arm64、payload x64」）
    PY="$APP/Contents/Resources/resources/python/bin/python3"
    [[ -f "$PY" ]] || PY="$APP/Contents/Resources/python/bin/python3"
    if [[ -f "$PY" ]]; then
      HAVE_PY="$(lipo -archs "$PY" 2>/dev/null || true)"
      if archs_cover_all "$HAVE_PY" "$WANT_ARCH"; then
        mark_ok "随包 Python 架构覆盖 ${WANT_ARCH}（实测：${HAVE_PY:-?}）"
      else
        mark_fail "随包 Python 架构不符：要求 ${WANT_ARCH}，实为 ${HAVE_PY:-未知}"
      fi
    fi
  fi

  # k) ITMS-90886 回检：profile 与签名 entitlements 的标识符同源
  #    含点的平铺键不能用 plutil -extract 直读（点被当路径分隔符），array 用 raw
  #    只能得到元素个数——两边一律走 json dump + grep。profile 明文已由 g) 解好。
  if [[ "$PROF_OK" == "1" ]]; then
    PROF_ID="$(json_get_str "$PROF_ENT" 'com\.apple\.application-identifier')"
    [[ -n "$PROF_ID" ]] || PROF_ID="$(json_get_str "$PROF_ENT" 'application-identifier')"
    PROF_BUNDLES="$(plutil -extract ApplicationPropertyBundleIDIdentifierFilenames json -o - "$PROF_PLIST" 2>/dev/null || true)"
    if [[ -n "$PROF_BUNDLES" ]]; then
      # json 形式 ["bundle.id"]；通配符为 ["*"] 或 ["prefix.*"]（BSD grep -E 不支持空分支）
      if grep -q -- "\"$APP_BUNDLE_ID\"" <<<"$PROF_BUNDLES" \
         || grep -q '"\*"' <<<"$PROF_BUNDLES" \
         || grep -qE '"[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*\.\*"' <<<"$PROF_BUNDLES"; then
        mark_ok "profile Bundle ID 覆盖 App：${APP_BUNDLE_ID:-?}"
      else
        mark_fail "ITMS-90886：profile Bundle ID（${PROF_BUNDLES}）≠ App ${APP_BUNDLE_ID:-?}"
      fi
    fi
    SIG_ENT_TMP="$(mktemp)"
    printf '%s' "$ENT" >"$SIG_ENT_TMP"
    SIG_ENT_JSON="$(plutil -convert json -o - "$SIG_ENT_TMP" 2>/dev/null || true)"
    rm -f "$SIG_ENT_TMP"
    SIG_APP_ID="$(json_get_str "$SIG_ENT_JSON" 'com\.apple\.application-identifier')"
    [[ -n "$SIG_APP_ID" ]] || SIG_APP_ID="$(json_get_str "$SIG_ENT_JSON" 'application-identifier')"
    if [[ -z "$SIG_APP_ID" ]]; then
      mark_fail "ITMS-90886：签名 entitlements 缺 application-identifier（上传必报「签名缺少 application identifier」）"
    elif [[ -n "$PROF_ID" && "${SIG_APP_ID%%.*}" == "${PROF_ID%%.*}" ]]; then
      # 前缀只取第一段（Team ID）：通配符 profile 的 TEAM.* 与签名的 TEAM.bundleid
      # 前缀相同，用 %.* 会多剔一段导致误判。
      mark_ok "签名 application-identifier 与 profile 同源：${SIG_APP_ID}"
    elif [[ -n "$PROF_ID" ]]; then
      mark_fail "ITMS-90886：签名 ${SIG_APP_ID} 前缀 ≠ profile ${PROF_ID} 前缀，签名主体与描述文件不同源"
    else
      mark_ok "签名 application-identifier=${SIG_APP_ID}（profile 未声明可比对值）"
    fi
  fi

  # l) ITMS-91109 回检（best-effort）：pkg 展开的 payload 里扫隔离属性。
  #    ProductArchive 的 cpio 不保证保留 xattr，扫不到不代表包里干净；真正的
  #    清理闸门在 macos-pkg-appstore.sh 签名前（xattr -cr + 残留 fail-fast）。
  QRH="$(xattr -sr "$APP" 2>/dev/null | grep -oE '^[^:]+: (com\.apple\.)?quarantine$' | head -5 || true)"
  if [[ -n "$QRH" ]]; then
    mark_fail "payload 内仍检出 com.apple.quarantine（打包前未清干净，重建前先 xattr -cr App 目录）"
  else
    mark_ok "payload 内未检出隔离属性（best-effort，构建链已 xattr -cr 为准）"
  fi

  rm -rf "$TMP"
  rm -f "$PROF_PLIST"
  if [[ "$BAD" -ne 0 ]]; then OVERALL=21; FAILED_LIST+=("$BN"); fi
done

echo
if [[ "$OVERALL" -eq 0 ]]; then
  log "全部通过：可提交 App Store Connect。"
else
  log "存在未通过项（${#FAILED_LIST[@]} 个）："
  printf '  ✗ %s\n' "${FAILED_LIST[@]}" >&2
  echo "  修复后再上传；重签走 scripts/macos-pkg-appstore.sh。" >&2
fi
exit "$OVERALL"
