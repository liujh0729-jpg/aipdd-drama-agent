<!-- lang-switch -->
[English](../../en/guides/mcp-claude-code.md) · **简体中文**

# 用 AI 客户端驱动 AIPDD-Agent-Claw（MCP）

AIPDD-Agent-Claw 内置一个 [MCP](https://modelcontextprotocol.io) 服务器，把整条短剧管线
—— 导入、角色、剧本、分镜、首帧、视频、音频、合成、导出、剪映 / CapCut
剪辑草稿，工具页的批量处理（视频复刻 / 混剪短视频 / 白板动画视频 / 科普讲解视频 / 渠道短视频 / 一句话生成短视频 /
公众号文章编写与发布 / 人声复刻 / 对口型 / 换人 / 去水印 / 抖音文案提取），一键自动化（故事→分镜步进
驱动器、按分镜区间批量生视频），以及画布（freezone）的生图 /
图片编辑 / 生视频 / 音频 / 文本脚本 / 资源目录读，环境自动安装（`aipdd_drama_env_setup`：探测运行依赖 → 待办计划 → 逐项安装），环境与代码自愈（`aipdd_drama_self_heal`：调用失败时交给默认文本模型分析，`diagnose` 只诊断、`repair` 仅执行白名单修复），可执行工作计划（`aipdd_drama_get_plan` / `aipdd_drama_update_plan` / `aipdd_drama_run_plan`），小说故事源读写、章节读写与导入、动效大片（MG）目录 / 渲染 / 静帧 / 剪映草稿、成片与整集导出 —— 共 122 个工具。
任何会说 MCP 的客户端（**Claude Code**、Codex 等）都能用它从头做完一部短剧。

服务器（`src/novelvideo/chat/dramaclaw_mcp.py`）是 AIPDD-Agent-Claw REST API 之上的 薄
stdio 桥：每个工具就是对你自己实例的一次 HTTP 调用，因此天然继承与网页端一致的
鉴权、项目护栏与任务队列。

## 前置条件

- 一个正在运行的 AIPDD-Agent-Claw 实例（`docker compose up -d`，REST API 在 `:8780`）。
- 已配置、可用的模型网关（设置 → 模型与渠道）—— 规划 / 写剧本不依赖模型，但
  图像 / 视频 / 音频生成需要真实可用的模型。生成可走 **AIPDD 平台的低成本闲置
  算力**、你在 **LocalAPI** 里注册的任意渠道，或**本机 ComfyUI 自持 GPU**——详见
  [模型指南](../../../README.md#supported-models)。
- 本机有仓库副本并同步过依赖（`uv sync`），使 `novelvideo` 包可导入。

## 认证：两种模式

服务器通过环境变量向 API 认证：

| 变量 | 用途 |
|---|---|
| `DRAMACLAW_API_URL` | 实例地址，如 `http://localhost:8780` |
| `DRAMACLAW_CE_OWNER` | 设为 `1` 走 **CE**（自托管）模式：以本机 owner 身份调用，无需令牌。目标**必须是回环地址**（`localhost`、`127.0.0.1`、`::1`、`[::1]`） |
| `DRAMACLAW_CE_OWNER_ALLOW_REMOTE` | 危险开关：设为 `1` 才允许无令牌的 CE-owner 模式连**非回环**地址（见下方警告） |
| `DRAMACLAW_AGENT_TOKEN` | 令牌模式的 Bearer 凭证：`st-…` **统一 API 密钥**（设置 → API 密钥；与站内 MCP 页面、模型广场 OpenAI 兼容入口共用同一把），或 EE 的受限 agent 令牌 |
| `DRAMACLAW_PROJECT_ID` | 可选：默认项目，工具即可省略 `project_id` |

**社区版**默认信任本机请求为 owner，`DRAMACLAW_CE_OWNER=1` 就够了 —— 无需
铸造令牌。一旦设置了 `DRAMACLAW_AGENT_TOKEN`，它始终优先：服务器改为发送
`Authorization: Bearer <令牌>`，不再以本机 owner 身份调用。最简单的令牌就是
**设置 → API 密钥**里生成的 `st-…` 统一密钥 —— 站内 MCP 页面叫你粘贴的、模型
广场接受的，都是同一把。EE 场景请改用受限 agent 令牌；服务器会强制它的项目
边界与写权限。统一密钥刻意不能访问管理端点（如密钥管理）—— 那留给交互式会话。

**强制回环。** 无令牌 owner 模式发出的请求不带鉴权却具备 owner 级权限，因此
桥会拒绝任何主机不是回环地址的 `DRAMACLAW_API_URL`（`localhost`、`127.0.0.1`、
`::1`、`[::1]`），并给出明确报错。确实需要无令牌访问远程 CE（比如你完全掌控的
SSH 隧道）时，用 `DRAMACLAW_CE_OWNER_ALLOW_REMOTE=1` 显式开启。它是独立变量，
刻意做到不可能被误开；任何非本机目标都优先用受限的 `DRAMACLAW_AGENT_TOKEN`。

## 接入 Claude Code

仓库自带项目级 [`.mcp.json`](../../../.mcp.json)。用 Claude Code 打开本仓库，
在提示时批准 `dramaclaw` 服务器即可。

也可以显式添加：

```bash
claude mcp add dramaclaw \
  --env DRAMACLAW_API_URL=http://localhost:8780 \
  --env DRAMACLAW_CE_OWNER=1 \
  -- uv run python -m novelvideo.chat.dramaclaw_mcp
```

验证工具已生效：

```bash
claude mcp list        # dramaclaw → ✓ connected
```

> **只用 Docker 跑？** 直接把桥放进容器里启动：
> 把 `.mcp.json` 的 command 换成
> `docker compose exec -T -e DRAMACLAW_API_URL=http://localhost:8780 -e DRAMACLAW_CE_OWNER=1 api python -m novelvideo.chat.dramaclaw_mcp`。

### 站内 MCP 页面：按运行形态生成配置

站内 **MCP → 本地 Agent 工具** 可一键把 Workbuddy / Claude Code / Cursor / Codex
接到当前实例，写入的条目随运行形态变化：

- **Docker 部署**：`docker exec -i <容器名> python -m novelvideo.chat.aipdd_drama_mcp`，
  容器名由 `AIPDD_DRAMA_MCP_CONTAINER` 指定（缺省 `aipdd-drama-api`，与发布编排一致）；
- **桌面版（exe / dmg）**：直接使用随包 Python（`sys.executable -m …`），令牌与项目号
  走条目 `env`，无需另装 Docker 或 uv。

内置服务器（Chrome DevTools / Blender / ComfyUI）在页面上按运行环境标注可用性：
本机缺少 `chrome-devtools-mcp` 或 `uvx` 时显示「不可用」并给出安装命令，Agent
回合不会再尝试拉起缺失的命令；装好后下一次请求自动恢复，无需改配置。

## 代理能做什么

工具覆盖完整管线。一次典型的端到端流程：

1. `aipdd_drama_start_ingest` → `/projects/{p}/ingest/start` —— 导入已上传稿件
   （上传本身走网页/客户端 multipart；`aipdd_drama_post` 仍可直调同一端点）
2. `aipdd_drama_build_characters` → 抽取角色
3. `aipdd_drama_plan_episodes` → 分集
4. `aipdd_drama_plan_identities` → 每集身份
5. `aipdd_drama_get_episode_story` / `aipdd_drama_optimize_episode_story` /
   `aipdd_drama_save_episode_story` → 剧集「故事」读写与 AI 优化；`aipdd_drama_story_to_script`
   → 故事转剧本：自动导入剧本模板并触发 `script_writer`（等价于剧本生成）
6. `aipdd_drama_generate_script` → 剧集剧本（无故事、直接生成时的入口）
7. `aipdd_drama_generate_portrait` / `aipdd_drama_generate_scene_master` → 关键美术
8. `aipdd_drama_generate_sketches` → 分镜
9. `aipdd_drama_render_first_frames` → 首帧
10. `aipdd_drama_start_single_video` → 单镜视频
11. `aipdd_drama_generate_audio` → 配音
12. `aipdd_drama_compose_episode` → 合成
13. `aipdd_drama_get_final_video` → 成片（返回可直接访问的 URL）
14. `vectcut_*` → 把分镜 / 配音 / 字幕组装成剪映 / CapCut 剪辑草稿（需启动 VectCut
    剪辑服务：本机 `scripts/start-vectcut.sh`，见 `services/vectcut/`）
15. 工具页批量工具：`aipdd_drama_video_clone`（视频复刻：可带提示词与参考内容图
    ≤4 张共同作用于复刻）、`aipdd_drama_video_mix`
    （混剪短视频：素材池按序循环铺满 + 配音 + 字幕 + BGM）、`aipdd_drama_video_idea`
    （一句话生成短视频：创意 → 故事 / 角色 / 分镜 → 逐镜生成 → 拼接成片）、
    `aipdd_drama_narration_smart_edit`（口播智能剪辑：一条口播视频 → 本地静音剪 +
    网关 ASR/文本瑕疵剪与分镜/B-roll 判定 + 生图/BGM → 直出 MP4 或 VectCut 剪映草稿，
    或两者；各阶段模型可留空走默认）、
    `aipdd_drama_voice_clone`、`aipdd_drama_lip_sync`、`aipdd_drama_replace_person`、
    `aipdd_drama_video_watermark`、`aipdd_drama_image_watermark` —— 生成类产物默认
    「生成就入库」，自动登记进资产库「待分类资产」（显式传 `archive_to_library=false`
    关闭）；另有 `aipdd_drama_douyin_copywrite`（抖音文案提取：分享
    链接 → 解析视频 → 按选定 ASR 模型转写口播文案，模型候选见
    `/freezone/douyin-copywrite/models`）——文案全文随任务结果返回，产物 txt 默认
    同样归档进资产库「文本」载体（同样可显式关闭）
16. 画布生成工具（与网页端 freezone 节点完全同源，提交后返回 `job_id`，按描述
    轮询对应任务类型）：**生图** `aipdd_drama_image_generate`（文生图 / 参考图）、
    `aipdd_drama_image_edit`（指令编辑，可带额外参考图，产物默认入库）、
    `aipdd_drama_image_upscale`（超分）、`aipdd_drama_image_outpaint`（扩图）、
    `aipdd_drama_image_relight`（重打光）、`aipdd_drama_image_multi_view`（多视角）、
    `aipdd_drama_image_template_edit`（模板编辑，`mode` 必填）、
    `aipdd_drama_image_redraw`（局部重绘，可带 mask）、`aipdd_drama_image_reverse_prompt`
    （图生提示词）；**生视频** `aipdd_drama_video_generate`（文生视频）、
    `aipdd_drama_video_i2v`（图生视频，`gen_mode` 取 imageToVideo / imageReference）、
    `aipdd_drama_video_omni_gen`（全参考 omni-gen）、`aipdd_drama_video_upscale`、
    `aipdd_drama_video_edit`、`aipdd_drama_video_extend`（续写）—— 画布生成类节点
    默认「生成就入库」，产物完成后自动登记进「待分类资产」（处理/变换类同样登记，
    只有抽帧、分析等中间过程不入库）；**音频**
    `aipdd_drama_audio_separate`（人声 / 伴奏分离）、`aipdd_drama_audio_music`
    （ElevenLabs 音乐）；**文本脚本** `aipdd_drama_text_generate`、
    `aipdd_drama_text_translate`、`aipdd_drama_story_script_generate`（故事转分集脚本）、
    `aipdd_drama_video_script_reverse`（视频反推脚本）、`aipdd_drama_media_copywrite`
    （素材文案）—— 内联纯文本产物不单独建条目；落成 txt 文件的反推脚本 / 文案转写
    默认归入「文本」载体，全文不进 `/result` 白名单，用
    `aipdd_drama_get_task` 读取 `task.result`；**资源目录读** `aipdd_drama_list_assets`、
    `aipdd_drama_get_character_library`、`aipdd_drama_get_audio_references`、
    `aipdd_drama_get_models`（按 `kind` 映射 image / video / audio / text / vision 目录）。
17. 一键自动化：`aipdd_drama_auto_storyboard`（自动化分镜步进驱动器：上传故事后
    自动分析风格→转剧本→分集→分镜脚本→草图→镜头首帧，每次调用只推进一段并
    返回 `stage` / `next_action`，反复调用直到 `done`；可选模型 / 风格 / 画幅 /
    集数 / 总时长 / 单集时长，系统按剧本自动推导分镜数；**不生成视频**）。
    该工具与下面的 `aipdd_drama_run_pipeline` 都只是**薄壳**：阶段状态机统一到后端
    权威计划器 `POST /api/v1/projects/{project}/pipeline/plan`（默认 `execute=false`
    只“决策不发射”，`execute=true` 才就地发射下一段动作），MCP 侧不再各写一份逻辑。
    `aipdd_drama_video_range`（按分镜编号或区间如 `1-8` 一次性并发提交镜头视频，
    需选视频模型；缺首帧的分镜自动跳过并在 `skipped` 中报告，逐个用
    `aipdd_drama_get_task(task_type="single_video", beat_num=…)` 轮询），`aipdd_drama_regen_sketches`
    （按分镜编号或区间如 `1-8` 批量重生成镜头草图，省略分镜则整集，需已有分镜脚本，用
    `aipdd_drama_get_task(task_type="sketch_regen", episode=…)` 轮询），以及
    `aipdd_drama_generate_video_prompts`（按分镜编号或区间逐个批量生成镜头视频提示词，
    缺前置内容的分镜在 `skipped` 中报告，用 `aipdd_drama_get_task(task_type="beat_video_prompt", beat_num=…)` 轮询）。
18. 编排层（自动化生产线，把选择权给用户）：`aipdd_drama_run_pipeline`（把同一个
    后端计划器包成三档：`manual` 逐段推进；`checkpoint`（默认）先“决策不发射”，返回
    `planned_requests` 闸门，带 `approve=true` 再调才真正触发该阶段——系统主导 + 用户
    逐段确认；`auto` 在 `budget_s` 内自动推进到**镜头首帧完成**（**绝不提交视频**，
    视频请改用 `aipdd_drama_video_range`）；`use_suggested=true` 用智能默认补齐模型/画幅/风格），
    `aipdd_drama_get_run_status`
    （聚合流水线进度 + 正在运行/失败/已完成任务 + 本集镜头计数）、`aipdd_drama_cancel_run`
    （一键取消本集在跑任务）与 `aipdd_drama_suggest_config`（按模型目录与画幅给出推荐生成
    配置与理由，先看好再生成）。
19. **可执行工作计划（用户设计划 → Agent 自动做）**：`aipdd_drama_update_plan`
    （add / update / complete / remove；条目带 `stage` 里程碑——story / screenplay /
    ingest / characters / episodes / identities / script / scenes / props / sketches /
    frames——和可选 `episode` 集号，写入后即可执行；不带 stage 的是手工待办）、
    `aipdd_drama_get_plan`（读取计划、未完成数与可执行条目，供 Agent 每轮先确认再动手）、
    `aipdd_drama_run_plan`（`mode=auto` 在 `budget_s` 内按计划连续推进；`manual` 只推
    一段；`checkpoint` 只预览。执行器复用同一个后端计划器，按**真实产物**（故事正文、
    角色、分集、身份、分镜脚本、场景/道具、草图、首帧）回勾任务，重复调用幂等，
    已完成的条目不会重跑；同样**只推进到镜头首帧，绝不生成视频**）。
    对应的 HTTP 接口是 `GET/POST/PATCH/DELETE /api/v1/agent/plan*` 与
    `POST /api/v1/agent/plan/run`，Agent 页右侧「待办计划」面板可直接查看阶段与执行。
20. **小说故事源 / 章节 / 动效大片 / 成片导出**：`aipdd_drama_get_story_source` /
    `aipdd_drama_save_story_source`（用户在 Agent 里直接粘贴整本小说的入口，与网页导入
    同口径校验）、`aipdd_drama_get_chapters`（章节结构；默认只给
    元数据，`chapter=N` 读单章正文）、`aipdd_drama_update_chapter`（保存单章正文，标题行
    由后端保留）、`aipdd_drama_start_ingest`（对 `uploads/` 里已上传的文件启动
    `ingest_fast`，先配 `aipdd_drama_list_ingest_uploads`）；动效大片
    `aipdd_drama_motion_cases` / `aipdd_drama_motion_styles`（案例与画风图册，给 id 即返回
    完整提示词）、`aipdd_drama_motion_renders`（已渲染成片清单）、
    `aipdd_drama_motion_render`（一镜 MG 成片，`freezone_motion_render`）、
    `aipdd_drama_motion_stills`（拍点静帧预检，`freezone_motion_stills`）、
    `aipdd_drama_motion_draft`（逐镜成片 → 剪映草稿，`freezone_motion_draft`）；导出
    `aipdd_drama_get_subtitles`（一集 SRT 文本）与 `aipdd_drama_export_episode`
    （`kind=video` 下载成片 mp4，`kind=zip` 下载整集资源包，流式落盘并返回本地路径）。

生成是**异步**的：工具调用启动任务，之后代理轮询 `aipdd_drama_pipeline_status` /
`aipdd_drama_list_tasks` / `aipdd_drama_get_task` 直到完成。每个工具的描述里写明了
该轮询哪个任务类型。通用的 `aipdd_drama_get` / `aipdd_drama_post` /
`aipdd_drama_patch` / `aipdd_drama_delete` 是逃生通道：任何没有被专门工具覆盖的
端点都能调。

> **画布（freezone）。** 常见生成 / 编辑 / 资源动作都已提炼为上面的专门工具，
> 参数与网页端节点一一对应。画布内部操作（canvases CRUD / restore / projections /
> 3D / screenshot / marks / detect）没有专门工具，用通用动词走逃生通道：
> `aipdd_drama_get('/projects/{p}/freezone/canvases')` 列出画布，`…/freezone/canvases/{canvas_id}`
> 查看节点与生成历史，`aipdd_drama_post('/projects/{p}/freezone/gen', {…})` 触发与画布
> 节点等价出图 —— 与网页端操作完全同源。

> **动效大片（MG）** 已有专门工具：`aipdd_drama_motion_cases` /
> `aipdd_drama_motion_styles` 读案例与画风图册（给 `case_id`/`style_id` 返回完整提示词），
> `aipdd_drama_motion_render` 起渲染（`html` 直贴页面，或 `case_id`/`style_id` + `subject`
> 让模型按契约写页面），`aipdd_drama_motion_stills` 先按拍点出静帧预检，
> `aipdd_drama_motion_renders` 列已渲染成片（事实来源是磁盘上的 `*.motion.json`
> sidecar），`aipdd_drama_motion_draft` 导出剪映 10.2 草稿。任务类型是
> `freezone_motion_render` / `freezone_motion_stills` / `freezone_motion_draft`，用
> `aipdd_drama_get_task(task_type=..., episode=0, scope=<job_id>)` 轮询。这条链路依赖
> 本机 Chrome 与 ffmpeg，见[动效大片指南](motion-feature-film.md)。

## 安全提示

- CE-owner 模式对所指向实例拥有**完整 owner 权限** —— 只对你自己掌控的**本机**
  CE 使用，切勿指向共享实例。
- 桥强制了这一点：无令牌 CE-owner 模式只接受**回环** `DRAMACLAW_API_URL`，
  远程主机会直接报错，除非你显式设置 `DRAMACLAW_CE_OWNER_ALLOW_REMOTE=1`。
- EE 场景使用受限 `DRAMACLAW_AGENT_TOKEN`；服务器会强制令牌的项目边界与写范围。