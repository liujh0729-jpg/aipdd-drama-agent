"""[aipdd fork delta req46] MiniMax 直连（V2 视频协议）的回归。

钉住五件事（对应官方 V2 契约的四处差异 + 一条通道边界）：

1. 判定顺序与 :meth:`_uses_aipdd_videos_api` 同形：provider（设置页的确定事实）
   → 渠道目录声明（声明了就按声明走，不猜）→ host 兜底（官方域名，自建代理用
   ``MINIMAX_VIDEO_API_HOST_HINT`` 覆盖）。
2. 档位换算按上游事实收口：分辨率 H3 = 768P / 2K、快生成变体只到 768P（**2K 越界
   当场拦住**，不让上游默默降档）；时长标准 4–15、快生成 5–15；画幅首尾帧恒
   adaptive、纯文必须给具体值。型号名一律不写死（req40 口径），只按名字分段分档。
3. ``content[]`` 按模式装配：文生 / 首尾帧（first_frame + last_frame）/ 参考生成
   （reference_image / reference_video / reference_audio），**帧角色与参考角色互斥**、
   参考数量超限截断、缺提示词与超字数在提交前就报错。
4. 任务体翻译：``task.content.url`` → 扁平 ``result_url``（复用既有下载/记账/日志
   链路），``task.error.{code,message}`` → 一条带上游原话的失败原因，
   queued / running 原样留给「继续等」分支。
5. 素材递交两条通道：先复用媒体中转公网直链，拿不到才回落 MiniMax 素材库
   ``mm_file://{file_id}``；两条都不通时报错必须同时带上两边原因（延续「失败提示
   必须是详细诊断」的口径），绝不静默丢素材出一条不相干的片。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo.generators.video_generator import NewApiVideoGenerator as G
from novelvideo.i18n_message import log_lines_text
from novelvideo.generators.video_generator.backends import newapi as newapi_backend

@pytest.fixture(autouse=True)
def _catalog_says_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认让目录「没说话」，判定回到 provider / host 那两级；单独用例再验声明优先。

    [aipdd fork delta req46] 参考上限也是目录驱动的，不放平就会读到开跑环境的
    真实目录快照，同一用例在不同机器上会得出不同答案（零网络零本地状态是本仓口径）。
    """

    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.catalog_endpoints", lambda *a, **k: []
    )
    monkeypatch.setattr(newapi_backend, "_aipdd_video_input_caps", lambda *_a, **_kw: {})
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.upstream_catalog_entry",
        lambda *a, **k: None,
    )


def _bare(model: str) -> G:
    """不经 __init__ 的裸实例：只测纯函数面（判定 / 档位 / 建单体 / 任务体翻译）。"""

    gen = G.__new__(G)
    gen.model = model
    gen.resolution = "768P"
    gen.channel_provider = ""
    return gen


# ---------------------------------------------------------------------------
# 1. 判定
# ---------------------------------------------------------------------------


def test_provider_is_the_strongest_signal() -> None:
    assert G._uses_minimax_videos_v2("", "MiniMax-H3", "minimax") is True
    assert G._uses_minimax_videos_v2("", "MiniMax-H3", "minimax_cn") is True
    assert G._uses_minimax_videos_v2("", "MiniMax-H3", "volcengine") is False


def test_declared_catalog_endpoints_win_over_host_guess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """目录声明了端点 = 网关按声明代理该协议，host 像 MiniMax 也不许改道直连。"""

    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.catalog_endpoints",
        lambda name, provider="": ["video/generations"],
    )
    assert (
        G._uses_minimax_videos_v2("https://api.minimax.io/v1", "MiniMax-H3", "") is False
    )


@pytest.mark.parametrize(
    "base_url,expected",
    [
        ("https://api.minimax.io/v1", True),
        ("https://api.minimax.cn/v1", True),
        ("https://api.minimaxi.com/v1", True),
        ("https://gateway.example/v1", False),
        ("", False),
    ],
)
def test_host_fallback(base_url: str, expected: bool) -> None:
    assert G._uses_minimax_videos_v2(base_url, "MiniMax-H3", "") is expected


