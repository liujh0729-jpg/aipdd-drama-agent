---
name: short-drama-production
title: 短剧创作工作流（MCP）
description: "Use when the user wants to turn a novel or story into a short drama end-to-end, or asks to continue/automate an existing drama project. Trigger on: 小说转短剧、故事转视频、做成短剧、做一集、分集、剧本、角色、场景、道具、草图、首帧、配音、合成、成片、按计划执行、继续做第 N 集、自动完成计划. Also use when the user sets a work plan and asks the agent to execute it. Do NOT use for one-off image/video edits, pure greetings, or assembling existing media into a Jianying/CapCut draft (that is the vectcut skill)."
agent: true
---

# 短剧创作工作流（MCP）

本技能把「小说 / 故事 → 短剧成片」收成一条可执行流水线。所有平台动作都通过
`aipdd_drama_*` MCP 工具完成，不猜 HTTP 路径、不伪造产物。

> 工具名说明：本文统一写裸名（如 `aipdd_drama_get_plan`）。宿主的工具列表若带
> 客户端前缀（Agent 里是 `mcp__aipdd-drama__aipdd_drama_get_plan`），按宿主显示的
> 全名调用；Agent 执行器也接受唯一命中的裸名，语义完全相同。

## 1. 边界

- 只处理已经绑定项目的制作；没有项目时先让用户创建/选择项目，不要编造 `project_id`。
- 输入必须是真实故事/小说：用户粘贴的正文（整段故事）或项目里已上传的稿件。
  一句话主题不足以生成整部剧本——先请用户补故事正文或文档。
- 本技能推进到**镜头首帧**为止；视频、合成、导出属于交付动作，必须由用户明确要求
  后再执行（见第 6 节）。
- 任何工具返回失败立即停下，把后端 `error/detail/message` 转成简短结论；不要换路径重试，
  也不要声称做了没做的事。
- 失败原因是环境缺依赖、网关不通、网络/5xx 等**非输入错误**时，允许且只允许补调用一次
  `aipdd_drama_self_heal(symptom=…, tool_name=…, error=…)` 做自愈诊断（默认
  `mode="diagnose"`，它不会重试原调用）；用户明确要求修复时用 `mode="repair"`。
  把 `analysis.reply` 转述给用户；`repairs` 里 `status="proposed"` 只是建议，须用户同意
  再修复；`degraded=true` 时说明默认文本模型当前不可用及原因。连同原始错误一起告诉用户，
  再停止本轮。

## 2. 开工协议（每次接到制作请求）

1. 确认项目：使用当前会话绑定的项目；工具都支持省略 `project_id`，但显式传入更稳。
2. 读计划：`aipdd_drama_get_plan`。
   - 有计划（`items` 非空）→ 按计划执行，见第 5 节。
   - 没计划 → 先把用户目标拆成带 `stage` 的条目，用
     `aipdd_drama_update_plan(action="add", items=[{title,stage,episode?}, …])` 写入，
     再执行；不要跳过计划连发多个写工具。
3. 查在跑任务：`aipdd_drama_pipeline_status` / `aipdd_drama_list_tasks`。
   有 `queued` / `running` 任务时，只报告任务名、状态和等待建议，本轮不再启动新写任务。
4. 一次只推进一个写任务；启动成功后立刻收口，下一轮再轮询或推进。

## 3. 权威阶段顺序

阶段顺序与后端计划器一致，**不可跳步、不可倒序**：

| 阶段 | stage | 首选工具 | 轮询 task_type |
|---|---|---|---|
| 故事源 | `story` | `aipdd_drama_save_story_source`（用户粘贴正文）或 `aipdd_drama_get_story_source` 确认已有正文 | 同步返回 |
| 整本剧本 | `screenplay` | `aipdd_drama_run_pipeline(mode="manual")` 推进；单步可用 `aipdd_drama_post('/projects/{p}/story/generate-script', {…})` | 读 `story/generate-script/result` |
| 剧本入库 | `ingest` | `aipdd_drama_start_ingest(filename=…)`（先 `aipdd_drama_list_ingest_uploads`） | `ingest_fast`，episode=0 |
| 角色提取 | `characters` | `aipdd_drama_build_characters` | `build_characters`，episode=0 |
| 分集规划 | `episodes` | `aipdd_drama_plan_episodes` | `build_episodes`，episode=0 |
| 人物身份 | `identities` | `aipdd_drama_plan_identities(episode=N)` | `identity_planner`，episode=N |
| 分镜脚本 | `script` | `aipdd_drama_generate_script(episode=N)`，或 `aipdd_drama_story_to_script(episode=N)` 从故事改编 | `script_writer`，episode=N |
| 场景规划 | `scenes` | `aipdd_drama_plan_scenes(episode=N)` | `episode_scene_planner` |
| 道具规划 | `props` | `aipdd_drama_plan_props(episode=N)` | `episode_prop_planner` |
| 草图与配色 | `sketches` | `aipdd_drama_generate_sketches(episode=N)` | `sketch_generation` |
| 镜头首帧 | `frames` | `aipdd_drama_render_first_frames(episode=N)`（省略 `beat_indices` = 全集） | `selected_regen` |

