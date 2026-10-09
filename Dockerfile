FROM python:3.12-slim

# ════════════════════════════════════════════════════════════════════════
# 构建缓存纪律（改本文件前必读）
# ════════════════════════════════════════════════════════════════════════
# 构建刻意分三段，保证「只改业务代码」时零下载、分钟级重建：
#
#   第 1 段 系统与依赖文件（FROM ~ COPY LICENSES）
#     · 已提交在本地构建缓存里（ffmpeg/fonts-noto-cjk 系统包约 30 分钟下载）。
#     · **不要改动这段任何指令的文本**（含换行与参数顺序）——改一个字，
#       Docker 缓存即全链失效，所有下载从头再来。
#
#   第 2 段 依赖与工具（apt git/nodejs/npm、splat-transform、uv 依赖、
#          hermes、chrome-devtools-mcp）
#     · 仅当 pyproject.toml / uv.lock 变化时才重建；下载物存
#       /root/.cache/uv 随层持久化，供第 3 段复用。
#
#   第 3 段 业务代码（COPY src 起）
#     · 日常改代码只从这里重建：依赖已就绪，项目本体 uv sync 只做
#       editable 链接，零下载。
#
#   纪律：永远不要 docker image prune / docker rmi 旧版本镜像或 <none>
#   中间层——它们就是构建缓存本体，删掉 = 下次全量重下。磁盘紧张时只删
#   Exited 的临时容器（docker rm）与悬空卷。
#
#   本机环境提示：macOS + Colima（Docker 存储卷 25G）+ docker-compose v1
#   （无 BuildKit）→ 构建走 classic builder 逐层提交，缓存复用靠上面纪律。
#   完整说明见 需求/环境说明.md。
# ════════════════════════════════════════════════════════════════════════

# 项目全程用 uv 管理(与 host 一致)。Dockerfile 也用 uv,使 uv.lock 锁版本 +
# [[tool.uv.dependency-metadata]] override(da2 的 torch==2.5.0 冲突、sharp 的 gsplat)
# 生效——pip 不认这些 override,会在 da2/world 解析冲突时 build 失败。
RUN pip install --no-cache-dir uv

ENV ST_EDITION=ce \
    ST_CONTROL_PLANE_DSN= \
    ST_REDIS_URL= \
    ST_CELERY_BROKER_URL= \
    ST_CELERY_RESULT_BACKEND= \
    NOVELVIDEO_DATA_ROOT=/data \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    HERMES_CLI_PATH=/root/.local/bin/hermes

# fonts-noto-cjk: 容器是独立部署环境，默认不含任何 CJK 字体；混剪短视频烧录
# 字幕时 libass 需要字形来源，缺失会静默渲染成方块/空白。这里只提供默认字体
# 之一，运行时按可用性自由选用部署环境的字体（含挂载进容器的宿主机字体）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
# license 正文按 REUSE 惯例只存于 LICENSES/(pyproject license-files 指向它),
# hatchling 构建 wheel 时需要这份文件在上下文中。
COPY LICENSES ./LICENSES
# world 特性（3DGS / SHARP）的本地 vendored 源：uv.lock 把 sharp/da2 锁成下面两个
# 目录源（third_party/ml-sharp、third_party/DA-2/src）。运行期「下载并部署」与
# INSTALL_WORLD=1 构建都从这些本地路径装，无需访问 github。不 COPY 进镜像的话，
# uv sync --extra world 会因找不到目录源而失败。
COPY third_party/ml-sharp ./third_party/ml-sharp
COPY third_party/DA-2 ./third_party/DA-2

# ════════════════════════════════════════════════════════════════════════
# 第 2 段：依赖与工具（只在 pyproject.toml / uv.lock 变化时重建）
# ════════════════════════════════════════════════════════════════════════

