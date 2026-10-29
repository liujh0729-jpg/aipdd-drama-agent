<!-- lang-switch -->
[English](../../en/getting-started/configuring-models.md) · **简体中文**

# 配置模型

DramaClaw CE 通过 LocalAPI 兼容网关调用文本、视觉理解、Embedding、图片、视频和音频模型。音乐模型是例外：设置里的「音乐」类型只登记型号与站点渠道，实际调用按厂商音乐协议直连，不经过网关。模型设置保存在 CE 的本地 `settings.db` 中；密钥不会回传到浏览器，只会显示脱敏后的保存状态。

启动后打开 `http://localhost:8080`，进入 **设置 → 模型与渠道**。页面顶部的“当前生效”表示实际运行模式，不是当前正在查看的标签。

## 选择运行模式

| 模式 | 适用场景 | 需要配置的内容 |
|---|---|---|
| 官方 | 直接使用 官方网关提供的模型 | 只需 DC Key |
| 自定义 | 所有模型都通过自定义 LocalAPI 配置 | 初始化 LocalAPI、供应商渠道、业务模型、Embedding、媒体模型 |
| LocalAPI + 官方混合 | 官方模型照常使用，同时新增本地 ComfyUI 视频模型，或用本地模型覆盖同名官方模型 | 官方 DC Key、LocalAPI、ComfyUI URL 和 Workflow |

配置完成后点击对应模式的启用按钮。更换模式、Key 或模型后，新任务会读取新配置；正在运行的任务不会中途切换。

## 官方模式

官方模式是最简单的接入方式：

1. 打开 **官方**。
2. 填写官方网关 DC Key。
3. 点击 **保存并启用**。

官方网关地址由 DramaClaw 固定管理。官方网关已配置 `DC-*-LLM`、`DC-cognee-embedding` 和官方媒体模型，不需要在 CE 中填写上游模型映射。

官方媒体模型及其分辨率、比例、时长和参考素材能力来自 CE 内置的 `src/novelvideo/official_media_models.json`。

### 更新官方媒体模型列表

官方模式和 LocalAPI + 官方混合模式会显示当前官方模型列表的版本、模型数量和来源：

- 点击 **立即检查更新**，可以从 DramaClaw 官方发布地址获取最新模型列表并立即应用。
- **自动更新官方模型列表**默认关闭。开启后，后端默认每 5 分钟检查一次；打开对应设置面板时也会立即检查。
- 下载的模型列表保存在本地 `state/local/official_media_models.json`，重启后继续生效。
- DramaClaw 不会安装低于当前生效版本的远端列表。应用升级后，如果新版内置列表比本地缓存更新，会优先使用新版内置列表。
- 开启自动更新后，已打开的画布浏览器每分钟观察一次目录状态；内容 SHA256 变化时会自动刷新图片和视频模型列表。
- 状态接口会返回当前内容的 SHA256、发布 Git revision、发布时间、远端地址和最近一次更新错误，便于确认每个实例实际使用的版本。

用户在自定义模式中维护的渠道、模型映射和能力配置不受官方模型列表更新影响。

官方目录默认使用成都地域的 `dramaclaw-dl` Bucket：`https://dramaclaw-dl.oss-cn-chengdu.aliyuncs.com/official-media-catalog/manifest.json`。manifest 指向按 SHA256 命名、永不覆盖的目录快照；后端会校验 manifest、目录版本和内容 SHA256，并使用 ETag 检查更新。可通过 `OFFICIAL_MEDIA_CATALOG_MANIFEST_URL` 覆盖默认地址；`OFFICIAL_MEDIA_CATALOG_URL` 是兼容旧部署的直接 JSON 地址。轮询间隔可通过 `OFFICIAL_MEDIA_CATALOG_POLL_SECONDS` 调整，最低为 60 秒。

仓库的 `publish-official-media-catalog` 工作流负责发布：先上传带长期不可变缓存头的 `catalogs/<sha256>.json`，最后上传缓存 60 秒的 `manifest.json`。以下密钥既可配置为仓库级 Secrets，也可配置在 GitHub `official-media-catalog` environment 中：