要点：

- `plan_identities` 是 `generate_script` 的硬前置；缺身份时后端返回
  `identity_plan_required`，先补身份再重跑，不要绕开。
- 场景/道具规划在脚本之后、草图之前；计划器需要时会自动补齐，手工推进时按表顺序做。
- 草图默认一次覆盖全部网格；`render_first_frames` 需要草图已存在。
- 项目级阶段（story/screenplay/ingest/characters/episodes）只做一次；逐集阶段对每一集分别执行。

## 4. 异步任务与轮询

写工具启动的是后台任务，返回 `task_id` / `queued` 即算成功：

- 单任务：`aipdd_drama_get_task(task_type=…, episode=N, beat_num=…)`；
- 项目总览：`aipdd_drama_pipeline_status`、`aipdd_drama_list_tasks`；
- 连续推进：`aipdd_drama_run_pipeline` 返回 `running` 时，用
  `aipdd_drama_get_run_status` 轮询，不要在同一轮里死等；
- 批量视频/提示词：`aipdd_drama_video_range` / `aipdd_drama_generate_video_prompts`
  立即返回 `submitted/skipped/errors`，逐个 beat 轮询。

轮询到 `failed` / `cancelled` 时报告真实错误并停止；不要自动重试整条链路。

## 5. 工作计划（用户设计划 → Agent 自动做）

- 读计划：`aipdd_drama_get_plan`（返回 `items`、`executable_pending`、阶段说明）。
- 写计划：`aipdd_drama_update_plan`，`action=add|update|complete|remove`；
  制作条目必须带 `stage`（见第 3 节）和可选 `episode`；不带 stage 的是手工待办。
- 执行计划：`aipdd_drama_run_plan`。
  - `mode="auto"`（默认）：预算内连续推进，按**真实产物**回勾条目；
  - `mode="manual"`：只推一段；
  - `mode="checkpoint"`：只预览，`approve=true` 才发射。
  - 执行器幂等：已完成条目不会重跑；只推进到镜头首帧，绝不提交视频。
- 用户说「按计划执行 / 自动完成计划 / 继续按计划做」时，先 `get_plan` 确认，再
  `run_plan`；计划里没有 stage 的条目不要擅自执行。

## 6. 视频、合成与导出（需用户明确要求）

前置全部满足后再做，一次一个写任务：

1. 视频提示词（可选）：`aipdd_drama_generate_video_prompts(episode=N, beats="1-8")`；
2. 镜头视频：`aipdd_drama_video_range(episode=N, beats="1-8", model=…)`；
   模型候选用 `aipdd_drama_get_models(kind="video")`；
3. 配音：`aipdd_drama_generate_audio(episode=N)`；缺声线时按返回的
   `voice_prereq_required` 报告，不要继续启动视频/合成；
4. 合成：`aipdd_drama_compose_episode(episode=N)`，需所有 beat 视频已存在；
5. 成片：`aipdd_drama_get_final_video(episode=N)` 返回正式地址后直接交付；
6. 导出：`aipdd_drama_export_episode(episode=N, kind="video"|"zip")` 落盘。

用户没有明确要成片/导出时，不要主动扩大到合成与导出。

## 7. 内容修改

- 故事：`aipdd_drama_get_episode_story` / `save_episode_story` /
  `optimize_episode_story` / `story_to_script`；
- 章节：`aipdd_drama_get_chapters` / `update_chapter`；改正文后需要重建图谱时
  `aipdd_drama_start_ingest(rebuild=true)`；
- 单镜重做：`aipdd_drama_regen_sketches(episode=N, beats="3")`、
  `aipdd_drama_render_first_frames(episode=N, beat_indices=[3])`；
- 局部更新只动用户点名的对象，不顺带重做整集。

## 8. 交付与回复

- 只交付用户要的产物；路径用工具返回的正式字段，不拼接、不猜测绝对 URL。
- 状态类回复给「当前阶段 + 正在跑的任务 + 下一步」，不要贴 JSON 或内部路径。
- 用户问项目/进度/上传文件时，先查真实数据再回答：
  `aipdd_drama_pipeline_status`、`aipdd_drama_list_tasks`、
  `aipdd_drama_list_ingest_uploads`。

## 9. 参考文件

- `references/work-plan.md` — 计划条目写法、执行模式、回勾规则；
- `references/pipeline.md` — 各阶段参数、前置条件和轮询细节；
- `references/tool-index.md` — 全部 MCP 工具的索引（由工具注册表生成）。
