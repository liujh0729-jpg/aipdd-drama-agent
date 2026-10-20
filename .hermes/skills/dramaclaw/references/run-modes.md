# 运行模式（两种）

用户在配置完成后（init.md 决策树第 5 步）选择运行模式。两种模式共用同一套
pipeline 步骤顺序与专用工具，只是「是否每步解释并确认」不同；两种模式都不能在一轮里连续启动多个写任务。

模式由用户本轮明确表达决定，并在本会话内保持：
- 用户说「每步确认 / 一步步 / 手动 / 每步问我」→ **逐步确认模式**（见下）
- 用户说「一次性 / 全自动 / 自动驾驶 / 一口气跑完 / 不用问我」→ **自动推进模式（每轮一步）**；这些说法只表示下次“继续”时不重复解释流程，不表示本轮可以跑完整集
- 用户没说 → 默认先问一句「要我**每步确认**还是**自动推进（每轮一步）**？」，问完按回答走

大任务例外：用户说「完成第 N 集视频制作 / 完成第 N 集视频生成 / 做完这一集 / 帮我生成第 N 集视频 / 生成整集视频 / 做成片」这类跨多个阶段的目标时，即使用户没有明确要求每步确认，也必须先进入“拆解确认”：

1. 第一轮不要启动写工具，也不要先自动查完整状态。
2. 先告诉用户需要拆成明确小任务，例如检查进度、补前置、生成单个 beat 音频、生成单个 beat 视频、合成成片。
3. 询问用户是否需要先列出当前制作进度和建议下一步，然后结束本轮。
4. 用户确认后，下一轮只读查询 `pipeline/status` 和必要状态。
5. 用 3-5 条列出当前卡点、缺失阶段和建议下一步。
6. 只询问是否执行下一步这一个任务。
7. 用户再次确认后，下一轮最多启动这一个任务，启动后收口。

---

## 模式一：逐步确认模式（step-by-step）

**核心规则：一次只推进一个步骤，每步之前先停下问用户，得到确认才执行；绝不连续跳步。**

### 每一步的固定动作

1. **报下一步**（一句话，不展开）：
   - 要做什么（步骤中文名）+ 会调用的工具 + 前置是否已满足
   - 例：「下一步：分集规划（`aipdd_drama_plan_episodes`，目标 10 集）。原文与角色已就绪，可执行。」
2. **停下来问**：「执行这一步吗？（继续 / 跳过 / 调整参数 / 停）」
   —— 然后**结束本轮输出，等用户回复**。不要自动往下做。
3. 用户回复后：
   - 「继续 / 执行 / 好 / 下一步」→ 先查当前任务状态；若已有 queued/running，告知后台正在生成中并停止；若没有运行中任务，调对应专用工具启动当前一步 → **立即收口**，询问稍后是否继续查看进度或执行下一步
   - 「跳过」→ 不执行，直接报「再下一步」
   - 「改成 N 集 / 用某风格 …」→ 按调整后的参数执行该步
   - 「停 / 暂停」→ 停在当前步，等用户下次指令

### 不允许的行为

- ❌ 一次确认后连跑多步（哪怕用户只说「继续」，也只推进**一步**）
- ❌ 不问就执行写操作（plan/build/generate/compose 这类触发任务的步骤）
- ❌ 把「报结果」和「执行下一步」合并——必须报完结果/启动状态后结束本轮，等用户下一条消息
- ❌ 启动异步任务后继续轮询到 completed，再自动进入下一步

### 步骤顺序（按 pipeline 主线，逐步走）

当前项目准备（init.md Steps 1-7，项目已由前端/系统创建并绑定）：
1. 上传小说 → 2. 摄入(ingest) → 3. 配置项目 →
4. 角色提取 `aipdd_drama_build_characters` →
5. 角色 face_prompt 检查/补齐 `aipdd_drama_update_character_face_prompt`（仅缺失角色） →
6. 分集规划 `aipdd_drama_plan_episodes` →
7. 角色肖像 `aipdd_drama_generate_portrait`（逐个核心角色）

> Steps 1-2 是摄入准备动作，可合并成「准备阶段」一次确认；
> 从 **Step 3 配置项目起**，每个写操作步骤都单独确认。

每集制作（episode.md，对每一集 N 重复）：
8. 身份规划 `aipdd_drama_plan_identities`(ep=N) →
9. 身份图生成 `aipdd_drama_generate_identity_image`(逐身份) →
10. 脚本生成 `aipdd_drama_generate_script`(ep=N) →
11. 场景规划 `aipdd_drama_plan_scenes`(ep=N) →
12. 道具规划 `aipdd_drama_plan_props`(ep=N) →
13. 场景参考图 `aipdd_drama_generate_scene_master` / `aipdd_drama_generate_scene_reverse`(按本集场景需要逐个生成) →
14. 草图生成 `aipdd_drama_generate_sketches`(ep=N) →
15. AI 检测 `aipdd_drama_detect_sketch_identities`(ep=N) →
16. 全局视频优化 `aipdd_drama_optimize_video_global`(ep=N) →
17. 首帧生成 `aipdd_drama_render_first_frames`(ep=N) →
18. 音频生成 `aipdd_drama_generate_audio`(ep=N) →
19. 单 beat 视频 `aipdd_drama_start_single_video`(逐 beat) →
20. 合成导出 `aipdd_drama_compose_episode`(ep=N) →
21. 最终成片展示 `aipdd_drama_get_final_video`(ep=N)

