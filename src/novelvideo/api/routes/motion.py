"""[aipdd fork delta] 动效大片素材的公开托管端点。

`/api/v1/motion-gallery/<case_id>/<文件名>` 与画风图墙的 `/style-gallery` 一样必须
免鉴权：画布和图册里的 `<img>` / `<video>` 不会带 API token。

两类素材两种来路：海报读随包文件，一次读完回字节；原片可能是几十 MB，先查本机
缓存、没有再按 `MOTION_GALLERY_MIRROR` 现取，然后**带 Range 分片回** —— `<video>`
拖进度条靠的就是 Range，整文件 200 的话播放器只能从头读到尾。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from novelvideo.freezone import motion_media

router = APIRouter()

_POSTER_CACHE_CONTROL = "public, max-age=300"
_VIDEO_CACHE_CONTROL = "public, max-age=86400"


@router.get("/motion-gallery/{case_id}/{filename}", include_in_schema=False)
async def get_motion_gallery_asset(case_id: str, filename: str, request: Request):
    """提供案例海报或原片；路径严格以图册清单为 allowlist。"""
    if filename == motion_media.POSTER_BASENAME:
        path = motion_media.poster_path(case_id)
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="Poster not found")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise HTTPException(status_code=404, detail="Poster not found") from exc
        return Response(
            content=data,
            media_type=motion_media.sniff_content_type(data),
            headers={"Cache-Control": _POSTER_CACHE_CONTROL},
        )

    path = await motion_media.resolve_video(case_id, filename)
    if path is None:
        raise HTTPException(status_code=404, detail="Asset not found")

    size = path.stat().st_size
    media_type = motion_media.media_type_for(filename)
    range_header = request.headers.get("range", "")
    if not range_header:
        return StreamingResponse(
            motion_media.open_chunk(path, 0, size),
            media_type=media_type,
            headers=motion_media.video_headers(size, _VIDEO_CACHE_CONTROL),
        )

    span = motion_media.parse_range(range_header, size)
    if span is None:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
        )
    start, end = span
    return StreamingResponse(
        motion_media.open_chunk(path, start, end - start + 1),
        status_code=206,
        media_type=media_type,
        headers=motion_media.video_partial_headers(start, end, size, _VIDEO_CACHE_CONTROL),
    )
