"""[aipdd fork delta] 混剪短视频（``freezone_video_mix``）回归测试。

覆盖三层：
1. `novelvideo.freezone.mix` 纯逻辑：画布归一化 / 文案 prompt / 字幕时间轴 /
   format_srt / 素材切段规划（循环铺满、残差吸附、原始下标过滤）；
2. 路由层：入队走 video 队列、素材分类与模型定型、BGM 解析、载荷透传及各类 4xx；
3. runner 全链：文案 → 配音 → 字幕 → 探时长 → 规划 → 渲染 → 归档。

runner 用例直接调用 `_run_freezone_video_mix_async`（与
`test_freezone_video_clone_backend.py` 同口径），leaf 与渲染全部换成显式签名
假对象，守的是「链路编排与产物形状」而不是真实出网或出 ffmpeg。
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.freezone import jobs as jobs_module
from novelvideo.freezone.mix import (
    MixMaterial,
    build_mix_script_prompt,
    build_mix_subtitle_cues,
    format_srt,
    normalize_mix_aspect_ratio,
    normalize_mix_resolution,
    plan_mix_clips,
    resolve_mix_canvas,
)
from novelvideo.freezone.paths import outputs_dir
from novelvideo.project_context import ProjectContext


# ---------------------------------------------------------------
# 1) mix.py 纯逻辑
# ---------------------------------------------------------------


def test_mix_canvas_normalization_falls_back_to_vertical_default() -> None:
    assert normalize_mix_aspect_ratio("16:9") == "16:9"
    assert normalize_mix_aspect_ratio(" 1:1 ") == "1:1"
    assert normalize_mix_aspect_ratio("4:3") == "9:16"
    assert normalize_mix_aspect_ratio(None) == "9:16"

    assert normalize_mix_resolution("1080P") == "1080p"
    assert normalize_mix_resolution("4k") == "720p"
    assert normalize_mix_resolution(None) == "720p"

    assert resolve_mix_canvas("9:16", "1080p") == (1080, 1920)
    assert resolve_mix_canvas("16:9", "720p") == (1280, 720)
    assert resolve_mix_canvas("bogus", "bogus") == (720, 1280)


def test_mix_script_prompt_carries_subject_and_plain_text_rules() -> None:
    prompt = build_mix_script_prompt(subject="  春日露营的三个理由  ")

    assert "春日露营的三个理由" in prompt
    assert "口播" in prompt
    assert "不要标题" in prompt


def test_mix_subtitle_cues_merge_short_leading_piece() -> None:
    # 「好。」按权重只分到 444ms，低于 700ms 下限 → 并回后一条。
    cues = build_mix_subtitle_cues("好。今天天气非常棒", duration_ms=2000)

    assert len(cues) == 1
    assert cues[0].start_ms == 0
    assert cues[0].end_ms == 2000
    assert cues[0].text == "好。今天天气非常棒"


def test_mix_subtitle_cues_split_proportionally_by_text_weight() -> None:
    cues = build_mix_subtitle_cues("春天来了。夏天走了。", duration_ms=4000)

    assert [(cue.start_ms, cue.end_ms, cue.text) for cue in cues] == [
        (0, 2000, "春天来了。"),
        (2000, 4000, "夏天走了。"),
    ]


def test_mix_subtitle_cues_empty_inputs_return_empty() -> None:
    assert build_mix_subtitle_cues("", 4000) == []
    assert build_mix_subtitle_cues("有字", 0) == []


def test_format_srt_renders_numbered_blocks_with_timestamps() -> None:
    srt = format_srt(build_mix_subtitle_cues("春天来了。夏天走了。", duration_ms=4000))

    assert srt == (
        "1\n00:00:00,000 --> 00:00:02,000\n春天来了。\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\n夏天走了。\n"
    )
    assert format_srt([]) == ""


# ---------------------------------------------------------------
# 1b) 字幕字体：运行时自由选用部署环境字体（不绑定特定字体族）
# ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_mix_font_probe_cache() -> Iterator[None]:
    jobs_module._mix_available_font_families.cache_clear()
    jobs_module._mix_subtitle_font_name.cache_clear()
    yield
    jobs_module._mix_available_font_families.cache_clear()
    jobs_module._mix_subtitle_font_name.cache_clear()


def _stub_fontconfig(
    monkeypatch: pytest.MonkeyPatch,
    *,
    list_output: str | None,
    match_output: str = "",
    list_error: type[Exception] | None = None,
    match_error: type[Exception] | None = None,
) -> None:
    """把 jobs 模块内的 subprocess 换成 fc-list / fc-match 假实现。"""

    def _fake_run(cmd: list[str], **_: object) -> SimpleNamespace:
        if cmd[0] == "fc-list":
            if list_error is not None:
                raise list_error("fc-list unavailable")
            return SimpleNamespace(returncode=0, stdout=list_output or "", stderr="")
        if cmd[0] == "fc-match":
            if match_error is not None:
                raise match_error("fc-match unavailable")
            return SimpleNamespace(returncode=0, stdout=match_output, stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(
        jobs_module,
        "subprocess",
        # except 子句会求值 subprocess.SubprocessError，必须同步保留该属性。
        SimpleNamespace(run=_fake_run, SubprocessError=subprocess.SubprocessError),
    )


def test_mix_subtitle_font_free_picks_deployment_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 部署服务器只有 PingFang SC / Hiragino Sans GB（如 macOS 主机）→ 用部署环境的字体。
    _stub_fontconfig(
        monkeypatch,
        list_output="DejaVu Sans\nPingFang SC\nHiragino Sans GB\n",
    )

    assert jobs_module._mix_subtitle_font_name() == "PingFang SC"


def test_mix_subtitle_font_prefers_earliest_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # fc-list 一行多族（逗号分隔）也要能解析；命中名单靠前者。
    _stub_fontconfig(
        monkeypatch,
        list_output="Noto Sans CJK SC,Noto Sans CJK JP\nWenQuanYi Zen Hei\n",
    )

    assert jobs_module._mix_subtitle_font_name() == "Noto Sans CJK SC"


def test_mix_subtitle_font_falls_back_to_zh_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 候选都没装：fontconfig 对简体中文的最佳匹配兜底。
    _stub_fontconfig(
        monkeypatch,
        list_output="Some Latin Font\nCustom CJK Font\n",
        match_output="Custom CJK Font\n",
    )

    assert jobs_module._mix_subtitle_font_name() == "Custom CJK Font"


def test_mix_subtitle_font_blank_when_fontconfig_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 环境没有 fontconfig（fc-list 缺失）→ 不写 FontName，交给 libass fallback。
    _stub_fontconfig(monkeypatch, list_output=None, list_error=FileNotFoundError)

    assert jobs_module._mix_subtitle_font_name() == ""
    style = jobs_module._mix_subtitle_force_style(width=1080, height=1920)
    assert "FontName=" not in style


def test_mix_subtitle_font_blank_without_candidate_or_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_fontconfig(
        monkeypatch,
        list_output="Some Latin Font\n",
        match_error=OSError,
    )

    assert jobs_module._mix_subtitle_font_name() == ""


def test_mix_subtitle_force_style_scales_and_embeds_detected_font(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_fontconfig(monkeypatch, list_output="Noto Sans CJK SC\n")

    style = jobs_module._mix_subtitle_force_style(width=1080, height=1920)

    assert style.startswith("FontName=Noto Sans CJK SC,")
    assert "FontSize=63," in style  # max(28, round(1080 * 0.058))
    assert "MarginV=115" in style  # max(24, round(1920 * 0.06))
    assert "Alignment=2" in style


def test_mix_subtitle_force_style_font_size_tiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_fontconfig(monkeypatch, list_output="Noto Sans CJK SC\n")

    # 档位 × min(画布宽,高)：small 0.046 / medium 0.058（原口径）/ large 0.072。
    small = jobs_module._mix_subtitle_force_style(width=1080, height=1920, size="small")
    medium = jobs_module._mix_subtitle_force_style(width=1080, height=1920)
    large = jobs_module._mix_subtitle_force_style(width=1080, height=1920, size="large")
    unknown = jobs_module._mix_subtitle_force_style(
        width=1080, height=1920, size="bogus"
    )

    assert "FontSize=50," in small  # max(28, round(1080 * 0.046))
    assert "FontSize=63," in medium
    assert "FontSize=78," in large  # max(28, round(1080 * 0.072))
    assert "FontSize=63," in unknown  # 未知档位回退 medium


def test_plan_mix_clips_cycles_materials_to_fill_target() -> None:
    materials = [
        MixMaterial(kind="image", path="a.jpg"),
        MixMaterial(kind="video", path="b.mp4", duration=4.0),
    ]

    plan = plan_mix_clips(materials, clip_cap=2.0, target_seconds=9.3)

    # 图（一轮 1 段）→ 视频（一轮 2 段：0s / 2s）→ 图 → 视频残段。
    assert [
        (item.material_index, item.source_start, round(item.duration, 3), item.loop)
        for item in plan
    ] == [
        (0, 0.0, 2.0, False),
        (1, 0.0, 2.0, False),
        (1, 2.0, 2.0, False),
        (0, 0.0, 2.0, False),
        (1, 0.0, 1.3, False),
    ]
    assert sum(item.duration for item in plan) == pytest.approx(9.3)


def test_plan_mix_clips_absorbs_tail_into_looping_last_clip() -> None:
    materials = [MixMaterial(kind="video", path="b.mp4", duration=2.0)]

    plan = plan_mix_clips(materials, clip_cap=2.0, target_seconds=4.02)

    assert len(plan) == 2
    assert (plan[0].material_index, plan[0].source_start, plan[0].duration) == (
        0,
        0.0,
        2.0,
    )
    assert plan[0].loop is False
    # 尾差 0.02s 吸回上一段后越过视频剩余长度 → 渲染侧改走循环输入。
    assert plan[1].material_index == 0
    assert plan[1].source_start == 0.0
    assert plan[1].duration == pytest.approx(2.02)
    assert plan[1].loop is True


def test_plan_mix_clips_keeps_original_indices_and_skips_short_videos() -> None:
    materials = [
        MixMaterial(kind="video", path="tiny.mp4", duration=0.3),
        MixMaterial(kind="image", path="a.jpg"),
        MixMaterial(kind="video", path="b.mp4", duration=4.0),
    ]

    plan = plan_mix_clips(materials, clip_cap=1.5, target_seconds=None)

    # 0.3s 视频被跳过；material_index 是**原列表**下标（渲染侧按原列表取路径）。
    assert [(item.material_index, item.duration) for item in plan] == [
        (1, 1.5),
        (2, 1.5),
    ]


def test_plan_mix_clips_rejects_empty_or_unusable_pool() -> None:
    with pytest.raises(ValueError, match="no usable"):
        plan_mix_clips([], clip_cap=3.0, target_seconds=None)
    with pytest.raises(ValueError, match="no usable"):
        plan_mix_clips(
            [MixMaterial(kind="video", path="tiny.mp4", duration=0.2)],
            clip_cap=3.0,
            target_seconds=None,
        )


def test_plan_mix_clips_guard_raises_beyond_clip_limit() -> None:
    # 0.5s 一段铺 400s 需要 800 段，越过 600 段防御上限必须显式报错。
    with pytest.raises(ValueError, match="clip limit"):
        plan_mix_clips(
            [MixMaterial(kind="image", path="a.jpg")],
            clip_cap=0.5,
            target_seconds=400.0,
        )


# ---------------------------------------------------------------
# 2) 路由 / 入队
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_video_mix_enqueue_uses_video_queue_and_verbatim_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_enqueue_project_task(*_args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            task_state=SimpleNamespace(task_id="task_1"),
            backend="celery",
            queue="node.video",
        )

    monkeypatch.setattr(
        freezone_routes,
        "get_task_backend",
        lambda: SimpleNamespace(enqueue_project_task=fake_enqueue_project_task),
    )

    ctx = SimpleNamespace(project_id="project_59", requester_user_id="user-1")
    result = await freezone_routes._enqueue_or_start_freezone_video_mix(
        ctx=ctx,
        username="admin",
        project="59",
        project_dir=tmp_path,
        output_dir=str(tmp_path),
        job_id="mix_job",
        payload={"material_paths": ["/tmp/a.jpg"], "subject": "s"},
    )

    assert result["ok"] is True
    assert result["data"]["task_type"] == "freezone_video_mix"
    assert captured["product_surface"] == "freezone"
    assert captured["task_type"] == "freezone_video_mix"
    assert captured["queue_kind"] == "video"
    assert captured["episode"] == 0
    assert captured["scope"] == "mix_job"
    # 载荷原样透传：入队侧只补 job_id / project_dir，不改字段。
    assert captured["payload"] == {
        "job_id": "mix_job",
        "project_dir": str(tmp_path),
        "material_paths": ["/tmp/a.jpg"],
        "subject": "s",
    }


def _mix_material_files(tmp_path: Path) -> dict[str, Path]:
    image = tmp_path / "a.jpg"
    image.write_bytes(b"jpg")
    clip = tmp_path / "b.mp4"
    clip.write_bytes(b"mp4")
    bgm = tmp_path / "bgm.mp3"
    bgm.write_bytes(b"mp3")
    bad = tmp_path / "c.txt"
    bad.write_bytes(b"txt")
    return {"image": image, "clip": clip, "bgm": bgm, "bad": bad}


def _patch_mix_route_resolve(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ctx: SimpleNamespace,
    mapping: dict[str, Path],
) -> None:
    async def _fake_resolve(project: str, user: dict, *, required_role: str = "editor"):
        del user, required_role
        return ctx, "admin", project, tmp_path, str(tmp_path)

    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", _fake_resolve)
    monkeypatch.setattr(
        freezone_routes, "resolve_static_url_to_path", lambda url, _dir: mapping[url]
    )
    monkeypatch.setattr(freezone_routes, "_new_job_id", lambda: "mix_job")


@pytest.mark.asyncio
async def test_video_mix_route_resolves_materials_model_and_bgm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _mix_material_files(tmp_path)
    image_url = "/static/admin/59/freezone/_uploads/a.jpg"
    clip_url = "/static/admin/59/freezone/_uploads/b.mp4"
    bgm_url = "/static/admin/59/freezone/_uploads/bgm.mp3"

    ctx = SimpleNamespace(
        requester_user_id="user-1",
        requester_username="user-1",
        project_id="project_59",
        output_dir=str(tmp_path),
    )
    _patch_mix_route_resolve(
        monkeypatch,
        tmp_path,
        ctx,
        {
            image_url: files["image"],
            clip_url: files["clip"],
            bgm_url: files["bgm"],
        },
    )

    catalog_calls: list[tuple] = []
    projection_calls: list[dict] = []
    captured: dict[str, object] = {}

    async def fake_catalog(media_type, *, requester_user_id):
        catalog_calls.append((media_type, requester_user_id))
        return [{"id": "tts-a", "apiModel": "newapi-tts-1"}]

    async def fake_projection(**kwargs):
        projection_calls.append(kwargs)
        return {"projection": {"voice_registry": {}}}

    async def fake_enqueue(**kwargs):
        captured.update(kwargs)
        captured.update(kwargs["payload"])
        return {
            "ok": True,
            "data": {
                "task_type": "freezone_video_mix",
                "job_id": kwargs["job_id"],
                "task_key": f"freezone_video_mix:{kwargs['job_id']}",
            },
        }

    monkeypatch.setattr(freezone_routes, "_scoped_media_model_catalog", fake_catalog)
    monkeypatch.setattr(freezone_routes, "_task_projection_payload", fake_projection)
    monkeypatch.setattr(
        freezone_routes, "_enqueue_or_start_freezone_video_mix", fake_enqueue
    )

    result = await freezone_routes.freezone_video_mix(
        project="59",
        body=freezone_routes.FreezoneVideoMixRequest(
            material_urls=[image_url, clip_url],
            subject="  春日露营  ",
            audio_mode="generate",
            bgm_url=bgm_url,
            bgm_volume=0.3,
            aspect_ratio="16:9",
            resolution="1080p",
            clip_duration=4.0,
            transition="none",
            fit_mode="contain",
            emotion_prompt="轻快",
            model="tts-a",
            archive_to_library=True,
        ),
        user={"username": "admin"},
    )

    assert result["ok"] is True
    assert result["data"]["task_type"] == "freezone_video_mix"
    assert captured["material_paths"] == [
        files["image"].as_posix(),
        files["clip"].as_posix(),
    ]
    assert captured["material_kinds"] == ["image", "video"]
    assert captured["subject"] == "春日露营"
    assert captured["bgm_path"] == files["bgm"].as_posix()
    assert captured["bgm_volume"] == 0.3
    assert captured["aspect_ratio"] == "16:9"
    assert captured["resolution"] == "1080p"
    assert captured["clip_duration"] == 4.0
    assert captured["transition"] == "none"
    assert captured["fit_mode"] == "contain"
    assert captured["max_duration_seconds"] is None
    assert captured["emotion_prompt"] == "轻快"
    # 字幕默认不烧录、字号默认 medium（由用户选择是否烧录及字号）。
    assert captured["subtitle_enabled"] is False
    assert captured["subtitle_font_size"] == "medium"
    # 目录 id 在提交前定型为内部模型名；账号声线用户名随 requester 走。
    assert captured["model"] == "newapi-tts-1"
    assert captured["account_voice_username"] == "user-1"
    assert captured["archive_to_library"] is True
    # 配音模式投递时把项目态一并定型（执行方不再回读项目库）。
    assert captured["projection"] == {"voice_registry": {}}
    assert catalog_calls == [("audio", "user-1")]
    assert projection_calls[0]["task_type"] == "freezone_video_mix"
    assert projection_calls[0]["extra_config"] == {"voice_ref": None}


@pytest.mark.asyncio
async def test_video_mix_route_mute_mode_skips_model_and_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _mix_material_files(tmp_path)
    image_url = "/static/admin/59/freezone/_uploads/a.jpg"

    ctx = SimpleNamespace(
        requester_user_id="user-1",
        requester_username="user-1",
        project_id="project_59",
        output_dir=str(tmp_path),
    )
    _patch_mix_route_resolve(monkeypatch, tmp_path, ctx, {image_url: files["image"]})

    async def boom_catalog(*_args, **_kwargs):
        raise AssertionError("mute mode must not read the audio catalog")

    async def boom_projection(**_kwargs):
        raise AssertionError("mute mode must not freeze project projection")

    captured: dict[str, object] = {}

    async def fake_enqueue(**kwargs):
        captured.update(kwargs)
        captured.update(kwargs["payload"])
        return {"ok": True, "data": {"task_type": "freezone_video_mix"}}

    monkeypatch.setattr(freezone_routes, "_scoped_media_model_catalog", boom_catalog)
    monkeypatch.setattr(freezone_routes, "_task_projection_payload", boom_projection)
    monkeypatch.setattr(
        freezone_routes, "_enqueue_or_start_freezone_video_mix", fake_enqueue
    )

    await freezone_routes.freezone_video_mix(
        project="59",
        body=freezone_routes.FreezoneVideoMixRequest(
            material_urls=[image_url],
            audio_mode="mute",
            max_duration_seconds=6.0,
            subtitle_enabled=False,
            subtitle_font_size="large",
        ),
        user={"username": "admin"},
    )

    assert captured["audio_mode"] == "mute"
    assert captured["subject"] == ""
    assert captured["max_duration_seconds"] == 6.0
    assert captured["model"] == ""
    # 显式字号档位原样透传（归一化只兜未知值）。
    assert captured["subtitle_font_size"] == "large"
    assert "projection" not in captured


@pytest.mark.asyncio
async def test_video_mix_route_rejects_invalid_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _mix_material_files(tmp_path)
    image_url = "/static/admin/59/freezone/_uploads/a.jpg"
    bad_url = "/static/admin/59/freezone/_uploads/c.txt"
    ghost_url = "/static/admin/59/freezone/_uploads/ghost.png"

    ctx = SimpleNamespace(
        requester_user_id="user-1",
        requester_username="user-1",
        project_id="project_59",
        output_dir=str(tmp_path),
    )
    _patch_mix_route_resolve(
        monkeypatch,
        tmp_path,
        ctx,
        {
            image_url: files["image"],
            bad_url: files["bad"],
            ghost_url: tmp_path / "ghost.png",
        },
    )

    async def fake_enqueue(**_kwargs):
        return {"ok": True, "data": {"task_type": "freezone_video_mix"}}

    monkeypatch.setattr(
        freezone_routes, "_enqueue_or_start_freezone_video_mix", fake_enqueue
    )

    # generate 模式没有主题也没有文案。
    with pytest.raises(HTTPException) as excinfo:
        await freezone_routes.freezone_video_mix(
            project="59",
            body=freezone_routes.FreezoneVideoMixRequest(material_urls=[image_url]),
            user={"username": "admin"},
        )
    assert excinfo.value.status_code == 400
    assert "subject or script" in excinfo.value.detail

    # 不支持的后缀。
    with pytest.raises(HTTPException) as excinfo:
        await freezone_routes.freezone_video_mix(
            project="59",
            body=freezone_routes.FreezoneVideoMixRequest(
                material_urls=[bad_url], audio_mode="mute"
            ),
            user={"username": "admin"},
        )
    assert excinfo.value.status_code == 400
    assert "unsupported mix material" in excinfo.value.detail

    # 素材不存在。
    with pytest.raises(HTTPException) as excinfo:
        await freezone_routes.freezone_video_mix(
            project="59",
            body=freezone_routes.FreezoneVideoMixRequest(
                material_urls=[ghost_url], audio_mode="mute"
            ),
            user={"username": "admin"},
        )
    assert excinfo.value.status_code == 404

    # 素材池超限。
    with pytest.raises(HTTPException) as excinfo:
        await freezone_routes.freezone_video_mix(
            project="59",
            body=freezone_routes.FreezoneVideoMixRequest(
                material_urls=[image_url] * (freezone_routes._MIX_MAX_MATERIALS + 1),
                audio_mode="mute",
            ),
            user={"username": "admin"},
        )
    assert excinfo.value.status_code == 400
    assert "at most" in excinfo.value.detail


@pytest.mark.asyncio
async def test_video_mix_route_rejects_unknown_audio_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _mix_material_files(tmp_path)
    image_url = "/static/admin/59/freezone/_uploads/a.jpg"

    ctx = SimpleNamespace(
        requester_user_id="user-1",
        requester_username="user-1",
        project_id="project_59",
        output_dir=str(tmp_path),
    )
    _patch_mix_route_resolve(monkeypatch, tmp_path, ctx, {image_url: files["image"]})

    async def fake_catalog(_media_type, *, requester_user_id):
        del requester_user_id
        return []

    monkeypatch.setattr(freezone_routes, "_scoped_media_model_catalog", fake_catalog)

    with pytest.raises(HTTPException) as excinfo:
        await freezone_routes.freezone_video_mix(
            project="59",
            body=freezone_routes.FreezoneVideoMixRequest(
                material_urls=[image_url], subject="主题", model="ghost-tts"
            ),
            user={"username": "admin"},
        )
    assert excinfo.value.status_code == 409
    assert "音频模型" in excinfo.value.detail


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


def _mix_payload(
    project_dir: Path,
    material_paths: list[str],
    material_kinds: list[str],
    **overrides: object,
) -> dict:
    payload: dict[str, object] = {
        "job_id": "mix_job",
        "project_dir": str(project_dir),
        "material_paths": material_paths,
        "material_kinds": material_kinds,
        "subject": "春日露营",
        "script": "",
        "audio_mode": "generate",
        "subtitle_enabled": True,
        "subtitle_font_size": "medium",
        "bgm_path": "",
        "bgm_volume": None,
        "aspect_ratio": "9:16",
        "resolution": "720p",
        "clip_duration": 2.0,
        "transition": "fade",
        "fit_mode": "cover",
        "max_duration_seconds": None,
        "emotion_prompt": "",
        "voice_ref": None,
        "model": "newapi-tts-1",
        "account_voice_username": "",
        "archive_to_library": True,
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_video_mix_runner_generate_chain_plans_and_renders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    image_path = tmp_path / "a.jpg"
    image_path.write_bytes(b"jpg")
    clip_path = tmp_path / "b.mp4"
    clip_path.write_bytes(b"mp4")
    voice_path = tmp_path / "voice.mp3"

    text_calls: list[dict] = []
    speech_calls: list[dict] = []
    render_calls: list[dict] = []
    probe_calls: list[str] = []
    archived: list[dict] = []

    async def fake_generate_text(*, prompt, model=None):
        text_calls.append({"prompt": prompt, "model": model})
        return "DC-text", "春天来了。夏天走了。"

    async def fake_audio_speech(**kwargs):
        speech_calls.append(kwargs)
        return SimpleNamespace(
            audio_path=str(voice_path),
            duration_ms=9000,
            model="newapi-tts-1",
        )

    async def fake_probe(path):
        probe_calls.append(str(path))
        return 4.0

    async def fake_render(**kwargs):
        render_calls.append(kwargs)
        output_path = kwargs["output_path"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"mp4")
        on_progress = kwargs.get("on_progress")
        if on_progress is not None:
            on_progress(0.5, "渲染中...")
        return output_path

    async def fake_make_store(_ctx):
        return None

    task_manager = _TaskManager()
    monkeypatch.setattr(freezone_runner, "get_task_manager", lambda: task_manager)
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.ensure_freezone_dirs", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.run_freezone_video_mix_render", fake_render
    )
    monkeypatch.setattr(
        "novelvideo.freezone.text_node.generate_freezone_text", fake_generate_text
    )
    monkeypatch.setattr(
        "novelvideo.freezone.audio_node.generate_freezone_audio_speech",
        fake_audio_speech,
    )
    monkeypatch.setattr(
        "novelvideo.usage.video_billing.probe_video_duration_seconds", fake_probe
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.make_sqlite_store_for_context", fake_make_store
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.make_static_url_for_context",
        lambda _ctx, rel, **_k: f"/static/{rel}",
    )
    monkeypatch.setattr(
        freezone_runner,
        "_archive_tool_output_to_library",
        lambda **kwargs: archived.append(kwargs),
    )

    envelope = {
        "task_type": "freezone_video_mix",
        "payload": _mix_payload(
            project_dir,
            [image_path.as_posix(), clip_path.as_posix()],
            ["image", "video"],
            model_text="DC-llm-custom",
        ),
    }

    result = await freezone_runner._run_freezone_video_mix_async(
        envelope, _project_context(tmp_path)
    )

    assert result["job_id"] == "mix_job"
    assert result["script"] == "春天来了。夏天走了。"
    assert result["subject"] == "春日露营"
    assert result["audio_mode"] == "generate"
    assert result["narration_duration_ms"] == 9000
    assert result["model"] == "newapi-tts-1"
    assert result["aspect_ratio"] == "9:16"
    assert result["resolution"] == "720p"
    assert result["fit_mode"] == "cover"
    assert result["transition"] == "fade"
    assert result["clip_count"] == 5
    assert result["material_count"] == 2
    assert result["subtitle_enabled"] is True
    assert result["subtitle_font_size"] == "medium"

    # 文案 leaf：主题进 prompt，模型名来自 leaf。
    assert len(text_calls) == 1
    assert "春日露营" in text_calls[0]["prompt"]
    # [aipdd fork delta] 文案生成模型的显式选型原样透传到文本 leaf。
    assert text_calls[0]["model"] == "DC-llm-custom"
    assert result["script_model"] == "DC-text"

    # 配音 leaf：独立 job 号、手写文案直达、目录模型名原样传。
    assert len(speech_calls) == 1
    assert speech_calls[0]["job_id"] == "mix_job_voice"
    assert speech_calls[0]["text"] == "春天来了。夏天走了。"
    assert speech_calls[0]["model"] == "newapi-tts-1"
    assert speech_calls[0]["account_voice_username"] == "user"

    # 探时长只探测视频素材（图片无需探测），其余不触发。
    assert probe_calls == [clip_path.as_posix()]

    # 规划：按配音 9.3s 循环铺满，5 段（图 → 两段视频 → 图 → 视频残段）。
    assert len(render_calls) == 1
    render_call = render_calls[0]
    assert render_call["material_paths"] == [
        image_path.as_posix(),
        clip_path.as_posix(),
    ]
    assert render_call["material_kinds"] == ["image", "video"]
    assert [
        (item["material_index"], round(item["duration"], 3), item["loop"])
        for item in render_call["plan"]
    ] == [
        (0, 2.0, False),
        (1, 2.0, False),
        (1, 2.0, False),
        (0, 2.0, False),
        (1, 1.3, False),
    ]
    assert render_call["canvas_width"] == 720
    assert render_call["canvas_height"] == 1280
    assert render_call["fps"] == 30
    assert render_call["fit_mode"] == "cover"
    assert render_call["transition"] == "fade"
    assert render_call["audio_mode"] == "generate"
    assert render_call["narration_path"] == str(voice_path)
    assert render_call["bgm_path"] is None
    assert render_call["bgm_volume"] is None
    # 字幕 SRT 已按配音时长比例生成并交给渲染（字号档位随 payload 透传）。
    assert "春天来了。" in render_call["subtitle_srt_text"]
    assert "00:00:04,500" in render_call["subtitle_srt_text"]
    assert render_call["subtitle_font_size"] == "medium"

    expected_output = outputs_dir(project_dir, "freezone_video_mix") / "mix_job.mp4"
    assert render_call["output_path"] == expected_output
    assert result["output_path"] == str(expected_output)
    assert (
        result["output_url"]
        == "/static/freezone/_outputs/freezone_video_mix/mix_job.mp4"
    )

    # 归档：主题作条目名（stem），媒体类型视频。
    assert len(archived) == 1
    assert archived[0]["media"] == "video"
    assert archived[0]["task_type"] == "freezone_video_mix"
    assert archived[0]["url"] == result["output_url"]
    assert archived[0]["stem"] == "春日露营"

    # 进度覆盖文案 / 配音 / 规划 / 渲染（含 on_progress 折算挡位）/ 归档。
    progresses = [update["progress"] for update in task_manager.updates]
    assert 0.625 in progresses
    assert 0.97 in progresses
    assert len(task_manager.updates) >= 5


@pytest.mark.asyncio
async def test_video_mix_runner_generate_defaults_to_no_subtitles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[aipdd fork delta] 字幕默认不烧录：未勾选的生成链不产 SRT。"""

    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    image_path = tmp_path / "a.jpg"
    image_path.write_bytes(b"jpg")
    voice_path = tmp_path / "voice.mp3"

    render_calls: list[dict] = []

    async def fake_audio_speech(**_kwargs):
        return SimpleNamespace(
            audio_path=str(voice_path), duration_ms=6000, model="newapi-tts-1"
        )

    async def fake_render(**kwargs):
        render_calls.append(kwargs)
        output_path = kwargs["output_path"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"mp4")
        return output_path

    async def fake_make_store(_ctx):
        return None

    monkeypatch.setattr(freezone_runner, "get_task_manager", lambda: _TaskManager())
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.ensure_freezone_dirs", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.run_freezone_video_mix_render", fake_render
    )
    monkeypatch.setattr(
        "novelvideo.freezone.audio_node.generate_freezone_audio_speech",
        fake_audio_speech,
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.make_sqlite_store_for_context", fake_make_store
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.make_static_url_for_context",
        lambda _ctx, rel, **_k: f"/static/{rel}",
    )
    monkeypatch.setattr(
        freezone_runner, "_archive_tool_output_to_library", lambda **_kwargs: None
    )

    envelope = {
        "task_type": "freezone_video_mix",
        "payload": _mix_payload(
            project_dir,
            [image_path.as_posix()],
            ["image"],
            script="欢迎观看。",
            subtitle_enabled=False,
        ),
    }

    result = await freezone_runner._run_freezone_video_mix_async(
        envelope, _project_context(tmp_path)
    )

    assert result["subtitle_enabled"] is False
    assert result["subtitle_font_size"] == "medium"
    assert render_calls[0]["subtitle_srt_text"] == ""


