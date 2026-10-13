# syntax=docker/dockerfile:1
# [aipdd fork delta] HyperFrames 渲染服务（引擎即服务）。
#
# 用 @hyperframes/producer（Puppeteer/Chromium + FFmpeg）无头把 api 侧产出的
# HyperFrames 项目目录（hyperframes.json + index.html + compositions/frames/*.html）
# 渲染成 MP4。讲解视频（freezone_explainer_video）与渠道短视频
# （freezone_channel_short_video）共用这一渲染通路。
#
# 口径：
# - 构建期固定 @hyperframes/producer 版本（Apache-2.0），运行期零外联——不跑
#   `npx hyperframes`、不接 HeyGen；依赖与浏览器都在镜像内。
# - 项目目录经共享卷（compose 的 ce-data:/data）传入，producer 按 projectDir 读；
#   产物写回同一 /data 路径供 api 直接取用（详见 src/novelvideo/renderer/client.py）。
# - Chrome 沙箱由引擎的 buildChromeArgs 恒带 --no-sandbox/--disable-setuid-sandbox/
#   --disable-dev-shm-usage，容器内以 root 运行即可；编排侧另配 seccomp=unconfined
#   与更大 shm（见 docker-compose 的 renderer 服务）。

FROM node:22-bookworm-slim

# Chromium 运行所需系统库 + FFmpeg + 中文字体（讲解片字幕/正文以中文为主，缺字体会豆腐块）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates tzdata \
        ffmpeg \
        fonts-liberation fonts-noto-cjk fonts-noto-color-emoji \
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
        libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
        libasound2 libpango-1.0-0 libcairo2 libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY package.json ./

# 浏览器统一落在 root 的 puppeteer 缓存目录，engine 的 resolveHeadlessShellPath 会在此发现它。
ENV PUPPETEER_CACHE_DIR=/root/.cache/puppeteer \
    HOME=/root

# 装渲染引擎（postinstall 会拉 puppeteer 的 Chrome）；再显式装 linux/x64 的
# chrome-headless-shell 走 BeginFrame 快捕——非 x64 架构没有该构建，|| true 兜底，
# engine 自动回落全量 Chrome 的截图模式（更慢但可用）。
RUN npm install --omit=dev --no-audit --no-fund \
    && (npx --yes puppeteer browsers install chrome-headless-shell || true)

# 独立 HTTP 渲染服务入口（POST /render、/render/stream、/outputs/:token、/health）。
# 关闭 health worker 副端口，只暴露 9847；并发默认 1（重活），由编排/环境覆盖。
ENV NODE_ENV=production \
    PRODUCER_PORT=9847 \
    PRODUCER_DISABLE_HEALTH_WORKER=1 \
    PRODUCER_RENDERS_DIR=/data/output/_hyperframes_renders \
    PRODUCER_MAX_CONCURRENT_RENDERS=1 \
    TZ=Asia/Shanghai

EXPOSE 9847

CMD ["node", "node_modules/@hyperframes/producer/dist/public-server.js"]
