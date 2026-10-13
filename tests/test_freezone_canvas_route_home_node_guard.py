"""B2 步 11 · 只撤画布 14 条路由的 home node 守卫，别的 64 条一行不动。

方案文档（`B2-canvas-placement-free.md` §6.4 步 11）的原话是「撤掉
`api/routes/freezone.py:326` 的 home node 守卫」。**照字面删是错的**，
`TCP-P60` 已把范围收窄成「按调用点逐个撤」，理由本文件用可执行的形式钉住：

- 那一行（集成线上已漂到 `:331`，在 `_resolve_freezone_project` 内）
  **不是画布路由的守卫，是 freezone 全部路由的守卫** —— 本文件
  `test_only_canvas_routes_opt_out_of_the_home_node_guard` 现场点数：
  `@router.` 78 条 ≡ `_resolve_freezone_project` 77 处调用，
  其中 `tags=[TAG_FREEZONE_CANVAS]` 只有 14 条。
- 删那一行 ＝ 一次性放开另外 ~64 条读写 `Path(ctx.output_dir)` 本地项目文件、
  **既没有租约也没有共享存储交代**的路由，与 §6.3 的「逐个撤、不批量撤」直接冲突。

故落地形态是给 `_resolve_freezone_project` 加一个**带默认值 `True` 的关键字参数**
`require_home_node`（形制照它自己签名里已有的 `*, required_role: str = "editor"`），
只在 14 条画布路由的调用点显式传 `False`。

四条用例分工：

1. `test_default_still_rejects_a_non_home_node_project` —— 不传新参数时行为逐字不变
   （错误体照 `tests/test_project_context.py:35` 的既有断言口径）。
2. `test_canvas_read_route_passes_the_home_node_guard` /
   `test_canvas_write_route_passes_the_home_node_guard` —— 14 条里挑读写各一条，
   在非 home node 上**过得了这道守卫**。只断言这一件：真正落盘还依赖共享存储，
   那是 `dispatch-and-branching.md` §11 第 4 行的交接项，不在本 EU 内。
3. `test_non_canvas_freezone_routes_are_still_blocked_on_a_non_home_node` ——
   另外 64 条挑 3 条，**仍然被拦**且错误体逐字相同。
4. `test_only_canvas_routes_opt_out_of_the_home_node_guard` —— AST 静态护栏（双向）：
   传 `require_home_node=False` 的调用点必须都在画布路由里，且 14 条画布路由必须全传了。
   形制照同目录 `tests/test_freezone_canvas_route_to_thread.py:180` 的 AST 不变量。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi import HTTPException

from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.api.schemas import CanvasPayload
from novelvideo.project_context import ProjectContext

# 拆包自 freezone.py 后指向路由包目录；AST 取证口径不变。
ROUTES_SOURCE = Path("src/novelvideo/api/routes/freezone")

# 守卫的错误体真源：`project_context.py:50-58`。
HOME_NODE_REJECTION_CODE = "project_not_on_this_node"
# `_resolve_freezone_project` 传给守卫的字面量，别的用例可能在断言它。
FREEZONE_OPERATION = "access freezone project files"

USER = {"id": "owner_1", "username": "admin"}


def _remote_ctx(tmp_path: Path) -> ProjectContext:
    """一个「项目不在本节点」的上下文，形状照 `test_freezone_canvas_route_to_thread.py:48`。"""

    return ProjectContext(
        project_id="proj_freezone",
        project_name="demo",
        owner_type="user",
        owner_id="owner_1",
        owner_username="admin",
        requester_user_id="owner_1",
        requester_username="admin",
        requester_principals=(("user", "owner_1"),),
        effective_role="editor",
        home_node_id="node_a",
        output_dir=tmp_path / "output" / "admin" / "demo",
        state_dir=tmp_path / "state" / "admin" / "demo",
        runtime_dir=tmp_path / "runtime" / "admin" / "demo",
        is_home_node=False,
    )


def _patch_remote_project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ProjectContext:
    """只打桩控制面解析，**真守卫原样留在链路里**（这正是被测对象）。"""

    ctx = _remote_ctx(tmp_path)
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    ctx.state_dir.mkdir(parents=True, exist_ok=True)

    async def fake_resolve_project_context(**_kwargs) -> ProjectContext:
        return ctx

    monkeypatch.setattr(freezone_routes, "resolve_project_context", fake_resolve_project_context)
    return ctx


def _assert_home_node_rejection(exc: HTTPException, ctx: ProjectContext) -> None:
    assert exc.status_code == 409
    assert exc.detail == {
        "code": HOME_NODE_REJECTION_CODE,
        "message": f"{FREEZONE_OPERATION} must run on the project home node",
        "project_id": ctx.project_id,
        "home_node_id": ctx.home_node_id,
    }


def _assert_not_a_home_node_rejection(exc: BaseException | None) -> None:
    if isinstance(exc, HTTPException) and isinstance(exc.detail, dict):
        assert exc.detail.get("code") != HOME_NODE_REJECTION_CODE, (
            "画布路由仍然被 home node 守卫拦住了"
        )


async def test_default_still_rejects_a_non_home_node_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不传新参数 ＝ 今天的行为，逐字不变（64 条非画布路由靠这个默认值不改一行）。"""

    ctx = _patch_remote_project(monkeypatch, tmp_path)

    with pytest.raises(HTTPException) as excinfo:
        await freezone_routes._resolve_freezone_project("proj_freezone", USER)

    _assert_home_node_rejection(excinfo.value, ctx)


