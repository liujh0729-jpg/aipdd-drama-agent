# [aipdd fork delta req43] 渠道「能力绑定」事实：解析 / 名字族关联 / 落盘 / 刷新挂接。
#
# 病根（线上取证 req43）：``/v1/models`` 当时给 ap-minimax-h3 静态声明 supportsReferenceVideo=true，
# 而 AIPDD 自己 /v1/capabilities 里在线的三条 H3 绑定入参没有任何视频输入 —— 参考视频
# 递进去只是静默出一条不相干的片子。
# 【已更正的口径】绑定不足只说明「这条提交路径递不进去」，不能当成「模型没这个能力」
# 的结论（H3 的 Ref2VA 本来就是多图+多视频+多音频的全能参考形态）。判定必须落到
# 「哪个提交模式走哪条绑定」，并且不拿这个事实去把目录里的参考上限归零。
# 【同一个布尔会漂】2026-10-08 复拉：supportsReferenceVideo 已改成 false 并配上了
# maxVideoInputs: 0（递 video_urls 拿到的是「当前 MiniMax H3 渠道不支持视频参考；本次请求
# 未扣费」）。两个时点都是渠道自己写的，所以布尔不参与任何判定：参考上限只照数量申报取。
from __future__ import annotations

from typing import Any

import httpx
import pytest

from novelvideo import config
from novelvideo import model_gateway_settings
from novelvideo import newapi_provisioner
from novelvideo.model_gateway_settings import (
    PROVIDER_CAPABILITY_BINDINGS_SETTING_KEY,
    get_provider_capability_bindings,
    save_provider_capability_bindings,
)
from novelvideo.upstream_capabilities import (
    _capability_items,
    aipdd_videos_mode_selector,
    binding_accepts_input,
    bindings_accepting_input,
    fetch_upstream_capabilities,
    name_family_tokens,
    normalize_request_mode,
    summarize_video_bindings,
    video_binding_modes_for_model,
    video_bindings_need_mode_selector,
    video_input_facts_for_model,
)

# 线上实况的缩样（AIPDD /v1/capabilities，2026-10-07 docker cp 回的真数据裁剪）。
_AIPDD_CAPABILITIES = {
    "code": 0,
    "data": [
        {
            "id": "minimax-h3-model-file",
            "code": "aipdd_minimax_h3_t2v_speed_test",
            "name": "MiniMax H3 T2V Speed",
            "adapterCode": "comfyui",
            "endpointType": "openai-video",
            "taskKind": "text_to_video",
            "inputModalities": ["text"],
            "outputModalities": ["video", "audio"],
            "params": [
                {"uiType": "textarea", "paramKey": "prompt"},
                {"uiType": "number", "paramKey": "durationSeconds"},
            ],
            "available": True,
        },
        {
            "code": "aipdd_minimax_h3_first_last_frame_speed_test",
            "name": "MiniMax H3 首尾帧 Speed",
            "adapterCode": "comfyui",
            "endpointType": "openai-video",
            "taskKind": "first_last_frame_to_video",
            "inputModalities": ["text", "image"],
            "params": [
                {"uiType": "textarea", "paramKey": "prompt"},
                {"uiType": "image_url", "paramKey": "image"},
                {"uiType": "image_url", "paramKey": "referenceImage"},
            ],
            "available": True,
        },
        {
            # 线上实况（2026-10-07 实测 dump）：名字写着 ref2va，接线却只有首尾帧两张图，
            # 连 taskKind 都写成 first_last_frame_to_video。全能参考需要的参考视频 / 参考
            # 音频节点根本不在 —— 这是渠道接线不足，不是 H3 没能力。
            "code": "aipdd_minimax_h3_ref2va_test",
            "name": "MiniMax H3 参考图生视频",
            "adapterCode": "comfyui",
            "endpointType": "openai-video",
            "taskKind": "first_last_frame_to_video",
            "inputModalities": ["text", "image"],
            "params": [
                {
                    "uiType": "textarea",
                    "paramKey": "prompt",
                    "paramName": "提示词",
                    "isRequired": True,
                },
                {
                    "uiType": "image_url",
                    "paramKey": "image",
                    "paramName": "首帧图片",
                    "isRequired": True,
                },
                {
                    "uiType": "image_url",
                    "paramKey": "referenceImage",
                    "paramName": "尾帧图片",
                    "isRequired": True,
                },
                {
                    "uiType": "number",
                    "paramKey": "durationSeconds",
                    "paramName": "时长（秒）",
                },
            ],
            "available": True,
        },
        {
            "code": "aipdd_latentsync1.5",
            "name": "LatentSync 1.5",
            "adapterCode": "comfyui",
            "endpointType": "openai-video",
            "taskKind": "lip_sync",
            "inputModalities": ["video", "audio"],
            "params": [
                {"uiType": "video_url", "paramKey": "video"},
                {"uiType": "audio_url", "paramKey": "LoadAudio"},
            ],
            "available": True,
        },
        {
            # 非视频条目：不能进判定表，否则图片/音频绑定会干扰结论。
            "code": "aipdd_IndexTTS",
            "name": "IndexTTS",
            "adapterCode": "comfyui",
            "endpointType": "audio-speech",
            "taskKind": "voice_clone",
            "inputModalities": ["audio", "text"],
            "params": [{"uiType": "audio_url", "paramKey": "audio"}],
            "available": True,
        },
    ],
}