@pytest.mark.asyncio
async def test_video_mix_runner_mute_chain_skips_voice_and_subtitles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)
    image_path = tmp_path / "a.jpg"
    image_path.write_bytes(b"jpg")
    bgm_path = tmp_path / "bgm.mp3"
    bgm_path.write_bytes(b"mp3")

    render_calls: list[dict] = []

    async def boom_text(**_kwargs):
        raise AssertionError("mute mode must not call the text leaf")

    async def boom_speech(**_kwargs):
        raise AssertionError("mute mode must not call the speech leaf")

    async def fake_render(**kwargs):
        render_calls.append(kwargs)
        output_path = kwargs["output_path"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"mp4")
        return output_path

    monkeypatch.setattr(freezone_runner, "get_task_manager", lambda: _TaskManager())
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.ensure_freezone_dirs", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "novelvideo.freezone.jobs.run_freezone_video_mix_render", fake_render
    )
    monkeypatch.setattr(
        "novelvideo.freezone.text_node.generate_freezone_text", boom_text
    )
    monkeypatch.setattr(
        "novelvideo.freezone.audio_node.generate_freezone_audio_speech", boom_speech
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.make_static_url_for_context",
        lambda _ctx, rel, **_k: f"/static/{rel}",
    )
    monkeypatch.setattr(
        freezone_runner, "_archive_tool_output_to_library", lambda **_kwargs: None
    )

    envelope = {
        "task_type": "freezone_video_mix",
        "payload": _mix_payload(
            project_dir,
            [image_path.as_posix()],
            ["image"],
            audio_mode="mute",
            subject="",
            script="",
            max_duration_seconds=6.0,
            bgm_path=bgm_path.as_posix(),
        ),
    }

    result = await freezone_runner._run_freezone_video_mix_async(
        envelope, _project_context(tmp_path)
    )

    assert result["audio_mode"] == "mute"
    assert result["clip_count"] == 3
    assert result["narration_duration_ms"] == 0
    assert result["subtitle_enabled"] is False

    # 静音按 max_duration：单图 2s 一段循环 3 次铺满 6s；无配音、无字幕、BGM 直通。
    assert len(render_calls) == 1
    render_call = render_calls[0]
    assert [
        (item["material_index"], round(item["duration"], 3))
        for item in render_call["plan"]
    ] == [(0, 2.0), (0, 2.0), (0, 2.0)]
    assert render_call["narration_path"] is None
    assert render_call["bgm_path"] == bgm_path.as_posix()
    assert render_call["bgm_volume"] is None
    assert render_call["subtitle_srt_text"] == ""


@pytest.mark.asyncio
async def test_video_mix_runner_rejects_missing_material(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.task_backend.runners import freezone as freezone_runner

    project_dir = tmp_path / "output"
    project_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        "novelvideo.freezone.jobs.ensure_freezone_dirs", lambda *_a, **_k: None
    )

    envelope = {
        "task_type": "freezone_video_mix",
        "payload": _mix_payload(
            project_dir, [str(tmp_path / "ghost.jpg")], ["image"]
        ),
    }

    with pytest.raises(FileNotFoundError, match="mix material not found"):
        await freezone_runner._run_freezone_video_mix_async(
            envelope, _project_context(tmp_path)
        )