async def test_canvas_read_route_passes_the_home_node_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`get_canvas`（`:11537`）在非 home node 上不再被守卫拦。"""

    _patch_remote_project(monkeypatch, tmp_path)

    try:
        await freezone_routes.get_canvas(
            project="proj_freezone",
            canvas_id="never_written",
            user=USER,
        )
    except HTTPException as exc:  # pragma: no cover - 只在回潮时走到
        _assert_not_a_home_node_rejection(exc)
        raise


async def test_canvas_write_route_passes_the_home_node_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`put_canvas`（`:11775`）在非 home node 上不再被守卫拦。

    只断言过得了守卫这一件事 —— 落盘本身还依赖共享存储（交接项）。
    """

    _patch_remote_project(monkeypatch, tmp_path)

    try:
        await freezone_routes.put_canvas(
            project="proj_freezone",
            canvas_id="written_off_home_node",
            body=CanvasPayload(nodes=[{"id": "n1"}], edges=[], metadata={}),
            user=USER,
        )
    except HTTPException as exc:  # pragma: no cover - 只在回潮时走到
        _assert_not_a_home_node_rejection(exc)
        raise


@pytest.mark.parametrize(
    "handler_name",
    [
        "freezone_image_camera_options",
        "freezone_image_style_templates",
        "freezone_video_camera_templates",
    ],
)
async def test_non_canvas_freezone_routes_are_still_blocked_on_a_non_home_node(
    handler_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """另外 64 条一行不改：仍然被拦，错误体逐字相同。"""

    ctx = _patch_remote_project(monkeypatch, tmp_path)
    handler = getattr(freezone_routes, handler_name)

    with pytest.raises(HTTPException) as excinfo:
        await handler(project="proj_freezone", user=USER)

    _assert_home_node_rejection(excinfo.value, ctx)


def _is_router_decorator(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "router"
    )


def _is_canvas_decorator(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call):
        return False
    for keyword in node.keywords:
        if keyword.arg != "tags" or not isinstance(keyword.value, ast.List):
            continue
        if any(
            isinstance(element, ast.Name) and element.id == "TAG_FREEZONE_CANVAS"
            for element in keyword.value.elts
        ):
            return True
    return False


def _is_resolver_call(call: ast.AST) -> bool:
    """`_resolve_freezone_project` 调用点：裸名或经包命名空间 `_freezone.` 调用。

    拆包后调用点统一走 `_freezone._resolve_freezone_project(...)`（patch 兼容
    契约：测试 monkeypatch 包属性），定义仍在 `_common.py`。
    """

    if not isinstance(call, ast.Call):
        return False
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == "_resolve_freezone_project"
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "_resolve_freezone_project"
        and isinstance(func.value, ast.Name)
        and func.value.id == "_freezone"
    )


def _opts_out_of_the_guard(call: ast.Call) -> bool:
    return any(
        keyword.arg == "require_home_node"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is False
        for keyword in call.keywords
    )


def _routes_trees() -> list[ast.Module]:
    """路由包内全部模块的语法树（拆包自 freezone.py，AST 取证口径不变）。"""

    return [
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for path in sorted(ROUTES_SOURCE.glob("*.py"))
    ]


