#!/usr/bin/env bash
# =============================================================================
# build-macos-universal.sh —— 产出 arm64 + x86_64 双架构（universal）.app
#
#   为什么需要它：Apple 的 ITMS-91167 拒收「无 arm64 切片」的构建，而随包资源
#   （便携 Python / nginx / localapi / ffmpeg / uv）全是单架构预编译件——只编
#   Rust 侧是不够的，整棵 Mach-O 树都得双架构才算真的 universal。
#
#   做法（串行，不能并行）：
#     1) prepare-resources.sh mac-arm64  → cargo tauri build --target aarch64
#        → 把整棵 .app 快照到暂存目录（快照后允许清 resources 释放磁盘）
#     2) prepare-resources.sh mac-x64    → cargo tauri build --target x86_64
#        → 再快照一次
#     3) 以 arm64 树为基底逐个文件合并：凡是 Mach-O，就用 x86_64 树的同名切片
#        lipo -create 补进去；非 Mach-O（.py/.js/.json/图片）架构无关，留基底那份。
#     4) 全量校验：App 内每个 Mach-O 都必须同时含 arm64 与 x86_64，否则不出货。
#
#   为什么第 2 步会破坏第 1 步的输入：prepare-resources.sh 开头就
#   `rm -rf resources/{bin,python,app}`（见该脚本第 53 行），共享目录只保留最后一次
#   准备的架构。所以两棵树的 .app 必须在各自构建完、下一次准备前**快照出去**，
#   两条 prepare 也必须严格串行。
#
#   依赖版本必须两棵树一致（否则合并出「.py 是 A 版、.so 是 B 版」的必崩包）：
#   mac-x64 会把 lancedb / cryptography 钉到仍发 Intel 轮的老版本，arm 树默认不钉。
#   本脚本因此默认导出 FORCE_X64_COMPAT_PINS=1，让 arm 侧也走同一套钉版，
#   合并前还会用 dist-info 清单硬比对一次（不一致直接拒）。
#
#   用法：
#     ./build-macos-universal.sh                      # 全流程：备料 → 双构建 → 合并
#     ARM_APP=.../aarch64-.../macos/*.app \
#     X64_APP=.../x86_64-.../macos/*.app  ./build-macos-universal.sh   # 只合并已有产物
#
#   环境变量：
#     SKIP_PREPARE        0|arm|x64|both，跳过对应架构的 prepare-resources.sh
#                         （资源已就位、只改 Rust 代码时用）
#     ARM_APP/X64_APP     直接指定两棵源 .app，置齐则完全跳过构建
#     UNIVERSAL_MERGE_CHROME  默认 0：不合 Chrome for Testing 子树。
#                         ★ 要出双架构 **dmg**（直分发 Track A）必须置 1：Chrome 装在
#                         resources/bin/chrome，Intel 用户拿到 arm64-only 的 Chrome 会
#                         「装得上、一进逐帧渲染就崩」。只做 MAS 链可以置 0——后面
#                         build-macos-mas-app.sh 本来就要剥掉整棵 Chrome，合并只是白烧时间。
#     STRICT_UNIVERSAL   默认 1：每个 Mach-O 必须同时含 arm64 与 x86_64。置 0 只硬拦
#                         缺 arm64（MAS 底线），缺 x86_64 转告警——应急用，产物不是
#                         真双架构、Intel 机不可用。
#                         注意：「设计上就单架构」的那批（各架构 wheel 自带的不同版本
#                         依赖、overlay 副本）不受这个开关影响，它们由随包清单
#                         universal-per-arch.txt 登记放行（详见 3b 段）。
#     NEED_FREE_GB        磁盘余量下限，默认 14（两棵 1.5 GiB 级 .app + 合并输出）
#     DROP_RES_AFTER_STAGE 默认 1：每次快照后清掉 resources/{bin,python,app}
#                         （下一次 prepare-resources 本来就会 rm -rf 它们）
#     KEEP_STAGE          默认 0：成功后删暂存快照
#
#   产物：src-tauri/target/universal2-apple-darwin/release/bundle/macos/*.app
#         目录名沿用 Rust 的 universal target 命名，但产物由本脚本 lipo 合并而来，
#         不是 cargo 直接编出来的（cargo 的 universal2 只管 Rust 侧，管不到随包资源）。
#
#   下一步——两条链共用这一颗合并产物：
#     Track B（Mac App Store）：
#       NO_BUILD=1 ./build-macos-mas-app.sh universal      # 剥嵌套 .app + 清隔离属性
#       PROFILE=<.provisionprofile> ./macos-pkg-appstore.sh <macos-mas/*.app>
#       ./scripts/verify-release-pkg.sh <输出的 .pkg>
#     Track A（官网 / GitHub 直分发，需 UNIVERSAL_MERGE_CHROME=1）：
#       NOTARIZE=1 NOTARY_PROFILE=<凭据名> SIGN_IDENTITY="Developer ID Application: …" \\
#         ./mac-sign-notarize.sh <本脚本产物 .app>          # 它从任意 .app 出 dmg
#       ./scripts/verify-release-dmg.sh <输出的 .dmg>
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RES="$DESKTOP_DIR/src-tauri/resources"
TARGET_ROOT="$DESKTOP_DIR/src-tauri/target"
ARM_TRIPLE="aarch64-apple-darwin"
X64_TRIPLE="x86_64-apple-darwin"
OUT_TRIPLE="universal2-apple-darwin"
STAGE="${STAGE:-$TARGET_ROOT/.universal-stage}"
OUT_DIR="$TARGET_ROOT/$OUT_TRIPLE/release/bundle/macos"