- Variables 可选：默认使用 `oss-cn-chengdu.aliyuncs.com`、`dramaclaw-dl` 和 `official-media-catalog`；可用 `OFFICIAL_CATALOG_OSS_ENDPOINT`、`OFFICIAL_CATALOG_OSS_BUCKET`、`OFFICIAL_CATALOG_OSS_PREFIX` 覆盖。
- Secrets：优先使用 `OFFICIAL_CATALOG_OSS_ACCESS_KEY_ID`、`OFFICIAL_CATALOG_OSS_ACCESS_KEY_SECRET`；未配置时复用组织级 `OSS_RELAY_AK`、`OSS_RELAY_SK`。

`dramaclaw-dl` Bucket 本身保持私有，只对 `official-media-catalog/*` 前缀授予匿名 `GetObject`，CI 身份仅需该前缀的写权限。模型目录仍以 Git PR 为唯一内容源；建议开启 Bucket 版本控制作为基础设施灾备。

还没有 DC Key 时，可前往 <https://susciyuan.com> 注册或购买。

## 自定义模式

### 1. 启动本地服务

使用仓库的 compose 文件（LocalAPI 始终包含在内）：

```bash
docker compose up -d
```

它会启动 DramaClaw API、Web 和 LocalAPI。默认情况下 DramaClaw 在容器网络中访问 LocalAPI；浏览器访问的宿主机端口可以不同，不需要把内部地址改成浏览器地址。

仓库编排已经启用设置页所需的初始化和渠道管理能力。LocalAPI 的 SQLite 数据保存在 Compose 的 `localapi-data` 卷中；LocalAPI 容器内路径是 `/data/localapi.db`，DramaClaw API 通过共享卷中的 `/localapi-data/localapi.db` 管理它。通常不需要手动填写 SQLite 路径或数据库 DSN。

### 2. 初始化 LocalAPI

打开 **自定义**。如果状态为“等待初始化”：

1. 为全新的 LocalAPI 设置 root 管理员密码并确认，密码至少 8 位。
2. 点击 **初始化 LocalAPI**。

初始化会：

- 首次运行时创建 LocalAPI 管理员；已经初始化时跳过该步骤。
- 创建或复用 `dramaclaw-ce-runtime` 运行令牌。
- 将运行地址和令牌保存到 CE 本地配置。
- 检查 LocalAPI SQLite 数据库与管理员访问是否可用。

DramaClaw 不保存管理员密码。初始化完成后请自行保管该密码，以便登录 LocalAPI 后台。对已经初始化的 LocalAPI 再填写密码不会重置原密码。

### 3. 使用推荐配置

初始化完成后，推荐先使用 **推荐配置**。一份配置会同时处理：

- 供应商渠道及上游 Key。
- DramaClaw 业务模型映射。
- Cognee Embedding 模型、维度和批量大小。
- 图片、视频和音频模型映射。

按渠道填写 API Key，然后点击 **保存并应用全部配置**。Key 独立保存，不会写入配置 JSON；已经保存的 Key 可以留空，重新输入会替换旧值。

当前推荐模板是 VolcEngine Ark 单渠道：文本、视觉、Embedding 与 Seedance 视频媒体模型都走 VolcEngine。语音合成（IndexTTS2）走运行时生效的 LocalAPI 网关渠道。

内置推荐配置是只读模板。切换到 **我的配置** 后可以编辑 JSON，保存后的个人配置会在下次打开时恢复。JSON 的主要结构如下：

```json
{
  "version": 2,
  "name": "My CE profile",
  "channels": [
    {
      "id": "openrouter",
      "provider": "openrouter",
      "baseUrl": "",
      "priority": 0,
      "settings": {}
    }
  ],
  "featureModels": {
    "text": {"channel": "openrouter", "model": "upstream-text-model"},
    "vision": {"channel": "openrouter", "model": "upstream-vision-model"},
    "overrides": {}
  },
  "embedding": {
    "channel": "openrouter",
    "model": "upstream-embedding-model",
    "dimension": 1024,
    "batchSize": 10
  },
  "mediaModels": {
    "my-video-model": {
      "channel": "openrouter",
      "model": "upstream-video-model",
      "mediaType": "video",
      "label": "My Video Model",
      "enabled": true,
      "sortOrder": 100,
      "config": {}
    }
  }
}
```

