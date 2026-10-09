"""风格管理端点。"""

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

logger = logging.getLogger("novelvideo.api.styles")

from novelvideo.api.auth import get_api_user, require_owner
from novelvideo.api.deps import resolve_project_scope
from novelvideo.api.schemas import StylePreviewRequest


def _custom_preview_url(project: str | None, preview_path: str | None) -> str | None:
    if not project or not preview_path:
        return None
    from urllib.parse import quote

    return f"/api/v1/projects/{quote(project, safe='')}/media/{quote(preview_path, safe='/')}"

router = APIRouter()


def _requester_user_id(resolved: Any, user: dict) -> str:
    ctx = getattr(resolved, "ctx", None)
    return str(
        getattr(ctx, "requester_user_id", "")
        or user.get("id")
        or user.get("user_id")
        or user.get("username")
        or ""
    )


@router.get("/styles")
async def list_styles(
    project: str | None = Query(None, description="项目名；提供时返回该项目的自定义风格"),
    user: dict = Depends(get_api_user),
):
    """列出所有风格（预设 + 自定义）。"""
    from novelvideo.services.style_service import StyleService

    username = user["username"]
    project_name = project
    project_dir = None
    state_dir = None
    if project:
        resolved = await resolve_project_scope(project, user, required_role="viewer")
        username = resolved.username
        project_name = resolved.project_name
        project_dir = resolved.project_dir
        state_dir = resolved.state_dir
    styles = StyleService.list_all_styles(
        username=username,
        project=project_name,
        project_dir=project_dir,
        state_dir=state_dir,
    )
    for style in styles:
        if style.get("type") == "custom":
            style["preview_url"] = _custom_preview_url(
                project,
                style.get("preview_path"),
            )
    return {"ok": True, "data": styles}


@router.get("/styles/{style_id}")
async def get_style(
    style_id: str,
    project: str | None = Query(None, description="项目名"),
    user: dict = Depends(get_api_user),
):
    """获取风格详情。"""
    from novelvideo.services.style_service import StyleService

    username = user["username"]
    project_name = project
    project_dir = None
    state_dir = None
    if project:
        resolved = await resolve_project_scope(project, user, required_role="viewer")
        username = resolved.username
        project_name = resolved.project_name
        project_dir = resolved.project_dir
        state_dir = resolved.state_dir
    style = StyleService.get_style(
        style_id,
        username=username,
        project=project_name,
        project_dir=project_dir,
        state_dir=state_dir,
    )
    if style is None:
        return {"ok": False, "error": f"Style '{style_id}' not found"}

    payload = style.model_dump() if hasattr(style, "model_dump") else style.__dict__
    if not payload.get("is_preset"):
        payload["preview_url"] = _custom_preview_url(project, payload.get("preview_path"))
    return {"ok": True, "data": payload}


@router.get("/styles/{style_id}/preview")
async def get_style_preview(
    style_id: str,
    project: str | None = Query(None, description="项目名"),
    user: dict = Depends(get_api_user),
):
    """返回预设风格的参考预览图。"""
    from novelvideo.services.style_service import StyleService

    username = user["username"]
    project_name = project
    project_dir = None
    state_dir = None
    if project:
        resolved = await resolve_project_scope(project, user, required_role="viewer")
        username = resolved.username
        project_name = resolved.project_name
        project_dir = resolved.project_dir
        state_dir = resolved.state_dir

    if not StyleService.get_preset(style_id):
        style = StyleService.get_style(
            style_id,
            username=username,
            project=project_name,
            project_dir=project_dir,
            state_dir=state_dir,
        )
        if style:
            if not project or not getattr(style, "preview_path", None):
                return {"ok": False, "error": "自定义风格暂无参考图"}
            preview_path = (resolved.project_dir / style.preview_path).resolve()
            if not preview_path.is_relative_to(resolved.project_dir.resolve()) or not preview_path.exists():
                return {"ok": False, "error": "自定义风格参考图不存在"}
            return FileResponse(path=str(preview_path), media_type="image/*")
        return {"ok": False, "error": f"Style '{style_id}' not found"}

    preview_path = StyleService.PRESETS_DIR / f"{style_id}.png"
    if not preview_path.exists():
        return {"ok": False, "error": f"预设风格 '{style_id}' 暂无参考图"}

    return FileResponse(
        path=str(preview_path),
        media_type="image/png",
        filename=f"preview_{style_id}.png",
    )


