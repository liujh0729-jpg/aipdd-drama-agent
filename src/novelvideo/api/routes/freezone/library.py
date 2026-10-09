"""freezone.library 子模块（自 api/routes/freezone.py 拆分）。"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

import httpx
from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import Form
from fastapi import HTTPException
from fastapi import UploadFile
from fastapi.responses import StreamingResponse

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone as _freezone
from novelvideo.api.schemas import FreezoneAssetLibraryFolderPatchRequest
from novelvideo.api.schemas import FreezoneAssetLibraryFolderRequest
from novelvideo.api.schemas import FreezoneAssetLibraryItemPatchRequest
from novelvideo.api.schemas import FreezoneVideoCharacterLibraryItemRequest
from novelvideo.freezone.video_node import DRAFT_FOLDER_KEY
from novelvideo.freezone.video_node import MAINLINE_FOLDER_KEY
from novelvideo.freezone.video_node import STYLE_COVER_ITEM_PREFIX
from novelvideo.freezone.video_node import VECTCUT_DRAFT_ITEM_ID_PREFIX
from novelvideo.freezone.video_node import add_video_character_folder
from novelvideo.freezone.video_node import add_video_character_library_item
from novelvideo.freezone.video_node import compose_library_with_style_covers
from novelvideo.freezone.video_node import delete_video_character_folder
from novelvideo.freezone.video_node import delete_video_character_library_item
from novelvideo.freezone.video_node import library_folder_keys
from novelvideo.freezone.video_node import load_video_character_folders
from novelvideo.freezone.video_node import load_video_character_library
from novelvideo.freezone.video_node import register_vectcut_draft_item
from novelvideo.freezone.video_node import rename_video_character_library_item
from novelvideo.freezone.video_node import sync_mainline_assets_into_library
from novelvideo.freezone.video_node import update_video_character_folder
from novelvideo.model_gateway_settings import get_vectcut_draft_dir
from novelvideo.utils.path_resolver import canonical_identity_composite_path
from novelvideo.utils.path_resolver import canonical_identity_face_mat_path
from novelvideo.utils.path_resolver import canonical_identity_path
from novelvideo.utils.path_resolver import canonical_portrait_path
from novelvideo.utils.path_resolver import canonical_prop_reference_path
from novelvideo.utils.path_resolver import canonical_scene_master_path

from ._common import TAG_FREEZONE_VIDEO

router = APIRouter()


@router.get("/projects/{project}/freezone/video/character-library", tags=[TAG_FREEZONE_VIDEO])
async def freezone_video_character_library(
    project: str,
    user: dict = Depends(get_api_user),
):
    """视频处理：获取文生视频角色素材库。

    读取时把设置→风格管理里已配封面的风格投影成「风格」栏目的只读虚拟条目，
    虚拟条目不落库文件，改名/删除会被拒（400），请在设置里维护。
    """
    _ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user, required_role="viewer"
    )
    items = load_video_character_library(project_dir)
    return {"ok": True, "data": compose_library_with_style_covers(items)}


@router.post("/projects/{project}/freezone/video/character-library", tags=[TAG_FREEZONE_VIDEO])
async def freezone_add_video_character_library_item(
    project: str,
    body: FreezoneVideoCharacterLibraryItemRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：把上传好的素材登记到资产库（图片/视频/音频）。"""
    ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )

    if not body.name.strip():
        raise HTTPException(400, "name is required")

    def _require_local(url: str, label: str) -> None:
        try:
            path = _freezone.resolve_static_url_to_path(url, project_dir)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not path.exists():
            raise HTTPException(404, f"{label} not found: {path}")

    if body.media == "video":
        if not body.video_url:
            raise HTTPException(400, "video_url is required when media=video")
        _require_local(body.video_url, "video")
    elif body.media == "audio":
        if not body.audio_url:
            raise HTTPException(400, "audio_url is required when media=audio")
        _require_local(body.audio_url, "audio")
    else:
        if not body.image_urls:
            raise HTTPException(400, "image_urls is required (non-empty)")
        for url in body.image_urls:
            _require_local(url, "image")

    # 保存位置必须是已存在的文件夹；主线是同步产物、剪映草稿只收 VectCut 产物，
    # 都不接受上传。
    if body.folder:
        if body.folder in (MAINLINE_FOLDER_KEY, DRAFT_FOLDER_KEY):
            raise HTTPException(400, f"cannot upload into the {body.folder} folder")
        if body.folder not in library_folder_keys(project_dir):
            raise HTTPException(404, f"folder not found: {body.folder}")

    item = add_video_character_library_item(
        project_dir,
        name=body.name,
        media=body.media,
        image_urls=body.image_urls,
        video_url=body.video_url,
        audio_url=body.audio_url,
        category=body.category,
        folder=body.folder,
        scene=body.scene,
    )
    return {"ok": True, "data": item}


