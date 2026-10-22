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
| `NOVELVIDEO_PROJECT_ROOT` | 自动探(各消费方按「哪层能找到自己要的随包资源」反推) | 工程根。换脸边车、3D 世界安装器、剪映 10.2 草稿骨架三处都用它定位随包的 `services/`;部署布局非标准时显式指定。 |
| `ST_CONTROL_PLANE_DSN` / `ST_REDIS_URL` / `ST_CELERY_BROKER_URL` / `ST_CELERY_RESULT_BACKEND` | 空(CE 强制清空) | EE/分布式才用;CE 任务进程内 inline 执行,留空。 |

## 模型网关

CE 的渠道选择、网关地址和 token 由网页「设置 → 模型与渠道」写入本机
`settings.db`，不通过环境变量配置。官方渠道地址固定；本地渠道使用 CE
随附的 LocalAPI。

| 变量 | 默认 | 说明 |
|---|---|---|
| `NEWAPI_TEXT_TIMEOUT_SECONDS` | `120` | 文本模型 HTTP 超时(秒)。 |
| `NEWAPI_TEXT_TRUST_ENV` | `true` | 是否让文本客户端读系统代理;内网网关被代理拦截时设 `false`。 |

模型名**不通过环境变量配置**：每个功能位（通用文本生成、出场角色筛选、图片理解、风格分析……）
绑哪个模型，只在网页「设置 → 模型与渠道」里选，可绑清单来自渠道目录；模板里以前那些
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

这几处**不并进「设置 → 模型与渠道」清单**是有意为之：它们的模型名与端点、鉴权是解耦的——网关解析出来
的是「网关可路由真名」，把它打给直连端点只会变成 404/400；而且默认部署里这些密钥都不配，一条也
不会跑到。组织（多用户）部署下这些直连子进程会被 egress 边界直接拒绝，不依赖本节配置。

真要在生产启用，正确顺序是：先把该供应商在「设置 → 模型与渠道」配成一条 OpenAI 兼容渠道，再把
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
| `VIDEO_CODEC` | `libx264` | 视频编码(H.264)。现在**所有出片链路都认它**——编码参数统一由 `src/novelvideo/video_encoder.py` 给出，该解析器先读这个变量，再退到 `libx264 → h264_videotoolbox → h264_mf → libopenh264` 阶梯。两个前提：本机这台 ffmpeg build 真的有它，且它属于阶梯这四个 H.264 实现之一；否则忽略、按阶梯选，不会失败（指到 `mpeg4`/`aac` 这种非 H.264 时警告一次）。见 [ffmpeg 指南](../guides/ffmpeg.md)。 |
| `VIDEO_AUDIO_CODEC` | `aac` | 音频编码。 |
| `VIDEO_BITRATE` | `4M` | 码率。 |
| `IMAGE_DEFAULT_WIDTH` / `IMAGE_DEFAULT_HEIGHT` / `IMAGE_DEFAULT_STYLE` | `1440` / `2560` / `chinese_period_drama` | 图像默认尺寸与风格。 |

## 媒体工具

| 变量 | 默认 | 说明 |
|---|---|---|
| `FFMPEG_PATH` | `ffmpeg`(从 PATH) | ffmpeg 可执行路径,装在非标准位置时显式指定。**只有动效大片链路(逐帧编码 + 响度归一)和共用的编码器解析器认它**；`generators/video_composer.py` 与 `freezone/jobs.py` 那批老链路 argv 里写的是裸 `ffmpeg`,只从 PATH 找。指到 PATH 之外的二进制时,编码器清单按前者探、老链路按后者跑,解析器会打一条一次性 warning,详见 [ffmpeg 指南](../guides/ffmpeg.md)。|

## 动效大片(MG)

图册 → 模型按渲染契约出单文件页面 → 包内 Chrome 逐帧渲染 → 代码编曲归一响度 → 剪映草稿,整条链默认零配置、零外发。以下变量都是**可选**的覆盖开关。

| 变量 | 默认 | 说明 |
|---|---|---|
| `MOTION_GALLERY_MANIFEST` | 内置 `freezone/motion_templates.json` | 换一套案例图册时指到外部 JSON。读坏了回落内置那份并打 warning;内置那份读不出来才报错(那是打包坏了)。 |
| `MOTION_STYLE_MANIFEST` | 内置 `freezone/motion_styles.json` | 画风图册(66 条)是另一份独立清单,只换画面观感、不动案例分镜结构。回落口径与上面那条完全一致:外部读坏→回落内置并打 warning,内置那份读不出来才报错。 |
| `MOTION_GALLERY_ASSET_BASE` | `/api/v1/motion-gallery` | 海报/原片的地址前缀。把 37 条原片(~0.7 GB,不随包)放到 OSS/CDN 后指过去,前端直接按 `<前缀>/<清单相对路径>` 取。**随包小样片不走这个前缀**:它固定按同源 `/api/v1/motion-gallery/<case>/preview.mp4` 取,外部 CDN 只需放海报与原片。 |
| `MOTION_GALLERY_MIRROR` | 空 | 原片按需取源:本机缓存没有时从 `<MIRROR>/<相对路径>` 拉一次、校验 sha256 后落进状态卷再回给前端。不设则未缓存的原片不显示(只影响看片,不影响取提示词)。 |
| `ST_CHROME_CDP_URL` | 空 | 连一个现成的调试实例,渲染任务不再自己拉浏览器。不设时按部署形态取默认:本机/桌面为 `http://127.0.0.1:9222`,容器为 `http://host.docker.internal:9222`。与抖音详情、内置 Chrome MCP 同一变量。 |
| `ST_CHROME_PATH` | 空 | 指到某个 Chrome/Chromium 可执行文件,由渲染任务拉起临时无头实例。指了但文件不存在会吵,不会静默找不到。 |
| `MOTION_DRAFT_TEMPLATE_DIR` | 随包 `services/vectcut/template_jianying_10_2` | 剪映 10.2 草稿骨架目录(含根 `draft_content.json` 与 `Timelines/`)。骨架位置靠「哪一层能找到它」反推工程根,工程根不在默认位置时用 `NOVELVIDEO_PROJECT_ROOT` 指根,或直接指这个目录。 |

> 桌面形态不用设这几个:`env.rs` 把 `resources/bin`(含 `ffmpeg.exe`/`ffprobe.exe`/`chrome/`)前置进 `PATH`,并把 `APP_RESOURCE_PATH` 注入后端,浏览器与模板骨架都从这里探。详见 [桌面版环境变量契约](../../../desktop/README.md)。

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
