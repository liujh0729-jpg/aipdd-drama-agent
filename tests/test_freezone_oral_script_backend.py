"""[aipdd fork delta] 短视频口播稿创作（freezone_oral_script）后端契约测试。

覆盖三层：
1. 入队：走 default 队列、载荷逐字透传（只在漏斗里补 job_id / project_dir）；
2. 路由：topic 与素材至少要有一项、素材长度闸门、结构与数量档位在路由层归一化；
3. runner 全链：一次文本叶出稿 → 本地去 AI 味预检 → 命中假对比硬伤时**只补一轮**定向
   重写 → 写结果 JSON + 净台词 txt。

方法论本身（炉心与事实底线写进指令、假对比十种壳）在 ``freezone/oral_script`` 的纯函数
里，本文件守「链路编排与产物形状」：文本叶换成显式签名的假对象，全程不出网。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.api.routes.freezone import oral_script_ops
from novelvideo.api.schemas import FreezoneOralScriptRequest
from novelvideo.freezone.oral_script import SECTION_IDS
from novelvideo.freezone.oral_script import SECTION_TITLES
from novelvideo.freezone.paths import outputs_dir
from novelvideo.project_context import ProjectContext


#: 一次成稿的干净样本：六节齐全、镜头提示单独成行、标题里带「（推荐 - …）」标记。
_CLEAN_SCRIPT = """## 内核
讲清楚我为什么把全案客户退掉，靠账目留人。

## 开头
候选一：去年我退了三个全案客户。紧跟着报出退掉之后的排期。

## 口播逐字稿
（坐着对镜头，手里拿那张报价单）
去年我退了三个全案客户。
三个客户要的东西我交付不了，账算下来每个月倒贴两天。
现在我只接剪辑和包装，最短的一条片子改了七遍。
你要是也在自己剪视频，先把验收标准写进合同再开工。

## 为什么这么排
- 开头先给结果，让观众知道这条有账可算。

## 标题候选
去年我退了三个全案客户（推荐 - 有账可算）
验收标准写进合同再开工

## 拍之前
- 需要那张报价单的实拍特写。
"""

#: 带假对比硬拦的初稿：本地检查必须抓到它，重写 prompt 里要回喂这句原话。
_HARD_HIT_SCRIPT = _CLEAN_SCRIPT.replace(
    "三个客户要的东西我交付不了，账算下来每个月倒贴两天。",
    "这不是接单纠纷，是我把验收标准写错了。",
)

#: 重写后仍然不合格的版本：整节「口播逐字稿」没了，runner 要留住初稿。
_LOST_SCRIPT = """## 内核
讲清楚我为什么把全案客户退掉。