`channel` 引用 `channels[].id`。当前一个 profile 中同一 `provider` 只能配置一次。切换配置或查看 JSON 只会切换待应用模板，不会覆盖当前已经生效的高级配置；点击 **保存并应用全部配置** 后，供应商渠道、业务模型、Embedding 和媒体模型才会一起写入后端并更新下方列表。高级配置保存后会同步为当前个人配置，避免两套配置同时生效。推荐配置升级后，如果下方仍显示旧渠道或旧模型，重新应用推荐配置即可，不需要清理浏览器数据。

推荐配置不默认创建 ComfyUI 渠道；需要使用本地 MiniMax H3 时，请在高级配置中先通过 **渠道管理** 添加 ComfyUI，再在该渠道的 **ComfyUI Workflows** 区域载入模板。模板会补入 Workflow，并生成对应的 `MiniMax-H3-local` 媒体模型草稿。

### 4. 高级配置

高级配置用于逐项调整推荐配置应用后的结果。

#### 供应商渠道

点击 **渠道管理** 打开当前 LocalAPI 支持的供应商目录。渠道类型及能力从 `/api/channel/types` 动态读取，每个供应商只能添加一次。目录支持按名称搜索，以及按文本、视觉理解、Embedding、图片、视频、音频和音乐等能力筛选；刚添加的渠道会显示在已配置渠道列表顶部。渠道目录加载失败时，先确认 LocalAPI 正常运行，再点击 **重新加载**。

业务模型、Embedding 和媒体模型的渠道选择框会按照用途筛选：例如视频模型只列出支持视频能力的渠道，文本模型只列出支持文本能力的渠道。已有配置或暂时缺少能力元数据的渠道仍会保留，便于检查和修正，不会在升级后直接消失。

- **保存渠道配置**：保存 CE 本地渠道预设。
- **更新 LocalAPI 渠道**：立即更新 LocalAPI 中对应渠道的 Key 和 Base URL。
- **Base URL 覆盖**：通常留空，使用渠道默认地址；只有自建代理或供应商要求时填写。

删除普通渠道前会显示受影响的业务模型、媒体模型和 Embedding 映射数量。删除已保存渠道会立即把剩余渠道列表写回后端；如果后端保存失败，当前本地配置会保留。删除最后一个普通渠道同样会持久化为空列表，刷新后不会恢复。ComfyUI 使用独立的清理确认，详见下文。

保存推荐配置后，渠道 Key 应显示为“已保存”及脱敏预览。输入框中只有密码圆点且没有“已保存”标记时，它仍是尚未提交的草稿。

#### 业务模型

DramaClaw 使用稳定的内部逻辑模型名，例如 `DC-character-builder-LLM`、`DC-scene-builder-LLM` 和 `DC-freezone-vision-LLM`。自定义模式下，应保留这些内部名称，在 LocalAPI 渠道中把它们映射到真实上游模型。

- 文本理解与生成可以选择普通文本模型。
- 视觉理解功能会发送图片或视频，必须选择支持相应输入的多模态模型。
- 批量填充只修改页面草稿，仍需点击保存映射。
- Hermes 可以使用独立模型；其他 `DC-*-LLM` 可以按需要统一映射或单独覆盖。
- 升级后如果新增了功能行（例如 v2.0.3 新增的 `DC-character-builder-LLM`），新行默认为空，不会写入 LocalAPI；需要选好模型后点「保存映射」，或重新应用一次快捷配置。在此之前该功能会报 `No available channel for model DC-...`。

#### 功能位与一键绑定

`DC-*-LLM` 是渠道里的逻辑模型名；在它们之上还有一层**功能位**（模型槽位），每一位对应一处具体能力（故事抽取、剧本、角色、场景、分镜、视觉校验、风格参考等）。

- **真源是本项目的设置存储**：功能位到模型的别名映射与媒体映射都存在设置里，LocalAPI 渠道只是投影。模型名与凭据一律从「设置」解析，`.env` 不决定用哪个模型（少数不经网关的直连旁路除外，见[环境变量参考](../reference/environment-variables.md)的例外小节）。
- **运行前设置 → 整链模型**：为整条链选一次，用到的每个功能位都会指到它（故事、剧本、资产、分镜、风格）。系统只写入该供应商目录里真实存在的模型名；需要看懂图片的能力位会从同一渠道挑支持视觉输入的模型，挑不到就留空并说明原因。留空表示沿用现有设置。
- **模型广场**（只读）：列出本项目能被调用的模型，以及每个模型被哪些功能位使用；「该功能位还没绑定模型」「N 个功能位无别名映射」就是绑定缺口的提示。