def test_self_hosted_proxy_uses_env_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自建代理域名默认不算 MiniMax；给了提示就按提示走（与 AIPDD 那条同款口径）。"""

    monkeypatch.delenv("MINIMAX_VIDEO_API_HOST_HINT", raising=False)
    assert (
        G._uses_minimax_videos_v2("https://mm.example.com/v1", "MiniMax-H3", "") is False
    )
    monkeypatch.setenv("MINIMAX_VIDEO_API_HOST_HINT", "mm.example")
    assert (
        G._uses_minimax_videos_v2("https://mm.example.com/v1", "MiniMax-H3", "") is True
    )


# ---------------------------------------------------------------------------
# 2. 档位
# ---------------------------------------------------------------------------


def test_2k_is_only_available_on_the_standard_tier() -> None:
    assert _bare("MiniMax-H3")._minimax_v2_resolution("2K") == "2K"
    with pytest.raises(ValueError) as raised:
        _bare("MiniMax-H3-Max")._minimax_v2_resolution("2K")
    message = str(raised.value)
    assert "2K" in message and "768P" in message


@pytest.mark.parametrize("requested", ["720p", "1080P", "1080p", "", "weird"])
def test_unknown_resolution_falls_to_768p(requested: str) -> None:
    assert _bare("MiniMax-H3")._minimax_v2_resolution(requested) == "768P"
    assert _bare("MiniMax-H3-Max")._minimax_v2_resolution(requested) == "768P"


def test_480p_only_survives_on_the_fast_tier() -> None:
    # 标准档没 480P 这一档，落 768P；快生成变体保留。
    assert _bare("MiniMax-H3")._minimax_v2_resolution("480p") == "768P"
    assert _bare("MiniMax-H3-Max")._minimax_v2_resolution("480p") == "480P"


def test_duration_bounds_per_tier() -> None:
    assert _bare("MiniMax-H3")._minimax_v2_duration(4) == 4
    assert _bare("MiniMax-H3-Max")._minimax_v2_duration(5) == 5
    with pytest.raises(ValueError, match="4–15"):
        _bare("MiniMax-H3")._minimax_v2_duration(3)
    with pytest.raises(ValueError, match="5–15"):
        _bare("MiniMax-H3-Max")._minimax_v2_duration(4)
    with pytest.raises(ValueError, match="16"):
        _bare("MiniMax-H3")._minimax_v2_duration(16)


def test_vendor_name_alone_is_not_the_fast_tier() -> None:
    """「MiniMax」本身就含 max：分段判断漏了会把标准 H3 误降成 768P 上限。"""

    assert G._is_minimax_h3_max_model("MiniMax-H3") is False
    assert G._is_minimax_h3_max_model("MiniMax-H3-Max") is True


@pytest.mark.parametrize(
    "requested,frame_roles,has_media,expected",
    [
        ("16:9", True, True, newapi_backend.MINIMAX_V2_RATIO_ADAPTIVE),
        ("16:9", False, False, "16:9"),
        ("adaptive", False, False, "9:16"),
        ("", False, True, newapi_backend.MINIMAX_V2_RATIO_ADAPTIVE),
        ("4x3", False, False, "9:16"),
    ],
)
def test_ratio_rules(
    requested: str, frame_roles: bool, has_media: bool, expected: str
) -> None:
    assert (
        G._minimax_v2_ratio(requested, frame_roles=frame_roles, has_media=has_media)
        == expected
    )


# ---------------------------------------------------------------------------
# 3. content[] 建单体
# ---------------------------------------------------------------------------


def test_text_to_video_payload_has_only_text() -> None:
    body = _bare("MiniMax-H3")._build_minimax_v2_payload(
        prompt="雨夜霓虹",
        duration=6,
        resolution="768P",
        ratio="9:16",
        media={},
    )
    assert body == {
        "model": "MiniMax-H3",
        "content": [{"type": "text", "text": "雨夜霓虹"}],
        "resolution": "768P",
        "duration": 6,
        "ratio": "9:16",
    }


def test_first_last_frame_payload_and_adaptive_ratio() -> None:
    body = _bare("MiniMax-H3")._build_minimax_v2_payload(
        prompt="从 A 走到 B",
        duration=8,
        resolution="2K",
        ratio="16:9",
        media={
            "first_frame": "https://cdn.example/a.png",
            "last_frame": "https://cdn.example/b.png",
        },
    )
    assert [item["type"] for item in body["content"]] == [
        "text",
        "image_url",
        "image_url",
    ]
    assert [item["role"] for item in body["content"][1:]] == ["first_frame", "last_frame"]
    assert body["content"][1]["image_url"] == {"url": "https://cdn.example/a.png"}
    # 帧模式的画幅由图片决定，用户选的 16:9 必须被换掉（上游会忽略别的值）。
    assert body["ratio"] == newapi_backend.MINIMAX_V2_RATIO_ADAPTIVE
    assert body["resolution"] == "2K"


def test_reference_payload_carries_all_three_roles() -> None:
    body = _bare("MiniMax-H3-Max")._build_minimax_v2_payload(
        prompt="按这位角色演",
        duration=5,
        resolution="768P",
        ratio="16:9",
        media={
            "reference_images": ["https://cdn.example/p1.png"],
            "reference_videos": ["mm_file://9001"],
            "reference_audios": ["https://cdn.example/voice.mp3"],
        },
    )
    assert [(item["type"], item["role"]) for item in body["content"][1:]] == [
        ("image_url", "reference_image"),
        ("video_url", "reference_video"),
        ("audio_url", "reference_audio"),
    ]
    assert body["content"][2]["video_url"] == {"url": "mm_file://9001"}
    assert body["duration"] == 5


def test_reference_counts_beyond_upstream_limits_are_refused_not_truncated() -> None:
    """[req46] 单类超上游契约上限是**拒单摊数字**，不是默默截掉。

    截断就是丢素材：递 12 张图只递进去 9 张，用户看到的是一条「我明明挂了 12 张」的
    片子还照常计费（req44 同一个口径）。两类都不超总闸（12）也要逐类拒得下来。
    """

    gen = _bare("MiniMax-H3")
    with pytest.raises(ValueError, match="参考图片给了 12 个") as exc:
        gen._build_minimax_v2_payload(
            prompt="多图参考",
            duration=6,
            resolution="768P",
            ratio="adaptive",
            media={
                "reference_images": [f"https://cdn.example/{i}.png" for i in range(12)],
                "reference_videos": [f"https://cdn.example/{i}.mp4" for i in range(3)],
            },
        )
    assert "只收 9 个" in str(exc.value)
    # 没申报时数字出自上游契约，报错要把出处一起标出来（否则用户会去改渠道申报）。
    assert "上游契约" in str(exc.value)

    with pytest.raises(ValueError, match="参考音频给了 5 个") as exc:
        gen._build_minimax_v2_payload(
            prompt="多音频参考",
            duration=6,
            resolution="768P",
            ratio="adaptive",
            media={
                "reference_images": [f"https://cdn.example/{i}.png" for i in range(9)],
                "reference_audios": [f"https://cdn.example/{i}.mp3" for i in range(5)],
            },
        )
    assert "只收 3 个" in str(exc.value)

    # 刚好在限内的逐条不丢（旧实现在这里会把第 2、第 3 条默默丢掉）。
    body = gen._build_minimax_v2_payload(
        prompt="满配但不超闸",
        duration=6,
        resolution="768P",
        ratio="adaptive",
        media={
            "reference_images": [f"https://cdn.example/{i}.png" for i in range(9)],
            "reference_videos": [f"https://cdn.example/{i}.mp4" for i in range(3)],
        },
    )
    roles = [item["role"] for item in body["content"][1:]]
    assert roles.count("reference_image") == newapi_backend.MINIMAX_V2_MAX_REFERENCE_IMAGES
    assert roles.count("reference_video") == newapi_backend.MINIMAX_V2_MAX_REFERENCE_VIDEOS


def test_mixed_reference_beyond_the_total_cap_is_refused_not_dropped() -> None:
    """[req46] 混投合计超上游总闸（12）要拒单，不能静默丢掉排在后面的音频 / 视频。

    三类单看都在限内（9 + 3 + 3 = 15）依然递不进去：上游对参考入口写的是「混投
    合计 ≤ 12 个」（AIPDD ``/v1/models`` 同期写成 ``maxReferenceInputs: 12``）。静默截到 12
    会整批丢掉音频，用户只会看到出一条与自己给的素材无关的片子。
    """

    with pytest.raises(ValueError, match="混投上限 12") as exc:
        _bare("MiniMax-H3")._build_minimax_v2_payload(
            prompt="满配参考",
            duration=6,
            resolution="768P",
            ratio="adaptive",
            media={
                "reference_images": [f"https://cdn.example/{i}.png" for i in range(9)],
                "reference_videos": [f"https://cdn.example/{i}.mp4" for i in range(3)],
                "reference_audios": [f"https://cdn.example/{i}.mp3" for i in range(3)],
            },
        )
    # 报错必须把三个数字摊开，不然用户不知道该删哪一类。
    assert "参考图片 9 / 参考视频 3 / 参考音频 3" in str(exc.value)
    # 渠道没申报时，这道闸的出处是上游契约，不是渠道目录（两者要做的事不同）。
    assert "上游 MiniMax 契约" in str(exc.value)


def test_reference_limits_prefer_channel_declaration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[req46] 上限以渠道目录申报为准：申报比契约低就按低的拒，明说 0 就当场拒。

    两道意义不同的断言一起钉：① 数字要从 caps 来（不再拿契约常量覆盖渠道申报）；
    ② 报错里的出处要说对 —— 被渠道申报拦下时让用户去改申报，被契约拦下时只能删素材。
    总闸也从同一份 caps 读（以前它绕过 caps 二次查表且只认 >0）。
    """

    seen: list[tuple[str, str]] = []

    def _caps(_m: str, provider: str = "") -> dict[str, int | None]:
        seen.append(("minimax", provider))
        return {
            "max_image_inputs": 3,
            "max_video_inputs": 0,
            "max_audio_inputs": 2,
            # 总闸故意比单类上限之和（3 + 2）低：不然单类闸会先抢掉，测不到总闸那一句。
            "max_reference_inputs": 4,
        }

    monkeypatch.setattr(newapi_backend, "_aipdd_video_input_caps", _caps)
    inst = _bare("MiniMax-H3")
    with pytest.raises(ValueError, match="不支持参考视频"):
        inst._build_minimax_v2_payload(
            prompt="递视频",
            duration=6,
            resolution="768P",
            ratio="adaptive",
            media={"reference_videos": ["https://cdn.example/a.mp4"]},
        )
    # 单类按申报值拒（契约说 9 / 2，渠道只给 3 图 2 音频）。
    with pytest.raises(ValueError, match="参考图片给了 9 个") as exc:
        inst._build_minimax_v2_payload(
            prompt="递 9 图",
            duration=6,
            resolution="768P",
            ratio="adaptive",
            media={
                "reference_images": [f"https://cdn.example/{i}.png" for i in range(9)],
            },
        )
    assert "渠道目录" in str(exc.value)
    # 混投总闸也听申报：申报 4，3 图 + 1 音频 = 4 递得进去，3 图 + 2 音频 = 5 就要拒。
    body = inst._build_minimax_v2_payload(
        prompt="只递图和音频",
        duration=6,
        resolution="768P",
        ratio="adaptive",
        media={
            "reference_images": [f"https://cdn.example/{i}.png" for i in range(3)],
            "reference_audios": [f"https://cdn.example/{i}.mp3" for i in range(1)],
        },
    )
    kinds = [item["role"] for item in body["content"][1:]]
    assert kinds.count("reference_image") == 3
    assert kinds.count("reference_audio") == 1
    with pytest.raises(ValueError, match="混投上限 4"):
        inst._build_minimax_v2_payload(
            prompt="超渠道总闸",
            duration=6,
            resolution="768P",
            ratio="adaptive",
            media={
                "reference_images": [f"https://cdn.example/{i}.png" for i in range(3)],
                "reference_audios": [f"https://cdn.example/{i}.mp3" for i in range(2)],
            },
        )
    # provider 一路递到查目录（海外 / 国内两条同名渠道不能串读）。
    assert seen and all(item[1] == "" for item in seen)