# 线上实况（2026-10-08 实拉 docker 内 ``/v1/capabilities``）：渠道给 LTX 2.5 只接了
# 文生 + 图生视频两条绑定，两条 taskKind 互不重名，也没有任何参考形状。
_LTX_CAPABILITIES = {
    "code": 0,
    "data": [
        {
            "code": "aipdd_ltx_2_5_t2v_test",
            "name": "LTX 2.5 文生视频",
            "adapterCode": "comfyui",
            "endpointType": "openai-video",
            "taskKind": "text_to_video",
            "inputModalities": ["text"],
            "params": [
                {"uiType": "textarea", "paramKey": "prompt", "paramName": "提示词"},
                {"uiType": "number", "paramKey": "durationSeconds"},
            ],
            "available": True,
        },
        {
            "code": "aipdd_ltx_2_5_i2v_test",
            "name": "LTX 2.5 图生视频",
            "adapterCode": "comfyui",
            "endpointType": "openai-video",
            "taskKind": "image_to_video",
            "inputModalities": ["text", "image"],
            "params": [
                {"uiType": "textarea", "paramKey": "prompt", "paramName": "提示词"},
                {"uiType": "image_url", "paramKey": "image", "paramName": "图片"},
            ],
            "available": True,
        },
    ],
}


def _isolate_settings_db(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    for key in (
        "STATE_DIR",
        "ST_CONTROL_PLANE_DSN",
        "MODEL_GATEWAY_MODE",
        "NEWAPI_API_KEY",
        "NEWAPI_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------- 解析与名字族


def test_summarize_video_bindings_keeps_only_video_entries() -> None:
    bindings = summarize_video_bindings(_AIPDD_CAPABILITIES["data"])
    codes = [item["code"] for item in bindings]
    assert "aipdd_IndexTTS" not in codes
    assert len(bindings) == 4
    latentsync = next(item for item in bindings if "latentsync" in item["code"])
    assert latentsync["uiTypes"] == ["audio_url", "video_url"]
    assert latentsync["inputModalities"] == ["audio", "video"]


def test_summarize_accepts_wrapped_and_bare_payload_shapes() -> None:
    """响应外壳的几种实测形态都要能解出条目，解不出就回空表（不表态）。"""

    rows = _AIPDD_CAPABILITIES["data"]
    assert len(summarize_video_bindings(rows)) == 4
    assert _capability_items(rows) == rows
    assert _capability_items(_AIPDD_CAPABILITIES) == rows
    # {data: {"list": [...]}} 是 /shared-tasks/scripts 的形态。
    assert _capability_items({"data": {"list": rows}}) == rows
    assert _capability_items({"data": {"items": rows}}) == rows
    assert _capability_items({"data": "not-a-list"}) == []
    assert _capability_items("not-a-payload") == []
    # 没有 params 的裸条目也要进表：按「没有任何输入通道」算，而不是被丢掉。
    bare = summarize_video_bindings([{"code": "x_video", "endpointType": "openai-video"}])
    assert len(bare) == 1
    assert bare[0]["uiTypes"] == []


def test_h3_binding_union_only_answers_whether_the_model_has_a_channel() -> None:
    """不给 ``mode`` 时退回「全部在线绑定的并集」：整体看 H3 确实有参考图通道。

    并集只能回答「这个模型整体上有没有这条通道」，不能拿来当某一条提交模式的结论 ——
    渠道给 H3 的三条绑定入参集根本不同（文生不吃图、首尾帧只吃两张图、ref2va 接错）。
    """

    bindings = summarize_video_bindings(_AIPDD_CAPABILITIES["data"])
    facts = video_input_facts_for_model(bindings, "ap-minimax-h3")
    assert facts is not None and facts["matched"] is True
    assert facts["bindingCount"] == 3
    assert facts["acceptsReferenceImage"] is True
    assert facts["acceptsReferenceVideo"] is False
    assert facts["acceptsReferenceAudio"] is False


def test_h3_bindings_are_scoped_per_request_mode() -> None:
    """按模式筛绑定：文生 / 首尾帧 / 全能参考各自只看见自己要走的那几条。"""

    bindings = summarize_video_bindings(_AIPDD_CAPABILITIES["data"])

    text = video_input_facts_for_model(bindings, "ap-minimax-h3", mode="texttovideo")
    assert text is not None
    assert text["bindings"] == ["aipdd_minimax_h3_t2v_speed_test"]
    # 文生视频连参考图节点都没有；按模型名取并集就会把首尾帧那条的 image_url 当成它也能吃。
    assert text["acceptsReferenceImage"] is False
    assert text["inputUiTypes"] == ["number", "textarea"]

    frames = video_input_facts_for_model(bindings, "ap-minimax-h3", mode="firstLastFrame")
    assert frames is not None
    assert frames["acceptsReferenceImage"] is True
    assert frames["acceptsReferenceVideo"] is False
    # [req44] 帧类模式只得看见真正的首尾帧那条：两条绑定 taskKind 同名，渠道自己的
    # 区分点只在 code 里的 ref2va 缩写，不按 code 推开就会把全能参考那条当候选。
    assert frames["bindings"] == ["aipdd_minimax_h3_first_last_frame_speed_test"]

    # [req44] 用户路由口径：首帧 / 图生视频与首尾帧归同一条 ``first_last_frame_to_video``
    # 绑定（尾帧可选，单图时只填首帧槽）。
    for canvas_mode in ("firstFrame", "imageToVideo", "first_frame", "image_to_video"):
        scoped = video_input_facts_for_model(bindings, "ap-minimax-h3", mode=canvas_mode)
        assert scoped is not None, canvas_mode
        assert scoped["bindings"] == [
            "aipdd_minimax_h3_first_last_frame_speed_test"
        ], canvas_mode
        assert scoped["acceptsReferenceImage"] is True, canvas_mode

    # 线上实况：名字写着 ref2va、接线却只有首尾帧两张图，靠 code 里的模式缩写仍能认出它。
    omni = video_input_facts_for_model(bindings, "ap-minimax-h3", mode="all_reference")
    assert omni is not None
    assert omni["bindings"] == ["aipdd_minimax_h3_ref2va_test"]
    assert omni["taskKinds"] == ["first_last_frame_to_video"]
    assert omni["acceptsReferenceVideo"] is False
    assert omni["acceptsReferenceAudio"] is False
    # 报错要摊证据：渠道自己给这些入参起的名字必须原样带出来。
    assert "首帧图片" in omni["inputParams"][0]
    assert "尾帧图片" in omni["inputParams"][0]


def test_unrecognizable_mode_stays_silent() -> None:
    """词表对不上时不表态：「我们认不出该走哪条绑定」读不出「渠道不支持」。"""

    bindings = summarize_video_bindings(_AIPDD_CAPABILITIES["data"])
    assert video_input_facts_for_model(bindings, "ap-minimax-h3", mode="video_extend") is None
    assert video_input_facts_for_model(bindings, "ap-latentsync-1.5", mode="texttovideo") is None
    assert normalize_request_mode("firstLastFrame") == "first_last_frame"
    assert normalize_request_mode("all-reference") == "all_reference"
    assert normalize_request_mode("videoreference") == "all_reference"
    assert normalize_request_mode("") == ""


def test_aipdd_videos_mode_selector_covers_only_the_agreed_canvas_modes() -> None:
    """[req44] 递交给 AIPDD ``/v1/videos`` 的模式选择子按用户口径钉死。

    图生视频 / 首帧 / 首尾帧 → ``first_last_frame_to_video``，文生视频 →
    ``text_to_video``，全能参考 → ``ref2va``。为什么必须有这个选择子：渠道给 H3 的首尾帧
    与 ref2va 两条绑定入参面板一模一样、连 taskKind 都同名，只按输入形状递素材
    上游根本无法分辨该调哪条工作流。

    渠道没给出对应绑定的模式（视频编辑 / 延长 / 未传 / 认不出的串）**返回空串** =
    不发 ``mode``：自己发明一个取值只会被拒单或默默走错工作流。
    """

    for canvas_mode in ("firstFrame", "first_frame", "firstLastFrame", "imageToVideo", "image_to_video"):
        assert aipdd_videos_mode_selector(canvas_mode) == "first_last_frame_to_video", canvas_mode
    assert aipdd_videos_mode_selector("textToVideo") == "text_to_video"
    assert aipdd_videos_mode_selector("allReference") == "ref2va"
    assert aipdd_videos_mode_selector("videoReference") == "ref2va"
    for unknown in ("videoEdit", "video_edit", "image_reference", "lip_sync", "", None):
        assert aipdd_videos_mode_selector(unknown) == "", unknown


def test_frame_modes_still_match_a_channel_written_image_to_video_binding() -> None:
    """[req44] 帧类路由是「只加不减」：渠道自己写着 ``image_to_video`` 的绑定照旧认得到。

    LTX 2.5 在线就是一条 ``taskKind=image_to_video`` 的绑定。把图生视频改判到
    ``first_last_frame_to_video`` 如果把它自己的那条推开，就是拿我们的路由表去掩盖
    渠道已经给出的事实。
    """

    bindings = summarize_video_bindings(
        [
            {
                "code": "aipdd_ltx_2_5_i2v_test",
                "name": "LTX 2.5 图生视频",
                "endpointType": "openai-video",
                "taskKind": "image_to_video",
                "inputModalities": ["text", "image"],
                "params": [{"uiType": "image_url", "paramKey": "image"}],
                "available": True,
            }
        ]
    )
    for canvas_mode in ("imageToVideo", "firstFrame"):
        scoped = video_input_facts_for_model(bindings, "ap-ltx-2.5", mode=canvas_mode)
        assert scoped is not None, canvas_mode
        assert scoped["bindings"] == ["aipdd_ltx_2_5_i2v_test"], canvas_mode


def test_binding_modes_reverse_lookup_reports_only_bound_modes() -> None:
    """[req44] 反查「渠道为这个模型备了哪几种模式的绑定」（前端页签的放开证据）。

    H3 三条在线绑定反出：文生 + 帧类三种（图生视频 / 首帧 / 首尾帧按路由口径共用
    ``first_last_frame_to_video`` 那条）+ 全能参考（按 code 从 ref2va 那条认）。
    对口型（``lip_sync``）不属于任何提交模式；关联不上 / 绑定表为空一律空表 ——
    没读到事实读不出「不支持」。调用名带渠道位时必须与裸名同一答案。
    """

    bindings = summarize_video_bindings(_AIPDD_CAPABILITIES["data"])
    expected = [
        "all_reference",
        "first_frame",
        "first_last_frame",
        "image_to_video",
        "text_to_video",
    ]
    assert video_binding_modes_for_model(bindings, "ap-minimax-h3") == expected
    assert video_binding_modes_for_model(bindings, "aipdd::ap-minimax-h3") == expected
    assert video_binding_modes_for_model(bindings, "ap-latentsync-1.5") == []
    # 渠道把视频模型代理给第三方：自托管绑定清单里根本没这个名字，不表态。
    assert video_binding_modes_for_model(bindings, "some-proxied-model") == []
    assert video_binding_modes_for_model([], "ap-minimax-h3") == []
    assert video_binding_modes_for_model(bindings, "") == []


def test_binding_modes_drop_bindings_the_channel_took_offline() -> None:
    """[req44] 渠道把绑定摘下去（``available=False``）后，它备的模式不再是入口证据。

    H3 只有那一条帧类绑定（ref2va 那条按 code 不参与帧类），它一下线首尾帧 / 图生
    视频 / 首帧三个入口同时失去证据；文生与全能参考还在线，所以仍该被报出。
    """

    rows = [dict(item) for item in _AIPDD_CAPABILITIES["data"]]
    for row in rows:
        if row.get("code") == "aipdd_minimax_h3_first_last_frame_speed_test":
            row["available"] = False
    modes = video_binding_modes_for_model(summarize_video_bindings(rows), "ap-minimax-h3")
    assert modes == ["all_reference", "text_to_video"]


def test_ltx_binds_frames_but_never_the_omni_reference() -> None:
    """[req44] LTX 2.5 的模式入口 = 图生视频 + 首尾帧（+ 它自己那条文生），**不含全能参考**。

    用户 2026-10-08 的产品口径：“ltx 只有图生视频和首尾帧，不支持全能参考”。这条既是
    校验也是防归：渠道给 LTX 只接了 ``taskKind=image_to_video`` 一条帧类绑定，首尾帧入口
    必须能从它反出来（不然就是拿渠道接线不足当能力边界）；而 LTX 名下没有任何
    reference / ref2va 形状的绑定，全能参考就反不出证据 —— 页签不该被误放开。
    图能不能真递进两张由渠道 ``maxInputImages`` 声明在提交时门控，那是另一条独立事实。
    """

    bindings = summarize_video_bindings(_LTX_CAPABILITIES["data"])
    modes = video_binding_modes_for_model(bindings, "ap-ltx-2.5")
    assert modes == [
        "first_frame",
        "first_last_frame",
        "image_to_video",
        "text_to_video",
    ]
    assert "all_reference" not in modes
    # 帧类三种模式均能“绑到一条可用绑定”（事实层不表态 = 读不出不支持）。
    for canvas_mode in ("firstFrame", "firstLastFrame", "imageToVideo", "textToVideo"):
        scoped = video_input_facts_for_model(bindings, "ap-ltx-2.5", mode=canvas_mode)
        assert scoped is not None, canvas_mode
    # 全能参考在 LTX 名下没有对应绑定：“认不出该走哪条”读不出“渠道不支持”。
    assert video_input_facts_for_model(bindings, "ap-ltx-2.5", mode="allReference") is None


def test_mode_selector_is_only_for_channels_that_cannot_route_themselves() -> None:
    """[req44] 只有同一模型名下出现重复 taskKind 时，才需要替它把模式说出来。

    为什么不能无条件发 ``mode``：选择子的口径是用户针对 **minimax** 给的（两条 H3 绑定
    被渠道写成同一个 taskKind、入参面板还一模一样，上游自己分不开）。LTX 名下是
    ``text_to_video`` + ``image_to_video`` 两条互不重名的绑定，再替它发
    ``first_last_frame_to_video`` 就是拿一个模型的口径去改所有模型的提交形状（用户：
    “seedance 该怎样就怎样”）。名册关联不上（空表）= 不表态，也不改行为。
    """

    rows = list(_AIPDD_CAPABILITIES["data"]) + list(_LTX_CAPABILITIES["data"])
    aipdd = summarize_video_bindings(rows)
    assert video_bindings_need_mode_selector(aipdd, "ap-minimax-h3") is True
    assert video_bindings_need_mode_selector(aipdd, "aipdd::ap-minimax-h3") is True
    # 同一条名册里 LTX 自己的两条不重名 —— 判据按模型限定，不是整渠道。
    assert video_bindings_need_mode_selector(aipdd, "ap-ltx-2.5") is False
    assert video_bindings_need_mode_selector(aipdd, "doubao-seedance-2-0") is False
    assert video_bindings_need_mode_selector([], "ap-minimax-h3") is False
    assert video_bindings_need_mode_selector(aipdd, "") is False
    # 渠道把重名的那两条推下去一条之后，它自己就能路由了。
    rows = [dict(item) for item in _AIPDD_CAPABILITIES["data"]]
    for row in rows:
        if row.get("code") == "aipdd_minimax_h3_ref2va_test":
            row["available"] = False
    assert video_bindings_need_mode_selector(summarize_video_bindings(rows), "ap-minimax-h3") is False


def test_composite_call_name_matches_same_family() -> None:
    """带渠道前缀的复合名（``provider::model``）与裸名必须得到同一份事实。"""

    bindings = summarize_video_bindings(_AIPDD_CAPABILITIES["data"])
    plain = video_input_facts_for_model(bindings, "ap-minimax-h3")
    dotted = video_input_facts_for_model(bindings, "aipdd::ap-minimax-h3")
    assert dotted is not None
    assert dotted["bindings"] == plain["bindings"]
    assert dotted["acceptsReferenceVideo"] is False


def test_latentsync_family_matched_and_accepts_video_and_audio() -> None:
    bindings = summarize_video_bindings(_AIPDD_CAPABILITIES["data"])
    facts = video_input_facts_for_model(bindings, "ap-latentsync-1.5")
    assert facts is not None
    assert facts["acceptsReferenceVideo"] is True
    assert facts["acceptsReferenceAudio"] is True
    assert facts["taskKinds"] == ["lip_sync"]


def test_unrelated_model_does_not_match_h3_family() -> None:
    """关联不上就不表态：拿渠道局部的绑定去否全局清单是另一种误伤。"""

    bindings = summarize_video_bindings(_AIPDD_CAPABILITIES["data"])
    assert video_input_facts_for_model(bindings, "doubao-seedance-2-0-260128") is None
    assert video_input_facts_for_model(bindings, "ap-glm-5.2") is None
    assert video_input_facts_for_model([], "ap-minimax-h3") is None
    assert video_input_facts_for_model(bindings, "") is None


def test_family_tokens_drop_variant_suffixes_and_channel_prefixes() -> None:
    assert name_family_tokens("ap-minimax-h3") == {"minimax", "h3"}
    assert name_family_tokens("aipdd_minimax_h3_first_last_frame_speed_test") == {
        "minimax",
        "h3",
    }


def test_offline_bindings_are_never_capabilities() -> None:
    """渠道把绑定摘下去之后，它的入参声明既不能放开界面，也不能当成可用事实。

    命中项全下线时必须返回 None（不表态）而不是「都不吃」——「下线」读不出
    「不支持」，拿它判死会把一个只是暂时调不到的模型在界面上直接抹掉。
    """

    bindings = [
        {
            "code": "aipdd_minimax_h3_video_edit",
            "name": "",
            "taskKind": "video_edit",
            "uiTypes": ["video_url"],
            "inputModalities": ["video"],
            "available": False,
        },
        {
            "code": "aipdd_minimax_h3_t2v",
            "name": "",
            "taskKind": "text_to_video",
            "uiTypes": ["textarea"],
            "inputModalities": ["text"],
            "available": True,
        },
    ]
    facts = video_input_facts_for_model(bindings, "ap-minimax-h3")
    assert facts["bindings"] == ["aipdd_minimax_h3_t2v"]
    assert facts["acceptsReferenceVideo"] is False
    assert facts["inputUiTypes"] == ["textarea"]
    assert video_input_facts_for_model([dict(bindings[1], available=False)], "ap-minimax-h3") is None


def test_bindings_accepting_input_lists_only_online_alternatives() -> None:
    """报错里的「出路」候选：只给在线的、且不是当前模型自己那些绑定。"""

    bindings = summarize_video_bindings(_AIPDD_CAPABILITIES["data"])
    assert [item["code"] for item in bindings_accepting_input(bindings, "video")] == [
        "aipdd_latentsync1.5"
    ]
    assert [item["code"] for item in bindings_accepting_input(bindings, "audio")] == [
        "aipdd_latentsync1.5"
    ]
    # 已经是当前模型命中项的绑定不能当成「换个绑定」的出路再告诉用户一遍。
    assert bindings_accepting_input(bindings, "video", exclude_codes={"aipdd_latentsync1.5"}) == []
    # 非视频绑定不在表里；下线的同样不配当出路。
    assert bindings_accepting_input([dict(bindings[0], available=False)], "video") == []


# ---------------------------------------------------------------- 事实表里的证据字段


def test_param_labels_lead_with_required_inputs() -> None:
    """报错里的入参清单把必填项排前：用户最先要补的就是它们，没名就退 paramKey。"""

    bindings = summarize_video_bindings(_AIPDD_CAPABILITIES["data"])
    omni = next(item for item in bindings if item["code"] == "aipdd_minimax_h3_ref2va_test")
    assert omni["paramNames"] == ["提示词", "首帧图片", "尾帧图片", "时长（秒）"]
    fallback = summarize_video_bindings([
        {
            "code": "x_video",
            "endpointType": "openai-video",
            "params": [{"uiType": "video_url", "paramKey": "video"}],
        }
    ])
    assert fallback[0]["paramNames"] == ["video"]


def test_binding_accepts_input_uses_both_ui_type_and_modality() -> None:
    assert binding_accepts_input({"uiTypes": ["video_url"]}, "video") is True
    assert binding_accepts_input({"inputModalities": ["video"]}, "video") is True
    assert binding_accepts_input({"uiTypes": ["image_url"]}, "video") is False
    assert binding_accepts_input({"uiTypes": ["textarea"]}, "nonsense") is False


# ---------------------------------------------------------------- 远程读取降级


def test_fetch_capabilities_requires_base_url_and_key() -> None:
    assert fetch_upstream_capabilities("", "key") == (
        [],
        "base_url and upstream_key are required",
    )
    assert fetch_upstream_capabilities("https://x/v1", "") == (
        [],
        "base_url and upstream_key are required",
    )


def test_fetch_capabilities_swallows_remote_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """拿不到事实就是拿不到，绝不把远程故障当用户错误抛出去。"""

    def boom(*args: Any, **kwargs: Any) -> None:
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(httpx, "get", boom)
    entries, error = fetch_upstream_capabilities("https://api.example.test/v1", "sk-1")
    assert entries == [] and "timed out" in error


def test_fetch_capabilities_treats_http_error_as_no_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """404 / 401 只代表这个渠道没暴露能力接口，判定必须退回「不表态」。"""

    class _Resp:
        status_code = 404

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError(
                "404 Not Found",
                request=httpx.Request("GET", "https://api.example.test/v1/capabilities"),
                response=httpx.Response(404),
            )

        def json(self) -> Any:  # pragma: no cover - 404 分支不该读 body
            raise AssertionError("must not parse error body")

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: _Resp())
    entries, error = fetch_upstream_capabilities("https://api.example.test/v1", "sk-1")
    assert entries == [] and "404" in error


def test_fetch_capabilities_parses_ok_response(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> Any:
            return _AIPDD_CAPABILITIES

    seen: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> _Resp:
        seen["url"] = url
        seen["headers"] = kwargs.get("headers") or {}
        return _Resp()

    monkeypatch.setattr(httpx, "get", fake_get)
    entries, error = fetch_upstream_capabilities("https://api.example.test/v1/", "sk-1")
    assert error == ""
    assert len(summarize_video_bindings(entries)) == 4
    # base_url 带不带尾斜杠、是否带 /v1 之外的路径，都不能拼出双斜杠。
    assert seen["url"] == "https://api.example.test/v1/capabilities"
    assert seen["headers"]["Authorization"] == "Bearer sk-1"


# ---------------------------------------------------------------- 落盘与刷新


def test_capability_bindings_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    assert get_provider_capability_bindings("aipdd") == []
    save_provider_capability_bindings(
        "aipdd", summarize_video_bindings(_AIPDD_CAPABILITIES["data"]), base_url="https://x/v1"
    )
    stored = get_provider_capability_bindings("aipdd")
    assert len(stored) == 4
    # 整体覆盖：渠道摘掉绑定后判定要跟着收紧，不残留旧事实。
    save_provider_capability_bindings("aipdd", stored[:1], base_url="https://x/v1")
    assert len(get_provider_capability_bindings("aipdd")) == 1


def test_empty_capability_bindings_are_no_op(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """空清单不写：拉不到 / 没视频绑定都属于「不表态」，不能当成「都不吃」。"""

    _isolate_settings_db(monkeypatch, tmp_path)
    save_provider_capability_bindings(
        "aipdd", summarize_video_bindings(_AIPDD_CAPABILITIES["data"])
    )
    save_provider_capability_bindings("aipdd", [])
    save_provider_capability_bindings("", [{"code": "x"}])
    assert len(get_provider_capability_bindings("aipdd")) == 4


def test_corrupted_capability_payload_degrades_to_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    model_gateway_settings._write_many(
        {PROVIDER_CAPABILITY_BINDINGS_SETTING_KEY: "{not-json"}
    )
    assert get_provider_capability_bindings("aipdd") == []


def test_refresh_persists_facts(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setattr(
        newapi_provisioner,
        "get_newapi_provider_channels",
        lambda: [
            {
                "provider": "aipdd",
                "baseUrl": "https://api.example.test/v1",
                "upstreamKey": "sk-1",
            }
        ],
    )
    saved: dict[str, Any] = {}

    def fake_fetch(base_url: str, upstream_key: str, **kwargs: Any):
        saved["base_url"] = base_url
        saved["upstream_key"] = upstream_key
        return _AIPDD_CAPABILITIES["data"], ""

    monkeypatch.setattr(
        "novelvideo.upstream_capabilities.fetch_upstream_capabilities", fake_fetch
    )
    result = newapi_provisioner.refresh_provider_capability_bindings("AIPDD")
    assert result == {"provider": "aipdd", "ok": True, "count": 4}
    assert saved == {"base_url": "https://api.example.test/v1", "upstream_key": "sk-1"}
    assert len(get_provider_capability_bindings("aipdd")) == 4


def test_refresh_failure_and_empty_video_bindings_do_not_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setattr(
        newapi_provisioner,
        "get_newapi_provider_channels",
        lambda: [
            {
                "provider": "aipdd",
                "baseUrl": "https://api.example.test/v1",
                "upstreamKey": "sk-1",
            }
        ],
    )

    def failing_fetch(base_url: str, upstream_key: str, **kwargs: Any):
        return [], "HTTP 404"

    monkeypatch.setattr("novelvideo.upstream_capabilities.fetch_upstream_capabilities", failing_fetch)
    result = newapi_provisioner.refresh_provider_capability_bindings("aipdd")
    assert result["ok"] is False and result["error"] == "HTTP 404"
    assert get_provider_capability_bindings("aipdd") == []

    # 渠道暴露能力接口但没有视频绑定：同样不写库（可能是代理给第三方的模型）。
    monkeypatch.setattr(
        "novelvideo.upstream_capabilities.fetch_upstream_capabilities",
        lambda *args, **kwargs: ([{"code": "only-chat", "endpointType": "llm-chat"}], ""),
    )
    skipped = newapi_provisioner.refresh_provider_capability_bindings("aipdd")
    assert skipped["ok"] is True and skipped.get("skipped") == "no-video-bindings"
    assert get_provider_capability_bindings("aipdd") == []


def test_refresh_skips_unconfigured_channel(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setattr(newapi_provisioner, "get_newapi_provider_channels", lambda: [])
    result = newapi_provisioner.refresh_provider_capability_bindings("aipdd")
    assert result["ok"] is False and "channel not configured" in result["error"]
    assert newapi_provisioner.refresh_provider_capability_bindings("")["ok"] is False
