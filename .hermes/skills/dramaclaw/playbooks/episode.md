# 逐集生成阶段（Steps 8-21）

> API 请求细节不确定时，`Read references/pipeline-details.md`。
> 变量：`$EP` = 当前集数。
> **首步**：`GET /projects/{project}` 读取项目配置（rhythm, tts_provider, video_resolution），用于 `{项目...}` 占位符；视频后端默认统一使用 `huimeng_seedance-1.0-pro-fast`。

## 流水线
Steps 8-21 详见 `references/pipeline-details.md`。检查点：CP2(场景/道具后) | CP3(草图后) | CP4(音频后) | CP5(最终成片后)

CP2 判定：脚本、场景/道具上下文足够推进草图；当前后端没有 `anchor_image_url` 契约。

## 步骤要点

**整集制作入口**：
- 用户要求“完成第 N 集视频制作 / 完成第 N 集视频生成 / 做完这一集 / 帮我生成第 N 集视频 / 生成整集视频 / 做成片”时，第一轮先不要只读检查 `pipeline/status`、任务列表或媒体状态，也不要启动写工具。
- 先说明该需求需要拆成明确小任务，例如检查进度、补前置、生成单个 beat 音频、生成单个 beat 视频、合成成片，并询问是否要先列出当前制作进度和建议下一步。
- 用户确认后，下一轮才只读检查 `pipeline/status`、任务列表和必要媒体状态，给出当前集短计划，并只询问是否执行下一步这一个任务。
- 不得在同一轮直接从当前断点一路跑到 Step 21。
- 用户确认后，本轮最多启动一个写任务；任务启动后立刻收口。

**Step 9（服装一致性关键）**：
1. 先读取核心+重要角色的 identity 状态，选择第一个缺失身份图的 identity；本轮最多只生成这一个 identity 图
   - `CHAR_NAME` 只能来自已读到的角色名列表或当前返回结果中的明确角色字段，且必须非空
   - 如果当前拿不到角色名，就跳过该角色的 identity 补读，不要探测 `/characters//identities` 或任何空名路径
   - 不要在同一轮遍历并生成多个角色/多个 identity；剩余 identity 等用户下次“继续”再处理
2. 身份图是后续草图/首帧中服装外观的**视觉锚点**——没有身份图，服装每帧随机
3. `appearance_details` 必须具体到颜色+款式+材质，避免"正装"、"便装"等模糊词

**单步 continuation 收口**：
- 若 resume 已明确这是“从断点开始 / 继续做到下一步”的单步 continuation，
  则当前 `next_step` 完成后立刻停止，不继续探测后续步骤
- 例如断点在 Step 8（身份规划）时：
  - 完成 `POST /episodes/{ep}/identities/plan`
  - 直接汇报“本集身份规划已开始/已完成当前步”
  - 不继续轮询无关任务，不继续推进到 Step 9+
  - 默认直接使用 `identities/plan` 的返回结果汇报本步产物
  - 不为“核对每个角色身份”再补读 `GET /characters/{name}/identities`
  - 若规划返回已给出角色与身份位信息，就以该返回为最终事实源收口

**Step 10 固定主线**：
- 可选创作入口：剧集工作台「故事」tab —— `dramaclaw_get_episode_story` / `dramaclaw_optimize_episode_story` / `dramaclaw_save_episode_story` → `dramaclaw_story_to_script`(ep=N) 转剧本；内容自动导入剧本模板并触发 `script_writer`（与直接生成同一任务），等价完成本步
- 用户先准备 `raw-content`
- 若需要解说改写，先 `POST /rewrite/generate`，启动后立即收口；不要同一轮继续 `script/generate`
- 只有改写稿已完成/已存在时，才调用当前后端实际存在的 `POST /script/generate`；启动后立即收口
- 剧本生成完成后可进入 Step 11（场景/道具上下文），再进入 Step 12 草图
- 当前后端没有 `literal-script/generate`，也不保证 `script_mode == "literal_source"`

**Step 11-13（场景 / 道具上下文 — 草图前置）**：
- 当前后端没有 `anchor-image/*`、`scene_anchor`、`scenes/snapshot-sync` 路由，禁止调用这些旧接口
- 可用流程：
  1. `GET /projects/{project}/scenes` 列项目场景库
  2. `GET /projects/{project}/episodes/{ep}/beats` 读取本集 beats
  3. 如需补场景 → `POST /projects/{project}/scenes`，body 使用当前后端字段：`{"name":"...","description":"...","environment_prompt":"..."}`
  4. 本集道具规划 → `dramaclaw_plan_props`（`POST /projects/{project}/episodes/{ep}/props/plan`）
  5. 道具列表 → `GET /projects/{project}/props`
