# 工作计划：写法与执行

## 数据结构

每条计划条目：

```json
{"id": "…", "title": "第 1 集草图", "stage": "sketches", "episode": 1,
 "done": false, "last_error": "", "stage_inferred": false}
```

- `stage` 必须是以下之一（顺序即先决顺序）：
  `story` → `screenplay` → `ingest` → `characters` → `episodes` →
  `identities` → `script` → `scenes` → `props` → `sketches` → `frames`。
- `episode`：逐集阶段填集号；省略表示「全部集」。项目级阶段（story / screenplay /
  ingest / characters / episodes）忽略集号。
- 没有 `stage` 的条目是手工待办，执行器不会碰。

## 写入计划

```text
aipdd_drama_update_plan(action="add", items=[
  {"title": "导入小说", "stage": "story"},
  {"title": "整本剧本", "stage": "screenplay"},
  {"title": "剧本入库", "stage": "ingest"},
  {"title": "角色提取", "stage": "characters"},
  {"title": "分集规划", "stage": "episodes"},
  {"title": "第 1 集人物身份", "stage": "identities", "episode": 1},
  {"title": "第 1 集分镜脚本", "stage": "script", "episode": 1},
  {"title": "第 1 集场景规划", "stage": "scenes", "episode": 1},
  {"title": "第 1 集道具规划", "stage": "props", "episode": 1},
  {"title": "第 1 集草图", "stage": "sketches", "episode": 1},
  {"title": "第 1 集首帧", "stage": "frames", "episode": 1}
])
```

- `update`：按 `id` 改 `title/stage/episode/done`；
- `complete` / `remove`：传 `id` 或标题；
- 一次最多 30 条，标题 ≤200 字；
- 用户只说「做一集」时，只给这一集的逐集阶段，不要替用户扩到全集。

## 执行计划

```text
aipdd_drama_get_plan()                       # 先读，确认 items 与 executable_pending
aipdd_drama_run_plan(mode="auto")            # 预算内连续推进
```

- `mode=auto`（默认）：在 `budget_s`（默认 90，最大 600）内连续推进；
  `max_steps` 控制最多推进段数（默认 50）。
- `mode=manual`：只推进一段，适合逐步确认。
- `mode=checkpoint`：只返回决策不发射；带 `approve=true` 再调才执行。
- 执行器每推进一步都会读真实产物回勾 `done`，重复调用幂等。
- 只推进到镜头首帧；计划里的视频/合成/导出条目不会被自动执行。
- 计划包含 `story` 且项目还没有故事源时，第一次调用必须带 `story_text`。
- 执行器返回 `status/advanced/completed/remaining/items/next_action`；
  `next_action` 是下一轮要做的动作，不要自己另编顺序。

## 常见状态

- `running`：仍有阶段在跑，下一轮再调 `run_plan` 或看 `get_run_status`；
- `blocked`：缺前置或模型配置，按 `last_error` 提示补齐，不要强行越过；
- `done`：计划完成；若还有视频/合成需求，先向用户确认再进入交付。