SKIP_PREPARE="${SKIP_PREPARE:-0}"
UNIVERSAL_MERGE_CHROME="${UNIVERSAL_MERGE_CHROME:-0}"
STRICT_UNIVERSAL="${STRICT_UNIVERSAL:-1}"
NEED_FREE_GB="${NEED_FREE_GB:-14}"
DROP_RES_AFTER_STAGE="${DROP_RES_AFTER_STAGE:-1}"
KEEP_STAGE="${KEEP_STAGE:-0}"
# arm 侧也套用 x64 兼容钉版，保证两棵树的 Python 依赖版本完全一致（可显式置 0 关闭，
# 但关闭后下面的 dist-info 比对大概率会拦下来）。
export FORCE_X64_COMPAT_PINS="${FORCE_X64_COMPAT_PINS:-1}"

# 进度一律走 stderr：stage_one 在命令替换里取返回的 App 路径，写进 stdout 会混进路径。
log() { printf '\n\033[1;36m== %s\033[0m\n' "$*" >&2; }
die() { echo "$*" >&2; exit "${2:-2}"; }
is_macho() { file -b "$1" 2>/dev/null | grep -q 'Mach-O'; }
# 架构串匹配：arm64e 之类派生名也算含 arm64（grep -w 在这一条上不可靠）。
has_arch() {  # $1=lipo 架构串 $2=目标架构
  case "$2" in
    arm64) grep -qE '(^|[[:space:]])arm64([[:alnum:].]*|$)' <<<"$1" ;;
    *)     grep -qE "(^|[[:space:]])$2([[:space:]]|$)" <<<"$1" ;;
  esac
}
locate_app() {  # $1=triple → 打印该 triple 下 bundle/macos 里的 .app 路径
  local d="$TARGET_ROOT/$1/release/bundle/macos" a
  [[ -d "$d" ]] || return 1
  a="$(find "$d" -maxdepth 1 -type d -name '*.app' -print -quit)"
  [[ -n "$a" ]] || return 1
  printf '%s\n' "$a"
}

# ------------------------------------------------ 0) 预检
for T in lipo ditto file find stat; do
  command -v "$T" >/dev/null 2>&1 || die "缺少命令：$T" 3
done
[[ -f "$RES/../tauri.conf.json" ]] || die "desktop 目录结构不符合预期：$DESKTOP_DIR" 3

if [[ -z "${ARM_APP:-}" || -z "${X64_APP:-}" ]]; then
  command -v cargo >/dev/null 2>&1 || die "缺少 cargo（rustup 未装？）；或改用 ARM_APP/X64_APP 只跑合并" 3
  cargo tauri --version >/dev/null 2>&1 || die "缺少 tauri-cli：cargo install tauri-cli --version \"^2\"" 3
fi

# 磁盘闸门：双架构 + 合并输出要同时放下三棵 1.5 GiB 级的 .app，历史事故是 x64 编完
# 只剩 ~3 GB、后续步骤直接写不下。宁可提前拦，也不要半截产物。
AVAIL_GB="$(df -k "$DESKTOP_DIR" | awk 'NR==2 {print int($4/1024/1024)}')"
if [[ "${AVAIL_GB:-0}" -lt "$NEED_FREE_GB" ]]; then
  die "磁盘余量 ${AVAIL_GB} GB < 下限 ${NEED_FREE_GB} GB。请先清理：
  · rm -rf $TARGET_ROOT/$ARM_TRIPLE/release/bundle/dmg（历史 dmg 可重生）
  · rm -rf $TARGET_ROOT/{aarch64,x86_64}-apple-darwin/release/resources（tauri 暂存副本）
  · 或降低 NEED_FREE_GB（自担写半截的风险）" 12
fi
log "0/4 预检通过（磁盘余量 ${AVAIL_GB} GB，Chrome 合并=${UNIVERSAL_MERGE_CHROME}，arm 侧钉版兼容=${FORCE_X64_COMPAT_PINS}）"

