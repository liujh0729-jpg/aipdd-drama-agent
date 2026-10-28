"""[aipdd fork delta] AIPDD 视频走 OpenAI 风格 /v1/videos 协议的回归。

钉住四件事：
1. 按渠道 host 精确判定是否启用 /videos 协议（LocalAPI 的 /video/generations 在
   AIPDD 上会 404「资源不存在」，二者必须走不同路径）。
2. create body 按 AIPDD 公布的通用视频契约组装：时长/分辨率同时给新名
   ``duration_seconds`` / ``video_resolution``（MiniMax H3 文档红线：禁止只用
   ``duration`` / ``resolution`` 旧字段）与旧名（本地网关按旧名绑定计费/校验）；
   画幅字段名是顶层 ratio（AIPDD 对 aspect_ratio 静默忽略，2026-09-28 逐字段实测）；
   ratio=auto 时不下发 ratio。
   [req44] 图像素材按提交模式**分槽**：图生视频 / 首帧 / 首尾帧递 ``first_frame`` /
   ``last_frame`` 专用字段并带 ``mode=first_last_frame_to_video``，文生视频
   ``mode=text_to_video`` 且不递素材，全能参考 ``mode=ref2va`` 走 ``image_urls``；未传
   模式（历史调用面、视频编辑 / 延长）保持首帧+参考+尾帧收敛进 ``image_urls`` 且去重保序。
   **限定**（用户 2026-10-08：「我说的是 minimax 模型，seedance 该怎样就怎样」）：这套
   分槽 + 选择子只在「渠道自己的绑定名册里同一模型名下有重复 taskKind（分不清该跑哪
   条）」时才生效；LTX / Seedance / 没读成名册的模型建单体与 req44 之前逐字节一致。
3. [req42] 参考素材不得被丢：源视频 → ``video_urls``（复数；[req46] 2026-10-08 实测更正 ——
   单值 ``video_url`` 不在这个渠道的字段表里，递进去会被静默忽略并照样扣费），参考音频 →
   ``audio_urls``。「替换视频人物/产品」「对口型」此前只发 image_urls，产物与原视频无关。
4. 参考素材的数量与可否使用以渠道目录声明为准：**申报 0 则报错、超限一律拒单并摊开数字
   （不静默截断 —— 那是丢素材不是拒单，req46）**，目录没说话时不自锁；混投合计还要过
   渠道的 ``maxReferenceInputs`` 总闸。
5. 参考视频/音频必须是公网 http(s) 直链：上游对 data URL / 不可达直链一律 202
   受理、生成阶段才失败，所以提交前就报错（参考图不受此限）。
6. [req42] 建单体在「已成功提交」标记之前拼装并把**真正发出的 body** 记进调用日志。
7. 轮询完成后从 content_url 取结果视频。
"""

from __future__ import annotations

import inspect

import pytest

from novelvideo.generators.video_generator import NewApiVideoGenerator as G
from novelvideo.generators.video_generator.backends import newapi as newapi_backend


