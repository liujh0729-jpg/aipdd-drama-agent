"""[aipdd fork delta] 故事模块端点：故事存取、AI 优化、故事转剧本。

集级：故事存取、AI 优化、故事转剧本（逐行剧本 → 剧本模板，并自动进入剧本流程）。
项目级（L1「故事」栏目）：导入故事 → AI 优化 → 生成整本多集剧本 → 与「剧本」模块打通
（产物写入 uploads/ 剧本库，供 /ingest 章节预览与正式导入继续消费）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from novelvideo.api.auth import get_api_user, require_project_scope, require_scope
from novelvideo.api.chapter_preview import (
    build_chapter_preview,
    count_billable_novel_chars,
    load_novel_text,
    replace_chapter_body,
    split_chapter_title_body,
)
from novelvideo.api.deps import (
    get_sqlite_store,
    resolve_project_scope,
    sqlite_store_for_context_scope,
    sqlite_store_scope,
)
from novelvideo.api.llm_deadline import llm_request_deadline
from novelvideo.api.schemas import (
    ChapterBodyUpdateRequest,
    NovelToScreenplayRequest,
    ProjectStoryOptimizeRequest,
    ProjectStorySourceRequest,
    StoryOptimizeRequest,
    StoryToScriptRequest,
    StoryUpdateRequest,
)
from novelvideo.ports import get_task_backend, get_usage_meter
from novelvideo.project_config import (
    load_project_config_file_from_state_dir,
    save_project_config_in_state_dir,
)
from novelvideo.sqlite_store import SQLiteStore
from novelvideo.task_identity import project_task_state_key
from novelvideo.task_state import get_task_manager
from novelvideo.utils.document_parsers import (
    DocumentParseError,
    MAX_NOVEL_IMPORT_CHARS,
    is_supported_novel_path,
    supported_novel_extensions_label,
)
from novelvideo.utils.screenplay_quality import build_import_format_check
from novelvideo.utils.upload_safety import (
    MAX_NOVEL_IMPORT_BYTES,
    sanitize_upload_filename,
)

logger = logging.getLogger("novelvideo.api.story")

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


def _resolve_narrator_main_name(store: SQLiteStore) -> str:
    """从 store 里找 is_main=True 的解说主角名；没有返回空串。"""
    for character in store.get_all_characters():
        if getattr(character, "is_main", False):
            return character.name or ""
    return ""


def _episode_or_404(store: SQLiteStore, episode_num: int):
    episode = store.get_episode(episode_num)
    if episode is None:
        raise HTTPException(status_code=404, detail=f"第 {episode_num} 集不存在")
    return episode


def _validated_style(raw: str | None) -> str:
    from novelvideo.agents.director_screenwriter import is_valid_story_style

    value = (raw or "").strip()
    if not is_valid_story_style(value):
        raise HTTPException(status_code=400, detail="未知的导演风格")
    return value


async def _episode_story_text(store: SQLiteStore, episode: Any) -> str:
    """该集可用于改编的故事正文。

    用户在故事页手填过就以它为准；没填过则退回这份集已经在手的底稿
    （逐行解说稿 → 改编稿 → 原文 → 摘要），与 ``load_episode_planning_content``
    同一口径。自动化链路把「该集有内容」当前提，不该因为 ``story`` 这一列是空的
    就整轮失败——分集规划／原文导入产出的正文本来就是故事的下游输入。

    只读解析，不回写 ``episode.story``：那一列是用户自己的创作稿，不能让自动化静静盖掉。
    """
    explicit = str(getattr(episode, "story", "") or "").strip()
    if explicit:
        return explicit
    from novelvideo.sqlite_store import load_episode_planning_content

    return str(await load_episode_planning_content(store, episode) or "").strip()


@router.get("/projects/{project}/episodes/{episode_num}/story")
async def get_story(
    project: str,
    episode_num: int,
    user: dict = Depends(get_api_user),
    store: SQLiteStore = Depends(get_sqlite_store),
):
    """读取指定集的故事与导演风格。"""
    episode = _episode_or_404(store, episode_num)
    return {
        "ok": True,
        "data": {
            "episode": episode_num,
            "story": getattr(episode, "story", "") or "",
            "story_style": getattr(episode, "story_style", "") or "",
        },
    }


@router.put("/projects/{project}/episodes/{episode_num}/story")
async def put_story(
    project: str,
    episode_num: int,
    body: StoryUpdateRequest,
    user: dict = Depends(require_project_scope("projects:write")),
    store: SQLiteStore = Depends(get_sqlite_store),
):
    """保存指定集的故事；story_style 缺省时保持原值。"""
    logger.info(
        "[%s] EP%d put_story: %d chars, style=%r",
        project,
        episode_num,
        len(body.story),
        body.story_style,
    )
    fields: dict[str, Any] = {"story": body.story}
    if body.story_style is not None:
        fields["story_style"] = _validated_style(body.story_style)
    try:
        await store.patch_episode(episode_num, **fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "data": {
            "episode": episode_num,
            "length": len(body.story),
            "story_style": fields.get("story_style"),
        },
    }


@router.post("/projects/{project}/episodes/{episode_num}/story/optimize")
async def optimize_story_endpoint(
    project: str,
    episode_num: int,
    body: StoryOptimizeRequest | None = None,
    user: dict = Depends(get_api_user),
):
    """同步执行“故事诊断 → 优化故事”，结果不落盘（前端确认后再保存）。"""
    payload = body or StoryOptimizeRequest()
    resolved = await resolve_project_scope(project, user, required_role="editor")
    ctx = getattr(resolved, "ctx", None)
    store_scope = (
        sqlite_store_for_context_scope(ctx)
        if ctx
        else sqlite_store_scope(resolved.username, resolved.project_name)
    )
    async with store_scope as store:
        episode = _episode_or_404(store, episode_num)

        story_text = (payload.story or "").strip() or await _episode_story_text(store, episode)
    if not story_text:
        return {
            "ok": False,
            "error": f"第 {episode_num} 集还没有故事，也没有可改编的原文内容，请先填写故事或导入原文",
        }

    style_provided = payload.story_style is not None
    style_value = (
        _validated_style(payload.story_style)
        if style_provided
        else (getattr(episode, "story_style", "") or "")
    )

    usage_meter = get_usage_meter()
    # [C3/A7] 请求路径出网身份绑定：组织用户经本端点的优化 LLM 调用以其真实
    # 计费主体出网；非组织身份下什么都不绑、yield None，平台路径逐字节不变。
    from novelvideo.api.egress_binding import request_egress_scope

    async with request_egress_scope(
        requester_user_id=_requester_user_id(resolved, user),
        project_id=str(getattr(ctx, "project_id", "") or ""),
        task_type="story_optimize",
    ):
        try:
            usage_meter.set_llm_usage_context(
                _requester_user_id(resolved, user),
                project_id=str(getattr(ctx, "project_id", "") or ""),
                resource_kind="script",
            )
            from novelvideo.agents.director_screenwriter import optimize_story

            # [可靠性债] 诊断→优化是多轮往返，套总墙钟安全网：正常逐字节不变，
            # 只有病态挂起才转 504，不再无限占 worker。
            async with llm_request_deadline("故事优化"):
                optimized, notes = await optimize_story(
                    story_text,
                    project_title=resolved.project_name,
                    episode_title=getattr(episode, "title", "") or "",
                    episode_number=episode_num,
                    story_style=style_value,
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            usage_meter.clear_llm_usage_context()

    if not optimized:
        return {"ok": False, "error": "故事优化结果为空，请检查故事内容后重试"}

    return {
        "ok": True,
        "data": {
            "episode": episode_num,
            "story": optimized,
            "notes": notes,
            "changed": optimized != story_text,
        },
    }


@router.post("/projects/{project}/episodes/{episode_num}/story/to-script")
async def story_to_script_endpoint(
    project: str,
    episode_num: int,
    body: StoryToScriptRequest | None = None,
    user: dict = Depends(get_api_user),
):
    """故事转剧本：生成逐行剧本 → 导入剧本模板 → 自动进入剧本流程。"""
    payload = body or StoryToScriptRequest()
    resolved = await resolve_project_scope(project, user, required_role="editor")
    ctx = getattr(resolved, "ctx", None)
    output_dir = resolved.output_dir
    store_scope = (
        sqlite_store_for_context_scope(ctx)
        if ctx
        else sqlite_store_scope(resolved.username, resolved.project_name)
    )
    async with store_scope as store:
        episode = _episode_or_404(store, episode_num)

        story_text = (payload.story or "").strip() or await _episode_story_text(store, episode)
        if not story_text:
            return {
                "ok": False,
                "error": f"第 {episode_num} 集还没有故事，也没有可改编的原文内容，请先填写故事或导入原文",
            }

        style_provided = payload.story_style is not None
        style_value = (
            _validated_style(payload.story_style)
            if style_provided
            else (getattr(episode, "story_style", "") or "")
        )

        usage_meter = get_usage_meter()
        # [C3/A7] 请求路径出网身份绑定：组织用户经本端点的剧本生成 LLM 调用以
        # 其真实计费主体出网；非组织身份下什么都不绑、平台路径逐字节不变。
        from novelvideo.api.egress_binding import request_egress_scope

        async with request_egress_scope(
            requester_user_id=_requester_user_id(resolved, user),
            project_id=str(getattr(ctx, "project_id", "") or ""),
            task_type="story_to_script",
        ):
            try:
                usage_meter.set_llm_usage_context(
                    _requester_user_id(resolved, user),
                    project_id=str(getattr(ctx, "project_id", "") or ""),
                    resource_kind="script",
                )
                from novelvideo.agents.director_screenwriter import story_to_script

                async with llm_request_deadline("剧本生成"):
                    script_text = await story_to_script(
                        story_text,
                        project_title=resolved.project_name,
                        episode_title=getattr(episode, "title", "") or "",
                        episode_number=episode_num,
                        story_style=style_value,
                        protagonist_name=_resolve_narrator_main_name(store),
                        target_beats=payload.target_beats,
                        beat_chars_range=(payload.beat_chars_min, payload.beat_chars_max),
                    )
                normalized = script_text.strip()
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            finally:
                usage_meter.clear_llm_usage_context()

        if not normalized:
            return {"ok": False, "error": "剧本生成结果为空，请检查故事内容后重试"}

        # ── 导入剧本模板：adapted_content 与 beat_source_text 双写 ──
        patch_fields: dict[str, Any] = {"beat_source_text": normalized}
        front_story = (payload.story or "").strip()
        if front_story:
            patch_fields["story"] = front_story
        if style_provided:
            patch_fields["story_style"] = style_value
        try:
            await store.save_adapted_content(episode_num, normalized)
            await store.patch_episode(episode_num, **patch_fields)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # ── 自动进入剧本流程（与「生成剧本」按钮同一任务） ──
        task_info: dict[str, Any] | None = None
        identity_required = False
        if payload.autostart:
            fresh_episode = store.get_episode(episode_num)
            if not getattr(fresh_episode, "identity_ids", None):
                identity_required = True
            elif ctx is not None:
                from novelvideo.utils.path_resolver import PathResolver

                paths = PathResolver(output_dir, episode_num)
                await run_in_threadpool(paths.clean_sketches)
                queued = await get_task_backend().enqueue_project_task(
                    ctx,
                    product_surface="mainline",
                    task_type="script_writer",
                    queue_kind="default",
                    episode=episode_num,
                    payload={"episode": episode_num, "config": {}, "output_dir": output_dir},
                )
                task_info = {
                    "task_type": "script_writer",
                    "task_id": queued.task_state.task_id,
                    "task_key": project_task_state_key(
                        "script_writer", ctx.project_id, episode_num
                    ),
                    "backend": queued.backend,
                    "queue": queued.queue,
                }

    lines = [line for line in normalized.splitlines() if line.strip()]
    return {
        "ok": True,
        "data": {
            "episode": episode_num,
            "line_count": len(lines),
            "adapted_content": normalized,
            "story_saved": bool(front_story),
            "story_style": style_value,
            "identity_plan_required": identity_required,
            "task": task_info,
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# 项目级「故事」[aipdd fork delta]
# L1 故事栏目链路：导入故事 → AI 优化 → 生成整本多集剧本 → 入剧本库（uploads/），
# 生成的剧本可被「剧本」（/ingest）模块直接识别与继续消费。
# ──────────────────────────────────────────────────────────────────────────────

_STORY_DIR_NAME = "story"
_STORY_SOURCE_FILENAME = "source.txt"
_STORY_RAW_STEM = "raw_source"
_STORY_SOURCE_NAME_CONFIG_KEY = "story_source_filename"
_NOVEL_SCREENPLAY_CONFIG_KEY = "novel_screenplay_filename"
_NOVEL_SCREENPLAY_EPISODES_CONFIG_KEY = "novel_screenplay_episodes"
_NOVEL_SCREENPLAY_TASK_TYPE = "novel_to_screenplay"
_MAX_NOVEL_SCREENPLAY_EPISODES = 60


def _story_dir(project_dir: str | Path) -> Path:
    return Path(project_dir) / _STORY_DIR_NAME


def _story_source_path(project_dir: str | Path) -> Path:
    return _story_dir(project_dir) / _STORY_SOURCE_FILENAME


def _read_story_source(project_dir: str | Path) -> str:
    path = _story_source_path(project_dir)
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace").strip()


@router.get("/projects/{project}/story/source")
async def get_project_story_source(
    project: str,
    user: dict = Depends(get_api_user),
):
    """读取项目故事源（未导入时返回空文本）。"""
    resolved = await resolve_project_scope(project, user, required_role="viewer")
    text = _read_story_source(resolved.project_dir)
    config = load_project_config_file_from_state_dir(resolved.state_dir)
    return {
        "ok": True,
        "data": {
            "text": text,
            "chars": len(text),
            "billable_chars": count_billable_novel_chars(text),
            "filename": str(config.get(_STORY_SOURCE_NAME_CONFIG_KEY) or "").strip(),
        },
    }


@router.post("/projects/{project}/story/source/import")
async def import_project_story_source(
    project: str,
    file: Annotated[UploadFile | None, File()] = None,
    text: Annotated[str | None, Form()] = None,
    user: dict = Depends(get_api_user),
):
    """导入故事：粘贴文本或上传 .txt/.md/.docx 文件，落盘为项目故事源。"""
    resolved = await resolve_project_scope(project, user, required_role="editor")
    story_dir = _story_dir(resolved.project_dir)
    content = ""
    source_name = ""

    if file is not None and (file.filename or "").strip():
        source_name = sanitize_upload_filename(file.filename)
        if not is_supported_novel_path(source_name):
            return {
                "ok": False,
                "error": f"不支持的文件类型，当前支持: {supported_novel_extensions_label()}",
            }
        declared_size = getattr(file, "size", None)
        if isinstance(declared_size, int) and declared_size > MAX_NOVEL_IMPORT_BYTES:
            return {"ok": False, "error": "文件过大，无法作为故事导入"}
        data = await file.read()
        if len(data) > MAX_NOVEL_IMPORT_BYTES:
            return {"ok": False, "error": "文件过大，无法作为故事导入"}
        story_dir.mkdir(parents=True, exist_ok=True)
        raw_path = story_dir / f"{_STORY_RAW_STEM}{Path(source_name).suffix.lower()}"
        try:
            raw_path.write_bytes(data)
        except OSError:
            logger.exception("[%s] failed to persist story raw upload", project)
            return {"ok": False, "error": "保存上传文件失败"}
        try:
            content = (await run_in_threadpool(load_novel_text, raw_path)).strip()
        except DocumentParseError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception:
            logger.exception("[%s] failed to parse story upload", project)
            return {"ok": False, "error": "解析上传文件失败"}
    else:
        content = (text or "").strip()

    if not content:
        return {"ok": False, "error": "故事内容为空，请粘贴文本或上传文件"}

    billable_chars = count_billable_novel_chars(content)
    if billable_chars > MAX_NOVEL_IMPORT_CHARS:
        return {
            "ok": False,
            "error": (
                f"故事内容约 {billable_chars} 字，"
                f"超过 {MAX_NOVEL_IMPORT_CHARS} 字上限，请精简后重试"
            ),
        }

    story_dir.mkdir(parents=True, exist_ok=True)
    try:
        _story_source_path(resolved.project_dir).write_text(content, encoding="utf-8")
    except OSError:
        logger.exception("[%s] failed to persist story source", project)
        return {"ok": False, "error": "保存故事失败"}

    save_project_config_in_state_dir(
        resolved.state_dir, config={_STORY_SOURCE_NAME_CONFIG_KEY: source_name}
    )
    logger.info(
        "[%s] story source imported: %d chars (source=%r)",
        project,
        billable_chars,
        source_name,
    )
    return {
        "ok": True,
        "data": {
            "filename": source_name,
            "chars": len(content),
            "billable_chars": billable_chars,
        },
    }


@router.put("/projects/{project}/story/source")
async def put_project_story_source(
    project: str,
    body: ProjectStorySourceRequest,
    user: dict = Depends(get_api_user),
):
    """以 JSON 文本保存故事源（MCP/agent 通道），与导入端点的文本路径同口径校验。"""
    resolved = await resolve_project_scope(project, user, required_role="editor")
    content = (body.text or "").strip()
    if not content:
        return {"ok": False, "error": "故事内容为空，请传入故事文本"}

    billable_chars = count_billable_novel_chars(content)
    if billable_chars > MAX_NOVEL_IMPORT_CHARS:
        return {
            "ok": False,
            "error": (
                f"故事内容约 {billable_chars} 字，"
                f"超过 {MAX_NOVEL_IMPORT_CHARS} 字上限，请精简后重试"
            ),
        }

    story_dir = _story_dir(resolved.project_dir)
    story_dir.mkdir(parents=True, exist_ok=True)
    try:
        _story_source_path(resolved.project_dir).write_text(content, encoding="utf-8")
    except OSError:
        logger.exception("[%s] failed to persist story source via json", project)
        return {"ok": False, "error": "保存故事失败"}

    logger.info("[%s] story source saved via JSON: %d chars", project, billable_chars)
    return {
        "ok": True,
        "data": {
            "chars": len(content),
            "billable_chars": billable_chars,
        },
    }


@router.post("/projects/{project}/story/source/optimize")
async def optimize_project_story_source(
    project: str,
    body: ProjectStoryOptimizeRequest | None = None,
    user: dict = Depends(get_api_user),
):
    """整本故事诊断优化（故事优化模式），结果不落盘（前端确认后再保存）。"""
    payload = body or ProjectStoryOptimizeRequest()
    resolved = await resolve_project_scope(project, user, required_role="editor")
    ctx = getattr(resolved, "ctx", None)

    story_text = (payload.text or "").strip() or _read_story_source(
        resolved.project_dir
    )
    if not story_text:
        return {"ok": False, "error": "尚未导入故事，请先导入故事内容"}
    style_value = _validated_style(payload.story_style)

    usage_meter = get_usage_meter()
    # [C3/A7 + 可靠性] 项目级整本诊断优化此前只设了 usage_meter、未过出网身份
    # 绑定——组织用户经此调用由平台 ambient 身份垫付。现补上 request_egress_scope
    # （非组织 yield None、平台路径逐字节不变、组织拒绝作 AuthzError 逃出→fail-closed），
    # 并为多轮往返的生成套总墙钟安全网。
    from novelvideo.api.egress_binding import request_egress_scope

    async with request_egress_scope(
        requester_user_id=_requester_user_id(resolved, user),
        project_id=str(getattr(ctx, "project_id", "") or ""),
        task_type="story_source_optimize",
    ):
        try:
            usage_meter.set_llm_usage_context(
                _requester_user_id(resolved, user),
                project_id=str(getattr(ctx, "project_id", "") or ""),
                resource_kind="script",
            )
            from novelvideo.agents.director_screenwriter import optimize_novel

            async with llm_request_deadline("故事优化"):
                optimized, notes = await optimize_novel(
                    story_text,
                    project_title=resolved.project_name,
                    story_style=style_value,
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            usage_meter.clear_llm_usage_context()

    if not optimized:
        return {"ok": False, "error": "故事优化结果为空，请检查故事内容后重试"}

    return {
        "ok": True,
        "data": {
            "story": optimized,
            "notes": notes,
            "changed": optimized != story_text,
        },
    }


@router.post("/projects/{project}/story/generate-script")
async def generate_project_screenplay(
    project: str,
    body: NovelToScreenplayRequest | None = None,
    user: dict = Depends(require_scope("tasks:submit")),
):
    """把整本故事改编成多集剧本（后台任务），产物写入 uploads/ 剧本库。"""
    payload = body or NovelToScreenplayRequest()
    resolved = await resolve_project_scope(project, user, required_role="editor")
    ctx = resolved.ctx
    if ctx is None:
        return {"ok": False, "error": "生成剧本需要 project context"}

    story_text = _read_story_source(resolved.project_dir)
    if not story_text:
        return {"ok": False, "error": "尚未导入故事，请先导入故事内容"}

    episode_count = int(payload.episode_count or 0)
    if episode_count < 1 or episode_count > _MAX_NOVEL_SCREENPLAY_EPISODES:
        return {
            "ok": False,
            "error": f"集数需在 1-{_MAX_NOVEL_SCREENPLAY_EPISODES} 之间",
        }
    style_value = _validated_style(payload.story_style)

    output_filename = f"screenplay_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    output_path = Path(resolved.project_dir) / "uploads" / output_filename
    queued = await get_task_backend().enqueue_project_task(
        ctx,
        product_surface="mainline",
        task_type=_NOVEL_SCREENPLAY_TASK_TYPE,
        queue_kind="default",
        episode=0,
        payload={
            "story_path": str(_story_source_path(resolved.project_dir)),
            "output_path": str(output_path),
            "episode_count": episode_count,
            "story_style": style_value,
        },
    )
    logger.info(
        "[%s] novel_to_screenplay queued: %d episodes (style=%r)",
        project,
        episode_count,
        style_value,
    )
    return {
        "ok": True,
        "task_type": _NOVEL_SCREENPLAY_TASK_TYPE,
        "task_id": queued.task_state.task_id,
        "task_key": project_task_state_key(
            _NOVEL_SCREENPLAY_TASK_TYPE, ctx.project_id, 0
        ),
        "backend": queued.backend,
        "queue": queued.queue,
        # 这句只是给 API 调用者的人读回执：两处前端入口（故事页与剧本节点）弹的都是
        # 自己的本地词条，不读这个字段；把它换成 lmsg 会让 message 从 string 变成对象，
        # 反而打断已有契约。
        "message": f"已开始生成 {episode_count} 集剧本",  # i18n-exempt
    }


@router.get("/projects/{project}/story/generate-script/result")
async def get_generated_screenplay_result(
    project: str,
    user: dict = Depends(get_api_user),
):
    """返回整本剧本的生成进度与产物摘要（含格式校验与分集预览）。"""
    resolved = await resolve_project_scope(project, user, required_role="viewer")
    ctx = resolved.ctx

    manager = get_task_manager()
    task = (
        await run_in_threadpool(
            manager.get_task_for_project, ctx, _NOVEL_SCREENPLAY_TASK_TYPE, 0
        )
        if ctx is not None
        else None
    )
    status = task.status if task is not None else None
    task_error = (
        (str(task.error).strip() if task is not None and task.error else None) or None
    )
    task_progress = (
        float(task.progress)
        if task is not None and task.progress is not None
        else None
    )
    task_current = (
        (str(task.current_task).strip() if task is not None and task.current_task else None)
        or None
    )

    project_config = load_project_config_file_from_state_dir(resolved.state_dir)
    filename = str(project_config.get(_NOVEL_SCREENPLAY_CONFIG_KEY) or "").strip()
    episodes = project_config.get(_NOVEL_SCREENPLAY_EPISODES_CONFIG_KEY)
    episodes = int(episodes) if isinstance(episodes, int) else None

    total_chars = 0
    billable_chars = 0
    chapter_summaries: list[dict[str, Any]] = []
    format_check: dict | None = None
    has_valid_artifact = False

    if filename:
        uploads_dir = Path(resolved.project_dir) / "uploads"
        safe_name = sanitize_upload_filename(filename)
        script_path = uploads_dir / safe_name
        if safe_name == filename and script_path.is_file():
            has_valid_artifact = True
            text = script_path.read_text(encoding="utf-8", errors="replace")
            preview = build_chapter_preview(text)
            format_check = build_import_format_check(
                text,
                has_chapters=bool(preview.get("chapters")),
                chapters=preview.get("chapters"),
                require_scene_headers=True,
            )
            total_chars = int(preview.get("total_chars") or len(text))
            billable_chars = int(preview.get("billable_chars") or 0)
            for chapter in preview.get("chapters") or []:
                chapter_content = str(chapter.get("content") or "").strip()
                chapter_summaries.append(
                    {
                        "number": chapter.get("number"),
                        "title": str(chapter.get("title") or ""),
                        "preview": chapter_content[:80],
                        "word_count": int(
                            chapter.get("word_count") or len(chapter_content)
                        ),
                    }
                )

    # 任务记录会随 TTL 过期清理或服务重启而丢失；只要 config 仍记录着有效
    # 产物（uploads 文件存在），就把状态回退为 completed，让结果卡、同步按钮
    # 与分集预览入口在任务历史缺失时依然可用。
    if not status and has_valid_artifact:
        status = "completed"

    return {
        "ok": True,
        "data": {
            "status": status,
            "filename": filename or None,
            "episodes": episodes,
            "total_chars": total_chars,
            "billable_chars": billable_chars,
            "chapter_summaries": chapter_summaries,
            "format_check": format_check,
            "error": task_error,
            "progress": task_progress,
            "current_task": task_current,
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# 生成产物「同步到剧本」与单集查看/编辑 [aipdd fork delta]
# 「同步到剧本」把整本剧本产物写为项目 canonical novel.txt 并记录导入来源，
# 使「剧本」模块（/ingest）立即把生成结果作为当前剧本显示与继续消费。
# 单集接口支持逐集查看细节与编辑保存（写回剧本产物；若该产物已同步，
# 同时镜像到 novel.txt，保持两处一致）。
# ──────────────────────────────────────────────────────────────────────────────


def _resolve_generated_screenplay(resolved: Any) -> tuple[str, Path] | None:
    """解析当前生成剧本产物（config 记录的文件名 + uploads/ 安全校验）。"""
    config = load_project_config_file_from_state_dir(resolved.state_dir)
    filename = str(config.get(_NOVEL_SCREENPLAY_CONFIG_KEY) or "").strip()
    if not filename:
        return None
    safe_name = sanitize_upload_filename(filename)
    if safe_name != filename:
        return None
    path = Path(resolved.project_dir) / "uploads" / safe_name
    if not path.is_file():
        return None
    return safe_name, path


@router.post("/projects/{project}/story/generate-script/sync")
async def sync_generated_screenplay_to_library(
    project: str,
    user: dict = Depends(get_api_user),
):
    """把已生成的整本剧本同步到「剧本」模块（novel.txt + 来源记录）。"""
    resolved = await resolve_project_scope(project, user, required_role="editor")
    located = _resolve_generated_screenplay(resolved)
    if located is None:
        return {"ok": False, "error": "尚未生成剧本，请先生成整本剧本"}
    filename, script_path = located
    try:
        text = script_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        logger.exception("[%s] failed to read generated screenplay", project)
        return {"ok": False, "error": "读取剧本产物失败"}
    if not text:
        return {"ok": False, "error": "剧本产物为空，请重新生成"}

    preview = build_chapter_preview(text)
    if not preview.get("chapters"):
        return {"ok": False, "error": "剧本产物未检测到分集结构，请重新生成"}

    try:
        (Path(resolved.project_dir) / "novel.txt").write_text(text, encoding="utf-8")
    except OSError:
        logger.exception("[%s] failed to persist synced screenplay", project)
        return {"ok": False, "error": "写入剧本失败"}

    # 产物文件已在 uploads/ 剧本库中；记录为导入来源，重新导入可直接复用它。
    save_project_config_in_state_dir(
        resolved.state_dir, config={"ingest_source_filename": filename}
    )
    logger.info(
        "[%s] screenplay synced to library: %s (%d chapters)",
        project,
        filename,
        len(preview.get("chapters") or []),
    )
    return {
        "ok": True,
        "data": {
            "filename": filename,
            "chapters": len(preview.get("chapters") or []),
            "total_chars": int(preview.get("total_chars") or len(text)),
            "billable_chars": int(preview.get("billable_chars") or 0),
        },
    }


@router.get("/projects/{project}/story/generate-script/episodes/{episode_number}")
async def get_generated_screenplay_episode(
    project: str,
    episode_number: int,
    user: dict = Depends(get_api_user),
):
    """读取生成剧本中某一集的完整正文（供分集详情查看/编辑）。"""
    resolved = await resolve_project_scope(project, user, required_role="viewer")
    located = _resolve_generated_screenplay(resolved)
    if located is None:
        return {"ok": False, "error": "尚未生成剧本，请先生成整本剧本"}
    _filename, script_path = located
    try:
        text = script_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        logger.exception("[%s] failed to read generated screenplay", project)
        return {"ok": False, "error": "读取剧本产物失败"}
    preview = build_chapter_preview(text)
    chapter = next(
        (
            item
            for item in preview.get("chapters") or []
            if item.get("number") == episode_number
        ),
        None,
    )
    if chapter is None:
        return {"ok": False, "error": f"未找到第 {episode_number} 集"}
    title_line, body = split_chapter_title_body(str(chapter.get("content") or ""))
    return {
        "ok": True,
        "data": {
            "number": episode_number,
            "title": title_line,
            "body": body,
            "chars": len(body),
        },
    }


@router.put("/projects/{project}/story/generate-script/episodes/{episode_number}")
async def update_generated_screenplay_episode(
    project: str,
    episode_number: int,
    body: ChapterBodyUpdateRequest,
    user: dict = Depends(get_api_user),
):
    """保存某一集的正文编辑（标题行保持不变，写回剧本产物）。"""
    resolved = await resolve_project_scope(project, user, required_role="editor")
    new_body = body.body.strip()
    if not new_body:
        return {"ok": False, "error": "正文不能为空"}
    located = _resolve_generated_screenplay(resolved)
    if located is None:
        return {"ok": False, "error": "尚未生成剧本，请先生成整本剧本"}
    _filename, script_path = located
    try:
        text = script_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        logger.exception("[%s] failed to read generated screenplay", project)
        return {"ok": False, "error": "读取剧本产物失败"}
    updated = replace_chapter_body(text, episode_number, body.body)
    if updated is None:
        return {"ok": False, "error": f"未找到第 {episode_number} 集"}
    try:
        script_path.write_text(updated, encoding="utf-8")
    except OSError:
        logger.exception("[%s] failed to save screenplay episode", project)
        return {"ok": False, "error": "保存失败"}

    # 若该产物已同步为项目 canonical 剧本（novel.txt 内容一致），镜像这次编辑，
    # 让「剧本」模块与故事页看到同一份内容；否则说明 novel.txt 另有来源，不动它。
    novel_path = Path(resolved.project_dir) / "novel.txt"
    try:
        if novel_path.is_file():
            novel_text = novel_path.read_text(encoding="utf-8", errors="replace")
            if novel_text.strip() == text.strip():
                novel_updated = replace_chapter_body(
                    novel_text, episode_number, body.body
                )
                if novel_updated is not None:
                    novel_path.write_text(novel_updated, encoding="utf-8")
    except OSError:
        logger.warning(
            "[%s] failed to mirror screenplay episode edit to novel.txt",
            project,
            exc_info=True,
        )

    return {
        "ok": True,
        "data": {"number": episode_number, "chars": len(new_body)},
    }