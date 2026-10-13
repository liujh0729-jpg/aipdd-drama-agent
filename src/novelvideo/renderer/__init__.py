"""[aipdd fork delta] HyperFrames 渲染服务客户端（引擎即服务）。

把已就位的 HyperFrames 项目目录交给本地/容器内的 ``@hyperframes/producer``
HTTP 渲染服务，产出 MP4。讲解视频与渠道短视频共用这一渲染通路（Phase 1/2）。
"""

from __future__ import annotations

from novelvideo.renderer.client import (
    RenderResult,
    RendererNotConfigured,
    RenderFailed,
    is_configured,
    render_project,
    renderer_base_url,
)

__all__ = [
    "RenderResult",
    "RendererNotConfigured",
    "RenderFailed",
    "is_configured",
    "render_project",
    "renderer_base_url",
]
