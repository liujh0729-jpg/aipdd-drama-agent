# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 动效大片（MG）的本地渲染引擎。

一句话：**代码即素材** —— 模型按渲染契约产出一页自包含的 HTML/JS
（`window.render(t)` / `window.DUR` / 可选 `window.EV`），本模块用自带的无头
Chrome 把时间轴一帧一帧钉住截图，再把帧流喂给随包的 ffmpeg 编成 MP4。渲染这一段
全程不出网、不订阅任何渲染服务，与画风图册 / 分镜链路用的是同一批随包二进制；
唯一可能出网的是"让模型写这页代码"的那次文本网关调用（`page` 拼提示词、由调用方
经 `_call_freezone_leaf` 分发，出网口径记在 `FREEZONE_LEAF_EGRESS` 里）。

模块分工（渲染任务 leaf 调 :func:`render_motion_video`，拍点预检调
:func:`render_motion_stills` —— 两者共用同一个 CDP 截图通道，预检看到的就是成片会有的画面）：

- `chrome`：找 Chrome（`ST_CHROME_CDP_URL` → `ST_CHROME_PATH` → 包内 → PATH）并
  拉起/收尾一个我们自己掌控的实例；
- `page`：渲染的**前一级**——把文本模型的回复收成那一个 HTML 文档，并在起浏览器之前
  按契约预检（模型没写 `window.render`/`window.DUR` 时，给用户一句可行动的话）；
- `capturer`：开一页、把页面时钟与 rAF 钉在指定秒上、逐帧交回 PNG 字节；
- `encoder`：PNG 帧流 → ffmpeg → H.264 MP4；
- `pipeline`：把上面三步串起来并做时长/帧数换算。

与 `freezone/chrome_cdp.py` 的关系：那份是给「抖音详情」连**用户正在用的** Chrome，
所以绝不启动也不杀进程；这里要的是一条能独占时间轴的通道，因此复用它的裸 CDP
客户端（`_connect_browser_client` / `CdpClient`），但自己拉起、自己收尾。
"""

from __future__ import annotations

from novelvideo.freezone.motion_render.capturer import (
    MotionPage,
    MotionRenderError,
    MotionTiming,
    parse_timing,
    with_bootstrap,
)
from novelvideo.freezone.motion_render.chrome import (
    ChromeUnavailable,
    chrome_endpoint,
    chrome_executable,
)
from novelvideo.freezone.motion_render.encoder import (
    build_encoder_command,
    encode_frames,
    ffmpeg_executable,
)
from novelvideo.freezone.motion_render.page import (
    MAX_PAGE_CHARS,
    build_motion_page_prompt,
    extract_motion_page,
    motion_page_error,
)
from novelvideo.freezone.motion_render.pipeline import (
    FramePlan,
    MotionRenderResult,
    MotionStill,
    MotionStillsResult,
    chrome_available,
    clamp_fps,
    clamp_max_stills,
    page_source_path,
    plan_frames,
    render_motion_stills,
    render_motion_video,
    render_preflight,
    still_times,
    stills_preflight,
)

__all__ = [
    "ChromeUnavailable",
    "FramePlan",
    "MAX_PAGE_CHARS",
    "MotionPage",
    "MotionRenderError",
    "MotionRenderResult",
    "MotionStill",
    "MotionStillsResult",
    "MotionTiming",
    "build_encoder_command",
    "build_motion_page_prompt",
    "chrome_available",
    "chrome_endpoint",
    "chrome_executable",
    "clamp_fps",
    "clamp_max_stills",
    "encode_frames",
    "extract_motion_page",
    "ffmpeg_executable",
    "motion_page_error",
    "page_source_path",
    "parse_timing",
    "plan_frames",
    "render_motion_stills",
    "render_motion_video",
    "render_preflight",
    "still_times",
    "stills_preflight",
    "with_bootstrap",
]