def _no_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认让拼装不看目录配额，单独的用例再验配额本身。"""

    monkeypatch.setattr(newapi_backend, "_aipdd_video_input_caps", lambda *_a, **_kw: {})


# [req44] 渠道在线绑定名册缩样（AIPDD /v1/capabilities 实况）：H3 的三条里两条的
# taskKind 被渠道写成同一个 ``first_last_frame_to_video``，入参面板也一模一样 —— 只有
# 这种「渠道自己分不清」的场合才轮到我们把模式说出来。
_H3_BINDINGS = [
    {
        "code": "aipdd_minimax_h3_t2v_speed_test",
        "name": "MiniMax H3 T2V Speed",
        "taskKind": "text_to_video",
        "uiTypes": ["number", "textarea"],
        "inputModalities": ["text"],
        "paramNames": ["提示词"],
        "available": True,
    },
    {
        "code": "aipdd_minimax_h3_first_last_frame_speed_test",
        "name": "MiniMax H3 首尾帧 Speed",
        "taskKind": "first_last_frame_to_video",
        "uiTypes": ["image_url", "textarea"],
        "inputModalities": ["text", "image"],
        "paramNames": ["首帧图片", "尾帧图片"],
        "available": True,
    },
    {
        "code": "aipdd_minimax_h3_ref2va_test",
        "name": "MiniMax H3 参考图生视频",
        "taskKind": "first_last_frame_to_video",
        "uiTypes": ["image_url", "textarea"],
        "inputModalities": ["text", "image"],
        "paramNames": ["首帧图片", "尾帧图片"],
        "available": True,
    },
]

# LTX 2.5 名下是 ``text_to_video`` + ``image_to_video`` 两条互不重名的绑定：渠道光按
# 输入形状就能路由，不需要也不允许我们替它改提交形状。
_LTX_BINDINGS = [
    {
        "code": "aipdd_ltx_2_5_t2v_test",
        "name": "LTX 2.5 文生视频",
        "taskKind": "text_to_video",
        "uiTypes": ["textarea"],
        "inputModalities": ["text"],
        "paramNames": ["提示词"],
        "available": True,
    },
    {
        "code": "aipdd_ltx_2_5_i2v_test",
        "name": "LTX 2.5 图生视频",
        "taskKind": "image_to_video",
        "uiTypes": ["image_url"],
        "inputModalities": ["text", "image"],
        "paramNames": ["图片"],
        "available": True,
    },
]


def _roster(monkeypatch: pytest.MonkeyPatch, bindings: list[dict[str, object]]) -> None:
    """把「该渠道读回的绑定名册」钉成用例里那份，绕开真实设置库。"""

    monkeypatch.setattr(
        newapi_backend,
        "_capability_bindings_for_model",
        lambda _model: [dict(item) for item in bindings],
    )


def test_detect_aipdd_videos_host(monkeypatch) -> None:
    monkeypatch.delenv("AIPDD_VIDEO_API_HOST_HINT", raising=False)
    assert G._uses_aipdd_videos_api("https://api.aipdd.work/v1") is True
    assert G._uses_aipdd_videos_api("https://susciyuan.com/v1") is False
    assert G._uses_aipdd_videos_api("") is False
    monkeypatch.setenv("AIPDD_VIDEO_API_HOST_HINT", "custom-gw")
    assert G._uses_aipdd_videos_api("https://custom-gw.example/v1") is True
    assert G._uses_aipdd_videos_api("https://api.aipdd.work/v1") is False


def _inst() -> G:
    inst = G.__new__(G)
    inst.model = "ap-minimax-h3"
    # 建单体现在要带生效渠道的 provider 查目录（同名模型在不同渠道可以是不同形态），
    # 裸实例不设就是读不到 —— 真实构造里 __init__ 一定会设这个字段。
    inst.channel_provider = ""
    return inst


def test_build_aipdd_videos_payload_text_to_video(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_caps(monkeypatch)
    body = _inst()._build_aipdd_videos_payload(
        {"prompt": "x"},
        {"resolution": "768p", "ratio": "16:9"},
        prompt="a cat",
        duration=5,
        resolution="768p",
        ratio="16:9",
    )
    assert body == {
        "model": "ap-minimax-h3",
        "prompt": "a cat",
        "resolution": "768p",
        "video_resolution": "768p",
        "duration": 5,
        "duration_seconds": 5,
        "ratio": "16:9",
    }
    assert "image_urls" not in body
    assert "video_url" not in body
    assert "video_urls" not in body
    assert "audio_urls" not in body


def test_build_aipdd_videos_payload_images_dedupe_and_auto_ratio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_caps(monkeypatch)
    body = _inst()._build_aipdd_videos_payload(
        {"image": "https://x/a.png"},
        {
            "resolution": "480p",
            "ratio": "auto",
            "reference_images": ["https://x/b.png", "https://x/a.png"],
            "last_frame_image": "https://x/c.png",
        },
        prompt="p",
        duration=4,
        resolution="",
        ratio="",
    )
    # resolution 回退到 metadata；ratio=auto 时不下发画幅字段。
    assert body["resolution"] == "480p"
    assert "ratio" not in body
    assert "aspect_ratio" not in body
    # 首帧 + 参考 + 尾帧收敛进 image_urls，去重且保序。
    assert body["image_urls"] == [
        "https://x/a.png",
        "https://x/b.png",
        "https://x/c.png",
    ]


def test_build_aipdd_videos_payload_carries_source_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[req42] 换人/换产品的源视频必须进建单体。

    旧实现把 ``metadata["reference_videos"]`` 整个丢掉，上游只收到提示词 + 参考图，
    于是「替换视频人物」出一条和原视频毫无关系的片子。
    """

    _no_caps(monkeypatch)
    body = _inst()._build_aipdd_videos_payload(
        {"prompt": "x"},
        {
            "reference_images": ["https://x/person.png"],
            "reference_videos": ["https://x/source.mp4", "https://x/extra.mp4"],
        },
        prompt="把画面里的男人换成这张图里的人",
        duration=6,
        resolution="480p",
        ratio="9:16",
    )
    assert body["video_urls"] == ["https://x/source.mp4", "https://x/extra.mp4"]
    assert "video_url" not in body
    assert body["image_urls"] == ["https://x/person.png"]
    assert body["duration_seconds"] == 6
    assert body["video_resolution"] == "480p"


