#!/usr/bin/env bash
# SPDX-License-Identifier: Elastic-2.0
# [aipdd fork delta] 抖音文案提取依赖「真实（有头）Chrome 经 CDP」过抖音 Argus 详情
# 签名——无头浏览器 / 裸 yt-dlp 都会被风控拒（2026-10 实测）。本脚本在宿主机拉起一个
# 带远程调试端口(9222)的独立 Chrome，供 api 容器经 host.docker.internal:9222 接管。
#
# 用法：
#   bash scripts/douyin-cdp-chrome.sh            # 前台启动（Ctrl-C 退出）
#   bash scripts/douyin-cdp-chrome.sh --background  # 后台常驻，日志见 /tmp
#
# 说明：
#   - 用独立 user-data-dir，不动你日常 Chrome 的书签/登录；
#   - 首次可在该窗口登录抖音（公共视频不登录也能过，登录更稳）；
#   - Chrome 新版只监听 127.0.0.1:9222；Docker Desktop 的 host.docker.internal
#     会把它透给容器，代码里的 browsable_cdp_url 已把主机名换成 IP 绕开 Host 头校验。
set -euo pipefail

PORT="${DOUYIN_CDP_PORT:-9222}"
PROFILE="${DOUYIN_CDP_PROFILE:-$HOME/.chrome-douyin-cdp}"

# 只探测本机「已经装好」的 Chrome（可用 DOUYIN_CDP_BIN 显式指定）；
# 不下载、不安装任何浏览器，也不引入 Playwright 之类的驱动。
CHROME=""
if [[ -n "${DOUYIN_CDP_BIN:-}" ]]; then
  CHROME="$DOUYIN_CDP_BIN"
else
  for candidate in \
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    "/usr/bin/google-chrome" \
    "/usr/bin/google-chrome-stable" \
    "/opt/google/chrome/chrome"
  do
    if [[ -x "$candidate" ]]; then CHROME="$candidate"; break; fi
  done
fi

if [[ -z "$CHROME" || ! -x "$CHROME" ]]; then
  echo "未找到本机已安装的 Chrome（当前探测路径：${CHROME:-无}）" >&2
  echo "请用 DOUYIN_CDP_BIN 指向你本机的 Chrome 可执行文件后重试，例如：" >&2
  echo "  DOUYIN_CDP_BIN='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' bash scripts/douyin-cdp-chrome.sh" >&2
  exit 1
fi

mkdir -p "$PROFILE"

ARGS=(
  --remote-debugging-port="$PORT"
  --user-data-dir="$PROFILE"
  --no-first-run
  --no-default-browser-check
  --disable-blink-features=AutomationControlled
  "https://www.douyin.com/"
)

echo "启动抖音 CDP Chrome：port=$PORT profile=$PROFILE"
if [[ "${1:-}" == "--background" ]]; then
  nohup "$CHROME" "${ARGS[@]}" >/tmp/douyin-cdp-chrome.log 2>&1 &
  echo "后台 PID=$!  日志：/tmp/douyin-cdp-chrome.log"
else
  exec "$CHROME" "${ARGS[@]}"
fi
