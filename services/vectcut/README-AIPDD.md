# VectCut 服务（AIPDD 集成说明）

本目录是 [VectCut / VectCutAPI](https://github.com/sun-guannan/VectCutAPI) 上游代码的
**原样副本 + AIPDD 适配层**。VectCut 为独立进程的剪辑服务：通过 HTTP 接口生成
剪映 / CapCut 草稿（画布尺寸、视频、图片、音频、文本、字幕、特效、贴纸、关键帧），
草稿保存后可直接在剪映桌面端打开继续精修。

集成原则（与 Dramaclaw 主业务低耦合）：

- **独立进程、独立依赖**：服务私有 venv（`services/vectcut/.venv`），不进入主仓库依赖树；
- **独立接口**：上游全部端点可直接调用（默认 `http://127.0.0.1:9001`），不依赖主 API；
- **上游零改动**：升级时用上游新版本覆盖本目录（保留清单见下），无需合并补丁；
- **素材通道**：与 Dramaclaw 共享文件系统，通过本地路径直读素材（见下文）。

## AIPDD 增量文件（升级时保留，勿被上游覆盖）

| 路径 | 作用 |
| --- | --- |
| `aipdd_server.py` | 服务入口：非 Windows 平台的 `desktop_companion` 兼容 shim、`config.json` 自举、注册 `/aipdd/*` 附加路由 |
| `aipdd/server.py` | 入口实现（环境变量、默认草稿目录、素材根、附加路由） |
| `aipdd/media_resolver.py` | `/static/projects/...` URL → 本地文件路径解析（纯函数，可单测） |
| `README-AIPDD.md` | 本文档 |
| `Dockerfile` / `.dockerignore` | 独立镜像构建（context 为 `services/vectcut/`） |
| `drafts/` | 默认草稿输出目录（内容不进 git） |

其余文件均来自上游。上游 `.gitignore` 忽略的 `config.json` 由入口在缺失时按环境变量自动生成。

## 启动

```bash
# 本机（推荐）：自动准备私有 venv 并启动
scripts/start-vectcut.sh              # 默认 127.0.0.1:9001
VECTCUT_PORT=9101 scripts/start-vectcut.sh
scripts/start-vectcut.sh --reinstall  # requirements.txt 变化后强制重装依赖

# 直接启动（已有私有 venv 时）
services/vectcut/.venv/bin/python services/vectcut/aipdd_server.py

# 容器（与主栈同网络）：
docker compose -f docker-compose.aipdd.yml --profile vectcut up -d vectcut
```

健康检查：`curl http://127.0.0.1:9001/aipdd/health`

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `VECTCUT_HOST` | `127.0.0.1` | 监听地址（容器内需 `0.0.0.0`） |
| `VECTCUT_PORT` | `9001` | 监听端口 |
| `VECTCUT_DRAFT_PROFILE` | `jianying_pro_10` | 草稿格式（`capcut_legacy` / `jianying_legacy` / `jianying_pro_10`），仅首次生成 `config.json` 时生效 |
| `VECTCUT_DRAFT_DIR` | `<service>/drafts` | 默认草稿输出目录（`/aipdd/save_draft` 未显式传 `draft_folder` 时使用） |
| `VECTCUT_MEDIA_ROOTS` | `<repo>/state:<repo>/output` | 素材根目录列表（`:` 分隔），供 `/aipdd/resolve_media` 解析 |

## 附加路由（`/aipdd/*`）

上游端点（`/create_draft`、`/add_video`、`/add_image`、`/add_audio`、`/add_text`、
`/add_subtitle`、`/add_effect`、`/add_sticker`、`/add_video_keyframe`、
`/get_duration`、`/save_draft` 等）全部保持上游原样。AIPDD 仅新增：

- `GET /aipdd/health` — 服务信息（草稿目录、素材根、平台），供 MCP/skill 探测；
- `POST /aipdd/resolve_media` — 把素材引用解析为本地路径：

  ```json
  // 请求
  {"reference": "/static/projects/<project>/frames/ep001/beat_01.png?v=1", "project_dir": "test"}
  // 响应
  {"success": true, "path": "/abs/path/output/local/test/frames/ep001/beat_01.png", "mode": "resolved"}
  ```

  解析规则：http(s) 直链与已存在的本地路径原样透传；`/static/projects/{P}/{file}`
  在素材根下按 `{root}/{username}/{项目目录}/{file}` 查找，`project_dir`
  （项目目录名）可提升唯一性——URL 里的 `{P}` 是项目 id，磁盘目录通常是项目名。
  命中多个候选时报 `ambiguous`（附带候选列表），绝不猜测。

- `POST /aipdd/save_draft` — 保存草稿并注入默认输出目录：

  ```json
  {"draft_id": "dfd_cat_...", "project_name": "可选", "auto_deploy": true}
  // 响应
  {"success": true, "output": {"success": true, "draft_url": "/abs/path/<草稿目录>"}, "error": ""}
  ```

  未指定 `draft_folder` 时落到 `VECTCUT_DRAFT_DIR`；`auto_deploy=true`（默认）时
  Windows/macOS 上会尝试把草稿复制到剪映的草稿箱目录，失败不影响已保存的产物。

## 素材通道说明

Dramaclaw 的 `/static/projects/...` 静态资源要求登录会话，而 VectCut 的媒体处理
（上游 `downloader.py` 由 ffmpeg 完成任务）无法携带 Cookie。因此集成约定：

- **首选**：VectCut 与 Dramaclaw 共享文件系统（本机同机部署，或容器共享 `ce-data` 卷），
  调用方传本地绝对路径（可用 `/aipdd/resolve_media` 从 `/static` URL 换算）；
- **备选**：公开可访问的外链 URL 直接透传（上游自行下载）；
- 需要认证的 `/static` URL 直接传给媒体参数会失败，请先解析为本地路径。

## 升级 VectCut 上游

1. 拉取上游新版本源码；
2. 用 `rsync` 覆盖本目录，**排除** AIPDD 增量文件：

   ```bash
   rsync -a --delete \
     --exclude '.venv/' --exclude '__pycache__/' \
     --exclude 'aipdd/' --exclude 'aipdd_server.py' \
     --exclude 'README-AIPDD.md' --exclude 'Dockerfile' \
     --exclude '.dockerignore' --exclude 'drafts/' \
     --exclude 'config.json' \
     /path/to/VectCutAPI-main/ services/vectcut/
   ```

3. 如上游新增依赖，`scripts/start-vectcut.sh --reinstall` 重装私有 venv；
4. 容器部署时先 `build` 再 `up`（镜像 tag 未变时，不带 build 的 `up` 会继续跑旧镜像）：

   ```bash
   docker compose -f docker-compose.aipdd.yml --profile vectcut build vectcut
   docker compose -f docker-compose.aipdd.yml --profile vectcut up -d vectcut
   ```

5. 跑验收：`curl http://127.0.0.1:9001/aipdd/health` → 创建草稿 → 添加素材 → 保存草稿；
   回归 MCP 工具层与素材解析：`uv run pytest tests/test_hermes_vectcut_tools.py tests/test_vectcut_media_resolver.py`。

若上游端点参数有变化，只需同步更新 MCP 工具层（`.hermes/plugins/dramaclaw/`），
适配层与入口无需改动。

## 与 MCP / Skill 的关系

- **MCP 工具**：Dramaclaw 的 MCP 服务（`.mcp.json` → `novelvideo.chat.dramaclaw_mcp`）
  内置 `vectcut_*` 工具组，自动完成「素材解析 → 调 VectCut → 保存草稿」，
  调用方无需关心本服务的地址与路径换算（服务地址取自 `VECTCUT_BASE_URL`）。
- **Skill**：`.hermes/skills/vectcut/` 指导 Agent 用 `vectcut_*` 工具完成
  画布组装与剪辑（读分镜/素材 → 组装时间线 → 保存草稿 → 交给剪映精修）。
- **独立调用**：任何脚本也可直接 `POST http://127.0.0.1:9001/...` 使用上游原始接口。