功能位解析不到模型时不会回落到任何预设模型名，而是直接失败并用中文说明缺的是哪一处、去哪里绑。看到这类提示请去绑定，反复重试不会有效果。

#### Embedding

`DC-cognee-embedding` 用于小说知识图谱和语义检索。需要设置：

- 上游 embedding 模型。
- 模型输出维度。
- 批量大小，默认 10。

Embedding 模型和维度在项目创建时绑定。修改配置只自动影响新项目；已有项目更换模型或维度前，需要清空并重建知识图谱。

出现 embedding HTTP 400/422 时，优先检查模型是否支持配置维度，以及批量大小是否超过上游单次 `input` 上限。

#### 图片、视频、音频和音乐模型

自定义模式下，逐模型的详细能力不再单独占一块表：在 **默认生成模型** 面板里，每个图片 / 视频 / 音频 / 音乐模型行的 **设为默认** 左侧都有 **模型详细设置** 按钮，点开就是能力编辑器（编辑媒体模型），不需要展开高级配置。文本、视觉理解、Embedding 和「其它」分类没有逐模型能力配置，因此不显示该按钮。混合模式下同一套能力编辑器位于 **ComfyUI 配置** 内。

「模型详细设置」与「设为默认」写的是同一份媒体模型映射：前者只改该行的能力、渠道、显示名称和启用状态，不改默认归属；后者只改默认归属。保存后两者都会立即写回 LocalAPI。

媒体模型配置同时决定：

- 画布中是否显示该模型。
- 显示名称与排序。
- 发送给 LocalAPI 的上游模型名。
- 分辨率、比例、图片质量、时长等控件选项。
- 视频模型的时长范围：在 **模型详细设置** 里填写 **最短时长（秒）** 和 **最长时长（秒）** 后，画布上的时长滑杆与输入框就用该范围；两项都留空时画布按 5–15 秒兜底，即使上游模型支持更短或更长。
- 文生视频、首帧、首尾帧、图片参考、全能参考和视频编辑等能力。
- 参考图片、视频和音频数量上限。
- 是否显示真人审核选项。
- 模型专属请求参数。

「音乐」条目只用于选型号与站点渠道：网关里没有音乐路由，保存时不会被补上 `audio/speech` 之类的端点后缀，工具页「音乐创作」按厂商音乐协议直连该渠道的 Key。注意它与 ComfyUI 的本地 `MiniMax-H3-local` 模板无关，那是本机视频工作流的名字。主线内置模型提供默认能力基线，不能从配置中删除。我的配置可以新增图片或视频模型，并编辑自定义模型能力。保存后刷新画布，模型列表和控件会使用最新配置。

模型 ID 是 DramaClaw 使用的稳定名称，**上游模型名**是 LocalAPI 渠道实际调用的名称，两者可以不同。

### 5. ComfyUI 配置

在 **自定义** 模式中，ComfyUI 通过 **高级配置 → 供应商渠道** 添加。在 **LocalAPI + 官方混合** 模式中，通过独立的 **ComfyUI 配置** 添加。两种模式读取同一个 LocalAPI 和 SQLite 数据，也使用相同的渠道编辑器。

点击 **渠道管理**，从渠道目录添加 ComfyUI 后，渠道下方会出现 **ComfyUI Workflows** 区域。只有这时才显示 **载入 MiniMax H3 模板**；没有添加 ComfyUI 的用户不会看到模板入口。载入模板会填入三条初始 Workflow，并在 Base URL 为空时填入 `http://127.0.0.1:8188`，但不会覆盖已有的非空地址或同 ID Workflow。载入后仍需保存渠道和视频模型配置，才会写入 LocalAPI。

每个 ComfyUI 渠道配置需要：

- 一个 DramaClaw 使用的模型名称。该名称会注册到 LocalAPI，并显示在画布中。
- ComfyUI 服务地址；本机默认是 `http://127.0.0.1:8188`。
- 一条或多条 Workflow。每条包含唯一的 **Workflow ID** 和从 ComfyUI 导出的 **API Format Workflow JSON**，不能使用浏览器工作流 JSON。
- 该模型的媒体能力，例如支持模式、比例、分辨率、时长和参考素材数量。