def test_frame_roles_and_reference_roles_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="互斥"):
        _bare("MiniMax-H3")._build_minimax_v2_payload(
            prompt="首帧加参考",
            duration=6,
            resolution="768P",
            ratio="16:9",
            media={
                "first_frame": "https://cdn.example/a.png",
                "reference_images": ["https://cdn.example/p1.png"],
            },
        )


def test_prompt_is_required_and_bounded() -> None:
    gen = _bare("MiniMax-H3")
    with pytest.raises(ValueError, match="提示词"):
        gen._build_minimax_v2_payload(
            prompt="   ",
            duration=6,
            resolution="768P",
            ratio="16:9",
            media={},
        )
    with pytest.raises(ValueError, match="7000"):
        gen._build_minimax_v2_payload(
            prompt="字" * (newapi_backend.MINIMAX_V2_TEXT_MAX_CHARS + 1),
            duration=6,
            resolution="768P",
            ratio="16:9",
            media={},
        )


# ---------------------------------------------------------------------------
# 4. 任务体翻译
# ---------------------------------------------------------------------------


def test_task_view_maps_succeeded_content_url() -> None:
    view = G._minimax_v2_task_view(
        {
            "task": {
                "id": "mm-task-1",
                "model": "MiniMax-H3",
                "status": "succeeded",
                "content": {"url": "https://cdn.minimax.io/out.mp4"},
                "resolution": "768P",
                "duration": 6,
                "ratio": "16:9",
            }
        }
    )
    assert view["status"] == "succeeded"
    assert view["result_url"] == "https://cdn.minimax.io/out.mp4"
    assert "error" not in view
    # 扁平形状正好喂给既有的 _extract_video_url / 记账链路
    assert G._extract_video_url(view) == "https://cdn.minimax.io/out.mp4"


