<!-- lang-switch -->
[English](../../en/reference/environment-variables.md) · **简体中文**

# 环境变量参考

> 配置通过项目根目录 `.env` 注入(从 `.env.example` 复制)。**`.env.example` 是权威全集且有逐项注释**;本篇按组归纳最常用的变量与默认值,便于查阅。

## 运行环境与路径

| 变量 | 默认 | 说明 |
|---|---|---|
| `ST_EDITION` | `ce`(compose/镜像强制) | 版本标识。CE 模式不可降级。 |
| `NOVELVIDEO_DATA_ROOT` | `.`(Docker 设为 `/data`) | 数据根目录,下面三项默认派生于此。 |
| `NOVELVIDEO_OUTPUT_DIR` | `$DATA_ROOT/output` | 成片与产物输出目录。Docker Compose 固定为 `/data/output`，随 `ce-data` 卷持久化。 |
| `NOVELVIDEO_STATE_DIR` | `$DATA_ROOT/state` | 本地状态。 |
| `NOVELVIDEO_RUNTIME_DIR` | `$DATA_ROOT/runtime` | 运行时临时目录。 |
| `ST_CONTROL_PLANE_DSN` / `ST_REDIS_URL` / `ST_CELERY_BROKER_URL` / `ST_CELERY_RESULT_BACKEND` | 空(CE 强制清空) | EE/分布式才用;CE 任务进程内 inline 执行,留空。 |

## 模型网关

CE 的渠道选择、网关地址和 token 由网页「设置 → 模型配置」写入本机
`settings.db`，不通过环境变量配置。官方渠道地址固定；本地渠道使用 CE
随附的 LocalNewAPI。

| 变量 | 默认 | 说明 |
|---|---|---|
| `NEWAPI_TEXT_TIMEOUT_SECONDS` | `120` | 文本模型 HTTP 超时(秒)。 |
| `NEWAPI_TEXT_TRUST_ENV` | `true` | 是否让文本客户端读系统代理;内网网关被代理拦截时设 `false`。 |

模型名**不通过环境变量配置**：每个功能位（通用文本生成、出场角色筛选、图片理解、风格分析……）
绑哪个模型，只在网页「设置 → 模型」里选，可绑清单来自渠道目录；模板里以前那些
`*_MODEL=DC-*-LLM` 与 `TEXT_MODEL_FALLBACK` / `VISION_MODEL_FALLBACK` 已全部退出解析链。
原因：env 排在设置前面，面板换了模型而环境还指着旧名字时，用户看到一个模型、实际跑的是
另一个；名字下架后更会变成网关 503 死链。解析不到时任务在提交前就报中文错误并指回设置页，
系统绝不回落到任何预设模型名——详见[配置模型供应商](../getting-started/configuring-models.md)。

### 例外：不经网关的直连旁路（默认部署内不生效）

少数旁路能力直连供应商、不经过渠道目录，因此仍保留自己的 env 键与出厂默认名：

| 变量 | 用途 | 生效前提 |
|---|---|---|
| `SCENE_SPATIAL_CONTRACT_MODEL`（回退 `OPENROUTER_VISION_MODEL`，出厂名 `openai/gpt-5.5`） | 场景 360 的空间契约 / 重叠分析 VLM | 配了 `OPENROUTER_API_KEY`，否则整段跳过 |
| `VOXEL_VLM_MODEL` / `VOXEL_VLM_PROVIDER` | 场景体素化 codegen | 同上 |
| `SKETCH_GATE_MODEL` / `SKETCH_GATE_PROVIDER` / `SKETCH_GATE_API_KEY` | 草图视觉质量门 | provider 与 key 同时在场，否则不做门控 |
| `BLOCK_WORLD_MODEL`（回退 `MODEL_PROVIDER` / `MODEL_NAME`） | 导演世界 block world（宿主 codex CLI） | 宿主机装了该 CLI |

这几处**不并进「设置 → 模型」清单**是有意为之：它们的模型名与端点、鉴权是解耦的——网关解析出来
的是「网关可路由真名」，把它打给直连端点只会变成 404/400；而且默认部署里这些密钥都不配，一条也
不会跑到。组织（多用户）部署下这些直连子进程会被 egress 边界直接拒绝，不依赖本节配置。

真要在生产启用，正确顺序是：先把该供应商在「设置 → 模型网关」配成一条 OpenAI 兼容渠道，再把
调用点整体改走网关（名字 / base_url / 密钥三件事同一个提交里成对切换），而不是只把设置里的名字
塞给直连端点。这条约束、实证与触发条件登记在 `tests/test_no_hardcoded_model_names.py` 的
wave3 归档说明里。

## 参考媒体 relay(可选)

| 变量 | 说明 |
|---|---|
| `OSS_RELAY_AK` / `OSS_RELAY_SK` | 参考图功能所需的对象存储凭据。纯文本→成片流程可不配。 |
| `OSS_RELAY_ENDPOINT` / `OSS_RELAY_BUCKET` | relay 端点与桶。 |

## 视频 / 图像参数

| 变量 | 默认 | 说明 |
|---|---|---|
| `VIDEO_FPS` | `30` | 帧率。 |
| `VIDEO_WIDTH` / `VIDEO_HEIGHT` | `1080` / `1920` | 竖屏分辨率。 |
| `VIDEO_CODEC` | `libx264` | 视频编码(H.264);ffmpeg build 须含此编码器,见 [ffmpeg 指南](../guides/ffmpeg.md)。 |
| `VIDEO_AUDIO_CODEC` | `aac` | 音频编码。 |
| `VIDEO_BITRATE` | `4M` | 码率。 |
| `IMAGE_DEFAULT_WIDTH` / `IMAGE_DEFAULT_HEIGHT` / `IMAGE_DEFAULT_STYLE` | `1440` / `2560` / `chinese_period_drama` | 图像默认尺寸与风格。 |

## 媒体工具

| 变量 | 默认 | 说明 |
|---|---|---|
| `FFMPEG_PATH` | `ffmpeg`(从 PATH) | ffmpeg 可执行路径,装在非标准位置时显式指定。 |

## 安全

| 变量 | 默认 | 说明 |
|---|---|---|
| `PROMPT_EXPORT_PASSWORD` | `change_me` | 提示词导出口令,**部署务必覆盖**。 |
| `ST_COOKIE_SECURE` | `true` | 管理 Cookie 是否 Secure。本机 HTTP 开发需设 `0`,否则浏览器丢弃 cookie。 |

## 版本更新通知

| 变量 | 默认 | 说明 |
|---|---|---|
| `RELEASE_NOTIFICATIONS_ENABLED` | `true` | 设为 `false` 可完全关闭 release feed,包括包内 notes 解析与 GitHub 检查。 |
| `RELEASE_NOTIFICATIONS_GITHUB_TOKEN` | 空 | 可选 GitHub token,用于提高 `releases/latest` 限流额度;留空走匿名请求。 |

## 可观测追踪(可选,默认关闭)

| 变量 | 说明 |
|---|---|
| `NOVELVIDEO_ENABLE_LOGFIRE` | 打开 PydanticAI 追踪埋点。 |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | 导出 trace 到你自己的 OTLP/Jaeger。 |
| `LOGFIRE_TOKEN` | 唯一会把数据发往 Logfire SaaS 的开关。 |

详见 [遥测说明](../guides/telemetry.md)。默认三者都不设,不发送任何数据。

## 相关

- 完整列表与注释:仓库根 `.env.example`
- [快速开始](../getting-started/quickstart.md) ｜ [配置模型供应商](../getting-started/configuring-models.md) ｜ [自托管手册](../guides/self-hosting.md)