普通本地 ComfyUI 不需要 API Key，可以留空。只有在 ComfyUI 前方部署了要求认证的代理时，才需要按代理方案提供认证信息。

同一个模型名称可以绑定多条 Workflow。DramaClaw 将模型名称、Workflow ID 和 Workflow JSON 保存到 LocalAPI，具体选择哪条 Workflow 由网关处理；画布只显示一个统一模型，不再为每条 Workflow 创建一个模型。

MiniMax H3 模板使用模型名 `MiniMax-H3-local`，内置文生、首帧和全能参考三条 Workflow。初始媒体能力为文生视频、首帧和全能参考，分辨率为 `480p`、`768p`、`1080p`，比例为 `21:9`、`16:9`、`4:3`、`1:1`、`3:4`、`9:16`。这些是初始值，仍可在媒体模型能力配置中调整。

删除单条 Workflow 只删除该路由，不会自动删除统一模型。需要彻底移除时，点击仅在已配置后显示的 **清除 ComfyUI 配置**，确认后会删除本地和 LocalAPI 中的 ComfyUI 渠道、Workflow 及对应媒体模型映射；项目和已生成媒体不会被删除。

## LocalAPI + 官方混合模式

混合模式用于保留官方网关，同时让指定视频模型从本地 ComfyUI 生成：

1. 先在 **官方** 保存 DC Key。
2. 在 **自定义** 中初始化一次 LocalAPI；同一个 SQLite 不需要重复初始化。
3. 打开 **LocalAPI + 官方混合**。
4. 打开独立的 **ComfyUI 配置**，点击 **渠道管理** 并从目录中添加 ComfyUI。
5. 在该渠道的 **ComfyUI Workflows** 区域载入 MiniMax H3 模板；也可以填写自己的本地视频模型名称，再添加一条或多条 Workflow ID 和 **API Format Workflow JSON**。本机默认地址是 `http://127.0.0.1:8188`。
6. 保存视频配置并启用混合模式。

ComfyUI API Key 是可选项。Workflow 必须是 API Format，而不是浏览器工作流格式。自定义模式与混合模式共享 ComfyUI 渠道、Workflow 和媒体模型能力配置；任一模式保存后，另一模式会读取相同结果。

ComfyUI 渠道存在时，MiniMax H3 模板按钮会保留在该渠道的 **ComfyUI Workflows** 区域，方便恢复缺少的模板。重复载入时会把模板合并到现有 Workflow 中，保留用户已经配置的同 ID Workflow；当 ComfyUI 地址为空时，会自动填入 `http://127.0.0.1:8188`，不会覆盖非空的自定义地址。

混合模式按模型 ID 路由：本地 ComfyUI 模型可以作为新模型加入画布；本地存在与官方同名的视频模型时，则使用本地模型覆盖该官方模型。其他模型继续使用官方网关。保存视频配置时，DramaClaw 会先保存 ComfyUI 渠道，再保存对应媒体模型。DramaClaw 不会在本地生成失败后自动回退官方，是否重试或改选官方模型由用户决定。混合模式只管理本地视频模型，不要求再次配置 OpenRouter、火山等官方上游渠道。

## 参考媒体存储

图生图、视频首帧、首尾帧、参考图片和身份图等功能需要让上游模型读取本地文件。进入 **设置 → 媒体存储** 选择存放方式。

### 本地存储（默认）

CE 默认档位就是 **本地存储**，未配置过时也按它处理。生成产物直接存在本服务磁盘；参考图片内联成 data URL 交给上游，无需对象存储。

参考视频与参考音频（替换视频人物 / 替换视频产品、对口型等）必须换公网直链，这一步走 AIPDD 数字资产云。凭据自动取自已连接的 **AIPDD 渠道**——前面在「模型与渠道」里连过，这里就不用再填 Key，站点地址也自动取渠道站点根；只有尚未连接 AIPDD 渠道时才需要在表单里补填。

### AIPDD 数字资产云

所有参考媒体先上传到 AIPDD 数字资产云，换取公网直链后再交给上游抓取。同样复用已连接 AIPDD 渠道的 Key 与站点地址，按钮旁会提示「凭据已就绪」；未就绪时才显示 API Key 与站点地址输入框。

### 阿里云 OSS

需要 Bucket 和拥有该 Bucket 读写权限的 AccessKey。推荐使用只授权该 Bucket 的 RAM 子账号。

