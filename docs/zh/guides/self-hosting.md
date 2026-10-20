<!-- lang-switch -->
[English](../../en/guides/self-hosting.md) · **简体中文**

# 自托管手册（Docker）

> 用 Docker 部署、配置、升级、备份 DramaClaw CE。

CE 三个容器：`api` + `newapi`（LocalNewAPI，切到自定义/LocalNewAPI + 官方混合模式前闲置）+ `web`，**无 PostgreSQL / 无 Redis / 无 Celery**（`ST_EDITION=ce`，任务在进程内 inline 执行）。模型默认走 DramaClaw 官方网关。

仓库里有两份 compose 文件，**两者都全部源码构建、不拉任何已发布镜像**。`docker-compose.release.yml` 是三服务运行定义（env / 端口 / 卷 / 健康检查）的唯一来源，并且自带各自的 `build:` 块；`docker-compose.yml`（默认入口）只是 `extends` 它。DramaClaw 网关已 vendored 进仓（`third_party/dramaclaw-gateway`，AGPL-3.0），**无需再单独 clone**。用 `docker compose up -d --build` 构建。

## 1. 前置

- Docker + `docker compose`。
- Docker Compose ≥ 2.24（`docker compose version` 确认）。
- 资源：建议 ≥ 2 vCPU / 4GB（不含模型推理，推理走外部网关）。
- 一个 DC key（默认官方网关,见 <https://susciyuan.com>），或自己的 OpenAI 兼容网关。

## 2. 拿到 compose 与配置

```bash
git clone https://github.com/liujh0729-jpg/aipdd-drama-agent.git
cd aipdd-drama-agent   # 网关已 vendored 在 third_party/dramaclaw-gateway，无需单独 clone
cp .env.example .env
```

两份文件都从源码构建三服务，已为你定好，无需改：

| 文件 | 角色 | 命令 |
|---|---|---|
| `docker-compose.yml` | 默认入口—— `extends` 下面那份并叠加本地构建镜像名 | `docker compose up -d --build` |
| `docker-compose.release.yml` | 运行定义 base—— 同样从源码构建 `api`/网关/`web` | `docker compose -f docker-compose.release.yml up -d --build` |

两者共享的关键点（定义在 `docker-compose.release.yml` 里，`docker-compose.yml` 通过 `extends` 复用）：

| 项 | 值 | 说明 |
|---|---|---|
| 服务 | `api` + `newapi` + `web` | 无 PG/Redis；`newapi` 是 LocalNewAPI，由 vendored 的 `third_party/dramaclaw-gateway` 构建 |
| 构建上下文 | `api` = `.` · `newapi` = `${DRAMACLAW_GATEWAY_SRC:-./third_party/dramaclaw-gateway}` · `web` = `./frontend` | 本地构建镜像名 `aipdd/api`、`aipdd/gateway`、`aipdd/web`；零外部镜像 |
| 网关源码 | `third_party/dramaclaw-gateway`（vendored） | 想改用自己的 checkout 构建：`.env` 设 `DRAMACLAW_GATEWAY_SRC=<路径>` |
| 端口 | `8780:8780` | REST API。发布到所有网卡；单用户信任开启时（未启用 `ST_CE_MULTI_USER`）任何请求都免登录被当成本地 owner——请把 8780 挡在反代/防火墙之后，或只绑 `127.0.0.1`。绑定未被确认为仅回环时，启动日志会告警 |
| 网关管理端口 | `${ST_NEWAPI_BIND:-127.0.0.1}:${ST_NEWAPI_PORT:-3000}:3000` |  默认只绑 `127.0.0.1`；`.env` 设 `ST_NEWAPI_BIND=0.0.0.0` 放开。**[aipdd fork] `docker-compose.aipdd.yml` 不再映射该端口**：网关只作项目内部传输层，对外统一走 web 的 `/gateway/v1/`（模型广场调用示例即 `NEWAPI_PUBLIC_BASE_URL`） |
| 强制环境 | `ST_EDITION=ce`、清空 control-plane/Redis/Celery | CE 模式不可降级 |
| 数据卷 | `ce-data:/data`（输出为 `/data/output`） | 持久化项目数据库、设置和生成媒体 |

## 3. 配置 `.env`

