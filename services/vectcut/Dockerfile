# [AIPDD] VectCut 剪辑服务镜像。
#
# 独立构建（context = services/vectcut/），与主仓库镜像无依赖：
#   docker build -f services/vectcut/Dockerfile -t aipdd-vectcut services/vectcut
#
# 或经编排（推荐）：docker compose -f docker-compose.aipdd.yml --profile vectcut up -d
FROM python:3.12-slim

# ffmpeg：上游 downloader.py 依赖它下载/转码媒体物料。
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /service

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 容器内必须监听 0.0.0.0 才能被同网络的 api 容器访问。
ENV VECTCUT_HOST=0.0.0.0 \
    VECTCUT_PORT=9001 \
    PYTHONUNBUFFERED=1

EXPOSE 9001

CMD ["python", "aipdd_server.py"]