| 网页字段 | 环境变量 | 示例或说明 |
|---|---|---|
| Endpoint | `OSS_RELAY_ENDPOINT` | `oss-cn-chengdu.aliyuncs.com`，不要带 `https://` |
| Bucket | `OSS_RELAY_BUCKET` | 临时媒体 Bucket 名称 |
| AccessKey ID | `OSS_RELAY_AK` | Bucket 有限权限的 AK |
| AccessKey Secret | `OSS_RELAY_SK` | 对应 SK |
| 有效期 | `MEDIA_RELAY_TTL_SECONDS` | 默认 1800 秒 |

Bucket 无需公开读；DramaClaw 使用临时签名 URL 授权上游读取。

### Cloudinary

填写 Cloud name、API Key、API Secret 和可选文件夹。可在 Cloudinary 控制台的 **Product environment settings → API Keys** 查看这些值。保存后本地数据库配置优先于环境变量，完整密钥不会返回前端。

## 常见问题

| 现象 | 检查方法 |
|---|---|
| 保存 Key 后高级配置仍没有“已保存”标记 | Key 可能只存在于页面草稿。重新保存对应渠道或重新应用完整配置，并确认使用的是包含最新代码的镜像。 |
| 添加媒体模型时提示缺少供应商 Key | 对应供应商渠道尚未真正写入 LocalAPI；先保存/更新渠道，再保存媒体模型。 |
| LocalAPI 报 `No available channel for model ...` | 检查逻辑模型映射、渠道是否启用、上游模型名及分组。 |
| 走 Codex 类中转站（Codex2API 等）时结构化环节报 `Exceeded maximum output retries` | 中转站在 `/v1/chat/completions` 上丢掉了 `tool_calls`。到 LocalAPI 后台给该渠道开启 **ChatCompletions → Responses Compatibility**。详见[排错指南](../guides/troubleshooting.md#模型--网关类)。 |
| LocalAPI 初始化失败 | 检查 LocalAPI 服务、SQLite 挂载、目录权限和 `LOCALAPI_PROVISIONER_ENABLED`。 |
| 画布没有显示新增模型 | 确认模型已启用、媒体类型正确、已保存全部配置并刷新页面。 |
| 模型控件与实际能力不一致 | 检查媒体模型的 `config`，尤其是分辨率、比例、模式和参考素材上限。 |
| 知识图谱 embedding 失败 | 检查 embedding Key、上游模型、维度和批量大小；429 表示上游限流。 |
| 参考图或首帧无法读取 | 检查媒体存储配置和临时 URL 是否能被上游公网访问。 |
| 生成到「调用视频生成器」就报 `unsupported MEDIA_RELAY_PROVIDER: -` | 媒体存储档位为空（旧版本在未配置时不落默认档）。当前版本空值按 **本地存储** 处理；仍报错则到 **设置 → 媒体存储** 重新选择一次档位。 |
| 提示「媒体存储档位不可用」 | 档位值不在支持列表内（本地存储 / AIPDD 数字资产云 / 阿里云 OSS / AWS S3 / Cloudinary），重新选择一个受支持档位后重试。 |
| 本地存储档下参考视频 / 音频上传失败 | 只影响需要公网直链的参考视频与音频；确认已连接 AIPDD 渠道，或在该档表单里补填 AIPDD API Key。 |
| 混合模式本地视频失败后没有走官方 | 这是预期行为；混合模式不做自动失败回退。 |
| ComfyUI 无法连接 `127.0.0.1:8188` | `127.0.0.1` 指 DramaClaw 后端所在环境。容器或远程部署时改为后端可访问的宿主机名或局域网地址。 |
| MiniMax H3 Workflow 执行时报节点或模型缺失 | 确认 ComfyUI 已安装推荐 Workflow 使用的自定义节点和模型文件，并按本机安装情况修改 Workflow。 |

## 相关文件

- `src/novelvideo/official_media_models.json`：CE 官方媒体模型与能力。
- `.env.example`：环境变量参考。
- `docker-compose.yml`（源码构建）/ `docker-compose.release.yml`（镜像）：部署文件（api + LocalAPI + web），网关模式在设置页选择。
- [自托管手册](../guides/self-hosting.md)
- [环境变量参考](../reference/environment-variables.md)