> ⚠️ **密钥类默认值（如 `PROMPT_EXPORT_PASSWORD=change_me`）必须改。** 模型网关见 [模型配置](#模型配置)。

分组（`.env.example` 内有逐项注释）：LocalNewAPI provisioner、参考媒体 OSS relay（OSS_RELAY_*）、Cognee 知识图谱、文本/图片/视频/音频各模型、图像与视频基础参数、UI、输出目录。渠道、网关地址和 token 通过网页保存到 `settings.db`。

### 模型配置

推荐与备选(详见 [配置模型供应商](../getting-started/configuring-models.md)):

- **A. DC 官方 key(推荐)**：默认 compose 已走官方网关。起栈后开 `http://localhost:8080` → 设置 → 模型与渠道 → 「官方」→ 粘贴 DC key 保存即用,**无需映射模型**。到 <https://susciyuan.com> 取 key。
- **B. LocalNewAPI**：LocalNewAPI 已在运行；到 设置 → 模型与渠道 → 自定义，点初始化，然后在「LocalNewAPI」页配置上游渠道和模型映射。

LocalNewAPI 需把 DramaClaw 逻辑模型映射到真实上游模型。参考图功能需要 `OSS_RELAY_AK/SK`（纯文本流程可暂不配）。

## 4. 起停

```bash
docker compose up -d --build                          # 源码构建：启动（首次会构建）
docker compose ps                                     # 状态
docker compose logs -f api                            # 日志
docker compose down                                   # 停止（保留数据卷）
```

## 5. 数据在哪 / 备份、恢复与迁移

- 项目数据库、设置和生成媒体都在命名卷 `ce-data`（容器内 `/data`）；生成媒体固定写入 `/data/output`。删除或重建容器不会删除该卷，只有显式执行 `docker compose down -v` 才会删除。
- 备份数据卷：

```bash
docker run --rm -v dramaclaw-ce_ce-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/ce-data-backup.tar.gz -C /data .
```

（卷名前缀随 compose 项目名，`docker volume ls` 确认实际名。）

- 恢复 / 搬到新机 —— 把 `ce-data-backup.tar.gz` 拷到目标机，反向解回数据卷（`-v` 挂载会在卷不存在时自动创建）：

```bash
docker run --rm -v dramaclaw-ce_ce-data:/data -v "$PWD":/backup alpine \
  tar xzf /backup/ce-data-backup.tar.gz -C /data
```

然后照常起服务（`docker compose up -d`）。数据卷备份已包含生成媒体；`.env` 仍需单独备份。

- `newapi-data` 卷存放 LocalNewAPI 的 SQLite 数据库（上游渠道、密钥、token），不可再生——按同样方式备份：

```bash
docker run --rm -v dramaclaw-ce_newapi-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/newapi-data.tgz -C /data .
```

按同样方式恢复到目标卷：

```bash
docker run --rm -v dramaclaw-ce_newapi-data:/data -v "$PWD":/backup alpine \
  tar xzf /backup/newapi-data.tgz -C /data
```

## 6. 升级

源码构建：

```bash
git pull
docker compose up -d --build
```

`docker compose up -d --build` 会连网关一起从 vendored 的 `third_party/dramaclaw-gateway` 重新构建（Go + bun，要几分钟）。只改了本仓代码时，只重建两个本地服务：`docker compose up -d --build api web`；只改了 vendored 网关时：`docker compose up -d --build newapi`。

升级不会碰你的 `.env`；`ce-data` 与 `newapi-data` 卷原样复用。

如果旧版本曾将媒体写入容器内 `/app/output`，请在启动新版本**之前**运行一次迁移（zip 用户没有 git 仓库，直接从 GitHub 下载 `scripts/migrate_docker_output.py`）：

```bash
git pull
docker compose exec -T api python - < scripts/migrate_docker_output.py
docker compose up -d
```

Windows PowerShell 使用：

```powershell
git pull
Get-Content scripts/migrate_docker_output.py -Raw | docker compose exec -T api python -
docker compose up -d
```

脚本只复制缺失文件，不覆盖、不删除源文件，并在更新项目路径前备份 `projects.db`。只存在于已删除容器层里的文件无法从数据卷恢复。

### 从旧 checkout 升级

`docker-compose.selfhosted.yml` 与 `docker-compose.selfhosted.release.yml` 已移除，改用上面两份文件——服务名与 `ce-data` / `newapi-data` 数据卷不变，已有数据原样复用。

| 以前 | 现在 |
|---|---|
| `docker compose up -d --build`（源码构建、旁边放一个 `../dramaclaw-gateway` clone） | 同一命令；网关现在取自 vendored 的 `third_party/dramaclaw-gateway`，无需旁边再 clone |
| `docker compose -f docker-compose.release.yml up -d`（镜像模式） | 镜像模式已退役——`docker-compose.release.yml` 现在也源码构建；加 `--build` |
| `docker compose -f docker-compose.selfhosted.yml ...` / `docker-compose.selfhosted.release.yml ...` | 改用 `docker-compose.yml` / `docker-compose.release.yml`（均源码构建）；`newapi-data` 卷复用，先备份 |
| `.env` 里的 `NEWAPI_BASE_URL` / `NEWAPI_API_KEY` / `ST_*_PORT` / `INSTALL_WORLD` / `NEWAPI_PROVISIONER_ENABLED` | 含义不变，继续生效 |

## 7. 排错

| 现象 | 排查 |
|---|---|
| 容器起不来 | `docker compose logs api`；多半是 `.env` 网关地址/Key 未改或不可达 |
| 8780 端口占用 | 改 compose `ports` 左值，如 `8888:8780` |
| 3000 端口被占用（LocalNewAPI 起不来） | **[aipdd fork] `docker-compose.aipdd.yml` 不映射 3000**，本项只见于上游那两个编排文件。上游默认只绑 `127.0.0.1`，`.env` 设 `ST_NEWAPI_PORT=<空闲端口>` 后重启即可；`api` 不等网关健康，不会被卡住。 |
| 模型调用报错 | 确认网关可达、`*_MODEL` 名在网关后台存在 |

## 相关

- [快速开始](../getting-started/quickstart.md) ｜ [配置模型供应商](../getting-started/configuring-models.md)