# [aipdd fork delta] 3D 世界/3DGS「world」特性改为**运行时按需安装**（设置页「下载并部署」）。
# sharp/da2 已 vendored 进仓（上方 COPY 的 third_party/ml-sharp、third_party/DA-2/src），
# uv.lock 里是本地 directory 源——运行期 uv sync --extra world 只从本地路径 + PyPI 装，
# 不再联网 git+https://github.com/apple/ml-sharp。无论 INSTALL_WORLD 为何,精简镜像都常驻轻工具:
#   - nodejs/npm + @playcanvas/splat-transform(PLY→SOG,MIT): 转 SOG 需要
#   - git: world 依赖已全部 vendored，正常安装不再需要 git；保留仅作运维兜底
# torch/sharp/da2 这些 GB 级重依赖**不进**默认镜像,由后台任务 uv sync --extra world
# 装到持久卷 <STATE_DIR>/world/venv(跨重建保留)。模型权重也不进镜像,运行时下载。
# INSTALL_WORLD=1 仍保留:构建期就把 world extra 装进 /app/.venv(离线/无外网场景)。
# 注:slim base 为 CPU;GPU 加速需 CUDA base + nvidia runtime。
ARG INSTALL_WORLD=0

# git/nodejs/npm 独立成层：apt 下载慢（本机 ~10 分钟），与下面 npm/uv 层
# 分开，任一失败可单独重试，不用连坐重跑。
RUN apt-get update \
    && apt-get install -y --no-install-recommends git nodejs npm \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @playcanvas/splat-transform

# 只装第三方依赖（--no-install-project 不装本项目本体）；下载物进
# /root/.cache/uv 随本层持久化，供第 3 段本体安装复用。
# **不要加 --no-cache**：uv 的 --no-cache 既不读也不写缓存，会把 hatchling
# 等隔离构建依赖全部重下，与「不下载」目标相反。
RUN set -eux; \
    if [ "$INSTALL_WORLD" = "1" ]; then \
        uv sync --frozen --no-dev --extra world --no-install-project; \
    else \
        uv sync --frozen --no-dev --no-install-project; \
    fi

RUN uv tool install 'hermes-agent[acp]'

# [aipdd fork delta] 内置「Chrome MCP」服务器（agent/mcp.py 第二内置项）：
# chrome-devtools-mcp 经 CDP 连接客户自带 Chrome 的调试端口（--browserUrl，
# 默认 host.docker.internal:9222，可用 ST_CHROME_CDP_URL 覆盖）。
RUN npm install -g chrome-devtools-mcp

# [aipdd fork delta] 抖音文案提取（freezone/douyin_copywrite.py + chrome_cdp.py）
# 经裸 CDP 连接客户本机已安装的真实 Chrome（调试端口，与上面「Chrome MCP」
# 同一浏览器）拦截详情——不随镜像分发任何浏览器、不执行 playwright install。
# 宿主机需以 --remote-debugging-port=9222 启动 Chrome（可用 ST_CHROME_CDP_URL 覆盖）。

# ════════════════════════════════════════════════════════════════════════
# 第 3 段：业务代码（日常改代码只从这里重建，零下载）
# ════════════════════════════════════════════════════════════════════════
COPY src ./src
COPY .hermes ./.hermes

# 资产完整性兜底(等价原 wheel 检查):login 媒体须随 src 带入(.dockerignore 已 ! 放行)。
RUN test -f src/novelvideo/assets/login_bgm.mp3 \
    && test -f src/novelvideo/assets/login_bg_v1.mp4 \
    && test -f src/novelvideo/assets/login_bg_v2.mp4 \
    && test -f src/novelvideo/assets/login_bg_v3.mp4

# 项目本体安装：依赖上面已装齐且 --frozen 锁死，本步只构建/链接本项目
# （hatchling editable 从 /root/.cache/uv 读缓存，零下载）；
# mkdir -p /data 与本体安装同层，避免额外层。
RUN set -eux; \
    if [ "$INSTALL_WORLD" = "1" ]; then \
        uv sync --frozen --no-dev --extra world; \
    else \
        uv sync --frozen --no-dev; \
    fi; \
    mkdir -p /data

ENV PATH="/app/.venv/bin:/root/.local/bin:$PATH"

EXPOSE 8780
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8780/api/v1/config', timeout=2).status == 200 else 1)"

CMD novelvideo api --host 0.0.0.0 --port 8780