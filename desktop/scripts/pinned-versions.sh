#!/usr/bin/env bash
# =============================================================================
# pinned-versions.sh —— 构建期「可下载依赖」的唯一真相。
#
# 本项目的硬约束：除非我们自己改这个文件，任何上游发新版都不该改变产物。
# 所以这里不允许出现 latest / LATEST_* / “按当前 node -v 推断”这类解析：
# 每个下载都必须能写出 (版本, 资产名, sha256) 三元组。
#
# 约定：校验和写官方值；官方确实不公布校验和的（Chrome for Testing 就没有），
# 写 `-` 表示「只钉版本、不做哈希校验」——版本号在 URL 里，内容不会自己漂移，
# 但少了防篡改这一层。新增此类条目请在注释里写清原因。
# =============================================================================

# ---------------------------------------------------------------- 便携 Python
# python-build-standalone：用发布标签 + 具体 patch 版本，不用 releases/latest。
PBS_RELEASE="20261026"
PBS_VERSION="3.12.16"

pbs_spec() {  # $1 = 目标三元组 → "资产名 sha256"
  case "$1" in
    aarch64-apple-darwin)
      echo "cpython-${PBS_VERSION}+${PBS_RELEASE}-aarch64-apple-darwin-install_only.tar.gz fbf4afef53c2a6b72bb741bc06d5671a9dfaf87bd84e5aa73ac9b3d0d589f14e" ;;
    x86_64-apple-darwin)
      echo "cpython-${PBS_VERSION}+${PBS_RELEASE}-x86_64-apple-darwin-install_only.tar.gz a962e47df522889eaa7ff0d6a56203f1079f68aabb65ffdbe8455f362bc769f3" ;;
    x86_64-pc-windows-msvc)
      echo "cpython-${PBS_VERSION}+${PBS_RELEASE}-x86_64-pc-windows-msvc-install_only.tar.gz 590dfcbe3a34c07301e6332e13e2188b98655cd072b08afa45a7998dc4c22bc5" ;;
    *) return 1 ;;
  esac
}

# ---------------------------------------------------------------------- nginx
NGINX_VER="1.27.0"
NGINX_WIN_SHA256="e5f460166b56414c21ac03138a8b5ab824254004194f6ff0f9f8f3297a2a5773"

# --------------------------------------------------------------------- ffmpeg
# Windows：BtbN 的 *日期标签*（不是 latest —— latest 的内容每天都会变）。
# 资产名带 ffmpeg 自身的 git 判定，故版本与资产名一起换。
FFMPEG_WIN_TAG="autobuild-2026-10-21-13-06"
FFMPEG_WIN_ASSET="ffmpeg-N-127900-ge2798d5cad-win64-lgpl.zip"
FFMPEG_WIN_SHA256="c8e6fdd8005534b25360029072a84b9d148a7e7c85413f61e93c5c3b6d0f1950"
# macOS Intel：eugeneware/ffmpeg-static 的发布标签（不可变）+ npm 的 ffprobe-static。
FFMPEG_X64_RELEASE="b6.1.1"
# 该 release 没有官方校验和文件；标签不可变，此处只钉版本。
FFMPEG_X64_SHA256="-"
FFPROBE_STATIC_VER="3.1.0"
FFPROBE_STATIC_SHA256="0193bc86ec086edcb7c3198fe8aca944e4a5d748f63d2d1b4603b26892ebb628"

# ------------------------------------------------------------------ Chromium
# Chrome for Testing：Google 不发校验和文件，只按版本号寻址（版本目录不可变）。
CFT_VER="156.0.8078.47"

# ------------------------------------------------------------------------ uv
UV_VER="0.10.9"
uv_spec() {  # $1 = 目标三元组 → "校验和"（zip / tar.gz 两个包装都同源同版本）
  case "$1" in
    x86_64-pc-windows-msvc) echo "f58dc40896000229db7c52b8bdd931394040ef2ad59abd1eda841f6d70b13d7a" ;;
    aarch64-apple-darwin)   echo "a92f61e9ac9b0f29668c15f56152e4a60143fca148ff5bfadb86718472c3f376" ;;
    x86_64-apple-darwin)    echo "9cc2de7d195fa157f98b306a8a1cb151ded93f488939b93363cebc8b9d598c28" ;;
    *) return 1 ;;
  esac
}

# ---------------------------------------------------------------------- node
# 便携 Node（仅 INSTALL_RENDERER=1 时随包）：不跟打包机的 node -v 走。
NODE_VER="24.18.0"
node_spec() {  # $1 = NODE_PLATFORM（darwin-arm64 / darwin-x64 / win-x64）
  case "$1" in
    win-x64)      echo "zip 0ae68406b42d7725661da979b1403ec9926da205c6770827f33aac9d8f26e821" ;;
    darwin-arm64) echo "targz e1a97e14c99c803e96c7339403282ea05a499c32f8d83defe9ef5ec66f979ed1" ;;
    darwin-x64)   echo "targz dfd0dbd3e721503434df7b7205e719f61b3a3a31b2bcf9729b8b91fea240f080" ;;
    *) return 1 ;;
  esac
}

# --------------------------------------------------------------------- 工具
_sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1   # macOS 没有 sha256sum
  fi
}

# verify_sha256 <文件> <期望值|-> <说明>：不一致直接失败，绝不让坏包进产线。
verify_sha256() {
  local file="$1" want="$2" label="${3:-$1}" got
  [[ "$want" == "-" || -z "$want" ]] && return 0
  got="$(_sha256_of "$file")"
  if [[ "$got" != "$want" ]]; then
    echo "校验失败：${label} sha256=${got} 期望=${want}（下载被篡改或版本钉错）" >&2
    rm -f "$file"
    return 1
  fi
  return 0
}
