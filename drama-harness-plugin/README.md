# dsh-plugin-drama-harness

DeepSeek Harness 的 BYOK 短剧生产插件。配置一个 API Key，即可在本地完成：

**小说 → 设定集 → 分集 → 剧本/分镜 → 角色定妆照 → 场景图 → 首帧 → 视频片段 → 配音/字幕 → 成片**

生成过程只调用你配置的模型网关，不依赖本项目的云端服务，也不要求安装 ComfyUI。所有项目状态、图片、视频、音频和字幕都保存在本地工作区。

## 环境要求

- DeepSeek Harness（桌面版，或 `npx @deepseek-ai/dsh web`）
- Node.js >= 20
- 一个 OpenAI 兼容网关的 API Key
- 可选：`ffmpeg`（仅用于把逐段视频合成为最终 mp4；没有时仍会生成全部逐段产物和合成清单）

## 安装

### 从 GitHub Release 下载（推荐）

在 Release 页下载 `dsh-plugin-drama-harness-0.1.0.zip`，解压后把目录挂到 DSH：

```bash
unzip dsh-plugin-drama-harness-0.1.0.zip
dsh plugin --profile web add link:/absolute/path/to/dsh-plugin-drama-harness-0.1.0
dsh --profile web --dump-config   # 确认配置树里出现 drama-harness
```

zip 内已包含构建好的单文件 bundle，解压即可用，不需要执行 `pnpm install`。

### 从源码安装（开发）

```bash
cd drama-harness-plugin
pnpm install
pnpm build

dsh plugin --profile web add ./drama-harness-plugin
```

也可以先打包再安装：

```bash
pnpm pack
dsh plugin --profile web add ./dsh-plugin-drama-harness-0.1.0.tgz
```

发布到 GitHub 后可使用：

```bash
dsh plugin --profile web add "github:<org>/<repo>#main"
```

插件包内自带 `cordis.patch.yml`，DSH 安装时会自动把 `drama-harness` 插入 profile 配置树。安装后重启 `dsh web`。本地开发挂载可参考 [cordis.example.yml](cordis.example.yml)。

> 运行依赖（`@deepseek-ai/dsh-tools`、`@deepseek-ai/schemastery`）在构建时打包进 `lib/index.js`。请不要把它们声明成安装到 profile 的运行依赖：DSH 会把 profile 里的 `@deepseek-ai/dsh-tools` 当作 base 的挂载项，与宿主实例产生两份注册表，工具调用会报 `Cannot read properties of undefined (reading 'prepare')`。

## 配置

插件只需要填一个 `apiKey`。安装后编辑 profile 的补丁文件（默认 `~/.dsh/profiles/web/cordis.patch.yml`，Windows 为 `C:\Users\<你>\.dsh\profiles\web\cordis.patch.yml`）：

```yaml
- id: drama-harness
  config:
    apiKey: 'sk-你的Key'
    gateway: aipdd
```

只填 `apiKey` 即可使用 AIPDD 网关；换其他网关时再补 `gateway` 和 `baseUrl`。补丁层的 `config` 是整行覆盖，没写的字段会回落到下表默认值。保存后 DSH 热加载，无需重启。

其余配置都有默认值：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `apiKey` | 空 | 网关 API Key（唯一必填） |
| `gateway` | `aipdd` | `aipdd` / `newapi` / `volcengine` / `deepseek` / `custom` |
| `baseUrl` | 按网关预设 | `newapi`、`custom` 必填；通常以 `/v1` 结尾 |
| `textModel` | 自动探测 | 文本/剧本模型 |
| `imageModel` | 自动探测 | 图片模型 |
| `videoModel` | 自动探测 | 视频模型 |
| `ttsModel` | 自动探测 | OpenAI 兼容语音模型 |
| `ttsVoice` | `alloy` | 语音音色 |
| `videoApi` | `auto` | `auto` / `aipdd-videos` / `newapi-jobs` / `ark` |
| `workspaceDir` | `~/drama-projects` | 本地工作区；支持 `~`，相对路径基于 DSH 启动目录 |
| `ffmpegPath` | 空 | 留空时从 `PATH` 查找 `ffmpeg` |
| `concurrency` | `2` | 图片/视频/配音并发数，范围 1-8 |
| `videoTimeoutSec` | `1800` | 单个视频任务轮询超时 |
| `imageSize` | `1024x1536` | 角色/场景参考图尺寸 |
| `frameSize` | `1024x1536` | 分镜首帧尺寸 |
| `aspectRatio` | `9:16` | 视频画幅 |

### 网关预设

| gateway | baseUrl | 视频协议 | 说明 |
| --- | --- | --- | --- |
| `aipdd` | `https://api.aipdd.work/v1` | `/videos` 异步任务 | 全链路推荐；图片、视频、语音都走同一网关 |
| `newapi` | 用户填写 | `/video/generations` 异步任务 | 自建 NewAPI 或其他 OpenAI 兼容聚合网关 |
| `volcengine` | `https://ark.cn-beijing.volces.com/api/v3` | `/contents/generations/tasks` | 火山方舟；`videoModel` 填推理接入点 ID |
| `deepseek` | `https://api.deepseek.com/v1` | `/video/generations` | 官方只有文本能力；图片/视频/配音需另配网关或模型 |
| `custom` | 用户填写 | `/video/generations` | 其他兼容网关，按实际协议用 `videoApi` 覆盖 |