def test_task_view_surfaces_upstream_error_verbatim() -> None:
    view = G._minimax_v2_task_view(
        {
            "task": {
                "id": "mm-task-1",
                "status": "failed",
                "error": {"code": "1002", "message": "insufficient balance"},
            }
        }
    )
    assert view["status"] == "failed"
    assert view["error"] == "MiniMax 任务失败（code=1002）：insufficient balance"


@pytest.mark.parametrize("status", ["queued", "running"])
def test_task_view_keeps_inflight_status(status: str) -> None:
    view = G._minimax_v2_task_view({"task": {"status": status}})
    assert view == {"status": status}


# ---------------------------------------------------------------------------
# 5. 素材通道
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_url_passes_through_without_any_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.freezone import minimax_api

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("公网直链不该再中转一次")

    monkeypatch.setattr(minimax_api, "upload_file", forbidden)
    url = await _bare("MiniMax-H3")._minimax_v2_media_url(
        "https://cdn.example/a.png",
        kind="image",
        label="首帧",
        channel=None,
        log=lambda _m: None,
    )
    assert url == "https://cdn.example/a.png"


@pytest.mark.asyncio
async def test_relay_failure_falls_back_to_material_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.freezone import minimax_api

    gen = _bare("MiniMax-H3")

    async def broken_relay(*_args, **_kwargs):
        raise RuntimeError("媒体中转未配置")

    async def fake_upload(_channel, path, **_kwargs):
        assert isinstance(path, Path)
        return "9100"

    logs: list[str] = []
    monkeypatch.setattr(gen, "_relay_frame_input", broken_relay)
    monkeypatch.setattr(minimax_api, "upload_file", fake_upload)

    url = await gen._minimax_v2_media_url(
        "/tmp/a.png", kind="image", label="首帧", channel=object(), log=logs.append
    )
    assert url == "mm_file://9100"
    # [aipdd fork delta req46] 上屏文案走 i18n：断言必须落在「中文兜底 + code + 插值
    # 参数」三件事上，只比对整串中文会让换语言时静默失守。
    assert log_lines_text(logs) == ["首帧已上传到 MiniMax 素材库"]
    assert logs[0].code == "tasks.log.video.minimaxFileUploaded"
    assert dict(logs[0].params or {}) == {"label": "首帧"}