# ------------------------------------------------ 1) 两棵源树
stage_one() {  # $1=arm|x64 $2=triple $3=res-target；打印值只有快照路径一行
  local kind="$1" triple="$2" res_target="$3" src_app out
  case "$SKIP_PREPARE" in
    both) log "跳过 prepare-resources（${kind}，SKIP_PREPARE=both）" ;;
    "$kind") log "跳过 prepare-resources（${kind}）" ;;
    *)
      [[ -x "$SCRIPT_DIR/prepare-resources.sh" || -f "$SCRIPT_DIR/prepare-resources.sh" ]] \
        || die "缺少 prepare-resources.sh：$SCRIPT_DIR" 3
      log "prepare-resources.sh ${res_target}（会清掉上一架构的 resources/{bin,python,app}）"
      # 备料输出必须落文件：stage_one 的 stdout 是「只输出一行快照路径」的协议通道，
      # 混进日志会让 ARM_TREE 变成整段文本；更要命的是 prepare 失败时退出码在命令
      # 替换子 shell 里没能终止父脚本，真机带着一棵空 resources/bin 继续跑 cargo，
      # 十几分钟后只换来一句 “glob pattern resources/bin/* path not found”。
      local plog="${PREPARE_LOG_DIR:-/tmp}/dramaclaw-prepare-${res_target}.log"
      if ! bash "$SCRIPT_DIR/prepare-resources.sh" "$res_target" >"$plog" 2>&1; then
        tail -30 "$plog" >&2 || true
        die "prepare-resources.sh ${res_target} 失败（完整日志：$plog）" 3
      fi
      log "备料完成，日志：$plog"
      ;;
  esac
  # 备料产物闸门：bin 空 = 随附二进制（nginx/ffmpeg/localapi/chrome）一个都没落地，
  # 再往下的 cargo 只会拿一个打不出包的残树。
  [[ -n "$(ls -A "$RES/bin" 2>/dev/null)" ]] \
    || die "备料残树：$RES/bin 为空（python 依赖或二进制步骤中断），修好 prepare-resources 再重跑" 3
  cd "$DESKTOP_DIR" || die "无法进入 $DESKTOP_DIR" 3
  log "cargo tauri build --target $triple --bundles app"
  cargo tauri build --target "$triple" --bundles app
  src_app="$(locate_app "$triple")" || die "构建后找不到 .app：$TARGET_ROOT/$triple/release/bundle/macos/" 5
  rm -rf "$STAGE/$kind"
  mkdir -p "$STAGE/$kind"
  out="$STAGE/$kind/$(basename "$src_app")"
  log "快照 $src_app → $out"
  ditto "$src_app" "$out"
  if [[ "$DROP_RES_AFTER_STAGE" == "1" ]]; then
    log "释放磁盘：清理 resources/{bin,python,app}（下一次 prepare 本就会重建）"
    rm -rf "$RES/bin" "$RES/python" "$RES/app"
  fi
  printf '%s\n' "$out"
}

if [[ -n "${ARM_APP:-}" && -n "${X64_APP:-}" ]]; then
  [[ -d "$ARM_APP" && -d "$X64_APP" ]] || die "ARM_APP / X64_APP 指向的目录不存在" 5
  ARM_TREE="$ARM_APP"; X64_TREE="$X64_APP"
  log "1/4 只合并模式：沿用已有两棵产物（不重新构建）"
else
  log "1/4 串行构建两棵单架构产物（arm64 → x64）"
  ARM_TREE="$(stage_one arm "$ARM_TRIPLE" mac-arm64)"
  X64_TREE="$(stage_one x64 "$X64_TRIPLE" mac-x64)"
fi
[[ "$ARM_TREE" == "$X64_TREE" ]] && die "两棵源树指向同一个目录，合并没有意义" 5
BASE_NAME="$(basename "$ARM_TREE")"
[[ "$(basename "$X64_TREE")" == "$BASE_NAME" ]] \
  || die "两棵产物 App 名不一致：${BASE_NAME} vs $(basename "$X64_TREE")" 5