@router.post("/styles")
async def create_style(body: dict, user: dict = Depends(get_api_user)):
    """创建自定义风格。"""
    from novelvideo.services.style_service import StyleService
    from novelvideo.models import StyleConfig

    style_id = body.get("id")
    project = body.get("project")
    if not style_id:
        return {"ok": False, "error": "Style id is required"}
    if not project:
        return {"ok": False, "error": "Project is required"}
    resolved = await resolve_project_scope(project, user, required_role="editor")

    # 检查是否与预设冲突
    if StyleService.get_preset(style_id):
        return {"ok": False, "error": f"Cannot override preset style '{style_id}'"}

    try:
        config_payload = dict(body.get("config", {}) or {})
        preview_path = body.get("preview_path")
        config_payload["id"] = style_id
        config_payload["name"] = body.get("name") or config_payload.get("name") or style_id
        config = StyleConfig(**config_payload)
        if preview_path:
            config.preview_path = StyleService.validate_style_preview_path(
                resolved.project_dir,
                style_id,
                str(preview_path),
            )
        else:
            config.preview_path = StyleService.find_style_preview(
                resolved.project_dir,
                style_id,
            )
        success = StyleService.save_custom_style(
            style_id,
            config,
            username=resolved.username,
            project=resolved.project_name,
            project_dir=resolved.project_dir,
            state_dir=resolved.state_dir,
        )
        if not success:
            return {"ok": False, "error": "保存自定义风格失败"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception:
        logger.exception("create custom style failed: %s", style_id)
        return {"ok": False, "error": "创建自定义风格失败"}

    return {"ok": True, "data": {"id": style_id, "message": "风格已创建"}}


@router.delete("/styles/{style_id}")
async def delete_style(
    style_id: str,
    project: str | None = Query(None, description="项目名"),
    user: dict = Depends(get_api_user),
):
    """删除自定义风格。"""
    from novelvideo.services.style_service import StyleService

    # 不允许删除预设
    if StyleService.get_preset(style_id):
        return {"ok": False, "error": "Cannot delete preset styles"}

    if not project:
        return {"ok": False, "error": "Project is required"}
    resolved = await resolve_project_scope(project, user, required_role="editor")
    success = StyleService.delete_custom_style(
        style_id,
        username=resolved.username,
        project=resolved.project_name,
        project_dir=resolved.project_dir,
        state_dir=resolved.state_dir,
    )
    if not success:
        return {"ok": False, "error": f"Custom style '{style_id}' not found"}

    return {"ok": True, "data": {"id": style_id, "message": "风格已删除"}}


@router.post("/styles/{style_id}/preview")
async def preview_style(
    style_id: str, body: StylePreviewRequest, user: dict = Depends(get_api_user)
):
    """使用指定风格生成预览图。"""
    from novelvideo.services.style_service import StyleService

    username = user["username"]
    project_name = body.project
    project_dir = None
    state_dir = None
    resolved = None
    if body.project:
        resolved = await resolve_project_scope(body.project, user, required_role="viewer")
        username = resolved.username
        project_name = resolved.project_name
        project_dir = resolved.project_dir
        state_dir = resolved.state_dir
    style = StyleService.get_style(
        style_id,
        username=username,
        project=project_name,
        project_dir=project_dir,
        state_dir=state_dir,
    )
    if style is None:
        return {"ok": False, "error": f"Style '{style_id}' not found"}

    from novelvideo.generators.image_generator import generate_character_reference_unified
    import shutil
    import tempfile

    # 使用临时目录生成预览图：读进内存后立刻在 finally 里回收临时目录。
    # 旧实现用 FileResponse 引用临时文件且从不 rmtree，成功/失败/空结果三条路径
    # 都会往磁盘留一个残留 PNG，反复预览即磁盘泄漏。
    tmp_dir = tempfile.mkdtemp(prefix="style_preview_")
    try:
        # [C3/A7] 请求路径出网身份绑定：组织用户经本端点触发的预览图生成需以其
        # 真实计费主体出网。无项目（预设预览）不绑、逐字节不变；非组织下
        # request_egress_scope yield None。scope 置于内层 try 之外：AuthzError 逃出
        # except Exception（否则被吞成 200 错误体），由 app 层 fail-closed，finally 仍回收临时目录。
        import contextlib

        from novelvideo.api.egress_binding import request_egress_scope

        if resolved is not None:
            egress_ctx = getattr(resolved, "ctx", None)
            egress_cm = request_egress_scope(
                requester_user_id=_requester_user_id(resolved, user),
                project_id=str(getattr(egress_ctx, "project_id", "") or ""),
                task_type="style_preview",
            )
        else:
            egress_cm = contextlib.nullcontext()
        async with egress_cm:
            try:
                paths = await generate_character_reference_unified(
                    character_name="preview",
                    appearance_prompt=body.prompt,
                    style=style_id,
                    model=body.model,
                    output_dir=tmp_dir,
                    project_dir=tmp_dir,
                    state_dir=state_dir or "",
                    count=1,
                )
            except Exception as e:
                logger.warning("style preview generation failed for %s: %s", style_id, e)
                return {"ok": False, "error": "预览图生成失败"}
            if not paths:
                return {"ok": False, "error": "No preview image generated"}
            data = Path(paths[0]).read_bytes()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    from novelvideo.freezone.style_gallery import sniff_content_type

    media_type = sniff_content_type(data)
    if media_type == "application/octet-stream":
        media_type = "image/png"
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="preview_{style_id}.png"'},
    )


