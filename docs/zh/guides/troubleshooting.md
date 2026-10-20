<!-- lang-switch -->
[English](../../en/guides/troubleshooting.md) · **简体中文**

# 排错

> 自托管 DramaClaw CE 时的常见故障与排查。先看日志:`docker compose logs -f api`,或本地开发时看 `novelvideo api` 的终端输出。

## 启动类

| 现象 | 排查 |
|---|---|
| **容器起不来 / 立即退出** | 查看 `docker compose logs api`，按启动日志定位配置、端口或数据卷问题。 |
| **`8780` 端口被占用** | 改 compose `ports` 左值,如 `8888:8780`;或停掉占用进程(`lsof -i :8780`)。 |
| **健康检查一直 unhealthy** | 探活打 `/api/v1/config`;若 API 自身报错,看启动日志定位真正异常。 |
| **本地开发起不来:Python 版本** | 需 **3.11–3.12**(`>=3.11,<3.13`)。`uv python pin 3.12` 或装对应版本后 `uv sync`。 |

## 模型 / 网关类

| 现象 | 排查 |
|---|---|
| **模型调用全报错** | 在「设置 → 模型与渠道」确认当前渠道已配置；官方渠道检查 DC key，LocalNewAPI 检查服务、runtime token 和上游渠道。 |
| **某个环节报"模型不存在"** | LocalNewAPI 中没有对应逻辑模型映射，或目标渠道未启用。详见[配置模型供应商](../getting-started/configuring-models.md)。 |
| **结构化环节报 `Exceeded maximum output retries`**（角色抽取、剧本规划等），纯文本环节正常 | 上游没有返回 function/tool call。任务日志（v2.0.3 起）会打出重试提示和底层原因。自己做 Chat Completions 格式转换的中转站（Codex2API 及类似的 Codex 反代）已知会在 `/v1/chat/completions` 上丢掉 `tool_calls`：到 LocalNewAPI 后台给该渠道开启 **ChatCompletions → Responses Compatibility**（`chat_completions_to_responses_policy`），让 LocalNewAPI 用 `/v1/responses` 发往上游。见 #490。 |
| **文本模型超时** | 调大 `NEWAPI_TEXT_TIMEOUT_SECONDS`(默认 120);内网网关被系统代理拦截时设 `NEWAPI_TEXT_TRUST_ENV=false`。 |
| **参考图功能不可用** | 需配 `OSS_RELAY_AK/SK`;纯文本→成片流程可不配。 |

## 媒体 / ffmpeg 类

| 现象 | 排查 |
|---|---|
| **合成阶段报找不到 ffmpeg** | 本地开发需自行装 ffmpeg(Docker 已自带)。老链路(整集合成、`jobs.py` 那批一键工具)只从 `PATH` 找;动效大片链路可用 `FFMPEG_PATH` 指定。见 [ffmpeg 指南](ffmpeg.md)。 |
| **合成失败,提示编码器不可用** | 不该再出现:编码器由 `src/novelvideo/video_encoder.py` 按本机 `ffmpeg -encoders` 实测挑选,`Unknown encoder 'libx264'` 已收口。若报「没有可用的 H.264 编码器」,是这台 build 四个候选(x264 / videotoolbox / h264_mf / libopenh264)都没有;想指定某个实现设 `VIDEO_CODEC`(本机有才生效)。见 [ffmpeg 指南](ffmpeg.md)。 |
| **成片黑屏 / 时长异常** | 多为上游图片/音频产物缺失;回看前序环节日志,确认素材已生成。 |

## 数据 / 升级类

| 现象 | 排查 |
|---|---|
| **重建后数据没了** | 数据在命名卷 `ce-data`(容器内 `/data`)。`docker compose down` 保留卷,**别加 `-v`**(会删卷)。备份见 [自托管手册](self-hosting.md#5-数据在哪--备份)。 |
| **`unable to prepare context: path ".../dramaclaw-gateway" not found`** | 网关从 vendored 的 `third_party/dramaclaw-gateway` 构建，因此只有当你把 `DRAMACLAW_GATEWAY_SRC` 指向了一个不存在的路径才会这样。取消该变量即可回到 vendored 副本；或把 `.env` 里的 `DRAMACLAW_GATEWAY_SRC` 指向一个有效的网关 checkout 路径。 |
| **升级后报配置错误** | 现在全部源码构建（`docker-compose.yml` 与 `docker-compose.release.yml`）：`git pull && docker compose up -d --build`。旧的拉镜像模式已退役。详见自托管手册 §6。 |

## world 特性(3DGS/SHARP)类

| 现象 | 排查 |
|---|---|
| **报 `FileNotFoundError` 指向 `BuilderGPT/...`** | 这些重特性脚本不在 CE 精简包内,纯文本→成片不需要;走 3D/体素流程才需补齐。 |
| **`uv sync --extra world` 安装失败** | 需用 uv(非 pip)以使依赖 override 生效;GPU 加速需 CUDA 环境,slim/CPU 环境仅 CPU 路径。 |

## 自愈诊断（MCP Agent）

| 现象 | 排查 |
|---|---|
| **Agent 调工具失败后不知道怎么继续** | 不是调用方自己能改的失败（服务端 5xx、限流 429、无状态码的 ok:false）会带上 `self_heal` 提示；参数/路径这类 4xx 按错误信息自行修正即可。让 Agent 调一次 `aipdd_drama_self_heal`（默认 `mode="diagnose"`）：它收集诊断、环境待办、日志尾部和失败工具源码，交给已配置的默认文本模型给出分类结论与下一步。 |
| **想让它直接修** | 只有用户明确要求修复时才用 `mode="repair"`。`diagnose` 返回的修复项状态是 `proposed`（只是建议，不会执行）；`repair` 仅执行白名单动作：安装可自动装的运行依赖（如 world，需 `allow_heavy=true`；结果是 `started` 表示异步安装已开始、装没装好稍后用 `check` 确认，`needs_manual` 表示当前环境无法自动安装、按返回的 `guide` 手工处理）、对 `src/novelvideo/` 或 `.hermes/plugins/` 下的 `.py` 做唯一匹配替换、创建状态/运行时/输出目录。不执行 shell、不装 pip/npm 包、不改密钥与鉴权文件；补丁先备份并做语法校验，失败自动回滚。 |
| **工具报 `network_error` / 服务连不上** | 不要调自愈：自愈端点就在同一个服务上，也会失败。先确认 AIPDD 已启动，并检查 `DRAMACLAW_API_URL` 的地址与端口，再重试一次。 |
| **查自愈做了什么** | 审计日志在数据目录 `self_heal/audit.jsonl`，补丁备份在 `self_heal/backups/<UTC 时间>/`。改了源码后需重启服务（响应里 `restart_required` 为 true）；macOS `.app` 内拒绝改代码以保住签名。 |
| **自愈本身报"模型未配置"** | 响应里 `degraded=true` 且 `degraded_reason` 指向网关未配置。先到「设置 → 模型与渠道」配好默认文本模型，再重试。 |

## 还没解决?

- 用法/想法 → [GitHub Discussions](https://github.com/liujh0729-jpg/aipdd-drama-agent/discussions)
- 确认是 Bug → [提交 Bug](https://github.com/liujh0729-jpg/aipdd-drama-agent/issues/new?template=bug_report.yml)(附日志、复现步骤、环境)
- 安全问题 → 勿走公开 issue,见 [SECURITY](../../../SECURITY.md)

## 相关

- [安装指南](../getting-started/installation.md) ｜ [快速开始](../getting-started/quickstart.md) ｜ [自托管手册](self-hosting.md)
