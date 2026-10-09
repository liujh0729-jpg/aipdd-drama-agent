"""freezone.uploads 子模块（自 api/routes/freezone.py 拆分）。"""
from __future__ import annotations

import asyncio
import base64
import binascii
import os
from pathlib import Path
from typing import Annotated
from typing import Any

import httpx

from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import HTTPException
from fastapi import UploadFile

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone as _freezone
from novelvideo.api.schemas import FreezoneAssetCopyRequest
from novelvideo.api.schemas import FreezoneBlenderIngestRequest
from novelvideo.api.schemas import FreezoneBlenderPullRequest
from novelvideo.api.schemas import FreezoneThreeDViewerScreenshotRequest
from novelvideo.freezone import canvas_store
from novelvideo.freezone.canvas_lock import CanvasLockBusy
from novelvideo.api.upload_workers import run_asset_upload_operation
from novelvideo.freezone.asset_copy import AssetCopyError
from novelvideo.freezone.asset_copy import allocate_target_path
from novelvideo.freezone.asset_copy import copy_project_file
from novelvideo.freezone.asset_copy import parse_project_asset_url
from novelvideo.freezone.asset_copy import resolve_source_file
from novelvideo.freezone.paths import CANVAS_ID_RE
from novelvideo.freezone.paths import output_path_for_job
from novelvideo.freezone.paths import uploads_dir
from novelvideo.project_context import ProjectContext
from novelvideo.utils.net_guard import PublicUrlRejected, ensure_public_http_url

from ._common import (TAG_FREEZONE_MEDIA, logger)

router = APIRouter()


# ============================================================
# 图片处理：上传
# ============================================================

REFERENCE_FILE_MAX_BYTES = 100 * 1024 * 1024
_UPLOAD_READ_CHUNK_BYTES = 1024 * 1024


async def _read_upload_contents(
    file: UploadFile,
    *,
    max_bytes: int | None = None,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_UPLOAD_READ_CHUNK_BYTES):
        total += len(chunk)
        if max_bytes is not None and total > max_bytes:
            raise HTTPException(413, "reference file must be 100 MB or smaller")
        chunks.append(chunk)
    return b"".join(chunks)


def _persist_freezone_upload(target: Path, contents: bytes) -> None:
    """Write one upload to a staging file and atomically publish it."""

    target.parent.mkdir(parents=True, exist_ok=True)
    staged_path = _freezone.create_staged_upload_file(
        target.parent,
        prefix=".upload-",
        suffix=".tmp",
        destination=target,
    )
    try:
        staged_path.write_bytes(contents)
        staged_path.replace(target)
    finally:
        staged_path.unlink(missing_ok=True)