def test_build_aipdd_videos_payload_carries_reference_audios(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[req42] 对口型的驱动音频走 ``audio_urls``（复数；单值 ``audio`` 是被禁用的旧字段）。"""

    _no_caps(monkeypatch)
    body = _inst()._build_aipdd_videos_payload(
        {"prompt": "x"},
        {
            "reference_videos": ["https://x/source.mp4"],
            "reference_audios": ["https://x/voice.mp3", "", "  "],
        },
        prompt="口型对齐这段音频",
        duration=5,
        resolution="480p",
        ratio="9:16",
    )
    assert body["audio_urls"] == ["https://x/voice.mp3"]
    assert body["video_urls"] == ["https://x/source.mp4"]


def test_first_last_frame_mode_uses_dedicated_frame_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[req44] 首尾帧走渠道的 ``first_last_frame_to_video``，图递首/尾帧专用字段。

    渠道那条绑定的入参槽是 ``image``（首帧图片）/ ``referenceImage``（尾帧图片），并且
    与 ref2va 那条入参面板完全同名同构 —— 只把图折进 ``image_urls`` 上游无法分辨该调
    哪条工作流，所以模式也要说出来。
    """

    _no_caps(monkeypatch)
    _roster(monkeypatch, _H3_BINDINGS)
    body = _inst()._build_aipdd_videos_payload(
        {"image": "https://x/first.png"},
        {"last_frame_image": "https://x/last.png"},
        prompt="p",
        duration=5,
        resolution="480p",
        ratio="16:9",
        mode="first_last_frame",
    )
    assert body["mode"] == "first_last_frame_to_video"
    assert body["first_frame"] == "https://x/first.png"
    assert body["last_frame"] == "https://x/last.png"
    assert "image_urls" not in body


def test_first_frame_mode_falls_back_to_the_single_reference_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[req44] 首帧与图生视频同走那条绑定，首帧图取第一张参考图。

    执行协议里画布的图生视频 / 图片参考都是 ``image_reference``（``api/schemas.py``
    写明两者共用该协议）；到这里只剩素材形状能分开它们：**单图 = 图生视频**，递首帧
    槽。多图仍然是参考素材，不能被截到两个图槽里（下一条用例钉住）。
    """

    _no_caps(monkeypatch)
    _roster(monkeypatch, _H3_BINDINGS)
    inst = _inst()
    only_first = inst._build_aipdd_videos_payload(
        {},
        {"reference_images": ["https://x/a.png"]},
        prompt="p",
        duration=5,
        resolution="480p",
        ratio="",
        mode="image_reference",
    )
    assert only_first["mode"] == "first_last_frame_to_video"
    assert only_first["first_frame"] == "https://x/a.png"
    assert "last_frame" not in only_first
    assert "image_urls" not in only_first

    frames = inst._build_aipdd_videos_payload(
        {},
        {"reference_images": ["https://x/a.png"]},
        prompt="p",
        duration=5,
        resolution="480p",
        ratio="",
        mode="firstFrame",
    )
    assert frames["first_frame"] == "https://x/a.png"


def test_multi_image_reference_stays_on_the_reference_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[req44] 图片参考（多图）不折进首尾帧槽：保持 ``image_urls``，不发模式。"""

    _no_caps(monkeypatch)
    body = _inst()._build_aipdd_videos_payload(
        {},
        {"reference_images": ["https://x/a.png", "https://x/b.png"]},
        prompt="p",
        duration=5,
        resolution="480p",
        ratio="",
        mode="image_reference",
    )
    assert "mode" not in body
    assert body["image_urls"] == ["https://x/a.png", "https://x/b.png"]


def test_all_reference_mode_names_the_ref2va_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[req44] 全能参考 → ``ref2va``，参考图 / 源视频 / 参考音频依旧走各自复数字段。"""

    _no_caps(monkeypatch)
    _roster(monkeypatch, _H3_BINDINGS)
    body = _inst()._build_aipdd_videos_payload(
        {},
        {
            "reference_images": ["https://x/person.png", "https://x/product.png"],
            "reference_videos": ["https://x/source.mp4"],
            "reference_audios": ["https://x/voice.mp3"],
        },
        prompt="把画面里的男人换成这张图里的人",
        duration=6,
        resolution="480p",
        ratio="9:16",
        mode="all_reference",
    )
    assert body["mode"] == "ref2va"
    assert body["image_urls"] == ["https://x/person.png", "https://x/product.png"]
    assert body["video_urls"] == ["https://x/source.mp4"]
    assert body["audio_urls"] == ["https://x/voice.mp3"]
    assert "first_frame" not in body


def test_text_to_video_mode_carries_no_media_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[req44] 文生视频 → ``text_to_video``，一条素材都不递。

    渠道那条 t2v 绑定只认 prompt / 时长 / 种子。旧行为把附带的图折进 ``image_urls``，
    上游默默忽略 —— 用户看到的就是「传了图却没按图出片」。
    """

    _no_caps(monkeypatch)
    _roster(monkeypatch, _H3_BINDINGS)
    body = _inst()._build_aipdd_videos_payload(
        {"image": "https://x/a.png"},
        {
            "reference_videos": ["https://x/source.mp4"],
            "reference_audios": ["https://x/voice.mp3"],
        },
        prompt="p",
        duration=5,
        resolution="480p",
        ratio="16:9",
        mode="textToVideo",
    )
    assert body["mode"] == "text_to_video"
    for key in (
        "image_urls",
        "first_frame",
        "last_frame",
        "video_url",
        "video_urls",
        "audio_urls",
    ):
        assert key not in body


def test_channel_that_can_route_itself_still_gets_the_legacy_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[req44] 渠道自己按 taskKind 分得清时**不发** ``mode``：LTX 的建单体逐字节照旧。

    模式选择子是替「渠道自己分不清」的场合发明的（H3：两条绑定同名 taskKind + 同样的
    入参面板）。LTX 2.5 名下是互不重名的两条绑定，再替它发一个
    ``first_last_frame_to_video`` 就是拿 minimax 的口径去改别的模型的提交形状 ——
    用户 2026-10-08：「我说的是 minimax 模型，seedance 该怎样就怎样」。
    """

    _no_caps(monkeypatch)
    _roster(monkeypatch, _LTX_BINDINGS)
    inst = _inst()
    inst.model = "ap-ltx-2.5"
    body = inst._build_aipdd_videos_payload(
        {"image": "https://x/first.png"},
        {"last_frame_image": "https://x/last.png"},
        prompt="p",
        duration=5,
        resolution="480p",
        ratio="16:9",
        mode="first_last_frame",
    )
    assert "mode" not in body
    assert "first_frame" not in body
    assert "last_frame" not in body
    # 素材回到 req44 之前的形状：首帧 + 尾帧收敛进复数参考图字段。
    assert body["image_urls"] == ["https://x/first.png", "https://x/last.png"]


def test_missing_binding_roster_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[req44] 没读成绑定名册（渠道没开能力接口 / 模型被代理）= 不表态，不发明字段。"""

    _no_caps(monkeypatch)
    _roster(monkeypatch, [])
    body = _inst()._build_aipdd_videos_payload(
        {"image": "https://x/a.png"},
        {"reference_images": ["https://x/b.png"]},
        prompt="p",
        duration=5,
        resolution="480p",
        ratio="",
        mode="first_last_frame",
    )
    assert "mode" not in body
    assert body["image_urls"] == ["https://x/a.png", "https://x/b.png"]


def test_video_edit_mode_sends_no_invented_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[req44] 渠道没给出对应绑定的模式不发 ``mode``，行为与旧版逐字相同。"""

    _no_caps(monkeypatch)
    body = _inst()._build_aipdd_videos_payload(
        {},
        {"reference_videos": ["https://x/source.mp4"], "reference_images": ["https://x/a.png"]},
        prompt="p",
        duration=5,
        resolution="480p",
        ratio="",
        mode="video_edit",
    )
    assert "mode" not in body
    assert body["video_urls"] == ["https://x/source.mp4"]
    assert body["image_urls"] == ["https://x/a.png"]


def test_build_aipdd_videos_payload_refuses_beyond_catalog_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[req46] 单类超目录申报的上限是**拒单摊数字**，不是截断。

    截断就是静默丢素材：递 12 张图只递进去 9 张，用户看到的是一条「我明明挂了 12 张」
    的片子还照常计费（req44 同一个口径）。报错里要把「你给了几个 / 只收几个」摊清。
    """

    monkeypatch.setattr(
        newapi_backend,
        "_aipdd_video_input_caps",
        lambda *_a, **_kw: {
            "max_image_inputs": 9,
            "max_audio_inputs": 3,
            "max_video_inputs": 3,
            "max_reference_inputs": None,
        },
    )
    inst = _inst()
    with pytest.raises(ValueError, match="参考图片给了 12 个") as exc:
        inst._build_aipdd_videos_payload(
            {"prompt": "x"},
            {"reference_images": [f"https://x/{i}.png" for i in range(12)]},
            prompt="p",
            duration=5,
            resolution="480p",
            ratio="",
        )
    assert "只收 9 个" in str(exc.value)
    with pytest.raises(ValueError, match="参考音频给了 5 个"):
        inst._build_aipdd_videos_payload(
            {"prompt": "x"},
            {"reference_audios": [f"https://x/{i}.mp3" for i in range(5)]},
            prompt="p",
            duration=5,
            resolution="480p",
            ratio="",
        )
    with pytest.raises(ValueError, match="参考视频给了 5 个"):
        inst._build_aipdd_videos_payload(
            {"prompt": "x"},
            {"reference_videos": [f"https://x/{i}.mp4" for i in range(5)]},
            prompt="p",
            duration=5,
            resolution="480p",
            ratio="",
        )
    # 在限内的那些逐条不丢（包括同类的第二、第三条 —— 旧实现在这里只活一条）。
    body = inst._build_aipdd_videos_payload(
        {"prompt": "x"},
        {
            "reference_images": [f"https://x/{i}.png" for i in range(9)],
            "reference_audios": [f"https://x/{i}.mp3" for i in range(3)],
            "reference_videos": [f"https://x/{i}.mp4" for i in range(3)],
        },
        prompt="p",
        duration=5,
        resolution="480p",
        ratio="",
    )
    assert len(body["image_urls"]) == 9
    assert body["image_urls"][0] == "https://x/0.png"
    assert body["audio_urls"] == [f"https://x/{i}.mp3" for i in range(3)]
    assert body["video_urls"] == [f"https://x/{i}.mp4" for i in range(3)]


def test_reference_videos_reach_upstream_as_the_named_plural_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[req46] 参考视频走渠道点名的复数字段，不再折成单值 ``video_url``。

    2026-10-08 带外探针（同一提示词 + 一个拉不到的素材 URL，看上游读不读）：

    * ``video_urls`` → 400「当前 MiniMax H3 渠道不支持视频参考；本次请求未扣费」
    * ``videos`` → 400，文案点名「请使用 image_urls、audio_urls、video_urls 或首尾帧字段」
    * ``video_url`` / ``reference_video`` / ``reference_videos`` → 202 开跑，素材拉不到
      也照样 SUCCEEDED 并扣 3600 awcoin（字段根本没被读）
    * ``image_urls`` 递不可达直链 → 400「参考图片链接无法被上游访问（HTTP 404）」（会被读）

    所以旧实现那根单值 ``video_url`` 是「付钱出一条与原视频无关的片子」；按点名字段递，
    不支持的那类才能拿到不扣费的明拒，支持的渠道能拿到多条而不是只活一条。
    """

    _no_caps(monkeypatch)
    body = _inst()._build_aipdd_videos_payload(
        {"prompt": "x"},
        {
            "reference_videos": [
                "https://x/a.mp4",
                "https://x/b.mp4",
                "https://x/c.mp4",
            ]
        },
        prompt="参照这三段视频的动作",
        duration=5,
        resolution="480p",
        ratio="",
    )
    assert body["video_urls"] == ["https://x/a.mp4", "https://x/b.mp4", "https://x/c.mp4"]
    assert "video_url" not in body


def test_build_aipdd_videos_payload_enforces_declared_mixed_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[req46] 混投合计超渠道申报的 ``maxReferenceInputs`` 要拒单，不能静默丢素材。

    H3 今日实拉：图 9 / 音频 3 / 视频 0 / 总闸 12。三类各自看着都在限内，合计仍可能超
    闸门（官方契约也是 12）——这里不替用户挑丢哪三份，把数字摊出来让他自己删。
    """

    monkeypatch.setattr(
        newapi_backend,
        "_aipdd_video_input_caps",
        lambda *_a, **_kw: {
            "max_image_inputs": 9,
            "max_audio_inputs": 3,
            "max_video_inputs": 3,
            "max_reference_inputs": 12,
        },
    )
    inst = _inst()
    with pytest.raises(ValueError, match="混投上限 12"):
        inst._build_aipdd_videos_payload(
            {"prompt": "x"},
            {
                "reference_images": [f"https://x/{i}.png" for i in range(9)],
                "reference_videos": [f"https://x/{i}.mp4" for i in range(3)],
                "reference_audios": [f"https://x/{i}.mp3" for i in range(3)],
            },
            prompt="p",
            duration=5,
            resolution="480p",
            ratio="",
        )
    # 截到刚好 12 就递得出去，且各角色逐条不丢。
    body = inst._build_aipdd_videos_payload(
        {"prompt": "x"},
        {
            "reference_images": [f"https://x/{i}.png" for i in range(9)],
            "reference_videos": [f"https://x/{i}.mp4" for i in range(2)],
            "reference_audios": [f"https://x/{i}.mp3" for i in range(1)],
        },
        prompt="p",
        duration=5,
        resolution="480p",
        ratio="",
    )
    assert len(body["image_urls"]) + len(body["video_urls"]) + len(body["audio_urls"]) == 12


def test_build_aipdd_videos_payload_refuses_inputs_catalog_rules_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[req42] 目录明说不能吃的素材要报错，不能静默降级成文生视频。"""

    monkeypatch.setattr(
        newapi_backend,
        "_aipdd_video_input_caps",
        lambda *_a, **_kw: {
            "max_image_inputs": None,
            "max_audio_inputs": 0,
            "max_video_inputs": 0,
        },
    )
    inst = _inst()
    with pytest.raises(ValueError, match="不支持参考视频"):
        inst._build_aipdd_videos_payload(
            {"prompt": "x"},
            {"reference_videos": ["https://x/source.mp4"]},
            prompt="p",
            duration=5,
            resolution="480p",
            ratio="",
        )
    with pytest.raises(ValueError, match="不支持参考音频"):
        inst._build_aipdd_videos_payload(
            {"prompt": "x"},
            {"reference_audios": ["https://x/voice.mp3"]},
            prompt="p",
            duration=5,
            resolution="480p",
            ratio="",
        )


def test_build_aipdd_videos_payload_refuses_non_public_media_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[req42] 参考视频/音频只要不是公网直链就当场报错，不许静默出废片。

    实测：媒体中转处于「本地存储」档时上传回的是 base64 data URL，而这类不是直链的
    素材递上游只会收到 202/QUEUED（不校验），生成阶段才因拉不到素材出一条与原视频无关
    的片子。参考图不受本约束（data URL 是图片输入的既有可用路径）。
    """

    _no_caps(monkeypatch)
    inst = _inst()
    with pytest.raises(ValueError, match="参考视频必须使用公网可达"):
        inst._build_aipdd_videos_payload(
            {"prompt": "x"},
            {"reference_videos": ["data:video/mp4;base64,AAAA"]},
            prompt="p",
            duration=5,
            resolution="480p",
            ratio="",
        )
    with pytest.raises(ValueError, match="参考音频必须使用公网可达"):
        inst._build_aipdd_videos_payload(
            {"prompt": "x"},
            {"reference_audios": ["data:audio/mpeg;base64,AAAA"]},
            prompt="p",
            duration=5,
            resolution="480p",
            ratio="",
        )
    # 参考图允许内联，不拦。
    body = inst._build_aipdd_videos_payload(
        {"prompt": "x"},
        {"reference_images": ["data:image/png;base64,AAAA"]},
        prompt="p",
        duration=5,
        resolution="480p",
        ratio="",
    )
    assert body["image_urls"] == ["data:image/png;base64,AAAA"]


def test_aipdd_video_input_caps_reads_gateway_declaration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[req42] 配额只照网关申报的**数量**取：没写数量 = 不支持也不成立 = 未表态。

    [req46] 两个布尔不参与判定：同一份声明里的 ``supportsReferenceVideo`` 已实测到会漂
    （req43 看到 true / 2026-10-08 看到 false），拿它归零会打死本可以用的入口。
    """

    def _entry(name: str, provider: str = "") -> dict:
        return {"mediaMetadata": {"maxImageInputs": 9, "maxAudioInputs": 3,
                                   "supportsReferenceVideo": True}}

    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.upstream_catalog_entry", _entry
    )
    caps = newapi_backend._aipdd_video_input_caps("ap-minimax-h3")
    assert caps == {
        "max_image_inputs": 9,
        "max_video_inputs": None,
        "max_audio_inputs": 3,
        "max_reference_inputs": None,
    }

    def _no_video(name: str, provider: str = "") -> dict:
        return {"mediaMetadata": {"supportsReferenceVideo": False, "maxVideoInputs": 0}}

    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.upstream_catalog_entry", _no_video
    )
    assert newapi_backend._aipdd_video_input_caps("ap-agnes-video-2.5-flash")[
        "max_video_inputs"
    ] == 0

    # 只有布尔 false、没写数量：按「渠道没说」处理（不表态），不归零。
    def _boolean_only(name: str, provider: str = "") -> dict:
        return {"mediaMetadata": {"maxImageInputs": 9, "supportsReferenceVideo": False}}

    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.upstream_catalog_entry", _boolean_only
    )
    assert newapi_backend._aipdd_video_input_caps("ap-minimax-h3")["max_video_inputs"] is None

    def _silent(name: str, provider: str = "") -> dict:
        return {}

    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.upstream_catalog_entry", _silent
    )
    assert newapi_backend._aipdd_video_input_caps("unknown") == {
        "max_image_inputs": None,
        "max_video_inputs": None,
        "max_audio_inputs": None,
        "max_reference_inputs": None,
    }


def test_aipdd_video_input_caps_forwards_provider_to_catalog_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[req46] 查目录必须带上生效渠道的 provider。

    本轮新登记了海外 / 国内两条 MiniMax 渠道：同一个模型名在两条渠道里可以是不同形态，
    不带渠道位就是全表找第一个同名条目，会把另一条渠道的申报读成自己这条的事实。
    """

    seen: list[tuple[str, str]] = []

    def _entry(name: str, provider: str = "") -> dict:
        seen.append((name, provider))
        return {"mediaMetadata": {"maxImageInputs": 9}}

    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.upstream_catalog_entry", _entry
    )
    newapi_backend._aipdd_video_input_caps("minimax-h3", "minimax-cn")
    assert seen == [("minimax-h3", "minimax-cn")]
    # 不传也是原来的行为（全表找），不至于把旧调用点弄坏。
    newapi_backend._aipdd_video_input_caps("minimax-h3")
    assert seen[-1] == ("minimax-h3", "")


