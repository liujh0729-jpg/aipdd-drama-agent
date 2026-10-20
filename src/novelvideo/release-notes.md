---
version: 2.3.0
attention: medium
---
# v2.3.0

## User-facing Highlights (zh)

- **MCP 工具集扩容**: Agent 现在能直接读写结构化数据、导入小说并读取章节、生成动效大片、导出成片，并按计划自动连续执行任务；设置页可管理外部 MCP 服务器。
- **环境与代码自愈**: 新增自愈工具与环境安装检查——调用因服务端 5xx、限流或无状态失败时，可借助已配置的默认文本模型分析原因并给出或执行修复，安装进度可随时查询。
- **动效大片与配乐**: 新增动效案例图册（案例/风格两套）、逐镜生成、按拍点出静帧预检、源码可修改后再渲染，并支持代码编曲与拍点对齐。
- **短剧创作 Skill 包**: 技能面板可直接查看、下载、安装随包技能（短剧生产、动效大片、VectCut），Agent 按技能说明完成端到端短剧生产。

## User-facing Highlights (en)

- **Expanded MCP toolset**: Agents can now read/write structured data, import novels and read chapters, generate motion feature clips, export finished videos, and run plans continuously; external MCP servers are manageable from Settings.
- **Environment & code self-heal**: A new self-heal tool plus environment install checks — when a call fails with a server 5xx, rate limit, or status-less error, the configured default text model analyzes the cause and proposes or applies a fix, with install progress queryable at any time.
- **Motion feature clips & scoring**: New motion case/style galleries, per-shot generation, beat-synced still-frame preview, editable source before rendering, plus code-based scoring and beat alignment.
- **Short-drama Skill packages**: The skills panel lets you view, download, and install bundled skills (short-drama production, motion clips, VectCut); agents follow them to produce a short drama end to end.

## Fixes

- 任务进度/完成/失败写入改为事务内 CAS，取消与进度更新并发时不再互相覆盖。
- Agent 计划：修复素材导入误判失败、计划执行丢集参数的问题。
- MCP：修正服务器探测与默认连接地址；成员账号不再看到仅管理员可用的连接管理入口。
- 技能包：安装、导出、上传流程加固，避免路径穿越与半安装状态。
- 安全：单用户部署监听非回环地址时，启动日志明确提示「所有请求免登录即 owner」。
