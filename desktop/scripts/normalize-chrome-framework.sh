#!/usr/bin/env bash
# =============================================================================
# normalize-chrome-framework.sh —— 把 Chrome-for-Testing 的 framework 整理成
#   「标准 versioned 布局 + 签名友好」结构，让后续 codesign --force --deep 不再报
#   「bundle format is ambiguous」/「unsealed contents present in the root directory」。
#
#   这是「包结构改造」的唯一事实源：prepare-resources.sh 在解压 Chrome 后就地调用它，
#   mac-sign-notarize.sh 在签名前再防御性调用一次（幂等，已整理过则近乎空操作）。
#
#   用法： normalize-chrome-framework.sh <path/to/Google Chrome for Testing.app>
#
#   处理内容（对 chrome_root 内每个 *.framework）：
#     a) 删除顶层非标准重复项（Helpers/Libraries）——同内容在 Versions/Current/ 下。
#     b) 主件/Resources 若仍是与 Versions/Current 同名但不同 inode 的真文件 → 替为 symlink。
#     c) ★ 删除顶层 Info.plist（symlink 或真文件）——versioned framework 顶层不应有它，
#          多了它 codesign 会报 "unsealed contents present in the root directory of an
#          embedded framework"（这是本仓库公证反复 Invalid 的最终根因）。
#     d) 将 Versions/X/Libraries/ 移入 Versions/X/Resources/Libraries/，使其被默认资源
#        规则 ^Resources/ seal 覆盖（Libraries 不是 codesign 认可的嵌套代码位置）。
#     e) 将 Helpers/ 里的裸 Mach-O 可执行件（非 .app bundle，如 app_mode_loader /
#        chrome_crashpad_handler / web_app_shortcut_copier）移入 Resources/Libraries/bin/，
#        让 Helpers/ 只剩标准 .app bundle；裸件仍会被 1c 的 dylib/深签流程单独签。
#   全程幂等：每步都先判存在/类型，重复执行安全。
# =============================================================================
set -euo pipefail

CHROME_ROOT="${1:?用法: normalize-chrome-framework.sh <path/to/Google Chrome for Testing.app>}"
[[ -d "$CHROME_ROOT" ]] || { echo "目录不存在：$CHROME_ROOT" >&2; exit 2; }

log() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
is_macho() { file -b "$1" 2>/dev/null | grep -q 'Mach-O'; }

log "归一化 Chrome framework：$CHROME_ROOT"
while IFS= read -r -d '' fw; do
  # 仅处理 versioned framework（有 Versions/Current/）；flat 布局原样保留
  [[ -d "$fw/Versions/Current" ]] || continue
  fw_name="$(basename "$fw" .framework)"
  vdir="$fw/Versions/Current"

  # a) 移除顶层非标准项（仅当 Versions/Current 里确有同名，避免数据丢失）
  for entry in Helpers Libraries; do
    flat="$fw/$entry"
    vc="$vdir/$entry"
    [[ -e "$vc" ]] || continue
    [[ -e "$flat" || -L "$flat" ]] || continue
    [[ -L "$flat" ]] && unlink "$flat"
    [[ -e "$flat" ]] && rm -rf "$flat"
  done

  # b) 主件/Resources：同名但不同 inode 的真文件 → symlink
  for entry in "$fw_name" Resources; do
    flat="$fw/$entry"
    vc="$vdir/$entry"
    [[ -e "$flat" && ! -L "$flat" ]] || continue
    [[ -e "$vc" ]] || continue
    if [[ "$(stat -f %i "$flat")" != "$(stat -f %i "$vc")" ]]; then
      rm -rf "$flat"
      ln -s "Versions/Current/$entry" "$flat"
    fi
  done

  # c) ★ 删除顶层 Info.plist —— versioned framework 顶层不应有
  [[ -L "$fw/Info.plist" ]] && unlink "$fw/Info.plist"
  [[ -f "$fw/Info.plist" ]] && rm -f "$fw/Info.plist"

  # d) Libraries/ → Resources/Libraries/（被资源规则覆盖）
  #    防御中断恢复场景：若目标已存在且源也还存在（上一次 mv 后又被外部恢复/手动拷贝），
  #    直接 mv 会嵌套出 Resources/Libraries/Libraries——先清目标再搬，幂等承诺才算闭环。
  if [[ -d "$vdir/Libraries" ]]; then
    [[ -e "$vdir/Resources/Libraries" ]] && rm -rf "$vdir/Resources/Libraries"
    mv "$vdir/Libraries" "$vdir/Resources/Libraries"
  fi

  # e) Helpers/ 裸 Mach-O 件 → Resources/Libraries/bin/
  if [[ -d "$vdir/Helpers" ]]; then
    mkdir -p "$vdir/Resources/Libraries/bin"
    for h in "$vdir/Helpers"/*; do
      [[ -f "$h" ]] || continue        # .app 是目录，跳过
      # 已在 Resources/Libraries/bin 里的（重复执行）跳过；只搬仍在 Helpers 下的裸件
      is_macho "$h" && mv "$h" "$vdir/Resources/Libraries/bin/"
    done
  fi
done < <(find "$CHROME_ROOT" -mindepth 1 -type d -name '*.framework' -print0)
log "归一化完成（幂等）"