模型名留空时会读取 `GET /models`，按名称特征自动匹配。匹配不理想时，直接填写 `textModel` / `imageModel` / `videoModel` / `ttsModel`。

## 工具与推荐流程

插件注册 11 个 `drama_*` 工具。直接对 Agent 说“帮我做一部短剧”，或按下面顺序调用：

| 步骤 | 工具 | 作用 |
| --- | --- | --- |
| 0 | `drama_check` | 验证配置、探测模型、检测 ffmpeg |
| 1 | `drama_init` | 创建项目、保存正文、解析设定集/人物/章节 |
| 2 | `drama_plan_episodes` | 规划分集 |
| 3 | `drama_script` | 生成指定集的 beat 剧本 |
| 4 | `drama_cast` | 生成人物定妆照 |
| 5 | `drama_scenes` | 生成场景参考图 |
| 6 | `drama_frames` | 生成每个 beat 的首帧（自动引用场景图和人物图） |
| 7 | `drama_videos` | 首帧转视频片段（异步轮询） |
| 8 | `drama_audio` | 生成配音并写出 `subtitles.srt` |
| 9 | `drama_compose` | 合成 `final.mp4`；无 ffmpeg 时输出 `compose.json` |
| - | `drama_status` | 查看项目/阶段/产物和下一步建议（不需要 API Key） |

示例提示：

```text
先调用 drama_check 检查配置。
然后对 /path/to/novel.txt 执行 drama_init(projectId="my-drama", sourcePath="/path/to/novel.txt")，
规划 12 集，先完成第 1 集的剧本、角色、场景、首帧、视频、配音和成片。
```

## 本地产物

```text
<workspaceDir>/<projectId>/
  project.json                  # 项目状态、阶段、断点信息
  source.txt                    # 小说正文
  characters/portraits/*.png    # 角色定妆照
  scenes/*.png                  # 场景参考图
  ep001/
    script.json                 # beat 剧本（结构化）
    script.md                   # 可读剧本
    frames/beat_001.png         # 首帧
    videos/beat_001.mp4         # 视频片段
    audio/beat_001.mp3          # 配音
    subtitles.srt               # 字幕
    final.mp4                   # 成片（有 ffmpeg 时）
    compose.json                # 合成清单（始终生成）
```

- 每个阶段默认跳过已有产物；需要重做时传 `force=true`。
- 任务失败会保留已完成的部分，状态写入 `project.json`，可直接重跑未完成步骤。
- 重写剧本会重置该集后续阶段，避免旧视频/配音与新剧本错位。

## 合成与 ffmpeg

`drama_compose` 会：

1. 按 beat 顺序把视频、配音归一化到统一分辨率/帧率/采样率；
2. 拼接为 `final.mp4`；
3. 默认把 SRT 作为软字幕嵌入，同时保留独立 `.srt` 文件。

如果找不到 ffmpeg，工具不会自动安装，也不会伪造 mp4：

- 写出 `epXXX/compose.json`，列出每个 beat 的视频、音频、时长和字幕；
- 保留全部逐段产物；
- 返回 `composed: false` 和明确原因。

安装 ffmpeg 或配置 `ffmpegPath` 后重跑 `drama_compose(force=true)` 即可。

## 开发与验证

```bash
pnpm install
pnpm check      # TypeScript 类型检查
pnpm build      # 输出 lib/
pnpm smoke      # 加载 lib/index.js，校验插件契约和 11 个工具
pnpm e2e:mock   # 启动本地 mock 网关，跑完整生产链路并校验产物
```

`pnpm smoke` 会验证：

- 模块导出 `name` / `inject` / `Config` / `apply`，且没有 default export；
- DSH 的 `defineTool` 能编译全部参数与输出 schema；
- 11 个工具全部注册；
- `drama_status` 能读取空工作区；
- 缺少必填参数时会被 DSH 工具校验拦截。

## 边界说明

- 本插件面向 DeepSeek Harness 的 Cordis 插件契约，按官方 `defineTool` 形式注册；DSH 仍处于开发者预览阶段，宿主升级后需重新验证。
- 图片编辑使用 OpenAI 风格的 JSON `image` data URL；只接受 multipart 上传的网关需要适配。
- 配音使用 OpenAI 兼容的 `POST /audio/speech`；网关不开放该端点时请跳过配音，视频和字幕仍可产出。
- 当前配音按 beat 生成，整集使用同一音色，不区分角色音色；旁白与对白会连续朗读。
- 视频轮询兼容 AIPDD `/videos`、NewAPI `/video/generations`、火山方舟 `/contents/generations/tasks`；其他协议用 `videoApi` 或 `custom` 扩展。
- 生成调用会消耗你配置的网关额度；插件不内置重试风暴，失败后由 Agent/用户决定是否重跑。

## License

MIT。本插件是独立代码包，不修改宿主项目代码。