@router.post("/projects/{project}/styles/analyze")
async def analyze_style(
    project: str,
    file: UploadFile = File(...),
    style_id: str = Form(""),
    user: dict = Depends(get_api_user),
):
    """上传参考图片，AI 分析并提取风格参数。"""
    resolved = await resolve_project_scope(project, user, required_role="editor")
    from novelvideo.services.style_service import StyleService
    from novelvideo.generators.style_analyzer import StyleAnalyzer
    from novelvideo.ports import get_usage_meter

    content = await file.read()
    if not content:
        return {"ok": False, "error": "No file uploaded"}

    mime_type = file.content_type or "image/jpeg"

    preview_token = None
    if style_id.strip():
        extension = Path(file.filename or "").suffix.lower() or ".png"
        preview_token = StyleService.stage_style_preview(
            resolved.project_dir,
            content,
            extension,
        )

    usage_meter = get_usage_meter()
    ctx = getattr(resolved, "ctx", None)
    project_id = str(getattr(ctx, "project_id", "") or "")
    usage_user_id = _requester_user_id(resolved, user)
    # [C3/A7] 请求路径出网身份绑定：组织用户经本端点触发的 Vision 风格分析必须以
    # 其真实计费主体出网，而非平台 ambient 身份垫付。非组织身份下
    # request_egress_scope 什么都不绑并 yield None，平台路径逐字节不变。scope 置于
    # try 之外——组织侧真实拒绝作为 AuthzError 逃出 except Exception（否则会被吞成
    # 200 错误体）→ 由 app 层 handler 渲染正确 4xx/5xx，fail-closed。
    from novelvideo.api.egress_binding import request_egress_scope

    async with request_egress_scope(
        requester_user_id=usage_user_id,
        project_id=project_id,
        task_type="style_analyze",
    ):
        try:
            usage_meter.set_llm_usage_context(
                usage_user_id,
                project_id=project_id,
                resource_kind="script",
            )
            analyzer = StyleAnalyzer()
            result = await analyzer.analyze(content, mime_type=mime_type)
        except Exception as e:
            logger.warning("style analysis failed for project %s: %s", project, e)
            return {"ok": False, "error": "风格分析失败"}
        finally:
            usage_meter.clear_llm_usage_context()

    data = dict(result)
    if preview_token:
        data["preview_token"] = preview_token
    return {"ok": True, "data": data}


