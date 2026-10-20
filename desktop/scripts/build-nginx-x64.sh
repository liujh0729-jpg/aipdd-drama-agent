#!/usr/bin/env bash
# =============================================================================
# build-nginx-x64.sh —— 在 macOS 构建机（Apple Silicon 或 Intel 皆可）交叉编译一个
# 「最小、自包含、纯 HTTP」的 x86_64 nginx，供 mac-x64 dmg 随包。
#
# 为什么不用预编译 / Homebrew：
#   - 2026 起 Homebrew 已彻底停发 macOS Intel 瓶（formulae API 里 x86_64 只剩 x86_64_linux），
#     在 Apple Silicon 上 `brew install nginx` 只会拿到 arm64，进 Intel 包必 Bad CPU type。
#   - 无任何可信的 x86_64 macOS 预编译 nginx 源。
# 为什么最小编译足够且能绕开 OpenSSL：
#   - 桌面 nginx.conf 只用 listen(http)/proxy_pass/map/add_header/upstream keepalive，
#     不含 ssl / http2 / stream / mail。而 ssl/stream/mail 在 nginx 里**默认就不编**
#     （只有 --with 开关、没有 --without），所以“什么都不加”就是纯 HTTP 最小件，
#     从而不需要 x86_64 OpenSSL（那正是 cryptography 源码编译失败的根因）。
#   - PCRE 用 nginx 自带 `--with-pcre=<源码目录>` 静态编入，避免依赖本机(arm64) pcre2。
#   产物只链接 /usr/lib/libz + libSystem（任何 Mac 都自带），自包含、可跨机分发。
#
# 用法： build-nginx-x64.sh [输出路径]      默认 <repo>/desktop/src-tauri/resources/bin/nginx
# 覆盖： NGINX_VER=1.31.6 PCRE_VER=8.45 CACHE_DIR=/tmp/nginx-x64-build
# =============================================================================
set -euo pipefail

[[ "$(uname)" == Darwin ]] || { echo "本脚本只在 macOS 构建机上跑" >&2; exit 2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-$SCRIPT_DIR/../src-tauri/resources/bin/nginx}"
NGINX_VER="${NGINX_VER:-1.31.6}"
PCRE_VER="${PCRE_VER:-8.45}"
CACHE_DIR="${CACHE_DIR:-/tmp/nginx-x64-build}"
ARCH_FLAG="-arch x86_64"

log() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "缺少命令：$1" >&2; exit 3; }; }
for c in clang make tar curl gunzip lipo; do need "$c"; done

mkdir -p "$CACHE_DIR"; cd "$CACHE_DIR"

# 抗 GitHub/SourceForge CDN 呆滞：已下且 tar 校验通过则复用。
dl() {
  local url="$1" out="$2"
  if [[ -s "$out" ]] && tar -tzf "$out" >/dev/null 2>&1; then
    log "复用已下载 $out"; return 0
  fi
  curl -fL --retry 8 --retry-all-errors --retry-delay 3 --speed-limit 2000 --speed-time 60 -o "$out" "$url"
}

log "下载 nginx-$NGINX_VER / pcre-$PCRE_VER 源码"
dl "https://nginx.org/download/nginx-$NGINX_VER.tar.gz" nginx.tar.gz
dl "https://sourceforge.net/projects/pcre/files/pcre/$PCRE_VER/pcre-$PCRE_VER.tar.gz/download" pcre.tar.gz

rm -rf "nginx-$NGINX_VER" "pcre-$PCRE_VER"
tar -xzf nginx.tar.gz
tar -xzf pcre.tar.gz

cd "nginx-$NGINX_VER"
log "configure（交叉到 x86_64，纯 HTTP，静态 PCRE）"
# env 里的 -arch 会透传给 nginx 及其内部 pcre 子 configure；再用 cc/ld-opt 兜底。
export CC="clang $ARCH_FLAG"
export CFLAGS="$ARCH_FLAG"
export LDFLAGS="$ARCH_FLAG"
./configure \
  --prefix="$CACHE_DIR/_install" \
  --with-pcre="$CACHE_DIR/pcre-$PCRE_VER" \
  --with-pcre-jit \
  --with-cc-opt="$ARCH_FLAG" \
  --with-ld-opt="$ARCH_FLAG" \
  >"$CACHE_DIR/configure.log" 2>&1 || { echo "configure 失败"; tail -30 "$CACHE_DIR/configure.log"; exit 1; }

log "make -j"
make -j"$(sysctl -n hw.ncpu)" >>"$CACHE_DIR/make.log" 2>&1 || { echo "make 失败"; tail -40 "$CACHE_DIR/make.log"; exit 1; }

BIN="$PWD/objs/nginx"
# 硬校验：必须是 x86_64（能跑 Intel 包），且不含 arm64 残留。
ARCHS="$(lipo -archs "$BIN" 2>/dev/null || true)"
case "$ARCHS" in
  *x86_64*) : ;;
  *) echo "编译产物架构异常：'$ARCHS'（期望含 x86_64）" >&2; exit 1 ;;
esac
log "产物架构：$ARCHS"
echo "  动态依赖（应仅系统库）:"; otool -L "$BIN" | sed -n '2,20p' | sed 's/^/    /'

log "安装到 $OUT"
mkdir -p "$(dirname "$OUT")"
install -m 0755 "$BIN" "$OUT"
xattr -c "$OUT" 2>/dev/null || true
echo "完成：$(lipo -archs "$OUT")  $OUT"