@pytest.mark.asyncio
async def test_both_channels_failing_reports_both_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.freezone import minimax_api

    gen = _bare("MiniMax-H3")

    async def broken_relay(*_args, **_kwargs):
        raise RuntimeError("媒体中转未配置")

    async def broken_upload(*_args, **_kwargs):
        raise ValueError("HTTP 401 invalid api key")

    monkeypatch.setattr(gen, "_relay_media_input", broken_relay)
    monkeypatch.setattr(minimax_api, "upload_file", broken_upload)

    with pytest.raises(ValueError) as raised:
        await gen._minimax_v2_media_url(
            "/tmp/a.mp4",
            kind="video",
            label="参考视频",
            channel=object(),
            log=lambda _m: None,
        )
    message = str(raised.value)
    assert "媒体中转未配置" in message
    assert "HTTP 401 invalid api key" in message


@pytest.mark.asyncio
async def test_mode_routing_into_role_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    """模式 → 角色槽：单图即首帧、多图按参考、帧槽从参考里摘掉同一张图。"""

    gen = _bare("MiniMax-H3")

    async def passthrough(value, **_kwargs):
        return str(value)

    monkeypatch.setattr(gen, "_minimax_v2_media_url", passthrough)

    class _Ref:
        def __init__(self, path: str, media_type: str = "image") -> None:
            self.path = path
            self.type = media_type
            self.role = ""

    single = await gen._collect_minimax_v2_media(
        mode="imageReference",
        image_path="",
        last_frame_path=None,
        references=[_Ref("https://cdn.example/only.png")],
        channel=object(),
        log=lambda _m: None,
    )
    assert single == {"first_frame": "https://cdn.example/only.png"}

    multi = await gen._collect_minimax_v2_media(
        mode="allReference",
        image_path="https://cdn.example/first.png",
        last_frame_path="https://cdn.example/last.png",
        references=[_Ref("https://cdn.example/p.png"), _Ref("https://cdn.example/v.mp4", "video")],
        channel=object(),
        log=lambda _m: None,
    )
    assert multi["first_frame"] == "https://cdn.example/first.png"
    assert multi["last_frame"] == "https://cdn.example/last.png"
    assert multi["reference_images"] == ["https://cdn.example/p.png"]
    assert multi["reference_videos"] == ["https://cdn.example/v.mp4"]

    empty = await gen._collect_minimax_v2_media(
        mode="textToVideo",
        image_path="https://cdn.example/ignored.png",
        last_frame_path=None,
        references=[],
        channel=object(),
        log=lambda _m: None,
    )
    assert empty == {}

    with pytest.raises(ValueError, match="不支持的提交模式"):
        await gen._collect_minimax_v2_media(
            mode="cameraControl",
            image_path="",
            last_frame_path=None,
            references=[],
            channel=object(),
            log=lambda _m: None,
        )