async def test_all_reference_inputs_survive_into_aipdd_create_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[req42] 「替换视频人物」全链路拼装：源视频 + 人物参考图都得进建单体。

    只测单元拼装不够——旧版就是在 ``_build_aipdd_videos_payload`` 这一手把
    ``_apply_huimeng_protocol_media_inputs`` 已经填好的 ``reference_videos`` 丢掉的。
    这里把两段接起来跑，拿本地素材路径（靠假中转成公网直链）验证最终 body。
    """

    from novelvideo.generators.video_generator.backends.seedance2 import ShotReference

    _no_caps(monkeypatch)
    inst = _inst()

    async def fake_relay_frame(path: str) -> str:
        return f"https://relay.example/{str(path).rsplit('/', 1)[-1]}"

    async def fake_relay_media(path: str, **_kwargs: object) -> str:
        return f"https://relay.example/{str(path).rsplit('/', 1)[-1]}"

    monkeypatch.setattr(inst, "_relay_frame_input", fake_relay_frame)
    monkeypatch.setattr(inst, "_relay_media_input", fake_relay_media)

    metadata: dict[str, object] = {}
    await inst._apply_huimeng_protocol_media_inputs(
        metadata,
        mode="all_reference",
        image_path="",
        last_frame_path=None,
        references=[
            ShotReference(type="video", path="/static/p/source.mp4", role=""),
            ShotReference(type="image", path="/static/p/person.png", role=""),
            ShotReference(type="audio", path="/static/p/voice.mp3", role=""),
        ],
        log=lambda _message: None,
    )
    body = inst._build_aipdd_videos_payload(
        {"prompt": "p"},
        metadata,
        prompt="把画面里的男人换成这张图里的人，口型对齐这段音频",
        duration=6,
        resolution="480p",
        ratio="9:16",
    )
    assert body["video_urls"] == ["https://relay.example/source.mp4"]
    assert body["image_urls"] == ["https://relay.example/person.png"]
    assert body["audio_urls"] == ["https://relay.example/voice.mp3"]


def test_aipdd_create_body_is_built_before_submit_flag_and_gets_logged() -> None:
    """[req42] 建单体必须晚于 ``submit_attempted`` 置位，并且自己进调用日志。

    两个各自独立、都会复发的事实：

    1. 本地校验（素材不是公网直链 / 目录明说不支持）如果在 ``submit_attempted = True``
       之后抛，失败分类会把组织请求标成「已提交、结果不确定」，留下悬挂操作；正确语义
       是 rejected（一个上游请求都没发出）。
    2. 调用日志原先只记转换前的 payload，``video_url`` / ``audio_urls`` 从来不进日志 ——
       本轮就是被「日志里看着参数没问题」骗到一个错误结论上的。
    """

    src = inspect.getsource(G.generate)
    branch = src[src.index("if use_videos_api:") : src.index("elif organization_request:")]
    build_at = branch.index("self._build_aipdd_videos_payload(")
    flag_at = branch.index("submit_attempted = True")
    log_at = branch.index("update_current_model_call_log(request_payload=submit_body)")
    assert build_at < flag_at, "建单体必须晚置位，本地报错才能被归为提交前失败"
    assert log_at < flag_at, "真正发出的 body 必须在 POST 前记账"
    assert "submit_body,\n" in branch, "POST 必须发拼好的 body，不是转换前的 payload"


def test_extract_video_url_prefers_content_url() -> None:
    assert (
        G._extract_video_url(
            {"status": "SUCCEEDED", "content_url": "https://oss.aipdd.work/x.mp4"}
        )
        == "https://oss.aipdd.work/x.mp4"
    )