- 上述流程中同一轮最多执行一个写操作：补一个场景、启动一次道具规划、或执行当前 `next_step` 的一个任务；不要补多个场景后再启动道具规划。
- 完成判定：脚本、场景/道具上下文足够推进草图；不要要求 `anchor_image_url`

**Step 12（草图生成前置）**：先调 `assign-colors`（幂等），再生成草图
- 若草图需要多个 grid_index，当前轮最多启动一个 grid 的生成；不要循环所有 grid_index。下轮“继续”再处理下一个 grid。

**Step 12.3**：先配色再检测。无身份图时检测无效

**Step 12.5**：`{"language":"en"}` 默认英文 SuperPower 模式，决定 video_mode + motion prompt

**Step 18 音频生成**：使用 `dramaclaw_generate_audio`，即当前 `audio/generate` [ASYNC: `audio_generation_indextts2`]。旧 `/tts/generate` 已移除，不要调用。

**局部音频更新**：
- 当用户修改 beat 的 `audio_type`、`speaker`、`fish_speech_prompt` 或对白文本时，
  必须按固定顺序执行：
  1. 先 `PATCH /episodes/{ep}/beats/{beat}`
  2. 再重做该 beat 音频（`POST /episodes/{ep}/beats/{beat}/audio` 或对应音频生成路径）
  3. 最后才允许 `POST /episodes/{ep}/videos/compose`
- 这个顺序跨多轮执行：一轮只做其中一个写操作。完成 PATCH 后先收口，用户继续时再重做音频；音频完成后再由下一轮合成。
- 即使 beat 当前对白文本已经等于目标文本，只要 `audio_type`、`speaker`
  或其他音频相关字段还需要调整，也必须先完成这次 `PATCH`，
  不要先重做音频再补 `PATCH`
- `compose` 不能预启动、不能抢跑、不能为了省时间先发起再回头补音频。
  必须等该 beat 的音频重做请求已经发出并返回成功后，才允许进入 `compose`。
- 不要在该 beat 的音频重做之前先发起 `compose`

**Step 19 视频模型**：当前后端没有整集 `/videos/generate` 路由。默认用 `POST /episodes/{ep}/beats/{beat}/video` 单 beat 生成；如需整集片段，读取 beats 后只选择第一个未完成且前置满足的 beat，本轮最多启动这一个 beat。默认 `huimeng_seedance-1.0-pro-fast`。

**Step 19-21**：分别为 `single_video`（逐 beat）、`compose_episode`、`dramaclaw_get_final_video`，**必须顺序执行**

## 检查点规则（仅手动模式）

到达检查点时停止工具调用，展示成果，等用户回复。

| CP | 展示 | 用户可改 |
|----|------|----------|
| CP2 | Beat 摘要（序号+场景+画面，≤3行/beat） | 画面描述、对白、增删 |
| CP3 | 3-5 张代表首帧 | 重渲染、改 visual_description |
| CP4 | 音频播放器 + 对白声线列表 | 换声线、调语速 |
| CP5 | 成片视频 + 时长 + beat 数 | 重做 beat、重新合成 |

自动推进下不逐检查点等待用户确认，但仍必须每轮只启动一个写任务；启动任务或发现任务运行中后立即收口。

用户回复：
- "继续" / "ok" → 只推进当前 `next_step` 的一个任务；若已有任务运行中，只反馈当前状态
- "看第X张" / "看全部" → 补充展示，仍在同一检查点
- 具体修改指令 → 执行修改，再展示，仍等确认
- "自动跑" → 切到自动推进模式（每轮一步）

## 渐进式生成策略

```
阶段一：当前项目准备（Step 1-6，项目已创建并绑定） → 手动模式在 CP1 暂停
阶段二：逐集生成（Step 8-21，per EP） → 手动模式在 CP2-CP5 暂停
阶段三：多集推进 → 用户可选逐集/指定某几集，但每轮仍只推进一个写任务
```

模式贯穿所有阶段。**手动模式**在检查点暂停；**自动推进**自动选择下一步，但不是全程零停顿：每轮只启动一个写任务，后台生成中时只汇报状态，完成后由用户继续触发下一步。