async def _save_freezone_upload(
    project: str,
    file: UploadFile,
    user: dict,
    *,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    ctx, _username, _project_name, project_dir, _output_dir = (
        await _freezone._resolve_freezone_project(project, user)
    )
    target_dir = uploads_dir(project_dir)
    filename = _freezone.safe_upload_filename(file.filename)
    target = target_dir / filename
    contents = await _read_upload_contents(file, max_bytes=max_bytes)
    await run_asset_upload_operation(_persist_freezone_upload, target, contents)
    rel = target.relative_to(project_dir).as_posix()
    return {
        "ok": True,
        "data": {
            "url": (_freezone.make_static_url_for_context(ctx, rel, local_path=target)),
            "filename": filename,
            "size": len(contents),
        },
    }


@router.post("/projects/{project}/freezone/upload", tags=[TAG_FREEZONE_MEDIA])
async def freezone_upload(
    project: str,
    file: Annotated[UploadFile, File()],
    user: dict = Depends(get_api_user),
):
    """把外部资源上传保存到 `freezone/_uploads/`。"""
    return await _save_freezone_upload(project, file, user)


@router.post("/projects/{project}/freezone/reference-file-upload", tags=[TAG_FREEZONE_MEDIA])
async def freezone_reference_file_upload(
    project: str,
    file: Annotated[UploadFile, File()],
    user: dict = Depends(get_api_user),
):
    """保存视频模型参考文件，并限制为不超过 100 MB。"""
    return await _save_freezone_upload(
        project,
        file,
        user,
        max_bytes=REFERENCE_FILE_MAX_BYTES,
    )


_ASSET_COPY_CONCURRENCY = 4


@router.post("/projects/{project}/freezone/assets/copy", tags=[TAG_FREEZONE_MEDIA])
async def freezone_copy_assets_from_project(
    project: str,
    body: FreezoneAssetCopyRequest,
    user: dict = Depends(get_api_user),
):
    """跨项目粘贴：把源项目的素材拷进本项目的 `freezone/_uploads/`。

    前端只报「这些 URL 要拷到当前项目」，字节不再经过浏览器：OSS 可用时在服务端
    CopyObject，否则文件系统拷贝。目标项目要 editor，每个源项目要 viewer；单条失败
    只记进 `failed`（reason 为 invalid_source / not_found / forbidden / unavailable /
    copy_failed），不影响同批其它文件。本来就属于目标项目的 URL 原样跳过。
    """
    ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    target_dir = uploads_dir(project_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    mapping: dict[str, str] = {}
    failed: list[dict[str, str]] = []
    source_projects: dict[str, ProjectContext | str] = {}

    async def _source_project(project_id: str) -> ProjectContext | str:
        cached = source_projects.get(project_id)
        if cached is not None:
            return cached
        try:
            src_ctx, *_ = await _freezone._resolve_freezone_project(project_id, user, required_role="viewer")
            result: ProjectContext | str = src_ctx
        except HTTPException as exc:
            if exc.status_code == 403:
                result = "forbidden"
            elif exc.status_code == 404:
                result = "not_found"
            else:
                result = "unavailable"
        source_projects[project_id] = result
        return result

    # 第一遍：解析 + 授权 + 定位源文件，同一个源文件只排一次拷贝。
    jobs: dict[Path, tuple[Path, list[str]]] = {}
    for raw_url in dict.fromkeys(body.sources):
        parsed = parse_project_asset_url(raw_url)
        if parsed is None:
            failed.append({"source": raw_url, "reason": "invalid_source"})
            continue
        source_project_id, rel = parsed
        if source_project_id == ctx.project_id:
            continue
        source = await _source_project(source_project_id)
        if isinstance(source, str):
            failed.append({"source": raw_url, "reason": source})
            continue
        # 准备阶段（定位源文件、给目标起名）的任何异常都只算这一条失败，别拖垮整批。
        try:
            source_path = resolve_source_file(Path(source.output_dir), rel)
            job = jobs.get(source_path)
            if job is None:
                job = jobs[source_path] = (allocate_target_path(target_dir, source_path.name), [])
        except AssetCopyError as exc:
            failed.append({"source": raw_url, "reason": exc.reason})
            continue
        except (OSError, ValueError) as exc:
            logger.warning("cross-project asset prepare failed for %s: %s", raw_url, exc)
            failed.append({"source": raw_url, "reason": "copy_failed"})
            continue
        job[1].append(raw_url)

    # 第二遍：有限并发地拷，失败的把半成品清掉。
    semaphore = asyncio.Semaphore(_ASSET_COPY_CONCURRENCY)

    async def _copy_one(source_path: Path, target: Path) -> str | None:
        async with semaphore:
            try:
                method = await asyncio.to_thread(copy_project_file, source_path, target)
            except OSError as exc:
                logger.warning(
                    "cross-project asset copy failed %s -> %s: %s", source_path, target, exc
                )
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass
                return None
        logger.info(
            "cross-project asset copied via %s: %s -> %s/%s",
            method,
            source_path.name,
            ctx.project_id,
            target.name,
        )
        new_rel = target.relative_to(project_dir).as_posix()
        return _freezone.make_static_url_for_context(ctx, new_rel, local_path=target)

    results = await asyncio.gather(
        *(_copy_one(source_path, target) for source_path, (target, _urls) in jobs.items())
    )
    for (_target, urls), new_url in zip(jobs.values(), results):
        for raw_url in urls:
            if new_url is None:
                failed.append({"source": raw_url, "reason": "copy_failed"})
            else:
                mapping[raw_url] = new_url

    return {"ok": True, "data": {"mapping": mapping, "failed": failed}}


@router.post("/projects/{project}/freezone/three-d-viewer/screenshot", tags=[TAG_FREEZONE_MEDIA])
async def freezone_three_d_viewer_screenshot(
    project: str,
    body: FreezoneThreeDViewerScreenshotRequest,
    user: dict = Depends(get_api_user),
):
    """保存内置 3D viewer 普通截图到 Freezone 输出目录。"""

    ctx, username, project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    prefix = "data:image/png;base64,"
    data_url = (body.data_url or "").strip()
    if not data_url.startswith(prefix):
        raise HTTPException(400, "expected PNG data URL")
    try:
        payload = base64.b64decode(data_url[len(prefix) :], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(400, "invalid PNG data URL") from exc
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(400, "screenshot payload is not PNG")
    if len(payload) > 20 * 1024 * 1024:
        raise HTTPException(413, "screenshot is too large")

    job_id = _freezone._new_job_id()
    out = output_path_for_job(project_dir, "three_d_viewer", job_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(payload)
    rel_path = out.relative_to(project_dir).as_posix()
    label = (body.label or "3D viewer screenshot").strip() or "3D viewer screenshot"
    return {
        "ok": True,
        "data": {
            "id": job_id,
            "label": label,
            "node_id": body.node_id,
            "rel_path": rel_path,
            "url": (_freezone.make_static_url_for_context(ctx, rel_path, local_path=out)),
            "media_type": "image",
            "size": len(payload),
        },
    }


# ============================================================
# [aipdd fork delta] Blender MCP 白模视频联合（B+C）
#   - 拉取：驱动本机 Blender 渲染导出白模(clay)视频 → 入库成本项目静态直链 →
#     写回指定画布「白模视频」节点。Blender 不可达时回 {status:"unreachable"}（HTTP 200
#     信封，不抛错），前端据此弹手动入库兜底，而非把错误直接吐给用户。
#   - 入库：把外链 / 已上传的本项目直链归一成本项目静态 URL 再写节点——画布保存有
#     reject_new_foreign_media_refs（SuperTale#192），外链直塞节点会被 422 拒。
# ============================================================
WHITE_MODEL_NODE_TYPE = "whiteModelVideoNode"
_BLENDER_REMOTE_MAX_BYTES = 200 * 1024 * 1024


async def _fetch_white_model_bytes(url: str) -> bytes:
    """抓取可访问外链白模视频字节（有限大小，不重定向滥用）。

    [aipdd fork delta] 这是认证用户可控 URL 的直接回字节路径：先过公网 SSRF 守卫
    （拒私网/回环/链路本地/保留地址与非 http(s) 协议），并关闭重定向——3xx 一律拒，
    否则一次合法外链就能 302 到内网元数据端点绕过前置校验。
    """
    try:
        ensure_public_http_url(url)
    except PublicUrlRejected as exc:
        # 不回显原始 URL（可能含内网主机名），只给稳定原因。
        raise HTTPException(400, "白模视频外链地址不合法") from exc
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=15.0),
            follow_redirects=False,
        ) as client:
            resp = await client.get(url)
            if resp.is_redirect or resp.is_redirection:
                raise HTTPException(400, "白模视频外链不允许重定向")
            resp.raise_for_status()
            data = resp.content
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"无法抓取白模视频外链：{exc}") from exc
    if len(data) > _BLENDER_REMOTE_MAX_BYTES:
        raise HTTPException(413, "白模视频外链超过 200 MB")
    if not data:
        raise HTTPException(400, "白模视频外链返回空内容")
    return data


async def ingest_white_model_video(
    ctx: ProjectContext,
    project_dir: Path,
    *,
    source_path: Path | None = None,
    source_url: str | None = None,
) -> dict[str, Any]:
    """把 Blender 导出文件或可访问外链入库成本项目 ``/static`` 直链。

    同项目已有的静态直链原样放行（不重复入库）；其余来源（本地导出件 / 外项目或外部
    链）统一落进 ``freezone/_uploads/`` 并以本项目身份回 URL。
    """
    if source_path is not None:
        path = Path(source_path)
        contents = await asyncio.to_thread(path.read_bytes)
        filename = _freezone.safe_upload_filename(path.name)
    elif source_url:
        raw = str(source_url).strip()
        parsed = parse_project_asset_url(raw)
        if parsed is not None and parsed[0] == ctx.project_id:
            return {"url": raw, "filename": Path(parsed[1]).name}
        contents = await _fetch_white_model_bytes(raw)
        tail = raw.rsplit("/", 1)[-1].split("?")[0].split("#")[0]
        filename = _freezone.safe_upload_filename(tail or "blender-white-model.mp4")
    else:
        raise HTTPException(400, "白模视频来源缺失（source_path 或 source_url）")
    target_dir = uploads_dir(project_dir)
    target = target_dir / filename
    await run_asset_upload_operation(_persist_freezone_upload, target, contents)
    rel = target.relative_to(project_dir).as_posix()
    url = _freezone.make_static_url_for_context(ctx, rel, local_path=target)
    return {"url": url, "filename": filename}


def _apply_white_model_to_nodes(
    existing: dict | None, node_id: str, url: str, filename: str, commit_slot: bool
) -> list[dict]:
    """在节点列表里定位 node_id 并写白模视频（图类型不符 / 未找到则报错）。"""
    nodes = (existing or {}).get("nodes") or []
    new_nodes: list[dict] = []
    found = False
    for node in nodes:
        if isinstance(node, dict) and node.get("id") == node_id:
            found = True
            if node.get("type") != WHITE_MODEL_NODE_TYPE:
                raise HTTPException(
                    400,
                    f"节点 {node_id} 不是白模视频节点（type={node.get('type')!r}）",
                )
            data = dict(node.get("data") or {})
            data["videoUrl"] = url
            data["sourceFileName"] = filename
            data["isUploading"] = False
            data["isPulling"] = False
            if commit_slot:
                data["committed_slot_url"] = url
                data["slot_target"] = {"kind": "white_model_reference", "node": node_id}
            merged = dict(node)
            merged["data"] = data
            new_nodes.append(merged)
        else:
            new_nodes.append(node)
    if not found:
        raise HTTPException(404, f"画布中未找到节点 {node_id}")
    return new_nodes


async def commit_white_model_video_node(
    *,
    project: str,
    ctx: ProjectContext,
    project_dir: Path,
    user: dict,
    canvas_id: str,
    node_id: str,
    url: str,
    filename: str,
    commit_slot: bool = False,
) -> dict[str, Any]:
    """把白模视频直链写回指定画布节点（读整图→改单节点→带 base_revision 写回）。

    与 ``PUT /freezone/canvases/{id}`` 同走 ``_prepare_canvas_payload_for_write``，保证
    revision / 元数据 / 归属一致；URL 已在本项目内，不触发 foreign-media 守卫。
    """
    if not CANVAS_ID_RE.match(canvas_id):
        raise HTTPException(400, "invalid canvas_id")
    canvas_project_dir = _freezone._canvas_state_project_dir(ctx, project_dir)
    try:
        existing = canvas_store.read_canvas(canvas_project_dir, canvas_id)
    except (canvas_store.CanvasStoreError, CanvasLockBusy) as exc:
        _freezone._raise_canvas_store_http(exc)
    if not isinstance(existing, dict):
        raise HTTPException(404, "canvas not found")
    base_revision = existing.get("revision") if isinstance(existing.get("revision"), int) else None
    # 预校验（报错语义清晰）；真正写回在保存锁内重新基于最新 existing 定位。
    _apply_white_model_to_nodes(existing, node_id, url, filename, commit_slot)

    def build_payload(current_existing: dict | None) -> dict:
        mutated_nodes = _apply_white_model_to_nodes(
            current_existing, node_id, url, filename, commit_slot
        )
        raw_payload = {
            **dict(current_existing or {}),
            "nodes": mutated_nodes,
            "edges": (current_existing or {}).get("edges") or [],
            "viewport": (current_existing or {}).get("viewport"),
        }
        return _freezone._prepare_canvas_payload_for_write(
            project_id=project,
            canvas_id=canvas_id,
            body=None,
            raw_payload=raw_payload,
            existing=current_existing,
            user=user,
        )

    try:
        await canvas_store.save_canvas_async(
            canvas_project_dir,
            canvas_id,
            base_revision=base_revision,
            build_payload=build_payload,
            save_source="agent",
        )
    except (canvas_store.CanvasStoreError, CanvasLockBusy) as exc:
        _freezone._raise_canvas_store_http(exc)
    return {"url": url, "node_id": node_id, "canvas_id": canvas_id}


@router.post(
    "/projects/{project}/freezone/blender/pull-white-model-video",
    tags=[TAG_FREEZONE_MEDIA],
)
async def freezone_blender_pull_white_model(
    project: str,
    body: FreezoneBlenderPullRequest,
    user: dict = Depends(get_api_user),
):
    """拉取本机 Blender 白模视频并写入指定画布节点（不可达则回 unreachable 供前端兜底）。"""
    from novelvideo import blender_bridge

    ctx, _username, _project_name, project_dir, _output_dir = (
        await _freezone._resolve_freezone_project(project, user)
    )
    export_dir = Path(
        os.environ.get(blender_bridge.BLENDER_EXPORT_DIR_ENV, "").strip()
        or (project_dir / "blender")
    )
    try:
        exported = await asyncio.to_thread(
            blender_bridge.pull_white_model_video,
            export_dir,
            host=body.host,
            port=body.port,
        )
    except blender_bridge.BlenderUnreachable as exc:
        return {"ok": True, "data": {"status": "unreachable", "message": str(exc)}}
    ingested = await ingest_white_model_video(ctx, project_dir, source_path=exported)
    await commit_white_model_video_node(
        project=project,
        ctx=ctx,
        project_dir=project_dir,
        user=user,
        canvas_id=body.canvas_id,
        node_id=body.node_id,
        url=ingested["url"],
        filename=ingested["filename"],
        commit_slot=body.commit_slot,
    )
    return {
        "ok": True,
        "data": {
            "status": "pulled",
            "url": ingested["url"],
            "filename": ingested["filename"],
            "canvas_id": body.canvas_id,
            "node_id": body.node_id,
        },
    }


@router.post(
    "/projects/{project}/freezone/blender/ingest-white-model-video",
    tags=[TAG_FREEZONE_MEDIA],
)
async def freezone_blender_ingest_white_model(
    project: str,
    body: FreezoneBlenderIngestRequest,
    user: dict = Depends(get_api_user),
):
    """把白模视频来源（外链或本项目直链）入库并写入指定画布节点（手动兜底 / Agent 落片）。"""
    ctx, _username, _project_name, project_dir, _output_dir = (
        await _freezone._resolve_freezone_project(project, user)
    )
    ingested = await ingest_white_model_video(ctx, project_dir, source_url=body.url)
    await commit_white_model_video_node(
        project=project,
        ctx=ctx,
        project_dir=project_dir,
        user=user,
        canvas_id=body.canvas_id,
        node_id=body.node_id,
        url=ingested["url"],
        filename=ingested["filename"],
        commit_slot=body.commit_slot,
    )
    return {
        "ok": True,
        "data": {
            "status": "ingested",
            "url": ingested["url"],
            "filename": ingested["filename"],
            "canvas_id": body.canvas_id,
            "node_id": body.node_id,
        },
    }