# ---------------------------------------------------------------------------
# 6. generate() 端到端（零网络：只打桩 _post_json / _get_json / _download_video）
# ---------------------------------------------------------------------------


def _v2_generator(model: str = "MiniMax-H3") -> G:
    from novelvideo.generators.video_generator import NewApiVideoGenerator

    return NewApiVideoGenerator(
        api_key="mm-key",
        endpoint="https://api.minimax.io/v1",
        model=model,
        resolution="768P",
    )


@pytest.mark.asyncio
async def test_generate_submits_and_polls_v2_paths(monkeypatch, tmp_path: Path) -> None:
    from novelvideo.generators import video_generator as module
    from novelvideo.generators.video_generator import VideoGenStatus
    from novelvideo.ports.video_delivery import VideoDeliveryReceipt

    generator = _v2_generator()
    seen: dict[str, object] = {}

    async def post(url, payload, *, headers=None):
        seen["submit_url"] = url
        seen["submit_body"] = payload
        seen["headers"] = headers
        return {"task_id": "mm-task-1"}

    async def poll(url, *, headers=None):
        seen["poll_url"] = url
        return {
            "task": {
                "id": "mm-task-1",
                "status": "succeeded",
                "content": {"url": "https://cdn.minimax.io/out.mp4"},
            }
        }

    async def download(url, output_path):
        seen["download_url"] = url
        Path(output_path).write_bytes(b"mm-video")
        return VideoDeliveryReceipt(method="download", size=8, sha256="0" * 64)

    monkeypatch.setattr(generator, "_post_json", post)
    monkeypatch.setattr(generator, "_get_json", poll)
    monkeypatch.setattr(generator, "_download_video", download)
    monkeypatch.setattr(module, "get_video_result_delivery", lambda: None)

    async def _noop_record(**_kwargs):
        return None

    monkeypatch.setattr(module, "_record_video_model_call", _noop_record)

    result = await generator.generate(
        image_path=None,
        prompt="雨夜霓虹下的街头",
        output_path=str(tmp_path / "video.mp4"),
        duration=6,
        aspect_ratio="16:9",
        poll_interval=0,
        max_polls=2,
    )

    assert result.status is VideoGenStatus.DONE
    assert result.video_url == "https://cdn.minimax.io/out.mp4"
    # 站点根 + V2 端点：不能拼成 /v1/v2/...，也不能走网关的 /video/generations
    assert seen["submit_url"] == "https://api.minimax.io/v2/video_generation"
    assert seen["poll_url"] == "https://api.minimax.io/v2/query/video_generation/mm-task-1"
    assert seen["submit_body"] == {
        "model": "MiniMax-H3",
        "content": [{"type": "text", "text": "雨夜霓虹下的街头"}],
        "resolution": "768P",
        "duration": 6,
        "ratio": "16:9",
    }
    # 平台侧任务不覆盖请求头（回落实例自身的 Bearer），组织信封任务才另取 Key
    assert seen["headers"] is None
    assert generator.headers["Authorization"] == "Bearer mm-key"