def test_only_canvas_routes_opt_out_of_the_home_node_guard() -> None:
    """双向棘轮：opt-out 只许出现在画布路由里，且 14 条必须全部 opt-out。

    这条防的是「以后有人顺手多传一个 `require_home_node=False`」——
    `TCP-P60` 的整个论证建立在「撤除面恰好是那 14 条」上。

    2026-08-27 由 13 → 14：新增 `DELETE .../nodes/{node_id}/generation-history`。
    它带 `tags=[TAG_FREEZONE_CANVAS]`、只改画布自己的历史 JSONL，落在撤除面内。
    """

    trees = _routes_trees()
    # 拆包后 AST 分布在多个文件，跨文件 lineno 不再唯一，反向断言
    # 改用节点对象集合（ast 节点无自定义 __eq__，即身份比较）。
    nodes_all = [node for tree in trees for node in ast.walk(tree)]

    canvas_routes: dict[str, list[ast.Call]] = {}
    opt_outs_inside_routes: set[ast.Call] = set()
    router_decorators = 0

    for node in nodes_all:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorators = [dec for dec in node.decorator_list if _is_router_decorator(dec)]
        if not decorators:
            continue
        router_decorators += len(decorators)
        resolver_calls = [call for call in ast.walk(node) if _is_resolver_call(call)]
        if any(_is_canvas_decorator(dec) for dec in decorators):
            canvas_routes[node.name] = resolver_calls
            opt_outs_inside_routes.update(call for call in resolver_calls if _opts_out_of_the_guard(call))
        else:
            assert [call.lineno for call in resolver_calls if _opts_out_of_the_guard(call)] == [], (
                f"非画布路由 {node.name} 传了 require_home_node=False"
            )

    all_opt_outs = {
        call for call in nodes_all if _is_resolver_call(call) and _opts_out_of_the_guard(call)
    }

    # 取证口径（`TCP-P60`）：freezone 79 条路由全过同一个解析器，画布只占 14 条；
    # 之后新增的跨项目素材拷贝路由（assets/copy）和视频延长路由也走守卫，计 81。
    # [aipdd fork delta] 新增画布音频模型列表端点（GET /freezone/audio/models），计 82；
    # 视频复刻路由（POST /freezone/video/clone）计 83；
    # 混剪短视频路由（POST /freezone/video/mix）计 84；
    # 一句话生成短视频路由（POST /freezone/video/idea）计 85；
    # 抖音文案提取模型候选路由（GET /freezone/douyin-copywrite/models）计 86；
    # 抖音文案提取提交路由（POST /freezone/douyin-copywrite）计 87；
    # [aipdd fork delta] 资产库剪映草稿下载代理路由
    #（GET /freezone/video/drafts/{draft_id}/download）计 88；
    # VectCut 草稿目录上传（POST /freezone/video/drafts/upload）计 89；
    # VectCut 草稿目录补登记同步（POST /freezone/video/drafts/sync）计 90。
    # [aipdd fork delta] 音视频提取文案（req21）补记两条：
    #（GET /freezone/media-copywrite/models）计 91；（POST /freezone/media-copywrite）计 92；
    # 视频反推剧本提交路由（POST /freezone/video-script-reverse）计 93。
    # [aipdd fork delta] 补记 req23 批次欠账一条：视频反推故事提交路由
    #（POST /freezone/analyze-video-story）计 94；
    # [aipdd fork delta] 白板动画视频提交路由（POST /freezone/whiteboard-animation）计 95。
    # [aipdd fork delta] 科普讲解视频提交路由（POST /freezone/explainer-video）计 96。
    # [aipdd fork delta req46] 音乐模型候选目录（GET /freezone/music/models）计 97；
    # 翻唱预处理（POST /freezone/audio/music-cover-preprocess）计 98；
    # 音乐创作提交（POST /freezone/audio/music-create）计 99。
    # [aipdd fork delta B+C] Blender 白模视频两条路由（前一批漏记，在此补齐）：
    # 拉回（POST /freezone/blender/pull-white-model-video）计 100；
    # 入库（POST /freezone/blender/ingest-white-model-video）计 101。
    # [aipdd fork delta] 视频配音提交路由（POST /freezone/audio/video-dubbing）计 102。
    # [aipdd fork delta] Phase 2A 公众号文章两条路由（前一批漏记，在此补齐）：
    # 写作（POST /freezone/article/write）计 103；发布（POST /freezone/article/publish）计 104。
    # [aipdd fork delta] Phase 2B 渠道短视频提交路由（POST /freezone/channel-short-video）计 105。
    # [aipdd fork delta] 口播智能剪辑提交路由（POST /freezone/narration-smart-edit）计 106。
    assert router_decorators == 106
    assert len(canvas_routes) == 14

    # 正向：14 条画布路由必须全部、且每一处调用都 opt-out。
    missing = {
        name: [call.lineno for call in calls if not _opts_out_of_the_guard(call)]
        for name, calls in canvas_routes.items()
        if not calls or not all(_opts_out_of_the_guard(call) for call in calls)
    }
    assert missing == {}

    # 反向：全包的 opt-out 一个都不许落在画布路由之外（含模块级 helper）。
    assert all_opt_outs == opt_outs_inside_routes