@router.post("/projects/{project}/styles/preview-upload")
async def upload_style_preview(
    project: str,
    file: UploadFile = File(...),
    style_id: str = Form(...),
    user: dict = Depends(get_api_user),
):
    """立即保存自定义风格参考图，不等待 AI 风格分析。"""
    resolved = await resolve_project_scope(project, user, required_role="editor")
    from novelvideo.services.style_service import StyleService

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="No preview image uploaded")

    extension = Path(file.filename or "").suffix.lower() or ".png"
    allowed_mime_types = {
        ".png": {"image/png"},
        ".jpg": {"image/jpeg"},
        ".jpeg": {"image/jpeg"},
        ".webp": {"image/webp"},
        ".gif": {"image/gif"},
    }
    if (
        extension not in allowed_mime_types
        or (file.content_type or "").lower() not in allowed_mime_types[extension]
    ):
        raise HTTPException(
            status_code=415,
            detail="Unsupported style preview image type",
        )

    try:
        staged_token = StyleService.stage_style_preview(
            resolved.project_dir,
            content,
            extension,
        )
        preview_token = StyleService.finalize_style_preview(
            resolved.project_dir,
            style_id,
            staged_token,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except OSError as e:
        logger.exception("Failed to persist style preview for %s", style_id)
        raise HTTPException(status_code=500, detail="Failed to persist style preview") from e

    return {"ok": True, "data": {"preview_path": preview_token}}


# ---------------------------------------------------------------------------
# [aipdd fork delta] 风格图墙本地托管：设置→风格管理逐个上传封面，
# 落到状态卷里的持久目录，再由下面的公开端点同源提供。不依赖 OSS、传完即生效、
# 跨部署留存。style_id 一律以清单为 allowlist 校验，挡掉越权写盘与路径穿越。
# ---------------------------------------------------------------------------

_STYLE_GALLERY_MAX_BYTES = 10 * 1024 * 1024
_STYLE_GALLERY_ALLOWED = {
    ".png": {"image/png"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".webp": {"image/webp"},
    ".gif": {"image/gif"},
}


@router.get("/style-gallery/styles")
async def list_style_gallery(user: dict = Depends(require_owner)):
    """管理面板用：返回全部风格 + 是否已上传封面。仅超管。"""
    from novelvideo.freezone import style_gallery

    return {"ok": True, "data": style_gallery.list_gallery_styles()}


class StyleCoverGenerateRequest(BaseModel):
    """[aipdd fork delta] AI 生成封面的可选参数。

    model 可传图片模型目录（/style-gallery/image-models）里任一条目的
    catalogId / id / apiModel（与画布生成同一解析口径）；留空时沿用
    「设置→默认生成模型」的默认图片模型（历史行为）。
    """

    model: str | None = None


@router.get("/style-gallery/image-models")
async def list_style_gallery_image_models(user: dict = Depends(require_owner)):
    """[aipdd fork delta] 「AI 生成封面」可选的图片模型目录。仅超管。

    与 cover/generate 用同一作用域目录解析口径（scoped media model catalog）：
    选择器里能看到的模型，就是生成时可解析执行的模型。
    """
    from novelvideo.api.routes.freezone import _scoped_media_model_catalog

    catalog = await _scoped_media_model_catalog(
        "image",
        requester_user_id=_requester_user_id(None, user),
    )
    return {"ok": True, "data": catalog or []}


@router.post("/style-gallery/{style_id}/cover")
async def upload_style_gallery_cover(
    style_id: str,
    file: UploadFile = File(...),
    user: dict = Depends(require_owner),
):
    """为某条风格上传/替换封面图。仅超管；style_id 必须在清单里。"""
    from novelvideo.freezone import style_gallery

    if not style_gallery.is_known_style(style_id):
        raise HTTPException(status_code=404, detail=f"Style '{style_id}' not found")

    extension = Path(file.filename or "").suffix.lower()
    allowed = _STYLE_GALLERY_ALLOWED.get(extension)
    if allowed is None or (file.content_type or "").lower() not in allowed:
        raise HTTPException(status_code=415, detail="Unsupported style cover image type")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="No cover image uploaded")
    if len(content) > _STYLE_GALLERY_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Cover image too large (max 10MB)")

    try:
        style_gallery.save_cover(style_id, content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except OSError as e:
        logger.exception("Failed to persist style gallery cover for %s", style_id)
        raise HTTPException(status_code=500, detail="Failed to persist style cover") from e

    return {
        "ok": True,
        "data": {
            "id": style_id,
            "has_cover": True,
            "cover_url": (
                f"/api/v1/style-gallery/{style_id}/cover.webp"
                f"?v={style_gallery.cover_cache_token(style_id)}"
            ),
        },
    }


@router.delete("/style-gallery/{style_id}/cover")
async def delete_style_gallery_cover(
    style_id: str,
    user: dict = Depends(require_owner),
):
    """删除某条风格的本地封面，回落到图墙占位块。仅超管。"""
    from novelvideo.freezone import style_gallery

    if not style_gallery.is_known_style(style_id):
        raise HTTPException(status_code=404, detail=f"Style '{style_id}' not found")
    removed = style_gallery.delete_cover(style_id)
    return {"ok": True, "data": {"id": style_id, "removed": removed}}


@router.post("/style-gallery/{style_id}/cover/generate")
async def generate_style_gallery_cover(
    style_id: str,
    payload: StyleCoverGenerateRequest | None = None,
    user: dict = Depends(require_owner),
):
    """[aipdd fork delta] 用该风格自身的画风描述走画布同源出图路径同步生成封面,
    直接覆盖已有封面。仅超管;style_id 必须在清单里。

    画廊模板 id(如 period_idol)属于 freezone 体系,不能传给 StyleService 的
    style= 参数(那边只认预设/自定义风格 id,未知 id 会 KeyError),所以这里经
    run_freezone_gen 走与画布一致的 grid_generation 引擎。payload.model 可指定
    图片模型(见 /style-gallery/image-models),留空沿用默认图片模型。"""
    import shutil
    import tempfile

    from novelvideo.freezone import style_gallery

    if not style_gallery.is_known_style(style_id):
        raise HTTPException(status_code=404, detail=f"Style '{style_id}' not found")
    entry = style_gallery.get_style_entry(style_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Style '{style_id}' not found")
    prompt = style_gallery.cover_generation_prompt(style_id)
    if not prompt:
        raise HTTPException(status_code=400, detail=f"Style '{style_id}' has no prompt to generate")

    # [aipdd fork delta] 指定模型时与画布生成同一解析口径：经媒体模型目录校验
    # 并折算执行身份（provider + apiModel）与请求参数；留空沿用默认图片模型。
    requested_model = (payload.model if payload is not None else None) or None
    request_schema: dict[str, Any] = {}
    model_params: dict[str, Any] = {}
    execution_provider: str | None = None
    execution_model: str | None = None
    if requested_model:
        from novelvideo.api.routes.freezone import (
            _catalog_image_execution_selection,
            _resolve_catalog_request,
        )

        request_schema, model_params, catalog_entry = await _resolve_catalog_request(
            "image",
            requested_model,
            None,
            requester_user_id=_requester_user_id(None, user),
        )
        execution_provider, execution_model = _catalog_image_execution_selection(
            catalog_entry,
            requested_provider=None,
            requested_model=requested_model,
        )

    from novelvideo.freezone.jobs import run_freezone_gen

    tmp_dir = Path(tempfile.mkdtemp(prefix="style_cover_gen_"))
    try:
        generated = await run_freezone_gen(
            project_dir=tmp_dir,
            job_id="style_gallery_cover",
            prompt=prompt,
            aspect_ratio="16:9",
            image_size="2K",
            provider=execution_provider,
            model=execution_model,
            model_params=model_params or None,
            request_schema=request_schema or None,
        )
        generated = Path(generated)
        if not generated.is_file():
            raise HTTPException(status_code=502, detail="封面生成失败:图片不存在")
        data = generated.read_bytes()
        if not data:
            raise HTTPException(status_code=502, detail="封面生成失败:图片为空")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一回干净错误给管理面板(含余额不足/出网受限)
        logger.warning("[style-gallery] AI 生成封面失败 %s: %s", style_id, exc)
        raise HTTPException(status_code=502, detail=f"封面生成失败:{exc}") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    try:
        style_gallery.save_cover(style_id, data)
    except (ValueError, OSError) as exc:
        logger.exception("Failed to persist generated style cover for %s", style_id)
        raise HTTPException(status_code=500, detail="Failed to persist generated cover") from exc

    return {
        "ok": True,
        "data": {
            "id": style_id,
            "has_cover": True,
            "cover_url": (
                f"/api/v1/style-gallery/{style_id}/{style_gallery.COVER_BASENAME}"
                f"?v={style_gallery.cover_cache_token(style_id)}"
            ),
        },
    }


@router.get("/style-gallery/{style_id}/{filename}", include_in_schema=False)
async def get_style_gallery_cover(style_id: str, filename: str):
    """公开提供封面：画布图墙的 <img> 不带鉴权头，和 /static 一样必须免鉴权。

    文件名固定 cover.webp（与清单 cover 相对路径的 basename 对齐），真实 content-type
    由字节魔数决定，所以传进来的 png/jpg 也能正常显示。
    """
    from novelvideo.freezone import style_gallery

    if filename != style_gallery.COVER_BASENAME:
        raise HTTPException(status_code=404, detail="Not found")
    path = style_gallery.cover_path(style_id)
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail="Cover not found")
    try:
        data = path.read_bytes()
    except OSError as e:
        raise HTTPException(status_code=404, detail="Cover not found") from e
    return Response(
        content=data,
        media_type=style_gallery.sniff_content_type(data),
        headers={"Cache-Control": "public, max-age=300"},
    )
