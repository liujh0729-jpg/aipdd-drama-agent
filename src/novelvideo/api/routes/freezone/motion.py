"""freezone.motion 子模块：动效大片图册目录、逐镜渲染、拍点预检与剪映草稿交付。

图册把「别人怎么做出这支片子」变成可浏览、可取用的数据：按分类分组看案例，
点一条拿走它的完整提示词（案例自身的分镜规格 + 本系统渲染契约）。契约只在
清单里存一份，这里负责拼装，于是图册、「存入提示词库」和渲染任务拿到的是
同一段文字。图册接口是只读的，角色门槛与画风图册一致（viewer）；渲染与草稿
交付是任务提交接口，按项目成员权限走。
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone as _freezone
from novelvideo.api.schemas import FreezoneMotionDraftRequest
from novelvideo.api.schemas import FreezoneMotionRenderRequest
from novelvideo.api.schemas import FreezoneMotionStillsRequest
from novelvideo.freezone import motion_media
from novelvideo.freezone.motion_draft import MOTION_RENDER_TASK_TYPE
from novelvideo.freezone.motion_draft import scan_motion_renders
from novelvideo.freezone.motion_draft import template_dir
from novelvideo.freezone.motion_render import MAX_PAGE_CHARS, MotionRenderError, render_preflight
from novelvideo.freezone.motion_render import stills_preflight
from novelvideo.freezone.motion_styles import MotionStyleManifestError
from novelvideo.freezone.motion_templates import MotionManifestError
from novelvideo.freezone.route_helpers import build_freezone_motion_prompt as _build_motion_prompt
from novelvideo.freezone.route_helpers import (
    build_freezone_motion_style_prompt as _build_motion_style_prompt,
)
from novelvideo.freezone.route_helpers import get_freezone_motion_asset_base as _get_motion_asset_base
from novelvideo.freezone.route_helpers import get_freezone_motion_manifest as _get_motion_manifest
from novelvideo.freezone.route_helpers import get_freezone_motion_manifest_version as _get_motion_manifest_version
from novelvideo.freezone.route_helpers import (
    get_freezone_motion_style_manifest as _get_motion_style_manifest,
)

from ._common import TAG_FREEZONE_IMAGE, TAG_FREEZONE_VIDEO

router = APIRouter()


def _manifest_unavailable(what: str, exc: Exception) -> HTTPException:
    """清单本身读不出来 —— 与「条目不存在」是两件事，装机形态缺文件时得说清楚。"""
    return HTTPException(409, f"{what}清单不可用（随包文件缺失或结构不合法）：{exc}")


def _check_page_source(html: str, case_id: str, style_id: str) -> None:
    """页面来源只能有一条：贴好的源码、按案例生成、按风格生成。

    两个 id 同时给要当场拒掉 —— 两套规格串成一条提示词不会「取两家之长」，只会让
    模型在互相冲突的色板与禁则之间取平均，出来的东西两边都不像。
    """
    if case_id and style_id:
        raise HTTPException(
            400, "案例与风格只能选一个：案例给分镜结构，风格给画面观感，不要同时给"
        )
    if not html.strip() and not case_id and not style_id:
        raise HTTPException(
            400,
            "MG 页面内容为空：请粘贴页面源码，或选择案例/风格并给出本镜头文案让模型生成",
        )
    # 入队前就挡一次超大正文：任务 envelope 会落盘，几 MB 的页面排进队列只会白等一轮。
    if len(html) > MAX_PAGE_CHARS:
        raise HTTPException(
            400,
            f"MG 页面过大（{len(html)} 字符，上限 {MAX_PAGE_CHARS}）："
            "渲染契约要求单文件自包含，请精简页面后重试",
        )


@router.get("/projects/{project}/freezone/motion/cases", tags=[TAG_FREEZONE_IMAGE])
async def freezone_motion_cases(
    project: str,
    user: dict = Depends(get_api_user),
):
    """动效大片图册清单：案例、方法卡、分类顺序、渲染契约与素材前缀。

    `data` 与画风图册一样保持**裸数组**（案例列表），其余口径走兄弟键：
    老客户端只读 `data`，把它换成对象会直接崩。
    """
    await _freezone._resolve_freezone_project(project, user, required_role="viewer")
    try:
        manifest = _get_motion_manifest()
        version = _get_motion_manifest_version()
    except (RuntimeError, MotionManifestError) as exc:
        raise _manifest_unavailable("动效大片", exc) from exc
    cases = [
        {
            **case,
            "poster_ready": motion_media.has_poster(case["id"]),
            "preview_ready": motion_media.has_preview(case["id"]),
            "preview_version": motion_media.preview_cache_token(case["id"]),
            "video_ready": motion_media.video_ready(case["id"]),
        }
        for case in manifest["cases"]
    ]
    return {
        "ok": True,
        "data": cases,
        "categories": manifest["categories"],
        "methods": manifest["methods"],
        "model_hint": manifest["model_hint"],
        "render_contract": manifest["render_contract"],
        "source": manifest["source"],
        "asset_base": _get_motion_asset_base(),
        "videos_available": motion_media.videos_available(),
        "version": version,
    }


@router.get(
    "/projects/{project}/freezone/motion/cases/{case_id}/prompt",
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_motion_case_prompt(
    project: str,
    case_id: str,
    user: dict = Depends(get_api_user),
):
    """取一条案例的完整提示词（分镜规格 + 渲染契约），供复制或填入节点。

    文字取的是提示词库里 `motion.<case_id>` 的生效值：超管在「设置→提示词库」
    改过这条，这里就直接跟着变，图册与渲染任务拿到的是同一份。
    """
    await _freezone._resolve_freezone_project(project, user, required_role="viewer")
    try:
        prompt = _build_motion_prompt(case_id)
    except KeyError as exc:
        raise HTTPException(404, f"未知的动效大片案例: {case_id}") from exc
    except (MotionManifestError, RuntimeError) as exc:
        raise _manifest_unavailable("动效大片", exc) from exc
    return {"ok": True, "data": {"case_id": case_id, "prompt": prompt}}


@router.get("/projects/{project}/freezone/motion/styles", tags=[TAG_FREEZONE_IMAGE])
async def freezone_motion_styles(
    project: str,
    user: dict = Depends(get_api_user),
):
    """画风图册清单：66 条风格（51 支画风 + 15 支 MG 动态设计）与分类顺序。

    与案例图册的分工不同：案例回答「这片子怎么排」，风格回答「想要这种观感，画布该
    怎么画」。所以这里没有海报与原片字段 —— 卡片上的样张由前端按每条的 `palette`
    用代码画，上游样片一律不随包（名录只是规格出处）。
    """
    await _freezone._resolve_freezone_project(project, user, required_role="viewer")
    try:
        manifest = _get_motion_style_manifest()
    except (RuntimeError, MotionStyleManifestError) as exc:
        raise _manifest_unavailable("画风图册", exc) from exc
    return {
        "ok": True,
        "data": manifest["styles"],
        "categories": manifest["categories"],
        "source": manifest["source"],
        "version": manifest["version"],
    }


@router.get(
    "/projects/{project}/freezone/motion/styles/{style_id}/prompt",
    tags=[TAG_FREEZONE_IMAGE],
)
async def freezone_motion_style_prompt(
    project: str,
    style_id: str,
    user: dict = Depends(get_api_user),
):
    """取一条风格的完整提示词（风格规格 + 渲染契约），供复制或填入节点。

    与案例同一口径：文字取提示词库 `motion-style.<style_id>` 的生效值，超管改过这条
    就跟着变；契约只在案例清单存一份，由后端拼装，图册与渲染任务拿到同一段文字。
    """
    await _freezone._resolve_freezone_project(project, user, required_role="viewer")
    try:
        prompt = _build_motion_style_prompt(style_id)
    except KeyError as exc:
        raise HTTPException(404, f"未知的画风条目: {style_id}") from exc
    except (MotionManifestError, MotionStyleManifestError, RuntimeError) as exc:
        raise _manifest_unavailable("画风图册", exc) from exc
    return {"ok": True, "data": {"style_id": style_id, "prompt": prompt}}


@router.get("/projects/{project}/freezone/motion/renders", tags=[TAG_FREEZONE_VIDEO])
async def freezone_motion_renders(
    project: str,
    episode: int = 0,
    user: dict = Depends(get_api_user),
):
    """本项目已渲染的 MG 成片清单（供「导出剪映草稿」挑片段、按镜归位）。

    事实来源是磁盘而不是任务记录：``freezone/_outputs/freezone_motion_render/`` 里的
    每个 mp4 配一份同名 ``*.motion.json``（画幅/时长/拍点/集号镜号）。任务记录会过期、
    资产库条目名会被改名，只有这份目录能回答「这一镜的成片是哪一条」。
    ``episode`` 非 0 时只回该集的成片；分镜工作台默认按集过滤，其它入口留全量。
    """
    ctx, _username, _project_name, project_dir, _output_dir = (
        await _freezone._resolve_freezone_project(project, user, required_role="viewer")
    )
    clips = scan_motion_renders(
        _freezone.outputs_dir(project_dir, MOTION_RENDER_TASK_TYPE)
    )
    data = [
        {
            **clip.as_dict(),
            "url": _freezone.make_static_url_for_context(
                ctx, clip.video_path.relative_to(project_dir).as_posix()
            ),
        }
        for clip in clips
        if not episode or clip.episode == episode
    ]
    return {"ok": True, "data": data, "total": len(data)}


@router.post("/projects/{project}/freezone/motion/render", tags=[TAG_FREEZONE_VIDEO])
async def freezone_motion_render(
    project: str,
    body: FreezoneMotionRenderRequest,
    user: dict = Depends(get_api_user),
):
    """[aipdd fork delta] 动效大片渲染：一页 MG 脚本 → 一条 MG 成片（后台任务）。

    画面由页面里的代码画：本系统用随包/本机的无头 Chrome 把时间轴钉在 ``i/fps``
    上逐帧截图，再流式喂给 ffmpeg 编码。**渲染这一步不出网** —— 页面是文本节点按
    图册的渲染契约产出的，这里只负责把它确定性地变成像素。提交前先自检本机依赖
    （Chrome / ffmpeg），缺什么当场说清楚，不排注定失败的任务。

    两种入口：带 ``html`` 就是"渲染这一页"；``html`` 留空则必须带 ``case_id``
    （可再带 ``subject`` 本镜头文案），任务里先按同一份契约让网关文本模型写页面
    ——分镜工作台逐镜一键出片走的是后者。
    """
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    _check_page_source(body.html, body.case_id.strip(), body.style_id.strip())
    try:
        render_preflight()
    except MotionRenderError as exc:
        raise HTTPException(409, str(exc)) from exc

    job_id = _freezone._new_job_id()
    payload = {
        "html": body.html,
        "case_id": body.case_id.strip(),
        "style_id": body.style_id.strip(),
        "subject": body.subject.strip(),
        "model_text": body.model_text.strip(),
        "title": body.title.strip(),
        "aspect_ratio": body.aspect_ratio.strip(),
        "resolution": body.resolution.strip(),
        "fps": body.fps,
        "duration_seconds": body.duration_seconds,
        "node_id": body.node_id.strip(),
        "canvas_id": body.canvas_id.strip(),
        "archive_to_library": body.archive_to_library,
        "source_tool": body.source_tool,
        "episode": body.episode,
        "beat_number": body.beat_number,
        "music": body.music,
    }
    try:
        return await _freezone._enqueue_or_start_freezone_motion_render(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=job_id,
            payload=payload,
        )
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error(
            "failed to start freezone motion render task", exc
        )
        raise HTTPException(503, f"failed to start freezone motion render task: {exc}") from exc


@router.post("/projects/{project}/freezone/motion/stills", tags=[TAG_FREEZONE_VIDEO])
async def freezone_motion_stills(
    project: str,
    body: FreezoneMotionStillsRequest,
    user: dict = Depends(get_api_user),
):
    """[aipdd fork delta] 动效大片拍点预检：一页 MG 脚本 → 每个拍点一张静帧（后台任务）。

    渲染契约第 4 条「先出静帧再出成片」的入口。走的是与成片同一个包内 Chrome、同一个
    :meth:`MotionPage.capture_at`，因此预检看到的就是这一镜成片会有的画面——差别只在
    这里不编码、不配乐，所以**只要求本机有 Chrome，不要求 ffmpeg**。页面来源与渲染
    同口径：带 ``html`` 就检这一页，留空则先按案例契约让文本模型写页面。
    """
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    _check_page_source(body.html, body.case_id.strip(), body.style_id.strip())
    try:
        stills_preflight()
    except MotionRenderError as exc:
        raise HTTPException(409, str(exc)) from exc

    job_id = _freezone._new_job_id()
    payload = {
        "html": body.html,
        "case_id": body.case_id.strip(),
        "style_id": body.style_id.strip(),
        "subject": body.subject.strip(),
        "model_text": body.model_text.strip(),
        "aspect_ratio": body.aspect_ratio.strip(),
        "resolution": body.resolution.strip(),
        "max_stills": body.max_stills,
        "source_tool": body.source_tool,
    }
    try:
        return await _freezone._enqueue_or_start_freezone_motion_stills(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=job_id,
            payload=payload,
        )
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error(
            "failed to start freezone motion stills task", exc
        )
        raise HTTPException(503, f"failed to start freezone motion stills task: {exc}") from exc


@router.post("/projects/{project}/freezone/motion/draft", tags=[TAG_FREEZONE_VIDEO])
async def freezone_motion_draft(
    project: str,
    body: FreezoneMotionDraftRequest,
    user: dict = Depends(get_api_user),
):
    """[aipdd fork delta] 动效大片交付：逐镜 MG 成片 → 一盘剪映草稿（后台任务）。

    渲染一次只出一镜，交付要的是一整条时间线。本接口把选中的成片按顺序贴到主视频轨，
    每镜自报的拍点（``window.EV``）落成剪映的打点，可选再垫旁白与 BGM，然后按剪映认得的
    目录结构落盘（含 ``Timelines/`` 与各镜像），``deploy`` 时整盘复制进本机剪映工程目录。
    **全程不出网、不调模型、不依赖 VectCut 服务**——装机形态没有服务容器，这条是桌面
    用户唯一的交付路径。提交前自检随包模板骨架是否完整，缺了当场说清楚。
    """
    ctx, username, project_name, project_dir, output_dir = await _freezone._resolve_freezone_project(
        project, user
    )
    if not body.clips:
        raise HTTPException(400, "没有任何 MG 成片可落轨：请先渲染至少一个镜头")
    if template_dir() is None:
        raise HTTPException(
            409,
            "没找到随包的剪映 10.2 草稿模板（services/vectcut/template_jianying_10_2）："
            "请确认安装包资源完整，或用 MOTION_DRAFT_TEMPLATE_DIR 指定模板目录",
        )

    job_id = _freezone._new_job_id()
    payload = {
        "title": body.title.strip(),
        "clips": [clip.model_dump() for clip in body.clips],
        "narration": body.narration.model_dump() if body.narration else None,
        "bgm": body.bgm.model_dump() if body.bgm else None,
        "subtitles": [item.model_dump() for item in body.subtitles],
        "text_style": body.text_style.model_dump() if body.text_style else None,
        "aspect_ratio": body.aspect_ratio.strip(),
        "resolution": body.resolution.strip(),
        "fps": body.fps,
        "draft_dir": body.draft_dir.strip(),
        "deploy": body.deploy,
    }
    try:
        return await _freezone._enqueue_or_start_freezone_motion_draft(
            ctx=ctx,
            username=username,
            project=project_name,
            project_dir=project_dir,
            output_dir=output_dir,
            job_id=job_id,
            payload=payload,
        )
    except RuntimeError as exc:
        _freezone._handle_task_start_runtime_error(
            "failed to start freezone motion draft task", exc
        )
        raise HTTPException(503, f"failed to start freezone motion draft task: {exc}") from exc