# ------------------------------------------------ 2) 依赖版本一致性硬比对
# 合并按「同名文件」配对：.py 来自基底树、.so 却是另一棵树的版本，装上就 import 崩。
# 因此先比对便携 Python 的 dist-info 清单与解释器版本目录，任何漂移都在合并前拦掉。
log "2/4 校验两棵树的 Python 依赖版本集合一致"
site_list() {  # $1=app 根 → 打印排序后的 dist-info / 解释器版本目录清单
  local root="$1/Contents/Resources/resources"
  {
    find "$root/python/lib" -maxdepth 1 -type d -name 'python3.*' 2>/dev/null | sed "s#$root/##"
    find "$root/python/lib"/*/site-packages -maxdepth 1 -type d -name '*.dist-info' 2>/dev/null | sed "s#$root/##"
    find "$root/hermes/site-packages" -maxdepth 1 -type d -name '*.dist-info' 2>/dev/null | sed "s#$root/##"
  } | LC_ALL=C sort
}
DIFF_TXT="$(diff <(site_list "$ARM_TREE") <(site_list "$X64_TREE") || true)"
if [[ -n "$DIFF_TXT" ]]; then
  echo "两棵树的依赖清单不一致，合并会产出版本错配的包。差异：" >&2
  printf '%s\n' "$DIFF_TXT" | head -40 >&2
  echo "常见原因：arm 侧没套用 x64 兼容钉版。请清掉 SKIP_PREPARE 重跑（本脚本默认已导出" >&2
  echo "FORCE_X64_COMPAT_PINS=1），或确认两次 prepare-resources 用的是同一份锁。" >&2
  exit 13
fi
echo "  ✓ 依赖清单一致（$(site_list "$ARM_TREE" | wc -l | tr -d ' ') 项）"

# ------------------------------------------------ 3) 合并
log "3/4 合并 Mach-O 切片（基底=arm64 树）"
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"
OUT_APP="$OUT_DIR/$BASE_NAME"
# ditto 保权限与符号链接（PBS 的 bin/python3 是指向 python3.11 的软链，derefs 会坏结构）
ditto "$ARM_TREE" "$OUT_APP"

TMPD="$(mktemp -d "${TMPDIR:-/tmp}/univ-merge.XXXXXX")"
trap 'rm -rf "$TMPD"' EXIT

# 遍历基底里的「潜在 Mach-O」：可执行件 + 动态库。清单落临时文件（站点包文件上千，
# 不能用 bash 数组扛）。
CAND="$TMPD/candidates"
( cd "$OUT_APP" && find . -type f \( -perm -u+x -o -name '*.dylib' -o -name '*.so' -o -name '*.so.*' \) -print0 ) >"$CAND"
MERGED="$TMPD/merged"; ALREADY="$TMPD/already"; NO_PARTNER="$TMPD/no_partner"; BAD="$TMPD/bad"
: >"$MERGED"; : >"$ALREADY"; : >"$NO_PARTNER"; : >"$BAD"

while IFS= read -r -d '' rel; do
  # Chrome for Testing 子树默认不参与合并：MAS 链后面整棵剥掉，合并只是耗时。
  if [[ "$UNIVERSAL_MERGE_CHROME" != "1" && "$rel" == */bin/chrome/* ]]; then
    continue
  fi
  base_f="$OUT_APP/$rel"
  alt_f="$X64_TREE/$rel"
  is_macho "$base_f" || continue
  if [[ ! -f "$alt_f" ]]; then
    printf '%s\n' "$rel" >>"$NO_PARTNER"
    continue
  fi
  is_macho "$alt_f" || { printf '%s  （对侧同名文件不是 Mach-O）\n' "$rel" >>"$BAD"; continue; }
  b_archs="$(lipo -archs "$base_f" 2>/dev/null || true)"
  a_archs="$(lipo -archs "$alt_f" 2>/dev/null || true)"
  [[ -n "$b_archs" && -n "$a_archs" ]] \
    || { printf '%s  （lipo 读不到架构：base=%s alt=%s）\n' "$rel" "${b_archs:-?}" "${a_archs:-?}" >>"$BAD"; continue; }
  if has_arch "$b_archs" x86_64; then
    printf '%s\n' "$b_archs" >>"$ALREADY"
    continue
  fi
  if ! has_arch "$a_archs" x86_64; then
    printf '%s  （对侧树不含 x86_64 切片：实为 %s——x64 那棵树是不是拿错了架构？）\n' "$rel" "$a_archs" >>"$BAD"
    continue
  fi
  # 只补 alt 的 x86_64 切片：两棵树可能都带 arm64（比如 alt 已是 universal），直接
  # lipo -create 会撞「arch is duplicated」。先 -extract 出缺的那个架构再合并。
  new_slice="$TMPD/x86_64.thin"
  out_bin="$TMPD/fat.bin"
  rm -f "$new_slice" "$out_bin"
  if ! lipo -extract x86_64 "$alt_f" -output "$new_slice" 2>/dev/null; then
    printf '%s  （lipo -extract x86_64 失败）\n' "$rel" >>"$BAD"; continue
  fi
  if ! lipo -create -output "$out_bin" "$base_f" "$new_slice" 2>/dev/null; then
    printf '%s  （lipo -create 失败：%s + x86_64）\n' "$rel" "$b_archs" >>"$BAD"; continue
  fi
  # 原位覆写：cat > 而不是 mv，保住原 inode 的权限与属主（nginx/ffmpeg 常是 0755/0555）。
  mode="$(stat -f '%Lp' "$base_f" 2>/dev/null || echo 0755)"
  chmod u+w "$base_f" 2>/dev/null || true
  cat "$out_bin" >"$base_f"
  chmod "$mode" "$base_f"
  printf '%s\n' "$rel" >>"$MERGED"
done <"$CAND"

N_MERGED="$(wc -l <"$MERGED" | tr -d ' ')"
N_ALREADY="$(wc -l <"$ALREADY" | tr -d ' ')"
N_NO_PARTNER="$(wc -l <"$NO_PARTNER" | tr -d ' ')"
N_BAD="$(wc -l <"$BAD" | tr -d ' ')"
echo "  合并补片 ${N_MERGED} 个 · 本就双架构 ${N_ALREADY} 个 · 无对侧同名件 ${N_NO_PARTNER} 个 · 异常 ${N_BAD} 个"

if [[ "$N_BAD" != "0" ]]; then
  echo "合并异常（逐条修掉再出货）：" >&2
  head -30 "$BAD" >&2
  die "共 ${N_BAD} 个文件无法安全合并" 14
fi

# ------------------------------------------------ 3b) 反向缺口分流：union 或 per-arch overlay
# 只在 x64 树里存在的 Mach-O。2026-11 在本项目真机上实测过两棵 3.8 GB 的树，缺口
# 全部来自「同版本 wheel 的平台文件布局差异」（前置 dist-info 比对已证明两棵树依赖
# 版本完全一致，所以不是版本漂移），且分两类——处置方式相反，混在一起处理必出错：
#
#   ① 可并存（additive）：基底同目录里**没有暴露同一个模块名**的另一份。
#      典型是 wheel 自带的不同版本依赖（cv2/.dylibs/libnettle.8.10 vs 8.4）和架构专属
#      可选加速件（Crypto/Cipher/_raw_aesni.abi3.so —— arm 轮压根不发这个名字，
#      pycryptodome 是 try/except 加载，缺了只是 Intel 上 AES 慢一点）。这类文件按原样
#      补进去就是对的：每个架构的主件按自己写死的 install name 找自己那份，互不干扰。
#
#   ② 模块名冲突（collision）：同目录存在「模块名相同、后缀不同」的另一份。典型是 lupa：
#      arm64 轮发 lua54.cpython-312-darwin.so，x86_64 轮发 lua54.abi3.so。两个文件都暴露
#      lupa.lua54，而 CPython 只按 EXTENSION_SUFFIXES 优先级取第一个（cpython-312 排在
#      abi3 前），Intel 上 dlopen 报 wrong architecture **不会**回退下一个后缀 → union
#      等于把 Intel 侧 import 打死。正解是让每个架构只看见自己那份：把整个包目录挪到
#      site-packages 同级的 overlays/<arch>/<pkg>，共享 site-packages 里两份都不留（不留
#      就没有选错的余地，也不依赖 sys.path 顺序）；再由一个 .pth 按 platform.machine()
#      把对应 overlay 插进 sys.path。
#      .pth 由 CPython 的 site 初始化执行（随包解释器实测 sys.flags.no_site==0），所以这
#      套分流是**纯随包数据**，Rust 侧一行不用改：per-arch 的包里既没有 overlays/ 也没有
#      这个 .pth，dmg 那条链的产物行为与合并前完全一致。
ONLY_X64="$TMPD/only_x64"
( cd "$X64_TREE" && find . -type f \( -perm -u+x -o -name '*.dylib' -o -name '*.so' -o -name '*.so.*' \) -print0 ) >"$TMPD/x64_cand"
: >"$ONLY_X64"
while IFS= read -r -d '' rel; do
  [[ "$UNIVERSAL_MERGE_CHROME" != "1" && "$rel" == */bin/chrome/* ]] && continue
  is_macho "$X64_TREE/$rel" || continue
  [[ -e "$OUT_APP/$rel" ]] || printf '%s\n' "$rel" >>"$ONLY_X64"
done <"$TMPD/x64_cand"
N_ONLY_X64="$(wc -l <"$ONLY_X64" | tr -d ' ')"

# 模块名本体：去掉 .so/.dylib 尾巴与 abi3 / cpython-XY 中缀，只留 import 用的名字。
#   lua54.cpython-312-darwin.so → lua54      _raw_aesni.abi3.so → _raw_aesni
mod_name() { local n="$1"; n="${n%.dylib}"; n="${n%.so}"; case "$n" in *.abi3) n="${n%.abi3}";; esac; printf '%s\n' "${n%%.cpython-*}"; }

ADDITIVE="$TMPD/x64_additive"; COLLIDE="$TMPD/x64_collide"; COLL_PKG="$TMPD/coll_pkg"
: >"$ADDITIVE"; : >"$COLLIDE"; : >"$COLL_PKG"
while IFS= read -r rel; do
  f="${rel##*/}"; d="${rel%/*}"
  # 只对扩展模块（.so）判冲突：dylib 走 install name 精确引用，不同文件名天然不冲突。
  clash=0
  case "$f" in *.so)
    stem="$(mod_name "$f")"
    while IFS= read -r bf; do
      [[ -n "$bf" ]] || continue
      [[ "${bf##*/}" == "$f" ]] && continue
      [[ "$(mod_name "${bf##*/}")" == "$stem" ]] && { clash=1; break; }
    done < <(cd "$ARM_TREE/$d" 2>/dev/null && ls *.so 2>/dev/null)
    ;;
  esac
  if [[ "$clash" == "1" ]]; then
    printf '%s\n' "$rel" >>"$COLLIDE"
    # overlay 的落点按「site-packages 的父目录 + 包名」定位。只认随包 Python 那套布局：
    # hermes 的 site-packages 是走 PYTHONPATH 挂进来的，PYTHONPATH 目录**不处理 .pth**，
    # 真在那儿出现冲突就不能用这套机制，必须当场拒绝而不是猜一个路径糊过去。
    case "$rel" in
      */resources/python/lib/*/site-packages/*)
        parent="${rel%%site-packages/*}"
        sub="${rel#*site-packages/}"; pkg="${sub%%/*}" ;;
      */hermes/site-packages/*)
        die "模块名冲突出现在 hermes/site-packages（${rel}）：PYTHONPATH 挂进来的目录不会处理 .pth，overlay 方案在这里无效。请改用按架构分装两套 hermes 依赖或去掉冲突包。" 16 ;;
      *)
        die "模块名冲突出现在无法识别的布局（${rel}）：不是随包 Python 的 site-packages。请人工确认该资源的按架构布局再扩这个脚本。" 16 ;;
    esac
    printf '%s|%s\n' "$parent" "$pkg" >>"$COLL_PKG"
  else
    printf '%s\n' "$rel" >>"$ADDITIVE"
  fi
done <"$ONLY_X64"

ALLOW_SINGLE="$TMPD/per_arch.allow"; : >"$ALLOW_SINGLE"

# ② 冲突包 → 每个架构一份完整副本放进 overlay，共享 site-packages 里删干净
N_OVL_PKG=0
if [[ -s "$COLL_PKG" ]]; then
  log "3b-i 模块名冲突包转 per-arch overlay（$(sort -u "$COLL_PKG" | wc -l | tr -d ' ') 个包）"
  while IFS='|' read -r parent pkg; do
    [[ -n "$pkg" ]] || continue
    sp="$OUT_APP/${parent}site-packages"
    [[ -d "$sp/$pkg" ]] || { printf 'overlay 基底缺包：%s%s\n' "$parent" "$pkg" >&2; die "分流失败" 16; }
    [[ -d "$X64_TREE/${parent}site-packages/$pkg" ]] \
      || { printf 'overlay 对侧缺包：%s%s\n' "$parent" "$pkg" >&2; die "分流失败" 16; }
    mkdir -p "$OUT_APP/${parent}overlays"
    rm -rf "$OUT_APP/${parent}overlays/arm64/$pkg" "$OUT_APP/${parent}overlays/x86_64/$pkg"
    ditto "$sp/$pkg" "$OUT_APP/${parent}overlays/arm64/$pkg"
    ditto "$X64_TREE/${parent}site-packages/$pkg" "$OUT_APP/${parent}overlays/x86_64/$pkg"
    rm -rf "$sp/$pkg"
    printf '  ✓ %s → overlays/{arm64,x86_64}/%s\n' "$parent" "$pkg" >&2
    N_OVL_PKG=$((N_OVL_PKG + 1))
  done < <(LC_ALL=C sort -u "$COLL_PKG")
  # 每个受影响的 site-packages 落一个 .pth。写死 sys.prefix/lib/python3.N 是因为这就是
  # 上面 case 已经校验过的随包 Python 布局；overlay 目录不存在时整句是空操作。
  while IFS= read -r parent; do
    [[ -n "$parent" ]] || continue
    cat >"$OUT_APP/${parent}site-packages/_dramaclaw_arch_overlay.pth" <<'PTH'
import os, platform, sys; _od = os.path.join(sys.prefix, "lib", "python3.%d" % sys.version_info[1], "overlays", platform.machine()); sys.path.insert(0, _od) if os.path.isdir(_od) else None
PTH
    printf '  ✓ 写入 %ssite-packages/_dramaclaw_arch_overlay.pth\n' "$parent" >&2
  done < <(LC_ALL=C cut -d'|' -f1 "$COLL_PKG" | LC_ALL=C sort -u)
fi

# ① 可并存的 x64 独有件 → 原样补进包里
N_ADD=0
if [[ -s "$ADDITIVE" ]]; then
  while IFS= read -r rel; do
    mkdir -p "$OUT_APP/${rel%/*}"
    mode="$(stat -f '%Lp' "$X64_TREE/$rel" 2>/dev/null || echo 0755)"
    ditto "$X64_TREE/$rel" "$OUT_APP/$rel"
    chmod "$mode" "$OUT_APP/$rel"
    printf '%s\n' "$rel" >>"$ALLOW_SINGLE"
    N_ADD=$((N_ADD + 1))
  done <"$ADDITIVE"
fi

# 基底独有的 Mach-O（上一步记在 NO_PARTNER）是对称的可并存件，同样登记进清单；
# 但它们里凡是属于冲突包的，上面已经整包挪进 overlay，旧路径不复存在。
if [[ -s "$NO_PARTNER" ]]; then
  while IFS= read -r rel; do printf '%s\n' "$rel" >>"$ALLOW_SINGLE"; done <"$NO_PARTNER"
fi
# overlay 里的两份副本天生各自单架构，全部登记进清单。
# 父目录从冲突清单里推，不写死 python3.12：写死后一旦解释器升版就会静默漏登记，
# 而那些件确实单架构 → 下游闸门反而会把合法包判成不合规。
if [[ -s "$COLL_PKG" ]]; then
  while IFS= read -r parent; do
    [[ -n "$parent" ]] || continue
    ( cd "$OUT_APP" && find "${parent}overlays" -type f \( -perm -u+x -o -name '*.dylib' -o -name '*.so' \) -print 2>/dev/null ) \
      >>"$ALLOW_SINGLE"
  done < <(LC_ALL=C cut -d'|' -f1 "$COLL_PKG" | LC_ALL=C sort -u)
fi
N_COLLIDE="$(wc -l <"$COLLIDE" | tr -d ' ')"
echo "  反向缺口 ${N_ONLY_X64} 个：可并存补入 ${N_ADD} 个 · 模块名冲突转 overlay ${N_COLLIDE} 个（${N_OVL_PKG} 个包）"
if [[ -s "$COLLIDE" ]]; then
  echo "  转为 overlay 的冲突文件：" >&2; LC_ALL=C sort "$COLLIDE" | sed 's#^#    #' >&2
fi

# ------------------------------------------------ 4) 全量双架构校验
# STRICT_UNIVERSAL=1（默认）：任何 Mach-O 缺 arm64 或缺 x86_64 都不出货。
# STRICT_UNIVERSAL=0：只硬拦缺 arm64（MAS 上传的底线），缺 x86_64 降级为告警——
# 适用于“有个别资源拿不到 x86_64 件、但先要一个能过 ITMS-91167 的包”的应急场景，
# 代价是 Intel 机跑到那些件会 Bad CPU type，不是真正的 universal。
if [[ "$STRICT_UNIVERSAL" == "1" ]]; then
  log "4/4 校验产物：每个 Mach-O 都必须同时含 arm64 与 x86_64"
else
  log "4/4 校验产物（STRICT_UNIVERSAL=0）：硬拦缺 arm64，缺 x86_64 仅告警"
fi
NOT_UNIV="$TMPD/not_universal"
LACK_X64="$TMPD/lack_x86_64"
PERARCH="$TMPD/per_arch_seen"
PERARCH_RAW="$TMPD/per_arch_raw"
: >"$NOT_UNIV"; : >"$LACK_X64"; : >"$PERARCH"; : >"$PERARCH_RAW"
while IFS= read -r -d '' f; do
  [[ "$UNIVERSAL_MERGE_CHROME" != "1" && "$f" == */bin/chrome/* ]] && continue
  is_macho "$f" || continue
  archs="$(lipo -archs "$f" 2>/dev/null || true)"
  [[ -n "$archs" ]] || continue
  # 展示用相对路径：这个 find 是从 OUT_APP 绝对路径出发的，不能再拼一次前缀。
  rel="${f#"$OUT_APP"/}"
  if has_arch "$archs" arm64 && has_arch "$archs" x86_64; then continue; fi
  # 不双切片的件先查按架构清单。ALLOW_SINGLE 是上一步从两棵树差集里机械生成的，代表
  # 「设计上就单架构」的那批：不同版本 wheel 自带的依赖（各架构主件按自己写死的 install
  # name 引用）、架构专属的可选加速件、以及 overlay 里的按架构副本。这批件不可能也不该
  # 被刷成双架构，硬拦只会逼人关掉闸门；清单外的单架构件仍然一律拒绝。
  if grep -Fxq "./$rel" "$ALLOW_SINGLE" 2>/dev/null; then
    printf '%s  （%s）\n' "$rel" "$archs" >>"$PERARCH"
    printf '%s\t%s\n' "$rel" "$archs" >>"$PERARCH_RAW"
    continue
  fi
  if ! has_arch "$archs" arm64; then
    printf '%s  （%s）—— 缺 arm64，ITMS-91167 会直接拒\n' "$rel" "$archs" >>"$NOT_UNIV"
  elif ! has_arch "$archs" x86_64; then
    if [[ "$STRICT_UNIVERSAL" == "1" ]]; then
      printf '%s  （%s）—— 缺 x86_64\n' "$rel" "$archs" >>"$NOT_UNIV"
    else
      printf '%s  （%s）\n' "$rel" "$archs" >>"$LACK_X64"
    fi
  fi
done < <(find "$OUT_APP" -type f \( -perm -u+x -o -name '*.dylib' -o -name '*.so' -o -name '*.so.*' \) -print0)
if [[ -s "$NOT_UNIV" ]]; then
  echo "以下件不满足架构要求（合并没覆盖到，或基底本来就是不合规单架构件）：" >&2
  head -30 "$NOT_UNIV" >&2
  echo "如果是已知拿不到 x86_64 件个例、先要一个可上传 MAS 的包：STRICT_UNIVERSAL=0 重跑（Intel 不可用）。" >&2
  die "共 $(wc -l <"$NOT_UNIV" | tr -d ' ') 个文件不合规" 15
fi
if [[ -s "$LACK_X64" ]]; then
  echo "  ⚠ $(wc -l <"$LACK_X64" | tr -d ' ') 个件缺 x86_64（STRICT_UNIVERSAL=0 已放行）：Intel 机上不可用" >&2
  head -10 "$LACK_X64" >&2
fi
if [[ -s "$PERARCH" ]]; then
  echo "  ✓ 按架构清单内单架构件 $(wc -l <"$PERARCH" | tr -d ' ') 个（各架构各自引用，设计如此）"
  # 把清单随包落盘：下游闸门（macos-pkg-appstore.sh 的全树 arm64 核验）靠它区分
  # 「设计上就单架构」与「资源拿错了架构」。没有这份清单，要么闸门把合法包误拒，
  # 要么逼人关闸——两者都会让这道校验失去意义。清单由本步从两棵树差集机械生成，
  # 不是人工维护的豁免表。
  MANIFEST="$OUT_APP/Contents/Resources/resources/universal-per-arch.txt"
  {
    echo "# universal-per-arch.txt —— 由 build-macos-universal.sh 生成，随包供下游闸门核对"
    echo "#"
    echo "# 下面这些 Mach-O 有意只含一个架构：universal 包的基底是两棵单架构树 lipo 合并而来，"
    echo "# 而同一个版本的 wheel 在不同平台下会带不同版本的依赖库（cv2/.dylibs/libnettle.8.10 vs"
    echo "# 8.4）、架构专属的可选加速件（Crypto/Cipher/_raw_aesni —— arm 轮不发这个名字），以及"
    echo "# overlays/<arch>/ 里按架构分流的副本。这些件不可能也不该刷成双架构：每个架构的主件按"
    echo "# 自己写死的 install name 引用自己那份，另一份对它既不可读也不该被加载。"
    echo "#"
    echo "# 每行：<相对 .app 根的路径>\t<实测架构>。闸门只放行列在这里的单架构件。"
    cat "$PERARCH_RAW"
  } >"$MANIFEST"
  echo "    清单已随包写入：Contents/Resources/resources/universal-per-arch.txt（$(wc -l <"$MANIFEST" | tr -d ' ') 行）"
fi

MAIN="$(find "$OUT_APP/Contents/MacOS" -maxdepth 1 -type f -perm -u+x 2>/dev/null | head -1)"
[[ -n "$MAIN" ]] || die "合并后找不到主执行件：$OUT_APP/Contents/MacOS" 15
MAIN_ARCHS="$(lipo -archs "$MAIN")"
has_arch "$MAIN_ARCHS" arm64 || die "主执行件缺 arm64：$MAIN_ARCHS" 15
if has_arch "$MAIN_ARCHS" x86_64; then
  echo "  ✓ 主执行件：${MAIN_ARCHS}"
else
  echo "  ⚠ 主执行件只有 ${MAIN_ARCHS}（不是双架构）" >&2
fi
echo "  ✓ 架构校验通过（Chrome 子树按开关 ${UNIVERSAL_MERGE_CHROME} 处理）"

# 合并过程中写入了新内容，扩展属性需要重清一次（pkg 脚本签前还会再兜一道）
xattr -cr "$OUT_APP" 2>/dev/null || true

if [[ "$KEEP_STAGE" != "1" && -z "${ARM_APP:-}" && -z "${X64_APP:-}" ]]; then
  log "清理暂存快照：$STAGE"
  rm -rf "$STAGE"
fi

cat <<EOF

$(printf '\033[1;32m✔ universal .app 已就绪\033[0m')
  产物：$OUT_APP
  架构：arm64 + x86_64（合并补片 ${N_MERGED} 个 Mach-O）
  大小：$(du -sh "$OUT_APP" | awk '{print $1}')

下一步 —— Mac App Store 上架链（Track B）：
  # 1) MAS 化裁剪（剥嵌套 .app / Chrome）+ 清隔离属性
  NO_BUILD=1 ./scripts/build-macos-mas-app.sh universal
  # 2) 注入 profile、全量重签、出 pkg
  PROFILE=<你的 Mac App Store 类型 .provisionprofile> \\
    ./scripts/macos-pkg-appstore.sh "$OUT_DIR/../macos-mas/$BASE_NAME"
  # 3) 出厂终检
  ./scripts/verify-release-pkg.sh <上一步输出的 .pkg>

下一步 —— 官网 / GitHub 直分发（Track A，用同一颗合并产物，不重编）：
  # 前置：本次必须用 UNIVERSAL_MERGE_CHROME=1 跑，否则包内 Chrome 仍是 arm64-only，
  #        Intel 用户一进逐帧渲染就崩。
  NOTARIZE=1 NOTARY_PROFILE=<凭据名> \\
    ./scripts/mac-sign-notarize.sh "$OUT_APP"
  ./scripts/verify-release-dmg.sh <上一步输出的 .dmg>

  说明：pkg 脚本会从内向外重签 App 内全部 Mach-O，合并产生的新切片布局在这一步
  统一重新密封，不需要单独签合并结果。
EOF
