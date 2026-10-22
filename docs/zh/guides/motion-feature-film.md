<!-- lang-switch -->
[English](../../en/guides/motion-feature-film.md) · **简体中文**

# 动效大片（MG）

> 从案例图册挑一条 → 模型按**渲染契约**写出一张单文件 MG 页面 → 本机 Chrome 逐帧渲染成 mp4（顺手配一段程序生成的配乐）→ 把逐镜产物拼成一盘能在剪映里打开的草稿。
> **全程不出网、不调第三方草稿库、不依赖 VectCut 服务**——装机形态没有服务容器,这条是桌面用户唯一走得通的交付路径。

相关变量见 [环境变量参考](../reference/environment-variables.md#动效大片mg),二进制选型见 [ffmpeg 指南](ffmpeg.md#桌面版随包的-ffmpeg实测结论)。

## 1. 为什么是三个环节

| 环节 | 解决的问题 | 落点 |
|---|---|---|
| **图册** | 「想要什么样的片子」得先看得见。37 条案例带分类、时长、分辨率、分镜规格与渲染契约 | `src/novelvideo/freezone/motion_templates.json` + `motion_templates.py`（内置兜底,换图册用 `MOTION_GALLERY_MANIFEST`） |
| **渲染** | 模型产出的是一张会动的网页,不是一个视频文件 | `motion_render/`：引导脚本接管时钟,CDP 逐帧截图,ffmpeg `image2pipe` 流式编码 |
| **交付** | 渲染一次只出一镜,剪辑要的是整条时间线 | `motion_audio/`（配乐）+ `motion_draft/`（剪映 10.2 草稿） |

素材口径（海报与小样片随包、原片按需取）写在 [环境变量参考](../reference/environment-variables.md#动效大片mg) 里：37 条原片约 0.7 GB,不进安装包；每条的 6 秒、480p、无音轨小样片（`motion_previews/<case_id>/preview.mp4`,合计约 15 MB）随包,图册悬停、详情与工具页选中卡直接播这一段,不用等原片下载。

## 2. 怎么用

1. **分镜页 → 动效大片**：打开图册,海报即本地随包的图,鼠标移到卡片上播该案例的小样片,点开详情自动循环播放；点「取用提示词」拿到该案例的分镜规格 + 本系统渲染契约（契约只在图册清单里存一份,提示词库、图册、渲染任务取到的是同一份文字）。
2. **逐镜生成 MG**：模型按契约产出页面后提交渲染任务（`POST …/freezone/motion/render`）。可调宽/高、帧率、时长,以及**配乐开关 `music`（默认开）**。任务进度：0.12 起页 → 0.94 逐帧 → 0.95 编曲归一 → 0.97 登记资产。
3. **导出剪映草稿**：`POST …/freezone/motion/draft`,把选中的成片按顺序贴到主视频轨,每镜自报的拍点落成剪映打点,可选再垫旁白与整片 BGM,整盘复制进本机剪映工程目录。

产物同目录会留下三样：`<job>.mp4`（成片,已合音轨）、`<job>.motion.json`（sidecar：时长、拍点、音频结果）、`<job>.score.wav`（归一后的分轨干声,剪映里想单独拧音乐音量就用它）。页面 `<job>.html` 也留着,「这一帧为什么长这样」可以直接用浏览器打开复现。

## 3. 渲染契约第 3、6 条落到哪

**第 3 条：时长先按拍点网格排,镜头切换与关键动作落在网格上,音效放在动作发生的那一帧。**

- 页面用 `window.EV=[{t,type}]` 自报拍点；`motion_audio/grid.py` 取**间隔中位数**当一拍,推不出才退案例文本的 BPM,再退 120,并把速度折进 45–180 的可听区间（解八度歧义：每两拍打一次点会推出 60,网格按 120 排）。
- 相位对齐到拍点上——卡点片子的网格必须穿过动作帧,否则「落在网格上」是空话。
- **音效提前一帧**（`TRANSIENT_LEAD_FRAMES = 1`）：画面在第 N 帧画出变化,声音要在那一帧已经响着,落在第 N 帧就永远慢一帧（30fps 约 33ms,卡点听感直接散）。剪映时间轴上的**打点仍写原始 EV 时刻**,两个口径各管各的。
- 编曲只有三档：`drive` / `pulse` / `ambient`,由案例分类优先、速度兜底（更具体的分类先判,科普片不排成满编鼓）。声部为 kick/snare/hat/bass/pad/pluck + impact/tick/riser,和声走 Am–F–C–G。
- 合成不采样的第三方音源：纯 numpy 加法合成 + FFT 带通塑形 + 包络,常数功率声像。**同参数逐位一致**,所以敢做快照测试,桌面包里也没有版权与体积问题。

**第 6 条：声音交付统一到 -14 LUFS、真峰值不高于 -1 dBTP。**

- 两遍 `loudnorm`（EBU R128）：第一遍 `-f null` 只量,把 `print_format=json` 量到的五个值原样喂给第二遍并 `linear=true`——单遍动态归一会在一段里来回推拉,几秒的 MG 一听就听得出。
- 合轨 `-c:v copy -c:a aac -b:a 192k -shortest +faststart`,写临时文件再 `os.replace` 原子替换：任务被取消时不能留下半个成片。
- 实测（包内 LGPL ffmpeg,同一页 MG 跑两遍）：只出画面 = 一条 `video/h264`；接配乐 = `video/h264 + audio/aac`,归一前 `-15.47 LUFS / +0.01 dBTP`,**合轨后再量 `-14.05 LUFS / -1.00 dBTP`**。
- 量到的响度事实随 sidecar 落盘（`audio.loudness`）,「为什么这集听起来响」可查。

**失败不许拖垮整单**：本机没有可用 ffmpeg、或编曲排不出音符时,渲染任务交付**无声片**,sidecar 与结果里记 `audio.engine = "silent"` 加原因（`off` 表示提交方明确只要画面）。取消信号不会被这个降级吞掉（`TaskCancelled` 不是 `MotionAudioError`/`OSError`）。

## 4. 剪映草稿的形状

自研,不用 pyJianYingDraft、不起 VectCut：

- 时间是**微秒整数**,路径用正斜杠,写根 `draft_content.json` **和** `Timelines/<UUID>/` 及各项镜像,并清掉 `.locked`——只写根文件在剪映 9.x/10.x 会打开成空时间轴。
- 一条剪映轨上的片段不许重叠,所以**视频 / 旁白 / BGM 分轨**；整片旁白与逐镜旁白互斥。
- 提交时自检随包模板骨架（`services/vectcut/template_jianying_10_2`）是否完整,缺了当场 409 说清楚,而不是落出一盘坏草稿。
- **内嵌配乐会让位**：这镜成片里封了程序生成的配乐,草稿又进了旁白 → 片段音量 0.2；只有外部 BGM → 0.35（都不归零,用户在剪映里删掉外部 BGM 还留得下拍点）；无声渲染不让位。判定依据是 sidecar 里 `audio.engine == "code-arranged"`,不是猜的。

## 5. 接口一览

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/projects/{p}/freezone/motion/cases` | 图册目录（分类/标签筛选、海报与原片地址） |
| GET | `/api/v1/projects/{p}/freezone/motion/cases/{id}/prompt` | 取该案例的完整提示词（分镜规格 + 渲染契约） |
| GET | `/api/v1/projects/{p}/freezone/motion/renders` | 已渲染成片清单（「导出剪映草稿」的候选,直接扫渲染目录连 sidecar 一起读回） |
| POST | `/api/v1/projects/{p}/freezone/motion/render` | 提交一镜渲染（`music` 控制是否配乐） |
| POST | `/api/v1/projects/{p}/freezone/motion/draft` | 逐镜成片 → 剪映草稿（可带旁白与 BGM） |
| GET | `/api/v1/motion-gallery/{case_id}/{filename}` | 海报/小样片/原片托管 + 按需取源缓存（支持 Range）；`preview.mp4` 只读取随包文件,`preview_ready` / `preview_version` 由目录接口逐条给出 |
| GET | `/api/v1/motion-gallery/{case_id}/preview.mp4` | 随包小样片（6 秒 / 480p / 无音轨,Range 可分片）,卡片悬停与详情直接播这一段 |

## 6. 三种交付形态

| 形态 | ffmpeg | 浏览器 | 备注 |
|---|---|---|---|
| **Docker / web** | 镜像 `apt-get install ffmpeg` 已带 | 需自备：`ST_CHROME_CDP_URL` 连现成调试实例,或 `ST_CHROME_PATH` 指可执行 | 模型出页面那一环要 `MODEL_PROVIDER` / `MODEL_API_KEY` |
| **Windows exe** | 随包 `resources/bin/ffmpeg.exe`（BtbN **LGPL**,无 `libx264`） | 随包 `resources/bin/chrome/` | 编排器把 `resources/bin` 前置进后端 `PATH` 并注入 `APP_RESOURCE_PATH` |
| **macOS dmg** | 构建机 `PATH` 上的 ffmpeg（通常 Homebrew,含 x264）或 `FFMPEG_BIN=` | 随包 Chrome for Testing（`bundle.resources` 已显式收 `resources/bin/chrome/**/*`；单层 glob 收不进目录） | 与 Windows 同一份 Python 逻辑,没有平台分支；**dmg 的实包产出与装机启动仍未验收** |

渲染链路**不写死 `libx264`**：`src/novelvideo/video_encoder.py` 先 `ffmpeg -encoders` 探本机清单,按 `libx264 → h264_videotoolbox → h264_mf → libopenh264` 取第一个可用的 H.264 实现,交付给剪辑软件/平台的仍然是 H.264。这一处解析器是全系统唯一的 `-c:v` 来源,整集合成与 `jobs.py` 那批一键工具也走它,详见 [ffmpeg 指南](ffmpeg.md)。

## 7. 排查

| 现象 | 原因与处置 |
|---|---|
| 「找不到可用的 Chrome」 | 三个取物档都没命中：设 `ST_CHROME_PATH`（指可执行文件）或 `ST_CHROME_CDP_URL`（连现成实例）。指了但路径不存在会吵,不会静默当成没浏览器 |
| 结果里 `audio.engine = "silent"` | 本机没有可用 ffmpeg 或响度归一失败,已交付无声片。原因在 `audio.error`；装上 ffmpeg（或设 `FFMPEG_PATH`）后重渲这一镜即可 |
| 「这一镜排不出配乐」 | 时长非法或页面没自报拍点且推不出网格。检查页面 `window.DUR` 与 `window.EV` |
| 剪映打开是空时间轴 | 草稿被剪映自己的自动保存覆盖过,或目录不完整。关闭剪映 → 重新导出 → 回首页重开工程 |
| 409「没找到随包的剪映 10.2 草稿模板」 | 安装包资源不完整或布局非标准：设 `MOTION_DRAFT_TEMPLATE_DIR`,或 `NOVELVIDEO_PROJECT_ROOT` 指工程根 |
| 画面正常但听不见 | 这镜是无声交付（看 `audio.engine`）,或草稿里配乐被让位到 0.2/0.35（删掉外部 BGM/旁白轨即恢复) |

## 相关

- [ffmpeg 指南](ffmpeg.md) ｜ [环境变量参考](../reference/environment-variables.md) ｜ [桌面版环境变量契约](../../../desktop/README.md)
- 测试：`tests/test_freezone_motion_{gallery,page,render,render_task,audio,draft,draft_task}.py`