> **可选创作入口（剧集工作台「故事」tab，替代 Step 10 直接生成）**：
> `aipdd_drama_get_episode_story`(ep=N) 读取故事 → `aipdd_drama_optimize_episode_story`(ep=N) AI 优化（结果不落盘，用户确认后才需保存）→ `aipdd_drama_save_episode_story`(ep=N) 保存 → `aipdd_drama_story_to_script`(ep=N) 转剧本。
> 转剧本成功后内容自动导入剧本模板并触发 `script_writer`（与 Step 10 同一任务），完成后直接继续 Step 11；无故事或用户要求直接生成时仍用 `aipdd_drama_generate_script`。

每完成一集，问「继续做第 N+1 集吗？」再进入下一集。

### 状态回执（每步执行后）

- 触发后最多做一次必要的 `aipdd_drama_get_task(task_type=..., episode=...)` 状态查询
- 若状态为 queued/running：告诉用户后台正在生成中，等待完成后再继续；不要轮询到 completed
- 成功：按下表读取或展示完成数据；不要只说“完成”
- 失败：报 `task.error` / `error_code`，**停在该步**，不要自动重试或跳过

### 完成数据展示规则

每个步骤完成后，必须展示或汇总该步骤的真实产物。媒体类必须调用对应展示工具；文本/列表类用 markdown 表格或简短列表。没有可展示数据时，如实说明“已完成，但当前接口未返回可展示产物”，不要拼 URL、猜路径或拿旧数据充数。

| 步骤 | 完成后读取 / 展示 |
|------|-------------------|
| 摄入 | `aipdd_drama_pipeline_status` 汇总 ingested/configured 状态；不要展示原文全文 |
| 配置项目 | `aipdd_drama_get(path="/projects/{project}")` 汇总视觉风格、叙事方式、节奏、音频/视频配置 |
| 角色提取 | `aipdd_drama_get(path="/projects/{project}/characters")` 展示角色列表 |
| 角色 face_prompt 检查/补齐 | `aipdd_drama_get(path="/projects/{project}/characters")` 检查核心角色 `face_prompt`；缺失时先补齐并展示已补角色 |
| 分集规划 | `aipdd_drama_get(path="/projects/{project}/episodes")` 展示分集列表 |
| 角色肖像 | `aipdd_drama_get_character_media(media_kind="portrait")` 展示肖像 |
| 身份规划 | `aipdd_drama_get_character_media(media_kind="identity")` 或角色 identities 接口汇总身份列表；没有身份图时只列身份 |
| 身份图生成 | `aipdd_drama_get_character_media(media_kind="identity")` 展示身份图 |
| 故事读取/优化/保存 | `aipdd_drama_get_episode_story(episode=N)` 展示故事文本、风格与字数；优化结果需保存后才落盘 |
| 故事转剧本 | 自动导入剧本模板并触发 `script_writer`；完成后按「脚本生成」行展示 `aipdd_drama_get_episode_script(episode=N)` beat 摘要 |
| 脚本生成 | `aipdd_drama_get_episode_script(episode=N)` 展示 beat 摘要 |
| 场景规划 | `aipdd_drama_get(path="/projects/{project}/scenes")` 展示场景列表 |
| 道具规划 | `aipdd_drama_get(path="/projects/{project}/props")` 展示道具列表 |
| 场景参考图 | `aipdd_drama_get_scene_images()` 展示场景图 |
| 草图生成 | `aipdd_drama_get_sketches(episode=N)` 展示草图 |
| AI 检测 | `aipdd_drama_get_episode_script(episode=N)` 或 beats 接口汇总每个 beat 检测到的身份/道具；不要重复调用检测 |
| 全局视频优化 | `aipdd_drama_get_episode_script(episode=N)` 或 beats 接口汇总 video_mode / video_prompt 就绪情况 |
| 首帧生成 | `aipdd_drama_get_first_frames(episode=N)` 展示首帧 |
| 音频生成 | `aipdd_drama_get_episode_media(episode=N, media_type="audio")` 展示音频 |
| 单 beat 视频 | `aipdd_drama_get_episode_media(episode=N, media_type="video")` 展示视频片段 |
| 合成导出 | `aipdd_drama_get_final_video(episode=N)` 展示最终成片 |
| 最终成片展示 | `aipdd_drama_get_final_video(episode=N)`，若不存在则只说明暂无成片 |

---

## 模式二：自动推进模式（bounded auto）

自动推进不是单轮跑完整集。为了避免聊天超时和队列拥塞，自动推进也必须遵守：

- 一次用户消息最多启动 1 个写操作/异步任务。
- 启动任务成功后立即收口，告诉用户“已进入队列/已启动”，并提示下一步等任务完成后继续。
- 不在同一轮等待长任务完成，不继续提交下一步。
- 任何失败、429、前置缺失、任务不存在、404 或网络错误都立即停止并反馈错误原文。

自动推进的含义是：用户下次说“继续”时，按 `pipeline/status.next_step` 自动选择下一步，不需要每步重新解释流程；但每轮仍只推进一个任务。