## 标题候选
去年我退了三个全案客户（推荐 - 有账可算）
"""

#: 假对比只藏在「为什么这么排」里：门禁与交付报告取同一份文本才抓得到它。
_WHY_HARD_SCRIPT = _CLEAN_SCRIPT.replace(
    "- 开头先给结果，让观众知道这条有账可算。",
    "- 这不是接单纠纷，是我把验收标准写错了。",
)


def _oral_payload(project_dir: Path, **overrides: object) -> dict:
    payload: dict[str, object] = {
        "job_id": "oral_job",
        "project_dir": str(project_dir),
        "topic": "去年我退了三个全案客户",
        "persona": "做了八年后期，现在只接剪辑和包装",
        "material": "上个月退掉三个全案客户，报价单还在墙上。",
        "audience": "正在自己剪视频的小店主",
        "scene": "坐着对镜头讲，手里拿报价单",
        "tone": "直、不爱绕弯",
        "formula": "contrarian",
        "duration_s": 60,
        "title_count": 3,
        "rewrite_hard_hits": True,
        "model_text": "DC-llm-custom",
        "source_tool": "oral-script",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------
# 1) 入队
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_oral_script_enqueue_uses_default_queue_and_verbatim_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_enqueue_project_task(*_args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            task_state=SimpleNamespace(task_id="task_1"),
            backend="celery",
            queue="node.default",
        )

    monkeypatch.setattr(
        freezone_routes,
        "get_task_backend",
        lambda: SimpleNamespace(enqueue_project_task=fake_enqueue_project_task),
    )

    ctx = SimpleNamespace(project_id="project_59", requester_user_id="user-1")
    result = await freezone_routes._enqueue_or_start_freezone_oral_script(
        ctx=ctx,
        username="admin",
        project="59",
        project_dir=tmp_path,
        output_dir=str(tmp_path),
        job_id="oral_job",
        payload={"topic": "退掉三个全案客户", "duration_s": 60},
    )

    assert result["ok"] is True
    assert result["data"]["task_type"] == "freezone_oral_script"
    assert captured["product_surface"] == "freezone"
    assert captured["task_type"] == "freezone_oral_script"
    # 只走一次文本生成（硬伤时多一轮），是轻量任务：留在 default 队列，不占 video。
    assert captured["queue_kind"] == "default"
    assert captured["episode"] == 0
    assert captured["scope"] == "oral_job"
    assert captured["payload"] == {
        "job_id": "oral_job",
        "project_dir": str(tmp_path),
        "topic": "退掉三个全案客户",
        "duration_s": 60,
    }


@pytest.mark.asyncio
async def test_oral_script_enqueue_requires_project_context() -> None:
    with pytest.raises(HTTPException) as info:
        await freezone_routes._enqueue_or_start_freezone_oral_script(
            ctx=None,
            username="admin",
            project="59",
            project_dir=Path("/tmp"),
            output_dir="/tmp",
            job_id="oral_job",
            payload={},
        )
    assert info.value.status_code == 503


# ---------------------------------------------------------------
# 2) 路由
# ---------------------------------------------------------------


def _patch_oral_route(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, captured: dict[str, object]
) -> None:
    ctx = SimpleNamespace(
        requester_user_id="user-1",
        requester_username="user-1",
        project_id="project_59",
        output_dir=str(tmp_path),
    )

    async def _fake_resolve(project: str, user: dict, *, required_role: str = "editor"):
        del user, required_role
        return ctx, "admin", project, tmp_path, str(tmp_path)

    async def fake_projection(**kwargs):
        captured["projection_task_type"] = kwargs["task_type"]
        return {"projection": {"voice_registry": {}}}

    async def fake_enqueue(**kwargs):
        captured.update(kwargs)
        captured.update(kwargs["payload"])
        return {
            "ok": True,
            "data": {
                "task_type": "freezone_oral_script",
                "job_id": kwargs["job_id"],
                "task_key": f"freezone_oral_script:{kwargs['job_id']}",
            },
        }

    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", _fake_resolve)
    monkeypatch.setattr(freezone_routes, "_new_job_id", lambda: "oral_job")
    monkeypatch.setattr(freezone_routes, "_task_projection_payload", fake_projection)
    monkeypatch.setattr(
        freezone_routes, "_enqueue_or_start_freezone_oral_script", fake_enqueue
    )


@pytest.mark.asyncio
async def test_oral_script_route_normalizes_at_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}
    _patch_oral_route(monkeypatch, tmp_path, captured)

    result = await oral_script_ops.freezone_oral_script(
        project="59",
        body=FreezoneOralScriptRequest(
            topic="  去年我退了三个全案客户  ",
            persona="  做了八年后期  ",
            material="  报价单还在墙上。  ",
            audience="正在自己剪视频的小店主",
            scene="坐着对镜头讲",
            tone="直、不爱绕弯",
            # 未知结构 + 越界档位：都在路由层夹成任务记录里那一份口径。
            formula="not-a-formula",
            duration_s=999,
            title_count=99,
            rewrite_hard_hits=False,
            model_text="DC-llm-custom",
            source_tool="oral-script",
        ),
        user={"username": "admin"},
    )

    assert result["ok"] is True
    assert captured["projection_task_type"] == "freezone_oral_script"
    assert captured["topic"] == "去年我退了三个全案客户"
    assert captured["persona"] == "做了八年后期"
    assert captured["material"] == "报价单还在墙上。"
    assert captured["audience"] == "正在自己剪视频的小店主"
    assert captured["scene"] == "坐着对镜头讲"
    assert captured["tone"] == "直、不爱绕弯"
    assert captured["formula"] == "auto"
    assert captured["duration_s"] == 180
    assert captured["title_count"] == 5
    assert captured["rewrite_hard_hits"] is False
    assert captured["model_text"] == "DC-llm-custom"
    assert captured["source_tool"] == "oral-script"
    # 默认生成就入库：txt 口播稿进资产库「文本」载体。
    assert captured["archive_to_library"] is True


@pytest.mark.asyncio
async def test_oral_script_route_defaults_without_a_topic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}
    _patch_oral_route(monkeypatch, tmp_path, captured)

    await oral_script_ops.freezone_oral_script(
        project="59",
        body=FreezoneOralScriptRequest(
            material="只给一段素材，主题让模型从里面挑。"
        ),
        user={"username": "admin"},
    )

    assert captured["topic"] == ""
    assert captured["formula"] == "auto"
    assert captured["duration_s"] == 60
    assert captured["title_count"] == 3
    assert captured["rewrite_hard_hits"] is True
    assert captured["archive_to_library"] is True


@pytest.mark.asyncio
async def test_oral_script_route_omits_archive_key_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """显式关闭时不往 payload 写归档键（画布等入口的口径）。"""

    captured: dict[str, object] = {}
    _patch_oral_route(monkeypatch, tmp_path, captured)

    await oral_script_ops.freezone_oral_script(
        project="59",
        body=FreezoneOralScriptRequest(
            material="只给一段素材。",
            archive_to_library=False,
        ),
        user={"username": "admin"},
    )

    assert "archive_to_library" not in captured


@pytest.mark.asyncio
async def test_oral_script_route_rejects_missing_topic_and_material(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_oral_route(monkeypatch, tmp_path, {})

    with pytest.raises(HTTPException) as info:
        await oral_script_ops.freezone_oral_script(
            project="59",
            body=FreezoneOralScriptRequest(topic="   ", material="  "),
            user={"username": "admin"},
        )
    assert info.value.status_code == 400


@pytest.mark.asyncio
async def test_oral_script_route_rejects_oversize_material(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_oral_route(monkeypatch, tmp_path, {})

    with pytest.raises(HTTPException) as info:
        await oral_script_ops.freezone_oral_script(
            project="59",
            body=FreezoneOralScriptRequest(
                material="字" * (oral_script_ops.MAX_MATERIAL_CHARS + 1)
            ),
            user={"username": "admin"},
        )
    assert info.value.status_code == 400
    assert "material" in str(info.value.detail)


# ---------------------------------------------------------------
# 3) runner 全链
# ---------------------------------------------------------------


def _project_context(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        project_id="project-1",
        project_name="project",
        owner_type="user",
        owner_id="user-1",
        owner_username="user",
        requester_user_id="user-1",
        requester_username="user",
        requester_principals=(("user", "user-1"),),
        effective_role="editor",
        home_node_id="local",
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "runtime",
        is_home_node=True,
    )


class _TaskManager:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    def update_progress_for_project(self, *_args, **kwargs) -> None:
        self.updates.append(kwargs)


def _patch_oral_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    responses: list[str],
    *,
    fail_on: int | None = None,
) -> dict[str, Any]:
    """文本叶换成显式签名假对象：按调用顺序回放 responses，最后一份用到底。

    ``fail_on`` 指定第几次调用（1 起）直接抛错：用来守「可选的重写轮抖动不能把
    首轮已经算好的初稿连坐判死」。
    """
    from novelvideo.task_backend.runners import freezone as freezone_runner

    calls: dict[str, Any] = {"text": []}
    queue = list(responses)

    async def fake_generate_text(*, prompt, model=None):
        calls["text"].append({"prompt": prompt, "model": model})
        if fail_on is not None and len(calls["text"]) == fail_on:
            raise RuntimeError("gateway 502")
        reply = queue.pop(0) if queue else responses[-1]
        return "DC-llm-custom", reply

    task_manager = _TaskManager()
    monkeypatch.setattr(freezone_runner, "get_task_manager", lambda: task_manager)
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.ensure_freezone_dirs", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "novelvideo.freezone.text_node.generate_freezone_text", fake_generate_text
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.make_static_url_for_context",
        lambda _ctx, rel, **_k: f"/static/{rel}",
    )
    calls["task_manager"] = task_manager
    return calls


@pytest.mark.asyncio
async def test_oral_script_runner_delivers_six_sections_and_clean_txt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.freezone.oral_script import prompts as oral_prompts
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    calls = _patch_oral_runner(monkeypatch, tmp_path, [_CLEAN_SCRIPT])

    result = await freezone_runner._run_freezone_oral_script_async(
        {"task_type": "freezone_oral_script", "payload": _oral_payload(project_dir)},
        _project_context(tmp_path),
    )

    assert result["job_id"] == "oral_job"
    assert result["model"] == "DC-llm-custom"
    assert result["formula"] == "contrarian"
    assert result["title"] == "去年我退了三个全案客户"
    assert result["first_line"] == "去年我退了三个全案客户。"

    # 一次干净的成稿只走一次出网：文本叶是唯一的那一处（EG-18a）。
    assert len(calls["text"]) == 1
    prompt = calls["text"][0]["prompt"]
    assert calls["text"][0]["model"] == "DC-llm-custom"
    # 处境与素材都进了同一条指令，交付契约的六个标题也在里面。
    assert "做了八年后期，现在只接剪辑和包装" in prompt
    assert "上个月退掉三个全案客户" in prompt
    assert "正在自己剪视频的小店主" in prompt
    assert "## 口播逐字稿" in prompt
    assert "这不是接单纠纷" not in prompt

    # 六节按契约顺序导出，带稳定 ASCII id（前端靠它取词条，不吃中文章节名）。
    assert [item["id"] for item in result["sections"]] == list(SECTION_IDS)
    assert [item["title"] for item in result["sections"]] == list(SECTION_TITLES)
    assert result["sections"][2]["body"].startswith("（坐着对镜头")

    # 净台词把镜头提示挡在嘴外：括号行整行剔除，正文里不再留括号。
    assert "（坐着对镜头，手里拿那张报价单）" in result["script"]
    assert "（" not in result["spoken_text"]
    assert result["lines"][0].startswith("（坐着对镜头")
    assert result["stage_notes"] == ["坐着对镜头，手里拿那张报价单"]
    assert result["titles"][0] == {
        "title": "去年我退了三个全案客户",
        "recommended": True,
        "note": "",
    }

    spoken_chars = sum(
        1 for ch in result["spoken_text"] if "\u4e00" <= ch <= "\u9fff" or ch.isalnum()
    )
    assert result["word_count"] == spoken_chars
    assert result["estimated_seconds"] == oral_prompts.estimate_speech_seconds(
        result["spoken_text"]
    )
    assert result["estimated_seconds"] > 0

    # 本地预检：干净稿通过，且报告随产物一起交付（没命中不等于没有 AI 味）。
    assert result["rewritten"] is False
    assert result["rewrite_attempted"] is False
    assert result["rewrite_reason"] == ""
    assert result["ai_tell"]["passed"] is True
    assert result["ai_tell"]["hard_count"] == 0

    out_dir = outputs_dir(project_dir, "freezone_oral_script")
    txt_path = out_dir / "oral_job.txt"
    json_path = out_dir / "oral_job.json"
    assert result["txt_path"] == str(txt_path)
    assert result["output_path"] == str(json_path)
    assert result["txt_url"] == "/static/freezone/_outputs/freezone_oral_script/oral_job.txt"
    assert txt_path.read_text(encoding="utf-8") == result["spoken_text"]
    stored = json.loads(json_path.read_text(encoding="utf-8"))
    assert stored["sections"] == result["sections"]
    assert stored["ai_tell"]["passed"] is True

    progresses = [update["progress"] for update in calls["task_manager"].updates]
    assert progresses == [0.1, 0.85]


@pytest.mark.asyncio
async def test_oral_script_runner_rewrites_once_on_hard_hits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    calls = _patch_oral_runner(
        monkeypatch, tmp_path, [_HARD_HIT_SCRIPT, _CLEAN_SCRIPT]
    )

    result = await freezone_runner._run_freezone_oral_script_async(
        {"task_type": "freezone_oral_script", "payload": _oral_payload(project_dir)},
        _project_context(tmp_path),
    )

    # 「最多一轮有依据的修改」：命中硬伤补一轮就收口，不做无限重写。
    assert len(calls["text"]) == 2
    assert result["rewritten"] is True
    assert result["rewrite_attempted"] is True
    assert result["rewrite_reason"] == "ai_tell"
    assert result["ai_tell"]["passed"] is True
    assert result["spoken_text"].startswith("去年我退了三个全案客户。")

    rewrite_prompt = calls["text"][1]["prompt"]
    # 定向重写只针对被点名的句子，并把上一版全文原样带上（事实与结构冻住）。
    assert "这不是接单纠纷，是我把验收标准写错了" in rewrite_prompt
    assert "不是X而是Y" in rewrite_prompt
    assert _HARD_HIT_SCRIPT.strip() in rewrite_prompt

    progresses = [update["progress"] for update in calls["task_manager"].updates]
    assert progresses == [0.1, 0.55, 0.85]


@pytest.mark.asyncio
async def test_oral_script_runner_keeps_first_draft_when_rewrite_is_worse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    calls = _patch_oral_runner(monkeypatch, tmp_path, [_HARD_HIT_SCRIPT, _LOST_SCRIPT])

    result = await freezone_runner._run_freezone_oral_script_async(
        {"task_type": "freezone_oral_script", "payload": _oral_payload(project_dir)},
        _project_context(tmp_path),
    )

    # 重试把正文整节弄丢了就退回初稿：为了过检查交白卷比留着瑕疵更糟。
    assert len(calls["text"]) == 2
    assert result["script"].startswith("（坐着对镜头")
    # 重写确实跑了一轮，但没交出合格稿子：交付的仍是未重写的初稿，不能对外声称
    # 已经改过（前端据此展示「已保留初稿」）。
    assert result["rewritten"] is False
    assert result["rewrite_attempted"] is True
    assert result["rewrite_reason"] == "ai_tell"
    assert result["ai_tell"]["passed"] is False
    assert result["ai_tell"]["hard_count"] == 1
    assert result["ai_tell"]["hard"][0]["rule"] == "不是X而是Y"


@pytest.mark.asyncio
async def test_oral_script_runner_structure_gap_triggers_one_repair_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    calls = _patch_oral_runner(
        monkeypatch, tmp_path, [_LOST_SCRIPT, _CLEAN_SCRIPT]
    )

    result = await freezone_runner._run_freezone_oral_script_async(
        {"task_type": "freezone_oral_script", "payload": _oral_payload(project_dir)},
        _project_context(tmp_path),
    )

    # 缺「口播逐字稿」判结构不合格，补的那一轮按原契约重新交齐六节。
    assert len(calls["text"]) == 2
    assert "章节不完整或缺「口播逐字稿」整节" in calls["text"][1]["prompt"]
    assert result["rewrite_reason"] == "structure"
    assert result["rewritten"] is True
    assert [item["id"] for item in result["sections"]] == list(SECTION_IDS)


@pytest.mark.asyncio
async def test_oral_script_runner_skips_rewrite_when_toggle_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    calls = _patch_oral_runner(monkeypatch, tmp_path, [_HARD_HIT_SCRIPT])

    result = await freezone_runner._run_freezone_oral_script_async(
        {
            "task_type": "freezone_oral_script",
            "payload": _oral_payload(project_dir, rewrite_hard_hits=False),
        },
        _project_context(tmp_path),
    )

    # 关掉开关就一次出网：初稿照交付，硬伤随体检报告一起交给用户自己定。
    assert len(calls["text"]) == 1
    assert result["rewritten"] is False
    assert result["rewrite_reason"] == ""
    assert result["ai_tell"]["passed"] is False
    assert result["ai_tell"]["hard_count"] == 1

    txt = Path(result["txt_path"]).read_text(encoding="utf-8")
    assert "这不是接单纠纷，是我把验收标准写错了。" in txt


@pytest.mark.asyncio
async def test_oral_script_runner_omits_model_when_following_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    calls = _patch_oral_runner(monkeypatch, tmp_path, [_CLEAN_SCRIPT])

    result = await freezone_runner._run_freezone_oral_script_async(
        {
            "task_type": "freezone_oral_script",
            "payload": _oral_payload(project_dir, model_text="", persona=""),
        },
        _project_context(tmp_path),
    )

    # 留空 = 不传 model 键，让网关走系统默认文本链（不是传空串覆盖默认）。
    assert calls["text"][0]["model"] is None
    # 没给「这个人是谁」时，指令里要显式禁掉第一人称经历，而不是让模型自由发挥。
    assert "本次没有提供「这个人是谁」" in calls["text"][0]["prompt"]
    assert result["model"] == "DC-llm-custom"


@pytest.mark.asyncio
async def test_oral_script_runner_rewrites_when_hard_hit_hides_in_why_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    calls = _patch_oral_runner(
        monkeypatch, tmp_path, [_WHY_HARD_SCRIPT, _CLEAN_SCRIPT]
    )

    result = await freezone_runner._run_freezone_oral_script_async(
        {"task_type": "freezone_oral_script", "payload": _oral_payload(project_dir)},
        _project_context(tmp_path),
    )

    # 假对比藏在「为什么这么排」：只扫正文会漏掉它，门禁与交付报告必须同一份文本。
    assert len(calls["text"]) == 2
    assert "这不是接单纠纷，是我把验收标准写错了" in calls["text"][1]["prompt"]
    assert result["rewritten"] is True
    assert result["rewrite_reason"] == "ai_tell"
    assert result["ai_tell"]["passed"] is True


@pytest.mark.asyncio
async def test_oral_script_runner_keeps_first_draft_when_rewrite_round_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    calls = _patch_oral_runner(monkeypatch, tmp_path, [_HARD_HIT_SCRIPT], fail_on=2)

    result = await freezone_runner._run_freezone_oral_script_async(
        {"task_type": "freezone_oral_script", "payload": _oral_payload(project_dir)},
        _project_context(tmp_path),
    )

    # 首轮已经出稿：可选的重写轮被网关打回（502）也只降级成「初稿 + 体检报告」，
    # 不能把任务判失败——用户手里的稿子不该因为一次可选的优化丢掉。
    assert len(calls["text"]) == 2
    assert result["script"].startswith("（坐着对镜头")
    assert result["rewritten"] is False
    assert result["rewrite_attempted"] is True
    assert result["ai_tell"]["passed"] is False
    assert result["ai_tell"]["hard_count"] == 1
    assert Path(result["txt_path"]).read_text(encoding="utf-8") == result["spoken_text"]


@pytest.mark.asyncio
async def test_oral_script_runner_strips_inline_bold_from_spoken_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    bold_script = _CLEAN_SCRIPT.replace(
        "现在我只接剪辑和包装，最短的一条片子改了七遍。",
        "现在我只接**剪辑和包装**，最短的一条片子改了七遍。",
    )
    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    _patch_oral_runner(monkeypatch, tmp_path, [bold_script])

    result = await freezone_runner._run_freezone_oral_script_async(
        {"task_type": "freezone_oral_script", "payload": _oral_payload(project_dir)},
        _project_context(tmp_path),
    )

    # 净台词要直接进提词器 / 配音：markdown 星号会被当成正文念出来，成稿正文保留原样。
    assert "**" not in result["spoken_text"]
    assert "**剪辑和包装**" in result["script"]
    assert "剪辑和包装" in result["spoken_text"]
