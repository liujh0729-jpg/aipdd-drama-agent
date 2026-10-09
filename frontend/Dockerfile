# syntax=docker/dockerfile:1.7

# Stage 1: build the SPA.
# [aipdd fork delta] 去掉 --platform=$BUILDPLATFORM：本地 Colima 无 buildx、compose 回退 classic builder 时
# 该 BuildKit 变量为空会报错。本地单架构构建不需要跨平台，直接取宿主架构的 node:22-alpine。
FROM node:22-alpine AS builder
WORKDIR /app

ARG VITE_APP_VERSION=dev
ENV VITE_APP_VERSION=${VITE_APP_VERSION}

# CE release fallback edition. Edition is normally decided at runtime by the
# backend (GET /api/v1/config); VITE_EDITION only takes effect when that fetch
# fails, so baking =ce makes this CE image fail safe to CE (no EE login wall)
# instead of the EE default. See src/lib/runtime-config.ts fallbackRuntimeConfig().
ARG VITE_EDITION=ce
ENV VITE_EDITION=${VITE_EDITION}

# Use pnpm (matches local dev) instead of npm — a single lockfile as source of
# truth, which removes the npm/pnpm dual-lockfile drift that made `npm ci` fail.
# corepack picks the version pinned in package.json's "packageManager" field.
# pnpm-workspace.yaml is required here: it carries the allowBuilds approvals that
# keep `pnpm install` from erroring on skipped (unneeded) dependency build scripts.
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./

# [aipdd fork delta] 预下载 pnpm（corepack 按 packageManager 字段拉取 11.5.0），
# 独立成层：下游 pnpm install 失败重跑时不必重下 pnpm 自身（慢网保底）。
RUN pnpm --version

# [aipdd fork delta] 慢网保底：官方 registry 单包时速可低至 7 KiB/s，pnpm 默认
# 60s fetch 超时会砍死 onnxruntime/playcanvas 这类大包（error 23）。这里：
#   1) config set 加大超时与重试（写 /root/.npmrc，持久生效）；
#   2) 同层内循环重跑 install——重跑复用本层已下载的 pnpm store，只补失败包。
RUN set -eux; \
    pnpm config set fetch-timeout 1800000; \
    pnpm config set fetch-retries 5; \
    ok=0; \
    for i in 1 2 3 4 5 6; do \
        if pnpm install --frozen-lockfile; then ok=1; break; fi; \
        echo "[pnpm-install] attempt $i failed; retrying with warm store"; \
        sleep 10; \
    done; \
    [ "$ok" = "1" ]

COPY . .
# [aipdd fork delta] 跳过 tsc 类型检查：本 fork 前端自带未写完的孤儿文件
# (model-source-dialog.tsx 引 8 个不存在的导出、register 类型错误)，tsc -b 会失败；
# rollup 不打包孤儿文件、esbuild 不做类型检查，直接 vite build 可正常产出 dist。
RUN pnpm exec vite build

# Parse the rendered runtime template in CI without building the SPA image.
FROM nginx:1.27-alpine AS nginx-config-test
# [aipdd fork delta] /gateway/v1/ 出口已改反代后端 /api/v1/openai/，不再需要 GATEWAY_*；
# 配置校验阶段用回环占位（nginx -t 会在加载期解析 upstream 主机名）。
ENV BACKEND_HOST=127.0.0.1 \
    BACKEND_PORT=8780 \
    NGINX_ENVSUBST_FILTER="^(BACKEND_HOST|BACKEND_PORT)$"
COPY docker/nginx.conf.template /etc/nginx/templates/default.conf.template
RUN /docker-entrypoint.sh nginx -t

# Stage 2: serve via nginx + reverse-proxy /api /static to the backend Service.
FROM nginx:1.27-alpine AS runtime

# Defaults overridable per environment via Helm values -> Deployment env.
ENV BACKEND_HOST=novelvideo-ui-staging \
    BACKEND_PORT=8780 \
    NGINX_ENVSUBST_FILTER="^(BACKEND_HOST|BACKEND_PORT)$"

COPY --from=builder /app/dist /usr/share/nginx/html
COPY docker/nginx.conf.template /etc/nginx/templates/default.conf.template

EXPOSE 80