@pytest.mark.asyncio
async def test_generate_reports_upstream_task_error(monkeypatch, tmp_path: Path) -> None:
    from novelvideo.generators import video_generator as module
    from novelvideo.generators.video_generator import VideoGenStatus

    generator = _v2_generator()

    async def post(_url, _payload, *, headers=None):
        return {"task_id": "mm-task-2"}

    async def poll(_url, *, headers=None):
        return {
            "task": {
                "id": "mm-task-2",
                "status": "failed",
                "error": {"code": "1026", "message": "InputTextSensitiveContentDetected"},
            }
        }

    monkeypatch.setattr(generator, "_post_json", post)
    monkeypatch.setattr(generator, "_get_json", poll)
    monkeypatch.setattr(module, "get_video_result_delivery", lambda: None)

    result = await generator.generate(
        image_path=None,
        prompt="测试",
        output_path=str(tmp_path / "video.mp4"),
        duration=6,
        poll_interval=0,
        max_polls=1,
    )

    assert result.status is VideoGenStatus.FAILED
    assert "InputTextSensitiveContentDetected" in result.error
    assert result.task_id == "mm-task-2"


@pytest.mark.asyncio
async def test_generate_blocks_out_of_tier_before_touching_upstream(
    monkeypatch, tmp_path: Path
) -> None:
    """档位越界是本地校验：一个上游请求都不能发出去（提交前拦住，不派注定废的任务）。"""

    from novelvideo.generators import video_generator as module
    from novelvideo.generators.video_generator import VideoGenStatus

    generator = _v2_generator("MiniMax-H3-Max")
    generator.resolution = "2K"

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("提交前就该拦住，不该发上游请求")

    monkeypatch.setattr(generator, "_post_json", forbidden)
    monkeypatch.setattr(generator, "_get_json", forbidden)
    monkeypatch.setattr(module, "get_video_result_delivery", lambda: None)

    result = await generator.generate(
        image_path=None,
        prompt="测试",
        output_path=str(tmp_path / "video.mp4"),
        duration=6,
        poll_interval=0,
        max_polls=1,
    )

    assert result.status is VideoGenStatus.FAILED
    assert "2K" in result.error


@pytest.mark.asyncio
async def test_gateway_channel_is_not_rerouted_to_v2(monkeypatch, tmp_path: Path) -> None:
    """非 MiniMax 渠道的行为必须与 req46 之前一致：仍走网关 /video/generations。"""

    from novelvideo.generators import video_generator as module
    from novelvideo.generators.video_generator import NewApiVideoGenerator, VideoGenStatus
    from novelvideo.ports.video_delivery import VideoDeliveryReceipt

    generator = NewApiVideoGenerator(
        api_key="gw-key",
        endpoint="https://gateway.example/v1",
        model="some-video-model",
    )
    seen: dict[str, str] = {}

    async def post(url, payload, *, headers=None):
        seen["submit_url"] = url
        seen["body"] = str(sorted(payload.keys()))
        return {"id": "gw-task-1"}

    async def poll(_url, *, headers=None):
        return {
            "data": {
                "status": "succeeded",
                "result_url": "https://cdn.example/out.mp4",
            }
        }

    async def download(url, output_path):
        Path(output_path).write_bytes(b"x")
        return VideoDeliveryReceipt(method="download", size=1, sha256="0" * 64)

    monkeypatch.setattr(generator, "_post_json", post)
    monkeypatch.setattr(generator, "_get_json", poll)
    monkeypatch.setattr(generator, "_download_video", download)
    monkeypatch.setattr(module, "get_video_result_delivery", lambda: None)

    async def _noop_record(**_kwargs):
        return None

    monkeypatch.setattr(module, "_record_video_model_call", _noop_record)

    result = await generator.generate(
        image_path=None,
        prompt="测试",
        output_path=str(tmp_path / "video.mp4"),
        poll_interval=0,
        max_polls=1,
    )

    assert result.status is VideoGenStatus.DONE
    assert seen["submit_url"] == "https://gateway.example/v1/video/generations"
    assert "metadata" in seen["body"]
