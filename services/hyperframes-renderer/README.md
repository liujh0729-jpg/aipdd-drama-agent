# HyperFrames 渲染服务（renderer）

讲解视频与渠道短视频的**底层渲染服务**：用
[`@hyperframes/producer`](https://www.npmjs.com/package/@hyperframes/producer)
（Puppeteer/Chromium + FFmpeg）无头把 api 侧产出的 HyperFrames 项目目录渲染成 MP4。
这是「引擎即服务」落点——Python 保留解说词/分镜/时间轴/TTS 控制面，只把画面渲染外包给本服务。

## 运行契约（HTTP）

镜像入口即 producer 的独立服务（`node_modules/@hyperframes/producer/dist/public-server.js`，默认端口 `9847`）：

- `POST /render` — 阻塞式渲染，请求体 `{projectDir, outputPath?, fps?, quality?, format?, entryFile?, outputResolution?, variables?}`；成功返回 `{success, outputPath, outputToken, outputUrl, fileSize, videoDurationSeconds, outcome, warnings}`。
- `GET /outputs/:token` — 按 token 下载渲染好的 MP4（跨机、无共享卷时兜底）。
- `GET /health` — 健康检查。

调用方是 `src/novelvideo/renderer/client.py`（只认 `DRAMACLAW_RENDERER_URL`）。

## 共享卷 / projectDir

`/render` 用**服务器本地文件系统的 projectDir 路径**加载多帧项目（index.html + 逐帧
`compositions/frames/*.html` + 字幕 + 配音），内联 `html` 只落一个 index.html、兄弟资源会 404，
因此独立容器必须与 api 共享项目卷：compose 里 renderer 与 api 同挂 `ce-data:/data`，
`PRODUCER_RENDERS_DIR` 也落在 `/data/output/_hyperframes_renders`，产物 api 直接可读。
桌面端 api 与本地 producer 子进程共享宿主 FS，路径语义一致。

## 本地起服务

```bash
cd services/hyperframes-renderer
npm install
npx puppeteer browsers install chrome-headless-shell   # linux/x64 BeginFrame 快捕
PRODUCER_PORT=9847 npm start 2>/dev/null || \
  node node_modules/@hyperframes/producer/dist/public-server.js
```

## 关键环境

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `PRODUCER_PORT` | 9847 | 服务端口 |
| `PRODUCER_RENDERS_DIR` | /data/output/_hyperframes_renders | 产物目录（与 api 共享卷） |
| `PRODUCER_MAX_CONCURRENT_RENDERS` | 1 | 并发渲染数（重活，低并发更稳） |
| `PRODUCER_DISABLE_HEALTH_WORKER` | 1 | 关健康副端口，只留 9847 |
| `HYPERFRAMES_BROWSER_PATH` | — | 逃生阀：指定系统 Chrome/headless-shell |

## 运行注意

- Chrome 沙箱由引擎 `buildChromeArgs` 恒带 `--no-sandbox`/`--disable-setuid-sandbox`/`--disable-dev-shm-usage`，容器内 root 可跑；编排侧另配 `seccomp=unconfined` 与更大 `shm_size`（见 compose 的 `renderer`）。
- 依赖与浏览器全部构建期就位，运行期零外联；不接 HeyGen/`npx hyperframes`。
- 版本固定 `@hyperframes/producer@0.8.150`（core/engine/parsers/lint 同版本），升级需回归渲染链路。
- 许可：HyperFrames 为 Apache-2.0，可 vendor；记录进依赖清单/SBOM。
