# 流水线阶段细节

## 故事源（story）

- 已有正文：`aipdd_drama_get_story_source`（返回 `text/chars/billable_chars/filename`）。
- 用户粘贴整本小说：`aipdd_drama_save_story_source(text=…)`；保存后可用
  `aipdd_drama_get_chapters` 看章节结构（默认只给元数据，`chapter=N` 读单章）。
- 上传文件：先 `aipdd_drama_list_ingest_uploads`，再 `aipdd_drama_start_ingest(filename=…)`。
- 章节改写：`aipdd_drama_update_chapter(chapter=N, body=…)`；改正文后如需重建图谱，
  `aipdd_drama_start_ingest(filename=…, rebuild=true)`。

## 整本剧本（screenplay）

- 无独立 MCP 工具；用 `aipdd_drama_run_pipeline(mode="manual")` 推进，或走逃生通道
  `aipdd_drama_post('/projects/{p}/story/generate-script', {...})`。
- 完成后读 `story/generate-script/result`（`aipdd_drama_get('/projects/{p}/story/generate-script/result')`）。
- 风格参数：`story_style`（空 = 自动；如 beat_punch/spectacle/suspense/emotional/prestige/realism/comedy/info_pacing/auteur）。

## 项目级阶段

| 阶段 | 工具 | 关键参数 | 轮询 |
|---|---|---|---|
| ingest | `aipdd_drama_start_ingest` | `filename`、`rebuild`、`spine_template` | `ingest_fast`，episode=0 |
| characters | `aipdd_drama_build_characters` | — | `build_characters`，episode=0 |
| episodes | `aipdd_drama_plan_episodes` | `target_episodes`、`planning_mode` | `build_episodes`，episode=0 |

## 逐集阶段

| 阶段 | 工具 | 前置 | 轮询 |
|---|---|---|---|
| identities | `aipdd_drama_plan_identities` | ingest + episodes | `identity_planner` |
| script | `aipdd_drama_generate_script` / `aipdd_drama_story_to_script` | identities | `script_writer` |
| scenes | `aipdd_drama_plan_scenes` | script | `episode_scene_planner` |
| props | `aipdd_drama_plan_props` | script | `episode_prop_planner` |
| sketches | `aipdd_drama_generate_sketches` | script（场景/道具按需） | `sketch_generation` |
| frames | `aipdd_drama_render_first_frames` | sketches | `selected_regen` |

- `aipdd_drama_generate_script` 缺身份时返回 `identity_plan_required`；先补身份再重跑。
- `aipdd_drama_story_to_script` 会把改编稿写回剧集并默认自动启动 `script_writer`
  （`autostart=false` 可只导入不启动）。
- 故事读写：`get_episode_story` / `save_episode_story` / `optimize_episode_story`；
  优化结果不落库，确认后再 `save_episode_story`。
- `aipdd_drama_generate_sketches` 默认先配色（`auto_assign_colors=true`）、
  `grid_index=-1` 覆盖全部网格、`aspect_ratio="2:3"`；需要指定风格/模型时显式传。
- `aipdd_drama_render_first_frames` 省略 `beat_indices` 即全集；单镜重做传 `[N]`。

## 交付阶段（需用户明确要求）

| 动作 | 工具 | 轮询 / 返回 |
|---|---|---|
| 视频提示词 | `aipdd_drama_generate_video_prompts` | `beat_video_prompt`，逐 beat |
| 镜头视频 | `aipdd_drama_video_range` | `single_video`，逐 beat |
| 单镜视频 | `aipdd_drama_start_single_video` | `single_video` |
| 配音 | `aipdd_drama_generate_audio` | `audio_generation_indextts2` |
| 合成 | `aipdd_drama_compose_episode` | `compose_episode` |
| 成片 | `aipdd_drama_get_final_video` | 直接返回正式地址 |
| 导出 | `aipdd_drama_export_episode` | `kind=video`/`zip` 落盘 |

- 视频模型：`aipdd_drama_get_models(kind="video")`；画幅/时长按用户输入。
- `video_range` 只提交首帧已存在的 beat，其余列在 `skipped`；不要为跳过的 beat 编造视频。
- `compose_episode` 需要该集所有 beat 视频；缺视频时报告缺项，不自动补跑整链。
- 配音缺声线（`voice_prereq_required`）时先让用户补声线，不要继续合成。

## 逃生通道

任何未被专门工具覆盖的端点用 `aipdd_drama_get/post/patch/delete`。只有在明确知道
真实路径时使用；优先专门工具，避免自行拼 body。
