#!/usr/bin/env bash
# =============================================================================
# verify-release-dmg.sh —— 发布产物的出厂终检：一条命令回答「这个 dmg 能不能对外分发」。
#
#   检查项（每个 dmg 独立判定，全部通过才 exit 0）：
#     a) dmg 已钉公证票（stapler validate）——离线首启的凭据；
#     b) 挂载后包内 App 过 Gatekeeper 实测（spctl --assess 须为 Notarized Developer ID，
#        而不是只看外层 dmg——钉票顺序错了这里就会红）；
#     c) 包内 App 全量 deep 签名校验（codesign --verify --deep --strict）；
#     d) 主执行件架构与文件名一致（_aarch64 必须 arm64、_x64 必须 x86_64——
#        防跨架构缓存误用打出的「能过签名但 Intel 机 Bad CPU type」的包）；
#     d2) 随包 Python 解释器架构也与文件名一致——只看主执行件会漏「壳 arm64、
#        payload x64」的污染（Apple Silicon 走 Rosetta 后崩 dlopen，2.3.1 真机事故）；
#     e) 包内 CFBundleShortVersionString 与文件名版本一致（防拿旧构建换名重发）。
#
#   用法： ./verify-release-dmg.sh <path/to/*.dmg> [<path/to/*.dmg> ...]
#   退出码：0 全部通过；21 存在未通过项。只读挂载、自动卸载，不改动产物。
# =============================================================================
set -uo pipefail

log() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }

[[ $# -ge 1 ]] || { echo "用法: verify-release-dmg.sh <path/to/*.dmg> [...]" >&2; exit 2; }

OVERALL=0
FAILED_LIST=()

for DMG in "$@"; do
  BN="$(basename "$DMG")"
  log "终检：$BN"
  BAD=0
  mark_fail() { echo "  ✗ $1" >&2; BAD=1; }
  mark_ok()   { echo "  ✓ $1"; }

  [[ -f "$DMG" ]] || { mark_fail "文件不存在：$DMG"; OVERALL=21; FAILED_LIST+=("${BN}（文件不存在）"); continue; }

  # a) dmg 钉票
  if xcrun stapler validate "$DMG" >/dev/null 2>&1; then mark_ok "dmg 已钉公证票"; else mark_fail "dmg 未钉票/公证未过——离线首启会被 Gatekeeper 拦截"; fi

  # b)~e) 挂载包内 App 实测
  MNT="$(hdiutil attach -nobrowse -readonly "$DMG" 2>/dev/null | awk -F'\t' 'END{print $NF}')"
  if [[ -z "$MNT" || ! -d "$MNT" ]]; then
    mark_fail "dmg 挂载失败"; OVERALL=21; FAILED_LIST+=("${BN}（挂载失败）"); continue
  fi
  APP="$(find "$MNT" -maxdepth 1 -type d -name '*.app' 2>/dev/null | head -1)"
  if [[ -z "$APP" ]]; then
    mark_fail "卷内未找到 .app"
  else
    # b) Gatekeeper 实测：必须是 Notarized Developer ID（只 accepted 不公证也可能是本机缓存放行）
    SPCTL_OUT="$(spctl --assess --type execute --verbose=2 "$APP" 2>&1 || true)"
    if grep -q 'accepted' <<<"$SPCTL_OUT" && grep -q 'Notarized Developer ID' <<<"$SPCTL_OUT"; then
      mark_ok "包内 App 过 Gatekeeper（Notarized Developer ID）"
    else
      mark_fail "包内 App 未过 Gatekeeper：$(tr '\n' ' ' <<<"$SPCTL_OUT")"
    fi
    # c) 全量 deep 校验
    if codesign --verify --deep --strict "$APP" 2>/dev/null; then mark_ok "包内 App deep 签名校验通过"
    else mark_fail "包内 App deep 签名校验未过"; fi
    # d) 架构一致性：文件名 _aarch64/_x64 ↔ 主执行件 lipo 架构
    EXE="$(find "$APP/Contents/MacOS" -maxdepth 1 -type f 2>/dev/null | head -1)"
    if [[ "$BN" =~ _aarch64\.dmg$ ]]; then WANT_ARCH=arm64; elif [[ "$BN" =~ _x64\.dmg$ ]]; then WANT_ARCH=x86_64; else WANT_ARCH=""; fi
    if [[ -n "$WANT_ARCH" && -n "$EXE" ]]; then
      HAVE_ARCHS="$(lipo -archs "$EXE" 2>/dev/null || true)"
      if grep -qw "$WANT_ARCH" <<<"$HAVE_ARCHS"; then mark_ok "主执行件架构含 ${WANT_ARCH}（实测：${HAVE_ARCHS:-?}）"
      else mark_fail "架构不符：文件名要求 ${WANT_ARCH}，主执行件实为 ${HAVE_ARCHS:-未知}——疑跨架构缓存误用"; fi
    fi
    # d2) 随包 Python 解释器架构与文件名一致——只看主执行件会漏「壳 arm64、payload x64」
    #     这类污染（2.3.1 真机事件：arm 包里 nginx/new-api/python 全为 x86_64，用户端走
    #     Rosetta 后因用户目录包架构不匹配崩 dlopen）。以随包解释器为 payload 代表查一道。
    PY="$APP/Contents/Resources/resources/python/bin/python3"
    [[ -f "$PY" ]] || PY="$APP/Contents/Resources/python/bin/python3"
    if [[ -n "$WANT_ARCH" && -f "$PY" ]]; then
      HAVE_PY="$(lipo -archs "$PY" 2>/dev/null || true)"
      if grep -qw "$WANT_ARCH" <<<"$HAVE_PY"; then mark_ok "随包 Python 架构含 ${WANT_ARCH}（实测：${HAVE_PY:-?}）"
      else mark_fail "随包 Python 架构不符：文件名要求 ${WANT_ARCH}，实为 ${HAVE_PY:-未知}——payload 跨架构污染，重跑 prepare-resources $([ "$WANT_ARCH" = arm64 ] && echo mac-arm64 || echo mac-x64)"; fi
    fi
    # e) 版本一致性：文件名 _<ver>_ ↔ CFBundleShortVersionString
    if [[ "$BN" =~ _([0-9][0-9a-zA-Z.]*)_(aarch64|x64)\.dmg$ ]]; then
      WANT_VER="${BASH_REMATCH[1]}"
      REAL_VER="$(plutil -extract CFBundleShortVersionString raw "$APP/Contents/Info.plist" 2>/dev/null || true)"
      if [[ -n "$REAL_VER" && "$REAL_VER" == "$WANT_VER" ]]; then mark_ok "版本一致（${WANT_VER}）"
      else mark_fail "版本不符：文件名 ${WANT_VER}，包内实为 ${REAL_VER:-读取失败}——疑旧构建换名"; fi
    fi
  fi
  hdiutil detach "$MNT" -quiet 2>/dev/null || true

  if [[ "$BAD" -ne 0 ]]; then OVERALL=21; FAILED_LIST+=("$BN"); fi
done

echo
if [[ "$OVERALL" -eq 0 ]]; then
  log "全部通过：这些 dmg 可对外分发。"
else
  log "存在未通过项（$(( ${#FAILED_LIST[@]} )) 个）："
  printf '  ✗ %s\n' "${FAILED_LIST[@]}" >&2
  echo "  修复后再分发；若是漏公证/未钉票：NOTARIZE=1 scripts/mac-sign-notarize.sh <App> <dmg>" >&2
fi
exit "$OVERALL"