@router.get(
    "/projects/{project}/freezone/video/asset-library/folders",
    tags=[TAG_FREEZONE_VIDEO],
)
async def freezone_asset_library_folders(
    project: str,
    user: dict = Depends(get_api_user),
):
    """视频处理：列出用户自建的资产库文件夹（系统文件夹由前端按保留 key 生成）。"""
    _ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user, required_role="viewer"
    )
    return {"ok": True, "data": load_video_character_folders(project_dir)}


@router.post(
    "/projects/{project}/freezone/video/asset-library/folders",
    tags=[TAG_FREEZONE_VIDEO],
)
async def freezone_add_asset_library_folder(
    project: str,
    body: FreezoneAssetLibraryFolderRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：新建一个资产库文件夹。"""
    _ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    try:
        folder = add_video_character_folder(project_dir, name=body.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "data": folder}


@router.patch(
    "/projects/{project}/freezone/video/asset-library/folders/{folder_id}",
    tags=[TAG_FREEZONE_VIDEO],
)
async def freezone_update_asset_library_folder(
    project: str,
    folder_id: str,
    body: FreezoneAssetLibraryFolderPatchRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：给资产库文件夹改名或换封面。系统文件夹没有实体记录，一律 404。"""
    _ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    if body.cover:
        # 封面只能是本项目 static 下的素材。这个字段会被所有打开资产库的协作者当
        # <img src> 渲染，放行外链等于让别人的浏览器替你去访问第三方地址（referrer
        # 和 IP 都带过去）。和录入素材那条路由的 _require_local 同一套要求。
        try:
            cover_path = _freezone.resolve_static_url_to_path(body.cover, project_dir)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not cover_path.exists():
            # 只回传进来的 URL：cover_path 是绝对路径，带用户名和落盘目录。
            raise HTTPException(404, f"cover not found: {body.cover}")
    try:
        folder = update_video_character_folder(
            project_dir, folder_id, name=body.name, cover=body.cover
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if folder is None:
        raise HTTPException(404, f"folder not found: {folder_id}")
    return {"ok": True, "data": folder}


@router.delete(
    "/projects/{project}/freezone/video/asset-library/folders/{folder_id}",
    tags=[TAG_FREEZONE_VIDEO],
)
async def freezone_delete_asset_library_folder(
    project: str,
    folder_id: str,
    user: dict = Depends(get_api_user),
):
    """视频处理：删掉一个自建文件夹，柜内素材条目一并从资产库移除。

    只删索引，磁盘上的素材文件保留——画布节点可能还引用着同一个 URL，真删文件会
    造成裂图。孤儿文件归项目级清理负责，不在这条路由的职责里。
    """
    _ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    removed = delete_video_character_folder(project_dir, folder_id)
    if removed is None:
        raise HTTPException(404, f"folder not found: {folder_id}")
    return {"ok": True, "data": {"deleted_items": removed}}


@router.post(
    "/projects/{project}/freezone/video/asset-library/sync-from-mainline",
    tags=[TAG_FREEZONE_VIDEO],
)
async def freezone_sync_asset_library_from_mainline(
    project: str,
    user: dict = Depends(get_api_user),
):
    """视频处理：把主线的人物/场景/道具参考图与人物语音幂等同步进资产库。

    人物除了角色 Portrait，还会按身份同步造型三视图 / 五官垫图 / 「三视图与五官
    垫图」拼接图（文件存在才同步）。

    走稳定合成 id（``mainline:<kind>:<name>``），重复同步只更新 URL、不产生重复。

    [aipdd fork delta] 返回时同样投影「风格」封面虚拟条目——与 GET 读取端一致。
    否则前端“先读后同步”的时序里，同步结果会用未投影列表覆盖刚显示的投影封面，
    风格封面就「一闪而过」（2026-09-28 修复）。
    """
    ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    async with _freezone.freezone_store_for_context_scope(ctx) as store:
        characters = list(store.get_all_characters())
        scenes = await store.list_scenes()
        props = await store.list_props()

    def _static_url(abs_path: Path) -> str:
        if not abs_path.exists():
            return ""
        try:
            rel = abs_path.relative_to(project_dir).as_posix()
        except ValueError:
            return ""
        return _freezone.make_static_url_for_context(ctx, rel, local_path=abs_path)

    assets: list[dict[str, Any]] = []

    # 人物：肖像 → 图片；参考语音 → 音频
    for character in characters:
        name = getattr(character, "name", "") or ""
        if not name:
            continue
        portrait_url = _static_url(canonical_portrait_path(project_dir, name))
        if portrait_url:
            assets.append(
                {
                    "id": f"mainline:character:{name}",
                    "name": name,
                    "media": "image",
                    "source": "character",
                    "url": portrait_url,
                }
            )
        # [aipdd fork delta] 身份级一致性三件套（造型三视图 / 五官垫图 /
        # 「三视图与五官垫图」拼接图）也要进资产库：画布里做参考时，光有角色
        # Portrait 给不出头肩比、服装与多角度脸，必须能把这三张拖进来。
        prefix = f"{name}_"
        for ident in getattr(character, "identities", []) or []:
            raw_identity = str(getattr(ident, "identity_name", "") or "")
            if not raw_identity:
                continue
            display = (
                raw_identity[len(prefix) :]
                if raw_identity.startswith(prefix)
                else raw_identity
            )
            for slot, label, path in (
                (
                    "identity_image",
                    "造型三视图",
                    canonical_identity_path(project_dir, name, raw_identity),
                ),
                (
                    "face_mat",
                    "五官垫图",
                    canonical_identity_face_mat_path(project_dir, name, raw_identity),
                ),
                (
                    "composite",
                    "三视图与五官垫图",
                    canonical_identity_composite_path(project_dir, name, raw_identity),
                ),
            ):
                slot_url = _static_url(path)
                if not slot_url:
                    continue
                assets.append(
                    {
                        "id": f"mainline:{slot}:{name}:{display}",
                        "name": f"{name}·{display} {label}",
                        "media": "image",
                        "source": "character",
                        "url": slot_url,
                    }
                )
        # 走全站统一的三级声线级联（身份覆盖 → 年龄段预设 → 角色默认），并让
        # resolve_character_voice 负责把项目相对/绝对路径解析成真实存在的绝对路径，
        # 避免这里手拼 project_dir / rel 时对绝对路径拼错、静默丢音。
        voice = _freezone.resolve_character_voice(project_dir=project_dir, character=character)
        if voice.audio_path is not None:
            voice_url = _static_url(voice.audio_path)
            if voice_url:
                assets.append(
                    {
                        "id": f"mainline:voice:{name}",
                        "name": name,
                        "media": "audio",
                        "source": "character",
                        "url": voice_url,
                    }
                )

    # 场景：master → 图片
    for scene in scenes:
        name = getattr(scene, "name", "") or ""
        if not name:
            continue
        master_url = _static_url(canonical_scene_master_path(project_dir, name))
        if master_url:
            assets.append(
                {
                    "id": f"mainline:scene:{name}",
                    "name": name,
                    "media": "image",
                    "source": "scene",
                    "url": master_url,
                }
            )

    # 道具：reference → 图片
    for prop in props:
        name = getattr(prop, "name", "") or ""
        if not name:
            continue
        ref_url = _static_url(canonical_prop_reference_path(project_dir, name))
        if ref_url:
            assets.append(
                {
                    "id": f"mainline:prop:{name}",
                    "name": name,
                    "media": "image",
                    "source": "prop",
                    "url": ref_url,
                }
            )

    # [aipdd fork delta] 分镜草图也作为可复用资产进资产库（类目「草图」）：逐集逐
    # beat 的草图落盘在 <output>/sketches/epNNN/beat_NN.png，存在即幂等同步，稳定 id
    # ``mainline:sketch:<ep>:<beat>`` 保证重复同步只更新 URL、不产生重复。走非 upload 来
    # 源，跟人物/场景/道具一样归到「主线」文件夹、带「草图」类目标签。
    sketches_root = project_dir / "sketches"
    if sketches_root.exists():
        for sketch_path in sorted(sketches_root.glob("*/beat_*.png")):
            sketch_url = _static_url(sketch_path)
            if not sketch_url:
                continue
            ep_dir = sketch_path.parent.name  # e.g. "ep001"
            beat_stem = sketch_path.stem  # e.g. "beat_03"
            try:
                episode_no = int(ep_dir[2:]) if ep_dir.startswith("ep") else -1
                beat_no = int(beat_stem[len("beat_") :])
            except ValueError:
                continue
            if episode_no < 0:
                continue
            assets.append(
                {
                    "id": f"mainline:sketch:{episode_no}:{beat_no}",
                    "name": f"草图 · E{episode_no} B{beat_no}",
                    "media": "image",
                    "source": "sketch",
                    "category": "sketch",
                    "url": sketch_url,
                }
            )

    library = sync_mainline_assets_into_library(project_dir, assets=assets)
    return {
        "ok": True,
        "data": compose_library_with_style_covers(library),
        "synced": len(assets),
    }


@router.patch(
    "/projects/{project}/freezone/video/character-library/{item_id}", tags=[TAG_FREEZONE_VIDEO]
)
async def freezone_rename_video_character_library_item(
    project: str,
    item_id: str,
    body: FreezoneAssetLibraryItemPatchRequest,
    user: dict = Depends(get_api_user),
):
    """视频处理：给资产库条目改名。

    只改展示名，素材地址不动——画布上已经引用该素材的节点照旧可用。主线同步来的
    条目也能改，但下次「从主线同步」会按主线名字覆盖回去，前端据此只对本地上传的
    条目开放这个入口。
    """
    _ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    if item_id.startswith(STYLE_COVER_ITEM_PREFIX):
        raise HTTPException(
            400,
            "style cover items are read-only; manage them in Settings → Style Gallery",
        )
    try:
        item = rename_video_character_library_item(project_dir, item_id, name=body.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if item is None:
        raise HTTPException(404, f"video character library item not found: {item_id}")
    return {"ok": True, "data": item}


@router.delete(
    "/projects/{project}/freezone/video/character-library/{item_id}", tags=[TAG_FREEZONE_VIDEO]
)
async def freezone_delete_video_character_library_item(
    project: str,
    item_id: str,
    user: dict = Depends(get_api_user),
):
    """视频处理：删除角色素材库条目。"""
    _ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    if item_id.startswith(STYLE_COVER_ITEM_PREFIX):
        raise HTTPException(
            400,
            "style cover items are read-only; manage them in Settings → Style Gallery",
        )
    deleted = delete_video_character_library_item(project_dir, item_id)
    if not deleted:
        raise HTTPException(404, f"video character library item not found: {item_id}")
    return {"ok": True, "data": {"id": item_id, "deleted": True}}


# ---------------------------------------------------------------------------
# [aipdd fork delta] 剪映草稿（VectCut 产物）代理：下载 / 上传 / 目录同步
#
# 草稿文件落在 VectCut 服务自己的草稿目录（VECTCUT_DRAFT_DIR 控制，设置 → 剪映
# 草稿里可配「剪映草稿目录」覆盖它），api 进程看不见这份目录，所以下载 / 上传 /
# 同步都经 VectCut 的 /aipdd/drafts* 端点转发。资产库「剪映草稿」条目的下载按钮
# 打到下载路由；上传文件夹与「同步草稿目录」按钮打到 upload / sync 路由。
# draft_id 白名单与路径校验在 vectcut 侧还会再做一遍。
# ---------------------------------------------------------------------------

# draft_id 白名单：VectCut 生成的 id 形如 ``dfd_xxxx``，资产库上传生成的形如
# ``up_xxxxxx``。点号在白名单内，但整段纯点（``.`` / ``..``）会把拼出的上游
# URL 交给路径归一化改写，用前瞻要求至少一个字母数字，非法 id 在代理层就拦下。
VECTCUT_DRAFT_ID_RE = re.compile(r"^(?=.*[A-Za-z0-9])[A-Za-z0-9._-]{1,64}$")

# [aipdd fork delta req41] 不兜底 ``http://127.0.0.1:9001``：VectCut 是独立进程/独立容器，
# 它的地址属于部署形态数据（compose 会注 VECTCUT_BASE_URL=http://vectcut:9001）。
# 旧默认值在容器里对着的是 api 容器自己，等于猜“VectCut 一定跟本机同机”，
# 局域网 / 多容器 / 反代部署下会拼出一个永远连不上的上游。
VECTCUT_NOT_CONFIGURED = (
    "未配置 VectCut 服务地址：请设 VECTCUT_BASE_URL（compose 部署已注入 "
    "http://vectcut:9001，宿主机直跑则填你自己的地址）"
)


def _vectcut_base_url() -> str:
    """VectCut 服务地址（只认配置，没配置就报可执行错误，不猜本机）。"""

    value = (os.environ.get("VECTCUT_BASE_URL") or "").strip().rstrip("/")
    if not value:
        raise HTTPException(400, VECTCUT_NOT_CONFIGURED)
    return value


def _vectcut_draft_params() -> dict[str, str] | None:
    """设置 → 剪映草稿目录 的透传参数；未配置时返回 None（用服务部署默认）。"""
    draft_dir = get_vectcut_draft_dir()
    return {"draft_folder": draft_dir} if draft_dir else None


def _vectcut_headers() -> dict[str, str]:
    """O19 服务间共享密钥透传：与 vectcut 侧同名变量 ``VECTCUT_INTERNAL_TOKEN``。

    未设时返回空 dict（请求不带该头，vectcut 也不校验）；设了则附带，供
    vectcut 侧 ``hmac.compare_digest`` 校验。与 dramaclaw_tools 调用侧行为对齐。
    """
    token = (os.environ.get("VECTCUT_INTERNAL_TOKEN") or "").strip()
    return {"X-VectCut-Token": token} if token else {}


@router.get(
    "/projects/{project}/freezone/video/drafts/{draft_id}/download",
    tags=[TAG_FREEZONE_VIDEO],
)
async def freezone_download_vectcut_draft(
    project: str,
    draft_id: str,
    user: dict = Depends(get_api_user),
):
    """视频处理：下载一份剪映草稿包（zip），转发自 VectCut 服务。"""
    if not VECTCUT_DRAFT_ID_RE.match(draft_id):
        raise HTTPException(400, f"invalid draft_id: {draft_id!r}")
    _ctx, _username, _project_name, _project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user, required_role="viewer"
    )
    upstream_url = f"{_vectcut_base_url()}/aipdd/drafts/{draft_id}/download"
    client = httpx.AsyncClient(timeout=httpx.Timeout(300.0))
    try:
        upstream = await client.send(
            client.build_request(
                "GET", upstream_url, params=_vectcut_draft_params(), headers=_vectcut_headers()
            ),
            stream=True,
        )
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(502, f"cannot reach the VectCut service: {exc}") from exc
    if upstream.status_code >= 400:
        detail = (await upstream.aread()).decode("utf-8", errors="replace")
        await upstream.aclose()
        await client.aclose()
        status = 404 if upstream.status_code == 404 else 502
        raise HTTPException(
            status, detail or f"VectCut responded HTTP {upstream.status_code}"
        )

    async def _stream() -> Any:
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        _stream(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{draft_id}.zip"'},
    )


@router.post(
    "/projects/{project}/freezone/video/drafts/upload",
    tags=[TAG_FREEZONE_VIDEO],
)
async def freezone_upload_vectcut_draft(
    project: str,
    name: Annotated[str, Form()],
    files: Annotated[list[UploadFile], File()],
    relative_paths: Annotated[list[str], Form()] = [],
    user: dict = Depends(get_api_user),
):
    """视频处理：上传一份剪映草稿文件夹（multipart），转发给 VectCut 落盘。

    一个草稿 = 一个文件夹：浏览器把文件夹里的文件平铺传入（``files``），并用
    同序的 ``relative_paths`` 保留原目录结构；api 生成安全 draft_id（``up_`` 前缀，
    符合白名单）后转发落盘，成功后把草稿登记进资产库「剪映草稿」。
    """
    _ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    clean_name = name.strip()
    if not clean_name:
        raise HTTPException(400, "name is required")
    if not files:
        raise HTTPException(400, "files is required")
    if len(relative_paths) != len(files):
        raise HTTPException(400, "relative_paths count must match files")
    # 上传目录名走白名单字符（原文件夹名可能是中文，只作条目显示名）。
    draft_id = f"up_{uuid4().hex[:12]}"
    form: dict[str, Any] = {"draft_id": draft_id, "relative_paths": list(relative_paths)}
    params = _vectcut_draft_params()
    if params:
        form["draft_folder"] = params["draft_folder"]
    payload_files = []
    for upload, rel in zip(files, relative_paths):
        filename = rel.replace("\\", "/").rsplit("/", 1)[-1] or "file"
        payload_files.append(
            (
                "files",
                (
                    filename,
                    upload.file,
                    upload.content_type or "application/octet-stream",
                ),
            )
        )
    client = httpx.AsyncClient(timeout=httpx.Timeout(900.0))
    try:
        upstream = await client.post(
            f"{_vectcut_base_url()}/aipdd/drafts/upload",
            data=form,
            files=payload_files,
            headers=_vectcut_headers(),
        )
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(502, f"cannot reach the VectCut service: {exc}") from exc
    await client.aclose()
    try:
        upstream_payload = upstream.json()
    except ValueError:
        upstream_payload = {}
    if upstream.status_code >= 400 or not upstream_payload.get("success"):
        message = str(upstream_payload.get("error") or "").strip()
        status = 400 if upstream.status_code == 400 else 502
        raise HTTPException(
            status,
            message or f"VectCut responded HTTP {upstream.status_code}",
        )
    item = register_vectcut_draft_item(
        project_dir, draft_id=draft_id, name=clean_name
    )
    return {"ok": True, "data": item}


@router.post(
    "/projects/{project}/freezone/video/drafts/sync",
    tags=[TAG_FREEZONE_VIDEO],
)
async def freezone_sync_vectcut_drafts(
    project: str,
    user: dict = Depends(get_api_user),
):
    """视频处理：把草稿目录里尚未登记进资产库的草稿文件夹批量补登记。

    目录是真源：VectCut 直接产物与上传的草稿都落在同一目录；已登记的条目不动
    （保留用户改过的显示名），目录里消失了但资产库尚有登记条目的也不删——
    「同步」只做补登记，不做清理。
    """
    _ctx, _username, _project_name, project_dir, _output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    client = httpx.AsyncClient(timeout=httpx.Timeout(120.0))
    try:
        upstream = await client.get(
            f"{_vectcut_base_url()}/aipdd/drafts",
            params=_vectcut_draft_params(),
            headers=_vectcut_headers(),
        )
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(502, f"cannot reach the VectCut service: {exc}") from exc
    await client.aclose()
    if upstream.status_code >= 400:
        detail = upstream.text
        raise HTTPException(
            502, detail or f"VectCut responded HTTP {upstream.status_code}"
        )
    try:
        drafts = upstream.json().get("drafts") or []
    except ValueError:
        drafts = []
    known = {
        str(item.get("id") or "")
        for item in load_video_character_library(project_dir)
    }
    added: list[dict[str, Any]] = []
    for draft in drafts:
        draft_id = str((draft or {}).get("draft_id") or "").strip()
        if not draft_id or not VECTCUT_DRAFT_ID_RE.match(draft_id):
            continue
        item_id = f"{VECTCUT_DRAFT_ITEM_ID_PREFIX}{draft_id}"
        if item_id in known:
            continue
        item = register_vectcut_draft_item(
            project_dir, draft_id=draft_id, name=draft_id
        )
        added.append(item)
        known.add(item_id)
    return {
        "ok": True,
        "data": {"added": len(added), "total": len(drafts), "items": added},
    }
