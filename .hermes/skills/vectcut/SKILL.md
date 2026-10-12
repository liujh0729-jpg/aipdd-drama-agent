---
name: vectcut
description: "Use when the user asks to edit/cut/assemble existing media into a Jianying (剪映) / CapCut draft — 剪辑、剪映草稿、CapCut、时间线、拼接、把素材拼成视频、给草稿加字幕/配音/配乐/贴纸/特效/关键帧、把画布产物做成剪映草稿、我剪一下/拼一下/合成一个草稿. Also use when the user asks which editing options exist (转场/蒙版/动画/特效名称) before assembling. Do NOT use for pipeline generation (草图/首帧/配音生成/成片合成) — that belongs to the dramaclaw skill."
compatibility: Requires the VectCut editing service reachable at VECTCUT_BASE_URL (no built-in default: docker compose injects http://vectcut:9001; a host-run service started by scripts/start-vectcut.sh is usually http://127.0.0.1:9001). Start it with scripts/start-vectcut.sh, or with docker compose -f docker-compose.aipdd.yml --profile vectcut up -d vectcut.
requires:
  env: []
---

# VectCut 剪辑 — 剪映/CapCut 草稿组装

用 `vectcut_*` 工具把素材（分镜帧、视频片段、配音、音乐、文本、字幕）组装成剪映/CapCut
可打开的剪辑草稿，交给用户在剪映里精修或导出。VectCut 是独立剪辑服务，与流水线生成解耦。

## 1. 核心契约

- 只使用 `vectcut_*` 工具完成剪辑；不要用 `dramaclaw_*` 工具代替剪辑动作，也不要用
  shell/curl 直连服务（服务地址由工具内部管理）。
- 面向用户只输出剪辑结果与状态（草稿名、内容概要、下一步），不暴露 API 路径、JSON
  参数与内部路径换算；草稿所在位置只在用户明确索要（"草稿在哪/怎么打开"）时给出。
- 剪辑是分步写操作：一轮消息最多推进一个写步骤（建画布 / 添加一个素材 / 保存草稿），
  执行中不逐步叙述，完成后一段话收口；不在一轮里跑完整条时间线。
- 素材引用遵循第 4 节规则，优先用项目素材引用（工具自动解析为可读路径），不要凭空拼路径。
- 任一工具返回 `success:false` 时立即停止后续剪辑动作，把 `error` 原文转成简短自然语言；
  若提示 "cannot reach the VectCut service"，说明服务未启动，给出启动方式后停止。

## 2. 标准工作流

1. **检查服务**（首次剪辑或工具报网络错误时）：`vectcut_health`。失败则提示用户启动：
   `scripts/start-vectcut.sh`（本机）或 `docker compose -f docker-compose.aipdd.yml --profile vectcut up -d vectcut`（容器）。
2. **创建画布**：`vectcut_create_draft`（默认 1080x1920 竖屏；横/方屏传 width/height）。
   拿到 `draft_id` —— 后续每一步都必须带上。
3. **逐项添加素材**（用户要什么加什么，不自行扩展）：
   - `vectcut_add_image` — 分镜帧/图片（入场/出场动画、转场、蒙版）
   - `vectcut_add_video` — 视频片段（转场、变速、音量、背景模糊）
   - `vectcut_add_audio` — 配音/配乐/音效
   - `vectcut_add_text` — 文字/标题（阴影、背景、多段样式）
   - `vectcut_add_subtitle` — SRT 字幕（文件路径、URL 或 SRT 文本）
   - `vectcut_add_effect` / `vectcut_add_sticker` / `vectcut_add_keyframe` — 特效/贴纸/关键帧动画
4. **保存交付**：`vectcut_save_draft`（`auto_deploy` 默认 true：落盘后同步复制进本机剪映草稿箱）。
5. **收尾回复**：草稿名 + 已放入的内容概要 + "可在剪映中打开继续精修"。

时间参数单位一律为秒：`start`/`end` 是素材自身起止；`target_start` 是放到时间线上的位置。

## 3. 工具速查

| 工具 | 必填 | 要点 |
| --- | --- | --- |
| `vectcut_health` | — | 服务信息（草稿目录、素材根、平台） |
| `vectcut_list_options` | `kind` | 枚举名称表：`transition`/`mask`/`intro_animation`/`outro_animation`/`combo_animation`/`audio_effect`/`font`/`text_intro`/`text_outro`/`text_loop`/`video_scene_effect`/`video_character_effect` |
| `vectcut_create_draft` | — | 返回 `draft_id`（空白画布） |
| `vectcut_add_image` | `image_url` `draft_id` | 图片轨道；可带动画/转场/蒙版 |
| `vectcut_add_video` | `video_url` `draft_id` | 视频轨道；可变速/调音量/转场 |
| `vectcut_add_audio` | `audio_url` `draft_id` | 音频轨道；可调音量/音效 |
| `vectcut_add_text` | `text` `start` `end` `draft_id` | 文本；`text_styles` 支持逐段样式 |
| `vectcut_add_subtitle` | `srt` `draft_id` | 字幕；SRT 路径/URL/文本均可 |
| `vectcut_add_effect` | `effect_type` `draft_id` | 特效；`effect_category`：`scene`/`character` |
| `vectcut_add_sticker` | `sticker_id` `start` `end` `draft_id` | 贴纸（按资源 id） |
| `vectcut_add_keyframe` | `draft_id` + `property_type`（或批量 `property_types`） | 关键帧：位置/缩放/旋转/透明度等 |
| `vectcut_get_duration` | `video_url` | 媒体时长（秒） |
| `vectcut_save_draft` | `draft_id` | 保存草稿；`auto_deploy` 默认 true |

枚举名称（动画/转场/蒙版/特效/字体）先用 `vectcut_list_options` 查，再用于添加素材；不要凭空编造名称。

## 4. 媒体引用规则

素材参数（`image_url`/`video_url`/`audio_url`/`srt`）支持三类引用，工具会自动解析：

1. **项目素材引用**（推荐）：形如 `/static/projects/{project}/frames/ep001/beat_01.png`
   —— 工具自动换算为剪辑服务可读的本地路径；项目目录名可省略（自动识别），
   若服务报 `ambiguous`（多项目同名文件），补传 `project_dir`（例如 `test`）。
2. **本地绝对路径**：与剪辑服务同机/共享卷的文件路径，直接可用。
3. **公网直链**：公开可访问的 http(s) 链接，由服务自行下载。

需要认证的 `/static` URL 禁止直接丢给底层下载器（无 Cookie 会 401）——一律走上面的第 1 类，
由解析通道转成本地路径。素材不存在时不要猜路径，向用户确认素材来源或先到画布生成对应产物。

## 5. 交付与话术

- 保存成功后回复示例："已生成剪辑草稿《第1集 v1》，包含 6 张分镜图、1 段配音、2 条字幕，
  已同步到剪映草稿箱，可直接打开继续精修。"
- 用户没有索要草稿文件/路径时，不主动给绝对路径、下载链接或服务地址。
- 用户索要时，可返回 `vectcut_save_draft` 输出里的草稿路径（`output.draft_url`）。

## 6. 错误处理

- `success:false` → 停止后续步骤，按 `error` 给出简短原因与下一步（补素材/改参数/启动服务）。
- `cannot reach ...` → 服务未启动：`scripts/start-vectcut.sh` 或 `docker compose -f docker-compose.aipdd.yml --profile vectcut up -d vectcut`。
- `media not found` → 素材路径或项目上下文不对：请用户确认素材（含项目目录名）后重试，不要猜。
- `ambiguous` → 同名素材命中多处：请用户给出正确的 `project_dir`。
- 不重试同一个失败工具，不换路径乱试，不把失败描述成成功。

## 7. 独立调用（可选）

VectCut 服务可脱离 MCP 单独调用（低耦合设计）：地址就是 `VECTCUT_BASE_URL` 的值（代码不兜底本机端口：compose 部署为 `http://vectcut:9001`，`scripts/start-vectcut.sh` 宿主机直跑时为 `http://127.0.0.1:9001`），提供上游全部
HTTP 端点（`/create_draft`、`/add_*`、`/save_draft` 等）。仅在用户明确要求"直接调接口/
写脚本"时提供；日常剪辑一律走 `vectcut_*` 工具。升级 VectCut 上游见
`services/vectcut/README-AIPDD.md`。